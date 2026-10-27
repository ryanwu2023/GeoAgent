import json

from backend.adapters import load_bundle
from backend.research import metrics


def test_ukraine_aid_dual_output_imports_topic_scoped_yields(tmp_path):
    output = tmp_path / "ukraine-aid-monitor" / "output"
    output.mkdir(parents=True)
    (output / "ALL-records.jsonl").write_text(
        json.dumps({
            "id": "aid-news",
            "title": "Ukraine aid delivery",
            "summary": "An aid package was delivered to Ukraine.",
            "link": "https://example.test/aid",
            "source_id": "official-aid",
            "published": "2026-10-23T00:00:00Z",
        }) + "\n",
        encoding="utf-8",
    )
    records = []
    observations = []
    for series, name, unit, source_ids in [
        ("ua_aid_de10y", "德国10年期国债收益率", "%", ["de", "fred-de"]),
        ("ua_aid_fr10y", "法国10年期国债收益率", "%", ["fr", "fred-fr"]),
        ("ua_aid_eu10y", "欧元区10年期收益率", "%", ["eu", "fred-eu"]),
        ("ua_aid_fr_de_10y_spread", "法德10年期国债利差", "bp", ["fr", "de"]),
    ]:
        for source_id in source_ids:
            if source_id not in {item["id"] for item in records}:
                records.append({
                    "id": source_id,
                    "title": source_id,
                    "summary": "结构化收益率来源",
                    "link": f"https://example.test/{source_id}",
                    "source_id": source_id,
                    "topics": ["ukraine"],
                    "dimensions": {"ukraine": "aid"},
                })
        observations.append({
            "id": series,
            "series": series,
            "name": name,
            "unit": unit,
            "frequency": "日频",
            "scope": "俄乌冲突资产传导",
            "topic": "ukraine",
            "dimension": "aid",
            "bucket": "rates",
            "shared_market": False,
            "method": "测试",
            "boundary": "不可单独归因",
            "history": [
                {"date": "2026-10-22", "value": 3.0},
                {"date": "2026-10-23", "value": 3.1},
            ],
            "source_ids": source_ids,
        })
    (output / "geo-v6.json").write_text(json.dumps({
        "schema_version": "geo.v6/1",
        "name": "欧洲收益率",
        "as_of": "2026-10-24T00:00:00Z",
        "records": records,
        "observations": observations,
        "source_status": [],
    }), encoding="utf-8")
    (output / "_source_status.json").write_text(json.dumps({
        "run_at": "2026-10-24T00:00:00Z",
        "sources": [{"id": "tradingview-de10y", "publisher": "TradingView", "state": "ok"}],
    }), encoding="utf-8")

    bundle = load_bundle(tmp_path)
    ukraine = {item["series"] for item in metrics(bundle, "ukraine")}
    usiran = {item["series"] for item in metrics(bundle, "usiran")}
    expected = {
        "ua_aid_de10y", "ua_aid_fr10y", "ua_aid_eu10y", "ua_aid_fr_de_10y_spread"
    }
    assert expected <= ukraine
    assert not expected & usiran
    assert any(item["monitor"] == "ukraine-aid-monitor" and item["state"] == "ok" for item in bundle["sources"])

