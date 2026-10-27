#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
meeting-calendar-monitor — 俄乌 / 美伊关键人物会晤日程监测

要回答的问题只有一个：**什么时候、在哪、谁和谁、见没见成。**

与其他新闻监测器的区别（也是本文件所有设计的出发点）：
  1. 产出是**日程表**，不是新闻流。一行 = 一个会晤/会议，不是一个报道。
  2. 所以必须做**实体归并 + 状态机**：同一条会晤会被几十家媒体重复报道，
     必须收敛成一行，并且随每日抓取更新「确认度」。
  3. "会晤没开成"和"会晤开了但没人报"在公开源上**长得一模一样**。
     因此「过期未证实」是一等状态，绝不静默删除。

口径纪律（与全库一致）：
  - 采集了就必须消费：signals 落盘、reject 原因进报告、状态变化进台账。
  - 只给计数不给日期等于没报：缺口、变更一律带日期。
  - 日期解析失败 = 双重静默损失：要么明确标 tbd，要么拒收并记账，不猜。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

BASE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.environ.get("COLLECTOR_OUTPUT_ROOT", os.path.join(BASE, "output"))
STATE = os.environ.get("COLLECTOR_STATE_ROOT", os.path.join(BASE, "state"))
CFG_PATH = os.path.join(BASE, "config.json")

CST = timezone(timedelta(hours=8))
TODAY: date = datetime.now(CST).date()
RUN_AT = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
PROXY_OVERRIDE: Optional[str] = None
VERBOSE = False

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ====================================================================== 基础

def log(msg: str) -> None:
    if VERBOSE:
        print(msg, flush=True)


def today_iso() -> str:
    return TODAY.isoformat()


def d(s: str) -> Optional[date]:
    try:
        return date.fromisoformat(s) if s else None
    except (ValueError, TypeError):
        return None


def iso(x: Optional[date]) -> str:
    return x.isoformat() if x else ""


def read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def append_jsonl(path: str, rows: Iterable[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def merge_jsonl(path: str, rows: Iterable[dict], key=("title", "link")) -> int:
    """当日快照合并：同一 (标题, 链接) 只留一条，后一次运行覆盖前一次。

    为什么不能直接 append：同一天跑多次（调试、补跑、定时+手动）时行数会翻倍——
    实测同日 3 次运行后 signals.jsonl 有 754 行、去重后只有 148 条。
    审计的人若把「行数」当「条数」读，信号量会被高估一个数量级，
    而文件本身看起来完全正常（这比报错更危险）。返回合并后唯一行数。
    """
    merged: Dict[tuple, dict] = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue          # 半行（上次被杀）跳过，不让它污染整份快照
                merged[tuple(str(r.get(k, "")) for k in key)] = r
    for r in rows:
        merged[tuple(str(r.get(k, "")) for k in key)] = r
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in merged.values():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)         # 原子替换：中途失败不会留下半份快照
    return len(merged)


def norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def strip_html(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
          .replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">")
          .replace("&apos;", "'"))
    s = re.sub(r"&#\d+;", " ", s)
    return norm_space(s)


def clamp(s: str, n: int) -> str:
    s = norm_space(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def is_ascii(s: str) -> bool:
    return all(ord(c) < 128 for c in s)


def contains_phrase(text: str, phrase: str) -> bool:
    """词级匹配。ASCII 词条加前后非字母数字边界，避免 Rais 命中 Raisi、
    Trump 命中 Trumpism；非 ASCII 词条直接子串匹配（中文/波斯语无词边界）。"""
    if not text or not phrase:
        return False
    if is_ascii(phrase):
        pat = r"(?<![A-Za-z0-9])" + re.escape(phrase) + r"(?![A-Za-z0-9])"
        return re.search(pat, text, re.I) is not None
    return phrase in text


# ★ 否定语境守卫。典型假阳性：「She has not met with the Russian Federation」
#   被判成「已举行」。不加守卫时，这类句子会把整条事件翻成 held。
_NEG_TAIL = re.compile(
    r"(?:\bnot|\bnever|\bno longer|\bwithout|\bdenied|\bdenies|\bruled out|\bdismissed)\b[^.]{0,24}$", re.I)


def phrase_hit(text: str, phrase: str) -> bool:
    """带否定语境的词级匹配：命中处前 28 字符内出现否定词则跳过。"""
    if not text or not phrase:
        return False
    if not is_ascii(phrase):
        return phrase in text
    pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(phrase) + r"(?![A-Za-z0-9])", re.I)
    for m in pat.finditer(text):
        pre = text[max(0, m.start() - 28): m.start()]
        if _NEG_TAIL.search(pre):
            continue
        return True
    return False


def fingerprint(cfg: dict) -> str:
    raw = json.dumps(cfg, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


# ====================================================================== 网络

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml,application/atom+xml,application/xml,"
              "text/xml,application/json,text/html,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

_EGRESS_CACHE: Dict[str, Any] = {"at": 0.0, "ok": []}
_EGRESS_TTL = 600.0
_OPENERS: Dict[str, Any] = {}
_HOST_LAST: Dict[str, float] = {}
_SOURCE_HEALTH: List[dict] = []

# 深探必须用**真实目标**：通用探活站 200 只能证明端口通，证明不了这个站能抓。
_DEEP_PROBES = (
    "https://news.google.com/rss/search?q=probe&hl=en-US&gl=US&ceid=US:en",
    "https://www.state.gov/rss-feed/press-releases/feed/",
)


def proxy_candidates() -> List[str]:
    """候选出口：环境变量优先，常见本地端口次之。端口会漂移，绝不写死。"""
    out: List[str] = []
    try:
        env = urllib.request.getproxies()
    except Exception:  # noqa: BLE001
        env = {}
    for k in ("https", "http"):
        u = env.get(k)
        if isinstance(u, str) and u.startswith("http") and u not in out:
            out.append(u)
    for port in (7897, 7890, 10809, 1080, 8080, 8888, 4780, 2080, 20171, 33210):
        u = f"http://127.0.0.1:{port}"
        if u not in out:
            out.append(u)
    return out


def _opener(proxy: Optional[str]):
    key = proxy or ""
    if key not in _OPENERS:
        if proxy:
            _OPENERS[key] = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        else:
            _OPENERS[key] = urllib.request.build_opener(
                urllib.request.ProxyHandler({}))
    return _OPENERS[key]


def _alive(proxy: Optional[str], timeout: int = 5) -> bool:
    for probe in ("http://www.gstatic.com/generate_204",
                  "https://www.cloudflare.com/cdn-cgi/trace"):
        try:
            r = _opener(proxy).open(
                urllib.request.Request(probe, headers=BROWSER_HEADERS), timeout=timeout)
            if r.status in (200, 204):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _deep_score(proxy: Optional[str], timeout: int = 6) -> int:
    """真实目标深探。★探针必须带与抓取相同的头集，否则会把可用出口判成 0 分。"""
    good = 0
    for probe in _DEEP_PROBES:
        try:
            r = _opener(proxy).open(
                urllib.request.Request(probe, headers=BROWSER_HEADERS), timeout=timeout)
            if r.status == 200:
                good += 1
        except Exception:  # noqa: BLE001
            pass
    return good


def live_egress() -> List[Optional[str]]:
    if PROXY_OVERRIDE:
        return [None if PROXY_OVERRIDE == "direct" else PROXY_OVERRIDE]
    now = time.time()
    if _EGRESS_CACHE["ok"] and now - _EGRESS_CACHE["at"] < _EGRESS_TTL:
        return list(_EGRESS_CACHE["ok"])
    scored: List[Tuple[int, str]] = []
    for u in proxy_candidates():
        if not _alive(u):
            continue
        scored.append((-_deep_score(u), u))
    scored.sort(key=lambda x: (x[0], x[1]))
    ok: List[Optional[str]] = [u for _, u in scored] or [None]
    _EGRESS_CACHE.update({"at": now, "ok": ok})
    return list(ok)


_ROTATE_HINTS = ("tunnel connection failed", "10060", "10054", "10053",
                 "remote end closed", "connection reset", "connection aborted",
                 "timed out", "timeouterror", "eof occurred")


def _rotatable(err: Exception) -> bool:
    s = f"{type(err).__name__}: {err}".lower()
    return any(h in s for h in _ROTATE_HINTS)


def _decompress(raw: bytes, enc: str = "") -> bytes:
    """★ urllib **不会**自动解压 gzip（curl 会，所以探测脚本看不出来）。
    不解压的后果不是报错，而是 HTTP 200 + 字节数正常 + 解析 0 条 ——
    报告里显示「✅ 0 条」，看起来像个空源。这是本类项目最阴的一种假成功。
    """
    enc = (enc or "").lower()
    if raw[:2] == b"\x1f\x8b" or "gzip" in enc:
        try:
            import gzip as _gz
            return _gz.decompress(raw)
        except OSError:
            pass
    if "deflate" in enc:
        try:
            import zlib
            return zlib.decompress(raw)
        except zlib.error:
            try:
                import zlib
                return zlib.decompress(raw, -zlib.MAX_WBITS)
            except zlib.error:
                pass
    return raw


def http_get(url: str, timeout: int = 25, accept_bytes: bool = True,
             egress: str = "auto") -> Tuple[Optional[bytes], str]:
    """返回 (bytes|None, 备注)。失败不抛异常——一个源挂了不该中断整轮。

    egress="direct" 表示**该源先直连、失败再退回代理**。
    ★ 踩过的坑：state.gov 挂在 CloudFront 后面，对代理出口 IP 返回 407
    （无 Proxy-Authenticate，说明是源站/CDN 拒绝，不是代理要认证），
    而直连同 URL 是 200。全局只走代理时，这个 T1 源会整段静默缺失。
    反过来 Google News 直连会超时，所以不能全局改直连 —— 必须做成源级偏好。
    """
    hosts = live_egress()
    if egress == "direct":
        hosts = [None] + [h for h in hosts if h is not None]
    last = "unknown"
    for attempt, proxy in enumerate(hosts[:3]):
        host = urllib.parse.urlparse(url).netloc
        gap = time.time() - _HOST_LAST.get(host, 0)
        if gap < 1.0:
            time.sleep(1.0 - gap)
        _HOST_LAST[host] = time.time()
        try:
            r = _opener(proxy).open(
                urllib.request.Request(url, headers=BROWSER_HEADERS), timeout=timeout)
            raw = r.read()
            if r.status != 200:
                return None, f"HTTP {r.status}"
            if not raw:
                return None, "空响应"
            body = _decompress(raw, r.headers.get("Content-Encoding", ""))
            if not body:
                return None, "解压后为空"
            return body, "ok"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (403, 401, 404, 410, 451):
                return None, last
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {str(e)[:80]}"
            if not _rotatable(e) or attempt == len(hosts[:3]) - 1:
                return None, last
    return None, last


# ====================================================================== 源解析

_ATOM_LINK_RE = re.compile(r'<link[^>]+href="([^"]+)"', re.I)


def parse_pub_date(s: str) -> Optional[date]:
    """RFC822 / ISO8601。★两位年份必须在 strptime 前展开，否则 26 → 公元 26 年。"""
    if not s:
        return None
    s = norm_space(s)
    s = re.sub(r"\b(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{2})\b", r"\1 \2 20\3", s)
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(s)
        if dt is None:
            return None
        out = dt.date() if dt.tzinfo else dt.replace(tzinfo=timezone.utc).date()
    except Exception:  # noqa: BLE001
        try:
            out = datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    # Google News 对无日期条目填 Unix 纪元零值 —— 那不是「很旧的日期」，是「日期未知」
    if out.year <= 1970 or out.year < 2000 or out.year > TODAY.year + 3:
        return None
    return out


def parse_feed(raw: bytes) -> List[dict]:
    txt = raw.decode("utf-8", "replace").lstrip("\ufeff")
    if "<rss" not in txt.lower() and "<feed" not in txt.lower():
        return []
    try:
        root = ET.fromstring(txt)
    except ET.ParseError:
        return []
    out: List[dict] = []
    for node in root.iter():
        tag = node.tag.split("}")[-1].lower()
        if tag not in ("item", "entry"):
            continue
        rec: Dict[str, str] = {}
        for ch in node:
            ctag = ch.tag.split("}")[-1].lower()
            if ctag == "link":
                rec["link"] = (ch.get("href") or (ch.text or "")).strip()
            elif ctag in ("title", "summary", "description", "content", "published", "updated", "pubdate", "date"):
                rec[ctag] = ch.text or ""
        if not rec.get("link"):
            m = _ATOM_LINK_RE.search(ET.tostring(node, encoding="unicode"))
            if m:
                rec["link"] = m.group(1)
        title = strip_html(rec.get("title", ""))
        if not title:
            continue
        out.append({
            "title": title,
            "link": rec.get("link", ""),
            "desc": strip_html(rec.get("description") or rec.get("summary") or rec.get("content") or ""),
            "published": parse_pub_date(rec.get("pubdate") or rec.get("published") or rec.get("updated") or rec.get("date") or ""),
        })
    return out


def parse_kremlin_html(raw: bytes) -> List[dict]:
    """克里姆林宫日程页没有可用 RSS（/rss 全 404），只能从列表页取标题+链接。
    这里只取「标题 + 链接」，日期从 URL（/events/president/news/12345 无日期）
    取不到，因此一律标记 published=None，由正文标题决定状态。"""
    txt = raw.decode("utf-8", "replace")
    out: List[dict] = []
    for m in re.finditer(r'<a[^>]+href="(/events/president/news/\d+)"[^>]*>(.*?)</a>', txt, re.S):
        title = strip_html(m.group(2))
        if title and len(title) > 6:
            out.append({"title": title, "link": "http://en.kremlin.ru" + m.group(1),
                        "desc": "", "published": None})
    return out


# ====================================================================== 日期抽取

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DASH = r"(?:-|–|—|to|until)"

_MON_FIRST_RANGE = re.compile(_MON + r"\.?\s+(\d{1,2})\s*" + _DASH + r"\s*(\d{1,2})(?:\s*,?\s*(\d{4}))?", re.I)
_MON_FIRST = re.compile(_MON + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{4}))?", re.I)
_DAY_FIRST_RANGE = re.compile(r"(?<!\d)(\d{1,2})\s*" + _DASH + r"\s*(\d{1,2})\s+" + _MON + r"\.?(?:\s*,?\s*(\d{4}))?", re.I)
_DAY_FIRST = re.compile(r"(?<!\d)(\d{1,2})\s+" + _MON + r"\.?(?:\s*,?\s*(\d{4}))?", re.I)
_MON_YEAR = re.compile(_MON + r"\.?,?\s+(\d{4})", re.I)
_REL_WEEK = re.compile(r"\bnext\s+week\b", re.I)
_REL_MONTH = re.compile(r"\bnext\s+month\b", re.I)
_REL_TOMORROW = re.compile(r"\btomorrow\b", re.I)
_REL_WEEKEND = re.compile(r"\b(this|next)\s+weekend\b", re.I)
_MON_QUALIFIED = re.compile(r"\b(?:in|early|mid|late|next|this|by)\s+" + _MON + r"\b", re.I)


