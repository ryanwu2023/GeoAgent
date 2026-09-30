import json

from backend.mcp_news import public_config, save_config, store_news


def test_mcp_config_is_persistent_but_secret_is_never_returned(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path / "data"))
    result = save_config("news", "https://example.org/mcp", "Bearer secret", True)
    assert result == {"name": "news", "url": "https://example.org/mcp", "configured": True, "verify_tls": True}
    assert "authorization" not in result
    saved = json.loads((tmp_path / "data" / "mcp-config.json").read_text(encoding="utf-8"))
    assert saved["authorization"] == "Bearer secret"
    assert "secret" not in json.dumps(public_config(saved))


def test_mcp_hits_become_pending_geo_records_and_accumulate(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLECTOR_DATA_ROOT", str(tmp_path / "runtime"))
    config = {"name": "haifutong"}
    envelope = {
        "ok": True,
        "data": {
            "hits": [
                {
                    "document_id": 7,
                    "title": "Iran talks continue",
                    "url": "https://example.org/news/7",
                    "published_at": "2026-09-30",
                    "source_name": "example",
                    "source_type": "mainstream_news",
                    "excerpt": "Third-party news text",
                }
            ]
        },
    }
    target, count = store_news(config, "Iran", "usiran", "diplomacy", envelope)
    assert count == 1
    record = json.loads(target.read_text(encoding="utf-8"))["records"][0]
    assert record["verification"] == "MCP 导入，待核验"
    assert record["topics"] == ["usiran"]
    assert record["dimensions"] == {"usiran": "diplomacy"}

    envelope["data"]["hits"][0].update(document_id=8, title="Second", url="https://example.org/news/8")
    store_news(config, "Iran", "usiran", "diplomacy", envelope)
    assert len(json.loads(target.read_text(encoding="utf-8"))["records"]) == 2

