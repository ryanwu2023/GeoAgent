"""Exercise release boundaries without Docker, registry, or Kubernetes access."""
import os
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ci/sealos-release.sh"
pytestmark = pytest.mark.skipif(os.name == "nt", reason="Release runner uses Linux Bash; verify in Docker on Windows")


@pytest.mark.parametrize("tag,protected", [
    ("geoagent-test-v1.0.0", "true"),
    ("stocklink-prod-v1.0.0", "true"),
    ("geoagent-prod-v1.0.0-rc1", "true"),
    ("geoagent-prod-v1.0.0", "false"),
    ("", "true"),
])
def test_nonproduction_refs_cannot_execute_commands(tmp_path, tag, protected):
    marker = tmp_path / "executed"
    for command in ["git", "docker", "kubectl"]:
        stub = tmp_path / command
        stub.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 0\n')
        stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
           "CI_COMMIT_TAG": tag, "CI_COMMIT_REF_PROTECTED": protected}
    result = subprocess.run(["/bin/bash", str(SCRIPT), "release"], env=env, capture_output=True)
    assert result.returncode != 0
    assert not marker.exists()


def test_unmerged_production_commit_cannot_build(tmp_path):
    git = tmp_path / "git"
    git.write_text('#!/bin/sh\n[ "$1" = fetch ] && exit 0\nexit 1\n')
    git.chmod(0o755)
    result = subprocess.run(["/bin/bash", str(SCRIPT), "verify"], capture_output=True,
                            env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
                                 "CI_COMMIT_TAG": "geoagent-prod-v1.0.0",
                                 "CI_COMMIT_REF_PROTECTED": "true", "CI_COMMIT_SHA": "abc"})
    assert result.returncode != 0
    assert b"merged into main" in result.stdout
