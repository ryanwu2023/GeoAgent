"""可选的问答语义向量协议；未配置时不发出网络请求。"""
from __future__ import annotations

from typing import Protocol, Sequence

import httpx


class EmbeddingProvider(Protocol):
    model_version: str

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class NullEmbeddingProvider:
    model_version = "disabled"

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return []


class HttpEmbeddingProvider:
    def __init__(self, *, base_url: str, api_key: str, model: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_version = model
        self.timeout = timeout

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        values = [str(item) for item in texts if str(item)]
        if not values:
            return []
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/embeddings", headers=headers,
                json={"model": self.model_version, "input": values},
            )
            response.raise_for_status()
            rows = response.json().get("data") or []
        ordered = sorted((item for item in rows if isinstance(item, dict)), key=lambda item: int(item.get("index") or 0))
        vectors = [item.get("embedding") for item in ordered]
        if len(vectors) != len(values) or any(not isinstance(item, list) for item in vectors):
            raise ValueError("向量服务返回数量或结构不正确")
        dimensions = {len(item) for item in vectors}
        if len(dimensions) != 1 or 0 in dimensions:
            raise ValueError("向量服务返回维度不一致")
        return [[float(value) for value in item] for item in vectors]


async def embed_query_or_none(provider: EmbeddingProvider, text: str) -> list[float] | None:
    """Return no semantic lane on provider failure; lexical retrieval remains available."""
    try:
        rows = await provider.embed([text])
    except Exception:
        return None
    return rows[0] if rows else None
