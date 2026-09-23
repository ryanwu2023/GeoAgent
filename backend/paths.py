"""Repository-local paths with optional deployment overrides."""
import os
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def collector_source_root() -> Path:
    return project_root() / "collectors"


def collector_data_root() -> Path:
    configured = os.getenv("COLLECTOR_DATA_ROOT")
    return Path(configured) if configured else project_root() / "data" / "collector-output"


def collector_read_root() -> Path:
    configured = os.getenv("CRAWLER_ROOT")
    if configured:
        return Path(configured)
    runtime = collector_data_root()
    recognizable = any(runtime.glob("*/output/ALL-records.jsonl")) or any(runtime.glob("*/output/geo-v6.json")) or (runtime / "taco-monitor/output/taco-latest.csv").is_file()
    return runtime if recognizable else project_root() / "examples" / "demo-data"

