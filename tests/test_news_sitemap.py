from scripts.news_sitemap import (coverage_gap, is_news_sitemap, parse_news_sitemap,
                                  pub_datetime, sitemap_children)

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url><loc>https://www.bbc.com/news/articles/a1</loc>
    <news:news><news:publication><news:name>BBC News</news:name><news:language>en</news:language></news:publication>
      <news:publication_date>2026-11-05T10:00:00Z</news:publication_date>
      <news:title>Oman hosts US &amp; Iran envoys</news:title><news:keywords>Iran, Oman</news:keywords></news:news></url>
  <url><loc>https://www.bbc.com/persian/articles/p1</loc>
    <news:news><news:publication><news:name>BBC Persian</news:name><news:language>fa</news:language></news:publication>
      <news:publication_date>2026-11-05T09:00:00Z</news:publication_date><news:title>x</news:title></news:news></url>
  <url><loc>https://www.bbc.com/news/no-news-tag</loc></url>
</urlset>"""


def test_parse_news_sitemap_filters_language_and_keeps_keywords():
    assert is_news_sitemap(SITEMAP)
    items = parse_news_sitemap(SITEMAP, language="en")
    assert items == [{"title": "Oman hosts US & Iran envoys", "link": "https://www.bbc.com/news/articles/a1",
                      "pub": "2026-11-05T10:00:00Z", "desc": "", "source_name": "BBC News",
                      "keywords": "Iran, Oman"}]
    assert len(parse_news_sitemap(SITEMAP)) == 2
    assert parse_news_sitemap("<html>not xml") == []


def test_sitemap_children_reads_index_only():
    index = ("<sitemapindex><sitemap><loc>https://a.example/news-1.xml</loc></sitemap>"
             "<sitemap><loc>https://a.example/news-2.xml</loc></sitemap></sitemapindex>")
    assert sitemap_children(index, limit=1) == ["https://a.example/news-1.xml"]
    assert sitemap_children(SITEMAP) == []


def test_pub_datetime_handles_rss_and_iso_variants():
    assert pub_datetime("Thu, 05 Nov 2026 19:17:51 -0500").isoformat() == "2026-11-06T00:17:51+00:00"
    assert pub_datetime("2026-11-05T18:46:28-0500").isoformat() == "2026-11-05T23:46:28+00:00"
    assert pub_datetime("2026-11-05T10:00:00Z").hour == 10
    assert pub_datetime("yesterday") is None


def test_coverage_gap_flags_feeds_that_no_longer_reach_last_fetch():
    items = [{"pub": "2026-11-05T10:00:00Z"}, {"pub": "2026-11-05T12:00:00Z"}]
    assert coverage_gap(items, "2026-11-05T09:00:00+00:00") == "2026-11-05T10:00+00:00"
    assert coverage_gap(items, "2026-11-05T11:00:00+00:00") == ""
    assert coverage_gap(items, "") == ""  # first run: nothing to compare
    assert coverage_gap([{"pub": "n/a"}], "2026-11-05T09:00:00+00:00") == ""
