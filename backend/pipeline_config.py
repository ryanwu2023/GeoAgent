"""采集管线配置的校验与翻译。

这是网页（以及后续 PiChat/MCP）提交管线配置的唯一校验入口，包含 SSRF 防护：
所有 URL 的主机必须在来源 allowed_hosts 内，且解析结果不得落在内网/保留地址。
"""
from __future__ import annotations

import copy
import ipaddress
import re
import socket
from typing import Any, Callable
from urllib.parse import urlsplit

SCHEMA_VERSION = "pipeline/1"
VALID_TOPICS = ("usiran", "ukraine")
VALID_TIERS = ("T1", "T2", "T3")
VALID_POLICIES = ("public_fulltext", "metadata_only")
VALID_TYPES = ("web", "rss")
SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

MAX_SOURCES = 30
MAX_TERMS_TOTAL = 200
MAX_TERM_LEN = 100
MAX_PATTERN_LEN = 200
MAX_ENTRY_URLS = 10
MAX_ALLOWED_HOSTS = 10
MAX_URL_LEN = 2000

ROW_KEYS = ("name", "description", "topics")
TOP_KEYS = {"schema_version", "sources", "filters", "request"}
SOURCE_KEYS = {"id", "type", "name", "feed_url", "entry_urls", "allowed_hosts", "language", "tier",
               "content_policy", "link_include_pattern"}
FILTER_DEFAULTS = {"include_any": [], "include_strong": [], "exclude": [], "require_category": True,
                   "lookback_days": 14, "max_candidates_per_source": 80}
REQUEST_DEFAULTS = {"timeout_seconds": 25, "rate_limit_seconds": 1.5, "max_pages_per_source": 3}
# (最小值, 最大值, 是否整数)
FILTER_RANGES = {"lookback_days": (1, 90, True), "max_candidates_per_source": (1, 200, True)}
REQUEST_RANGES = {"timeout_seconds": (1, 60, False), "rate_limit_seconds": (0, 30, False),
                  "max_pages_per_source": (1, 5, True)}


class PipelineConfigError(ValueError):
    def __init__(self, field: str, message: str):
        super().__init__(f"{field}：{message}")
        self.field = field
        self.message = message


