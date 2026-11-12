"""Google News RSS helpers shared by the RSS-template collectors.

Google News items carry the real outlet only as a ``<source>`` element and a
``Headline - Outlet`` title suffix, and their links point at news.google.com.
These helpers recover the outlet and, within a per-run budget, the original
article URL so reports can cite Reuters/AP/BBC instead of "Google News".
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from typing import Callable, Iterable

GN_HOST = "news.google.com"
_BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
_TITLE_SUFFIX = re.compile(r"^(.*\S)\s+[-–—]\s+([^-–—]{2,60})$")


def is_google_news(url: str) -> bool:
    return urllib.parse.urlsplit(url or "").netloc.lower() == GN_HOST


def split_outlet(title: str, source_name: str = "") -> tuple[str, str]:
    """Return (headline, outlet) for a ``Headline - Outlet`` title.

    The ``<source>`` name wins when present; otherwise the suffix is accepted only
    if it looks like a name (≤8 words), so ordinary dashes in headlines survive.
    """
    title = (title or "").strip()
    source_name = (source_name or "").strip()
    if source_name:
        for sep in (" - ", " – ", " — "):
            if title.endswith(sep + source_name):
                return title[: -(len(sep) + len(source_name))].strip(), source_name
        return title, source_name
    m = _TITLE_SUFFIX.match(title)
    if m and len(m.group(2).split()) <= 8:
        return m.group(1).strip(), m.group(2).strip()
    return title, ""


def annotate_outlets(records: Iterable[dict]) -> int:
    """Fill ``outlet`` on Google News records (incl. archived ones) from the title."""
    n = 0
    for r in records:
        if r.get("outlet") or not is_google_news(r.get("url") or r.get("link") or ""):
            continue
        _, outlet = split_outlet(r.get("title", ""))
        if outlet:
            r["outlet"] = outlet
            n += 1
    return n


def outlet_key(name: str) -> str:
    """Normalize an outlet name so "The Guardian" / "Guardian" / "Bloomberg.com" compare equal."""
    s = re.sub(r"[^a-z0-9]+", "", (name or "").lower())
    s = re.sub(r"^the(?=.{3})", "", s)
    return re.sub(r"(?<=.{3})com$", "", s)


def headline_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", split_outlet(title)[0].lower())[:80]


def _direct_keys(records: Iterable[dict], aliases: dict[str, list[str]]) -> set[tuple[str, str]]:
    keys = set()
    for r in records:
        if is_google_news(r.get("url") or ""):
            continue
        hk = headline_key(r.get("title", ""))
        if not hk:
            continue
        for name in [r.get("publisher", "")] + list(aliases.get(r.get("source_id", ""), [])):
            if outlet_key(name):
                keys.add((hk, outlet_key(name)))
    return keys


def prefer_direct(records: list[dict], archive: dict, sources: Iterable[dict]) -> tuple[list[dict], int]:
    """Drop Google News copies of articles a direct feed already carries (same headline + outlet).

    Direct feeds give a summary and the publisher's own URL, so they win. Checks the
    incoming batch and the archive: direct feeds roll items off within a day while
    Google News keeps surfacing them for weeks. Matching archived Google News copies
    are removed from ``archive`` in place. Sources may list ``outlets`` aliases for
    names Google uses differently (e.g. NYT → "The New York Times").
    """
    aliases = {s["id"]: list(s.get("outlets") or []) for s in sources if s.get("id")}
    keys = _direct_keys(list(archive.values()) + list(records), aliases)

    def dup(r: dict) -> bool:
        return (is_google_news(r.get("url") or "") and bool(r.get("outlet"))
                and (headline_key(r.get("title", "")), outlet_key(r["outlet"])) in keys)

    kept = [r for r in records if not dup(r)]
    stale = [rid for rid, r in archive.items() if dup(r)]
    for rid in stale:
        del archive[rid]
    return kept, len(records) - len(kept) + len(stale)


def _article_id(url: str) -> str:
    parts = urllib.parse.urlsplit(url).path.rstrip("/").split("/")
    return parts[-1] if len(parts) >= 2 and parts[-2] == "articles" else ""


def resolve_article_url(url: str, open_url: Callable[[urllib.request.Request, float], bytes],
                        ua: str = "Mozilla/5.0", timeout: float = 20) -> str:
    """Resolve a news.google.com article link to the publisher URL ("" on failure).

    Current links (``CBMi…AU_yqL…``) no longer embed the URL, so this takes two
    requests: the article page for its signature/timestamp, then batchexecute.
    """
    aid = _article_id(url)
    if not aid:
        return ""
    page = open_url(urllib.request.Request(f"https://{GN_HOST}/articles/{aid}",
                                           headers={"User-Agent": ua}), timeout)
    text = page.decode("utf-8", "replace")
    sg = re.search(r'data-n-a-sg="([^"]+)"', text)
    ts = re.search(r'data-n-a-ts="([^"]+)"', text)
    if not (sg and ts):
        return ""
    inner = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
             '"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
             f'"{aid}",{ts.group(1)},"{sg.group(1)}"]')
    data = urllib.parse.urlencode({"f.req": json.dumps([[["Fbv4je", inner]]])}).encode()
    body = open_url(urllib.request.Request(_BATCH_URL, data=data, headers={
        "User-Agent": ua, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}), timeout)
    try:
        payload = json.loads(body.decode("utf-8", "replace").split("\n\n", 1)[1])[:-2]
        resolved = json.loads(payload[0][2])[1]
    except (IndexError, ValueError, TypeError):
        return ""
    return resolved if isinstance(resolved, str) and resolved.startswith("http") and not is_google_news(resolved) else ""


def resolve_records(records: Iterable[dict], open_url: Callable[[urllib.request.Request, float], bytes], *,
                    max_new: int, ua: str = "Mozilla/5.0", timeout: float = 20,
                    min_interval: float = 3.0, max_consecutive_failures: int = 2) -> dict:
    """Set ``original_url`` on up to ``max_new`` unresolved Google News records, newest first.

    Results are stored on the archived record itself, so each article is resolved once.
    Failures are counted and retried on a later run; they never fail the collector.
    Google answers bursts on the article endpoint with a 429 /sorry/ page (seen after
    ~10 lookups from one IP), so consecutive failures stop the run before the block
    can spread to the RSS search the collectors depend on.
    """
    todo = [r for r in records
            if not r.get("original_url") and is_google_news(r.get("url") or "")]
    todo.sort(key=lambda r: r.get("published") or "", reverse=True)
    stats = {"pending": len(todo), "resolved": 0, "failed": 0, "stopped": False}
    streak = 0
    for r in todo[: max(0, int(max_new or 0))]:
        try:
            resolved = resolve_article_url(r["url"], open_url, ua=ua, timeout=timeout)
        except Exception:  # network/HTTP errors: leave for the next run
            resolved = ""
        if resolved:
            r["original_url"] = resolved
            stats["resolved"] += 1
            streak = 0
        else:
            stats["failed"] += 1
            streak += 1
            if streak >= max_consecutive_failures:
                stats["stopped"] = True
                break
        time.sleep(min_interval)
    return stats
