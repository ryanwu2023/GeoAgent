import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from backend.collector_jobs import CollectorBusyError
from backend.geo_mcp import collect
from backend.geo_mcp.contracts import MCPError, Principal

NOW = datetime(2026, 11, 5, 8, 0, tzinfo=timezone.utc)
REAL_VALIDATE = collect._validate


@dataclass
class Job:
    run_id: str
    request_id: str = "req-1"
    submitted_at: str = NOW.isoformat()


class Jobs:
    def __init__(self, busy=False):
        self.calls, self.busy = [], busy

    def submit(self, **kwargs):
        if self.busy:
            raise CollectorBusyError("locked")
        self.calls.append(kwargs)
        return Job(run_id=f"manual-{len(self.calls)}")


@pytest.fixture
def principal(monkeypatch):
    monkeypatch.setenv("GEO_REMOTE_COLLECT_PRINCIPALS", "hft-key")
    monkeypatch.setenv("GEO_REMOTE_COLLECT_MIN_INTERVAL_SEC", "1800")
    monkeypatch.setattr(collect, "_validate", lambda ids: None)
    monkeypatch.setattr(collect, "_running_run_id", lambda: None)
    monkeypatch.setattr(collect, "load_config", lambda: {"exclude_ids": ["eu-gas-storage"]})
    scope = frozenset({"*"})
    return Principal("hft-key", "external-agent", "project", scope, scope, scope, scope)


def test_principal_must_be_allow_listed(principal, monkeypatch):  # RC-02
    monkeypatch.setenv("GEO_REMOTE_COLLECT_PRINCIPALS", "")
    jobs = Jobs()
    with pytest.raises(MCPError, match="GEO_TOOL_NOT_ALLOWED"):
        collect.request_collection(principal, jobs=jobs, now=NOW)
    assert jobs.calls == []


def test_request_submits_partial_import_batch(principal):  # RC-03
    jobs = Jobs()
    result = collect.request_collection(principal, ["b", "a", "a"], "usiran", jobs=jobs, now=NOW)
    assert result["run_id"] == "manual-1" and result["reused"] is False
    assert jobs.calls == [{"ids": ["a", "b"], "topic": "usiran", "auto_import": True,
                           "allow_partial_import": True, "exclude_ids": ["eu-gas-storage"]}]
    state = json.loads(collect._state_path().read_text())
    assert state == {"run_id": "manual-1", "requested_at": NOW.isoformat(), "principal_id": "hft-key"}


def test_running_batch_is_reused(principal, monkeypatch):  # RC-04
    monkeypatch.setattr(collect, "_running_run_id", lambda: "scheduled-9")
    jobs = Jobs()
    assert collect.request_collection(principal, jobs=jobs, now=NOW)["run_id"] == "scheduled-9"
    assert jobs.calls == []
    calls = iter([None, "manual-race"])
    monkeypatch.setattr(collect, "_running_run_id", lambda: next(calls))
    result = collect.request_collection(principal, jobs=Jobs(busy=True), now=NOW)
    assert result == {"run_id": "manual-race", "request_id": None, "reused": True, "min_interval_sec": 1800}


def test_new_batches_are_rate_limited(principal):  # RC-05
    jobs = Jobs()
    collect.request_collection(principal, jobs=jobs, now=NOW)
    with pytest.raises(MCPError) as error:
        collect.request_collection(principal, jobs=jobs, now=NOW + timedelta(minutes=10))
    assert error.value.code == "GEO_RATE_LIMITED"
    assert error.value.details == {"retry_after_sec": 1200, "run_id": "manual-1"}
    assert collect.request_collection(principal, jobs=jobs, now=NOW + timedelta(minutes=31))["run_id"] == "manual-2"


def test_unknown_or_excluded_collectors_are_rejected(principal, monkeypatch):  # RC-06
    monkeypatch.setattr(collect, "_validate", REAL_VALIDATE)
    monkeypatch.setattr(collect, "load_registry", lambda _: [{"id": "a"}, {"id": "eu-gas-storage"}, {"id": "off", "enabled": False}])
    for ids in (["missing"], ["off"], ["eu-gas-storage"]):
        with pytest.raises(MCPError, match="GEO_INVALID_ARGUMENT"):
            collect.request_collection(principal, ids, jobs=Jobs(), now=NOW)