def _valid(y: int, m: int, dd: int) -> Optional[date]:
    try:
        return date(y, m, dd)
    except ValueError:
        return None


def infer_year(month: int, published: date, explicit: Optional[int]) -> int:
    if explicit:
        return explicit
    y = published.year
    # 12 月的稿子说 "January" 必是明年；10 月的稿子说 "March" 也是明年
    if month < published.month - 6:
        y += 1
    return y


def parse_date_text(text: str, published: date) -> Optional[dict]:
    """返回 {start,end,precision,raw}。解析不出来返回 None —— 绝不猜。"""
    if not text:
        return None

    def mk(y, m1, d1, d2=None, raw=""):
        s = _valid(y, m1, d1)
        if not s:
            return None
        if d2 and d2 != d1:
            e = _valid(y, m1, d2)
            if e and e >= s:
                return {"start": s, "end": e, "precision": "range", "raw": raw}
        return {"start": s, "end": s, "precision": "day", "raw": raw}

    m = _MON_FIRST_RANGE.search(text)
    if m:
        mon = MONTHS[m.group(1).lower()]
        y = infer_year(mon, published, int(m.group(4)) if m.group(4) else None)
        r = mk(y, mon, int(m.group(2)), int(m.group(3)), m.group(0))
        if r:
            return r

    m = _DAY_FIRST_RANGE.search(text)
    if m:
        mon = MONTHS[m.group(3).lower()]
        y = infer_year(mon, published, int(m.group(4)) if m.group(4) else None)
        r = mk(y, mon, int(m.group(1)), int(m.group(2)), m.group(0))
        if r:
            return r

    m = _MON_FIRST.search(text)
    if m:
        mon = MONTHS[m.group(1).lower()]
        # "May" 作情态动词时几乎都是小写且后接动词；有数字才算日期
        y = infer_year(mon, published, int(m.group(3)) if m.group(3) else None)
        r = mk(y, mon, int(m.group(2)), None, m.group(0))
        if r:
            return r

    m = _DAY_FIRST.search(text)
    if m:
        mon = MONTHS[m.group(2).lower()]
        y = infer_year(mon, published, int(m.group(3)) if m.group(3) else None)
        r = mk(y, mon, int(m.group(1)), None, m.group(0))
        if r:
            return r

    m = _REL_WEEK.search(text)
    if m:
        monday = published + timedelta(days=(7 - published.weekday()))
        return {"start": monday, "end": monday + timedelta(days=6),
                "precision": "week", "raw": m.group(0)}

    m = _REL_MONTH.search(text)
    if m:
        y, mo = (published.year + 1, 1) if published.month == 12 else (published.year, published.month + 1)
        start = date(y, mo, 1)
        end = date(y + (mo == 12), (mo % 12) + 1, 1) - timedelta(days=1)
        return {"start": start, "end": end, "precision": "month", "raw": m.group(0)}

    m = _REL_WEEKEND.search(text)
    if m:
        # 下一个周六（含当天）
        add = (5 - published.weekday()) % 7
        sat = published + timedelta(days=add)
        return {"start": sat, "end": sat + timedelta(days=1), "precision": "range", "raw": m.group(0)}

    m = _REL_TOMORROW.search(text)
    if m:
        t = published + timedelta(days=1)
        return {"start": t, "end": t, "precision": "day", "raw": m.group(0)}

    m = _MON_YEAR.search(text)
    if m:
        mon = MONTHS[m.group(1).lower()]
        y = int(m.group(2))
        start = date(y, mon, 1)
        end = date(y + (mon == 12), (mon % 12) + 1, 1) - timedelta(days=1)
        return {"start": start, "end": end, "precision": "month", "raw": m.group(0)}

    m = _MON_QUALIFIED.search(text)
    if m:
        mon = MONTHS[m.group(1).lower()]
        y = infer_year(mon, published, None)
        start = date(y, mon, 1)
        end = date(y + (mon == 12), (mon % 12) + 1, 1) - timedelta(days=1)
        return {"start": start, "end": end, "precision": "month", "raw": m.group(0)}

    return None


# ====================================================================== 抽取

def _alias_hit(text: str, alias: str) -> bool:
    return contains_phrase(text, alias)


_LOC_BLOCK = {"times", "post", "examiner", "institute", "news", "university",
              "bureau", "journal", "tribune", "herald"}


def extract_location(text: str, locs: Sequence[dict]) -> Tuple[str, str]:
    """返回 (中文名, 原文片段)。★要排除 "Tehran Times" / "Washington Post" 这类
    媒体名——否则每篇《德黑兰时报》的稿子都会被判成「地点在德黑兰」。"""
    for loc in locs:
        for al in loc["aliases"]:
            if not _alias_hit(text, al):
                continue
            pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(al) + r"(?![A-Za-z0-9])", re.I)
            m = pat.search(text)
            if not m:
                continue
            after = text[m.end(): m.end() + 16].strip().lower()
            nxt = re.match(r"[a-z']+", after)
            if nxt and nxt.group(0) in _LOC_BLOCK:
                continue
            return loc["zh"], text[max(0, m.start() - 12): m.end() + 12]
    return "", ""


def extract_persons(text: str, persons: Sequence[dict]) -> Set[str]:
    hits: Set[str] = set()
    for p in persons:
        if any(_alias_hit(text, a) for a in p.get("aliases", [])):
            hits.add(p["id"])
    if hits:
        for p in persons:
            for w in p.get("weak", []):
                if _alias_hit(text, w):
                    hits.add(p["id"])
        return hits
    # 没有强命中时，弱别名需要职务/另一位人物作上下文
    ctx_words = ("minister", "president", "secretary", "spokesman", "spokesperson",
                 "leader", "prime minister", "official", "envoy", "adviser")
    low = text.lower()
    for p in persons:
        for w in p.get("weak", []):
            if not _alias_hit(text, w):
                continue
            for m in re.finditer(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])", text, re.I):
                window = low[max(0, m.start() - 70): m.end() + 70]
                if any(c in window for c in ctx_words):
                    hits.add(p["id"])
                    break
    return hits


def extract_actors(text: str, actors: Sequence[dict]) -> Set[str]:
    out: Set[str] = set()
    for a in actors:
        if any(_alias_hit(text, al) for al in a.get("aliases", [])):
            out.add(a["id"])
    return out


def classify_family(text: str, families: Sequence[dict], persons: Set[str], default: bool = True) -> str:
    for f in families:
        if any(phrase_hit(text, k) for k in f["keywords"]):
            return f["id"]
    if not default:
        return ""
    if len(persons) >= 3:
        return "trilateral"
    if len(persons) == 2:
        return "bilateral"
    return "talks"


_FUTURE_MARK = ("will ", "to meet", "to hold", "to visit", "upcoming", "next ",
                "plans", "plan ", "expect", "schedul", "set for", "due to", "in november",
                "in december", "in january", "ahead of")


def _status_in(text: str, allow_held: bool) -> Optional[Tuple[str, str, int]]:
    spec = STATUS_SPEC
    for code in ("canceled", "postponed", "denied"):
        for ph in spec[code]["phrases"]:
            if phrase_hit(text, ph):
                return code, ph, spec[code]["rank"]
    for code in ("scheduled", "confirmed", "proposed", "arranged", "reported"):
        for ph in spec[code]["phrases"]:
            if phrase_hit(text, ph):
                return code, ph, spec[code]["rank"]
    if allow_held:
        for ph in spec["held"]["phrases"]:
            if phrase_hit(text, ph):
                return "held", ph, spec["held"]["rank"]
    return None


_HEDGE = re.compile(r"\b(could|may|might|possible|possibly|open to|weighing|considering|speculat)\b", re.I)

# ★ 否定出席 ≠ 行程。实测一条 `Ghalibaf not to attend BRICS parliamentary summit`
#   被当成"卡利巴夫要出席"写进日程表 —— 读者会看到一条完全反着的安排。
_NOT_ATTEND = re.compile(
    r"\b(?:will\s+not|won'?t|is\s+not|are\s+not|not)\s+(?:to\s+)?"
    r"(?:attend|take\s+part|participate|join)\b"
    r"|\b(?:skips?|skipped|to\s+skip)\b",
    re.I)


def detect_status(headline: str, desc: str = "") -> Tuple[str, str, int]:
    """★ 标题优先，且 **「已举行」只允许来自标题**。

    原因（实测踩过）：国务院一篇长新闻稿里，只要有一句 "met with ..."，
    整条 —— 包括标题在讲「愿意举行三方会谈」的那条 —— 都会被打成 held。
    标题里的动词才决定这条新闻在讲什么；正文里的过去式多数是背景陈述。

    ★ 情态动词降级（2026-10-24 补）：标题 "Kremlin confirms Putin, Trump and Xi
      **could** hold a three-way meeting" 里确实有 confirms，但确认的是
      「有可能」，不是「已确认」。不降级就会把放风写成定案。
    """
    hit = _status_in(headline, allow_held=True)
    if hit:
        code, phrase, rank = hit
        if code in ("confirmed", "arranged") and _HEDGE.search(headline):
            return "proposed", phrase + "（情态降级）", STATUS_SPEC["proposed"]["rank"]
        return code, phrase, rank
    if _NOT_ATTEND.search(headline):
        return "denied", "not-attend", STATUS_SPEC["denied"]["rank"]
    if re.match(r"^\s*(meeting|talks|phone call|press conference|readout)\s+with\b", headline, re.I):
        return "held", "readout-title", STATUS_SPEC["held"]["rank"]
    hit = _status_in(desc, allow_held=False)
    if hit:
        return hit
    return "unconfirmed", "", 10


def is_opinion(title: str, excludes: Sequence[str]) -> bool:
    return any(p.lower() in title.lower() for p in excludes)


def strip_outlet(title: str) -> Tuple[str, str]:
    """Google News 标题形如 `Headline - Source`。返回 (headline, outlet)。"""
    m = re.match(r"^(.*?)\s+-\s+([^-]{2,40})$", title)
    if m and len(m.group(2).split()) <= 8:
        return norm_space(m.group(1)), norm_space(m.group(2))
    return title, ""


# ====================================================================== 事件模型

STATUS_SPEC: Dict[str, dict] = {}
STATUS_LABEL: Dict[str, str] = {}
STATUS_RANK: Dict[str, int] = {}
CFG: dict = {}

FAMILY_LABEL: Dict[str, str] = {}
PERSON_ZH: Dict[str, str] = {}
PERSON_ROLE: Dict[str, str] = {}
PERSON_COUNTRY: Dict[str, str] = {}
ACTOR_ZH: Dict[str, str] = {}
LOCS: List[dict] = []
PERSONS: List[dict] = []
ACTORS: List[dict] = []
FAMILIES: List[dict] = []

