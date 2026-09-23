#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""候选信源可达性探测（第一轮）。

★ 为什么必须先探测再写 config：
  写 URL 是「猜」，抓取源是「量」。凡未实测的端点，大概率是 404 / 200 空体 /
  JS 渲染页（拿不到条目），而症状与「被 CDN 拦截」难以区分。
  本脚本对每个候选端点报告：HTTP 状态 / Server 头 / 字节数 / 解析出的条目数 /
  源内最新日期，并**同时对比两个代理出口**，用于区分「站点不可达」与「本地出口问题」。

用法
----
    python tools/probe_sources.py                # 自动探测系统代理 + 对比备选端口
    python tools/probe_sources.py --direct       # 直连（仅用于确认是否必须走代理）
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import io
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
import zlib
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/rss+xml,application/atom+xml,application/xml;q=0.9,"
              "text/xml;q=0.8,application/json;q=0.7,*/*;q=0.6",
    "Accept-Language": "en-US,en;q=0.9,fa;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

# ---------------------------------------------------------------- 候选源
# 分区：iran=伊朗官方/半官方  us=美方官方  third=第三方媒体  think=智库/分析  io=航运/能源/核专业
CANDIDATES: List[Dict[str, str]] = [
    # ---------- 一、打击美军海上作战平台（海军/海事）
    {"id": "usni-news",        "zone": "naval",   "tier": "T3", "url": "https://news.usni.org/feed"},
    {"id": "naval-news",       "zone": "naval",   "tier": "T3", "url": "https://www.navalnews.com/feed/"},
    {"id": "twz",              "zone": "naval",   "tier": "T3", "url": "https://www.twz.com/feed"},
    {"id": "defensenews-naval", "zone": "naval",  "tier": "T3", "url": "https://www.defensenews.com/arc/outboundfeeds/rss/category/naval/"},
    {"id": "gcaptain",         "zone": "maritime", "tier": "T3", "url": "https://gcaptain.com/feed/"},
    {"id": "maritime-exec",    "zone": "maritime", "tier": "T3", "url": "https://www.maritime-executive.com/articles.rss"},
    {"id": "splash247",        "zone": "maritime", "tier": "T3", "url": "https://splash247.com/feed/"},
    {"id": "safety4sea",       "zone": "maritime", "tier": "T3", "url": "https://safety4sea.com/feed/"},
    {"id": "ukmto",            "zone": "maritime", "tier": "T1", "url": "https://www.ukmto.org/rss"},
    {"id": "ukmto-news",       "zone": "maritime", "tier": "T1", "url": "https://www.ukmto.org/news"},

    # ---------- 二、霍尔木兹 + 曼德海峡双线
    {"id": "gnews-hormuz",     "zone": "chokepoint", "tier": "T3", "url": "https://news.google.com/rss/search?q=%22Strait+of+Hormuz%22+when%3A7d&hl=en-US&gl=US&ceid=US%3Aen"},
    {"id": "gnews-babelmandeb", "zone": "chokepoint", "tier": "T3", "url": "https://news.google.com/rss/search?q=%22Bab+el-Mandeb%22+when%3A7d&hl=en-US&gl=US&ceid=US%3Aen"},
    {"id": "gnews-redsea",     "zone": "chokepoint", "tier": "T3", "url": "https://news.google.com/rss/search?q=%22Red+Sea%22+shipping+attack+when%3A7d&hl=en-US&gl=US&ceid=US%3Aen"},
    {"id": "gnews-houthi",     "zone": "chokepoint", "tier": "T3", "url": "https://news.google.com/rss/search?q=Houthi+attack+shipping+when%3A7d&hl=en-US&gl=US&ceid=US%3Aen"},

    # ---------- 三、打击美盟友能源设施
    {"id": "oilprice",         "zone": "energy", "tier": "T3", "url": "https://oilprice.com/rss/main"},
    {"id": "gnews-abqaiq",     "zone": "energy", "tier": "T3", "url": "https://news.google.com/rss/search?q=(Abqaiq+OR+%22Ras+Tanura%22+OR+Kharg+Island)+attack+when%3A14d&hl=en-US&gl=US&ceid=US%3Aen"},
    {"id": "gnews-oilattack",  "zone": "energy", "tier": "T3", "url": "https://news.google.com/rss/search?q=oil+facility+drone+strike+Gulf+when%3A7d&hl=en-US&gl=US&ceid=US%3Aen"},
    {"id": "arabnews",         "zone": "energy", "tier": "T3", "url": "https://www.arabnews.com/rss.xml"},
    {"id": "thenational-ae",   "zone": "energy", "tier": "T3", "url": "https://www.thenationalnews.com/rss"},

    # ---------- 四、海底光缆
    {"id": "gnews-cable",      "zone": "cable", "tier": "T3", "url": "https://news.google.com/rss/search?q=(%22undersea+cable%22+OR+%22submarine+cable%22)+cut+Red+Sea+when%3A30d&hl=en-US&gl=US&ceid=US%3Aen"},
    {"id": "submarinenetworks", "zone": "cable", "tier": "T3", "url": "https://www.submarinenetworks.com/rss"},
    {"id": "telegeography",    "zone": "cable", "tier": "T2", "url": "https://www.telegeography.com/rss"},

    # ---------- 五、核试验 / 核计划
    {"id": "iaea-news",        "zone": "nuclear", "tier": "T1", "url": "https://www.iaea.org/feeds/news"},
    {"id": "iaea-pressrss",    "zone": "nuclear", "tier": "T1", "url": "https://www.iaea.org/news/rss"},
    {"id": "gnews-nuclear",    "zone": "nuclear", "tier": "T3", "url": "https://news.google.com/rss/search?q=Iran+nuclear+(test+OR+enrichment+OR+IAEA)+when%3A14d&hl=en-US&gl=US&ceid=US%3Aen"},
    {"id": "thebulletin",      "zone": "nuclear", "tier": "T2", "url": "https://thebulletin.org/feed/"},
    {"id": "armscontrol",      "zone": "nuclear", "tier": "T2", "url": "https://www.armscontrol.org/rss.xml"},
    {"id": "isis-online",      "zone": "nuclear", "tier": "T2", "url": "https://isis-online.org/rss"},

    # ---------- 六、伊朗官方 / 半官方（战备表态一手面）
    {"id": "irna-en",          "zone": "iran-official", "tier": "T1", "url": "https://en.irna.ir/rss"},
    {"id": "mehr-en",          "zone": "iran-official", "tier": "T2", "url": "https://en.mehrnews.com/rss"},
    {"id": "isna-en",          "zone": "iran-official", "tier": "T2", "url": "https://en.isna.ir/rss"},
    {"id": "tehrantimes",      "zone": "iran-official", "tier": "T2", "url": "https://www.tehrantimes.com/rss"},
    {"id": "parstoday",        "zone": "iran-official", "tier": "T1", "url": "https://parstoday.ir/en/rss"},
    {"id": "sepahnews",        "zone": "iran-official", "tier": "T1", "url": "https://sepahnews.ir/fa/rss/allnews"},
    {"id": "defapress",        "zone": "iran-official", "tier": "T1", "url": "https://defapress.ir/fa/rss/allnews"},
    {"id": "mashreghnews",     "zone": "iran-official", "tier": "T1", "url": "https://www.mashreghnews.ir/rss"},
    {"id": "kayhan",           "zone": "iran-official", "tier": "T2", "url": "https://kayhan.ir/fa/rss/allnews"},
    {"id": "presstv",          "zone": "iran-official", "tier": "T2", "url": "https://www.presstv.ir/rss.xml"},
    {"id": "farsnews-direct",  "zone": "iran-official", "tier": "T1", "url": "https://www.farsnews.ir/rss"},
    {"id": "tasnim-direct",    "zone": "iran-official", "tier": "T1", "url": "https://www.tasnimnews.com/en/rss/feed/0/7/0/all-news"},
    {"id": "khamenei-en",      "zone": "iran-official", "tier": "T1", "url": "https://english.khamenei.ir/rss"},
    {"id": "iranintl",         "zone": "iran-opp", "tier": "T3", "url": "https://www.iranintl.com/en/rss"},
    {"id": "radiofarda",       "zone": "iran-opp", "tier": "T3", "url": "https://www.rferl.org/rss"},

    # ---------- 美方官方（对手方表态）
    {"id": "dod-news",         "zone": "us-official", "tier": "T1", "url": "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=60"},
    {"id": "dod-releases",     "zone": "us-official", "tier": "T1", "url": "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max=60"},
    {"id": "state-press",      "zone": "us-official", "tier": "T1", "url": "https://www.state.gov/rss-feed/press-releases/feed/"},
    {"id": "treasury-ofac",    "zone": "us-official", "tier": "T1", "url": "https://home.treasury.gov/rss/press-releases.xml"},
    {"id": "dvids-centcom",    "zone": "us-official", "tier": "T1", "url": "https://www.dvidshub.net/rss/unit/72"},
    {"id": "gnews-centcom",    "zone": "us-official", "tier": "T3", "url": "https://news.google.com/rss/search?q=site%3Acentcom.mil&hl=en-US&gl=US&ceid=US%3Aen"},

    # ---------- 第三方区域媒体
    {"id": "aljazeera",        "zone": "third", "tier": "T3", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
    {"id": "bbc-me",           "zone": "third", "tier": "T3", "url": "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml"},
    {"id": "france24-me",      "zone": "third", "tier": "T3", "url": "https://www.france24.com/en/middle-east/rss"},
    {"id": "mideasteye",       "zone": "third", "tier": "T3", "url": "https://www.middleeasteye.net/rss"},
    {"id": "almonitor",        "zone": "third", "tier": "T3", "url": "https://www.al-monitor.com/rss"},
    {"id": "amwaj",            "zone": "third", "tier": "T3", "url": "https://amwaj.media/rss"},
    {"id": "timesofisrael",    "zone": "third", "tier": "T3", "url": "https://www.timesofisrael.com/feed/"},
    {"id": "jpost",            "zone": "third", "tier": "T3", "url": "https://www.jpost.com/rss/rssfeedsfrontpage.aspx"},

    # ---------- 智库/分析（T2）
    {"id": "csis-analysis",    "zone": "think", "tier": "T2", "url": "https://www.csis.org/rss/analysis"},
    {"id": "atlanticcouncil",  "zone": "think", "tier": "T2", "url": "https://www.atlanticcouncil.org/feed/"},
    {"id": "washinstitute",    "zone": "think", "tier": "T2", "url": "https://www.washingtoninstitute.org/rss.xml"},
    {"id": "isw",              "zone": "think", "tier": "T2", "url": "https://www.understandingwar.org/rss.xml"},
]


def decompress(raw: bytes, enc: str) -> bytes:
    enc = (enc or "").lower()
    try:
        if "gzip" in enc or raw[:2] == b"\x1f\x8b":
            return __import__("gzip").GzipFile(fileobj=io.BytesIO(raw)).read()
        if "deflate" in enc:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
    except Exception:  # noqa: BLE001
        pass
    return raw


def make_opener(proxy: Optional[str]):
    handlers: List[Any] = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
    return urllib.request.build_opener(*handlers)


ITEM_RE = re.compile(r"<(item|entry)\b.*?</\1>", re.S | re.I)
DATE_RE = re.compile(
    r"<(?:pubDate|published|updated|dc:date)\b[^>]*>(.*?)</", re.S | re.I)


def probe_one(cand: Dict[str, str], proxy: Optional[str], timeout: int = 20,
              retries: int = 1) -> Dict[str, Any]:
    """探测单个源。

    ★ 默认 retries=1（快），但**失败源必须用 --retries 3 复测**：
      经同一代理出网时 `SSL: UNEXPECTED_EOF_WHILE_READING` 这类错误常是**抖动**，
      首次失败不等于站点不可用（实测同一源一会儿好一会儿坏）。
      不复测就会把「可用的源」误判成「停用」，凭空制造采集缺口。
    """
    op = make_opener(proxy)
    out: Dict[str, Any] = dict(cand)
    out.update({"proxy": proxy or "直连", "http": None, "server": "", "bytes": 0,
                "items": 0, "latest": None, "ct": "", "err": None, "note": "",
                "attempts": 0})
    t0 = time.time()
    last_err = ""
    for attempt in range(max(1, retries)):
        out["attempts"] = attempt + 1
        try:
            req = urllib.request.Request(cand["url"], headers=HEADERS)
            r = op.open(req, timeout=timeout)
            body = decompress(r.read(), r.headers.get("Content-Encoding", ""))
            out["http"] = r.status
            out["server"] = (r.headers.get("Server") or "")[:28]
            out["ct"] = (r.headers.get("Content-Type") or "")[:40]
            out["bytes"] = len(body)
            txt = body.decode("utf-8", "replace")
            out["items"] = len(ITEM_RE.findall(txt))
            dates = DATE_RE.findall(txt)
            if dates:
                out["latest"] = dates[0].strip()[:40]
            out["err"] = None
            if r.status == 200 and not body:
                out["note"] = "★ HTTP 200 + 空响应体（最像正常的失败）"
            elif r.status == 200 and not out["items"]:
                head = txt[:120].replace("\n", " ")
                out["note"] = f"200 但解析出 0 条目（疑似 HTML/JS 页）: {head[:60]}"
            else:
                out["note"] = ""
            if out["items"] > 0:
                break                       # 成功即止
        except urllib.error.HTTPError as e:
            out["http"] = e.code
            out["server"] = (e.headers.get("Server") or "")[:28] if e.headers else ""
            last_err = f"HTTP {e.code}"
            out["err"] = last_err
            if e.code in (401, 403, 404, 410, 429):
                break                       # 这几类重试无意义（或已知限流）
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            out["err"] = last_err[:110]
        if attempt < retries - 1:
            time.sleep(1.5 * (attempt + 1))
    if out["items"] > 0:
        out["err"] = None
    out["err"] = out["err"] or (last_err or None)
    out["ms"] = int((time.time() - t0) * 1000)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="候选信源可达性探测")
    ap.add_argument("--direct", action="store_true", help="只测直连")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--retries", type=int, default=1,
                    help="每源尝试次数。★ 复测失败源时用 3，区分「抖动」与「真不可用」")
    ap.add_argument("--extra", help="追加候选源 JSON（数组，字段同 CANDIDATES）")
    ap.add_argument("--egress", help="只测指定出口（如 http://127.0.0.1:7897）")
    ap.add_argument("--only", help="只测指定 id（逗号分隔）")
    ap.add_argument("--json", help="把结果写成 JSON")
    args = ap.parse_args()

    cands = list(CANDIDATES)
    if args.extra:
        with open(args.extra, encoding="utf-8") as f:
            cands += json.load(f)
    if args.only:
        want = {s.strip() for s in args.only.split(",") if s.strip()}
        cands = [c for c in cands if c["id"] in want]

    sys_proxy = (urllib.request.getproxies() or {}).get("https")
    egresses: List[Optional[str]] = []
    if args.egress:
        egresses = [args.egress]
    elif args.direct:
        egresses = [None]
    else:
        if sys_proxy:
            egresses.append(sys_proxy)
        # 备选端口：用于区分「站点不可达」与「本地出口问题」
        for port in (7897, 7890, 10809, 1080):
            cand = f"http://127.0.0.1:{port}"
            s = socket.socket()
            s.settimeout(1.5)
            try:
                s.connect(("127.0.0.1", port))
                if cand != sys_proxy:
                    egresses.append(cand)
            except Exception:  # noqa: BLE001
                pass
            finally:
                s.close()

    print("=" * 108)
    print("候选信源可达性探测")
    print("=" * 108)
    print(f"系统代理: {sys_proxy or '无'}　|　待测出口: {[e or '直连' for e in egresses]}")
    print(f"候选源 {len(cands)} 个，并发 {args.workers}，每源尝试 {args.retries} 次")
    print()

    results: List[Dict[str, Any]] = []
    for eg in egresses:
        with futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(probe_one, c, eg, 20, args.retries) for c in cands]
            for f in futures.as_completed(futs):
                results.append(f.result())

    ok_ids: Dict[str, Dict[str, Any]] = {}
    for r in results:
        if r["http"] == 200 and r["items"] > 0:
            ok_ids.setdefault(r["id"], r)

    for eg in egresses:
        rows = [r for r in results if (r["proxy"] or "直连") == (eg or "直连")]
        good = [r for r in rows if r["http"] == 200 and r["items"] > 0]
        print("─" * 108)
        print(f"出口 {eg or '直连'}　可用 {len(good)}/{len(rows)}")
        print("─" * 108)
        for r in sorted(rows, key=lambda x: (x["http"] != 200 or x["items"] == 0, x["zone"], x["id"])):
            flag = "✅" if (r["http"] == 200 and r["items"] > 0) else ("⚠️" if r["http"] == 200 else "⛔")
            http = r["http"] if r["http"] is not None else "—"
            latest = (r["latest"] or "—")[:24]
            print(f"  {flag} {r['id']:20s} {r['zone']:14s} {r['tier']:3s} http={str(http):5s} "
                  f"条={r['items']:4d} {r['bytes']:8d}B {r['ms']:6d}ms srv={r['server']:22s} 最新={latest}")
            if r["err"]:
                print(f"       └ {r['err']}")
            elif r["note"]:
                print(f"       └ {r['note'][:100]}")
        print()

    print("=" * 108)
    print(f"至少在一个出口可用的源：{len(ok_ids)} 个")
    print("=" * 108)
    by_zone: Dict[str, List[str]] = {}
    for r in ok_ids.values():
        by_zone.setdefault(r["zone"], []).append(r["id"])
    for z in sorted(by_zone):
        print(f"  {z:16s} ({len(by_zone[z]):2d}) {sorted(by_zone[z])}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
        print(f"\n结果已写入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
