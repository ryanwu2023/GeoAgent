"""增量建立 GeoAgent 问答证据向量索引。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.qa_embeddings import HttpEmbeddingProvider
from backend.qa_retrieval_store import QaRetrievalStore
from backend.store import get_snapshot, get_working_snapshot_id


def _content(record: dict) -> str:
    return "\n".join(str(record.get(key) or "").strip() for key in (
        "title_zh", "title", "text_zh", "translated_text", "text", "summary_zh",
    ) if str(record.get(key) or "").strip())


async def run(args) -> dict:
    snapshot_id = args.snapshot_id or get_working_snapshot_id()
    if not snapshot_id:
        raise SystemExit("没有可用工作快照")
    snapshot = get_snapshot(snapshot_id)
    if not snapshot:
        raise SystemExit("快照不存在")
    provider = HttpEmbeddingProvider(
        base_url=args.base_url or os.getenv("QA_EMBEDDING_BASE_URL", ""),
        api_key=os.getenv("QA_EMBEDDING_API_KEY", ""),
        model=args.model or os.getenv("QA_EMBEDDING_MODEL", ""),
    )
    if not provider.base_url or not provider.model_version:
        raise SystemExit("请配置 QA_EMBEDDING_BASE_URL 与 QA_EMBEDDING_MODEL")
    store = QaRetrievalStore()
    rows = []
    for record in snapshot.get("records", []):
        text = _content(record)
        if not text or not record.get("id"):
            continue
        rows.append((str(record["id"]), hashlib.sha256(text.encode("utf-8")).hexdigest(), text, record))
    indexed = store.indexed_hashes(provider.model_version, [item[0] for item in rows])
    pending = [item for item in rows if indexed.get(item[0]) != item[1]]
    if args.dry_run:
        return {"snapshot_id": snapshot_id, "eligible": len(rows), "pending": len(pending), "model": provider.model_version}
    completed = 0
    for offset in range(0, len(pending), args.batch_size):
        batch = pending[offset:offset + args.batch_size]
        vectors = await provider.embed([item[2] for item in batch])
        for (record_id, content_hash, _text, record), vector in zip(batch, vectors):
            store.upsert(record_id, content_hash, provider.model_version, vector, {
                "snapshot_id": snapshot_id,
                "topics": record.get("topics") or [],
                "source_id": record.get("source_id"),
                "tier": record.get("tier"),
            })
            completed += 1
        print(json.dumps({"indexed": completed, "total": len(pending)}, ensure_ascii=False))
    return {"snapshot_id": snapshot_id, "eligible": len(rows), "indexed": completed, "model": provider.model_version}


def main() -> None:
    parser = argparse.ArgumentParser(description="增量建立问答证据向量索引")
    parser.add_argument("--snapshot-id", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.batch_size = max(1, min(128, args.batch_size))
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
