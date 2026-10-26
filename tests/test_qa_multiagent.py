import asyncio
import json

from backend.qa_multiagent import (
    SpecialistBudget,
    build_specialist_specs,
    run_specialists,
)
from backend.qa_planner import deterministic_plan


def test_complex_research_question_dispatches_three_specialists():
    plan = deterministic_plan(
        "总结最新美伊局势、资产影响、反证和后续风险",
        topic="usiran",
        dimension="",
        start="",
        end="",
        snapshot_as_of="2026-10-24T00:00:00Z",
    )
    specs = build_specialist_specs("总结最新美伊局势、资产影响、反证和后续风险", plan)
    assert [item.role for item in specs] == ["event", "market", "counterevidence"]
    assert set(specs[0].allowed_tools) <= {
        "geo_workspace_summary", "geo_evidence_search", "geo_evidence_get",
        "geo_internal_message_search", "geo_internal_message_get", "geo_internal_message_stats",
    }
    assert "geo_metric_series" in specs[1].allowed_tools
    assert "geo_analysis_view" in specs[2].allowed_tools
    assert plan["multi_agent"]["enabled"] is True


def test_narrow_lookup_keeps_single_agent_path():
    plan = deterministic_plan(
        "布伦特原油最新值是多少",
        topic="usiran",
        dimension="",
        start="",
        end="",
        snapshot_as_of="2026-10-24T00:00:00Z",
    )
    assert plan["multi_agent"]["enabled"] is False
    assert build_specialist_specs("布伦特原油最新值是多少", plan) == []

    plan_with_polite_analysis_word = deterministic_plan(
        "分析一下布伦特原油最新值是多少",
        topic="usiran", dimension="", start="", end="",
        snapshot_as_of="2026-10-24T00:00:00Z",
    )
    assert plan_with_polite_analysis_word["multi_agent"]["enabled"] is False


def test_specialists_run_concurrently_and_keep_partial_failure():
    plan = {
        "multi_agent": {
            "enabled": True,
            "agents": ["event", "market", "counterevidence"],
        },
        "search_queries": ["美伊 最新", "美伊 原油", "美伊 缓和 反证"],
        "focuses": ["事件主线", "市场与资产", "反证与不确定性"],
    }
    seen = []

    async def execute_tools(role, requests):
        seen.append((role, tuple(request.tool for request in requests)))
        await asyncio.sleep(0.03)
        if role == "counterevidence":
            raise TimeoutError("counter agent timed out")
        return [
            {
                "call_id": request.call_id,
                "tool": request.tool,
                "ok": True,
                "preview": {"records": [{"id": f"{role}-1", "title": f"{role} evidence"}]},
                "record_count": 1,
            }
            for request in requests
        ]

    async def complete(messages, model_role="reasoning"):
        payload = json.loads(messages[-1]["content"])
        role = payload["role"]
        return json.dumps({
            "summary": f"{role} 摘要",
            "findings": [f"{role} 发现"],
            "counterevidence": [],
            "gaps": [],
            "watchpoints": [],
            "evidence_ids": [f"{role}-1"],
            "metric_series": [],
        }, ensure_ascii=False), "test-model"

    result = asyncio.run(run_specialists(
        question="总结最新美伊局势、资产影响、反证和后续风险",
        plan=plan,
        scope={"snapshot_id": "s1", "topic": "usiran", "start": "", "end": ""},
        execute_tools=execute_tools,
        complete=complete,
        budget=SpecialistBudget(max_parallel=3, per_agent_tool_calls=3, total_tool_calls=9, per_agent_seconds=2),
    ))
    assert len(seen) == 3
    assert result.status == "degraded"
    assert result.memos["event"].status == "completed"
    assert result.memos["market"].status == "completed"
    assert result.memos["counterevidence"].status == "degraded"
    assert "timed out" not in result.memos["counterevidence"].summary
    assert result.total_tool_calls <= 9


def test_specialist_rejects_unregistered_tool_request():
    plan = {
        "multi_agent": {"enabled": True, "agents": ["market"]},
        "search_queries": ["原油"],
        "focuses": ["市场与资产"],
    }
    called = []

    async def execute_tools(role, requests):
        called.extend(requests)
        assert all(request.tool in build_specialist_specs("资产影响分析", plan)[0].allowed_tools for request in requests)
        return []

    async def complete(messages, model_role="reasoning"):
        return json.dumps({"summary": "证据不足", "findings": [], "counterevidence": [], "gaps": ["缺少指标"], "watchpoints": [], "evidence_ids": [], "metric_series": []}, ensure_ascii=False), "test-model"

    result = asyncio.run(run_specialists(
        question="资产影响分析", plan=plan,
        scope={"snapshot_id": "s1", "topic": "usiran", "start": "", "end": ""},
        execute_tools=execute_tools, complete=complete,
    ))
    assert result.memos["market"].status == "completed"
    assert called


