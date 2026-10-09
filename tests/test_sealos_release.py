"""CI release guards must fail before touching credentials or a cluster."""
import os
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ci/sealos-release.sh"
pytestmark = pytest.mark.skipif(os.name == "nt", reason="Linux runner script")


@pytest.mark.parametrize("tag,protected", [
    ("geoagent-test-v1.0.0", "true"), ("stocklink-prod-v1.0.0", "true"),
    ("geoagent-prod-v1.0.0-rc1", "true"), ("geoagent-prod-v1.0.0", "false"), ("", "true"),
])
@pytest.mark.parametrize("action", ["deploy", "rollback"])
def test_nonproduction_refs_cannot_execute_commands(tmp_path, tag, protected, action):
    marker = tmp_path / "executed"
    for command in ["git", "docker"]:
        stub = tmp_path / command
        stub.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 0\n')
        stub.chmod(0o755)
    result = subprocess.run(["/bin/bash", str(SCRIPT), action], capture_output=True,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
             "CI_COMMIT_TAG": tag, "CI_COMMIT_REF_PROTECTED": protected})
    assert result.returncode != 0
    assert not marker.exists()


def test_unmerged_production_commit_cannot_build(tmp_path):
    git = tmp_path / "git"
    git.write_text('#!/bin/sh\n[ "$1" = fetch ] && exit 0\nexit 1\n')
    git.chmod(0o755)
    result = subprocess.run(["/bin/bash", str(SCRIPT), "verify"], capture_output=True,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
             "CI_COMMIT_TAG": "geoagent-prod-v1.0.0", "CI_COMMIT_REF_PROTECTED": "true",
             "CI_COMMIT_SHA": "a" * 40, "CI_PIPELINE_ID": "123"})
    assert result.returncode != 0
    assert b"merged into main" in result.stdout


def test_branch_verify_can_build_without_production_secrets(tmp_path):
    marker = tmp_path / "build"
    docker = tmp_path / "docker"
    docker.write_text(f'#!/bin/sh\n[ "$1" = build ] && touch "{marker}"\nexit 17\n')
    docker.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GEOAGENT_PROD_")}
    result = subprocess.run(["/bin/bash", str(SCRIPT), "verify"], cwd=tmp_path, capture_output=True,
        env={**env, "PATH": f"{tmp_path}:{os.environ['PATH']}", "CI_COMMIT_TAG": "",
             "CI_COMMIT_SHA": "a" * 40, "CI_PIPELINE_ID": "123"})
    assert result.returncode == 17
    assert marker.exists()
