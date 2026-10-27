import importlib.util
import json
import math
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "collectors" / "ukraine-aid-monitor" / "european_yields.py"


def load_module():
    spec = importlib.util.spec_from_file_location("european_yields_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_same_day_spread_uses_only_overlapping_valid_dates():
    module = load_module()
    france = [
        {"date": "2026-10-23", "value": 4.4},
        {"date": "2026-10-24", "value": 4.5},
        {"date": "2026-10-25", "value": None},
    ]
    germany = [
        {"date": "2026-10-22", "value": 3.3},
        {"date": "2026-10-23", "value": 3.4},
        {"date": "2026-10-24", "value": 3.45},
    ]
    assert module.same_day_spread(france, germany) == [
        {"date": "2026-10-23", "value": 100.0},
        {"date": "2026-10-24", "value": 105.0},
    ]


def test_completed_daily_history_excludes_today_and_invalid_values():
    module = load_module()
    rows = [
        {"date": "2026-10-25", "value": 3.4},
        {"date": "2026-10-26", "value": None},
        {"date": "2026-10-27", "value": 3.6},
        {"date": "2026-10-24", "value": math.inf},
    ]
    assert module.completed_daily_history(rows, date(2026, 10, 27)) == [
        {"date": "2026-10-25", "value": 3.4}
    ]


def test_build_payload_publishes_daily_series_and_keeps_fred_as_validation(monkeypatch, tmp_path):
    module = load_module()
    daily = {
        "TVC:DE10Y": [{"date": "2026-10-23", "value": 3.4}],
        "TVC:FR10Y": [{"date": "2026-10-23", "value": 4.4}],
        "TVC:EU10Y": [{"date": "2026-10-23", "value": 3.8}],
    }
    monthly = {
        "IRLTLT01DEM156N": [{"date": "2026-09-01", "value": 3.3}],
        "IRLTLT01FRM156N": [{"date": "2026-09-01", "value": 4.3}],
        "IRLTLT01EZM156N": [{"date": "2026-09-01", "value": 3.7}],
    }
    monkeypatch.setattr(
        module,
        "fetch_tradingview_history",
        lambda symbol, bars=800: (json.dumps(daily[symbol]).encode(), daily[symbol]),
    )
    monkeypatch.setattr(
        module,
        "fetch_fred_monthly",
        lambda series_id: (json.dumps(monthly[series_id]).encode(), monthly[series_id]),
    )
    payload = module.build_payload(tmp_path, today=date(2026, 10, 27))
    observations = {item["id"]: item for item in payload["observations"]}
    assert set(observations) == {
        "ua_aid_de10y", "ua_aid_fr10y", "ua_aid_eu10y", "ua_aid_fr_de_10y_spread"
    }
    assert observations["ua_aid_fr_de_10y_spread"]["history"] == [
        {"date": "2026-10-23", "value": 100.0}
    ]
    assert all(item["topic"] == "ukraine" for item in observations.values())
    assert all(item["dimension"] == "aid" for item in observations.values())
    assert all(item["bucket"] == "rates" for item in observations.values())
    assert all(item.get("shared_market") is False for item in observations.values())
    assert observations["ua_aid_de10y"]["history"] == daily["TVC:DE10Y"]
    assert "validation" in observations["ua_aid_de10y"]
    assert observations["ua_aid_de10y"]["validation"]["frequency"] == "月度"


def test_partial_tradingview_failure_keeps_other_series_and_omits_spread(monkeypatch, tmp_path):
    module = load_module()

    def tradingview(symbol, bars=800):
        if symbol == "TVC:FR10Y":
            raise RuntimeError("source unavailable")
        rows = [{"date": "2026-10-23", "value": 3.4}]
        return b"daily", rows

    monkeypatch.setattr(module, "fetch_tradingview_history", tradingview)
    monkeypatch.setattr(module, "fetch_fred_monthly", lambda series_id: (b"fred", []))
    payload = module.build_payload(tmp_path, today=date(2026, 10, 27))
    ids = {item["id"] for item in payload["observations"]}
    assert "ua_aid_de10y" in ids
    assert "ua_aid_eu10y" in ids
    assert "ua_aid_fr10y" not in ids
    assert "ua_aid_fr_de_10y_spread" not in ids
    assert payload["collection_status"] == "partial"
    failed = [item for item in payload["source_status"] if item["state"] == "failed"]
    assert any(item["source"] == "tradingview-fr10y" for item in failed)

