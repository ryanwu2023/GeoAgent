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
_XML_DECLARATION = re.compile(r"^\s*<\?xml\b[^>]*\?>", re.I)
# 单字节西文编码与 UTF-16/32 不做乱码还原（前者解码本身无损，后者不兼容 ASCII）
_NO_REDECODE = ("utf-16", "utf-32", "iso-8859", "iso8859", "latin", "windows-125", "cp125", "ascii", "us-ascii")
_DECLARED_ENCODING = re.compile(r"""encoding\s*=\s*["']([A-Za-z0-9._-]+)["']""", re.I)


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


def _without_declaration(xml_text: str) -> str:
    """去掉 XML 声明：传进来的已是解码后的文本，声明里的 gb2312/gbk 等编码会让解析器按错误编码重读。

    响应头没给 charset 时，HTTP 层可能先按 windows-1252 解出了乱码；能无损还原成字节时按声明的编码重解。
    """
    text = xml_text.lstrip("\ufeff").strip()
    match = _XML_DECLARATION.match(text)
    if not match:
        return text
    declared = _DECLARED_ENCODING.search(match.group(0))
    body = text[match.end():]
    name = declared.group(1).lower().replace("_", "-") if declared else ""
    # 只对兼容 ASCII 的非 UTF-8 编码、且正文含非 ASCII 字符（可能是乱码）时尝试还原
    if name and name not in {"utf-8", "utf8"} and not name.startswith(_NO_REDECODE) and not body.isascii():
        try:
            body = body.encode("windows-1252").decode(declared.group(1))
        except (UnicodeError, LookupError, ValueError):
            pass
    return body


LINKS_OUTSIDE_ALLOWED_HOSTS = "LINKS_OUTSIDE_ALLOWED_HOSTS"


class CandidateList(list):
    """候选列表；dropped_hosts 记录因域名不在 allowed_hosts 而被丢弃的条目链接（域名 -> 条数）。"""

    def __init__(self, items=(), dropped_hosts: dict[str, int] | None = None):
        super().__init__(items)
        self.dropped_hosts: dict[str, int] = dict(dropped_hosts or {})


def merge_dropped(total: dict[str, int], candidates: object) -> None:
    for host, count in getattr(candidates, "dropped_hosts", {}).items():
        total[host] = total.get(host, 0) + count


def outside_hosts_message(dropped: dict[str, int]) -> str:
    top = sorted(dropped.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    listed = "、".join(f"{host}（{count} 条）" for host, count in top)
    return f"订阅源条目链接的域名不在允许列表：{listed}，请把这些域名加入 allowed_hosts"


def discover_feed_candidates(xml_text: str, base_url: str, source: dict[str, Any]) -> list[Candidate]:
    if len(xml_text.encode("utf-8", errors="ignore")) > MAX_FEED_BYTES:
        raise FeedParseError("订阅源超过 5 MB 上限")
    if "<!ENTITY" in xml_text:
        raise FeedParseError("订阅源含实体声明，已拒绝")
    try:
        root = ET.fromstring(_without_declaration(xml_text).encode("utf-8"))
    except (ET.ParseError, RecursionError, ValueError, LookupError, UnicodeError) as exc:
        raise FeedParseError(f"订阅源不是有效 XML：{exc}") from exc
    allowed = {host.lower() for host in source.get("allowed_hosts", [])}
    found: dict[str, Candidate] = {}
    dropped: dict[str, int] = {}
    for element in root.iter():
        if _local(element.tag) not in {"item", "entry"}:
            continue
        href = _link(element)
        title = _text(element, "title")
        if not href or not title:
            continue
        url = canonicalize_url(urljoin(base_url, href))
        split = urlsplit(url)
        if split.scheme not in {"http", "https"}:
            continue
        if (split.hostname or "").lower() not in allowed:
            host = (split.hostname or "").lower()
            if host:
                dropped[host] = dropped.get(host, 0) + 1
            continue
        found[url] = Candidate(
            url,
            title,
            _text(element, "description", "summary", "content"),
            _iso_utc(_text(element, "pubdate", "published", "updated", "date")),
            "rss",
        )
    return CandidateList(found.values(), dropped)
