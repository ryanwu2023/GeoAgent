import asyncio
import json

import httpx

from backend.qa_harness import run_local_harness
from backend.qa_harness_store import HarnessStore


class FakeTools:
    def __init__(self):
        self.snapshot = {"id": "s1"}
        self.topic = "usiran"
        self.dimension = ""
        self.start = "2026-10-01"
        self.end = "2026-10-24"
        self.record = {"id": "r1", "title": "美伊公开报道", "source_name": "新华社", "published_at": "2026-10-24", "text": "公开报道正文"}
        self.record_by_id = {"r1": self.record}
        self.retrieved_ids = []
        self.full_text_ids = []
        self.internal_message_ids = []
        self.internal_full_text_ids = []
        self.calls = []
        self.validation_facts = []

    def call(self, name, arguments):
        self.calls.append({"tool": name, "arguments": arguments, "result_count": 1})
        if name == "geo_evidence_search":
            if "r1" not in self.retrieved_ids:
                self.retrieved_ids.append("r1")
            return {"records": [{"id": "r1", "title": self.record["title"]}], "returned": 1}
        if name == "geo_evidence_get":
            if "r1" not in self.full_text_ids:
                self.full_text_ids.append("r1")
            return {"record": self.record}
        if name == "geo_workspace_summary":
            return {"records_count": 1, "source_count": 1}
        return {"records": []}

    def citations(self):
        return [self.record] if "r1" in self.retrieved_ids else []

    def internal_sources(self):
        return []


def plan():
    return {
        "focuses": ["最新进展"], "search_queries": ["美伊 最新进展"],
        "needs_metrics": False, "needs_internal_messages": False,
        "request_scope": {"start": "", "end": ""},
        "budget": {"evidence": 24, "full_text": 8, "search_page": 8, "tool_calls": 4},
    }


def test_model_failure_returns_deterministic_non_empty_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    tools = FakeTools()

    async def complete(*args, **kwargs):
        raise TimeoutError("模型超时")

    progress = []
    outcome = asyncio.run(run_local_harness(
        question="最新美伊进展", plan=plan(), tools=tools, complete=complete,
        publish=lambda item: _capture(progress, item), run_id="qa-fallback", store=HarnessStore(),
    ))
    assert outcome["final"]["text"]
    assert outcome["status"] == "degraded"
    assert outcome["final"]["text"].startswith("## ")
    assert outcome["final"]["evidence_ids"] == ["r1"]


def test_cost_limit_still_returns_answer_without_calling_model(tmp_path, monkeypatch):
    from backend.qa_agent_settings import save_settings

    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    save_settings({
        "per_agent_timeout_seconds": 70,
        "total_timeout_seconds": 150,
        "max_parallel": 2,
        "input_price_per_million": 1000,
        "output_price_per_million": 1000,
        "max_cost_cny": 0.000001,
    })
    calls = 0

    async def complete(*args, **kwargs):
        nonlocal calls
        calls += 1
        return "不应调用", "test-model"

    outcome = asyncio.run(run_local_harness(
        question="最新美伊进展", plan=plan(), tools=FakeTools(), complete=complete,
        publish=lambda item: _capture([], item), run_id="qa-cost-limit", store=HarnessStore(),
    ))
    assert calls == 0
    assert outcome["final"]["text"]
    assert outcome["status"] == "degraded"
    assert outcome["cost_estimate"]["limit_enabled"] is True
    assert "美伊局势" in outcome["final"]["text"]


def test_system_prompt_requires_question_focused_conclusion():
    from backend.qa_harness import _system_prompt

    prompt = _system_prompt("usiran")
    assert "首句必须围绕用户原始问题" in prompt
    assert "不得用记录数" in prompt
    assert "只能放在口径与限制" in prompt


