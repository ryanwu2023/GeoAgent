"""Run the fixed GeoAgent Q&A benchmark against the currently configured provider."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


def _answer_text(answer):
    summary = answer.get("summary")
    if isinstance(summary, dict):
        return str(summary.get("text") or "")
    final = answer.get("final")
    if isinstance(final, dict):
        return str(final.get("text") or final.get("answer_markdown") or "")
    return str(summary or "")


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _ratio(hit, total):
    return round(hit / max(1, total), 4)


def _component(score_value, maximum, detail):
    return {"score": round(max(0.0, min(float(maximum), float(score_value))), 2), "max": maximum, "detail": detail}


def _markdown_table_valid(text):
    rows = [line for line in text.splitlines() if line.strip().startswith("|")]
    if not rows:
        return True
    widths = [line.count("|") for line in rows]
    return min(widths) >= 2 and len(set(widths)) == 1


def evaluate_thresholds(scored, policy):
    """Turn component metrics into a repeatable benchmark pass/fail decision."""
    failures = []
    if policy.get("fail_on_hard_failure", True) and scored.get("hard_failures"):
        failures.extend(f"hard_failure:{item}" for item in scored["hard_failures"])
    checks = (
        ("quality_score", "min_quality_score"),
        ("fact_coverage_rate", "min_fact_coverage_rate"),
        ("citation_support_rate", "min_citation_support_rate"),
        ("topic_purity_rate", "min_topic_purity_rate"),
    )
    for metric, threshold_name in checks:
        if threshold_name not in policy:
            continue
        actual = float(scored.get(metric) or 0)
        minimum = float(policy[threshold_name])
        if actual < minimum:
            failures.append(f"{metric}:{actual}<{minimum}")
    if policy.get("require_non_empty_final", True) and scored.get("text_chars", 0) <= 0:
        failures.append("final_answer_empty")
    return {"passed": not failures, "failures": failures}


def score(answer, expected, gold=None):
    gold = gold or {}
    expected_config = expected if isinstance(expected, dict) else {"expected_facets": expected}
    expected_facets = expected_config.get("expected_facets") or []
    topic = str(expected_config.get("topic") or gold.get("topic") or "all")
    text = _answer_text(answer).strip()
    citations = answer.get("citations") or []
    summary = answer.get("summary") if isinstance(answer.get("summary"), dict) else {}
    evidence_ids = {str(item) for item in summary.get("evidence_ids", []) if str(item)}
    citation_ids = {str(item.get("id")) for item in citations if isinstance(item, dict) and item.get("id")}
    citation_map = answer.get("citation_map") or (answer.get("final") or {}).get("citation_map") or []
    facet_hits = {facet: facet.lower() in text.lower() for facet in expected_facets}
    uncertainty = any(term in text for term in ("不确定", "待核验", "不能", "尚未", "缺口", "反证"))
    boundary = any(term in text for term in ("不等于因果", "条件传导", "同期变化", "报道计数不是", "不是概率"))
    trace = answer.get("analysis_trace") or []
    tool_steps = [item for item in trace if item.get("kind") == "tool" or item.get("tool")]
    durations = [int(item.get("duration_ms") or 0) for item in tool_steps]

    hard_failures = []
    if not text or re.fullmatch(r"[（(]?\s*(?:见上|同上|参见上文)\s*[）)]?[。.]?", text):
        hard_failures.append("empty_or_placeholder_answer")
    forbidden = [str(item) for item in gold.get("forbidden_terms", []) if str(item)]
    pollution_hits = [item for item in forbidden if item.lower() in text.lower()]
    if pollution_hits:
        hard_failures.append("topic_pollution")
    if not _markdown_table_valid(text):
        hard_failures.append("malformed_markdown_table")

    as_of = _stamp(answer.get("as_of") or answer.get("created_at")) or datetime.now(timezone.utc)
    recent_days = max(1, int(gold.get("recent_days", 3) or 3))
    recent_cutoff = as_of - timedelta(days=recent_days)
    dated_citations = [item for item in citations if isinstance(item, dict) and _stamp(item.get("published_at"))]
    recent_citations = [item for item in dated_citations if _stamp(item.get("published_at")) >= recent_cutoff]
    recent_rate = _ratio(len(recent_citations), len(dated_citations)) if dated_citations else 0.0
    stale_disclosed = any(term in text for term in ("无新增", "较早记录", "数据滞后", "截至", "最近72小时", "最近 72 小时"))

    fact_groups = [group for group in gold.get("fact_groups", []) if isinstance(group, list) and group]
    fact_hits = [any(str(term).lower() in text.lower() for term in group) for group in fact_groups]
    fact_rate = _ratio(sum(fact_hits), len(fact_hits)) if fact_hits else 1.0

    valid_maps = []
    for item in citation_map if isinstance(citation_map, list) else []:
        if not isinstance(item, dict):
            continue
        anchor = str(item.get("anchor") or "")
        ids = {str(value) for value in item.get("evidence_ids", []) if str(value)}
        valid = bool(anchor and anchor in text and ids and ids.issubset(citation_ids))
        valid_maps.append(valid)
    citation_support = _ratio(sum(valid_maps), len(valid_maps)) if valid_maps else (
        _ratio(len(evidence_ids & citation_ids), len(evidence_ids)) if evidence_ids else 0.0
    )
    openable_rate = _ratio(len(evidence_ids & citation_ids), len(evidence_ids)) if evidence_ids else (1.0 if citations else 0.0)

    topic_citations = [item for item in citations if isinstance(item, dict) and isinstance(item.get("topics"), list) and item.get("topics")]
    topic_matches = sum(topic in item.get("topics", []) for item in topic_citations) if topic not in {"", "all"} else len(topic_citations)
    topic_purity = _ratio(topic_matches, len(topic_citations)) if topic_citations else (0.0 if pollution_hits else 1.0)

    date_mentions = len(re.findall(r"(?:20\d{2}[-年/.]\d{1,2}(?:[-月/.]\d{1,2})?|\d{1,2}月\d{1,2}日)", text))
    change_language = any(term in text for term in ("变化", "此前", "随后", "同期", "相较", "从", "转为", "主线"))
    source_boundaries = sum(any(term in text for term in group) for group in (
        ("官方", "行政动作"), ("单方", "当事方声明"), ("媒体转述", "报道称"), ("模型推论", "研究推断", "判断"),
    ))
    counter_present = any(term in text for term in ("反证", "相反", "否认", "尚未", "不确定", "但", "不过"))
    watch_present = any(term in text for term in ("观察点", "后续观察", "触发条件", "跟踪", "未来"))
    asset_terms = sum(term in text for term in ("原油", "黄金", "美债", "美元", "权益", "信用", "汇率"))
    mechanism = any(term in text for term in ("传导", "通过", "导致", "推升", "压制", "风险溢价", "通胀预期"))
    numeric_present = bool(re.search(r"\d+(?:\.\d+)?\s*(?:%|美元|基点|bp|艘次|桶|点|亿元|万桶)", text, re.I))
    observation_basis = any(term in text for term in ("观测日期", "截至", "前值", "变化", "频率", "滞后"))
    metric_problem = any(term in text for term in ("数值校验未通过", "单位错误", "引用数值不存在"))
    required_sections = [str(item) for item in gold.get("required_sections", []) if str(item)]
    section_rate = _ratio(sum(item in text for item in required_sections), len(required_sections)) if required_sections else 1.0

    components = {
        "scope_freshness": _component(8 * min(1.0, 0.65 * recent_rate + 0.35 * float(stale_disclosed)), 8, {"recent_rate": recent_rate, "stale_disclosed": stale_disclosed}),
        "fact_coverage": _component(15 * fact_rate, 15, {"groups": fact_hits}),
        "timeline_mainline": _component(8 * min(1.0, (min(date_mentions, 2) / 2) * 0.55 + float(change_language) * 0.45), 8, {"date_mentions": date_mentions, "change_language": change_language}),
        "source_boundaries": _component(10 * min(1.0, source_boundaries / 3), 10, {"boundary_classes": source_boundaries}),
        "citation_accuracy": _component(15 * (0.75 * citation_support + 0.25 * openable_rate), 15, {"support_rate": citation_support, "openable_rate": openable_rate}),
        "counterevidence": _component(10 * (0.65 * float(counter_present) + 0.35 * float(watch_present)), 10, {"counter": counter_present, "watch": watch_present}),
        "metric_integrity": _component(10 if not gold.get("requires_metrics") else 10 * (0.55 * float(numeric_present) + 0.45 * float(observation_basis)) * (0 if metric_problem else 1), 10, {"numeric": numeric_present, "basis": observation_basis, "problem": metric_problem}),
        "asset_explanation": _component(8 if not gold.get("requires_assets") else 8 * min(1.0, asset_terms / 3 * 0.55 + float(mechanism) * 0.25 + float(boundary) * 0.2), 8, {"asset_terms": asset_terms, "mechanism": mechanism, "causal_boundary": boundary}),
        "gaps_watchpoints": _component(6 * (0.55 * float(uncertainty) + 0.45 * float(watch_present)), 6, {"uncertainty": uncertainty, "watch": watch_present}),
        "topic_purity": _component(7 * topic_purity, 7, {"topic": topic, "matching_rate": topic_purity, "pollution_hits": pollution_hits}),
        "readability": _component(3 * (0.6 * section_rate + 0.4 * float(len(text) >= 120 and _markdown_table_valid(text))), 3, {"section_rate": section_rate, "chars": len(text)}),
    }
    quality_score = round(sum(item["score"] for item in components.values()), 2)
    if "empty_or_placeholder_answer" in hard_failures:
        quality_score = 0.0
    elif any(item in hard_failures for item in ("topic_pollution", "malformed_markdown_table")):
        quality_score = min(59.0, quality_score)

    result = {
        "text_chars": len(text),
        "citations": len(citations),
        "citation_openable_rate": openable_rate,
        "citation_support_rate": citation_support,
        "recent_citation_rate": recent_rate,
        "topic_purity_rate": topic_purity,
        "fact_coverage_rate": fact_rate,
        "facet_hits": facet_hits,
        "facet_coverage": round(sum(facet_hits.values()) / max(1, len(facet_hits)), 4),
        "uncertainty_present": uncertainty,
        "causal_boundary_present": boundary,
        "tool_calls": len(answer.get("tool_calls") or []),
        "run_id": answer.get("run_id"),
        "run_status": answer.get("run_status"),
        "stage_count": len(trace),
        "tool_success_rate": round(sum(item.get("status") == "completed" for item in tool_steps) / max(1, len(tool_steps)), 4),
        "tool_duration_ms": sum(durations),
        "tool_retries": sum(int(item.get("retry_count") or 0) for item in tool_steps),
        "context_compactions": sum(bool(item.get("compacted")) for item in trace),
        "context_token_peak": max([int(item.get("context_tokens") or 0) for item in trace] or [0]),
        "candidate_evidence": max([len(item.get("evidence_ids") or []) for item in trace] or [0]),
        "degraded_reason": next((item.get("summary") for item in reversed(trace) if item.get("status") == "degraded"), ""),
        "mode": answer.get("mode"),
        "quality_score": quality_score,
        "components": components,
        "hard_failures": hard_failures,
        "pollution_hits": pollution_hits,
    }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--label", default="local-agent")
    parser.add_argument("--output", default="")
    parser.add_argument("--config", default="config/qa-benchmark.json")
    args = parser.parse_args()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    suite = json.loads(config_path.read_text(encoding="utf-8"))
    score_policy = suite.get("score_policy") or {}
    gold_path = Path(suite.get("gold_file") or "tests/fixtures/qa_gold.json")
    if not gold_path.is_absolute():
        gold_path = ROOT / gold_path
    gold_rows = json.loads(gold_path.read_text(encoding="utf-8")) if gold_path.exists() else {}
    output = Path(args.output) if args.output else ROOT / "data" / "qa-benchmarks" / f"{datetime.now():%Y%m%d-%H%M%S}-{args.label}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=300) as client:
        status = client.get("/api/status").json()
        snapshot_id = status.get("working_snapshot_id")
        if not snapshot_id:
            raise SystemExit("No working snapshot")
        results = []
        for item in suite["questions"]:
            started = time.monotonic()
            response = client.post("/api/ask", json={
                "snapshot_id": snapshot_id,
                "question": item["question"],
                "topic": item["topic"],
                "dimension": "",
                "start": "",
                "end": "",
                "use_model": True,
                "previous_answer_id": "",
            })
            response.raise_for_status()
            answer = response.json()
            run_status = None
            if answer.get("run_id"):
                status_response = client.get(f"/api/qa-runs/{answer['run_id']}")
                if status_response.is_success:
                    run_status = status_response.json()
            scored = score(answer, item, gold_rows.get(item["id"], {}))
            verdict = evaluate_thresholds(scored, score_policy)
            results.append({
                "id": item["id"],
                "question": item["question"],
                "topic": item["topic"],
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "score": scored,
                "verdict": verdict,
                "run": run_status,
                "answer": answer,
            })
    passed = sum(bool(item["verdict"]["passed"]) for item in results)
    payload = {
        "benchmark_version": suite["version"],
        "label": args.label,
        "snapshot_id": snapshot_id,
        "model": status.get("model"),
        "created_at": datetime.now().astimezone().isoformat(),
        "score_policy": score_policy,
        "summary": {
            "questions": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "pass_rate": _ratio(passed, len(results)),
            "average_quality_score": round(sum(item["score"]["quality_score"] for item in results) / max(1, len(results)), 2),
        },
        "results": results,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
