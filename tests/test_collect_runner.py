from scripts.collect import run_collectors, select_collectors


def _registry():
    return [
        {"id": "first", "topics": ["usiran"], "enabled": True},
        {"id": "second", "topics": ["ru_ua"], "enabled": True},
        {"id": "disabled", "topics": ["ru_ua"], "enabled": False},
    ]


def test_runner_continues_after_failure(monkeypatch):
    codes = iter([1, 0])
    monkeypatch.setattr(
        "scripts.collect.run_one",
        lambda *args, **kwargs: {"returncode": next(codes)},
    )
    results = run_collectors(
        ["first", "second"], topic=None, timeout=10, registry=_registry()
    )
    assert [item["returncode"] for item in results] == [1, 0]


def test_topic_filter_only_selects_matching_collectors():
    selected = select_collectors(_registry(), ids=[], topic="ru_ua")
    assert [item["id"] for item in selected] == ["second"]

