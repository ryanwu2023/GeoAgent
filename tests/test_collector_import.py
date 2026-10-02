import json

import pytest

from backend.collector_import import import_collector_output


def test_import_collector_output_rejects_missing_published_tree(tmp_path):
    with pytest.raises(ValueError, match="没有可导入"):
        import_collector_output(tmp_path)


def test_import_collector_output_creates_snapshot_from_published_tree(tmp_path, monkeypatch):
    output = tmp_path / "demo-monitor" / "output"
    output.mkdir(parents=True)
    (output / "ALL-records.jsonl").write_text(
        json.dumps(
            {
                "title": "Iran briefing",
                "summary": "A monitored public record.",
                "link": "https://example.org/iran-briefing",
                "published": "2026-10-02T09:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("backend.collector_import.start_translation", lambda: None)

    result = import_collector_output(tmp_path)

    assert result["created"] is True
    assert result["snapshot_id"]
    assert result["records"] == 1
    assert (tmp_path / "data" / "research.sqlite3").is_file()
    assert (tmp_path / "data" / "archive").is_dir()
