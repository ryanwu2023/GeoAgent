"""遵守公开访问边界的 HTTP、候选发现和新闻正文解析。"""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import json
import re
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser


RETRYABLE = {429, 502, 503}
TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
RESTRICTED_MARKERS = (
    "please sign in", "sign in to continue", "subscribe to continue", "subscription required",
    "already a subscriber", "enable javascript and cookies", "verify you are human",
    "access denied", "captcha", "请登录", "订阅后继续", "访问验证",
)


@dataclass
class TransportResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    final_url: str


@dataclass
class FetchResult:
    status: int
    body: str
    final_url: str
    headers: dict[str, str]
    error_type: str = ""
    error_message: str = ""
    from_cache: bool = False


@dataclass
class Candidate:
    url: str
    title: str
    summary: str = ""
    published: str = ""
    discovery_method: str = "listing"


@dataclass
class ParsedArticle:
    title: str
    summary: str
    body: str
    published: str
    content_access: str
    error_type: str = ""


def canonicalize_url(url: str) -> str:
    split = urlsplit(url)
    query = [
        (key, value) for key, value in parse_qsl(split.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS
    ]
    path = re.sub(r"/{2,}", "/", split.path or "/")
    return urlunsplit((split.scheme.lower(), split.netloc.lower(), path, urlencode(query), ""))


def _default_transport(request: Request, timeout: float) -> TransportResponse:
    try:
        with urlopen(request, timeout=timeout) as response:
            return TransportResponse(
                int(getattr(response, "status", 200)),
                dict(response.headers.items()),
                response.read(),
                response.geturl(),
            )
    except HTTPError as exc:
        raise exc


def _decode(body: bytes, headers: dict[str, str]) -> str:
    content_type = next((value for key, value in headers.items() if key.lower() == "content-type"), "")
    match = re.search(r"charset=([\w-]+)", content_type, re.I)
    encodings = [match.group(1)] if match else []
    encodings.extend(["utf-8", "windows-1252", "gb18030"])
    for encoding in encodings:
        try:
            return body.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode("utf-8", errors="replace")


class PolicyHttpClient:
    def __init__(
        self,
        *,
        user_agent: str,
        transport: Callable[[Request, float], TransportResponse] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        robots_fetcher: Callable[[str], str] | None = None,
    ):
        self.user_agent = user_agent
        self.transport = transport or _default_transport
        self.sleep = sleep
        self.robots_fetcher = robots_fetcher
        self.cache: dict[str, dict[str, Any]] = {}
        self.robot_cache: dict[str, RobotFileParser] = {}
        self.last_request_at: dict[str, float] = {}

    def _robot_parser(self, url: str, timeout: float) -> RobotFileParser:
        split = urlsplit(url)
        root = f"{split.scheme}://{split.netloc}"
        if root in self.robot_cache:
            return self.robot_cache[root]
        parser = RobotFileParser()
        parser.set_url(root + "/robots.txt")
        try:
            if self.robots_fetcher:
                text = self.robots_fetcher(root + "/robots.txt")
            else:
                request = Request(root + "/robots.txt", headers={"User-Agent": self.user_agent})
                response = self.transport(request, timeout)
                text = _decode(response.body, response.headers) if response.status == 200 else ""
            parser.parse(text.splitlines())
        except Exception:
            # robots 无法读取时只访问来源配置里的公开入口及同域公开链接；
            # 真实 401/403 仍由正文请求返回并停止重试。
            parser.parse([])
        self.robot_cache[root] = parser
        return parser

    def fetch(self, url: str, source: dict[str, Any]) -> FetchResult:
        clean_url = canonicalize_url(url)
        split = urlsplit(clean_url)
        allowed = {str(host).lower() for host in source.get("allowed_hosts", [])}
        if split.scheme not in {"http", "https"} or (split.hostname or "").lower() not in allowed:
            return FetchResult(0, "", clean_url, {}, "host_not_allowed", "URL 不在来源允许域名内")
        timeout = float(source.get("timeout_seconds", 25))
        if not self._robot_parser(clean_url, timeout).can_fetch(self.user_agent, clean_url):
            return FetchResult(0, "", clean_url, {}, "robots_disallowed", "robots.txt 禁止访问")

        host = (split.hostname or "").lower()
        interval = float(source.get("rate_limit_seconds", 0))
        elapsed = time.monotonic() - self.last_request_at.get(host, 0.0)
        if interval > elapsed:
            self.sleep(interval - elapsed)
        cached = self.cache.get(clean_url, {})
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.5",
            "Accept-Language": "en,zh-CN;q=0.8,*;q=0.5",
        }
        if cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        if cached.get("last_modified"):
            headers["If-Modified-Since"] = cached["last_modified"]
        retries = int(source.get("max_retries", 2))
        for attempt in range(retries + 1):
            request = Request(clean_url, headers=headers)
            try:
                response = self.transport(request, timeout)
                self.last_request_at[host] = time.monotonic()
                if response.status in RETRYABLE and attempt < retries:
                    retry_after = float(response.headers.get("Retry-After", 0) or 0)
                    self.sleep(max(retry_after, min(2 ** attempt, 8)))
                    continue
                if response.status == 304 and cached:
                    return FetchResult(304, cached["body"], response.final_url, response.headers, from_cache=True)
                text = _decode(response.body, response.headers)
                if response.status == 200:
                    lower_headers = {key.lower(): value for key, value in response.headers.items()}
                    self.cache[clean_url] = {
                        "body": text,
                        "etag": lower_headers.get("etag", ""),
                        "last_modified": lower_headers.get("last-modified", ""),
                    }
                error = "" if 200 <= response.status < 400 else f"http_{response.status}"
                return FetchResult(response.status, text, response.final_url, response.headers, error)
            except HTTPError as exc:
                self.last_request_at[host] = time.monotonic()
                if exc.code in RETRYABLE and attempt < retries:
                    self.sleep(min(2 ** attempt, 8))
                    continue
                return FetchResult(exc.code, "", clean_url, dict(exc.headers or {}), f"http_{exc.code}", str(exc.reason)[:160])
            except (URLError, TimeoutError, OSError) as exc:
                self.last_request_at[host] = time.monotonic()
                if attempt < retries:
                    self.sleep(min(2 ** attempt, 8))
                    continue
                return FetchResult(0, "", clean_url, {}, type(exc).__name__.lower(), str(exc)[:160])
        return FetchResult(0, "", clean_url, {}, "request_failed", "请求未返回结果")


