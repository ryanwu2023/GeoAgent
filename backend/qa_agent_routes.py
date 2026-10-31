"""本地问答 Agent 运行策略接口。"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import qa_agent_settings
from .integrations import admin, no_cache


router = APIRouter(
    prefix="/api/qa-agent/settings",
    dependencies=[Depends(admin), Depends(no_cache)],
)


class AgentModelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    provider: str = "inherit"
    base_url: str = Field(default="", max_length=1000)
    key_ref: str = Field(default="backend_default", max_length=100)
    model: str = Field(default="", max_length=200)
    supports_tools: bool = True
    max_context_tokens: Annotated[int, Field(ge=4096, le=2_000_000)] = 128_000
    max_output_tokens: Annotated[int, Field(ge=1, le=131_072)] = 4_000
    temperature: Annotated[float | None, Field(ge=0, le=2)] = None
    timeout_seconds: Annotated[float, Field(ge=5, le=300)] = 60
    retries: Annotated[int, Field(ge=0, le=3)] = 1
    reasoning_mode: str = "auto"
    json_action_compat: bool = False
    data_classifications: list[str] = Field(default_factory=lambda: ["public", "internal"])
    input_price_per_million: Annotated[float | None, Field(ge=0)] = None
    output_price_per_million: Annotated[float | None, Field(ge=0)] = None
    price_version: str = Field(default="default", max_length=80)


class AgentSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    per_agent_timeout_seconds: Annotated[float, Field(ge=5, le=300)] = 70
    total_timeout_seconds: Annotated[float, Field(ge=10, le=900)] = 150
    run_deadline_seconds: Annotated[float, Field(ge=30, le=1800)] = 300
    synthesis_reserve_seconds: Annotated[float, Field(ge=5, le=600)] = 40
    max_parallel: Annotated[int, Field(ge=1, le=3)] = 2
    input_price_per_million: Annotated[float | None, Field(ge=0)] = None
    output_price_per_million: Annotated[float | None, Field(ge=0)] = None
    max_cost_cny: Annotated[float | None, Field(gt=0)] = None
    role_models: dict[str, AgentModelInput] = Field(default_factory=dict)
    auxiliary_models: dict[str, AgentModelInput] = Field(default_factory=dict)


@router.get("")
def read_agent_settings():
    try:
        return qa_agent_settings.effective_settings()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError:
        raise HTTPException(503, "Agent运行配置暂时无法读取") from None


@router.put("")
def write_agent_settings(body: AgentSettingsInput):
    try:
        payload = body.model_dump()
        # 兼容尚未展示角色配置的旧前端：未提交的新字段沿用当前值，
        # 不能因为用户只调整费用或超时就静默重置角色模型。
        current = qa_agent_settings.effective_settings(strict=False)
        for field in (
            "run_deadline_seconds", "synthesis_reserve_seconds",
            "role_models", "auxiliary_models",
        ):
            if field not in body.model_fields_set:
                payload[field] = current[field]
        return qa_agent_settings.save_settings(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError:
        raise HTTPException(503, "Agent运行配置暂时无法保存") from None

