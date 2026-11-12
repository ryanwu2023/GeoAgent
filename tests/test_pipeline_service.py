from datetime import datetime, timedelta, timezone

import pytest

from backend.pipeline_service import (Actor, PipelineError, can_edit, create_pipeline, delete_pipeline,
                                      get_pipeline, list_pipelines, list_versions, rollback, set_enabled,
                                      update_pipeline)
from backend.store import db

ADMIN = Actor("admin", True)
ALICE = Actor("alice", False)
BOB = Actor("bob", False)
ROW = {"name": "路透社", "description": "d", "topics": ["usiran"], "interval_sec": 3600}


def resolve(host):
    return ["93.184.216.34"]


def cfg(n=1, term="iran"):
    return {"sources": [{"id": f"s{i}", "type": "rss", "name": f"源{i}", "feed_url": f"https://example.com/{i}.xml",
                         "allowed_hosts": ["example.com"], "language": "en", "tier": "T1",
                         "content_policy": "public_fulltext"} for i in range(n)],
            "filters": {"include_any": [term]}}


def make(actor=ADMIN, name="路透社", **kw):
    return create_pipeline(actor, {**ROW, "name": name}, cfg(**kw), resolve=resolve)


def upd(pid, actor=ADMIN, **kw):
    kw.setdefault("row_patch", {})
    kw.setdefault("config", None)
    kw.setdefault("resolve", resolve)
    return update_pipeline(actor, pid, **kw)


def test_create_starts_disabled_with_version_1():
    p = make(ALICE)
    assert p["id"].startswith("p-") and len(p["id"]) == 14
    assert p["enabled"] is False and p["current_version"] == 1 and p["owner"] == "alice"
    assert p["kind"] == "configurable" and p["next_run_at"] is None
    assert p["config"]["sources"][0]["id"] == "s0"
    assert p["created_by"] == "alice" and p["updated_by"] is None
    vs = list_versions(p["id"])
    assert [v["version"] for v in vs] == [1] and vs[0]["source_channel"] == "web"


def test_create_invalid_maps_error_and_duplicate_name():
    with pytest.raises(PipelineError) as e:
        create_pipeline(ADMIN, {**ROW, "topics": ["x"]}, cfg(), resolve=resolve)
    assert e.value.code == "INVALID" and e.value.details["field"] == "row.topics"
    make()
    with pytest.raises(PipelineError) as e:
        make()
    assert e.value.code == "INVALID" and e.value.details["field"] == "row.name"


def test_update_rejects_stale_version():
    p = make()
    upd(p["id"], expected_version=1, config=cfg(term="a"))
    with pytest.raises(PipelineError) as e:
        upd(p["id"], expected_version=1, config=cfg(term="b"))
    assert e.value.code == "VERSION_CONFLICT" and e.value.details["current_version"] == 2


def test_row_only_change_keeps_version():
    p = make()
    u = upd(p["id"], expected_version=1, row_patch={"description": "新"})
    assert u["current_version"] == 1 and u["description"] == "新" and u["updated_by"] == "admin"
    assert u["updated_at"] is not None and u["data_timestamp"] == p["data_timestamp"]
    assert len(list_versions(p["id"])) == 1
    u2 = upd(p["id"], expected_version=1, config=cfg())  # 配置未变化
    assert u2["current_version"] == 1


def test_config_change_adds_version():
    p = make()
    u = upd(p["id"], expected_version=1, config=cfg(term="x"), change_note="改词")
    assert u["current_version"] == 2 and u["config"]["filters"]["include_any"] == ["x"]
    assert list_versions(p["id"])[0]["change_note"] == "改词"


def test_update_rejects_unknown_patch_and_invalid_schedule():
    p = make()
    for patch in ({"owner": "x"}, {"bogus": 1}):
        with pytest.raises(PipelineError) as e:
            upd(p["id"], expected_version=1, row_patch=patch)
        assert e.value.code == "INVALID"
    with pytest.raises(PipelineError) as e:
        upd(p["id"], expected_version=1, row_patch={"interval_sec": 5})
    assert e.value.code == "INVALID" and e.value.details["field"] == "row.interval_sec"


def test_service_rejects_non_admin_even_as_owner():
    p = make(ALICE)
    assert p["owner"] == "alice"
    assert not can_edit(ALICE, p) and can_edit(ADMIN, p) and not can_edit(BOB, p)
    with pytest.raises(PipelineError) as e:
        upd(p["id"], ALICE, expected_version=1, row_patch={"description": "x"})
    assert e.value.code == "FORBIDDEN"
    for fn in (lambda: upd(p["id"], BOB, expected_version=1, row_patch={"description": "x"}),
               lambda: set_enabled(BOB, p["id"], True), lambda: delete_pipeline(BOB, p["id"]),
               lambda: rollback(BOB, p["id"], to_version=1, expected_version=1)):
        with pytest.raises(PipelineError) as e:
            fn()
        assert e.value.code == "FORBIDDEN"
    assert upd(p["id"], ADMIN, expected_version=1, row_patch={"description": "x"})["description"] == "x"


