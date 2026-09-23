import json

from scripts.audit_repository import audit_repository


def test_audit_reports_sensitive_material_without_value(tmp_path):
    secret = "super-" + "secret-value"
    (tmp_path / "bad.env").write_text("API_" + "KEY=" + secret, encoding="utf-8")
    findings = audit_repository(tmp_path)
    assert findings[0]["path"] == "bad.env"
    assert secret not in json.dumps(findings)


def test_audit_reports_personal_absolute_path(tmp_path):
    (tmp_path / "config.py").write_text("ROOT = 'C:/" + "Users/person/WorkBuddy/x'", encoding="utf-8")
    assert any(x["kind"] == "personal_path" for x in audit_repository(tmp_path))


def test_audit_reports_large_file(tmp_path):
    (tmp_path / "large.dat").write_bytes(b"x" * 2048)
    assert any(x["kind"] == "large_file" for x in audit_repository(tmp_path, max_file_mb=0.001))


def test_audit_skips_local_root_env_because_git_ignores_it(tmp_path):
    (tmp_path / ".env").write_text("API_" + "KEY=local-runtime-secret", encoding="utf-8")
    assert audit_repository(tmp_path) == []


def test_audit_scans_committed_demo_output(tmp_path):
    sample = tmp_path / "examples" / "demo-data" / "sample" / "output"
    sample.mkdir(parents=True)
    (sample / "record.json").write_text("C:/" + "Users/person/private", encoding="utf-8")
    assert any(item["kind"] == "personal_path" for item in audit_repository(tmp_path))
