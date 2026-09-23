#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""probe_round2.py — 第二轮：修正路径 + 剩余官方/协调方候选"""
import gzip, re, time, zlib, urllib.request, urllib.error
import xml.etree.ElementTree as ET

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HDR = {"User-Agent": UA, "Accept": "*/*",
       "Accept-Language": "en-US,en;q=0.9,ru;q=0.8,tr;q=0.8",
       "Accept-Encoding": "gzip, deflate", "Connection": "close"}

CANDIDATES = [
    ("whitehouse-actions", "https://www.whitehouse.gov/presidential-actions/feed/"),
    ("whitehouse-news", "https://www.whitehouse.gov/news/feed/"),
    ("state-dept-brief", "https://www.state.gov/press-briefings/feed/"),
    ("kyivpost", "https://www.kyivpost.com/feed"),
    ("kyivpost2", "https://www.kyivpost.com/rss/feed"),
    ("meduza-rss", "https://meduza.io/rss/all"),
    ("meduza-en2", "https://meduza.io/en/rss/all"),
    ("meduza-en3", "https://meduza.io/en/feed"),
    ("nato-rss", "https://www.nato.int/cps/en/natohq/rss.xml"),
    ("un-seccon", "https://press.un.org/en/rss/press-releases.xml"),
    ("turkey-mfa", "https://www.mfa.gov.tr/en.mfa-rss.en.mfa"),
    ("kremlin-en", "http://en.kremlin.ru/feeds/news"),
    ("europa-ec", "https://ec.europa.eu/commission/presscorner/api/documents?language=en"),
]

def fetch(url):
    req = urllib.request.Request(url, headers=HDR)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
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
            return r.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"[:120]

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
    n = len(re.findall(r'href="https://english\.news\.cn/20\d{6}/', body))
    if n:
        return f"html-list links={n}", n, ""
    return f"html size={len(body)} links={len(re.findall(r'https?://', body))}", 0, ""

def main():
    print("[proxy]", urllib.request.getproxies())
    for sid, url in CANDIDATES:
        s, b = fetch(url)
        kind, n, fresh = sniff(s, b)
        print(f"{s:>3} {kind:<28} fresh={fresh:<28} {sid:<20} {url[:66]}")
        time.sleep(1.2)

if __name__ == "__main__":
    main()
