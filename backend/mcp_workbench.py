"""Project workbench inspection and isolated calls to the same MCP tools."""
import asyncio
import json
import hashlib
import secrets
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .integrations import admin, no_cache
from .geo_mcp.audit import recent_calls
from .geo_mcp.server import create_server, create_app
from .geo_mcp.auth import AuthConfig
from .geo_mcp.contracts import Principal

router = APIRouter(prefix="/api/integrations/mcp-workbench", dependencies=[Depends(admin), Depends(no_cache)])


@router.get("")
async def catalog():
    server = create_server(project_transport=True)
    tools = await server.list_tools()
    return {"instructions": server.instructions, "tools": [tool.model_dump(mode="json", exclude_none=True) for tool in tools],
            "calls": recent_calls(), "statistics_basis": "近7天，从工作台功能上线后开始记录；不含认证失败及进入工具前的协议错误。不保存参数、正文或密钥。",
            "model_view_basis": "本项目 MCP 原始契约，不是 PiChat 适配、裁剪或压缩后的模型最终上下文。"}


class Trial(BaseModel):
    tool: str = Field(min_length=1, max_length=100)
    arguments: dict = Field(default_factory=dict)


@router.post("/call")
async def trial(body: Trial, request: Request, response: Response):
    if len(json.dumps(body.arguments, ensure_ascii=False)) > 20_000:
        raise HTTPException(422, "参数超过20000字符")
    server = create_server(project_transport=True)
    if body.tool not in {tool.name for tool in await server.list_tools()}:
        raise HTTPException(422, "未知业务工具")
    # After the project's same-origin check: isolated in-process transport using
    # the same registered SDK tools. No credential is stored or sent to the UI;
    # the public /mcp endpoint remains Bearer-protected.
    credential = secrets.token_urlsafe(32)
    scope = frozenset({"*"})
    principal = Principal("workbench-admin", "workbench-debug", "project", scope, scope, scope, scope, True)
    debug_app = create_app(auth_config=AuthConfig(token_hash=hashlib.sha256(credential.encode()).hexdigest(), principal=principal), project_transport=True)
    async def invoke():
        async with debug_app.router.lifespan_context(debug_app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=debug_app), base_url="http://localhost") as client:
                return await client.post("/mcp", headers={"Authorization": f"Bearer {credential}", "Accept": "application/json, text/event-stream"},
                                         json={"jsonrpc": "2.0", "id": f"workbench-{uuid.uuid4().hex}", "method": "tools/call", "params": {"name": body.tool, "arguments": body.arguments}})
    try:
        result = await asyncio.wait_for(invoke(), timeout=30)
    except asyncio.TimeoutError:
        raise HTTPException(504, "MCP 试调用超过30秒")
    response.status_code = result.status_code
    return result.json()
