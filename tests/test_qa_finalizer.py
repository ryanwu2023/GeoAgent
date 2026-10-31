from backend.qa_claims import (
    ClaimDependency, ClaimLedger, ClaimRecord, ClaimSpan, EvidenceRelation, NumericProvenance,
)
from backend.qa_finalizer import deterministic_fallback, normalize_markdown_lists, validate_and_repair
from backend.qa_harness_state import HarnessScope
from backend.qa_synthesizer import DraftAnswer


def test_deterministic_fallback_always_returns_chinese_answer_and_separates_internal_messages():
    result = deterministic_fallback(
        last_valid_draft="", research_memory={"gaps": ["缺少官方声明"]},
        evidence=[{"id": "r1", "title": "公开报道标题", "source_name": "新华社", "published_at": "2026-10-24"}],
        metrics=[], internal_messages=[{"id": "hm1", "title": "内部观察"}],
        scope=HarnessScope("s1"), failure="模型超时",
    )
    assert result.text
    assert "结论摘要" in result.text
    assert "最新进展与依据" in result.text
    assert "判断与不确定性" in result.text
    assert "华泰内部消息补充" in result.text
    assert "hm1" in result.text
    assert result.status == "degraded"
    assert result.evidence_ids == ["r1"]
    assert result.citation_map == [{"anchor": "S1", "evidence_ids": ["r1"]}]
    assert "〔S1〕" in result.annotated_text
    assert "〔S1〕" not in result.text


def test_readable_draft_survives_citation_failure_without_broad_evidence_binding():
    result = deterministic_fallback(
        last_valid_draft="## 结论\n\n外交接触仍在继续，但行动层面尚待核验〔S9〕。",
        research_memory={"gaps": []},
        evidence=[
            {"id": "r1", "title": "第一条公开报道", "source_name": "新华社"},
            {"id": "r2", "title": "第二条公开报道", "source_name": "路透社"},
        ],
        metrics=[], internal_messages=[], scope=HarnessScope("s1", topic="usiran"),
        failure="引用映射未通过校验",
    )
    assert "外交接触仍在继续" in result.text
    assert "S9" not in result.text
    assert result.citation_map == []
    assert result.evidence_ids == []
    assert any("逐主张引用映射" in gap for gap in result.gaps)


def test_generated_fallback_maps_each_fact_to_its_own_evidence():
    result = deterministic_fallback(
        last_valid_draft="", research_memory={"gaps": []},
        evidence=[
            {"id": "r1", "title": "第一条公开报道", "source_name": "新华社"},
            {"title": "没有稳定ID的报道", "source_name": "公开来源"},
            {"id": "r2", "title": "第二条公开报道", "source_name": "路透社"},
        ],
        metrics=[], internal_messages=[], scope=HarnessScope("s1", topic="usiran"),
        failure="模型超时",
    )
    assert result.citation_map == [
        {"anchor": "S1", "evidence_ids": ["r1"]},
        {"anchor": "S2", "evidence_ids": ["r2"]},
    ]
    assert "第一条公开报道（新华社，时间未标注）〔S1〕" in result.annotated_text
    assert "第二条公开报道（路透社，时间未标注）〔S2〕" in result.annotated_text
    assert "没有稳定ID的报道（公开来源，时间未标注）〔" not in result.annotated_text


def test_invalid_numeric_draft_is_removed_but_fallback_still_answers():
    result = deterministic_fallback(
        last_valid_draft="局势升级概率为99%，已经可以确认。",
        research_memory={"gaps": []},
        evidence=[{"id": "r1", "title": "美伊双方继续通过调解方接触", "source_name": "新华社", "published_at": "2026-10-24"}],
        metrics=[], internal_messages=[],
        scope=HarnessScope("s1", topic="usiran"), failure="模型总结包含无来源数值：99",
    )
    assert "99" not in result.text
    assert "美伊局势" in result.text
    assert "美伊双方继续通过调解方接触" in result.text
    assert "后续观察" in result.text
    assert result.status == "degraded"


def test_fallback_keeps_a_valid_zero_metric_value():
    result = deterministic_fallback(
        last_valid_draft="", research_memory={"gaps": []}, evidence=[],
        metrics=[{"series": "spread", "name": "信用利差变化", "current": 0}],
        internal_messages=[], scope=HarnessScope("s1", topic="usiran"), failure="模型超时",
    )
    assert "量化观察" in result.text
    assert "信用利差变化：0" in result.text