class _DocumentParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.meta: dict[str, str] = {}
        self.json_ld: list[str] = []
        self.headings: list[str] = []
        self.paragraphs: list[str] = []
        self._anchor_href = ""
        self._anchor_text: list[str] = []
        self._capture_json = False
        self._json_parts: list[str] = []
        self._capture_heading = False
        self._heading_parts: list[str] = []
        self._capture_paragraph = False
        self._paragraph_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {key.lower(): value or "" for key, value in attrs}
        if tag == "a" and data.get("href"):
            self._anchor_href = data["href"]
            self._anchor_text = []
        elif tag == "meta":
            key = (data.get("property") or data.get("name") or "").lower()
            if key and data.get("content"):
                self.meta[key] = data["content"].strip()
        elif tag == "script" and "ld+json" in data.get("type", "").lower():
            self._capture_json = True
            self._json_parts = []
        elif tag in {"h1", "h2", "h3"}:
            self._capture_heading = True
            self._heading_parts = []
        elif tag == "p":
            self._capture_paragraph = True
            self._paragraph_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._anchor_href:
            self.links.append((self._anchor_href, _clean_text(" ".join(self._anchor_text))))
            self._anchor_href = ""
            self._anchor_text = []
        elif tag == "script" and self._capture_json:
            self.json_ld.append("".join(self._json_parts).strip())
            self._capture_json = False
        elif tag in {"h1", "h2", "h3"} and self._capture_heading:
            value = _clean_text(" ".join(self._heading_parts))
            if value:
                self.headings.append(value)
            self._capture_heading = False
        elif tag == "p" and self._capture_paragraph:
            value = _clean_text(" ".join(self._paragraph_parts))
            if value:
                self.paragraphs.append(value)
            self._capture_paragraph = False

    def handle_data(self, data: str) -> None:
        if self._anchor_href:
            self._anchor_text.append(data)
        if self._capture_json:
            self._json_parts.append(data)
        if self._capture_heading:
            self._heading_parts.append(data)
        if self._capture_paragraph:
            self._paragraph_parts.append(data)


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", unescape(str(value or ""))).strip()


