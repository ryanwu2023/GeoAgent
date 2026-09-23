#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""probe_sources.py — ru-diplomacy-monitor 候选信源探测（第一轮）
只读探测：状态码 / feed 类型 / 条数 / 最新条目日期 / Server 头。
不修改任何配置；结果人工判定后写入 config.json（启用/停用留档纪律）。"""
import gzip, re, ssl, sys, time, zlib, urllib.request, urllib.error
import xml.etree.ElementTree as ET

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HDR = {"User-Agent": UA, "Accept": "*/*",
       "Accept-Language": "en-US,en;q=0.9,ru;q=0.8,tr;q=0.7,uk;q=0.7",
       "Accept-Encoding": "gzip, deflate", "Connection": "close"}

CANDIDATES = [
    # --- T1 官方：美国 ---
    ("state-dept", "https://www.state.gov/rss-page"),
    ("state-feed", "https://www.state.gov/press-releases/feed/"),
    ("whitehouse", "https://www.whitehouse.gov/feed/"),
    # --- T1 官方：乌克兰 ---
    ("ua-president", "https://www.president.gov.ua/en/rss"),
    ("ua-president-news", "https://www.president.gov.ua/rss"),
    ("ua-mfa", "https://mfa.gov.ua/en/rss"),
    ("ua-mfa-news", "https://mfa.gov.ua/rss"),
    # --- T1 官方：国际组织 ---
    ("un-news", "https://news.un.org/feed/subscribe/en/news/all/rss.xml"),
    ("eeas", "https://www.eeas.europa.eu/rss.xml"),
    ("nato-news", "https://www.nato.int/cps/en/natohq/news_feed.rss"),
    # --- T3 俄方窗口 ---
    ("tass", "https://tass.com/rss/v2.xml"),
    ("interfax", "https://www.interfax.ru/rss.asp"),
    ("meduza-en", "https://meduza.io/en/rss"),
    ("moscowtimes", "https://www.themoscowtimes.com/rss/news"),
    # --- T3 乌克兰窗口 ---
    ("kyivpost", "https://www.kyivpost.com/rss"),
    ("euromaidan", "https://euromaidanpress.com/feed/"),
    ("pravda-en", "https://www.pravda.com.ua/eng/rss/"),
    # --- T3 协调方窗口 ---
    ("anadolu", "https://www.aa.com.tr/en/rss/default?cat=world"),
    ("dailysabah", "https://www.dailysabah.com/rssFeed/50?en"),
    # --- 中文窗口 ---
    ("xinhua-home", "https://english.news.cn/home.htm"),
    ("chinanews", "https://www.chinanews.com.cn/rss/scroll-news.xml"),
]

def fetch(url, ctx=None):
    req = urllib.request.Request(url, headers=HDR)
    try:
        with urllib.request.urlopen(req, timeout=25, context=ctx) as r:
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
            return r.status, raw.decode("utf-8", "replace"), r.headers.get("Server", "")
    except urllib.error.HTTPError as e:
        return e.code, "", ""
    except Exception as e:
        return 0, "", f"{type(e).__name__}: {e}"[:110]

def sniff(status, raw):
    if status != 200:
        return f"{status}", 0, ""
    body = raw
    head = body[:300].lstrip().lower()
    if head.startswith("<?xml") or "<rss" in head or "<feed" in head or "<rdf" in head:
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return "xml-parse-fail", 0, ""
        nodes = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        fresh = ""
        for n in nodes[:30]:
            for tag in ("pubDate", "published", "updated"):
                e = n.find(tag) if n.find(tag) is not None else n.find(f"{{http://www.w3.org/2005/Atom}}{tag}")
                if e is not None and (e.text or "").strip():
                    fresh = e.text.strip()[:28]
                    break
            if fresh: break
        return f"rss items={len(nodes)}", len(nodes), fresh
    # html listing（新华社首页）
    n = len(re.findall(r'href="https://english\.news\.cn/20\d{6}/', body))
    if n:
        return f"html-list links={n}", n, "2026-09-19(首页)"
    return f"html size={len(body)}", 0, ""

def main():
    import urllib.request as _u
    print("[proxy]", _u.getproxies())
    ok, bad = [], []
    for sid, url in CANDIDATES:
        s, b, srv = fetch(url)
        kind, n, fresh = sniff(s, b)
        line = f"{s:>3} {kind:<24} fresh={fresh:<28} {sid:<16} {url[:70]}"
        print(line)
        (ok if s == 200 and n > 0 else bad).append((sid, url, line))
        time.sleep(1.2)
    print(f"\n== 可用 {len(ok)} / 候选 {len(CANDIDATES)} ==")

if __name__ == "__main__":
    main()