FAMILY_COMPAT = {"bilateral", "trilateral", "talks", "visit"}
NAMED_FAMILIES: Set[str] = set()

# 这三个状态回答的是「会不会开」，不是「哪天开」。
# ★ 它们不能留在「未来日程」里：一场已被否认/取消的会晤若还占着日历位置，
#   读者会把「没这回事」读成「有这事、只是日期没定」。单列。
NEG_STATUS = ("canceled", "postponed", "denied")


def status_cell(ev: dict) -> str:
    code = ev.get("status", "unconfirmed")
    label = STATUS_LABEL.get(code, code)
    if code == "lapsed":
        return "过期未证实"
    # ★ 顺序要紧：先判否定态，再判「有没有日期」。
    #   原实现把无日期一律渲染成「暂无确认日期」，于是一条 denied 事件
    #   （Ghalibaf not to attend ...）在表里显示为「暂无确认日期」——
    #   等于把否认改写成了待定。这是错的最厉害的一种。
    if code in NEG_STATUS:
        return label
    if not ev.get("date_start"):
        return "暂无确认日期"
    prec = ev.get("precision", "tbd")
    if prec == "month":
        return f"{label}（{ev['date_start'][:7]} 月内）"
    if prec == "week":
        return f"{label}（当周）"
    return label


def date_cell(ev: dict) -> str:
    s = ev.get("date_start") or ""
    if not s:
        return "待定"
    prec = ev.get("precision", "day")
    if prec == "range" and ev.get("date_end") and ev["date_end"] != s:
        return f"{s}—{ev['date_end'][5:]}"
    if prec == "month":
        return f"{s[:7]}（月内）"
    if prec == "week":
        return f"{s} 当周"
    return s


def persons_cell(ev: dict) -> str:
    ids = ev.get("persons") or []
    names = [PERSON_ZH.get(i, i) for i in ids]
    if names:
        return "、".join(names[:3]) + ("等" if len(names) > 3 else "")
    acc = [ACTOR_ZH.get(a, a) for a in (ev.get("actors") or [])]
    return ("、".join(acc) + "（未点名）") if acc else "未识别"


def sides_of(persons: Iterable[str], actors: Iterable[str], location_zh: str) -> Set[str]:
    out: Set[str] = set()
    for p in persons:
        c = PERSON_COUNTRY.get(p)
        if c:
            out.add(c)
    for a in actors:
        out.add(ACTOR_ZH.get(a, a))
    for loc in LOCS:
        if loc["zh"] == location_zh:
            out.add(loc["country"])
            break
    return out


def build_title(family: str, persons: Sequence[str], location: str, raw: str,
                anchored: bool = False) -> str:
    """★ 只用「核心人物」生成标题，不用全集。

    原先把合并后的并集直接拼标题，于是 {普京, 特朗普, 习近平, 泽连斯基}
    会渲染成「四方会晤」—— 而原始报道说的其实是「三方峰会，且明确排除泽连斯基」。
    人名并集 ≠ 同一张桌子旁坐着的人。

    ★ 命名峰会类但**没有**落到基线的（例如「G20 财长会议」），保留原始标题。
      否则一律写成「G20 峰会」，会把财长会、筹备会、领导人峰会混为一谈。
    """
    if family in FAMILY_LABEL and family not in FAMILY_COMPAT:
        if anchored:
            return FAMILY_LABEL[family]
        return clamp(raw, 46) if raw else FAMILY_LABEL[family]
    n = 3 if family == "trilateral" else 2
    persons = list(persons)[:n]
    if len(persons) == 2:
        return f"{PERSON_ZH.get(persons[0], persons[0])}—{PERSON_ZH.get(persons[1], persons[1])} 会晤"
    if len(persons) >= 3:
        return "、".join(PERSON_ZH.get(p, p) for p in persons) + " 会晤"
    if len(persons) == 1:
        return f"{PERSON_ZH.get(persons[0], persons[0])} 出访/会晤"
    if location:
        return f"{location}会谈"
    return clamp(raw, 48)


# ====================================================================== 抓取→信号

def source_items(cfg: dict, day_dir: str) -> Tuple[List[dict], List[dict]]:
    items: List[dict] = []
    health: List[dict] = []
    raw_dir = os.path.join(day_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    for src in cfg["sources"]:
        if not src.get("enabled", True):
            continue
        if src["kind"] == "query":
            continue
        t0 = time.time()
        body, note = http_get(src["url"], cfg["request_timeout"],
                              egress=src.get("egress", "auto"))
        rec = {"source": src["id"], "name": src["name"], "tier": src["tier"],
               "url": src["url"], "ok": body is not None, "note": note,
               "count": 0, "latest": "", "seconds": round(time.time() - t0, 1)}
        if body is None:
            health.append(rec)
            log(f"[src] {src['id']:14s} FAIL {note}")
            continue
        # 只有 HTTP 200 才留原始载荷 —— 这是「可复算」的物理基础
        with open(os.path.join(raw_dir, f"{src['id']}.xml"), "wb") as f:
            f.write(body)
        parsed = parse_kremlin_html(body) if src["kind"] == "html" else parse_feed(body)
        if not parsed and len(body) > 300:
            # 200 + 有字节 + 0 条 = 结构性失败，必须当失败报，不能显示成「空源」
            rec.update({"ok": False, "note": f"200 但解析 0 条（{len(body)}B，编码或结构变化）"})
            health.append(rec)
            log(f"[src] {src['id']:14s} PARSE-0 ({len(body)}B)")
            continue
        for it in parsed:
            it["source_id"] = src["id"]
            it["source_name"] = src["name"]
            it["tier"] = src["tier"]
        items.extend(parsed)
        latest = max([it["published"] for it in parsed if it["published"]], default=None)
        rec.update({"count": len(parsed), "latest": iso(latest)})
        health.append(rec)
        log(f"[src] {src['id']:14s} {len(parsed):4d} 条  latest={iso(latest)}")

    for q in cfg["queries"]:
        url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q["q"]) +
               "&hl=en-US&gl=US&ceid=US:en")
        t0 = time.time()
        body, note = http_get(url, cfg["request_timeout"])
        rec = {"source": q["id"], "name": f"GNews:{q['id']}", "tier": 3,
               "url": url, "ok": body is not None, "note": note,
               "count": 0, "latest": "", "seconds": round(time.time() - t0, 1)}
        if body is None:
            health.append(rec)
            log(f"[q]   {q['id']:14s} FAIL {note}")
            continue
        with open(os.path.join(raw_dir, f"{q['id']}.xml"), "wb") as f:
            f.write(body)
        parsed = parse_feed(body)
        if not parsed and len(body) > 300:
            rec.update({"ok": False, "note": f"200 但解析 0 条（{len(body)}B，编码或结构变化）"})
            health.append(rec)
            log(f"[q]   {q['id']:14s} PARSE-0 ({len(body)}B)")
            continue
        for it in parsed:
            it["source_id"] = q["id"]
            it["source_name"] = "Google News"
            it["tier"] = 3
            it["query_track"] = q.get("track", "")
        items.extend(parsed)
        latest = max([it["published"] for it in parsed if it["published"]], default=None)
        rec.update({"count": len(parsed), "latest": iso(latest)})
        health.append(rec)
        log(f"[q]   {q['id']:14s} {len(parsed):4d} 条")

    return items, health


def build_signals(items: List[dict], cfg: dict) -> Tuple[List[dict], List[dict]]:
    """把原始条目转成信号。★拒收也要留记录（reject_reason），否则
    「为什么这条没进表」永远只能靠猜。

    ★ 分层抽取（2026-10-24 重构）：**标题优先，正文只做补全**。
      原先把 title+全文拼成一个字符串统一抽取，结果一篇 3000 字的国务院新闻稿
      里任意一句 "met with ..." 就能把整条判成「已举行」，任意一个城市名都能
      成为「会晤地点」。这不是精度问题，是**证据归属错位**：正文里的背景陈述
      被当成了标题事件的事实。
    """
    signals: List[dict] = []
    rejects: List[dict] = []
    seen: Set[str] = set()
    desc_n = int(cfg.get("report", {}).get("desc_scan_chars", 300))

    for it in items:
        title = it["title"]
        headline, outlet = strip_outlet(title)
        desc = clamp(it.get("desc") or "", desc_n)
        pub = it.get("published") or TODAY
        date_unknown = not it.get("published")

        base = {"title": title, "link": it.get("link", ""), "source_id": it.get("source_id", ""),
                "source_name": it.get("source_name", ""), "tier": it.get("tier", 3),
                "published": iso(pub), "date_unknown": date_unknown}
        if outlet:
            base["outlet"] = outlet

        if (TODAY - pub).days > cfg["max_signal_age_days"]:
            rejects.append({**base, "reason": "too_old",
                            "detail": f"{iso(pub)} 早于 {cfg['max_signal_age_days']} 天"})
            continue
        if is_opinion(headline, cfg["exclude_title_patterns"]):
            rejects.append({**base, "reason": "opinion", "detail": "评论/分析体裁"})
            continue

        # ---- 人物：标题优先；标题不足两人时才由正文补
        p_title = extract_persons(headline, PERSONS)
        persons = set(p_title)
        if len(persons) < 2 and desc:
            persons |= extract_persons(desc, PERSONS)

        # ---- 会晤词：标题命中，或正文命中且标题已点名至少两人
        meet_t = any(phrase_hit(headline, t) for t in cfg["meeting_terms"])
        meet_d = any(phrase_hit(desc, t) for t in cfg["meeting_terms"]) if desc else False
        if not (meet_t or (meet_d and len(p_title) >= 2)):
            rejects.append({**base, "reason": "no_meeting_term",
                            "detail": f"标题无会晤词（正文={'有' if meet_d else '无'}，标题人物={len(p_title)}）"})
            continue

        actors = extract_actors(headline, ACTORS) | (extract_actors(desc, ACTORS) if desc else set())

        # ---- 事件类型：标题优先，避免正文里一句旁述把 G20 标签贴到双边会晤上
        family = classify_family(headline, FAMILIES, persons, default=False)
        if not family:
            # ★ 只有明确写了 trilateral/three-way 才算三方。
            #   否则「Pezeshkian 将会见 Putin（外长阿拉格齐说的）」这种
            #   三个名字、两个人的会晤会被误判成三方会谈。
            tri_kw = ("trilateral", "three-way", "three way", "tripartite")
            if any(phrase_hit(headline, k) for k in tri_kw):
                family = "trilateral"
            elif len(persons) >= 2:
                family = "bilateral"
            else:
                family = (classify_family(desc, FAMILIES, persons, default=False) or "talks") if desc else "talks"
        # 「中期选举」是非会晤类目：标题里若有明确的双人会见，按会晤归类
        if family == "election" and meet_t and len(persons) >= 2:
            family = "trilateral" if any(phrase_hit(headline, k) for k in
                                         ("trilateral", "three-way", "three way")) else "bilateral"

        loc_zh, loc_raw = extract_location(headline, LOCS)
        loc_src = "title" if loc_zh else ""
        # ★ 正文只在「像摘要」时才提供地点。长新闻稿里随便一句
        #   "spokesman met with reporters in Washington" 会让整条会晤跑到华盛顿。
        #   摘要 vs 文档的判据用长度近似 —— 用语义判据成本远高于收益。
        if not loc_zh and desc and len(desc) <= int(cfg.get("report", {}).get("desc_loc_max_chars", 220)):
            loc_zh, loc_raw = extract_location(desc, LOCS)
            loc_src = "desc" if loc_zh else ""

        dt = parse_date_text(headline, pub) or (parse_date_text(desc, pub) if desc else None)
        status, phrase, rank = detect_status(headline, desc)

        # ★ 只是「放风/传闻」时，不要给出确定地点 —— 地点若只来自正文，
        #   多半是背景陈述（"他在莫斯科表示愿意…"），不是会晤地点。
        if loc_zh and loc_src == "desc" and status in ("proposed", "reported", "unconfirmed"):
            loc_zh, loc_raw, loc_src = "", "", ""

        # ---- 「已举行」但没有日期：用发稿日近似，并显式标注 approx
        approx = False
        if status == "held" and dt is None:
            dt = {"start": pub, "end": pub, "precision": "day", "raw": "发稿日近似"}
            approx = True

        sides = sides_of(persons, actors, loc_zh)
        if len(sides) < 2:
            rejects.append({**base, "reason": "one_sided",
                            "detail": f"只有一方：人物={sorted(persons)} 主体={sorted(actors)} 地点={loc_zh}"})
            continue

        # ★ 一句话里只有一个名字，不构成「谁和谁」。
        #   这类条目的标题只能写成「某甲 出访/会晤」，对读者零信息量，
        #   却会占满日程表 —— 实测一轮能混进 8 行。
        if family in FAMILY_COMPAT and len(persons) < 2:
            rejects.append({**base, "reason": "single_person",
                            "detail": f"只点名 1 人（{','.join(sorted(persons)) or '无'}），无法确定对谈方"})
            continue

        # ★ 本表的主角是「关键人物」。没有点名任何人、又不是命名峰会的信号，
        #   即使谈到 talks 也只是一般性表态，不进日历（否则会塞满「X 会谈」空行）。
        if not persons and (family not in NAMED_FAMILIES or dt is None):
            rejects.append({**base, "reason": "no_named_person",
                            "detail": f"未点名人物，类型={family}，日期={'有' if dt else '无'}"})
            continue

        # 没有日期时只接受「有意向/已宣布」级别以上的信号；
        # 「媒体报道 + 无日期」既不进日历也无法过期，留着只会污染表。
        if dt is None and status in ("reported", "unconfirmed"):
            rejects.append({**base, "reason": "no_date_weak",
                            "detail": f"状态={status} 且无可解析日期"})
            continue

        key = hashlib.sha1((norm_space(headline).lower() + "|" + iso(pub)).encode("utf-8")).hexdigest()[:16]
        if key in seen:
            rejects.append({**base, "reason": "duplicate", "detail": "同标题同日已收"})
            continue
        seen.add(key)

        ctx = _evidence_quote(f"{headline}. {desc}", phrase) if phrase else clamp(headline, 90)
        signals.append({
            **base,
            "family": family,
            "persons": sorted(persons),
            "actors": sorted(actors),
            "location": loc_zh,
            "location_raw": loc_raw,
            "loc_src": loc_src,
            "date_start": iso(dt["start"]) if dt else "",
            "date_end": iso(dt["end"]) if dt else "",
            "precision": dt["precision"] if dt else "tbd",
            "date_raw": dt["raw"] if dt else "",
            "date_approx": approx,
            "status": status,
            "status_phrase": phrase,
            "rank": rank,
            "quote": ctx,
            "desc_excerpt": desc[:160],
            "track": track_of(persons, actors, loc_zh),
        })
    return signals, rejects


