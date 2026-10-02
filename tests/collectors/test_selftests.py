from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.integrate_collectors import load_registry


ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "collectors" / "collectors.json"
REGISTRY = load_registry(REGISTRY_PATH)
SELFTEST_COLLECTORS = [item for item in REGISTRY if item.get("selftest_args")]


@pytest.mark.parametrize("item", SELFTEST_COLLECTORS, ids=lambda item: item["id"])
def test_registered_collector_selftest(item, tmp_path):
    entrypoint = (ROOT / "collectors" / item["id"] / item["entrypoint"]).resolve()
    output_root = tmp_path / item["id"] / "collector-output"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONUTF8": "1",
            "COLLECTOR_OUTPUT_ROOT": str(output_root),
        }
    )

    result = subprocess.run(
        [sys.executable, str(entrypoint), *item["selftest_args"]],
        cwd=entrypoint.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )

    if result.returncode != 0:
        details = (result.stdout + "\n" + result.stderr).strip()
        pytest.fail(f"collector {item['id']} selftest failed (exit {result.returncode})\n{details}")
