"""RSS 2.0 与 Atom 订阅源的候选发现（仅用标准库解析）。"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
from typing import Any
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

from web import Candidate, canonicalize_url


MAX_FEED_BYTES = 5 * 1024 * 1024


class FeedParseError(ValueError):
    """订阅源不是可解析的 XML，或超过大小上限。"""


def _local(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _text(element: ET.Element, *names: str) -> str:
    for child in element:
        if _local(child.tag) in names:
            value = "".join(child.itertext()).strip()
            if value:
                return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()
    return ""


def _link(element: ET.Element) -> str:
    fallback = ""
    for child in element:
        if _local(child.tag) != "link":
            continue
        href = (child.get("href") or "").strip()
        if href:
            if child.get("rel", "alternate") == "alternate":
                return href
            fallback = fallback or href
        elif (child.text or "").strip():
            return child.text.strip()
    return fallback


def _iso_utc(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed: datetime | None = None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def discover_feed_candidates(xml_text: str, base_url: str, source: dict[str, Any]) -> list[Candidate]:
    if len(xml_text.encode("utf-8", errors="ignore")) > MAX_FEED_BYTES:
        raise FeedParseError("订阅源超过 5 MB 上限")
    if "<!ENTITY" in xml_text:
        raise FeedParseError("订阅源含实体声明，已拒绝")
    try:
        root = ET.fromstring(xml_text.strip().encode("utf-8"))
    except ET.ParseError as exc:
        raise FeedParseError(f"订阅源不是有效 XML：{exc}") from exc
    allowed = {host.lower() for host in source.get("allowed_hosts", [])}
    found: dict[str, Candidate] = {}
    for element in root.iter():
        if _local(element.tag) not in {"item", "entry"}:
            continue
        href = _link(element)
        title = _text(element, "title")
        if not href or not title:
            continue
        url = canonicalize_url(urljoin(base_url, href))
        split = urlsplit(url)
        if split.scheme not in {"http", "https"} or (split.hostname or "").lower() not in allowed:
            continue
        found[url] = Candidate(
            url,
            title,
            _text(element, "description", "summary", "content"),
            _iso_utc(_text(element, "pubdate", "published", "updated", "date")),
            "rss",
        )
    return list(found.values())
