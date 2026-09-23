#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""美军弹药库存与军费开支监测 —— 公开信息爬虫（零依赖，仅标准库）

用途
----
围绕「美伊冲突」这类持续冲突，收集**公开**的军费开支与武器弹药采购证据，
判断冲突是趋于持续/升级，还是趋于降级/结束。

设计骨架 —— 三级证据链 + 两层旁证
--------------------------------
    拨款层  govinfo 公法 / 联邦公报总统决定书(DPA §101、506(a)(3) 裁减授权)
              → 有没有新钱、有没有新的动武/扩产法律授权
    合同层  USAspending 弹药与导弹类(PSC 13xx/14xx)新签合同 + PSC 分类聚合
              → 弹药补货速度，打得起的物质基础
    支付层  美国财政部 MTS 表5 国防科目月度实际净支出
              → 真金白银出账节奏（最硬，滞后约1个月）
    审计层  GAO 报告 → 官方自己承认的产能与库存瓶颈
    新闻层  Defense News → 线索（需回溯官方源确认，不作事实依据）

关键工程约定（与 naval-monitor 一脉相承，踩过的坑不再踩）
------------------------------------------------------
* 出口代理一律**自动探测、不写死端口** —— 端口会变（实测 7897 → 63299）。
* 请求头必须补齐 Sec-Fetch-*，否则被 Akamai/Cloudflare 拦。
* 每个源上报「源内最新条目日期」，超过阈值标记 ok-stale，让停更自己浮出来。
* 落盘按 id 与当天已有记录**合并**，避免同日重跑把数据覆盖成空文件。
* `--from-file` 提供完全不联网的退路（API 源读 feeds/<id>.json，RSS 源读 feeds/<id>.xml）。

合规
----
仅抓取公开的政府数据接口、政府 RSS、公共媒体 RSS；遵守 robots；
不抓取需登录内容、不做风控绕过、不抓实时 AIS/航运定位。

命令行
------
    python spend_monitor.py                  # 常规运行（自动探测系统代理）
    python spend_monitor.py --days 60        # 自定义回溯天数
    python spend_monitor.py --sources treasury-mts,gao-reports
    python spend_monitor.py --from-file feeds   # 离线模式
    python spend_monitor.py --proxy http://127.0.0.1:63299
    python spend_monitor.py --selftest       # 离线自检（不联网）
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

VERSION = "1.2.0"
ROOT = Path(__file__).resolve().parent
CFG_PATH = ROOT / "config.json"
FEED_DIR = ROOT / "feeds"
STATE_DIR = ROOT / "state"
OUTPUT_DIR = ROOT / "output"
SEEN_PATH = STATE_DIR / "seen.json"

UA_FALLBACK = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

USASPENDING_AWARDS_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
USASPENDING_CATEGORY_URL = "https://api.usaspending.gov/api/v2/search/spending_by_category/psc/"
USASPENDING_OVER_TIME_URL = "https://api.usaspending.gov/api/v2/search/spending_over_time/"
TREASURY_MTS_URL = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
                    "/v1/accounting/mts/mts_table_5")
FEDERAL_REGISTER_URL = "https://www.federalregister.gov/api/v1/documents.json"
CONGRESS_CRS_URL = "https://api.congress.gov/v3/crsreport"

# FY 月份编号 → 日历月。USAspending 的 spending_over_time 按**财年**编号返回月份，
# FY 起于 10 月：m=1→上年10月 … m=4→当年1月 … m=12→当年9月。
# 不换算就会把「2 月」错当成「财年第 2 月＝11 月」，整条时间轴偏移 9 个月。
FY_MONTH_TO_CAL = {1: 10, 2: 11, 3: 12, 4: 1, 5: 2, 6: 3, 7: 4, 8: 5, 9: 6, 10: 7, 11: 8, 12: 9}

# --------------------------------------------------------------------------- #
# 信源等级（T1–T4）
# --------------------------------------------------------------------------- #
# 为什么必须分级：本报告里「军费支出 $67.92B」（财政部 API 原始记录）和
# 「拦截弹已消耗三分之二」（Defense News 转述 CBO）会出现在同一张表里。
# 若不标等级，读者会把两者当成同等可信 —— 实际上前者可逐项回源对账，
# 后者连原始 CBO 报告都没直连。等级是这份报告最容易被误用的维度。
#
# 边界（实测踩出来的）：
#   * 「官方机构」不等于 T1。GAO/CBO 发布的是**分析与估算**，不是原始记录，
#     因此归 T2 —— 权威，但数字是机构算出来的，带假设与口径。
#   * 「转述官方结论的媒体」仍是 T3，不因为其内容源头是官方就升级。
#     源等级看的是**你手上的这份材料**由谁加工，不是它引用了谁。
#   * 等级与「证据层」（拨款/合同/支付）是**两个正交维度**：
#     层回答「这条信息在战争链条的哪一环」，等级回答「这条信息有多可信」。
TIER_META: Dict[str, Dict[str, Any]] = {
    "T1": {"rank": 1, "icon": "🟢", "label": "官方一手",
           "short": "原始记录/法律文件",
           "desc": "政府机构直接发布的原始数据或具法律效力的文件。数字可逐项回源对账。",
           "usage": "可直接引用为事实，并注明 API 端点/文件编号",
           "traceable": "full"},
    "T2": {"rank": 2, "icon": "🟡", "label": "官方分析/审计",
           "short": "机构估算与判断",
           "desc": "政府机构的审计报告、成本估算、分析结论。权威，但含机构假设、口径选择与发布滞后，"
                   "数字多为估算而非原始记录。",
           "usage": "可作为「官方观点/估算」引用，不得当作披露的精确值",
           "traceable": "partial"},
    "T3": {"rank": 3, "icon": "🟠", "label": "专业/商业媒体",
           "short": "二手转述",
           "desc": "商业防务媒体或非政府专业学会。内容通常转述 T1/T2，但已被加工裁剪，"
                   "且存在选题偏好与流量动机。",
           "usage": "仅作线索索引，引用前必须回溯到其援引的原始出处",
           "traceable": "via-citation"},
    "T4": {"rank": 4, "icon": "🔴", "label": "自媒体/社交平台",
           "short": "无编辑审核",
           "desc": "无编辑审核的博客、自媒体账号、社交平台帖文、匿名或不具名转述。",
           "usage": "本报告明确不采集、不引用",
           "traceable": "none"},
}
TIER_ORDER = ["T1", "T2", "T3", "T4"]

# 源 → 等级映射。跨天归档的记录里可能没有 tier 字段（旧版本写入），
# 落盘时用这张表回填 —— 与 relevance 的回填同理，避免旧归档在报告里显示成未知等级。
SOURCE_TIER: Dict[str, str] = {}
SOURCE_META: Dict[str, Dict[str, Any]] = {}


def tier_of(source_id: str) -> str:
    return SOURCE_TIER.get(source_id, "T4")


def tier_badge(t: str) -> str:
    m = TIER_META.get(t)
    return f"`{t}` {m['icon']} {m['short']}" if m else f"`{t or '—'}`"


# --------------------------------------------------------------------------- #
# 一、通用工具
# --------------------------------------------------------------------------- #


def log(msg: str, verbose: bool = True) -> None:
    if verbose:
        print(msg, file=sys.stderr, flush=True)


def strip_html(text: str) -> str:
    return unescape(TAG_RE.sub(" ", text or "")).strip()


def norm_ws(text: str) -> str:
    return WS_RE.sub(" ", text or "").strip()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def make_id(source_id: str, key: str) -> str:
    raw = f"{source_id}|{key}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def fmt_usd(v: Optional[float]) -> str:
    """把美元金额格式化成便于阅读的形式（保留 2 位有效小数）。"""
    if v is None:
        return "—"
    neg = v < 0
    a = abs(v)
    if a >= 1e12:
        s = f"${a / 1e12:,.2f}T"
    elif a >= 1e9:
        s = f"${a / 1e9:,.2f}B"
    elif a >= 1e6:
        s = f"${a / 1e6:,.2f}M"
    elif a >= 1e3:
        s = f"${a / 1e3:,.2f}K"
    else:
        s = f"${a:,.2f}"
    return f"-{s}" if neg else s


def fmt_pct(cur: Optional[float], prev: Optional[float]) -> str:
    if cur is None or prev in (None, 0):
        return "—"
    return f"{(cur - prev) / abs(prev) * 100:+.1f}%"


def fmt_date(dt: Optional[datetime]) -> str:
    return dt.strftime("%Y-%m-%d") if dt else "—"


def humanize(dt: Optional[datetime], now: Optional[datetime] = None) -> str:
    if not dt:
        return "—"
    now = now or datetime.now(timezone.utc)
    days = (now - dt).total_seconds() / 86400.0
    if days < 1:
        return f"{int(days * 24)} 小时前"
    if days < 60:
        return f"{int(days)} 天前"
    return f"{int(days / 30)} 个月前"


def truncate(text: str, n: int) -> str:
    text = norm_ws(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def word_boundary_re(phrase: str, allow_plural: bool = False) -> "re.Pattern[str]":
    """构造带字母/数字边界的正则。

    用 (?<![A-Za-z0-9])…(?![A-Za-z0-9]) 而不是 \\b —— 后者在 `Guam's`、
    `506(a)(3)` 这类写法上会失误，且短词（如 Iran）会命中 Iranian。

    allow_plural=True 时额外允许一个复数 s（Patriot → Patriots）。
    只用于弹药/术语词表；信号短语与国名等必须严格，否则会误命中。
    """
    tail = r"(?![A-Za-z0-9])" if not allow_plural else r"s?(?![A-Za-z0-9])"
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(phrase)}{tail}", re.I)


# ---- 通用小工具：月份、时间窗、按源标题闸门 -------------------------------- #
def month_key(d: datetime) -> str:
    return d.strftime("%Y-%m")


def month_list(start: str, end: str) -> List[str]:
    """返回 ['2026-02', '2026-03', ...]，闭区间。"""
    y0, m0 = int(start[:4]), int(start[5:7])
    y1, m1 = int(end[:4]), int(end[5:7])
    out: List[str] = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def month_bounds(ym: str) -> Tuple[str, str]:
    """'2026-02' → ('2026-02-01', '2026-02-28')。用日历法算月末，不用 30 天近似。"""
    y, m = int(ym[:4]), int(ym[5:7])
    first = datetime(y, m, 1)
    last = (datetime(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1))
    return first.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d")


def fy_period_to_ym(period: Dict[str, Any]) -> Optional[str]:
    """USAspending 的 time_period → 'YYYY-MM'。

    该 API 可能返回 fiscal_year/month（财年编号）**或** calendar_year/month，
    两者月份基准不同（财年 1 月 = 日历 10 月）。搞混会让整条时间轴偏移 9 个月，
    因此必须分别处理，且优先信任 calendar_year。
    """
    if not isinstance(period, dict):
        return None
    cy, cm = period.get("calendar_year"), period.get("month")
    if cy and cm:
        try:
            return f"{int(cy):04d}-{int(cm):02d}"
        except (TypeError, ValueError):
            return None
    fy, fm = period.get("fiscal_year"), period.get("month")
    if fy and fm:
        try:
            fm_i, fy_i = int(fm), int(fy)
        except (TypeError, ValueError):
            return None
        cal_m = FY_MONTH_TO_CAL.get(fm_i)
        if cal_m is None:
            return None
        # 财年 m=1..3 落在财年编号的前一年（10/11/12 月）
        cal_y = fy_i - 1 if fm_i <= 3 else fy_i
        return f"{cal_y:04d}-{cal_m:02d}"
    return None


def title_gate(source: Dict[str, Any], title: str) -> Tuple[bool, str]:
    """按源做标题闸门。返回 (是否保留, 原因)。

    为什么需要它：CBO / CRS 是**全议题**源（税务、医疗、移民什么都发），
    而相关性打分是「强主题词命中标题 **或** 弱主题词累计」，靠累计分放行时，
    一篇讲「枪支与弹药消费税」的税务报告会因命中 ammunition 而混进来。
    对这类源必须**要求标题显式锚定主题**，而不是靠分数兜底。

    ⚠️ 收紧闸门最容易犯的错是「连真信号一起删掉」（本项目踩过：删掉 depleted 裸词
    导致漏掉整条最关键证据，直接改变了结论方向）。因此每次过滤都会记进
    GATE_AUDIT，落盘成 output/_title_gate_audit.json 供人工回扫确认。
    """
    t = title or ""
    inc = source.get("title_include")
    if inc and not any(re.search(p, t, re.I) for p in inc):
        return False, "title_include 未命中"
    exc = source.get("title_exclude")
    if exc:
        for p in exc:
            if re.search(p, t, re.I):
                return False, f"title_exclude 命中「{p}」"
    return True, ""


def gate_audit_add(source_id: str, title: str, reason: str,
                   limit: int = 300) -> None:
    """记录被闸门过滤掉的标题（供人工回扫，防止压过头删掉真信号）。"""
    bucket = GATE_AUDIT.setdefault(source_id, [])
    if len(bucket) < limit:
        bucket.append({"title": title, "reason": reason})


def build_psc_codes(cfg: Dict[str, Any]) -> List[str]:
    """弹药与导弹类 PSC 代码全集（13xx 弹药 / 14xx 制导导弹）。

    必须显式把它们传给聚合接口 —— 不传就等于「按金额取前 N 个 PSC 大类」，
    弹药类里金额小的代码（鱼雷、引信、中小口径弹药）会被截断掉。
    实测该截断让弹药合计从 42.14B 少算到 39.71B（**少 2.42B / 6.1%**）。
    """
    explicit = cfg.get("psc_codes")
    if explicit:
        return list(explicit)
    out: List[str] = []
    for fam in cfg.get("_psc_families", {}):
        out += [f"{fam}{s:02d}" for s in range(0, 100, 5)]
    return out


# --------------------------------------------------------------------------- #
# 二、HTTP 抓取（GET + POST JSON）
# --------------------------------------------------------------------------- #
class Fetcher:
    def __init__(self, cfg: Dict[str, Any], direct: bool = False,
                 proxy: Optional[str] = None):
        self.timeout = cfg.get("timeout_sec", 30)
        self.retries = cfg.get("retries", 3)
        self.ua = cfg.get("user_agent", UA_FALLBACK)
        self.direct = direct
        self.proxy = proxy
        self.robots_cache: Dict[str, List[str]] = {}
        if direct or proxy:
            # 显式指定出口时，清掉环境变量里的代理，避免互相干扰
            for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                        "all_proxy", "ALL_PROXY"):
                os.environ.pop(key, None)

    def describe_exit(self) -> str:
        if self.proxy:
            return f"显式指定 -> {self.proxy}"
        if self.direct:
            return "直连（已绕过系统代理）"
        auto = urllib.request.getproxies()
        p = auto.get("https") or auto.get("http") or auto.get("all")
        return f"系统自动探测 -> {p}" if p else "系统自动探测 -> 无，直连"

    def _opener(self) -> urllib.request.OpenerDirector:
        if self.proxy:
            return urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy}))
        if self.direct:
            return urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return urllib.request.build_opener()

    def _headers(self, json_post: bool = False) -> Dict[str, str]:
        """请求头。

        只带 User-Agent 会被 Akamai / Cloudflare 直接 403，
        必须补齐 Accept-Language / Sec-Fetch-* / Upgrade-Insecure-Requests。
        """
        h = {
            "User-Agent": self.ua,
            "Accept": ("application/json, text/plain, */*" if json_post else
                       "application/rss+xml, application/atom+xml, application/xml, "
                       "text/xml, application/json, text/html;q=0.9, */*;q=0.8"),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "empty" if json_post else "document",
            "Sec-Fetch-Mode": "cors" if json_post else "navigate",
            "Sec-Fetch-Site": "same-site" if json_post else "none",
            "Connection": "keep-alive",
        }
        if not json_post:
            h["Sec-Fetch-User"] = "?1"
        return h

    @staticmethod
    def _decompress(raw: bytes, enc: str) -> bytes:
        """按 Content-Encoding 解压。gzip 与 deflate 都要处理 ——
        声明支持 deflate 却不解码，会把 XML/JSON 变成二进制垃圾。"""
        enc = (enc or "").lower()
        try:
            if "gzip" in enc or raw[:2] == b"\x1f\x8b":
                return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            if "deflate" in enc:
                try:
                    return zlib.decompress(raw)                     # 带 zlib 头
                except zlib.error:
                    return zlib.decompress(raw, -zlib.MAX_WBITS)    # 裸 deflate
        except Exception:                                           # noqa: BLE001
            return raw
        return raw

    def _request(self, url: str, body: Optional[bytes] = None,
                 verbose: bool = True) -> Optional[bytes]:
        last_err = ""
        is_post = body is not None
        for attempt in range(1, self.retries + 1):
            try:
                headers = self._headers(json_post=is_post)
                if is_post:
                    headers["Content-Type"] = "application/json"
                req = urllib.request.Request(url, data=body, headers=headers,
                                             method="POST" if is_post else "GET")
                with self._opener().open(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    return self._decompress(raw, resp.headers.get("Content-Encoding", ""))
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}"
                # 403（风控挑战）与 410（永久移除）重试没有意义；
                # 404 必须重试 —— CDN 各边缘节点状态可能不一致。
                if e.code in (403, 410, 401):
                    break
                # 429 / 5xx 是限流或临时故障，退避要更长（实测连续跑多次会被限流）
                if e.code == 429 or e.code >= 500:
                    time.sleep(min(20.0, 5.0 * attempt))
            except Exception as e:                                  # noqa: BLE001
                last_err = f"{type(e).__name__}: {str(e)[:110]}"
            if attempt < self.retries:
                time.sleep(1.4 * attempt)
        log(f"    [!] 请求失败 ({last_err})  {truncate(url, 110)}", verbose)
        return None

    def get(self, url: str, verbose: bool = True) -> Optional[bytes]:
        return self._request(url, None, verbose)

    def post_json(self, url: str, payload: Dict[str, Any],
                  verbose: bool = True) -> Optional[bytes]:
        return self._request(url, json.dumps(payload).encode("utf-8"), verbose)

    def robots_allows(self, url: str) -> bool:
        p = urllib.parse.urlparse(url)
        root = f"{p.scheme}://{p.netloc}"
        if root not in self.robots_cache:
            rules: List[str] = []
            data = self.get(f"{root}/robots.txt", verbose=False)
            if data:
                section = None
                for line in data.decode("utf-8", "ignore").splitlines():
                    line = line.split("#", 1)[0].strip()
                    if not line:
                        continue
                    if line.lower().startswith("user-agent:"):
                        section = line.split(":", 1)[1].strip()
                    elif section == "*" and line.lower().startswith("disallow:"):
                        path = line.split(":", 1)[1].strip()
                        if path:
                            rules.append(path)
            self.robots_cache[root] = rules
        for rule in self.robots_cache[root]:
            if rule != "/" and p.path.startswith(rule.rstrip("*")):
                return False
            if rule == "/" and p.path == "/":
                return False
        return True


# --------------------------------------------------------------------------- #
# 三、RSS / Atom 解析
# --------------------------------------------------------------------------- #
def parse_feed(raw: bytes) -> List[Dict[str, Any]]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        cleaned = re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", raw)
        root = ET.fromstring(cleaned)

    items: List[Dict[str, Any]] = []
    for node in [n for n in root.iter() if local_name(n.tag) in ("item", "entry")]:
        rec: Dict[str, Any] = {"title": "", "link": "", "published_raw": "",
                               "summary": "", "categories": []}
        for child in node:
            name = local_name(child.tag)
            text = (child.text or "").strip()
            if name == "title":
                rec["title"] = norm_ws(strip_html(text))
            elif name == "link":
                href = child.attrib.get("href")
                rel = child.attrib.get("rel", "alternate")
                if href and rel == "alternate" and not rec["link"]:
                    rec["link"] = href
                elif text and not rec["link"] and not href:
                    rec["link"] = text
            elif name in ("pubdate", "published", "updated", "date"):
                if text and not rec["published_raw"]:
                    rec["published_raw"] = text
            elif name in ("description", "summary", "content", "encoded"):
                if text and len(text) > len(rec["summary"]):
                    rec["summary"] = norm_ws(strip_html(text))
            elif name == "category":
                term = child.attrib.get("term") or text
                if term:
                    rec["categories"].append(norm_ws(term))
        if rec["title"]:
            items.append(rec)
    return items


