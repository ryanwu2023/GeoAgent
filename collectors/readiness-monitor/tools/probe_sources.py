#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量源可达性探针 —— 建源阶段第一步（crawler-source-triage 四层模型）。

对每个候选 URL 输出：
  HTTP 状态 · Server 头 · 内容类型 · 条目数 · 源内最新日期 · 距今天数 · 正文长度
并按四层模型给出判定：网络层 / 风控层 / 源可用层 / 解析层。

必看三件事：
  1) 出口代理 —— 每次运行都打印，端口会漂移，绝不写死
  2) Server 头 —— AkamaiGHost / cloudflare 说明是 CDN 层，不是站点层
  3) 「源内最新日期距今天数」—— 200 + 合法 XML + 30 条，但内容是三周前的，
     这是最容易被忽略的一层（停更）

用法:
    python tools/probe_sources.py                 # 跑全部内置候选
    python tools/probe_sources.py --group iran    # 只跑某一组
    python tools/probe_sources.py --url https://... --label mytest
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import socket
import sys
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timezone

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 浏览器常规头组合 —— 只带 UA 会被 Akamai 拦（实测 403 → 200 的开关就是 Sec-Fetch-*）
RSS_HEADERS = {
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

JSON_HEADERS = dict(RSS_HEADERS, **{
    "Accept": "application/json, text/plain, */*",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
})

DATE_RES = [
    re.compile(r"<pubDate>([^<]+)</pubDate>", re.I),
    re.compile(r"<updated>([^<]+)</updated>", re.I),
    re.compile(r"<published>([^<]+)</published>", re.I),
    re.compile(r"<dc:date>([^<]+)</dc:date>", re.I),
    re.compile(r'"(?:date|published_at|date_gmt|pubDate|created_at)"\s*:\s*"([^"]+)"'),
]
ITEM_RE = re.compile(r"<(?:item|entry)\b", re.I)


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


def parse_dt(s: str):
    s = s.strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s.replace("GMT", "+0000").strip(), fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def fetch(url: str, timeout: int = 30, is_json: bool = False):
    hdrs = JSON_HEADERS if is_json else RSS_HEADERS
    req = urllib.request.Request(url, headers=hdrs)
    ctx = None
    try:
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()
        except Exception:  # noqa: BLE001
            pass
        return {"status": e.code, "server": e.headers.get("Server", ""),
                "ctype": e.headers.get("Content-Type", ""),
                "body": decompress(body, e.headers.get("Content-Encoding", "")),
                "err": None}
    except Exception as e:  # noqa: BLE001
        return {"status": None, "server": "", "ctype": "", "body": b"",
                "err": f"{type(e).__name__}: {e}"}
    raw = resp.read()
    return {"status": resp.status, "server": resp.headers.get("Server", ""),
            "ctype": resp.headers.get("Content-Type", ""),
            "body": decompress(raw, resp.headers.get("Content-Encoding", "")),
            "err": None}


def analyze(url: str, is_json: bool = False, timeout: int = 30) -> dict:
    r = fetch(url, timeout=timeout, is_json=is_json)
    out = {"url": url, "status": r["status"], "server": r["server"],
           "ctype": r["ctype"], "bytes": len(r["body"]), "items": 0,
           "latest": None, "age_days": None, "verdict": "", "err": r["err"]}
    if r["status"] is None:
        out["verdict"] = f"①网络层失败 —— {r['err']}"
        return out
    if r["status"] in (401, 403, 406, 429):
        out["verdict"] = f"②风控层 —— HTTP {r['status']} / Server={r['server'] or '?'}"
        return out
    if r["status"] == 404:
        out["verdict"] = f"③源可用层 —— HTTP 404（URL 参数可能已过期）"
        return out
    if r["status"] != 200:
        out["verdict"] = f"HTTP {r['status']}"
        return out

    txt = ""
    try:
        txt = r["body"].decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        txt = r["body"].decode("latin-1", "replace")

    out["items"] = len(ITEM_RE.findall(txt))
    if not out["items"] and is_json:
        try:
            j = json.loads(txt)
            if isinstance(j, list):
                out["items"] = len(j)
            elif isinstance(j, dict):
                for k in ("data", "items", "results", "records", "articles"):
                    if isinstance(j.get(k), list):
                        out["items"] = len(j[k])
                        out["json_key"] = k
                        break
                out["json_top_keys"] = list(j.keys())[:12]
        except Exception:  # noqa: BLE001
            pass

    dates = []
    for rx in DATE_RES:
        for m in rx.findall(txt):
            dt = parse_dt(m)
            if dt:
                dates.append(dt)
    if dates:
        latest = max(dates)
        out["latest"] = latest.isoformat()
        out["age_days"] = (datetime.now(timezone.utc) - latest).days

    if out["items"] == 0 and not dates:
        head = txt[:200].replace("\n", " ")
        out["verdict"] = f"④解析层 —— 200 但未识别到条目/日期。首200字: {head}"
    elif out["age_days"] is not None and out["age_days"] > 14:
        out["verdict"] = f"③源可用层 —— ⚠️ 停更 {out['age_days']} 天"
    else:
        out["verdict"] = (f"✅ 可用（{out['items']} 条，最新 "
                          f"{str(out.get('age_days'))} 天前）")
    return out


# ---------------------------------------------------------------- 候选源
CANDIDATES = [
    # ---------- 美方：CENTCOM ----------
    ("us", "centcom-press",
     "https://www.centcom.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1060&max=50"),
    ("us", "centcom-press-alt",
     "https://www.centcom.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1060&max=50&isdashboardselected=0"),
    ("us", "centcom-releases",
     "https://www.centcom.mil/MEDIA/PRESS-RELEASES/"),
    ("us", "centcom-statements",
     "https://www.centcom.mil/MEDIA/STATEMENTS/"),
    # ---------- 美方：DoD / Pentagon ----------
    ("us", "dod-news",
     "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=50"),
    ("us", "dod-releases",
     "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max=50"),
    ("us", "dod-transcripts",
     "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=13&Site=945&max=50"),
    ("us", "dod-contracts",
     "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=3&Site=945&max=50"),
    ("us", "dod-statements",
     "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=4&Site=945&max=50"),
    ("us", "dod-rss-list",
     "https://www.defense.gov/About/RSS/"),
    # ---------- 美方：政策/立法层 ----------
    ("us", "whitehouse-actions",
     "https://www.whitehouse.gov/presidential-actions/feed/"),
    ("us", "federal-register-dod",
     "https://www.federal-register.gov/api/v1/documents.json?conditions%5Bagencies%5D%5B%5D=department-of-defense&per_page=40&order=newest"),
    # ---------- 美方：媒体（T3，作线索） ----------
    ("us", "usni-news", "https://news.usni.org/feed"),
    ("us", "defensenews-pentagon",
     "https://www.defensenews.com/arc/outboundfeeds/rss/category/pentagon/"),
    ("us", "breakingdefense", "https://breakingdefense.com/feed/"),
    ("us", "airspaceforces", "https://www.airandspaceforces.com/feed/"),
    ("us", "stripes-middleeast",
     "https://www.stripes.com/arc/outboundfeeds/rss/category/middle-east/"),
    ("us", "reuters-world",
     "https://feeds.reuters.com/reuters/worldNews"),
    # ---------- 伊朗侧 ----------
    ("iran", "sepahnews-home", "https://sepahnews.ir/"),
    ("iran", "sepahnews-en", "https://english.sepahnews.ir/"),
    ("iran", "farsnews-rss", "https://www.farsnews.ir/rss"),
    ("iran", "farsnews-en-rss", "https://www.farsnews.ir/en/rss"),
    ("iran", "tasnim-en-rss",
     "https://www.tasnimnews.com/en/rss/feed/0/7/0/all-news"),
    ("iran", "tasnim-fa-rss", "https://www.tasnimnews.com/fa/rss/feed/0/7/0/all-news"),
    ("iran", "irna-en-rss", "https://en.irna.ir/rss"),
    ("iran", "irna-fa-rss", "https://www.irna.ir/rss"),
    ("iran", "mehr-en-rss", "https://en.mehrnews.com/rss"),
    ("iran", "presstv-rss", "https://www.presstv.ir/rss.xml"),
    ("iran", "khamenei-en-rss", "https://english.khamenei.ir/rss"),
    ("iran", "iran-mfa-en", "https://en.mfa.gov.ir/"),
    ("iran", "iran-mfa-rss", "https://en.mfa.gov.ir/rss"),
    ("iran", "tehrantimes-rss", "https://www.tehrantimes.com/rss"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="all", help="us|iran|all")
    ap.add_argument("--url", help="只探一个 URL")
    ap.add_argument("--label", default="custom")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--json", dest="is_json", action="store_true",
                    help="按 JSON API 解析（否则按 RSS/HTML）")
    args = ap.parse_args()

    auto = urllib.request.getproxies()
    print("=" * 100)
    print(f"实际出口代理: {auto.get('https') or auto.get('http') or '无（直连）'}")
    print("=" * 100)

    if args.url:
        targets = [(args.label, args.url, args.url)]
    else:
        targets = [c for c in CANDIDATES
                   if args.group == "all" or c[0] == args.group]

    results = []
    for grp, label, url in targets:
        is_json = args.is_json or ("api/" in url and url.endswith(".json")) \
            or "api/v1/documents.json" in url
        r = analyze(url, is_json=is_json, timeout=args.timeout)
        r["group"], r["label"] = grp, label
        results.append(r)
        print(f"\n[{grp}] {label}")
        print(f"    URL    : {url}")
        print(f"    status : {r['status']}  server={r['server'] or '-'}  "
              f"bytes={r['bytes']}  ctype={r['ctype'][:60] or '-'}")
        print(f"    items  : {r['items']}   latest={r['latest'] or '-'}  "
              f"age={r['age_days'] if r['age_days'] is not None else '-'}天")
        print(f"    判定   : {r['verdict']}")

    print("\n" + "=" * 100)
    ok = [r for r in results if r["verdict"].startswith("✅")]
    print(f"汇总: {len(ok)}/{len(results)} 可用")
    for r in results:
        flag = "✅" if r["verdict"].startswith("✅") else "❌"
        print(f"  {flag} [{r['group']:4}] {r['label']:22} "
              f"status={str(r['status']):5} {r['verdict'][:58]}")

    out = ".probe/probe_sources_result.json"
    try:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
        print(f"\n结果已写入 {out}")
    except Exception as e:  # noqa: BLE001
        print(f"\n写结果失败: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
