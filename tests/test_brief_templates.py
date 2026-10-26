import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.adapters import normalize
from backend.brief_templates import DEFAULT_TEMPLATE, get_template, save_template
from backend.research import create_brief
from backend.store import get_snapshot, save_snapshot


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)


def test_template_crud_requires_sources_and_preserves_system_default():
    client = TestClient(app)
    rows = client.get("/api/brief-templates").json()
    assert rows[0]["is_default"] is True and rows[0]["is_system"] is True
    assert client.delete(f'/api/brief-templates/{rows[0]["id"]}').status_code == 409
    config = {**rows[0], "name": "基金经理晨会模板", "is_default": False, "is_system": False}
    created = client.post("/api/brief-templates", json={"config": config})
    assert created.status_code == 200
    tid = created.json()["id"]
    assert client.put(f"/api/brief-templates/{tid}/default", json={}).json()["is_default"] is True
    invalid = {**config, "sections": [{**item, "enabled": False} for item in config["sections"]]}
    assert client.post("/api/brief-templates", json={"config": invalid}).status_code == 422
    assert client.delete(f"/api/brief-templates/{tid}").status_code == 200
    remaining = client.get("/api/brief-templates").json()
    assert next(item for item in remaining if item["is_system"])["is_default"] is True


def topic_snapshot():
    timestamp = datetime.now(timezone.utc).isoformat()
    iran = normalize({"title": "伊朗与美国恢复谈判但军事戒备持续", "summary": "双方声明谈判继续，地区军事戒备尚未解除。", "link": "https://example.org/iran", "published": timestamp, "source_id": "iran", "tier": "T2"}, "new")
    iran.update(topics=["usiran"], dimensions={"usiran": "diplomacy"})
    ukraine = normalize({"title": "俄乌前线交火持续并讨论停火条件", "summary": "公开报道显示前线行动仍在持续，各方同时讨论停火条件。", "link": "https://example.org/ukraine", "published": timestamp, "source_id": "ukraine", "tier": "T2"}, "new")
    ukraine.update(topics=["ukraine"], dimensions={"ukraine": "frontline"})
    payload = {"records": [iran, ukraine], "observations": [], "sources": [], "manifest": [], "errors": []}
    sid, _ = save_snapshot(payload)
    return get_snapshot(sid)


def test_model_daily_brief_has_bluf_developments_outlook_and_sources(monkeypatch):
    snapshot = topic_snapshot()
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    async def complete(messages):
        context = json.loads(messages[1]["content"])
        evidence_id = context["evidence"][0]["id"]
        topic = context["topic"]
        result = {
            "headline": f"{topic}局势每日简报",
            "bluf": {"text": "当前军事压力与外交接触并行，基准走势仍取决于后续行动是否收敛；资产影响需要结合能源供应与风险偏好继续验证。", "evidence_ids": [evidence_id]},
            "developments": [{"group": "iran" if "美伊" in topic else "third", "fact": "公开报道显示行动与外交接触同时延续。", "evidence_ids": [evidence_id]}],
            "outlook": {
                "baseline": {"text": "基准情景为局势延续。", "condition": "行动保持当前范围。", "counter": "出现可验证的全面降温。", "watch": "后续行动和谈判执行。", "evidence_ids": [evidence_id]},
                "tail": {"text": "尾部风险为冲突范围扩大。", "condition": "出现新的参与方或基础设施受损。", "counter": "行动范围持续受限。", "watch": "参与方与关键设施状态。", "evidence_ids": [evidence_id]},
            },
            "uncertainties": ["现有报道仍缺少独立交叉核验。"],
        }
        return json.dumps(result, ensure_ascii=False), "test-model"

    monkeypatch.setattr("backend.research.llm.complete", complete)
    result = asyncio.run(create_brief(snapshot, True))
    assert result["mode"] == "现场模型分析" and result["model"] == "test-model"
    for heading in ("核心判断", "当前主要事项", "事态发展研判", "大类资产影响", "证据与信源"):
        assert heading in result["markdown"]
    assert "基准情景" in result["markdown"] and "尾部风险" in result["markdown"]
    assert "发布时间" in result["markdown"] and "采集时间" in result["markdown"]
    assert "**伊朗**" in result["markdown"] and "**iran**" not in result["markdown"]


