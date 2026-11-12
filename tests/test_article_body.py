from scripts.article_body import fetch_bodies, make_client, _web_module

ARTICLE = ("<html><head><script type='application/ld+json'>"
           '{"@type": "NewsArticle", "headline": "Envoys meet", "articleBody": "' + "Envoys met in Muscat. " * 20 + '"}'
           "</script></head><body></body></html>")
PAYWALL = "<html><body><h1>Deal</h1><p>Subscribe to continue reading this story.</p></body></html>"


def _client(pages):
    web = _web_module()
    seen = []

    def transport(req, timeout):
        seen.append(req.full_url)
        if req.full_url.endswith("/robots.txt"):
            return web.TransportResponse(404, {}, b"", req.full_url)
        status, body = pages[req.full_url]
        return web.TransportResponse(status, {"Content-Type": "text/html; charset=utf-8"}, body.encode(), req.full_url)
    client = make_client("test-agent", transport=transport)
    client.sleep = lambda s: None
    return client, seen


def test_fetch_bodies_only_opted_in_sources_newest_first_and_marks_paywall():
    pages = {"https://a.example/new": (200, ARTICLE), "https://a.example/old": (200, PAYWALL),
             "https://p.example/x": (200, ARTICLE), "https://a.example/gone": (403, "")}
    client, seen = _client(pages)
    sources = [{"id": "open", "body": True}, {"id": "paid"}]
    records = [
        {"source_id": "open", "url": "https://a.example/old", "published": "2026-11-01"},
        {"source_id": "open", "url": "https://a.example/new", "published": "2026-11-05"},
        {"source_id": "open", "url": "https://a.example/gone", "published": "2026-11-03"},
        {"source_id": "paid", "url": "https://p.example/x", "published": "2026-11-05"},
        {"source_id": "open", "url": "https://a.example/done", "published": "2026-11-05", "content_access": "public_fulltext"},
    ]
    stats = fetch_bodies(records, sources, client, max_new=10, max_chars=50, rate_limit_seconds=0)
    assert stats == {"pending": 3, "fulltext": 1, "other": 2, "stopped": False}
    assert records[1]["content_access"] == "public_fulltext" and len(records[1]["body"]) == 50
    assert records[0]["content_access"] == "restricted_metadata_only" and "body" not in records[0]
    assert records[2]["content_access"] == "http_403"
    assert not any("p.example" in u for u in seen)


def test_network_errors_are_retried_next_run():
    web = _web_module()

    def transport(req, timeout):
        raise OSError("tunnel down")
    client = make_client("test-agent", transport=transport)
    client.sleep = lambda s: None
    records = [{"source_id": "open", "url": "https://a.example/x", "published": "2026-11-05"}]
    stats = fetch_bodies(records, [{"id": "open", "body": True}], client, max_new=1, rate_limit_seconds=0)
    assert stats["other"] == 1 and "content_access" not in records[0]


def test_captcha_word_inside_scripts_does_not_hide_public_article():
    web = _web_module()
    page = ("<html><head><script>window.recaptchaSiteKey='x'; // captcha</script></head><body>"
            + "<p>" + "Envoys met in Muscat on Thursday to discuss a ceasefire. " * 4 + "</p></body></html>")
    assert web.parse_public_article(page, "https://a.example/x", {}).content_access == "public_fulltext"
    challenge = "<html><body><h1>Verify you are human</h1><p>" + "x" * 200 + "</p></body></html>"
    assert web.parse_public_article(challenge, "https://a.example/x", {}).content_access == "restricted_metadata_only"


def test_time_budget_stops_starting_new_fetches():
    pages = {f"https://a.example/{i}": (200, ARTICLE) for i in range(5)}
    client, seen = _client(pages)
    ticks = iter([0, 10, 70, 130, 190, 250])
    records = [{"source_id": "open", "url": f"https://a.example/{i}", "published": f"2026-11-0{5 - i}"} for i in range(5)]
    stats = fetch_bodies(records, [{"id": "open", "body": True}], client, max_new=5,
                         rate_limit_seconds=0, budget_seconds=120, clock=lambda: next(ticks))
    assert stats["fulltext"] == 2 and stats["stopped"] is True
    assert "content_access" not in records[2]  # left for the next run
