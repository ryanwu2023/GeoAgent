"""Run the repeatable local/live Gate checks for the standalone GeoAgent MCP.

The script intentionally uses the official MCP client instead of probing the
HTTP route with hand-written JSON-RPC.  It is a local A3 evidence generator;
production credential, TLS, and network controls still require deployment
evidence and are not inferred from this check.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


EXPECTED_TOOLS = {
    "geo_snapshot_list",
    "geo_snapshot_get",
    "geo_workspace_summary",
    "geo_evidence_search",
    "geo_evidence_get",
    "geo_metric_catalog",
    "geo_metric_series",
    "geo_analysis_view",
    "geo_brief_list",
    "geo_brief_get",
    "geo_collector_status",
}


def _payload(result: Any) -> dict[str, Any]:
    """Decode the JSON envelope returned by a FastMCP tool."""

    if not result.content:
        raise RuntimeError("MCP tool returned no content")
    text = getattr(result.content[0], "text", None)
    if not isinstance(text, str):
        raise RuntimeError("MCP tool returned a non-text content block")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise RuntimeError("MCP tool returned a non-object envelope")
    return payload


def _assert_ok(result: Any, tool: str) -> dict[str, Any]:
    payload = _payload(result)
    if result.isError:
        raise RuntimeError(f"{tool} MCP result isError=true: {payload}")
    if payload.get("ok") is not True:
        raise RuntimeError(f"{tool} returned an error envelope: {payload.get('error')}")
    return payload


def _assert_error(result: Any, tool: str, code: str) -> dict[str, Any]:
    payload = _payload(result)
    if result.isError:
        raise RuntimeError(f"{tool} transport isError=true; expected structured {code}: {payload}")
    if payload.get("ok") is not False or payload.get("error", {}).get("code") != code:
        raise RuntimeError(f"{tool} expected {code}, got {payload}")
    return payload


async def verify(base_url: str, token: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    async with httpx.AsyncClient(base_url=base_url, timeout=10) as http_client:
        health = await http_client.get("/healthz")
        health.raise_for_status()
        health_payload = health.json()
        if health_payload.get("service") != "geoagent-mcp":
            raise RuntimeError(f"unexpected health payload: {health_payload}")
        checks.append({"name": "healthz", "status": "passed"})

        unauthorized = await http_client.post(
            "/mcp",
            headers={"Authorization": "Bearer invalid-gate-token"},
            json={"jsonrpc": "2.0", "id": "gate-auth", "method": "initialize", "params": {}},
        )
        if unauthorized.status_code != 401 or unauthorized.json().get("error", {}).get("code") != "GEO_AUTH_REQUIRED":
            raise RuntimeError(f"invalid credential was not rejected as expected: {unauthorized.status_code} {unauthorized.text}")
        checks.append({"name": "unauthorized_bearer", "status": "passed"})

        headers = {"Authorization": f"Bearer {token}"}
        http_client.headers.update(headers)
        async with streamable_http_client(
            f"{base_url.rstrip('/')}/mcp",
            http_client=http_client,
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                actual_tools = {tool.name for tool in tools.tools}
                if actual_tools != EXPECTED_TOOLS:
                    raise RuntimeError(
                        f"tool contract mismatch; expected={sorted(EXPECTED_TOOLS)} actual={sorted(actual_tools)}"
                    )
                checks.append({"name": "initialize_and_tools", "status": "passed", "tool_count": len(actual_tools)})

                snapshot_payload = _assert_ok(await session.call_tool("geo_snapshot_list", {}), "geo_snapshot_list")
                snapshots = snapshot_payload["data"]["snapshots"]
                if not snapshots:
                    raise RuntimeError("geo_snapshot_list returned no visible snapshots")
                snapshot_id = next(
                    (item["id"] for item in snapshots if item.get("is_working_snapshot")),
                    snapshots[0]["id"],
                )
                checks.append(
                    {
                        "name": "snapshot_list",
                        "status": "passed",
                        "visible_snapshot_count": len(snapshots),
                        "working_snapshot_id": snapshot_id,
                    }
                )

                snapshot = _assert_ok(
                    await session.call_tool("geo_snapshot_get", {"snapshot_id": snapshot_id}),
                    "geo_snapshot_get",
                )
                if snapshot["provenance"].get("snapshot_id") != snapshot_id:
                    raise RuntimeError("geo_snapshot_get provenance does not match requested snapshot")
                checks.append({"name": "snapshot_get", "status": "passed"})

                summary = _assert_ok(
                    await session.call_tool("geo_workspace_summary", {"snapshot_id": snapshot_id, "limit": 5}),
                    "geo_workspace_summary",
                )
                if summary["provenance"].get("snapshot_id") != snapshot_id:
                    raise RuntimeError("geo_workspace_summary provenance does not match requested snapshot")
                checks.append({"name": "workspace_summary", "status": "passed"})

                catalog = _assert_ok(
                    await session.call_tool("geo_metric_catalog", {"snapshot_id": snapshot_id, "limit": 5}),
                    "geo_metric_catalog",
                )
                metrics = catalog["data"].get("metrics", [])
                if metrics:
                    series = metrics[0].get("series") or metrics[0].get("id")
                    metric = _assert_ok(
                        await session.call_tool(
                            "geo_metric_series",
                            {"snapshot_id": snapshot_id, "series": series, "max_points": 5},
                        ),
                        "geo_metric_series",
                    )
                    if metric["data"]["metric"].get("series") != series:
                        raise RuntimeError("geo_metric_series returned the wrong series")
                checks.append({"name": "metrics", "status": "passed", "metric_count": len(metrics)})

                evidence = _assert_ok(
                    await session.call_tool("geo_evidence_search", {"snapshot_id": snapshot_id, "limit": 5}),
                    "geo_evidence_search",
                )
                records = evidence["data"].get("records", [])
                if records:
                    detail = _assert_ok(
                        await session.call_tool(
                            "geo_evidence_get",
                            {"snapshot_id": snapshot_id, "record_id": records[0]["id"]},
                        ),
                        "geo_evidence_get",
                    )
                    if "record" not in detail["data"]:
                        raise RuntimeError("geo_evidence_get did not return a record")
                checks.append({"name": "evidence", "status": "passed", "record_count": len(records)})

                analysis = _assert_ok(
                    await session.call_tool("geo_analysis_view", {"snapshot_id": snapshot_id, "limit": 5}),
                    "geo_analysis_view",
                )
                if analysis["provenance"].get("snapshot_id") != snapshot_id:
                    raise RuntimeError("geo_analysis_view provenance does not match requested snapshot")
                checks.append({"name": "analysis", "status": "passed"})

                briefs = _assert_ok(
                    await session.call_tool("geo_brief_list", {"snapshot_id": snapshot_id, "limit": 5}),
                    "geo_brief_list",
                )
                brief_items = briefs["data"].get("briefs", [])
                if brief_items:
                    brief = _assert_ok(
                        await session.call_tool(
                            "geo_brief_get",
                            {"brief_id": brief_items[0]["id"], "max_bytes": 2000},
                        ),
                        "geo_brief_get",
                    )
                    if "brief" not in brief["data"]:
                        raise RuntimeError("geo_brief_get did not return a brief")
                checks.append({"name": "briefs", "status": "passed", "brief_count": len(brief_items)})

                collector = _assert_ok(
                    await session.call_tool("geo_collector_status", {"limit": 10}),
                    "geo_collector_status",
                )
                if collector["provenance"].get("kind") != "live" or collector["data"].get("live") is not True:
                    raise RuntimeError("geo_collector_status is missing live provenance")
                serialized_collector = json.dumps(collector, ensure_ascii=False)
                for forbidden in ("DEMO_KEY", "/Users/", "payload"):
                    if forbidden in serialized_collector:
                        raise RuntimeError(f"collector response leaked forbidden value: {forbidden}")
                checks.append({"name": "collector_live", "status": "passed"})

                _assert_error(
                    await session.call_tool(
                        "geo_metric_catalog",
                        {"snapshot_id": snapshot_id, "dimension": "__gate_invalid_dimension__"},
                    ),
                    "geo_metric_catalog.invalid_dimension",
                    "GEO_INVALID_DIMENSION",
                )
                _assert_error(
                    await session.call_tool(
                        "geo_evidence_search",
                        {"snapshot_id": snapshot_id, "limit": 51},
                    ),
                    "geo_evidence_search.limit",
                    "GEO_LIMIT_EXCEEDED",
                )
                checks.append({"name": "structured_errors", "status": "passed"})

    return {"status": "passed", "base_url": base_url, "checks": checks}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("GEOAGENT_MCP_VERIFY_URL", "http://127.0.0.1:8401"))
    parser.add_argument(
        "--token",
        default=os.getenv("GEOAGENT_MCP_VERIFY_TOKEN", ""),
        help="Bearer token; prefer GEOAGENT_MCP_VERIFY_TOKEN so it is not recorded in shell history",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.token:
        print(json.dumps({"status": "failed", "error": "missing --token or GEOAGENT_MCP_VERIFY_TOKEN"}, ensure_ascii=False))
        return 2
    try:
        result = asyncio.run(verify(args.url, args.token))
    except Exception as exc:  # pragma: no cover - exercised by live gate failures
        result = {"status": "failed", "base_url": args.url, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
