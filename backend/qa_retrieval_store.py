"""PostgreSQL 精确余弦向量存储，不依赖 pgvector 扩展。"""
from __future__ import annotations

import json
import math
from typing import Any, Iterable

from .store import canonical, db, now


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return numerator / denominator if denominator else 0.0


class QaRetrievalStore:
    def upsert(
        self, record_id: str, content_hash: str, model_version: str,
        vector: list[float], scope_metadata: dict[str, Any],
    ) -> None:
        values = [float(item) for item in vector]
        if not values:
            raise ValueError("向量不能为空")
        with db() as connection:
            connection.execute(
                """INSERT INTO qa_evidence_embeddings
                   (record_id,content_hash,model_version,dimensions,vector,scope_metadata,updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(record_id,model_version) DO UPDATE SET
                   content_hash=EXCLUDED.content_hash,dimensions=EXCLUDED.dimensions,
                   vector=EXCLUDED.vector,scope_metadata=EXCLUDED.scope_metadata,updated_at=EXCLUDED.updated_at""",
                (record_id, content_hash, model_version, len(values), values, canonical(scope_metadata), now()),
            )

    def indexed_hashes(self, model_version: str, record_ids: Iterable[str]) -> dict[str, str]:
        ids = list(dict.fromkeys(str(item) for item in record_ids if str(item)))
        if not ids:
            return {}
        with db() as connection:
            rows = connection.execute(
                "SELECT record_id,content_hash FROM qa_evidence_embeddings WHERE model_version=%s AND record_id=ANY(%s)",
                (model_version, ids),
            ).fetchall()
        return {str(row["record_id"]): str(row["content_hash"]) for row in rows}

    def search(
        self, vector: list[float], eligible_ids: Iterable[str], limit: int,
        *, model_version: str,
    ) -> list[tuple[str, float]]:
        ids = list(dict.fromkeys(str(item) for item in eligible_ids if str(item)))
        if not ids or not vector or limit <= 0:
            return []
        with db() as connection:
            rows = connection.execute(
                """SELECT record_id,dimensions,vector FROM qa_evidence_embeddings
                   WHERE model_version=%s AND dimensions=%s AND record_id=ANY(%s)""",
                (model_version, len(vector), ids),
            ).fetchall()
        scored = []
        for row in rows:
            stored = row["vector"]
            if isinstance(stored, str):
                stored = json.loads(stored)
            score = _cosine([float(item) for item in vector], [float(item) for item in stored])
            scored.append((str(row["record_id"]), score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:max(0, int(limit))]
