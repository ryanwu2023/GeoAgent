#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""probe_sources.py — eu-domestic-monitor 候选信源探测（只读，不写配置）。
每次运行打印实际代理出口（绝不写死端口）。"""
import gzip, re, sys, time, zlib, urllib.request
import xml.etree.ElementTree as ET

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9,fr;q=0.8,it;q=0.7",
        "Accept-Encoding": "gzip, deflate", "Connection": "close"})
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
            return r.status, raw.decode("utf-8", "replace"), int((time.time()-t0)*1000)
    except urllib.error.HTTPError as e:
        return e.code, "", int((time.time()-t0)*1000)
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"[:140], int((time.time()-t0)*1000)

def sniff(status, body):
    if status != 200:
        return f"{status}"
    head = body[:300].lstrip().lower()
    if head.startswith("<?xml") or "<rss" in head or "<feed" in head:
        n = body.count("<item") + body.count("<entry")
        m = re.search(r"<pubDate>(.*?)</pubDate>", body) or re.search(r"<updated>(.*?)</updated>", body)
        fresh = m.group(1)[:30] if m else "?"
        return f"rss items={n} fresh={fresh}"
    links = len(re.findall(r'https?://', body))
    return f"html size={len(body)} links={links}"

CANDIDATES = [
    # ---- 法国 ----
    ("lemonde-une", "https://www.lemonde.fr/rss/une.xml"),
    ("lemonde-inter", "https://www.lemonde.fr/international/rss_full.xml"),
    ("lefigaro-actu", "https://www.lefigaro.fr/rss/figaro_actualites.xml"),
    ("lefigaro-pol", "https://www.lefigaro.fr/rss/figaro_politique.xml"),
    ("france24-en", "https://www.france24.com/en/rss"),
    ("france24-fr", "https://www.france24.com/fr/rss"),
    ("bfmtv-pol", "https://www.bfmtv.com/rss/politique/"),
    ("bfmtv-inter", "https://www.bfmtv.com/rss/international/"),
    ("elysee", "https://www.elysee.fr/rss.xml"),
    # ---- 意大利 ----
    ("ansa-en", "https://www.ansa.it/english/rss/eng.xml"),
    ("ansa-top", "https://www.ansa.it/sito/notizie/topnews/topnews_rss.xml"),
    ("ansa-politica", "https://www.ansa.it/sito/notizie/politica/politica_rss.xml"),
    ("ilsole-italia", "https://www.ilsole24ore.com/rss/italia.xml"),
    ("repubblica", "https://www.repubblica.it/rss/homepage/rss2.0.xml"),
    ("ilpost", "https://www.ilpost.it/feed/"),
    # ---- 欧盟机构/泛欧 ----
    ("politicoeu", "https://www.politico.eu/feed/"),
    ("euobserver", "https://euobserver.com/rss"),
    ("euronews", "https://www.euronews.com/rss"),
    ("euractiv", "https://www.euractiv.com/feed/"),
    ("ec-presscorner", "https://ec.europa.eu/commission/presscorner/home/rss"),
    ("consilium", "https://www.consilium.europa.eu/en/rss/"),
    ("parlament-eu", "https://www.europarl.europa.eu/rss/doc/top-stories/en.xml"),
    # ---- T2 智库 ----
    ("ecfr", "https://ecfr.eu/feed/"),
    ("bruegel", "https://www.bruegel.org/feed"),
    ("ceps", "https://www.ceps.eu/feed/"),
    # ---- 匈牙利/其他成员国窗口 ----
    ("telex-en", "https://telex.hu/rss/english"),
    ("polandin? warsaw", "https://www.polskieradio.pl/395/rss"),
    # ---- 中文窗口 ----
    ("xinhua-home", "https://english.news.cn/home.htm"),
    ("chinanews-scroll", "https://www.chinanews.com.cn/rss/scroll-news.xml"),
    # ---- gnews 通道（出口依赖）----
    ("gn-france", "https://news.google.com/rss/search?q=France+2027+presidential+(%22Le+Pen%22+OR+%22Rassemblement+national%22+OR+%22National+Rally%22)&hl=en-US&gl=US&ceid=US:en"),
    ("gn-italy", "https://news.google.com/rss/search?q=Italy+election+2027+(Meloni+OR+coalition+OR+poll)&hl=en-US&gl=US&ceid=US:en"),
    ("gn-eu-ua", "https://news.google.com/rss/search?q=(EU+OR+Europe)+(Ukraine)+(military+aid+OR+weapons+OR+support)&hl=en-US&gl=US&ceid=US:en"),
    ("gn-eu-ru", "https://news.google.com/rss/search?q=(EU+OR+Europe)+Russia+(sanctions+OR+talks+OR+peace)&hl=en-US&gl=US&ceid=US:en"),
    ("gn-xinhua", "https://news.google.com/rss/search?q=Xinhua+(EU+OR+Europe+OR+France+OR+Italy)&hl=en-US&gl=US&ceid=US:en"),
    ("gn-china-eu", "https://news.google.com/rss/search?q=(China+OR+Beijing)+(EU+OR+Europe)+(Ukraine+OR+Russia+OR+trade)&hl=en-US&gl=US&ceid=US:en"),
]

def main():
    print("[proxy]", urllib.request.getproxies())
    for sid, url in CANDIDATES:
        st, body, ms = fetch(url)
        print(f"{sid:18s} {sniff(st, body):42s} {ms:5d}ms  {url[:80]}")
        time.sleep(1.2)

if __name__ == "__main__":
    main()
