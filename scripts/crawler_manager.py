"""Run every registered Windows collector sequentially in the background."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

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
            headers={"Origin": "http://127.0.0.1:8000"},
            timeout=180,
        )
        response.raise_for_status()
        state["snapshot"] = response.json()
    except Exception as exc:
        state["import_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    state["state"] = "completed"
    state["finished_at"] = now()
    write_state(state)
    return 0 if all(item["status"] in {"succeeded", "skipped"} for item in state["results"]) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--import-url", default="http://127.0.0.1:8000/api/import")
    parser.add_argument("--timeout", type=int, default=int(os.getenv("CRAWLER_TASK_TIMEOUT", "1800")))
    args = parser.parse_args()
    return run_all(args.import_url, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())

