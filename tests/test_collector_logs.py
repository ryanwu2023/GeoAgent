from pathlib import Path

from backend.collector_logs import read_log_tail, redact


def test_log_tail_falls_back_to_ledger_event(tmp_path):
    from backend.collector_ledger import CollectorLedger

    root = tmp_path / "collection-runs"
    root.mkdir()
    ledger = CollectorLedger()
    ledger.run_started(run_id="run-1", run_kind="test", started_at="now", topic=None, timeout_sec=1)
    ledger.event(run_id="run-1", event_type="collector_finished", collector_id="demo", payload={"stdout_tail": "hello"})

    payload = read_log_tail(root, "run-1", "demo", stream="stdout", tail=10)

    assert payload["available"] is True
    assert payload["source"] == "ledger-event"
    assert payload["content"] == "hello"


def test_redact_masks_bearer_and_quoted_json_credentials():
    value = 'Authorization: Bearer abc.def "access_token": "secret-value", password=hunter2'

    safe = redact(value)

    assert "abc.def" not in safe
    assert "secret-value" not in safe
    assert "hunter2" not in safe
    assert safe.count("[REDACTED]") == 3
