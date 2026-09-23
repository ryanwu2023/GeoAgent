#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""probe_round2.py — 变体探测 + 内容抽样"""
import json, os, sys, time, gzip, zlib, re
import urllib.request
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_sources import fetch, sniff_verdict, UA

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "_probe_round2.json")

GN = "https://news.google.com/rss/search?q={}&hl=en-US&gl=US&ceid=US:en"
CANDIDATES = [
    ("senate-votes-floor", "https://www.senate.gov/general/rss/RSS_floor.xml", "T1", "US Senate", "参院议程 RSS"),
    ("senate-rollcall-list", "https://www.senate.gov/legislative/LIS/roll_call_votes/vote1192", "T1", "US Senate", "119届2次会期唱名表决列表页(HTML表)"),
    ("govinfo-rss-index", "https://www.govinfo.gov/rss", "T1", "govinfo", "RSS 目录页"),
    ("paul-feed", "https://www.paul.senate.gov/feed/", "T1", "Sen. Paul", "WordPress 风格"),
    ("paul-rss-xml", "https://www.paul.senate.gov/rss.xml", "T1", "Sen. Paul", "变体"),
    ("collins-retry", "https://www.collins.senate.gov/rss/press-releases", "T1", "Sen. Collins", "重试"),
    ("collins-rss-xml", "https://www.collins.senate.gov/rss.xml", "T1", "Sen. Collins", "变体"),
    ("murkowski-rss-xml", "https://www.murkowski.senate.gov/rss.xml", "T1", "Sen. Murkowski", "变体"),
    ("massie-pressrss", "https://massie.house.gov/rss/pressreleases.xml", "T1", "Rep. Massie", "变体"),
    ("massie-newsrss", "https://massie.house.gov/news/rss.xml", "T1", "Rep. Massie", "变体"),
    ("antiwar-news", "https://news.antiwar.com/feed/", "T3", "Antiwar.com News", "变体"),
    ("antiwar-original", "https://original.antiwar.com/feed/", "T3", "Antiwar.com Original", "变体"),
    ("gallup-rss2", "https://news.gallup.com/rss", "T1", "Gallup", "变体"),
    ("cd-rss-xml", "https://www.commondreams.org/rss.xml", "T3", "Common Dreams", "变体"),
    ("gn-kaine-wp", GN.format('Kaine+OR+Schumer+war+powers+Iran'), "T3", "Google News", "决议发起人动向"),
    ("gn-poll-quinnipiac", GN.format('Quinnipiac+poll+Iran'), "T3", "Google News", "昆尼皮亚克转述"),
    ("gn-poll-marist", GN.format('Marist+poll+Iran'), "T3", "Google News", "Marist/NPR-PBS 转述"),
    ("gn-trump-approval", GN.format('Trump+approval+rating+poll+war+Iran'), "T3", "Google News", "总统支持率与战争关联"),
    ("gn-_midterm-war", GN.format('midterm+elections+Iran+war+polls'), "T3", "Google News", "中期选举与战争民意"),
]

def main():
    proxies = urllib.request.getproxies()
    print(f"[proxy] 出口配置: {proxies}")
    results = []
    last = {}
    for cid, url, tier, pub, note in CANDIDATES:
        host = urlparse(url).netloc
        if host in last:
            dt = time.time() - last[host]
            if dt < 1.2: time.sleep(1.2 - dt)
        r = fetch(url)
        last[host] = time.time()
        v, detail = sniff_verdict(r["ct"], r["body"])
        sample = ""
        if v == "rss-ok":
            m = re.findall(r"<title>(.*?)</title>", r["body"][:6000], re.S)[:4]
            sample = " | ".join(t.strip()[:70] for t in m)
        elif v == "html" and r["len"]:
            m = re.search(r"<title>(.*?)</title>", r["body"][:8000], re.S)
            sample = (m.group(1).strip()[:90] if m else "")
        results.append({"id": cid, "url": url, "tier": tier, "publisher": pub, "note": note,
                        "status": r["status"], "server": r["server"], "ct": r["ct"][:60],
                        "len": r["len"], "verdict": v, "detail": detail, "sample": sample,
                        "err": r.get("err", "")})
        flag = "✅" if v == "rss-ok" else "❌"
        print(f"{flag} {cid:22s} {r['status']:>3} {v:12s} {r['len']:>8}B  {sample[:80]}")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"probed_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "proxy": proxies,
                   "results": results}, f, ensure_ascii=False, indent=1)
    print(f"\n[probe2] rss-ok {sum(1 for r in results if r['verdict']=='rss-ok')}/{len(results)}")

if __name__ == "__main__":
    main()
