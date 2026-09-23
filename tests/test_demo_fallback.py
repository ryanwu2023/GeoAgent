import json

from backend.paths import collector_read_root
from scripts.build_demo_data import build_demo


def test_empty_runtime_root_uses_demo_data(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLECTOR_DATA_ROOT", str(tmp_path / "empty runtime"))
    monkeypatch.delenv("CRAWLER_ROOT", raising=False)
    assert collector_read_root().name == "demo-data"


def test_build_demo_bounds_jsonl_and_removes_personal_path(tmp_path):
    source = tmp_path / "source"
    output = source / "ua-front-monitor" / "output"
    output.mkdir(parents=True)
    lines = [json.dumps({"id": str(i), "title": f"item {i}", "path": "C:/Users/person/WorkBuddy/raw"}) for i in range(5)]
    (output / "ALL-records.jsonl").write_text("\n".join(lines), encoding="utf-8")
    destination = tmp_path / "demo"
    manifest = build_demo(source, destination, max_records=2)
    copied = (destination / "ua-front-monitor/output/ALL-records.jsonl").read_text(encoding="utf-8")
    assert len(copied.splitlines()) == 2
    assert "C:/Users/" not in copied
    assert manifest["record_count"] == 2