def test_custom_template_order_and_disabled_section_are_applied():
    snapshot = topic_snapshot()
    config = {**DEFAULT_TEMPLATE, "name": "精简模板", "is_default": False, "is_system": False}
    config["sections"] = [
        next(item for item in DEFAULT_TEMPLATE["sections"] if item["id"] == "title"),
        next(item for item in DEFAULT_TEMPLATE["sections"] if item["id"] == "outlook"),
        {**next(item for item in DEFAULT_TEMPLATE["sections"] if item["id"] == "developments"), "enabled": False},
        next(item for item in DEFAULT_TEMPLATE["sections"] if item["id"] == "bluf"),
        next(item for item in DEFAULT_TEMPLATE["sections"] if item["id"] == "assets"),
        next(item for item in DEFAULT_TEMPLATE["sections"] if item["id"] == "sources"),
    ]
    template = save_template(config)
    result = asyncio.run(create_brief(snapshot, False, template["id"]))
    assert "当前主要事项" not in result["markdown"]
    assert result["markdown"].index("事态发展研判") < result["markdown"].index("核心判断")


def test_daily_brief_excludes_broad_topic_mentions_and_deduplicates_news():
    timestamp = datetime.now(timezone.utc).isoformat()
    relevant = normalize({"title": "伊朗与美国恢复核谈判，军事戒备仍持续", "summary": "双方恢复核谈判，公开报道同时提到军事戒备尚未解除。", "link": "https://example.org/talks", "published": timestamp, "source_id": "wire-a", "tier": "T1"}, "new")
    duplicate = normalize({"title": "美国与伊朗恢复核谈判 军事戒备继续", "summary": "双方恢复核谈判，地区军事戒备持续。", "link": "https://example.org/talks-copy", "published": timestamp, "source_id": "wire-b", "tier": "T2"}, "new")
    pollution = normalize({"title": "伊朗代表出席国际环境大会", "summary": "会议讨论气候与环境合作。", "link": "https://example.org/environment", "published": timestamp, "source_id": "conference", "tier": "T1"}, "new")
    for record in (relevant, duplicate, pollution):
        record.update(topics=["usiran"], dimensions={"usiran": "diplomacy"})
    sid, _ = save_snapshot({"records": [relevant, duplicate, pollution], "observations": [], "sources": [], "manifest": [], "errors": []})
    snapshot = get_snapshot(sid)
    config = {**DEFAULT_TEMPLATE, "name": "美伊精简模板", "topic_scope": "usiran", "is_default": False, "is_system": False}
    template = save_template(config)
    result = asyncio.run(create_brief(snapshot, False, template["id"]))
    assert "环境大会" not in result["markdown"]
    developments = result["markdown"].split("## 当前主要事项", 1)[1].split("## 事态发展研判", 1)[0]
    assert developments.count("- **") == 1
    assert pollution["id"] not in result["markdown"]


def test_successful_model_brief_is_reused_and_force_bypasses_cache(monkeypatch):
    snapshot = topic_snapshot()
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    calls = []

    async def complete(messages):
        context = json.loads(messages[1]["content"])
        calls.append(context["topic"])
        evidence_id = context["evidence"][0]["id"]
        payload = {
            "headline": f'{context["topic"]}每日研判',
            "bluf": {"text": "军事与外交信号并存，后续方向取决于行动是否收敛，资产影响仍需市场数据验证。", "evidence_ids": [evidence_id]},
            "developments": [{"group": "third", "fact": "公开报道显示军事行动与外交接触仍在继续。", "evidence_ids": [evidence_id]}],
            "outlook": {
                "baseline": {"text": "局势延续。", "condition": "行动保持当前范围。", "counter": "出现可验证降温。", "watch": "后续行动。", "evidence_ids": [evidence_id]},
                "tail": {"text": "冲突扩散。", "condition": "更多参与方介入。", "counter": "行动持续受限。", "watch": "参与方变化。", "evidence_ids": [evidence_id]},
            },
            "uncertainties": ["仍缺少独立交叉核验。"],
        }
        return json.dumps(payload, ensure_ascii=False), "test-model"

    monkeypatch.setattr("backend.research.llm.complete", complete)
    first = asyncio.run(create_brief(snapshot, True))
    second = asyncio.run(create_brief(snapshot, True))
    forced = asyncio.run(create_brief(snapshot, True, force=True))
    assert first["reused"] is False
    assert second["reused"] is True and second["id"] == first["id"]
    assert forced["reused"] is False and forced["id"] != first["id"]
    assert len(calls) == 4


