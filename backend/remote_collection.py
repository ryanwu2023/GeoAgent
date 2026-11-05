"""Remote collection mode: collect on another GeoAgent and mirror the result.

A deployment without overseas egress (``GEO_COLLECTION_MODE=remote``) asks a
trusted GeoAgent over MCP to collect, mirrors that batch's progress into the
local report directory so the existing monitor renders it, and pulls the
imported snapshot back. Snapshot ids are content hashes: an imported mirror
must re-derive the remote id, otherwise nothing is written.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from . import store
from .collector_jobs import CollectorJob
from .collector_ledger import CollectorLedger
from .geo_mcp.mirror import FORMAT as MIRROR_FORMAT, translatable_texts
from .mcp_news import _json_response

logger = logging.getLogger(__name__)

REQUIRED_TOOLS = {"geo_collect_request", "geo_collector_status", "geo_mirror_manifest", "geo_mirror_chunk"}
SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
UNREACHABLE_GIVE_UP_SEC = 600


def collection_mode() -> str:
    return "remote" if os.getenv("GEO_COLLECTION_MODE", "local").strip().lower() == "remote" else "local"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RemoteError(RuntimeError):
    """A failure the API reports to the user; ``code`` drives the HTTP status."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}


@dataclass(frozen=True)
class RemoteConfig:
    url: str
    key: str
    sync_interval_sec: int = 1800
    poll_sec: float = 5.0

    @classmethod
    def from_env(cls) -> "RemoteConfig":
        url = os.getenv("GEO_REMOTE_MCP_URL", "").strip()
        key = os.getenv("GEO_REMOTE_MCP_KEY", "").strip()
        if not url or not key:
            raise RemoteError("REMOTE_NOT_CONFIGURED", "远端采集未配置：需要 GEO_REMOTE_MCP_URL 和 GEO_REMOTE_MCP_KEY")
        if not url.startswith("https://") and not re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?/", url):
            raise RemoteError("REMOTE_NOT_CONFIGURED", "远端 MCP 地址必须使用 HTTPS")
        return cls(url=url, key=key,
                   sync_interval_sec=max(0, int(os.getenv("GEO_REMOTE_SYNC_INTERVAL_SEC", "1800"))),
                   poll_sec=max(0.0, float(os.getenv("GEO_REMOTE_POLL_SEC", "5"))))


