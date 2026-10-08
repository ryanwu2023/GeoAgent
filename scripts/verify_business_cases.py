"""Validate the menu-level GeoAgent business cases against the live MCP.

This is the deterministic data-side half of the acceptance.  PiChat Agent
semantic routing is recorded separately from the embedded live runs; this
script proves that every proposed answer has a usable GeoAgent data path.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from verify_mcp_gate import _assert_ok


ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests" / "fixtures" / "geoagent_business_cases.json"
sys.path.insert(0, str(ROOT))


def _has_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_has_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_has_key(item, key) for item in value)
    return False


async def verify(base_url: str, token: str) -> dict[str, Any]:
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(base_url=base_url, timeout=30, headers={"Authorization": f"Bearer {token}"}) as client:
        async with streamable_http_client(f"{base_url.rstrip('/')}/mcp", http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = {tool.name for tool in (await session.list_tools()).tools}
                snapshot_payload = _assert_ok(await session.call_tool("geo_snapshot_list", {}), "geo_snapshot_list")
                snapshots = snapshot_payload["data"]["snapshots"]
                snapshot_id = next(item["id"] for item in snapshots if item.get("is_working_snapshot"))

                async def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
                    return _assert_ok(await session.call_tool(name, args), name)

                catalog = await call("geo_metric_catalog", {"snapshot_id": snapshot_id, "limit": 50})
                metric_items = catalog["data"].get("metrics", [])
                series = next((item.get("series") for item in metric_items if item.get("series")), None)
                evidence = await call("geo_evidence_search", {"snapshot_id": snapshot_id, "limit": 5})
                record_id = (evidence["data"].get("records") or [{}])[0].get("id")

                shared: dict[str, dict[str, Any]] = {
                    "snapshot_list": snapshot_payload,
                    "snapshot_get": await call("geo_snapshot_get", {"snapshot_id": snapshot_id}),
                    "workspace_coverage": await call("geo_workspace_summary", {"snapshot_id": snapshot_id, "limit": 5}),
                    "metric_catalog": catalog,
                    "metric_series": await call("geo_metric_series", {"snapshot_id": snapshot_id, "series": series, "max_points": 50}) if series else {},
                    "evidence_search": evidence,
                    "evidence_get": await call("geo_evidence_get", {"snapshot_id": snapshot_id, "record_id": record_id}) if record_id else {},
                    "analysis": await call("geo_analysis_view", {"snapshot_id": snapshot_id, "limit": 20}),
                    "collector_live": await call("geo_collector_status", {"limit": 20}),
                    "brief_list": await call("geo_brief_list", {"snapshot_id": snapshot_id, "limit": 20}),
                }
                for case in cases:
                    missing_tools = [tool for tool in case["required_tools"] if tool not in listed]
                    payloads = [shared.get(check, {}) for check in case["direct_checks"]]
                    missing_fields = [field for field in case["required_fields"] if not any(_has_key(payload, field) for payload in payloads)]
                    if case["id"] == "pipeline-live" and not shared["collector_live"]["provenance"].get("kind") == "live":
                        missing_fields.append("provenance.kind=live")
                    results.append({
                        "id": case["id"],
                        "menu": case["menu"],
                        "status": "passed" if not missing_tools and not missing_fields else "failed",
                        "missing_tools": missing_tools,
                        "missing_fields": missing_fields,
                        "snapshot_id": snapshot_id,
                    })
    return {"status": "passed" if all(item["status"] == "passed" for item in results) else "failed", "cases": results}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.getenv("GEOAGENT_MCP_VERIFY_URL", "http://127.0.0.1:8401"))
    parser.add_argument("--token", default=os.getenv("GEOAGENT_MCP_VERIFY_TOKEN", ""))
    args = parser.parse_args()
    if not args.token:
        print(json.dumps({"status": "blocked", "error": "missing --token or GEOAGENT_MCP_VERIFY_TOKEN"}, ensure_ascii=False))
        return 2
    try:
        result = asyncio.run(verify(args.url, args.token))
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
