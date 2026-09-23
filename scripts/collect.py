"""List and run repository-local collectors with isolated result reporting."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.paths import collector_data_root, collector_source_root
from scripts.integrate_collectors import load_registry


def serialize_for_console(value) -> str:
    """Return JSON that is safe even when Windows stdout still uses GBK."""
    return json.dumps(value, ensure_ascii=True, indent=2)


def select_collectors(registry: list[dict], ids: list[str], topic: str | None) -> list[dict]:
    wanted = set(ids)
    return [
        item
        for item in registry
        if item.get("enabled", True)
        and (not wanted or item["id"] in wanted)
        and (not topic or topic in item.get("topics", []))
    ]


def _sync_output(collector_id: str, source_root: Path, data_root: Path) -> None:
    source = source_root / collector_id / "output"
    if source.is_dir():
        shutil.copytree(source, data_root / collector_id / "output", dirs_exist_ok=True)


def run_one(item: dict, *, timeout: int, verify_only: bool = False) -> dict:
    source_root = collector_source_root()
    data_root = collector_data_root()
    collector_dir = source_root / item["id"]
    entrypoint = (collector_dir / item["entrypoint"]).resolve()
    args = item.get("selftest_args", []) if verify_only else []
    if verify_only and not args:
        return {"id": item["id"], "returncode": "not_provided"}
    if not entrypoint.is_file():
        return {"id": item["id"], "returncode": "entrypoint_missing", "entrypoint": str(entrypoint)}
    env = {**os.environ, "PYTHONUTF8": "1", "COLLECTOR_OUTPUT_ROOT": str(data_root / item["id"] / "output")}
    started = datetime.now(timezone.utc).isoformat()
    try:
        completed = subprocess.run(
            [sys.executable, str(entrypoint), *args],
            cwd=collector_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        result = {
            "id": item["id"],
            "returncode": completed.returncode,
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "stdout_tail": completed.stdout[-2000:],
            "stderr_tail": completed.stderr[-2000:],
        }
        if completed.returncode == 0 and not verify_only:
            _sync_output(item["id"], source_root, data_root)
        return result
    except subprocess.TimeoutExpired:
        return {"id": item["id"], "returncode": "timeout", "started_at": started}


def run_collectors(
    ids: list[str],
    topic: str | None,
    timeout: int,
    *,
    registry: list[dict] | None = None,
    verify_only: bool = False,
) -> list[dict]:
    registry = registry if registry is not None else load_registry(collector_source_root() / "collectors.json")
    selected = select_collectors(registry, ids, topic)
    return [run_one(item, timeout=timeout, verify_only=verify_only) for item in selected]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--id", action="append", default=[])
    parser.add_argument("--topic")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    registry = load_registry(collector_source_root() / "collectors.json")
    if args.list:
        for item in registry:
            print(f"{item['id']}\t{item['name_zh']}\t{','.join(item['topics'])}")
        return 0
    if not (args.all or args.id or args.topic):
        parser.error("请使用 --all、--id 或 --topic 选择采集器")
    results = run_collectors(args.id, args.topic, args.timeout, registry=registry, verify_only=args.verify_only)
    report_dir = PROJECT_ROOT / "data" / "collection-runs" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(serialize_for_console(results))
    return 0 if all(item["returncode"] in (0, "not_provided") for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