def test_numeric_validation_failure_removes_only_the_rejected_sentence():
    result = deterministic_fallback(
        last_valid_draft="## 结论\n\n外交接触仍在继续。未经支持的升级概率为0.07。后续需核对官方声明。",
        research_memory={"gaps": []},
        evidence=[{"id": "r1", "title": "调解方继续接触", "source_name": "新华社", "published_at": "2026-10-24"}],
        metrics=[], internal_messages=[], scope=HarnessScope("s1", topic="usiran"),
        failure="模型总结包含引用材料中不存在的数值：0.07",
    )
    assert "0.07" not in result.text
    assert "外交接触仍在继续" in result.text
    assert "后续需核对官方声明" in result.text


def test_citation_mapping_failure_keeps_clean_readable_draft():
    result = deterministic_fallback(
        last_valid_draft="## 结论\n\n局势仍处于外交接触与升级风险并行阶段〔S9〕。",
        research_memory={"gaps": []}, evidence=[], metrics=[], internal_messages=[],
        scope=HarnessScope("s1", topic="usiran"), failure="资产或情景引用无效",
    )
    assert "局势仍处于外交接触与升级风险并行阶段" in result.text
    assert "S9" not in result.text


def test_reference_only_draft_is_replaced_by_a_complete_fallback_answer():
    result = deterministic_fallback(
        last_valid_draft="（见上）", research_memory={"gaps": ["缺少联合声明"]},
        evidence=[{"id": "r1", "title": "俄乌双方继续接触", "source_name": "新华社", "published_at": "2026-10-25"}],
        metrics=[], internal_messages=[], scope=HarnessScope("s1", topic="ukraine"),
        failure="模型仅返回了上文引用，未生成可展示的最终正文",
    )
    assert result.text != "（见上）"
    assert "结论摘要" in result.text
    assert "俄乌双方继续接触" in result.text


def test_markdown_ordered_blocks_are_renumbered_without_changing_body_numbers():
    text = "## 观察点\n\n1. 第一项\n2. 第二项含油价110.5美元\n5. 第三项\n\n1、另一列表\n4、下一项"
    repaired = normalize_markdown_lists(text)
    assert "1. 第一项" in repaired
    assert "2. 第二项含油价110.5美元" in repaired
    assert "3. 第三项" in repaired
    assert "1、另一列表" in repaired
    assert "2、下一项" in repaired
    assert "110.5" in repaired


def test_numeric_failure_drops_complete_bad_list_item_and_closes_numbering_gap():
    result = deterministic_fallback(
        last_valid_draft=(
            "## 反证\n\n1. 外交接触仍在继续。\n"
            "2. 升级概率为0.07；样本为单日点值。\n"
            "4. 市场变化不能单独证明因果。"
        ),
        research_memory={"gaps": []}, evidence=[], metrics=[], internal_messages=[],
        scope=HarnessScope("s1", topic="usiran"),
        failure="模型总结包含引用材料中不存在的数值：0.07",
    )
    assert "0.07" not in result.text
    assert "样本为单日点值" not in result.text
    assert "1. 外交接触仍在继续" in result.text
    assert "2. 市场变化不能单独证明因果" in result.text
    assert "4. 市场" not in result.text


def test_numeric_failure_drops_complete_bad_table_row():
    result = deterministic_fallback(
        last_valid_draft="| 指标 | 值 | 说明 |\n|---|---|---|\n| 布伦特 | 116.72 | 周涨7.3%无依据 |\n| 美债 | 5.00 | 可用 |",
        research_memory={"gaps": []}, evidence=[], metrics=[], internal_messages=[],
        scope=HarnessScope("s1", topic="usiran"),
        failure="模型总结包含引用材料中不存在的数值：7.3",
    )
    assert "7.3" not in result.text
    assert "无依据" not in result.text
    assert "美债 | 5.00" in result.text


def test_numeric_failure_matches_exact_values_and_scientific_notation():
    result = deterministic_fallback(
        last_valid_draft=(
            "## 结论\n\n2029年仍需持续观察。\n"
            "科学计数法错误值为1E+1。\n"
            "第9项未经证据支持。"
        ),
        research_memory={"gaps": []}, evidence=[], metrics=[], internal_messages=[],
        scope=HarnessScope("s1", topic="usiran"),
        failure="模型总结包含引用材料中不存在的数值：10、9",
    )
    assert "1E+1" not in result.text
    assert "第9项" not in result.text
    assert "2029年仍需持续观察" in result.text


