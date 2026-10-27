"""Question planning for the bounded local GeoAgent tool loop."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import re
from typing import Any, Awaitable, Callable

from .qa_retrieval import evidence_budget
from .qa_query import FacetQuery, freshness_windows


def _date(value: str) -> str:
    return value if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or "") else ""


def _facet_for_query(value: str) -> str:
    if re.search(r"原油|黄金|美债|汇率|市场|资产|oil|gold|market", value, re.I):
        return "market"
    if re.search(r"反证|缓和|执行|条件|否认", value):
        return "counterevidence"
    if re.search(r"外交|谈判|会谈|调解|停火|协议|talk|ceasefire|negotiat", value, re.I):
        return "diplomacy"
    if re.search(r"军事|打击|战线|袭击|导弹|无人机|防空|strike|frontline|drone|missile", value, re.I):
        return "military"
    return "event"


def _query_specs(topic: str, queries: list[str], start: str, end: str) -> list[dict[str, Any]]:
    return [
        FacetQuery.for_topic(
            topic, value, facet=_facet_for_query(value), start=start, end=end,
            expected_role="counterevidence" if _facet_for_query(value) == "counterevidence" else "support",
        ).to_dict()
        for value in queries
    ]


def deterministic_plan(
    question: str,
    *,
    topic: str,
    dimension: str,
    start: str,
    end: str,
    snapshot_as_of: str,
) -> dict[str, Any]:
    inferred_start, inferred_end = _date(start), _date(end)
    try:
        anchor = datetime.fromisoformat(snapshot_as_of.replace("Z", "+00:00")).date()
    except ValueError:
        anchor = datetime.utcnow().date()
    if not inferred_start and not inferred_end:
        match = re.search(r"(?:最近|近)\s*(\d{1,3}|[一二两三四五六七八九十])\s*(天|周|个月|月)", question)
        if match:
            raw_amount = match.group(1)
            chinese_numbers = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
            amount = max(1, min(365, int(raw_amount) if raw_amount.isdigit() else chinese_numbers[raw_amount]))
            days = amount if match.group(2) == "天" else amount * 7 if match.group(2) == "周" else amount * 30
            inferred_start, inferred_end = (anchor - timedelta(days=days)).isoformat(), anchor.isoformat()
        elif "本周" in question:
            inferred_start, inferred_end = (anchor - timedelta(days=anchor.weekday())).isoformat(), anchor.isoformat()
        elif re.search(r"最新|今天|今日", question):
            # “最新”问题先看最近一周，避免两周前的高相关报道挤掉
            # 当天新增。更早背景仍可由后续补查按需读取。
            inferred_start, inferred_end = (anchor - timedelta(days=6)).isoformat(), anchor.isoformat()
        elif re.search(r"近期|最近|当前|目前|现在|进展", question):
            inferred_start, inferred_end = (anchor - timedelta(days=14)).isoformat(), anchor.isoformat()

    current_situation_request = bool(re.search(r"(?:最新|近期|当前|目前|现在).{0,8}(?:情况|动态|局势|进展)", question))
    complex_question = current_situation_request or any(
        term in question for term in ("分析", "研判", "总结", "简报", "局势", "进展", "影响", "风险", "展望")
    )
    focuses: list[str] = []
    mapping = {
        "事件主线": ("进展", "局势", "动态", "最新", "时间线"),
        "外交": ("外交", "谈判", "停火", "斡旋", "协议"),
        "军事": ("军事", "打击", "部署", "战线", "袭击", "导弹", "无人机"),
        "市场与资产": ("市场", "资产", "原油", "油价", "黄金", "美债", "汇率", "股指", "信用"),
        "反证与不确定性": ("反证", "不确定", "核验", "真假", "风险"),
        "后续观察": ("未来", "后续", "展望", "走势", "取决于"),
    }
    for label, terms in mapping.items():
        if any(term in question for term in terms):
            focuses.append(label)
    explicitly_requested_focus_count = len(focuses)
    if not focuses:
        focuses = ["事件主线", "反证与不确定性"]
    if complex_question:
        for label in ("事件主线", "外交", "军事", "市场与资产", "反证与不确定性", "后续观察"):
            if label not in focuses:
                focuses.append(label)

    budget = evidence_budget(question, topic=topic)
    # Multi-agent research is reserved for questions that require synthesis
    # across several research facets. Narrow lookups stay on the faster single
    # agent path. The parent coordinator remains the only answer writer.
    complexity_terms = (
        "分析", "研判", "总结", "简报", "局势", "进展", "影响", "风险", "展望",
        "原因", "传导", "反证", "不确定", "未来", "后续",
    )
    complexity_score = sum(1 for term in complexity_terms if term in question)
    narrow_lookup = bool(
        re.search(r"(?:是多少|最新值|当前值|具体数值|哪一条|哪个来源|原文)", question)
        and not re.search(r"(?:影响|传导|反证|风险|展望|走势|原因|总结|局势)", question)
    )
    multi_agent_enabled = bool(
        not narrow_lookup
        and complex_question
        and (complexity_score >= 2 or explicitly_requested_focus_count >= 2 or current_situation_request)
    )
    specialist_roles: list[str] = []
    if multi_agent_enabled:
        if any(label in focuses for label in ("事件主线", "外交", "军事")):
            specialist_roles.append("event")
        if "市场与资产" in focuses or any(term in question for term in ("资产", "市场", "油价", "原油", "黄金", "美债", "汇率")):
            specialist_roles.append("market")
        if any(label in focuses for label in ("反证与不确定性", "后续观察")):
            specialist_roles.append("counterevidence")
    task_definitions = {
        "event": {
            "task_id": "event-1", "agent_id": "event-agent", "agent_role": "event",
            "objective": "梳理事件主线、外交与军事变化并区分信息性质。",
            "allowed_tools": ["geo_workspace_summary", "geo_evidence_search", "geo_evidence_get", "geo_internal_message_search", "geo_internal_message_get", "geo_internal_message_stats"],
        },
        "market": {
            "task_id": "market-1", "agent_id": "market-agent", "agent_role": "market",
            "objective": "核对量化指标、资产表现和条件传导。",
            "allowed_tools": ["geo_workspace_summary", "geo_evidence_search", "geo_evidence_get", "geo_metric_catalog", "geo_metric_series", "geo_analysis_view", "geo_internal_message_search", "geo_internal_message_get", "geo_internal_message_stats"],
        },
        "counterevidence": {
            "task_id": "counterevidence-1", "agent_id": "counterevidence-agent", "agent_role": "counterevidence",
            "objective": "寻找相反证据、来源冲突、执行缺口和判断失效条件。",
            "allowed_tools": ["geo_workspace_summary", "geo_evidence_search", "geo_evidence_get", "geo_analysis_view", "geo_internal_message_search", "geo_internal_message_get", "geo_internal_message_stats"],
        },
    }
    topic_anchor = {
        "usiran": "美伊 伊朗 美国",
        "ukraine": "俄乌 俄罗斯 乌克兰",
        "all": "美伊 俄乌",
    }.get(topic, question[:60])
    # Keep the user's concrete entities in the first query, but remove request
    # boilerplate that weakens lexical retrieval.  Facet queries then ensure a
    # broad question covers diplomacy, military action, markets and counterevidence.
    core_query = re.sub(r"(?:请|帮我|给我|一下|情况|进行|综合|总结|分析|研判)", " ", question)
    core_query = re.sub(r"\s+", " ", core_query).strip()
    search_queries = [f"{topic_anchor} {core_query}".strip()]
    # Topic-specific tracks make a broad “latest situation” request cover the
    # material event paths instead of spending every slot on near-duplicate
    # generic searches.
    if topic == "usiran" and complex_question:
        search_queries.extend([
            # Keep English entity/action terms alongside Chinese wording. A
            # newly collected headline can reach search before translation,
            # so Chinese-only expansion otherwise misses the freshest item.
            "美伊 Iran Trump attack strike deal Sunday 军事 打击 撤离 安全警告",
            "美伊 Iran US talks deal negotiations Qatar Oman Pakistan 外交 谈判",
            "胡塞 Houthi 沙特 Saudi 霍尔木兹 Hormuz Bab el-Mandeb attack oil",
        ])
    elif topic == "ukraine" and complex_question:
        search_queries.extend([
            "俄乌 Ukraine Russia latest ceasefire humanitarian corridor peace talks negotiations 外交 停火 会谈",
            "俄乌 Ukraine Russia latest frontline drone missile air defense Donetsk Kherson Sumy Belgorod 战线 无人机 导弹 防空",
            "乌克兰 Ukraine aid Patriot Starlink intelligence weapons delivery EU US Russia sanctions refinery budget ruble 援助 制裁",
        ])
    if "外交" in focuses:
        search_queries.append(f"{topic_anchor} 外交 谈判 调解 停火 协议")
    if "军事" in focuses:
        search_queries.append(f"{topic_anchor} 军事 行动 部署 打击 战线")
    if "市场与资产" in focuses:
        search_queries.append(f"{topic_anchor} 原油 黄金 美债 汇率 市场")
    if "反证与不确定性" in focuses or "后续观察" in focuses:
        search_queries.append(f"{topic_anchor} 缓和 反证 执行 条件 观察")
    final_queries = list(dict.fromkeys(search_queries))[:6]
    return {
        "question": question,
        "topic": topic,
        "dimension": dimension,
        "start": inferred_start,
        "end": inferred_end,
        "focuses": focuses,
        "subquestions": [
            "最新发生了什么，按时间和当事方如何组织？",
            "哪些材料属于官方事实、媒体转述、单方声明或分析推断？",
            "有哪些相反证据、执行缺口和下一观察点？",
        ] + (
            ["停火或会谈是否有明确时间、地点、参与方和执行条件？", "前线打击、援助交付及美欧支持能力出现了哪些新变化？"]
            if topic == "ukraine" and complex_question else []
        ) + (["量化指标与资产表现如何变化，传导边界是什么？"] if "市场与资产" in focuses else []),
        "search_queries": final_queries,
        "query_specs": _query_specs(topic, final_queries, inferred_start, inferred_end),
        "time_windows": freshness_windows(question, anchor),
        "needs_metrics": "市场与资产" in focuses,
        "needs_analysis": any(label in focuses for label in ("市场与资产", "反证与不确定性", "后续观察")),
        "needs_internal_messages": any(label in focuses for label in ("事件主线", "外交", "军事", "市场与资产")),
        "readiness_gate": complex_question,
        "multi_agent": {
            "enabled": multi_agent_enabled,
            "agents": specialist_roles,
            "reason": "复杂综合问题，按事件、市场传导和反证核验拆分" if multi_agent_enabled else "窄范围问题使用单 Agent 路径",
            "tasks": [task_definitions[role] for role in specialist_roles],
            "budget": {
                "total_tool_attempts": 36 if multi_agent_enabled else 18,
                "specialist_tool_calls": 12 if multi_agent_enabled else 0,
                "per_task_tool_calls": 4,
                "wall_seconds": 120,
                "max_parallel": 2,
            },
        },
        "budget": budget,
        "basis": "deterministic",
    }


async def build_question_plan(
    question: str,
    *,
    topic: str,
    dimension: str,
    start: str,
    end: str,
    snapshot_as_of: str,
    complete: Callable[..., Awaitable[tuple[str, str]]] | None = None,
) -> tuple[dict[str, Any], str]:
    fallback = deterministic_plan(question, topic=topic, dimension=dimension, start=start, end=end, snapshot_as_of=snapshot_as_of)
    if complete is None:
        return fallback, ""
    prompt = {
        "question": question,
        "fixed_scope": {"topic": topic, "dimension": dimension, "start": start, "end": end, "snapshot_as_of": snapshot_as_of},
        "fallback_plan": fallback,
    }
    system = (
        "你是GeoAgent问题规划器。只输出JSON对象，不回答问题。保留fixed_scope中的显式筛选；"
        "把问题拆成时间范围、事件主线、外交、军事、市场、反证和观察点。"
        "字段只能是focuses、subquestions、search_queries、needs_metrics、needs_analysis、needs_internal_messages；"
        "search_queries最多5个简短中文内部检索词，不得添加事实。"
    )
    try:
        content, model = await complete(
            [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
            model_role="fast",
        )
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        value = json.loads(cleaned)
        if not isinstance(value, dict):
            raise ValueError("plan is not an object")
        for key in ("focuses", "subquestions", "search_queries"):
            if isinstance(value.get(key), list):
                proposed = [str(item).strip()[:180] for item in value[key] if str(item).strip()]
                # A fast planning model may omit a research facet. Merge its
                # additions into the deterministic minimum instead of letting
                # it erase diplomacy, military, market or counterevidence work.
                if key == "search_queries":
                    merged = [*fallback[key][:4], *proposed, *fallback[key][4:]]
                    fallback[key] = list(dict.fromkeys(merged))[:6]
                else:
                    fallback[key] = list(dict.fromkeys([*fallback[key], *proposed]))[:8]
        for key in ("needs_metrics", "needs_analysis", "needs_internal_messages"):
            if isinstance(value.get(key), bool):
                fallback[key] = bool(fallback[key] or value[key])
        fallback["basis"] = "model+deterministic"
        fallback["query_specs"] = _query_specs(
            topic, fallback["search_queries"], fallback.get("start", ""), fallback.get("end", ""),
        )
        return fallback, model
    except Exception:
        return fallback, ""