def _evidence_quote(text: str, phrase: str) -> str:
    """命中处前后各取一段，**保留原始大小写** —— 读者要能复制回去检索。"""
    if not phrase:
        return clamp(text, 90)
    idx = text.lower().find(phrase.lower())
    if idx < 0:
        return clamp(text, 90)
    return clamp(text[max(0, idx - 40): idx + len(phrase) + 50], 120)


_EU_COUNTRIES = {"德国", "法国", "英国", "意大利", "欧盟", "波兰", "西班牙", "荷兰"}
_ASIA_COUNTRIES = {"印度", "日本", "韩国", "印度尼西亚", "新加坡", "越南", "泰国",
                   "马来西亚", "菲律宾"}
_MIDEAST_COUNTRIES = {"沙特阿拉伯", "土耳其", "埃及", "卡塔尔", "阿联酋", "约旦",
                      "科威特", "伊拉克"}


def track_of(persons: Iterable[str], actors: Iterable[str], location: str) -> str:
    """按「最核心的两个人」判轨道，不用全体名册。

    ★ 原实现把演员表全并进来，于是一场特朗普—习近平会晤因为同批报道提到
      普京与泽连斯基，就被归进了「俄乌」；一场俄伊会晤因为某篇稿子提到以色列，
      就被归进「以伊」。轨道是给人扫读用的，必须按主角判，不能按「提到过谁」判。

    ★ 新轨道（cn-eu / asia / mideast）插在 multi 之前、原有三条之后：
      原有判定优先级一律不动，避免扩表把老条目改了轨道。
      中国**不进** _ASIA_COUNTRIES —— 否则「特朗普—习近平」会从 multi 掉进 asia。
    """
    ppl = list(persons)
    pcs = {PERSON_COUNTRY.get(p) for p in ppl[:2] if PERSON_COUNTRY.get(p)}
    cs = set(pcs)
    if len(cs) < 2:
        cs |= {ACTOR_ZH.get(a) for a in actors if ACTOR_ZH.get(a)}
    loc_country = ""
    if location:
        for loc in LOCS:
            if loc["zh"] == location:
                loc_country = loc["country"]
                cs.add(loc_country)
                break
    if "俄罗斯" in cs and "乌克兰" in cs:
        return "ru-ua"
    if "美国" in cs and "伊朗" in cs:
        return "us-ir"
    if "以色列" in cs or "也门" in cs:
        return "il-ir"
    # ★ 新轨道只认「点名的人 + 会晤城市」，**不吃 actors**。
    #   实测教训：一条伊朗议会代表团出席 BRICS 会议的稿子，actors 里带了印度，
    #   就被拖进「亚洲」；一条卡利巴夫的稿子同样。轨道是主角的轨道，不是围观者的。
    ncs = set(pcs)
    if loc_country:
        ncs.add(loc_country)
    if "中国" in ncs and (ncs & _EU_COUNTRIES):
        return "cn-eu"
    if ncs & _ASIA_COUNTRIES:
        return "asia"
    if ncs & _MIDEAST_COUNTRIES:
        return "mideast"
    if "美国" in cs and ("俄罗斯" in cs or "乌克兰" in cs):
        return "ru-ua"
    return "multi"


# ====================================================================== 状态机

def _pub_date(x) -> Optional[date]:
    """claims 里的 published 可能是 date，也可能是落盘回读的 ISO 字符串。"""
    if isinstance(x, date):
        return x
    return d(x) if isinstance(x, str) else None


def choose_status(claims: List[dict]) -> Tuple[str, bool]:
    pos = [c for c in claims if c["rank"] > 0]
    neg = [c for c in claims if c["rank"] < 0]
    if neg:
        strong_neg = [c for c in neg if c["tier"] <= 2]
        if strong_neg:
            bn = max(strong_neg, key=lambda c: (c["rank"], -c["tier"], c["published"]))
            if pos:
                bp = max(pos, key=lambda c: (c["rank"], -c["tier"], c["published"]))
                # 更强的正面消息比否认晚 2 天以上 → 视为翻案，但保留冲突标记
                # ★ claims 里的 published 是 **ISO 字符串**（落盘后回读也是字符串），
                #   直接 + timedelta 会 TypeError。必须解析成 date 再比。
                bp_d = _pub_date(bp["published"])
                bn_d = _pub_date(bn["published"])
                if bp_d and bn_d and bp_d > bn_d + timedelta(days=2):
                    return bp["status"], True
            return bn["status"], bool(pos)
    if not pos:
        return "unconfirmed", False
    best = max(pos, key=lambda c: (c["rank"], -c["tier"], c["published"]))
    return best["status"], False


def rank_persons(ev: dict, limit: int = 4) -> List[str]:
    """按「出现在多少条独立报道里」排序。合并后的人名并集不能直接当标题用。"""
    cnt: Dict[str, int] = {}
    for c in ev.get("claims", []):
        for p in c.get("persons") or []:
            cnt[p] = cnt.get(p, 0) + 1
    if not cnt:
        return list(ev.get("persons") or [])[:limit]
    out = [p for p, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))]
    # 基线里人工录入、但没有对应报道的人物不能丢（否则会显示成「无人名」）
    for p in ev.get("persons") or []:
        if p not in out:
            out.append(p)
    return out[:limit]


def core_persons(ev: dict) -> Set[str]:
    """事件身份用「事件已收录的人物集合」，并用**包含度**判重。

    ★ 不再固定取前两人。原实现按频次取 top-2，频次打平时按 id 字母序，
      于是 {araghchi, pezeshkian, putin} 会切成 {araghchi, pezeshkian}，
      与真正的会晤双方 {pezeshkian, putin} 只重合一半，同一条新闻被拆成两行。
      正确判据是「信号的参会人是否被事件覆盖」，而不是「谁的字母序更靠前」。
    """
    return set(ev.get("persons") or [])


def find_match(sig: dict, events: List[dict]) -> Optional[dict]:
    best, best_score = None, 0.0
    sd = d(sig.get("date_start", ""))
    sp = set(sig.get("persons") or [])
    for ev in events:
        # ★ 已归档事件**必须**继续参与判重（2026-10-26 修）。
        #   聚合检索会把同一篇旧稿反复吐出来；归档后若跳过它，同一条稿子
        #   每轮都会新建一个事件。实测：state/events.json 里 20 条归档事件中
        #   有 17 条是同 3 篇稿子的重复拷贝（Rubio 那篇重复了 12 次）。
        #   归档只影响「要不要显示」，不影响「是不是同一件事」。

        # 已经判定「这条信号属于哪场命名峰会」时，直接归并到那条基线 ——
        # 同一场峰会的报道人物组合千差万别（有人写特朗普+习近平，有人写普京），
        # 靠人物相似度会分裂成好几行。
        if sig.get("anchor_snap") and ev.get("anchor_id") == sig["anchor_snap"]:
            return ev
        fam_ok = (ev["family"] == sig["family"] or
                  (ev["family"] in FAMILY_COMPAT and sig["family"] in FAMILY_COMPAT))
        if not fam_ok:
            continue
        ep = core_persons(ev)

        if sp and ep:
            inter = len(ep & sp)
            if inter == 0:
                continue
            contain = inter / min(len(ep), len(sp))
            jac = inter / len(ep | sp)
            # 小集合必须基本被包含，防止「只共享普京」就把两场会晤并起来
            if contain < 0.8 or jac < 0.5:
                continue
        elif sp or ep:
            # 一方无人名（基线、机构对机构）→ 只能靠类型 + 日期 + 地点
            contain, jac = 0.6, 0.5
        else:
            ea, sa = set(ev.get("actors") or []), set(sig.get("actors") or [])
            if not (ea & sa):
                continue
            jac = len(ea & sa) / len(ea | sa)
            if jac < 0.5:
                continue
            contain = 0.6

        ed = d(ev.get("date_start", ""))
        if ed and sd:
            delta = abs((ed - sd).days)
            if delta > 5:
                continue
            # ★ 月份精度是磁铁：两条都只精确到月时，允许「子集合并」
            #   （双边报道并入三方峰会），但不允许两个各有旁人的集合硬拼，
            #   否则「11 月的所有事」会被吸成一条。
            if ev.get("precision") == "month" and sig.get("precision") == "month" \
                    and not (ep <= sp or sp <= ep):
                continue
            dscore = max(0.0, 1 - delta / 6)
        else:
            # tbd 不是万能胶水：至少一方无日期时，必须人物集高度重合
            if not (ep and sp) or contain < 0.99 or jac < 0.66:
                continue
            dscore = 0.4

        if ev.get("location") and sig.get("location") and ev["location"] != sig["location"]:
            continue
        score = jac * 0.65 + contain * 0.15 + dscore * 0.2
        # 分数相同时让「还在表里」的事件优先，避免合并进一条已归档的旧副本后
        # 新信息直接隐身（归档事件只是吸收目标，不该抢走活跃事件）。
        if ev.get("archived"):
            score -= 0.001
        if score > best_score:
            best, best_score = ev, score
    return best if best_score >= 0.6 else None


def anchor_for(sig: dict, cfg: dict) -> Optional[dict]:
    """把「日期固定的命名峰会」信号吸附到基线上。

    ★ 为什么必须吸附：一篇稿子说 "Trump will meet Xi in China in November,
      again at G20 in Miami" 时，正文里同时出现两个不同峰会的日期与城市，
      单条信号只能落一个，结果是「11 月的 G20（迈阿密）」这种拼装事件。
      命名峰会的会期以基线为准，文中日期只用来判断「是不是同一场」。

    ★ 只对 `snap: true` 的基线生效（2026-10-24 修）。原实现对**所有**基线吸附，
      于是 `双边上会晤 2026-10-27` 这条基线成了磁铁：97 条互不相干的信号
      （赫格塞思见内塔尼亚胡、鲁比奥见泽连斯基…）全被吸到「10-27 莫斯科」。
      双边会晤的日期是**事件属性**，不是日历常数，不能吸附。
    """
    fam = sig.get("family")
    cands = [a for a in cfg.get("anchors", []) if a.get("family") == fam and a.get("snap")]
    if not cands:
        return None
    sd = d(sig.get("date_start", "")) or d(sig.get("published", ""))
    if not sd:
        return None
    best, bestscore = None, -1e9
    for a in cands:
        ad = d(a.get("date", ""))
        if not ad:
            continue
        dist = abs((ad - sd).days)
        if dist > 45:
            continue
        hits = sum(1 for k in (a.get("match_any") or []) if phrase_hit(sig.get("title", ""), k))
        if hits == 0 and dist > 10:
            continue
        score = hits * 10 - dist * 0.25
        if score > bestscore:
            best, bestscore = a, score
    return best


def snap_to_anchor(sig: dict, cfg: dict) -> Optional[dict]:
    a = anchor_for(sig, cfg)
    if not a:
        return None
    sig["anchor_snap"] = a["id"]
    sig["pre_snap_date"] = sig.get("date_start", "")
    sig["date_start"] = a["date"]
    sig["date_end"] = a.get("date_end", "")
    sig["precision"] = a.get("precision", "day")
    if a.get("location"):
        sig["location"] = a["location"]
    return a


