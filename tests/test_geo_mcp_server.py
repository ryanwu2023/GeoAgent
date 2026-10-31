from __future__ import annotations

import hashlib
import json
import os
from backend.database import connect
import subprocess
import sys
import tempfile
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from backend.geo_mcp.auth import AuthConfig
from backend.geo_mcp.contracts import MCPError, Principal
from backend.geo_mcp.scope import authorize
from backend.geo_mcp.provenance import snapshot_provenance
from backend.geo_mcp.server import create_app, _workspace_counts, _analysis_evidence_boundary, _collector_projection, _redacted_record
from backend import store


TOKEN = "geoagent-test-token"


def test_counterevidence_boundary_does_not_turn_unassessed_into_absent():
    boundary = _analysis_evidence_boundary()
    assert boundary["verified_counterevidence_status"] == "not_evaluated"
    assert "不是系统已判断反证不存在" in boundary["assessment_scope"]
    assert boundary["verified_counterevidence_available"] == "unknown"
    assert "是否存在未知" in boundary["assessment_summary"]
    assert "不能据此断言整个快照" in boundary["retrieval_scope"]
    assert "沿用旧回执" in boundary["retrieval_scope"]
    assert "单方行动通报" in boundary["retrieval_scope"]


def test_collector_projection_distinguishes_ledger_from_heartbeat():
    result = _collector_projection({"run_id": "r", "collectors": []})
    assert result["live"] is True
    assert "不是进程/调度器心跳" in result["interpretation_basis"]
    assert "有限 events 不能证明此后无新事件" in result["interpretation_basis"]
    assert "不证明整个快照" in result["interpretation_basis"]
    assert result["activity_assessment"]["process_running"] == "unknown"
    assert result["activity_assessment"]["events_after_latest_visible"] == "unknown"
    assert result["activity_assessment"]["no_updates_since_last_event"] == "not_established"


def test_evidence_projection_keeps_panel_timestamps_without_raw_paths():
    item = _redacted_record({"id": "r", "published_at": "2026-10-09T11:20:20+08:00", "fetched_at": "2026-10-09T06:59:00Z", "first_seen": None, "event_at": None, "tier": "T2", "raw_refs": ["/private/archive.json"]})
    assert item["fetched_at"] == "2026-10-09T06:59:00Z"
    assert item["published_at"] == "2026-10-09T11:20:20+08:00"
    assert item["event_at"] is None
    assert item["tier"] == "T2"
    assert "raw_refs" not in item


def test_relative_week_uses_selected_snapshot_import_date():
    result = snapshot_provenance({"id": "historical", "created_at": "2026-10-09T07:30:40Z"}, working_snapshot_id="newer", latest_snapshot_id="newer")
    assert result["relative_time_basis"]["this_week"] == {"start": "2026-10-05", "end": "2026-10-09"}
    boundary = result["collection_snapshot_boundary"]
    assert "采集恢复不等于补齐旧快照" in boundary["collection_recovery"]
    assert "原始归档材料" in boundary["historical_material"]
    assert "展示投影更新不等于新增归档材料" in boundary["historical_material"]
    assert "未读取不能断言" in boundary["verification"]
    for status in ("success", "partial", "failed"):
        projected = _collector_projection({"run_id": "r", "run_status": status, "collectors": []})
        assert projected["collection_snapshot_boundary"] == boundary
    assert not result["is_latest_snapshot"]
    missing = snapshot_provenance({"id": "missing"}, working_snapshot_id=None, latest_snapshot_id=None)
    assert missing["relative_time_basis"]["this_week"] is None


