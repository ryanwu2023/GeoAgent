import json
import urllib.request

from backend.adapters import normalize
from scripts.google_news import annotate_outlets, outlet_key, prefer_direct, resolve_article_url, resolve_records, split_outlet

GN = "https://news.google.com/rss/articles/CBMiAU_yqLtest?oc=5"


def test_split_outlet_prefers_source_element_and_keeps_headline_dashes():
    assert split_outlet("Iran talks resume - Reuters") == ("Iran talks resume", "Reuters")
    assert split_outlet("Iran talks resume - The Wall Street Journal", "The Wall Street Journal") == (
        "Iran talks resume", "The Wall Street Journal")
    assert split_outlet("US-Iran talks: what we know")[1] == ""
    long_tail = "Talks stall - officials say the next round could be delayed by several more weeks"
    assert split_outlet(long_tail) == (long_tail, "")


def test_annotate_outlets_only_touches_google_news_records():
    records = [{"title": "Talks resume - AP News", "url": GN},
               {"title": "Talks resume - AP News", "url": "https://apnews.com/x"}]
    assert annotate_outlets(records) == 1
    assert records[0]["outlet"] == "AP News" and "outlet" not in records[1]


def _fake_google(resolved="https://www.reuters.com/world/iran-talks"):
    calls = []

    def open_url(req: urllib.request.Request, timeout):
        calls.append(req.full_url)
        if req.data is None:
            return b'<div data-n-a-sg="SIG" data-n-a-ts="1730000000"></div>'
        inner = json.dumps(["garturlres", resolved, 1])
        return (")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", inner, None, None, None, "generic"], ["di", 1], ["af.httprm", 1]])).encode()
    return open_url, calls


def test_resolve_article_url_uses_signature_then_batchexecute():
    open_url, calls = _fake_google()
    assert resolve_article_url(GN, open_url) == "https://www.reuters.com/world/iran-talks"
    assert calls[0] == "https://news.google.com/articles/CBMiAU_yqLtest"
    assert calls[1].endswith("/batchexecute")


def test_resolve_records_respects_budget_and_skips_resolved_or_direct_links():
    open_url, _ = _fake_google()
    records = [{"url": GN, "published": "2026-11-05"}, {"url": GN, "published": "2026-11-04"},
               {"url": GN, "original_url": "https://bbc.com/a"}, {"url": "https://apnews.com/x"}]
    stats = resolve_records(records, open_url, max_new=1, min_interval=0)
    assert stats == {"pending": 2, "resolved": 1, "failed": 0, "stopped": False}
    assert records[0]["original_url"].startswith("https://www.reuters.com/")
    assert "original_url" not in records[1]


def test_failed_resolution_never_raises():
    def broken(req, timeout):
        raise OSError("tunnel down")
    records = [{"url": GN}] * 5
    stats = resolve_records(records, broken, max_new=5, min_interval=0)
    # Two straight failures (e.g. Google's 429 /sorry/ page) stop the run early.
    assert stats["failed"] == 2 and stats["stopped"] is True


def test_normalize_cites_outlet_and_original_url_for_google_news_items():
    raw = {"source_id": "gn-trump", "publisher": "Google News", "tier": "T3", "url": GN,
           "title": "Trump weighs next move on Iran - PBS", "outlet": "PBS",
           "original_url": "https://www.pbs.org/newshour/iran"}
    record = normalize(raw, "diplomacy-monitor")
    assert record["publisher"] == "PBS" and record["source_name"] == "PBS"
    assert record["url"] == "https://www.pbs.org/newshour/iran" and record["via"] == "Google News"
    # Archived lines from before the outlet field still recover it from the title.
    legacy = normalize({k: v for k, v in raw.items() if k not in {"outlet", "original_url"}}, "diplomacy-monitor")
    assert legacy["publisher"] == "PBS" and legacy["url"].startswith("https://news.google.com/")
    direct = normalize({"source_id": "bbc", "publisher": "BBC", "url": "https://bbc.com/a", "title": "A - B"}, "diplomacy-monitor")
    assert direct["publisher"] == "BBC" and "via" not in direct


def test_outlet_key_matches_common_name_variants():
    assert outlet_key("The Guardian") == outlet_key("Guardian")
    assert outlet_key("Bloomberg.com") == outlet_key("Bloomberg")
    assert outlet_key("The Hill") == outlet_key("the hill") != outlet_key("The National")


def test_prefer_direct_drops_google_copies_in_batch_and_archive():
    sources = [{"id": "nyt", "publisher": "NYT", "outlets": ["The New York Times"]}, {"id": "gn"}]
    archive = {
        "d1": {"id": "d1", "source_id": "pbs", "publisher": "PBS", "url": "https://www.pbs.org/a", "title": "Iran talks stall"},
        "g1": {"id": "g1", "source_id": "gn", "url": GN, "title": "Iran talks stall - PBS", "outlet": "PBS"},
        "g2": {"id": "g2", "source_id": "gn", "url": GN, "title": "Iran talks stall - Reuters", "outlet": "Reuters"},
    }
    batch = [
        {"id": "n1", "source_id": "nyt", "publisher": "NYT", "url": "https://www.nytimes.com/b", "title": "Oman hosts envoys"},
        {"id": "g3", "source_id": "gn", "url": GN, "title": "Oman hosts envoys - The New York Times", "outlet": "The New York Times"},
        {"id": "g4", "source_id": "gn", "url": GN, "title": "Qatar steps in - CNBC", "outlet": "CNBC"},
    ]
    kept, dropped = prefer_direct(batch, archive, sources)
    assert [r["id"] for r in kept] == ["n1", "g4"]
    assert set(archive) == {"d1", "g2"}  # same headline from another outlet is kept
    assert dropped == 2
