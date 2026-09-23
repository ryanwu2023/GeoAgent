from scripts.integrate_collectors import copy_collector


def test_copy_collector_separates_source_and_runtime_output(tmp_path):
    source = tmp_path / "source" / "sample-monitor"
    (source / "output").mkdir(parents=True)
    (source / "__pycache__").mkdir()
    (source / ".probe").mkdir()
    (source / "monitor.py").write_text("print('ok')", encoding="utf-8")
    (source / "output" / "ALL-records.jsonl").write_text("{}\n", encoding="utf-8")
    (source / "__pycache__" / "monitor.pyc").write_bytes(b"cache")
    (source / ".probe" / "debug.json").write_text("{}", encoding="utf-8")

    result = copy_collector(
        source,
        tmp_path / "collectors" / "sample-monitor",
        tmp_path / "data" / "sample-monitor",
    )

    assert (tmp_path / "collectors/sample-monitor/monitor.py").exists()
    assert not (tmp_path / "collectors/sample-monitor/output/ALL-records.jsonl").exists()
    assert (tmp_path / "data/sample-monitor/output/ALL-records.jsonl").exists()
    assert not (tmp_path / "collectors/sample-monitor/__pycache__").exists()
    assert not (tmp_path / "collectors/sample-monitor/.probe").exists()
    assert result["source_files"] == 1