def upsert(sig: dict, events: List[dict], next_id: List[int], transitions: List[dict]) -> dict:
    ev = find_match(sig, events)
    claim = {
        "status": sig["status"], "rank": sig["rank"], "tier": sig["tier"],
        "source": sig["source_name"], "source_id": sig["source_id"],
        "outlet": sig.get("outlet", ""), "url": sig["link"],
        "persons": list(sig.get("persons") or []),
        "location": sig.get("location", ""),
        "published": sig["published"], "quote": sig["quote"],
        "date_start": sig["date_start"], "precision": sig["precision"],
        "anchor_snap": sig.get("anchor_snap", ""),
    }
    is_new = ev is None
    if is_new:
        ev = {
            "id": f"EV-{next_id[0]:04d}",
            "family": sig["family"],
            "title": "", "title_raw": sig["title"],
            "date_start": sig.get("date_start", ""), "date_end": sig.get("date_end", ""),
            "precision": sig.get("precision", "tbd"), "date_raw": sig.get("date_raw", ""),
            "location": sig.get("location", ""), "location_raw": sig.get("location_raw", ""),
            "persons": sig["persons"], "actors": sig["actors"],
            "track": sig.get("track") or track_of(sig.get("persons", []), sig.get("actors", []),
                                                  sig.get("location", "")),
            "status": "unconfirmed", "conflict": False,
            "claims": [], "sources": [],
            "first_seen": RUN_AT, "last_seen": RUN_AT,
            "updates": [], "baseline": False, "baseline_ids": [],
            "days_seen": 0,
        }
        next_id[0] += 1
        events.append(ev)
    old_status = ev["status"]

    # 合并日期：未知 → 已知；日 → 区间；同起点保持
    if sig["date_start"] and not ev["date_start"]:
        ev["date_start"] = sig["date_start"]
        ev["date_end"] = sig["date_end"]
        ev["precision"] = sig["precision"]
        ev["date_raw"] = sig["date_raw"]
    elif sig["date_start"] and ev["date_start"] and sig["precision"] == "range" and ev["precision"] != "range":
        if sig["date_start"] <= ev["date_start"] <= (d(sig["date_end"]) or d(sig["date_start"])):
            ev["date_start"], ev["date_end"], ev["precision"] = sig["date_start"], sig["date_end"], "range"

    if sig["location"] and not ev["location"]:
        ev["location"] = sig["location"]
        ev["location_raw"] = sig["location_raw"]

    ev["persons"] = sorted(set(ev["persons"]) | set(sig["persons"]))
    ev["actors"] = sorted(set(ev["actors"]) | set(sig["actors"]))
    ev["claims"].append(claim)

    # 同一来源重复报道只保留最新一条，防止 20 家转载把 rank 抬成「已确认」
    dedup: Dict[str, dict] = {}
    for c in ev["claims"]:
        k = (c["source_id"], c["outlet"] or c["source"], c["quote"][:40])
        if k not in dedup or c["published"] > dedup[k]["published"]:
            dedup[k] = c
    ev["claims"] = list(dedup.values())
    # ★ 展示用人物按报道频次取前 4，而不是并集 —— 并集会随转载无限膨胀
    ev["persons"] = rank_persons(ev, limit=4)
    # ★ 地点按「多少条独立报道这么说」投票，不取第一条。
    #   首条定终身会让一个引用背景的段落永久决定会晤地点。
    #   ★ 只采信「已安排」及以上等级报道给出的地点：放风阶段的稿子里，
    #     莫斯科/迈阿密往往是「他此前在哪里说过话」，不是会晤地点
    #     （实测：一场 11 月的特朗普—习近平会晤被 38 篇稿子投成了莫斯科）。
    if not ev.get("anchor_id"):
        locs = [c["location"] for c in ev["claims"]
                if c.get("location") and c.get("rank", 0) >= 60]
        ev["location"] = Counter(locs).most_common(1)[0][0] if locs else ""

    new_status, conflict = choose_status(ev["claims"])
    # ★ 基线对「峰会本身」是有权威的：APEC 的会期与「已定档」不该被
    #   某家媒体一句 "could meet at APEC" 降级成「已预告」。
    if ev.get("anchor_id") and ev.get("anchor_status"):
        if STATUS_RANK.get(new_status, 0) < STATUS_RANK.get(ev["anchor_status"], 0):
            new_status = ev["anchor_status"]
    # 「已举行」不能用在未来事件上（标题里的 met 可能是别的会晤）
    if new_status == "held" and ev["date_start"] and d(ev["date_start"]) and d(ev["date_start"]) > TODAY:
        new_status = "arranged"
    ev["status"] = new_status
    ev["conflict"] = conflict

    if ev.get("anchor_id"):
        # 基线标题是人工录入的，不能被通用模板覆盖
        ev["title"] = ev.get("title") or build_title(ev["family"], rank_persons(ev, 3),
                                                    ev["location"], ev["title_raw"], anchored=True)
    else:
        ev["title"] = build_title(ev["family"], rank_persons(ev, 3),
                                  ev["location"], ev["title_raw"])
    ev["track"] = track_of(ev["persons"], ev["actors"], ev["location"])
    ev["last_seen"] = RUN_AT
    ev["days_seen"] = int(ev.get("days_seen", 0)) + (1 if is_new else 0)

    if not is_new and new_status != old_status:
        transitions.append({
            "id": ev["id"], "title": ev["title"],
            "from": old_status, "to": new_status,
            "at": RUN_AT, "evidence": sig["quote"], "url": sig["link"],
            "outlet": sig.get("outlet", ""), "tier": sig["tier"],
        })
        ev["updates"].append({"at": RUN_AT, "from": old_status, "to": new_status,
                              "evidence": sig["quote"], "url": sig["link"]})
    return ev


def update_lifecycle(ev: dict, today: date, transitions: List[dict]) -> None:
    """★「过期未证实」是本监测器最重要的一等状态。
    会晤没开成、和会晤开了但没人报道，在公开源上完全一样。
    静默删除等于假装它发生过。"""
    if ev.get("archived") or ev["status"] in ("canceled", "postponed", "denied", "lapsed", "held"):
        return
    end = d(ev.get("date_end") or ev.get("date_start") or "")
    if not end or end >= today:
        return
    has_held = any(c["status"] == "held" for c in ev["claims"])
    new = "held" if has_held else "lapsed"
    old = ev["status"]
    ev["status"] = new
    if old != new:
        transitions.append({"id": ev["id"], "title": ev["title"], "from": old, "to": new,
                            "at": RUN_AT, "evidence": "日期已过，且无任何一方确认举行" if new == "lapsed" else "存在已举行的报道",
                            "url": "", "outlet": "", "tier": 0})
        ev["updates"].append({"at": RUN_AT, "from": old, "to": new,
                              "evidence": "生命周期判定", "url": ""})


def archive_old(events: List[dict], today: date, retention: int) -> int:
    n = 0
    for ev in events:
        if ev.get("archived"):
            continue
        end = d(ev.get("date_end") or ev.get("date_start") or "")
        if end and (today - end).days > retention:
            ev["archived"] = True
            n += 1
    return n


# ====================================================================== 基线

def seed_anchors(cfg: dict, events: List[dict], next_id: List[int]) -> int:
    """基线是「人工录入的已知峰会」。★必须可被公开源推翻：
    它们以 baseline=True 进入，被真实信号合并后保留，但依据栏会显示已证实。
    每条基线都带 verify_query，报告里单列核验状态。"""
    added = 0
    existing = {ev.get("anchor_id") for ev in events if ev.get("anchor_id")}
    for a in cfg.get("anchors", []):
        if a["id"] in existing:
            continue
        ev = {
            "id": f"EV-{next_id[0]:04d}",
            "anchor_id": a["id"],
            "family": a.get("family", "other"),
            "title": a["title"], "title_raw": a["title"],
            "date_start": a["date"], "date_end": a.get("date_end", ""),
            "precision": a.get("precision", "day"), "date_raw": "",
            "location": a.get("location", ""), "location_raw": "",
            "persons": list(a.get("persons", [])), "actors": list(a.get("actors", [])),
            "track": track_of(a.get("persons", []), a.get("actors", []), a.get("location", "")),
            "status": a.get("status", "unconfirmed"), "conflict": False,
            "anchor_status": a.get("status", "unconfirmed"),
            "claims": [], "sources": [],
            "first_seen": RUN_AT, "last_seen": RUN_AT, "updates": [],
            "baseline": True, "baseline_ids": [a["id"]],
            "baseline_note": a.get("note", ""), "verify_query": a.get("verify_query", ""),
            "days_seen": 0,
        }
        next_id[0] += 1
        events.append(ev)
        added += 1
    return added


def attach_sources(ev: dict) -> None:
    ev["sources"] = sorted(
        [{"name": c["source"], "outlet": c.get("outlet", ""), "tier": c["tier"],
          "url": c["url"], "published": c["published"], "quote": c["quote"]}
         for c in ev["claims"]],
        key=lambda s: (s["tier"], s["published"]), reverse=False)
    if ev.get("baseline") and ev["claims"]:
        ev["baseline_confirmed"] = True


# ====================================================================== 渲染

def fmt_date_short(iso_s: str) -> str:
    x = d(iso_s)
    return f"{x.month}-{x.day}" if x else iso_s[5:]


def evidence_cell(ev: dict, max_name: int = 22) -> str:
    if not ev.get("sources"):
        if ev.get("baseline"):
            return "基线（未回源）"
        return "—"
    s = ev["sources"][0]
    label = s["outlet"] or s["name"]
    tag = {0: "B", 1: "T1", 2: "T2", 3: "T3"}.get(s["tier"], "T3")
    link = f"[{clamp(label, max_name)}]({s['url']})" if s.get("url") else clamp(label, max_name)
    extra = f" +{len(ev['sources']) - 1}" if len(ev["sources"]) > 1 else ""
    return f"{tag} {link}{extra}"


def sort_key(ev: dict):
    """月份精度的条目一律按**该月月初**排序。

    ★ 不能按内部 date_start 排。基线「美伊潜在谈判」内部记的是 11-15（月中），
      但对外只显示「2026-11（月内）」；于是表里会出现
      「11 月内 → 11-03 → 11 月内」这种读者看起来乱序、而程序认为有序的表。
      对外承诺的是「月份」，排序就该按月份起点，显示与排序用同一把尺子。
    """
    s = ev.get("date_start")
    if not s:
        return (1, "", ev["id"])
    if ev.get("precision") == "month":
        s = s[:7] + "-01"
    return (0, s, ev["id"])


