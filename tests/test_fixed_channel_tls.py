import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace


COLLECTORS = Path(__file__).resolve().parents[1] / "collectors"
sys.path.insert(0, str(COLLECTORS))

import fixed_channel_common as fixed  # noqa: E402


def test_russian_ca_pin_is_the_reviewed_intermediate():
    certificate = fixed.RUSSIAN_TRUSTED_SUB_CA.read_bytes()
    assert hashlib.sha256(certificate).hexdigest() == (
        "6f9d829c8e6712444fce3624658d8788672849c5d5b7b53fd9cf7e83eac4193e"
    )


def test_fetch_passes_source_scoped_ca_to_curl(monkeypatch, tmp_path):
    certificate = tmp_path / "ca.crt"
    certificate.write_bytes(b"test certificate")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return SimpleNamespace(returncode=0, stdout=b"payload", stderr=b"")

    monkeypatch.setattr(fixed.subprocess, "run", fake_run)
    assert fixed.fetch("https://example.test/data", ca_cert=certificate) == b"payload"
    assert captured["command"][0] in {"curl", "curl.exe"}
    assert captured["command"][captured["command"].index("--cacert") + 1] == str(certificate)


def test_fetch_rejects_missing_source_ca_before_network(tmp_path):
    missing = tmp_path / "missing.crt"
    try:
        fixed.fetch("https://example.test/data", ca_cert=missing)
    except FileNotFoundError as exc:
        assert str(missing) in str(exc)
    else:
        raise AssertionError("missing source CA must fail closed")
