import pytest

from backend.qa_cost import QaCostLedger
from backend.qa_role_models import ModelRequirements, RoleModelResolver


def model(name, **kwargs):
    return {
        "provider": "openai-compatible", "base_url": "https://models.example/v1",
        "model": name, "key_ref": "backend_default", "context_tokens": 64000,
        "supports_tools": True, "data_classifications": ["public", "internal"],
        "input_price_per_million": 2, "output_price_per_million": 8,
        "price_version": "2026-10", **kwargs,
    }


def test_role_specific_model_wins_and_only_key_reference_is_exposed():
    resolver = RoleModelResolver(
        role_models={"planner": model("planner-model")},
        reasoning_models=[model("reasoner", reasoning=True)],
        default_models=[model("default")], config_version="cfg-1",
    )
    result = resolver.resolve("planner", ModelRequirements(supports_tools=True), QaCostLedger(max_cost_cny=1))
    assert result.model == "planner-model"
    assert result.key_ref == "backend_default"
    assert not hasattr(result, "api_key")
    assert result.config_version == "cfg-1"


def test_resolver_falls_back_by_capability_and_deduplicates_same_target():
    no_tools = model("same", supports_tools=False)
    resolver = RoleModelResolver(
        role_models={"worker": no_tools},
        reasoning_models=[no_tools, model("reasoner", reasoning=True)],
        default_models=[model("default")],
    )
    result = resolver.resolve(
        "worker", ModelRequirements(supports_tools=True, reasoning_preferred=True),
        QaCostLedger(max_cost_cny=1),
    )
    assert result.model == "reasoner"
    assert result.selected_from == "reasoning"


def test_limited_budget_skips_unpriced_model_and_unlimited_can_use_it():
    unpriced = model("unpriced")
    unpriced["input_price_per_million"] = None
    unpriced["output_price_per_million"] = None
    resolver = RoleModelResolver(role_models={"classifier": unpriced})
    with pytest.raises(LookupError):
        resolver.resolve("classifier", ModelRequirements(), QaCostLedger(max_cost_cny=1))
    assert resolver.resolve(
        "classifier", ModelRequirements(), QaCostLedger(max_cost_cny=None),
    ).model == "unpriced"


def test_data_classification_and_context_permissions_cannot_expand_on_fallback():
    resolver = RoleModelResolver(default_models=[model(
        "public-small", context_tokens=8000, data_classifications=["public"],
    )])
    with pytest.raises(LookupError):
        resolver.resolve(
            "worker", ModelRequirements(min_context_tokens=16000, data_classification="internal"),
            QaCostLedger(max_cost_cny=None),
        )


def test_resolver_builds_roles_from_frozen_settings_without_exposing_secret():
    settings = {
        "config_version": "cfg-2",
        "input_price_per_million": 2,
        "output_price_per_million": 8,
        "role_models": {
            "planner": {
                "enabled": True, "provider": "inherit", "base_url": "",
                "model": "planner-override", "key_ref": "backend_default",
                "supports_tools": True, "max_context_tokens": 64_000,
                "data_classifications": ["public", "internal"],
                "reasoning_mode": "auto", "input_price_per_million": None,
                "output_price_per_million": None, "price_version": "default",
            }
        },
    }
    resolver = RoleModelResolver.from_settings(settings, {
        "provider": "openai-compatible", "base_url": "https://models.example/v1",
        "model": "backend-model", "api_key": "must-not-leak", "supports_tools": True,
        "context_tokens": 128_000, "reasoning_mode": "auto",
    })
    resolved = resolver.resolve("planner", ModelRequirements(supports_tools=True), QaCostLedger(max_cost_cny=1))
    assert resolved.model == "planner-override"
    assert resolved.context_tokens == 64_000
    assert resolved.config_version == "cfg-2"
    assert "must-not-leak" not in repr(resolved)
