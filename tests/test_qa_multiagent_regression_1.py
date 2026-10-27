import asyncio
import json

from backend.qa_multiagent import SpecialistBudget, _parse_action, run_specialists


def test_specialist_action_normalizes_flash_tool_aliases_and_drops_unknown_fields():
    action = _parse_action(json.dumps({
        "action": "tool",
        "tool": "geo_evidence_search",
        "arguments": {
            "query": "伊朗 美国 最新进展",
            "limit": 99,
            "time_window_hours": 168,
            "source_tiers": ["t1", "T2", "unknown"],
        },
    }, ensure_ascii=False))

    assert action.arguments == {
        "q": "伊朗 美国 最新进展",
        "limit": 20,
        "source_tiers": ["T1", "T2"],
    }


def test_specialist_action_accepts_double_encoded_action_and_string_memo():
    payload = json.dumps({
        "action": "memo",
        "memo": json.dumps({
            "summary": "已完成复核",
            "findings": ["公开材料仍需核验"],
            "evidence_ids": [],
        }, ensure_ascii=False),
    }, ensure_ascii=False)
    action = _parse_action(json.dumps(payload, ensure_ascii=False))

    assert action.action == "memo"
    assert action.memo["summary"] == "已完成复核"


def test_specialist_keeps_a_final_synthesis_turn_after_using_tool_budget():
    plan = {
        "topic": "usiran",
        "multi_agent": {"enabled": True, "agents": ["event"]},
        "search_queries": ["美伊 最新"],
        "focuses": ["事件主线"],
    }
    executed = []

    async def execute_tools(role, requests):
        results = []
        for request in requests:
            executed.append((request.tool, dict(request.arguments)))
            preview = {"records": [{"id": "r1", "title": "最新事件"}]}
            if request.tool == "geo_evidence_get":
                preview = {"record": {"id": "r1", "title": "最新事件", "text": "正文"}}
            results.append({
                "call_id": request.call_id,
                "tool": request.tool,
                "ok": True,
                "preview": preview,
            })
        return results

    replies = iter([
        {
            "action": "tool",
            "tool": "geo_evidence_get",
            "arguments": {"evidence_id": "r1"},
            "purpose": "读取全文",
        },
        {
            "action": "memo",
            "memo": {
                "summary": "已读取正文并完成核验",
                "findings": ["存在一条待核验线索"],
                "claims": [{"text": "存在行动线索", "evidence_ids": ["r1"]}],
                "counter_evidence": [],
                "gaps": [],
                "watchpoints": [],
                "evidence_ids": ["r1"],
                "metric_series": [],
                "follow_up_queries": [],
            },
        },
    ])

    async def complete(messages, model_role="reasoning"):
        return json.dumps(next(replies), ensure_ascii=False), "test-model"

    result = asyncio.run(run_specialists(
        question="最新美伊局势",
        plan=plan,
        scope={"snapshot_id": "s1", "topic": "usiran", "start": "", "end": ""},
        execute_tools=execute_tools,
        complete=complete,
        budget=SpecialistBudget(
            per_agent_tool_calls=2,
            total_tool_calls=2,
            per_agent_seconds=2,
        ),
    ))

    assert executed == [
        ("geo_evidence_search", {"q": "美伊 最新", "limit": 8}),
        ("geo_evidence_get", {"record_id": "r1"}),
    ]
    assert result.memos["event"].status == "completed"
    assert result.memos["event"].summary == "已读取正文并完成核验"


def test_specialist_fills_missing_evidence_id_only_from_own_search_receipt():
    plan = {
        "topic": "usiran",
        "multi_agent": {"enabled": True, "agents": ["counterevidence"]},
        "search_queries": ["美伊 反证"],
        "focuses": ["反证"],
    }
    executed = []

    async def execute_tools(role, requests):
        results = []
        for request in requests:
            executed.append((request.tool, dict(request.arguments)))
            preview = {
                "records": [],
                "top_existing_ids": ["already-known-1"],
                "returned_new": 0,
            }
            if request.tool == "geo_evidence_get":
                preview = {"record": {"id": request.arguments["record_id"], "text": "完整正文"}}
            results.append({
                "call_id": request.call_id,
                "tool": request.tool,
                "ok": True,
                "preview": preview,
            })
        return results

    replies = iter([
        {"action": "tool", "tool": "geo_evidence_get", "arguments": {}, "purpose": "读取已有候选"},
        {"action": "memo", "memo": {
            "summary": "已核对既有候选正文",
            "findings": [], "claims": [], "counter_evidence": [],
            "gaps": [], "watchpoints": [],
            "evidence_ids": ["already-known-1"], "metric_series": [],
            "follow_up_queries": [],
        }},
    ])

    async def complete(messages, model_role="reasoning"):
        return json.dumps(next(replies), ensure_ascii=False), "test-model"

    result = asyncio.run(run_specialists(
        question="最新美伊局势有哪些反证",
        plan=plan,
        scope={"snapshot_id": "s1", "topic": "usiran", "start": "", "end": ""},
        execute_tools=execute_tools,
        complete=complete,
        budget=SpecialistBudget(
            per_agent_tool_calls=2,
            total_tool_calls=2,
            per_agent_seconds=2,
        ),
    ))

    assert executed[1] == ("geo_evidence_get", {"record_id": "already-known-1"})
    assert result.memos["counterevidence"].status == "completed"
