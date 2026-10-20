"""Run every registered Windows collector sequentially in the background."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.integrate_collectors import load_registry

STATE_DIR = ROOT / "data" / "crawler-runs"
STATE_FILE = STATE_DIR / "current.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def choose_launcher(project: Path) -> Path | None:
    for name in ("run_new.bat", "run.bat"):
        candidate = project / name
        if candidate.is_file():
            return candidate
    return None


def write_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(STATE_FILE)


def run_all(import_url: str, timeout: int) -> int:
    registry = load_registry(ROOT / "collectors" / "collectors.json")
    selected = [item for item in registry if item.get("enabled", True)]
    run_dir = STATE_DIR / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "state": "running",
        "started_at": now(),
        "finished_at": None,
        "total": len(selected),
        "current": None,
        "child_pid": None,
        "results": [],
        "snapshot": None,
    }
    write_state(state)
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "RM_NO_PAUSE": "1",
        "RIM_NO_PAUSE": "1",
        "DSM_NO_PAUSE": "1",
    }
    for item in selected:
        project = ROOT / "collectors" / item["id"]
        launcher = choose_launcher(project)
        result = {
            "id": item["id"],
            "name": item.get("name_zh", item["id"]),
            "launcher": launcher.name if launcher else None,
            "started_at": now(),
            "finished_at": None,
            "returncode": None,
            "status": "running" if launcher else "skipped",
            "log": None,
        }
        state["current"] = item["id"]
        if not launcher:
            result.update(finished_at=now(), returncode="launcher_missing", status="skipped")
            state["results"].append(result)
            write_state(state)
            continue
        log_path = run_dir / f"{item['id']}.log"
        result["log"] = str(log_path.relative_to(ROOT))
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            process = subprocess.Popen(
                ["cmd.exe", "/d", "/c", launcher.name],
                cwd=project,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP),
            )
            state["child_pid"] = process.pid
            write_state(state)
            try:
                result["returncode"] = process.wait(timeout=timeout)
                result["status"] = "succeeded" if result["returncode"] == 0 else "failed"
            except subprocess.TimeoutExpired:
                subprocess.run(
                    ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    check=False,
                )
                result.update(returncode="timeout", status="failed")
        result["finished_at"] = now()
        state["results"].append(result)
        state["child_pid"] = None
        write_state(state)
    state["current"] = None
    try:
        response = httpx.post(
            import_url,
            headers={"Origin": f"{urlsplit(import_url).scheme}://{urlsplit(import_url).netloc}"},
            timeout=180,
        )
        response.raise_for_status()
        state["snapshot"] = response.json()
    except Exception as exc:
        detail = ""
        if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
            detail = f"；响应：{exc.response.text[:500]}"
        state["import_error"] = f"{type(exc).__name__}: {str(exc)[:300]}{detail}"
    state["state"] = "completed"
    state["finished_at"] = now()
    write_state(state)
    return 0 if all(item["status"] in {"succeeded", "skipped"} for item in state["results"]) else 1


def run_loop(import_url: str, timeout: int, interval_sec: int, *, once: bool = False) -> int:
    """Run immediately, then stay alive so collection continues each day."""
    last_returncode = 0
    while True:
        last_returncode = run_all(import_url, timeout)
        if once:
            return last_returncode
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        state.update(
            # Keep the existing UI contract: the latest batch is completed,
            # while next_run_at shows that the manager remains scheduled.
            state="completed",
            next_run_at=datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() + interval_sec,
                timezone.utc,
            ).isoformat(),
            last_returncode=last_returncode,
        )
        write_state(state)
        # A transient source failure is persisted above but must not disable
        # all later refreshes until somebody restarts the web application.
        time.sleep(max(60, interval_sec))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--import-url", default="http://127.0.0.1:8000/api/import")
    parser.add_argument("--timeout", type=int, default=int(os.getenv("CRAWLER_TASK_TIMEOUT", "1800")))
    parser.add_argument("--interval-sec", type=int, default=int(os.getenv("CRAWLER_INTERVAL_SEC", "86400")))
    parser.add_argument("--once", action="store_true", help="只执行一轮；默认每 24 小时自动执行")
    args = parser.parse_args()
    if args.interval_sec < 60:
        parser.error("--interval-sec 必须至少为 60 秒")
    return run_loop(args.import_url, args.timeout, args.interval_sec, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
