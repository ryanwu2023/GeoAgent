import os
from pathlib import Path
import subprocess

import pytest

from scripts.ci.backup import backup, postgres_environment
from psycopg.conninfo import conninfo_to_dict

POLICY = Path(__file__).resolve().parents[1] / "scripts/ci/release-policy.sh"


def test_backup_uses_postgres_dump_and_does_not_overwrite(tmp_path, monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "pg_dump":
            Path(args[-1]).write_bytes(b"PGDMP-test")
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr("scripts.ci.backup.subprocess.run", run)
    target = backup(tmp_path, "123")
    assert (target / "database.dump").read_bytes() == b"PGDMP-test"
    assert calls[0][0][0] == "pg_dump"
    assert calls[0][1]["env"]["PGDATABASE"] == conninfo_to_dict(os.environ["GEO_DATABASE_URL"])["dbname"]
    assert calls[1][0][:2] == ["pg_restore", "--list"]
    assert not (target / "database.dump.partial").exists()
    with pytest.raises(FileExistsError):
        backup(tmp_path, "123")


def test_backup_environment_decodes_password_and_preserves_tls(monkeypatch):
    with monkeypatch.context() as pg_env:
        pg_env.setenv("PGHOSTADDR", "203.0.113.1")
        env = postgres_environment("postgresql://geo:p%40ss%3Aword@database:5433/research?sslmode=verify-full")
    assert env["PGDATABASE"] == "research"
    assert env["PGHOST"] == "database" and env["PGPORT"] == "5433"
    assert env["PGPASSWORD"] == "p@ss:word"
    assert env["PGSSLMODE"] == "verify-full"
    assert "PGHOSTADDR" not in env


def test_failed_dump_has_no_success_manifest(tmp_path, monkeypatch):
    def fail(args, **kwargs):
        raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr("scripts.ci.backup.subprocess.run", fail)
    with pytest.raises(RuntimeError, match="PostgreSQL backup failed"):
        backup(tmp_path, "123")
    assert not (tmp_path / "release-backups/123/manifest.json").exists()


def test_active_collection_blocks_backup(tmp_path):
    lock = tmp_path / "collection-runs/.collector-service.lock"
    lock.parent.mkdir()
    lock.touch()
    with pytest.raises(RuntimeError, match="Collector lock"):
        backup(tmp_path, "123")
    assert not (tmp_path / "release-backups").exists()


@pytest.mark.skipif(os.name == "nt", reason="Linux release policy")
@pytest.mark.parametrize("function,args,ok", [
    ("assert_forward_release", ["124", "123"], True),
    ("assert_forward_release", ["123", "123"], False),
    ("assert_forward_release", ["122", "123"], False),
    ("assert_forward_release", ["124", "invalid"], False),
    ("assert_rollback_owner", ["123", "123", "image-a", "image-a"], True),
    ("assert_rollback_owner", ["123", "124", "image-a", "image-a"], False),
    ("assert_rollback_owner", ["123", "123", "image-a", "image-b"], False),
])
def test_release_ownership(function, args, ok):
    result = subprocess.run(["bash", "-c", 'source "$1"; shift; "$@"', "test", str(POLICY), function, *args])
    assert (result.returncode == 0) == ok
