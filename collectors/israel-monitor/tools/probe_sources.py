#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""probe_sources.py — opinion-monitor 数据源探测
逐 URL 实测可达性，判定 RSS 可用性（按内容判定，不信状态码）。
用法: python tools/probe_sources.py [--only substr] [--min-interval 1.2]
"""
import json, os, sys, time, gzip, io, zlib
import urllib.request
from urllib.parse import urlparse
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "_probe_round1.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

def sniff_verdict(ct, body):
    """按内容判定，不信状态码。返回 (verdict, detail)"""
    if body is None:
        return "no-body", ""
    head = body[:800].lstrip().lower()
    low = body[:4000].lower()
    if "<rss" in head or "<feed" in head or "<rdf" in head:
        return "rss-ok", ""   # 无 <?xml 声明也算（jpost/aljazeera 实测）
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
    p = urlparse(url)
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

# ---- 候选源清单（tier: 谁加工的这份材料就是什么等级）----
GN = "https://news.google.com/rss/search?q={}&hl=en-US&gl=US&ceid=US:en"
CANDIDATES = [
    # 以色列媒体（T3：对官方发布/事件的报道加工层）
    ("toi-feed", "https://www.timesofisrael.com/feed/", "T3",
     "The Times of Israel", "以色列英文大报，四域军事动向覆盖好"),
    ("jpost-rss1", "https://www.jpost.com/rss/rssfeedsfrontpage.aspx", "T3",
     "Jerusalem Post", "头版 RSS"),
    ("jpost-rss2", "https://www.jpost.com/rss", "T3", "Jerusalem Post", "备用 URL"),
    ("haaretz-rss", "https://www.haaretz.com/rss", "T3", "Haaretz", "左翼大报"),
    ("haaretz-rss2", "https://www.haaretz.com/cmlink/1.4471204", "T3", "Haaretz", "备用（历史已知路径）"),
    ("ynetnews-rss", "https://www.ynetnews.com/feed/", "T3", "Ynetnews", "Ynet 英文版"),
    ("jns-feed", "https://www.jns.org/feed/", "T3", "JNS", "亲官方立场英文媒体（与 TOI 互验）"),
    ("arutzsheva-rss", "https://www.israelnationalnews.com/rss", "T3", "Arutz Sheva", "右翼媒体（平衡左翼）"),
    ("arutzsheva-rss2", "https://www.israelnationalnews.com/Controls/RssFeed.ashx", "T3",
     "Arutz Sheva", "备用 URL"),
    ("i24-rss", "https://www.i24news.tv/en/rss", "T3", "i24NEWS", "以色列国际频道"),
    # 区域/国际媒体
    ("aljazeera-all", "https://www.aljazeera.com/xml/rss/all.xml", "T3",
     "Al Jazeera", "区域视角（与以方媒体对照当事方立场）"),
    ("mee-rss", "https://www.middleeasteye.net/rss", "T3", "Middle East Eye", "区域批评视角"),
    ("cradle-rss", "https://thecradle.co/feed", "T3", "The Cradle", "抵抗轴心视角媒体"),
    ("cradle-rss2", "https://thecradle.co/articles/rss", "T3", "The Cradle", "备用 URL"),
    ("aawsat-rss", "https://english.aawsat.com/rss", "T3", "Asharq Al-Awsat", "沙特系英文（黎巴嫩官方数字多源）"),
    ("aawsat-rss2", "https://english.aawsat.com/arabic/rss", "T3", "Asharq Al-Awsat", "备用"),
    ("rstatecraft", "https://responsiblestatecraft.org/feed", "T3",
     "Responsible Statecraft", "军控/外交分析（政策层）"),
    # 官方一手（T1）
    ("un-press", "https://press.un.org/en/rss.xml", "T1",
     "United Nations", "UN 新闻稿（秘书长表态/安理会）"),
    ("unifil-rss", "https://unifil.unmissions.org/rss.xml", "T1",
     "UNIFIL", "联黎部队动态（黎南局势一手）"),
    ("ochaopt", "https://www.ochaopt.org/rss.xml", "T1", "OCHA oPt", "西岸/加沙人道数据一手"),
    ("idf-site", "https://www.idf.il/en/", "T1", "IDF", "以军官网（探测是否 HTML）"),
    ("pmo-govil", "https://www.gov.il/en/departments/israelgovernment_rss", "T1",
     "Israeli PMO", "总理办公室新闻（探测）"),
    # Google News 关键词通道（T3 转述层：路透/AP/BBC 等对同一事件的报道）
    ("gn-netanyahu", GN.format('Netanyahu+(trial+OR+election+OR+UNGA)'), "T3", "Google News",
     "内塔尼亚胡动向总通道"),
    ("gn-election-polls", GN.format('Israel+election+poll+Knesset'), "T3", "Google News",
     "大选民调通道（开票前为唯一结果预测层）"),
    ("gn-election-parties", GN.format('Israel+election+(Eisenkot+OR+Bennett+OR+Lapid+OR+Golan)'),
     "T3", "Google News", "主要竞选人动向"),
    ("gn-idf-lebanon", GN.format('IDF+Lebanon+Hezbollah+strike'), "T3", "Google News",
     "黎巴嫩军事动向"),
    ("gn-idf-syria", GN.format('Israel+Syria+strike+buffer+zone'), "T3", "Google News",
     "叙利亚军事动向"),
    ("gn-westbank", GN.format('IDF+"West+Bank"+raid+arrest'), "T3", "Google News",
     "约旦河西岸军事动向"),
    ("gn-gaza", GN.format('Gaza+ceasefire+IDF+buffer'), "T3", "Google News",
     "加沙停火与缓冲区"),
    ("gn-knesset", GN.format('Knesset+coalition+government+talks'), "T3", "Google News",
     "组阁动向（开票后主通道）"),
    ("gn-katz", GN.format('Israel+Katz+defense+minister'), "T3", "Google News",
     "防长表态通道"),
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
        flag = "✅" if v == "rss-ok" else ("⚠️ " if v in ("json",) else "❌")
        print(f"{flag} {cid:18s} {r['status']:>3} {v:12s} {r['len']:>8}B  {detail[:60]}")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"probed_at": datetime.now(timezone.utc).isoformat(),
                   "proxy": proxies, "results": results}, f, ensure_ascii=False, indent=1)
    ok = sum(1 for r in results if r["verdict"] == "rss-ok")
    print(f"\n[probe] rss-ok {ok}/{len(results)}，证据已落 {OUT}")

if __name__ == "__main__":
    main()
