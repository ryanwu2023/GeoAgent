import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

from scripts.ci.backup import backup

POLICY = Path(__file__).resolve().parents[1] / "scripts/ci/release-policy.sh"


def test_backup_is_consistent_and_does_not_overwrite(tmp_path):
    with sqlite3.connect(tmp_path / "research.sqlite3") as db:
        db.execute("CREATE TABLE sample(value TEXT)")
        db.execute("INSERT INTO sample VALUES ('preserved')")
    target = backup(tmp_path, "123")
    with sqlite3.connect(target / "research.sqlite3") as db:
        assert db.execute("SELECT value FROM sample").fetchone()[0] == "preserved"
    with pytest.raises(FileExistsError):
        backup(tmp_path, "123")


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
