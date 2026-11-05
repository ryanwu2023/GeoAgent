"""HFT-side remote collection against a real in-process Sealos MCP endpoint.

The "remote" GeoAgent uses its own PostgreSQL schema and data directory, so
snapshots, reports and ledgers on the two sides never share state.
"""
import hashlib
import json
import os
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace

import psycopg
import pytest
from psycopg import sql
from starlette.testclient import TestClient

from backend import collector_jobs as collector_jobs_module, remote_collection as rc, store
from backend.chinese import save_translation
from backend.collector_ledger import CollectorLedger
from backend.collector_monitor import load_ledger_events, load_runs
from backend.database import _initialized
from backend.geo_mcp import mirror
from backend.geo_mcp.auth import AuthConfig
from backend.geo_mcp.contracts import Principal
from backend.geo_mcp.server import create_app

TOKEN = "hft-test-token"
REGISTRY = store.ROOT / "collectors" / "collectors.json"


def payload(text="Talks resume", label="a"):
    return {"records": [{"id": label, "title": "Talks", "text": text, "source_name": "Reuters", "topics": ["usiran"]}],
            "observations": [], "sources": [], "projects": [], "errors": [], "cleaning": {}}


@pytest.fixture
def remote(tmp_path, monkeypatch):
    remote_env = {"GEO_DATABASE_SCHEMA": os.environ["GEO_DATABASE_SCHEMA"] + "_r",
                  "GEO_DATA_DIR": str(tmp_path / "remote")}
    monkeypatch.setenv("GEO_REMOTE_COLLECT_PRINCIPALS", "hft-key")
    monkeypatch.setenv("GEO_REMOTE_COLLECT_MIN_INTERVAL_SEC", "0")
    scope = frozenset({"*"})
    principal = Principal("hft-key", "hft", "project", scope, scope, scope, scope)
    app = create_app(auth_config=AuthConfig(token_hash=hashlib.sha256(TOKEN.encode()).hexdigest(), principal=principal))

    @contextmanager
    def as_remote():
        saved = {key: os.environ[key] for key in remote_env}
        os.environ.update(remote_env)
        store.get_snapshot.cache_clear()
        try:
            yield
        finally:
            os.environ.update(saved)
            store.get_snapshot.cache_clear()

    calls = []
    with TestClient(app, base_url="http://127.0.0.1:8401") as wire:
        class Http:
            def post(self, url, json, headers):
                if json.get("method") == "tools/call":
                    calls.append(json["params"])
                with as_remote():
                    return wire.post("/mcp", json=json, headers=headers)

        client = rc.RemoteClient(rc.RemoteConfig(url="http://127.0.0.1:8401/mcp", key=TOKEN, poll_sec=0), http=Http())
        yield SimpleNamespace(client=client, as_remote=as_remote, calls=calls)
    dsn = os.environ["GEO_DATABASE_URL"]
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(remote_env["GEO_DATABASE_SCHEMA"])))
    _initialized.discard((dsn, remote_env["GEO_DATABASE_SCHEMA"]))


def seed_remote(remote, text="Talks resume", label="a"):
    with remote.as_remote():
        sid = store.save_snapshot(payload(text, label))[0]
        save_translation("Talks", "会谈")
    return sid


def local_translation(text):
    with store.db() as connection:
        row = connection.execute("SELECT chinese FROM translations WHERE hash=%s", (store.digest(text),)).fetchone()
    return row["chinese"] if row else None


def chunk_calls(remote):
    return [call for call in remote.calls if call["name"] == "geo_mirror_chunk"]


# ------------------------------------------------------------- snapshot pull

def test_pull_imports_same_snapshot_and_translations(remote):  # RC-10
    remote.client.ensure_compatible()
    sid = seed_remote(remote)
    result = rc.pull_snapshot(remote.client)
    assert result["status"] == "imported" and result["snapshot_id"] == sid and result["switched"]
    assert store.get_working_snapshot_id() == sid
    assert store.get_snapshot(sid)["records"][0]["text"] == "Talks resume"
    assert local_translation("Talks") == "会谈"
    assert rc.sync_state()["snapshot_id"] == sid and rc.sync_state()["error"] is None


