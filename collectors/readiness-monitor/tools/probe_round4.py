#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第四轮 —— ①验证革命卫队官方源 ②枚举 CENTCOM 正确 Site/ContentType ③聚合器 site: 兜底。"""
from __future__ import annotations

import urllib.parse

import re
import ssl
import sys
import time
import gzip
import io
import zlib
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HDRS = {
    "User-Agent": UA,
    "Accept": ("application/rss+xml, application/xml, text/xml, "
               "text/html;q=0.9, */*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Connection": "keep-alive",
}
ITEM_RE = re.compile(r"<item\b", re.I)
T_RE = re.compile(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", re.I | re.S)
D_RE = re.compile(r"<pubDate>(.*?)</pubDate>", re.I | re.S)


def decompress(raw: bytes, enc: str) -> bytes:
    enc = (enc or "").lower()
    try:
        if "gzip" in enc or raw[:2] == b"\x1f\x8b":
            return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        if "deflate" in enc:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
    except Exception:  # noqa: BLE001
        pass
    return raw


def get(url, verify=True, tries=2, timeout=25):
    ctx = None if verify else ssl._create_unverified_context()
    last = None
    for i in range(tries):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=HDRS),
                                       timeout=timeout, context=ctx)
            return {"status": r.status, "server": r.headers.get("Server", ""),
                    "body": decompress(r.read(), r.headers.get("Content-Encoding", "")),
                    "err": None}
        except urllib.error.HTTPError as e:
            b = b""
            try:
                b = decompress(e.read(), e.headers.get("Content-Encoding", ""))
            except Exception:  # noqa: BLE001
                pass
            return {"status": e.code, "server": e.headers.get("Server", ""),
                    "body": b, "err": None}
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            time.sleep(1.5 * (i + 1))
    return {"status": None, "server": "", "body": b"", "err": last}


def show(label, url, verify=True, n_titles=3):
    r = get(url, verify=verify)
    txt = r["body"].decode("utf-8", "replace")
    items = len(ITEM_RE.findall(txt))
    print(f"-- {label:26} status={r['status']} items={items:4} bytes={len(r['body']):7} "
          f"{'err=' + r['err'][:46] if r['err'] else ''}")
    if items:
        titles = [re.sub(r"\s+", " ", t).strip() for t in T_RE.findall(txt)][1:n_titles + 1]
        dates = [d.strip() for d in D_RE.findall(txt)][:2]
        for t in titles:
            print(f"      · {t[:96]}")
        if dates:
            print(f"      pubDate: {dates}")
    return r


def main() -> int:
    auto = urllib.request.getproxies()
    print("=" * 96)
    print(f"实际出口代理: {auto.get('https') or auto.get('http') or '无（直连）'}")
    print("=" * 96)

    print("\n########## A. 革命卫队系官方/半官方源验证 ##########")
    show("sepahnews(IRGC官方)", "https://sepahnews.ir/fa/rss/allnews", verify=False)
    show("defapress(IRGC军事)", "https://defapress.ir/fa/rss/allnews")
    show("mashreghnews(IRGC系)", "https://www.mashreghnews.ir/rss")
    show("kayhan(强硬派)", "https://kayhan.ir/fa/rss/allnews")
    show("parstoday(IRIB英文)", "https://parstoday.ir/en/rss")
    show("isna-en", "https://en.isna.ir/rss")
    show("tehrantimes", "https://www.tehrantimes.com/rss")
    show("mehr-en", "https://en.mehrnews.com/rss")
    show("irna-en(官方通讯社)", "https://en.irna.ir/rss")

    print("\n########## B. CENTCOM Site id / ContentType 枚举 ##########")
    print("（AFPIMS：Site 有效但该 ContentType 无内容 → 返回空 RSS；Site 无效 → 返回 HTML）")
    for site in (1000, 1001, 1002, 1010, 1024, 1025, 1030, 1040, 1050, 1060, 1070):
        r = get(f"https://www.centcom.mil/DesktopModules/ArticleCS/RSS.ashx"
                f"?ContentType=1&Site={site}&max=20", tries=1, timeout=15)
        txt = r["body"].decode("utf-8", "replace")
        kind = "RSS-empty" if txt.lstrip().startswith("<?xml") else (
            "HTML" if txt.lstrip()[:1] == "<" else "?")
        print(f"   Site={site:5} status={r['status']} bytes={len(r['body']):6} "
              f"items={len(ITEM_RE.findall(txt)):3} kind={kind}")

    print("\n   -- centcom Site=1000 各 ContentType --")
    for ct in (1, 2, 3, 4, 5, 9, 13):
        r = get(f"https://www.centcom.mil/DesktopModules/ArticleCS/RSS.ashx"
                f"?ContentType={ct}&Site=1000&max=20", tries=1, timeout=15)
        txt = r["body"].decode("utf-8", "replace")
        print(f"   ContentType={ct:2} status={r['status']} bytes={len(r['body']):6} "
              f"items={len(ITEM_RE.findall(txt)):3}")
        if ITEM_RE.findall(txt):
            for t in [re.sub(r'\s+', ' ', x).strip() for x in T_RE.findall(txt)][1:3]:
                print(f"        · {t[:90]}")

    print("\n########## C. 美方其它战区/军种 feed（作 CENTCOM 替代与对照）##########")
    show("navcent(CENTCOM海军)", "https://www.cusnc.navy.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1015&max=30")
    show("dod-news", "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=30")
    show("dod-releases", "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max=30")
    show("dod-transcripts", "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=13&Site=945&max=30")

    print("\n########## D. 公开聚合器：site: 定向取回不可达媒体（T3 线索层）##########")
    for label, q in [
        ("gnews-tasnim", "site:tasnimnews.com"),
        ("gnews-farsnews", "site:farsnews.ir"),
        ("gnews-sepahnews", "site:sepahnews.ir"),
        ("gnews-defapress", "site:defapress.ir"),
        ("gnews-centcom", '"CENTCOM" statement'),
        ("gnews-irgc-readiness", 'IRGC "combat readiness"'),
    ]:
        url = ("https://news.google.com/rss/search?q="
               + urllib.parse.quote(q) + "&hl=en-US&gl=US&ceid=US:en")
        show(label, url, n_titles=3)

    return 0


if __name__ == "__main__":
    import urllib.parse  # noqa: F401  (供 D 组使用)
    sys.exit(main())
