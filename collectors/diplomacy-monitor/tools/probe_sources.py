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
    # --- 协调方官方 T1（历史上 QNA 无 RSS，复测）---
    ("qna-rss", "https://www.qna.org.qa/en/rss", "T1", "QNA", "卡塔尔通讯社（9-1 已知无 RSS，复测）"),
    ("ona-oman", "https://ona.gov.om/", "T1", "ONA", "阿曼通讯社（可达性未知）"),
    ("app-pk", "https://www.app.com.pk/feed/", "T1", "APP", "巴基斯坦联合通讯社 WordPress feed"),
    ("radiopk", "https://www.radio.gov.pk/rss", "T1", "Radio Pakistan", "巴基斯坦国家电台"),
    ("anatolia", "https://www.aa.com.tr/en/rss/default?cat=world", "T3", "Anadolu", "土耳其阿纳多卢（mideast 项目实测可用，土耳其也是协调方之一）"),
    # --- 研究机构 T2 ---
    ("crisisgroup", "https://www.crisisgroup.org/rss.xml", "T2", "ICG", "国际危机组织（mideast 项目可用）"),
    ("atlantic", "https://www.atlanticcouncil.org/category/blogs/iransource/feed/", "T2", "Atlantic Council", "IranSource（iran-domestic 项目可用）"),
    ("carnegie", "https://carnegieendowment.org/rss/solr/?lang=en", "T2", "Carnegie", "卡内基"),
    # --- 媒体 T3（mideast/iran-domestic 项目实测结果沿用）---
    ("aljazeera", "https://www.aljazeera.com/xml/rss/all.xml", "T3", "Al Jazeera", "半岛（mideast 可用，卡塔尔背景但按加工方定级）"),
    ("aawsat", "https://www.aawsat.com/rss", "T3", "Asharq Al-Awsat", "中东报（mideast 可用）"),
    ("bbc-mideast", "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml", "T3", "BBC", "中东频道"),
    ("guardian-iran", "https://www.theguardian.com/world/iran/rss", "T3", "The Guardian", "伊朗频道"),
    ("nyt-mideast", "https://rss.nytimes.com/services/xml/rss/nyt/MiddleEast.xml", "T3", "NYT", "中东频道"),
    ("mee", "https://www.middleeasteye.net/rss", "T3", "MEE", "中东之眼"),
    ("arabnews", "https://www.arabnews.com/rss/feed/27", "T3", "Arab News", "沙特英文报（斡旋报道密集）"),
    ("arabnews2", "https://www.arabnews.com/rss", "T3", "Arab News", "备用路径"),
    ("dawn", "https://www.dawn.com/feed/home", "T3", "Dawn", "巴基斯坦英文大报（巴方斡旋视角）"),
    ("dawn-world", "https://www.dawn.com/rss/world", "T3", "Dawn", "备用路径"),
    ("timesofoman", "https://www.timesofoman.com/rss", "T3", "Times of Oman", "阿曼英文媒体"),
    ("muscatdaily", "https://www.muscatdaily.com/feed/", "T3", "Muscat Daily", "马斯喀特日报"),
    ("gulftimes", "https://www.gulf-times.com/feed/", "T3", "Gulf Times", "卡塔尔英文报"),
    ("dohanews", "https://dohanews.co/feed/", "T3", "Doha News", "多哈新闻"),
    ("thenational", "https://www.thenationalnews.com/rss?latest", "T3", "The National", "阿联酋英文报（mideast 遗留？）"),
    ("tehrantimes", "https://www.tehrantimes.com/rss", "T3", "Tehran Times", "伊朗官媒英文（伊朗官方立场窗口）"),
    ("presstv", "https://www.presstv.ir/rss.xml", "T3", "Press TV", "伊朗国家台（宣传口径，标注使用）"),
    ("trt-world", "https://www.trtworld.com/rss", "T3", "TRT World", "土耳其国际频道"),
    # --- Google News 关键词通道（T3 转述层）---
    ("gn-mediation", GN.format('(US+OR+America)+Iran+(mediation+OR+mediator+OR+intermediaries+OR+go-between)'), "T3", "Google News", "斡旋总通道"),
    ("gn-qatar", GN.format('Qatar+Iran+(mediation+OR+mediator+OR+talks+OR+Al+Thani)'), "T3", "Google News", "卡塔尔斡旋"),
    ("gn-oman", GN.format('Oman+Iran+(talks+OR+mediation+OR+Muscat+OR+corridor)'), "T3", "Google News", "阿曼斡旋"),
    ("gn-pakistan", GN.format('Pakistan+Iran+(mediation+OR+backchannel+OR+Munir+OR+messages)'), "T3", "Google News", "巴基斯坦斡旋"),
    ("gn-trump", GN.format('Trump+Iran+(deal+OR+talks+OR+negotiate+OR+agreement)'), "T3", "Google News", "特朗普谈判意愿"),
    ("gn-taco", GN.format('"TACO"+Trump+Iran'), "T3", "Google News", "TACO 交易（市场押注降温）"),
    ("gn-memo", GN.format('Iran+US+("Islamabad+memorandum"+OR+"memorandum+of+understanding"+OR+"June+deal")'), "T3", "Google News", "备忘录动向"),
    ("gn-deesc", GN.format('US+Iran+(ceasefire+OR+de-escalation+OR+"wind+down"+OR+"pull+back"+OR+halt+strikes)'), "T3", "Google News", "军事降温信号"),
    ("gn-esc", GN.format('Trump+Iran+(strike+OR+"military+action"+OR+"major+decision"+OR+attack)'), "T3", "Google News", "再升级信号（对照）"),
    ("gn-talks", GN.format('US+Iran+(indirect+talks+OR+negotiations+OR+"diplomatic+channel")+Oman'), "T3", "Google News", "间接谈判渠道"),
    ("gn-unga", GN.format('Iran+("General+Assembly"+OR+UNGA)+(visa+OR+delegation+OR+Pezeshkian+OR+Araghchi)'), "T3", "Google News", "联大周外交"),
    ("gn-china", GN.format('China+Iran+(mediation+OR+Wang+Yi+OR+Araghchi+OR+talks)'), "T3", "Google News", "中国渠道"),
    ("gn-gulf", GN.format('Gulf+leaders+Trump+Iran+(meeting+OR+summit+OR+postwar)'), "T3", "Google News", "海湾六国联大会晤"),
    ("gn-nuclear", GN.format('Iran+nuclear+(E3+OR+snapback+OR+IAEA+OR+talks)+sanctions'), "T3", "Google News", "核文件/快速恢复制裁"),
    ("gn-europe", GN.format('Europe+Iran+(diplomacy+OR+talks+OR+Geneva+OR+Switzerland)'), "T3", "Google News", "欧洲渠道"),
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
