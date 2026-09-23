#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JSON API 探针 —— crawler-source-triage 四层模型的补充件。

原 probe.py 只处理 GET + XML/RSS，遇到 JSON API（尤其 POST）会误报
「内容不是 XML ❌」。本脚本补上这一块：
  * 支持 GET / POST(JSON body)
  * 识别 application/json 并打印顶层键、数组长度
  * 从响应里递归找形如 2026-09-14 / 2026-09-14T.. 的日期，报「最新日期距今天数」
  * 打印出口代理（每次都要看，端口会变）

用法:
    python probe_api.py --post https://api.usaspending.gov/api/v2/... --body body.json --label usaspending
    python probe_api.py --get https://api.fiscaldata.treasury.gov/...
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import date, datetime

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "Connection": "keep-alive",
}

DATE_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")


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
        return raw
    return raw


def walk_dates(obj, found: list) -> None:
    if isinstance(obj, dict):
        for v in obj.values():
            walk_dates(v, found)
    elif isinstance(obj, list):
        for v in obj:
            walk_dates(v, found)
    elif isinstance(obj, str):
        found.extend(DATE_RE.findall(obj))


def summarize(obj, depth: int = 0) -> None:
    pad = "  " * depth
    if isinstance(obj, dict):
        keys = list(obj.keys())
        print(f"{pad}{{}} keys={keys[:12]}{'...' if len(keys) > 12 else ''}")
        for k in keys[:6]:
            v = obj[k]
            if isinstance(v, (dict, list)):
                print(f"{pad}  .{k}:")
                summarize(v, depth + 2)
            else:
                print(f"{pad}  .{k} = {str(v)[:110]}")
    elif isinstance(obj, list):
        print(f"{pad}[] len={len(obj)}")
        if obj:
            summarize(obj[0], depth + 1)


def probe(url: str, label: str, body: dict | None, timeout: int = 30) -> bool:
    print("=" * 78)
    print(f"[{label}] {'POST' if body else 'GET'} {url[:150]}")
    auto = urllib.request.getproxies()
    print(f"  出口: {auto.get('https') or auto.get('http') or '无，直连'}")

    data = json.dumps(body).encode() if body else None
    headers = dict(HEADERS)
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=ssl.create_default_context()) as resp:
            raw = resp.read()
            raw = decompress(raw, resp.headers.get("Content-Encoding", ""))
            ct = resp.headers.get("Content-Type", "")
            print(f"  ② 风控层  HTTP {resp.status}  Server={resp.headers.get('Server','-')}  "
                  f"CT={ct[:60]}  {len(raw):,} 字节")
            text = raw.decode("utf-8", "ignore")
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                print(f"  ④ 解析层  非 JSON，开头: {text[:180]!r}")
                return False
            print("  ④ 解析层  JSON 顶层结构:")
            summarize(obj)
            found = []
            walk_dates(obj, found)
            if found:
                ds = sorted({f"{a}-{b}-{c}" for a, b, c in found})[-5:]
                latest = max(f"{a}-{b}-{c}" for a, b, c in found)
                lag = (date.today() - date.fromisoformat(latest)).days
                print(f"  ③ 源可用层  响应内日期样本 {ds} → 最新 {latest}（距今 {lag} 天）")
                if lag > 45:
                    print("     [!] 最新日期偏旧，注意源可能停更或需调整查询区间")
            else:
                print("  ③ 源可用层  响应内未发现日期")
            return True
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "ignore")
        print(f"  ✗ HTTP {e.code}  Server={e.headers.get('Server','-')}  body={detail!r}")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ {type(e).__name__}: {e}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--get", action="append", default=[])
    ap.add_argument("--post", action="append", default=[])
    ap.add_argument("--body", help="POST 用的 JSON 文件")
    ap.add_argument("--label", default="api")
    args = ap.parse_args()

    body = None
    if args.body:
        body = json.loads(open(args.body, encoding="utf-8").read())

    ok = 0
    for u in args.get:
        ok += probe(u, args.label, None)
    for u in args.post:
        ok += probe(u, args.label, body)
    print("=" * 78)
    print(f"成功 {ok} / {len(args.get) + len(args.post)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
