"""Exercise deploy/rollback orchestration without sending writes to a real cluster."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ci/sealos-release.sh"
REPO = "crpi-pkfixdfs4f7qepax.cn-shanghai.personal.cr.aliyuncs.com/stocklink-base/geoagent"
pytestmark = pytest.mark.skipif(os.name == "nt", reason="Linux release flow")


def test_verify_mounts_only_gateway_policy_read_only(tmp_path):
    """The runtime image omits deploy/; policy tests need this one public file."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy/gateway.conf").write_text("# fixture gateway policy\n")
    log = tmp_path / "commands.jsonl"
    docker = tmp_path / "docker"
    docker.write_text(f'''#!{sys.executable}
import json,sys
from pathlib import Path
args=sys.argv[1:]
with open({str(log)!r},"a") as f: f.write(json.dumps(args)+"\\n")
if args[0]=="run":
 for i,arg in enumerate(args):
  if arg=="--mount" and "target=/app/deploy/gateway.conf" in args[i+1]:
   options=dict(part.split("=",1) for part in args[i+1].split(",") if "=" in part)
   assert Path(options["source"]).is_file()
''')
    docker.chmod(0o755)
    result = subprocess.run(["bash", str(SCRIPT), "verify"], cwd=tmp_path, capture_output=True,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
             "CI_COMMIT_SHA": "f" * 40, "CI_PIPELINE_ID": "123",
             "CI_COMMIT_TAG": "", "CI_COMMIT_REF_PROTECTED": "false"})
    assert result.returncode == 0, result.stderr.decode()
    commands = [json.loads(line) for line in log.read_text().splitlines()]
    run = next(command for command in commands if command[0] == "run")
    mounts = [run[index + 1] for index, value in enumerate(run) if value == "--mount"]
    assert mounts == [
        f"type=bind,source={tmp_path}/release,target=/reports",
        f"type=bind,source={tmp_path}/tests,target=/app/tests,readonly",
        f"type=bind,source={tmp_path}/deploy/gateway.conf,target=/app/deploy/gateway.conf,readonly",
    ]


@pytest.mark.parametrize("action,rollout_ok", [("deploy", True), ("deploy", False), ("rollback", True)])
def test_release_record_patch_and_health_check(tmp_path, action, rollout_ok):
    release = tmp_path / "release"
    release.mkdir()
    data = b"fixture image archive"
    (release / "candidate.tar.gz").write_bytes(data)
    (release / "candidate.sha256").write_text(hashlib.sha256(data).hexdigest() + "  candidate.tar.gz\n")
    (release / "candidate.id").write_text("sha256:" + "c" * 64)
    log = tmp_path / "commands.jsonl"
    docker = tmp_path / "docker"
    docker.write_text(f'''#!{sys.executable}
import json,sys
args=sys.argv[1:]
with open({str(log)!r},"a") as f: f.write(json.dumps(args)+"\\n")
repo={REPO!r}
if args[0]=="image":
 print(repo+"@sha256:"+"b"*64 if "RepoDigests" in args[-1] else "sha256:"+"c"*64)
elif args[0]=="run":
 if any(a.startswith("geoagent-ci:") for a in args): sys.exit(0)
 i=next(i for i,a in enumerate(args) if a.startswith(repo+"@"))
 k=args[i+1:]
 if "config" in k: print("https://gzg.sealos.run:6443")
 elif "get" in k and "deployment" in k:
  field=k[-1]
  if "owner" in field: print("123" if {action!r}=="rollback" else "122")
  elif "sequence" in field: print("123" if {action!r}=="rollback" else "122")
  elif "resourceVersion" in field: print("42")
  elif ".name" in field and "range" in field: print("gateway\\ngeoagent\\nproxy-tunnel")
  elif ".image" in field: print(repo+"@sha256:"+("b" if {action!r}=="rollback" else "a")*64)
  elif "annotations" in field: print('{{"existing":"preserve"}}')
  else: print("deployment.apps/geoagent")
 elif "get" in k and "configmap" in k:
  if "previous" in k[-1]: print(repo+"@sha256:"+"a"*64)
  elif "image" in k[-1]: print(repo+"@sha256:"+"b"*64)
 elif "rollout" in k: sys.exit({0 if rollout_ok else 1})
 elif "exec" in k: print("/app/data/release-backups/123")
''')
    docker.chmod(0o755)
    for command, output in [("git", ""), ("curl", "401")]:
        stub = tmp_path / command
        stub.write_text(f"#!/bin/sh\nprintf '%s' '{output}'\n")
        stub.chmod(0o755)
    # Backup payload is read from cwd by the release script.
    (tmp_path / "scripts/ci").mkdir(parents=True)
    (tmp_path / "scripts/ci/backup.py").write_text("# fixture")
    result = subprocess.run(["bash", str(SCRIPT), action], cwd=tmp_path, capture_output=True,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
             "CI_COMMIT_SHA": "f" * 40, "CI_PIPELINE_ID": "123",
             "CI_COMMIT_TAG": "geoagent-prod-v1.0.4", "CI_COMMIT_REF_PROTECTED": "true",
             "GEOAGENT_PROD_KUBECONFIG": str(tmp_path / "config"),
             "GEOAGENT_PROD_ACR_USER": "test", "GEOAGENT_PROD_ACR_PASSWORD": "test"})
    assert (result.returncode == 0) == rollout_ok, result.stderr.decode()
    commands = [json.loads(line) for line in log.read_text().splitlines()]
    assert not any(c[0] == "build" for c in commands)
    patches = [c for c in commands if "patch" in c]
    assert len(patches) == 1  # Failed health checks do not silently alter data or rollback.
    patch = json.loads(patches[0][-1])
    assert patch[0] == {"op": "test", "path": "/metadata/resourceVersion", "value": "42"}
    assert patch[1]["path"] == "/spec/template/spec/containers/1/image"
    assert patch[1]["value"] == REPO + "@sha256:" + ("a" if action == "rollback" else "b") * 64
    if action == "deploy":
        assert any("create" in c and "configmap" in c for c in commands)
    assert f"status={'passed' if rollout_ok else 'failed'}" in (release / "geoagent-release.txt").read_text()
