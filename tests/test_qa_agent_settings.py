import json

import pytest

from backend import qa_agent_settings


def test_defaults_leave_cost_unlimited(monkeypatch, tmp_path):
    monkeypatch.setattr(qa_agent_settings, "settings_path", lambda: tmp_path / "missing.json")
    value = qa_agent_settings.effective_settings()
    assert value["per_agent_timeout_seconds"] == 70
    assert value["total_timeout_seconds"] == 150
    assert value["max_cost_cny"] is None
    assert value["cost_limit_enabled"] is False


def test_cost_limit_requires_both_prices():
    with pytest.raises(ValueError, match="同时填写输入和输出价格"):
        qa_agent_settings.validate_settings({"max_cost_cny": 1, "input_price_per_million": 2})


def test_total_timeout_cannot_be_shorter():
    with pytest.raises(ValueError, match="不能小于"):
        qa_agent_settings.validate_settings({"per_agent_timeout_seconds": 80, "total_timeout_seconds": 60})


def test_save_round_trip_is_atomic(monkeypatch, tmp_path):
    target = tmp_path / "qa-agent-settings.json"
    monkeypatch.setattr(qa_agent_settings, "settings_path", lambda: target)
    saved = qa_agent_settings.save_settings({
        "per_agent_timeout_seconds": 90,
        "total_timeout_seconds": 240,
        "max_parallel": 3,
        "input_price_per_million": 2.5,
        "output_price_per_million": 10,
        "max_cost_cny": 0.5,
    })
    assert saved["cost_limit_enabled"] is True
    assert saved["max_cost_cny"] == 0.5
    assert json.loads(target.read_text(encoding="utf-8"))["max_parallel"] == 3


def test_corrupt_file_uses_safe_defaults_only_for_runtime(monkeypatch, tmp_path):
    target = tmp_path / "qa-agent-settings.json"
    target.write_text("{", encoding="utf-8")
    monkeypatch.setattr(qa_agent_settings, "settings_path", lambda: target)
    with pytest.raises(ValueError, match="无法读取"):
        qa_agent_settings.effective_settings()
    assert qa_agent_settings.effective_settings(strict=False)["source"] == "safe-default"

