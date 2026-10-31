import json

import pytest
from fastapi.testclient import TestClient

from backend.adapters import normalize
from backend.answer_business_lead import BusinessLeadResult
from backend.answer_report import _lead_from_markdown, render_answer_report
from backend.app import app
from backend.store import canonical, db, save_snapshot


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path / "data"))
    async def generate(answer, snapshot):
        return BusinessLeadResult(
            "美伊局势处于高压僵持，外交窗口仍未关闭；近期谈判信号与军事戒备并行，后续主要取决于双方是否出现可核验的接触和实际行动降温。若紧张继续推升能源与运输风险，通胀预期和利率压力可能上升；若谈判落实并伴随部署回撤，风险溢价才可能缓解。现有媒体转述只能作为线索，不能证明行动已经发生。",
            "usiran",
            "model",
            ("report-source",),
        )
    monkeypatch.setattr("backend.app.generate_business_lead", generate)


def persisted_answer(summary=True):
    record = normalize(
        {
            "id": "report-source",
            "title": "美伊谈判与地区军事戒备出现新变化",
            "summary": "公开报道显示谈判仍在推进，但相关军事部署说法仍需独立核验。",
            "link": "https://example.org/report",
            "published": "2026-10-22T01:10:00Z",
            "fetched_at": "2026-10-22T01:20:00Z",
            "source_id": "wire",
            "source_name": "示例通讯社",
            "tier": "T2",
        },
        "readiness-monitor",
    )
    snapshot_id, _ = save_snapshot({"records": [record], "observations": [], "sources": [], "manifest": [], "errors": []})
    answer = {
        "id": "internal-answer-id",
        "snapshot_id": snapshot_id,
        "as_of": "2026-10-22T01:18:00Z",
        "query": "美伊冲突最新进展",
        "topic": "usiran",
        "filters": {"dimension": "", "start": "", "end": ""},
        "created_at": "2026-10-22T01:22:00Z",
        "mode": "现场模型分析",
        "message": "回答完成",
        "summary": {"text": "## 一、结论摘要\n\n当前局势仍需交叉核验。", "evidence_ids": [record["id"]]} if summary else None,
        "facts": [],
        "citations": [record],
        "inferences": [],
        "gaps": ["缺少双方共同确认的协议文本。"],
        "steps": [],
    }
    with db() as conn:
        conn.execute(
            "INSERT INTO answers VALUES (%s,%s,%s,%s,%s)",
            (answer["id"], snapshot_id, answer["query"], answer["created_at"], canonical(answer)),
        )
    return answer, record


def test_answer_report_download_contains_readable_evidence_without_internal_ids():
    answer, record = persisted_answer()
    response = TestClient(app).get(f'/api/answers/{answer["id"]}/report.html')

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    assert "美伊冲突最新进展" in response.text
    assert "示例通讯社" in response.text
    assert "发布时间与采集时间" in response.text
    assert "2026-10-22 01:10 世界时" in response.text
    assert "https://example.org/report" in response.text
    assert answer["id"] not in response.text
    assert answer["snapshot_id"] not in response.text
    assert record["id"] not in response.text
    assert "美伊局势处于高压僵持" in response.text
    assert "导出时调用模型生成业务核心判断" in response.text


def test_answer_report_uses_reference_dark_research_report_style():
    answer, _ = persisted_answer()

    document = render_answer_report(answer, "2026-10-22T02:00:00Z")

    assert "--bg:#0b1118" in document
    assert "--gold:#c9a227" in document
    assert 'class="wrap"' in document
    assert 'class="lead"' in document
    assert 'class="cards"' in document
    assert "综合研判" in document
    assert "证据与信源" in document
    assert "口径、限制与缺口" in document
    assert "@media print" in document
    assert "grid-template-columns:220px" not in document


def test_answer_report_lead_keeps_complete_markdown_answer():
    answer, _ = persisted_answer()
    answer["summary"]["text"] = (
        "## 一、结论摘要\n\n"
        "当前局势处于高压对峙，外交窗口仍未关闭。\n\n"
        "## 二、关键证据\n\n"
        "后续完整证据章节必须保留。"
    )

    document = render_answer_report(answer, "2026-10-22T02:00:00Z")

    assert "当前局势处于高压对峙，外交窗口仍未关闭。" in document
    assert "二、关键证据" in document
    assert "后续完整证据章节必须保留。" in document
    assert document.count("当前局势处于高压对峙，外交窗口仍未关闭。") == 2


def test_answer_report_prefers_generated_business_lead_and_keeps_original_body():
    answer, _ = persisted_answer()
    generated = BusinessLeadResult("这是根据用户业务语境生成的核心判断。", "usiran", "model")

    document = render_answer_report(answer, "2026-10-22T02:00:00Z", generated)

    assert "这是根据用户业务语境生成的核心判断。" in document
    assert "当前局势仍需交叉核验。" in document
    assert "导出时调用模型生成业务核心判断" in document


def test_answer_report_fallback_warning_is_in_limitations():
    answer, _ = persisted_answer()
    generated = BusinessLeadResult(
        "当前局势仍需交叉核验。",
        "usiran",
        "fallback",
        warning="业务核心判断生成失败，已使用原回答回退。",
    )

    document = render_answer_report(answer, "2026-10-22T02:00:00Z", generated)

    assert "业务核心判断生成失败，已使用原回答回退。" in document


def test_lead_from_markdown_ignores_headings_and_markup():
    value = "## 一、结论摘要\n\n**核心判断：** 冲突仍在持续，[外交接触](https://example.org)尚未中断。\n\n- 线索一"

    assert _lead_from_markdown(value) == "核心判断： 冲突仍在持续，外交接触尚未中断。"


def test_answer_report_renders_explicit_empty_states():
    answer, _ = persisted_answer()
    answer["summary"]["evidence_ids"] = []
    answer["citations"] = []
    answer["gaps"] = []

    document = render_answer_report(answer, "2026-10-22T02:00:00Z")

    assert "当前回答没有可展开的逐条引用" in document
    assert "当前回答未单列其他数据缺口" in document


def test_answer_report_escapes_active_content_and_unsafe_source_links():
    answer, record = persisted_answer()
    answer["summary"]["text"] = "结论需要核验。<script>alert(1)</script>"
    answer["citations"][0]["title"] = '<img src=x onerror="alert(1)">'
    answer["citations"][0]["url"] = "javascript:alert(1)"

    document = render_answer_report(answer, "2026-10-22T02:00:00Z")

    assert "<script>" not in document
    assert "<img src=x" not in document
    assert "javascript:" not in document
    assert "&lt;script&gt;" in document
    assert "&lt;img" in document


def test_answer_report_without_summary_returns_conflict():
    answer, _ = persisted_answer(summary=False)
    response = TestClient(app).get(f'/api/answers/{answer["id"]}/report.html')
    assert response.status_code == 409
    assert response.json()["detail"] == "该回答没有可导出的总结内容"


def test_unknown_answer_report_returns_not_found():
    response = TestClient(app).get('/api/answers/missing/report.html')
    assert response.status_code == 404

