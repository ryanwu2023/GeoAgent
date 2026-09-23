#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""probe_round2.py — 变体源补测 + 复验 round1 误判"""
import sys, json, time
sys.path.insert(0, __file__.rsplit("\\", 1)[0] if "\\" in __file__ else ".")
from probe_sources import fetch, sniff_verdict, GN
from urllib.parse import urlparse

CANDIDATES = [
    ("jpost-recheck", "https://www.jpost.com/rss/rssfeedsfrontpage.aspx", "T3", "Jerusalem Post", "复验（无xml声明的rss）"),
    ("aljazeera-recheck", "https://www.aljazeera.com/xml/rss/all.xml", "T3", "Al Jazeera", "复验"),
    ("bbci-mideast", "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml", "T3", "BBC", "中东频道"),
    ("guardian-mideast", "https://www.theguardian.com/world/middleeast/rss", "T3", "The Guardian", "中东频道"),
    ("nyt-mideast", "https://rss.nytimes.com/services/xml/rss/nyt/MiddleEast.xml", "T3", "NYT", "中东频道"),
    ("wafa-rss", "https://english.wafa.ps/rss.xml", "T1", "WAFA", "巴勒斯坦官方通讯社（对方当事方一手）"),
    ("wafa-rss2", "https://english.wafa.ps/page/rss", "T1", "WAFA", "备用"),
    ("haaretz-latest", "https://www.haaretz.com/sitemaps/rss.xml", "T3", "Haaretz", "变体"),
    ("ynet-3084", "https://www.ynetnews.com/rss/0,14840,L-3084,00.xml", "T3", "Ynetnews", "变体（历史已知路径）"),
    ("i24-feed2", "https://www.i24news.tv/en/feed", "T3", "i24NEWS", "变体"),
    ("aawsat-feed", "https://english.aawsat.com/feed", "T3", "Asharq Al-Awsat", "变体"),
    ("unifil-rss2", "https://unifil.unmissions.org/en/rss.xml", "T1", "UNIFIL", "变体"),
    ("ochaopt2", "https://www.ochaopt.org/feed", "T1", "OCHA oPt", "变体"),
    ("almonitor", "https://www.al-monitor.com/rss", "T3", "Al-Monitor", "探测"),
    ("gn-eisenkot", GN.format('Eisenkot+Yashar+poll'), "T3", "Google News", "艾森科特个人通道"),
    ("gn-hostages", GN.format('Gaza+hostages+ceasefire+deal'), "T3", "Google News", "人质/停火协议通道"),
    ("gn-annexation", GN.format('Israel+annexation+"West+Bank"+settlement'), "T3", "Google News", "吞并/定居点政治动向"),
    ("gn-iran-front", GN.format('Israel+Iran+strike+warn'), "T3", "Google News", "对伊第二战线预警"),
]

def main():
    import urllib.request
    print(f"[proxy] {urllib.request.getproxies()}")
    out = []
    last = {}
    for cid, url, tier, pub, note in CANDIDATES:
        host = urlparse(url).netloc
        if host in last:
            dt = time.time() - last[host]
            if dt < 1.2: time.sleep(1.2 - dt)
        r = fetch(url)
        last[host] = time.time()
        v, d = sniff_verdict(r["ct"], r["body"])
        out.append({"id": cid, "url": url, "status": r["status"], "verdict": v, "len": r["len"], "note": note})
        print(("✅" if v == "rss-ok" else "❌"), f"{cid:18s} {r['status']:>3} {v:12s} {r['len']:>8}B {d[:50]}")
    json.dump(out, open("tools/_probe_round2.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"rss-ok {sum(1 for x in out if x['verdict']=='rss-ok')}/{len(out)}")

if __name__ == "__main__":
    main()
