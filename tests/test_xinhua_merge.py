import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location("xinhua_monitor_merge", ROOT / "collectors" / "xinhua-monitor" / "xinhua_monitor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_retitled_article_keeps_one_record_per_id():
    monitor = load()
    url = "https://www.news.cn/world/20261111/cb4a/c.html"
    old = {"id": "2082ac89", "origin_id": "xinhua:old", "title": "综合消息｜阿布扎比三方会谈", "link": url}
    mirror = {"id": "mirror01", "origin_id": "xinhua:new", "title": "综合消息丨阿布扎比三方会谈", "link": "https://www.news.cn/m.html"}
    new = {"id": "2082ac89", "origin_id": "xinhua:new", "title": "综合消息丨阿布扎比三方会谈", "link": url}
    merged = monitor.merge_captured({"2082ac89": old, "mirror01": mirror}, [new])
    assert [r["id"] for r in merged] == ["2082ac89"]
    assert merged[0]["title"] == "综合消息丨阿布扎比三方会谈"
    assert set(merged[0]["aliases"]) == {url, "https://www.news.cn/m.html"}
