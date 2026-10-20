"""Incremental post-import translation pipeline.

Every successful collector import launches this process once.  It translates
locally supported text first and then uses the configured Agent/LLM only for
the remaining gaps.  Individual translations are committed by the child
workers, so interruption is resumable and completed work is never repeated.
"""
from __future__ import annotations

import subprocess
import sys
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import llm
from backend.chinese import is_chinese, translation_cache
from backend.store import data_dir, digest, get_snapshot, snapshots


def run_worker(script_name: str, *, env: dict[str, str] | None = None) -> int:
    result = subprocess.run(
        [sys.executable, "-u", str(ROOT / "scripts" / script_name)],
        cwd=ROOT,
        env=env,
        check=False,
    )
    return result.returncode


def pending_text_count() -> int:
    available = snapshots()
    if not available:
        return 0
    cache = translation_cache()
    records = get_snapshot(available[0]["id"])["records"]
    texts = {
        value
        for record in records
        for value in (record.get("title") or "", record.get("text") or "")
        if value and not is_chinese(value) and digest(value) not in cache
    }
    return len(texts)


def run_pass(*, retry: bool = False) -> int:
    local_model = data_dir() / "models" / "opus-mt-en-zh" / "pytorch_model.bin"
    if local_model.is_file():
        code = run_worker("translate_snapshot.py")
        print(f"Local translation stage finished: returncode={code}", flush=True)
    else:
        print("Local translation stage skipped: bundled model is unavailable", flush=True)

    if llm.status()["configured"]:
        worker_env = os.environ.copy()
        if retry:
            # Smaller retry batches reduce ID omissions and partial responses
            # after the fast first pass has handled most of the snapshot.
            worker_env["LLM_TRANSLATION_BATCH_ITEMS"] = "1"
        code = run_worker("translate_snapshot_llm.py", env=worker_env)
        print(f"Model translation stage finished: returncode={code}", flush=True)
        return code

    print("Model translation stage skipped: Agent/LLM is not configured", flush=True)
    return 0


def main() -> int:
    request = data_dir() / "translation.request"
    result = 0
    pass_number = 0
    idle_passes = 0
    max_passes = max(1, int(os.getenv("TRANSLATION_MAX_PASSES", "3") or 3))
    while True:
        request.unlink(missing_ok=True)
        before = pending_text_count()
        if before == 0:
            return result
        pass_number += 1
        result = run_pass(retry=pass_number > 1)
        after = pending_text_count()
        made_progress = after < before
        idle_passes = 0 if made_progress else idle_passes + 1
        print(
            f"Translation pass {pass_number}: pending {before} -> {after}; "
            f"progress={made_progress}",
            flush=True,
        )
        if request.exists():
            # A newer import deserves a fresh fast pass and its own retry budget.
            pass_number = idle_passes = 0
            print("A newer import requested another translation pass", flush=True)
            continue
        if after == 0 or pass_number >= max_passes or idle_passes >= 2:
            return result
        delay = min(30, 5 * pass_number)
        print(f"Automatically retrying untranslated items in {delay}s", flush=True)
        time.sleep(delay)


if __name__ == "__main__":
    raise SystemExit(main())