def default_resolve(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _text(value: Any, field: str, *, max_len: int, min_len: int = 1, strip: bool = True) -> str:
    if not isinstance(value, str):
        raise PipelineConfigError(field, "必须是字符串")
    text = value.strip() if strip else value
    if len(text) < min_len:
        raise PipelineConfigError(field, "不能为空")
    if len(text) > max_len:
        raise PipelineConfigError(field, f"长度不能超过 {max_len} 个字符")
    return text


# ---------------------------------------------------------------- row

def _validate_row(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise PipelineConfigError("row", "必须是对象")
    for key in row:
        if key not in ROW_KEYS:
            raise PipelineConfigError(f"row.{key}", "未知字段（管线不单独定时，只接受名称、说明、主题）")
    name = _text(row.get("name"), "row.name", max_len=100)
    description = row.get("description") or ""
    description = _text(description, "row.description", max_len=2000, min_len=0)
    raw = row.get("topics")
    if (not isinstance(raw, list) or not raw
            or any(not isinstance(t, str) or t not in VALID_TOPICS for t in raw)):
        raise PipelineConfigError("row.topics", f"主题至少选一个，且只能是 {'、'.join(VALID_TOPICS)}")
    topics = [t for t in VALID_TOPICS if t in raw]
    return {"name": name, "description": description, "topics": topics}


def validate_row(row: Any) -> dict[str, Any]:
    """只校验行字段（name/description/topics），不涉及配置；其它字段一律拒绝。"""
    return _validate_row(row)


# ---------------------------------------------------------------- URL / SSRF

def _forbidden_address(raw: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return True  # 无法识别的地址按不安全处理
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    # not is_global 另覆盖 100.64.0.0/10（CGNAT、含阿里云元数据 100.100.100.200）等共享地址段
    return (not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified)


class _HostChecker:
    def __init__(self, resolve: Callable[[str], list[str]]):
        self._resolve = resolve
        self._cache: dict[str, str | None] = {}

    def problem(self, host: str) -> str | None:
        if host in self._cache:
            return self._cache[host]
        result = self._check(host)
        self._cache[host] = result
        return result

    def _check(self, host: str) -> str | None:
        try:
            ipaddress.ip_address(host)
            addresses = [host]
        except ValueError:
            try:
                addresses = list(self._resolve(host))
            except (OSError, UnicodeError):
                return f"无法解析域名：{host}"
            if not addresses:
                return f"无法解析域名：{host}"
        if any(_forbidden_address(a) for a in addresses):
            return f"域名 {host} 解析到内网或保留地址，已拒绝"
        return None


def _check_url(url: Any, field: str, allowed: list[str], checker: _HostChecker) -> str:
    url = _text(url, field, max_len=MAX_URL_LEN)
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        parts.port  # noqa: B018  触发端口格式校验
    except ValueError as exc:
        raise PipelineConfigError(field, "URL 格式无效") from exc
    if parts.scheme not in ("http", "https"):
        raise PipelineConfigError(field, "只允许 http 或 https 链接")
    if not host:
        raise PipelineConfigError(field, "URL 缺少主机名")
    if parts.username is not None or parts.password is not None:
        raise PipelineConfigError(field, "URL 不允许包含用户名或密码")
    if host not in allowed:
        raise PipelineConfigError(field, f"主机 {host} 不在该来源的 allowed_hosts 内")
    problem = checker.problem(host)
    if problem:
        raise PipelineConfigError(field, problem)
    return url


# ---------------------------------------------------------------- config

def _unknown_keys(obj: dict, allowed: set[str], prefix: str) -> None:
    for key in obj:
        if key not in allowed:
            raise PipelineConfigError(f"{prefix}{key}", "未知字段")


_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def _valid_hostname(host: str) -> bool:
    if not host or len(host) > 253 or host != host.lower():
        return False
    return all(_LABEL_RE.match(label) for label in host.split("."))


def _validate_source(src: Any, index: int, checker: _HostChecker, seen: set[str]) -> dict[str, Any]:
    p = f"sources[{index}]"
    if not isinstance(src, dict):
        raise PipelineConfigError(p, "必须是对象")
    _unknown_keys(src, SOURCE_KEYS, f"{p}.")
    sid = src.get("id")
    if not isinstance(sid, str) or not SOURCE_ID_RE.match(sid):
        raise PipelineConfigError(f"{p}.id", "来源 ID 只能含小写字母、数字、连字符，以字母或数字开头，最长 64 位")
    if sid in seen:
        raise PipelineConfigError(f"{p}.id", f"来源 ID 重复：{sid}")
    seen.add(sid)
    stype = src.get("type")
    if stype not in VALID_TYPES:
        raise PipelineConfigError(f"{p}.type", "来源类型只能是 web 或 rss")
    name = _text(src.get("name"), f"{p}.name", max_len=100)
    language = _text(src.get("language") or "en", f"{p}.language", max_len=16)
    if src.get("tier") not in VALID_TIERS:
        raise PipelineConfigError(f"{p}.tier", "tier 只能是 T1、T2、T3")
    if src.get("content_policy") not in VALID_POLICIES:
        raise PipelineConfigError(f"{p}.content_policy", "content_policy 只能是 public_fulltext 或 metadata_only")
    hosts = src.get("allowed_hosts")
    if not isinstance(hosts, list) or not hosts or len(hosts) > MAX_ALLOWED_HOSTS:
        raise PipelineConfigError(f"{p}.allowed_hosts", f"allowed_hosts 必须是 1 ~ {MAX_ALLOWED_HOSTS} 个域名的数组")
    for h in hosts:
        if not isinstance(h, str) or not _valid_hostname(h):
            raise PipelineConfigError(f"{p}.allowed_hosts", f"必须是小写主机名（不含协议、路径、端口）：{h!r}")
    out: dict[str, Any] = {"id": sid, "type": stype, "name": name, "allowed_hosts": list(hosts),
                           "language": language, "tier": src["tier"], "content_policy": src["content_policy"]}
    if stype == "rss":
        if "entry_urls" in src:
            raise PipelineConfigError(f"{p}.entry_urls", "rss 来源使用 feed_url，不应填写 entry_urls")
        if not src.get("feed_url"):
            raise PipelineConfigError(f"{p}.feed_url", "rss 来源必须填写 feed_url")
        out["feed_url"] = _check_url(src["feed_url"], f"{p}.feed_url", hosts, checker)
    else:
        if "feed_url" in src:
            raise PipelineConfigError(f"{p}.feed_url", "web 来源使用 entry_urls，不应填写 feed_url")
        urls = src.get("entry_urls")
        if not isinstance(urls, list) or not urls or len(urls) > MAX_ENTRY_URLS:
            raise PipelineConfigError(f"{p}.entry_urls", f"web 来源的 entry_urls 必须是 1 ~ {MAX_ENTRY_URLS} 个链接的数组")
        out["entry_urls"] = [_check_url(u, f"{p}.entry_urls[{i}]", hosts, checker) for i, u in enumerate(urls)]
    pattern = src.get("link_include_pattern")
    if pattern not in (None, ""):
        pattern = _text(pattern, f"{p}.link_include_pattern", max_len=MAX_PATTERN_LEN, strip=False)
        try:
            re.compile(pattern)
        except re.error as exc:
            raise PipelineConfigError(f"{p}.link_include_pattern", f"不是有效的正则表达式：{exc}") from exc
        out["link_include_pattern"] = pattern
    return out


def _validate_number(value: Any, field: str, lo: float, hi: float, integer: bool) -> Any:
    if not _is_number(value) or (integer and not isinstance(value, int)):
        raise PipelineConfigError(field, "必须是整数" if integer else "必须是数字")
    if not lo <= value <= hi:
        raise PipelineConfigError(field, f"取值范围 {lo} ~ {hi}")
    return value


def _validate_filters(filters: Any) -> dict[str, Any]:
    if filters is None:
        filters = {}
    if not isinstance(filters, dict):
        raise PipelineConfigError("filters", "必须是对象")
    _unknown_keys(filters, set(FILTER_DEFAULTS), "filters.")
    out = copy.deepcopy(FILTER_DEFAULTS)
    total = 0
    for key in ("include_any", "include_strong", "exclude"):
        if key not in filters:
            continue
        terms = filters[key]
        if not isinstance(terms, list):
            raise PipelineConfigError(f"filters.{key}", "必须是数组")
        out[key] = [_text(t, f"filters.{key}", max_len=MAX_TERM_LEN) for t in terms]
        total += len(out[key])
    if total > MAX_TERMS_TOTAL:
        raise PipelineConfigError("filters", f"锚词、强锚词、排除词合计不能超过 {MAX_TERMS_TOTAL} 个（当前 {total}）")
    if "require_category" in filters:
        if not isinstance(filters["require_category"], bool):
            raise PipelineConfigError("filters.require_category", "必须是布尔值")
        out["require_category"] = filters["require_category"]
    for key, (lo, hi, integer) in FILTER_RANGES.items():
        if key in filters:
            out[key] = _validate_number(filters[key], f"filters.{key}", lo, hi, integer)
    return out


def _validate_request(request: Any) -> dict[str, Any]:
    if request is None:
        request = {}
    if not isinstance(request, dict):
        raise PipelineConfigError("request", "必须是对象")
    _unknown_keys(request, set(REQUEST_DEFAULTS), "request.")
    out = dict(REQUEST_DEFAULTS)
    for key, (lo, hi, integer) in REQUEST_RANGES.items():
        if key in request:
            out[key] = _validate_number(request[key], f"request.{key}", lo, hi, integer)
    return out


def validate_pipeline(row: dict, config: dict, *,
                      resolve: Callable[[str], list[str]] = default_resolve) -> tuple[dict, dict]:
    """校验并规范化 (row, config)；任何问题抛 PipelineConfigError。不修改入参。"""
    clean_row = _validate_row(row)
    if not isinstance(config, dict):
        raise PipelineConfigError("config", "必须是对象")
    _unknown_keys(config, TOP_KEYS, "")
    if config.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise PipelineConfigError("schema_version", f"只支持 {SCHEMA_VERSION}")
    sources = config.get("sources")
    if not isinstance(sources, list) or not sources:
        raise PipelineConfigError("sources", "至少需要 1 个来源")
    if len(sources) > MAX_SOURCES:
        raise PipelineConfigError("sources", f"来源最多 {MAX_SOURCES} 个")
    checker = _HostChecker(resolve)
    seen: set[str] = set()
    clean_sources = [_validate_source(s, i, checker, seen) for i, s in enumerate(sources)]
    clean_config = {"schema_version": SCHEMA_VERSION, "sources": clean_sources,
                    "filters": _validate_filters(config.get("filters")),
                    "request": _validate_request(config.get("request"))}
    return clean_row, clean_config


# ---------------------------------------------------------------- 翻译

def to_monitor_config(row: dict, config: dict, base: dict) -> dict:
    """把管线配置翻译为 geopolitical-source-monitor 的配置（不修改入参）。"""
    out = copy.deepcopy(base)
    filters = config["filters"]
    topics = list(row["topics"])
    out["name"] = row.get("name") or out.get("name")
    out["topics"] = topics
    out["topic_terms"] = {t: {"anchors": [*filters["include_any"], *filters["include_strong"]],
                              "strong_anchors": list(filters["include_strong"])} for t in topics}
    # 准入后归属哪些主题，参考多源采集器内置的主题锚词（设计 §6.2.1）
    out["topic_hints"] = {t: list((base.get("topic_terms", {}).get(t) or {}).get("anchors", [])) for t in topics}
    out["exclusion_terms"] = [*base.get("exclusion_terms", []), *filters["exclude"]]
    out["require_category"] = filters["require_category"]
    out["lookback_days"] = filters["lookback_days"]
    request = dict(out.get("request") or {})
    request.update(config["request"])
    request["max_candidates_per_source"] = filters["max_candidates_per_source"]
    out["request"] = request
    sources = []
    for src in config["sources"]:
        item = {"id": src["id"], "name_zh": src["name"], "publisher": src["name"], "tier": src["tier"],
                "language": src["language"], "allowed_hosts": list(src["allowed_hosts"]),
                "content_policy": src["content_policy"]}
        if src["type"] == "rss":
            item["discovery_mode"] = "rss"
            item["entry_urls"] = [src["feed_url"]]
        else:
            item["discovery_mode"] = "listing"
            item["entry_urls"] = list(src["entry_urls"])
        if src.get("link_include_pattern"):
            item["link_include_pattern"] = src["link_include_pattern"]
        sources.append(item)
    out["sources"] = sources
    return out
