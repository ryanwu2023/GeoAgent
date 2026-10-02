import json
from pathlib import Path


def test_market_output_root_honors_collector_staging(monkeypatch):
    from scripts import supplement_market

    staging = Path("/tmp/geoagent-staging/market/output")
    monkeypatch.setenv("COLLECTOR_OUTPUT_ROOT", str(staging))
    assert supplement_market.output_root() == staging


def test_market_output_root_defaults_to_project_data(monkeypatch, tmp_path):
    from scripts import supplement_market

    monkeypatch.delenv("COLLECTOR_OUTPUT_ROOT", raising=False)
    monkeypatch.setattr(
        supplement_market,
        "collector_data_root",
        lambda: tmp_path / "collector-output",
    )
    assert supplement_market.output_root() == tmp_path / "collector-output" / "market-context-monitor" / "output"


def test_market_source_artifacts_match_monitor_contract(monkeypatch, tmp_path):
    from scripts import supplement_market

    monkeypatch.setattr(supplement_market, "ROOT", tmp_path)
    results = [
        ({"id": "hy_oas"}, {"series": "hy_oas", "history": [{"date": "2026-10-01", "value": 1.0}]}),
        ({"id": "ig_oas"}, {"series": "ig_oas", "history": []}),
        ({"id": "breakeven10_fred"}, {"series": "breakeven10_fred", "history": [{"date": "2026-10-01", "value": 2.0}]}),
        ({"id": "eurusd"}, {"series": "eurusd", "history": [{"date": "2026-10-01", "value": 1.1}]}),
    ]
    supplement_market.write_source_artifacts(results)

    fetch_stats = json.loads((tmp_path / "_fetch_stats.json").read_text())
    status = json.loads((tmp_path / "_source_status.json").read_text())
    health = json.loads((tmp_path / "_source_health.json").read_text())
    assert fetch_stats["src"]["hy_oas"]["ok"] is True
    assert status["sources"][0]["state"] == "ok"
    assert health["eurusd"]["consec_fail"] == 0
