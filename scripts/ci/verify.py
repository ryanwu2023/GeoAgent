"""Seed isolated CI data, then run the complete backend suite in the built image."""
import sys

import pytest

from backend.adapters import load_bundle
from backend.paths import project_root
from backend.store import save_snapshot, set_working_snapshot_id

if __name__ == "__main__":
    snapshot_id, _ = save_snapshot(load_bundle(project_root() / "examples/demo-data"))
    set_working_snapshot_id(snapshot_id)
    sys.exit(pytest.main(["-q", "/app/tests", "-p", "no:cacheprovider",
                          "--junitxml=/reports/junit.xml"]))