def test_non_retryable_model_http_error_falls_back_without_duplicate_call(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    tools = FakeTools()
    calls = 0

    async def complete(*args, **kwargs):
        nonlocal calls
        calls += 1
        request = httpx.Request("POST", "https://model.invalid/v1/chat/completions")
        response = httpx.Response(401, request=request)
        raise httpx.HTTPStatusError("unauthorized", request=request, response=response)

    store = HarnessStore()
    outcome = asyncio.run(run_local_harness(
        question="最新美伊进展", plan=plan(), tools=tools, complete=complete,
        publish=lambda item: _capture([], item), run_id="qa-auth-fallback", store=store,
    ))
    run = store.load_run("qa-auth-fallback")
    assert calls == 1
    assert outcome["status"] == "degraded"
    assert run is not None
    assert run.errors[-1] == {
        "kind": "model_http", "message": "模型服务返回 HTTP 401", "retryable": False,
    }


def test_httpx_timeout_is_classified_and_retried_once(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    tools = FakeTools()
    calls = 0

    async def complete(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out")

    store = HarnessStore()
    outcome = asyncio.run(run_local_harness(
        question="最新美伊进展", plan=plan(), tools=tools, complete=complete,
        publish=lambda item: _capture([], item), run_id="qa-httpx-timeout", store=store,
    ))
    run = store.load_run("qa-httpx-timeout")
    assert calls == 2
    assert outcome["status"] == "degraded"
    assert run is not None
    assert all(item["kind"] == "model_timeout" for item in run.errors)


def test_completed_run_is_reused_without_repeating_model_or_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    store = HarnessStore()
    tools = FakeTools()
    model_calls = 0

    async def complete(*args, **kwargs):
        nonlocal model_calls
        model_calls += 1
        return json.dumps({
            "action": "final", "answer_markdown": "## 结论\n\n当前公开资料显示局势仍需观察〔S1〕。",
            "citation_map": [{"anchor": "S1", "evidence_ids": ["r1"]}], "gaps": [],
        }, ensure_ascii=False), "test-model"

    first = asyncio.run(run_local_harness(
        question="最新美伊进展", plan=plan(), tools=tools, complete=complete,
        publish=lambda item: _capture([], item), run_id="qa-resume", store=store,
    ))
    first_tool_calls = len(tools.calls)
    second_tools = FakeTools()
    second = asyncio.run(run_local_harness(
        question="最新美伊进展", plan=plan(), tools=second_tools, complete=complete,
        publish=lambda item: _capture([], item), run_id="qa-resume", store=store,
    ))
    assert first["final"]["text"] == second["final"]["text"]
    assert model_calls == 1
    assert first_tool_calls > 0
    assert second_tools.calls == []


def test_completed_resume_rehydrates_public_citations_from_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    store = HarnessStore()
    first_tools = FakeTools()

    async def complete(*args, **kwargs):
        return json.dumps({
            "action": "final", "answer_markdown": "## 结论\n\n公开资料显示局势仍需观察〔S1〕。",
            "citation_map": [{"anchor": "S1", "evidence_ids": ["r1"]}], "gaps": [],
        }, ensure_ascii=False), "test-model"

    first = asyncio.run(run_local_harness(
        question="最新美伊进展", plan=plan(), tools=first_tools, complete=complete,
        publish=lambda item: _capture([], item), run_id="qa-public-rehydrate", store=store,
    ))
    assert first["citations"]

    resumed_tools = FakeTools()
    resumed_tools.record_by_id = {}
    second = asyncio.run(run_local_harness(
        question="最新美伊进展", plan=plan(), tools=resumed_tools, complete=complete,
        publish=lambda item: _capture([], item), run_id="qa-public-rehydrate", store=store,
    ))
    assert second["citations"] == first["citations"]
    assert resumed_tools.calls == []


def test_plain_chinese_markdown_is_accepted_as_final_model_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    tools = FakeTools()

    async def complete(*args, **kwargs):
        return (
            "## 结论摘要\n\n当前公开资料显示，美伊局势仍存在外交接触与升级风险并行的特征。"
            "现阶段不能仅凭报道数量判断冲突概率，需要继续核对官方声明、实际行动和市场指标。"
            "\n\n## 后续观察\n\n关注谈判条件、军事部署与能源运输是否出现可验证变化。",
            "markdown-model",
        )

    outcome = asyncio.run(run_local_harness(
        question="最新美伊进展", plan=plan(), tools=tools, complete=complete,
        publish=lambda item: _capture([], item), run_id="qa-markdown", store=HarnessStore(),
    ))
    assert outcome["status"] == "completed"
    assert "结论摘要" in outcome["final"]["text"]
    # Plain Markdown without claim-level anchors remains readable, but must
    # not silently bind every sentence to the first retrieved record.
    assert outcome["final"]["evidence_ids"] == []
    assert outcome["final"]["citation_map"] == []
    assert any("逐主张引用映射" in gap for gap in outcome["final"]["gaps"])


def test_progress_events_only_include_evidence_from_that_tool_result(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    tools = FakeTools()

    async def complete(*args, **kwargs):
        return json.dumps({
            "action": "final", "answer_markdown": "## 结论\n\n当前公开资料显示局势仍需观察。",
            "citation_map": [], "gaps": [],
        }, ensure_ascii=False), "test-model"

    progress = []
    asyncio.run(run_local_harness(
        question="最新美伊进展", plan=plan(), tools=tools, complete=complete,
        publish=lambda item: _capture(progress, item), run_id="qa-progress", store=HarnessStore(),
    ))
    tool_events = [
        item for item in progress
        if item.get("kind") == "tool" and item.get("status") in {"completed", "degraded", "failed"}
    ]
    workspace = next(item for item in tool_events if item.get("tool") == "geo_workspace_summary")
    search = next(item for item in tool_events if item.get("tool") == "geo_evidence_search")
    assert workspace["evidence_ids"] == []
    assert search["evidence_ids"] == ["r1"]


def test_tool_progress_has_running_and_terminal_trace_events(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    tools = FakeTools()

    async def complete(*args, **kwargs):
        return json.dumps({
            "action": "final", "answer_markdown": "## 结论\n\n当前公开资料显示局势仍需观察。",
            "citation_map": [], "gaps": [],
        }, ensure_ascii=False), "test-model"

    progress = []
    asyncio.run(run_local_harness(
        question="最新美伊进展", plan=plan(), tools=tools, complete=complete,
        publish=lambda item: _capture(progress, item), run_id="qa-trace-v1", store=HarnessStore(),
    ))
    workspace = [item for item in progress if item.get("call_id") == "bootstrap-workspace"]
    assert [item["status"] for item in workspace] == ["running", "completed"]
    assert all(item["event_version"] == "tool-trace/v1" for item in workspace)
    assert all(item["run_id"] == "qa-trace-v1" for item in workspace)
    assert workspace[0]["started_at"]
    assert workspace[0]["arguments_summary"] == {}
    assert workspace[1]["finished_at"]
    assert workspace[1]["artifact_id"].startswith("qa-art-")
    assert "已读取范围摘要" in workspace[1]["result_summary"]
    assert workspace[1]["result_count"] == 1


def test_budget_exhausted_search_has_honest_trace_summary():
    from backend.qa_harness import _tool_result_summary

    result = {"records": [], "returned": 0, "budget_exhausted": True, "remaining_evidence_budget": 0}
    assert "预算已用尽" in _tool_result_summary("geo_evidence_search", result, 0)
    assert "未命中" not in _tool_result_summary("geo_evidence_search", result, 0)


def test_generic_conflict_word_is_not_misclassified_as_counterevidence():
    from backend.qa_harness import _facet_from_text

    assert _facet_from_text("美伊冲突最新进展与地区动态") == "event"
    assert _facet_from_text("官方否认打击计划已经获批") == "counterevidence"


def test_tool_attempt_exhaustion_still_runs_no_tool_final_synthesis(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    tools = FakeTools()
    exhausted_plan = plan()
    exhausted_plan["budget"] = {
        **exhausted_plan["budget"],
        "total_tool_attempts": 1,
        "tool_calls": 2,
    }
    model_calls = 0

    async def complete(*args, **kwargs):
        nonlocal model_calls
        model_calls += 1
        return json.dumps({
            "action": "final",
            "answer_markdown": "## 结论摘要\n\n工具预算耗尽后，仍使用已取得材料完成总结。",
            "citation_map": [],
            "gaps": ["部分检索未完成"],
        }, ensure_ascii=False), "test-model"

    outcome = asyncio.run(run_local_harness(
        question="最新美伊进展", plan=exhausted_plan, tools=tools, complete=complete,
        publish=lambda item: _capture([], item), run_id="qa-budget-final", store=HarnessStore(),
    ))
    assert model_calls == 1
    assert outcome["status"] == "completed"
    assert "仍使用已取得材料完成总结" in outcome["final"]["text"]


def test_metric_table_is_rendered_from_program_values_without_model_unit_mutation():
    from backend.qa_harness import _replace_model_metric_table

    draft = (
        "## 一、结论摘要\n\n局势仍需观察。\n\n"
        "## 三、量化指标与资产传导\n\n"
        "| 指标 | 最新值 |\n|---|---|\n| 欧盟拨款 | 29 十亿欧元 |\n\n"
        "传导解释由模型保留。\n\n## 四、反证与不确定性\n\n仍有缺口。"
    )
    metrics = [{
        "series": "eu_ukraine_facility_disbursement", "name": "欧盟Ukraine Facility单笔实际拨款",
        "unit": "十亿欧元", "scope": "俄乌冲突资产传导",
        "current": {"date": "2026-10-02", "value": 2.9},
        "previous": {"date": "2026-06-08", "value": 2.8},
        "delta": 0.1, "pct": 3.57,
    }]
    rendered = _replace_model_metric_table(draft, metrics)
    assert "| 欧盟Ukraine Facility单笔实际拨款 | 2.9 十亿欧元 | 2.8 十亿欧元 | 0.1 十亿欧元；3.57% | 2026-10-02 |" in rendered
    assert "29 十亿欧元" not in rendered
    assert "传导解释由模型保留" in rendered

    bare_heading = draft.replace("## 三、量化指标与资产传导", "三、量化指标与资产传导")
    bare_rendered = _replace_model_metric_table(bare_heading, metrics)
    assert "2.9 十亿欧元" in bare_rendered
    assert "29 十亿欧元" not in bare_rendered


def test_metric_table_accepts_program_computed_metric_block_rows():
    from backend.qa_harness import _replace_model_metric_table

    draft = "## 三、量化指标与资产传导\n\n模型暂未列出表格。\n\n## 四、反证与不确定性"
    metrics = [{
        "series": "finance:brent", "name": "布伦特原油", "unit": "美元/桶",
        "first": {"date": "2026-10-21", "value": 110.0},
        "latest": {"date": "2026-10-24", "value": 116.0},
        "change": 6.0, "change_pct": 5.4545, "observations": 4,
        "lag_days": 2, "scope": "共享市场背景",
    }]
    rendered = _replace_model_metric_table(draft, metrics)
    assert "| 布伦特原油 | 116 美元/桶 | 110 美元/桶 | 6 美元/桶；5.4545% | 2026-10-24 |" in rendered
    assert "共享市场背景；4期；滞后2天" in rendered


class FakeInternalTools(FakeTools):
    def __init__(self):
        super().__init__()
        self.internal_record = {"id": "hm1", "title": "内部观察", "content_abstract": "内部正文"}

    def call(self, name, arguments):
        if name == "geo_internal_message_stats":
            self.calls.append({"tool": name, "arguments": arguments, "result_count": 1})
            return {"records": 1}
        if name == "geo_internal_message_search":
            self.calls.append({"tool": name, "arguments": arguments, "result_count": 1})
            self.internal_message_ids = ["hm1"]
            return {"messages": [self.internal_record], "returned": 1}
        if name == "geo_internal_message_get":
            self.calls.append({"tool": name, "arguments": arguments, "result_count": 1})
            self.internal_full_text_ids = ["hm1"]
            return {"message": self.internal_record}
        return super().call(name, arguments)

    def internal_sources(self):
        return [self.internal_record] if "hm1" in self.internal_message_ids else []


def test_completed_resume_restores_cached_internal_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    store = HarnessStore()
    first_tools = FakeInternalTools()
    internal_plan = plan()
    internal_plan["needs_internal_messages"] = True
    internal_plan["budget"].update({"internal_messages": 3, "internal_full_text": 2})

    async def complete(*args, **kwargs):
        return json.dumps({
            "action": "final", "answer_markdown": "## 结论\n\n当前公开资料与内部线索均需继续核验。",
            "citation_map": [], "gaps": [],
        }, ensure_ascii=False), "test-model"

    first = asyncio.run(run_local_harness(
        question="最新美伊进展", plan=internal_plan, tools=first_tools, complete=complete,
        publish=lambda item: _capture([], item), run_id="qa-internal-resume", store=store,
    ))
    assert first["internal_sources"]
    second_tools = FakeInternalTools()
    second = asyncio.run(run_local_harness(
        question="最新美伊进展", plan=internal_plan, tools=second_tools, complete=complete,
        publish=lambda item: _capture([], item), run_id="qa-internal-resume", store=store,
    ))
    assert second["internal_sources"] == first["internal_sources"]
    assert second_tools.calls == []


def test_stream_failure_checkpoints_completed_batch_before_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    store = HarnessStore()
    first_tools = FakeTools()

    async def complete(*args, **kwargs):
        return json.dumps({
            "action": "final", "answer_markdown": "## 结论\n\n当前公开资料显示局势仍需观察。",
            "citation_map": [], "gaps": [],
        }, ensure_ascii=False), "test-model"

    async def broken_publish(item):
        if item.get("kind") == "tool":
            raise RuntimeError("stream disconnected")

    try:
        asyncio.run(run_local_harness(
            question="最新美伊进展", plan=plan(), tools=first_tools, complete=complete,
            publish=broken_publish, run_id="qa-stream-resume", store=store,
        ))
    except RuntimeError as exc:
        assert "disconnected" in str(exc)
    else:
        raise AssertionError("expected progress stream failure")

    checkpoint = store.load_run("qa-stream-resume")
    assert checkpoint is not None
    assert checkpoint.tool_calls["bootstrap-workspace"].status == "completed"
    assert checkpoint.tool_calls["bootstrap-search-1"].status == "completed"

    resumed_tools = FakeTools()
    outcome = asyncio.run(run_local_harness(
        question="最新美伊进展", plan=plan(), tools=resumed_tools, complete=complete,
        publish=lambda item: _capture([], item), run_id="qa-stream-resume", store=store,
    ))
    assert outcome["final"]["text"]
    assert all(item["tool"] not in {"geo_workspace_summary", "geo_evidence_search"} for item in resumed_tools.calls)


def test_model_failure_fallback_keeps_metric_catalog_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    tools = FakeTools()
    original_call = tools.call

    def call(name, arguments):
        if name == "geo_metric_catalog":
            tools.calls.append({"tool": name, "arguments": arguments, "result_count": 1})
            return {"metrics": [{"series": "brent", "name": "布伦特原油", "current": 110.2}], "total": 1}
        return original_call(name, arguments)

    tools.call = call
    metric_plan = {**plan(), "needs_metrics": True}

    async def complete(*args, **kwargs):
        raise TimeoutError("模型超时")

    outcome = asyncio.run(run_local_harness(
        question="原油影响", plan=metric_plan, tools=tools, complete=complete,
        publish=lambda item: _capture([], item), run_id="qa-metric-fallback", store=HarnessStore(),
    ))
    assert "布伦特原油：110.2" in outcome["final"]["text"]


def test_complex_run_persists_parallel_specialist_memos_and_agent_trace(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    tools = FakeTools()
    research_plan = plan()
    research_plan["multi_agent"] = {
        "enabled": True,
        "agents": ["event", "market", "counterevidence"],
        "budget": {
            "total_tool_attempts": 24,
            "specialist_tool_calls": 9,
            "per_task_tool_calls": 3,
            "wall_seconds": 5,
            "max_parallel": 3,
        },
    }
    research_plan["focuses"] = ["事件主线", "市场与资产", "反证与不确定性"]

    async def complete(messages, model_role="reasoning"):
        try:
            payload = json.loads(messages[-1]["content"])
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if payload.get("role") in {"event", "market", "counterevidence"}:
            role = payload["role"]
            return json.dumps({
                "summary": f"{role} 子任务结论",
                "findings": [f"{role} 发现"],
                "claims": [], "counter_evidence": [], "gaps": [],
                "watchpoints": [], "evidence_ids": [],
                "internal_message_ids": [], "follow_up_queries": [], "metric_series": [],
            }, ensure_ascii=False), "test-specialist"
        return json.dumps({
            "action": "final", "answer_markdown": "## 结论\n\n多 Agent 已分别核对事件、市场与反证，当前结论仍需持续验证。",
            "citation_map": [], "gaps": [],
        }, ensure_ascii=False), "test-coordinator"

    progress = []
    store = HarnessStore()
    outcome = asyncio.run(run_local_harness(
        question="总结局势、资产影响和反证", plan=research_plan, tools=tools, complete=complete,
        publish=lambda item: _capture(progress, item), run_id="qa-multi", store=store,
    ))
    assert outcome["status"] == "completed"
    assert list(outcome["agent_memos"]) == ["event", "market", "counterevidence"]
    agent_events = [item for item in progress if item.get("kind") == "agent"]
    assert {item["agent_role"] for item in agent_events} == {"event", "market", "counterevidence"}
    assert all(item["task_id"] and item["agent_id"] and item["parent_agent_id"] for item in agent_events)
    persisted = store.load_run("qa-multi")
    assert persisted is not None
    assert all(task.memo and task.status == "completed" for task in persisted.agent_tasks.values())


def test_runtime_attempt_budget_covers_bootstrap_reads_and_specialists(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    tools = FakeTools()
    research_plan = plan()
    research_plan["multi_agent"] = {
        "enabled": True,
        "agents": ["event", "market", "counterevidence"],
        "budget": {
            "total_tool_attempts": 4,
            "specialist_tool_calls": 9,
            "per_task_tool_calls": 3,
            "wall_seconds": 5,
            "max_parallel": 3,
        },
    }

    async def complete(messages, model_role="reasoning"):
        try:
            payload = json.loads(messages[-1]["content"])
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if payload.get("role"):
            return json.dumps({"summary": "预算内备忘录", "findings": [], "claims": [], "counter_evidence": [], "gaps": [], "watchpoints": [], "evidence_ids": [], "internal_message_ids": [], "follow_up_queries": [], "metric_series": []}, ensure_ascii=False), "test"
        return json.dumps({"action": "final", "answer_markdown": "## 结论\n\n预算耗尽后仍使用已有证据形成非空回答。", "citation_map": [], "gaps": []}, ensure_ascii=False), "test"

    store = HarnessStore()
    outcome = asyncio.run(run_local_harness(
        question="综合分析局势与风险", plan=research_plan, tools=tools, complete=complete,
        publish=lambda item: _capture([], item), run_id="qa-budget", store=store,
    ))
    persisted = store.load_run("qa-budget")
    assert outcome["final"]["text"]
    assert persisted is not None
    assert persisted.tool_attempts_used == 4
    assert len(tools.calls) <= 4
    assert any(
        state.error and state.error.get("kind") == "budget_exhausted"
        for state in persisted.tool_calls.values()
    )


async def _capture(target, item):
    target.append(item)
