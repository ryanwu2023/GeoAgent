#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""probe_round2.py — 变体路径补测 + 新华社 feed 内容核对"""
import json, os, sys, time, gzip, zlib, re
import urllib.request
from urllib.parse import urlparse
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "_probe_round2.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

def sniff(ct, body):
    if body is None: return "no-body", ""
    head = body[:800].lstrip().lower(); low = body[:4000].lower()
    if "<rss" in head or "<feed" in head or "<rdf" in head: return "rss-ok", ""
    if "<html" in low or head.startswith("<!doctype html"): return "html", ""
    if low.startswith("{") or low.startswith("["): return "json", ""
    return "unknown", body[:120]

def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.5",
        "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, deflate", "Connection": "close"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            enc = (r.headers.get("Content-Encoding") or "").lower()
            if enc == "gzip" or raw[:2] == b"\x1f\x8b":
                try: raw = gzip.decompress(raw)
                except Exception:
                    try: raw = zlib.decompress(raw, 47)
                    except Exception: pass
            elif enc == "deflate":
                try: raw = zlib.decompress(raw)
                except Exception: raw = zlib.decompress(raw, -15)
            body = raw.decode("utf-8", "replace")
            return {"status": r.status, "len": len(body), "body": body}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "len": 0, "body": ""}
    except Exception as e:
        return {"status": 0, "len": 0, "body": "", "err": f"{type(e).__name__}: {e}"[:120]}

CANDIDATES = [
    ("uk-gov-news-atom", "https://www.gov.uk/government/news.atom", "T1", "UK GOV", "英国政府新闻 atom 全量"),
    ("uk-gov-ukraine2", "https://www.gov.uk/government/all.atom?keywords=Ukraine&departments%5B%5D=foreign-commonwealth-office", "T1", "UK GOV", "FCDO 过滤"),
    ("treasury2", "https://home.treasury.gov/news/press-releases", "T1", "US Treasury", "财政部新闻稿列表页"),
    ("state-rssfeed", "https://www.state.gov/rss-feed/", "T1", "US State Dept", "国务院 rss-feed 路径"),
    ("state-briefings", "https://www.state.gov/department-press-briefings/feed/", "T1", "US State Dept", "国务院吹风会 feed"),
    ("nato-rss2", "https://www.nato.int/cps/en/natohq/news_rss.htm", "T1", "NATO", "北约 news_rss"),
    ("nato-rss3", "https://nato.usmission.gov/feed/", "T1", "US Mission NATO", "美驻北约使团 feed"),
    ("imf2", "https://www.imf.org/en/News/RSS?Language=ENG&category=News", "T1", "IMF", "IMF News 路径"),
    ("worldbank2", "https://blogs.worldbank.org/en/external/rss.xml", "T1", "World Bank", "世行博客 feed"),
    ("ebrd2", "https://www.ebrd.com/home/news/rss", "T1", "EBRD", "EBRD 备选"),
    ("kiel3", "https://www.ifw-kiel.de/publications/news/", "T2", "Kiel Institute", "Kiel news 页（http 直连重试）"),
    ("csis2", "https://www.csis.org/analysis/feed", "T2", "CSIS", "CSIS 重试"),
    ("atlantic2", "https://www.atlanticcouncil.org/feed/", "T2", "Atlantic Council", "重试"),
    ("breaking2", "https://breakingdefense.com/feed/", "T3", "Breaking Defense", "重试"),
    ("xinhua-mil", "http://www.news.cn/english/rss/militaryrss.xml", "T3", "Xinhua", "新华社英文军事频道"),
    ("xinhua-biz", "http://www.news.cn/english/rss/bizrss.xml", "T3", "Xinhua", "新华社英文财经频道"),
    ("xinhua-cn-world", "http://www.news.cn/world/rss.xml", "T3", "新华网中文", "新华网中文国际 RSS"),
    ("reuters-gn", "https://news.google.com/rss/search?q=Ukraine+aid+when:7d&hl=en-US&gl=US&ceid=US:en", "T3", "Google News", "gnews 连通性复测"),
    ("gn-weapons2", "https://news.google.com/rss/search?q=Ukraine+weapons+package", "T3", "Google News", "gnews 简查询复测"),
]

proxies = urllib.request.getproxies()
print(f"[proxy] 出口: {proxies}")
results = []
last_host = None
for cid, url, tier, pub, note in CANDIDATES:
    host = urlparse(url).netloc
    if last_host == host: time.sleep(1.2)
    r = fetch(url)
    last_host = host
    v, d = sniff(r.get("ct",""), r.get("body"))
    item = {"id": cid, "url": url, "status": r["status"], "len": r["len"],
            "verdict": v, "detail": d, "note": note}
    if "err" in r: item["err"] = r["err"]
    results.append(item)
    print(f"{'✅' if v=='rss-ok' else ('⚠️ ' if v in ('json','html') else '❌')} {cid:20s} {r['status']:>3} {v:10s} {r['len']:>8}B  {d[:60]}")

# 新华社 world feed 内容核对
print("\n--- xinhua worldrss 内容核对 ---")
xw = next(x for x in results if x["id"] == "xinhua-world")
if xw["status"] == 200:
    body = fetch("http://www.news.cn/english/rss/worldrss.xml")["body"]
    titles = re.findall(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", body, re.S)[1:]
    dates = re.findall(r"<pubDate>(.*?)</pubDate>", body)
    print(f"条数≈{len(titles)}, pubDate 样例: {dates[:3]}")
    for t in titles[:8]:
        print("  ·", t.strip()[:90])

with open(OUT, "w", encoding="utf-8") as f:
    json.dump({"probed_at": datetime.now(timezone.utc).isoformat(), "results": results}, f, ensure_ascii=False, indent=1)
print(f"\n[probe2] 证据已落 {OUT}")