def test_specialist_accepts_json_wrapped_in_think_and_explanatory_text():
    plan = {
        "multi_agent": {"enabled": True, "agents": ["market"]},
        "search_queries": ["美伊 市场 资产"], "focuses": ["市场与资产"],
    }

    async def execute_tools(role, requests):
        return [{"call_id": item.call_id, "tool": item.tool, "ok": True, "preview": {}} for item in requests]

    async def complete(messages, model_role="reasoning"):
        payload = {"summary": "市场指标已核对", "findings": [], "counterevidence": [], "gaps": [], "watchpoints": [], "evidence_ids": [], "metric_series": ["brent"]}
        return "<think>internal</think>以下为结果：\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```", "test-model"

    result = asyncio.run(run_specialists(
        question="分析最新美伊局势的资产影响", plan=plan,
        scope={"snapshot_id": "s1", "topic": "usiran", "start": "", "end": ""},
        execute_tools=execute_tools, complete=complete,
    ))
    assert result.status == "completed"
    assert result.memos["market"].summary == "市场指标已核对"


def test_event_specialist_adaptively_reads_selected_full_text_before_memo():
    plan = {
        "topic": "usiran",
        "multi_agent": {"enabled": True, "agents": ["event"]},
        "search_queries": ["美伊 最新军事外交"],
        "focuses": ["事件主线", "军事"],
    }
    executed = []

    async def execute_tools(role, requests):
        executed.extend((role, request.tool, request.arguments) for request in requests)
        results = []
        for request in requests:
            preview = (
                {"records": [{"id": "r-new", "title": "最新行动线索"}]}
                if request.tool == "geo_evidence_search"
                else {"record": {"id": request.arguments["record_id"], "title": "最新行动线索", "text": "完整正文"}}
            )
            results.append({"call_id": request.call_id, "tool": request.tool, "ok": True, "preview": preview})
        return results

    replies = iter([
        {"action": "tool", "tool": "geo_evidence_get", "arguments": {"record_id": "r-new"}, "purpose": "读取关键正文"},
        {"action": "memo", "memo": {
            "summary": "已读取最新行动全文并保留核验边界",
            "findings": ["发现一条待核验行动线索"],
            "claims": [{"text": "存在行动线索", "evidence_ids": ["r-new"], "confidence": "limited"}],
            "counter_evidence": [], "gaps": [], "watchpoints": [],
            "evidence_ids": ["r-new"], "metric_series": [], "follow_up_queries": [],
        }},
    ])

    async def complete(messages, model_role="reasoning"):
        return json.dumps(next(replies), ensure_ascii=False), "test-model"

    result = asyncio.run(run_specialists(
        question="最新美伊局势", plan=plan,
        scope={"snapshot_id": "s1", "topic": "usiran", "start": "", "end": ""},
        execute_tools=execute_tools, complete=complete,
        budget=SpecialistBudget(per_agent_tool_calls=3, total_tool_calls=3, per_agent_seconds=2),
    ))
    assert [item[1] for item in executed] == ["geo_evidence_search", "geo_evidence_get"]
    assert result.memos["event"].status == "completed"
    assert result.memos["event"].evidence_ids == ["r-new"]


