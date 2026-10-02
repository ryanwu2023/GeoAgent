"""Small in-process bridge from the local API to the collector service.

The operating-system service remains the production scheduler.  This bridge is
for the local monitor's manual-run buttons: it queues one service execution in
a daemon thread, returns a run id immediately, and leaves locking, retries,
reports, and the ledger to ``scripts.collector_service``.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .collector_ledger import service_lock
from .paths import project_root
from .store import data_dir
from scripts.collector_service import execute_once


class CollectorBusyError(RuntimeError):
    """Raised when another scheduled or manual collector run owns the lock."""


@dataclass
class CollectorJob:
    request_id: str
    run_id: str
    ids: list[str]
    topic: str | None
    status: str = "queued"
    submitted_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    report: dict[str, Any] | None = None
    error: str | None = None
    auto_import: bool = True
    allow_partial_import: bool = False
    exclude_ids: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "run_id": self.run_id,
            "status": self.status,
            "ids": self.ids,
            "exclude_ids": self.exclude_ids,
            "topic": self.topic,
            "submitted_at": self.submitted_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "collection_status": self.report.get("status") if self.report else self.status,
            "import_status": self.report.get("import_status") if self.report else ("pending" if self.auto_import else "not_requested"),
            "snapshot_id": self.report.get("snapshot_id") if self.report else None,
            "import_error": self.report.get("import_error_message") if self.report else None,
        }


class CollectorJobManager:
    def __init__(self, *, report_root: Path | None = None):
        self._report_root = report_root
        self._lock = threading.Lock()
        self._jobs: dict[str, CollectorJob] = {}

    @property
    def report_root(self) -> Path:
        return self._report_root or data_dir() / "collection-runs"

    def _service_lock_path(self) -> Path:
        return self.report_root / ".collector-service.lock"

    def _preflight_lock(self) -> None:
        lock = service_lock(self._service_lock_path())
        try:
            lock.__enter__()
        except RuntimeError as exc:
            raise CollectorBusyError(str(exc)) from exc
        else:
            lock.__exit__(None, None, None)

    def submit(
        self,
        *,
        ids: list[str] | None = None,
        topic: str | None = None,
        timeout: int = 900,
        retry_attempts: int = 2,
        retry_backoff_sec: float = 1.0,
        auto_import: bool = True,
        allow_partial_import: bool = False,
        exclude_ids: list[str] | None = None,
    ) -> CollectorJob:
        self._preflight_lock()
        run_name = f"manual-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        job = CollectorJob(
            request_id=uuid.uuid4().hex,
            run_id=run_name,
            ids=list(ids or []),
            topic=topic,
            auto_import=auto_import,
            allow_partial_import=allow_partial_import,
            exclude_ids=list(exclude_ids or []),
        )
        with self._lock:
            self._jobs[job.request_id] = job

        def worker() -> None:
            job.status = "running"
            try:
                report = execute_once(
                    ids=job.ids,
                    topic=job.topic,
                    timeout=timeout,
                    report_root=self.report_root,
                    retry_attempts=retry_attempts,
                    retry_backoff_sec=retry_backoff_sec,
                    run_id_override=job.run_id,
                    auto_import=job.auto_import,
                    allow_partial_import=job.allow_partial_import,
                    exclude_ids=job.exclude_ids,
                )
                job.report = report
                job.run_id = report["run_id"]
                job.status = report["status"]
            except Exception as exc:  # the API can expose the failure instead of losing the thread
                job.status = "failed"
                job.error = f"{type(exc).__name__}: {str(exc)[:500]}"
            finally:
                job.finished_at = datetime.now(timezone.utc).isoformat()

        threading.Thread(target=worker, name=f"collector-{job.request_id[:8]}", daemon=True).start()
        return job

    def get(self, request_id: str) -> CollectorJob | None:
        with self._lock:
            return self._jobs.get(request_id)


collector_jobs = CollectorJobManager()
