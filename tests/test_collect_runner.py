import json
from pathlib import Path

import pytest

from backend.collector_ledger import service_lock
from scripts.collect import run_collectors, run_one, select_collectors, serialize_for_console


def _registry():
    return [
        {"id": "first", "topics": ["usiran"], "enabled": True},
        {"id": "second", "topics": ["ru_ua"], "enabled": True},
        {"id": "disabled", "topics": ["ru_ua"], "enabled": False},
    ]


def test_runner_continues_after_failure(monkeypatch):
    codes = iter([1, 0])
    monkeypatch.setattr(
        "scripts.collect.run_one",
        lambda *args, **kwargs: {"returncode": next(codes)},
    )
    results = run_collectors(
        ["first", "second"], topic=None, timeout=10, registry=_registry()
    )
    assert [item["returncode"] for item in results] == [1, 0]


def test_topic_filter_only_selects_matching_collectors():
    selected = select_collectors(_registry(), ids=[], topic="ru_ua")
    assert [item["id"] for item in selected] == ["second"]


def test_exclude_ids_are_applied_after_registry_and_topic_filters():
    selected = select_collectors(_registry(), ids=[], topic=None, exclude_ids=["second"])
    assert [item["id"] for item in selected] == ["first"]


def test_run_collectors_passes_exclude_ids_to_selection(monkeypatch):
    monkeypatch.setattr(
        "scripts.collect.run_one",
        lambda item, **kwargs: {"id": item["id"], "returncode": 0},
    )
    results = run_collectors(
        [], topic=None, timeout=10, registry=_registry(), exclude_ids=["second"]
    )
    assert [item["id"] for item in results] == ["first"]


def test_console_json_is_ascii_safe_for_windows_codepages():
    rendered = serialize_for_console([{"message": "✅ 完成"}])
    rendered.encode("ascii")


def _write_collector_script(root: Path, collector_id: str, body: str) -> dict:
    collector_dir = root / collector_id
    collector_dir.mkdir(parents=True)
    entrypoint = collector_dir / "collector.py"
    entrypoint.write_text(body, encoding="utf-8")
    return {"id": collector_id, "entrypoint": "collector.py"}


def test_run_one_retries_with_backoff_and_publishes_staged_output(tmp_path, monkeypatch):
    source_root = tmp_path / "collectors"
    data_root = tmp_path / "data"
    old_output = data_root / "demo" / "output"
    old_output.mkdir(parents=True)
    (old_output / "previous.json").write_text('{"version": 1}', encoding="utf-8")
    item = _write_collector_script(
        source_root,
        "demo",
        """
import os
import sys
from pathlib import Path
output = Path(os.environ['COLLECTOR_OUTPUT_ROOT'])
output.mkdir(parents=True, exist_ok=True)
marker = output.parent / 'failed-once'
if not marker.exists():
    marker.write_text('1')
    sys.exit(7)
(output / 'current.json').write_text('{\"version\": 2}', encoding='utf-8')
""",
    )
    monkeypatch.setattr("scripts.collect.collector_source_root", lambda: source_root)
    monkeypatch.setattr("scripts.collect.collector_data_root", lambda: data_root)
    sleeps = []

    result = run_one(
        item,
        timeout=10,
        max_attempts=2,
        backoff_factor=0.25,
        sleep=sleeps.append,
        staging_root=tmp_path / "staging",
    )

    assert result["returncode"] == 0
    assert result["attempts"] == 2
    assert result["published"] is True
    assert result["runtime_files"] == ["current.json"]
    assert sleeps == [0.25]
    assert json.loads((old_output / "current.json").read_text()) == {"version": 2}
    assert not (old_output / "previous.json").exists()


def test_run_one_failure_keeps_last_published_output_and_cleans_stage(tmp_path, monkeypatch):
    source_root = tmp_path / "collectors"
    data_root = tmp_path / "data"
    live_output = data_root / "demo" / "output"
    live_output.mkdir(parents=True)
    (live_output / "previous.json").write_text('{"version": 1}', encoding="utf-8")
    item = _write_collector_script(
        source_root,
        "demo",
        """
import os
import sys
from pathlib import Path
output = Path(os.environ['COLLECTOR_OUTPUT_ROOT'])
output.mkdir(parents=True, exist_ok=True)
(output / 'partial.json').write_text('{\"bad\": true}', encoding='utf-8')
sys.exit(9)
""",
    )
    monkeypatch.setattr("scripts.collect.collector_source_root", lambda: source_root)
    monkeypatch.setattr("scripts.collect.collector_data_root", lambda: data_root)

    stage = tmp_path / "staging"
    result = run_one(item, timeout=10, max_attempts=2, backoff_factor=0, sleep=lambda _: None, staging_root=stage)

    assert result["returncode"] == 9
    assert result["attempts"] == 2
    assert result["published"] is False
    assert (live_output / "previous.json").read_text() == '{"version": 1}'
    assert not (live_output / "partial.json").exists()
    assert not (stage / "demo").exists()


def test_run_one_success_without_output_does_not_erase_last_output(tmp_path, monkeypatch):
    source_root = tmp_path / "collectors"
    data_root = tmp_path / "data"
    live_output = data_root / "demo" / "output"
    live_output.mkdir(parents=True)
    (live_output / "previous.json").write_text('{"version": 1}', encoding="utf-8")
    item = _write_collector_script(source_root, "demo", "import sys; sys.exit(0)\n")
    monkeypatch.setattr("scripts.collect.collector_source_root", lambda: source_root)
    monkeypatch.setattr("scripts.collect.collector_data_root", lambda: data_root)

    result = run_one(item, timeout=10, staging_root=tmp_path / "staging")

    assert result["returncode"] == 0
    assert result["published"] is False
    assert (live_output / "previous.json").read_text() == '{"version": 1}'


def test_run_one_rejects_concurrent_collector_lock(tmp_path, monkeypatch):
    source_root = tmp_path / "collectors"
    data_root = tmp_path / "data"
    item = _write_collector_script(source_root, "demo", "import sys; sys.exit(0)\n")
    monkeypatch.setattr("scripts.collect.collector_source_root", lambda: source_root)
    monkeypatch.setattr("scripts.collect.collector_data_root", lambda: data_root)
    lock = data_root / ".collector-locks" / "demo.lock"

    with service_lock(lock):
        with pytest.raises(RuntimeError, match="lock is held"):
            run_one(item, timeout=10)
