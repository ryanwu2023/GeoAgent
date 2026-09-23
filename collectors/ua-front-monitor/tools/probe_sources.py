#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""probe_sources.py — diplomacy-monitor 数据源探测
逐 URL 实测可达性，按内容判定 RSS 可用性（不信状态码）。
用法: python tools/probe_sources.py [--only substr] [--min-interval 1.2]
"""
import json, os, sys, time, gzip, zlib
import urllib.request
from urllib.parse import urlparse
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "_probe_round1.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

def sniff_verdict(ct, body):
    if body is None:
        return "no-body", ""
    head = body[:800].lstrip().lower()
    low = body[:4000].lower()
    if "<rss" in head or "<feed" in head or "<rdf" in head:
        return "rss-ok", ""
    if "<html" in low or head.startswith("<!doctype html"):
        if "just a moment" in low or "challenge" in low or "cf-browser-verification" in low:
            return "js-challenge", "CDN 挑战页，假 200"
        if "akamaighost" in low or "access denied" in low or "error reference" in low:
            return "cdn-block", "CDN 拦截页"
        return "html", "普通 HTML（非 feed）"
    if low.startswith("{") or low.startswith("["):
        return "json", ""
    return "unknown", body[:120]

def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/xml, text/xml, application/json;q=0.8, text/html;q=0.5",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
    })
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
            return {"status": r.status, "ms": int((time.time()-t0)*1000),
                    "server": r.headers.get("Server") or "",
                    "ct": r.headers.get("Content-Type") or "",
                    "len": len(body), "body": body}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "ms": int((time.time()-t0)*1000),
                "server": e.headers.get("Server") if e.headers else "",
                "ct": e.headers.get("Content-Type") if e.headers else "",
                "len": 0, "body": ""}
    except Exception as e:
        return {"status": 0, "ms": int((time.time()-t0)*1000), "server": "",
                "ct": "", "len": 0, "body": "", "err": f"{type(e).__name__}: {e}"[:160]}

GN = "https://news.google.com/rss/search?q={}&hl=en-US&gl=US&ceid=US:en"

# ---- 候选源清单（tier: 谁加工的这份材料就是什么等级）----
CANDIDATES = [
    # --- T1/T2 直接源 ---
    ("un-press", "https://press.un.org/en/rss.xml", "T1", "United Nations", "UN 新闻稿（秘书处/人道，verified 系列）"),
    ("ukrinform-ato", "https://www.ukrinform.net/rubric-ato/feed", "T2", "Ukrinform", "乌国家通讯社 ATO 栏（总参战报转写，标注口径）"),
    ("isw-landing", "https://isw.pub/UkrWar091826", "T2", "ISW", "ISW 每日评估 landing（understandingwar.org 403，试短链）"),
    ("crisisgroup", "https://www.crisisgroup.org/rss.xml", "T2", "ICG", "国际危机组织"),
    # --- T3 直接媒体 ---
    ("kyiv-independent", "https://kyivindependent.com/feed/", "T3", "Kyiv Independent", "乌英文大报"),
    ("kyiv-post", "https://www.kyivpost.com/rss/feed", "T3", "Kyiv Post", "乌英文老牌"),
    ("euromaidan", "https://euromaidanpress.com/feed/", "T3", "Euromaidan Press", "乌独立英文媒体（OSINT 向）"),
    ("mil-in-ua", "https://mil.in.ua/en/feed/", "T3", "Mil.in.ua", "乌军事媒体"),
    ("armyinform", "https://armyinform.com.ua/feed", "T3", "ArmyInform", "乌国防部官媒（官方立场窗口）"),
    ("pravda-ua", "https://www.pravda.com.ua/eng/rss/", "T3", "Ukrainska Pravda EN", "乌主流"),
    ("defence-blog", "https://defenceblog.com/feed/", "T3", "Defence Blog", "军事媒体"),
    ("tass", "https://tass.com/rss/v2.xml", "T3", "TASS", "俄国家通讯社（俄方官方立场窗口）"),
    ("moscowtimes", "https://www.themoscowtimes.com/rss/news", "T3", "Moscow Times", "独立俄媒"),
    ("meduza-en", "https://meduza.io/en/rss", "T3", "Meduza EN", "独立俄媒英文"),
    ("novaya-eu", "https://novayagazeta.eu/rss/all.xml", "T3", "Novaya Gazeta EU", "流亡独立俄媒"),
    ("aljazeera", "https://www.aljazeera.com/xml/rss/all.xml", "T3", "Al Jazeera", "国际转述层"),
    ("reuters-world", "https://www.reuters.com/world/rss", "T3", "Reuters", "通讯社（常 401，实测）"),
    # --- gnews 关键词通道（T3 转述层）---
    ("gn-pokrovsk", "https://news.google.com/rss/search?q=(Pokrovsk+OR+Dobropillia+OR+Myrnohrad)+front+Ukraine&hl=en-US&gl=US&ceid=US:en", "T3", "Google News", "波克罗夫斯克方向"),
    ("gn-donbas", "https://news.google.com/rss/search?q=(Donetsk+OR+Donbas+OR+Kostiantynivka+OR+Kramatorsk+OR+%22fortress+belt%22)+frontline&hl=en-US&gl=US&ceid=US:en", "T3", "Google News", "顿巴斯-堡垒带"),
    ("gn-kharkiv", "https://news.google.com/rss/search?q=(Kharkiv+OR+Kupiansk+OR+Kupyansk+OR+Vovchansk+OR+Borova)+front&hl=en-US&gl=US&ceid=US:en", "T3", "Google News", "哈尔科夫方向"),
    ("gn-lyman", "https://news.google.com/rss/search?q=(Lyman+OR+%22Operation+Vivaldi%22+OR+Siversk+OR+%22Chasiv+Yar%22)+Ukraine+front&hl=en-US&gl=US&ceid=US:en", "T3", "Google News", "莱曼/恰西夫亚尔"),
    ("gn-drone", "https://news.google.com/rss/search?q=Russia+Ukraine+(drone+attack+OR+Shahed+OR+Geran+OR+kamikaze+drones)+overnight&hl=en-US&gl=US&ceid=US:en", "T3", "Google News", "无人机战"),
    ("gn-missile", "https://news.google.com/rss/search?q=Russia+Ukraine+(missile+strike+OR+ballistic+OR+Iskander+OR+KN-23+OR+%22glide+bombs%22)&hl=en-US&gl=US&ceid=US:en", "T3", "Google News", "导弹/滑翔弹打击"),
    ("gn-ua-strike", "https://news.google.com/rss/search?q=Ukraine+strike+Russia+(refinery+OR+depot+OR+ATACMS+OR+%22Storm+Shadow%22+OR+Flamingo+OR+Neptune)&hl=en-US&gl=US&ceid=US:en", "T3", "Google News", "乌纵深打击俄境"),
    ("gn-blacksea", "https://news.google.com/rss/search?q=(%22Black+Sea%22+OR+%22Sea+of+Azov%22)+(naval+drone+OR+fleet+OR+%22Odesa%22+OR+port+OR+shipping)+Ukraine&hl=en-US&gl=US&ceid=US:en", "T3", "Google News", "黑海/亚速海战线"),
    ("gn-airdef", "https://news.google.com/rss/search?q=Ukraine+(air+defense+OR+air+defences)+(intercept+OR+Patriot+OR+NASAMS)+drones&hl=en-US&gl=US&ceid=US:en", "T3", "Google News", "防空能力"),
    ("gn-front-gen", "https://news.google.com/rss/search?q=Ukraine+General+Staff+(clashes+OR+combat+engagements)+front+day&hl=en-US&gl=US&ceid=US:en", "T3", "Google News", "每日战报通道"),
    ("gn-kursk", "https://news.google.com/rss/search?q=(Kursk+OR+Sumy+OR+Belgorod)+(border+OR+incursion+OR+shelling+OR+drone)&hl=en-US&gl=US&ceid=US:en", "T3", "Google News", "苏梅/库尔斯克边境带"),
    ("gn-zaporizhzhia", "https://news.google.com/rss/search?q=(Zaporizhzhia+OR+Kherson)+(front+OR+assault+OR+shelling+OR+crossing)&hl=en-US&gl=US&ceid=US:en", "T3", "Google News", "南部方向"),
    ("gn-energy", "https://news.google.com/rss/search?q=Russia+strikes+Ukraine+(energy+OR+grid+OR+power+OR+heating)+winter&hl=en-US&gl=US&ceid=US:en", "T3", "Google News", "能源基础设施打击"),
]

def main():
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only")+1]
    min_iv = 1.2
    if "--min-interval" in sys.argv:
        min_iv = float(sys.argv[sys.argv.index("--min-interval")+1])

    proxies = urllib.request.getproxies()
    print(f"[proxy] 出口配置: {proxies}   (自动探测，未写死)")
    print(f"[probe] 候选源 {len(CANDIDATES)} 个，min-interval={min_iv}s")
    results = []
    last_by_host = {}
    for cid, url, tier, pub, note in CANDIDATES:
        if only and only not in cid:
            continue
        host = urlparse(url).netloc
        wait = last_by_host.get(host)
        if wait is not None:
            dt = time.time() - wait
            if dt < min_iv:
                time.sleep(min_iv - dt)
        r = fetch(url)
        last_by_host[host] = time.time()
        v, detail = sniff_verdict(r["ct"], r["body"])
        item = {"id": cid, "url": url, "tier": tier, "publisher": pub, "note": note,
                "status": r["status"], "ms": r["ms"], "server": r["server"],
                "ct": r["ct"][:60], "len": r["len"], "verdict": v, "detail": detail}
        if "err" in r: item["err"] = r["err"]
        results.append(item)
        flag = "✅" if v == "rss-ok" else ("⚠️ " if v in ("json", "html") else "❌")
        print(f"{flag} {cid:14s} {r['status']:>3} {v:13s} {r['len']:>8}B  {detail[:50]}")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"probed_at": datetime.now(timezone.utc).isoformat(),
                   "proxy": proxies, "results": results}, f, ensure_ascii=False, indent=1)
    ok = sum(1 for r in results if r["verdict"] == "rss-ok")
    print(f"\n[probe] rss-ok {ok}/{len(results)}，证据已落 {OUT}")

if __name__ == "__main__":
    main()
