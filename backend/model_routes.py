"""Model settings and a bounded preview through the production translator."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, SecretStr

from . import llm, model_settings
from .integrations import admin, no_cache
from .translation_service import translate_items


class PrivateSettingsRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # FastAPI's default validation payload can echo a submitted key.
                return JSONResponse(
                    status_code=422,
                    content={"detail": "模型配置格式无效，请检查提供方、地址、模型与超时秒数"},
                    headers={"Cache-Control": "no-store"},
                )
        return safe_handler


router = APIRouter(
    prefix="/api/integrations/model",
    route_class=PrivateSettingsRoute,
    dependencies=[Depends(admin), Depends(no_cache)],
)


class ModelSettingsInput(BaseModel):
    provider: Literal["disabled", "deepseek", "openai-compatible", "pichat"]
    base_url: str = Field(default="", max_length=1000)
    model: str = Field(default="", max_length=200)
    timeout_seconds: float = Field(default=90, ge=5, le=300)
    api_key: SecretStr = SecretStr("")

    def private_payload(self):
        key = self.api_key.get_secret_value()
        return {**self.model_dump(exclude={"api_key"}), "api_key": key}


@router.get("")
def read_model_settings():
    try:
        return model_settings.public_settings()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError:
        raise HTTPException(503, "后台模型配置暂时无法读取，请检查数据目录权限") from None


@router.put("")
def write_model_settings(body: ModelSettingsInput):
    try:
        return model_settings.save_settings(body.private_payload())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError:
        raise HTTPException(503, "后台模型配置暂时无法保存，请检查数据目录权限") from None


TEST_TEXT = "The report lists 12 ships on 20 October."
TEST_ID = "model-config-check"


async def test_translation(settings_override=None, *, source="saved"):
    """Exactly one small translation, with no cache or snapshot mutations."""
    try:
        result = await translate_items(
            [{"id": TEST_ID, "text": TEST_TEXT}], settings_override=settings_override,
        )
        passed = not result["failed"] and len(result["items"]) == 1
        translation = result["items"][0]["text"] if passed else ""
        issues = [
            issue for item in result["failed"]
            for issue in (item["issues"] or [item["reason"]])
        ]
        return {
            "ok": passed, "model": result["model"], "source": source,
            "message": (
                "模型调用及真实翻译链路基础校验通过；尚未启动批量翻译，译文仍需语义复核"
                if passed else "模型已响应，但译文未通过基础校验"
            ),
            "translation": {"ok": passed, "original": TEST_TEXT, "text": translation, "issues": issues},
        }
    except Exception as exc:
        return {
            "ok": False, "source": source, "message": llm.error_message(exc),
            "translation": {"ok": False, "original": TEST_TEXT, "text": "", "issues": []},
        }


@router.post("/test")
async def test_model_settings(body: ModelSettingsInput):
    try:
        draft = model_settings.prepare_settings(body.private_payload())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError:
        raise HTTPException(503, "后台模型配置暂时无法读取，请检查数据目录权限") from None
    if draft["provider"] == "disabled" or not draft["api_key"]:
        raise HTTPException(422, "请先选择模型提供方并填写该服务的 API Key")
    return await test_translation(draft, source="draft")
