"""Translation readiness is separate from PiChat embed availability."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from backend import chinese, llm
from backend.adapters import normalize
from backend.app import app
from backend.store import get_snapshot, save_snapshot, set_working_snapshot_id


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(chinese, "_worker", None)
    monkeypatch.setattr(chinese, "_worker_snapshot_id", None)
    monkeypatch.setattr(chinese, "_translation_status_memory", None)
    monkeypatch.setattr(llm, "status", lambda: {"configured": False})


def record(title="Iran AI report", text="Original report has not been translated."):
    return normalize(
        {"title": title, "summary": text, "link": "https://example.org/report"},
        "new",
    )


def stored(*records):
    sid, _ = save_snapshot({
        "records": list(records), "observations": [], "sources": [],
        "manifest": [], "errors": [],
    })
    return sid


def test_untranslated_source_is_readable_without_false_progress():
    source = record()
    before = deepcopy(source)
    for present in (chinese.present_record, chinese.present_record_summary, chinese.present_map_record):
        result = present(source, {})
        assert result["title"] == source["title"]
        assert result["translation_state"] == "untranslated"
        assert result["title_translation_state"] == "untranslated"
        assert result["text_translation_state"] == "untranslated"
        assert "准备中" not in result["title"]
    detail = chinese.present_record(source, {})
    assert detail["text"] == source["text"]
    assert detail["original_title"] == source["title"]
    assert detail["original_text"] == source["text"]
    assert "显示原文" in detail["translation_note"]
    assert "text" not in chinese.present_map_record(source, {})
    assert source == before


def test_explicit_label_fallback_is_preserved():
    assert chinese.chinese_text("Iran AI report", {}) == "Iran AI report"
    assert chinese.chinese_text("unknown place", {}, "相关地区") == "相关地区"
    assert chinese.chinese_text("unknown place", {}, "") == ""


def test_partial_and_complete_translation_states():
    source = record()
    chinese.save_translation(source["title"], "伊朗人工智能报告")
    result = chinese.present_record(source)
    assert result["translation_state"] == "partial"
    assert result["title_translation_state"] == "ready"
    assert result["text_translation_state"] == "untranslated"
    assert result["text"] == source["text"]
    assert result["translation_quality"]["issues"] == []
    assert "译文含较长外文片段" not in result["translation_note"]
    assert "机器译文不等于语义已验证" in result["translation_note"]
    chinese.save_translation(source["text"], "这份原始报告尚未翻译。")
    assert chinese.present_record(source)["translation_state"] == "ready"


@pytest.mark.parametrize("title,text", [
    ("Iran published a new report", "The original report has not been translated."),
    ("Иран опубликовал доклад", "Этот текст еще не переведен."),
])
def test_untranslated_original_is_not_reviewed_as_machine_translation(title, text):
    result = chinese.present_record(record(title, text), {})
    assert result["translation_state"] == "untranslated"
    assert result["translation_quality"]["status"] == "未翻译"
    assert result["translation_quality"]["issues"] == []
    assert result["translation_note"] == "尚未翻译，当前显示原文；未执行译文质量检查。"


def test_partial_native_chinese_and_original_has_no_machine_translation_claim():
    result = chinese.present_record(record("伊朗发布报告", "The original report is available."), {})
    assert result["translation_state"] == "partial"
    assert result["translation_quality"]["status"] == "部分内容未翻译"
    assert result["translation_quality"]["issues"] == []
    assert "显示原文" in result["translation_note"]
    assert "机器译文" not in result["translation_note"]


@pytest.mark.parametrize("complete", [False, True])
def test_real_translated_fields_keep_numeric_quality_warnings(complete):
    source = record("Iran reported 20 ships", "The original report has not been translated.")
    source["title_zh"] = "伊朗报告有30艘船只"
    if complete:
        source["summary_zh"] = "这份原始报告尚未翻译。"
    result = chinese.present_record(source, {})
    assert result["translation_state"] == ("ready" if complete else "partial")
    assert result["translation_quality"]["status"] == "待复核"
    assert result["translation_quality"]["issues"] == ["数字表达发生变化，需核对原文"]
    assert "机器译文不等于语义已验证" in result["translation_note"]


def test_native_chinese_original_does_not_claim_machine_translation():
    result = chinese.present_record(record("伊朗发布报告", "这是中文原文。"), {})
    assert result["translation_state"] == "ready"
    assert result["translation_quality"]["status"] == "中文原文"
    assert result["translation_quality"]["issues"] == []
    assert result["translation_note"] == "中文原文，无需翻译。"


def test_translated_fields_keep_original_aware_terminology_corrections():
    source = record("AI navigation systems for Patriot batteries", "")
    chinese.save_translation(source["title"], "大赦国际为爱国者电池提供导航系统")
    for present in (chinese.present_record, chinese.present_record_summary, chinese.present_map_record):
        result = present(source)
        assert result["title"] == "人工智能为爱国者防空导弹连提供导航系统"
        assert result["translation_state"] == "ready"
    assert source["title"] == "AI navigation systems for Patriot batteries"


def test_native_chinese_and_inline_translations_count_as_ready():
    native = record("伊朗发布报告", "")
    inline = record()
    inline.update(title_zh="伊朗人工智能报告", summary_zh="这份原始报告尚未翻译。")
    sid = stored(native, inline)
    status = chinese.translation_status(sid)
    assert (status["ready"], status["pending"], status["total"]) == (2, 0, 2)
    assert status["blocked_reason"] is None
    assert not status["available"]


def test_invalid_inline_text_does_not_hide_the_original():
    source = record("伊朗发布报告", "")
    source["title_zh"] = "Not Chinese"
    assert chinese.present_record(source, {})["title"] == "伊朗发布报告"


def test_missing_capability_does_not_launch_a_worker(tmp_path, monkeypatch):
    sid = stored(record())
    set_working_snapshot_id(sid)
    monkeypatch.setattr(chinese.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("worker must not start"))
    chinese.start_translation()
    assert not (tmp_path / "translation.request").exists()
    status = chinese.translation_status()
    assert status["snapshot_id"] == sid
    assert status["pending"] == 1
    assert status["blocked_reason"] == "translation_not_configured"
    assert status["model_configured"] is False
    assert status["local_model_available"] is False
    assert status["running"] is False
    assert status["worker_running"] is False
    assert status["worker_snapshot_id"] is None


def test_status_defaults_to_working_snapshot_not_latest(monkeypatch):
    old = stored(record())
    latest = stored(record("第二条中文记录", "中文正文"))
    set_working_snapshot_id(old)
    default = chinese.translation_status()
    assert default["snapshot_id"] == old
    assert default["latest_snapshot_id"] == latest
    assert default["pending"] == 1
    assert chinese.translation_status(latest)["pending"] == 0
    # Cache hits must not reuse counts across different snapshot scopes.
    assert chinese.translation_status()["pending"] == 1
    assert get_snapshot(old)["records"][0]["title"] == "Iran AI report"


def test_worker_for_latest_is_not_reported_as_translating_old_snapshot(monkeypatch):
    old = stored(record())
    latest = stored(record("Second Iran report", "Other report"))
    set_working_snapshot_id(old)
    monkeypatch.setattr(llm, "status", lambda: {"configured": True})
    monkeypatch.setattr(chinese, "translation_running", lambda: True)
    monkeypatch.setattr(chinese, "_worker_snapshot_id", latest)
    status = chinese.translation_status()
    assert status["worker_running"] is True
    assert status["worker_snapshot_id"] == latest
    assert status["worker_scope"] == "latest_snapshot_only"
    assert status["running"] is False
    assert status["blocked_reason"] == "worker_scope_latest"
    latest_status = chinese.translation_status(latest)
    assert latest_status["running"] is True
    assert latest_status["blocked_reason"] is None


def test_worker_target_is_unknown_after_a_newer_import(monkeypatch):
    old = stored(record())
    latest = stored(record("New Iran report", "New text"))
    set_working_snapshot_id(latest)
    monkeypatch.setattr(llm, "status", lambda: {"configured": True})
    monkeypatch.setattr(chinese, "translation_running", lambda: True)
    monkeypatch.setattr(chinese, "_worker_snapshot_id", old)
    status = chinese.translation_status()
    assert status["worker_running"] is True
    assert status["worker_snapshot_id"] is None
    assert status["running"] is False
    assert status["blocked_reason"] == "worker_scope_unknown"


def test_capability_change_does_not_require_count_cache_invalidation(monkeypatch):
    sid = stored(record())
    assert chinese.translation_status(sid)["blocked_reason"] == "translation_not_configured"
    monkeypatch.setattr(llm, "status", lambda: {"configured": True})
    status = chinese.translation_status(sid)
    assert status["model_configured"] is True
    assert status["available"] is True
    assert status["blocked_reason"] == "not_running"


def test_status_endpoint_validates_explicit_scope():
    old = stored(record())
    latest = stored(record("中文新闻", "中文正文"))
    set_working_snapshot_id(old)
    client = TestClient(app)
    default = client.get("/api/status").json()
    assert default["translation"]["snapshot_id"] == old
    requested = client.get("/api/status", params={"snapshot_id": latest}).json()
    assert requested["translation"]["snapshot_id"] == latest
    assert requested["translation"]["pending"] == 0
    assert client.get("/api/status", params={"snapshot_id": "missing"}).status_code == 404


def test_no_snapshot_has_no_pending_or_running_claim():
    status = chinese.translation_status()
    assert status["snapshot_id"] is None
    assert status["total"] == status["pending"] == status["ready"] == 0
    assert status["running"] is False
    assert status["blocked_reason"] == "no_snapshot"