def test_workspace_counts_match_panel_scope_and_latest_reviews(monkeypatch):
    context = connect()
    connection = context.__enter__()
    connection.execute("DROP TABLE reviews")
    connection.execute("CREATE TABLE reviews(snapshot_id TEXT, record_id TEXT, version INTEGER, signal TEXT, created_at TEXT)")
    connection.cursor().executemany("INSERT INTO reviews VALUES(%s,%s,%s,%s,%s)", [
        ("snap", "a", 2, "easing", "2026-10-10T12:00:00Z"), ("snap", "a", 1, "sustain", "2026-10-09T12:00:00Z"),
        ("other", "b", 1, "easing", "2026-10-11T12:00:00Z"),
        ("snap", "e", 3, "sustain", "2026-10-12T12:00:00Z"),
    ])
    @contextmanager
    def db():
        yield connection
    monkeypatch.setattr(store, "db", db)
    def record(rid, topic, signal, date, supplemental=False):
        return {"id": rid, "topics": [topic], "dimensions": {topic: "d"},
                "published_at": date, "situation_signal": signal, "signal_basis": "fixture",
                "title": "fixture", "text": "fixture", "points": [], "supplemental": supplemental}
    snapshot = {"id": "snap", "records": [
        record("a", "usiran", "escalation", "2026-10-09"),
        record("b", "usiran", "escalation", "2026-10-09"),
        record("c", "usiran", "unknown", "2026-10-09", True),
        record("d", "ukraine", "sustain", "2026-10-09"),
        record("e", "usiran", "sustain", "2026-10-08"),
    ]}
    try:
        result = _workspace_counts(snapshot, "usiran", "d", "2026-10-09", "2026-10-09")
        assert result["records_count"] == 3
        assert result["supplemental_records_count"] == 1
        assert result["signal_counts"] == {"escalation": 1, "sustain": 0, "easing": 1, "unknown": 1}
        assert sum(result["signal_counts"].values()) == result["records_count"]
        assert next(row for row in result["coverage"] if row["id"] == "usiran")["count"] == 2
        assert next(row for row in result["coverage"] if row["id"] == "ukraine")["count"] == 1
        assert result["framework_candidate_coverage"]["total"] == 13
        assert result["review_evidence"]["applied_records_count"] == 1
        assert result["review_evidence"]["latest_applied_review_at"] == "2026-10-10T12:00:00Z"
        assert result["change_attribution"]["status"] == "not_established"
        assert result["analysis_evidence_boundary"]["verified_counterevidence_status"] == "not_evaluated"
        assert "不是维度合计" in result["coverage_basis"]
        empty = _workspace_counts(snapshot, "usiran", "", "2026-10-10", "")
        assert empty["records_count"] == 0
        assert not any(empty["signal_counts"].values())
        assert empty["review_evidence"]["applied_records_count"] == 0
        assert empty["review_evidence"]["latest_applied_review_at"] is None
        # Historical snapshots may contain no precomputed signal at all. The
        # panel computes event_metadata on read; MCP must use that same path.
        historical = {"id": "historical", "records": [
            {**record("strike", "usiran", "unknown", "2026-10-09"), "title": "Iranian strike on tanker"},
            {**record("peace", "usiran", "unknown", "2026-10-09"), "title": "Peace talks resume"},
            {**record("supply", "usiran", "unknown", "2026-10-09"), "title": "Resupply continues"},
        ]}
        for item in historical["records"]:
            del item["situation_signal"]
            del item["signal_basis"]
        computed = _workspace_counts(historical, "usiran", "", "", "")
        assert computed["signal_counts"] == {"escalation": 1, "sustain": 1, "easing": 1, "unknown": 0}
        assert computed["review_evidence"]["applied_records_count"] == 0
        assert computed["change_attribution"]["status"] == "not_established"
    finally:
        context.__exit__(None, None, None)


def auth_config(*, tools: set[str] | None = None) -> AuthConfig:
    return AuthConfig(
        token_hash=hashlib.sha256(TOKEN.encode()).hexdigest(),
        principal=Principal(
            principal_id="test-principal",
            agent_id="test-agent",
            geo_workspace_id="test-workspace",
            allowed_snapshot_ids=frozenset({"*"}),
            allowed_topics=frozenset({"*"}),
            allowed_dimensions=frozenset({"*"}),
            allowed_tools=frozenset(tools or {"*"}),
        ),
    )


def test_health_is_public_and_mcp_requires_bearer() -> None:
    app = create_app(auth_config=auth_config())
    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["service"] == "geoagent-mcp"

        response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "GEO_AUTH_REQUIRED"


