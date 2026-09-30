from pathlib import Path


def test_default_paths_are_inside_repository(monkeypatch):
    monkeypatch.delenv("CRAWLER_ROOT", raising=False)
    monkeypatch.delenv("COLLECTOR_DATA_ROOT", raising=False)
    from backend.paths import collector_data_root, collector_source_root, project_root

    assert collector_source_root() == project_root() / "collectors"
    assert collector_data_root() == project_root() / "data" / "collector-output"


def test_crawler_root_override_is_preserved(monkeypatch, tmp_path):
    monkeypatch.setenv("CRAWLER_ROOT", str(tmp_path / "legacy data"))
    from backend.paths import collector_read_root

    assert collector_read_root() == tmp_path / "legacy data"


def test_knowledge_root_is_repository_local(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_ROOT", raising=False)
    from backend.paths import knowledge_root, project_root

    assert knowledge_root() == project_root() / "knowledge"


def test_latest_records_are_recognized_as_runtime_data(monkeypatch, tmp_path):
    from backend.paths import collector_read_root

    runtime = tmp_path / "runtime"
    output = runtime / "sample-monitor" / "output"
    output.mkdir(parents=True)
    (output / "LATEST-records.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("COLLECTOR_DATA_ROOT", str(runtime))
    monkeypatch.delenv("CRAWLER_ROOT", raising=False)
    assert collector_read_root() == runtime


def test_direct_collector_outputs_are_incrementally_synced(tmp_path):
    from backend.paths import sync_local_collector_outputs

    source_root = tmp_path / "collectors"
    data_root = tmp_path / "runtime"
    source = source_root / "sample-monitor" / "output" / "LATEST-records.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text('{"id":"fresh"}\n', encoding="utf-8")

    first = sync_local_collector_outputs(source_root, data_root)
    target = data_root / "sample-monitor" / "output" / source.name
    assert first["projects"] == 1 and first["files"] == 1
    assert target.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")

    second = sync_local_collector_outputs(source_root, data_root)
    assert second == {"projects": 0, "files": 0, "bytes": 0}

    source.write_text('{"id":"newer"}\n', encoding="utf-8")
    third = sync_local_collector_outputs(source_root, data_root)
    assert third["files"] == 1
    assert "newer" in target.read_text(encoding="utf-8")
