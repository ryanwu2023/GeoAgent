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


@pytest.mark.parametrize("valid_checksum", [True, False])
def test_release_bootstraps_kubectl_before_cluster_access(tmp_path, valid_checksum):
    """A runner without kubectl downloads it; corrupt downloads never execute."""
    import hashlib
    marker = tmp_path / "kubectl-ran"
    payload = f'#!/bin/sh\ntouch "{marker}"\necho wrong-cluster\n'
    checksum = hashlib.sha256(payload.encode()).hexdigest() if valid_checksum else "0" * 64
    stubs = {
        "git": "#!/bin/sh\nexit 0\n",
        "curl": '#!/bin/bash\nwhile [[ $1 != -o ]]; do shift; done\nout=$2\nurl=$3\n'
                + f"if [[ $url == *.sha256 ]]; then printf '%s' '{checksum}' > \"$out\"; "
                + f"else cat > \"$out\" <<'PAYLOAD'\n{payload}PAYLOAD\nfi\n",
    }
    for name, body in stubs.items():
        target = tmp_path / name
        target.write_text(body)
        target.chmod(0o755)
    result = subprocess.run(["/bin/bash", str(SCRIPT), "release"], capture_output=True,
                            env={**os.environ, "PATH": f"{tmp_path}:/usr/bin:/bin",
                                 "CI_COMMIT_TAG": "geoagent-prod-v1.0.0",
                                 "CI_COMMIT_REF_PROTECTED": "true", "CI_COMMIT_SHA": "abc",
                                 "GEOAGENT_PROD_KUBECONFIG": str(tmp_path / "config"),
                                 "GEOAGENT_PROD_ACR_USER": "test",
                                 "GEOAGENT_PROD_ACR_PASSWORD": "test"})
    assert result.returncode != 0  # Wrong cluster must still stop deployment.
    assert marker.exists() == valid_checksum, result.stderr.decode()