def test_scope_file_supports_multiple_principals_and_revocation(tmp_path, monkeypatch) -> None:
    first_token = "first-principal-token"
    second_token = "second-principal-token"
    expired_token = "expired-principal-token"
    scope_file = tmp_path / "scope.json"
    scope_file.write_text(
        json.dumps(
            {
                "credentials": [
                    {
                        "credential_id": "agent-one",
                        "token_hash": hashlib.sha256(first_token.encode()).hexdigest(),
                        "principal": {
                            "principal_id": "principal-one",
                            "agent_id": "agent-one",
                            "geo_workspace_id": "workspace-one",
                            "allowed_snapshot_ids": ["working"],
                            "allowed_topics": ["usiran"],
                            "allowed_dimensions": ["energy"],
                            "allowed_tools": ["geo_snapshot_list"],
                            "read_only": True,
                        },
                        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                    },
                    {
                        "credential_id": "agent-two-revoked",
                        "token_hash": hashlib.sha256(second_token.encode()).hexdigest(),
                        "principal": {
                            "principal_id": "principal-two",
                            "agent_id": "agent-two",
                            "geo_workspace_id": "workspace-two",
                            "allowed_snapshot_ids": ["*"],
                            "allowed_topics": ["*"],
                            "allowed_dimensions": ["*"],
                            "allowed_tools": ["*"],
                        },
                        "revoked": True,
                    },
                    {
                        "credential_id": "agent-three-expired",
                        "token_hash": hashlib.sha256(expired_token.encode()).hexdigest(),
                        "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
                        "principal": {
                            "principal_id": "principal-three",
                            "agent_id": "agent-three",
                            "geo_workspace_id": "workspace-three",
                            "allowed_snapshot_ids": ["working"],
                            "allowed_topics": ["*"],
                            "allowed_dimensions": ["*"],
                            "allowed_tools": ["*"],
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GEOAGENT_MCP_SCOPE_FILE", str(scope_file))
    config = AuthConfig.from_env()
    assert config.authenticate(first_token).principal_id == "principal-one"
    with pytest.raises(MCPError) as exc_info:
        config.authenticate(second_token)
    assert exc_info.value.code == "GEO_AUTH_REQUIRED"
    with pytest.raises(MCPError) as exc_info:
        config.authenticate(expired_token)
    assert exc_info.value.code == "GEO_AUTH_REQUIRED"


def test_missing_snapshot_mapping_has_distinct_scope_error() -> None:
    principal = Principal(
        principal_id="unmapped",
        agent_id="agent-unmapped",
        geo_workspace_id="workspace-unmapped",
        allowed_snapshot_ids=frozenset(),
        allowed_topics=frozenset({"*"}),
        allowed_dimensions=frozenset({"*"}),
        allowed_tools=frozenset({"geo_snapshot_get"}),
    )
    with pytest.raises(MCPError) as exc_info:
        authorize(principal, tool="geo_snapshot_get", snapshot_id="snapshot-1")
    assert exc_info.value.code == "GEO_SCOPE_MAPPING_REQUIRED"


def test_loopback_legacy_principal_defaults_to_local_open_scope(monkeypatch) -> None:
    monkeypatch.setenv("GEOAGENT_MCP_BEARER_TOKEN", "local-token")
    monkeypatch.setenv("GEOAGENT_MCP_HOST", "127.0.0.1")
    for name in (
        "GEOAGENT_MCP_SCOPE_FILE",
        "GEOAGENT_MCP_ALLOWED_SNAPSHOTS",
        "GEOAGENT_MCP_ALLOWED_TOPICS",
        "GEOAGENT_MCP_ALLOWED_DIMENSIONS",
    ):
        monkeypatch.delenv(name, raising=False)
    config = AuthConfig.from_env()
    principal = config.authenticate("local-token")
    assert principal.allowed_snapshot_ids == frozenset({"*"})
    assert principal.allowed_topics == frozenset({"*"})
    assert principal.allowed_dimensions == frozenset({"*"})


def test_non_loopback_host_requires_scope_file(monkeypatch) -> None:
    monkeypatch.setenv("GEOAGENT_MCP_BEARER_TOKEN", "dev-token")
    monkeypatch.setenv("GEOAGENT_MCP_HOST", "0.0.0.0")
    monkeypatch.delenv("GEOAGENT_MCP_SCOPE_FILE", raising=False)
    with pytest.raises(ValueError, match="非 loopback"):
        AuthConfig.from_env()


def test_scope_file_rejects_legacy_token_fallback(tmp_path, monkeypatch) -> None:
    scope_file = tmp_path / "scope.json"
    scope_file.write_text(
        json.dumps(
            {
                "credentials": [
                    {
                        "credential_id": "scoped-agent",
                        "token_hash": hashlib.sha256(b"scoped-token").hexdigest(),
                        "principal": {
                            "principal_id": "scoped-principal",
                            "agent_id": "scoped-agent",
                            "geo_workspace_id": "scoped-workspace",
                            "allowed_snapshot_ids": ["working"],
                            "allowed_topics": ["*"],
                            "allowed_dimensions": ["*"],
                            "allowed_tools": ["geo_snapshot_list"],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GEOAGENT_MCP_SCOPE_FILE", str(scope_file))
    monkeypatch.setenv("GEOAGENT_MCP_BEARER_TOKEN", "legacy-token")
    with pytest.raises(ValueError, match="legacy Bearer"):
        AuthConfig.from_env()


@asynccontextmanager
async def running_server(
    *,
    tools: str,
    snapshots: str = "*",
    topics: str = "*",
    dimensions: str = "*",
) -> str:
    port = 18401
    env = os.environ.copy()
    env.update(
        {
            "GEOAGENT_MCP_BEARER_TOKEN": TOKEN,
            "GEOAGENT_MCP_ALLOWED_SNAPSHOTS": snapshots,
            "GEOAGENT_MCP_ALLOWED_TOPICS": topics,
            "GEOAGENT_MCP_ALLOWED_DIMENSIONS": dimensions,
            "GEOAGENT_MCP_ALLOWED_TOOLS": tools,
        }
    )
    # A long MCP session can fill an undrained PIPE and block the server's
    # logging thread, appearing as a client ReadTimeout. Keep diagnostics in
    # a temporary file instead of leaving stdout/stderr pipes undrained.
    server_log = tempfile.TemporaryFile()
    process = subprocess.Popen(
        [sys.executable, "scripts/start_mcp.py", "--host", "127.0.0.1", "--port", str(port)],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                with urlopen(f"{url}/healthz", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            server_log.seek(0)
            raise RuntimeError(f"MCP server did not start: {server_log.read()!r}")
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        server_log.close()


@pytest.mark.anyio
async def test_official_mcp_client_can_initialize_list_and_call() -> None:
    from backend.adapters import load_bundle
    snapshot_id, _ = store.save_snapshot(load_bundle(store.ROOT / "examples/demo-data"))
    store.set_working_snapshot_id(snapshot_id)
    async with running_server(tools="*") as base_url:
        async with httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {TOKEN}"},
            # Full snapshot projections can exceed httpx's default 5s locally.
            # This bounds only the isolated contract test, not the live service.
            timeout=30,
        ) as http_client:
            async with streamable_http_client(f"{base_url}/mcp", http_client=http_client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    names = {tool.name for tool in tools.tools}
                    assert {'geo_business_catalog', 'geo_metric_changes', 'geo_source_health', 'geo_collector_runs', 'geo_brief_compare', 'geo_risk_map'} <= names
                    for tool in tools.tools:
                        assert tool.annotations.readOnlyHint is True
                        for parameter in tool.inputSchema.get('properties', {}).values():
                            assert parameter.get('description'), tool.name
                    assert {
                        "geo_snapshot_list",
                        "geo_snapshot_get",
                        "geo_workspace_summary",
                        "geo_metric_catalog",
                        "geo_metric_series",
                        "geo_evidence_search",
                        "geo_evidence_get",
                        "geo_analysis_view",
                        "geo_brief_list",
                        "geo_brief_get",
                        "geo_collector_status",
                    } <= names

                    result = await session.call_tool("geo_snapshot_list", {})
                    assert result.isError is False
                    payload = json.loads(result.content[0].text)
                    assert payload["ok"] is True
                    assert payload["tool"] == "geo_snapshot_list"
                    assert "snapshots" in payload["data"]
                    assert payload["provenance"]["rule_version"] == "v6.3"

                    snapshot_id = payload["provenance"]["working_snapshot_id"] or payload["data"]["snapshots"][0]["id"]
                    assert snapshot_id in {item["id"] for item in payload["data"]["snapshots"]}
                    for name, arguments in [
                        ('geo_business_catalog', {}),
                        ('geo_metric_changes', {'snapshot_id': snapshot_id, 'topic': 'usiran', 'limit': 2}),
                        ('geo_source_health', {'snapshot_id': snapshot_id, 'limit': 2}),
                        ('geo_risk_map', {'snapshot_id': snapshot_id, 'topic': 'usiran', 'limit': 2}),
                        ('geo_collector_runs', {'limit': 2}),
                    ]:
                        trial = json.loads((await session.call_tool(name, arguments)).content[0].text)
                        assert trial['ok'], (name, trial)
                        if name == 'geo_collector_runs':
                            assert '采集恢复不等于补齐旧快照' in trial['data']['collection_snapshot_boundary']['collection_recovery']
                        elif 'snapshot_id' in arguments:
                            assert '采集恢复不等于补齐旧快照' in trial['provenance']['collection_snapshot_boundary']['collection_recovery']
                    invalid_date = json.loads((await session.call_tool('geo_metric_changes', {'snapshot_id': snapshot_id, 'start': '2026-02-30'})).content[0].text)
                    assert invalid_date['ok'] is False
                    overview = await session.call_tool("geo_workspace_summary", {"snapshot_id": snapshot_id, "topic": "usiran", "limit": 1})
                    overview_payload = json.loads(overview.content[0].text)
                    assert overview_payload["ok"] is True
                    assert datetime.fromisoformat(overview_payload["read_at"]).tzinfo is not None
                    counts = overview_payload["data"]
                    assert sum(counts["signal_counts"].values()) == counts["records_count"]
                    assert counts["framework_candidate_coverage"]["total"] == 13
                    assert "supplemental" in counts["coverage_basis"]
                    assert len(counts["asset_data_gaps"]) == 5
                    assert all(item["missing_data"] for item in counts["asset_data_gaps"])
                    assert "有效零值不是缺失" in counts["metric_quality_basis"]
                    assert "不在目录不证明没有原始数据" in counts["metric_quality_basis"]
                    assert counts["query_scope"]["topic"] == "usiran"
                    assert counts["quality_warning_scope"]["topic"] == "usiran"
                    assert counts["quality_warning_scope"]["count"] == len(overview_payload["warnings"])
                    if len(payload["data"]["snapshots"]) > 1:
                        historical_id = payload["data"]["snapshots"][1]["id"]
                        historical_result = await session.call_tool(
                            "geo_snapshot_get",
                            {"snapshot_id": historical_id},
                        )
                        historical_payload = json.loads(historical_result.content[0].text)
                        assert historical_payload["ok"] is True
                        assert historical_payload["provenance"]["snapshot_id"] == historical_id

                    catalog_result = await session.call_tool(
                        "geo_metric_catalog",
                        {"snapshot_id": snapshot_id, "limit": 2},
                    )
                    assert catalog_result.isError is False
                    catalog_payload = json.loads(catalog_result.content[0].text)
                    assert catalog_payload["ok"] is True
                    assert len(catalog_payload["data"]["metrics"]) <= 2
                    assert catalog_payload["data"]["total"] >= len(catalog_payload["data"]["metrics"])

                    metric = catalog_payload["data"]["metrics"][0]
                    series_result = await session.call_tool(
                        "geo_metric_series",
                        {"snapshot_id": snapshot_id, "series": metric["series"], "max_points": 1},
                    )
                    assert series_result.isError is False
                    series_payload = json.loads(series_result.content[0].text)
                    assert series_payload["ok"] is True
                    returned_metric = series_payload["data"]["metric"]
                    assert returned_metric["series"] == metric["series"]
                    assert len(returned_metric["history"]) <= 1

                    evidence_result = await session.call_tool(
                        "geo_evidence_search",
                        {"snapshot_id": snapshot_id, "limit": 1},
                    )
                    evidence_payload = json.loads(evidence_result.content[0].text)
                    assert evidence_payload["ok"] is True
                    assert evidence_payload["data"]["limit"] == 1
                    assert "不能据此断言整个快照" in evidence_payload["data"]["analysis_evidence_boundary"]["retrieval_scope"]
                    if evidence_payload["data"]["records"]:
                        record_id = evidence_payload["data"]["records"][0]["id"]
                        detail_result = await session.call_tool(
                            "geo_evidence_get",
                            {"snapshot_id": snapshot_id, "record_id": record_id},
                        )
                        detail_payload = json.loads(detail_result.content[0].text)
                        assert detail_payload["ok"] is True
                        assert detail_payload["data"]["record"]["id"] == record_id
                        assert detail_payload["data"]["analysis_evidence_boundary"] == evidence_payload["data"]["analysis_evidence_boundary"]

                    invalid_result = await session.call_tool(
                        "geo_metric_series",
                        {"snapshot_id": snapshot_id, "series": "missing-series"},
                    )
                    invalid_payload = json.loads(invalid_result.content[0].text)
                    assert invalid_payload["ok"] is False
                    assert invalid_payload["error"]["code"] == "GEO_METRIC_NOT_FOUND"

                    invalid_dimension_result = await session.call_tool(
                        "geo_metric_catalog",
                        {"snapshot_id": snapshot_id, "dimension": "not-a-dimension"},
                    )
                    invalid_dimension_payload = json.loads(invalid_dimension_result.content[0].text)
                    assert invalid_dimension_payload["ok"] is False
                    assert invalid_dimension_payload["error"]["code"] == "GEO_INVALID_DIMENSION"

                    limit_result = await session.call_tool(
                        "geo_evidence_search",
                        {"snapshot_id": snapshot_id, "limit": 51},
                    )
                    limit_payload = json.loads(limit_result.content[0].text)
                    assert limit_payload["ok"] is False
                    assert limit_payload["error"]["code"] == "GEO_LIMIT_EXCEEDED"

                    analysis_result = await session.call_tool(
                        "geo_analysis_view",
                        {"snapshot_id": snapshot_id, "limit": 2},
                    )
                    analysis_payload = json.loads(analysis_result.content[0].text)
                    assert analysis_payload["ok"] is True
                    assert analysis_payload["provenance"]["snapshot_id"] == snapshot_id
                    assert len(analysis_payload["data"]["assets"]) <= 2
                    assert analysis_payload["data"]["query_scope"] == {"topic": "all", "dimension": "", "start": "", "end": ""}
                    assert len(analysis_payload["data"]["asset_overview"]) == len(analysis_payload["data"]["assets"])
                    assert all("topic_paths" in item for item in analysis_payload["data"]["asset_overview"])
                    assert "不参与 topic_paths 匹配计算" in analysis_payload["data"]["limit_basis"]
                    assert all('history' not in m and m['history_included'] is False
                               for m in analysis_payload['data']['important_metrics'])

                    brief_result = await session.call_tool("geo_brief_list", {"snapshot_id": snapshot_id, "limit": 2})
                    brief_payload = json.loads(brief_result.content[0].text)
                    assert brief_payload["ok"] is True
                    assert brief_payload["provenance"]["working_snapshot_id"]
                    if brief_payload["data"]["briefs"]:
                        brief_detail_result = await session.call_tool(
                            "geo_brief_get",
                            {"brief_id": brief_payload["data"]["briefs"][0]["id"], "max_bytes": 1000},
                        )
                        brief_detail_payload = json.loads(brief_detail_result.content[0].text)
                        assert brief_detail_payload["ok"] is True
                        assert len(brief_detail_payload["data"]["brief"]["markdown"].encode("utf-8")) <= 1000

                    collector_result = await session.call_tool("geo_collector_status", {"limit": 2})
                    collector_payload = json.loads(collector_result.content[0].text)
                    assert collector_payload["ok"] is True
                    assert collector_payload["provenance"]["kind"] == "live"
                    assert collector_payload["data"]["live"] is True
                    for event in collector_payload["data"]["events"]:
                        assert "payload" not in event
                        assert "DEMO_KEY" not in json.dumps(event, ensure_ascii=False)
                        assert "/Users/" not in json.dumps(event, ensure_ascii=False)


@pytest.mark.anyio
async def test_tool_allowlist_returns_structured_scope_error() -> None:
    async with running_server(tools="geo_snapshot_list") as base_url:
        async with httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as http_client:
            async with streamable_http_client(f"{base_url}/mcp", http_client=http_client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("geo_snapshot_get", {"snapshot_id": "not-allowed"})
                    assert result.isError is False
                    payload = json.loads(result.content[0].text)
                    assert payload["ok"] is False
                    assert payload["error"]["code"] == "GEO_TOOL_NOT_ALLOWED"


@pytest.mark.anyio
async def test_snapshot_and_dimension_scope_rejections_are_structured() -> None:
    summaries = store.snapshot_summaries()
    if len(summaries) < 2:
        pytest.skip("需要至少一个历史快照验证 snapshot scope")
    working_id = store.get_working_snapshot_id()
    historical_id = next(item["id"] for item in summaries if item["id"] != working_id)
    async with running_server(
        tools="geo_snapshot_get,geo_metric_catalog",
        snapshots="working",
        dimensions="us_readiness",
    ) as base_url:
        async with httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as http_client:
            async with streamable_http_client(f"{base_url}/mcp", http_client=http_client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    snapshot_result = await session.call_tool("geo_snapshot_get", {"snapshot_id": historical_id})
                    snapshot_payload = json.loads(snapshot_result.content[0].text)
                    assert snapshot_payload["ok"] is False
                    assert snapshot_payload["error"]["code"] == "GEO_SCOPE_DENIED"

                    dimension_result = await session.call_tool(
                        "geo_metric_catalog",
                        {"snapshot_id": working_id, "dimension": "aid"},
                    )
                    dimension_payload = json.loads(dimension_result.content[0].text)
                    assert dimension_payload["ok"] is False
                    assert dimension_payload["error"]["code"] == "GEO_SCOPE_DENIED"
