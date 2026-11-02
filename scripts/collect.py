"""List and run repository-local collectors with isolated result reporting."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.paths import collector_data_root, collector_source_root
from backend.collector_ledger import service_lock
from scripts.integrate_collectors import load_registry
from backend.collector_logs import redact


def serialize_for_console(value) -> str:
    """Return JSON that is safe even when Windows stdout still uses GBK."""
    return json.dumps(value, ensure_ascii=True, indent=2)


def select_collectors(
    registry: list[dict],
    ids: list[str],
    topic: str | None,
    exclude_ids: list[str] | None = None,
) -> list[dict]:
    wanted = set(ids)
    excluded = set(exclude_ids or [])
    return [
        item
        for item in registry
        if item.get("enabled", True)
        and item["id"] not in excluded
        and (not wanted or item["id"] in wanted)
        and (not topic or topic in item.get("topics", []))
    ]


SOURCE_DIAGNOSTICS = ("_source_status.json", "_fetch_stats.json", "_source_health.json")

def _source_diagnostics(output: Path) -> dict:
    """Retain bounded, redacted failure evidence before staging cleanup."""
    def scrub(value):
        if isinstance(value, str):
            return redact(value)
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, dict):
            return {key: "[REDACTED]" if any(word in key.lower() for word in ("password", "token", "api_key", "authorization", "cookie")) else scrub(item) for key, item in value.items()}
        return value
    files = {}
    for name in SOURCE_DIAGNOSTICS:
        path = output / name
        try:
            if path.is_file() and path.stat().st_size <= 256 * 1024:
                files[name] = scrub(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return files


def _collector_environment(item: dict, env: dict) -> dict:
    result = dict(env)
    result["GEO_COLLECTOR_MANAGED"] = "true"
    # Only reviewed domestic sources bypass the egress proxy, within this child.
    hosts = item.get("direct_hosts", [])
    if hosts:
        entries = [x for x in (env.get("NO_PROXY", "") + "," + env.get("no_proxy", "")).split(",") if x]
        value = ",".join(dict.fromkeys([*entries, *hosts]))
        result["NO_PROXY"] = result["no_proxy"] = value
    return result


def _copy_missing(source: Path, target: Path) -> None:
    """Merge legacy source output into a staging tree without overwriting it."""
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            _copy_missing(item, destination)
        elif not destination.exists():
            shutil.copy2(item, destination)


def _publish_staged_output(stage: Path, live: Path) -> None:
    """Swap a completed staging directory into the live data root with rollback."""
    live.parent.mkdir(parents=True, exist_ok=True)
    backup = live.parent / f".{live.name}.previous-{uuid.uuid4().hex}"
    had_live = live.exists()
    if had_live:
        live.replace(backup)
    try:
        stage.replace(live)
    except Exception:
        if had_live and backup.exists() and not live.exists():
            backup.replace(live)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def _stage_and_publish(collector_id: str, source_root: Path, data_root: Path, staging_root: Path) -> bool:
    source = source_root / collector_id / "output"
    stage = staging_root / collector_id / "output"
    live = data_root / collector_id / "output"
    if source.is_dir():
        _copy_missing(source, stage)
    if not stage.is_dir() or not any(stage.iterdir()):
        return False
    _publish_staged_output(stage, live)
    return True


def _published_runtime_files(collector_id: str, data_root: Path) -> list[str]:
    """Return stable relative output paths for the monitoring report."""
    live = data_root / collector_id / "output"
    if not live.is_dir():
        return []
    return sorted(
        str(path.relative_to(live))
        for path in live.rglob("*")
        if path.is_file()
    )


def _tail(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return str(value)[-2000:]


def _execute_process(command: list[str], *, cwd: Path, env: dict[str, str], timeout: int,
                     output_callback: Callable[[str, str], None] | None = None):
    """Run a collector, optionally forwarding stdout/stderr lines as they arrive."""
    if output_callback is None:
        return subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    process = subprocess.Popen(
        command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    streams: dict[str, list[str]] = {"stdout": [], "stderr": []}

    def consume(name: str, pipe) -> None:
        for line in iter(pipe.readline, ""):
            streams[name].append(line)
            output_callback(name, line.rstrip("\\r\\n"))
        pipe.close()

    readers = [threading.Thread(target=consume, args=(name, pipe), daemon=True)
               for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr))]
    for reader in readers:
        reader.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        for reader in readers:
            reader.join(timeout=1)
        raise subprocess.TimeoutExpired(
            command, timeout, output="".join(streams["stdout"]), stderr="".join(streams["stderr"])
        )
    for reader in readers:
        reader.join(timeout=1)

    class Completed:
        pass
    completed = Completed()
    completed.returncode = returncode
    completed.stdout = "".join(streams["stdout"])
    completed.stderr = "".join(streams["stderr"])
    return completed


def run_one(
    item: dict,
    *,
    timeout: int,
    verify_only: bool = False,
    max_attempts: int = 1,
    backoff_factor: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    staging_root: Path | None = None,
    output_callback: Callable[[str, str], None] | None = None,
) -> dict:
    """Run one collector while preventing concurrent runs for the same id."""
    lock_path = collector_data_root() / ".collector-locks" / f"{item['id']}.lock"
    with service_lock(lock_path):
        return _run_one_unlocked(
            item,
            timeout=timeout,
            verify_only=verify_only,
            max_attempts=max_attempts,
            backoff_factor=backoff_factor,
            sleep=sleep,
            staging_root=staging_root,
            output_callback=output_callback,
        )


def _run_one_unlocked(
    item: dict,
    *,
    timeout: int,
    verify_only: bool = False,
    max_attempts: int = 1,
    backoff_factor: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    staging_root: Path | None = None,
    output_callback: Callable[[str, str], None] | None = None,
) -> dict:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    source_root = collector_source_root()
    data_root = collector_data_root()
    collector_dir = source_root / item["id"]
    entrypoint = (collector_dir / item["entrypoint"]).resolve()
    args = item.get("selftest_args", []) if verify_only else []
    if verify_only and not args:
        return {"id": item["id"], "returncode": "not_provided", "attempts": 0, "max_attempts": max_attempts}
    if not entrypoint.is_file():
        return {"id": item["id"], "returncode": "entrypoint_missing", "entrypoint": str(entrypoint), "attempts": 0, "max_attempts": max_attempts}
    staging_root = Path(staging_root) if staging_root is not None else data_root / ".staging" / f"manual-{uuid.uuid4().hex}"
    collector_stage = staging_root / item["id"]
    output_stage = collector_stage / "output"
    history: list[dict] = []
    first_started: str | None = None
    last_result: dict = {"id": item["id"], "returncode": "failed"}
    for attempt in range(1, max_attempts + 1):
        if output_stage.exists():
            shutil.rmtree(output_stage)
        output_stage.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
            "COLLECTOR_RUN_ID": staging_root.name,
            "COLLECTOR_PREVIOUS_OUTPUT_ROOT": str(data_root / item["id"] / "output"),
            "COLLECTOR_OUTPUT_ROOT": str(output_stage),
            "COLLECTOR_STATE_ROOT": str(data_root / item["id"] / "state"),
        }
        env = _collector_environment(item, env)
        started = datetime.now(timezone.utc).isoformat()
        first_started = first_started or started
        try:
            completed = _execute_process(
                [sys.executable, str(entrypoint), *args], cwd=collector_dir,
                env=env, timeout=timeout, output_callback=output_callback,
            )
            returncode = completed.returncode
            attempt_record = {
                "attempt": attempt,
                "returncode": returncode,
                "started_at": started,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "stdout_tail": _tail(completed.stdout),
                "stderr_tail": _tail(completed.stderr),
            }
            last_result = {
                "id": item["id"],
                "returncode": returncode,
                "started_at": first_started,
                "finished_at": attempt_record["finished_at"],
                "stdout_tail": attempt_record["stdout_tail"],
                "stderr_tail": attempt_record["stderr_tail"],
            }
            history.append(attempt_record)
            if returncode == 0:
                if not verify_only:
                    last_result["published"] = _stage_and_publish(item["id"], source_root, data_root, staging_root)
                    if last_result["published"]:
                        last_result["runtime_files"] = _published_runtime_files(item["id"], data_root)
                last_result.update({"attempts": attempt, "max_attempts": max_attempts, "attempt_history": history})
                return last_result
        except subprocess.TimeoutExpired as exc:
            finished = datetime.now(timezone.utc).isoformat()
            attempt_record = {
                "attempt": attempt,
                "returncode": "timeout",
                "started_at": started,
                "finished_at": finished,
                "stdout_tail": _tail(exc.stdout),
                "stderr_tail": _tail(exc.stderr),
            }
            last_result = {
                "id": item["id"],
                "returncode": "timeout",
                "started_at": first_started,
                "finished_at": finished,
                "stdout_tail": attempt_record["stdout_tail"],
                "stderr_tail": attempt_record["stderr_tail"],
            }
            history.append(attempt_record)
        last_result["source_diagnostics"] = _source_diagnostics(output_stage)
        # Configuration errors do not become valid by repeating the same request.
        if last_result.get("returncode") == 2 and "configuration_required" in last_result.get("stdout_tail", ""):
            break
        if attempt < max_attempts:
            sleep(max(0.0, backoff_factor) * (2 ** (attempt - 1)))
    if collector_stage.exists():
        shutil.rmtree(collector_stage)
    last_result.update({"attempts": len(history), "max_attempts": max_attempts, "attempt_history": history, "published": False})
    return last_result


def run_collectors(
    ids: list[str],
    topic: str | None,
    timeout: int,
    *,
    registry: list[dict] | None = None,
    exclude_ids: list[str] | None = None,
    verify_only: bool = False,
    max_attempts: int = 1,
    backoff_factor: float = 1.0,
    staging_root: Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[dict], None] | None = None,
    output_callback: Callable[[str, str, str], None] | None = None,
) -> list[dict]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    registry = registry if registry is not None else load_registry(collector_source_root() / "collectors.json")
    selected = select_collectors(registry, ids, topic, exclude_ids=exclude_ids)
    staging_root = Path(staging_root) if staging_root is not None else collector_data_root() / ".staging" / f"run-{uuid.uuid4().hex}"
    staging_root.mkdir(parents=True, exist_ok=True)
    try:
        def execute(item: dict) -> dict:
            if progress:
                progress({"id": item["id"], "status": "running", "started_at": datetime.now(timezone.utc).isoformat()})
            result = run_one(
                item, timeout=timeout, verify_only=verify_only, max_attempts=max_attempts,
                backoff_factor=backoff_factor, sleep=sleep, staging_root=staging_root,
                output_callback=(lambda stream, line: output_callback(item["id"], stream, line)) if output_callback else None,
            )
            if progress:
                progress({**result, "status": "finished"})
            return result

        max_workers = min(4, max(1, len(selected)))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="collector") as pool:
            results = list(pool.map(execute, selected))
        return results
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--id", action="append", default=[])
    parser.add_argument("--exclude-id", action="append", default=[], help="排除指定采集器，可重复")
    parser.add_argument("--topic")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--max-attempts", type=int, default=1, help="进程失败/超时时的最大尝试次数")
    parser.add_argument("--backoff-sec", type=float, default=1.0, help="重试指数退避初始秒数")
    args = parser.parse_args()
    registry = load_registry(collector_source_root() / "collectors.json")
    if args.list:
        for item in registry:
            print(f"{item['id']}\t{item['name_zh']}\t{','.join(item['topics'])}")
        return 0
    if not (args.all or args.id or args.topic):
        parser.error("请使用 --all、--id 或 --topic 选择采集器")
    results = run_collectors(
        args.id,
        args.topic,
        args.timeout,
        registry=registry,
        exclude_ids=args.exclude_id,
        verify_only=args.verify_only,
        max_attempts=args.max_attempts,
        backoff_factor=args.backoff_sec,
    )
    report_dir = PROJECT_ROOT / "data" / "collection-runs" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(serialize_for_console(results))
    return 0 if all(item["returncode"] in (0, "not_provided") for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
