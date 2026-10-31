"""本地问答 Agent 的热更新运行策略；不保存任何模型凭据。"""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from .store import data_dir


def settings_path() -> Path:
    return data_dir() / "qa-agent-settings.json"


def default_settings() -> dict[str, Any]:
    return {
        "per_agent_timeout_seconds": 70.0,
        "total_timeout_seconds": 150.0,
        "max_parallel": 2,
        "input_price_per_million": None,
        "output_price_per_million": None,
        "max_cost_cny": None,
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


def validate_settings(payload: dict[str, Any], *, source: str = "saved") -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Agent运行配置必须为对象")
    try:
        per_agent = float(payload.get("per_agent_timeout_seconds", 70))
        total = float(payload.get("total_timeout_seconds", 150))
        parallel = int(payload.get("max_parallel", 2))
    except (TypeError, ValueError):
        raise ValueError("Agent超时和并发数必须是数值") from None
    if not math.isfinite(per_agent) or not 5 <= per_agent <= 300:
        raise ValueError("单个子Agent超时须为5到300秒")
    if not math.isfinite(total) or not 10 <= total <= 900:
        raise ValueError("全部子Agent总超时须为10到900秒")
    if total < per_agent:
        raise ValueError("全部子Agent总超时不能小于单个子Agent超时")
    if not 1 <= parallel <= 3:
        raise ValueError("最大并行子Agent数须为1到3")
    input_price = _optional_number(payload.get("input_price_per_million"), "输入价格")
    output_price = _optional_number(payload.get("output_price_per_million"), "输出价格")
    max_cost = _optional_number(payload.get("max_cost_cny"), "单次问答费用上限", positive=True)
    if max_cost is not None and (input_price is None or output_price is None):
        raise ValueError("设置费用上限时必须同时填写输入和输出价格")
    return {
        "per_agent_timeout_seconds": per_agent,
        "total_timeout_seconds": total,
        "max_parallel": parallel,
        "input_price_per_million": input_price,
        "output_price_per_million": output_price,
        "max_cost_cny": max_cost,
        "cost_limit_enabled": max_cost is not None,
        "source": source,
    }


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
    validated = validate_settings(payload, source="saved")
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Agent运行配置路径不能是符号链接")
    persisted = {key: value for key, value in validated.items() if key not in {"source", "cost_limit_enabled"}}
    fd, temporary = tempfile.mkstemp(prefix=".qa-agent-settings-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(persisted, stream, ensure_ascii=False, indent=2)
        Path(temporary).replace(path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
    return effective_settings()