def render_md(events: List[dict], stats: dict, ctx: dict) -> str:
    L: List[str] = []
    A = L.append
    today = TODAY
    active = [e for e in events if not e.get("archived")]

    upcoming = sorted([e for e in active if e.get("date_start") and d(e["date_start"]) >= today
                       and e["status"] not in NEG_STATUS], key=sort_key)
    tbd = sorted([e for e in active if not e.get("date_start") and e["status"] not in NEG_STATUS],
                 key=lambda e: e["id"])
    # 已否认 / 已推迟 / 已取消：不进日历，单列（否则「取消」会被读成「待定」）
    dropped = sorted([e for e in active if e["status"] in NEG_STATUS], key=sort_key)
    past = sorted([e for e in active if e.get("date_start") and d(e["date_start"]) < today],
                  key=lambda e: (e.get("date_start") or "", e["id"]), reverse=True)
    lapsed = [e for e in past if e["status"] == "lapsed"]

    A(f"# {ctx['title']}（{today.isoformat()}）")
    A("")
    A(f"> 生成 {RUN_AT} (UTC+8) ｜ 出口 `{ctx['egress']}` ｜ 配置指纹 `{ctx['cfg_fp']}` ｜ 运行 `meeting_monitor.py`")
    A("")
    A("## 0. 阅读须知（口径先于结论）")
    A("")
    A("- 本表只回答四件事：**什么时候、在哪、谁和谁、见没见成**。不做立场判断，不预测结果。")
    A("- **「确认情况」是分级，不是布尔值**：已确认 ＞ 日期已定 ＞ 已安排 ＞ 已预告 ＞ 媒体报道 ＞ 暂无确认日期。")
    A("  `已否认 / 已推迟 / 已取消` 单列；强信源与弱信源冲突时标 `⚠口径冲突`，不替你裁决。")
    A("- **「过期未证实」是一等状态**：日期已过、又没有任何一方确认举行 → 标 `过期未证实`，")
    A("  **不删除**。会晤「没开成」和「开了但没人报道」在公开源上长得一模一样，删除等于假装它发生过。")
    A("- 日期精度分 `日 / 区间 / 当周 / 月内 / 待定`。**「月内」不等于某一天要开会。**")
    A("- 基线 = 人工录入的已知峰会（带核验查询），被公开源证实后自动转为有来源状态。")
    A("- 信源等级：**T1** 官方一手 ｜ **T2** 主流媒体 / 官方背景通讯社 ｜ **T3** 聚合检索 ｜ **B** 人工基线。")
    A("")

    # ---------- 速览
    A("## 1. 一页速览")
    A("")
    by_status: Dict[str, int] = {}
    for e in upcoming + tbd:
        by_status[e["status"]] = by_status.get(e["status"], 0) + 1
    order = ["confirmed", "scheduled", "arranged", "proposed", "reported", "unconfirmed"]
    dist = "、".join(f"{STATUS_LABEL.get(k, k)} {by_status[k]}" for k in order if by_status.get(k))
    A("| 指标 | 数值 |")
    A("|---|---|")
    A(f"| 未来 {ctx['horizon']} 天内会晤/会议 | **{len(upcoming)}** 项（另有日期待定 {len(tbd)} 项） |")
    A(f"| 确认度分布 | {dist or '—'} |")
    A(f"| 本次状态变化 | 升级 {stats['upgraded']} ｜ 降级/否认/推迟 {stats['downgraded']} ｜ 过期未证实 {stats['lapsed_new']} |")
    A(f"| 本次新识别 | {stats['new_events']} 项（合并自 {stats['signals_used']} 条信号） |")
    A(f"| 已举行（近 {ctx['retention']} 天） | {stats['held']} 项 |")
    A("")
    nxt = [e for e in upcoming if (d(e["date_start"]) - today).days <= 14]
    if nxt:
        A(f"**未来 14 天（{today.isoformat()} 起）：**")
        A("")
        for e in nxt[:8]:
            A(f"- `{date_cell(e)}` **{e['title']}**（{e['location'] or '地点未定'}）— {persons_cell(e)} — {status_cell(e)}")
        A("")

    # ---------- 主表
    A(f"## 2. 会晤日程表（按日期，含日期待定）")
    A("")
    A("| 日期 | 会议 / 地点 | 关键人物 | 确认情况 | 依据 |")
    A("|---|---|---|---|---|")
    if not (upcoming or tbd):
        A("| — | 本轮未识别到符合条件的会晤 | — | — | — |")
    for e in upcoming + tbd:
        loc = e["location"] or "地点未定"
        conf = status_cell(e)
        if e.get("conflict"):
            conf += " ⚠口径冲突"
        A(f"| {date_cell(e)} | {e['title']}，{loc} | {persons_cell(e)} | {conf} | {evidence_cell(e)} |")
    A("")
    A(f"> 表内 {len(upcoming) + len(tbd)} 项 = 未来 {len(upcoming)} + 待定 {len(tbd)}。"
      f"全部明细（含所有来源链接、首次/最后发现时间）见 `LATEST.csv`。")
    A("")

    # ---------- 分轨
    A("## 3. 分轨视图")
    A("")
    for tid, tinfo in ctx["tracks"].items():
        rows = [e for e in upcoming + tbd if e["track"] == tid]
        A(f"### 3.{list(ctx['tracks']).index(tid) + 1} {tinfo['label']}（{len(rows)} 项）")
        A("")
        if not rows:
            A("本轮未识别到。")
            A("")
            continue
        A("| 日期 | 会议 / 地点 | 关键人物 | 确认情况 |")
        A("|---|---|---|---|")
        for e in rows:
            A(f"| {date_cell(e)} | {e['title']}，{e['location'] or '地点未定'} | {persons_cell(e)} | {status_cell(e)} |")
        A("")

    # ---------- 人物索引
    A("## 4. 关键人物索引")
    A("")
    idx: Dict[str, List[dict]] = {}
    for e in upcoming + tbd:
        for p in e["persons"]:
            idx.setdefault(p, []).append(e)
    A("| 人物 | 职务 | 未来会晤数 | 最近一项 |")
    A("|---|---|---|---|")
    for pid, evs in sorted(idx.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        evs = sorted(evs, key=sort_key)
        A(f"| {PERSON_ZH.get(pid, pid)} | {PERSON_ROLE.get(pid, '')} | {len(evs)} | {date_cell(evs[0])} {evs[0]['title']} |")
    if not idx:
        A("| — | — | 0 | — |")
    A("")

    # ---------- 状态变化
    A("## 5. 本次状态变化")
    A("")
    if ctx["transitions"]:
        A("| 事件 | 变化 | 证据 | 来源 |")
        A("|---|---|---|---|")
        for t in ctx["transitions"][:30]:
            arrow = f"{STATUS_LABEL.get(t['from'], t['from'])} → **{STATUS_LABEL.get(t['to'], t['to'])}**"
            ev = clamp(t["evidence"], 60)
            src = clamp(t.get("outlet") or "", 24)
            A(f"| {t['title']} | {arrow} | {ev} | {src} |")
    else:
        A("本轮无状态变化。")
    A("")

    # ---------- 未成行
    A("## 6. 未成行（已否认 / 已推迟 / 已取消 / 过期未证实）")
    A("")
    if dropped:
        A(f"### 6.1 已否认 / 已推迟 / 已取消（{len(dropped)} 项）")
        A("")
        A("这些项**不进上面的日历表**——它们的结论是「这场不开」，不是一个待定的日程。")
        A("")
        A("| 状态 | 原定日期 | 会议 / 地点 | 关键人物 | 依据 |")
        A("|---|---|---|---|---|")
        for e in dropped[:20]:
            A(f"| {status_cell(e)} | {date_cell(e)} | {e['title']}，{e['location'] or '地点未定'} | "
              f"{persons_cell(e)} | {evidence_cell(e)} |")
        A("")
    else:
        A("### 6.1 已否认 / 已推迟 / 已取消")
        A("")
        A("本轮无。")
        A("")
    A(f"### 6.2 过期未证实（{len(lapsed)} 项）")
    A("")
    if lapsed:
        A(f"以下 **{len(lapsed)}** 项的日期已过，但本轮及此前**没有任何一方确认举行**。")
        A("它们可能：①根本没开成；②开了但未被公开报道；③报道用了别的说法。本表不替你判断。")
        A("")
        A("| 原定日期 | 会议 / 地点 | 关键人物 | 此前确认度 | 最后证据 |")
        A("|---|---|---|---|---|")
        for e in lapsed[:20]:
            prev = [u["from"] for u in reversed(e["updates"]) if u["to"] == "lapsed"]
            A(f"| {date_cell(e)} | {e['title']}，{e['location'] or '地点未定'} | {persons_cell(e)} | "
              f"{STATUS_LABEL.get(prev[0], '—') if prev else '—'} | {evidence_cell(e)} |")
    else:
        A("本轮无。")
    A("")

    # ---------- 已举行
    A(f"## 7. 已举行（近 {ctx['retention']} 天，{stats['held']} 项）")
    A("")
    held = [e for e in past if e["status"] == "held"]
    if held:
        A("| 日期 | 会议 / 地点 | 关键人物 | 依据 |")
        A("|---|---|---|---|")
        for e in held[:20]:
            A(f"| {date_cell(e)} | {e['title']}，{e['location'] or '地点未定'} | {persons_cell(e)} | {evidence_cell(e)} |")
    else:
        A("本轮无。")
    A("")

    # ---------- 源状态
    A("## 8. 数据源状态")
    A("")
    A("| 源 | 等级 | 条目 | 最新条目 | 状态 |")
    A("|---|---|---|---|---|")
    for h in ctx["health"]:
        st = "✅" if h["ok"] else f"❌ {h['note']}"
        A(f"| {h['name']} | T{h['tier']} | {h['count']} | {h['latest'] or '—'} | {st} |")
    okn = sum(1 for h in ctx["health"] if h["ok"])
    A(f"\n源可用 **{okn}/{len(ctx['health'])}**。失败源不会中断整轮，但**该源覆盖的会晤可能整段缺失** ——")
    A("这是本表最主要的遗漏来源，不要把它读成「没有这类会晤」。")
    A("")

    # ---------- 基线核验
    base = [e for e in active if e.get("baseline")]
    if base:
        A("## 9. 基线条目核验")
        A("")
        A("| 基线项 | 日期 | 状态 | 是否被公开源证实 |")
        A("|---|---|---|---|")
        for e in sorted(base, key=sort_key):
            confirmed = "✅ 已证实" if e.get("baseline_confirmed") else "⬜ 仅基线"
            A(f"| {e['title']} | {date_cell(e)} | {STATUS_LABEL.get(e['status'], e['status'])} | {confirmed} |")
        ncf = sum(1 for e in base if e.get("baseline_confirmed"))
        A(f"\n基线 **{len(base)}** 项，其中被本轮公开源证实 **{ncf}** 项。"
          f"未证实的项只代表「人工录入时认为如此」，**不等于已核实**。")
        A("")

    # ---------- 拒收与偏差
    A("## 10. 已知偏差与口径边界")
    A("")
    A(f"- 本轮抓取条目 {stats['items']}，入信号 {stats['signals']}，拒收 {stats['rejected']}。"
      f"拒收原因：{stats['reject_breakdown'] or '—'}。")
    A(f"- **当日快照与本轮口径不同**：`signals.jsonl` / `rejects.jsonl` 是**当天历次运行合并去重**后的结果"
      f"（当前唯一 {stats.get('signals_daily_unique', 0)} / {stats.get('rejects_daily_unique', 0)} 条），"
      "而上面这行的数字只算**本轮一次**。数文件行数会得到合并值，两者不要混读。")
    A("- **人物识别是别名词典，不是 NER**：同一人用非常规称呼（如只写「伊朗总统」而不写姓名）时，"
      "若该称呼未进别名表就会漏；代词「他」一律不识别。")
    A("- **地点只认城市/场馆表**：写「在峰会间隙」而没有城市的，会落成「地点未定」。")
    A("- **一条新闻可以同时支撑两个会晤**（如「普京在莫斯科会见佩泽希齐扬，并将会见莱希」），"
      "当前只取最强的一个日期，可能漏掉第二个。")
    A("- **「月内」精度**来自原文 `in November` / `next month` 这类表述，不是我们推断出来的具体日期。")
    A("- 聚合检索（T3）会重复转载同一篇稿；已按「标题+日期」去重，但不同措辞的同一事件仍可能分成两行。")
    A("- 本表服务于**日程跟踪**，不构成对会晤能否举行、或冲突走向的判断。")
    A("")
    return "\n".join(L)


def render_csv(events: List[dict], path: str) -> int:
    rows = [e for e in events if not e.get("archived")]
    rows.sort(key=sort_key)
    cols = ["事件ID", "日期", "日期精度", "日期原文", "会议", "地点", "关键人物",
            "确认情况", "状态码", "轨道", "事件类型", "信源等级", "主要来源",
            "链接", "首次发现", "最后更新", "来源数", "基线"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for e in rows:
            s = e["sources"][0] if e.get("sources") else {}
            w.writerow([
                e["id"], date_cell(e), e.get("precision", "tbd"), e.get("date_raw", ""),
                e["title"], e.get("location", "") or "地点未定", persons_cell(e),
                status_cell(e) + (" ⚠口径冲突" if e.get("conflict") else ""),
                e["status"], e.get("track", ""), e.get("family", ""),
                f"T{s['tier']}" if s else ("B" if e.get("baseline") else ""),
                s.get("outlet") or s.get("name", ""), s.get("url", ""),
                e.get("first_seen", ""), e.get("last_seen", ""),
                len(e.get("sources") or []), "是" if e.get("baseline") else "否",
            ])
    return len(rows)


# ====================================================================== 主流程

def load_cfg(path: str = CFG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def bind_globals(cfg: dict) -> None:
    global CFG, STATUS_SPEC, STATUS_LABEL, STATUS_RANK, FAMILY_LABEL
    global PERSON_ZH, PERSON_ROLE, PERSON_COUNTRY, ACTOR_ZH
    global LOCS, PERSONS, ACTORS, FAMILIES, NAMED_FAMILIES
    CFG = cfg
    STATUS_SPEC = cfg["status_lexicon"]
    STATUS_LABEL = {k: v["label"] for k, v in STATUS_SPEC.items()}
    STATUS_LABEL.update({"unconfirmed": "暂无确认日期", "lapsed": "过期未证实"})
    STATUS_RANK = {k: v["rank"] for k, v in STATUS_SPEC.items()}
    STATUS_RANK.update({"unconfirmed": 10, "lapsed": -80})
    FAMILY_LABEL = {f["id"]: f["label"] for f in cfg["families"]}
    NAMED_FAMILIES = {f["id"] for f in cfg["families"]} - FAMILY_COMPAT
    PERSONS = cfg["persons"]
    ACTORS = cfg["actors"]
    FAMILIES = cfg["families"]
    LOCS = cfg["locations"]
    PERSON_ZH = {p["id"]: p["zh"] for p in PERSONS}
    PERSON_ROLE = {p["id"]: p.get("role", "") for p in PERSONS}
    PERSON_COUNTRY = {p["id"]: p.get("country", "") for p in PERSONS}
    ACTOR_ZH = {a["id"]: a["zh"] for a in ACTORS}


def run() -> int:
    cfg = load_cfg()
    bind_globals(cfg)
    os.makedirs(OUTPUT, exist_ok=True)
    os.makedirs(STATE, exist_ok=True)
    day = TODAY.isoformat()
    day_dir = os.path.join(OUTPUT, day)
    os.makedirs(day_dir, exist_ok=True)

    print(f"[1/5] 抓取：{len([s for s in cfg['sources'] if s.get('kind') != 'query'])} 个直连源 + "
          f"{len(cfg['queries'])} 条检索")
    items, health = source_items(cfg, day_dir)
    print(f"      原始条目 {len(items)}")

    print("[2/5] 抽取：人物 / 地点 / 日期 / 确认度")
    signals, rejects = build_signals(items, cfg)
    print(f"      信号 {len(signals)}，拒收 {len(rejects)}")
    n_sig_daily = n_rej_daily = 0
    if signals:
        n_sig_daily = merge_jsonl(os.path.join(day_dir, "signals.jsonl"), signals)
    if rejects:
        n_rej_daily = merge_jsonl(os.path.join(day_dir, "rejects.jsonl"), rejects)

    st = read_json(os.path.join(STATE, "events.json"),
                   {"next_id": 1, "events": [], "archived": [], "updated": ""})
    events: List[dict] = st.get("events", [])
    next_id = [int(st.get("next_id", 1))]
    transitions: List[dict] = []

    print("[3/5] 归并与状态机")
    added_anchor = seed_anchors(cfg, events, next_id)
    before = len(events)
    snapped = 0
    for sig in signals:
        if snap_to_anchor(sig, cfg):
            snapped += 1
        upsert(sig, events, next_id, transitions)
    new_events = len(events) - before
    print(f"      吸附到已知峰会 {snapped} 条")
    for ev in events:
        update_lifecycle(ev, TODAY, transitions)
    for ev in events:
        attach_sources(ev)
    archived_now = archive_old(events, TODAY, cfg["retention_days"])

    # 生命周期/归档也要回写
    active = [e for e in events if not e.get("archived")]
    archived = [e for e in events if e.get("archived")]
    write_json(os.path.join(STATE, "events.json"), {
        "next_id": next_id[0], "events": events, "archived": archived, "updated": RUN_AT,
    })
    if transitions:
        append_jsonl(os.path.join(STATE, "transitions.jsonl"), transitions)

    print("[4/5] 渲染")
    reject_breakdown: Dict[str, int] = {}
    for r in rejects:
        reject_breakdown[r["reason"]] = reject_breakdown.get(r["reason"], 0) + 1
    stats = {
        "items": len(items), "signals": len(signals), "signals_used": len(signals),
        "rejected": len(rejects),
        "reject_breakdown": "、".join(f"{k} {v}" for k, v in
                                      sorted(reject_breakdown.items(), key=lambda kv: -kv[1])),
        "new_events": new_events + added_anchor,
        "upgraded": sum(1 for t in transitions if t["to"] in ("confirmed", "scheduled", "arranged", "held")),
        "downgraded": sum(1 for t in transitions if t["to"] in ("denied", "postponed", "canceled", "reported")),
        "lapsed_new": sum(1 for t in transitions if t["to"] == "lapsed"),
        "held": sum(1 for e in active if e["status"] == "held"),
        "archived_now": archived_now,
        # 当日快照是跨运行合并的，唯一行数才等于「今天一共有多少条候选」；
        # 本轮的 signals/rejected 是单次口径，两者不要混读
        "signals_daily_unique": n_sig_daily,
        "rejects_daily_unique": n_rej_daily,
    }
    ctx = {
        "title": cfg["report"]["title"], "horizon": cfg["horizon_days"],
        "retention": cfg["retention_days"], "tracks": cfg["tracks"],
        "health": health, "transitions": transitions,
        "egress": _EGRESS_CACHE["ok"][0] if _EGRESS_CACHE.get("ok") else (PROXY_OVERRIDE or "direct"),
        "cfg_fp": fingerprint(cfg),
    }
    md = render_md(events, stats, ctx)
    with open(os.path.join(OUTPUT, "LATEST.md"), "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(day_dir, f"meetings-{day}.md"), "w", encoding="utf-8") as f:
        f.write(md)
    n_csv = render_csv(events, os.path.join(OUTPUT, "LATEST.csv"))
    render_csv(events, os.path.join(day_dir, "calendar.csv"))
    with open(os.path.join(OUTPUT, "LATEST.jsonl"), "w", encoding="utf-8") as f:
        for e in active:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    write_json(os.path.join(day_dir, "_source_health.json"), health)
    write_json(os.path.join(day_dir, "_stats.json"), stats)

    print(f"      CSV {n_csv} 行")
    print(f"[5/5] 完成：output/LATEST.md / LATEST.csv")
    print(f"      未来 {len([e for e in active if e.get('date_start') and d(e['date_start']) >= TODAY])} 项，"
          f"待定 {len([e for e in active if not e.get('date_start')])} 项，"
          f"过期未证实 {sum(1 for e in active if e['status']=='lapsed')} 项")
    return 0


# ====================================================================== 自检

class _Checker:
    def __init__(self) -> None:
        self.n = 0
        self.fail: List[str] = []

    def __call__(self, name: str, got: Any, want: Any) -> None:
        self.n += 1
        ok = got == want
        if not ok:
            self.fail.append(f"  ✗ {name}\n      期望={want!r}\n      实际={got!r}")

    def truth(self, name: str, cond: bool) -> None:
        self(name, bool(cond), True)


def selftest() -> int:
    cfg = load_cfg()
    bind_globals(cfg)
    c = _Checker()
    P = date(2026, 10, 24)

    # ---- 1. 人物别名：边界、弱别名、角色代称
    c("Trump 命中", "trump" in extract_persons("President Trump will meet Putin", PERSONS), True)
    c("Trumpism 不误命中", "trump" in extract_persons("Trumpism is a doctrine", PERSONS), False)
    c("Putin / Pezeshkian 同时命中",
      extract_persons("Putin meets Pezeshkian in Moscow", PERSONS) >= {"putin", "pezeshkian"}, True)
    c("角色代称「Iranian president」→ 佩泽希齐扬",
      "pezeshkian" in extract_persons("Iranian president to visit Moscow", PERSONS), True)
    c("Katz 无职务上下文不误命中",
      "katz" in extract_persons("Katz won the lottery", PERSONS), False)
    c("Katz 带职务命中",
      "katz" in extract_persons("Defense Minister Katz said on Sunday", PERSONS), True)
    c("Sa'ar 变体命中",
      "saar" in extract_persons("Foreign Minister Gideon Sa'ar met his counterpart", PERSONS), True)

    # ---- 2. 地点：媒体名不得当地点
    c("Moscow 识别", extract_location("talks in Moscow next week", LOCS)[0], "莫斯科")
    c("Tehran Times 不识别为德黑兰",
      extract_location("report by Tehran Times", LOCS)[0], "")
    c("Washington Post 不识别为华盛顿",
      extract_location("according to the Washington Post", LOCS)[0], "")
    c("White House → 华盛顿", extract_location("meeting at the White House", LOCS)[0], "华盛顿")
    c("无地点返回空", extract_location("talks will be held", LOCS)[0], "")

    # ---- 3. 日期：日 / 区间 / 月份 / 相对 / 跨年 / 非法
    r = parse_date_text("talks on Nov. 18-19, 2026 in Shenzhen", P)
    c("区间 Nov 18-19", (iso(r["start"]), iso(r["end"]), r["precision"]), ("2026-11-18", "2026-11-19", "range"))
    r = parse_date_text("on 18-19 November", P)
    c("区间 18-19 November", (iso(r["start"]), iso(r["end"])), ("2026-11-18", "2026-11-19"))
    r = parse_date_text("summit on October 27", P)
    c("单日 October 27", (iso(r["start"]), r["precision"]), ("2026-10-27", "day"))
    r = parse_date_text("talks in November", P)
    c("月份精度", (iso(r["start"]), iso(r["end"]), r["precision"]),
      ("2026-11-01", "2026-11-30", "month"))
    r = parse_date_text("meeting next week", P)
    c("next week → 下周一", (iso(r["start"]), r["precision"]), ("2026-10-26", "week"))
    r = parse_date_text("talks tomorrow", P)
    c("tomorrow", iso(r["start"]), "2026-10-25")
    r = parse_date_text("planned for January 5", date(2026, 12, 20))
    c("跨年推断 January → 次年", iso(r["start"]), "2027-01-05")
    c("非法日期 Nov 31 不猜", parse_date_text("on November 31", P), None)
    c("无日期返回 None", parse_date_text("talks will be held soon", P), None)
    r = parse_date_text("late November", P)
    c("late November 仍是月份精度", r["precision"], "month")
    c("May 作动词不误判", parse_date_text("Trump may meet Putin", P), None)

    # ---- 4. 状态：分级与优先级
    c("will meet → 已安排", detect_status("Putin will meet Pezeshkian")[0], "arranged")
    c("confirms → 已确认", detect_status("Kremlin confirms Putin will meet Pezeshkian")[0], "confirmed")
    c("plans to → 已预告", detect_status("Putin plans to meet Pezeshkian")[0], "proposed")
    c("denies → 已否认", detect_status("Iran denies that Trump will meet Pezeshkian")[0], "denied")
    c("postponed → 已推迟", detect_status("The talks were postponed")[0], "postponed")
    c("met with → 已举行", detect_status("Putin met with Pezeshkian in Moscow")[0], "held")
    c("readout 标题 → 已举行", detect_status("Meeting with President of Iran", "Meeting with President of Iran")[0], "held")
    c("scheduled for → 日期已定", detect_status("The summit is scheduled for November 18")[0], "scheduled")
    c("无信号 → 无确认日期", detect_status("Putin and Pezeshkian discussed gas")[0], "unconfirmed")

    # ---- 5. 状态选择：强信源否认压过弱信源宣布；弱信源不能抬升
    cl = [
        {"status": "arranged", "rank": 65, "tier": 3, "published": P},
        {"status": "denied", "rank": -50, "tier": 2, "published": P},
    ]
    c("T2 否认压过 T3 宣布", choose_status(cl)[0], "denied")
    cl = [
        {"status": "confirmed", "rank": 85, "tier": 1, "published": P},
        {"status": "denied", "rank": -50, "tier": 2, "published": P - timedelta(days=5)},
    ]
    c("更新的 T1 确认翻掉旧否认", choose_status(cl)[0], "confirmed")
    cl = [{"status": "reported", "rank": 30, "tier": 3, "published": P}]
    c("只有 T3 报道不会变成已安排", choose_status(cl)[0], "reported")

    # ---- 6. 信号过滤：单边、无会晤词、无日期弱信号
    items = [{
        "title": "Iranian president to meet Putin in Moscow next week - Reuters",
        "desc": "Iran's Pezeshkian will travel to Moscow.", "published": P,
        "link": "https://example.com/1", "source_id": "q_test", "source_name": "Google News", "tier": 3,
    }]
    sigs, rej = build_signals(items, cfg)
    c("正面样例被接收", len(sigs), 1)
    s0 = sigs[0] if sigs else {}
    c("样例人物", set(s0.get("persons", [])), {"pezeshkian", "putin"})
    c("样例地点", s0.get("location"), "莫斯科")
    c("样例日期为当周", s0.get("precision"), "week")
    c("样例状态为已安排", s0.get("status"), "arranged")

    one = [{"title": "Putin says economy is growing", "desc": "", "published": P,
            "link": "https://example.com/2", "source_id": "q", "source_name": "G", "tier": 3}]
    c("无会晤词被拒", build_signals(one, cfg)[1][0]["reason"] if build_signals(one, cfg)[1] else "", "no_meeting_term")

    op = [{"title": "Opinion | Why Putin will meet nobody", "desc": "", "published": P,
           "link": "https://example.com/3", "source_id": "q", "source_name": "G", "tier": 3}]
    c("评论体裁被拒", build_signals(op, cfg)[1][0]["reason"] if build_signals(op, cfg)[1] else "", "opinion")

    weak = [{"title": "Putin reportedly met someone in Moscow", "desc": "", "published": P,
             "link": "https://example.com/4", "source_id": "q", "source_name": "G", "tier": 3}]
    c("单方/弱信号不进日历", len(build_signals(weak, cfg)[0]), 0)

    # ---- 7. 聚类：同事件合并 / 不同人物不合并 / tbd 后补日期沿用同一 ID
    bind_globals(cfg)
    evs: List[dict] = []
    nid = [1]
    tr: List[dict] = []
    s1 = {"family": "bilateral", "persons": ["putin", "pezeshkian"], "actors": ["russia", "iran"],
          "location": "莫斯科", "date_start": "2026-10-27", "date_end": "", "precision": "day",
          "status": "arranged", "rank": 65, "tier": 3, "source_name": "G", "source_id": "q1",
          "outlet": "A", "link": "u1", "published": iso(P), "quote": "will meet", "date_raw": "",
          "title": "Putin to meet Pezeshkian"}
    upsert(dict(s1), evs, nid, tr)
    s2 = dict(s1, source_id="q2", outlet="B", link="u2", status="confirmed", rank=85, tier=2, source_name="TASS")
    upsert(s2, evs, nid, tr)
    c("同事件合并为一条", len(evs), 1)
    c("合并后状态升级为已确认", evs[0]["status"], "confirmed")
    c("状态变化被记录", len(tr), 1)

    s3 = dict(s1, persons=["trump", "putin"], actors=["usa", "russia"], location="华盛顿",
              date_start="2026-10-27", source_id="q3", outlet="C", link="u3")
    upsert(s3, evs, nid, tr)
    c("不同人物不合并", len(evs), 2)

    tbd_ev = dict(s1, date_start="", date_end="", precision="tbd", source_id="q4", outlet="D",
                  link="u4", persons=["trump", "zelensky"], actors=["usa", "ukraine"],
                  location="", family="bilateral")
    upsert(tbd_ev, evs, nid, tr)
    n_before = len(evs)
    dated = dict(tbd_ev, date_start="2026-11-20", date_end="", precision="day", source_id="q5",
                 outlet="E", link="u5", status="arranged", rank=65, tier=2)
    upsert(dated, evs, nid, tr)
    c("待定事件补上日期后仍是同一条", len(evs), n_before)
    tgt = [e for e in evs if "zelensky" in e["persons"]][0]
    c("补上的日期落到原事件", tgt["date_start"], "2026-11-20")

    # ---- 8. 生命周期
    past_ev = {"status": "arranged", "date_start": "2026-10-20", "date_end": "", "claims": [],
               "updates": [], "archived": False, "title": "X", "id": "EV-9", "persons": [], "actors": []}
    update_lifecycle(past_ev, P, [])
    c("日期已过且无举行证据 → 过期未证实", past_ev["status"], "lapsed")
    past2 = dict(past_ev, status="arranged", claims=[{"status": "held"}])
    update_lifecycle(past2, P, [])
    c("日期已过且有举行证据 → 已举行", past2["status"], "held")
    fut = dict(past_ev, status="arranged", date_start="2026-11-20", claims=[])
    update_lifecycle(fut, P, [])
    c("未来事件不被判过期", fut["status"], "arranged")

    # ---- 9. 渲染
    bind_globals(cfg)
    ev_ok = {"id": "EV-1", "title": "普京—佩泽希齐扬 会晤", "date_start": "2026-11-01",
             "date_end": "2026-11-30", "precision": "month", "location": "莫斯科",
             "persons": ["putin", "pezeshkian"], "actors": ["russia", "iran"],
             "status": "proposed", "conflict": False, "sources": [], "baseline": True,
             "family": "bilateral", "track": "us-ir", "claims": [], "updates": [],
             "first_seen": RUN_AT, "last_seen": RUN_AT}
    c("月份精度确认情况", status_cell(ev_ok), "已预告（2026-11 月内）")
    ev_tbd = dict(ev_ok, date_start="", precision="tbd")
    c("无日期确认情况", status_cell(ev_tbd), "暂无确认日期")
    c("无日期显示待定", date_cell(ev_tbd), "待定")
    ev_w = dict(ev_ok, date_start="2026-10-26", precision="week", status="arranged")
    c("当周显示", date_cell(ev_w), "2026-10-26 当周")
    c("人物列中文", persons_cell(ev_ok), "普京、佩泽希齐扬")
    c("基线无来源显示未回源", evidence_cell(ev_ok), "基线（未回源）")

    md = render_md(evs + [ev_ok], {"items": 0, "signals": 0, "signals_used": 0, "rejected": 0,
                                   "reject_breakdown": "", "new_events": 0, "upgraded": 0,
                                   "downgraded": 0, "lapsed_new": 0, "held": 0, "archived_now": 0},
                   {"title": "测试", "horizon": 120, "retention": 21, "tracks": cfg["tracks"],
                    "health": [], "transitions": [], "egress": "direct", "cfg_fp": "x"})
    c("报告含主表表头", "| 日期 | 会议 / 地点 | 关键人物 | 确认情况 | 依据 |" in md, True)
    c("报告无 RFC822 残留", re.search(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun),", md) is None, True)
    c("报告无 1970 哨兵", "Jan 1970" in md or "1970-01-01" in md, False)
    c("报告披露拒收原因", "拒收原因" in md, True)

    # ---- 10. 源解析
    rss = b"""<?xml version="1.0"?><rss><channel><item>
      <title>Putin to meet Pezeshkian - TASS</title>
      <link>https://x/1</link><description>&lt;a&gt;Iran&lt;/a&gt;</description>
      <pubDate>Sat, 24 Oct 2026 07:00:00 GMT</pubDate></item></channel></rss>"""
    pit = parse_feed(rss)
    c("RSS 解析条目数", len(pit), 1)
    c("RSS 标题", pit[0]["title"], "Putin to meet Pezeshkian - TASS")
    c("RSS 日期", iso(pit[0]["published"]), "2026-10-24")
    c("RSS 描述去标签", pit[0]["desc"], "Iran")
    atom = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>A</title><link href="https://y/1"/><updated>2026-10-24T01:00:00Z</updated></entry></feed>"""
    c("Atom 解析条目数", len(parse_feed(atom)), 1)
    c("Atom 链接", parse_feed(atom)[0]["link"], "https://y/1")
    c("纪元零值视为无日期", parse_pub_date("Thu, 01 Jan 1970 00:00:00 GMT"), None)
    c("两位数年份", iso(parse_pub_date("Wed, 29 Jul 26 12:37:14 +0200")), "2026-07-29")
    c("离谱年份拒收", parse_pub_date("Mon, 01 Jan 2035 00:00:00 GMT"), None)

    # ---- 11. 今日新增的结构性回归（都是真实抓取暴露的）
    import gzip as _gz
    c("gzip 响应体自动解压", b"<rss" in _decompress(_gz.compress(b"<rss><channel/></rss>"), "gzip"), True)
    c("非压缩体原样返回", _decompress(b"<rss/>", ""), b"<rss/>")
    c("否定句不算已举行",
      detect_status("Russia says she has not met with the US delegation")[0] != "held", True)
    c("否定句不触发 met with",
      detect_status("The two sides have not met with each other")[0] != "held", True)

    # 长正文污染：标题说「将会谈」，正文里一句「会见记者」不得把整条打成已举行
    long_item = [{
        "title": "Rubio and Lavrov will meet for talks - State Dept",
        "desc": ("The spokesman met with reporters in Washington. " * 12) + "G20 summit was mentioned.",
        "published": P, "link": "https://e/9", "source_id": "state", "source_name": "美国国务院", "tier": 1,
    }]
    long_sigs, _ = build_signals(long_item, cfg)
    c("长正文不改变标题状态", long_sigs[0]["status"] if long_sigs else "", "arranged")
    c("长正文里的城市不当地点", long_sigs[0]["location"] if long_sigs else "X", "")

    # 月份精度不能当磁铁
    evm = {"family": "bilateral", "persons": ["putin", "trump"], "date_start": "2026-11-01",
           "precision": "month", "location": "", "claims": [], "archived": False}
    sigm = {"family": "bilateral", "persons": ["putin", "zelensky"], "date_start": "2026-11-01",
            "precision": "month", "location": ""}
    c("月份精度不吞并不同人物", find_match(sigm, [evm]), None)
    # tbd 不能当万能胶水
    evt = {"family": "bilateral", "persons": ["putin", "trump"], "date_start": "",
           "precision": "tbd", "location": "", "claims": [], "archived": False}
    sigt = {"family": "bilateral", "persons": ["putin", "trump", "xi"], "date_start": "",
            "precision": "tbd", "location": ""}
    c("待定事件只在人物高度重合时合并", find_match(sigt, [evt]) is not None, True)
    sigt2 = dict(sigt, persons=["putin", "zelensky"])
    c("待定事件不跨人物合并", find_match(sigt2, [evt]), None)

    # 归档事件仍须被认出，否则同一篇旧稿每轮都会新建一条（实测重复 12 次）
    eva = {"family": "bilateral", "persons": ["lee_jm", "trump"], "date_start": "2026-09-27",
           "precision": "day", "location": "", "claims": [], "archived": True}
    siga = {"family": "bilateral", "persons": ["lee_jm", "trump"], "date_start": "2026-09-27",
            "precision": "day", "location": ""}
    c("同一篇旧稿不会因归档而重复建事件", find_match(siga, [eva]), eva)
    eva2 = {"family": "bilateral", "persons": ["lee_jm", "trump"], "date_start": "2026-09-27",
            "precision": "day", "location": "", "claims": [], "archived": False}
    c("分数相同时优先合并进未归档事件", find_match(siga, [eva, eva2]), eva2)

    # 基线吸附：11 月的 G20 报道必须落到 12-14 的基线
    sig_g20 = {"family": "g20", "title": "Trump to meet Xi at G20 summit in Miami",
               "date_start": "2026-11-01", "published": "2026-10-24",
               "location": "迈阿密", "persons": ["trump", "xi"]}
    snap = snap_to_anchor(sig_g20, cfg)
    c("G20 信号吸附到基线", (sig_g20["date_start"], snap["id"] if snap else None), ("2026-12-14", "a_g20"))
    sig_gecf = {"family": "gecf", "title": "Pezeshkian to attend GECF leaders summit in Moscow",
                "date_start": "2026-10-26", "published": "2026-10-24",
                "location": "莫斯科", "persons": ["pezeshkian", "putin"]}
    snap2 = snap_to_anchor(sig_gecf, cfg)
    c("GECF 领导人峰会不误吸到部长级会议", snap2["id"] if snap2 else None, "a_gecf_summit")

    c("情态动词降级（confirmed + could）",
      detect_status("Kremlin confirms Putin, Trump and Xi could hold a three-way meeting")[0],
      "proposed")
    sig_bi = {"family": "bilateral", "title": "Hegseth to meet Netanyahu in Washington",
              "date_start": "2026-10-26", "published": "2026-10-24",
              "location": "华盛顿", "persons": ["hegseth", "netanyahu"]}
    c("双边会晤不被吸附到基线", snap_to_anchor(sig_bi, cfg), None)

    # ---- 12. 输出一致性（离线）
    c("状态标签齐全", set(STATUS_LABEL) >= {"confirmed", "scheduled", "arranged", "proposed",
                                             "reported", "unconfirmed", "denied", "postponed",
                                             "canceled", "held", "lapsed"}, True)
    c("每个锚点都带核验查询", all(a.get("verify_query") for a in cfg["anchors"]), True)
    c("每条查询都有轨道", all(q.get("track") in cfg["tracks"] for q in cfg["queries"]), True)

    # ---- 13. 当日快照跨运行合并（append 会让行数翻倍，虚高信号量）
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "s.jsonl")
        d1 = [{"title": "A meets B", "link": "u1", "v": 1},
              {"title": "C meets D", "link": "u2", "v": 1}]
        d2 = [{"title": "A meets B", "link": "u1", "v": 2},   # 同一稿，后一次覆盖
              {"title": "E meets F", "link": "u3", "v": 2}]
        n1 = merge_jsonl(p, d1)
        n2 = merge_jsonl(p, d2)
        rows = [json.loads(x) for x in open(p, encoding="utf-8").read().splitlines() if x.strip()]
        c("合并：首次 2 条", n1, 2)
        c("合并：同日第二次仍 3 条（不翻倍）", n2, 3)
        c("合并：同稿取后一次", [r["v"] for r in rows if r["link"] == "u1"], [2])
        c("合并：顺序稳定（先出现的在前）", [r["link"] for r in rows], ["u1", "u2", "u3"])
        # 半行（上次进程被杀）不能让整份快照报废
        with open(p, "a", encoding="utf-8") as f:
            f.write('{"title": "半行')
        c("合并：能容忍截断的半行", merge_jsonl(p, []), 3)

    total = c.n
    if c.fail:
        print(f"[selftest] {total - len(c.fail)}/{total} 通过，{len(c.fail)} 项失败：")
        print("\n".join(c.fail))
        return 1
    print(f"[selftest] {total}/{total} 全部通过")
    return 0


# ====================================================================== CLI

def main(argv: Optional[Sequence[str]] = None) -> int:
    global PROXY_OVERRIDE, VERBOSE, TODAY
    ap = argparse.ArgumentParser(description="俄乌/美伊关键人物会晤日程监测")
    ap.add_argument("--selftest", action="store_true", help="离线自检，不联网")
    ap.add_argument("--proxy", default=None, help="出口代理；direct 表示直连")
    ap.add_argument("--today", default=None, help="覆盖运行日（仅测试用）")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    PROXY_OVERRIDE = args.proxy
    VERBOSE = args.verbose
    if args.today:
        TODAY = date.fromisoformat(args.today)

    if args.selftest:
        return selftest()
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