def test_rollback_creates_new_version_with_old_config():
    p = make()
    upd(p["id"], expected_version=1, config=cfg(term="b"))
    r = rollback(ADMIN, p["id"], to_version=1, expected_version=2, resolve=resolve)
    assert r["current_version"] == 3 and r["config"]["filters"]["include_any"] == ["iran"]
    assert [v["version"] for v in list_versions(p["id"])] == [3, 2, 1]
    with pytest.raises(PipelineError) as e:
        rollback(ADMIN, p["id"], to_version=1, expected_version=2)
    assert e.value.code == "VERSION_CONFLICT"
    with pytest.raises(PipelineError) as e:
        rollback(ADMIN, p["id"], to_version=9, expected_version=3)
    assert e.value.code == "NOT_FOUND"


def test_set_enabled_computes_and_clears_next_run():
    p = make()
    on = set_enabled(ADMIN, p["id"], True)
    assert on["enabled"] and on["next_run_at"] > datetime.now(timezone.utc)
    off = set_enabled(ADMIN, p["id"], False)
    assert off["enabled"] is False and off["next_run_at"] is None
    m = make(name="手动")
    upd(m["id"], expected_version=1, row_patch={"interval_sec": None})
    assert set_enabled(ADMIN, m["id"], True)["next_run_at"] is None


def test_schedule_change_recomputes_next_run_when_enabled():
    p = make()
    set_enabled(ADMIN, p["id"], True)
    u = upd(p["id"], expected_version=1, row_patch={"interval_sec": None, "daily_times": ["03:00"]})
    assert u["daily_times"] == ["03:00"] and u["interval_sec"] is None and u["next_run_at"] is not None


def test_delete_hides_from_list_and_disables():
    p = make()
    set_enabled(ADMIN, p["id"], True)
    assert delete_pipeline(ADMIN, p["id"]) is None
    assert list_pipelines() == []
    for fn in (lambda: get_pipeline(p["id"]), lambda: set_enabled(ADMIN, p["id"], True),
               lambda: upd(p["id"], expected_version=1)):
        with pytest.raises(PipelineError) as e:
            fn()
        assert e.value.code == "NOT_FOUND"
    with db() as conn:
        r = conn.execute("SELECT enabled,is_deleted,next_run_at FROM collection_pipelines WHERE id=%s", (p["id"],)).fetchone()
    assert r["enabled"] is False and r["is_deleted"] is True and r["next_run_at"] is None
    with pytest.raises(PipelineError) as e:
        get_pipeline("p-nope")
    assert e.value.code == "NOT_FOUND"


def test_recreate_after_delete_has_fresh_seen():
    old = make()
    with db() as conn:
        conn.execute("INSERT INTO pipeline_seen_items(pipeline_id,record_id,first_run_id) VALUES (%s,'r1','run1')", (old["id"],))
    delete_pipeline(ADMIN, old["id"])
    new = make()
    assert new["id"] != old["id"]
    with db() as conn:
        n = conn.execute("SELECT count(*) AS n FROM pipeline_seen_items WHERE pipeline_id=%s", (new["id"],)).fetchone()["n"]
    assert n == 0


def _builtin(pid="b-news"):
    with db() as conn:
        conn.execute("INSERT INTO collection_pipelines(id,kind,name,topics,current_version,owner,interval_sec) "
                     "VALUES (%s,'builtin','内置新闻',ARRAY['usiran'],1,'system',3600)", (pid,))
    return pid


def test_admin_can_edit_pipeline_created_by_other_admin():
    p = make(Actor("admin2", True))
    assert p["owner"] == "admin2" and can_edit(ADMIN, p)
    assert upd(p["id"], ADMIN, expected_version=1, row_patch={"description": "x"})["description"] == "x"


def test_builtin_only_schedule_editable():
    pid = _builtin()
    assert get_pipeline(pid)["config"] is None
    assert not can_edit(ALICE, get_pipeline(pid)) and can_edit(ADMIN, get_pipeline(pid))
    with pytest.raises(PipelineError) as e:
        upd(pid, ALICE, expected_version=1, row_patch={"interval_sec": 1200})
    assert e.value.code == "FORBIDDEN"
    u = upd(pid, expected_version=1, row_patch={"interval_sec": 1200, "timezone": "UTC"})
    assert u["interval_sec"] == 1200 and u["timezone"] == "UTC"
    for kw in ({"row_patch": {"name": "x"}}, {"row_patch": {"topics": ["ukraine"]}}, {"config": cfg()}):
        with pytest.raises(PipelineError) as e:
            upd(pid, expected_version=1, **kw)
        assert e.value.code == "BUILTIN_READONLY"
    with pytest.raises(PipelineError) as e:
        upd(pid, expected_version=1, row_patch={"interval_sec": 5})
    assert e.value.code == "INVALID"
    assert set_enabled(ADMIN, pid, True)["enabled"] is True
    for fn in (lambda: delete_pipeline(ADMIN, pid), lambda: rollback(ADMIN, pid, to_version=1, expected_version=1)):
        with pytest.raises(PipelineError) as e:
            fn()
        assert e.value.code == "BUILTIN_READONLY"


