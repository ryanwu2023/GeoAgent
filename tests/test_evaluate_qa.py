from scripts.evaluate_qa import evaluate_suite, evaluate_thresholds, load_benchmark_inputs, score


def _answer(text, *, citations=None, citation_map=None, as_of="2026-10-26T02:00:00Z"):
    return {
        "summary": {"text": text, "evidence_ids": [item["id"] for item in citations or []]},
        "citations": citations or [],
        "citation_map": citation_map or [],
        "analysis_trace": [],
        "tool_calls": [],
        "as_of": as_of,
        "run_status": "completed",
    }


GOLD = {
    "fact_groups": [["谈判", "斡旋"], ["军事", "打击"], ["霍尔木兹", "胡塞"]],
    "required_sections": ["结论", "依据", "不确定", "观察"],
    "forbidden_terms": ["足球", "猴痘", "地震"],
    "recent_days": 3,
    "requires_counterevidence": True,
    "requires_assets": True,
    "requires_metrics": True,
}


def test_quality_score_rewards_fresh_supported_balanced_answer():
    text = """## 结论摘要
外交谈判仍在斡旋，军事打击计划尚未等于已经行动；霍尔木兹通行仍需核验。[S1]
## 判断依据
官方声明只证明该方作出表态，媒体转述仍待核验。布伦特原油为116.72美元/桶（2026-10-25），油价通过通胀预期影响美债收益率，但同期变化不等于因果。[S2]
## 反证与不确定性
尚未取得执行文件，黄金走势构成反向证据。
## 后续观察
观察正式协议、军事部署和海峡绝对通行量。"""
    citations = [
        {"id": "r1", "published_at": "2026-10-25T01:00:00Z", "topics": ["usiran"]},
        {"id": "r2", "published_at": "2026-10-24T01:00:00Z", "topics": ["usiran"]},
    ]
    result = score(
        _answer(text, citations=citations, citation_map=[
            {"anchor": "S1", "evidence_ids": ["r1"]},
            {"anchor": "S2", "evidence_ids": ["r2"]},
        ]),
        {"topic": "usiran", "expected_facets": ["外交", "军事", "反证", "观察点"]},
        GOLD,
    )
    assert result["quality_score"] >= 80
    assert result["hard_failures"] == []
    assert result["recent_citation_rate"] == 1.0
    assert result["citation_support_rate"] == 1.0


def test_empty_or_reference_only_final_answer_is_hard_failure():
    result = score(_answer("（见上）"), {"topic": "usiran", "expected_facets": []}, GOLD)
    assert "empty_or_placeholder_answer" in result["hard_failures"]
    assert result["quality_score"] == 0


def test_topic_pollution_caps_total_score():
    polluted = "## 结论\n俄乌最新变化是足球比赛、猴痘和地震消息。\n## 依据\n尚待核验。\n## 后续观察\n继续观察。"
    result = score(_answer(polluted), {"topic": "ukraine", "expected_facets": []}, {
        **GOLD, "fact_groups": [], "requires_assets": False, "requires_metrics": False,
    })
    assert "topic_pollution" in result["hard_failures"]
    assert result["quality_score"] <= 59


def test_invalid_citation_mapping_is_not_counted_as_supported():
    citations = [{"id": "r1", "published_at": "2026-10-25T01:00:00Z", "topics": ["usiran"]}]
    result = score(
        _answer("## 结论\n外交谈判。[S1]", citations=citations, citation_map=[{"anchor": "S1", "evidence_ids": ["missing"]}]),
        {"topic": "usiran", "expected_facets": []},
        {**GOLD, "fact_groups": [], "requires_assets": False, "requires_metrics": False},
    )
    assert result["citation_support_rate"] == 0.0
    assert result["components"]["citation_accuracy"]["score"] < 15


def test_old_citations_reduce_freshness_score():
    citations = [{"id": "old", "published_at": "2026-10-01T01:00:00Z", "topics": ["usiran"]}]
    result = score(
        _answer("## 结论\n近期尚无新增，存在不确定性。", citations=citations),
        {"topic": "usiran", "expected_facets": []},
        {**GOLD, "fact_groups": [], "requires_assets": False, "requires_metrics": False},
    )
    assert result["recent_citation_rate"] == 0.0
    assert result["components"]["scope_freshness"]["score"] < 8


def test_wrong_topic_citations_reduce_topic_purity_component():
    citations = [{"id": "r1", "published_at": "2026-10-25T01:00:00Z", "topics": ["ukraine"]}]
    result = score(
        _answer("## 结论\n美伊外交仍待核验。", citations=citations),
        {"topic": "usiran", "expected_facets": []},
        {**GOLD, "fact_groups": [], "requires_assets": False, "requires_metrics": False},
    )
    assert result["topic_purity_rate"] == 0.0
    assert result["components"]["topic_purity"]["score"] == 0.0


