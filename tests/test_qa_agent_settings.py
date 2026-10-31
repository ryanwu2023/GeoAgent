import json

import pytest

from backend import qa_agent_settings
from backend import qa_agent_routes


def test_defaults_leave_cost_unlimited(monkeypatch, tmp_path):
    monkeypatch.setattr(qa_agent_settings, "settings_path", lambda: tmp_path / "missing.json")
    value = qa_agent_settings.effective_settings()
    assert value["per_agent_timeout_seconds"] == 70
    assert value["total_timeout_seconds"] == 150
    assert value["run_deadline_seconds"] == 300
    assert value["synthesis_reserve_seconds"] == 40
    assert value["max_cost_cny"] is None
    assert value["cost_limit_enabled"] is False
    assert set(value["role_models"]) == {
        "classifier", "planner", "worker_fast", "worker_reasoning",
        "evaluator", "synthesizer", "translation",
    }
    assert set(value["auxiliary_models"]) == {"embedding", "reranker"}
    assert value["config_version"]


def test_cost_limit_requires_both_prices():
    with pytest.raises(ValueError, match="同时填写输入和输出价格"):
        qa_agent_settings.validate_settings({"max_cost_cny": 1, "input_price_per_million": 2})


def test_total_timeout_cannot_be_shorter():
    with pytest.raises(ValueError, match="不能小于"):
        qa_agent_settings.validate_settings({"per_agent_timeout_seconds": 80, "total_timeout_seconds": 60})


def test_run_deadline_and_synthesis_reserve_are_distinct_from_specialist_timeout():
    value = qa_agent_settings.validate_settings({
        "per_agent_timeout_seconds": 80,
        "total_timeout_seconds": 120,
        "run_deadline_seconds": 300,
        "synthesis_reserve_seconds": 40,
    })
    assert value["total_timeout_seconds"] == 120
    assert value["run_deadline_seconds"] == 300
    assert value["research_cutoff_seconds"] == 260
    with pytest.raises(ValueError, match="综合预留"):
        qa_agent_settings.validate_settings({
            "run_deadline_seconds": 60, "synthesis_reserve_seconds": 60,
        })


def test_role_settings_keep_only_key_references_and_validate_capabilities():
    value = qa_agent_settings.validate_settings({
        "role_models": {
            "planner": {
                "provider": "openai-compatible",
                "base_url": "https://models.example/v1",
                "key_ref": "planner_key",
                "model": "planner-large",
                "supports_tools": True,
                "max_context_tokens": 128000,
                "max_output_tokens": 6000,
                "timeout_seconds": 45,
                "temperature": 0.2,
                "reasoning_mode": "enabled",
                "input_price_per_million": 3,
                "output_price_per_million": 12,
                "price_version": "2026-10",
            }
        }
    })
    planner = value["role_models"]["planner"]
    assert planner["key_ref"] == "planner_key"
    assert planner["supports_tools"] is True
    assert "api_key" not in json.dumps(value)
    with pytest.raises(ValueError, match="未知模型角色"):
        qa_agent_settings.validate_settings({"role_models": {"root": {}}})
    with pytest.raises(ValueError, match="API Key"):
        qa_agent_settings.validate_settings({
            "role_models": {"planner": {"api_key": "should-not-be-here"}},
        })


def test_limited_cost_requires_prices_for_enabled_explicit_role_models():
    with pytest.raises(ValueError, match="角色模型.*价格"):
        qa_agent_settings.validate_settings({
            "max_cost_cny": 1,
            "input_price_per_million": 2,
            "output_price_per_million": 8,
            "role_models": {
                "planner": {
                    "model": "separate-planner", "base_url": "https://models.example/v1",
                    "input_price_per_million": None, "output_price_per_million": None,
                }
            },
        })


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
    persisted = json.loads(target.read_text(encoding="utf-8"))
    assert persisted["max_parallel"] == 3
    assert persisted["config_version"] == saved["config_version"]
    assert saved["previous_config_version"]
    assert saved["config_diff"]
    assert not list(tmp_path.glob(".qa-agent-settings-*"))


def test_same_configuration_has_stable_version(monkeypatch, tmp_path):
    target = tmp_path / "qa-agent-settings.json"
    monkeypatch.setattr(qa_agent_settings, "settings_path", lambda: target)
    first = qa_agent_settings.save_settings({"max_parallel": 2})
    second = qa_agent_settings.save_settings({"max_parallel": 2})
    assert first["config_version"] == second["config_version"]
    assert second["config_diff"] == []


def test_corrupt_file_uses_safe_defaults_only_for_runtime(monkeypatch, tmp_path):
    target = tmp_path / "qa-agent-settings.json"
    target.write_text("{", encoding="utf-8")
    monkeypatch.setattr(qa_agent_settings, "settings_path", lambda: target)
    with pytest.raises(ValueError, match="无法读取"):
        qa_agent_settings.effective_settings()
    assert qa_agent_settings.effective_settings(strict=False)["source"] == "safe-default"


def test_legacy_settings_request_does_not_reset_role_configuration(monkeypatch):
    current = qa_agent_settings.validate_settings({
        "role_models": {"planner": {"model": "kept-planner"}},
        "run_deadline_seconds": 420,
    })
    captured = {}
    monkeypatch.setattr(qa_agent_settings, "effective_settings", lambda strict=False: current)
    monkeypatch.setattr(qa_agent_settings, "save_settings", lambda payload: captured.update(payload) or payload)
    qa_agent_routes.write_agent_settings(qa_agent_routes.AgentSettingsInput(max_parallel=3))
    assert captured["role_models"]["planner"]["model"] == "kept-planner"
    assert captured["run_deadline_seconds"] == 420

