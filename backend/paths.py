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
    return Path(configured) if configured else collector_data_root()