def test_threshold_policy_returns_executable_failure_reasons():
    verdict = evaluate_thresholds(
        {
            "quality_score": 69,
            "fact_coverage_rate": 0.8,
            "citation_support_rate": 0.5,
            "topic_purity_rate": 1.0,
            "text_chars": 200,
            "hard_failures": [],
        },
        {"min_quality_score": 70, "min_citation_support_rate": 0.8},
    )
    assert verdict["passed"] is False
    assert "quality_score:69.0<70.0" in verdict["failures"]
    assert "citation_support_rate:0.5<0.8" in verdict["failures"]


def test_explicit_answer_topic_mismatch_is_hard_failure():
    answer = _answer("## 结论\n美伊谈判仍待核验。")
    answer["topic"] = "ukraine"
    result = score(answer, {"topic": "usiran", "expected_facets": []}, {
        **GOLD, "fact_groups": [], "requires_assets": False, "requires_metrics": False,
    })
    assert "topic_mismatch" in result["hard_failures"]


def test_forbidden_core_claim_is_hard_failure():
    result = score(
        _answer("## 结论\n美国已经正式批准对伊朗发动全面战争。"),
        {"topic": "usiran", "expected_facets": []},
        {
            **GOLD,
            "fact_groups": [],
            "forbidden_claims": [{"id": "f1", "all_terms": ["正式批准", "全面战争"]}],
            "requires_assets": False,
            "requires_metrics": False,
        },
    )
    assert "forbidden_core_claim:f1" in result["hard_failures"]


def test_claim_support_requires_allowed_evidence_not_only_existing_id():
    citations = [
        {"id": "r1", "published_at": "2026-10-25T01:00:00Z", "topics": ["usiran"]},
        {"id": "r2", "published_at": "2026-10-25T02:00:00Z", "topics": ["usiran"]},
    ]
    result = score(
        _answer(
            "## 结论\n谈判尚未形成正式协议。[S1]",
            citations=citations,
            citation_map=[{"anchor": "S1", "evidence_ids": ["r2"]}],
        ),
        {"topic": "usiran", "expected_facets": []},
        {
            **GOLD,
            "fact_groups": [],
            "required_claims": [{
                "id": "c1", "all_terms": ["谈判", "尚未", "正式协议"],
                "evidence_ids": ["r1"], "review_status": "reviewed",
            }],
            "requires_assets": False,
            "requires_metrics": False,
        },
    )
    assert result["claim_support_rate"] == 0.0
    assert "unsupported_required_claim:c1" in result["hard_failures"]


def test_syndicated_citations_do_not_count_as_independent_confirmation():
    citations = [
        {"id": "r1", "origin_id": "wire-a", "topics": ["usiran"]},
        {"id": "r2", "origin_id": "wire-a", "topics": ["usiran"]},
    ]
    result = score(
        _answer(
            "## 结论\n设施受损已获多方确认。[S1]",
            citations=citations,
            citation_map=[{"anchor": "S1", "evidence_ids": ["r1", "r2"]}],
        ),
        {"topic": "usiran", "expected_facets": []},
        {
            **GOLD,
            "fact_groups": [],
            "required_claims": [{
                "id": "c2", "all_terms": ["设施受损", "多方确认"],
                "evidence_ids": ["r1", "r2"], "min_independent_sources": 2,
                "review_status": "reviewed",
            }],
            "requires_assets": False,
            "requires_metrics": False,
        },
    )
    assert "insufficient_independent_sources:c2" in result["hard_failures"]


def test_timeout_with_explicit_degraded_answer_is_not_fact_hard_failure():
    answer = _answer("## 结论\n工具超时；当前只能确认谈判仍待核验，资料存在缺口。")
    answer["run_status"] = "degraded"
    answer["analysis_trace"] = [{"status": "degraded", "error": {"kind": "tool_timeout"}}]
    result = score(answer, {"topic": "usiran", "expected_facets": []}, {
        **GOLD, "fact_groups": [], "requires_assets": False, "requires_metrics": False,
    })
    assert result["hard_failures"] == []


def test_suite_gate_checks_average_and_each_category_without_dropping_failures():
    current = [
        {"id": "a", "category": "最新局势", "score": {"quality_score": 82, "hard_failures": []}},
        {"id": "b", "category": "资产影响", "score": {"quality_score": 75, "hard_failures": []}},
    ]
    baseline = [
        {"id": "a", "category": "最新局势", "score": {"quality_score": 75, "hard_failures": []}},
        {"id": "b", "category": "资产影响", "score": {"quality_score": 76, "hard_failures": []}},
    ]
    verdict = evaluate_suite(current, baseline, {"min_average_gain": 5, "category_must_not_decline": True})
    assert verdict["passed"] is False
    assert "category_declined:资产影响" in verdict["failures"]


def test_benchmark_manifest_dry_load_has_fixed_ids_categories_and_gold():
    benchmark = load_benchmark_inputs(manifest_path="tests/fixtures/qa_benchmark_manifest.json")
    assert benchmark["validation"]["passed"] is True
    assert benchmark["validation"]["questions"] == 15
    assert "最新局势" in benchmark["validation"]["categories"]
    assert benchmark["validation"]["claims_review_status"] == "待冻结快照后逐条审阅"