def _json_objects(parser: _DocumentParser) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            objects.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for raw in parser.json_ld:
        try:
            visit(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
    return objects


def discover_candidates(html_text: str, base_url: str, source: dict[str, Any]) -> list[Candidate]:
    parser = _DocumentParser()
    parser.feed(html_text)
    allowed = {host.lower() for host in source.get("allowed_hosts", [])}
    found: dict[str, Candidate] = {}
    for href, title in parser.links:
        url = canonicalize_url(urljoin(base_url, href))
        if urlsplit(url).scheme not in {"http", "https"} or (urlsplit(url).hostname or "").lower() not in allowed:
            continue
        if len(title) < 8:
            continue
        found[url] = Candidate(url, title, discovery_method=source.get("discovery_mode", "listing"))
    for item in _json_objects(parser):
        kind = str(item.get("@type") or "").lower()
        if kind not in {"newsarticle", "article", "listitem"} and not item.get("url"):
            continue
        url_value = item.get("url") or item.get("item")
        if isinstance(url_value, dict):
            url_value = url_value.get("url") or url_value.get("@id")
        url = canonicalize_url(urljoin(base_url, str(url_value or "")))
        title = _clean_text(item.get("headline") or item.get("name"))
        if not title or (urlsplit(url).hostname or "").lower() not in allowed:
            continue
        found[url] = Candidate(
            url,
            title,
            _clean_text(item.get("description")),
            _clean_text(item.get("datePublished")),
            "json_ld",
        )
    return list(found.values())


def parse_public_article(html_text: str, url: str, source: dict[str, Any]) -> ParsedArticle:
    del url, source
    parser = _DocumentParser()
    try:
        parser.feed(html_text)
    except Exception:
        return ParsedArticle("", "", "", "", "unavailable", "parse_error")
    objects = _json_objects(parser)
    article = next((item for item in objects if str(item.get("@type") or "").lower() in {"newsarticle", "article", "reportagenewsarticle"}), {})
    title = _clean_text(
        article.get("headline") or parser.meta.get("og:title") or parser.meta.get("twitter:title") or (parser.headings[0] if parser.headings else "")
    )
    summary = _clean_text(article.get("description") or parser.meta.get("description") or parser.meta.get("og:description"))
    published = _clean_text(article.get("datePublished") or parser.meta.get("article:published_time") or parser.meta.get("date"))
    article_body = _clean_text(article.get("articleBody"))
    paragraphs = [value for value in parser.paragraphs if len(value) >= 30]
    body = article_body or "\n".join(paragraphs)
    lowered = _clean_text(html_text).casefold()
    restricted = any(marker in lowered for marker in RESTRICTED_MARKERS)
    if restricted:
        return ParsedArticle(title, summary, "", published, "restricted_metadata_only")
    if body and len(body) >= 120:
        return ParsedArticle(title, summary, body, published, "public_fulltext")
    if title and summary:
        return ParsedArticle(title, summary, "", published, "public_summary")
    return ParsedArticle(title, summary, "", published, "unavailable", "parse_error")

