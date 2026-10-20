"""Fill translation gaps with the configured model and persist every result.

The bundled local model handles the bulk English/Persian pass.  This worker is
for records the local language detector/model could not safely finish, such as
Cyrillic, Arabic and mixed-script headlines.  It is resumable because every
accepted translation is committed to the shared SQLite cache immediately.
"""
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import llm
from backend.chinese import is_chinese, save_translation, translation_cache
from backend.store import data_dir, digest, get_snapshot, snapshots
from backend.translation_service import parse_translation_response, translate_items


def acquire_lock():
    data_dir().mkdir(parents=True, exist_ok=True)
    lock = (data_dir() / "translation.lock").open("a+b")
    lock.write(b"0")
    lock.flush()
    lock.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Translation worker already running", flush=True)
        lock.close()
        return None
    return lock


def pending_texts():
    available = snapshots()
    if not available:
        return []
    snapshot = get_snapshot(available[0]["id"])
    cache = translation_cache()
    ordered = sorted(
        snapshot["records"],
        key=lambda record: (bool(record.get("topics")), record.get("published_at") or ""),
        reverse=True,
    )
    texts = list(dict.fromkeys(
        value
        for record in ordered
        for value in (record.get("title") or "", record.get("text") or "")
        if value
    ))
    return [text for text in texts if not is_chinese(text) and digest(text) not in cache]


def make_batches(texts, max_items=3, max_chars=4000):
    batch = []
    size = 0
    for original in texts:
        item = {"id": digest(original), "text": original}
        item_size = len(original)
        if batch and (len(batch) >= max_items or size + item_size > max_chars):
            yield batch
            batch, size = [], 0
        batch.append(item)
        size += item_size
    if batch:
        yield batch


def parse_result(response):
    """Compatibility wrapper; every caller now uses the shared JSON parser."""
    return parse_translation_response(response)


async def translate_batch(batch):
    """Keep the worker interface while sharing the preview/route contract."""
    result = await translate_items(batch)
    return result["items"], result["model"]


async def run():
    lock = acquire_lock()
    if lock is None:
        return
    try:
        texts = pending_texts()
        try:
            batch_items = max(1, int(os.getenv("LLM_TRANSLATION_BATCH_ITEMS", "3") or 3))
        except ValueError:
            batch_items = 3
        batches = list(make_batches(texts, max_items=batch_items))
        try:
            max_batches = max(0, int(os.getenv("LLM_TRANSLATION_MAX_BATCHES", "0") or 0))
        except ValueError:
            max_batches = 0
        if max_batches:
            batches = batches[:max_batches]
        print(f"LLM pending texts: {len(texts)}; batches: {len(batches)}", flush=True)
        accepted = failed = 0
        started = time.time()
        for index, batch in enumerate(batches, 1):
            originals = {item["id"]: item["text"] for item in batch}
            try:
                items, model = await translate_batch(batch)
                for item in items:
                    key = item["id"]
                    save_translation(originals[key], item["text"], "模型翻译（批量），待语义核验")
                    accepted += 1
                failed += len(originals) - len(items)
                print(
                    f"Batch {index}/{len(batches)}; accepted={accepted}; failed={failed}; "
                    f"model={model}; seconds={time.time()-started:.0f}",
                    flush=True,
                )
            except Exception as exc:
                failed += len(batch)
                print(f"Batch {index}/{len(batches)} failed: {llm.error_message(exc)}", flush=True)
            await asyncio.sleep(0.25)
    finally:
        lock.close()


if __name__ == "__main__":
    asyncio.run(run())
