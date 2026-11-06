"""Periodic collection for Linux deployments (Sealos), which have no OS launcher.

News collectors (registry ``"cadence": "hourly"``) run every hour without
importing, so short-lived RSS and news-sitemap windows (Al Jazeera's RSS keeps
about 9 hours) are archived before items roll off. Every full interval all
enabled collectors run and a snapshot is imported; a failed collector keeps its
previous published output, so partial runs may import. Hourly runs skip import
because each snapshot is a full copy (~15-20 MB).

Off unless ``GEO_COLLECT_SCHEDULE_ENABLED`` is true, and never in remote
collection mode (the remote side collects). Run times persist under the data
directory so a Pod restart does not re-trigger a batch.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .collector_jobs import CollectorBusyError, collector_jobs
from .paths import project_root
from .remote_collection import collection_mode
from .store import data_dir

logger = logging.getLogger(__name__)
TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ScheduleConfig:
    enabled: bool = False
    hourly_interval_sec: int = 3600
    full_interval_sec: int = 21600
    allow_partial_import: bool = True
    start_delay_sec: int = 300
    tick_sec: int = 60

    @classmethod
    def from_env(cls) -> "ScheduleConfig":
        env = os.getenv
        return cls(
            enabled=env("GEO_COLLECT_SCHEDULE_ENABLED", "false").strip().lower() in TRUE,
            hourly_interval_sec=max(600, int(env("GEO_COLLECT_HOURLY_INTERVAL_SEC", "3600"))),
            full_interval_sec=max(3600, int(env("GEO_COLLECT_FULL_INTERVAL_SEC", "21600"))),
            allow_partial_import=env("GEO_COLLECT_SCHEDULE_PARTIAL_IMPORT", "true").strip().lower() in TRUE,
            start_delay_sec=max(0, int(env("GEO_COLLECT_SCHEDULE_START_DELAY_SEC", "300"))),
        )


def hourly_collector_ids(registry_path: Path | None = None) -> list[str]:
    path = registry_path or project_root() / "collectors" / "collectors.json"
    collectors = json.loads(path.read_text(encoding="utf-8")).get("collectors", [])
    return [c["id"] for c in collectors if c.get("enabled") and c.get("cadence") == "hourly"]


class CollectorScheduler:
    def __init__(self, config: ScheduleConfig, *, state_path: Path | None = None,
                 submit: Callable[..., object] | None = None, hourly_ids: list[str] | None = None):
        self.config = config
        self.state_path = state_path or data_dir() / "service-config" / "collect-schedule.json"
        self._submit = submit or collector_jobs.submit
        self._hourly_ids = hourly_ids

    def _load(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    @staticmethod
    def _due(last: str | None, interval: int, now: datetime) -> bool:
        if not last:
            return True
        try:
            return now - datetime.fromisoformat(last) >= timedelta(seconds=interval)
        except ValueError:
            return True

    def tick(self, now: datetime | None = None) -> str | None:
        """Submit at most one due batch; returns "full", "hourly" or None."""
        now = now or datetime.now(timezone.utc)
        state = self._load()
        if self._due(state.get("full_at"), self.config.full_interval_sec, now):
            kind, kwargs = "full", {"auto_import": True, "allow_partial_import": self.config.allow_partial_import}
        elif self._due(state.get("hourly_at"), self.config.hourly_interval_sec, now):
            ids = self._hourly_ids if self._hourly_ids is not None else hourly_collector_ids()
            if not ids:
                return None
            kind, kwargs = "hourly", {"ids": ids, "auto_import": False}
        else:
            return None
        try:
            job = self._submit(run_prefix=f"scheduled-{kind}", **kwargs)
        except CollectorBusyError:
            # A manual or remote run owns the pipeline; try again on the next tick.
            return None
        stamp = now.isoformat()
        state[f"{kind}_at"] = stamp
        if kind == "full":
            state["hourly_at"] = stamp  # the full batch includes every hourly collector
        state[f"{kind}_run_id"] = getattr(job, "run_id", None)
        self._save(state)
        return kind

    def run_forever(self) -> None:
        time.sleep(self.config.start_delay_sec)
        while True:
            try:
                kind = self.tick()
                if kind:
                    logger.info("scheduled %s collection submitted", kind)
            except Exception:  # keep the schedule alive; runs record their own failures
                logger.exception("collector schedule tick failed")
            time.sleep(self.config.tick_sec)


def start_background() -> None:
    config = ScheduleConfig.from_env()
    if not config.enabled or collection_mode() == "remote":
        return
    threading.Thread(target=CollectorScheduler(config).run_forever, name="collector-schedule", daemon=True).start()
