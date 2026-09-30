"""Local MCP configuration and evidence-safe news ingestion."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .paths import collector_data_root
from .store import data_dir

PROTOCOL_VERSION = "2025-03-26"
DEFAULT_CONFIG = {
    "name": "haifutong",
    "url": "",
    "authorization": "",
    "verify_tls": True,
}


def _config_path() -> Path:
    return data_dir() / "mcp-config.json"


def load_config() -> dict:
    path = _config_path()
    if not path.is_file():
        return dict(DEFAULT_CONFIG)
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return dict(DEFAULT_CONFIG)
    return {**DEFAULT_CONFIG, **{k: saved.get(k, v) for k, v in DEFAULT_CONFIG.items()}}


def public_config(config: dict | None = None) -> dict:
    config = config or load_config()
    return {
        "name": config.get("name", ""),
        "url": config.get("url", ""),
        "configured": bool(config.get("url") and config.get("authorization")),
        "verify_tls": bool(config.get("verify_tls", True)),
    }


def save_config(name: str, url: str, authorization: str, verify_tls: bool) -> dict:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("MCP 地址必须是有效的 HTTP 或 HTTPS URL")
    previous = load_config()
    secret = authorization.strip() or previous.get("authorization", "")
    if not secret:
        raise ValueError("请填写 Authorization")
    config = {
        "name": (name.strip() or "mcp-news")[:80],
        "url": url.strip(),
        "authorization": secret,
        "verify_tls": bool(verify_tls),
    }
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return public_config(config)


def _json_response(response: httpx.Response) -> dict:
    response.raise_for_status()
    if len(response.content) > 20 * 1024 * 1024:
        raise ValueError("MCP 返回内容超过 20MB 限制")
    if not response.content:
        return {}
    if "text/event-stream" in response.headers.get("content-type", ""):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise ValueError("MCP 流式响应中没有数据")
    return response.json()


class McpClient:
    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        if not public_config(self.config)["configured"]:
            raise ValueError("MCP 尚未配置")
        self.session_id = ""
        self._request_id = 0
        self.initialized = False

    def _headers(self) -> dict[str, str]:
        result = {
            "Authorization": self.config["authorization"],
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.session_id:
            result["Mcp-Session-Id"] = self.session_id
        return result

    async def _post(self, payload: dict) -> dict:
        async with httpx.AsyncClient(
            verify=bool(self.config.get("verify_tls", True)), timeout=60, follow_redirects=False
        ) as client:
            response = await client.post(self.config["url"], headers=self._headers(), json=payload)
        self.session_id = response.headers.get("mcp-session-id", self.session_id)
        body = _json_response(response)
        if body.get("error"):
            error = body["error"]
            raise ValueError(str(error.get("message") or error)[:500])
        return body.get("result", {})

    async def connect(self) -> dict:
        self._request_id += 1
        result = await self._post(
            {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "GeoAgent", "version": "0.7.0"},
                },
            }
        )
        await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.initialized = True
        return result

    async def list_tools(self) -> list[dict]:
        if not self.initialized:
            await self.connect()
        self._request_id += 1
        result = await self._post(
            {"jsonrpc": "2.0", "id": self._request_id, "method": "tools/list", "params": {}}
        )
        return result.get("tools", [])

    async def call(self, name: str, arguments: dict) -> dict:
        if not self.initialized:
            await self.connect()
        self._request_id += 1
        result = await self._post(
            {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        if result.get("isError"):
            raise ValueError("MCP 工具返回错误")
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        for item in result.get("content", []):
            if item.get("type") == "text":
                try:
                    return json.loads(item.get("text", ""))
                except ValueError:
                    continue
        raise ValueError("MCP 工具没有返回可解析的结构化内容")


def _safe_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")[:100] or "record"


def _tier(hit: dict) -> str:
    return {
        "official": "T1",
        "international_org": "T2",
        "think_tank": "T2",
        "market_research": "T2",
        "mainstream_news": "T3",
    }.get(hit.get("source_type"), "未分级")


def store_news(config: dict, query: str, topic: str, dimension: str, envelope: dict) -> tuple[Path, int]:
    if not envelope.get("ok"):
        error = envelope.get("error") or {}
        raise ValueError(str(error.get("message") or error or "MCP 查询失败")[:500])
    hits = envelope.get("data", {}).get("hits", [])
    if not isinstance(hits, list):
        raise ValueError("MCP 新闻结果缺少 hits 列表")
    output = collector_data_root() / ("mcp-" + _safe_id(config["name"])) / "output"
    output.mkdir(parents=True, exist_ok=True)
    target = output / "geo-v6.json"
    existing = {}
    if target.is_file():
        try:
            previous = json.loads(target.read_text(encoding="utf-8"))
            existing = {item["id"]: item for item in previous.get("records", []) if item.get("id")}
        except (OSError, ValueError, TypeError):
            existing = {}
    fetched_at = datetime.now(timezone.utc).isoformat()
    for hit in hits:
        url = str(hit.get("url") or "")
        document_id = str(hit.get("document_id") or hit.get("dedupe_key") or url)
        if not hit.get("title") or not url.startswith(("http://", "https://")):
            continue
        rid = "mcp-" + _safe_id(document_id)
        existing[rid] = {
            "id": rid,
            "title": str(hit["title"]),
            "summary": str(hit.get("excerpt") or ""),
            "link": url,
            "published": hit.get("published_at"),
            "fetched_at": fetched_at,
            "source_id": "mcp:" + str(hit.get("source_name") or config["name"]),
            "source_name": str(hit.get("source_name") or config["name"]),
            "publisher": str(hit.get("source_name") or ""),
            "tier": _tier(hit),
            "topics": [topic],
            "dimensions": {topic: dimension},
            "nature": "MCP 新闻检索",
            "verification": "MCP 导入，待核验",
            "mcp_query": query,
            "mcp_document_id": hit.get("document_id"),
            "mcp_source_type": hit.get("source_type"),
            "mcp_score": hit.get("score"),
        }
    payload = {
        "schema_version": "geo.v6/1",
        "name": config["name"] + " MCP 新闻",
        "as_of": fetched_at,
        "records": sorted(existing.values(), key=lambda item: (item.get("published") or "", item["id"])),
        "observations": [],
    }
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    raw = output / "raw"
    raw.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    (raw / f"{stamp}.json").write_text(
        json.dumps({"query": query, "topic": topic, "response": envelope}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target, len(hits)

