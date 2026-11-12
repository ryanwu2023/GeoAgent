"""taco: a rate-limited live source falls back to verified history with a scalar status value."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("taco_monitor_under_test", ROOT / "collectors" / "taco-monitor" / "taco_monitor.py")
taco_monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(taco_monitor)


def test_multi_field_history_restores_with_scalar_last_value(tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    rows = [{"date": "2026-10-24", "value": {"n_total": 3, "n_tanker": 1, "capacity": 0}},
            {"date": "2026-10-25", "value": {"n_total": 2, "n_tanker": 1, "capacity": 0}}]
    (history / "TRHBCCCD.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    statuses = [{"id": "TRHBCCCD", "status": "INVALID", "error": "arcgis-error:{'code': 429}"}]

    restored = taco_monitor.restore_recent_history(statuses, str(tmp_path))

    st = restored[0]
    assert st["status"] == "LOADED_CACHE" and st["live_status"] == "INVALID"
    assert st["last_date"] == "2026-10-25" and st["last_value"] == 2
    assert st["last_record"] == {"n_total": 2, "n_tanker": 1, "capacity": 0}
    assert st["_data"]["2026-10-24"]["n_total"] == 3
