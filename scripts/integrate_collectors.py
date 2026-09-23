"""Copy collector source and runtime output into this repository without changing sources."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


EXCLUDED_DIRS = {".workbuddy", ".git", ".probe", "__pycache__", ".pytest_cache", "logs", "state", "_probe", "config-archive"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".log"}
RESEARCH_FILES = (
    "华泰中东简报.md",
    "deep-research-report.md",
    "霍尔木兹通行量核对报告-2026-09-21.md",
    "全库加固完成报告-2026-09-20.md",
    "全库爬虫核对报告-2026-09-20.md",
)


def _excluded(relative: Path) -> bool:
    return (
        any(part in EXCLUDED_DIRS for part in relative.parts)
        or relative.suffix.lower() in EXCLUDED_SUFFIXES
        or ".bak" in relative.name.lower()
    )


def _copy_tree(source: Path, destination: Path, *, dry_run: bool = False) -> tuple[int, int]:
    count = size = 0
    if not source.is_dir():
        return count, size
    for file in sorted(source.rglob("*")):
        if not file.is_file():
            continue
        relative = file.relative_to(source)
        if _excluded(relative):
            continue
        count += 1
        size += file.stat().st_size
        if not dry_run:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, target)
    return count, size


def copy_collector(source: Path, destination: Path, output_destination: Path, *, dry_run: bool = False) -> dict:
    source_count = source_bytes = 0
    for file in sorted(source.rglob("*")):
        if not file.is_file():
            continue
        relative = file.relative_to(source)
        if relative.parts and relative.parts[0] == "output":
            continue
        if _excluded(relative):
            continue
        source_count += 1
        source_bytes += file.stat().st_size
        if not dry_run:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, target)
    output_count, output_bytes = _copy_tree(source / "output", output_destination / "output", dry_run=dry_run)
    return {
        "id": source.name,
        "source_files": source_count,
        "source_bytes": source_bytes,
        "output_files": output_count,
        "output_bytes": output_bytes,
    }


def load_registry(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    collectors = payload.get("collectors", [])
    required = {"id", "name_zh", "topics", "entrypoint", "selftest_args", "output_kind", "enabled"}
    if any(not required.issubset(item) for item in collectors):
        raise ValueError("采集器注册表字段不完整")
    return collectors


def integrate(source_roots: list[Path], project_root: Path, *, dry_run: bool = False) -> list[dict]:
    registry = load_registry(project_root / "collectors" / "collectors.json")
    by_name = {child.name: child for root in source_roots if root.is_dir() for child in root.iterdir() if child.is_dir()}
    results = []
    for item in registry:
        source = by_name.get(item["id"])
        if source is None:
            results.append({"id": item["id"], "state": "source_missing"})
            continue
        result = copy_collector(
            source,
            project_root / "collectors" / item["id"],
            project_root / "data" / "collector-output" / item["id"],
            dry_run=dry_run,
        )
        result["state"] = "planned" if dry_run else "copied"
        results.append(result)
    for root in source_roots:
        for name in RESEARCH_FILES:
            file = root / name
            if file.is_file() and not dry_run:
                target = project_root / "knowledge" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, target)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", action="append", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    results = integrate(args.source_root, args.project_root.resolve(), dry_run=args.dry_run)
    print(json.dumps({"collectors": results, "count": len(results), "dry_run": args.dry_run}, ensure_ascii=False, indent=2))
    return 0 if all(item.get("state") != "source_missing" for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