def test_interrupted_download_resumes_from_written_bytes(remote):  # RC-11
    sid = seed_remote(remote, secrets.token_hex(400_000))
    info = remote.client.call("geo_mirror_manifest", {"snapshot_id": sid})
    assert info["total_bytes"] > 256 * 1024
    first = remote.client.call("geo_mirror_chunk", {"snapshot_id": sid, "offset": 0})
    folder = store.data_dir() / "remote-sync"
    folder.mkdir(parents=True)
    import base64
    (folder / f"{sid}.part").write_bytes(base64.b64decode(first["content"]))
    (folder / f"{sid}.part.json").write_text(json.dumps({"sha256": info["sha256"]}))
    remote.calls.clear()
    assert rc.pull_snapshot(remote.client)["status"] == "imported"
    assert chunk_calls(remote)[0]["arguments"]["offset"] == first["next_offset"]
    assert not list(folder.glob("*.part*"))


def test_tampered_chunk_is_rejected_and_working_snapshot_kept(remote, monkeypatch):  # RC-12
    local = store.save_snapshot(payload("local only", "z"))[0]
    store.set_working_snapshot_id(local)
    sid = seed_remote(remote)
    real_call = remote.client.call

    def tamper(name, arguments):
        result = real_call(name, arguments)
        if name == "geo_mirror_chunk":
            result["chunk_sha256"] = "0" * 64
        return result

    monkeypatch.setattr(remote.client, "call", tamper)
    with pytest.raises(rc.RemoteError) as error:
        rc.pull_snapshot(remote.client)
    assert error.value.code == "MIRROR_TAMPERED"
    assert store.get_working_snapshot_id() == local
    with store.db() as connection:
        assert connection.execute("SELECT 1 FROM snapshots WHERE id=%s", (sid,)).fetchone() is None
    assert rc.sync_state()["error"]["code"] == "MIRROR_TAMPERED"


def test_payload_not_matching_snapshot_id_is_refused(remote, monkeypatch):  # RC-13
    sid = seed_remote(remote)
    real_document = mirror.mirror_document

    def altered(snapshot_id):
        document = real_document(snapshot_id)
        document["payload"]["records"][0]["text"] = "forged"
        return document

    monkeypatch.setattr(mirror, "mirror_document", altered)
    with pytest.raises(rc.RemoteError, match="快照ID不符"):
        rc.pull_snapshot(remote.client)
    with store.db() as connection:
        assert connection.execute("SELECT 1 FROM snapshots WHERE id=%s", (sid,)).fetchone() is None
    assert not list((store.data_dir() / "remote-sync").glob("*.part"))


def test_existing_snapshot_is_not_downloaded_again(remote):  # RC-14
    seed_remote(remote)
    rc.pull_snapshot(remote.client)
    remote.calls.clear()
    assert rc.pull_snapshot(remote.client)["status"] == "unchanged"
    assert chunk_calls(remote) == []


def test_periodic_sync_keeps_a_users_other_selection(remote):  # RC-15
    first = seed_remote(remote)
    rc.pull_snapshot(remote.client, switch="auto")
    assert store.get_working_snapshot_id() == first
    chosen = store.save_snapshot(payload("analyst choice", "y"))[0]
    store.set_working_snapshot_id(chosen)
    second = seed_remote(remote, "newer", "b")
    result = rc.pull_snapshot(remote.client, switch="auto")
    assert result["snapshot_id"] == second and result["switched"] is False
    assert store.get_working_snapshot_id() == chosen
    store.set_working_snapshot_id(second)
    third = seed_remote(remote, "newest", "c")
    assert rc.pull_snapshot(remote.client, switch="auto")["switched"]
    assert store.get_working_snapshot_id() == third


# -------------------------------------------------------- remote batches

@dataclass
class Job:
    run_id: str
    request_id: str = "remote-req"
    submitted_at: str = "2026-11-05T08:00:00+00:00"