def test_model_failure_is_visible_and_remains_retryable(monkeypatch):
    snapshot = topic_snapshot()
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    calls = 0

    async def broken(_messages):
        nonlocal calls
        calls += 1
        raise ValueError("模型返回结构不完整")

    monkeypatch.setattr("backend.research.llm.complete", broken)
    first = asyncio.run(create_brief(snapshot, True))
    second = asyncio.run(create_brief(snapshot, True))
    assert first["mode"] == second["mode"] == "本地规则简报"
    assert set(first["generation_status"]["failures"]) == {"usiran", "ukraine"}
    assert calls == 4
    listed = TestClient(app).get("/api/briefs", params={"snapshot_id": snapshot["id"]}).json()
    assert listed[0]["generation_status"]["failures"]["usiran"] == "模型返回结构不完整"


def test_source_appendix_is_exactly_the_rendered_citation_closure():
    snapshot = topic_snapshot()
    result = asyncio.run(create_brief(snapshot, False))
    body, appendix = result["markdown"].split("## 证据与信源", 1)
    cited = set()
    for raw in __import__("re").findall(r"\[证据：([^\]]+)\]", body):
        cited.update(part.strip() for part in raw.split(",") if part.strip())
    source_ids = set(__import__("re").findall(r"^- ([0-9a-f]{24}) ·", appendix, __import__("re").M))
    assert source_ids == cited
    assert result["generation_status"]["source_count"] == len(source_ids)


def test_source_ledger_is_filled_when_template_places_it_first():
    snapshot = topic_snapshot()
    config = {**DEFAULT_TEMPLATE, "name": "信源前置模板", "is_default": False, "is_system": False}
    sources = next(item for item in DEFAULT_TEMPLATE["sections"] if item["id"] == "sources")
    config["sections"] = [sources] + [item for item in DEFAULT_TEMPLATE["sections"] if item["id"] != "sources"]
    template = save_template(config)
    result = asyncio.run(create_brief(snapshot, False, template["id"]))
    source_block = result["markdown"].split("## 证据与信源", 1)[1].split("## 核心判断", 1)[0]
    assert "https://example.org/" in source_block


def test_brief_outlook_lists_upcoming_meetings_with_traceable_source():
    snapshot = topic_snapshot()
    evidence = normalize({"title": "美伊领导人计划会晤", "summary": "双方计划在多哈会晤。",
                          "link": "https://example.org/meeting", "published": "2026-10-24T08:00:00+00:00",
                          "source_id": "meeting-calendar-monitor", "tier": "T2"}, "new")
    evidence.update(topics=["usiran"], dimensions={"usiran": "diplomacy"})
    snapshot["records"].append(evidence)
    snapshot["meetings"] = [{
        "id": "EV-FUTURE", "evidence_id": evidence["id"], "title": "美伊领导人计划会晤",
        "topics": ["usiran"], "date_start": "2099-11-03", "date_raw": "2099年11月3日",
        "location": "多哈", "status": "arranged", "status_label": "已安排",
        "inactive": False, "non_meeting": False,
    }]
    result = asyncio.run(create_brief(snapshot, False))
    assert "未来会晤与政治日程" in result["markdown"]
    assert "2099年11月3日 · 多哈 · 美伊领导人计划会晤 · 已安排" in result["markdown"]
    assert evidence["id"] in result["markdown"]
    assert result["generation_status"]["source_count"] >= 1
