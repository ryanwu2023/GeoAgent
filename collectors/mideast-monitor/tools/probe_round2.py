#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""probe_round2.py — 变体源补测（海湾媒体 403 变体 / 伊拉克补充 / 分析类补充）"""
import json, time
import urllib.request
from urllib.parse import urlparse
from probe_sources import fetch, sniff_verdict, GN

CANDIDATES = [
    ("national-recheck", "https://www.thenationalnews.com/rss", "T3", "The National", "复验"),
    ("national-2", "https://www.thenationalnews.com/news/mena/rss.xml", "T3", "The National", "MENA 频道变体"),
    ("national-3", "https://www.thenationalnews.com/feed", "T3", "The National", "feed 变体"),
    ("arabnews-3", "https://www.arabnews.com/rss/feed.xml", "T3", "Arab News", "变体"),
    ("alarabiya-3", "https://www.alarabiya.net/tools/rss", "T3", "Al Arabiya", "变体"),
    ("alarabiya-4", "https://english.aawsat.com/arab-world", "T3", "Asharq Al-Awsat", "（非feed，略）"),
    ("gulfnews-3", "https://gulfnews.com/rss?generatorName=world", "T3", "Gulf News", "world 变体"),
    ("rudaw-3", "https://www.rudaw.net/english/mideast/rss", "T3", "Rudaw", "mideast 变体"),
    ("shafaq-3", "https://shafaq.com/en/rss/news", "T3", "Shafaq News", "变体"),
    ("shafaq-4", "https://www.shafaq.com/en/rss", "T3", "Shafaq News", "变体"),
    ("alsumaria-en", "https://www.alsumaria.tv/en/RSS", "T3", "Alsumaria", "伊拉克本土英文"),
    ("ina-en", "https://en.ina-news.org/rss", "T1", "Iraqi News Agency", "伊拉克官方通讯社（探测）"),
    ("ina2", "https://ina-news.org/rss", "T1", "Iraqi News Agency", "备用"),
    ("jordantimes", "https://jordantimes.com/feed/", "T3", "Jordan Times", "约旦"),
    ("anadolu-mideast", "https://www.aa.com.tr/en/rss/default?cat=middle-east", "T3", "Anadolu", "土耳其官方通讯社"),
    ("anadolu2", "https://www.aa.com.tr/en/rss", "T3", "Anadolu", "备用"),
    ("ap-mideast", "https://apnews.com/index/middle-east/rss.rss", "T3", "AP", "中东频道（变体）"),
    ("ap2", "https://rss.app/feeds/middleeast.xml", "T3", "AP", "（探测）"),
    ("isw-recheck", "https://www.understandingwar.org/backgrounder/rss.xml", "T2", "ISW", "复验（403）"),
    ("mee-recheck", "https://www.middleeasteye.net/rss", "T3", "MEE", "复验（超时）"),
    ("defpost", "https://www.defensepost.com/feed/", "T3", "The Defense Post", "军务（伊拉克/海湾覆盖）"),
    ("lwj-feed", "https://www.longwarjournal.org/feed", "T2", "Long War Journal", "FDD 旗下（民兵袭击记录）"),
    ("gn-turkey-saudi", GN.format('Turkey+Saudi+(Erdogan+OR+ankara)+defence+Iran'), "T3", "Google News", "土耳其侧动向"),
    ("gn-egypt-gulf", GN.format('Egypt+(Sisi+OR+Cairo)+(Saudi+OR+Houthi+OR+Red+Sea)+defence'), "T3", "Google News", "埃及侧动向"),
    ("gn-jordan-iran", GN.format('Jordan+(Iran+OR+Houthi+OR+militia)+attack+defence'), "T3", "Google News", "约旦侧动向"),
    ("gn-us-troops-gulf", GN.format('"US+troops"+(Kuwait+OR+Bahrain+OR+Jordan+OR+"Prince+Sultan")+(killed+OR+attack+OR+base)'), "T3", "Google News", "驻海湾美军遇袭"),
    ("gn-unsc-iran", GN.format('UN+Security+Council+(Iran+OR+Hormuz+OR+"Arab+states")+resolution'), "T3", "Google News", "安理会动向"),
]

def main():
    print(f"[proxy] {urllib.request.getproxies()}")
    out = []
    last = {}
    for cid, url, tier, pub, note in CANDIDATES:
        host = urlparse(url).netloc
        if host in last:
            dt = time.time() - last[host]
            if dt < 1.2:
                time.sleep(1.2 - dt)
        r = fetch(url)
        last[host] = time.time()
        v, d = sniff_verdict(r["ct"], r["body"])
        ok = v == "rss-ok" and r["status"] == 200
        out.append({"id": cid, "url": url, "tier": tier, "publisher": pub,
                    "status": r["status"], "verdict": v, "len": r["len"],
                    "ok": ok, "note": note})
        print(("✅" if ok else "❌"), f"{cid:18s} {r['status']:>3} {v:9s} {r['len']:>8}B {(d or r.get('err',''))[:50]}")
    json.dump(out, open("tools/_probe_round2.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\nrss-ok {sum(1 for x in out if x['ok'])}/{len(out)}")

if __name__ == "__main__":
    main()