def _claim(ledger, text, *, nature="media_report", supported=True, evidence="e1"):
    numeric = []
    if nature == "program_calculation":
        numeric = [NumericProvenance(
            value="5.0", unit="%", observed_at="2026-10-31", statistical_scope="收盘",
            input_series=["x:t", "x:t-1"], formula="(t/t-1-1)*100", result="5.0",
        )]
    item = ledger.upsert_claim(ClaimRecord.create(
        text=text, information_nature=nature, scope_version="research-scope/v1",
        numeric_provenance=numeric,
    ))
    if supported:
        ledger.add_relation(EvidenceRelation.create(
            claim_id=item.claim_id, evidence_id=evidence, relation="supports",
            evidence_nature=nature, origin_group=evidence,
            span=ClaimSpan(0, 2, f"{evidence}-v1", "证据", content_kind="structured_value" if nature == "program_calculation" else "full_text"),
        ))
    return item


def _draft(text, claim_quotes, **kwargs):
    version = DraftAnswer.content_version(text)
    spans = {}
    for claim_id, quote in claim_quotes.items():
        start = text.index(quote)
        spans[claim_id] = [ClaimSpan(start, start + len(quote), version, quote)]
    return DraftAnswer(
        markdown=text, claim_spans=spans, citation_map=kwargs.get("citation_map", []),
        gaps=[], stage_version="stage-1", business_lead=kwargs.get("business_lead", ""),
        business_lead_claim_ids=kwargs.get("business_lead_claim_ids", []),
    )


def test_claim_level_repair_removes_only_unsupported_duplicate_number_claim():
    ledger = ClaimLedger()
    supported = _claim(ledger, "程序显示指标上升5.0%", nature="program_calculation", evidence="metric-1")
    unsupported = _claim(ledger, "媒体称开战概率为5.0%", supported=False)
    text = "## 核心判断\n\n程序显示指标上升5.0%。媒体称开战概率为5.0%。后续仍需观察。"
    final = validate_and_repair(_draft(text, {
        supported.claim_id: "程序显示指标上升5.0%",
        unsupported.claim_id: "媒体称开战概率为5.0%",
    }), ledger, deadline=9999999999)
    assert "程序显示指标上升5.0%" in final.text
    assert "开战概率" not in final.text
    assert "后续仍需观察" in final.text
    assert final.evidence_ids == ["metric-1"]


def test_rejected_premise_also_removes_dependent_inference():
    ledger = ClaimLedger()
    premise = _claim(ledger, "航运已经完全中断", supported=False)
    inference = _claim(ledger, "因此油价必然持续上涨", nature="model_inference", supported=False)
    ledger.add_dependency(ClaimDependency(premise.claim_id, inference.claim_id))
    text = "航运已经完全中断。因此油价必然持续上涨。外交接触仍在继续。"
    final = validate_and_repair(_draft(text, {
        premise.claim_id: premise.text, inference.claim_id: inference.text,
    }), ledger, deadline=9999999999)
    assert "航运已经完全中断" not in final.text
    assert "油价必然持续上涨" not in final.text
    assert "外交接触仍在继续" in final.text


def test_invalid_citation_mapping_does_not_clear_supported_body():
    ledger = ClaimLedger()
    claim = _claim(ledger, "外交接触仍在继续", evidence="e1")
    text = "## 核心判断\n\n外交接触仍在继续。"
    draft = _draft(text, {claim.claim_id: claim.text}, citation_map=[{
        "anchor": "S9", "claim_ids": [claim.claim_id], "evidence_ids": ["invented"],
    }])
    final = validate_and_repair(draft, ledger, deadline=9999999999)
    assert "外交接触仍在继续" in final.text
    assert final.citation_map[0]["evidence_ids"] == ["e1"]


def test_business_lead_is_repaired_with_same_claim_version_as_body():
    ledger = ClaimLedger()
    claim = _claim(ledger, "外交接触持续但军事行动未获确认", evidence="e1")
    text = "## 核心判断\n\n外交接触持续但军事行动未获确认。\n\n## 依据\n正文。"
    draft = _draft(
        text, {claim.claim_id: claim.text},
        business_lead="外交接触持续但军事行动未获确认",
        business_lead_claim_ids=[claim.claim_id],
    )
    final = validate_and_repair(draft, ledger, deadline=9999999999)
    assert final.business_lead == "外交接触持续但军事行动未获确认"
    assert final.business_lead_claim_ids == [claim.claim_id]
    assert final.stage_version == "stage-1"