class RemoteClient:
    """Minimal synchronous MCP tools/call client for the stateless /mcp endpoint."""

    def __init__(self, config: RemoteConfig, http: httpx.Client | None = None):
        self.config = config
        self.http = http or httpx.Client(timeout=60, follow_redirects=False)
        self._request_id = 0

    def _rpc(self, method: str, params: dict) -> dict:
        self._request_id += 1
        try:
            response = self.http.post(self.config.url, json={"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params},
                                      headers={"Authorization": "Bearer " + self.config.key,
                                               "Accept": "application/json, text/event-stream"})
        except httpx.HTTPError as exc:
            raise RemoteError("REMOTE_UNREACHABLE", f"远端不可达：{type(exc).__name__}") from exc
        if response.status_code == 401:
            raise RemoteError("REMOTE_AUTH", "远端拒绝了 MCP Key（无效、过期或已撤销）")
        try:
            body = _json_response(response)
        except (httpx.HTTPError, ValueError) as exc:
            raise RemoteError("REMOTE_UNREACHABLE", f"远端响应异常：HTTP {response.status_code}") from exc
        if body.get("error"):
            raise RemoteError("REMOTE_PROTOCOL", str(body["error"].get("message") or body["error"])[:300])
        return body.get("result", {})

    def tools(self) -> set[str]:
        return {tool.get("name") for tool in self._rpc("tools/list", {}).get("tools", [])}

    def call(self, name: str, arguments: dict) -> Any:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        text = next((item.get("text") for item in result.get("content", []) if item.get("type") == "text"), None)
        try:
            envelope = json.loads(text or "")
        except ValueError as exc:
            if result.get("isError"):
                raise RemoteError("REMOTE_PROTOCOL", (text or "远端工具调用失败")[:300]) from exc
            raise RemoteError("REMOTE_PROTOCOL", "远端工具没有返回可解析的结果") from exc
        if not envelope.get("ok"):
            error = envelope.get("error") or {}
            raise RemoteError(error.get("code") or "REMOTE_PROTOCOL", error.get("message") or "远端工具调用失败", error.get("details"))
        return envelope["data"]

    def ensure_compatible(self) -> None:
        missing = REQUIRED_TOOLS - self.tools()
        if missing:
            raise RemoteError("REMOTE_TOO_OLD", "远端版本过旧或未开放远端采集", {"missing_tools": sorted(missing)})


def default_client() -> RemoteClient:
    return RemoteClient(RemoteConfig.from_env())


# ---------------------------------------------------------------- snapshot pull

_pull_lock = threading.Lock()


def _sync_dir() -> Path:
    return store.data_dir() / "remote-sync"


def sync_state() -> dict:
    try:
        return json.loads((_sync_dir() / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(**changes) -> dict:
    state = {**sync_state(), **changes}
    _sync_dir().mkdir(parents=True, exist_ok=True)
    temporary = _sync_dir() / "state.json.tmp"
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(_sync_dir() / "state.json")
    return state


def _snapshot_exists(snapshot_id: str) -> bool:
    with store.db() as connection:
        return connection.execute("SELECT 1 FROM snapshots WHERE id=%s", (snapshot_id,)).fetchone() is not None


def _download(client: RemoteClient, info: dict) -> bytes:
    """Download into a resumable part file and return the verified bytes."""
    sid, sha = info["snapshot_id"], info["sha256"]
    if not SAFE_RUN_ID.match(sid):
        raise RemoteError("MIRROR_INVALID", "远端快照ID格式无效")
    _sync_dir().mkdir(parents=True, exist_ok=True)
    part, meta = _sync_dir() / f"{sid}.part", _sync_dir() / f"{sid}.part.json"
    try:
        if json.loads(meta.read_text(encoding="utf-8")).get("sha256") != sha:
            part.unlink(missing_ok=True)
    except (OSError, ValueError):
        part.unlink(missing_ok=True)
    meta.write_text(json.dumps({"sha256": sha}), encoding="utf-8")
    offset = part.stat().st_size if part.exists() else 0
    if offset > info["total_bytes"]:
        part.unlink()
        offset = 0
    with part.open("ab") as output:
        while offset < info["total_bytes"]:
            page = client.call("geo_mirror_chunk", {"snapshot_id": sid, "offset": offset})
            chunk = base64.b64decode(page["content"], validate=True)
            if (page.get("sha256") != sha or page.get("offset") != offset or not chunk
                    or hashlib.sha256(chunk).hexdigest() != page.get("chunk_sha256")):
                raise RemoteError("MIRROR_TAMPERED", "镜像分块校验失败")
            output.write(chunk)
            output.flush()
            offset += len(chunk)
    data = part.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha:
        part.unlink(missing_ok=True)
        raise RemoteError("MIRROR_TAMPERED", "镜像整体校验失败")
    return data


def _import_translations(payload: dict, translations: list) -> int:
    texts = translatable_texts(payload)
    rows = [(row["hash"], texts[row["hash"]], row["chinese"], row["method"], _now())
            for row in translations if isinstance(row, dict) and row.get("hash") in texts
            and isinstance(row.get("chinese"), str) and isinstance(row.get("method"), str)]
    with store.db() as connection:
        for row in rows:
            # A locally reviewed translation wins over the mirrored one.
            connection.execute("INSERT INTO translations VALUES (%s,%s,%s,%s,%s) ON CONFLICT(hash) DO NOTHING", row)
    return len(rows)


def pull_snapshot(client: RemoteClient, snapshot_id: str = "", *, switch: str = "always") -> dict:
    """Mirror one remote snapshot; ``switch='auto'`` keeps a user's other selection."""
    with _pull_lock:
        checked_at = _now()
        try:
            info = client.call("geo_mirror_manifest", {"snapshot_id": snapshot_id})
            if not info.get("available"):
                _write_state(last_checked_at=checked_at, error=None)
                return {"status": "unavailable", "snapshot_id": None}
            sid = info["snapshot_id"]
            if info.get("schema_version") != MIRROR_FORMAT:
                raise RemoteError("REMOTE_TOO_OLD", f"不支持的镜像格式：{info.get('schema_version')}")
            created = False
            translations = 0
            if not _snapshot_exists(sid):
                try:
                    document = json.loads(gzip.decompress(_download(client, info)))
                except (OSError, ValueError, EOFError) as exc:
                    (_sync_dir() / f"{sid}.part").unlink(missing_ok=True)
                    raise RemoteError("MIRROR_TAMPERED", "镜像内容无法解压或解析") from exc
                payload = document.get("payload") if isinstance(document, dict) else None
                if (document.get("schema_version") != MIRROR_FORMAT or document.get("snapshot_id") != sid
                        or not isinstance(payload, dict) or store.digest(payload) != sid):
                    (_sync_dir() / f"{sid}.part").unlink(missing_ok=True)
                    raise RemoteError("MIRROR_TAMPERED", "镜像内容与快照ID不符，已拒绝导入")
                saved, created = store.save_snapshot(payload)
                assert saved == sid
                translations = _import_translations(payload, document.get("translations") or [])
                for name in (f"{sid}.part", f"{sid}.part.json"):
                    (_sync_dir() / name).unlink(missing_ok=True)
            working = store.get_working_snapshot_id()
            previous = sync_state().get("snapshot_id")
            switched = working != sid and (switch == "always" or working in (None, previous))
            if switched:
                store.set_working_snapshot_id(sid)
            _write_state(last_checked_at=checked_at, last_success_at=_now(), snapshot_id=sid,
                         source_created_at=info.get("source_created_at"), error=None)
            return {"status": "imported" if created else "unchanged", "snapshot_id": sid, "created": created,
                    "switched": switched, "translations": translations, "counts": info.get("counts")}
        except RemoteError as exc:
            _write_state(last_checked_at=checked_at, error={"code": exc.code, "message": exc.message, "at": _now()})
            raise


# ------------------------------------------------------------ remote batches

def _runs_root() -> Path:
    return store.data_dir() / "collection-runs"


def _report_path(run_id: str) -> Path:
    if not SAFE_RUN_ID.match(run_id or ""):
        raise RemoteError("REMOTE_PROTOCOL", "远端批次ID格式无效")
    return _runs_root() / run_id / "results.json"


def _read_report(run_id: str) -> dict:
    try:
        return json.loads(_report_path(run_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_report(run_id: str, report: dict) -> None:
    path = _report_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class RemoteCollectorJobManager:
    """Same contract as ``CollectorJobManager`` for the monitor's run buttons."""

    def __init__(self, client_factory=default_client, *, sleep=time.sleep, clock=time.monotonic):
        self._client_factory = client_factory
        self._sleep, self._clock = sleep, clock
        self._lock = threading.Lock()
        self._jobs: dict[str, CollectorJob] = {}
        self._following: set[str] = set()

    def submit(self, *, ids: list[str] | None = None, topic: str | None = None, **_ignored) -> CollectorJob:
        client = self._client_factory()
        client.ensure_compatible()
        result = client.call("geo_collect_request", {"ids": list(ids or []), "topic": topic or ""})
        run_id = result["run_id"]
        job = CollectorJob(request_id=uuid.uuid4().hex, run_id=run_id, ids=list(ids or []), topic=topic,
                           status="running", allow_partial_import=True)
        report = _read_report(run_id)
        if not report:
            _write_report(run_id, {"projected": True, "run_kind": "remote", "status": "running",
                                   "import_status": "pending", "started_at": result.get("submitted_at") or _now(),
                                   "remote_reused": bool(result.get("reused")), "collectors": []})
        with self._lock:
            self._jobs[job.request_id] = job
        self._start(run_id, job, client)
        return job

    def get(self, request_id: str) -> CollectorJob | None:
        with self._lock:
            return self._jobs.get(request_id)

    def recover(self) -> list[str]:
        """Resume following remote batches interrupted by a local restart."""
        resumed = []
        if _runs_root().is_dir():
            for path in _runs_root().glob("*/results.json"):
                report = _read_report(path.parent.name)
                if report.get("run_kind") == "remote" and (report.get("status") == "running"
                                                          or report.get("import_status") in {"pending", "syncing"}):
                    self._start(path.parent.name, None, None)
                    resumed.append(path.parent.name)
        return resumed

    def _start(self, run_id: str, job: CollectorJob | None, client: RemoteClient | None) -> None:
        with self._lock:
            if run_id in self._following:
                return
            self._following.add(run_id)
        threading.Thread(target=self._follow_safely, args=(run_id, job, client), name=f"remote-{run_id[:24]}", daemon=True).start()

    def _follow_safely(self, run_id, job, client) -> None:
        try:
            self.follow(run_id, job, client)
        finally:
            with self._lock:
                self._following.discard(run_id)

    def follow(self, run_id: str, job: CollectorJob | None = None, client: RemoteClient | None = None) -> dict:
        """Mirror remote progress until the batch ends, then pull its snapshot."""
        started = last_ok = self._clock()
        delay = 5.0
        status: dict = {}
        while True:
            try:
                client = client or self._client_factory()
                status = client.call("geo_collector_status", {"run_id": run_id, "limit": 100})
                last_ok, delay = self._clock(), 5.0
            except RemoteError as exc:
                # A just-queued batch has no report until its worker starts.
                not_found = exc.code == "GEO_RUN_NOT_FOUND" and self._clock() - started > 60
                if not_found or self._clock() - last_ok > UNREACHABLE_GIVE_UP_SEC:
                    message = "远端没有该采集批次" if not_found else "远端不可达：超过 10 分钟未能获取进度"
                    return self._finish(run_id, job, "failed", "failed", None, message)
                self._sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            self._mirror(run_id, status)
            if status.get("run_status") not in {"running", "queued", None}:
                break
            self._sleep(client.config.poll_sec)
        run_status = status.get("run_status")
        remote_sid = status.get("snapshot_id")
        if remote_sid and status.get("import_status") == "succeeded":
            self._update(run_id, import_status="syncing", remote_snapshot_id=remote_sid)
            try:
                pulled = pull_snapshot(client, remote_sid, switch="always")
            except RemoteError as exc:
                return self._finish(run_id, job, run_status, "failed", None, f"快照拉取失败：{exc.message}")
            return self._finish(run_id, job, run_status, "succeeded", pulled["snapshot_id"], None)
        return self._finish(run_id, job, run_status, status.get("import_status") or "not_imported", None,
                            "远端本批次未导入快照")

    def _mirror(self, run_id: str, status: dict) -> None:
        report = _read_report(run_id)
        report.update({
            "projected": True, "run_kind": "remote",
            "status": status.get("run_status") or "running",
            "started_at": status.get("started_at") or report.get("started_at"),
            "finished_at": status.get("finished_at"),
            "collectors": [item for item in status.get("collectors") or [] if isinstance(item, dict)],
            "remote_import_status": status.get("import_status"),
            "remote_updated_at": status.get("updated_at"),
        })
        report.setdefault("import_status", "pending")
        cursor = report.get("remote_event_cursor") or 0
        events = [event for event in status.get("events") or []
                  if isinstance(event, dict) and isinstance(event.get("id"), int) and event["id"] > cursor]
        if events:
            ledger = CollectorLedger()
            ledger.ensure_run(run_id=run_id, run_kind="remote", started_at=report.get("started_at"))
            for event in events:
                ledger.event(run_id=run_id, event_type=str(event.get("event_type") or "remote_event")[:80],
                             status=event.get("status"), collector_id=event.get("collector_id"),
                             message=event.get("message"), created_at=event.get("created_at"))
            report["remote_event_cursor"] = max(event["id"] for event in events)
        _write_report(run_id, report)

    def _update(self, run_id: str, **changes) -> None:
        report = _read_report(run_id)
        report.update(changes)
        _write_report(run_id, report)

    def _finish(self, run_id, job, run_status, import_status, snapshot_id, error) -> dict:
        report = _read_report(run_id)
        report.update({"status": run_status or "failed", "import_status": import_status,
                       "snapshot_id": snapshot_id, "import_error_message": error})
        report["finished_at"] = report.get("finished_at") or _now()
        _write_report(run_id, report)
        if job is not None:
            job.report = {"status": report["status"], "import_status": import_status, "snapshot_id": snapshot_id,
                          "import_error_message": error}
            job.status = report["status"]
            job.error = error if report["status"] == "failed" else None
            job.finished_at = _now()
        return report


remote_jobs = RemoteCollectorJobManager()


def start_background() -> None:
    """Resume remote batches and start periodic snapshot discovery."""
    if collection_mode() != "remote":
        return
    try:
        config = RemoteConfig.from_env()
    except RemoteError as exc:
        logger.warning("remote collection disabled: %s", exc.message)
        return
    remote_jobs.recover()
    if config.sync_interval_sec <= 0:
        return

    def loop() -> None:
        while True:
            try:
                pull_snapshot(RemoteClient(config), switch="auto")
            except RemoteError as exc:
                logger.warning("remote snapshot sync failed: %s %s", exc.code, exc.message)
            except Exception:  # keep the periodic thread alive; the state file records RemoteError details
                logger.exception("remote snapshot sync crashed")
            time.sleep(config.sync_interval_sec)

    threading.Thread(target=loop, name="remote-snapshot-sync", daemon=True).start()