def test_list_filters_and_source_count():
    a = make(name="Alpha", n=2)
    b = make(name="Beta")
    set_enabled(ADMIN, a["id"], True)
    _builtin()
    names = [p["name"] for p in list_pipelines()]
    assert names == sorted(names) and set(names) == {"Alpha", "Beta", "内置新闻"}
    assert {p["name"]: p["source_count"] for p in list_pipelines()}["Alpha"] == 2
    assert [p["name"] for p in list_pipelines(status="enabled")] == ["Alpha"]
    assert {p["name"] for p in list_pipelines(status="disabled")} == {"Beta", "内置新闻"}
    assert [p["name"] for p in list_pipelines(kind="builtin")] == ["内置新闻"]
    assert [p["name"] for p in list_pipelines(q="ALP")] == ["Alpha"]
    with db() as conn:
        conn.execute("UPDATE collection_pipelines SET topics=ARRAY['ukraine'], last_status='failed' WHERE id=%s", (b["id"],))
    assert [p["name"] for p in list_pipelines(topic="ukraine")] == ["Beta"]
    assert [p["name"] for p in list_pipelines(status="failed")] == ["Beta"]
    with pytest.raises(PipelineError) as e:
        list_pipelines(status="weird")
    assert e.value.code == "INVALID"


def test_changed_since_filters_by_updated_or_finished():
    a = make(name="A")
    b = make(name="B")
    c = make(name="C")
    now = datetime.now(timezone.utc)
    with db() as conn:
        conn.execute("UPDATE collection_pipelines SET created_at=%s", (now - timedelta(days=5),))
        conn.execute("UPDATE collection_pipelines SET updated_at=%s WHERE id=%s", (now - timedelta(hours=1), a["id"]))
        conn.execute("UPDATE collection_pipelines SET last_finished_at=%s WHERE id=%s", (now - timedelta(minutes=5), b["id"]))
    since = now - timedelta(hours=2)
    assert [p["name"] for p in list_pipelines(changed_since=since)] == ["A", "B"]
    assert [p["name"] for p in list_pipelines(changed_since=now)] == []
    assert c["id"]


def test_list_versions_not_found_for_deleted_or_unknown():
    p = make()
    assert len(list_versions(p["id"])) == 1
    delete_pipeline(ADMIN, p["id"])
    for pid in (p["id"], "p-nope"):
        with pytest.raises(PipelineError) as e:
            list_versions(pid)
        assert e.value.code == "NOT_FOUND"


def test_set_enabled_again_keeps_next_run_at():
    p = make()
    first = set_enabled(ADMIN, p["id"], True)
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    with db() as conn:
        conn.execute("UPDATE collection_pipelines SET next_run_at=%s WHERE id=%s", (past, p["id"]))
    again = set_enabled(ADMIN, p["id"], True)
    assert again["next_run_at"] == past and first["next_run_at"] != past


def test_rollback_rejects_current_version_and_revalidates():
    p = make()
    with pytest.raises(PipelineError) as e:
        rollback(ADMIN, p["id"], to_version=1, expected_version=1)
    assert e.value.code == "INVALID"
    upd(p["id"], expected_version=1, config=cfg(term="b"))
    def bad(host):
        return ["10.0.0.1"]
    with pytest.raises(PipelineError) as e:
        rollback(ADMIN, p["id"], to_version=1, expected_version=2, resolve=bad)
    assert e.value.code == "INVALID"
    assert get_pipeline(p["id"])["current_version"] == 2
    assert rollback(ADMIN, p["id"], to_version=1, expected_version=2, resolve=resolve)["current_version"] == 3


def test_list_filter_topic_matches_any():
    a = make(name="Alpha")
    b = make(name="Beta")
    with db() as conn:
        conn.execute("UPDATE collection_pipelines SET topics=ARRAY['usiran','ukraine'] WHERE id=%s", (a["id"],))
        conn.execute("UPDATE collection_pipelines SET topics=ARRAY['ukraine'] WHERE id=%s", (b["id"],))
    assert {p["name"] for p in list_pipelines(topic="ukraine")} == {"Alpha", "Beta"}
    assert {p["name"] for p in list_pipelines(topic="usiran")} == {"Alpha"}
    assert get_pipeline(a["id"])["topics"] == ["usiran", "ukraine"]