class RemoteSite:
    """Stand-in for the remote collector service, driven by poll ticks."""

    def __init__(self, remote, outcome="partial"):
        self.remote, self.outcome, self.ticks, self.snapshot_id = remote, outcome, 0, None
        self.submitted = []
        self.seen_running = None

    def submit(self, **kwargs):
        self.submitted.append(kwargs)
        run_id = "manual-20261105T080000Z-abcd1234"
        self.write(run_id, {"status": "running", "started_at": "2026-11-05T08:00:00+00:00",
                            "results": [{"id": "diplomacy-monitor", "status": "running", "started_at": "2026-11-05T08:00:01+00:00"}]})
        ledger = CollectorLedger()
        ledger.run_started(run_id=run_id, run_kind="manual", started_at="2026-11-05T08:00:00+00:00", topic=None, timeout_sec=900)
        ledger.event(run_id=run_id, event_type="collector_started", collector_id="diplomacy-monitor", status="running", message="开始采集")
        return Job(run_id=run_id)

    def write(self, run_id, report):
        folder = store.data_dir() / "collection-runs" / run_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "results.json").write_text(json.dumps(report), encoding="utf-8")

    def tick(self, run_id):
        self.ticks += 1
        if self.ticks == 1:
            self.seen_running = load_runs(store.data_dir() / "collection-runs", REGISTRY, run_id=run_id)[0]
            with self.remote.as_remote():
                ledger = CollectorLedger()
                if self.outcome == "partial":
                    self.snapshot_id = store.save_snapshot(payload("collected", "n"))[0]
                    ledger.run_finished(run_id=run_id, status="partial", finished_at="2026-11-05T08:14:00+00:00", expected=2,
                                        completed=2, failed=1, import_status="succeeded", snapshot_id=self.snapshot_id)
                    report = {"status": "partial", "import_status": "succeeded", "snapshot_id": self.snapshot_id}
                else:
                    ledger.run_finished(run_id=run_id, status="failed", finished_at="2026-11-05T08:14:00+00:00", expected=2,
                                        completed=2, failed=2, import_status="skipped_collection_status")
                    report = {"status": "failed", "import_status": "skipped_collection_status"}
                ledger.event(run_id=run_id, event_type="run_finished", status=report["status"], message="批次结束")
                self.write(run_id, {**report, "started_at": "2026-11-05T08:00:00+00:00", "finished_at": "2026-11-05T08:14:00+00:00",
                                    "results": [{"id": "diplomacy-monitor", "returncode": 0, "started_at": "2026-11-05T08:00:01+00:00",
                                                 "finished_at": "2026-11-05T08:06:00+00:00"},
                                                {"id": "eu-gas-storage", "returncode": 1, "started_at": "2026-11-05T08:00:01+00:00",
                                                 "finished_at": "2026-11-05T08:00:03+00:00"}]})


def manager_for(remote, site, monkeypatch, *, client=None):
    monkeypatch.setattr(collector_jobs_module, "collector_jobs", site)
    run_id = "manual-20261105T080000Z-abcd1234"
    manager = rc.RemoteCollectorJobManager(lambda: client or remote.client, sleep=lambda _: site.tick(run_id))
    monkeypatch.setattr(manager, "_start", lambda run, job, c: manager.follow(run, job, c))
    return manager


def test_remote_batch_progress_and_snapshot_are_mirrored(remote, monkeypatch):  # RC-16
    site = RemoteSite(remote)
    job = manager_for(remote, site, monkeypatch).submit(ids=[], topic=None)
    assert site.submitted[0]["allow_partial_import"] is True
    assert site.seen_running["run_status"] == "running"
    assert site.seen_running["collectors"][0]["status"] == "running"
    public = job.public()
    assert public["status"] == "partial" and public["import_status"] == "succeeded"
    assert public["snapshot_id"] == site.snapshot_id == store.get_working_snapshot_id()
    run = load_runs(store.data_dir() / "collection-runs", REGISTRY, run_id=job.run_id)[0]
    assert run["run_kind"] == "remote" and run["failed_count"] == 1
    assert [item["status"] for item in run["collectors"]] == ["success", "failed"]
    events = load_ledger_events(store.data_dir() / "collection-runs", job.run_id)["events"]
    assert [event["event_type"] for event in events] == ["collector_started", "run_finished"]


