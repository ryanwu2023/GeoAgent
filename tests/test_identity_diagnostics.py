"""Production diagnosis must not expose identity material or run on arbitrary refs."""
import asyncio
import json
import os
from pathlib import Path
import subprocess

import httpx
import pytest

from scripts.ci import diagnose_identity


@pytest.mark.parametrize("branch,protected", [("feature", "true"), ("main", "false"), ("", "true")])
@pytest.mark.skipif(os.name == "nt", reason="Linux runner script")
def test_diagnostic_ref_gate(tmp_path, branch, protected):
    marker = tmp_path / "executed"
    stub = tmp_path / "docker"
    stub.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    stub.chmod(0o755)
    script = Path(__file__).resolve().parents[1] / "scripts/ci/diagnose_identity.sh"
    result = subprocess.run(["bash", str(script)], env={**os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}", "CI_COMMIT_BRANCH": branch,
        "CI_COMMIT_REF_PROTECTED": protected}, capture_output=True)
    assert result.returncode != 0
    assert not marker.exists()


def test_diagnostic_never_emits_identity_or_exception_secrets(monkeypatch, capsys):
    monkeypatch.setattr(diagnose_identity, "integration_settings", lambda: {"origin": "https://example.test"})
    monkeypatch.setattr(diagnose_identity.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("1.2.3.4", 443))])

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url):
            if "127.0.0.1" in url:
                return httpx.Response(200, json={"token": "SECRET_JWT", "user": {"id": "PRIVATE_USER"}})
            raise httpx.ConnectTimeout("SECRET_EXCEPTION")

    monkeypatch.setattr(diagnose_identity.httpx, "AsyncClient", Client)
    asyncio.run(diagnose_identity.main())
    output = capsys.readouterr().out
    assert "SECRET" not in output and "PRIVATE_USER" not in output
    rows = [json.loads(line) for line in output.splitlines()]
    assert rows[1]["error_type"] == "ConnectTimeout"
    assert rows[2]["identity_present"] is True
