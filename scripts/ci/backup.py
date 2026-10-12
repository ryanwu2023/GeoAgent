"""Run via kubectl exec stdin; create consistent SQLite release recovery points."""
import json
from pathlib import Path
import re
import sqlite3
import sys


def backup(root: Path, release_id: str) -> Path:
    if not re.fullmatch(r"[0-9]+", release_id):
        raise ValueError("Invalid release ID")
    # Fail closed on collector locks, including stale locks requiring operator review.
    locks = [root / "collection-runs/.collector-service.lock"]
    locks += list((root / "collector-output/.collector-locks").glob("*.lock"))
    if any(path.exists() for path in locks):
        raise RuntimeError("Collector lock exists; finish/recover collection before deploying")
    destination = root / "release-backups" / release_id
    destination.mkdir(parents=True, exist_ok=False)
    saved = []
    for relative in ["research.sqlite3", "collection-runs/ledger.sqlite3"]:
        source = root / relative
        if source.exists():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src:
                with sqlite3.connect(target) as dst:
                    src.backup(dst)
                    if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("Backup integrity check failed")
            saved.append(relative)
    if "research.sqlite3" not in saved:
        raise RuntimeError("Production research database is missing")
    (destination / "manifest.json").write_text(json.dumps({"release": release_id, "files": saved}))
    return destination


if __name__ == "__main__":
    print(backup(Path("/app/data"), sys.argv[1]))
