"""Import a published collector tree into an immutable research snapshot.

The importer is deliberately separate from the collector process.  A run may
finish successfully while importing fails (for example because a schema or a
source file changed); callers must persist those two outcomes independently.
"""

from __future__ import annotations

from pathlib import Path

from .adapters import load_bundle
from .chinese import start_translation
from .paths import collector_data_root
from .store import save_snapshot


def _has_published_output(root: Path) -> bool:
    return any(root.glob("*/output/ALL-records.jsonl")) or any(
        root.glob("*/output/LATEST-records.jsonl")
    ) or any(root.glob("*/output/geo-v6.json")) or (
        root / "taco-monitor" / "output" / "taco-latest.csv"
    ).is_file()


def import_collector_output(root: Path | None = None) -> dict:
    """Import only the real published collector tree, never the demo fallback."""
    target = Path(root) if root is not None else collector_data_root()
    if not target.is_dir():
        raise FileNotFoundError(f"采集输出目录不存在: {target}")
    if not _has_published_output(target):
        raise ValueError("没有可导入的已发布采集输出")
    bundle = load_bundle(target)
    if not bundle["records"] and not bundle.get("observations"):
        raise ValueError("采集输出为空，未生成快照")
    snapshot_id, created = save_snapshot(bundle)
    start_translation()
    return {
        "snapshot_id": snapshot_id,
        "created": created,
        "records": len(bundle["records"]),
        "observations": len(bundle.get("observations", [])),
        "errors": len(bundle.get("errors", [])),
        "cleaning": bundle.get("cleaning", {}),
    }
