"""本地问答 Agent 的热更新运行策略；模型密钥只保存引用。"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import model_settings
from .store import data_dir


ROLE_NAMES = (
    "classifier", "planner", "worker_fast", "worker_reasoning",
    "evaluator", "synthesizer", "translation",
)
AUXILIARY_MODEL_NAMES = ("embedding", "reranker")
REASONING_MODES = {"auto", "enabled", "disabled"}
DATA_CLASSIFICATIONS = {"public", "internal", "confidential"}


def settings_path() -> Path:
    return data_dir() / "qa-agent-settings.json"


def _model_defaults(*, enabled: bool = True, reasoning_mode: str = "auto") -> dict[str, Any]:
    return {
        "enabled": enabled,
        "provider": "inherit",
        "base_url": "",
        "key_ref": "backend_default",
        "model": "",
        "supports_tools": True,
        "max_context_tokens": 128_000,
        "max_output_tokens": 4_000,
        "temperature": None,
        "timeout_seconds": 60.0,
        "retries": 1,
        "reasoning_mode": reasoning_mode,
        "json_action_compat": False,
        "data_classifications": ["public", "internal"],
        "input_price_per_million": None,
        "output_price_per_million": None,
        "price_version": "default",
    }


def _default_role_models() -> dict[str, dict[str, Any]]:
    roles = {name: _model_defaults() for name in ROLE_NAMES}
    roles["classifier"].update(max_output_tokens=1_200, temperature=0.0, timeout_seconds=15.0)
    roles["planner"].update(max_output_tokens=3_000, temperature=0.1, timeout_seconds=30.0)
    roles["worker_fast"].update(max_output_tokens=3_000, temperature=0.1)
    roles["worker_reasoning"].update(max_output_tokens=6_000, reasoning_mode="enabled")
    roles["evaluator"].update(max_output_tokens=2_500, temperature=0.0, timeout_seconds=35.0)
    return roles


def _default_auxiliary_models() -> dict[str, dict[str, Any]]:
    embedding = _model_defaults(enabled=False)
    embedding.update(supports_tools=False, max_output_tokens=1, temperature=0.0, retries=0)
    reranker = _model_defaults(enabled=False)
    reranker.update(supports_tools=False, max_output_tokens=2_000, temperature=0.0)
    return {"embedding": embedding, "reranker": reranker}


def default_settings() -> dict[str, Any]:
    return {
        "per_agent_timeout_seconds": 70.0,
        "total_timeout_seconds": 150.0,
        "run_deadline_seconds": 300.0,
        "synthesis_reserve_seconds": 40.0,
        "max_parallel": 2,
        "input_price_per_million": None,
        "output_price_per_million": None,
        "max_cost_cny": None,
        "role_models": _default_role_models(),
        "auxiliary_models": _default_auxiliary_models(),
        "source": "default",
    }


def _optional_number(value: Any, label: str, *, positive: bool = False) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label}必须是数值")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}必须是数值") from None
    if not math.isfinite(number) or number < 0 or (positive and number <= 0):
        requirement = "大于0" if positive else "大于等于0"
        raise ValueError(f"{label}必须{requirement}")
    return number


def _bounded_number(value: Any, label: str, minimum: float, maximum: float) -> float:
    number = _optional_number(value, label)
    if number is None or not minimum <= number <= maximum:
        raise ValueError(f"{label}须为{minimum:g}到{maximum:g}之间的数值")
    return number


def _bounded_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label}须为{minimum}到{maximum}之间的整数")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}须为{minimum}到{maximum}之间的整数") from None
    if not minimum <= number <= maximum:
        raise ValueError(f"{label}须为{minimum}到{maximum}之间的整数")
    return number


def _safe_text(value: Any, label: str, *, maximum: int = 300) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label}必须是文本")
    result = value.strip()
    if len(result) > maximum or any(ord(char) < 32 or ord(char) == 127 for char in result):
        raise ValueError(f"{label}格式无效")
    return result


def _contains_private_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).lower() in {"api_key", "authorization", "password", "secret"}
            or _contains_private_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_private_key(item) for item in value)
    return False


def _validate_model(value: Any, *, label: str, defaults: dict[str, Any]) -> dict[str, Any]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise ValueError(f"{label}配置必须为对象")
    if _contains_private_key(value):
        raise ValueError(f"{label}不能保存API Key，只能保存后端密钥引用")
    merged = {**deepcopy(defaults), **value}
    provider = _safe_text(merged.get("provider", "inherit"), f"{label}提供方", maximum=60)
    allowed_providers = {*model_settings.PROVIDERS - {"disabled", "pichat"}, "inherit"}
    if provider not in allowed_providers:
        raise ValueError(f"{label}提供方无效")
    base_url = _safe_text(merged.get("base_url", ""), f"{label}服务地址", maximum=1000)
    if base_url:
        validation_provider = "deepseek" if provider == "deepseek" else "openai-compatible"
        base_url = model_settings.normalize_base_url(base_url, validation_provider)
    key_ref = _safe_text(merged.get("key_ref", "backend_default"), f"{label}密钥引用", maximum=100)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", key_ref):
        raise ValueError(f"{label}密钥引用格式无效")
    model = _safe_text(merged.get("model", ""), f"{label}模型", maximum=200)
    reasoning_mode = _safe_text(merged.get("reasoning_mode", "auto"), f"{label}推理模式", maximum=20)
    if reasoning_mode not in REASONING_MODES:
        raise ValueError(f"{label}推理模式无效")
    classifications = merged.get("data_classifications", ["public"])
    if not isinstance(classifications, list) or not classifications:
        raise ValueError(f"{label}数据权限至少包含一项")
    classifications = list(dict.fromkeys(
        _safe_text(item, f"{label}数据权限", maximum=30) for item in classifications
    ))
    if not set(classifications) <= DATA_CLASSIFICATIONS:
        raise ValueError(f"{label}数据权限无效")
    temperature = _optional_number(merged.get("temperature"), f"{label}温度")
    if temperature is not None and temperature > 2:
        raise ValueError(f"{label}温度须为0到2之间的数值")
    return {
        "enabled": bool(merged.get("enabled", True)),
        "provider": provider,
        "base_url": base_url,
        "key_ref": key_ref,
        "model": model,
        "supports_tools": bool(merged.get("supports_tools", True)),
        "max_context_tokens": _bounded_int(
            merged.get("max_context_tokens", 128_000), f"{label}上下文长度", 4_096, 2_000_000,
        ),
        "max_output_tokens": _bounded_int(
            merged.get("max_output_tokens", 4_000), f"{label}最大输出Token", 1, 131_072,
        ),
        "temperature": temperature,
        "timeout_seconds": _bounded_number(
            merged.get("timeout_seconds", 60), f"{label}超时秒数", 5, 300,
        ),
        "retries": _bounded_int(merged.get("retries", 1), f"{label}重试次数", 0, 3),
        "reasoning_mode": reasoning_mode,
        "json_action_compat": bool(merged.get("json_action_compat", False)),
        "data_classifications": classifications,
        "input_price_per_million": _optional_number(
            merged.get("input_price_per_million"), f"{label}输入价格",
        ),
        "output_price_per_million": _optional_number(
            merged.get("output_price_per_million"), f"{label}输出价格",
        ),
        "price_version": _safe_text(merged.get("price_version", "default"), f"{label}价格版本", maximum=80),
    }


def _validate_models(
    value: Any, *, names: tuple[str, ...], defaults: dict[str, dict[str, Any]], label: str,
) -> dict[str, dict[str, Any]]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise ValueError(f"{label}配置必须为对象")
    unknown = sorted(set(value) - set(names))
    if unknown:
        prefix = "未知模型角色" if label == "角色模型" else "未知辅助模型"
        raise ValueError(f"{prefix}：{', '.join(unknown)}")
    return {
        name: _validate_model(value.get(name, {}), label=f"{label}{name}", defaults=defaults[name])
        for name in names
    }


def _configuration_payload(value: dict[str, Any]) -> dict[str, Any]:
    ignored = {
        "source", "cost_limit_enabled", "research_cutoff_seconds", "config_version",
        "previous_config_version", "config_diff",
    }
    return {key: deepcopy(item) for key, item in value.items() if key not in ignored}


def _config_version(value: dict[str, Any]) -> str:
    encoded = json.dumps(_configuration_payload(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _diff_paths(left: Any, right: Any, prefix: str = "") -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        result: list[str] = []
        for key in sorted(set(left) | set(right)):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in left or key not in right:
                result.append(path)
            else:
                result.extend(_diff_paths(left[key], right[key], path))
        return result
    return [] if left == right else [prefix or "配置"]


def validate_settings(payload: dict[str, Any], *, source: str = "saved") -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Agent运行配置必须为对象")
    if _contains_private_key(payload):
        raise ValueError("Agent运行配置不能保存API Key，只能保存后端密钥引用")
    defaults = default_settings()
    try:
        per_agent = float(payload.get("per_agent_timeout_seconds", defaults["per_agent_timeout_seconds"]))
        total = float(payload.get("total_timeout_seconds", defaults["total_timeout_seconds"]))
        deadline = float(payload.get("run_deadline_seconds", defaults["run_deadline_seconds"]))
        synthesis = float(payload.get("synthesis_reserve_seconds", defaults["synthesis_reserve_seconds"]))
        parallel = int(payload.get("max_parallel", defaults["max_parallel"]))
    except (TypeError, ValueError):
        raise ValueError("Agent超时、截止时间和并发数必须是数值") from None
    if not math.isfinite(per_agent) or not 5 <= per_agent <= 300:
        raise ValueError("单个子Agent超时须为5到300秒")
    if not math.isfinite(total) or not 10 <= total <= 900:
        raise ValueError("全部子Agent总超时须为10到900秒")
    if total < per_agent:
        raise ValueError("全部子Agent总超时不能小于单个子Agent超时")
    if not math.isfinite(deadline) or not 30 <= deadline <= 1800:
        raise ValueError("整次问答总时限须为30到1800秒")
    if not math.isfinite(synthesis) or synthesis < 5 or synthesis >= deadline:
        raise ValueError("综合预留须至少5秒且小于整次问答总时限")
    if not 1 <= parallel <= 3:
        raise ValueError("最大并行子Agent数须为1到3")
    input_price = _optional_number(payload.get("input_price_per_million"), "输入价格")
    output_price = _optional_number(payload.get("output_price_per_million"), "输出价格")
    max_cost = _optional_number(payload.get("max_cost_cny"), "单次问答费用上限", positive=True)
    if max_cost is not None and (input_price is None or output_price is None):
        raise ValueError("设置费用上限时必须同时填写输入和输出价格")
    roles = _validate_models(
        payload.get("role_models"), names=ROLE_NAMES, defaults=_default_role_models(), label="角色模型",
    )
    auxiliary = _validate_models(
        payload.get("auxiliary_models"), names=AUXILIARY_MODEL_NAMES,
        defaults=_default_auxiliary_models(), label="辅助模型",
    )
    if max_cost is not None:
        for label, entries in (("角色模型", roles), ("辅助模型", auxiliary)):
            for name, entry in entries.items():
                explicit_target = bool(entry["model"] or entry["base_url"] or entry["provider"] != "inherit")
                if entry["enabled"] and explicit_target and (
                    entry["input_price_per_million"] is None or entry["output_price_per_million"] is None
                ):
                    raise ValueError(f"启用费用上限时，{label}{name}的显式模型必须填写输入和输出价格")
    result = {
        "per_agent_timeout_seconds": per_agent,
        "total_timeout_seconds": total,
        "run_deadline_seconds": deadline,
        "synthesis_reserve_seconds": synthesis,
        "research_cutoff_seconds": deadline - synthesis,
        "max_parallel": parallel,
        "input_price_per_million": input_price,
        "output_price_per_million": output_price,
        "max_cost_cny": max_cost,
        "cost_limit_enabled": max_cost is not None,
        "role_models": roles,
        "auxiliary_models": auxiliary,
        "source": source,
    }
    result["config_version"] = _config_version(result)
    result["previous_config_version"] = str(payload.get("previous_config_version") or "")
    raw_diff = payload.get("config_diff")
    result["config_diff"] = [str(item) for item in raw_diff] if isinstance(raw_diff, list) else []
    return result


def effective_settings(*, strict: bool = True) -> dict[str, Any]:
    path = settings_path()
    if not path.exists():
        return validate_settings(default_settings(), source="default")
    if path.is_symlink():
        if strict:
            raise ValueError("Agent运行配置路径不能是符号链接")
        return validate_settings(default_settings(), source="safe-default")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return validate_settings(payload, source="saved")
    except (OSError, ValueError, json.JSONDecodeError):
        if strict:
            raise ValueError("Agent运行配置无法读取或格式无效") from None
        return validate_settings(default_settings(), source="safe-default")


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    previous = effective_settings(strict=False)
    validated = validate_settings(payload, source="saved")
    validated["previous_config_version"] = previous["config_version"]
    validated["config_diff"] = _diff_paths(
        _configuration_payload(previous), _configuration_payload(validated),
    )
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Agent运行配置路径不能是符号链接")
    persisted = {
        key: value for key, value in validated.items()
        if key not in {"source", "cost_limit_enabled", "research_cutoff_seconds"}
    }
    fd, temporary = tempfile.mkstemp(prefix=".qa-agent-settings-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(persisted, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return effective_settings()