def test_remote_failure_without_snapshot_does_not_import(remote, monkeypatch):  # RC-17
    site = RemoteSite(remote, outcome="failed")
    job = manager_for(remote, site, monkeypatch).submit()
    assert job.public()["status"] == "failed"
    assert job.public()["snapshot_id"] is None
    assert job.public()["import_status"] == "skipped_collection_status"
    assert store.get_working_snapshot_id() is None


def test_unreachable_remote_gives_up_after_ten_minutes(remote, monkeypatch, tmp_path):  # RC-18
    now = [0.0]

    class Down:
        config = remote.client.config

        def call(self, name, arguments):
            raise rc.RemoteError("REMOTE_UNREACHABLE", "远端不可达：ConnectError")

    def sleep(seconds):
        now[0] += 400

    manager = rc.RemoteCollectorJobManager(lambda: Down(), sleep=sleep, clock=lambda: now[0])
    report = manager.follow("manual-x")
    assert report["status"] == "failed" and "远端不可达" in report["import_error_message"]


def test_restart_resumes_unfinished_remote_batches(monkeypatch):  # RC-20
    folder = store.data_dir() / "collection-runs"
    for run_id, report in {"remote-running": {"run_kind": "remote", "status": "running"},
                           "remote-done": {"run_kind": "remote", "status": "partial", "import_status": "succeeded"},
                           "local-running": {"status": "running"}}.items():
        (folder / run_id).mkdir(parents=True)
        (folder / run_id / "results.json").write_text(json.dumps({"projected": True, **report}))
    manager = rc.RemoteCollectorJobManager(lambda: None)
    started = []
    monkeypatch.setattr(manager, "_start", lambda run, job, client: started.append(run))
    assert manager.recover() == ["remote-running"] and started == ["remote-running"]


def test_invalid_remote_run_id_is_rejected():
    with pytest.raises(rc.RemoteError):
        rc._report_path("../../etc")


# ----------------------------------------------------------------- API

@pytest.fixture
def api(monkeypatch):
    from backend.app import app
    monkeypatch.setenv("GEO_COLLECTION_MODE", "remote")
    monkeypatch.delenv("GEO_REMOTE_MCP_URL", raising=False)
    monkeypatch.delenv("GEO_REMOTE_MCP_KEY", raising=False)
    return TestClient(app)


def test_remote_mode_blocks_remote_held_operations(api):  # RC-19
    folder = store.data_dir() / "collection-runs" / "remote-run"
    folder.mkdir(parents=True)
    (folder / "results.json").write_text(json.dumps({"projected": True, "run_kind": "remote", "status": "partial", "collectors": []}))
    for method, url, body in (
        ("put", "/api/collector/config", {"interval_sec": 3600, "exclude_ids": [], "allow_partial_import": False}),
        ("post", "/api/collector/runs/remote-run/gate", {"decision": "approved", "reviewer": "ops", "reason": "ok"}),
        ("get", "/api/collector/runs/remote-run/logs/diplomacy-monitor", None),
    ):
        response = getattr(api, method)(url, **({"json": body} if body is not None else {}))
        assert response.status_code == 409, (url, response.text)
        assert "远端采集模式" in response.json()["detail"]
    # Snapshot review notes are local research judgements and stay available.
    review = api.put("/api/collector/runs/remote-run/review/snapshot", json={"status": "accepted", "reviewer": "ops", "reason": ""})
    assert review.status_code == 200
    assert api.get("/api/collector/summary").json()["collection_mode"] == "remote"


def test_unconfigured_remote_reports_clear_error(api):  # RC-21
    response = api.post("/api/collector/runs", json={})
    assert response.status_code == 503 and "未配置" in response.json()["detail"]
    assert api.post("/api/collector/remote-sync").status_code == 503
    assert api.get("/api/collector/remote-sync").json()["collection_mode"] == "remote"
