"""Snapshot-switch performance: cached views and incremental translation cache."""
import pytest

from backend import app as A, chinese
from backend.store import db, digest, now


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(A, "_view_cache", {})
    monkeypatch.setattr(A, "_view_locks", {})
    monkeypatch.setattr(chinese, "_translation_memory", None)


def test_cached_view_reuses_result_until_reviews_or_local_translations_change(monkeypatch):
    marker = [(0, "")]
    monkeypatch.setattr(A, "_review_marker", lambda sid: marker[0])
    calls = []

    def compute():
        calls.append(1)
        return {"n": len(calls)}

    assert A.cached_view("workspace", "s1", ("usiran",), compute) == {"n": 1}
    assert A.cached_view("workspace", "s1", ("usiran",), compute) == {"n": 1}
    assert A.cached_view("workspace", "s1", ("ukraine",), compute) == {"n": 2}
    marker[0] = (1, "2026-11-06T08:00:00")  # a researcher saved a review
    assert A.cached_view("workspace", "s1", ("usiran",), compute) == {"n": 3}
    monkeypatch.setattr(chinese, "local_translation_epoch", chinese.local_translation_epoch + 1)
    assert A.cached_view("workspace", "s1", ("usiran",), compute) == {"n": 4}


def test_cached_view_expires_after_ttl(monkeypatch):
    monkeypatch.setattr(A, "_review_marker", lambda sid: (0, ""))
    clock = [1000.0]
    monkeypatch.setattr(A.time, "monotonic", lambda: clock[0])
    calls = []
    A.cached_view("map-records", "s1", (), lambda: calls.append(1))
    clock[0] += A.VIEW_CACHE_TTL + 1
    A.cached_view("map-records", "s1", (), lambda: calls.append(1))
    assert len(calls) == 2


def _insert(text, chinese_text, method="模型翻译"):
    with db() as c:
        chinese.table(c)
        c.execute("INSERT INTO translations VALUES (%s,%s,%s,%s,%s) ON CONFLICT(hash) DO UPDATE SET "
                  "chinese=excluded.chinese,method=excluded.method,created_at=excluded.created_at",
                  (digest(text), text, chinese_text, method, now()))


def test_translation_cache_applies_background_writes_incrementally(monkeypatch):
    _insert("Iran talks resume", "伊朗会谈恢复")
    first = chinese.translation_cache()
    assert first[digest("Iran talks resume")] == "伊朗会谈恢复"
    full_reads = []
    original = chinese._read_translation_cache
    monkeypatch.setattr(chinese, "_read_translation_cache",
                        lambda cache=None, since=None: full_reads.append(since) or original(cache, since))
    _insert("Oman hosts envoys", "阿曼接待特使")          # worker adds a row
    _insert("Iran talks resume", "x", "本地机器翻译")       # and demotes another
    second = chinese.translation_cache()
    assert full_reads and full_reads[0] is not None      # incremental, not a full reload
    assert second[digest("Oman hosts envoys")] == "阿曼接待特使"
    assert digest("Iran talks resume") not in second
    assert digest("Iran talks resume") in first           # earlier callers keep their dict
