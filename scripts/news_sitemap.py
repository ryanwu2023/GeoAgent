"""Google News sitemap (``<urlset>`` with ``news:`` tags) parsing for RSS-template collectors.

Publishers list their last ~48 hours of articles in a news sitemap to be indexed by
Google News, so it is a stable, unthrottled discovery channel with the publisher's own
URL, headline, publication time and often keywords. Items come back in the same shape
as ``parse_rss`` items so the collectors' pipelines stay unchanged.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
NEWS = "{http://www.google.com/schemas/sitemap-news/0.9}"


def is_news_sitemap(body: str) -> bool:
    head = (body or "")[:4000]
    return "<urlset" in head and "sitemap-news" in head


def sitemap_children(body: str, limit: int = 3) -> list[str]:
    """Child sitemap URLs of a ``<sitemapindex>`` (first ``limit``), else []."""
    if "<sitemapindex" not in (body or "")[:4000]:
        return []
    return [html.unescape(u.strip()) for u in re.findall(r"<sitemap>\s*<loc>([^<]+)</loc>", body)][: max(0, limit)]


def parse_news_sitemap(body: str, language: str = "", cap: int = 1000) -> list[dict]:
    """Return items ``{title, link, pub, desc, source_name, keywords}``.

    ``language`` keeps only entries whose ``news:language`` matches (multi-language
    sitemaps such as BBC's list every World Service edition).
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    items = []
    for url in root.iter(f"{SM}url"):
        news = url.find(f"{NEWS}news")
        loc = (url.findtext(f"{SM}loc") or "").strip()
        if news is None or not loc:
            continue
        title = (news.findtext(f"{NEWS}title") or "").strip()
        lang = (news.findtext(f"{NEWS}publication/{NEWS}language") or "").strip().lower()
        if not title or (language and lang and not lang.startswith(language.lower())):
            continue
        items.append({
            "title": html.unescape(title), "link": loc,
            "pub": (news.findtext(f"{NEWS}publication_date") or "").strip(),
            "desc": "",
            "source_name": (news.findtext(f"{NEWS}publication/{NEWS}name") or "").strip(),
            "keywords": (news.findtext(f"{NEWS}keywords") or "").strip(),
        })
    return items[:cap]


def pub_datetime(value: str) -> datetime | None:
    """Aware UTC datetime from an RSS (RFC 822) or ISO 8601 timestamp; None if unparseable."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})([+-]\d{2})(\d{2})$", value)
            if not m:
                return None
            dt = datetime.fromisoformat(f"{m.group(1)}{m.group(2)}:{m.group(3)}")
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def coverage_gap(items: list[dict], last_ok_at: str) -> str:
    """ISO time of the oldest item if the feed no longer reaches back to the previous
    successful fetch (articles in between may have rolled off unseen), else "".

    Feeds where no item date parses are not judged.
    """
    prev = pub_datetime(last_ok_at)
    times = [t for t in (pub_datetime(i.get("pub", "")) for i in items) if t]
    if not prev or not times:
        return ""
    oldest = min(times)
    return oldest.isoformat(timespec="minutes") if oldest > prev else ""
