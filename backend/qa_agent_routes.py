"""本地问答 Agent 运行策略接口。"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import qa_agent_settings
from .integrations import admin, no_cache


router = APIRouter(
    prefix="/api/qa-agent/settings",
    dependencies=[Depends(admin), Depends(no_cache)],
)


class AgentSettingsInput(BaseModel):
    per_agent_timeout_seconds: Annotated[float, Field(ge=5, le=300)] = 70
    total_timeout_seconds: Annotated[float, Field(ge=10, le=900)] = 150
    max_parallel: Annotated[int, Field(ge=1, le=3)] = 2
    input_price_per_million: Annotated[float | None, Field(ge=0)] = None
    output_price_per_million: Annotated[float | None, Field(ge=0)] = None
    max_cost_cny: Annotated[float | None, Field(gt=0)] = None


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
        return qa_agent_settings.save_settings(body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError:
        raise HTTPException(503, "Agent运行配置暂时无法保存") from None

