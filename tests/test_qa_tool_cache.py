import asyncio

from backend.qa_tool_cache import SharedToolCache, canonical_tool_key


def test_canonical_tool_key_is_scope_and_argument_stable():
    scope = {
        "snapshot_id": "snap-1",
        "topic": "usiran",
        "dimension": "",
        "start": "2026-10-20",
        "end": "2026-10-25",
    }
    left = canonical_tool_key(scope, "geo_evidence_search", {"limit": 8, "q": "伊朗"})
    right = canonical_tool_key(scope, "geo_evidence_search", {"q": "伊朗", "limit": 8})
    assert left == right
    assert left != canonical_tool_key({**scope, "topic": "ukraine"}, "geo_evidence_search", {"q": "伊朗", "limit": 8})


def test_shared_cache_coalesces_concurrent_calls_and_marks_reuse():
    async def scenario():
        calls = 0

        async def execute():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return {"records": [{"id": "r1"}], "returned": 1}

        cache = SharedToolCache()
        first, second = await asyncio.gather(
            cache.get_or_execute("same", execute),
            cache.get_or_execute("same", execute),
        )
        assert calls == 1
        assert first.value == second.value
        assert sorted([first.cache_reused, second.cache_reused]) == [False, True]

    asyncio.run(scenario())


def test_shared_cache_does_not_persist_failures():
    async def scenario():
        calls = 0

        async def execute():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("temporary")
            return {"ok": True}

        cache = SharedToolCache()
        try:
            await cache.get_or_execute("retryable", execute)
        except RuntimeError:
            pass
        else:
            raise AssertionError("first call must fail")
        result = await cache.get_or_execute("retryable", execute)
        assert result.value == {"ok": True}
        assert result.cache_reused is False
        assert calls == 2

    asyncio.run(scenario())


def test_shared_cache_returns_defensive_copies():
    async def scenario():
        cache = SharedToolCache()

        async def execute():
            return {"records": [{"id": "r1"}]}

        first = await cache.get_or_execute("copy", execute)
        first.value["records"][0]["id"] = "changed"
        second = await cache.get_or_execute("copy", execute)
        assert second.value["records"][0]["id"] == "r1"
        assert second.cache_reused is True

    asyncio.run(scenario())
