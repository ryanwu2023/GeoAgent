"""Repository-local paths with optional deployment overrides."""
import os
import shutil
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def collector_source_root() -> Path:
    return project_root() / "collectors"


def knowledge_root() -> Path:
    configured = os.getenv("KNOWLEDGE_ROOT")
    return Path(configured) if configured else project_root() / "knowledge"


def collector_data_root() -> Path:
    configured = os.getenv("COLLECTOR_DATA_ROOT")
    return Path(configured) if configured else project_root() / "data" / "collector-output"


def collector_read_root() -> Path:
    configured = os.getenv("CRAWLER_ROOT")
    if configured:
        return Path(configured)
    runtime = collector_data_root()
    recognizable = any(runtime.glob("*/output/ALL-records.jsonl")) or any(runtime.glob("*/output/LATEST-records.jsonl")) or any(runtime.glob("*/output/geo-v6.json")) or (runtime / "taco-monitor/output/taco-latest.csv").is_file()
    return runtime if recognizable else project_root() / "examples" / "demo-data"


def sync_local_collector_outputs(
    source_root: Path | None = None, data_root: Path | None = None
) -> dict[str, int]:
    """Incrementally copy outputs produced by directly-run local collectors.

    The unified runner already writes to the runtime data directory, but each
    bundled collector can also be run from its own directory. Those legacy
    entry points write to ``collectors/<id>/output``. Snapshot import calls this
    helper so both execution modes feed the same runtime data directory.
    """
    source_root = source_root or collector_source_root()
    data_root = data_root or collector_data_root()
    result = {"projects": 0, "files": 0, "bytes": 0}
    if not source_root.is_dir():
        return result
    for project in source_root.iterdir():
        output = project / "output"
        if not project.is_dir() or not output.is_dir():
            continue
        project_changed = False
        for source in output.rglob("*"):
            if not source.is_file() or source.is_symlink():
                continue
            relative = source.relative_to(output)
            target = data_root / project.name / "output" / relative
            source_stat = source.stat()
            if target.is_file():
                target_stat = target.stat()
                if (
                    source_stat.st_size == target_stat.st_size
                    and source_stat.st_mtime_ns <= target_stat.st_mtime_ns
                ):
                    continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            project_changed = True
            result["files"] += 1
            result["bytes"] += source_stat.st_size
        result["projects"] += int(project_changed)
    return result