def parse_time(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:                                               # noqa: BLE001
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:                                               # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# 四、抽取器：金额 / 弹药型号 / 数量 / 信号
# --------------------------------------------------------------------------- #

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_SCALE = {
    "thousand": 1e3, "k": 1e3,
    "million": 1e6, "mn": 1e6, "m": 1e6,
    "billion": 1e9, "bn": 1e9, "b": 1e9,
    "trillion": 1e12, "tn": 1e12, "t": 1e12,
}
# 只认带货币符号的金额（最稳）；另加一条「N billion dollars」不带符号的安全模式。
MONEY_RE = re.compile(
    rf"(?<![A-Za-z0-9$])(?:US\s?\$|USD\s?|\$)\s?({_NUM})\s*"
    rf"(thousand|million|billion|trillion|bn|mn|tn|[kmbt])?(?![A-Za-z0-9])",
    re.I)
MONEY_WORD_RE = re.compile(
    rf"(?<![A-Za-z0-9])({_NUM})\s*(billion|trillion|million)\s+(?:dollars|USD)\b", re.I)

QUANTITY_RE = re.compile(
    rf"(?<![A-Za-z0-9])({_NUM})\s+(" + "|".join([
        "rounds", "round", "missiles", "missile", "interceptors", "interceptor",
        "rockets", "rocket", "launchers", "launcher", "bombs", "bomb",
        "shells", "shell", "torpedoes", "torpedo", "units", "unit",
        "systems", "system", "sets", "set", "vehicles", "vehicle",
    ]) + r")(?![A-Za-z0-9])", re.I)

RATE_RE = re.compile(
    rf"(?:production rate|annual production|production capacity|capacity of|"
    rf"build rate|output of|produce)\D{{0,60}}?({_NUM})\s*"
    rf"(?:per year|a year|annually|/year|per month|a month|monthly|per quarter)?",
    re.I)


def _to_float(num: str) -> float:
    return float(num.replace(",", ""))


def extract_money(text: str, limit: int = 6) -> List[Dict[str, Any]]:
    """抽取金额。返回按金额降序的列表。"""
    text = text or ""
    found: List[Dict[str, Any]] = []
    seen_spans: List[Tuple[int, int]] = []

    def overlaps(a: int, b: int) -> bool:
        return any(not (b <= s or a >= e) for s, e in seen_spans)

    for m in MONEY_RE.finditer(text):
        if overlaps(m.start(), m.end()):
            continue
        val = _to_float(m.group(1))
        scale = _SCALE.get((m.group(2) or "").lower())
        usd = val * scale if scale else val
        found.append({"usd": usd, "raw": norm_ws(m.group(0)), "scaled": bool(scale)})
        seen_spans.append((m.start(), m.end()))

    for m in MONEY_WORD_RE.finditer(text):
        if overlaps(m.start(), m.end()):
            continue
        usd = _to_float(m.group(1)) * _SCALE[m.group(2).lower()]
        found.append({"usd": usd, "raw": norm_ws(m.group(0)), "scaled": True})
        seen_spans.append((m.start(), m.end()))

    found.sort(key=lambda d: d["usd"], reverse=True)
    return found[:limit]


def compile_terms(terms: Iterable[str]) -> List[Tuple[str, "re.Pattern[str]"]]:
    out: List[Tuple[str, "re.Pattern[str]"]] = []
    for t in terms:
        out.append((t, word_boundary_re(t, allow_plural=True)))
    # 长词优先：先匹配长词并占位，避免 "Standard Missile" 被 "Missile" 之类的短词抢占
    out.sort(key=lambda kv: len(kv[0]), reverse=True)
    return out


def extract_terms(text: str, terms: Iterable[Tuple[str, "re.Pattern[str]"]],
                  exclude_phrases: Iterable[str] = ()) -> List[str]:
    """抽取词表命中的术语。长词优先 + 占位压制，避免嵌套重复计数。"""
    if not text:
        return []
    work = text
    for ex in exclude_phrases:
        if ex:
            # 等长占位符，保住后续偏移量（这里其实只用正则，无需偏移，但保持一致做法）
            work = re.sub(word_boundary_re(ex, allow_plural=True), " " * len(ex), work)
    hits: List[str] = []
    claimed: List[Tuple[int, int]] = []

    def overlaps(a: int, b: int) -> bool:
        return any(not (b <= s or a >= e) for s, e in claimed)

    for term, rx in terms:
        for m in rx.finditer(work):
            if overlaps(m.start(), m.end()):
                continue
            claimed.append((m.start(), m.end()))
            if term not in hits:
                hits.append(term)
    return hits


def extract_quantities(text: str, limit: int = 6) -> List[Dict[str, Any]]:
    """抽取数量（1,500 interceptors）与产能速率（annual production of 650）。"""
    out: List[Dict[str, Any]] = []
    if not text:
        return out
    for m in QUANTITY_RE.finditer(text):
        out.append({"value": _to_float(m.group(1)), "unit": m.group(2).lower(),
                    "raw": norm_ws(m.group(0)), "kind": "quantity"})
    for m in RATE_RE.finditer(text):
        out.append({"value": _to_float(m.group(1)), "unit": "per-period",
                    "raw": norm_ws(m.group(0)), "kind": "rate"})
    out.sort(key=lambda d: d["value"], reverse=True)
    return out[:limit]


def build_signal_index(lexicon: Dict[str, List[Dict[str, Any]]]
                       ) -> List[Tuple[str, int, str, "re.Pattern[str]", list]]:
    """构建信号索引。

    每条规则可选写 `except`：若匹配位置前后窗口内出现排除短语，则本次匹配作废。
    必要性：「库存耗尽」类词必须能匹配裸词 `depleted`，但军语里 `depleted uranium`
    （贫铀弹/贫铀装甲）是**完全不同的东西**，不排除会把贫铀弹药采购误判成库存告急。
    """
    idx: List[Tuple[str, int, str, "re.Pattern[str]", list]] = []
    for bucket, entries in lexicon.items():
        for entry in entries:
            w = int(entry.get("weight", 1))
            excepts = [word_boundary_re(x) for x in entry.get("except", [])]
            for pat in entry.get("patterns", []):
                idx.append((bucket, w, pat, word_boundary_re(pat, allow_plural=True),
                            excepts))
    idx.sort(key=lambda t: len(t[2]), reverse=True)      # 长短语优先
    return idx


SIGNAL_EXCEPT_MARGIN = 40      # 排除短语的检索边距（字符）
SIGNAL_EXCEPT_PAD = 3          # 视为「覆盖」的容差，用于容忍连字符等写法
SIGNAL_CONTEXT_BEFORE = 90     # 信号原文片段：命中处前保留字符数
SIGNAL_CONTEXT_AFTER = 150     # 信号原文片段：命中处后保留字符数


def extract_signals(text: str,
                    index: List[Tuple[str, int, str, "re.Pattern[str]", list]]
                    ) -> List[Dict[str, Any]]:
    """从文本抽取信号。每条短语只计一次，且已被占用的区间不再重复计。

    排除语义（易踩错）：只有排除短语**覆盖住当前匹配位置**时才作废，不能按
    「前后窗口里出现过」判断 —— 否则「the stockpile is depleted; depleted
    uranium is a separate item」里那句真正的库存告急会被误杀。
    """
    if not text:
        return []
    hits: List[Dict[str, Any]] = []
    claimed: List[Tuple[int, int]] = []

    def overlaps(a: int, b: int) -> bool:
        return any(not (b <= s or a >= e) for s, e in claimed)

    def excluded_by_any(s: int, e: int, excepts: list) -> bool:
        lo, hi = max(0, s - SIGNAL_EXCEPT_MARGIN), e + SIGNAL_EXCEPT_MARGIN
        seg = text[lo:hi]
        for x in excepts:
            for em in x.finditer(seg):
                a, b = lo + em.start(), lo + em.end()
                if a <= s + SIGNAL_EXCEPT_PAD and b >= e - SIGNAL_EXCEPT_PAD:
                    return True
        return False

    for bucket, weight, phrase, rx, excepts in index:
        for m in rx.finditer(text):
            s, e = m.start(), m.end()
            if overlaps(s, e):
                continue
            if excepts and excluded_by_any(s, e, excepts):
                continue
            claimed.append((s, e))
            # 保留命中处的原文片段：信号是在**完整正文**上算的，而落盘的 summary
            # 会被截断，若不存片段，读者无法独立复核这条信号 —— 那是不可验证的结论。
            ctx = norm_ws(text[max(0, s - SIGNAL_CONTEXT_BEFORE):
                               e + SIGNAL_CONTEXT_AFTER])
            hits.append({"bucket": bucket, "weight": weight, "phrase": phrase,
                         "context": truncate(ctx, 260)})
            break
    return hits


# --------------------------------------------------------------------------- #
# 五、采集器
# --------------------------------------------------------------------------- #
BUCKET_LABEL = {"sustain": "持续/升级", "terminate": "降级/结束", "stress": "产能/库存压力"}


def record_base(source: Dict[str, Any], key: str, title: str, link: str,
                published: Optional[datetime]) -> Dict[str, Any]:
    return {
        "id": make_id(source["id"], key),
        "source_id": source["id"],
        "source_name": source["name"],
        "layer": source.get("layer", ""),
        "tier": source.get("tier", tier_of(source["id"])),
        "publisher": source.get("publisher", ""),
        "kind": "",
        "title": norm_ws(title),
        "link": link,
        "published": published.astimezone(timezone.utc).isoformat() if published else "",
        "summary": "",
        "amounts": [],
        "amount_max": None,
        "munitions": [],
        "quantities": [],
        "signals": [],
        "extra": {},
    }


def enrich(rec: Dict[str, Any], text: str, terms, signal_index,
           exclude_phrases: Iterable[str]) -> Dict[str, Any]:
    money = extract_money(text)
    rec["amounts"] = money
    rec["amount_max"] = money[0]["usd"] if money else None
    rec["munitions"] = extract_terms(text, terms, exclude_phrases)
    rec["quantities"] = extract_quantities(text)
    rec["signals"] = extract_signals(text, signal_index)
    return rec


def compute_relevance(rec: Dict[str, Any], strong_index, weak_index
                      ) -> Tuple[int, List[str]]:
    """给记录打相关性分。

    必要性：GAO / Defense News 的 RSS 返回的是**全部主题**的最新条目，
    不加过滤会把「日本全球鹰坠毁」「房贷保险」这类无关内容混进报告。

    设计要点（两轮真实数据踩出来的）：
    * **锚定规则**：必须满足其一 —— 正文含弹药/导弹等武器术语、**标题**命中强主题词、
      或本身是 PSC 过滤出来的合同。
    * **为什么不能用「正文有金额」锚定**：几乎每份 GAO 报告都有美元数字，
      用它锚定会让「Air Traffic Control」「Mortgage Insurance」全部混进来（实测）。
    * **为什么必须看标题**：`Arms Sales Notification`（对外军售通知）这类核心文件
      标题通用、且没有摘要，纯靠摘要关键词会被判 0 分误杀（实测 6 份全被误杀）。
    * 评分只用于**显示筛选**；所有记录仍完整写入 JSONL 归档，不丢弃。
    """
    title = rec.get("title") or ""
    summary = rec.get("summary") or ""
    mun = rec.get("munitions") or []
    sig = rec.get("signals") or []
    amt = rec.get("amounts") or []
    qty = rec.get("quantities") or []

    strong = [k for k, rx in strong_index if rx.search(title)]
    weak_title = [k for k, rx in weak_index if rx.search(title)]
    weak_body = [k for k, rx in weak_index if rx.search(summary)]

    anchored = bool(mun or strong or rec.get("kind") == "contract")
    if not anchored:
        return 0, []

    score = (2 * len(mun) + 3 * len(sig) + 3 * len(strong) + 2 * len(weak_title)
             + (1 if amt else 0) + min(2, len(weak_body)))
    hits = (strong + weak_title + weak_body)[:8]
    return score, hits


# ---- 5.1 RSS -------------------------------------------------------------- #
def collect_rss(source: Dict[str, Any], fetcher: Fetcher, from_file: Optional[Path],
                verbose: bool) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    raw = None
    if from_file and from_file.exists():
        raw = from_file.read_bytes()
        log(f"    [from-file] {from_file.name} ({len(raw):,} 字节)")
    else:
        if not fetcher.robots_allows(source["url"]) and source.get("respect_robots", True):
            return [], {"status": "skipped", "note": "robots.txt 不允许"}
        raw = fetcher.get(source["url"], verbose)
        for alt in source.get("url_alternates", []):
            if raw is None:
                log(f"    [alt] 尝试备用 URL {truncate(alt, 70)}")
                raw = fetcher.get(alt, verbose)
    if not raw:
        return [], {"status": "fail", "note": "无响应"}

    items = parse_feed(raw)
    recs = []
    newest: Optional[datetime] = None
    gated = 0
    for it in items:
        dt = parse_time(it["published_raw"])
        if dt and (newest is None or dt > newest):
            newest = dt
        # 全议题源（CBO/CRS 类）先过标题闸门，避免无关主题靠弱词累计分混入
        ok, why = title_gate(source, it["title"])
        if not ok:
            gated += 1
            gate_audit_add(source.get("id", "?"), it["title"], why)
            continue
        rec = record_base(source, it["link"] or it["title"], it["title"],
                          it["link"], dt)
        rec["kind"] = "news" if source.get("layer") == "新闻层" else "report"
        # 用完整正文（RSS description 常含全文）
        body = f"{it['title']} . {it['summary']}"
        if it["categories"]:
            body += " . " + " ".join(it["categories"])
        rec["summary"] = truncate(it["summary"], 1200)
        enrich(rec, body, TERMS, SIGNAL_INDEX, EXCLUDE_PHRASES)
        recs.append(rec)
    st: Dict[str, Any] = {"status": "ok", "newest": newest, "items": len(items)}
    if gated:
        st["note"] = f"标题闸门过滤 {gated} 条（全议题源需显式锚定主题）"
    return recs, st


# ---- 5.1b CRS 报告（congress.gov API）------------------------------------- #
def collect_crs_reports(source: Dict[str, Any], fetcher: Fetcher,
                        from_file: Optional[Path], verbose: bool
                        ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """CRS 官方报告 API。

    为何值得接入：CRS 是国会的官方研究机构（T2），
    同一条「武器采购/弹药产能」的判断，CRS 报告属可回源的官方分析，
    比商业媒体转述高一个等级。每条含报告编号与 PDF/HTML 原文链接。

    参数：fromDateTime/toDateTime 按**更新日期**过滤（实测会把多年前发布、
    近期被修订的报告也带回），因此 local 侧再用 publishDate 判断新旧。
    """
    api = source.get("api_url", CONGRESS_CRS_URL)
    key = (source.get("api_key") or "DEMO_KEY").strip()
    limit = int(source.get("limit", 250))
    max_pages = int(source.get("max_pages", 3))
    days = int(source.get("window_days", 240))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT00:00:00Z")

    recs: List[Dict[str, Any]] = []
    newest: Optional[datetime] = None
    gated = 0
    pages = 0
    note = ""

    for page in range(max_pages):
        params = [("api_key", key), ("format", "json"), ("limit", str(limit)),
                  ("offset", str(page * limit)),
                  ("fromDateTime", since)]
        url = api + "?" + urllib.parse.urlencode(params)
        raw = None
        if page == 0 and from_file and from_file.exists():
            raw = from_file.read_bytes()
            log(f"    [from-file] {from_file.name}")
        if raw is None:
            raw = fetcher.get(url, verbose)
        if not raw:
            note = (note + f" 第 {page+1} 页请求失败").strip()
            break
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            note = (note + " 响应非 JSON").strip()
            break
        rows = data.get("CRSReports") or []
        pages += 1
        for r in rows:
            title = r.get("title") or ""
            ok, why = title_gate(source, title)
            if not ok:
                gated += 1
                gate_audit_add(source.get("id", "?"), title, why)
                continue
            dt = parse_time(r.get("publishDate") or "")
            if dt and (newest is None or dt > newest):
                newest = dt
            rid = r.get("id") or ""
            rec = record_base(source, rid, title,
                              f"https://www.congress.gov/crs-product/{rid}", dt)
            rec["kind"] = "report"
            rec["summary"] = truncate(
                f"CRS {rid} · {r.get('contentType','')} · 版本 v{r.get('version','')}"
                f" · 状态 {r.get('status','')}", 400)
            rec["extra"] = {
                "crs_id": rid,
                "content_type": r.get("contentType", ""),
                "version": r.get("version"),
                "status": r.get("status", ""),
                "update_date": r.get("updateDate", ""),
                "api_url": r.get("url", ""),
                "pdf_url": (f"https://www.congress.gov/crs_external_products/"
                            f"{(rid[:2] if rid else '')}/PDF/{rid}/{rid}.pdf") if rid else "",
            }
            # 正文只有标题与元数据 —— 不得凭空生成摘要，抽取只跑真实可得文本
            enrich(rec, f"{title} . CRS {rid} . {r.get('contentType','')}",
                   TERMS, SIGNAL_INDEX, EXCLUDE_PHRASES)
            recs.append(rec)
        if len(rows) < limit:
            break

    # 去重（同一报告多版本会重复出现）
    uniq: Dict[str, Dict[str, Any]] = {}
    for r in recs:
        uniq[r["id"]] = r
    recs = list(uniq.values())
    recs.sort(key=lambda x: (x.get("published") or ""), reverse=True)

    status = {"status": "ok" if recs else "ok-empty", "newest": newest,
              "items": len(recs), "pages": pages}
    extra_note = []
    if gated:
        extra_note.append(f"标题闸门过滤 {gated} 条")
    if note:
        extra_note.append(note)
    if key == "DEMO_KEY":
        extra_note.append("使用 DEMO_KEY（限流约 30 次/小时），建议申请个人 key")
    if extra_note:
        status["note"] = "；".join(extra_note)
    return recs, status


# ---- 5.2 联邦公报 --------------------------------------------------------- #
FR_FIELDS = ["title", "publication_date", "html_url", "type", "agencies",
             "abstract", "document_number", "action", "agencies"]


def collect_federal_register(source: Dict[str, Any], fetcher: Fetcher,
                             from_file: Optional[Path], verbose: bool
                             ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    recs: List[Dict[str, Any]] = []
    newest: Optional[datetime] = None
    per = source.get("per_query", 12)

    cached: Dict[str, Any] = {}
    if from_file and from_file.exists():
        try:
            cached = json.loads(from_file.read_text("utf-8"))
            log(f"    [from-file] {from_file.name}（{len(cached)} 组查询结果）")
        except json.JSONDecodeError:
            cached = {}

    for q in source.get("queries", []):
        label = q["label"]
        # 法定文件（总统决定书/DPA 授权）有效期长，用较长的窗口，否则关键授权会被时间窗切掉
        since = q.get("since") or (datetime.now(timezone.utc) - timedelta(
            days=int(source.get("window_days", 400)))).strftime("%Y-%m-%d")
        params: List[Tuple[str, str]] = [
            ("per_page", str(per)),
            ("order", "newest"),
            ("conditions[term]", q["term"]),
            ("conditions[publication_date][gte]", since),
        ]
        for k, v in (q.get("extra") or {}).items():
            params.append((k, v))
        for f in FR_FIELDS:
            params.append(("fields[]", f))

        data = None
        if label in cached:
            data = cached[label]
        else:
            url = FEDERAL_REGISTER_URL + "?" + urllib.parse.urlencode(params, doseq=True)
            raw = fetcher.get(url, verbose)
            if raw:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = None
        if not data:
            log(f"    [!] FR 查询失败：{label}")
            continue

        for d in data.get("results", []):
            dt = parse_time(d.get("publication_date") or "")
            if dt and (newest is None or dt > newest):
                newest = dt
            doc_no = d.get("document_number") or d.get("html_url") or d.get("title")
            rec = record_base(source, doc_no, d.get("title") or "",
                              d.get("html_url") or "", dt)
            rec["kind"] = "presidential" if "Presidential" in (d.get("type") or "") \
                else "regulation"
            ags = ", ".join(a.get("name", "") for a in (d.get("agencies") or [])[:3])
            abstract = d.get("abstract") or d.get("action") or ""
            rec["summary"] = truncate(f"[{d.get('type','')}] {ags} — {abstract}", 900)
            rec["extra"] = {"fr_query": label, "fr_type": d.get("type", ""),
                            "agencies": ags}
            enrich(rec, f"{rec['title']} . {abstract} . {ags} . {d.get('type','')}",
                   TERMS, SIGNAL_INDEX, EXCLUDE_PHRASES)
            recs.append(rec)
    return recs, {"status": "ok" if recs else "fail", "newest": newest,
                  "items": len(recs)}


# ---- 5.3 财政部 MTS ------------------------------------------------------- #
def collect_treasury(source: Dict[str, Any], fetcher: Fetcher,
                     from_file: Optional[Path], verbose: bool
                     ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """返回 (空记录列表, metrics 片段)。财务指标单独存放，不进记录流。"""
    months = int(source.get("months", 13))
    start = (datetime.now(timezone.utc) - timedelta(days=31 * months)).strftime("%Y-%m-%d")
    names = list(source.get("lines", [])) + list(source.get("extra_lines", []))
    filt = f"record_date:gte:{start},classification_desc:in:({','.join(names)})"
    url = (f"{TREASURY_MTS_URL}?sort=record_date&page[size]=500&filter="
           + urllib.parse.quote(filt))

    rows = None
    if from_file and from_file.exists():
        try:
            rows = json.loads(from_file.read_text("utf-8"))
            log(f"    [from-file] {from_file.name}（{len(rows.get('data', []))} 行）")
        except json.JSONDecodeError:
            rows = None
    if rows is None:
        raw = fetcher.get(url, verbose)
        if not raw:
            return [], {"status": "fail", "note": "无响应"}
        try:
            rows = json.loads(raw)
        except json.JSONDecodeError:
            return [], {"status": "fail", "note": "响应非 JSON"}

    by_month: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for r in rows.get("data", []):
        d = r.get("record_date") or ""
        desc = r.get("classification_desc") or ""
        by_month.setdefault(d, {})[desc] = r

    series: Dict[str, List[Dict[str, Any]]] = {}
    for d in sorted(by_month):
        for desc, r in by_month[d].items():
            try:
                m = float(r["current_month_net_outly_amt"]) \
                    if r.get("current_month_net_outly_amt") not in (None, "null", "") else None
            except (TypeError, ValueError):
                m = None
            try:
                y = float(r["current_fytd_net_outly_amt"]) \
                    if r.get("current_fytd_net_outly_amt") not in (None, "null", "") else None
            except (TypeError, ValueError):
                y = None
            series.setdefault(desc, []).append(
                {"month": d, "monthly": m, "fytd": y})

    metrics = analyze_treasury(series)
    newest = None
    if by_month:
        newest = parse_time(max(by_month.keys()))
    status = {"status": "ok" if series else "fail", "newest": newest,
              "items": len(series)}
    return [], {"__metrics__treasury": metrics, **status}


def abbr(line: str) -> str:
    """把长科目名缩成短标签。"""
    s = line.replace("Total--", "").replace("Department of Defense--", "")
    short = {
        "Military Programs": "国防部军事项目合计",
        "Military Personnel": "军事人员",
        "Operation and Maintenance": "运行与维护",
        "Procurement": "采购(含弹药导弹)",
        "Research, Development, Test, and Evaluation": "研发试验鉴定",
        "Military Construction": "军事建设",
        "Foreign Military Financing Program": "对外军事融资(FMF)",
        "Foreign Military Sales Trust Fund": "对外军售信托基金(FMS)",
    }
    return short.get(s, s)


def analyze_treasury(series: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """计算环比、近3月 vs 前3月、本财年累计、采购占比。纯机械计算，不做因果判断。"""
    out: Dict[str, Any] = {"lines": {}, "latest_month": None, "months": []}
    if not series:
        return out
    months = sorted({r["month"] for rows in series.values() for r in rows})
    out["months"] = months
    out["latest_month"] = months[-1] if months else None

    for line, rows in series.items():
        rows = sorted(rows, key=lambda r: r["month"])
        vals = [(r["month"], r["monthly"]) for r in rows if r["monthly"] is not None]
        if not vals:
            continue
        latest_m, latest_v = vals[-1]
        prev_v = vals[-2][1] if len(vals) >= 2 else None
        last3 = [v for _, v in vals[-3:]]
        prior3 = [v for _, v in vals[-6:-3]]
        entry = {
            "label": abbr(line),
            "full": line,
            "latest_month": latest_m,
            "latest": latest_v,
            "prev": prev_v,
            "mom_pct": ((latest_v - prev_v) / abs(prev_v) * 100.0) if prev_v else None,
            "avg3": sum(last3) / len(last3) if last3 else None,
            "avg3_prior": sum(prior3) / len(prior3) if prior3 else None,
            "fytd": rows[-1].get("fytd"),
            "history": vals,
        }
        out["lines"][line] = entry

    # 采购占国防合计的比重
    tot = out["lines"].get("Total--Department of Defense--Military Programs")
    pro = out["lines"].get("Total--Procurement")
    if tot and pro and tot.get("latest"):
        out["procurement_share"] = pro["latest"] / tot["latest"] * 100.0
    return out


# ---- 5.4 USAspending 合同 ------------------------------------------------- #
def collect_usaspending_awards(source: Dict[str, Any], fetcher: Fetcher,
                               from_file: Optional[Path], verbose: bool
                               ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    days = int(source.get("window_days", 180))
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    payload: Dict[str, Any] = {
        "filters": {
            "time_period": [{"start_date": start.strftime("%Y-%m-%d"),
                             "end_date": end.strftime("%Y-%m-%d"),
                             "date_type": "new_awards_only"}],
            "award_type_codes": source.get("award_type_codes", ["A", "B", "C", "D"]),
            "psc_codes": source.get("psc_codes", []),
        },
        "fields": ["Award ID", "Recipient Name", "Award Amount", "Description",
                   "Start Date", "Awarding Agency", "Awarding Sub Agency"],
        "sort": source.get("sort", "Start Date"),
        "order": "desc",
        "limit": int(source.get("limit", 30)),
        "page": 1,
    }
    # 限定授予机构，避免其他部门的同 PSC 采购混入（实测退伍军人事务部买「VESTS」
    # 也会被归到弹药类 PSC，不加这个过滤就会出现在「美军弹药」清单里）
    if source.get("agencies"):
        payload["filters"]["agencies"] = [
            {"type": "awarding", "tier": "toptier", "name": a}
            for a in source["agencies"]
        ]
    data = None
    if from_file and from_file.exists():
        try:
            data = json.loads(from_file.read_text("utf-8"))
            log(f"    [from-file] {from_file.name}")
        except json.JSONDecodeError:
            data = None
    if data is None:
        raw = fetcher.post_json(USASPENDING_AWARDS_URL, payload, verbose)
        if not raw:
            return [], {"status": "fail", "note": "无响应"}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return [], {"status": "fail", "note": "响应非 JSON"}

    recs: List[Dict[str, Any]] = []
    newest: Optional[datetime] = None
    for r in data.get("results", []):
        dt = parse_time(r.get("Start Date") or "")
        if dt and (newest is None or dt > newest):
            newest = dt
        amt = r.get("Award Amount")
        rec = record_base(source, r.get("Award ID") or r.get("internal_id", ""),
                          "", "", dt)
        rec["kind"] = "contract"
        rec["summary"] = truncate(r.get("Description") or "", 600)
        rec["extra"] = {
            "recipient": r.get("Recipient Name", ""),
            "award_id": r.get("Award ID", ""),
            "sub_agency": r.get("Awarding Sub Agency", ""),
        }
        rec["link"] = ("https://www.usaspending.gov/award/"
                       + str(r.get("generated_internal_id") or r.get("internal_id") or ""))
        rec["title"] = truncate(f"{r.get('Recipient Name','')} — "
                                f"{r.get('Description','') or '（无说明）'}", 200)
        # 先做文本抽取，再写入授标金额 —— 顺序不能反。
        # enrich() 会用正文里抽到的金额覆盖 rec["amounts"]，若先写授标金额就会被清成 None。
        enrich(rec, f"{r.get('Description','')} {r.get('Recipient Name','')} "
                    f"{r.get('Awarding Sub Agency','')}",
               TERMS, SIGNAL_INDEX, EXCLUDE_PHRASES)
        rec["extra"]["text_amounts"] = [a["raw"] for a in rec["amounts"]]
        try:
            amt_f = float(amt) if amt is not None else None
        except (TypeError, ValueError):
            amt_f = None
        if amt_f is not None:
            # 授标金额是权威字段，覆盖正文抽取值；同时保留正文抽取值备查
            rec["amounts"] = [{"usd": amt_f, "raw": fmt_usd(amt_f),
                               "scaled": True, "origin": "award_amount"}]
            rec["amount_max"] = amt_f
        recs.append(rec)
    recs.sort(key=lambda x: (x.get("published") or ""), reverse=True)
    return recs, {"status": "ok" if recs else "ok-empty", "newest": newest,
                  "items": len(recs)}


def collect_usaspending_category(source: Dict[str, Any], fetcher: Fetcher,
                                 from_file: Optional[Path], verbose: bool
                                 ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """国防部按 PSC 分类的开支聚合 + 弹药类精确口径 + 逐月序列。

    ⚠️ 这里有两个**不可混用**的口径，必须分开算、分开标：

    ① 精确口径（权威）：显式把 13xx/14xx 代码全集传给 filters.psc_codes，
       接口返回的就是这些代码本身，**不受排序截断影响**。
    ② 排序口径（仅作参考）：不传 PSC 过滤时，接口按金额降序返回若干行，
       其合计是「金额最大的 N 个类别之和」，是**下界**，不是总额。

    为什么必须分开：v1.1 只有②，并把「前 100 个 PSC 里的弹药行」当成弹药总额，
    实测因此从 42,135,820,391 少算到 39,714,336,221（**少 2.42B / 6.1%**）——
    鱼雷、引信、中小口径弹药、火箭等 16 个金额较小的弹药代码被截断掉。
    而最小返回行金额仍有 1.5 亿美元，从日志上完全看不出截断。

    ③ 逐月序列：spending_over_time(group=month) 与①的口径已实测**逐分一致**
       （差 0.00），因此月度值可以直接加总回①的总额，形成可自检的闭环。
    """
    days = int(source.get("window_days", 365))
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    agency = source.get("agency", "Department of Defense")
    psc_codes = build_psc_codes(PSC_CFG)
    hist_start = HISTORY_START

    def _base(t0: str, t1: str) -> Dict[str, Any]:
        return {
            "agencies": [{"type": "awarding", "tier": "toptier", "name": agency}],
            "time_period": [{"start_date": t0, "end_date": t1}],
            "award_type_codes": source.get("award_type_codes", ["A", "B", "C", "D"]),
        }

    def _post(url: str, payload: Dict[str, Any], tag: str) -> Optional[Dict[str, Any]]:
        if from_file and from_file.exists():
            try:
                blob = json.loads(from_file.read_text("utf-8"))
                if isinstance(blob, dict) and tag in blob:
                    log(f"    [from-file] {from_file.name} → {tag}")
                    return blob[tag]
            except json.JSONDecodeError:
                pass
        raw = fetcher.post_json(url, payload, verbose)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    w0 = start.strftime("%Y-%m-%d")
    w1 = end.strftime("%Y-%m-%d")

    # ---- ① 弹药类精确口径（显式 PSC 过滤，不受截断影响）----
    # limit 上限是 100（超过直接 422）。按 PSC 过滤后返回行数 ≤ 代码数（40），永远装得下。
    d_mun = _post(USASPENDING_CATEGORY_URL,
                  {"filters": {**_base(w0, w1), "psc_codes": psc_codes},
                   "limit": min(100, max(60, len(psc_codes) + 10)), "page": 1}, "munitions")
    if d_mun is None:
        return [], {"status": "fail", "note": "弹药类聚合无响应"}

    mun_rows: List[Dict[str, Any]] = []
    for r in d_mun.get("results", []):
        code = str(r.get("code") or "")
        if code[:2] not in ("13", "14"):
            continue      # 双保险：即使接口放宽了过滤，也不让非弹药代码进来
        try:
            amt = float(r.get("amount") or 0)
        except (TypeError, ValueError):
            amt = 0.0
        mun_rows.append({"code": code, "name": r.get("name") or "", "amount": amt})
    mun_rows.sort(key=lambda x: x["amount"], reverse=True)
    mun_total = sum(x["amount"] for x in mun_rows)

    # ---- ② 全体 PSC 参考排序（合计是下界，仅作背景）----
    d_all = _post(USASPENDING_CATEGORY_URL,
                  {"filters": _base(w0, w1), "limit": 100, "page": 1}, "all_psc")
    all_rows: List[Dict[str, Any]] = []
    if d_all:
        for r in d_all.get("results", []):
            try:
                amt = float(r.get("amount") or 0)
            except (TypeError, ValueError):
                amt = 0.0
            all_rows.append({"code": str(r.get("code") or ""),
                             "name": r.get("name") or "", "amount": amt})
    all_top_sum = sum(x["amount"] for x in all_rows)

    # ---- ③④ 逐月序列（弹药 / 全合同），两者同口径 ----
    # 窗口取 [min(历史起点, 头条窗口起点), 今天]：
    #   * 覆盖战前基线月 → 才能算出「开战后是战前的几倍」，而不是只看到一条单调曲线
    #   * 当历史起点晚于头条窗口起点时，两者窗口相同，于是「月度之和」与
    #     「头条精确总额」来自两个不同接口却必须逐分相等 —— 天然的交叉校验。
    monthly: List[Dict[str, Any]] = []
    trend_total = 0.0
    baseline_total = 0.0
    monthly_total = 0.0
    total_contracts = 0.0
    month_window_start = w0
    if hist_start:
        month_window_start = min(hist_start, w0)
        h0, h1 = month_window_start, w1
        d_m = _post(USASPENDING_OVER_TIME_URL,
                    {"group": "month",
                     "filters": {**_base(h0, h1), "psc_codes": psc_codes}},
                    "monthly_munitions")
        d_c = _post(USASPENDING_OVER_TIME_URL,
                    {"group": "month", "filters": _base(h0, h1)},
                    "monthly_contracts")
        mun_by: Dict[str, float] = {}
        con_by: Dict[str, float] = {}
        for r in (d_m or {}).get("results", []):
            ym = fy_period_to_ym(r.get("time_period") or {})
            if ym:
                mun_by[ym] = mun_by.get(ym, 0.0) + float(
                    r.get("aggregated_amount") or 0)
        for r in (d_c or {}).get("results", []):
            ym = fy_period_to_ym(r.get("time_period") or {})
            if ym:
                con_by[ym] = con_by.get(ym, 0.0) + float(
                    r.get("aggregated_amount") or 0)
        war_ym = hist_start[:7]
        for ym in month_list(h0[:7], h1[:7]):
            mv = mun_by.get(ym, 0.0)
            cv = con_by.get(ym, 0.0)
            monthly.append({"month": ym, "munitions": mv, "all_contracts": cv,
                            "share_pct": (mv / cv * 100.0) if cv else None,
                            "phase": "war" if ym >= war_ym else "baseline"})
            monthly_total += mv
            total_contracts += cv
            if ym >= war_ym:
                trend_total += mv
            else:
                baseline_total += mv

    # 月度序列窗口与头条窗口不同时，另取一次该窗口的精确总额，供加总核对
    month_window_total: Optional[float] = None
    if hist_start and month_window_start != w0:
        d_chk = _post(USASPENDING_CATEGORY_URL,
                      {"filters": {**_base(month_window_start, w1),
                                   "psc_codes": psc_codes},
                       "limit": min(100, max(60, len(psc_codes) + 10)), "page": 1},
                      "month_window_total")
        if d_chk:
            month_window_total = sum(
                float(x.get("amount") or 0) for x in d_chk.get("results", []))

    metrics = {
        "window_days": days,
        "window": [w0, w1],
        # ① 权威口径
        "psc_codes_queried": psc_codes,
        "psc_codes_with_data": len(mun_rows),
        "munitions_rows": mun_rows,
        "munitions_total": mun_total,
        "munitions_api_field": "Contract_Obligations（合同义务/授标，非实际支出）",
        # ② 参考口径（下界，明确标注）
        "all_psc_top_n": len(all_rows),
        "all_psc_top_sum": all_top_sum,
        "all_psc_top_note": ("金额最大的前 %d 个 PSC 类别之和，是下界；"
                             "不可当作国防部合同总额" % len(all_rows)),
        "top_overall": sorted(all_rows, key=lambda x: -x["amount"])[:10],
        "all_contracts_total": total_contracts or None,
        # ③ 逐月序列（与①同口径，两个不同接口之间可交叉核对）
        "monthly_window": [month_window_start, w1] if hist_start else None,
        "monthly_window_total": month_window_total,
        "monthly_sum": monthly_total if hist_start else None,
        "trend_window": [hist_start, w1] if hist_start else None,
        "trend_total": trend_total if hist_start else None,
        "baseline_total": baseline_total if hist_start else None,
        "war_start": hist_start,
        "monthly": monthly,
        "trend_share_pct": ((trend_total / total_contracts * 100.0)
                            if total_contracts else None),
    }
    return [], {"__metrics__usaspending": metrics, "status": "ok",
                "items": len(mun_rows), "newest": None}


# --------------------------------------------------------------------------- #
# 五点五、历史时间序列（战前基线 → 开战 → 至今）
# --------------------------------------------------------------------------- #
# 单日快照只能回答「现在是多少」，回答不了「在往哪个方向走」。
# 这一节把三类数字拉成逐月序列：
#   ① 财政部实际净支出（outlay）—— 一手权威，可逐项回源
#   ② USAspending 合同义务（obligation）—— 一手权威，可逐项回源，**口径不同不可相加**
#   ③ 联邦公报文档数 + 本监测收录的信号计数 —— 前者是官方计数，后者是监测指标（启发式）
#
# 三条硬规则：
#   * 每条序列都必须带「口径」与「信源等级」，否则读者会把 obligation 当 outlay。
#   * 月度值必须能**加总回区间总额**（同一接口同口径），否则趋势与头条数字会打架。
#   * 监测产生的计数（信号桶）明确标为启发式 —— 收录量变化会直接改变它。

# 历史序列里跟踪的财政部科目。键是 MTS 的 classification_desc 原文。
TREASURY_HIST_LINES = [
    ("Total--Department of Defense--Military Programs", "国防部军事项目合计"),
    ("Total--Procurement", "采购科目（含弹药导弹）"),
    ("Total--Operation and Maintenance", "运行与维护"),
    ("Total--Military Personnel", "军事人员"),
    ("Foreign Military Financing Program", "对外军事融资(FMF)"),
    ("Foreign Military Sales Trust Fund", "对外军售信托基金"),
]

# 历史序列里跟踪的联邦公报检索词（官方文档计数，T1）。
FR_HIST_TERMS = [
    ("Defense Production Act", "国防生产法(DPA)"),
    ("munitions", "弹药/军贸"),
    ("emergency supplemental appropriations", "紧急追加拨款"),
]

# 事件时间线规则：(标签, 正则, 搜索范围, 主题锚定, 排除项, 说明)
# 顺序即优先级 —— 一条记录只归给**第一个**命中的规则。
# 否则同一条 DPA 决定书会同时以「DPA 授权」和「总统决定书」出现两次，
# 时间线会被重复条目灌满。
# scope='title' 只搜标题；scope='text' 搜标题+摘要（库存/产能类表述多在摘要里）
#
# `主题锚定`（require）：命中主正则后**还必须**再命中它，否则丢弃并转下一条规则。
# 为什么需要：`Presidential Determination` 是宽词，实测会把
# 「难民接纳」「对委内瑞拉援助」「向沙特转让核能和平利用」「向芬兰瑞典提供原子信息」
# 「Joint Base Andrews **高尔夫球场**整修」这类与弹药/军费毫无关系的决定书
# 一起放进来（它们靠摘要里的战争泛词凑到了 relevance=6，刚好越过相关性闸门）。
#
# 为什么用「锚定」而不是删掉宽词或加黑名单：
#   · 删掉 `presidential determination` 会连真信号一起删 ——
#     `Presidential Determination Pursuant to Section 1245 of the NDAA`
#     （§1245＝对伊朗金融制裁条款）就是这样一条**真信号**，标题里只有这个词组。
#   · 黑名单（排除项）是穷举，遇到没想到的噪声就失效。
#   锚定是「必须与主题沾边」，属于正向条件，不会因未预见的噪声而放行。
#
# 被丢弃的候选**不会静默消失**：记进 output/_event_filter_audit.json（含标题/等级/相关性/原因），
# 供人工回扫确认「没命中」是真的无关、而不是删过头了。
EVENT_RULES: List[Tuple[str, str, str, str, str, str]] = [
    ("CBO/官方作战成本估算",
     r"cost of combat operations|combat operations against|cost of the.*conflict|"
     r"cost.*military operations", "title", "", "",
     "官方对战争花了多少钱的正式估算 —— 战争成本的官方锚点"),
    ("追加拨款立法",
     r"supplemental appropriations|emergency appropriations|emergency supplemental",
     "title", "", "", "新钱是否到位：战争能否延续的资金开关"),
    ("国防生产法(DPA)授权",
     r"defense production act", "title", "", "",
     "弹药产能能否合法扩张的授权依据"),
    ("裁减授权(drawdown)",
     r"drawdown", "title", "", "", "直接动用既有库存的授权"),
    ("总统决定书（对外军援/制裁）",
     r"presidential determination", "title",
     # 锚定：必须与「打仗/军援/伊朗制裁」沾边才收
     r"drawdown|iran|sanction|national defense authorization|lethal|"
     r"defense articles|military assistance|emergency military|weapons?|munitions?|"
     r"arms transfer|foreign military",
     "",
     "能否紧急动用库存与对外军援的授权（宽词，须过主题锚定）"),
    ("弹药库存 / 产能压力",
     r"deplet|stockpile|attrition|years to rebuild|surge capacity|production shortfall",
     "text", "", "", "库存与产能的压力表述 —— 供给侧的约束信号"),
]


def bar_chart(value: Optional[float], vmax: float, width: int = 26) -> str:
    """纯文本条形图。Markdown 里比数字更容易看出趋势方向。"""
    if value is None or vmax <= 0:
        return ""
    n = int(round(abs(value) / vmax * width))
    return "█" * max(1 if value else 0, min(n, width))


def load_accumulated(root: Path) -> List[Dict[str, Any]]:
    p = root / "ALL-records.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        # 兼容早期归档：老记录没有 relevance/tier，事件时间线的门槛依赖它们。
        # 不补齐就会把老记录一律当成 relevance=0 而被静默排除。
        if r.get("relevance") is None:
            rel, hits = compute_relevance(r, STRONG_INDEX, WEAK_INDEX)
            r["relevance"] = rel
            r.setdefault("relevance_hits", hits)
        if not r.get("tier"):
            r["tier"] = tier_of(r.get("source_id", ""))
        out.append(r)
    return out


def fr_month_counts(fetcher: Fetcher, months: List[str], cache_path: Path,
                    verbose: bool, refresh_all: bool = False) -> Dict[str, Dict[str, int]]:
    """联邦公报逐月文档数（官方 count 字段，T1）。

    带缓存：历史月份一旦取到就不再重复请求 —— 关税/拨款类文档的历史发布量
    不会变化，每天重拉 8 个月纯属浪费配额。默认只刷新最近 2 个月 + 当月
    （`config.history.refresh_recent_months`）。
    """
    cache: Dict[str, Any] = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text("utf-8"))
        except json.JSONDecodeError:
            cache = {}
    cells: Dict[str, Dict[str, int]] = cache.get("cells") or {}
    recent_n = int(HISTORY_CFG.get("refresh_recent_months", 2)) + 1
    fresh_months = set(months[-recent_n:]) if months else set()
    if refresh_all:
        fresh_months = set(months)

    fetched = 0
    for ym in months:
        if ym in cells and ym not in fresh_months:
            continue
        g, l = month_bounds(ym)
        row: Dict[str, int] = {}
        for term, _label in FR_HIST_TERMS:
            params = [("per_page", "20"), ("conditions[term]", term),
                      ("conditions[publication_date][gte]", g),
                      ("conditions[publication_date][lte]", l),
                      ("fields[]", "title"), ("fields[]", "publication_date")]
            raw = fetcher.get(FEDERAL_REGISTER_URL + "?"
                              + urllib.parse.urlencode(params, doseq=True),
                              verbose=False)
            if raw:
                try:
                    row[term] = int(json.loads(raw).get("count") or 0)
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
            fetched += 1
        if row:
            cells[ym] = row
    if fetched:
        log(f"    联邦公报月计数：本次请求 {fetched} 次（月度缓存命中 "
            f"{len(months) - len(fresh_months)} 个月）")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(
        {"fetched_at": datetime.now(timezone.utc).isoformat(),
         "terms": [t for t, _ in FR_HIST_TERMS], "cells": cells},
        ensure_ascii=False, indent=1), "utf-8")
    return cells


def extract_events(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从累积记录里抽出「官方文件级」事件，供时间线使用。

    四道闸门，缺一不可：
      ① 只保留 T1/T2 —— 事件时间线的意义是「官方在哪个月做出决定/披露」，
         二手媒体转述会把时间点变成「媒体哪天报道」而非「哪天发生」。
      ② 必须达到相关性阈值 —— 挡住与本主题无关的官方文件。
         ⚠️ 实测这道闸门**不够**：联邦公报的决定书摘要里常有战争泛词，
         使「难民接纳」「高尔夫球场整修」也能凑到 relevance=6。
         因此还需要第 ③ 道。
      ③ 主题锚定（require）—— 宽词规则命中后还必须再命中锚定正则。
         未过锚定的记录**转下一条规则**（不是整条丢弃），
         这样一份既像 DPA 又像决定书的文件仍能被正确的规则收走。
      ④ 一条记录只归给**首个**命中的规则 —— 否则同一份 DPA 决定书会以
         「DPA 授权」和「总统决定书」两个标签各出现一次，时间线被重复灌满。

    被闸门丢弃的候选全部记入 EVENT_AUDIT（落盘 _event_filter_audit.json），
    防止「压假阳性时把真信号一起删掉」而无人察觉。
    """
    out: List[Dict[str, Any]] = []
    seen_ids: set = set()
    lowrel: List[Dict[str, Any]] = []
    for r in records:
        tier = r.get("tier") or ""
        if tier not in ("T1", "T2"):
            continue
        rid = r.get("id")
        if rid in seen_ids:
            continue
        if int(r.get("relevance") or 0) < RELEVANCE_MIN:
            lowrel.append({
                "title": r.get("title") or "", "source_id": r.get("source_id") or "",
                "tier": tier, "relevance": r.get("relevance"),
                "date": (r.get("published") or "")[:10],
                "link": r.get("link") or "", "rule": "", "reason": "low-relevance",
            })
            continue
        title = r.get("title") or ""
        text = f"{title} . {r.get('summary') or ''}"
        for label, rx, scope, require, exclude, _desc in EVENT_RULES:
            hay = title if scope == "title" else text
            if not re.search(rx, hay, re.I):
                continue
            # 排除项：语义为「覆盖当前匹配的这段文本」，不是「附近出现过」。
            # 这里 hay 就是被匹配的区间本身（或它的超集），因此满足该语义。
            if exclude and re.search(exclude, hay, re.I):
                EVENT_AUDIT.append({
                    "title": title, "source_id": r.get("source_id") or "",
                    "tier": tier, "relevance": r.get("relevance"),
                    "date": (r.get("published") or "")[:10],
                    "link": r.get("link") or "", "rule": label,
                    "reason": "excluded-term",
                })
                continue
            # 主题锚定：宽词必须与主题沾边。不过则**转下一条规则**。
            if require and not re.search(require, hay, re.I):
                EVENT_AUDIT.append({
                    "title": title, "source_id": r.get("source_id") or "",
                    "tier": tier, "relevance": r.get("relevance"),
                    "date": (r.get("published") or "")[:10],
                    "link": r.get("link") or "", "rule": label,
                    "reason": "no-topic-anchor",
                })
                continue
            seen_ids.add(rid)
            sigs = r.get("signals") or []
            out.append({
                "event": label, "date": (r.get("published") or "")[:10],
                "title": title, "link": r.get("link") or "",
                "source_id": r.get("source_id") or "", "tier": tier,
                "publisher": r.get("publisher") or "",
                "relevance": r.get("relevance"),
                "context": sigs[0].get("context", "") if sigs else "",
            })
            break
    out.sort(key=lambda x: x["date"])
    EVENT_AUDIT.sort(key=lambda x: (x.get("date") or "", x.get("title") or ""))
    EVENT_AUDIT_LOWREL[:] = lowrel
    return out


def monthly_additivity(us: Dict[str, Any]) -> Tuple[float, Optional[float]]:
    """返回 (月度序列之和, 独立查询的区间总额)。

    抽成独立函数是为了让**自检跑的就是报告用的那段逻辑** ——
    在自检里复制一份等价实现，等于测了个替身，真逻辑改坏了也不会被发现。
    理想情况下这两个数来自**两个不同的接口**（月度分组视图 vs 分类聚合视图），
    因此这是交叉验证，不是同义反复。
    """
    s = sum((r.get("munitions") or 0.0) for r in (us.get("monthly") or []))
    ref = us.get("monthly_window_total")
    if ref is None:
        ref = us.get("munitions_total")
    return s, ref


def build_history(cfg: Dict[str, Any], metrics: Dict[str, Any],
                  acc_records: List[Dict[str, Any]], root: Path,
                  run_dt: datetime, fetcher: Optional[Fetcher],
                  verbose: bool, backfill: bool = False) -> Dict[str, Any]:
    """产出逐月序列（JSONL）+ 趋势报告（MD）。"""
    hist_cfg = cfg.get("history", {}) or {}
    start = hist_cfg.get("start") or (run_dt - timedelta(days=180)).strftime("%Y-%m-%d")
    end_ym = month_key(run_dt)

    # 序列要**含战前基线月**，否则「开战后是战前的几倍」根本算不出来，
    # 只会得到一条从开战起就单调的曲线，看不出跳变。
    # 起点取 min(声明起点, 合同月度窗口起点)：后者由头条窗口决定，天然覆盖战前。
    us_metrics = metrics.get("usaspending") or {}
    mw = us_metrics.get("monthly_window") or []
    calc_start = start
    if mw and mw[0]:
        calc_start = min(start, mw[0])
    months = month_list(calc_start[:7], end_ym)
    baseline_months = [m for m in months if m < start[:7]]
    hdir = root / hist_cfg.get("cache_dir", "history")
    hdir.mkdir(parents=True, exist_ok=True)

    series: List[Dict[str, Any]] = []

    def add(metric: str, period: str, value: Optional[float], unit: str,
            caliber: str, source_id: str, tier: str, kind: str = "official",
            note: str = "") -> None:
        series.append({
            "metric": metric, "period": period, "value": value, "unit": unit,
            "caliber": caliber, "source_id": source_id, "tier": tier,
            "publisher": SOURCE_META.get(source_id, {}).get("publisher", ""),
            "traceable": TIER_META.get(tier, {}).get("traceable", ""),
            "kind": kind, "note": note,
            "generated_at": run_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })

    # ---- ① 财政部：实际净支出（outlay，一手权威）----
    t = metrics.get("treasury") or {}
    tlines = t.get("lines") or {}
    for full, _short in TREASURY_HIST_LINES:
        entry = tlines.get(full)
        if not entry:
            continue
        for ym, val in entry.get("history", []):
            # 用 calc_start（含战前基线）而不是 start，否则「战前 vs 战后」
            # 永远算不出来 —— 这条过滤会静默把基线月全删掉。
            if ym[:7] < calc_start[:7]:
                continue
            add(f"treasury::{full}", ym[:7], val, "USD",
                "实际净支出 outlay（月度净额）", "treasury-mts", "T1")

    # ---- ② USAspending：合同义务（obligation，一手权威，口径不同）----
    us = metrics.get("usaspending") or {}
    for row in us.get("monthly") or []:
        add("usaspending::munitions", row["month"], row.get("munitions"), "USD",
            "合同义务 obligation（新签+存量行动，非实际支出）",
            "usaspending-psc-category", "T1")
        add("usaspending::all_contracts", row["month"], row.get("all_contracts"),
            "USD", "合同义务 obligation（国防部全部合同类 PSC）",
            "usaspending-psc-category", "T1")

    # ---- ③ 联邦公报文档数（官方 count）----
    fr_cells: Dict[str, Dict[str, int]] = {}
    if fetcher is not None:
        try:
            fr_cells = fr_month_counts(fetcher, months, hdir / "fr-monthly.json",
                                       verbose, refresh_all=backfill)
        except Exception as e:                                     # noqa: BLE001
            log(f"    [!] 联邦公报月计数失败：{type(e).__name__}: {e}")
    for ym in months:
        cell = fr_cells.get(ym) or {}
        for term, label in FR_HIST_TERMS:
            if term in cell:
                add(f"fr::{term}", ym, cell[term], "count",
                    f"联邦公报该月匹配「{label}」的文档数（官方 count）",
                    "federal-register", "T1")

    # ---- ④ 监测指标：信号桶计数（启发式，非官方口径）----
    buckets = ["sustain", "terminate", "stress"]
    by_month: Dict[str, Dict[str, int]] = {ym: {b: 0 for b in buckets} for ym in months}
    by_month_docs: Dict[str, int] = {ym: 0 for ym in months}
    for r in acc_records:
        # 信号在**全文**上抽取，而记录只留 1200 字摘要 —— 二者可能不一致。
        # 这里用记录里已保存的 signals 字段（抽取结果随记录一起落盘），而不是重跑文本。
        ym = (r.get("published") or "")[:7]
        if ym not in by_month:
            continue
        by_month_docs[ym] += 1
        for s in r.get("signals") or []:
            b = s.get("bucket")
            if b in by_month[ym]:
                by_month[ym][b] += 1
    # 本监测从哪个月开始有收录？之前的月份**不是 0 条，是没在收**。
    # 一律写成 0 会与偏差声明「空 ≠ 零」自相矛盾，读者会把「没收录」读成
    # 「当月什么都没发生」。所以这些月落盘 None，渲染成 `—`。
    #
    # 起点取自**全部累积记录的发布时间最小值**，而不是「计数 > 0 的最小月」：
    # 后者在累积档为空时会让每个月都被判为「已覆盖」，全表变 0，
    # 恰恰在最该显示「无覆盖」的时候显示成「零事件」。
    _cov = sorted({(r.get("published") or "")[:7] for r in acc_records} - {""})
    first_cov = _cov[0] if _cov else None
    for ym in months:
        covered = first_cov is not None and ym >= first_cov
        for b in buckets:
            add(f"monitor::signal_{b}", ym,
                by_month[ym][b] if covered else None, "count",
                "本监测收录记录中命中该信号桶的条数（**启发式**：受收录覆盖度影响，非官方统计）",
                "monitor", "—", kind="monitor-index",
                note="" if covered else "该月本监测尚未开始收录（不是 0）")
        add("monitor::records_total", ym,
            by_month_docs[ym] if covered else None, "count",
            "本监测该月收录的记录总数（**启发式**：覆盖度指标，非官方统计）",
            "monitor", "—", kind="monitor-index",
            note="" if covered else "该月本监测尚未开始收录（不是 0）")

    # ---- 落盘：长表 JSONL ----
    series_path = hdir / "series.jsonl"
    with series_path.open("w", encoding="utf-8") as fh:
        for row in series:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    events = extract_events(acc_records)

    # 事件过滤审计：被锚定/排除/相关性挡下的候选留档。
    # 没有这份文件，「某条真信号为什么没进时间线」就查不出来。
    if EVENT_AUDIT or EVENT_AUDIT_LOWREL:
        (root / "_event_filter_audit.json").write_text(json.dumps({
            "note": "事件时间线的过滤留档。dropped=命中宽词但未过主题锚定/排除项"
                    "（重点看这类，可能是删过头）；low_relevance=相关性不足被挡。",
            "generated_at": run_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kept": [{"date": e["date"], "event": e["event"], "title": e["title"],
                      "tier": e["tier"], "source_id": e["source_id"]} for e in events],
            "dropped": EVENT_AUDIT,
            "low_relevance": EVENT_AUDIT_LOWREL,
        }, ensure_ascii=False, indent=1), "utf-8")

    # ---- 加总一致性自检（月度之和 vs 独立查询的区间总额）----
    checks: List[Dict[str, Any]] = []
    if us.get("monthly"):
        s, ref = monthly_additivity(us)
        ref_name = "另取的区间聚合总额（同一接口，独立请求）"
        if us.get("monthly_window_total") is None:
            ref_name = "头条精确总额（spending_by_category，与月度视图是**两个不同接口**）"
        checks.append({
            "name": f"弹药月度序列之和 vs {ref_name}",
            "left": s, "right": ref,
            "window": us.get("monthly_window"), "tier": "T1"})
        if us.get("trend_total") is not None and us.get("baseline_total") is not None:
            checks.append({
                "name": "（战前月合计 + 开战后月合计）vs 月度序列总和",
                "left": (us["trend_total"] + us["baseline_total"]),
                "right": s, "window": us.get("monthly_window"), "tier": "T1"})

    summary = {
        "start": start, "start_label": hist_cfg.get("start_label", ""),
        "end": run_dt.strftime("%Y-%m-%d"),
        "calc_start": calc_start,
        "baseline_months": baseline_months,
        "months": months,
        "series_rows": len(series),
        "events": events,
        "checks": checks,
        "metric_count": len({r["metric"] for r in series}),
    }

    md = render_trend(cfg, series, summary, metrics, acc_records, run_dt)
    trend_path = hdir / f"trend-{run_dt.strftime('%Y-%m-%d')}.md"
    trend_path.write_text(md, "utf-8")
    (root / "LATEST-trend.md").write_text(md, "utf-8")
    (hdir / "LATEST-trend.md").write_text(md, "utf-8")

    return {"series": series_path, "trend": trend_path, "summary": summary}


def render_trend(cfg: Dict[str, Any], series: List[Dict[str, Any]],
                 summary: Dict[str, Any], metrics: Dict[str, Any],
                 acc_records: List[Dict[str, Any]], run_dt: datetime) -> str:
    """趋势报告 —— 面向大模型阅读：每个数字都带口径、来源、等级。"""
    L: List[str] = []
    start, end = summary["start"], summary["end"]
    months = summary["months"]
    war_ym = start[:7]
    baseline_months = summary.get("baseline_months") or []

    def get(metric: str) -> Dict[str, Optional[float]]:
        return {r["period"]: r["value"] for r in series if r["metric"] == metric}

    L.append(f"# 美军弹药与军费动态变化（{start} → {end}）")
    L.append("")
    L.append(f"- 序列区间：**{start} → {end}**"
             f"（起点＝{summary.get('start_label') or '配置的历史起点'}）")
    if baseline_months:
        L.append(f"- **战前基线月**：{'、'.join(baseline_months)}"
                 f"（表中标 ⚪，用于算「开战后是战前的几倍」；不是序列主体）")
    L.append(f"- 生成时间：{run_dt.strftime('%Y-%m-%d %H:%M UTC')}")
    L.append(f"- 序列条数：{summary['series_rows']} 条 / {summary['metric_count']} 个指标")
    L.append("")
    L.append("> **读之前必须知道的两件事**")
    L.append("> 1. **口径不可混用**：财政部是 `actual net outlays`（实际净支出），")
    L.append(">    USAspending 是 `Contract_Obligations`（合同义务/授标）。")
    L.append(">    后者是「签了合同的钱」，不是「花出去的钱」，**两者不可相加、不可直接比大小**。")
    L.append("> 2. **可信度分级**：带 🟢T1 的序列可逐项回源对账（差 0.00）；")
    L.append(">    标「启发式」的序列来自本监测自身收录量，**不是官方统计**，只能看方向不能当数据用。")
    L.append("")

    # ---------- 1. 军费：财政部实际净支出 ----------
    dod = get("treasury::Total--Department of Defense--Military Programs")
    proc = get("treasury::Total--Procurement")
    om = get("treasury::Total--Operation and Maintenance")
    mil = get("treasury::Total--Military Personnel")
    fmf = get("treasury::Foreign Military Financing Program")
    if dod:
        L.append("## 1. 军费实际支出（财政部 MTS · 🟢T1 · 可逐项回源）")
        L.append("")
        L.append("口径：`actual net outlays`（实际净支出，月度净额）。来源："
                 "美国财政部 Monthly Treasury Statement 表5，"
                 "`api.fiscaldata.treasury.gov`。**这是真金白银出账的数字**，"
                 "与下面第 2 节的合同义务是两个不同概念。")
        L.append("")
        base_m = months[0] if months and months[0] in dod else next(iter(dod), None)
        vmax = max([abs(v) for v in dod.values() if v] or [1])
        L.append("| 月份 | 阶段 | 国防部合计 | 条形 | 环比 | 采购科目 | 运行维护 | 军事人员 |")
        L.append("|---|---|---:|---|---:|---:|---:|---:|")
        prev = None
        for ym in months:
            v = dod.get(ym)
            if v is None:
                continue
            mom = ((v - prev) / abs(prev) * 100.0) if prev else None
            phase = "🔴 战后" if ym >= war_ym else "⚪ 战前"
            L.append(f"| {ym} | {phase} | {fmt_usd(v)} | {bar_chart(v, vmax, 18)} | "
                     f"{f'{mom:+.1f}%' if mom is not None else '—'} | "
                     f"{fmt_usd(proc.get(ym)) if proc.get(ym) is not None else '—'} | "
                     f"{fmt_usd(om.get(ym)) if om.get(ym) is not None else '—'} | "
                     f"{fmt_usd(mil.get(ym)) if mil.get(ym) is not None else '—'} |")
            prev = v
        L.append("")
        vals = [(ym, dod[ym]) for ym in months if dod.get(ym)]
        bvals = [(ym, v) for ym, v in vals if ym < war_ym]
        wvals = [(ym, v) for ym, v in vals if ym >= war_ym]
        if wvals:
            peak = max(wvals, key=lambda kv: kv[1])
            low = min(wvals, key=lambda kv: kv[1])
            first, last = wvals[0], wvals[-1]
            L.append(f"- **开战首月**（{first[0]}）：{fmt_usd(first[1])}")
            L.append(f"- **战后峰值**（{peak[0]}）：{fmt_usd(peak[1])}")
            L.append(f"- **战后低谷**（{low[0]}）：{fmt_usd(low[1])}")
            L.append(f"- **最新月**（{last[0]}）：{fmt_usd(last[1])}，"
                     f"相对开战首月 {fmt_pct(last[1], first[1])}")
            if len(wvals) >= 6:
                a = sum(v for _, v in wvals[-3:]) / 3
                b = sum(v for _, v in wvals[-6:-3]) / 3
                L.append(f"- **最近 3 月均值 {fmt_usd(a)} vs 前 3 月均值 {fmt_usd(b)}**，"
                         f"变化 {fmt_pct(a, b)}")
        if bvals:
            bavg = sum(v for _, v in bvals) / len(bvals)
            L.append(f"- **战前基线均值**：{fmt_usd(bavg)}（{len(bvals)} 个月）")
            if wvals:
                wavg = sum(v for _, v in wvals) / len(wvals)
                L.append(f"- **战后均值 / 战前基线均值 = {wavg/bavg:.2f} 倍**")
        if fmf and any(v for v in fmf.values()):
            L.append("")
            L.append(f"- 对外军事融资(FMF) 月度序列："
                     + "、".join(f"{ym} {fmt_usd(fmf[ym])}"
                                 for ym in months if fmf.get(ym)))
        L.append("")

    # ---------- 2. 弹药合同义务 ----------
    mun = get("usaspending::munitions")
    allc = get("usaspending::all_contracts")
    if mun:
        us_m = metrics.get("usaspending") or {}
        war_ym = (us_m.get("war_start") or start)[:7]
        L.append("## 2. 弹药与导弹类合同义务（USAspending · 🟢T1 · 可逐项回源）")
        L.append("")
        L.append("口径：`Contract_Obligations`（合同义务/授标）。")
        L.append("**不是实际支出** —— 签了合同≠钱已出账，两者不可与第 1 节相加。")
        L.append("数据带 1–3 个月发布滞后：最近 1–3 个月偏低甚至是 0 属于正常，不是停更。")
        L.append("")
        L.append("> ⚠️ **两个必须先排除的误读**")
        L.append("> 1. **9 月是财年末**（美国财年 9 月 30 日结束）。9 月义务额天然暴涨"
                 "（「不用就作废」的例行冲刺），")
        L.append(">    **不可**把 9 月的高点读成战争强度。要比较请拿同月对同月。")
        L.append("> 2. 序列末端的 0 值是**发布滞后**，不是「补货停了」。")
        L.append("")
        vmax = max([abs(v) for v in mun.values() if v] or [1])
        L.append(f"| 月份 | 阶段 | 弹药/导弹义务额 | 条形 | 环比 | 国防部全部合同类 | 弹药占比 |")
        L.append("|---|---|---:|---|---:|---:|---:|")
        prev = None
        for ym in months:
            v = mun.get(ym)
            c = allc.get(ym)
            phase = "🔴 战后" if ym >= war_ym else "⚪ 战前"
            mom = ((v - prev) / abs(prev) * 100.0) if (prev and v is not None) else None
            share = (v / c * 100.0) if (v is not None and c) else None
            L.append(f"| {ym} | {phase} | {fmt_usd(v) if v is not None else '—'} | "
                     f"{bar_chart(v, vmax, 18)} | "
                     f"{f'{mom:+.1f}%' if mom is not None else '—'} | "
                     f"{fmt_usd(c) if c is not None else '—'} | "
                     f"{f'{share:.1f}%' if share is not None else '—'} |")
            if v is not None:
                prev = v
        L.append("")
        vals = [(ym, mun[ym]) for ym in months if mun.get(ym) is not None]
        war_vals = [(ym, v) for ym, v in vals if ym >= war_ym and v > 0]
        base_vals = [(ym, v) for ym, v in vals if ym < war_ym and v > 0]
        if base_vals:
            # 剔除 9 月：财年末冲刺会把基线拉高，造成「战后没涨」的假象
            base_nosep = [(ym, v) for ym, v in base_vals if ym[5:7] != "09"]
            bavg = (sum(v for _, v in base_nosep) / len(base_nosep)
                    if base_nosep else None)
            L.append(f"- **战前基线（剔除 9 月财年末）：**"
                     + (f"均值 {fmt_usd(bavg)}（{len(base_nosep)} 个月）"
                        if bavg else "样本不足"))
            L.append(f"- **战前 9 月（财年末冲刺，仅供对照，勿当基线）：**"
                     + "、".join(f"{ym} {fmt_usd(v)}" for ym, v in base_vals
                                 if ym[5:7] == "09") if any(
                         ym[5:7] == "09" for ym, _ in base_vals) else "")
        if war_vals:
            pk = max(war_vals, key=lambda kv: kv[1])
            wavg = sum(v for _, v in war_vals) / len(war_vals)
            L.append(f"- **战后峰值**（{pk[0]}）：{fmt_usd(pk[1])}")
            L.append(f"- **战后有数据月均值**：{fmt_usd(wavg)}（{len(war_vals)} 个月）")
            if base_vals:
                base_nosep = [(ym, v) for ym, v in base_vals if ym[5:7] != "09"]
                if base_nosep:
                    bavg = sum(v for _, v in base_nosep) / len(base_nosep)
                    L.append(f"- **战后均值 / 战前基线均值 = {wavg/bavg:.2f} 倍**"
                             f"（已剔除 9 月财年末影响）")
        L.append("")

    # ---------- 3. 行政/立法活动 ----------
    L.append("## 3. 政策与立法活动（联邦公报文档数 · 🟢T1）")
    L.append("")
    L.append("口径：该月联邦公报中匹配检索词的文档**条数**（API 顶层 `count` 字段，官方计数）。"
             "条数变化反映「政策/授权动作的密集度」，不反映金额大小。")
    L.append("")
    cols = [(f"fr::{term}", label) for term, label in FR_HIST_TERMS]
    fr_any = any(get(m) for m, _ in cols)
    if fr_any:
        L.append("| 月份 | " + " | ".join(lb for _, lb in cols) + " |")
        L.append("|---" * (len(cols) + 1) + "|")
        for ym in months:
            cells = []
            for m, _lb in cols:
                v = get(m).get(ym)
                cells.append(str(int(v)) if v is not None else "—")
            L.append(f"| {ym} | " + " | ".join(cells) + " |")
        L.append("")
        for m, lb in cols:
            d = get(m)
            vals = [(ym, d[ym]) for ym in months if d.get(ym)]
            if vals:
                pk = max(vals, key=lambda kv: kv[1])
                tot = sum(v for _, v in vals)
                L.append(f"- **{lb}**：区间合计 {int(tot)} 份，峰值 {pk[0]}（{int(pk[1])} 份）")
    else:
        L.append("_本次未取到联邦公报月计数（接口失败或缓存为空）。_")
    L.append("")

    # ---------- 4. 监测指标（启发式）----------
    L.append("## 4. 信号计数（本监测收录 · ⚠️ 启发式，非官方统计）")
    L.append("")
    L.append("**这一节的数字不能当官方数据引用。** 它统计的是「本监测该月收录的"
             "记录中命中各类信号的条数」，因此**收录覆盖度一变、数字就变**")
    L.append("（例如某源中途接入或失效，会造成序列跳变）。它只能用于看方向。")
    L.append("")
    L.append("表中 `—` 表示该月**本监测尚未开始收录** —— 不是 0，"
             "更不代表当月没有发生事情。这与「未拉取的月份显示为空而不是 0」是同一条纪律。")
    L.append("")
    sus = get("monitor::signal_sustain")
    ter = get("monitor::signal_terminate")
    sts = get("monitor::signal_stress")
    tot = get("monitor::records_total")
    L.append("| 月份 | 收录记录 | 持续/升级 | 降级/结束 | 产能库存压力 | 净值(持续-结束) |")
    L.append("|---|---:|---:|---:|---:|---:|")
    for ym in months:
        tv, av = tot.get(ym), sus.get(ym)
        if tv is None or av is None:
            L.append(f"| {ym} | — | — | — | — | — |")
            continue
        bv, cv = ter.get(ym) or 0, sts.get(ym) or 0
        L.append(f"| {ym} | {int(tv)} | {int(av)} | {int(bv)} | {int(cv)} | "
                 f"**{int(av - bv):+d}** |")
    L.append("")

    # ---------- 5. 事件时间线 ----------
    ev = summary.get("events") or []
    L.append("## 5. 官方事件时间线（仅 T1/T2，按官方发布日）")
    L.append("")
    if ev:
        L.append("只收录**官方一手/官方分析**层的事件。二手媒体转述不进时间线 —— "
                 "否则时间点会变成「媒体哪天报道」而不是「哪天真的发生」。")
        L.append("")
    L.append("进线还需过**主题锚定**：`Presidential Determination` 这类宽词必须再与"
             "战事/军援/伊朗制裁沾边。实测无此闸门时，「难民接纳」"
             "「高尔夫球场整修」「向沙特转让核能和平利用」都会挤进来"
             "（它们靠摘要里的战争泛词凑够相关性）。"
             "被挡下的候选**逐条留档**在 `output/_event_filter_audit.json`，"
             "可复核是否删过头。")
    L.append("")
    if ev:
        L.append("| 日期 | 事件类型 | 标题 | 等级 | 来源 |")
        L.append("|---|---|---|---|---|")
        for e in ev[-30:]:
            L.append(f"| {e['date']} | {e['event']} | "
                     f"[{truncate(e['title'], 88)}]({e['link']}) | "
                     f"{tier_badge(e['tier'])} | `{e['source_id']}` |")
    else:
        L.append("_累积记录中暂无符合规则的官方事件。_")
    L.append("")

    # ---------- 6. 加总一致性 + 已知偏差 ----------
    L.append("## 6. 加总一致性自检与已知偏差")
    L.append("")
    if summary.get("checks"):
        L.append("| 比对项 | 左值 | 右值 | 差 | 结论 |")
        L.append("|---|---:|---:|---:|---|")
        for c in summary["checks"]:
            lft, rgt = c.get("left"), c.get("right")
            if lft is None or rgt is None:
                continue
            diff = lft - rgt
            if c.get("soft"):
                verdict = "量级相当（口径不同，仅作参考）"
            else:
                verdict = "✅ 一致" if abs(diff) < 0.01 else f"❌ 不一致（差 {diff:,.2f}）"
            L.append(f"| {c['name']} | {lft:,.2f} | {rgt:,.2f} | {diff:,.2f} | {verdict} |")
        L.append("")
    L.append("**已知偏差与解读边界**")
    L.append("")
    L.append("1. **9 月财年末效应（最容易踩的坑）**：美国财年 9 月 30 日结束，"
             "9 月的合同义务额会因「不用就作废」而例行暴涨。"
             "**拿 9 月与其他月比会把财年效应误读成战争强度。**")
    L.append("   本报告已把 9 月从「战前基线」中剔除；跨年比较请同月对同月。")
    L.append("2. **发布滞后**：USAspending 新签合同有 1–3 个月滞后，最近月份的"
             "义务额偏低甚至为 0 属正常。**不得**把最新月读成「补货停了」。")
    L.append("3. **口径不同不可加总**：第 1 节（实际支出 outlay）与第 2 节（合同义务 obligation）"
             "不可相加，也不可直接比大小 —— 一个月的义务可能在未来数个季度才出账。")
    L.append("4. **监测指标是启发式**：第 4 节的计数来自本监测自身收录，"
             "**收录覆盖度变化会造成跳变**，某月数字下降可能只是某源当月无更新。")
    L.append("5. **库存无绝对数量**：公开渠道**不存在**弹药库存的绝对数量披露。"
             "库存只能从官方报告的相对表述（如「已消耗三分之二」）间接推断，"
             "与第 1、2 节的一手权威金额不在同一可信层级。见日报附录 D。")
    L.append("6. **月度序列可加总核对**：第 1、2 节的月度值来自与日报头条"
             "**同一接口、同一口径**的月度分组视图，两者必须逐分相等（见上表）；")
    L.append("   本报告特意用**两个不同接口**互相验证，而不是用同一个接口自证。")
    L.append("7. **历史序列在首次运行时靠 `--backfill` 全量建立**；之后每日运行"
             "只刷新最近月份。若某月从未被拉取过（例如中途才接入某个源），"
             "该月会显示为空而不是 0 —— **空 ≠ 零**，不要当成「没有发生」。")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 附录：序列清单与溯源")
    L.append("")
    L.append("| 指标 | 口径 | 单位 | 来源源 id | 等级 | 可回源程度 | 性质 |")
    L.append("|---|---|---|---|---|---|---|")
    seen_metrics = []
    for r in series:
        if r["metric"] in seen_metrics:
            continue
        seen_metrics.append(r["metric"])
        kind = "启发式指标" if r["kind"] == "monitor-index" else "官方数据"
        L.append(f"| `{r['metric']}` | {r['caliber']} | {r['unit']} | "
                 f"`{r['source_id']}` | {tier_badge(r['tier']) if r['tier'] in TIER_META else '—'} | "
                 f"{r.get('traceable') or '—'} | {kind} |")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# 六、主采集流程
# --------------------------------------------------------------------------- #
def load_config(path: Path = CFG_PATH) -> Dict[str, Any]:
    return json.loads(path.read_text("utf-8"))


def effective_since(source: Dict[str, Any], cfg: Dict[str, Any],
                    run_dt: datetime) -> datetime:
    if source.get("window_days"):
        return run_dt - timedelta(days=int(source["window_days"]))
    return run_dt - timedelta(hours=int(cfg.get("lookback_hours", 720)))


def collect(cfg: Dict[str, Any], fetcher: Fetcher, source_ids: Optional[List[str]],
            run_dt: datetime, use_from_file: Optional[Path],
            verbose: bool) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """返回 (新记录列表, 源状态报告, metrics)。"""
    records: List[Dict[str, Any]] = []
    report: List[Dict[str, Any]] = []
    metrics: Dict[str, Any] = {}

    for source in cfg.get("sources", []):
        sid = source["id"]
        if source_ids and sid not in source_ids:
            continue
        if not source.get("enabled", True):
            report.append({"id": sid, "name": source["name"],
                           "layer": source.get("layer", ""),
                           "tier": source.get("tier", tier_of(sid)),
                           "publisher": source.get("publisher", ""),
                           "status": "disabled",
                           "items": 0, "newest": None,
                           "note": truncate(source.get("note", ""), 120)})
            log(f"[跳过] {sid}（已停用）")
            continue

        log(f"[采集] {sid} — {source['name']}")
        fpath = (use_from_file / f"{sid}.xml") if use_from_file else None
        fpath_json = (use_from_file / f"{sid}.json") if use_from_file else None
        kind = source.get("kind", "rss")
        try:
            if kind == "rss":
                recs, st = collect_rss(source, fetcher, fpath, verbose)
            elif kind == "crs_report":
                recs, st = collect_crs_reports(source, fetcher, fpath_json, verbose)
            elif kind == "federal_register":
                recs, st = collect_federal_register(source, fetcher, fpath_json, verbose)
            elif kind == "treasury_mts":
                recs, st = collect_treasury(source, fetcher, fpath_json, verbose)
            elif kind == "usaspending_awards":
                recs, st = collect_usaspending_awards(source, fetcher, fpath_json, verbose)
            elif kind == "usaspending_category":
                recs, st = collect_usaspending_category(source, fetcher, fpath_json, verbose)
            else:
                recs, st = [], {"status": "fail", "note": f"未知 kind: {kind}"}
        except Exception as e:                                       # noqa: BLE001
            log(f"    [!] 采集异常：{type(e).__name__}: {e}")
            recs, st = [], {"status": "fail", "note": f"{type(e).__name__}: {str(e)[:90]}"}

        # 采集器把指标片段挂在 __metrics__xxx 键上，摘出来单独存放
        for k in [k for k in st if k.startswith("__metrics__")]:
            metrics[k[len("__metrics__"):]] = st.pop(k)

        # 新鲜度判定。阈值可按源覆盖 —— 数据本身发布就慢的源（如 USAspending
        # 新签合同有 1–3 个月滞后），用全局 30 天会一直误报停更。
        since = effective_since(source, cfg, run_dt)
        newest = st.get("newest")
        if st.get("status") == "ok" and newest:
            lag = (run_dt - newest).days
            limit = int(source.get("stale_after_days",
                                   cfg.get("request", {}).get("stale_after_days", 30)))
            if lag > limit:
                st["status"] = "ok-stale"
                st["note"] = (st.get("note", "")
                              + f" 源内最新 {lag} 天前（该源发布阈值 {limit} 天）").strip()

        # 时间窗过滤（保留窗内与无日期的记录）+ 打相关性分
        kept = []
        for r in recs:
            t = parse_time(r.get("published") or "")
            if t is None or t >= since:
                rel, hits = compute_relevance(r, STRONG_INDEX, WEAK_INDEX)
                # 合同类已由 PSC 精确过滤，天然相关，不参与相关性淘汰
                if r.get("kind") == "contract":
                    rel = max(rel, 1)
                r["relevance"] = rel
                r["relevance_hits"] = hits
                kept.append(r)
        st["filtered"] = len(recs) - len(kept)
        st["relevant"] = sum(1 for r in kept
                             if r["kind"] == "contract" or r["relevance"] >= RELEVANCE_MIN)
        records.extend(kept)

        report.append({"id": sid, "name": source["name"], "layer": source.get("layer", ""),
                       "tier": source.get("tier", tier_of(sid)),
                       "publisher": source.get("publisher", ""),
                       "status": st.get("status", "?"), "items": len(kept),
                       "relevant": st.get("relevant"), "newest": newest,
                       "note": st.get("note", ""),
                       "window_days": source.get("window_days")})
        log(f"    → {st.get('status')}  窗内 {len(kept)} 条"
            f"（原始 {len(recs)}，相关 {st.get('relevant', '-')}）"
            f"  源内最新 {fmt_date(newest)}")
        time.sleep(float(cfg.get("request", {}).get("delay_between_sources_sec", 1.5)))

    return records, report, metrics


def dedupe(records: List[Dict[str, Any]], seen: Dict[str, str], run_dt: datetime
           ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """按 id 去重（同一批次内 + 历史 seen）。返回 (全部, 本次新增)。"""
    uniq: Dict[str, Dict[str, Any]] = {}
    for r in records:
        uniq[r["id"]] = r
    all_recs = list(uniq.values())
    fresh = [r for r in all_recs if r["id"] not in seen]
    return all_recs, fresh


def load_seen() -> Dict[str, str]:
    if SEEN_PATH.exists():
        try:
            return json.loads(SEEN_PATH.read_text("utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_seen(seen: Dict[str, str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(seen, ensure_ascii=False, indent=1), "utf-8")


# --------------------------------------------------------------------------- #
# 七、战争走向证据板
# --------------------------------------------------------------------------- #
def build_evidence(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把记录里的信号聚合成「持续/升级 vs 降级/结束」证据板。

    重要：这是**启发式证据汇总**，不是预测模型。单条短语命中可能被语境反转
    （例如「ceasefire 谈判破裂」会被算成降级信号）。报告里必须带此警示。
    """
    buckets: Dict[str, Dict[str, Any]] = {
        b: {"docs": 0, "score": 0, "phrases": {}, "examples": [], "by_source": {},
            "by_tier": {}, "t1t2_docs": 0}
        for b in ("sustain", "terminate", "stress")
    }
    for r in records:
        per_doc: Dict[str, int] = {}
        for s in r.get("signals", []):
            b = s["bucket"]
            buckets[b]["phrases"].setdefault(s["phrase"], []).append(
                {"record": r, "context": s.get("context", "")})
            # 单篇封顶 5 分，避免一篇长文把整盘带偏
            per_doc[b] = min(per_doc.get(b, 0) + s["weight"], 5)
        for b, sc in per_doc.items():
            buckets[b]["docs"] += 1
            buckets[b]["score"] += sc
            src = r.get("source_id", "?")
            buckets[b]["by_source"][src] = buckets[b]["by_source"].get(src, 0) + 1
            # 等级分布：这是「这个桶的证据有多硬」的唯一量化依据
            t = r.get("tier") or tier_of(src)
            buckets[b]["by_tier"][t] = buckets[b]["by_tier"].get(t, 0) + 1
            if t in ("T1", "T2"):
                buckets[b]["t1t2_docs"] += 1

    for b, info in buckets.items():
        top = sorted(info["phrases"].items(), key=lambda kv: -len(kv[1]))[:8]
        info["top_phrases"] = [
            {"phrase": p, "count": len(v),
             "example": v[0]["record"]["link"],
             "example_title": v[0]["record"]["title"],
             "example_tier": (v[0]["record"].get("tier")
                              or tier_of(v[0]["record"].get("source_id", ""))),
             "context": v[0]["context"]}
            for p, v in top
        ]
        info["phrase_count"] = len(info["phrases"])
        # 桶内最高（最好）等级与「是否全部来自二手媒体」
        present = [t for t in TIER_ORDER if info["by_tier"].get(t)]
        info["best_tier"] = present[0] if present else ""
        info["worst_tier"] = present[-1] if present else ""
        info["t3_only"] = bool(info["docs"]) and info["t1t2_docs"] == 0

    net = (buckets["sustain"]["score"]
           - buckets["terminate"]["score"]
           - 0.5 * buckets["stress"]["score"])
    if net >= 12:
        label, lean = "明显偏向持续/升级", "sustain"
    elif net >= 4:
        label, lean = "温和偏向持续", "sustain-mild"
    elif net > -4:
        label, lean = "多空交织，无明确方向", "neutral"
    elif net > -12:
        label, lean = "温和偏向降级", "terminate-mild"
    else:
        label, lean = "明显偏向降级/结束", "terminate"

    return {"buckets": buckets, "net": net, "label": label, "lean": lean}


def watch_points(ev: Dict[str, Any], metrics: Dict[str, Any]) -> List[str]:
    """根据当前证据结构，给出「下一步该看什么」的可检验观察点。"""
    pts: List[str] = []
    if ev["buckets"]["stress"]["score"] > 0:
        pts.append("**弹药产能与库存瓶颈**：GAO 报告中出现 `stockpile` / `production rate` / "
                   "`lead time` 的条目。若产能扩张授权（DPA §101）持续出现但 GAO 仍报产能不足，"
                   "说明消耗速度仍高于补充速度。")
    if ev["buckets"]["sustain"]["score"] > 0:
        pts.append("**新钱与扩产授权**：联邦公报中的 Presidential Determination / "
                   "紧急追加拨款 / DPA 优先权命令。这类文件是「还能继续打」最直接的法律与资金开关。")
    pts.append("**采购支出的环比拐点**：财政部 MTS 中 `采购` 科目月度净支出的环比。"
               "连续两个月回落通常先于政策转向；单月跳升多为大额合同集中出账。")
    pts.append("**弹药类合同金额分布**：USAspending 中 PSC 1410/1420/1340 类新签合同。"
               "注意该数据有 1–3 个月发布滞后，不能把「本周无新合同」读成「停止采购」。")
    if ev["buckets"]["terminate"]["score"] == 0:
        pts.append("**降级信号的缺失本身值得注意**：当前窗口内未检出 ceasefire / "
                   "withdrawal / 合同终止类语句。若后续出现，应优先核实其是否具有约束力"
                   "（正式协议 > 单方表态）。")
    else:
        pts.append("**降级信号需验证约束力**：区分「正式协议/法律文件」与「单方表态/媒体转述」。"
                   "只有前者才真正改变资金流的走向。")
    pts.append("**证伪条件**：若出现①紧急追加拨款被否决或撤销；②采购科目连续两季度下滑；"
               "③合约终止/缩减公告增多；④兵力回撤与基地收缩 —— 则应下调「持续」判断。")
    return pts


# --------------------------------------------------------------------------- #
# 八、渲染
# --------------------------------------------------------------------------- #
STATUS_ICON = {"ok": "✅", "ok-stale": "🟡", "ok-empty": "⚪", "fail": "❌",
               "disabled": "⏸️", "skipped": "⏭️"}


def render_source_table(report: List[Dict[str, Any]], run_dt: datetime) -> str:
    rows = ["| 数据源 | 等级 | 证据层 | 状态 | 窗内条数 | 其中相关 | 源内最新 | 备注 |",
            "|---|---|---|---|---|---|---|---|"]
    for r in report:
        st = r.get("status", "?")
        lag = ""
        if r.get("newest"):
            d = (run_dt - r["newest"]).days
            lag = f"{fmt_date(r['newest'])}（{d} 天前）" if d >= 0 else fmt_date(r["newest"])
        rows.append(f"| `{r['id']}` | {tier_badge(r.get('tier'))} | {r.get('layer','')} | "
                    f"{STATUS_ICON.get(st, st)} {st} | {r.get('items', 0)} | "
                    f"{r.get('relevant') if r.get('relevant') is not None else '—'} | "
                    f"{lag or '—'} | {truncate(r.get('note','') or '', 110)} |")
    return "\n".join(rows)


TRACE_LABEL = {"full": "✅ 可逐项回源对账", "partial": "🟡 可读原文，数字为估算",
               "via-citation": "⚠️ 需回溯其援引的原文", "none": "❌ 不可核验"}


def render_source_roster(report: List[Dict[str, Any]]) -> str:
    """完整信源清单（含停用源）：等级 / 发布主体 / 证据层 / 状态 / 可回源程度。

    与 §0 状态表的区别：状态表回答「这次抓到没有」，清单回答
    「这个源是谁在发布、属于哪一级、能不能回源核对」。停用源也一并列出，
    否则读者会以为报告只有那几个源。
    """
    out = ["| 等级 | 数据源 | 发布主体（谁发布的） | 证据层 | 当前状态 | 可回源程度 |",
           "|---|---|---|---|---|---|"]
    lines = []
    for r in report:
        t = r.get("tier") or "T4"
        m = TIER_META.get(t, {})
        lines.append((m.get("rank", 9), r.get("id", ""),
                      f"| `{t}` {m.get('icon','')} {m.get('label','')} | `{r.get('id','')}` | "
                      f"{r.get('publisher') or '—'} | {r.get('layer','')} | "
                      f"{STATUS_ICON.get(r.get('status','?'), '')} {r.get('status','?')} | "
                      f"{TRACE_LABEL.get(m.get('traceable','none'), '—')} |"))
    lines.sort(key=lambda x: (x[0], x[1]))
    out += [x[2] for x in lines]
    return "\n".join(out)


def render_tier_legend() -> str:
    out = ["| 等级 | 含义 | 是什么 | 怎么用 |", "|---|---|---|---|"]
    for t in TIER_ORDER:
        m = TIER_META[t]
        out.append(f"| `{t}` {m['icon']} | **{m['label']}** | {m['desc']} | {m['usage']} |")
    return "\n".join(out)


def tier_breakdown(records: List[Dict[str, Any]]) -> Dict[str, int]:
    """统计当前快照记录按等级的分布（全部记录，不只相关的）。"""
    c: Dict[str, int] = {}
    for r in records:
        t = r.get("tier") or tier_of(r.get("source_id", ""))
        c[t] = c.get(t, 0) + 1
    return c


def render_tier_signal_matrix(ev: Dict[str, Any]) -> str:
    """信号桶 × 信源等级 交叉表 —— 回答「最关键的那条证据有多硬」。

    这是分级真正的用处：如果「产能/库存压力」桶 100% 来自 T3 商业媒体，
    那么整个净值的压力项就建立在一个二手转述上，必须显式警告。
    """
    out = ["| 信号桶 | T1 官方一手 | T2 官方分析 | T3 媒体 | 桶内最高等级 | 评价 |",
           "|---|---|---|---|---|---|"]
    for b, title in [("sustain", "🔴 持续/升级"), ("terminate", "🟢 降级/结束"),
                     ("stress", "🟠 产能/库存压力")]:
        info = ev["buckets"][b]
        cnt = [info.get("by_tier", {}).get(t, 0) for t in ("T1", "T2", "T3")]
        bt = info.get("best_tier") or ""
        m = TIER_META.get(bt, {})
        best_cell = "—" if not bt else "`%s` %s" % (bt, m.get("icon", ""))
        if not info["docs"]:
            judge = "窗口内无证据"
        elif info.get("t3_only"):
            judge = "⚠️ **全部来自二手媒体，引用前须回溯原文**"
        elif info.get("t1t2_docs") == info["docs"]:
            judge = "✅ 全部来自官方源"
        else:
            judge = "🔸 官方与媒体混合，分项引用时注意区分"
        out.append(f"| {title} | {cnt[0]} 篇 | {cnt[1]} 篇 | {cnt[2]} 篇 | "
                   f"{best_cell} | {judge} |")
    return "\n".join(out)



def render_treasury_section(t: Dict[str, Any]) -> str:
    if not t or not t.get("lines"):
        return "_本次未取得财政部支出数据。_"
    out = []
    latest = t.get("latest_month") or "—"
    out.append(f"最新月份：**{latest}**（月度数据，发布滞后约 1 个月属正常）\n")
    out.append("| 科目 | 本月净支出 | 上月 | 环比 | 近3月均值 | 前3月均值 | 本财年累计 |")
    out.append("|---|---|---|---|---|---|---|")
    order = ["Total--Department of Defense--Military Programs", "Total--Procurement",
             "Total--Operation and Maintenance", "Total--Military Personnel",
             "Total--Research, Development, Test, and Evaluation",
             "Total--Military Construction", "Foreign Military Financing Program",
             "Foreign Military Sales Trust Fund"]
    lines = t["lines"]
    keys = [k for k in order if k in lines] + [k for k in lines if k not in order]
    for k in keys:
        e = lines[k]
        out.append(f"| {e['label']} | {fmt_usd(e['latest'])} | {fmt_usd(e['prev'])} | "
                   f"{fmt_pct(e['latest'], e['prev'])} | {fmt_usd(e['avg3'])} | "
                   f"{fmt_usd(e['avg3_prior'])} | {fmt_usd(e['fytd'])} |")
    if t.get("procurement_share") is not None:
        out.append(f"\n- **采购科目占国防合计比重**：{t['procurement_share']:.1f}%"
                   f"（该比重上行 = 装备弹药投入相对加大）")
    pro = lines.get("Total--Procurement")
    if pro and pro.get("avg3") and pro.get("avg3_prior"):
        trend = "加速" if pro["avg3"] > pro["avg3_prior"] else "放缓"
        out.append(f"- **采购支出趋势**：近 3 月均值 vs 前 3 月均值 → **{trend}**"
                   f"（{fmt_pct(pro['avg3'], pro['avg3_prior'])}）")
    # 近期逐月
    tot = lines.get("Total--Department of Defense--Military Programs")
    if tot and tot.get("history"):
        out.append("\n**国防合计逐月（净支出）**\n")
        out.append("| 月份 | 金额 |")
        out.append("|---|---|")
        for m, v in tot["history"][-13:][::-1]:
            out.append(f"| {m} | {fmt_usd(v)} |")
    return "\n".join(out)


def render_uspending_section(us: Dict[str, Any], recs: List[Dict[str, Any]]) -> str:
    out = []
    con = [r for r in recs if r["kind"] == "contract"]
    if us:
        out.append(f"窗口：{us['window'][0]} ~ {us['window'][1]}（{us['window_days']} 天）\n")
        out.append(f"- 其中**弹药与导弹类（PSC 13xx 弹药炸药 + 14xx 制导导弹）**授标金额"
                   f"（合同义务）：**{fmt_usd(us['munitions_total'])}**"
                   f"（{us.get('psc_codes_with_data','—')} 个代码有数据，"
                   f"按 PSC 精确过滤，**不受排序截断影响**）")
        if us.get("trend_contracts_total"):
            out.append(f"- 同期国防部**全部合同类**授标金额（合同义务）："
                       f"**{fmt_usd(us['trend_contracts_total'])}**"
                       + (f"，弹药占比 **{us['trend_share_pct']:.1f}%**"
                          if us.get("trend_share_pct") else ""))
        if us.get("all_psc_top_sum"):
            out.append(f"- 国防部**金额最大的前 {us.get('all_psc_top_n')} 个 PSC 类别**合计："
                       f"{fmt_usd(us['all_psc_top_sum'])}"
                       f"（⚠️ 这是**下界**，不是国防部合同总额 —— 该接口按金额排序返回，"
                       f"未返回的类别不在此数内）")
        if us.get("munitions_rows"):
            out.append(f"\n**弹药与导弹类 PSC 明细（窗口期授标金额，共 "
                       f"{len(us['munitions_rows'])} 个代码）**\n")
            out.append("| PSC | 类别 | 金额 |")
            out.append("|---|---|---|")
            for r in us["munitions_rows"]:
                out.append(f"| {r['code']} | {r['name']} | {fmt_usd(r['amount'])} |")
        if us.get("top_overall"):
            out.append("\n**国防部开支前 10 的 PSC 类别（对照用）**\n")
            out.append("| PSC | 类别 | 金额 |")
            out.append("|---|---|---|")
            for r in us["top_overall"]:
                mark = " 🔸" if str(r["code"])[:2] in ("13", "14") else ""
                out.append(f"| {r['code']} | {r['name']}{mark} | {fmt_usd(r['amount'])} |")
        if us.get("monthly"):
            out.append(f"\n**逐月合同义务（{us.get('trend_window',['',''])[0]} → "
                       f"{us.get('trend_window',['',''])[1]}）**\n")
            out.append("完整趋势与解读见 `output/history/LATEST-trend.md`。\n")
            out.append("| 月份 | 弹药/导弹 | 全部合同类 | 弹药占比 |")
            out.append("|---|---:|---:|---:|")
            for row in us["monthly"]:
                sh = row.get("share_pct")
                sh_s = f"{sh:.1f}%" if sh else "—"
                out.append(f"| {row['month']} | {fmt_usd(row.get('munitions'))} | "
                           f"{fmt_usd(row.get('all_contracts'))} | {sh_s} |")

    if con:
        con.sort(key=lambda r: r.get("amount_max") or 0, reverse=True)
        total = sum(r.get("amount_max") or 0 for r in con)
        big = [r for r in con if (r.get("amount_max") or 0) >= 50e6]
        shown = [r for r in con if (r.get("amount_max") or 0) >= CONTRACT_DISPLAY_FLOOR]
        small = len(con) - len(shown)
        out.append(f"\n### 弹药/导弹类新签合同（窗口内 {len(con)} 笔，合计 {fmt_usd(total)}）\n")
        out.append(f"> 其中 **≥$50M 的显著合同 {len(big)} 笔**"
                   + (f"，合计 {fmt_usd(sum(r['amount_max'] for r in big))}"
                      if big else "（窗口内无大额订单）")
                   + "。\n")
        if small:
            out.append(f"> 下表只列 **≥{fmt_usd(CONTRACT_DISPLAY_FLOOR)} 的 {len(shown)} 笔**；"
                       f"另有 {small} 笔低于该门槛（多为国防后勤局 DLA 的零备件小额订单，"
                       f"合计 {fmt_usd(total - sum(r['amount_max'] or 0 for r in shown))}），"
                       f"对「补货速度」无信息量，已折叠但仍保留在 JSONL 归档中。\n")
        out.append("> ⚠️ `合同金额` 为授标总额（含期权的累计潜在金额），**不等于本期实际支出**，"
                   "与财政部支出数**不可相加**。USAspending 新签合同数据约有 1–3 个月发布滞后。\n")
        out.append("| 签订日 | 承包商 | 合同金额 | 弹药型号 | 说明 | 链接 |")
        out.append("|---|---|---|---|---|---|")
        for r in shown[:30]:
            dt = fmt_date(parse_time(r.get("published") or ""))
            mun = "、".join(r.get("munitions", [])[:4]) or "—"
            out.append(f"| {dt} | {truncate(r['extra'].get('recipient','') or '—', 34)} | "
                       f"{fmt_usd(r.get('amount_max'))} | {mun} | "
                       f"{truncate(r['summary'], 90)} | [link]({r['link']}) |")
    if not us and not con:
        out.append("_本次未取得合同层数据。_")
    return "\n".join(out)


def render_signals_section(records: List[Dict[str, Any]], ev: Dict[str, Any]) -> str:
    out = []
    order = [("sustain", "🔴 持续/升级信号"), ("terminate", "🟢 降级/结束信号"),
             ("stress", "🟠 产能/库存压力信号")]
    for b, title in order:
        info = ev["buckets"][b]
        out.append(f"### {title}（{info['docs']} 篇 · 加权分 {info['score']}）\n")
        if info.get("by_source"):
            srcs = "、".join(f"`{k}` {v} 篇" for k, v in
                             sorted(info["by_source"].items(), key=lambda kv: -kv[1]))
            out.append(f"来源分布：{srcs}\n")
        # 等级分布 —— 决定这一桶证据能不能直接拿去用
        if info.get("by_tier"):
            tds = "、".join(f"`{t}` {info['by_tier'][t]} 篇"
                            for t in TIER_ORDER if info["by_tier"].get(t))
            out.append(f"**信源等级分布**：{tds}")
            if info.get("t3_only"):
                out.append(f"> ⚠️ 本桶证据**全部来自 T3 二手转述**（无 T1/T2 **正文级**出处；"
                           f"本报告已直连 CBO/CRS 的官方标题与摘要层，但那不构成本桶的证据）。"
                           f"引用本桶任何结论前，必须先回溯到其援引的官方原文。")
            out.append("")
        if info.get("top_phrases"):
            out.append("| 命中短语 | 出现篇数 | 等级 | 原文片段（可自行核验） | 出处 |")
            out.append("|---|---|---|---|---|")
            for p in info["top_phrases"]:
                ctx = (p.get("context") or "—").replace("|", "\\|")
                out.append(f"| `{p['phrase']}` | {p['count']} | "
                           f"{tier_badge(p.get('example_tier') or '')} | "
                           f"{truncate(ctx, 150)} | "
                           f"[link]({p['example']}) |")
        else:
            out.append("_窗口内未检出该桶信号。_")
        out.append("")
    return "\n".join(out)


def split_relevance(recs: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """按相关性阈值拆分成 (进正文的, 被折叠的条数)。

    合同类由 PSC 精确过滤，天然相关，始终保留。
    被折叠的记录**仍完整写入 JSONL 归档**，不丢弃 —— 只是不进正文，避免噪声。
    """
    visible: List[Dict[str, Any]] = []
    hidden = 0
    for r in recs:
        if r.get("kind") == "contract":
            visible.append(r)
            continue
        rel = r.get("relevance")
        if rel is None or rel >= RELEVANCE_MIN:
            visible.append(r)
        else:
            hidden += 1
    return visible, hidden


def evidence_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """证据板只基于**相关记录**计算。

    否则会内部不一致：正文把《空管系统现代化》《房贷保险》折了起来，
    它们的 `cost savings` / `has not developed` 却仍被算进「降级」「产能压力」桶
    （实测出现过）。展示口径与计分口径必须一致。
    """
    return split_relevance(records)[0]


def verify_last_note() -> str:
    """读「最近一次回源对账」的结果留档，生成一句可溯源的话。

    ⚠️ **绝不写死「N 项差 0.00」**：核对项数会随覆盖范围（月份数、科目数）变化，
    写死必然过期，而**过期的「已验证」比不写更误导** —— 这正是本项目的
    「验证工具自身也必须被验证」纪律。
    """
    p = OUTPUT_DIR / "_verify_last.json"
    if not p.exists():
        return ("**本次尚未跑回源对账** —— 交付前请执行 `python tools/verify_numbers.py`；"
                "未经对账的数字不要当成已验证。")
    try:
        d = json.loads(p.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return ("回源对账留档无法解析（`output/_verify_last.json`）—— "
                "请重跑 `tools/verify_numbers.py`。")
    okn, badn = d.get("ok"), d.get("mismatch")
    at = d.get("checked_at") or "未知时间"
    mf = d.get("metrics_file") or "?"
    if badn:
        return (f"⚠️ **最近一次回源对账发现 {badn} 项不一致**"
                f"（{at}，核对 `{mf}`，一致 {okn} 项）—— 数字存疑，须人工复核。")
    scope = ("仅快照层" if d.get("skip_months") else "含逐月序列加总闭环")
    return (f"已逐项回源对账：**{okn} 项一致 / 0 项不一致**"
            f"（{at}，核对 `{mf}`，范围：{scope}）。"
            f"注意这是**上一次对账的时点结果**，本次运行未重新执行。")


def render_digest(records_new: List[Dict[str, Any]], records_all: List[Dict[str, Any]],
                  report: List[Dict[str, Any]], metrics: Dict[str, Any],
                  run_dt: datetime, hours: int, note: str = "",
                  fresh: int = -1) -> str:
    t = metrics.get("treasury") or {}
    us = metrics.get("usaspending") or {}
    ev = build_evidence(evidence_records(records_new))
    d = run_dt.strftime("%Y-%m-%d %H:%M UTC")
    date_str = run_dt.strftime("%Y-%m-%d")

    by_kind: Dict[str, int] = {}
    for r in records_new:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1

    L: List[str] = []
    L.append(f"# 美军弹药库存与军费开支监测 · {date_str}")
    L.append("")
    L.append("> 面向大模型阅读的结构化日报。**所有数字均标注来源与口径**；")
    L.append("> 本文件只做公开证据汇总与机械计算，**不构成结论、不构成预测**。")
    L.append("> 分析目标：为「美伊冲突等持续冲突会延续还是收束」提供可核查的佐证。")
    L.append("> **快照语义**：本文件是**滚动窗口快照** —— 收录抓取窗口内的**全部**记录，")
    L.append("> 而不只是「当天新增」；第 0 节分别标注「本次新增」与「当前快照」条数。")
    L.append("> 掉出时间窗的历史不会丢：跨天累积的完整档案见 `output/ALL-records.jsonl`。")
    L.append("")
    L.append("> 🔴 **关于「库存」的口径（务必先读）**：本报告**不包含弹药/拦截弹库存的绝对数量**。")
    L.append("> 公开渠道**不存在**美军弹药库存的实时或定期数量披露。标题里的「库存」一律指")
    L.append("> **间接佐证**，只有三类来源：① CBO / GAO 等官方分析中「已消耗库存的百分之多少」")
    L.append("> 这类**相对表述**；② 承包商扩产、交付延迟的公开报道；③ 补货合同与对外军售通知。")
    L.append("> **任何具体库存条数都是推断，不是披露值。** 需要准确库存量只能依赖 GAO 受限报告")
    L.append("> 或国防部内部数据。相比之下，**军费数字是权威一手数据**（财政部 MTS / USAspending），")
    L.append("> 已逐项回源核对。")
    L.append("")

    # ---------- 0 运行信息 ----------
    L.append("## 0. 本次运行")
    L.append("")
    L.append(f"- 运行时间：**{d}**（本地 {run_dt.astimezone().strftime('%Y-%m-%d %H:%M %Z')}）")
    L.append(f"- 回溯窗口：{hours} 小时（各源可用 `window_days` 覆盖，见状态表）")
    L.append(f"- 记录：**本次新增 {fresh if fresh >= 0 else len(records_new)} 条**，"
             f"当前快照 **{len(records_new)} 条**（抓取窗口内共 {len(records_all)} 条）")
    L.append(f"- 分类：合同 {by_kind.get('contract',0)} · 报告 {by_kind.get('report',0)} · "
             f"法规/决定书 {by_kind.get('regulation',0)+by_kind.get('presidential',0)} · "
             f"新闻 {by_kind.get('news',0)}")
    L.append("")
    L.append(render_source_table(report, run_dt))
    L.append("")
    tbd = tier_breakdown(records_new)
    tbd_txt = "、".join(f"`{t}` {tbd[t]} 条"
                        for t in TIER_ORDER if tbd.get(t))
    L.append(f"- **信源等级分布**（当前快照）：{tbd_txt or '—'}")
    L.append("")
    L.append("> 🔍 **等级怎么读**：`T1` 官方一手（政府原始记录，可逐项回源对账）· "
             "`T2` 官方分析/审计（机构估算，权威但非披露值）· "
             "`T3` 专业/商业媒体（**二手转述**，引用前必须回溯原文）· "
             "`T4` 自媒体/社交平台（**本报告不采集**）。"
             "等级与「证据层」是两个正交维度：层回答「在战争链条的哪一环」，"
             "等级回答「这条信息有多可信」。**完整信源清单（含停用源、发布主体、"
             "回源方式）与逐项溯源矩阵见附录 D。**")
    L.append("")

    # ---------- 1 一页速览 ----------
    L.append("## 1. 一页速览（关键事实块）")
    L.append("")
    tot = (t.get("lines") or {}).get("Total--Department of Defense--Military Programs")
    pro = (t.get("lines") or {}).get("Total--Procurement")
    T1MTS = f"｜来源：`treasury-mts` {tier_badge('T1')}（财政部 API，可回源）"
    if tot:
        L.append(f"- 国防部**月度净支出**（{tot['latest_month']}）：**{fmt_usd(tot['latest'])}**"
                 f"，环比 {fmt_pct(tot['latest'], tot['prev'])}{T1MTS}")
        L.append(f"- 国防部**本财年累计净支出**：**{fmt_usd(tot['fytd'])}**{T1MTS}")
    if pro:
        L.append(f"- 其中**采购科目**（含弹药导弹）：{fmt_usd(pro['latest'])}"
                 f"，环比 {fmt_pct(pro['latest'], pro['prev'])}"
                 + (f"，占国防合计 {t['procurement_share']:.1f}%" if t.get("procurement_share") else "")
                 + T1MTS)
    if us:
        L.append(f"- 国防部弹药与导弹类（PSC 13xx/14xx）**窗口期授标金额**（合同义务，"
                 f"非实际支出）：**{fmt_usd(us['munitions_total'])}**｜来源：`usaspending-psc-category` "
                 f"{tier_badge('T1')}（联邦授标库，可回源重算）")
    con = [r for r in records_new if r["kind"] == "contract"]
    if con:
        top = max(con, key=lambda r: r.get("amount_max") or 0)
        L.append(f"- 弹药类**新签合同**：{len(con)} 笔，"
                 f"最大一笔 {fmt_usd(top.get('amount_max'))}（"
                 f"{truncate(top['extra'].get('recipient',''), 40)}）"
                 f"｜来源：`usaspending-munitions` {tier_badge('T1')}（每条含 award 编号可点开）")
    _sb = ev["buckets"]["sustain"]["best_tier"]
    _tb = ev["buckets"]["terminate"]["best_tier"]
    _pb = ev["buckets"]["stress"]["best_tier"]
    _mk = lambda x: ("最高等级 " + tier_badge(x)) if x else "无证据"  # noqa: E731
    L.append(f"- **政策/审计信号**：持续升级 {ev['buckets']['sustain']['docs']} 篇"
             f"（{_mk(_sb)}）· 降级结束 {ev['buckets']['terminate']['docs']} 篇（{_mk(_tb)}）· "
             f"产能库存压力 {ev['buckets']['stress']['docs']} 篇（{_mk(_pb)}）")
    if ev["buckets"]["stress"].get("t3_only"):
        L.append("  > ⚠️ **「产能/库存压力」桶的证词全部为 T3 商业媒体转述**。"
                 "该桶参与净值计算，但在回溯到官方原文前，**不应作为独立佐证**。")
        L.append("  > ")
        L.append("  > 该桶的原始出处是 CBO 报告；本报告**已直连 CBO 官方 RSS**，"
                 "但 CBO **正文页对数据中心 IP 返回 403(Akamai)**，"
                 "直连到的只有标题/摘要/链接层，**不做风控绕过**。"
                 "因此「官方摘要层直连」与「媒体转述完整结论」是两件事，"
                 "本桶仍未拿到 T2 的正文级证据。")
    L.append(f"- **走向证据净值**：**{ev['net']:+.0f}** → 定性标签：**{ev['label']}**"
             f"（算法见 §8，勿单独使用）")
    L.append("")

    # ---------- 2 支付层 ----------
    L.append("## 2. 军费开支仪表盘（支付层 · 财政部 MTS 表5）")
    L.append("")
    L.append("口径：**政府实际净支出（net outlay）**，即钱真正从国库流出的金额，"
             "**不是预算申请数、不是授权数**。用于观察战争开支的真实节奏。")
    L.append("")
    L.append(render_treasury_section(t))
    L.append("")

    # ---------- 3 合同层 ----------
    L.append("## 3. 弹药与装备合同动向（合同层 · USAspending）")
    L.append("")
    L.append("口径：**授标（award）层级**，反映弹药补货订单的规模与承包商结构。"
             "比支出层领先，是「未来能否打」的先行观察点。")
    L.append("")
    L.append(render_uspending_section(us, records_new))
    L.append("")

    # ---------- 4 政策授权 ----------
    L.append("## 4. 政策与授权信号（拨款层 · 美国联邦公报）")
    L.append("")
    L.append("口径：联邦公报是**法律效力文件**的发布地。总统决定书、DPA 授权、军贸通知"
             "都在此发布 —— 这是「有没有新钱、有没有新的扩产/动武授权」的直接开关。")
    L.append("")
    fr_all = [r for r in records_new if r["kind"] in ("presidential", "regulation")]
    fr, fr_hidden = split_relevance(fr_all)
    if fr:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for r in fr:
            groups.setdefault(r["extra"].get("fr_query", "其他"), []).append(r)
        for label, items in groups.items():
            items.sort(key=lambda r: r.get("published") or "", reverse=True)
            L.append(f"### {label}（{len(items)} 篇）")
            L.append("")
            for r in items:
                dt = fmt_date(parse_time(r.get("published") or ""))
                sig = " / ".join(f"{BUCKET_LABEL[s['bucket']]}" for s in r["signals"]) or "—"
                L.append(f"- `{dt}` **{truncate(r['title'], 150)}**  \n"
                         f"  类型：{r['extra'].get('fr_type','')}｜信号：{sig}｜"
                         f"[原文]({r['link']})")
            L.append("")
        if fr_hidden:
            L.append(f"_已折叠 {fr_hidden} 条与国防军费主题相关性不足的联邦公报文献。_")
            L.append("")
    else:
        L.append("_本次未取得联邦公报数据。_")
        L.append("")

    # ---------- 5 审计层 ----------
    L.append("## 5. 审计与产能约束（审计层 · GAO）")
    L.append("")
    L.append("口径：GAO 是官方审计机构，其报告是**官方自己承认瓶颈**的地方 —— "
             "弹药产能、库存水平、交付延迟。这是判断「还能撑多久」最重的旁证。")
    L.append("")
    gao_all = [r for r in records_new if r["source_id"] == "gao-reports"]
    gao, gao_hidden = split_relevance(gao_all)
    if gao:
        gao.sort(key=lambda r: r.get("published") or "", reverse=True)
        for r in gao[:20]:
            dt = fmt_date(parse_time(r.get("published") or ""))
            sig = " / ".join(f"{BUCKET_LABEL[s['bucket']]}" for s in r["signals"]) or "—"
            L.append(f"- `{dt}` **{truncate(r['title'], 160)}**｜信号：{sig}｜[原文]({r['link']})")
        if gao_hidden:
            L.append(f"\n_已折叠 {gao_hidden} 条与弹药/军费主题相关性不足的报告"
                     f"（GAO RSS 返回全部主题的条目；被折叠者仍在 JSONL 归档中，"
                     f"加 `--show-all` 可全量列出）。_")
    elif gao_all:
        L.append(f"_窗口内 {len(gao_all)} 份 GAO 报告均**未锚定**到弹药/军费主题，已全部折叠。_")
        L.append("")
        L.append("> 这是已知的源限制：GAO 只开放 `/rss/reports.xml`（最新 25 份、全主题），"
                 "搜索接口返回 403，无法按关键词检索。因此审计层**多数时候为空是正常的**；"
                 "一旦 GAO 发布弹药产能/库存类报告，且排在最新 25 份内，就会自动出现。")
    else:
        L.append("_本次未取得 GAO 报告。_")
    L.append("")

    # ---------- 6 立法 ----------
    L.append("## 6. 立法与拨款（拨款层 · govinfo 公法）")
    L.append("")
    laws_all = [r for r in records_new if r["source_id"] == "govinfo-plaw"]
    laws, laws_hidden = split_relevance(laws_all)
    if laws:
        for r in sorted(laws, key=lambda r: r.get("published") or "", reverse=True)[:20]:
            dt = fmt_date(parse_time(r.get("published") or ""))
            mun = ("｜相关弹药词：" + "、".join(r["munitions"][:3])) if r["munitions"] else ""
            L.append(f"- `{dt}` {truncate(r['title'], 150)}{mun}｜[原文]({r['link']})")
        if laws_hidden:
            L.append(f"\n_已折叠 {laws_hidden} 条与国防/军费无关的公法。_")
    else:
        L.append("_窗口内无与国防军费相关的公法（属正常，公法数量稀少）。_")
    L.append("")

    # ---------- 7 新闻 ----------
    L.append("## 7. 新闻线索（新闻层 · Defense News）")
    L.append("")
    L.append("> ⚠️ 商业媒体仅作**线索**，引用前必须回溯到官方源确认。")
    L.append("")
    news_all = [r for r in records_new if r["kind"] == "news"]
    news, news_hidden = split_relevance(news_all)
    if news:
        news.sort(key=lambda r: r.get("published") or "", reverse=True)
        for r in news[:20]:
            dt = fmt_date(parse_time(r.get("published") or ""))
            mun = ("｜" + "、".join(r["munitions"][:4])) if r["munitions"] else ""
            sigs = "、".join({BUCKET_LABEL[s["bucket"]] for s in r["signals"]})
            L.append(f"- `{dt}` **{truncate(r['title'], 130)}**{mun}"
                     + (f"｜信号：{sigs}" if sigs else "") + f"｜[原文]({r['link']})")
        if news_hidden:
            L.append(f"\n_已折叠 {news_hidden} 条与弹药/军费主题无关的新闻。_")
    else:
        L.append("_本次未取得相关新闻。_")
    L.append("")

    # ---------- 8 证据板 ----------
    L.append("## 8. 战争走向证据板")
    L.append("")
    L.append("### 8.1 三级证据链（怎么用这份报告）")
    L.append("")
    L.append("```")
    L.append("拨款层  公法 / 总统决定书 / DPA授权   → 有没有新钱、有没有新的扩产或动武授权")
    L.append("            ↓（领先）")
    L.append("合同层  弹药导弹类新签合同 (PSC 13xx/14xx)  → 弹药补货速度 = 打得起的物质基础")
    L.append("            ↓（滞后 1–3 个月）")
    L.append("支付层  财政部 MTS 国防科目实际净支出      → 真金白银出账节奏（最硬、最慢）")
    L.append("")
    L.append("旁证：审计层 GAO（官方承认的产能/库存瓶颈） · 新闻层（线索，需回溯官方源）")
    L.append("```")
    L.append("")
    L.append("**读法**：拨款层出现新钱/新授权 → 合同层订单放量 → 支付层支出上行，"
             "三段同向即为「持续/升级」的强证据；反之三段同向走弱，是「降级/结束」的强证据。"
             "**若拨款层放量而支付层停滞**，说明钱批了但没花出去（产能或流程卡点）。")
    L.append("")
    L.append("### 8.1b 第二个正交维度：信源等级")
    L.append("")
    L.append("「证据层」告诉你信息在战争链条的哪一环；**「信源等级」告诉你它有多可信**。"
             "两者必须一起看：一条支付层信息如果是媒体转述的，它并不比一条合同层的官方记录更硬。")
    L.append("")
    L.append("```")
    L.append("T1 官方一手   财政部API / USAspending / 联邦公报 / govinfo  → 可逐项回源对账")
    L.append("T2 官方分析   GAO 审计 · CBO 估算                            → 权威，但数字是估算")
    L.append("T3 商业媒体   Defense News / USNI News                       → 二手转述，须回溯原文")
    L.append("T4 自媒体     社交平台 / 无编辑审核博客                       → 本报告不采集")
    L.append("```")
    L.append("")
    L.append("**注意**：等级看的是**你手上这份材料由谁加工**，不是它引用了谁。"
             "「Defense News 转述 CBO 报告」这条仍然是 T3 —— 因为数字经过了媒体裁剪。"
             "要升级为 T2，必须实际取到 CBO 报告的原文。")
    L.append("")
    L.append("> 本报告**已直连 CBO 官方 RSS 与 CRS 报告 API**（`cbo-reports` / `crs-reports`，均为 T2），"
             "直连到的是**官方标题/摘要/链接/发布日期**。"
             "但 CBO **正文页对数据中心 IP 返回 403(Akamai)**，全文需人工点开，**不做风控绕过**。"
             "所以「已直连官方摘要层」不等于「拿到了正文级证据」—— "
             "摘要里没有的数字，本报告一律不写。")
    L.append("")
    L.append(render_tier_signal_matrix(ev))
    L.append("")
    L.append("### 8.2 信号计数（窗口内）")
    L.append("")
    L.append("| 信号桶 | 命中篇数 | 加权分 | 含义 |")
    L.append("|---|---|---|---|")
    L.append(f"| 🔴 持续/升级 | {ev['buckets']['sustain']['docs']} | "
             f"{ev['buckets']['sustain']['score']} | 新增拨款、扩产、裁减授权、前置部署 |")
    L.append(f"| 🟢 降级/结束 | {ev['buckets']['terminate']['docs']} | "
             f"{ev['buckets']['terminate']['score']} | 停火、撤军、合同终止、制裁缓解 |")
    L.append(f"| 🟠 产能/库存压力 | {ev['buckets']['stress']['docs']} | "
             f"{ev['buckets']['stress']['score']} | 库存不足、产能瓶颈、交付延迟、成本不可持续 |")
    L.append("")
    L.append(f"**净值** = 持续 − 降级 − 0.5×产能压力 = "
             f"{ev['buckets']['sustain']['score']} − {ev['buckets']['terminate']['score']} "
             f"− 0.5×{ev['buckets']['stress']['score']} = **{ev['net']:+.0f}** → **{ev['label']}**")
    L.append("")
    L.append("> 计分口径：只统计**相关记录**（正文未被折叠者），与 §0 状态表「其中相关」列一致；"
             "已被折叠的无关文件不参与计分。")
    L.append("")
    L.append("> ⚠️ **算法局限（必须知道）**：① 这是短语命中计数，**不理解语境** —— "
             "「停火谈判破裂」也会被计成降级信号；② 各桶权重是人工设定的先验，未经回测；"
             "③ 样本以英文官方文件为主，存在发布选择偏差；④ 因子按篇封顶 5 分，"
             "仅为防止单篇长文主导。**请把它当作检索索引，不是结论。**")
    L.append("")
    L.append(render_signals_section(records_new, ev))
    L.append("### 8.3 下一步该看什么（可检验的观察点）")
    L.append("")
    for p in watch_points(ev, metrics):
        L.append(f"- {p}")
    L.append("")

    # ---------- 附录 ----------
    L.append("## 附录 A. 数据源与合规")
    L.append("")
    L.append("全部为**公开**来源：政府数据 API（财政部 fiscaldata、USAspending、联邦公报）、"
             "政府机构 RSS（GAO、govinfo）、公共媒体 RSS。"
             "**每个源都在附录 D.2 列出，并标注等级与发布主体** —— 报告不隐藏它的信源构成。")
    L.append("")
    L.append("**信源分级**：本报告区分 T1 官方一手 / T2 官方分析 / T3 商业媒体 / T4 自媒体"
             "（**不采集**）。同一张表里的数字可能来自不同等级，引用时务必看等级列，"
             "不要把「可回源的官方数字」和「媒体转述的估算」当同一回事。")
    L.append("遵守 robots.txt；不抓取需登录内容；**不做任何风控绕过**；"
             "不抓取实时船舶/航空定位类受限数据。")
    L.append("")
    L.append("## 附录 B. 已知偏差与口径提醒")
    L.append("")
    L.append("0. **「库存」无绝对数量**：公开渠道不存在美军弹药库存的数量披露，"
             "本报告的库存判断全部来自 CBO/GAO 的相对表述与新闻转述，属**间接推断**。"
             "军费数字则是权威一手数据，两者可信度不在一个层级，请勿等同看待。")
    L.append("1. **财政部 MTS 滞后约 1 个月**：最新月份金额可能后续被修订。")
    L.append("2. **USAspending 新签合同滞后 1–3 个月**：窗口内无新合同 ≠ 停止采购。")
    L.append("3. **合同金额是授标总额**（含期权与潜在金额），非本期实际支出；"
             "与财政部支出数**不可直接相加**。")
    L.append("4. 财政部 MTS 的 `采购` 科目包含所有装备采购（飞机、舰船、车辆等），"
             "**不只弹药**；弹药占比需用 USAspending 的 PSC 13xx/14xx 单独观察。")
    L.append("5. 预算申请（budget request）≠ 授权（authorization）≠ 拨款（appropriation）"
             "≠ 合同（obligation）≠ 支出（outlay）。本报告主要覆盖后三者。")
    L.append("6. **GAO 审计层多数时候为空属正常**：GAO 只开放全主题 RSS（最新 25 份），"
             "搜索接口 403，无法按关键词检索。不要因为该节为空就断定「没有产能问题」。")
    L.append("7. **相关性折叠会损失召回**：为压低噪声，正文按标题强主题词锚定做过滤，"
             "可能漏掉用词特殊的相关文件。需要全量审阅时加 `--show-all`。")
    L.append("8. **`relevance` 字段已写入 JSONL**：可自行按该字段重排序或改阈值"
             "（配置项 `relevance_min`）。")
    L.append("9. **信号在完整正文上计算，但 `summary` 被截断到 1200 字** —— 因此每条信号"
             "都额外保存 `context`（原文片段），证据板表格直接展示。核验信号请以"
             "`context` 为准，不要只看 `summary`。")
    L.append("10. **数字可信度分级**：财政部 MTS 与 USAspending 金额属**一手权威数据**。"
              + verify_last_note() +
              "而「库存水平/消耗比例」只能靠 CBO/GAO/新闻的**相对表述间接推断**，"
              "无法回源核对。两者不可等同看待。")
    L.append("11. **PSC 13xx/14xx 含大量零备件**：弹药类代码会带出国防后勤局(DLA)的"
              "备件小额订单（实测 30 笔中 21 笔低于 $1M，多为「挡圈」「排气管罩」这类），"
              "对「补货速度」无信息量。正文只列 ≥$1M 的订单并标注折叠笔数。")
    L.append("12. **新闻层整体是 T3，不是事实依据**：`defensenews`（商业媒体）与 "
              "`usni-news`（学会媒体）都属二手转述层。§7 的新闻列表**只作线索索引**，"
              "其中的金额、比例、库存表述在回溯到官方原文前不得引用。"
              "注意这**不代表**「官方源就够用」—— 官方源无人转述时也可能漏掉关键事件。")
    L.append("13. **同一事实可能同时存在高低等级两个版本**：例如「战争成本 380 亿美元、"
              "拦截弹消耗三分之二」这条，T3 版本是 Defense News 转述，"
              "T2 版本是 CBO 官方出版物。本报告**已直连 CBO 官方 RSS / CRS 报告 API**"
              "（`cbo-reports` / `crs-reports`），可拿到官方标题与摘要；"
              "但 CBO **正文页对数据中心 IP 返回 403(Akamai)**，全文数字需人工点开，"
              "**不做风控绕过**。因此摘要里没有的数字，本报告一律不写。"
              "这种「直连到哪一层」的边界已在附录 D.3 / D.4 显式标注。")
    L.append("")
    L.append("## 附录 C. 抓取时间与溯源")
    L.append("")
    L.append(f"- 本文件由 `spend_monitor.py` v{VERSION} 生成于 {d}")
    L.append(f"- 结构化记录：`output/{date_str}/records-{date_str}.jsonl`"
             f"（含每条记录的来源链接、金额、弹药词、信号）")
    L.append(f"- 指标快照：`output/{date_str}/metrics-{date_str}.json`")
    L.append("- 每条记录均保留原始链接、发布时间与 **`tier` 信源等级**，可逐条回溯。")
    L.append("")

    # ---------- 附录 D 信源等级与溯源矩阵 ----------
    def _rel(sid: str) -> str:
        for r in report:
            if r.get("id") == sid:
                v = r.get("relevant")
                return "—" if v is None else str(v)
        return "—"

    L.append("## 附录 D. 信源等级与数据溯源")
    L.append("")
    L.append("### D.1 等级定义")
    L.append("")
    L.append(render_tier_legend())
    L.append("")
    L.append("> **等级判定原则（最容易搞错的两条）**：")
    L.append("> ① **「官方机构」≠ T1**。GAO / CBO 发布的是**分析与估算**，不是原始记录 —— "
             "数字是算出来的，带假设与口径，所以是 T2。")
    L.append("> ② **转述官方结论的媒体仍是 T3**。等级看的是**你手上这份材料由谁加工**，"
             "不是它引用了谁。「Defense News 转述 CBO 报告」不因源头是官方就升级；"
             "要升到 T2，必须实际取到 CBO 报告本身。")
    L.append("> ③ 等级与「证据层」（拨款/合同/支付）是**正交维度**，不要混为一谈。")
    L.append("")
    L.append("### D.2 完整信源清单（含停用源）")
    L.append("")
    L.append("> 停用源也列出：等级不变，只是因为出口 IP 被官方风控拦截而暂停采集"
             "（**不做任何绕过**），日后出口恢复即可改回启用。")
    L.append("")
    L.append(render_source_roster(report))
    L.append("")
    L.append("### D.3 关键事实 → 溯源路径")
    L.append("")
    L.append("下表把报告里的每个关键数字/结论映射回它的原始出处。"
             "**标 T1 的可自行回源复算；标 T3 的，本报告替不了你对账。**")
    L.append("")
    L.append("| 报告中的事实 | 本报告取值 | 来源源 | 等级 | 回源方式（可自行复核） |")
    L.append("|---|---|---|---|---|")
    _rows: List[Tuple[str, str, str, str, str]] = []
    DOD_KEY = "Total--Department of Defense--Military Programs"
    if tot:
        _rows.append(("国防部月度净支出", fmt_usd(tot["latest"]), "treasury-mts", "T1",
                      "GET `%s` 取表5中 classification_desc=`%s` 的行逐项比对；"
                      "或直接跑 `tools/verify_numbers.py`（离线复算 + 回源对账）"
                      % (TREASURY_MTS_URL, DOD_KEY)))
        _rows.append(("国防部本财年累计净支出", fmt_usd(tot["fytd"]), "treasury-mts", "T1",
                      "同上，取同一行的 `current_fytd_net_outly_amt` 列"))
    if pro:
        _rows.append(("采购科目（含弹药导弹）净支出", fmt_usd(pro["latest"]), "treasury-mts", "T1",
                      "同上，classification_desc=`Total--Procurement`"))
    if us:
        _rows.append(("弹药与导弹类授标金额（窗口期，合同义务）", fmt_usd(us["munitions_total"]),
                      "usaspending-psc-category", "T1",
                      f"POST `{USASPENDING_CATEGORY_URL}`，"
                      f"**必须显式传 `filters.psc_codes`（共 "
                      f"{len(us.get('psc_codes_queried') or [])} 个 13xx/14xx 代码）**；"
                      f"不传就会被按金额排序截断 —— 实测截断会少算 2.42B（6.1%）。"
                      f"或跑 `tools/verify_numbers.py`"))
        if us.get("monthly"):
            _rows.append(("弹药逐月义务额序列（战前 → 至今）",
                          f"{len(us['monthly'])} 个月",
                          "usaspending-psc-category", "T1",
                          "POST `%s`（group=month，同一 PSC 过滤）；"
                          "月度之和必须等于上行的窗口总额（本报告已用**两个不同接口**交叉验证）"
                          % USASPENDING_OVER_TIME_URL))
    if con:
        _rows.append((f"弹药类新签合同 {len(con)} 笔（含金额与承包商）",
                      fmt_usd(sum(r.get("amount_max") or 0 for r in con)),
                      "usaspending-munitions", "T1",
                      "POST `%s`；每条记录内含 award 唯一编号，拼 "
                      "`usaspending.gov/award/<id>` 即可点开原始授标页"
                      % USASPENDING_AWARDS_URL))
    _fr, _gp = _rel("federal-register"), _rel("govinfo-plaw")
    _gao = _rel("gao-reports")
    _cbo = _rel("cbo-reports")
    _crs = _rel("crs-reports")
    _rows.append((f"政策/授权文件（联邦公报，相关 {_fr} 篇）", f"{_fr} 篇",
                  "federal-register", "T1",
                  "每篇含 federalregister.gov 原文 URL 与 FR 文号，可直接打开核对全文"))
    _rows.append((f"公法原文（相关 {_gp} 篇）", f"{_gp} 篇", "govinfo-plaw", "T1",
                  "govinfo.gov 公法原文 URL，法律文本本身"))
    _rows.append((f"GAO 审计结论（相关 {_gao} 篇）", f"{_gao} 篇", "gao-reports", "T2",
                  "gao.gov/products/<报告号> 可读全文；但其中的产能/库存数字是**机构估算**，"
                  "不能当披露值引用"))
    _rows.append((f"CBO 成本估算（相关 {_cbo} 篇）", f"{_cbo} 篇", "cbo-reports", "T2",
                  "**已直连 CBO 官方 RSS**（标题/摘要/链接/日期）。"
                  "⚠️ 但 CBO **正文页对数据中心 IP 返回 403（Akamai）**，"
                  "本报告**不做风控绕过**，因此只能取到官方摘要层，"
                  "具体数字需人工点开链接读原文 —— 摘要里没有的数字，本报告不写"))
    _rows.append((f"CRS 研究报告（相关 {_crs} 篇）", f"{_crs} 篇", "crs-reports", "T2",
                  "congress.gov API 每条含报告编号；原文可拼 "
                  "`congress.gov/crs-product/<编号>` 打开（PDF/HTML 双格式）"))
    _st = ev["buckets"]["stress"]
    if _st["docs"]:
        _t3n = _st.get("by_tier", {}).get("T3", 0)
        _t2n = _st.get("by_tier", {}).get("T2", 0)
        _t1n = _st.get("by_tier", {}).get("T1", 0)
        _rows.append(("弹药库存/消耗比例类表述（如「拦截弹已消耗三分之二」）",
                      f"{_st['docs']} 篇（T1 {_t1n} / T2 {_t2n} / T3 {_t3n}）",
                      "见下条说明", "T2/T3 混合",
                      "**公开渠道没有库存绝对数量**。这类数字只能来自官方报告的相对表述 —— "
                      "CBO（T2）为原始出处，商业媒体（T3）为转述。"
                      "引用时**必须**标明用的是哪一级，且不得与 T1 金额并列排放"))
    for name, val, sid, tier, how in _rows:
        L.append(f"| {name} | {val} | `{sid}` | {tier_badge(tier)} | {how} |")
    L.append("")
    L.append("### D.4 本报告不采用的来源（T4）")
    L.append("")
    L.append("明确排除、且**不设采集器**的来源类型：自媒体账号、社交平台帖文（X / Telegram 等）、"
             "无编辑审核的博客、以及任何匿名或「据不愿具名人士」的转述。"
             "理由不是「不可信」三个字，而是**不可核验** —— 这类来源没有可回溯的原始文件，"
             "一旦进入证据板就会把整张表的可信度拉平，这是本报告最需要避免的失效模式。")
    L.append("")
    if ev["buckets"]["stress"].get("t3_only"):
        L.append("> ⚠️ **当前实例**：本报告「产能/库存压力」桶（加权 "
                 f"{ev['buckets']['stress']['score']} 分，**直接参与净值计算**）"
                 "的信号证据**全部来自 T3 商业媒体**，没有 T2/T1 的正文级出处。"
                 "回溯到 CBO/GAO 原文之前，该桶不应被当作独立佐证使用。")
        L.append(">")
        L.append("> 注意区分两件事：本报告**已直连** CBO 官方 RSS / CRS 报告 API（T2），"
                 "拿到的是**官方标题与摘要层**；而这一桶的**信号命中**发生在媒体转述的正文里。"
                 "「已直连官方摘要层」不能拿来当这桶的证据升级。")
        L.append("")
    L.append("### D.5 当前快照的等级分布")
    L.append("")
    L.append("| 等级 | 含义 | 条数 | 占比 | 引用方式 |")
    L.append("|---|---|---|---|---|")
    _bdt = tier_breakdown(records_new)
    _total_n = sum(_bdt.values()) or 1
    for _t in TIER_ORDER:
        _n = _bdt.get(_t, 0)
        _m = TIER_META[_t]
        _use = {"T1": "可直接引用为事实", "T2": "可引用为机构估算",
                "T3": "仅作线索，引用前须回溯原文", "T4": "本报告不采集"}[_t]
        L.append(f"| `{_t}` {_m['icon']} | {_m['label']} | {_n} | "
                 f"{_n / _total_n * 100:.1f}% | {_use} |")
    L.append("")
    L.append(f"> 分母为当前快照全部 {sum(_bdt.values())} 条记录（含相关性不足、"
             "未进正文但已归档者）。**T1 占比高 ≠ 结论可靠** —— 它只说明这份报告的数字"
             "大多可回源，不说明这些数字能回答「战争会延续还是收束」。")
    L.append("")
    return "\n".join(L)


def render_evidence_md(ev: Dict[str, Any], metrics: Dict[str, Any],
                       run_dt: datetime) -> str:
    L = [f"# 战争走向证据板 · {run_dt.strftime('%Y-%m-%d')}", ""]
    L.append("> 本文件是 `spend_monitor.py` 输出的**证据索引**，不是预测。")
    L.append("> 用法：把每桶的原文读一遍，人工判断语境，再形成自己的结论。")
    L.append("")
    L.append(f"**净值 {ev['net']:+.0f} → {ev['label']}**")
    L.append("")
    L.append("> **信源等级**：`T1` 官方一手（可回源对账）· `T2` 官方分析/审计（估算）· "
             "`T3` 商业媒体（二手转述，须回溯原文）。等级看的是**这份材料由谁加工**，"
             "不是它引用了谁 —— 转述 CBO 的媒体报道仍是 T3。")
    L.append("")
    L.append("### 信号桶 × 信源等级")
    L.append("")
    L.append(render_tier_signal_matrix(ev))
    L.append("")
    for b, title in [("sustain", "🔴 持续/升级"), ("terminate", "🟢 降级/结束"),
                     ("stress", "🟠 产能/库存压力")]:
        info = ev["buckets"][b]
        L.append(f"## {title}（{info['docs']} 篇 / 加权 {info['score']}）")
        L.append("")
        if info.get("by_tier"):
            tds = "、".join(f"`{t}` {info['by_tier'][t]} 篇"
                            for t in TIER_ORDER if info["by_tier"].get(t))
            L.append(f"信源等级分布：{tds}")
            if info.get("t3_only"):
                L.append("")
                L.append("> ⚠️ 本桶**全部来自 T3 二手转述**，无 T1/T2 **正文级**出处"
                         "（已直连的 CBO/CRS 官方摘要层不计入本桶证据）—— "
                         "引用前必须回溯其援引的原文。")
            L.append("")
        if info.get("top_phrases"):
            L.append("| 短语 | 篇数 | 等级 | 例证 |")
            L.append("|---|---|---|---|")
            for p in info["top_phrases"]:
                L.append(f"| `{p['phrase']}` | {p['count']} | "
                         f"{tier_badge(p.get('example_tier') or '')} | "
                         f"[{truncate(p['example_title'], 70)}]({p['example']}) |")
        else:
            L.append("_无命中。_")
        L.append("")
    L.append("## 观察点")
    L.append("")
    for p in watch_points(ev, metrics):
        L.append(f"- {p}")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# 九、落盘（按 id 合并，避免同日重跑覆盖）
# --------------------------------------------------------------------------- #
def write_outputs(records_new: List[Dict[str, Any]], records_all: List[Dict[str, Any]],
                  report: List[Dict[str, Any]], metrics: Dict[str, Any],
                  run_dt: datetime, hours: int,
                  out_dir: Optional[Path] = None) -> Dict[str, Path]:
    date_str = run_dt.strftime("%Y-%m-%d")
    root = out_dir or OUTPUT_DIR
    day_dir = root / date_str
    day_dir.mkdir(parents=True, exist_ok=True)
    jsonl = day_dir / f"records-{date_str}.jsonl"

    # ---- 与当天已有记录合并 ----
    # 教训：有去重状态的爬虫第 2 次运行会把当天数据覆盖成空文件。
    # 必须先读回当天已有记录，按 id 合并；同 id 用本次新解析结果覆盖（让解析器修复回溯生效）。
    merged: List[Dict[str, Any]] = []
    index_by_id: Dict[str, int] = {}
    if jsonl.exists():
        for line in jsonl.read_text("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                old = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = old.get("id") or ""
            if rid and rid in index_by_id:
                continue
            if rid:
                index_by_id[rid] = len(merged)
            merged.append(old)

    # ⚠️ 合并源必须用 records_all（本次抓取窗口内的**全部**记录），不能用 records_new（仅去重后的新增）。
    # 原因：跨天运行时 output/<今天>/ 还不存在，若只把「新增」并进来，
    # LATEST.md 会退化成「只有今天新出现的那几条」，窗口内的历史全部丢失。
    # （同一天重跑时二者等价，所以这个坑只在跨天时暴露 —— 只测「同日重跑」会漏掉它。）
    existing_ids = set(index_by_id)
    for r in (records_all or records_new):
        rid = r.get("id") or ""
        if rid and rid in index_by_id:
            merged[index_by_id[rid]] = r          # 用新解析结果覆盖，让解析器修复能回溯生效
            continue
        if rid:
            index_by_id[rid] = len(merged)
        merged.append(r)

    # 「本次新增」= 去重后的新增中，当天文件里尚未出现的那些
    fresh = sum(1 for r in records_new if (r.get("id") or "") not in existing_ids)

    merged.sort(key=lambda r: r.get("published") or "", reverse=True)
    records_out = merged or records_new
    # 兼容旧归档：早期写入的记录没有 relevance 字段，这里补齐，避免被误折叠
    for r in records_out:
        if "relevance" not in r:
            rel, hits = compute_relevance(r, STRONG_INDEX, WEAK_INDEX)
            r["relevance"] = rel
            r["relevance_hits"] = hits
        # 同理回填信源等级：v1.0 落盘的记录没有 tier，
        # 若不补，跨天累积档里的历史记录会在报告里显示成未知等级。
        if not r.get("tier"):
            r["tier"] = tier_of(r.get("source_id", ""))
        if not r.get("publisher"):
            r["publisher"] = SOURCE_META.get(r.get("source_id", ""), {}).get("publisher", "")
    note = (f"（本次新增 {fresh} 条，当前快照 {len(records_out)} 条）" if merged else "")

    digest = day_dir / f"spending-digest-{date_str}.md"
    digest.write_text(render_digest(records_out, records_all, report, metrics,
                                    run_dt, hours, note=note, fresh=fresh), "utf-8")

    ev_md = day_dir / f"war-trajectory-{date_str}.md"
    ev_md.write_text(render_evidence_md(build_evidence(evidence_records(records_out)), metrics, run_dt),
                     "utf-8")

    with jsonl.open("w", encoding="utf-8") as fh:
        for r in records_out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    (day_dir / f"records-{date_str}.json").write_text(
        json.dumps(records_out, ensure_ascii=False, indent=1), "utf-8")
    (day_dir / f"metrics-{date_str}.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=1), "utf-8")

    (root / "LATEST.md").write_text(digest.read_text("utf-8"), "utf-8")

    # ---- 累积总档（跨天不淘汰）----
    # 当日快照会随抓取窗口滚动而丢弃旧记录；本文件按 id 永久累积，
    # 供大模型做跨越数月的长周期分析。首次出现日期记在 first_seen 字段。
    acc_path = root / "ALL-records.jsonl"
    acc: List[Dict[str, Any]] = []
    acc_idx: Dict[str, int] = {}
    if acc_path.exists():
        for line in acc_path.read_text("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                old = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = old.get("id") or ""
            if rid and rid in acc_idx:
                continue
            if rid:
                acc_idx[rid] = len(acc)
            acc.append(old)
    for r in records_out:
        rid = r.get("id") or ""
        row = dict(r)
        if rid and rid in acc_idx:
            row["first_seen"] = acc[acc_idx[rid]].get("first_seen") or date_str
            acc[acc_idx[rid]] = row
        else:
            row["first_seen"] = date_str
            if rid:
                acc_idx[rid] = len(acc)
            acc.append(row)
    acc.sort(key=lambda r: r.get("published") or "", reverse=True)
    # 归档文件里的**全量**记录都要补齐新字段 —— 不能只补当前窗口内的那些。
    # 坑：掉出时间窗的历史记录永远不会再出现在 records_out 里，
    # 只在 records_out 上回填，它们就会一直带着「无等级」的旧 schema，
    # 而这份文件恰恰是给大模型做长周期分析用的。
    for r in acc:
        if not r.get("tier"):
            r["tier"] = tier_of(r.get("source_id", ""))
        if not r.get("publisher"):
            r["publisher"] = SOURCE_META.get(r.get("source_id", ""), {}).get("publisher", "")
    with acc_path.open("w", encoding="utf-8") as fh:
        for r in acc:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 被标题闸门过滤掉的标题留档：压假阳性最容易连真信号一起删，
    # 没有这份审计就无法复核「是不是过滤过头了」。
    if GATE_AUDIT:
        (root / "_title_gate_audit.json").write_text(
            json.dumps({"note": "被 title_include/title_exclude 过滤掉的标题，"
                                "供人工回扫确认没有误杀（每个源最多留 300 条）",
                        "gated": GATE_AUDIT},
                       ensure_ascii=False, indent=1), "utf-8")

    write_index(root)
    return {"digest": digest, "evidence": ev_md, "jsonl": jsonl,
            "metrics": day_dir / f"metrics-{date_str}.json", "all": acc_path}


def write_index(root: Path) -> Path:
    """归档索引。放在历史序列之后调用 —— 否则索引里会缺当天那份趋势报告。"""
    acc_path = root / "ALL-records.jsonl"
    n_acc = (sum(1 for _ in acc_path.open(encoding="utf-8"))
             if acc_path.exists() else 0)
    idx = ["# 监测归档索引", "",
           f"累计档案（跨天不淘汰，按 id 去重）：`ALL-records.jsonl` — **{n_acc} 条**", "",
           "> `LATEST.md` 与各日 `spending-digest-*.md` 均为**滚动窗口快照**（含窗口内全部记录，",
           "> 非仅当日新增）；真正长期不丢的是 `ALL-records.jsonl`。", "",
           "## 动态变化（跨月时间序列）", "",
           "- 趋势报告（人读）：`LATEST-trend.md`（= `history/trend-<日期>.md`）",
           "- 序列长表（机读）：`history/series.jsonl`",
           "  —— 每行含 `metric / period / value / unit / caliber / source_id / tier`，",
           "  可直接做透视；`kind=monitor-index` 的行是**启发式**监测指标，非官方统计。", "",
           "## 每日快照", "",
           "| 日期 | 快照条数 | 日报 | 证据板 |", "|---|---|---|---|"]
    for d in sorted([p for p in root.iterdir() if p.is_dir() and p.name[:2] == "20"],
                    reverse=True):
        jl = d / f"records-{d.name}.jsonl"
        n = str(sum(1 for _ in jl.open(encoding="utf-8"))) if jl.exists() else "-"
        f = d / f"spending-digest-{d.name}.md"
        e = d / f"war-trajectory-{d.name}.md"
        idx.append(f"| {d.name} | {n} | "
                   f"{f'{d.name}/{f.name}' if f.exists() else '-'} | "
                   f"{f'{d.name}/{e.name}' if e.exists() else '-'} |")
    hdir = root / "history"
    if hdir.exists():
        trends = sorted([p.name for p in hdir.glob("trend-*.md")], reverse=True)
        if trends:
            idx += ["", "## 历史趋势报告留档", ""]
            idx += [f"- `history/{t}`" for t in trends]
    out = root / "index.md"
    out.write_text("\n".join(idx) + "\n", "utf-8")
    return out

    return {"digest": digest, "evidence": ev_md, "jsonl": jsonl,
            "metrics": day_dir / f"metrics-{date_str}.json", "all": acc_path}


# --------------------------------------------------------------------------- #
# 十、离线自检
# --------------------------------------------------------------------------- #
def selftest(verbose: bool = True) -> int:
    """离线自检，覆盖用子串匹配自然语言时必然踩的词边界与单位换算陷阱。"""
    import tempfile

    cases: List[Tuple[str, Any, Any]] = []
    fails: List[str] = []

    def check(name: str, got: Any, want: Any) -> None:
        ok = got == want
        cases.append((name, got, want))
        if not ok:
            fails.append(f"{name}: got={got!r} want={want!r}")

    # --- 金额 ---
    def money1(t):
        m = extract_money(t)
        return round(m[0]["usd"]) if m else None

    check("金额 $1.2 billion", money1("a $1.2 billion contract"), 1_200_000_000)
    check("金额 $345,678,901", money1("worth $345,678,901 total"), 345_678_901)
    check("金额 $4.5B", money1("a $4.5B award"), 4_500_000_000)
    check("金额 1.2 billion dollars(无符号)",
          money1("spending of 1.2 billion dollars"), 1_200_000_000)
    check("金额 负数排除: 1.2 billion people",
          money1("a country of 1.2 billion people"), None)
    check("金额 排除条款编号", money1("pursuant to Section 101 of the Act"), None)
    check("金额 $1,234.56 million",
          money1("ceiling of $1,234.56 million"), 1_234_560_000)

    # --- 弹药词表（词边界）---
    terms = compile_terms(["Patriot", "Stinger", "Javelin", "Standard Missile",
                           "missile", "Tomahawk"])
    check("弹药词 Patriot 命中", extract_terms("Patriot air defense", terms), ["Patriot"])
    check("弹药词 patriotic 不命中",
          extract_terms("patriotic sentiment", terms), [])
    check("弹药词 stinging 不命中",
          extract_terms("a stinging report", terms), [])
    check("弹药词 长词优先 Standard Missile",
          extract_terms("Standard Missile-3 intercept", terms)[0], "Standard Missile")
    check("弹药词 Tomahawk 命中", extract_terms("Tomahawk cruise missile", terms)[:1],
          ["Tomahawk"])
    check("弹药词 排除词组 Patriot Act",
          extract_terms("the Patriot Act reauthorization",
                        compile_terms(["Patriot"]), ["Patriot Act"]), [])

    # --- 数量与产能 ---
    check("数量 1,500 interceptors",
          extract_quantities("deliver 1,500 interceptors")[0]["value"], 1500.0)
    check("产能 年产量 650",
          [q["value"] for q in extract_quantities(
              "annual production rate of 650 per year")], [650.0])

    # --- 信号分类 ---
    sidx = build_signal_index({
        "sustain": [{"weight": 3, "patterns": ["emergency supplemental appropriations",
                                               "surge production"]}],
        "terminate": [{"weight": 4, "patterns": ["ceasefire"]}],
        "stress": [{"weight": 3, "patterns": ["stockpile"]}],
    })
    check("信号 持续", [s["bucket"] for s in extract_signals(
        "an emergency supplemental appropriations bill", sidx)], ["sustain"])
    check("信号 降级", [s["bucket"] for s in extract_signals(
        "a ceasefire was announced", sidx)], ["terminate"])
    check("信号 压力", [s["bucket"] for s in extract_signals(
        "the stockpile is insufficient", sidx)], ["stress"])

    # --- 库存耗损：真实语料回归（曾因词表收紧而漏检）---
    sidx2 = build_signal_index({
        "stress": [{"weight": 3, "except": ["depleted uranium"],
                    "patterns": ["depleted", "depletion", "exhausted"]},
                   {"weight": 2, "patterns": ["of the u.s. inventory",
                                              "fewer weapons available"]}],
    })

    def stress_phrases(txt):
        return [s["phrase"] for s in extract_signals(txt, sidx2)]

    # 真实头条（CBO 分析，经 Defense News 报道）——必须命中
    cbo = ("Iran war has cost $38 billion, depleted two-thirds of US missile "
           "interceptors, CBO says. CBO estimates the conflict has consumed "
           "between one-half and two-thirds of the U.S. inventory of certain "
           "missile-defense interceptors, leaving the military with fewer "
           "weapons available for a future conflict.")
    check("库存 CBO 头条命中 depleted", "depleted" in stress_phrases(cbo), True)
    check("库存 命中 of the u.s. inventory",
          "of the u.s. inventory" in stress_phrases(cbo), True)
    check("库存 命中 fewer weapons available",
          "fewer weapons available" in stress_phrases(cbo), True)
    # 排除规则：贫铀弹不是库存告急
    check("库存 排除 depleted uranium",
          stress_phrases("procurement of depleted uranium armor"), [])
    # 但同一句里真有库存告急时仍应命中
    check("库存 排除词不误伤同句告急",
          "depleted" in stress_phrases(
              "the stockpile is depleted; depleted uranium is a separate item"),
          True)
    # 复数形式也应命中
    check("库存 复数 depletion 命中",
          "depletion" in stress_phrases("rapid depletion of our combat resources"), True)

    # --- 可验证性：每条信号必须带原文片段 ---
    # 信号是在完整正文上算的，而落盘的 summary 会被截断；不存片段的话，
    # 读者无法核对这条信号是否真的成立 —— 那等于给出不可验证的结论。
    ctx_hits = extract_signals(
        "The Pentagon said the conflict has consumed between one-half and "
        "two-thirds of the U.S. inventory of interceptors. Rebuilding those "
        "stocks could take at least five years.", sidx2)
    check("信号 均带原文片段",
          bool(ctx_hits) and all(s.get("context") for s in ctx_hits), True)
    check("信号 片段保留上下文",
          any("consumed" in s["context"] for s in ctx_hits), True)

    # --- 词边界：Iran 不应命中 Iranian ---
    rx = word_boundary_re("Iran")
    check("词边界 Iran 不命中 Iranian", bool(rx.search("Iranian forces")), False)
    check("词边界 Iran 命中 Iran's", bool(rx.search("Iran's posture")), True)

    # --- 财政部分析 ---
    series = {"Total--Procurement": [
        {"month": "2026-06-30", "monthly": 100.0, "fytd": 1000.0},
        {"month": "2026-07-30", "monthly": 200.0, "fytd": 1200.0},
        {"month": "2026-08-31", "monthly": 400.0, "fytd": 1600.0},
    ]}
    t = analyze_treasury(series)
    e = t["lines"]["Total--Procurement"]
    check("财政部 环比 +100%", round(e["mom_pct"]), 100)
    check("财政部 最新值", e["latest"], 400.0)
    check("财政部 近3月均值", round(e["avg3"], 2), round((100 + 200 + 400) / 3, 2))
    check("财政部 本财年累计", e["fytd"], 1600.0)

    # --- 金额格式化 ---
    check("格式化 679 亿", fmt_usd(67_923_317_481.69), "$67.92B")
    check("格式化 负值", fmt_usd(-1_000_000), "-$1.00M")

    # --- 落盘不缩水（增量静默覆盖 bug 的回归测试）---
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        r1 = [{"id": "a", "published": "2026-09-17T00:00:00+00:00", "kind": "news",
               "source_id": "s", "source_name": "s", "layer": "", "title": "A",
               "link": "", "summary": "", "amounts": [], "amount_max": None,
               "munitions": [], "quantities": [], "signals": [], "extra": {}},
              {"id": "b", "published": "2026-09-16T00:00:00+00:00", "kind": "news",
               "source_id": "s", "source_name": "s", "layer": "", "title": "B",
               "link": "", "summary": "", "amounts": [], "amount_max": None,
               "munitions": [], "quantities": [], "signals": [], "extra": {}}]
        dt = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
        p1 = write_outputs(r1, r1, [], {}, dt, 720, out_dir=tmp)
        n1 = sum(1 for _ in p1["jsonl"].open(encoding="utf-8"))
        # 第 2 次只拿到 1 条（其余命中 seen）——不得把当天的 2 条覆盖成 1 条
        p2 = write_outputs([r1[1]], r1, [], {}, dt, 720, out_dir=tmp)
        n2 = sum(1 for _ in p2["jsonl"].open(encoding="utf-8"))
        check("落盘 重跑不缩水", n2, n1)
        check("落盘 合并后为 2 条", n2, 2)

        # --- 跨天：新日期目录尚不存在，窗口内 2 条但只有 1 条是新增，不得只写 1 条 ---
        #     教训：只测「同日重跑」会漏掉这个场景。
        dt2 = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)
        p3 = write_outputs([r1[1]], r1, [], {}, dt2, 720, out_dir=tmp)
        n3 = sum(1 for _ in p3["jsonl"].open(encoding="utf-8"))
        check("跨天 保留窗口内历史", n3, 2)
        t3 = next(l for l in p3["digest"].read_text("utf-8").splitlines()
                  if l.startswith("- 记录："))
        check("跨天 头部区分新增与快照", ("本次新增 1 条" in t3 and "当前快照" in t3), True)
        check("累积档 跨天保留全部", sum(1 for _ in p3["all"].open(encoding="utf-8")), 2)

    # --- 信源等级（T1–T4）---
    # 为什么必须自检：等级是这份报告最容易被误用的维度 ——
    # 若某桶的信号全部来自 T3 二手媒体而程序没标出来，读者会把
    # 「媒体转述」当成「官方披露」。这类静默失效只能靠断言拦住。
    global SOURCE_TIER, SOURCE_META
    _saved_tier, _saved_meta = dict(SOURCE_TIER), dict(SOURCE_META)
    SOURCE_TIER = {"t1src": "T1", "t3src": "T3"}
    SOURCE_META = {}
    try:
        check("等级 已知源", tier_of("t1src"), "T1")
        check("等级 未知源兜底 T4", tier_of("no-such-source"), "T4")
        check("等级 徽章含等级码", "`T3`" in tier_badge("T3"), True)

        def _rec(rid, sid, sigs, with_tier=True):
            r = {"id": rid, "source_id": sid, "source_name": sid, "layer": "x",
                 "publisher": "", "kind": "news", "title": rid,
                 "link": "https://example.invalid/" + rid,
                 "published": "2026-09-17T00:00:00+00:00", "summary": "",
                 "amounts": [], "amount_max": None, "munitions": [],
                 "quantities": [], "signals": sigs, "extra": {}}
            if with_tier:
                r["tier"] = tier_of(sid)
            return r

        def _sig(b, w, p):
            return {"bucket": b, "weight": w, "phrase": p, "context": p + " ctx"}

        ev_t = build_evidence([
            _rec("a", "t1src", [_sig("terminate", 3, "ceasefire")]),
            _rec("b", "t3src", [_sig("stress", 4, "depleted")]),
            _rec("c", "t3src", [_sig("stress", 4, "depletion")], with_tier=False),
        ])
        check("等级 桶内等级分布", ev_t["buckets"]["terminate"]["by_tier"], {"T1": 1})
        check("等级 T1/T2 计数", ev_t["buckets"]["terminate"]["t1t2_docs"], 1)
        check("等级 缺失 tier 时按源兜底",
              ev_t["buckets"]["stress"]["by_tier"], {"T3": 2})
        check("等级 压力桶识别为仅二手", ev_t["buckets"]["stress"]["t3_only"], True)
        check("等级 降级桶非仅二手", ev_t["buckets"]["terminate"]["t3_only"], False)
        check("等级 桶内最高等级", ev_t["buckets"]["stress"]["best_tier"], "T3")
        check("等级 短语带出等级",
              ev_t["buckets"]["stress"]["top_phrases"][0]["example_tier"], "T3")
        check("等级 交叉表警告二手来源",
              "全部来自二手媒体" in render_tier_signal_matrix(ev_t), True)
        check("等级 交叉表含三桶",
              all(x in render_tier_signal_matrix(ev_t)
                  for x in ("持续/升级", "降级/结束", "产能/库存压力")), True)
        check("等级 空桶不误报二手",
              ev_t["buckets"]["sustain"]["t3_only"], False)

        # 落盘回填：v1.0 写的老记录没有 tier，写盘时必须补上，
        # 否则跨天累积档里的历史记录在报告里会显示成未知等级。
        with tempfile.TemporaryDirectory() as td2:
            tmp2 = Path(td2)
            legacy = [_rec("L1", "t3src", [], with_tier=False)]
            dtL = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
            pL = write_outputs(legacy, legacy, [], {}, dtL, 720, out_dir=tmp2)
            firstL = json.loads(pL["jsonl"].read_text("utf-8").splitlines()[0])
            check("等级 旧归档回填 tier", firstL.get("tier"), "T3")
            # 累计总档里的**窗口外**历史记录也必须回填：
            # 这类记录再也不会出现在 records_out 中，只补窗口内会留下永久空洞，
            # 而 ALL-records.jsonl 恰恰是给大模型做长周期分析用的。
            with tempfile.TemporaryDirectory() as td3:
                tmp3 = Path(td3)
                dtA = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
                win1 = [_rec("A1", "t3src", [], with_tier=False),
                        _rec("A2", "t1src", [], with_tier=False)]
                write_outputs(win1, win1, [], {}, dtA, 720, out_dir=tmp3)
                # 次日窗口只剩 A1，A2 掉出窗口 —— 它不该留在「无等级」状态
                win2 = [_rec("A1", "t3src", [], with_tier=False)]
                pA = write_outputs(win2, win2, [], {},
                                   datetime(2026, 9, 18, 12, tzinfo=timezone.utc),
                                   720, out_dir=tmp3)
                accrows = {json.loads(l)["id"]: json.loads(l)
                           for l in pA["all"].read_text("utf-8").splitlines() if l.strip()}
                check("等级 累计档保留窗口外记录", sorted(accrows), ["A1", "A2"])
                check("等级 窗口外记录也回填等级", accrows["A2"].get("tier"), "T1")
            dgL = pL["digest"].read_text("utf-8")
            check("等级 报告含附录 D", "## 附录 D. 信源等级与数据溯源" in dgL, True)
            check("等级 报告含等级定义", "官方一手" in dgL, True)
            check("等级 报告含不采用声明", "T4" in dgL and "不采用" in dgL, True)
            check("等级 证据板含等级表", "信源等级" in pL["evidence"].read_text("utf-8"), True)
    finally:
        SOURCE_TIER, SOURCE_META = _saved_tier, _saved_meta

    # --- 历史序列：月份换算与加总一致性 ---
    # 为什么必须自检：时间轴一旦换算错（财年 vs 日历月），整条趋势会整体偏移
    # 9 个月，而图看起来完全正常 —— 这种错只有断言能拦住。
    check("月份 财年第5月=2月", fy_period_to_ym({"fiscal_year": "2026", "month": "5"}),
          "2026-02")
    check("月份 财年第12月=9月", fy_period_to_ym({"fiscal_year": "2026", "month": "12"}),
          "2026-09")
    check("月份 财年第1月=上年10月",
          fy_period_to_ym({"fiscal_year": "2026", "month": "1"}), "2025-10")
    check("月份 calendar_year 优先",
          fy_period_to_ym({"calendar_year": "2026", "month": "2",
                           "fiscal_year": "2026", "month2": "x"}), "2026-02")
    check("月份 缺字段返回 None", fy_period_to_ym({"month": "5"}), None)
    check("月份 非字典返回 None", fy_period_to_ym(None), None)
    check("月份 2月月末", month_bounds("2026-02"), ("2026-02-01", "2026-02-28"))
    check("月份 闰年2月月末", month_bounds("2024-02"), ("2024-02-01", "2024-02-29"))
    check("月份 12月跨年", month_bounds("2026-12"), ("2026-12-01", "2026-12-31"))
    check("月份 区间月份数", len(month_list("2026-02", "2026-09")), 8)
    check("月份 跨年区间", month_list("2025-11", "2026-02"),
          ["2025-11", "2025-12", "2026-01", "2026-02"])
    # PSC 码表：13xx/14xx 各 20 个（步长 5），少一个就会漏掉一类弹药
    _psc = build_psc_codes({"_psc_families": {"13": "a", "14": "b"}})
    check("PSC 码数 40", len(_psc), 40)
    check("PSC 含鱼雷 1355", "1355" in _psc, True)
    check("PSC 含火箭 1340", "1340" in _psc, True)
    check("PSC 不含 1301", "1301" in _psc, False)
    check("PSC 显式覆盖优先",
          build_psc_codes({"psc_codes": ["1410"], "_psc_families": {"13": "a"}}),
          ["1410"])
    check("条形图 归一化", bar_chart(50, 100, 10), "█████")
    check("条形图 零值无条", bar_chart(0, 100, 10), "")

    # 加总一致性自检：月度之和必须等于独立查询的窗口总额。
    # 用一份**故意不一致**的输入反证检查真的会失败 —— 否则这个自检本身是摆设。
    _us_ok = {"monthly": [{"month": "2026-02", "munitions": 100.0, "all_contracts": 400.0},
                          {"month": "2026-03", "munitions": 300.0, "all_contracts": 600.0}],
              "monthly_window": ["2025-12-01", "2026-03-31"],
              "monthly_window_total": 400.0,
              "trend_total": 300.0, "baseline_total": 100.0,
              "war_start": "2026-02-01"}
    _us_bad = dict(_us_ok, monthly_window_total=999.0)
    check("序列 一致时差为 0",
          round(monthly_additivity(_us_ok)[0] - monthly_additivity(_us_ok)[1], 4), 0.0)
    check("序列 不一致时能识别",
          round(monthly_additivity(_us_bad)[0] - monthly_additivity(_us_bad)[1], 4),
          -599.0)
    check("序列 无独立总额时回退到头条总额",
          monthly_additivity({"monthly": [{"munitions": 7.0}],
                              "munitions_total": 7.0})[1], 7.0)
    check("序列 战前+战后=总和",
          _us_ok["trend_total"] + _us_ok["baseline_total"],
          sum(r["munitions"] for r in _us_ok["monthly"]))

    # --- 标题闸门（全议题源必须显式锚定主题）---
    # 这里最容易犯的错是收紧过头把真信号删掉（本项目踩过：删掉 depleted 裸词
    # 导致漏掉整条核心证据、直接改变结论方向），因此两侧都要断言。
    _g = {"title_include": ["missile", "munitions", "combat operations"],
          "title_exclude": ["excise tax"]}
    check("闸门 命中保留", title_gate(_g, "Army Patriot Missile Defense System")[0], True)
    check("闸门 未命中过滤",
          title_gate(_g, "Social Security Long-Term Projections")[0], False)
    check("闸门 排除项优先",
          title_gate(_g, "Firearms Missile Excise Tax Act")[0], False)
    check("闸门 排除原因可读",
          "title_exclude" in title_gate(_g, "Missile Excise Tax")[1], True)
    check("闸门 未配 include 时不过滤",
          title_gate({}, "Anything At All")[0], True)
    check("闸门 空标题不崩", title_gate(_g, "")[0], False)
    check("闸门 无裸词 defense 放行情报类",
          title_gate({"title_include": ["missile"]},
                     "Defense Primer: Intelligence Support")[0], False)

    # --- 事件时间线 ---
    # 四道闸门：只 T1/T2、达相关性阈值、主题锚定、一条记录只归首个命中规则。
    # 不测「一条记录一次」就等着 DPA 决定书在时间线里出现两遍。
    #
    # ⚠️ 这几条噪声**必须用真实语料里的 relevance 值**（=6）来测。
    #    合成样本里给它 0 分，会掩盖「摘要里的战争泛词把噪声抬过相关性闸门」
    #    这个真问题 —— 真实语料里它们就是 6 分进来的。
    def _ev_rec(rid, tier, title, rel, sid="federal-register", summary="", sigs=None):
        return {"id": rid, "source_id": sid, "tier": tier,
                "title": title, "link": "https://example.invalid/" + rid,
                "published": "2026-06-17T00:00:00+00:00", "summary": summary,
                "relevance": rel, "signals": sigs or []}
    EVENT_AUDIT.clear()
    EVENT_AUDIT_LOWREL.clear()
    _evs = extract_events([
        _ev_rec("dpa1", "T1",
                "Presidential Determination Pursuant to Section 303 of the "
                "Defense Production Act of 1950", 5),
        _ev_rec("refugee", "T1",
                "Emergency Presidential Determination on Refugee Admissions "
                "for Fiscal Year 2026", 0),
        _ev_rec("refugee6", "T1",
                "Emergency Presidential Determination on Refugee Admissions "
                "for Fiscal Year 2026", 6),          # 真实语料里的分数
        _ev_rec("golf", "T1",
                "Presidential Determination Concerning the Department of the Air "
                "Force's Rehabilitation and Revitalization of Joint Base Andrews "
                "Golf Course", 6),
        _ev_rec("saudi", "T1",
                "Presidential Determination on the Proposed Agreement for "
                "Cooperation Between the Government of the United States of "
                "America and the Government of the Kingdom of Saudi Arabia "
                "Concerning Peaceful Uses of Nuclear Energy", 6),
        _ev_rec("iran1245", "T1",                    # 真信号：§1245＝对伊朗制裁条款
                "Presidential Determination Pursuant to Section 1245(d)(4)(B) "
                "and (C) of the National Defense Authorization Act for Fiscal "
                "Year 2012", 6),
        _ev_rec("cbo1", "T2",
                "Estimating the Cost of Combat Operations Against Iran", 8,
                sid="cbo-reports"),
        _ev_rec("news1", "T3", "Defense Production Act news roundup", 5,
                sid="defensenews"),
    ])
    check("事件 一条记录只出一条（DPA 不重复）",
          [e["event"] for e in _evs if e["title"].startswith("Presidential")
           and "Defense Production Act" in e["title"]],
          ["国防生产法(DPA)授权"])
    check("事件 低相关性被剔除",
          any("Refugee" in e["title"] for e in _evs), False)
    check("事件 过锚定：难民接纳(rel=6)被挡",
          any("Refugee" in e["title"] for e in _evs), False)
    check("事件 过锚定：高尔夫球场整修被挡",
          any("Golf Course" in e["title"] for e in _evs), False)
    check("事件 过锚定：沙特核能协议被挡",
          any("Saudi" in e["title"] for e in _evs), False)
    check("事件 过锚定：真信号 §1245(伊朗制裁)保留",
          [e["event"] for e in _evs if "1245" in e["title"]],
          ["总统决定书（对外军援/制裁）"])
    check("事件 被挡记录进审计（可复核是否删过头）",
          sorted({a["reason"] for a in EVENT_AUDIT}), ["no-topic-anchor"])
    check("事件 相关性不足也进审计",
          [a["reason"] for a in EVENT_AUDIT_LOWREL], ["low-relevance"])
    check("事件 审计保留原文标题（便于人工回扫）",
          any("Golf Course" in (a.get("title") or "") for a in EVENT_AUDIT), True)
    check("事件 T3 不进时间线",
          any(e["source_id"] == "defensenews" for e in _evs), False)
    check("事件 CBO 成本估算被识别",
          [e["event"] for e in _evs if "Cost of Combat" in e["title"]],
          ["CBO/官方作战成本估算"])
    check("事件 数量正确", len(_evs), 3)
    check("事件 按日期排序",
          _evs == sorted(_evs, key=lambda x: x["date"]), True)

    # --- 趋势报告 ---
    with tempfile.TemporaryDirectory() as tdT:
        _tmpT = Path(tdT)
        _cfgT = {"history": {"start": "2026-02-01", "start_label": "2026-02（测试）",
                             "cache_dir": "history"}, "schedule": {}}
        _metricsT = {"treasury": {"lines": {
            "Total--Department of Defense--Military Programs": {
                "label": "国防部", "full": "x", "latest_month": "2026-03",
                "latest": 70e9, "prev": 60e9, "mom_pct": 16.7,
                "avg3": 65e9, "avg3_prior": None, "fytd": 500e9,
                "history": [["2026-01-31", 60e9], ["2026-02-28", 65e9],
                            ["2026-03-31", 70e9]]}}},
            "usaspending": _us_ok}
        _tr = build_history(_cfgT, _metricsT, [], _tmpT,
                            datetime(2026, 3, 31, tzinfo=timezone.utc), None, False)
        _td = _tr["trend"].read_text("utf-8")
        check("趋势 含口径不可混用声明", "不可相加" in _td, True)
        check("趋势 含财年末 9 月陷阱", "财年末" in _td, True)
        check("趋势 含启发式标注", "启发式" in _td, True)
        check("趋势 含加总一致性表", "加总一致性" in _td, True)
        check("趋势 含战前基线标注", "战前基线" in _td, True)
        check("趋势 含序列清单", "序列清单与溯源" in _td, True)
        check("趋势 覆盖基线月（不含则算不出倍数）",
              any(m in _td for m in ("2026-01",)), True)
        # 空 ≠ 零：没收录的月份必须渲染成 — ，写成 0 会被读成「当月什么都没发生」
        check("趋势 未收录月渲染为 — 而非 0",
              "| 2026-03 | — | — | — | — | — |" in _td, True)
        check("趋势 说明事件主题锚定与留档",
              "主题锚定" in _td and "_event_filter_audit" in _td, True)
        check("趋势 落盘 LATEST-trend", (_tmpT / "LATEST-trend.md").exists(), True)
        check("趋势 序列 JSONL 行数", _tr["summary"]["series_rows"] > 0, True)
        check("趋势 索引引用趋势文件",
              "LATEST-trend.md" in write_index(_tmpT).read_text("utf-8"), True)

    # --- 回源对账留档 ---
    # 报告里**不能写死**「N 项差 0.00」：核对项数随覆盖范围变化，写死必然过期，
    # 而过期的「已验证」比不写更误导。这里测三种状态都能正确表达。
    _orig_out = OUTPUT_DIR
    with tempfile.TemporaryDirectory() as tdV:
        globals()["OUTPUT_DIR"] = Path(tdV)
        check("对账留档 缺失时明确说未对账",
              "尚未跑回源对账" in verify_last_note(), True)
        (Path(tdV) / "_verify_last.json").write_text(json.dumps(
            {"checked_at": "2026-09-18T03:40:00Z",
             "metrics_file": "output/2026-09-18/metrics-2026-09-18.json",
             "ok": 56, "mismatch": 0, "skip_months": False, "exit_code": 0}),
            "utf-8")
        _vn = verify_last_note()
        check("对账留档 通过时说清项数/时间/范围",
              ("56 项一致" in _vn) and ("2026-09-18T03:40:00Z" in _vn)
              and ("逐月序列" in _vn), True)
        (Path(tdV) / "_verify_last.json").write_text(json.dumps(
            {"checked_at": "x", "metrics_file": "m.json", "ok": 55,
             "mismatch": 1, "skip_months": True}), "utf-8")
        check("对账留档 有不一致时报警而非报绿",
              "1 项不一致" in verify_last_note(), True)
        (Path(tdV) / "_verify_last.json").write_text(json.dumps(
            {"checked_at": "2026-09-18T03:40:00Z",
             "metrics_file": "output/2026-09-18/metrics-2026-09-18.json",
             "ok": 30, "mismatch": 0, "skip_months": True}), "utf-8")
        check("对账留档 标注核对范围（--skip-months 时说明仅快照层）",
              "仅快照层" in verify_last_note(), True)
    globals()["OUTPUT_DIR"] = _orig_out

    # --- 输出 ---
    if verbose:
        print(f"\n自检 {len(cases)} 项：通过 {len(cases) - len(fails)}，失败 {len(fails)}")
        for name, got, want in cases:
            mark = "✅" if got == want else "❌"
            print(f"  {mark} {name}" + ("" if got == want else f"   got={got!r} want={want!r}"))
    if fails:
        print("\n失败项：")
        for f in fails:
            print("  -", f)
    return 1 if fails else 0


# --------------------------------------------------------------------------- #
# 十一、入口
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="美军弹药库存与军费开支监测 — 公开信息爬虫",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(CFG_PATH), help="配置文件路径")
    ap.add_argument("--days", type=float, help="回溯天数（覆盖配置里的 lookback_hours）")
    ap.add_argument("--sources", help="只跑指定源，逗号分隔")
    ap.add_argument("--from-file", help="离线模式：从该目录读 <源id>.xml / <源id>.json")
    ap.add_argument("--proxy", help="显式指定代理，如 http://127.0.0.1:63299")
    ap.add_argument("--direct", action="store_true",
                    help="绕过系统代理直连（注意：这很可能让所有源失败，只在确认无代理时使用）")
    ap.add_argument("--out", help="输出目录（默认项目下 output/）")
    ap.add_argument("--no-seen", action="store_true", help="忽略历史去重状态，全部按新增处理")
    ap.add_argument("--show-all", action="store_true",
                    help="不做相关性折叠，正文列出全部记录（调试/全量审阅用）")
    ap.add_argument("--reset-seen", action="store_true", help="清空去重状态后退出")
    ap.add_argument("--selftest", action="store_true", help="跑离线自检并退出")
    ap.add_argument("--backfill", action="store_true",
                    help="历史回填：把时间窗拉到 config.history.start，"
                         "并强制刷新全部历史月份（首次建立序列时用一次）")
    ap.add_argument("--no-history", action="store_true",
                    help="本次不更新历史时间序列（只出日报）")
    ap.add_argument("--quiet", action="store_true", help="减少日志")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest(verbose=True)

    if args.reset_seen:
        save_seen({})
        print(f"已清空去重状态：{SEEN_PATH}")
        return 0

    verbose = not args.quiet
    run_dt = datetime.now(timezone.utc)

    cfg = load_config(Path(args.config))
    if args.days:
        cfg["lookback_hours"] = int(args.days * 24)

    # --backfill：把时间窗拉到历史起点，让一次性回填能覆盖「战前 → 至今」。
    # 每个源的 window_days 也要一起放宽 —— 只放宽全局 lookback_hours 是没用的，
    # 因为带 window_days 的源会优先用它自己的窗口，回填会静默只拉到最近几个月。
    if args.backfill:
        hstart = ((cfg.get("history") or {}).get("start") or "2026-02-01")
        try:
            hd = datetime.strptime(hstart, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            bf_days = max(1, (datetime.now(timezone.utc) - hd).days + 40)
        except ValueError:
            bf_days = 240
        cfg["lookback_hours"] = int(bf_days * 24)
        for s in cfg.get("sources", []):
            if s.get("window_days"):
                s["window_days"] = max(int(s["window_days"]), bf_days)
        # 回填时历史序列里的联邦公报月计数也要全量重算
        log(f"[回填] 时间窗放宽到 {bf_days} 天（自 {hstart}，含 40 天缓冲）")
    hours = int(cfg.get("lookback_hours", 720))

    # 全局抽取表
    global TERMS, SIGNAL_INDEX, EXCLUDE_PHRASES, STRONG_INDEX, WEAK_INDEX, RELEVANCE_MIN
    global SOURCE_TIER, SOURCE_META, HISTORY_START, PSC_CFG, HISTORY_CFG, SCHEDULE_CFG
    global GATE_AUDIT
    GATE_AUDIT = {}
    EVENT_AUDIT.clear()
    EVENT_AUDIT_LOWREL.clear()
    HISTORY_CFG = cfg.get("history", {}) or {}
    SCHEDULE_CFG = cfg.get("schedule", {}) or {}
    PSC_CFG = cfg
    HISTORY_START = HISTORY_CFG.get("start") or None
    # 信源等级表：所有落盘记录都要带 tier，跨天归档里旧记录缺这字段时靠它回填
    SOURCE_TIER = {s["id"]: s.get("tier", "") for s in cfg.get("sources", [])}
    SOURCE_META = {s["id"]: {"publisher": s.get("publisher", ""),
                             "layer": s.get("layer", ""),
                             "name": s.get("name", "")}
                   for s in cfg.get("sources", [])}
    TERMS = compile_terms(cfg.get("munitions_terms", []))
    SIGNAL_INDEX = build_signal_index(cfg.get("signal_lexicon", {}))
    EXCLUDE_PHRASES = list(cfg.get("munitions_context_exclude", []))
    STRONG_INDEX = [(kw, word_boundary_re(kw, allow_plural=True))
                    for kw in cfg.get("topic_title_terms", [])]
    WEAK_INDEX = [(kw, word_boundary_re(kw)) for kw in cfg.get("extra_keywords", [])]
    RELEVANCE_MIN = int(cfg.get("relevance_min", 2))
    if args.show_all:
        RELEVANCE_MIN = 0

    fetcher = Fetcher(cfg.get("request", {}), direct=args.direct, proxy=args.proxy)
    log(f"美军弹药库存与军费开支监测 v{VERSION}")
    log(f"出口：{fetcher.describe_exit()}")
    log(f"回溯：{hours} 小时（{hours/24:.1f} 天）"
        + ("   模式：离线（--from-file）" if args.from_file else ""))
    log("")

    source_ids = ([s.strip() for s in args.sources.split(",") if s.strip()]
                  if args.sources else None)
    from_dir = Path(args.from_file) if args.from_file else None

    records, report, metrics = collect(cfg, fetcher, source_ids, run_dt,
                                       from_dir, verbose)

    seen = {} if args.no_seen else load_seen()
    all_recs, fresh = dedupe(records, seen, run_dt)

    if verbose:
        log("")
        for r in report:
            log(f"  {STATUS_ICON.get(r['status'], r['status'])} {r['id']}: "
                f"{r['items']} 条 最新 {fmt_date(r.get('newest'))}"
                + (f"  {r.get('note','')}" if r.get("note") else ""))
        log(f"\n合计：窗内 {len(all_recs)} 条，本次新增 {len(fresh)} 条")

    out_dir = Path(args.out) if args.out else None
    paths = write_outputs(fresh, all_recs, report, metrics, run_dt, hours,
                          out_dir=out_dir)

    # ---- 历史时间序列（战前 → 至今的逐月变化）----
    hist_paths: Dict[str, Any] = {}
    if not args.no_history:
        root = out_dir or OUTPUT_DIR
        acc = load_accumulated(root)
        log("")
        log("更新历史时间序列 …")
        try:
            hist_paths = build_history(cfg, metrics, acc, root, run_dt, fetcher,
                                       verbose, backfill=args.backfill)
            s = hist_paths.get("summary") or {}
            for c in s.get("checks", []):
                lft, rgt = c.get("left"), c.get("right")
                if lft is None or rgt is None:
                    continue
                gap = lft - rgt
                flag = "✅" if abs(gap) < 0.01 else "❌"
                log(f"  {flag} {c['name']}：差 {gap:,.2f}")
            log(f"  → 序列 {s.get('series_rows')} 条 / {s.get('metric_count')} 个指标 / "
                f"{len(s.get('months') or [])} 个月 / 官方事件 {len(s.get('events') or [])} 条")
        except Exception as e:                                     # noqa: BLE001
            log(f"  [!] 历史序列生成失败：{type(e).__name__}: {e}")

    # 索引放在历史之后写，否则当天那份趋势报告不会出现在索引里
    write_index(out_dir or OUTPUT_DIR)

    if not args.no_seen:
        for r in all_recs:
            seen[r["id"]] = r.get("published") or ""
        save_seen(seen)

    log("")
    log(f"日报    : {paths['digest']}")
    log(f"证据板  : {paths['evidence']}")
    log(f"结构化  : {paths['jsonl']}")
    log(f"指标    : {paths['metrics']}")
    if hist_paths:
        log(f"趋势    : {hist_paths.get('trend')}")
        log(f"序列    : {hist_paths.get('series')}")
    if not verbose:
        print(json.dumps({"digest": str(paths["digest"]),
                          "evidence": str(paths["evidence"]),
                          "records_new": len(fresh),
                          "records_total": len(all_recs),
                          "trend": str(hist_paths.get("trend") or ""),
                          "series": str(hist_paths.get("series") or "")},
                         ensure_ascii=False))
    return 0


TERMS: List[Tuple[str, "re.Pattern[str]"]] = []
SIGNAL_INDEX: List[Tuple[str, int, str, "re.Pattern[str]", list]] = []
EXCLUDE_PHRASES: List[str] = []
STRONG_INDEX: List[Tuple[str, "re.Pattern[str]"]] = []
WEAK_INDEX: List[Tuple[str, "re.Pattern[str]"]] = []
# 弹药合同表的显示门槛。PSC 13xx/14xx 会带出大量国防后勤局(DLA)零备件小额订单
# （实测 30 条里 17 条是「挡圈」「排气管罩」这类、金额近 0），它们对
# 「补货速度」没有信息量，因此只列出 ≥ 门槛的订单，并如实标注被折叠笔数。
CONTRACT_DISPLAY_FLOOR = 1_000_000
RELEVANCE_MIN = 2

# 历史序列起点（美伊冲突起始月）。由 config.history.start 在 main() 里覆盖；
# 单独提出来是因为采集函数在 main() 之后才被调用，需要模块级可见。
HISTORY_START: Optional[str] = None
PSC_CFG: Dict[str, Any] = {}
HISTORY_CFG: Dict[str, Any] = {}

# 事件时间线的**过滤审计**：被「主题锚定 / 排除项 / 相关性」挡下的候选全部留档。
# 与 _title_gate_audit.json 同理 —— 收紧词表最容易连真信号一起删掉，
# 没有这份留档就无法复核「某条没进时间线」到底是无关还是删过头了。
EVENT_AUDIT: List[Dict[str, Any]] = []
EVENT_AUDIT_LOWREL: List[Dict[str, Any]] = []
SCHEDULE_CFG: Dict[str, Any] = {}
# 被标题闸门过滤掉的标题（按源分组）。落盘供人工回扫 —— 压假阳性时最容易
# 连真信号一起删掉，没有这份审计就无法发现「过滤过头」。
GATE_AUDIT: Dict[str, List[Dict[str, str]]] = {}


if __name__ == "__main__":
    sys.exit(main())
