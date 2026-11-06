"""Article body fetching for RSS-template collectors.

Reuses geopolitical-source-monitor's public-access client (robots.txt, per-host
pacing, HTTP cache) and article parser (JSON-LD/paragraphs, paywall markers), so
the RSS collectors follow the same access boundary instead of a second scraper.
Only sources that opt in with ``"body": true`` are fetched; paywalled outlets
stay title + summary.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlsplit

_WEB_PATH = Path(__file__).resolve().parents[1] / "collectors" / "geopolitical-source-monitor" / "web.py"
_web = None
_FINAL_ERRORS = {"robots_disallowed", "host_not_allowed"}


def _web_module():
    global _web
    if _web is None:
        # The collector directory name has a hyphen, so load web.py by path; dataclasses
        # need the module registered in sys.modules while it executes.
        spec = importlib.util.spec_from_file_location("geo_source_web", _WEB_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _web = module
    return _web


def make_client(ua: str, transport: Callable | None = None):
    return _web_module().PolicyHttpClient(user_agent=ua, transport=transport)


def fetch_bodies(records: Iterable[dict], sources: Iterable[dict], client, *, max_new: int,
                 max_chars: int = 6000, rate_limit_seconds: float = 2.0, timeout: float = 25) -> dict:
    """Fill ``body`` / ``content_access`` on up to ``max_new`` records, newest first.

    Parsed pages and refusals (4xx, robots) set ``content_access`` (public_fulltext,
    public_summary, restricted_metadata_only, unavailable, http_403…), so each article
    is tried once; network errors leave it unset for the next run.
    """
    web = _web_module()
    opted = {s["id"] for s in sources if s.get("body")}
    todo = [r for r in records
            if r.get("source_id") in opted and not r.get("content_access")
            and urlsplit(r.get("url") or "").scheme in {"http", "https"}]
    todo.sort(key=lambda r: r.get("published") or "", reverse=True)
    stats = {"pending": len(todo), "fulltext": 0, "other": 0}
    for r in todo[: max(0, int(max_new or 0))]:
        url = r["url"]
        # The link comes from the source's own feed, so its host is the allowed one.
        policy = {"allowed_hosts": [(urlsplit(url).hostname or "").lower()],
                  "rate_limit_seconds": rate_limit_seconds, "timeout_seconds": timeout, "max_retries": 1}
        fetched = client.fetch(url, policy)
        if fetched.error_type:
            stats["other"] += 1
            # Timeouts and 5xx are retried next run; refusals are final.
            if fetched.error_type.startswith("http_4") or fetched.error_type in _FINAL_ERRORS:
                r["content_access"] = fetched.error_type
            continue
        article = web.parse_public_article(fetched.body, fetched.final_url or url, policy)
        r["content_access"] = article.content_access
        if article.content_access == "public_fulltext":
            r["body"] = article.body[:max_chars]
            stats["fulltext"] += 1
        else:
            stats["other"] += 1
    return stats