def test_specialist_executes_follow_up_query_before_accepting_memo():
    plan = {
        "topic": "usiran",
        "multi_agent": {"enabled": True, "agents": ["counterevidence"]},
        "search_queries": ["美伊 缓和 反证"], "focuses": ["反证与不确定性"],
    }
    queries = []

    async def execute_tools(role, requests):
        results = []
        for request in requests:
            if request.tool == "geo_evidence_search":
                queries.append(request.arguments["q"])
            results.append({
                "call_id": request.call_id, "tool": request.tool, "ok": True,
                "preview": {"records": [{"id": f"r{len(queries)}", "title": "证据"}]},
            })
        return results

    replies = iter([
        {"action": "memo", "memo": {
            "summary": "仍需补查", "findings": [], "claims": [], "counter_evidence": [],
            "gaps": [], "watchpoints": [], "evidence_ids": [], "metric_series": [],
            "follow_up_queries": ["官方 否认 打击计划"],
        }},
        {"action": "memo", "memo": {
            "summary": "已完成反证补查", "findings": ["找到否认线索"], "claims": [],
            "counter_evidence": [{"text": "官方尚未确认", "evidence_ids": ["r2"]}],
            "gaps": [], "watchpoints": [], "evidence_ids": ["r2"], "metric_series": [],
            "follow_up_queries": [],
        }},
    ])

    async def complete(messages, model_role="reasoning"):
        return json.dumps(next(replies), ensure_ascii=False), "test-model"

    result = asyncio.run(run_specialists(
        question="核对美伊升级判断的反证", plan=plan,
        scope={"snapshot_id": "s1", "topic": "usiran", "start": "", "end": ""},
        execute_tools=execute_tools, complete=complete,
        budget=SpecialistBudget(per_agent_tool_calls=3, total_tool_calls=3, per_agent_seconds=2),
    ))
    assert queries == ["美伊 缓和 反证", "官方 否认 打击计划"]
    assert result.memos["counterevidence"].summary == "已完成反证补查"


def test_specialist_rejects_model_requested_tool_outside_role_allowlist():
    plan = {
        "topic": "usiran",
        "multi_agent": {"enabled": True, "agents": ["event"]},
        "search_queries": ["美伊 最新"], "focuses": ["事件主线"],
    }
    tools_seen = []

    async def execute_tools(role, requests):
        tools_seen.extend(request.tool for request in requests)
        return [{"call_id": request.call_id, "tool": request.tool, "ok": True, "preview": {}} for request in requests]

    replies = iter([
        {"action": "tool", "tool": "geo_metric_series", "arguments": {"series": "brent"}},
        {"action": "memo", "memo": {
            "summary": "未执行越权工具", "findings": [], "claims": [], "counter_evidence": [],
            "gaps": ["缺少可用事件证据"], "watchpoints": [], "evidence_ids": [],
            "metric_series": [], "follow_up_queries": [],
        }},
    ])

    async def complete(messages, model_role="reasoning"):
        return json.dumps(next(replies), ensure_ascii=False), "test-model"

    result = asyncio.run(run_specialists(
        question="最新美伊局势", plan=plan,
        scope={"snapshot_id": "s1", "topic": "usiran", "start": "", "end": ""},
        execute_tools=execute_tools, complete=complete,
        budget=SpecialistBudget(per_agent_tool_calls=3, total_tool_calls=3, per_agent_seconds=2),
    ))
    assert tools_seen == ["geo_evidence_search"]
    assert result.memos["event"].status == "completed"


def test_metric_series_identity_falls_back_to_tool_preview_for_legacy_envelope():
    from backend.qa_multiagent import _series_from_call

    assert _series_from_call({
        "tool": "geo_metric_series",
        "preview": {"metric": {"series": "finance:brent"}},
    }) == "finance:brent"
    assert _series_from_call({
        "tool": "geo_metric_series",
        "arguments": {"series": "gold"},
        "preview": {"metric": {"series": "finance:brent"}},
    }) == "gold"


def test_specialist_accepts_useful_markdown_as_partial_memo():
    plan = {
        "topic": "usiran",
        "multi_agent": {"enabled": True, "agents": ["event"]},
        "search_queries": ["美伊 最新"], "focuses": ["事件主线"],
    }

    async def execute_tools(role, requests):
        return [{
            "call_id": request.call_id, "tool": request.tool, "ok": True,
            "preview": {"records": [{"id": "r1", "title": "最新事件"}]},
        } for request in requests]

    async def complete(messages, model_role="reasoning"):
        return "## 子任务摘要\n\n当前只有媒体转述，仍需核对官方声明与行动。", "test-model"

    result = asyncio.run(run_specialists(
        question="最新美伊局势", plan=plan,
        scope={"snapshot_id": "s1", "topic": "usiran", "start": "", "end": ""},
        execute_tools=execute_tools, complete=complete,
        budget=SpecialistBudget(per_agent_tool_calls=2, total_tool_calls=2, per_agent_seconds=2),
    ))
    memo = result.memos["event"]
    assert memo.status == "completed"
    assert "当前只有媒体转述" in memo.summary
    assert any("自由文本备忘录" in item for item in memo.gaps)
