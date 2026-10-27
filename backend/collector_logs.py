"""Read-only, redacted collector log tails for the monitoring UI."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .collector_ledger import CollectorLedger


_SECRET = re.compile(
    r"(?i)([\"']?\b(?:api[_-]?key|access[_-]?token|authorization|cookie|password|token|proxy[_-]?auth)\b[\"']?\s*[:=]\s*)(?:Bearer\s+\[REDACTED\]|\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)"
)
_BEARER = re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+")


def redact(value: str) -> str:
    value = _BEARER.sub("Bearer [REDACTED]", value)
    return _SECRET.sub(r"\1[REDACTED]", value)


def _report_item(report_path: Path, collector_id: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    results = payload.get("results", []) if isinstance(payload, dict) else payload
    if not isinstance(results, list):
        return None
    return next((item for item in results if isinstance(item, dict) and str(item.get("id")) == collector_id), None)


def _event_item(ledger_path: Path, run_id: str, collector_id: str) -> dict[str, Any] | None:
    if not ledger_path.is_file():
        return None
    try:
        events = CollectorLedger(ledger_path).events(run_id, limit=500)
    except (OSError, ValueError):
        return None
    for event in reversed(events):
        payload = event.get("payload")
        if event.get("collector_id") == collector_id and isinstance(payload, dict):
            return payload
    return None


def read_log_tail(
    runs_root: Path,
    run_id: str,
    collector_id: str,
    *,
    stream: str = "stderr",
    tail: int = 2000,
) -> dict[str, Any]:
    if stream not in {"stdout", "stderr"}:
        raise ValueError("stream must be stdout or stderr")
    if tail < 1:
        raise ValueError("tail must be positive")
    run_dir = Path(runs_root) / run_id
    live_log = run_dir / collector_id / f"{stream}.log"
    if live_log.is_file():
        try:
            content = live_log.read_text(encoding="utf-8")
        except OSError:
            content = ""
        return {
            "available": True,
            "source": "live-log",
            "run_id": run_id,
            "collector_id": collector_id,
            "stream": stream,
            "content": redact(content[-tail:]),
        }
    report_path = run_dir / "results.json"
    item = _report_item(report_path, collector_id) if report_path.is_file() else None
    source = "results-tail"
    if item is None:
        item = _event_item(Path(runs_root) / "ledger.sqlite3", run_id, collector_id)
        source = "ledger-event"
    content = item.get(f"{stream}_tail") if item else None
    if not isinstance(content, str):
        return {
            "available": False,
            "source": "none",
            "run_id": run_id,
            "collector_id": collector_id,
            "stream": stream,
            "content": "",
        }
    return {
        "available": True,
        "source": source,
        "run_id": run_id,
        "collector_id": collector_id,
        "stream": stream,
        "content": redact(content[-tail:]),
    }
