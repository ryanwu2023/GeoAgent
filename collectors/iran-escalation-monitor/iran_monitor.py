#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""iran-escalation-monitor —— 伊朗战备与升级动态监测（六大关注域 × 当事方对照）

设计前提（决定了整个项目的写法）
--------------------------------
本系统监测的是**公开表态（statements）**，不是**事实（facts）**。
因此美方官方源、伊方官方源、第三方源在证据地位上**按信源等级分层**：
每条记录都只作为「该发布主体说了什么」的一手/二手记录被采集，
而不是作为「事情真相」的证据。任何涉己表述都必须经交叉验证才能引用。

六大关注域（用户指定，见 §3）
------------------------------
  1. naval_platform   打击美军海上作战平台（航母/驱逐舰/两栖舰/基地驻泊）
  2. chokepoint       霍尔木兹海峡 + 曼德海峡「双线施压」
  3. energy_infra     打击美国盟友关键能源设施（油田/炼厂/出口终端）
  4. cable            切断海峡海底光缆
  5. nuclear          核武器试验 / 核计划里程碑
  6. readiness        一般战备动态（动员、演习、导弹、代理人战线）

另有第 7 个**前瞻域 foresight**（§4）：用于呈现「未来 1–4 周可能的升级动向」
的**意图性表述**。★ 它是**意图指标，不是预测**：威胁不等于预告，低分不等于无升级，
沉默（无公开表态）无法被本系统捕获。详见 §0.4 与 §11。

★ 「六大关注域」的匹配精度靠 `require_all` 二维命中（如 attack + 美军海上平台 同时命中），
  避免 `attack` 一词命中所有袭击新闻。词表必须是**原形**，`_inflections()` 自动派生变形族。

工程骨架沿用本项目群已验证的做法（见 crawler-source-triage / public-topic-monitor）：
  * 源开关 + 备用 URL，失效源停用留档而不是删除（不写死代理端口，每次打印出口）
  * 浏览器常规请求头（Sec-Fetch-* 是 403→200 的开关）
  * gzip/deflate 双解码；404 重试、403/410 不重试
  * 停更检测：上报「源内最新条目日期」
  * 落盘按 id 合并，**合并源是「窗口内全部记录」而不是「仅新增」**（跨天不塌）
  * 另存 ALL-records.jsonl 累积总档（永不淘汰，带 first_seen）
  * 每条信号都带 context 原文片段（可核验性）
  * 信源等级 T1–T4 + 发布主体 + 判定理由
  * 展示折叠与统计打分用**同一个函数**，折叠条数上报

用法
----
    python iran_monitor.py                          # 日常增量（默认窗口）
    python iran_monitor.py --backfill               # 首次回填到历史起点
    python iran_monitor.py --selftest               # 离线自检（不联网）
    python iran_monitor.py --from-file feeds        # 离线：读 feeds/<源id>.xml|json
    python iran_monitor.py --sources isna-en,reuters-world
    python iran_monitor.py --show-all               # 不做折叠，列全部记录
    python iran_monitor.py --days 720               # 覆盖回溯天数
    python iran_monitor.py --strict-tls             # 拒绝任何关闭证书校验的源
    python iran_monitor.py --render-only            # 离线仅重渲染（不联网抓取）
    python iran_monitor.py --proxy http://127.0.0.1:7897   # 排障：显式指定代理出口

★ 代理默认**自动探测系统代理**（每次运行都打印实际出口）。本机代理端口会漂移，
  端口写死会让大批源静默失败，且症状与「被 CDN 风控拦截」完全一样 —— 因此
  `--proxy` 只作排障覆盖，**不要**写进脚本或快捷方式。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import html as html_mod
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

VERSION = "1.5.0"
ROOT = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(ROOT, "output")
STATE_DIR = os.path.join(ROOT, "state")
FEEDS_DIR = os.path.join(ROOT, "feeds")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 只带 UA 会被 Akamai 拦；Sec-Fetch-* 是实测的 403→200 开关
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

# ---------------------------------------------------------------- 全局配置
CFG: Dict[str, Any] = {}
LOOKBACK_HOURS = 168
PER_SOURCE_MAX = 60
REQ: Dict[str, Any] = {}
REL_CFG: Dict[str, Any] = {}
SIG_CFG: Dict[str, Any] = {}
CLAIM_CFG: Dict[str, Any] = {}
CROSS_ANCHORS: List[Dict[str, str]] = []
CROSS_MATCH_DAYS = 7
BACKFILL = False
STRICT_TLS = False
SHOW_ALL = False
TLS_DOWNGRADED: List[Dict[str, str]] = []
GATE_AUDIT: Dict[str, Any] = {}
OUTLET_MAP: Dict[str, Any] = {}
SOURCE_BY_ID: Dict[str, Dict[str, Any]] = {}
# 词表指纹：记录每条记录的派生字段是**哪一版词表**算出来的。
# 没有它就无法察觉「历史记录用旧词表、新记录用新词表」这种混龄档案 ——
# 症状是趋势图与交叉验证都「看起来正常」，但两段数据口径不可比。
# ★ 改动解析/清洗逻辑时**必须**递增 PARSE_VER，否则历史记录不会重算。
PARSE_VER = "2"
CFG_VER = ""
BACKFILLED = 0
# 折叠明细的**展示**抽样上限。注意它只限制「列出多少条明细」，
# **不限制分类计数** —— 计数错在抽样上曾让 864 条被拆成 400 条（加总不自洽）。
FOLD_SAMPLE_LIMIT = 400
# 代理出口。默认 None = 用系统代理（自动探测，Windows 下读注册表）。
#
# ★★ 为什么不写死端口：本机代理端口会**漂移**（代理客户端重启后 7897 → 51681 之类）。
#    端口写死的后果是「大批源突然全挂」，而症状与被 CDN 风控拦截**完全一样** ——
#    实测同一次运行里 12 个源失败，其中 6 个报 `Tunnel connection failed: 502`、
#    6 个报 `403 AkamaiGHost/cloudflare`，看上去像风控，实际是代理端口过期。
#    换到存活端口后这 12 个源**全部返回 200 + 真实 RSS**。
#    因此：默认走系统代理，每次运行都打印实际出口；`--proxy` 只作**排障覆盖**，
#    绝不写进脚本或快捷方式。
PROXY_OVERRIDE: Optional[str] = None


# ================================================================ 文本工具
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def log(msg: str, quiet: bool = False) -> None:
    if not quiet:
        print(msg, flush=True)


# 波斯语/阿拉伯语字符变体归一：不同媒体混用 ی/ي 与 ک/ك，不归一就会漏匹配
_FA_MAP = {
    "\u064a": "\u06cc",   # ي → ی
    "\u0643": "\u06a9",   # ك → ک
    "\u0629": "\u0647",   # ة → ه
    "\u200c": " ",        # ZWNJ → 空格
    "\u200e": " ",        # LRM
    "\u200f": " ",        # RLM
    "\u064b": "", "\u064c": "", "\u064d": "", "\u064e": "",
    "\u064f": "", "\u0650": "", "\u0651": "", "\u0652": "",  # 阿拉伯语音符
    "\u0640": "",         # tatweel
}


def norm_text(s: str) -> str:
    """归一化后再匹配 —— 否则波斯语的 ZWNJ 与字符变体会造成大面积漏检。

    ★ **不转小写**。转小写会让 `context` 显示的「原文片段」全是小写，
    读者无法直接与原文比对，也无法复制回原文检索 —— 而「每个信号都能回溯
    到原文」正是本项目的可信度基础。
    需要大小写无关比较的地方（outlet 匹配、标题闸门）显式用 `norm_key()`；
    正则匹配本身走 re.I，波斯语无大小写之分。
    """
    if not s:
        return ""
    s = html_mod.unescape(s)
    for k, v in _FA_MAP.items():
        s = s.replace(k, v)
    # 阿拉伯数字 → 拉丁数字（伊朗媒体常用 ۰۱۲۳۴۵۶۷۸۹）
    fa_digits = "۰۱۲۳۴۵۶۷۸۹"
    for i, ch in enumerate(fa_digits):
        s = s.replace(ch, str(i))
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def norm_key(s: str) -> str:
    """大小写无关的比较键（用于 `in` / `==` 这类**非正则**比较）。"""
    return norm_text(s).lower()


def _is_ascii(p: str) -> bool:
    return all(ord(c) < 128 for c in p)


def _inflections(w: str) -> str:
    """给定**末词**，返回其词形族正则片段（不含词边界）。

    ★ 这是本项目第二类静默漏检的修复（第一类是「只写屈折形」）。
    实测：`attrition_admit` 桶里写的是半截词根 `deplet` / `casualt` / `attrit`，
    `strike_claim` 桶里写 `annihilat` / `neutraliz`。作者本意是让 -e 结尾的动词
    也能挂上 -ed/-ing，但代价是**原形与 -ion/-ation 名词形全部漏检**：
      · `deplet` 匹配不到 `the depletion of key weapons stockpiles`（弹药耗尽，本主题核心）
      · `casualt` 匹配不到 `US casualties`（战损承认）
      · `annihilat` 匹配不到 `Unable to Annihilate ...`（打击声明）
    而 `claims` 里写的却是完整原形 —— 同一个概念两套形态约定，必然有一处是错的。

    ★ 规则（按词尾分派，**后缀必选**）：

    - `-e` 结尾：`stem(?:e|es|ed|ing|ion|ions|ation|ations)`
      覆盖 deplete/depletes/depleted/depleting/**depletion**、
      neutralize/**neutralization**、mobilize/**mobilization**、
      annihilate/**annihilation**。
      `-ion` 与 `-ation` 两个分支都需要，因为去 e 后有的直接接 `ion`
      （deplete→depletion、annihilate→annihilation），有的接 `ation`
      （neutralize→neutralization、mobilize→mobilization）——
      实测只加 `-ion` 时 `neutralization` 命不中，自检当场报红。
      **后缀必选**是关键：若允许空后缀，`site`→`sit`、`mine`→`min`、
      `note`→`not` 就会把普通词匹配成信号词（假阳性）；必选则原形由 `e` 分支
      覆盖，`sit` / `min` / `not` 独立出现时**不会**命中。
    - 辅音 + `-y` 结尾：`stem(?:y|ies|ied|ying)`
      覆盖 supply/supplies/supplied/supplying、casualty/casualties。
      元音 + y（deploy/ready）走下面的通例，因为它们是 `+ed/+ing` 而非 `ied`。
    - 其余：`w(?:s|es|ed|ing|d)?`（末尾可选，因为原形就是裸词）
    """
    e = re.escape(w)
    if not w:
        return e
    low = w.lower()
    if len(low) > 2 and low.endswith("y") and low[-2] not in "aeiou":
        return re.escape(w[:-1]) + r"(?:y|ies|ied|ying)"
    if len(low) > 2 and low.endswith("e"):
        return re.escape(w[:-1]) + r"(?:e|es|ed|ing|ion|ions|ation|ations)"
    return e + r"(?:s|es|ed|ing|d)?"


def _phrase_pattern(p: str) -> re.Pattern:
    """拉丁词条：加字母/数字边界 + 按词尾生成词形族。

    ★ 词形族只加在**末词**上，不能加在整个短语上
    （否则 `engage the` → 会变成匹配 `engage thes`）。
    ★ 词表里请一律写**基础形**（`deplete` 而非 `deplet` 或 `depleted`），
    形态由 `_inflections` 派生；写半截词根会漏掉原形与 -ion 名词形。
    """
    words = p.split(" ")
    head = r"\s+".join(re.escape(w) for w in words[:-1])
    body = (head + r"\s+" if head else "") + _inflections(words[-1])
    return re.compile(r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])", re.I)


_PAT_CACHE: Dict[str, Any] = {}
_FA_CACHE: Dict[str, Any] = {}
# 波斯/阿拉伯/希伯来字母区（含阿拉伯补充区与呈现形式）。用于非 ASCII 词条的
# 「词内边界」判断 —— 波斯语没有空格分词，靠这个区间判断「是否落在词内部」。
_FA_RANGE = "\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFF"


def _fa_pattern(p: str) -> re.Pattern:
    """非 ASCII（波斯/阿拉伯）词条的匹配式，带**词内边界**。

    ★ 为什么必须加边界：非 ASCII 词条原先走的是**裸子串** `str.find()`，
      而波斯语短词会嵌进普通词里。实测 `رد`（否认）命中了：
        فردا（明天）· بردار（拿走）· خرد（微小）· درد（疼痛）· سردار（将领）
      —— 也就是说**普通波斯语句子几乎句句都命中「否认」**。
      后果不是「噪声多一点」：`denial` 正是交叉验证里判定 contradiction 的
      输入，灌水会**凭空造出对撞**，而这是本报告最核心的交付物。

    ★ 规则：
      · **左侧一律加边界**（安全：本项目词条都不是前缀黏着形式）
      · **右侧只在词条 ≤3 字符时加**。原因是波斯语常把后缀连写
        （نفت+کش→نفتکش「油轮」、رزم+ی→رزمی「作战的」），长词条连写多半仍是
        同一个词；而短词条连写往往是**另一个词**——
        `قطع`（切断）会命中 `قطعنامه`（决议），这在核问题报道里极高频。
      · 需要连写形式的，必须在词表里**显式列出**（自检里有对应用例）。
    """
    esc = re.escape(norm_text(p))
    left = f"(?<![{_FA_RANGE}])"
    right = f"(?![{_FA_RANGE}])" if len(esc) <= 3 else ""
    return re.compile(left + esc + right)


def _find_single(hay_norm: str, p: str) -> List[Tuple[str, int, int]]:
    """单个词条（不含复合条件）的匹配。"""
    out: List[Tuple[str, int, int]] = []
    pn = norm_text(p)
    if not pn:
        return out
    if _is_ascii(pn):
        rx = _PAT_CACHE.get(pn)
        if rx is None:
            rx = _phrase_pattern(pn)
            _PAT_CACHE[pn] = rx
        for m in rx.finditer(hay_norm):
            out.append((p, m.start(), m.end()))
    else:
        rx = _FA_CACHE.get(pn)
        if rx is None:
            rx = _fa_pattern(pn)
            _FA_CACHE[pn] = rx
        for m in rx.finditer(hay_norm):
            out.append((p, m.start(), m.end()))
    return out


def find_hits(hay_norm: str, phrases: List[str]) -> List[Tuple[str, int, int]]:
    """在归一化文本里找词条，返回 (词条, 起, 止)。

    支持**复合条件短语**：用 ` + ` 连接的部分必须**全部出现**才算命中。
    这是为了避开「泛化词灌成假阳性」与「具体词漏检」的两难：
    `sale of` 太泛（会命中卖房子），`sale of fighter` 太窄（漏掉 "sale of 48 F-35 fighter jets"），
    而 `sale + fighter` 恰好精确。
    """
    out: List[Tuple[str, int, int]] = []
    for p in phrases:
        if not p:
            continue
        # 复合条件
        if " + " in p:
            parts = [x.strip() for x in p.split(" + ") if x.strip()]
            spans: List[Tuple[str, int, int]] = []
            ok = True
            for part in parts:
                h = _find_single(hay_norm, part)
                if not h:
                    ok = False
                    break
                spans.append(h[0])
            if ok and spans:
                s = min(x[1] for x in spans)
                e = max(x[2] for x in spans)
                out.append((p, s, e))
            continue
        out.extend(_find_single(hay_norm, p))

    # 去重叠：长词优先（避免 "carrier" 抢先于 "carrier strike group"）
    out.sort(key=lambda x: (-(x[2] - x[1]), x[1]))
    kept: List[Tuple[str, int, int]] = []
    for h in out:
        if any(not (h[2] <= k[1] or h[1] >= k[2]) for k in kept):
            continue
        kept.append(h)
    kept.sort(key=lambda x: x[1])
    return kept


def make_context(hay: str, s: int, e: int, pad: int = 70) -> str:
    a = max(0, s - pad)
    b = min(len(hay), e + pad)
    frag = hay[a:b].strip()
    return ("…" if a > 0 else "") + frag + ("…" if b < len(hay) else "")


# ================================================================ 解析工具
TAG_RE = re.compile(r"<[^>]+>")
CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)


def strip_tags(s: str) -> str:
    """去标签 + 反转义。

    ★ 顺序很关键：必须先反转义再删标签，并且**重复两轮**。
    实测 Google News 的 description 是 `&lt;a href="..."&gt;`：
    先删标签（此时还不是标签，删不掉）→ 再反转义 → 结果把 `<a href=…>`
    原样留在了正文里，报表中每个「原文片段」末尾都挂着一串 HTML。
    """
    if not s:
        return ""
    for _ in range(2):
        s = CDATA_RE.sub(r"\1", s)
        s = html_mod.unescape(s)
        s = TAG_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def has_persian(s: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", s or ""))


# ★ 非英语月份 / 星期名：多语源（西语 crisisgroup、法语/德语媒体）原先一律解析失败。
#   失败代价是**双重的**：既进不了 §8 周趋势，又算不出年龄 → 绕过时间窗过滤。
#   这里统一换成英文缩写再走同一套 strptime，避免为每种语言各写一套格式表。
_MONTH_ALIAS = {
    "enero": "Jan", "ene": "Jan", "febrero": "Feb", "marzo": "Mar", "abril": "Apr",
    "abr": "Apr", "mayo": "May", "junio": "Jun", "julio": "Jul", "agosto": "Aug",
    "ago": "Aug", "septiembre": "Sep", "setiembre": "Sep", "sept": "Sep",
    "octubre": "Oct", "oct": "Oct", "noviembre": "Nov", "nov": "Nov",
    "diciembre": "Dec", "dic": "Dec",
    "janvier": "Jan", "février": "Feb", "fevrier": "Feb", "mars": "Mar",
    "avril": "Apr", "mai": "May", "juin": "Jun", "juillet": "Jul", "août": "Aug",
    "aout": "Aug", "septembre": "Sep", "octobre": "Oct", "novembre": "Nov",
    "décembre": "Dec", "decembre": "Dec",
    "januar": "Jan", "februar": "Feb", "märz": "Mar", "maerz": "Mar",
    "juni": "Jun", "juli": "Jul", "oktober": "Oct", "dezember": "Dec",
}
_WEEKDAY_ALIAS = {
    "lunes": "Mon", "martes": "Tue", "miércoles": "Wed", "miercoles": "Wed",
    "jueves": "Thu", "viernes": "Fri", "sábado": "Sat", "sabado": "Sat",
    "domingo": "Sun",
    "lundi": "Mon", "mardi": "Tue", "mercredi": "Wed", "jeudi": "Thu",
    "vendredi": "Fri", "samedi": "Sat", "dimanche": "Sun",
    "montag": "Mon", "dienstag": "Tue", "mittwoch": "Wed", "donnerstag": "Thu",
    "freitag": "Fri", "samstag": "Sat", "sonntag": "Sun",
}


def _alias_dates(norm: str) -> str:
    """把非英语星期/月份名换成英文缩写（只动词，不动数字）。"""
    def _sub(m):
        w = m.group(0)
        low = w.lower()
        return _WEEKDAY_ALIAS.get(low) or _MONTH_ALIAS.get(low) or w
    return re.sub(r"[A-Za-zÀ-ÿ]+", _sub, norm)


# ★ Unix 纪元零值：Google News RSS 对**没有日期的条目**填 `Thu, 01 Jan 1970`。
#   它不是「一个很旧的日期」，而是「日期未知」的哨兵值。
#   若把它当普通日期处理，会因为年份兜底判失败 → 记录**永远**绕不过时间窗，
#   在快照里永久滞留（重跑也清不掉）。因此显式识别、置空、单独计数。
_EPOCH_SENTINEL_RE = re.compile(
    r"^(?:Thu|Thursday),?\s*0?1\s+Jan\s+1970\b", re.I)


def is_epoch_sentinel(s: str) -> bool:
    return bool(s and _EPOCH_SENTINEL_RE.match(s.strip()))


def parse_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    if is_epoch_sentinel(s):            # 纪元零值 = 无日期，不是日期
        return None
    # ★ 归一化后再试：crisisgroup 用 `Wednesday, September 16, 2026 - 17:32`，
    #   IAEA 用两位数年份 `Wed, 29 Jul 26 12:37:14 +0200`。
    #   这两种格式原先都解析失败，后果是**双重的静默损失**：
    #     ① 记录进不了 §8 周趋势（按周统计要求日期可解析）；
    #     ② 年龄算不出来 → **绕过时间窗过滤**，过期条目混进快照。
    #   实测一次「14 天窗口」的快照里因此混入了 2026-07 的条目，
    #   并有 95 条记录只出现在 §1/§3、不出现在 §8 —— 两个口径各自「看着正常」。
    norm = " ".join(s.replace("GMT", "+0000").replace("UTC", "+0000").split())
    norm = _alias_dates(norm)           # 西/法/德 星期与月份 → 英文缩写
    # ★★ 两位数年份必须在**任何 strptime 之前**展开成四位数。
    #   Python 的 `%Y` 是宽松的（接受 1–4 位数字），`26` 会被解析成**公元 26 年** ——
    #   记录于是变成「极旧」，既被时间窗过滤掉、又让周归属彻底错乱，
    #   而解析**返回成功**、没有任何报错。
    norm = re.sub(r"\b(\d{1,2}) ([A-Z][a-z]{2}) (\d{2})\b",
                  lambda m: f"{m.group(1)} {m.group(2)} 20{m.group(3)}", norm)
    # 只在**数字后面**的 Z 才当作 UTC 标记（避免误伤正文里的字母 Z）
    norm = re.sub(r"(?<=\d)Z\b", "+0000", norm)
    norm = " ".join(norm.replace(",", ", ").split())
    now_y = now_utc().year
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%a, %d %b %Y %H:%M %z", "%a, %d %b %Y %H:%M", "%a, %d %b %Y", "%a, %d %b %y %H:%M:%S %z",
                "%a, %d %b %y %H:%M %z", "%d %b %Y %H:%M:%S %z", "%d %b %Y %H:%M %z",
                # 全称星期 + 全称月份：`Wednesday, September 16, 2026 - 17:32`
                "%A, %B %d, %Y - %H:%M", "%A, %B %d, %Y %H:%M", "%A, %B %d, %Y",
                # ★ 同上，但星期/月份已被 `_alias_dates` 换成英文**缩写**
                #   （西/法/德源走的就是这条路径）：`Fri, Sep 11, 2026 - 09:47`
                "%a, %b %d, %Y - %H:%M", "%a, %b %d, %Y %H:%M", "%a, %b %d, %Y",
                "%a, %b %d, %Y, %H:%M", "%a %b %d, %Y - %H:%M",
                "%B %d, %Y - %H:%M", "%B %d, %Y %H:%M", "%B %d, %Y",
                "%b %d, %Y - %H:%M", "%b %d, %Y %H:%M", "%b %d, %Y",
                # 德语常见写法：日+句点+月份名全称
                "%d. %B %Y %H:%M", "%d. %B %Y", "%d.%m.%Y %H:%M", "%d.%m.%Y",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d", "%d %b %Y %H:%M:%S", "%d %b %Y %H:%M", "%d %b %Y"):
        try:
            dt = datetime.strptime(norm, fmt)
        except ValueError:
            continue
        # ★ 年份合理性兜底：凡是解析成 2000 年前或未来一年之后的，一律判为失败。
        #   「解析成功但年份离谱」比「解析失败」更危险 —— 失败会被看见，
        #   离谱的年份会安静地把记录放到时间轴的两端。
        if dt.year < 2000 or dt.year > now_y + 1:
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def date_iso(pub: str) -> str:
    """已归一化的日期前缀；无法解析时返回空串（供机器可读文件用）。"""
    p = (pub or "").strip()
    return p[:10] if ISO_DATE_RE.match(p) else ""


def date_cell(pub: str) -> str:
    """表格里的「日期」单元格。

    ★ 绝不要直接 `published[:10]` —— 日期解析失败时 `published` 是**原始字符串**，
      截 10 个字符会印出 `Thu, 01 Ja` 这种半截垃圾（实测：Google News 的纪元零值
      哨兵 `Thu, 01 Jan 1970 00:00:00 GMT`）。它看着像日期，却既不是日期、
      也没有可读含义；一旦印进表里，读者无法判断是「日期缺失」还是「渲染坏了」。
      无法定位到时间轴的一律印 `—`，条数在 §8 单独披露。
    """
    return date_iso(pub) or "—"


def entry_id(source_id: str, link: str, title: str, pub: str) -> str:
    key = link.strip() or (title.strip() + "|" + (pub or ""))
    return hashlib.sha1(f"{source_id}|{key}".encode("utf-8")).hexdigest()[:16]


# 素材序号后缀：DVIDS 等官方影像分发源把同一条报道拆成
# 「标题 [Image 1 of 5]」…「[Image 5 of 5]」，描述几乎相同、链接不同。
ASSET_SUFFIX_RE = re.compile(
    r"\s*\[(?:image|video|photo|audio|graphic|infographic|document|"
    r"multimedia)\s*#?\s*\d+\s*(?:of|/)\s*\d+\]\s*$", re.I)


def title_key(title: str) -> str:
    """标题去重键：归一化 + 抹掉素材序号后缀。

    ★ 不这么做，一条「Eager Lion 26」演习新闻会变成 5 条记录 ——
    既虚增美方条数（正好是本项目最需要警惕的偏差方向），
    又让交叉矩阵里同一条重复出现。
    """
    t = norm_key(title or "")
    for _ in range(3):                    # 可能叠了多层后缀
        t2 = ASSET_SUFFIX_RE.sub("", t)
        if t2 == t:
            break
        t = t2
    return re.sub(r"\s+", " ", t).strip().lower()[:110]


# ================================================================ 网络层
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


_PROXY_STATE = {"checked": False, "url": None}
_EGRESS_CACHE = {"at": 0.0, "ok": []}
_EGRESS_OPENER: Dict[str, Any] = {}
_EGRESS_DEEP_PROBES = (
    "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=5d&interval=1d",
    "https://news.google.com/rss/search?q=probe&hl=en-US&gl=US&ceid=US:en",
)

def proxy_candidates():
    """候选出口：环境代理优先，常见本地端口次之，直连兜底。绝不写死单一端口。"""
    out = []
    env = urllib.request.getproxies()
    for k in ("https", "http"):
        u = env.get(k)
        if isinstance(u, str) and u.startswith("http") and u not in out:
            out.append(u)
    for port in (7897, 7890, 10809, 1080, 8080, 8888, 4780, 2080, 20171, 33210):
        u = "http://127.0.0.1:%d" % port
        if u not in out:
            out.append(u)
    return out + [None]

def _egress_opener(p, tls_verify: bool = True):
    key = (p or "") + ("|noverify" if not tls_verify else "")
    if key not in _EGRESS_OPENER:
        handlers: List[Any] = []
        if p:
            handlers.append(urllib.request.ProxyHandler({"http": p, "https": p}))
        else:
            handlers.append(urllib.request.ProxyHandler({}))
        if not tls_verify:
            handlers.append(urllib.request.HTTPSHandler(
                context=ssl._create_unverified_context()))
        _EGRESS_OPENER[key] = urllib.request.build_opener(*handlers)
    return _EGRESS_OPENER[key]

def _proxy_alive(p, timeout=5.0):
    """通用探活 —— 只负责筛掉**死端口**。

    ★ 为什么不能只靠它（2026-09-20 实测）：:57444 出口对 gstatic204 与
      cloudflare trace 两个探针都返回正常，但同一时刻
        Yahoo chart API → 403（JS 反爬挑战页），Google News RSS → 超时。
      它是「半死」出口 —— 连得上，但被目标站风控。只做通用探活的结果，
      就是整轮抓取静默拿回一堆 403/超时，日志上却只显示「源失败」，
      排查方向被带到「源挂了」，而真相是「这个出口被目标站拉黑」。
      → 真实可用性交给 _proxy_deep_score() 排序判断。
    """
    for probe in ("http://www.gstatic.com/generate_204",
                  "https://www.cloudflare.com/cdn-cgi/trace"):
        try:
            if _egress_opener(p).open(probe, timeout=timeout).status in (200, 204):
                return True
        except Exception:
            continue
    return False

def _proxy_deep_score(p, timeout=6):
    """真实目标深探得分（0–2）：只对**真的会给客户端发挑战页**的站探。"""
    good = 0
    for probe in _EGRESS_DEEP_PROBES:
        try:
            if _egress_opener(p).open(probe, timeout=timeout).status == 200:
                good += 1
        except Exception:
            pass
    return good

def live_proxies():
    """存活出口列表，按「真实目标可用性」降序（缓存 10 分钟）。

    启动只挑**一个**出口是不够的：挑中的那个可能正好是半死的。
    保留整条候选链，抓取时 403 / 隧道失败就顺延到下一个。
    """
    if PROXY_OVERRIDE:
        return [None if PROXY_OVERRIDE == "direct" else PROXY_OVERRIDE]
    now = time.time()
    if _EGRESS_CACHE["ok"] and now - _EGRESS_CACHE["at"] < 600:
        return list(_EGRESS_CACHE["ok"])
    scored = []
    for u in proxy_candidates():
        if not _proxy_alive(u):
            continue
        scored.append((-_proxy_deep_score(u), u))
    scored.sort(key=lambda x: (x[0], x[1] is None, x[1] or ""))
    ok = [u for _, u in scored] or [None]
    _EGRESS_CACHE.update({"at": now, "ok": ok})
    return list(ok)

def current_proxy():
    return live_proxies()[0]


_EGRESS_ROTATE = ("Tunnel connection failed",
                  "10060", "10054", "10053",
                  "RemoteDisconnected", "Remote end closed",
                  "Connection reset", "Connection aborted",
                  "timed out", "TimeoutError")

def egress_rotatable(err):
    """哪些网络层异常应当**立即换出口**，而不是原地重试。

    2026-09-20 全库审计补充：除 `Tunnel connection failed` 之外，
      · `WinError 10060`（连接方在一段时间后没有正确答复）
      · `RemoteDisconnected` / `Remote end closed` / `Connection reset`
    同样高度指向「**这个出口**到目标站的链路被掐了」，换一个出口经常立刻成功。
    以前只在 Tunnel 失败时轮换，这些就被误当成「源挂了」写进报告。
    """
    e = err or ""
    return any(p in e for p in _EGRESS_ROTATE)

def demote_proxy(p):
    """某出口被目标站拒绝 / 隧道失败时立即摘除（缓存到期后自动重新探测恢复）。"""
    if p in _EGRESS_CACHE.get("ok", []):
        _EGRESS_CACHE["ok"].remove(p)

def _resolve_auto_proxy():
    """向后兼容旧调用：返回**首选**出口。"""
    return current_proxy()


def _opener(tls_verify: bool = True):
    """构造 urllib opener。

    - 未指定 `--proxy` 时走出口池首选（自动探测，深探通过者优先）
    - 指定 `--proxy` 时用显式 ProxyHandler（仅供排障：代理客户端重启后端口会变）
    - `tls_verify=False` 时换一个不校验证书的 HTTPSHandler（仅用于个别自签名链站点）

    ★ 绝不在代码/脚本里写死代理端口：端口会“漂移”，症状与被 CDN 风控拦截完全一样。
    """
    handlers: List[Any] = []
    eff = live_proxies()[0]
    if eff:
        handlers.append(urllib.request.ProxyHandler(
            {"http": eff, "https": eff}))
    if not tls_verify:
        handlers.append(urllib.request.HTTPSHandler(
            context=ssl._create_unverified_context()))
    return urllib.request.build_opener(*handlers)


def http_get(url: str, *, json_mode: bool = False, tls_verify: bool = True,
             timeout: int = 30, retries: int = 3,
             backoff: float = 2.0) -> Dict[str, Any]:
    last = ""
    last_info: Optional[Dict[str, Any]] = None
    # ★ 出口轮换：403 在很多站上是「这个出口 IP 被风控」，不是「源拒绝了我们」。
    #   只挑一个出口的结果，就是半死出口把整轮 gnews 源全部拖成 0 条。
    pool = list(live_proxies()) or [None]
    for ei, egress in enumerate(pool):
        for i in range(max(1, retries)):
            try:
                req = urllib.request.Request(
                    url, headers=(JSON_HEADERS if json_mode else RSS_HEADERS))
                r = _egress_opener(egress, tls_verify).open(req, timeout=timeout)
                return {"status": r.status, "server": r.headers.get("Server", ""),
                        "body": decompress(r.read(), r.headers.get("Content-Encoding", "")),
                        "err": None, "attempts": i + 1,
                        "egress": egress or "direct"}
            except urllib.error.HTTPError as e:
                body = b""
                try:
                    body = decompress(e.read(), e.headers.get("Content-Encoding", ""))
                except Exception:  # noqa: BLE001
                    pass
                info = {"status": e.code, "server": e.headers.get("Server", ""),
                        "body": body, "err": None, "attempts": i + 1,
                        "egress": egress or "direct"}
                last_info = info
                # 410 Gone —— 内容级，换出口也没用，立刻返回；
                # 403 —— 可能是出口 IP 被风控，换出口再试
                if e.code == 410:
                    return info
                last = f"HTTP {e.code}"
                if e.code != 404:
                    if e.code == 403 and ei < len(pool) - 1:
                        demote_proxy(egress)
                        break
                    return info
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
                if egress_rotatable(last):
                    demote_proxy(egress)
                    break
            if i < retries - 1:
                time.sleep(backoff * (i + 1))
    if last_info is not None:
        return last_info
    return {"status": None, "server": "", "body": b"", "err": last,
            "attempts": retries,
            "egress": (pool[0] or "direct") if pool else "direct"}


def proxy_report() -> str:
    if PROXY_OVERRIDE:
        return f"{PROXY_OVERRIDE}（--proxy 显式覆盖，仅供排障）"
    pool = live_proxies()
    head = pool[0] if pool else None
    if not head:
        return "无（直连）"
    auto = urllib.request.getproxies()
    p = auto.get("https") or auto.get("http")
    tail = f"；备用 {len(pool) - 1} 个" if len(pool) > 1 else "（无备用）"
    if p and head != p:
        return f"{head}{tail}（环境代理 {p} 被判为半死出口，已按深探结果改选）"
    return f"{head}{tail}"


# ================================================================ feed 解析
ITEM_RE = re.compile(r"<(item|entry)\b.*?</\1>", re.S | re.I)


def _tag(block: str, names: List[str]) -> str:
    for n in names:
        m = re.search(rf"<{n}\b[^>]*>(.*?)</{n}>", block, re.S | re.I)
        if m:
            v = m.group(1).strip()
            v = CDATA_RE.sub(r"\1", v)
            if v:
                return v
    return ""


def _attr(block: str, tag: str, attr: str) -> str:
    m = re.search(rf"<{tag}\b[^>]*\b{attr}=[\"']([^\"']*)[\"']", block, re.I)
    return m.group(1) if m else ""


def parse_feed(xml_text: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for m in ITEM_RE.finditer(xml_text):
        b = m.group(0)
        title = strip_tags(_tag(b, ["title"]))
        if not title:
            continue
        link = ""
        lm = re.search(r"<link\b[^>]*href=[\"']([^\"']+)[\"']", b, re.I)
        if lm:
            link = lm.group(1)
        if not link:
            link = strip_tags(_tag(b, ["link", "guid", "id"]))
        summary = strip_tags(_tag(b, ["description", "summary", "content",
                                      "content:encoded"]))
        pub = strip_tags(_tag(b, ["pubDate", "published", "updated",
                                  "dc:date", "date"]))
        srcname = strip_tags(_tag(b, ["source"]))
        if not srcname:
            srcname = _attr(b, "source", "url")
        out.append({"title": title, "link": link.strip(), "summary": summary,
                    "published": pub, "item_source": srcname})
    return out


# ================================================================ 分析
def relevance_of(title: str, summary: str) -> Dict[str, Any]:
    hay = norm_text(title + " . " + summary)
    hits_s = find_hits(hay, REL_CFG.get("strong", []))
    hits_m = find_hits(hay, REL_CFG.get("medium", []))
    hits_a = find_hits(hay, REL_CFG.get("anchor", []))
    hits_d = find_hits(hay, REL_CFG.get("domain", []))
    score = 3 * len(hits_s) + 2 * len(hits_m) + 1 * len(hits_a) + 1 * len(hits_d)
    return {"score": score, "strong": len(hits_s), "medium": len(hits_m),
            "anchor": len(hits_a), "domain": len(hits_d),
            "anchor_ids": sorted({h[0] for h in hits_a})}


def signals_of(text: str, raw_text: str = "") -> List[Dict[str, Any]]:
    """返回命中信号。

    `context` 取自**归一化文本**（保留原大小写，仅归并空白/字符变体/ZWNJ），
    因此可以直接回原文检索比对；原始字符区间不做映射，因为归一化会改变偏移。

    ★ 两种命中语义：
      · `phrases`：**任一**命中即算命中（单维词表）
      · `require_all`：二维要求 —— 每个子组至少命中一个，**组间必须全部满足**。
        本主题里这是提精度的关键：`attack` 单独出现会命中一切攻击新闻，
        必须与「美方海上平台」同现才叫「打击美军海上作战平台」。
    """
    hay = norm_text(text)
    out: List[Dict[str, Any]] = []
    for bucket, spec in SIG_CFG.items():
        require_all = spec.get("require_all")
        if require_all:
            group_hits = []
            ok = True
            for grp in require_all:
                h = find_hits(hay, grp)
                if not h:
                    ok = False
                    break
                group_hits.append(h)
            if not ok:
                continue
            n_hits = sum(len(h) for h in group_hits)
            phrase, s, e = group_hits[0][0]
            grp_labels = [h[0][0] for h in group_hits]
            out.append({
                "bucket": bucket,
                "label": spec.get("label", bucket),
                "weight": spec.get("weight", 1),
                "phrase": " + ".join(grp_labels),
                "n_hits": n_hits,
                "context": make_context(hay, s, e),
                "require_all": True,
            })
            continue
        hits = find_hits(hay, spec.get("phrases", []))
        if not hits:
            continue
        phrase, s, e = hits[0]
        out.append({
            "bucket": bucket,
            "label": spec.get("label", bucket),
            "weight": spec.get("weight", 1),
            "phrase": phrase,
            "n_hits": len(hits),
            "context": make_context(hay, s, e),
        })
    return out


def claims_of(text: str) -> List[str]:
    hay = norm_text(text)
    kinds = []
    for kind, phrases in CLAIM_CFG.items():
        if find_hits(hay, phrases):
            kinds.append(kind)
    return kinds


def anchors_of(text: str) -> List[str]:
    hay = norm_text(text)
    out = []
    for a in CROSS_ANCHORS:
        try:
            rx = re.compile(a["re"], re.I)
        except re.error:
            continue
        if rx.search(hay) or rx.search(text):
            out.append(a["id"])
    return out


def load_accumulated() -> List[Dict[str, Any]]:
    p = os.path.join(OUTPUT_DIR, "ALL-records.jsonl")
    if not os.path.exists(p):
        return []
    recs = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return recs


def resolve_outlet(rec: Dict[str, Any], src: Dict[str, Any]) -> None:
    """★ 聚合器源会把不同立场的媒体混在同一个源 id 下。

    不重新判定就会出现「把流亡/反对派媒体的报道算成伊方官方表态」这种
    严重定性错误 —— 而它在报表里看不出任何异常。
    """
    if not src.get("aggregator"):
        return
    name = (rec.get("item_source") or "").strip()
    if not name:
        return
    # Google News 的标题形如 "Headline - Publisher"，把后缀去掉
    t = rec.get("title", "")
    for sep in (" - ", " – ", " — "):
        if t.endswith(sep + name):
            rec["title"] = t[: -(len(sep) + len(name))].strip()
            break
    n = norm_key(name)
    for rule in OUTLET_MAP.get("rules", []):
        for m in rule.get("match", []):
            if norm_key(m) in n:
                rec["party"] = rule.get("party", rec.get("party"))
                rec["publisher"] = rule.get("publisher", name)
                rec["outlet"] = name
                rec["outlet_resolved"] = True
                return
    # ★ 未登记媒体**绝不能继承该源的默认当事方**。
    # 实测踩过：irgc-gnews 是「IRGC when:14d」这类广泛检索，
    # 未登记的 Janes / AzerNews / KTVN / navalnews.com 全被继承成「伊方」——
    # 于是英国防务媒体、美国地方台都成了「伊朗官方表态」。
    # 正确做法：一律归第三方旁证，并打标记单列。
    rec["party"] = "third"
    rec["publisher"] = name or rec.get("publisher", "")
    rec["outlet"] = name
    rec["outlet_unclassified"] = True


def lexicon_fingerprint() -> str:
    """对「影响派生字段」的东西做指纹：词表 + 解析器版本。

    只纳入 signals / claims / relevance / cross_anchors —— party/publisher/tier
    来自 sources 与 outlet_map，改它们不会改变语义抽取结果（但会改变归类，
    故也纳入）。改词表必然改变指纹，从而触发累积档全量回填。

    ★ PARSE_VER 也纳入：解析器的修复（如 strip_tags 反转义顺序、
    标题素材后缀）同样必须能回溯作用到历史记录，否则档案里会长期留着
    旧解析器的产物（实测：Google News 描述里的 `&lt;a href=…&gt;` 残留）。
    """
    payload = json.dumps({
        "parse_ver": PARSE_VER,
        "signals": SIG_CFG, "claims": CLAIM_CFG, "relevance": REL_CFG,
        "cross_anchors": CROSS_ANCHORS, "outlet_map": OUTLET_MAP,
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def derived_fields(rec: Dict[str, Any]) -> Dict[str, Any]:
    """只依赖 (title, summary) + 当前词表的派生字段。

    ★ 单独抽出来的原因：累积档里掉出时间窗的记录不会再次被抓取，
    若不在落盘时用**当前**词表重算，它们会永远停在首次入库时的口径上。
    ★ 同时**重清洗 summary**：解析器的修复（例如 strip_tags 的反转义顺序）
    必须能回溯生效，否则历史记录会一直带着旧解析器的产物
    （实测：Google News 描述里的 `&lt;a href=…&gt;` 曾长期残留在正文中）。
    """
    title = rec.get("title", "") or ""
    summary = strip_tags(rec.get("summary", "") or "")
    rec["summary"] = summary
    text = f"{title} . {summary}"
    rel = relevance_of(title, summary)
    sigs = signals_of(text, text)
    return {
        "relevance": rel["score"], "relevance_parts": rel,
        "signals": sigs, "signal_buckets": [s["bucket"] for s in sigs],
        "claim_kinds": claims_of(text), "anchors": anchors_of(text),
        "lang": "fa" if has_persian(title) else "en",
        "cfg_ver": CFG_VER,
    }


def reclassify(rec: Dict[str, Any]) -> None:
    """按当前 outlet_map 重判当事方（聚合器源的历史记录同样需要回填）。"""
    src = SOURCE_BY_ID.get(rec.get("source_id") or "")
    if src:
        resolve_outlet(rec, src)


def backfill_derived(records: List[Dict[str, Any]]) -> int:
    """用当前词表重算「指纹过期」的记录。返回被回填的条数。"""
    n = 0
    for r in records:
        if r.get("cfg_ver") == CFG_VER:
            continue
        r.update(derived_fields(r))
        reclassify(r)
        n += 1
    return n


def enrich(rec: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    text = f"{rec.get('title','')} . {rec.get('summary','')}"
    rec.update({
        "party": src.get("party", "third"),
        "channel": src.get("channel", ""),
        "publisher": src.get("publisher", ""),
        "tier": src.get("tier", ""),
        "tier_note": src.get("tier_note", ""),
    })
    rec.update(derived_fields(rec))
    resolve_outlet(rec, src)
    return rec


def is_displayable(rec: Dict[str, Any]) -> bool:
    """展示闸门 = 命中战备信号 **且** 锚定到本主题。

    两道闸门缺一不可：
      ① 信号 —— 必须有战备语义的词（`withdrawal`、`targeted` 这类泛化词已从词表剔除）
      ② 主题锚定 —— 必须再落到当事方/地区词(anchor)或军事领域词(domain)，
         或者该源本身就是国防主题源(topic_anchored)。
    少了②就会出现「GAO 的《印第安人住房》被算成美方战果声明」这种荒唐结果，
    而日志、条数、状态码全部正常。

    展示与统计**共用本函数**，避免「正文看不到、却进了计数」。
    """
    if not rec.get("signals"):
        return False
    parts = rec.get("relevance_parts") or {}
    if parts.get("anchor", 0) >= 1 or parts.get("domain", 0) >= 1:
        return True
    src = SOURCE_BY_ID.get(rec.get("source_id") or "") or {}
    return bool(src.get("topic_anchored"))


def classify_folded(records: List[Dict[str, Any]]
                    ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """把未通过展示闸门的记录分成三类，返回 (逐条明细, **全量**分类计数)。

    ★ 两者必须分开：明细可以截断展示，**计数绝不能被截断**。
    实测踩过的坑：报告只统计了「前 400 条明细」的分布，却把结果当全量写出来 ——
    「864 条折叠」被拆成「252 + 142 + 6 = 400」，**加总都不自洽**，
    而最需要警惕的「有战备信号但未锚定」（词表/锚点收紧过头的唯一线索）
    被从真实的 9 条压成 6 条。程序无报错、数字看着合理，但含义错了 2.4 倍。

    ★ 三类语义：
      - `no-readiness-signal`：命中主题词但没有战备语义 → 正常的噪声
      - `off-topic`：主题词与战备词都没有 → 真无关
      - `no-topic-anchor`：**有战备信号**却没锚定本主题 → 最可疑，可能是
        锚点词表过窄把真信号挡在外面，必须逐条可复核
    """
    items: List[Dict[str, Any]] = []
    brk: Dict[str, int] = {}
    for r in records:
        if is_displayable(r):
            continue
        parts = r.get("relevance_parts") or {}
        if r.get("signals"):
            reason = "no-topic-anchor"
        elif parts.get("anchor", 0) or parts.get("domain", 0):
            reason = "no-readiness-signal"
        else:
            reason = "off-topic"
        brk[reason] = brk.get(reason, 0) + 1
        items.append({"reason": reason, "source_id": r.get("source_id"),
                      "party": r.get("party"), "title": r.get("title", "")[:120],
                      "link": r.get("link", ""), "relevance": r.get("relevance"),
                      "date": date_iso(r.get("published"))})
    return items, brk


# ================================================================ 采集
def collect_source(src: Dict[str, Any], *, quiet: bool = False,
                   from_file: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    sid = src["id"]
    status: Dict[str, Any] = {
        "id": sid, "party": src.get("party"), "channel": src.get("channel"),
        "publisher": src.get("publisher"), "tier": src.get("tier"),
        "enabled": bool(src.get("enabled")), "state": "unknown",
        "entries": 0, "latest": None, "age_days": None,
        "http_status": None, "server": "", "note": src.get("note", ""),
        "url_used": src.get("url"), "tls": "verified", "attempts": 0,
    }
    if not src.get("enabled"):
        status["state"] = "disabled"
        return [], status

    tls_verify = True
    tls_cfg = src.get("tls") or {}
    if tls_cfg.get("verify") is False:
        if STRICT_TLS:
            status["state"] = "tls-refused"
            status["note"] = "已经 --strict-tls 拒绝：该源需要关闭证书校验，本次跳过。" \
                             + (" " + tls_cfg.get("note", "") if tls_cfg.get("note") else "")
            return [], status
        tls_verify = False
        status["tls"] = "unverified"
        TLS_DOWNGRADED.append({"id": sid, "publisher": src.get("publisher", ""),
                               "url": src.get("url", ""),
                               "note": tls_cfg.get("note", "")})

    raw: Optional[bytes] = None
    bodies: List[bytes] = []
    used = src.get("url")
    if from_file:
        cands = [os.path.join(from_file, f"{sid}.xml"),
                 os.path.join(from_file, f"{sid}.json")]
        for c in cands:
            if os.path.exists(c):
                with open(c, "rb") as f:
                    raw = f.read()
                used = c
                status["state"] = "ok-from-file"
                break
        if raw is None:
            status["state"] = "no-local-file"
            return [], status
        bodies = [raw]
    else:
        urls = [src.get("url")] + list(src.get("url_alternates") or [])
        urls = [u for u in urls if u]
        if src.get("merge_alternates"):
            # 多个检索式**全部抓取并合并** —— 聚合器单条检索式覆盖面有限，
            # 实测 centcom 单用 site: 检索在 14 天窗口里只拿到 2 条。
            for u in urls:
                r = http_get(u, json_mode=(src.get("kind") == "json"),
                             tls_verify=tls_verify,
                             timeout=int(REQ.get("timeout", 30)),
                             retries=int(REQ.get("retries", 3)),
                             backoff=float(REQ.get("retry_backoff_sec", 2.0)))
                status["attempts"] += r.get("attempts", 0)
                if r["status"] == 200 and r["body"]:
                    bodies.append(r["body"])
                    status.setdefault("urls_ok", []).append(u)
                else:
                    status.setdefault("urls_fail", []).append(
                        f"{u} → {r['status'] or r['err']}")
            if not bodies:
                status["state"] = "fail-http"
                return [], status
            status["http_status"] = 200
            used = urls[0]
        else:
            res = None
            for u in urls:
                r = http_get(u, json_mode=(src.get("kind") == "json"),
                             tls_verify=tls_verify,
                             timeout=int(REQ.get("timeout", 30)),
                             retries=int(REQ.get("retries", 3)),
                             backoff=float(REQ.get("retry_backoff_sec", 2.0)))
                res = r
                used = u
                status["attempts"] += r.get("attempts", 0)
                status["http_status"] = r["status"]
                status["server"] = r["server"]
                if r["status"] == 200 and r["body"]:
                    break
            if res is None or res["status"] is None:
                status["state"] = "fail-network"
                status["note"] = (status["note"] + " | " if status["note"] else "") + \
                                 f"网络/TLS 层失败：{(res or {}).get('err')}"
                return [], status
            if res["status"] != 200:
                status["state"] = "fail-http"
                return [], status
            bodies = [res["body"]]

    text = bodies[0].decode("utf-8", "replace")
    if not any(strip_tags(b.decode("utf-8", "replace")) for b in bodies):
        status["state"] = "ok-empty"
        return [], status

    # --- 取条目（多检索式合并时逐块解析后拼接）
    items: List[Dict[str, Any]] = []
    if src.get("kind") == "json":
        try:
            j = json.loads(text)
        except json.JSONDecodeError:
            status["state"] = "fail-parse"
            return [], status
        arr = j
        jp = src.get("json_path")
        if jp:
            for part in jp.split("."):
                arr = (arr or {}).get(part) if isinstance(arr, dict) else None
        if not isinstance(arr, list):
            status["state"] = "fail-parse"
            return [], status
        for it in arr:
            title = str(it.get("title") or "").strip()
            if not title:
                continue
            items.append({"title": title,
                          "link": str(it.get("url") or it.get("link") or ""),
                          "summary": str(it.get("summary") or
                                         it.get("description") or ""),
                          "published": str(it.get("publishedDate") or
                                           it.get("date") or
                                           it.get("publicationDate") or ""),
                          "item_source": ""})
    else:
        for b in bodies:
            items.extend(parse_feed(b.decode("utf-8", "replace")))

    status["entries"] = len(items)

    # --- 同题多素材折叠：一条报道拆成多个影像条目时只留信息量最大的一条
    #  （保留最长 description，因为信号抽取只看文本）；
    #  并把「[Image 4 of 5]」这类序号后缀从展示标题里去掉，否则读者以为是 5 条。
    if src.get("collapse_asset_variants", True):
        order: List[str] = []
        best: Dict[str, Dict[str, str]] = {}
        for it in items:
            k = title_key(it["title"])
            if k not in best:
                order.append(k)
                best[k] = it
            elif len(it.get("summary") or "") > len(best[k].get("summary") or ""):
                best[k] = it
        n_before = len(items)
        items = [best[k] for k in order]
        if n_before != len(items):
            status["asset_collapsed"] = n_before - len(items)
        # 序号后缀一律去掉：它对读者没有信息量，留着会让人误以为有 5 条不同报道
        for it in items:
            cleaned = ASSET_SUFFIX_RE.sub("", it["title"]).strip()
            if cleaned:
                it["title"] = cleaned

    # --- 标题闸门（用于聚合器源，防止 site: 检索带进无关条目）
    inc = src.get("title_include") or []
    exc = src.get("title_exclude") or []
    if inc:
        kept = []
        for it in items:
            t = norm_key(it["title"])
            ok = any(norm_key(x) in t for x in inc)
            bad = any(norm_key(x) in t for x in exc) if exc else False
            if ok and not bad:
                kept.append(it)
            else:
                GATE_AUDIT.setdefault(sid, []).append(
                    {"reason": "title-gate", "title": it["title"][:110]})
        status["gated_out"] = len(items) - len(kept)
        items = kept

    # --- 窗口过滤 + 富化
    if not BACKFILL:
        cutoff = now_utc() - timedelta(hours=LOOKBACK_HOURS)
    else:
        hs = (CFG.get("history") or {}).get("start")
        cutoff = (datetime.fromisoformat(hs).replace(tzinfo=timezone.utc)
                  if hs else now_utc() - timedelta(days=3650))

    recs: List[Dict[str, Any]] = []
    latest_dt: Optional[datetime] = None
    seen_titles: set = set()
    # ★ 源内条数上限。`PER_SOURCE_MAX <= 0` 表示**不限**（默认值，见 config）。
    #  历史 bug：这里曾写成 `items[:max(PER_SOURCE_MAX, len(items))]`，
    #  而 `max(60, len)` 恒等于 `len` → **上限完全失效**，config 里的
    #  `per_source_max: 60` 是个从没生效过的承诺（实测 centcom-gnews 解析 300 条全采）。
    #  为什么默认仍取「不限」：截断会**静默丢弃窗口内的记录**，与项目
    #  「合并源=窗口内全部」原则直接冲突。要限制就必须让截断在报告里可见
    #  （见 status['kept_from'] 与 §2 的「解析/产出」两列）。
    if PER_SOURCE_MAX > 0 and len(items) > PER_SOURCE_MAX:
        status["kept_from"] = len(items)
        items = items[:PER_SOURCE_MAX]
    for it in items:
        # 同一报道会以不同聚合器跳转链接出现多次，链接不同但标题相同 →
        # 按归一化标题再去一次，否则交叉矩阵里同一条会重复出现
        tnorm = title_key(it["title"])
        if tnorm in seen_titles:
            continue
        seen_titles.add(tnorm)
        dt = parse_date(it["published"])
        if dt and (latest_dt is None or dt > latest_dt):
            latest_dt = dt
        if dt and dt < cutoff:
            continue
        rid = entry_id(sid, it["link"], it["title"], it["published"])
        # ★ 无日期条目：日期**不能**留原始字符串进 `published`。
        #   两个后果都是静默的：① 表格里印出 `Thu, 01 Ja` 这类半截垃圾；
        #   ② 之后每次重跑，它都算不出年龄 → 永远过不了窗口判定 → 永久滞留在快照里。
        #   统一置空 + `date_unknown=True`，让下游按「已知无日期」显式处理。
        undated = dt is None
        if undated:
            status["undated"] = status.get("undated", 0) + 1
        rec = {
            "id": rid, "source_id": sid, "kind": src.get("kind", "rss"),
            "title": it["title"], "link": it["link"],
            "published": dt.astimezone(timezone.utc).isoformat() if dt else "",
            "date_unknown": undated,
            "summary": it["summary"][:1500],
            "item_source": it.get("item_source", ""),
            "tls": "unverified" if not tls_verify else "verified",
            "fetched_at": now_utc().isoformat(timespec="seconds"),
        }
        recs.append(enrich(rec, src))

    status["latest"] = latest_dt.astimezone(timezone.utc).isoformat() if latest_dt else None
    # ★ 与 `entries`（feed 里**解析出**的条目数）区分：`kept` 是**窗口过滤后
    #  实际产出**的记录数。两者差距巨大（实测 centcom-gnews 解析 300 → 产出远少），
    #  只在 §2 印 `entries` 会让读者以为该源贡献了 300 条。
    status["kept"] = len(recs)
    if latest_dt:
        status["age_days"] = (now_utc() - latest_dt).days
    if status["entries"] == 0:
        status["state"] = "ok-empty"
    elif not recs:
        status["state"] = "ok-out-of-window"
    elif status["age_days"] is not None and status["age_days"] > 14:
        status["state"] = "ok-stale"
    else:
        status["state"] = "ok"
    return recs, status


# ================================================================ 交叉验证
def iso_week(dt: datetime) -> str:
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def party_group(p: str) -> str:
    """归组：只有「美方」与「伊方(官方/体制内)」是当事方；
    流亡/反对派媒体与其它国际媒体一律归为旁证，**不**计入当事方表态。"""
    return p if p in ("us", "iran") else "third"


def _brief(rs: List[Dict[str, Any]], n: int = 3) -> List[Dict[str, str]]:
    """把「触发某判定的记录」压成可展示的短凭证。"""
    out = []
    for r in rs[:n]:
        out.append({
            "title": truncate(r.get("title", ""), 96),
            "link": r.get("link", ""),
            "tier": r.get("tier", ""),
            "publisher": r.get("publisher", ""),
            "claim_kinds": ",".join(r.get("claim_kinds") or []),
        })
    return out


def build_crosscheck(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """议题锚点 × 周：把双方同期表态机械配对。

    局限（已写进产出）：程序只做「同一议题词 + 同一周出现」的机械配对，
    **不做语义一致性判断**。`both_mentioned` 只说明双方都在讲这件事，
    **不等于**双方说法一致，更不等于该事实成立。
    """
    anchors = {a["id"]: a for a in CROSS_ANCHORS}
    res: Dict[str, Any] = {"anchors": {}, "stats": {}, "pairs": []}
    # ★ 只用通过展示闸门的记录 —— 计算口径必须与展示口径一致，
    # 否则会出现「正文里看不到的记录，却参与了结论计数」。
    usable = [r for r in records if is_displayable(r)]
    # ★ 两侧词表**必须等宽**，否则 denial_target 会系统性偏向词表更宽的一方。
    #  历史问题：美方侧写成 `u\.?s\.?\s*(?:navy|military|forces)`，
    #  于是 "US claims" / "US destroyers" 里的 US **匹配不上**；
    #  而伊朗侧一个 `iran` 词根就覆盖 Iran / Iranian / Iran's —— 一宽一窄，
    #  结果几乎所有含双方的句子都被判成「被否认方是伊朗」。
    re_iran_side = re.compile(
        r"(?:\biran|\birgc\b|\btehran|\brevolutionary guard\b|ایران|سپاه)", re.I)
    re_us_side = re.compile(
        r"(?:\bcentcom\b|\bpentagon\b|\bamerican\b|\bwashington\b|آمریکا"
        # 局部关闭 re.I：只认大写 US／U.S.，避免把代词 "tell us" 当成美方
        r"|(?-i:\bU\.?S\.?\b))", re.I)
    # 否认动词本身：用它定位「谁的主张被否认」。
    # 语法上，否认动词**之后**的名词短语才是被否认的主张来源
    # （"CENTCOM denies **IRGC claims** of ..."）。
    re_denial_word = re.compile(
        r"(?:denies|denied|deny|rejects?|rejected|dismiss\w*|refut\w*|"
        r"disput\w*|baseless|unfounded|is false|not true)", re.I)

    def denial_target(rec: Dict[str, Any]) -> str:
        """第三方记录在替谁否认：被否认的是伊朗的主张→'iran'；美方的→'us'。

        ★ 判据必须是**否认动词之后的实体**，不能是整句的实体。
        实测踩坑：整句「伊朗优先」匹配时，`Iran denies US claims that ...`
        会被整句里的 `Iran` 判成「否认伊朗」，而它恰恰是**伊方在否认美方** ——
        方向反了，于是对撞配对也反了（见下方 contradiction 条件）。

        ★★ 找不到就返回 `""`，**绝不做「整句兜底」**。
        实测假阳性：`"…as Iran denied any involvement in the country's revived
        civil war"` —— 否认动词之后 140 字符内没有任何一方实体，
        若兜底扫整句就会因出现 `Iran` 而判成「被否认方是伊朗」，
        于是一条**也门内战**的报道被算成「第三方否认伊朗的战果宣称」，
        与伊方在 `missile` 锚点下的声明凑成一个**根本不存在的对撞**。
        注意 `Iran` 在这里是否认的**施动者（主语）**，不是被否认方 ——
        所以「往前看」同样是错的，唯一正确的位置是**否认动词之后**。
        代价：被否认方隐含不说的记录（"the claim was rejected as baseless"）
        会漏检。这是**刻意的保守方向**：宁可漏检，不可假阳性对撞。
        """
        txt = (rec.get("title") or "") + " " + (rec.get("summary") or "")
        m = re_denial_word.search(txt)
        if not m:
            return ""
        seg = txt[m.end(): m.end() + 140]
        mi, mu = re_iran_side.search(seg), re_us_side.search(seg)
        if mi and mu:
            return "iran" if mi.start() < mu.start() else "us"
        if mi:
            return "iran"
        if mu:
            return "us"
        return ""

    def pack(rs: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
        return [{"title": r.get("title", ""), "link": r.get("link", ""),
                 "publisher": r.get("publisher", ""), "tier": r.get("tier", ""),
                 "signals": [x.get("phrase", "") for x in r.get("signals", [])[:3]],
                 "claim_kinds": r.get("claim_kinds", []),
                 "context": (r["signals"][0].get("context", "")
                             if r.get("signals") else "")}
                for r in rs[:n]]

    for aid, a in anchors.items():
        weeks: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        for r in usable:
            if aid not in (r.get("anchors") or []):
                continue
            dt = parse_date(r.get("published") or "")
            if not dt:
                continue
            slot = weeks.setdefault(iso_week(dt),
                                    {"us": [], "iran": [], "third": []})
            slot[party_group(r.get("party", "third"))].append(r)
        rows = []
        for wk in sorted(weeks):
            slot = weeks[wk]
            us, ir, th = slot["us"], slot["iran"], slot["third"]
            trig: Dict[str, List[Dict[str, str]]] = {}
            if not us and not ir:
                cls = "third_only"
            elif us and not ir:
                cls = "us_only"
            elif ir and not us:
                cls = "iran_only"
            else:
                au_l = [r for r in us if "action_claim" in (r.get("claim_kinds") or [])]
                ai_l = [r for r in ir if "action_claim" in (r.get("claim_kinds") or [])]
                du_l = [r for r in us if "denial" in (r.get("claim_kinds") or [])]
                di_l = [r for r in ir if "denial" in (r.get("claim_kinds") or [])]
                # ★ 第三方转述的否认也要算 —— 实测这正是最典型的一类矛盾：
                #   「CENTCOM rejects Iranian claim that IRGC struck two US destroyers」
                #   这条既不是美方自己的发布、也不是伊方的，但它是标准的口径对撞。
                td_ir_l = [r for r in th if "denial" in (r.get("claim_kinds") or [])
                           and denial_target(r) == "iran"]
                td_us_l = [r for r in th if "denial" in (r.get("claim_kinds") or [])
                           and denial_target(r) == "us"]
                au, ai = bool(au_l), bool(ai_l)
                du, di = bool(du_l), bool(di_l)
                td_iran, td_us = bool(td_ir_l), bool(td_us_l)
                # ★ 配对方向必须一致：**谁的主张**被**谁**否认。
                #   · 美方主张 `au` ← 被伊方 `di` 或第三方 `td_us` 否认
                #   · 伊方主张 `ai` ← 被美方 `du` 或第三方 `td_iran` 否认
                #  历史 bug：这里写成了 `(au and (di or td_iran)) or
                #  (ai and (du or td_us))` —— 两个第三方变量**挂反了**，
                #  于是「美方宣称 + 第三方否认伊朗的说法」这种**并不矛盾**的组合
                #  会被判成 contradiction（假阳性对撞）。
                if (au and (di or td_us)) or (ai and (du or td_iran)):
                    cls = "contradiction"
                elif au and ai:
                    cls = "mutual_assert"
                elif du and di:
                    cls = "mutual_denial"
                else:
                    cls = "both_mentioned"
                # ★ 留下「是哪几条把这一组判成对撞」的凭证。
                #  只给一个机械标签、不给出触发它的具体条目，读者无法复核 ——
                #  而复核恰恰是这类跨方对照报告唯一的价值来源。
                trig = {
                    "us_assert": _brief(au_l), "iran_denial": _brief(di_l),
                    "third_denial_vs_iran": _brief(td_ir_l),
                    "iran_assert": _brief(ai_l), "us_denial": _brief(du_l),
                    "third_denial_vs_us": _brief(td_us_l),
                }
                trig = {k: v for k, v in trig.items() if v}
            rows.append({"week": wk, "cls": cls, "us": len(us), "iran": len(ir),
                         "third": len(th), "us_items": us, "iran_items": ir,
                         "third_items": th, "trigger": trig})
            if cls in ("contradiction", "mutual_assert", "mutual_denial",
                       "both_mentioned"):
                # 第三方旁证优先摆**带否认**的那些 —— 它们才是把这一组判成
                # contradiction 的直接依据；按原序取前 3 条会把无关的舰队动态排到前面。
                th_ranked = sorted(
                    th, key=lambda r: "denial" not in (r.get("claim_kinds") or []))
                res["pairs"].append({"anchor": aid, "anchor_label": a["label"],
                                     "week": wk, "cls": cls,
                                     "trigger": trig,
                                     "us": pack(us, 4), "iran": pack(ir, 4),
                                     "third": pack(th_ranked, 3)})
        if rows:
            res["anchors"][aid] = {"label": a["label"], "rows": rows}
            st = res["stats"].setdefault(aid, {})
            for r in rows:
                st[r["cls"]] = st.get(r["cls"], 0) + 1
    return res


def build_weekly(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按周 × 当事方统计**公开战备信号活跃度**（启发式）。

    措辞刻意用「活跃度」而非「强度」：本函数统计的是「本监测收录到的**公开表态**
    里命中了多少信号词」，衡量的是**公开话语的活跃程度**，不是任何一方真实战备
    水平的强弱。真实战备（战备等级、弹药库存实数、损失明细）大多不公开，
    因此这个分值对「真实强度」是零信息，对「公开发声热度」才有效。

    ★ 迭代**完整周区间**，而不是「只有数据的那些周」——
    后者会让「未收录」这个语义变成死代码（永远不触发），
    读者也就看不到「这段我们没在收」，只会看到一片空白。
    早于首条记录所在周的周一律 covered=False，渲染成 `—`（空 ≠ 零）。
    """
    buckets = list(SIG_CFG.keys())
    dates = [d for d in (parse_date(r.get("published") or "") for r in records) if d]
    if not dates:
        return []

    weeks: Dict[str, Dict[str, Any]] = {}
    for r in records:
        if not is_displayable(r):
            continue
        dt = parse_date(r.get("published") or "")
        if not dt:
            continue
        wk = iso_week(dt)
        slot = weeks.setdefault(wk, {p: {"docs": 0, "weight": 0,
                                         "buckets": {b: 0 for b in buckets}}
                                     for p in ("us", "iran", "third")})
        s = slot[party_group(r.get("party", "third"))]
        s["docs"] += 1
        s["weight"] += sum(x.get("weight", 0) for x in r.get("signals", []))
        for x in r.get("signals", []):
            s["buckets"][x["bucket"]] = s["buckets"].get(x["bucket"], 0) + 1

    # 区间起点：优先用配置的历史起点（这样「历史起点→首条记录」之间的空档
    # 会如实显示成「未收录」，而不是被静默跳过）
    hs = (CFG.get("history") or {}).get("start")
    start_dt = dates[0]
    if hs:
        try:
            hd = datetime.fromisoformat(hs).replace(tzinfo=timezone.utc)
            start_dt = min(start_dt, hd)
        except ValueError:
            pass
    first_data_week = iso_week(min(dates))
    end_dt = max(dates)

    cur = start_dt - timedelta(days=start_dt.weekday())
    end = end_dt - timedelta(days=end_dt.weekday())
    out: List[Dict[str, Any]] = []
    while cur <= end:
        wk = iso_week(cur)
        covered = wk >= first_data_week
        out.append({
            "week": wk,
            "week_start": cur.strftime("%Y-%m-%d"),
            "covered": covered,
            "parties": weeks.get(wk) or ({p: {"docs": 0, "weight": 0,
                                              "buckets": {b: 0 for b in buckets}}
                                          for p in ("us", "iran", "third")}
                                         if covered else None),
        })
        cur += timedelta(days=7)
    return out


# ================================================================ 产出
def write_jsonl(path: str, rows: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path: str) -> List[Dict[str, Any]]:
    """读 jsonl，坏行跳过（不因一行损坏丢掉整档）。"""
    out: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def refresh_gate_audit(records: List[Dict[str, Any]]) -> Dict[str, int]:
    """（重新）填充 GATE_AUDIT 的折叠部分，返回全量分类计数。

    ★ 抽成函数是因为「--render-only」也要走这一步：如果只重渲染却忘了重算
    折叠统计，报告会印出上一次运行的分类数 —— 与本次快照对不上。
    """
    items, brk = ([], {}) if SHOW_ALL else classify_folded(records)
    GATE_AUDIT["_folded_breakdown"] = brk
    GATE_AUDIT["_folded_total"] = len(items)
    _sample = items[:FOLD_SAMPLE_LIMIT]
    # 「有战备信号但未锚定」不受抽样上限约束（它是词表问题的唯一线索）
    _extra = [x for x in items[FOLD_SAMPLE_LIMIT:]
              if x.get("reason") == "no-topic-anchor"]
    GATE_AUDIT["_display_folded"] = _sample + _extra
    return brk


def dedup_by_title(records: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """按**标题键**跨源合并同一条报道，保留信源等级最高的一条。

    ★ 为什么必须在合并阶段也做一次，而不只在抓取时做：
    聚合器(Google News)会把 DVIDS 的图片条目当独立结果返回，标题是
    「… [Image 5 of 10]」「… [Image 1 of 10]」；直接源 dvids-afcent 给出的是
    去掉后缀的同一条。抓取期去重只能处理**同一源内**，跨源要在这里合。
    另外它还能清掉历次运行遗留的重复（旧快照里的条目会随 merge 被带回来）。
    保留较高 tier：T1 官方一手优先于 T3 聚合器转述。
    """
    best: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for r in records:
        k = title_key(r.get("title", ""))
        if not k:
            k = r.get("id", "")
        cur = best.get(k)
        if cur is None:
            order.append(k)
            best[k] = r
            continue
        rank = {"T1": 0, "T2": 1, "T3": 2, "T4": 3}
        newer = rank.get(r.get("tier", ""), 9) < rank.get(cur.get("tier", ""), 9)
        longer = len(r.get("summary") or "") > len(cur.get("summary") or "")
        if newer or (rank.get(r.get("tier", ""), 9)
                     == rank.get(cur.get("tier", ""), 9) and longer):
            also = sorted(set((cur.get("also_seen_in") or [])
                              + [cur.get("source_id", ""), r.get("source_id", "")]))
            r["also_seen_in"] = [x for x in also if x and x != r.get("source_id")]
            best[k] = r
        else:
            also = set((cur.get("also_seen_in") or []) + [r.get("source_id", "")])
            cur["also_seen_in"] = sorted(x for x in also
                                         if x and x != cur.get("source_id"))
    out = [best[k] for k in order]
    return out, len(records) - len(out)


def window_cutoff() -> Optional[datetime]:
    """当前窗口起点（与 `collect_source` 用同一套规则）。"""
    if BACKFILL:
        hs = (CFG.get("history") or {}).get("start")
        if hs:
            try:
                return datetime.fromisoformat(hs).replace(tzinfo=timezone.utc)
            except ValueError:
                return None
        return None
    return now_utc() - timedelta(hours=max(1, LOOKBACK_HOURS))


def merge_records(path: str, records_all: List[Dict[str, Any]],
                  records_new: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """★ 合并源必须是 records_all（窗口内全部），不是 records_new（仅新增）。
    只测「同日重跑」会完全掩盖跨天丢窗口内历史的问题。

    ★★ 但「保留旧快照记录」必须**有边界**，否则窗口会无限膨胀：
      保留旧记录的理由是「它仍在窗口内、却已从 feed 列表里掉出去，抓不回来」；
      一旦它**掉出窗口**，保留就变成永久滞留 —— 实测一次日期解析缺陷把
      两个月前的条目放进来后，它们会**永远留在当天快照里**：重跑也清不掉，
      因为重跑时它们既不在新抓取结果里（已被窗口过滤）、又已在旧快照里。
      修剪只作用于**旧快照**记录；本轮抓取结果本身已按窗口过滤，直接采纳。

    ★★ 无日期记录是这套规则唯一的**例外口子**，必须单独堵：
      无日期 ⇒ 算不出年龄 ⇒ 永远不满足「超出窗口」⇒ 只要进过一次就**永久滞留**。
      策略：旧快照里带 `date_unknown` 的一律不跨轮携带；若它仍在 feed 里，
      本轮抓取会再收进来（它是当前条目，只是没有日期）。详见下方行内注释。
    """
    merged: List[Dict[str, Any]] = []
    index: Dict[str, int] = {}
    cutoff = window_cutoff()
    pruned = 0
    stale_undated = 0
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    old = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if cutoff is not None:
                    _dt = parse_date(old.get("published") or "")
                    if _dt is not None and _dt < cutoff:
                        pruned += 1
                        continue
                    # ★★ 无日期记录**只给一轮展示机会**。
                    #   理由是它与窗口规则的对称性：能保留在快照里的旧记录，
                    #   前提是「仍属窗口内、却已从 feed 掉出去、抓不回来」。
                    #   无日期记录既无法判定是否在窗口内，也（因为无日期）
                    #   **永远不会**被上一条规则清理 —— 只要进过一次快照就永久滞留。
                    #   因此：旧快照里带 `date_unknown` 的，本轮一律清掉；
                    #   若它**仍在 feed 里**，本轮抓取结果会原样再收进来（那是对的，
                    #   它确实是当前条目，只是没有日期）；只有真正消失的才会被清走。
                    if _dt is None and old.get("date_unknown"):
                        stale_undated += 1
                        continue
                index[old["id"]] = len(merged)
                merged.append(old)
    if pruned:
        log(f"窗口修剪：{pruned} 条旧快照记录已掉出窗口，不再保留"
            f"（累积总档 ALL-records.jsonl 中仍有留档）")
    if stale_undated:
        log(f"无日期清理：{stale_undated} 条旧记录既无日期、又已不在本轮抓取结果中，"
            f"不再跨轮携带（累积总档中仍有留档）")
    existing_ids = set(index)
    for r in (records_all or records_new):
        if r["id"] in index:
            merged[index[r["id"]]] = r      # 覆盖而非丢弃，让解析器修复能回溯生效
        else:
            index[r["id"]] = len(merged)
            merged.append(r)
    fresh = sum(1 for r in records_new if r["id"] not in existing_ids)
    merged, n_dedup = dedup_by_title(merged)
    if n_dedup:
        log(f"标题键合并：{n_dedup} 条同题重复（跨源/历史遗留）已归并")
    return merged, fresh


def write_all_accumulated(path: str, records: List[Dict[str, Any]],
                          *, backfill: bool = True,
                          run_day: Optional[str] = None) -> int:
    """累积总档：按 id 去重、永不淘汰、带 first_seen。

    ★ first_seen 用**本次运行日**（main 启动时确定的 day），不用写入时刻的墙钟——
      跨午夜运行时墙钟已翻天，会让 first_seen 晚于快照日期、破坏不变量。

    ★ 落盘前对**全档**做一次词表回填：
    窗口外的记录不会再被抓到，如果不回填，档案里会同时存在
    「旧词表算的」和「新词表算的」两种派生字段 —— 趋势与交叉验证
    会拿两套口径混着算，而输出看起来完全正常。
    """
    global BACKFILLED
    old: Dict[str, Dict[str, Any]] = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                    old[o["id"]] = o
                except json.JSONDecodeError:
                    continue
    today = run_day or datetime.now().strftime("%Y-%m-%d")
    for r in records:
        prev = old.get(r["id"])
        if prev and prev.get("first_seen"):
            r["first_seen"] = prev["first_seen"]   # first_seen 不被覆盖
        else:
            r["first_seen"] = today
        old[r["id"]] = r
    rows = sorted(old.values(), key=lambda x: x.get("published") or "")
    BACKFILLED = backfill_derived(rows) if backfill else 0
    write_jsonl(path, rows)
    return len(rows)


STATUS_ICON = {"ok": "✅", "ok-stale": "🟡", "ok-empty": "⚪",
               "ok-out-of-window": "⚪", "disabled": "⛔", "fail-http": "❌",
               "fail-network": "❌", "fail-parse": "❌", "tls-refused": "⛔",
               "ok-from-file": "📁", "no-local-file": "⚪", "unknown": "❓"}


def tier_badge(t: str) -> str:
    return {"T1": "T1🟢", "T2": "T2🟡", "T3": "T3🟠", "T4": "T4🔴"}.get(t, t or "—")


def party_badge(p: str) -> str:
    return {"us": "🇺🇸 美方", "iran": "🇮🇷 伊方官方",
            "iran_opp": "🟣 伊流亡/反对派", "third": "🌐 第三方"}.get(p, p or "—")


def truncate(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def bar(n: int, scale: int = 1, width: int = 20) -> str:
    if not n:
        return ""
    k = max(1, min(width, round(n / max(1, scale))))
    return "█" * k


# ================================================================ 关注域 / 前瞻 / 实体
ZONES: List[Dict[str, Any]] = []

# ★ 升级前瞻桶：共同特征是「**尚未发生**」—— 未来时、条件式、临战式。
#   它们是**意向指标**，不是事实指标；报告必须这样标注，否则读者会把
#   「将会打击」读成「已经打击」。
FORESIGHT_BUCKETS = ("future_threat", "condition_ultimatum", "imminent_indicator",
                     "mobilization", "escalation_ladder")


def zone_of_bucket(bucket: str) -> str:
    return (SIG_CFG.get(bucket) or {}).get("zone") or "readiness"


def zone_name(zid: str) -> str:
    for z in ZONES:
        if z["id"] == zid:
            return z["name"]
    return zid


def zone_order(zid: str) -> int:
    for z in ZONES:
        if z["id"] == zid:
            return int(z.get("order", 99))
    return 99


def build_zone_summary(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按关注域汇总。

    ★ 只统计**通过展示闸门**的记录（`is_displayable`）——与正文口径完全一致，
      避免出现「正文看不到、却进了计数」。这是本项目反复踩过的那类错误。
    """
    out: List[Dict[str, Any]] = []
    disp = [r for r in records if is_displayable(r)]
    for z in sorted(ZONES, key=lambda x: int(x.get("order", 99))):
        zid = z["id"]
        rs = [r for r in disp
              if any(zone_of_bucket(s["bucket"]) == zid for s in r.get("signals", []))]
        parties: Dict[str, int] = {}
        buckets: Dict[str, int] = {}
        for r in rs:
            parties[r.get("party") or "third"] = parties.get(r.get("party") or "third", 0) + 1
            for s in r.get("signals", []):
                if zone_of_bucket(s["bucket"]) == zid:
                    buckets[s["bucket"]] = buckets.get(s["bucket"], 0) + 1
        out.append({
            "id": zid, "name": z["name"], "desc": z.get("desc", ""), "order": z.get("order", 99),
            "n": len(rs), "parties": parties, "buckets": buckets,
            "records": sorted(rs, key=lambda x: x.get("published") or "", reverse=True),
        })
    return out


def foresight_score(rec: Dict[str, Any]) -> int:
    """前瞻强度 = Σ(桶权重 × 命中次数)，只看前瞻桶。"""
    return sum(int(s.get("weight", 1)) * int(s.get("n_hits", 1))
               for s in rec.get("signals", []) if s["bucket"] in FORESIGHT_BUCKETS)


def build_foresight(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """升级前瞻指标（未来 1–4 周）。

    ★ 用途与局限必须同时写进报告：
      - 它统计的是「**谁在公开说要做什么**」，是意向指标；
      - **意向 ≠ 能力 ≠ 会发生**。最需要警惕的漏报恰好相反：
        真正要动手的一方往往**停止**公开发声（战略突然沉默），
        因此本表**不能**读成「分低 = 不会升级」。
    """
    disp = [r for r in records if is_displayable(r)]
    rs = [r for r in disp if foresight_score(r) > 0]
    rs.sort(key=lambda r: (-foresight_score(r), r.get("published") or ""))
    by_bucket: Dict[str, int] = {}
    by_party: Dict[str, int] = {}
    for r in rs:
        by_party[r.get("party") or "third"] = by_party.get(r.get("party") or "third", 0) + 1
        for s in r.get("signals", []):
            if s["bucket"] in FORESIGHT_BUCKETS:
                by_bucket[s["bucket"]] = by_bucket.get(s["bucket"], 0) + 1
    return {"records": rs, "by_bucket": by_bucket, "by_party": by_party,
            "total": len(rs), "window_n": len(disp)}


# ★ 实体抽取：一律加「字母/数字边界」，否则短专名会嵌进普通单词。
#   实测过的同类事故：`Rota` 命中 `Rotational`、`America` 命中 `American`、
#   `Nimitz`（舰级）被当成 `USS Nimitz`（舰名）——凭空多出一艘船。
ENTITY_GROUPS: List[Tuple[str, str, List[Tuple[str, str]]]] = [
    ("chokepoint", "海峡 / 水道", [
        ("Strait of Hormuz", "霍尔木兹海峡"), ("Hormuz", "霍尔木兹"),
        ("Bab el-Mandeb", "曼德海峡"), ("Bab al-Mandeb", "曼德海峡"),
        ("Red Sea", "红海"), ("Gulf of Aden", "亚丁湾"), ("Suez", "苏伊士运河"),
        ("Persian Gulf", "波斯湾"), ("Gulf of Oman", "阿曼湾"),
        ("Strait of Gibraltar", "直布罗陀海峡"), ("Malacca", "马六甲海峡"),
    ]),
    ("energy", "能源设施", [
        ("Abqaiq", "布盖格（沙特最大原油处理厂）"), ("Ras Tanura", "拉斯坦努拉（沙特最大油码头）"),
        ("Kharg", "哈尔克岛（伊朗主要出口码头）"), ("Khark", "哈尔克岛"),
        ("Shaybah", "沙伊巴油田"), ("Khurais", "库莱斯油田"), ("Yanbu", "延布"),
        ("Jubail", "朱拜勒"), ("Ras Laffan", "拉斯拉凡（卡塔尔 LNG）"),
        ("North Field", "北方气田（卡塔尔）"), ("South Pars", "南帕尔斯气田"),
        ("Fujairah", "富查伊拉（阿联酋加油枢纽）"), ("Ruwais", "鲁韦斯"),
        ("Mina al-Ahmadi", "艾哈迈迪港（科威特）"), ("Mina Saud", "米纳萨乌德"),
        ("Bandar Abbas", "阿巴斯港"), ("Assaluyeh", "阿萨卢耶"),
        ("Aramco", "沙特阿美"), ("ADNOC", "阿布扎比国家石油公司"),
    ]),
    ("cable", "海底光缆系统", [
        ("AAE-1", "亚非欧 1 号"), ("SEA-ME-WE", "亚欧海底光缆"),
        ("SMW5", "亚欧 5 号"), ("SMW-5", "亚欧 5 号"), ("FALCON", "FALCON 光缆"),
        ("2Africa", "2Africa 光缆"), ("IMEWE", "IMEWE 光缆"),
        ("Europe India Gateway", "欧印网关"), ("Blue Raman", "Blue Raman 光缆"),
        ("SEACOM", "SEACOM"), ("EASSy", "EASSy"), ("GBI", "海湾桥国际光缆"),
    ]),
    ("nuclear_site", "核设施", [
        ("Fordow", "福尔多（地下浓缩厂）"), ("Fordo", "福尔多"),
        ("Natanz", "纳坦兹（主要浓缩厂）"), ("Isfahan", "伊斯法罕（转化厂）"),
        ("Arak", "阿拉克（重水堆）"), ("Bushehr", "布什尔（核电站）"),
        ("Parchin", "帕尔钦"), ("Saghand", "萨甘德（铀矿）"),
        ("Gachin", "加钦（铀矿）"), ("Darkhovin", "达尔霍温"),
    ]),
    ("platform", "海上平台 / 装备", [
        ("aircraft carrier", "航空母舰"), ("carrier strike group", "航母打击群"),
        ("destroyer", "驱逐舰"), ("frigate", "护卫舰"),
        ("amphibious", "两栖舰"), ("USS ", "美国军舰"),
        ("5th Fleet", "第五舰队"), ("Fifth Fleet", "第五舰队"), ("NAVCENT", "美海军中央司令部"),
        ("anti-ship missile", "反舰导弹"), ("ASBM", "反舰弹道导弹"),
        ("drone boat", "无人艇"), ("USV", "无人水面载具"),
        ("Houthi", "胡塞武装"), ("Ansarallah", "安萨鲁拉（胡塞）"),
    ]),
    ("iran_force", "伊朗军事机构 / 编制", [
        ("IRGC", "伊斯兰革命卫队"), ("Revolutionary Guard", "革命卫队"),
        ("Basij", "巴斯基民兵"), ("Quds Force", "圣城旅"),
        ("IRGC Navy", "革命卫队海军"), ("Iranian Navy", "伊朗海军"),
        ("Artesh", "伊朗国防军"), ("Khatam al-Anbiya", "哈塔姆安比亚中央司令部"),
    ]),
]


def _entity_rx(name: str) -> "re.Pattern[str]":
    """实体匹配：字母/数字边界 + 大小写不敏感。

    ★ 必须用 `(?<![A-Za-z0-9])` 而不是 `\\b`：后者会把 `Guam's`、`Hormuz-based`
      这类写法切错；而短专名（`Arak`、`GBI`）不加边界会命中普通词。
    ★ **普通名词**（首字母小写）额外允许复数：实体表里既有专名（`Fordow`、
      `Kharg`）也有普通名词（`destroyer`、`aircraft carrier`），
      而后者在真实标题里几乎总以复数出现（"two US destroyers"）——
      不留复数尾巴会让 §5 实体分布系统性偏低，且偏低得**看不出来**。
      专名不加尾巴：`USS ` / `Fordow` 的复数形式不存在，
      加了反而可能命中无关词。
    """
    n = name.strip()
    tail = r"(?:s|es)?" if n[:1].islower() else ""
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(n) + tail + r"(?![A-Za-z0-9])",
                      re.I)


ENTITY_RX: Dict[str, "re.Pattern[str]"] = {
    f"{gid}:{en}": _entity_rx(en)
    for gid, _, items in ENTITY_GROUPS for en, _ in items
}

ENTITY_ORDER = {"chokepoint": 1, "energy": 2, "cable": 3, "nuclear_site": 4,
                "platform": 5, "iran_force": 6}


def extract_entities(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """在**通过展示闸门**的记录里抽取关键实体及其出现次数与代表记录。"""
    disp = [r for r in records if is_displayable(r)]
    out: List[Dict[str, Any]] = []
    for gid, gname, items in ENTITY_GROUPS:
        rows = []
        for en, zh in items:
            rx = ENTITY_RX[f"{gid}:{en}"]
            hits = []
            for r in disp:
                if rx.search(r.get("title") or "") or rx.search(r.get("summary") or ""):
                    hits.append(r)
            if not hits:
                continue
            rows.append({"en": en, "zh": zh, "n": len(hits),
                         "parties": {p: sum(1 for r in hits if (r.get("party") or "third") == p)
                                     for p in {r.get("party") or "third" for r in hits}},
                         "sample": sorted(hits, key=lambda x: x.get("published") or "",
                                          reverse=True)[:3]})
        rows.sort(key=lambda x: -x["n"])
        if rows:
            out.append({"id": gid, "name": gname, "rows": rows})
    return out


# ================================================================ 报告
def render_digest(records: List[Dict[str, Any]], statuses: List[Dict[str, Any]],
                  cross: Dict[str, Any], weekly: List[Dict[str, Any]],
                  run_dt: datetime, fresh: Optional[int], snapshot: int,
                  window_desc: str) -> str:
    L: List[str] = []
    A = L.append
    # 渲染前统一补默认字段 —— 真实记录都经 enrich()，但渲染层不该因缺字段崩
    disp: List[Dict[str, Any]] = []
    for r in records:
        r.setdefault("party", "third")
        r.setdefault("tier", "")
        r.setdefault("publisher", r.get("source_id", ""))
        r.setdefault("link", "")
        r.setdefault("title", "")
        r.setdefault("signals", [])
        r.setdefault("claim_kinds", [])
        r.setdefault("signal_buckets", [s.get("bucket") for s in r["signals"]])
        if is_displayable(r):
            disp.append(r)
    folded = len(records) - len(disp)

    A(f"# 伊朗战备与升级动态监测 · 六大关注域 × 当事方对照")
    A("")
    A(f"> 生成时间：{run_dt.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}　"
      f"·　版本 `v{VERSION}`　·　回溯窗口：{window_desc}")
    # fresh=None 表示「仅重新渲染」（--render-only）：没有重新抓取，
    # 所以绝不能写「本次新增 0 条」—— 那会被读成「这轮没有新记录」。
    if fresh is None:
        A(f"> **仅重新渲染**（未联网抓取）　·　当前快照 **{snapshot}** 条")
    else:
        A(f"> 本次新增 **{fresh}** 条　·　当前快照 **{snapshot}** 条")
    A("")

    # ---------- 0. 阅读须知
    A("## 0. 阅读须知（这条决定了怎么用这份报告）")
    A("")
    A("1. **本报告监测的是「表态」，不是「事实」。** 收录的是各方**公开说了什么**，"
      "不代表所说不成立，也不代表它成立。")
    A("2. **美方与伊方的官方源在证据地位上是完全对称的。** 两者都被标为 T1，"
      "含义同为「该方表态的原始出处」——**不是**「该方说法已被证实」。")
    A("3. **当事方对自己战损/战果的表述，属于有动机的自我陈述。** "
      "双方都可能夸大成果、淡化损失。任何单方声明在未经对方或第三方印证前，"
      "只能作为「该方声称」使用。")
    A("4. ★★ **§4「升级前瞻」是意向指标，不是事实指标，更不是预测。** "
      "它统计的是「谁在公开说下一步要做什么」，包含大量**威慑性措辞**（威胁本身常是"
      "为了避免行动，而非预告行动）。反过来，真正要动手的一方常常**停止**公开发声，"
      "因此**本表分值低 ≠ 不会升级**；它的正确读法是「公开叙事里下一步被怎么说」。")
    A("5. **交叉验证矩阵里的 `双方同期提及` 不等于双方说法一致。** "
      "程序只做「同一议题词 + 同一周出现」的机械配对，不做语义一致性判断。"
      "一致性必须读 §7 双方原文片段后人工判定。")
    A("6. 全部分析分三层：**事实**（收录到的原文）、**机械计算**（计数、配对）、"
      "**启发式判断**（信号计分、声明分类），后者均附局限说明。")
    A("")

    # ---------- 1. 一页速览
    A("## 1. 一页速览")
    A("")
    us = [r for r in disp if r.get("party") == "us"]
    ir = [r for r in disp if r.get("party") == "iran"]
    th = [r for r in disp if r.get("party") == "third"]
    opp = [r for r in disp if r.get("party") == "iran_opp"]

    zs = build_zone_summary(records)
    fs = build_foresight(records)

    A("**六大关注域信号分布**（只统计通过展示闸门的记录）")
    A("")
    A("| # | 关注域 | 伊方官方 | 美方 | 第三方 | 反对派 | 该域记录 | 最活跃信号桶（前 3） |")
    A("|---:|---|---:|---:|---:|---:|---:|---|")
    for z in zs:
        p = z["parties"]
        top = "、".join(f"{SIG_CFG[k].get('label', k)}({v})" for k, v in
                        sorted(z["buckets"].items(), key=lambda kv: -kv[1])[:3]) or "—"
        A(f"| {z['order']} | **{z['name']}** | {p.get('iran', 0)} | {p.get('us', 0)} | "
          f"{p.get('third', 0)} | {p.get('iran_opp', 0)} | {z['n']} | {top} |")
    A(f"| — | **当事方合计** | {len(ir)} | {len(us)} | {len(th)} | {len(opp)} | {len(disp)} | — |")
    A("")
    A("> ⚠️ **两种口径不可纵向相加**：上半部分的各行是**关注域口径**"
      "（一条记录命中多个域会被重复计入），末行是**当事方口径**（每条只算一次）。"
      "两个口径都由 `is_displayable()` 这一同一个闸门函数产出，因此内部自洽，"
      "但**列不能相加**。")
    A("")
    if fs["total"]:
        A(f"⚠️ **升级前瞻**：窗口内 **{fs['total']}** 条记录含**未来时 / 条件式 / 临战式**表述"
          f"（占展示记录的 {fs['total'] / max(1, len(disp)) * 100:.0f}%），"
          f"按强度排序见 **§4**。★ **这是意向指标，不是预测** —— "
          f"威慑性措辞本身常常正是为了避免行动（详见 §0 第 4 条）。")
    else:
        A("**升级前瞻**：窗口内未检出前瞻性表述。★ **这不等于不会升级** —— "
          "真正准备行动的一方往往会**减少**公开发声，本表对「沉默」不敏感。")
    A("")
    if opp:
        A(f"> 🟣 **单列**：另有 **{len(opp)}** 条来自**伊朗流亡/反对派媒体**"
          f"（如 Iran International、IranWire）。它们**不计入「伊方官方表态」**，"
          f"归入第三方旁证 —— 否则会把反对派报道当成伊朗官方立场。")
        A("")
    contra = sum(1 for p in cross.get("pairs", []) if p["cls"] == "contradiction")
    mutual = sum(1 for p in cross.get("pairs", []) if p["cls"] == "mutual_assert")
    both = sum(1 for p in cross.get("pairs", []) if p["cls"] == "both_mentioned")
    A(f"**交叉验证结果**：{len(cross.get('pairs', []))} 组同期对照"
      f"（其中 互相矛盾 **{contra}** · 双方均宣称行动 **{mutual}** · 双方仅同期提及 **{both}**）")
    A("")
    # 折叠原因必须分类上报：把「真无关」和「有信号但没锚定」混成一句
    # 「未命中任何战备信号桶」，会掩盖「词表或锚点收紧过头」这类问题。
    _fa = GATE_AUDIT.get("_display_folded") or []
    # ★ 分类计数取**全量**（_folded_breakdown），绝不从抽样明细里现算 ——
    #  从 _fa 现算会把「864 条」的分布错报成「前 400 条」的分布。
    _brk: Dict[str, int] = dict(GATE_AUDIT.get("_folded_breakdown") or {})
    if not _brk:                      # --show-all 或旧档兜底
        _brk = {}
        for _x in _fa:
            _brk[_x.get("reason", "?")] = _brk.get(_x.get("reason", "?"), 0) + 1
    _lbl = {"no-topic-anchor": "有战备信号但未锚定本主题",
            "no-readiness-signal": "命中主题词但无战备信号",
            "off-topic": "与本主题无关"}
    if _brk:
        _detail = "；".join(f"{_lbl.get(k, k)} **{v}** 条"
                           for k, v in sorted(_brk.items(), key=lambda kv: -kv[1]))
        # 自洽检查：分类计数之和必须等于折叠总数。不等就**如实标注**，
        # 而不是把不自洽的数字默默印出去（这正是上一版的缺陷）。
        _sum = sum(_brk.values())
        _chk = "" if _sum == folded else f" ⚠️（分类合计 {_sum} ≠ 折叠总数 {folded}，口径待查）"
        _listed = len(_fa)
        _tail = (f"；留档 {_listed} 条明细" if _listed < folded
                 else f"；{_listed} 条明细全部留档")
        A(f"**本次折叠**：{folded} 条未进入本报告正文 —— {_detail}{_chk}{_tail}"
          f"（完整记录仍写入 `records-<日期>.jsonl`，加 `--show-all` 可全量查看）。")
    else:
        A(f"**本次折叠**：{folded} 条未通过展示闸门，未进入本报告正文"
          f"（完整记录仍写入 `records-<日期>.jsonl`，加 `--show-all` 可全量查看）。")
    A("")

    # ---------- 2. 源状态
    A("## 2. 数据源状态")
    A("")
    A("| 源 | 当事方 | 等级 | 发布主体 | 状态 | 解析条目 | 产出 | 快照内 | 源内最新 | 距今 |")
    A("|---|---|---|---|---|---:|---:|---:|---|---:|")
    for s in statuses:
        icon = STATUS_ICON.get(s["state"], "❓")
        latest = (s["latest"] or "")[:10] or "—"
        age = f"{s['age_days']}天" if s.get("age_days") is not None else "—"
        tls = "　🔓**未校验证书**" if s.get("tls") == "unverified" else ""
        kept = s.get("kept")
        kept_s = "—" if kept is None else str(kept)
        trunc = " ⚠️截断" if s.get("kept_from") else ""
        snap = s.get("in_snapshot")
        snap_s = "—" if snap is None else str(snap)
        A(f"| `{s['id']}` | {party_badge(s['party'])} | {tier_badge(s['tier'])} | "
          f"{truncate(s['publisher'], 26)} | {icon} {s['state']}{tls} | "
          f"{s['entries']}{trunc} | {kept_s} | {snap_s} | "
          f"{latest} | {age} |")
    A("")
    A("> **四列条数的口径完全不同，不可混看**："
      "「解析条目」= 该源 feed 里解析出的条目总数；"
      "「产出」= **本轮**经时间窗过滤后产出的条数；"
      "「快照内」= 该源在**本次窗口快照**里贡献的条数（**参与 §1/§7/§8 统计的就是这一列**）。"
      "`状态` 只描述**本轮抓取**：某源本轮失败（❌ 0 条）时，它的历史记录仍在快照里、"
      "仍计入统计 —— 只看状态列会误判「这个源没有数据」。")
    A("")
    okn = sum(1 for s in statuses if s["state"].startswith("ok"))
    dis = [s for s in statuses if s["state"] == "disabled"]
    bad = [s for s in statuses if s["state"].startswith("fail")]
    A(f"共 {len(statuses)} 个源：可用 {okn} · 停用留档 {len(dis)} · 失败 {len(bad)}")
    # ★ feed 未给日期的条目：必须让它可见，否则它只会以「表格日期栏印 —」这种
    #   极容易被忽略的形式出现。Google News 对无日期条目填纪元零值
    #   `Thu, 01 Jan 1970`，本系统按「日期未知」显式处理（置空 + 单独计数）。
    _und = [s for s in statuses if s.get("undated")]
    if _und:
        A("")
        A(f"> 🕐 **{sum(s['undated'] for s in _und)} 条条目 feed 未提供可用日期**"
          f"（含 `Thu, 01 Jan 1970` 这类纪元零值哨兵）："
          + "、".join(f"`{s['id']}` {s['undated']} 条" for s in _und)
          + "。它们无法定位到时间轴，因此**不进 §8 周表**，"
            "也不会跨轮携带（详见 §8 说明）。")
    if dis:
        A("")
        A("**停用源（保留配置与原因，未删除）**：")
        for s in dis:
            A(f"- `{s['id']}`（{s['publisher']}）：{truncate(s.get('note', ''), 220)}")
    agg = [s for s in statuses if SOURCE_BY_ID.get(s["id"], {}).get("aggregator")]
    if agg:
        A("")
        A("> 🔀 **聚合器源提示**：下列源经公开聚合器取回，会**同时带入多个不同立场的媒体**。"
          "本系统按每条记录的**实际发布媒体**重新判定当事方（`outlet_map`），"
          "流亡/反对派媒体单列为 `iran_opp`、不计入伊方官方表态。"
          "未识别的媒体会打 `outlet_unclassified` 标记：")
        for s in agg:
            A(f"> - `{s['id']}` → {', '.join(s.get('url_alternates') or [])or ''}"
              f"{s['url_used']}")
    unclassified = [r for r in records if r.get("outlet_unclassified")]
    if unclassified:
        names = sorted({r.get("outlet", "") for r in unclassified})[:12]
        A(f"> - ⚠️ 本次有 {len(unclassified)} 条来自未登记媒体，"
          f"当事方按源默认值处理：{', '.join(n for n in names if n)}")
    if TLS_DOWNGRADED:
        A("")
        A("> 🔓 **传输层提示**：下列源**关闭了 TLS 证书校验**，因为其证书链含自签名根/"
          "缺中间证书，标准信任库无法验证。本系统不做风控绕过，但这一类是证书配置问题，"
          "不是站点反爬。**仅对这些特定源关闭校验**，其它源不受影响：")
        for d in TLS_DOWNGRADED:
            A(f"> - `{d['id']}`（{d['publisher']}）：{truncate(d['note'], 170)}")
        A("> 如需严格模式（拒绝任何关闭校验的源），加 `--strict-tls`。")
    A("")

    # ---------- 3. 六大关注域明细
    A("## 3. 六大关注域明细")
    A("")
    A("每个域下列出**该域内的记录**（按时间倒序）。一条记录命中多个域时会出现在多个域下。"
      "「信号」列是该记录在本域内命中的信号桶。")
    A("")
    for z in zs:
        A(f"### 3.{z['order']} {z['name']}　`{z['id']}`　（{z['n']} 条）")
        A("")
        A(f"> {z['desc']}")
        A("")
        if not z["n"]:
            A("_窗口内未检出该关注域的记录。_ **注意**：未检出 ≠ 未发生，"
              "只表示本窗口收录的公开信源里没有出现对应表述。")
            A("")
            continue
        zr = z["records"]
        A("| 日期 | 当事方 | 等级 | 来源 | 信号 | 标题 |")
        A("|---|---|---|---|---|---|")
        for r in zr[:45]:
            sigs = "/".join(sorted({s["label"] for s in r["signals"]
                                    if zone_of_bucket(s["bucket"]) == z["id"]}))
            A(f"| {date_cell(r.get('published'))} | {party_badge(r.get('party'))} | "
              f"{tier_badge(r['tier'])} | {truncate(r['publisher'], 18)} | "
              f"{truncate(sigs, 34)} | [{truncate(r['title'], 74)}]({r['link']}) |")
        A("")
        A("<details><summary>可核验片段（点击展开：本域每条结论对应的原文片段）</summary>")
        A("")
        for r in zr[:30]:
            for s in r.get("signals", []):
                if zone_of_bucket(s["bucket"]) != z["id"]:
                    continue
                A(f"- `{r['source_id']}` · **{s['label']}** · 命中 `{s['phrase']}`"
                  f"（{s.get('n_hits', 1)} 次）")
                A(f"  > {truncate(s['context'], 250)}")
                A(f"  > ── {party_badge(r.get('party'))} "
                  f"[{truncate(r['title'], 66)}]({r['link']})　`{date_cell(r.get('published'))}`")
                break        # 每条记录在本域只列一个代表片段，避免报告过长
        A("")
        A("</details>")
        A("")

    # ---------- 4. 升级前瞻（未来 1–4 周）
    A("## 4. 升级前瞻指标（未来 1–4 周 · 意向指标）")
    A("")
    A("★ **本节回答的是「公开叙事里，下一步被怎么说」，不是「会不会发生」。**")
    A("")
    A("- 统计对象是**未来时 / 条件式 / 临战式**表述：`" +
      "`、`".join(SIG_CFG[b].get("label", b) for b in FORESIGHT_BUCKETS if b in SIG_CFG) + "`。")
    A("- **强度 = Σ(信号权重 × 命中次数)**，是启发式排序，不是概率。")
    A("- **三种误读必须避免**：① 把「威胁」读成「预告」（威慑性措辞常是为了避免行动）；"
      "② 把分值低读成「不会升级」（真正准备行动的一方往往**减少**公开发声）；"
      "③ 把某一方的措辞密度读成其**实际意图的强度**（公开叙事密集也可能只是宣传战）。")
    A("")
    if not fs["total"]:
        A("_窗口内未检出前瞻性表述。_")
        A("")
    else:
        _ps = "　".join(f"{party_badge(p)} {n}" for p, n in
                       sorted(fs["by_party"].items(), key=lambda kv: -kv[1]))
        _bs = "　".join(f"{SIG_CFG[k].get('label', k)} **{v}**" for k, v in
                       sorted(fs["by_bucket"].items(), key=lambda kv: -kv[1]))
        A(f"命中 **{fs['total']}** 条　·　按当事方：{_ps}")
        A("")
        A(f"按信号桶：{_bs}")
        A("")
        A("| 强度 | 日期 | 当事方 | 等级 | 来源 | 前瞻信号 | 标题 |")
        A("|---:|---|---|---|---|---|---|")
        for r in fs["records"][:40]:
            fb = "/".join(SIG_CFG[s["bucket"]].get("label", s["bucket"])
                          for s in r["signals"] if s["bucket"] in FORESIGHT_BUCKETS)
            A(f"| {foresight_score(r)} | {date_cell(r.get('published'))} | "
              f"{party_badge(r.get('party'))} | {tier_badge(r['tier'])} | "
              f"{truncate(r['publisher'], 16)} | {truncate(fb, 30)} | "
              f"[{truncate(r['title'], 68)}]({r['link']}) |")
        A("")
        A("<details><summary>前瞻表述的原文片段（**必须逐条核对措辞强度**）</summary>")
        A("")
        for r in fs["records"][:28]:
            for s in r.get("signals", []):
                if s["bucket"] in FORESIGHT_BUCKETS:
                    A(f"- **{SIG_CFG[s['bucket']].get('label', s['bucket'])}**"
                      f"（强度 {foresight_score(r)}）· 命中 `{s['phrase']}`")
                    A(f"  > {truncate(s['context'], 260)}")
                    A(f"  > ── {party_badge(r.get('party'))} "
                      f"[{truncate(r['title'], 66)}]({r['link']})　`{date_cell(r.get('published'))}`")
                    break
        A("")
        A("</details>")
        A("")

    # ---------- 5. 关键实体
    ents = extract_entities(records)
    A("## 5. 关键实体分布（海峡 / 设施 / 装备 / 光缆 / 核设施）")
    A("")
    A("在**通过展示闸门**的记录里统计实体出现条数。**实体抽取一律带字母/数字边界**，"
      "否则短专名会嵌进普通单词（实测事故：`Rota` 命中 `Rotational`、"
      "`Nimitz`（舰级）被当成 `USS Nimitz`（舰名），凭空多出一艘船）。")
    A("")
    if not ents:
        A("_窗口内未抽到登记实体。_")
        A("")
    for g in ents:
        A(f"### 5.{ENTITY_ORDER.get(g['id'], 9)} "
          f"{g['name']}　`{g['id']}`")
        A("")
        A("| 实体 | 中文 | 条数 | 当事方分布 | 最近一条 |")
        A("|---|---|---:|---|---|")
        for row in g["rows"][:22]:
            pd = "/".join(f"{party_badge(p)}{n}" for p, n in
                          sorted(row["parties"].items(), key=lambda kv: -kv[1]))
            s0 = row["sample"][0] if row["sample"] else None
            link = (f"[{truncate(s0['title'], 52)}]({s0['link']})　`{date_cell(s0.get('published'))}`"
                    if s0 else "—")
            A(f"| `{row['en']}` | {row['zh']} | {row['n']} | {pd} | {link} |")
        A("")

    # ---------- 5. 交叉验证矩阵
    A("## 6. 交叉验证矩阵（议题 × 周）")
    A("")
    A("`美`=美方条数　`伊`=伊方条数　`三`=第三方条数　"
      "`判定`：🔴互相矛盾 · 🟠双方均宣称行动 · 🟡双方仅同期提及 · ➡️单方提及")
    A("")
    if not cross.get("anchors"):
        A("_窗口内未发现可用于交叉配对的议题锚点。_")
        A("")
    else:
        for aid, block in cross["anchors"].items():
            st = cross["stats"].get(aid, {})
            icons = {"contradiction": "🔴", "mutual_assert": "🟠",
                     "mutual_denial": "🟠", "both_mentioned": "🟡",
                     "us_only": "➡️", "iran_only": "⬅️", "third_only": "🌐"}
            A(f"### {block['label']}　`{aid}`")
            A("")
            A(f"汇总：" + " · ".join(f"{icons.get(k,'')}{k}×{v}"
                                     for k, v in sorted(st.items())))
            A("")
            A("| 周 | 美 | 伊 | 三 | 判定 |")
            A("|---|---:|---:|---:|---|")
            for row in block["rows"]:
                A(f"| {row['week']} | {row['us']} | {row['iran']} | {row['third']} | "
                  f"{icons.get(row['cls'],'')} {row['cls']} |")
            A("")

    # ---------- 6. 同期对照原文
    A("## 7. 同期对照：双方原文并列（可核验）")
    A("")
    A("> 只列「双方同期均有表态」的组。**这是给人读的段落，不是程序结论** —— "
      "程序只负责把同一周、同一议题的双方材料摆到一起。")
    A("")
    A("> ★ **第三方列不可省**：`contradiction` 的判定依据里，最典型的形态就是"
      "「第三方转述美方否认伊方说法」（如 `CENTCOM rejects Iranian claim that …`）——"
      "判定由它触发，若正文不列出来，读者就无从复核这个对撞是真是假。"
      "第三方最多列 3 条，且**带否认的排在最前**。")
    A("")
    pairs = cross.get("pairs", [])
    if not pairs:
        A("_窗口内没有双方同期就同一议题表态的组。_")
        A("")
    for p in pairs[:24]:
        A(f"### [{p['anchor_label']}] {p['week']}　·　`{p['cls']}`")
        A("")
        # ★ 第三方里**驱动本次判定**的那几条必须排最前 —— 它们是「判定依据」，
        #  而该组第三方往往还有一堆同议题但无关的旁证（舰队动态、演习通稿），
        #  按原序取前 3 条会把依据挤掉，读者看到的全是没用的旁证。
        _trig_titles = set()
        for _k in ("third_denial_vs_iran", "third_denial_vs_us"):
            for _it in (p.get("trigger") or {}).get(_k) or []:
                _trig_titles.add(_it.get("title", ""))
        _third_sorted = sorted(
            p["third"], key=lambda x: x.get("title", "") not in _trig_titles)
        A("| 当事方 | 等级 | 来源 | 标题 |")
        A("|---|---|---|---|")
        for side, label, items in (("us", "🇺🇸 美方", p["us"]),
                                   ("iran", "🇮🇷 伊方", p["iran"]),
                                   ("third", "🌐 第三方", _third_sorted)):
            for it in items:
                A(f"| {label} | {tier_badge(it['tier'])} | "
                  f"{truncate(it['publisher'], 18)} | [{truncate(it['title'], 70)}]({it['link']}) |")
        A("")
        for side, label, items in (("us", "美方", p["us"]),
                                   ("iran", "伊方", p["iran"]),
                                   ("third", "第三方", _third_sorted)):
            for it in items[:2]:
                if it.get("context"):
                    A(f"- **{label}** 原文片段（命中 `{'/'.join(it['signals'])}`）：")
                    A(f"  > {truncate(it['context'], 260)}")
        A("")

    # ---------- 7. 周趋势
    A("## 8. 公开升级信号活跃度（按周 · 启发式）")
    A("")
    A("> **这是「公开发声活跃度」的检索索引，不是战备强度测量，也不是战力对比。** "
      "计分只反映「本监测收录到的记录里命中了多少信号词」，受收录覆盖度影响。"
      "**空 ≠ 零**：未收录周渲染为 `—`。"
      "本表**回答不了**「哪一方战备更强」——它只回答「哪一周这个话题的公开表态更多」。")
    A("")
    A("| 周 | 美方条数 | 美方加权分 | 伊方条数 | 伊方加权分 | 第三方* | 覆盖 |")
    A("|---|---:|---:|---:|---:|---:|---|")
    # ★ 连续 3 周以上的「未收录」压成一行区间。
    #  语义完全不变（仍然不是 0），但 31 行 `—` 会把真正有数据的 3 行淹掉 ——
    #  而「未收录」这个事实只需要被看见一次，不需要占 31 行。
    def _uncovered(w: Dict[str, Any]) -> bool:
        return not w["covered"] or not w["parties"]

    i = 0
    while i < len(weekly):
        if not _uncovered(weekly[i]):
            p = weekly[i]["parties"]
            A(f"| {weekly[i]['week']} | {p['us']['docs']} | {p['us']['weight']} | "
              f"{p['iran']['docs']} | {p['iran']['weight']} | {p['third']['docs']} | ✅ |")
            i += 1
            continue
        j = i
        while j < len(weekly) and _uncovered(weekly[j]):
            j += 1
        run = j - i
        if run >= 3:
            A(f"| {weekly[i]['week']} ~ {weekly[j - 1]['week']} | — | — | — | — | — | "
              f"⚪ 未收录（{run} 周） |")
        else:
            for k in range(i, j):
                A(f"| {weekly[k]['week']} | — | — | — | — | — | ⚪ 未收录 |")
        i = j
    A("")
    A("> 「加权分」= 该周该方记录命中的各信号桶**权重之和**（权重在 config 的 `signals` 里人工设定）。"
      "它比条数多了一层「命中多重的信号」，但仍是启发式参数、不是可比的度量单位，"
      "**不同周之间不可直接比大小**；跨方比大小还会受 §10 第 9 条「采集不对称」污染。")
    A(">")
    A("> \\* **第三方列含「伊朗流亡/反对派媒体」**（`iran_opp`），与 §1 把它们**单列**"
      "的口径不同 —— 因此本表第三方合计会比 §1 的第三方多出反对派那部分（实测差 10 条）。"
      "§1 单列是为了避免把反对派报道当成伊朗官方立场；本表按 `us / iran / 其它` 三分以保持周表结构，"
      "故把反对派并入第三方。**两处不可直接对比。**")
    A("")
    # ★ 必须披露「因日期不可解析而不计入本表」的条数。
    #  否则读者会发现「§1 说展示 1171 条，§8 各周加起来只有 1100 条」，
    #  而差额没有任何解释 —— 这类无法解释的缺口会让读者怀疑整份报告。
    #  实测这一次差额高达 95 条（IAEA 的两位数年份 + crisisgroup 的长星期格式）。
    _nodate = [r for r in disp if not parse_date(r.get("published") or "")]
    if _nodate:
        _nd: Dict[str, int] = {}
        for _r in _nodate:
            _k = _r.get("party") or "third"
            _nd[_k] = _nd.get(_k, 0) + 1
        _nds = "　".join(f"{party_badge(k)} {v}" for k, v in
                        sorted(_nd.items(), key=lambda kv: -kv[1]))
        A(f"> ⚠️ **有 {len(_nodate)} 条展示记录因 `published` 无法解析而不计入本表**"
          f"（{_nds}）—— 它们**仍然出现在 §1/§3/§5**（那几处表格的日期栏印 `—`）。"
          f"本表按周统计**要求日期可解析**，因此本表合计会小于 §1 的展示条数，"
          f"差额恰好等于此数（**不是漏抓，也不是漏统计**）。"
          f"§2 里 `源内最新=—` 的源就是日期格式未被识别的源。")
        A(">")
        A("> \\* 无日期记录**不跨轮携带**：它既算不出年龄、也就永远过不了窗口判定，"
          "若允许保留就会永久滞留在快照里。策略是「只在它仍出现在 feed 里的那些轮次收录」，"
          "一旦从 feed 掉出去即从快照移除（累积总档 `ALL-records.jsonl` 中保留原始留档）。")
        A("")

    # ---------- 8. 声明类型
    A("## 9. 声明类型分布（宣称 / 否认 / 承认 / 表态 / 威胁 / 前瞻）")
    A("")
    A("| 声明类型 | 美方 | 伊方 | 第三方 |")
    A("|---|---:|---:|---:|")
    # ★ 标签必须与 §1 的**信号桶**标签区分开：两处都有 `denial`，
    #  但口径完全不同（§1 = 信号桶命中，§8 = 声明类型分类），
    #  若都写成「否认/驳斥」，报告里会出现**两行同名不同值**的表格行，
    #  读者无法判断该信哪个 —— 连回源对账工具都会提取错行。
    kinds = [("action_claim", "宣称行动效果（声明类）"),
             ("denial", "否认/驳斥（声明类）"),
             ("acknowledgement", "承认/确认（声明类）"),
             ("readiness_declaration", "战备表态（声明类）"),
             ("threat_warning", "威胁/警告（声明类）")]
    for k, lbl in kinds:
        cu = sum(1 for r in us if k in (r.get("claim_kinds") or []))
        ci = sum(1 for r in ir if k in (r.get("claim_kinds") or []))
        ct = sum(1 for r in th if k in (r.get("claim_kinds") or []))
        A(f"| {lbl} | {cu} | {ci} | {ct} |")
    A("")
    A("> 本表按**声明类型**（`claim_kinds`）统计，与 §1 的**信号桶**（`signal_buckets`）"
      "是**两套不同口径**：同一个词可能既落在某个信号桶、又算作某类声明，"
      "两表数字**不要求相等**，也不可互相印证。")
    A("")
    A("> 分类基于关键词命中，**不理解语境**（反讽、转述他人说法、引用对方声明都可能被计成该方自己的声明）。"
      "仅用于看分布形态，不能当逐条判定。")
    A("")

    # ---------- 9. 合规
    A("## 10. 数据源与合规")
    A("")
    A("- 全部为**公开**来源：政府机构 RSS/API、官方通讯社 RSS、公共媒体 RSS。")
    A("- 遵守 robots.txt；不抓需登录内容；**不做任何风控绕过**"
      "（不模拟浏览器指纹、不绕 Cloudflare/Akamai 挑战）。")
    A("- 站点只开放部分路径时，使用开放的路径，并在 §2 如实标注限制。")
    A("- 被 CDN 拦截的源（如 centcom.mil）**停用留档而不删除**，改用公开聚合器取标题+链接，"
      "并在源状态里降级标注。")
    A("- `--from-file` 提供完全离线的退路：用浏览器另存内容即可合法完成解析。")
    A("- 实际上并不采集任何社交媒体/自媒体（T4）内容：其内容没有可回溯的原始文件，"
      "进入证据表会把整表可信度拉平。")
    A("")

    # ---------- 10. 偏差
    A("## 11. 已知偏差与误读边界")
    A("")
    A("1. **选择性发布偏差（最需要警惕的一条）**：本报告收录的是各方**愿意公开说的话**。"
      "真正的战备状态（戒备等级、弹药与兵力实数、损失明细、行动时间表）几乎不公开。"
      "因此「某方某周表态少」**不能**读成「该方战备放松或降级」，"
      "只可能读成「该周公开表态少」——**尤其要警惕「沉默」**："
      "真正准备行动的一方常常主动停止公开发声。")
    A("2. **宣传激励偏差**：当事双方的官方媒体对自身战果/战损均有夸大或淡化的动机。"
      "本报告把双方声明并列，是为了让这种偏差**可见**，不是为给出「谁在说真话」的裁决。")
    A("3. **聚合器降级偏差**：标为 T3 的聚合器源（含按主题构造的 Google News 查询）"
      "**只有标题与链接，没有正文**。标题常被截断，收录延迟与排序均不透明。"
      "→ 引用前必须点开原文；本报告的「可核验片段」对这类源只能给到标题层。")
    A("4. **语言与翻译**：伊方多为波斯语原文。本报告**不做机器翻译**，保留原文标题以免引入"
      "翻译误差；信号词表同时配置了波斯语词条（如 `آمادگی`、`موشک بالستیک`、`تنگه هرمز`）。"
      "★ 但**词表覆盖一定不完整**：波斯语的构词与阿拉伯语借词变体多，"
      "**伊方记录的信噪比天然低于英文源**，这是本报告的结构性短板。")
    A("5. ★★ **前瞻指标（§4）的三种误读**："
      "① 把**威胁**读成**预告** —— 威慑性措辞本身常常正是为了避免行动；"
      "② 把**分值低**读成**不会升级** —— 本表对「战略沉默」不敏感；"
      "③ 把**措辞密度**读成**实际意图强度** —— 公开叙事密集也可能只是宣传战与国内动员。"
      "→ §4 的正确用法：把它当作**公开叙事的温度计**，不是风险概率。")
    A("6. **信号计分是启发式**（见 §8 说明）。分值衡量的是**公开发声活跃度**，"
      "既不可跨周直接比大小，更不可当作任何一方的**真实战备强度**或战力对比。")
    A("7. **周粒度粗于事件**：同一周内相隔数天的两次事件会被并入同一行。")
    A("8. **空 ≠ 零**：未收录周一律渲染 `—`，不是 `0`。")
    A("9. **第三方源同样有编辑立场**，仅用于三角验证与线索，不作为事实依据（T3）。")
    A("10. ★ **实体抽取是「被提及」，不是「已发生」的状态描述（§5 必读）**："
      "文中出现 `Kharg` 可能是「打击哈尔克岛」、也可能是「哈尔克岛正常装船」或"
      "「分析人士认为哈尔克岛是目标」。§5 只统计**提及条数**与代表记录，"
      "**不判定实体当前状态**。任何「某设施已被打击」的结论必须回到原文。")
    A("11. **采集不对称（含工程性与站点性两类，必读）**：两侧的可采集性并不对等，"
      "因此条数差不可直接当作表态热度差。具体地："
      "① **伊方**有 sepahnews（IRGC 官方）/ defapress（国防部关联）/ IRNA / Pars Today 等"
      "多个**可直接静态抓取**的官方源，单源产量高（sepahnews 100 条、parstoday 120 条）；"
      "**美方**的 CENTCOM 官网为 JS 渲染（静态 HTML 内无条目链接）、"
      "海军与参联会走 AFPIMS `RSS.ashx` 而 `Site` id 不对外暴露（反查失败即返回 200 空体），"
      "因此美方一手面主要靠国防部三个 feed + DVIDS + 聚合器。"
      "→ **跨方比条数会系统性高估伊方表态量。**"
      "② **海事一手通告缺失**：UKMTO（英国海事贸易行动办公室）是红海/亚丁湾海事安全通告的"
      "一手发布方，但官网未提供公开 RSS 端点（实测 404），本系统只能经航运媒体转述 —— "
      "涉及「商船被袭/被扣」的时间和位置细节因此**精度下降**。"
      "③ **部分源是「工程性不可用」而非「站点封锁」**：本机代理端口会漂移，"
      "实测某次运行 12 个源因出口指向过期端口而失败，报错形态（`502 Tunnel failed` 与"
      "`403 AkamaiGHost/cloudflare`）与「被 CDN 拦截」**肉眼无法区分**，换存活出口后全部恢复。"
      "→ **「某源失败」在归因到站点之前必须先排除本地出口因素**（归因三步见 README）。"
      "④ 全部停用源（含原因、状态码、Server 头与替代方案）见 §10 与 "
      "`output/_source_status.json`。")
    A("12. **各方声明的默认等级是「该方说了什么」的一手记录，不是「事情真相」**。"
      "美方国防部/中央司令部与伊方革命卫队通讯社在本系统中**同为 T1**，"
      "含义仅是一手记录；任何涉己战果/损失表述都必须有独立来源才可引用。")
    A("13. **本报告不做、也不应被用于以下用途**："
      "① 不做实时船舶/航空定位（AIS/ADS-B）类受限数据的抓取或推断；"
      "② 不做军事能力、打击效果或核能力的评估；"
      "③ 不给出「何时会发生什么」的预测——§4 是叙事指标，不是预测；"
      "④ 不作为任何行动决策的依据。本报告是**公开信息整理的索引**。")
    A("")

    # ---------- 附录：全量（--show-all）
    if SHOW_ALL:
        A("## 附录 A. 全量记录（`--show-all`，含被折叠项）")
        A("")
        A("| 日期 | 当事方 | 等级 | 来源 | 信号 | 标题 |")
        A("|---|---|---|---|---|---|")
        for r in sorted(records, key=lambda x: x.get("published") or "", reverse=True):
            sigs = "/".join(sorted({s.get("label", "") for s in r.get("signals", [])})) or "—"
            A(f"| {date_cell(r.get('published'))} | {party_badge(r.get('party'))} | "
              f"{tier_badge(r.get('tier'))} | {r.get('source_id')} | {sigs} | "
              f"[{truncate(r.get('title', ''), 70)}]({r.get('link', '')}) |")
        A("")

    return "\n".join(L)


def render_crosscheck_md(cross: Dict[str, Any], run_dt: datetime) -> str:
    L: List[str] = []
    A = L.append
    A("# 交叉验证明细")
    A("")
    A(f"> 生成时间：{run_dt.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}　·　版本 `v{VERSION}`")
    A("")
    A("## 配对口径")
    A("")
    A(f"- 配对条件：同一**议题锚点** + 发布周相差不超过 "
      f"{CROSS_MATCH_DAYS} 天（按 ISO 周归组）")
    A("- 判定为机械规则，**不做语义一致性判断**：")
    A("  - `contradiction` 🔴 一方宣称行动效果、另一方在同周否认")
    A("  - `mutual_assert` 🟠 双方同周均宣称行动效果（**不等于**说的是同一件事）")
    A("  - `mutual_denial` 🟠 双方同周均是否认")
    A("  - `both_mentioned` 🟡 双方同周都提到该议题（**不等于**说法一致）")
    A("  - `us_only` / `iran_only` 仅单方提及 —— 单方声明在获印证前不可作为事实")
    A("")
    A("## 全部同期对照组")
    A("")
    TRIG_LABEL = {
        "us_assert": "美方宣称有行动效果",
        "iran_denial": "伊方否认",
        "third_denial_vs_iran": "第三方转述的**替美方否认伊方**",
        "iran_assert": "伊方宣称有行动效果",
        "us_denial": "美方否认",
        "third_denial_vs_us": "第三方转述的**替伊方否认美方**",
    }
    for p in cross.get("pairs", []):
        A(f"### [{p['anchor_label']}] {p['week']} · `{p['cls']}`")
        A("")
        trig = p.get("trigger") or {}
        if trig:
            A(f"**判定依据**（程序按词表机械抽取，请逐条核对原文）：")
            A("")
            for k, items in trig.items():
                A(f"- _{TRIG_LABEL.get(k, k)}_")
                for it in items:
                    A(f"  - {tier_badge(it['tier'])} {it['publisher']} · "
                      f"`{it['claim_kinds']}` — [{it['title']}]({it['link']})")
            A("")
        for side, label in (("us", "美方"), ("iran", "伊方"), ("third", "第三方旁证")):
            items = p.get(side) or []
            if not items:
                continue
            A(f"**{label}**")
            A("")
            for it in items:
                A(f"- {tier_badge(it['tier'])} · {it['publisher']}")
                A(f"  - [{truncate(it['title'], 100)}]({it['link']})")
                if it.get("context"):
                    A(f"  - 原文片段（`{'/'.join(it['signals'])}`）：{truncate(it['context'], 240)}")
            A("")
    A("## 局限")
    A("")
    A("本文件由程序按「议题词 + 周」机械配对生成，只负责把材料摆到一起。"
      "**任何一致性/矛盾性结论都必须由人读原文后给出。**")
    A("")
    return "\n".join(L)


# --------------------------------------------- 事件核验（iran-escalation 专用）
def iran_annotate_corroboration(records):
    """跨源印证（与新版引擎同口径）：≥2 个不同发布方或 T1 → 已印证。"""
    groups = {}
    for r in records:
        tk = title_key(r.get("title", ""))
        if not tk:
            continue
        groups.setdefault(tk, []).append(r)
    for g in groups.values():
        pubs = sorted({(r.get("publisher") or r.get("source_id") or "?") for r in g})
        tiers = {(r.get("tier") or "") for r in g}
        confirmed = bool(len(pubs) >= 2 or "T1" in tiers)
        for r in g:
            r["corroboration"] = {"n_records": len(g), "n_publishers": len(pubs),
                                  "publishers": pubs[:6], "confirmed": confirmed}
    return records

def iran_append_verification(latest_path, records):
    try:
        with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
            gl = json.load(f).get("glossary") or {}
    except Exception:
        gl = {}
    L = ["", "## 事件核验（跨源印证 · 声明与行动 · 术语对照）", ""]
    iran_annotate_corroboration(records)
    corr = [r for r in records if isinstance(r.get("corroboration"), dict)]
    multi = [r for r in corr if r["corroboration"]["confirmed"]]
    single = [r for r in corr if not r["corroboration"]["confirmed"]]
    L.append(f"- 跨源印证：**{len(multi)}** 条已印证（≥2 个发布方或 T1 原始出处），"
             f"**{len(single)}** 条单源待证。")
    for r in sorted(multi, key=lambda x: -x["corroboration"]["n_publishers"])[:10]:
        c = r["corroboration"]
        L.append(f"- {str(r.get('published') or '—')[:10]} · {(r.get('title') or '')[:88]}"
                 f" · {r.get('publisher', '?')} / {r.get('tier', '?')}"
                 f" · 印证方 {c['n_publishers']} 个：{'、'.join(c['publishers'])[:110]}")
    # 声明 vs 行动：本项目的 claim_kinds 就是「谁说了什么」
    ck = {}
    for r in records:
        for k in (r.get("claim_kinds") or []):
            ck[k] = ck.get(k, 0) + 1
    if ck:
        L += ["", "**主张类型分布（声明 ≠ 行动）**：", ""]
        for k, v in sorted(ck.items(), key=lambda x: -x[1]):
            L.append(f"- `{k}` {v} 条")
    L += ["", "> 口径提醒：表态/主张只代表**说了什么**；`rss` 未分类条目不参与性质判断。"]
    hits = {}
    for lang, table in gl.items():
        for term, zh in table.items():
            pat = re.compile(re.escape(term) + r"\w*", re.I)
            n = sum(1 for r in records
                    if pat.search((r.get("title") or "") + " " + (r.get("summary") or "")))
            if n:
                hits[(term, zh, lang)] = n
    if hits:
        L += ["", "**多语种术语对照（本窗口命中）**：", ""]
        for (t, zh, lang), n in sorted(hits.items(), key=lambda x: -x[1])[:40]:
            L.append(f"- {t} = {zh}（{lang}）· 命中 {n} 条")
    try:
        with open(latest_path, "a", encoding="utf-8") as f:
            f.write("\n".join(L) + "\n")
    except Exception:
        pass


def write_outputs(records_all: List[Dict[str, Any]], records_new: List[Dict[str, Any]],
                  statuses: List[Dict[str, Any]], run_dt: datetime,
                  window_desc: str, out_dir: Optional[str] = None) -> Dict[str, Any]:
    day = run_dt.strftime("%Y-%m-%d")
    base = out_dir or os.path.join(OUTPUT_DIR, day)
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, f"records-{day}.jsonl")
    merged, fresh = merge_records(path, records_all, records_new)
    # ★★ 快照也必须按**当前**词表回填，不能只回填累积档。
    #  实测踩过的坑：`write_all_accumulated` 回填了累积档，但这里没有 ——
    #  于是同一次运行产出的两个文件口径不同：快照里混着「旧词表算的」记录
    #  （本次没被重抓、直接从旧快照继承的那些），而**报告是基于快照生成的**。
    #  症状极隐蔽：§6 的命中词条显示 `deni`（已改名的旧词条）、
    #  信号桶计数偏低，而日志、条数、状态码全部正常。
    _bf_snap = backfill_derived(merged)
    merged.sort(key=lambda x: (x.get("published") or ""), reverse=True)
    write_jsonl(path, merged)
    with open(os.path.join(base, f"records-{day}.json"), "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)

    cross = build_crosscheck(merged)
    weekly = build_weekly(merged)
    # 被展示闸门折叠的记录逐条留档 —— 收紧词表最容易连真信号一起删掉，
    # 没有留档就无法复核是「真无关」还是「删过头」。
    # ★ 计数走全量（classify_folded），只有**明细**按 FOLD_SAMPLE_LIMIT 截断。
    refresh_gate_audit(merged)
    # ★ 把「该源在**本次快照**里的条数」写回 status。
    #  没有这一列就会出现读者无法解释的矛盾：某源本轮抓取失败（§2 显示 ❌ 0 条），
    #  但它的历史记录仍在快照里、仍计入 §1 统计 —— 只看 §2 会以为「这个源没数据」，
    #  进而误判某一方的采集面被高估或低估。
    _snap: Dict[str, int] = {}
    for r in merged:
        sid_ = r.get("source_id") or ""
        _snap[sid_] = _snap.get(sid_, 0) + 1
    for s in statuses:
        s["in_snapshot"] = _snap.get(s["id"], 0)

    digest = render_digest(merged, statuses, cross, weekly, run_dt, fresh,
                           len(merged), window_desc)
    with open(os.path.join(base, f"iran-digest-{day}.md"), "w", encoding="utf-8") as f:
        f.write(digest)
    with open(os.path.join(base, f"crosscheck-{day}.md"), "w", encoding="utf-8") as f:
        f.write(render_crosscheck_md(cross, run_dt))
    with open(os.path.join(base, f"crosscheck-{day}.json"), "w", encoding="utf-8") as f:
        json.dump(cross, f, ensure_ascii=False, indent=1)
    with open(os.path.join(base, f"weekly-{day}.json"), "w", encoding="utf-8") as f:
        json.dump(weekly, f, ensure_ascii=False, indent=1)

    if not out_dir:
        # 全局文件
        with open(os.path.join(OUTPUT_DIR, "LATEST.md"), "w", encoding="utf-8") as f:
            f.write(digest)
        iran_append_verification(os.path.join(OUTPUT_DIR, "LATEST.md"), records_all)
        with open(os.path.join(OUTPUT_DIR, "LATEST-crosscheck.md"), "w", encoding="utf-8") as f:
            f.write(render_crosscheck_md(cross, run_dt))
        total = write_all_accumulated(
            os.path.join(OUTPUT_DIR, "ALL-records.jsonl"), merged,
            run_day=day)
        with open(os.path.join(OUTPUT_DIR, "_source_status.json"), "w", encoding="utf-8") as f:
            json.dump({"run_at": run_dt.isoformat(), "proxy": proxy_report(),
                       "sources": statuses, "tls_downgraded": TLS_DOWNGRADED},
                      f, ensure_ascii=False, indent=1)
        with open(os.path.join(OUTPUT_DIR, "_gate_audit.json"), "w", encoding="utf-8") as f:
            json.dump(GATE_AUDIT, f, ensure_ascii=False, indent=1)
    else:
        total = len(merged)

    # ★ 展示数与折叠数在**同一集合**（merged 快照）上统计，保证
    #  displayed + folded == merged，日志与报告 §1 永远自洽。
    _disp_n = sum(1 for r in merged if is_displayable(r))
    return {"day": day, "dir": base, "merged": len(merged), "fresh": fresh,
            "displayed": _disp_n, "folded": len(merged) - _disp_n,
            "cross": cross, "weekly": weekly, "digest": digest,
            "accumulated": total}


def write_index(run_dt: datetime) -> None:
    days = []
    if os.path.isdir(OUTPUT_DIR):
        for d in sorted(os.listdir(OUTPUT_DIR), reverse=True):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
                days.append(d)
    L = ["# 归档索引", "",
         f"最新更新：{run_dt.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
         "", "## 每日产出", ""]
    for d in days[:60]:
        L.append(f"- **{d}**　→　`{d}/iran-digest-{d}.md` · "
                 f"`{d}/crosscheck-{d}.md`")
    L += ["", "## 全局文件", "",
          "| 文件 | 内容 |", "|---|---|",
          "| `LATEST.md` | 最新日报副本 |",
          "| `LATEST-crosscheck.md` | 最新交叉验证明细 |",
          "| `ALL-records.jsonl` | **累积总档**（按 id 去重、永不淘汰、带 `first_seen`） |",
          "| `_source_status.json` | 各源状态、HTTP 码、停止更新天数、TLS 降级清单 |",
          "| `_gate_audit.json` | 被闸门挡下的候选逐条留档 + **全量**折叠分类计数（可复核是否删过头） |",
          "| `_verify_last.json` | 最近一次回源对账结果（报告数字能否由落盘数据复算） |", ""]
    with open(os.path.join(OUTPUT_DIR, "index.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))


# ================================================================ 自检
def selftest() -> int:
    ok = 0
    bad = 0
    fails: List[str] = []

    def check(name: str, got: Any, want: Any) -> None:
        nonlocal ok, bad
        if got == want:
            ok += 1
        else:
            bad += 1
            fails.append(f"{name}\n     期望: {want!r}\n     实际: {got!r}")

    print("=" * 92)
    print(f"离线自检 v{VERSION}（不联网）")
    print("=" * 92)

    # --- 1. 波斯语归一化（ZWNJ / 字符变体 / 阿拉伯数字）
    check("归一 ZWNJ→空格", norm_text("آمادگی\u200cکامل"), "آمادگی کامل")
    check("归一 阿拉伯 ی", norm_text("رزمي"), norm_text("رزمی"))
    check("归一 阿拉伯 ک", norm_text("کوه"), norm_text("کوه"))
    check("归一 波斯数字", norm_text("۱۰۷"), "107")
    check("归一 HTML 实体", norm_text("&amp;"), "&")
    check("波斯语命中（ZWNJ 变体）",
          len(find_hits(norm_text("ما آمادگی\u200cرزمی داریم"), ["آمادگی رزمی"])), 1)

    # --- 2. 拉丁词边界（词表最常翻车的地方）
    check("边界 Patriot 不命中 patriotic",
          len(find_hits(norm_text("a patriotic song"), ["Patriot"])), 0)
    check("边界 Stinger 不命中 stinging",
          len(find_hits(norm_text("a stinging remark"), ["Stinger"])), 0)
    check("Iran 不误命中 Iranian（应分别配置）",
          len(find_hits(norm_text("Iranian forces"), ["Iran"])), 0)
    check("Iranian 单独配置可命中",
          len(find_hits(norm_text("Iranian forces"), ["Iranian"])), 1)
    check("复数 s? 生效 drone→drones",
          len(find_hits(norm_text("three drones"), ["drone"])), 1)
    check("多词短语命中", len(find_hits(norm_text("a carrier strike group"), ["carrier strike group"])), 1)
    check("长词优先（carrier strike group 不被 carrier 抢先）",
          [h[0] for h in find_hits(norm_text("carrier strike group"), ["carrier", "carrier strike group"])],
          ["carrier strike group"])

    # --- 3. 信号抽取 + context 可核验
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        loaded = json.load(f)
    global SIG_CFG, REL_CFG, CLAIM_CFG, CROSS_ANCHORS, ZONES
    SIG_CFG = loaded["signals"]
    REL_CFG = loaded["relevance"]
    CLAIM_CFG = loaded["claims"]
    CROSS_ANCHORS = loaded["cross_anchors"]
    ZONES = loaded["zones"]

    sigs = signals_of("IRGC Ground Forces chief vows strong response; forces at "
                      "highest state of readiness", "")
    got = {s["bucket"] for s in sigs}
    check("信号 战备状态声明命中 readiness_claim", "readiness_claim" in got, True)
    check("信号 威慑/报复威胁命中 deterrence_threat", "deterrence_threat" in got, True)
    check("信号 每条都带非空 context", all(s["context"].strip() for s in sigs), True)

    def _ctx_has_phrase(s: Dict[str, Any]) -> bool:
        """context 必须真的含命中词 —— 用**同一套匹配器**回验，而不是子串比较。

        ★ 不能用 `phrase in context`：`require_all` 桶的 phrase 形如
          "destroy + u.s. navy"，整串当然不在 context 里；而单维桶存的是
          **词表原形**（destroy），context 里是**实际词形**（Destroys）。
          两者都不等价于「context 里能匹配到」。
        """
        ctx = norm_text(s["context"])
        return all(find_hits(ctx, [part]) for part in s["phrase"].split(" + "))

    check("信号 context 可用同一匹配器回验到命中词",
          all(_ctx_has_phrase(s) for s in sigs), True)
    sigs_fa = signals_of("سپاه در بالاترین سطح آمادگی رزمی است", "")
    check("信号 波斯语战备表态命中",
          any(s["bucket"] == "readiness_claim" for s in sigs_fa), True)
    sigs_ex = signals_of("the two navies held a joint naval exercise in the Gulf", "")
    check("信号 演习桶命中", any(s["bucket"] == "exercise" for s in sigs_ex), True)

    # --- 3b. `require_all` 二维命中：本主题**唯一**的精度机制 ★★
    #  没有它，`attack` 一个词就会命中「一切攻击新闻」——
    #  胡塞打沙特、以色列打黎巴嫩、乌克兰无人机，全都会被算成
    #  「打击美军海上作战平台」，而这正是本项目的核心交付域之一。
    _naval_ok = signals_of("Houthis attack a US Navy destroyer in the Red Sea", "")
    check("require_all 命中：动作 + 美方海上平台",
          any(s["bucket"] == "us_naval_attack" for s in _naval_ok), True)
    check("require_all 记录带标记与合并短语",
          all(s.get("require_all") for s in _naval_ok
              if s["bucket"] == "us_naval_attack"), True)
    _naval_bad = signals_of("Houthis attack a Saudi oil tanker in the Red Sea", "")
    check("require_all 不命中：有攻击动作但无美方海上平台（不得误判）★★",
          any(s["bucket"] == "us_naval_attack" for s in _naval_bad), False)
    _naval_bad2 = signals_of("The US Navy held a change of command ceremony", "")
    check("require_all 不命中：有美方海上平台但无攻击动作（不得误判）★★",
          any(s["bucket"] == "us_naval_attack" for s in _naval_bad2), False)
    #  同型检查覆盖另外三个 require_all 域，防止只有一处写对
    _hz_ok = signals_of("Iran threatens to close the Strait of Hormuz", "")
    _hz_bad = signals_of("Iran threatens to close its airspace to inspectors", "")
    check("require_all 霍尔木兹：动作+海峡命中",
          any(s["bucket"] == "hormuz_threat" for s in _hz_ok), True)
    check("require_all 霍尔木兹：无海峡词时不命中 ★",
          any(s["bucket"] == "hormuz_threat" for s in _hz_bad), False)
    _cab_ok = signals_of("Saboteurs cut an undersea cable in the Red Sea", "")
    _cab_bad = signals_of("Saboteurs cut the fence at the pipeline terminal", "")
    check("require_all 光缆：切断+光缆命中",
          any(s["bucket"] == "cable_threat" for s in _cab_ok), True)
    check("require_all 光缆：无光缆词时不命中 ★",
          any(s["bucket"] == "cable_threat" for s in _cab_bad), False)
    _en_ok = signals_of("Drone strike damaged an oil refinery in Saudi Arabia", "")
    _en_bad = signals_of("Drone strike damaged a hospital in Saudi Arabia", "")
    check("require_all 能源：打击+能源设施命中",
          any(s["bucket"] == "energy_strike" for s in _en_ok), True)
    check("require_all 能源：无能源设施词时不命中 ★",
          any(s["bucket"] == "energy_strike" for s in _en_bad), False)

    # --- 3c. 七个关注域的配置完整性（域 → 桶 双向可达）★★
    _zone_ids = [z["id"] for z in ZONES]
    check("关注域 共 7 个（六大关注域 + 前瞻）", len(_zone_ids), 7)
    check("关注域 顺序为 1..7",
          sorted(int(z.get("order", 0)) for z in ZONES), list(range(1, 8)))
    _bucket_zones = {zid for zid in (s.get("zone") for s in SIG_CFG.values()) if zid}
    check("关注域 每个域都至少有一个信号桶（无空域）★★",
          sorted(set(_zone_ids) - _bucket_zones), [])
    check("关注域 每个桶都归属到已定义的域（无孤儿桶）★★",
          sorted(_bucket_zones - set(_zone_ids)), [])
    check("关注域 用户指定的六大域全部存在 ★",
          sorted({"naval_platform", "chokepoint", "energy_infra", "cable",
                  "nuclear", "readiness"} - set(_zone_ids)), [])
    check("前瞻域 由 FORESIGHT_BUCKETS 单列而非混入六大域",
          sorted(b for b in SIG_CFG if SIG_CFG[b].get("zone") == "foresight"),
          sorted(FORESIGHT_BUCKETS))

    # --- 4. 声明分类
    check("声明 宣称", "action_claim" in claims_of("CENTCOM destroyed 3 IRGC tankers"), True)
    check("声明 否认", "denial" in claims_of("CENTCOM refutes media claims"), True)
    check("声明 战备表态",
          "readiness_declaration" in claims_of("forces at highest state of readiness"), True)
    check("声明 威胁", "threat_warning" in claims_of("Iran will respond with crushing response"), True)
    check("声明 承认", "acknowledgement" in claims_of("US officials confirmed three sailors were wounded"), True)
    check("声明 前瞻打算（foresight_plan）",
          "foresight_plan" in claims_of("Iran is preparing a new phase of operations"), True)
    check("声明 归因",
          "attribution" in claims_of("Houthis accused the US of backing the blockade"), True)
    check("声明 外交谈判",
          "negotiation_diplomacy" in claims_of("Talks on a ceasefire resumed in Muscat"), True)
    check("声明 无关句为空", claims_of("rain expected tomorrow"), [])

    # --- 5. 相关性分级
    r1 = relevance_of("US carrier strike group deploys to Persian Gulf", "")
    r2 = relevance_of("New tiger cat species identified in Bolivia", "")
    check("相关性 强相关得分高于无关", r1["score"] > r2["score"], True)
    check("相关性 无关记录得 0 分", r2["score"], 0)
    r3 = relevance_of("Iran threatens to cut undersea cables in the Strait of Hormuz", "")
    check("相关性 六大域关键词组合得分显著更高", r3["score"] > r1["score"], True)
    check("相关性 分值由四类词条分别计数",
          all(k in r3 for k in ("strong", "medium", "anchor", "domain")), True)
    check("相关性 命中锚点 id 会被记录",
          "hormuz" in r3["anchor_ids"], True)

    # --- 6. 展示闸门（展示与统计必须同源）
    global OUTLET_MAP, SOURCE_BY_ID
    OUTLET_MAP = loaded["outlet_map"]
    SOURCE_BY_ID = {s["id"]: s for s in loaded["sources"]}
    SRCTOPIC = {"party": "us", "tier": "T1", "publisher": "p",
                "topic_anchored": True}
    SRCGEN = {"party": "us", "tier": "T2", "publisher": "p",
              "topic_anchored": False}

    def mk_src(rid, src, title, pub="2026-09-17T00:00:00+00:00", summary="",
               item_source=""):
        return enrich({"id": rid, "source_id": src.get("id", "x"), "title": title,
                       "summary": summary, "link": "u", "published": pub,
                       "item_source": item_source}, src)

    rec_ok = mk_src("a", dict(SRCTOPIC, id="dod-releases"),
                    "IRGC at full combat readiness")
    rec_no = mk_src("b", dict(SRCGEN, id="gao-reports"),
                    "Weather forecast for Tehran", summary="sunny")
    check("闸门 战备记录可展示", is_displayable(rec_ok), True)
    check("闸门 无关记录被折叠", is_displayable(rec_no), False)

    # ★ 真实踩过的假阳性：GAO 的《印第安人住房》被裸词 targeted 算成「打击/战果声明」
    fp = mk_src("fp", dict(SRCGEN, id="gao-reports"),
                "Native American Issues: Preliminary Observations on Housing",
                summary="30 federal programs can provide housing support targeted "
                        "to native american communities, and the budget was "
                        "insufficient to meet their needs")
    check("闸门 泛化词假阳性已被消除（GAO 住房报告）", is_displayable(fp), False)
    # ★ 泛化词（attack / strike / target / hit）**只允许出现在 require_all 组里**。
    #  这是本主题的精度命门：它们一旦成为单维桶的词条，就会命中一切攻击新闻。
    #  readiness-monitor 里同型的坑是把 `targeted` 写进 strike_claim 单维桶，
    #  于是 GAO 的住房报告成了「美方战果声明」。这里改成**结构性不变量**，
    #  一次覆盖所有泛化词，而不是逐个桶写死断言（写死会随词表改名而失效）。
    GENERIC = {"attack", "strike", "target", "targeted", "hit",
               "damage", "destroy", "assault", "engage"}
    _leaked: List[str] = []
    for _b, _spec in SIG_CFG.items():
        if _spec.get("require_all"):
            continue        # 二维桶里出现泛化词是**设计意图**，允许
        for _p in (_spec.get("phrases") or []):
            if _p.lower() in GENERIC:
                _leaked.append(f"{_b}:{_p}")
    check("词表 泛化动作词只允许出现在 require_all 桶中 ★★", sorted(set(_leaked)), [])
    check("词表 targeted 未回填进单维桶",
          len(find_hits(norm_text("targeted to native american communities"),
                        [p for _b, _s in SIG_CFG.items() if not _s.get("require_all")
                         for p in (_s.get("phrases") or [])])), 0)
    check("词表 未设降级/撤退单维桶（本项目主题为升级，不设降级桶）★★",
          not any(k in SIG_CFG for k in ("deescalation", "withdrawal", "ceasefire")), True)
    check("词表 演习桶不吸收裸词 maneuver（会命中一切机动新闻）",
          "maneuver" not in [p.lower() for p in
                             (SIG_CFG.get("chokepoint_drill") or {}).get("phrases", [])],
          True)
    check("词表 enemy/warns 已从威慑桶剔除（避免军事媒体全命中）",
          len(find_hits(norm_text("commander warns about the enemy"),
                        SIG_CFG["deterrence_threat"]["phrases"])), 0)
    # ★ 核试验是本项目权重最高的桶（w=6），必须真的命中真实标题
    check("词表 核试验桶命中真实标题",
          any(s["bucket"] == "nuclear_test" for s in signals_of(
              "Iran conducts underground nuclear test at depth, monitors say", "")), True)

    # ★ 收紧不能误杀：**六大关注域 + 前瞻域**各留一条真实标题形态的用例。
    #  本主题最大的风险不是「多几条」，而是收紧词表时把整个域删到归零 ——
    #  少一条证据可能直接改变结论方向（readiness-monitor 实测过：
    #  证据板从「+7 温和偏向」翻成「±3 多空交织」）。
    #  因此每个域都要有一条**端到端**的用例，而不只是断言桶存在。
    domain_cases = [
        ("海上平台·打击宣称", "naval_platform", "us_naval_attack",
         "CENTCOM denies Iranian claim that IRGC struck two US Navy destroyers"),
        ("海上平台·美方增援", "naval_platform", "us_naval_reinforce",
         "US sends second carrier strike group toward the Middle East"),
        ("海上平台·反舰武器", "naval_platform", "anti_ship_weapon",
         "Iran deploys anti-ship missile launchers and drone boats near the Gulf"),
        ("海峡·双线施压", "chokepoint", "dual_front",
         "Iran says it can apply dual pressure on two chokepoints"),
        ("海峡·霍尔木兹封锁", "chokepoint", "hormuz_threat",
         "IRGC threatens to close the Strait of Hormuz"),
        ("海峡·商船扣押", "chokepoint", "shipping_interdiction",
         "Houthis seized a cargo ship in the Bab el-Mandeb strait"),
        ("能源·设施打击", "energy_infra", "energy_strike",
         "Drone strike set ablaze an oil facility at Abqaiq, sources say"),
        ("能源·设施点名", "energy_infra", "energy_facility_named",
         "Sabotage reported at Kharg Island oil terminal, Iran says"),
        ("能源·断供威胁", "energy_infra", "energy_threat",
         "Iran threatens to halt oil exports through the Gulf"),
        ("光缆·切断威胁", "cable", "cable_threat",
         "Undersea cable cut in the Red Sea disrupts internet across the Gulf"),
        ("光缆·系统点名", "cable", "cable_named",
         "Damage to the AAE-1 submarine cable system reported off Oman"),
        ("核·核试验", "nuclear", "nuclear_test",
         "Seismic monitors detect possible underground nuclear test in Iran"),
        ("核·浓缩里程碑", "nuclear", "enrichment_milestone",
         "IAEA says Iran has enriched uranium to 90 percent"),
        ("核·武器化", "nuclear", "weaponization",
         "Iran now capable of producing a nuclear warhead, report warns"),
        ("核·IAEA 对峙", "nuclear", "iaea_standoff",
         "Iran rejects IAEA resolution and curb on inspectors"),
        ("战备·战备状态声明", "readiness", "readiness_claim",
         "IRGC says its forces are at the highest state of readiness"),
        ("战备·导弹试射", "readiness", "missile_drill",
         "IRGC test-fired a hypersonic ballistic missile, state TV says"),
        ("战备·战损承认", "readiness", "attrition_admit",
         "Iran admits casualties after strikes on its naval base"),
        ("前瞻·未来时威胁", "foresight", "future_threat",
         "IRGC commander says Iran will strike US bases if attacked"),
        ("前瞻·条件式最后通牒", "foresight", "condition_ultimatum",
         "US warns Iran: any aggression will be met with decisive force"),
        ("前瞻·临战迫近", "foresight", "imminent_indicator",
         "Iranian forces are in the final stages of preparations, official says"),
        ("前瞻·升级阶梯", "foresight", "escalation_ladder",
         "Tehran warns of an unprecedented step and a widening conflict"),
        ("前瞻·兵力动员", "foresight", "mobilization",
         "Iran announces mobilization of reserves and troop movement to the south"),
    ]
    for label, zid, bucket, text in domain_cases:
        _sigs = signals_of(text, "")
        check(f"回扫 域[{zid}] 真信号未误杀 · {label} [{bucket}]",
              any(s["bucket"] == bucket for s in _sigs), True)
    # ★ 桶 → 域 的映射必须与用例标签一致（防止「桶改名后挂到别的域」）
    for label, zid, bucket, text in domain_cases:
        check(f"回扫 域[{zid}] 桶归属正确 · {bucket}", zone_of_bucket(bucket), zid)


    # ★ 波斯语真信号：用**本项目词表里真实存在**的概念，而不是 legacy 用例。
    #  `فرسایش`（侵蚀）属于 readiness-monitor 的弹药损耗主题，本项目不采集它 ——
    #  留着这条断言只会是「测一个不存在的词」，看起来绿了却什么都没保证。
    check("回扫 波斯语真信号仍在：تلفات（战损承认）",
          any(s["bucket"] == "attrition_admit" for s in signals_of(
              "تلفات نیروهای دشمن در جبهه گزارش شد", "")), True)
    check("回扫 波斯语真信号仍在：آمادگی رزمی（战备状态）",
          any(s["bucket"] == "readiness_claim" for s in signals_of(
              "سپاه در آمادگی رزمی کامل است", "")), True)
    check("回扫 波斯语真信号仍在：تنگه هرمز（霍尔木兹威胁）",
          any(s["bucket"] == "hormuz_threat" for s in signals_of(
              "تهدید به بستن تنگه هرمز", "")), True)

    # 主题锚定闸门：有信号但无锚定，且源本身非国防主题 → 折叠
    nonanchor = mk_src("na", dict(SRCGEN, id="whitehouse-actions"),
                       "Withdrawals Sent to the Senate")
    check("闸门 无战备信号且未锚定的记录被折叠", is_displayable(nonanchor), False)
    #  有信号但**锚点**为 0，靠**领域词**（domain）放行 —— 这条路径必须可用：
    #  若锚点/领域两道口子任一失效，大量专业媒体的真信号会被折叠掉，
    #  而报告只会「少几条」，不会报错。
    topicwise = mk_src("tw", dict(SRCGEN, id="whitehouse-actions"),
                       "A joint naval exercise was held off the coast")
    check("闸门 无当事方锚点但有领域词则放行（naval）",
          (topicwise["relevance_parts"].get("anchor", 0),
           topicwise["relevance_parts"].get("domain", 0))[0] == 0
          and is_displayable(topicwise), True)


    # --- 6b. 聚合器溯源：按实际发布媒体重判当事方 ★
    agg = mk_src("ag1", {"id": "irgc-gnews", "party": "iran", "tier": "T3",
                         "publisher": "多源", "aggregator": True,
                         "topic_anchored": True},
                 "IRGC says it captured advanced US underwater drone near Hormuz - Iran International",
                 item_source="Iran International")
    check("溯源 流亡/反对派媒体被单列为 iran_opp", agg["party"], "iran_opp")
    check("溯源 标题后缀被剥离", agg["title"].endswith("Hormuz"), True)
    agg2 = mk_src("ag2", {"id": "irgc-gnews", "party": "iran", "tier": "T3",
                          "publisher": "多源", "aggregator": True,
                          "topic_anchored": True},
                  "IRGC Downs 52nd US MQ-9 Drone South of Iran - تسنیم",
                  item_source="تسنیم")
    check("溯源 革命卫队系媒体仍判为伊方官方", agg2["party"], "iran")
    agg3 = mk_src("ag3", {"id": "irgc-gnews", "party": "iran", "tier": "T3",
                          "publisher": "多源", "aggregator": True,
                          "topic_anchored": True},
                  "Some outlet reports on Iran - Unknown Daily",
                  item_source="Unknown Daily")
    check("溯源 未登记媒体打标记", agg3.get("outlet_unclassified"), True)
    # ★ 未登记媒体**必须**归第三方：曾经继承源默认值，把 Janes/AzerNews/KTVN 都算成了「伊方」
    check("溯源 未登记媒体归第三方而非继承源默认当事方", agg3["party"], "third")
    check("当事方归组 流亡媒体不进伊方当事方",
          party_group("iran_opp"), "third")

    # --- 7. 日期解析
    check("日期 RFC822", parse_date("Thu, 17 Sep 2026 15:18:06 GMT") is not None, True)
    check("日期 带时区偏移波斯语源",
          parse_date("18 Sep 2026 04:12:09 +0330").isoformat().startswith("2026-09-18"), True)
    check("日期 ISO", parse_date("2026-09-17T00:00:00Z") is not None, True)
    check("日期 非法返回 None", parse_date("not a date"), None)
    # ★★ 这两条是**真实源**在用、而原先解析失败的格式。失败后果是双重的静默损失：
    #  ① 进不了 §8 周趋势；② 年龄算不出来 → 绕过时间窗过滤（实测混入了 2 个月前的条目）。
    check("日期 全称星期+全称月份（crisisgroup 实际格式）★",
          (parse_date("Wednesday, September 16, 2026 - 17:32") or
           datetime(1, 1, 1, tzinfo=timezone.utc)).strftime("%Y-%m-%d"),
          "2026-09-16")
    check("日期 两位数年份（IAEA 实际格式）★",
          (parse_date("Wed, 29 Jul 26 12:37:14 +0200") or
           datetime(1, 1, 1, tzinfo=timezone.utc)).strftime("%Y-%m-%d"),
          "2026-07-29")
    # ★★ 两位数年份**必须先展开**：strptime 的 %Y 是宽松的（接受 1–4 位），
    #   `Wed, 29 Jul 26 …` 会被直接解析成**公元 26 年**，且返回成功、不报错。
    #   那样记录会被当作「极旧」而被时间窗排除，同时把时间轴拉出两千年。
    _d26 = parse_date("Wed, 29 Jul 26 12:37:14 +0200")
    check("日期 两位数年份不得被解析成公元 26 年 ★★",
          (_d26 is None) or _d26.year >= 2000, True)
    #  年份合理性兜底：「解析成功但年份离谱」比「解析失败」危险得多 ——
    #  失败会被看见（§2 标注 `源内最新=—`），离谱的年份会安静地把记录放到时间轴两端。
    check("日期 年份早于 2000 的一律判失败 ★",
          parse_date("Wed, 29 Jul 1826 12:37:14 +0200"), None)
    check("日期 ISO 带 Z 与微秒",
          parse_date("2026-09-17T08:30:00.123456Z") is not None, True)
    check("日期 空字符串返回 None", parse_date(""), None)
    # ★★ 非英语月份/星期：西语源（crisisgroup）本来就在用，原先一律解析失败。
    #   这类失败**不会**报错，只会让记录「无声地」掉出周表并绕过时间窗。
    check("日期 西语全称星期+月份（crisisgroup 西语版实际格式）★",
          (parse_date("Viernes, Septiembre 11, 2026 - 09:47") or
           datetime(1, 1, 1, tzinfo=timezone.utc)).strftime("%Y-%m-%d"),
          "2026-09-11")
    check("日期 法语月份", (parse_date("Jeudi, 17 septembre 2026 15:18") or
                          datetime(1, 1, 1, tzinfo=timezone.utc)).strftime("%Y-%m-%d"),
          "2026-09-17")
    check("日期 德语月份", (parse_date("17. September 2026 15:18") or
                          datetime(1, 1, 1, tzinfo=timezone.utc)).strftime("%Y-%m-%d"),
          "2026-09-17")
    check("非英语别名不误伤英文月份",
          parse_date("Thu, 17 Sep 2026 15:18:06 GMT") is not None, True)
    # ★★★ 纪元零值 `Thu, 01 Jan 1970` 是「无日期」哨兵，不是「一个很旧的日期」。
    #   当日期处理会让它 ① 印成 `Thu, 01 Ja` 半截垃圾；② 永远过不了窗口判定 →
    #   只要进过一次快照就**永久滞留**（重跑也清不掉）。必须显式识别。
    _EPOCH = "Thu, 01 Jan 1970 00:00:00 GMT"
    check("纪元零值被识别为无日期哨兵 ★★", is_epoch_sentinel(_EPOCH), True)
    check("纪元零值不当作日期 ★★", parse_date(_EPOCH), None)
    check("纪元零值变体（Thursday / 不同空格）★",
          (is_epoch_sentinel("Thursday, 1 Jan 1970 00:00:00 GMT"),
           is_epoch_sentinel("Thu, 01 Jan  1970 00:00:00 GMT")), (True, True))
    check("正常日期不被误判为纪元零值", is_epoch_sentinel("Thu, 01 Jan 2026 00:00:00 GMT"), False)
    #   日期单元格：无法定位时间轴时印 `—`，绝不印截断后的原始字符串
    check("日期单元格 正常日期取前 10 位",
          date_cell("2026-09-17T08:30:00+00:00"), "2026-09-17")
    check("日期单元格 无日期印 —（不得印 `Thu, 01 Ja` 这类半截垃圾）★",
          date_cell(_EPOCH), "—")
    check("日期单元格 空值印 —", (date_cell(""), date_cell(None)), ("—", "—"))
    check("机器可读口径 无日期返回空串而非 —", date_iso(_EPOCH), "")

    # --- 8. feed 解析
    sample = """<rss version="2.0"><channel><title>T</title>
      <item><title><![CDATA[CENTCOM destroys 3 IRGC tankers]]></title>
      <link>https://example.invalid/a</link>
      <description><![CDATA[<p>US forces &amp; allies struck</p>]]></description>
      <pubDate>Tue, 08 Sep 2026 07:00:00 GMT</pubDate></item>
      <item><title>Second &amp; item</title><link>https://example.invalid/b</link>
      <pubDate>Mon, 07 Sep 2026 07:00:00 GMT</pubDate></item>
      </channel></rss>"""
    items = parse_feed(sample)
    check("feed 解析条数", len(items), 2)
    check("feed CDATA 与实体解码", items[0]["title"], "CENTCOM destroys 3 IRGC tankers")
    check("feed 摘要去标签", items[0]["summary"], "US forces & allies struck")
    check("feed 第二条实体解码", items[1]["title"], "Second & item")

    # --- 9. 跨天落盘不塌（形态 B）★
    import tempfile
    tmp = tempfile.mkdtemp(prefix="rm_selftest_")
    d1 = os.path.join(tmp, "2026-09-17")
    d2 = os.path.join(tmp, "2026-09-18")
    allrec = [{"id": f"r{i}", "title": f"t{i}", "published": f"2026-09-1{i%5}T00:00:00+00:00",
               "signals": [{"bucket": "readiness_claim", "label": "L", "weight": 3,
                            "phrase": "readiness", "context": "ctx"}]}
              for i in range(20)]
    newrec = allrec[:2]
    st = [{"id": "s", "party": "us", "channel": "", "publisher": "p", "tier": "T1",
           "enabled": True, "state": "ok", "entries": 20, "latest": None,
           "age_days": 0, "http_status": 200, "server": "", "note": "",
           "url_used": "", "tls": "verified", "attempts": 1}]
    r1 = write_outputs(allrec, allrec, st, datetime(2026, 9, 17, tzinfo=timezone.utc),
                       "test", out_dir=d1)
    check("落盘 首日 20 条", r1["merged"], 20)
    r2 = write_outputs(allrec, allrec, st, datetime(2026, 9, 17, tzinfo=timezone.utc),
                       "test", out_dir=d1)
    check("落盘 同日重跑不缩水（形态 A）", r2["merged"], 20)
    r3 = write_outputs(allrec, newrec, st, datetime(2026, 9, 18, tzinfo=timezone.utc),
                       "test", out_dir=d2)
    check("落盘 跨天不塌成「只剩新增」（形态 B）★", r3["merged"], 20)
    check("落盘 跨天仍有 20 条记录", len(r3["digest"]) > 100, True)

    # --- 9b. 窗口修剪：掉出窗口的旧记录不得**永久滞留**快照 ★★
    #  保留旧快照记录的理由是「它仍在窗口内、却已从 feed 列表掉出去，抓不回来」；
    #  一旦它掉出窗口，保留就变成永久污染 —— 实测一次日期解析缺陷把两个月前的
    #  条目放进来后，**重跑也清不掉**：重跑时它们既不在新抓取结果里（已被窗口
    #  过滤）、又已在旧快照里。症状是窗口快照里长期混着远古条目，而一切看着正常。
    #  ★ 两次调用必须用**同一天**的 run_dt，否则第二次读不到第一次的快照，
    #    修剪路径根本没被走到（测试会假绿）。
    d3 = os.path.join(tmp, "prune")
    _day_dt = datetime(2026, 9, 18, tzinfo=timezone.utc)
    _stale = [{"id": "oldout", "title": "two months ago", "signals": [],
               "published": "2026-07-01T00:00:00+00:00"},
              {"id": "oldin", "title": "yesterday", "signals": [],
               "published": (now_utc() - timedelta(days=1)).isoformat()}]
    write_outputs(_stale, _stale, st, _day_dt, "test", out_dir=d3)
    # 本轮抓取里**没有**这两条（模拟「已从 feed 掉出列表」），其中一条已超窗
    r4 = write_outputs(allrec[:3], allrec[:3], st, _day_dt, "test", out_dir=d3)
    _ids4 = {x["id"] for x in read_jsonl(
        os.path.join(d3, f"records-{_day_dt.strftime('%Y-%m-%d')}.jsonl"))}
    check("窗口修剪 掉出窗口的旧记录不再滞留快照 ★★", "oldout" in _ids4, False)
    check("窗口修剪 仍在窗口内、但抓不回来的旧记录被保留 ★★", "oldin" in _ids4, True)
    check("窗口修剪 本轮抓取的记录正常并入",
          {"r0", "r1", "r2"} <= _ids4, True)
    check("窗口修剪 修剪后快照条数自洽", r4["merged"], len(_ids4))
    # --- 9c. 无日期记录：只给一轮机会，不得跨轮永久滞留 ★★
    #  这是窗口规则唯一的**例外口子**：无日期 ⇒ 算不出年龄 ⇒ 永远不满足
    #  「超出窗口」⇒ 只要进过一次快照就永久滞留。实测 Google News 对无日期条目
    #  填纪元零值 `Thu, 01 Jan 1970`，3 条这样的记录会在快照里长期占位。
    #  正确行为：① 仍在 feed 里（本轮抓取结果里有）→ 照常收录；
    #            ② 已从 feed 掉出（本轮结果里没有）→ 从快照移除。
    d4 = os.path.join(tmp, "undated")
    _und_stale = [{"id": "und_gone", "title": "no date, dropped from feed",
                   "signals": [], "published": "", "date_unknown": True},
                  {"id": "und_here", "title": "no date, still in feed",
                   "signals": [], "published": "", "date_unknown": True}]
    write_outputs(_und_stale, _und_stale, st, _day_dt, "test", out_dir=d4)
    _und_now = [{"id": "und_here", "title": "no date, still in feed",
                 "signals": [], "published": "", "date_unknown": True},
                {"id": "fresh1", "title": "dated", "signals": [],
                 "published": (now_utc() - timedelta(hours=2)).isoformat()}]
    r5 = write_outputs(_und_now, _und_now, st, _day_dt, "test", out_dir=d4)
    _ids5 = {x["id"] for x in read_jsonl(
        os.path.join(d4, f"records-{_day_dt.strftime('%Y-%m-%d')}.jsonl"))}
    check("无日期 已从 feed 掉出的不再跨轮携带 ★★", "und_gone" in _ids5, False)
    check("无日期 仍在 feed 里的照常收录 ★", "und_here" in _ids5, True)
    check("无日期 清理后快照条数自洽", r5["merged"], len(_ids5))
    #  日期不可解析的**历史**记录（没有 `date_unknown` 标记的老档，原始字符串形态）
    #  仍按原策略保留：无法判定年龄，宁可留档并在 §8 披露，也不能凭猜测删。
    d5 = os.path.join(tmp, "undated_legacy")
    write_outputs([{"id": "legacy_und", "title": "legacy no date", "signals": [],
                    "published": "Thu, 01 Jan 1970 00:00:00 GMT"}],
                  [], st, _day_dt, "test", out_dir=d5)
    r6 = write_outputs([], [], st, _day_dt, "test", out_dir=d5)
    _ids6 = {x["id"] for x in read_jsonl(
        os.path.join(d5, f"records-{_day_dt.strftime('%Y-%m-%d')}.jsonl"))}
    check("无日期 无标记的历史未解析日期记录仍保留（不凭猜测删）",
          "legacy_und" in _ids6, True)

    # --- 10. 交叉验证配对
    def mk(rid, party, title, pub):
        return enrich({"id": rid, "source_id": "s", "title": title, "summary": "",
                       "link": "u", "published": pub},
                      {"party": party, "tier": "T1", "publisher": "p"})
    cross = build_crosscheck([
        mk("c1", "iran", "IRGC says it struck a US Navy destroyer in the Strait of Hormuz",
           "2026-09-17T00:00:00+00:00"),
        mk("c2", "us", "CENTCOM denies that a US Navy destroyer was struck near Hormuz",
           "2026-09-18T00:00:00+00:00"),
        mk("c3", "us", "CENTCOM: Hormuz remains open, US Navy at full combat readiness",
           "2026-09-10T00:00:00+00:00"),
        mk("c4", "third", "Undersea cable in the Red Sea damaged, monitors say",
           "2026-09-17T00:00:00+00:00"),
    ])
    a_un = cross["anchors"].get("us_naval", {}).get("rows", [])
    check("交叉 发现 us_naval 锚点", len(a_un) >= 1, True)
    check("交叉 敌对表述被标 contradiction",
          any(r["cls"] == "contradiction" for r in a_un), True)
    a_hz = cross["anchors"].get("hormuz", {}).get("rows", [])
    check("交叉 单方提及标 us_only", any(r["cls"] == "us_only" for r in a_hz), True)
    check("交叉 third_only 的组内当事方计数为 0（第三方不顶替当事方）★",
          all(r["us"] == 0 and r["iran"] == 0
              for r in cross["anchors"].get("subsea_cable", {}).get("rows", [])
              if r["cls"] == "third_only"), True)
    check("交叉 仅第三方旁证的议题标 third_only ★",
          any(r["cls"] == "third_only"
              for r in cross["anchors"].get("subsea_cable", {}).get("rows", [])), True)

    # --- 11. 周趋势：未收录周不是 0 ★
    global CFG
    CFG = loaded          # 让 build_weekly 能读到 history.start
    wk = build_weekly([mk("w1", "us", "US forces at full combat readiness",
                          "2026-09-17T00:00:00+00:00")])
    target_wk = iso_week(datetime(2026, 9, 17, tzinfo=timezone.utc))
    tgt = [w for w in wk if w["week"] == target_wk]
    check("周趋势 目标周存在", len(tgt), 1)
    check("周趋势 目标周已覆盖且有 1 条",
          bool(tgt) and tgt[0]["covered"] and tgt[0]["parties"]["us"]["docs"], 1)
    check("周趋势 早于历史起点的周标为未收录（空≠零）",
          any(not w["covered"] for w in wk), True)
    check("周趋势 未收录周不带数据", all(w["parties"] is None for w in wk
                                          if not w["covered"]), True)
    dg_w = render_digest([mk("w4", "us", "US forces at full combat readiness",
                             "2026-09-17T00:00:00+00:00")],
                         [{"id": "s", "party": "us", "channel": "", "publisher": "p",
                           "tier": "T1", "enabled": True, "state": "ok", "entries": 1,
                           "latest": None, "age_days": 0, "http_status": 200,
                           "server": "", "note": "", "url_used": "", "tls": "verified",
                           "attempts": 1}],
                         {"anchors": {}, "pairs": [], "stats": {}}, wk,
                         datetime(2026, 9, 18, tzinfo=timezone.utc), 1, 1, "test")
    check("周趋势 未收录周渲染为 —", "⚪ 未收录" in dg_w, True)

    # --- 11b. 源清单一致性（本项目用自己的实测证据，不用 legacy 的备用 URL 字段）
    #  ★ 同一 feed 以两个不同 URL（如 `/rss` 与 `/rss?lang=en`）被登记两次时，
    #    它的每条记录都会进两次快照 —— 计数虚高、交叉矩阵重复，
    #    而日志与状态码全部正常。构建期已按 id 去重（`_2` 别名一律丢弃），这里守住。
    check("源清单 无 _2 别名重复 feed ★★",
          [s2["id"] for s2 in loaded["sources"] if s2["id"].endswith("_2")], [])
    check("源清单 无完全相同的 URL 被登记两次 ★★",
          sorted({s2["url"] for s2 in loaded["sources"]
                  if [x["url"] for x in loaded["sources"]].count(s2["url"]) > 1}), [])
    check("源清单 启用源都带实测证据（probe.date）",
          [s2["id"] for s2 in loaded["sources"]
           if s2.get("enabled") and not (s2.get("probe") or {}).get("date")], [])
    check("源清单 启用源都有非空 url",
          [s2["id"] for s2 in loaded["sources"]
           if s2.get("enabled") and not (s2.get("url") or "").strip()], [])
    check("源清单 outlet_map 有规则（聚合器需要按实际发布媒体重判当事方）",
          len((loaded.get("outlet_map") or {}).get("rules") or []) > 0, True)
    check("源清单 聚合器源都有非空默认当事方（用于未登记媒体兜底前的初值）",
          all(s2.get("party") for s2 in loaded["sources"] if s2.get("aggregator")), True)

    def _norm_key(r):
        return (title_key(r.get("title", "")), (r.get("published") or "")[:10])
    dup_a = {"id": "d1", "source_id": "irgc-gnews", "tier": "T3",
             "title": "U.S. Destroys 5 IRGC Tankers", "published": "2026-09-08T07:00:00+00:00"}
    dup_b = {"id": "d2", "source_id": "centcom-gnews", "tier": "T3",
             "title": "U.S. Destroys 5 IRGC Tankers", "published": "2026-09-08T07:00:00+00:00"}
    check("跨源去重 标题相同视为同一事件", _norm_key(dup_a) == _norm_key(dup_b), True)

    # --- 12. 信源等级零缺失
    for src in loaded["sources"]:
        check(f"等级 源 {src['id']} 有 tier", bool(src.get("tier")), True)
        check(f"等级 源 {src['id']} 有 party", bool(src.get("party")), True)
    for src in loaded["sources"]:
        if not src.get("enabled"):
            check(f"停用源 {src['id']} 写明原因", bool(src.get("note")), True)

    # --- 13. 折叠条数上报
    dg = render_digest([rec_ok, rec_no], st, {"anchors": {}, "pairs": [], "stats": {}},
                       [], datetime(2026, 9, 18, tzinfo=timezone.utc), 2, 2, "test")
    check("报告 上报折叠条数", "**本次折叠**：1 条" in dg, True)
    check("报告 同时标注新增与快照", "本次新增" in dg and "当前快照" in dg, True)
    check("报告 含空≠零说明", "空 ≠ 零" in dg, True)

    # --- 14. 词形族覆盖：词表必须能命中同一概念的常见屈折形 ★★
    #  「末词加词形变化」的规则决定了词表里必须写**基础形**。
    #  实测踩坑：词表写 `destroyed` → `Destroys` 命不中；写 `struck` 不写 `strike`
    #  → `strikes` 命不中；写 `retaliat` → `retaliatory` 命不中。
    #  后果不是「少几条」，而是 **交叉验证的 contradiction 恒为 0**（美方 `action_claim`
    #  永远是空集），而日志、条数、状态码全绿。故按概念族逐一断言联合覆盖。
    #  ★ 断言对象是**全部词表**（signals + claims + relevance），不只是 claims：
    #    本项目的主体词表在 signals 里（旧项目只有 claims 有词），只看 claims
    #    会漏掉「域桶写死过去式」这一整类问题。
    FAMILIES = {
        "destroy": ["destroy", "destroys", "destroyed", "destroying"],
        "strike": ["strike", "strikes", "striking", "struck"],
        "shoot down": ["shoot down", "shoots down", "shooting down", "shot down"],
        "intercept": ["intercept", "intercepts", "intercepted"],
        "hit": ["hit", "hits", "hitting"],
        "attack": ["attack", "attacks", "attacked", "attacking"],
        "seize": ["seize", "seizes", "seized", "seizure"],
        "launch": ["launch", "launches", "launched", "launching"],
        "reject": ["reject", "rejects", "rejected"],
        "deny": ["deny", "denies", "denied", "denying"],
        "dismiss": ["dismiss", "dismisses", "dismissed"],
        "confirm": ["confirm", "confirms", "confirmed"],
        "retaliate": ["retaliate", "retaliated", "retaliation", "retaliatory"],
        "mobilize": ["mobilize", "mobilizes", "mobilized", "mobilization"],
        "escalate": ["escalate", "escalates", "escalated", "escalation", "escalatory"],
        "cut": ["cut", "cuts", "cutting"],
        "threaten": ["threaten", "threatens", "threatened"],
    }
    _all_claims: List[str] = []
    for _v in CLAIM_CFG.values():
        if isinstance(_v, list):
            _all_claims.extend(_v)

    def _all_terms_of_lexicon() -> List[str]:
        """signals（含 require_all 各组）+ claims + relevance 的全部词条。"""
        terms: List[str] = list(_all_claims)
        for _spec in SIG_CFG.values():
            terms.extend(_spec.get("phrases") or [])
            for _grp in (_spec.get("require_all") or []):
                terms.extend(_grp)
        for _v in REL_CFG.values():
            if isinstance(_v, list):
                terms.extend(_v)
        return terms

    _all_terms = _all_terms_of_lexicon()
    for base, forms in FAMILIES.items():
        missing = [f for f in forms
                   if not find_hits(norm_text(f), _all_terms)]
        check(f"词形族覆盖 {base}", missing, [])

    # --- 14a. 词形族**派生规则**本身必须正确（按词尾分派）★★
    #  第二类静默漏检：为规避「只写屈折形」，有人改写半截词根（`deplet`/`casualt`），
    #  结果原形与 -ion 名词形全部漏检。这里在**合成样本**上直接断言规则行为 ——
    #  规则一旦退化，本组必红，不依赖词表是否恰好写了某条。
    def _m(term, text):
        return bool(_phrase_pattern(term).search(text))

    check("形态族 -e: deplete 覆盖 depletion（去 e + ion）★",
          _m("deplete", "the depletion of key weapons stockpiles"), True)
    check("形态族 -e: deplete 覆盖原形 / -d / -ing ★",
          all([_m("deplete", "they deplete stocks"), _m("deplete", "depleted"),
               _m("deplete", "depleting")]), True)
    check("形态族 -e: annihilate 覆盖 annihilation ★",
          _m("annihilate", "unable to achieve annihilation"), True)
    check("形态族 -e: neutralize 覆盖 neutralization",
          _m("neutralize", "ordnance neutralization system"), True)
    check("形态族 -e: strike 覆盖 strikes/striking",
          all([_m("strike", "strikes"), _m("strike", "striking")]), True)
    check("形态族 +y: casualty 覆盖 casualties ★",
          _m("casualty", "number of US casualties"), True)
    check("形态族 +y: deny 覆盖 deny/denies/denied/denying ★",
          all([_m("deny", s) for s in ["deny", "denies", "denied", "denying"]]), True)
    check("形态族 +y: 元音+y 不被 -ies 规则带偏（deploy）★",
          all([_m("deploy", "deployed"), _m("deploy", "deploying"),
               _m("deploy", "deploys")]), True)
    check("形态族 +y: supply 覆盖 supplied/supplies",
          all([_m("supply", "supplied"), _m("supply", "supplies")]), True)
    # ★★ 后缀**必选**：去 e 的词干不得独立命中，否则 site→sit / note→not
    #    会把「我们得坐下谈谈」匹配成「基地」。
    check("形态族 不把 sit 误判为 site（后缀必选）★★",
          _m("site", "please sit down"), False)
    check("形态族 不把 not 误判为 note（后缀必选）★★",
          _m("note", "do not go there"), False)
    check("形态族 不把 min/mine 混淆",
          _m("mine", "at least ten min ago"), False)

    # --- 14b. 词表里不得残留半截词根（**必须覆盖全部词表**，不只 claims）★
    #  反例：`deplet`（少了 e）既不匹配 `deplete` 也不匹配 `depletion`；
    #  `casualt` 匹配不到 `casualties`；`attrit` 匹配不到 `attrition`。
    #  实测这些半截词根曾长期躺在 signals 桶里，而检查只扫了 claims ——
    #  于是「弹药耗尽」这类核心信号被整体漏掉，日志与计数却完全正常。
    _all_terms: List[str] = _all_terms_of_lexicon()
    #  ★ 这类启发式的**允许清单**必须逐词注明「已核对为完整词」，而不是随手加宽。
    #    允许清单每加一个词，就少一分发现真·半截词根的能力。
    _COMPLETE_WORDS = {
        "combat", "threat", "alert", "format",          # 常见完整词
        "control", "patrol", "coal", "goal",            # 以 -ol 结尾的完整词
        "jubail",                                       # 朱拜勒（沙特地名）
        "daniel", "israel",                             # 以 -el 结尾的专名
    }
    bad_stems = sorted({p for p in _all_terms
                        if _is_ascii(p) and len(p) > 4 and " " not in p
                        and p.endswith(("iz", "at", "il", "ol", "ur"))
                        and p.lower() not in _COMPLETE_WORDS})
    check("无半截词根（全词表，应以原形结尾）★★", bad_stems, [])
    # 已知的具体陷阱词根：一旦复现立即失败
    _known_traps = ["deplet", "casualt", "attrit", "neutraliz", "annihilat",
                    "refut", "deni", "fabricat", "mobiliz", "escalat"]
    check("已知半截词根不复现",
          [t for t in _known_traps if t in _all_terms], [])
    # ★ 词表里不得有**乱码**（同形异码/混入西里尔字母）。
    #  实测：cable_named 里有一条 `aaе`，末位是**西里尔 е（U+0435）**而不是拉丁 e，
    #  看上去与 `aae` 一模一样 —— 它永远匹配不到任何真实文本，
    #  而人眼复核词表时**看不出来**。这类错误只能靠程序发现。
    _cyr_lat = [t for t in _all_terms
                if any("\u0400" <= c <= "\u04FF" for c in t)
                or any("\u0370" <= c <= "\u03FF" for c in t)]
    check("词表无混入西里尔/希腊字母的乱码词条★★", _cyr_lat, [])

    # --- 14d. 波斯语的**词内边界**：非 ASCII 短词条不得嵌进普通词里 ★★★
    #  这是本项目发现的一类高危假阳性：非 ASCII 词条原先用裸子串匹配，
    #  `رد`（否认）于是命中了 فردا（明天）/ بردار（拿走）/ خرد（微小）/
    #  درد（疼痛）/ سردار（将领）——**普通波斯语句子几乎句句命中「否认」**。
    #  而 `denial` 正是交叉验证判定 contradiction 的输入：
    #  灌水不是「多几条」，而是**凭空造出口径对撞**。
    for _t in ["فردا جلسه دولت برگزار می‌شود", "بردار این کتاب را",
               "خرد و کلان اقتصاد", "درد مردم از تورم", "سردار در مراسم"]:
        check(f"波斯语边界 普通句不命中 denial · {_t[:12]}…",
              len(find_hits(norm_text(_t), SIG_CFG["denial"]["phrases"])), 0)
    #  右边界只对短词条生效，所以需要的**连写形式**必须在词表里显式存在；
    #  下面这组是「连写词被显式列出」的看门用例 —— 词表若把 'نفتکش' 删掉，
    #  'نفت' 的右边界会让它整体漏检（油轮扣押是本主题的核心信号之一）。
    check("波斯语连写 油轮（نفتکش）已显式列出而非依赖 نفت 前缀",
          bool(find_hits(norm_text("توقیف نفتکش در تنگه هرمز"),
                         SIG_CFG["shipping_interdiction"]["require_all"][1])), True)
    check("波斯语连写 也门人（یمنی）已列入锚点",
          bool(find_hits(norm_text("نیروهای یمنی"), REL_CFG["anchor"])), True)
    check("波斯语边界 真信号仍在：رد کرد（否认）",
          bool(find_hits(norm_text("سپاه این خبر را رد کرد"), SIG_CFG["denial"]["phrases"])),
          True)
    check("波斯语边界 切割（قطع）不误命中决议（قطعنامه）★★",
          len(find_hits(norm_text("قطعنامه شورای امنیت"),
                        SIG_CFG["cable_threat"]["require_all"][0])), 0)

    # --- 14g. 交叉验证：第三方转述的否认必须能判成 contradiction ★★
    #  这是本项目的核心交付物 —— 「CENTCOM 否认 IRGC 击沉驱逐舰」这类
    #  口径对撞必须真的出现在产出里，否则交叉验证等于没做。
    def _mk(sid, party, title, pub, kinds, anchors_, disp=True):
        return {"source_id": sid, "party": party, "title": title,
                "summary": title, "published": pub, "link": "http://x/" + sid,
                "publisher": sid, "tier": "T1", "signals": (
                    [{"bucket": kinds[0], "phrase": "x", "weight": 3,
                      "context": title}] if kinds else []),
                "claim_kinds": kinds, "anchors": anchors_,
                "relevance_parts": {"anchor": 1, "domain": 1},
                "_synthetic": True}
    _us = _mk("centcom-gnews", "us", "CENTCOM Destroys 5 IRGC Tankers",
              "2026-09-08T10:00:00+00:00",
              ["action_claim"], ["us_naval", "gulf_shipping"])
    _ir = _mk("parstoday", "iran", "IRGC says it struck two US Navy destroyers",
              "2026-09-08T12:00:00+00:00",
              ["action_claim"], ["us_naval"])
    _td = _mk("irgc-gnews", "third", "CENTCOM rejects Iranian claim that IRGC "
              "struck two US Navy destroyers", "2026-09-09T09:00:00+00:00",
              ["denial"], ["us_naval"])
    _cc = build_crosscheck([_us, _ir, _td])
    _cls = {p["cls"] for p in _cc["pairs"]}
    check("交叉验证 能判出 contradiction（第三方式否认）",
          "contradiction" in _cls, True)

    # 美方自己发否认、伊方宣称战果 → 同样是 contradiction
    _us_den = _mk("dod-transcripts", "us",
                  "Pentagon denies Iranian claim of striking US destroyer",
                  "2026-09-08T14:00:00+00:00", ["denial"], ["us_naval"])
    _cc2 = build_crosscheck([_us_den, _ir])
    check("交叉验证 能判出 contradiction（当事方否认）",
          "contradiction" in {p["cls"] for p in _cc2["pairs"]}, True)

    # 双方都宣称有战果 → mutual_assert（不是 contradiction）
    _ir2 = _mk("parstoday", "iran", "IRGC strikes US warship in Gulf",
               "2026-09-08T12:00:00+00:00",
               ["action_claim"], ["us_naval"])
    _cc3 = build_crosscheck([_us, _ir2])
    check("交叉验证 双方宣称战果→mutual_assert",
          "mutual_assert" in {p["cls"] for p in _cc3["pairs"]}, True)

    # ★ 14e. 配对**方向**必须一致：谁的主张被谁否认。这是曾挂反的地方 ★★
    #  旧代码写成 (au and (di or td_iran)) or (ai and (du or td_us))：
    #  td_iran（第三方否认**伊朗**的说法）挂在了美方主张上，td_us 同理挂反。
    #  后果是把「美方宣称 + 第三方否认伊朗的说法」这种**并不矛盾**的组合
    #  判成 contradiction —— 假阳性对撞，而这是本报告的核心交付物。
    _ir_plain = _mk("parstoday", "iran", "IRGC holds naval exercise in Gulf",
                    "2026-09-08T12:00:00+00:00", ["military_drill"], ["us_naval"])
    _td_vs_iran = _mk("irgc-gnews", "third",
                      "AzerNews: CENTCOM rejects IRGC claim on striking destroyers",
                      "2026-09-09T09:00:00+00:00", ["denial"], ["us_naval"])
    _cc4 = build_crosscheck([_us, _ir_plain, _td_vs_iran])
    check("交叉验证 美方宣称+第三方否认伊朗且伊方未宣称 → 不得判对撞（方向回归）★★",
          "contradiction" not in {p["cls"] for p in _cc4["pairs"]}, True)

    #  denial_target 必须看**否认动词之后**的实体，且窗口内取**最早出现**的一方：
    #  "USNI rejects US claims about IRGC striking ..." 里被否认的是 US，
    #  伊朗只是从句里的施动者 —— 「伊朗优先」会判反方向。
    _td_vs_us = _mk("irgc-gnews", "third",
                    "USNI rejects US claims about IRGC striking destroyers",
                    "2026-09-09T09:00:00+00:00", ["denial"], ["us_naval"])
    _cc5 = build_crosscheck([_us, _ir_plain, _td_vs_us])
    _c5 = [p for p in _cc5["pairs"] if p["cls"] == "contradiction"]
    check("交叉验证 第三方否认**美方**说法 → 判对撞 ★",
          bool(_c5), True)
    check("交叉验证 该条归入 third_denial_vs_us 桶（方向正确）★★",
          "third_denial_vs_us" in ((_c5[0].get("trigger") or {}) if _c5 else {}), True)
    #  两侧实体词表必须等宽：只认大写 US，不认代词 us
    check("交叉验证 拒绝把代词 us 当美方实体",
          bool(re.search(r"(?:\bcentcom\b|(?-i:\bU\.?S\.?\b))", "tell us about it")), False)

    # ★ 14f. 无关的「某方否认」不得凑成对撞 —— 假阳性回归 ★★
    #  实测：France24「…as **Iran denied any involvement** in the country's revived
    #  civil war」被算成「第三方否认伊朗的战果宣称」，于是与伊方在 `missile` 锚点下的
    #  声明凑成一个**根本不存在的对撞**。根因是 denial_target 找不到实体时
    #  **兜底扫整句**，而 `Iran` 在那里是否认的**施动者**、不是被否认方。
    _us_plain = _mk("dod-releases", "us",
                    "Pentagon announces missile production increase",
                    "2026-09-08T08:00:00+00:00", ["readiness_declaration"], ["missile_program"])
    _ir_assert2 = _mk("parstoday", "iran",
                      "Iranian missiles struck US bases in the region",
                      "2026-09-08T12:00:00+00:00",
                      ["action_claim"], ["missile_program"])
    _td_irrelevant = _mk("france24-me", "third",
                         "'Axis of Iranian centrality' remains one of more "
                         "destabilising forces in the region, expert says",
                         "2026-09-08T09:00:00+00:00",
                         ["action_claim", "denial"], ["missile_program"])
    _td_irrelevant["summary"] = (
        "Yemen's Houthis launched a barrage of missile and drone attacks on Saudi "
        "military targets, as Iran denied any involvement in the revived civil war.")
    _cc6 = build_crosscheck([_us_plain, _ir_assert2, _td_irrelevant])
    check("交叉验证 无关的「某方否认」不得凑成对撞（假阳性回归）★★",
          "contradiction" not in {p["cls"] for p in _cc6["pairs"]}, True)
    #  而真正指向对方主张的否认仍必须判出对撞（不能因收紧而漏检）
    _td_relevant = _mk("france24-me", "third",
                       "US officials reject Iranian claim that missiles struck US bases",
                       "2026-09-08T09:00:00+00:00", ["denial"], ["missile_program"])
    _cc7 = build_crosscheck([_us_plain, _ir_assert2, _td_relevant])
    check("交叉验证 收紧后仍能判出真对撞（不得漏检）★★",
          "contradiction" in {p["cls"] for p in _cc7["pairs"]}, True)

    # --- 14h. 关注域汇总：计数必须走**展示闸门**同一口径 ★★
    #  域汇总若把「正文里看不到的记录」也算进去，读者就会看到
    #  「§3 说该域有 12 条，表里却只列得出 7 条」这种无法解释的矛盾 ——
    #  而这类矛盾会让读者怀疑整份报告，却不指向任何具体缺陷。
    _zrecs = [
        mk("z1", "iran", "IRGC threatens to close the Strait of Hormuz",
           "2026-09-17T00:00:00+00:00"),
        mk("z2", "us", "CENTCOM: Hormuz remains open, US Navy at full combat readiness",
           "2026-09-17T00:00:00+00:00"),
        enrich({"id": "z3", "source_id": "gao-reports", "title": "Weather forecast",
                "summary": "sunny", "link": "u", "published": "2026-09-17T00:00:00+00:00"},
               {"party": "third", "tier": "T2", "publisher": "p"}),
    ]
    _zs = {z["id"]: z for z in build_zone_summary(_zrecs)}
    _disp_ids = {r["id"] for r in _zrecs if is_displayable(r)}
    _in_zone_ids = {r["id"] for z in _zs.values() for r in z["records"]}
    # ★ 断言「集合相等」而不是「条数之和相等」：一条记录可以同时落在多个域
    #   （一条「IRGC 在霍尔木兹附近打击美舰」同时属于 海上平台 + 海峡），
    #   所以条数之和 ≥ 记录数。把和当记录数来对，测试本身就会是错的。
    check("域汇总 记录集合 = 通过展示闸门的记录集合（与正文同口径）★★",
          _in_zone_ids, _disp_ids)
    check("域汇总 无关记录不出现在任何域", "z3" not in _in_zone_ids, True)
    check("域汇总 一条记录可属多个域（条数之和 ≥ 记录数）",
          sum(z["n"] for z in _zs.values()) >= len(_disp_ids), True)
    check("域汇总 七个域全部出现（含 0 条域）", len(_zs), 7)
    check("域汇总 0 条域仍带 desc 说明（不是空行）",
          bool(_zs["cable"].get("desc")), True)
    check("域汇总 海峡域命中 2 条", _zs["chokepoint"]["n"], 2)
    check("域汇总 按当事方分解",
          (_zs["chokepoint"]["parties"].get("iran"), _zs["chokepoint"]["parties"].get("us")),
          (1, 1))
    check("域汇总 按桶分解（hormuz_threat 至少 1 次）",
          _zs["chokepoint"]["buckets"].get("hormuz_threat", 0) >= 1, True)
    check("域汇总 记录按发布时间倒序",
          all([r.get("published") or "" for r in z["records"]]
              == sorted([r.get("published") or "" for r in z["records"]], reverse=True)
              for z in _zs.values()), True)

    # --- 14i. 前瞻指标：只吃前瞻桶，且**不得**被读成预测 ★★
    _frecs2 = [
        mk("f1", "iran", "IRGC commander says Iran will strike US bases if attacked",
           "2026-09-17T00:00:00+00:00"),
        mk("f2", "iran", "IRGC says its forces are at the highest state of readiness",
           "2026-09-17T00:00:00+00:00"),
    ]
    _fs = build_foresight(_frecs2)
    check("前瞻 只收录含前瞻桶的记录（战备声明不计入前瞻）★",
          [r["id"] for r in _fs["records"]], ["f1"])
    check("前瞻 分数 = Σ(权重 × 命中次数)", foresight_score(_frecs2[0]) > 0, True)
    check("前瞻 无前瞻桶的记录得 0 分", foresight_score(_frecs2[1]), 0)
    check("前瞻 按当事方分解", _fs["by_party"].get("iran"), 1)
    check("前瞻 上报窗口内总条数（读者据此判断占比）", _fs["window_n"], 2)
    check("前瞻桶 全部归属 foresight 域，未混入六大域 ★",
          sorted({zone_of_bucket(b) for b in FORESIGHT_BUCKETS}), ["foresight"])
    check("前瞻 分数排序为降序", _fs["records"] == sorted(
        _fs["records"], key=lambda r: (-foresight_score(r), r.get("published") or "")), True)

    # --- 14j. 实体抽取：字母/数字边界必须生效，且普通名词要认复数 ★★
    #  实测过的同类事故：`Rota` 命中 `Rotational`、`America` 命中 `American`、
    #  `Nimitz`（舰级）被当成 `USS Nimitz`（舰名）——凭空多出一艘船。
    _erecs = [
        mk("e1", "third", "USS Nimitz carrier strike group transits Hormuz",
           "2026-09-17T00:00:00+00:00"),
        mk("e2", "third", "IAEA says Iran has enriched uranium to 90 percent at Fordow",
           "2026-09-17T00:00:00+00:00"),
        mk("e3", "third", "Two US destroyers and drone boats deployed near Kharg",
           "2026-09-17T00:00:00+00:00"),
    ]
    _ent = {g["id"]: {r["en"]: r["n"] for r in g["rows"]}
            for g in extract_entities(_erecs)}
    check("实体 左边界生效：USS 不命中普通词 discuss ★★",
          all(not _entity_rx("USS ").search(t)
              for t in ("officials discuss the move", "a discussion about shipping",
                        "they discussed it")), True)
    check("实体 海峡实体命中 Hormuz",
          _ent.get("chokepoint", {}).get("Hormuz", 0) >= 1, True)
    check("实体 长词优先：Fordow 不被 Fordo 抢先（Fordo 计数为 0）★★",
          _ent.get("nuclear_site", {}).get("Fordo", 0), 0)
    check("实体 长词优先：Fordow 本身被计入",
          _ent.get("nuclear_site", {}).get("Fordow", 0) >= 1, True)
    check("实体 普通名词认复数：destroyers 计入 destroyer ★",
          _ent.get("platform", {}).get("destroyer", 0) >= 1, True)
    check("实体 普通名词认复数：drone boats 计入 drone boat ★",
          _ent.get("platform", {}).get("drone boat", 0) >= 1, True)
    check("实体 专名不加复数尾巴（USS 仍只匹配 USS + 空格）",
          bool(_entity_rx("USS ").search("USS Nimitz")), True)
    check("实体 分组固定为 6 组（海峡/能源/光缆/核/平台/伊朗军力）",
          sorted(ENTITY_ORDER), ["cable", "chokepoint", "energy", "iran_force",
                                 "nuclear_site", "platform"])
    check("实体 未被提及的组不出现在结果里（不留空行）★",
          "cable" not in _ent, True)
    check("实体 每行都带英文原名与中文名",
          all(r["en"] and r["zh"] for g in extract_entities(_erecs) for r in g["rows"]),
          True)

    # --- 15. 文本归一：保留大小写 + HTML 反转义顺序 ★
    #  转小写会让 context 里的「原文片段」全是小写，无法回原文检索；
    #  先删标签后反转义会留下 `<a href=…>` 这种原始 HTML。
    check("归一 保留原始大小写", norm_text("IRGC Navy Destroys USV"), "IRGC Navy Destroys USV")
    check("比较键 大小写无关", norm_key("IRGC Navy") == norm_key("irgc navy"), True)
    check("去标签 反转义后不留 HTML",
          strip_tags('x &lt;a href="https://a.b"&gt; y'), "x y")
    check("去标签 双重编码也能清干净",
          strip_tags("a &amp;lt;p&amp;gt;b"), "a b")
    check("去标签 普通 CDATA 正常",
          strip_tags("<![CDATA[Hello <b>World</b>]]>"), "Hello World")

    # --- 15b. 标题键：素材序号后缀必须抹掉 ★
    #  DVIDS 一条报道会以 [Image 1 of 5]…[Image 5 of 5] 出现 5 次，
    #  不抹后缀就会一条演习新闻变 5 条记录，虚增美方条数。
    check("标题键 抹掉 Image n of m",
          title_key("Eager Lion 26 [Image 4 of 5]") == title_key("Eager Lion 26 [Image 1 of 5]"),
          True)
    check("标题键 抹掉 Video 序号",
          title_key("USS Boxer Ops [Video 2 of 3]"), "uss boxer ops")
    check("标题键 不影响正常标题",
          title_key("CENTCOM Destroys 3 IRGC Oil Tankers"),
          "centcom destroys 3 irgc oil tankers")
    check("标题键 不同标题不误合并",
          title_key("Fleet Tracker: Sept. 8") == title_key("Fleet Tracker: Sept. 14"), False)

    # --- 16. 折叠分类统计：**计数必须走全量，不能被展示抽样截断** ★
    #  实测踩过的坑：报告把「864 条折叠」的分布错报成「前 400 条明细」的分布，
    #  252+142+6=400≠864（加总都不自洽），最关键的「有战备信号但未锚定」
    #  从真实 9 条被压成 6 条 —— 恰好掩盖了「词表/锚点收紧过头」的线索。
    #  本组断言保证：明细可以截断，计数永不截断。
    def _mkf(tag, sigs, parts):
        return {"id": f"f{tag}", "source_id": "s-x", "title": f"t{tag}", "link": "",
                "published": "2026-09-17", "signals": sigs, "relevance_parts": parts}

    _n_fold = FOLD_SAMPLE_LIMIT + 5
    _frecs = [_mkf(i, [], {}) for i in range(_n_fold - 2)]           # 真无关
    _frecs.append(_mkf("a", [{"bucket": "b"}], {"anchor": 0, "domain": 0}))  # 未锚定
    _frecs.append(_mkf("b", [], {"anchor": 2, "domain": 0}))         # 无战备信号
    _fitems, _fbrk = classify_folded(_frecs)
    check("折叠分类 明细条数=被折叠条数（含闸门判定）", len(_fitems), _n_fold)
    check("折叠分类 计数之和=折叠总数 ★", sum(_fbrk.values()), _n_fold)
    check("折叠分类 计数超过抽样上限时仍为全量 ★",
          sum(_fbrk.values()) > FOLD_SAMPLE_LIMIT, True)
    check("折叠分类 未锚定类单列", _fbrk.get("no-topic-anchor"), 1)
    check("折叠分类 无战备信号类单列", _fbrk.get("no-readiness-signal"), 1)
    check("折叠分类 真无关类单列", _fbrk.get("off-topic"), _n_fold - 2)

    # --- 17. 代理出口：默认跟随系统代理，`--proxy` 只能**显式**覆盖 ★
    #  端口写死、或系统代理指向一个已过期的端口，会让**大批源静默失败**，
    #  且症状与「被 CDN 风控拦截」一模一样（实测：12 个源失败，其中 6 个报
    #  `Tunnel connection failed: 502`、6 个报 `403 AkamaiGHost/cloudflare`，
    #  换到存活端口后这 12 个源**全部**返回 200 + 真实 RSS）。
    #  这里只断言「接线正确」，不做联网断言（自检必须离线）。
    _saved_proxy = PROXY_OVERRIDE
    try:
        globals()["PROXY_OVERRIDE"] = None
        _o_auto = _opener()
        _auto_proxies = urllib.request.getproxies()
        _h_auto = [h for h in _o_auto.handlers
                   if isinstance(h, urllib.request.ProxyHandler)]
        _p_auto = _h_auto[0].proxies if _h_auto else {}
        # ★ 这里**不能**断言「选中的就是环境变量里那一个」：
        #   出口池会按真实目标深探排序，环境代理若是「半死」的（gstatic 通、
        #   Google/Yahoo 不通），首选本就应该换成能抓到真实目标的那个出口。
        #   正确的断言是「选中的一定来自候选池，绝不写死端口」。
        check("代理 未覆盖时出口取自候选池（跟随探测，不写死端口）",
              (_p_auto.get("https") in proxy_candidates())
              or (not _auto_proxies), True)
        check("出口池非空且首选与 _resolve_auto_proxy() 一致",
              bool(live_proxies()) and current_proxy() == live_proxies()[0],
              True)

        globals()["PROXY_OVERRIDE"] = "http://127.0.0.1:9999"
        _h_ov = [h for h in _opener().handlers
                 if isinstance(h, urllib.request.ProxyHandler)]
        check("代理 --proxy 覆盖后注入指定出口 ★",
              (_h_ov[0].proxies.get("https"), _h_ov[0].proxies.get("http")),
              ("http://127.0.0.1:9999", "http://127.0.0.1:9999"))
        check("代理 报告串标注「显式覆盖」", "显式覆盖" in proxy_report(), True)
    finally:
        globals()["PROXY_OVERRIDE"] = _saved_proxy

    # --- 18. CLI 开关必须**真正接线**到模块全局 ★★
    #  同一类 bug 已犯过**两次**，且两次都「不报错、只是没作用」：
    #    · `per_source_max` 写在 config 里，但实现恒等于「不截断」→ 该配置从未生效；
    #    · `--proxy` 解析成功，却漏写进 main() 的 global 声明 → 赋值只建了个**局部变量**，
    #      模块全局仍是 None，opener 照旧走系统代理（实测：一轮 8 分钟的全量抓取白跑，
    #      日志里出口仍是旧端口，12 个源一个也没救回来）。
    #  → 断言：凡「必须在 main() 里写模块全局」的开关，都要出现在 global 声明中。
    import inspect as _inspect
    _main_src = _inspect.getsource(main)
    for _gname in ("PROXY_OVERRIDE", "OUTPUT_DIR", "CFG_VER", "GATE_AUDIT",
                   "LOOKBACK_HOURS", "PER_SOURCE_MAX"):
        _declared = any(
            _ln.strip().startswith("global ") and _gname in _ln
            for _ln in _main_src.splitlines())
        check(f"CLI 接线 main() 的 global 声明含 {_gname} ★", _declared, True)

    print()
    for f in fails:
        print("❌ " + f)
    print()
    print(f"自检结果：{ok} 项通过 / {bad} 项失败")
    print("=" * 92)
    return 1 if bad else 0


# ================================================================ main
def main() -> int:
    global CFG, LOOKBACK_HOURS, PER_SOURCE_MAX, REQ, REL_CFG, SIG_CFG
    global CLAIM_CFG, CROSS_ANCHORS, CROSS_MATCH_DAYS, BACKFILL, STRICT_TLS
    global SHOW_ALL, GATE_AUDIT, TLS_DOWNGRADED, OUTLET_MAP, SOURCE_BY_ID, CFG_VER
    global OUTPUT_DIR, PROXY_OVERRIDE, ZONES

    ap = argparse.ArgumentParser(
        description="伊朗战备与升级动态监测（六大关注域 × 当事方对照）")
    ap.add_argument("--config", default=os.path.join(ROOT, "config.json"))
    ap.add_argument("--days", type=int, help="回溯天数（覆盖配置 lookback_hours）")
    ap.add_argument("--sources", help="只跑指定源（逗号分隔）")
    ap.add_argument("--backfill", action="store_true",
                    help="回填：把窗口放宽到历史起点（首次运行用）")
    ap.add_argument("--from-file", help="离线模式：读该目录下 <源id>.xml|json")
    ap.add_argument("--show-all", action="store_true", help="不做折叠，全量列出")
    ap.add_argument("--as-of", help="把本次运行当作该日期（YYYY-MM-DD），用于跨天落盘验证")
    ap.add_argument("--out-dir", help="覆盖输出根目录，用于隔离验证运行")
    ap.add_argument("--strict-tls", action="store_true",
                    help="拒绝任何需要关闭证书校验的源")
    ap.add_argument("--no-seen", action="store_true", help="忽略去重状态")
    ap.add_argument("--recompute", action="store_true",
                    help="离线：按当前词表全量重算累积总档后退出")
    ap.add_argument("--reset-seen", action="store_true", help="清空去重状态后退出")
    ap.add_argument("--render-only", action="store_true",
                    help="离线**仅重新渲染**：从当日快照重建报告，不联网抓取。"
                         "改渲染层（表格结构/标签/说明）后用，避免重抓一遍。")
    ap.add_argument("--selftest", action="store_true", help="跑离线自检")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--proxy",
                    help="显式指定代理出口（如 http://127.0.0.1:7897），**仅供排障**："
                         "默认自动探测系统代理。★ 不要在脚本/快捷方式里写死端口 —— "
                         "代理端口会漂移（客户端重启后变化），写死会让大批源静默失败，"
                         "且症状与「被 CDN 风控拦截」完全一样。")
    args = ap.parse_args()

    if args.proxy:
        # http_get() 在模块层读取该全局，因此必须写全局
        PROXY_OVERRIDE = args.proxy

    if args.selftest:
        return selftest()

    with open(args.config, encoding="utf-8") as f:
        CFG = json.load(f)
    LOOKBACK_HOURS = int(CFG.get("lookback_hours", 168))
    if args.days:
        LOOKBACK_HOURS = args.days * 24
    PER_SOURCE_MAX = int(CFG.get("per_source_max", 60))
    REQ = CFG.get("request", {})
    REL_CFG = CFG.get("relevance", {})
    SIG_CFG = CFG.get("signals", {})
    CLAIM_CFG = CFG.get("claims", {})
    CROSS_ANCHORS = CFG.get("cross_anchors", [])
    ZONES = CFG.get("zones", [])
    OUTLET_MAP = CFG.get("outlet_map", {})
    SOURCE_BY_ID = {s["id"]: s for s in CFG.get("sources", [])}
    CROSS_MATCH_DAYS = int((CFG.get("window") or {}).get("cross_match_days", 7))
    CFG_VER = lexicon_fingerprint()
    BACKFILL = args.backfill
    STRICT_TLS = args.strict_tls
    SHOW_ALL = args.show_all
    GATE_AUDIT = {}
    TLS_DOWNGRADED = []
    if args.out_dir:
        OUTPUT_DIR = os.path.abspath(args.out_dir)

    if args.reset_seen:
        p = os.path.join(STATE_DIR, "seen.json")
        if os.path.exists(p):
            os.remove(p)
        log("已清空去重状态。")
        return 0

    if args.recompute:
        # 离线修复：把累积总档按当前词表全量重算（不联网、不改 first_seen）。
        # 改完词表后必须跑一次，否则档案里会混着两套口径。
        p = os.path.join(OUTPUT_DIR, "ALL-records.jsonl")
        if not os.path.exists(p):
            log(f"未找到累积总档：{p}")
            return 1
        recs = load_accumulated()
        n = backfill_derived(recs)
        recs.sort(key=lambda x: x.get("published") or "")
        write_jsonl(p, recs)
        log(f"词表指纹 {CFG_VER}｜累积总档 {len(recs)} 条，重算 {n} 条")
        return 0

    if args.render_only:
        # 离线**仅重新渲染**：改渲染层（表格结构 / 标签 / 说明文字）后不必重抓一遍。
        # 不联网、不改快照、不动累积档 —— 只把报告从落盘快照重画一遍。
        day = (datetime.fromisoformat(args.as_of).strftime("%Y-%m-%d")
               if args.as_of else datetime.now().strftime("%Y-%m-%d"))
        base = args.out_dir or os.path.join(OUTPUT_DIR, day)
        rp = os.path.join(base, f"records-{day}.jsonl")
        merged = read_jsonl(rp)
        if not merged:
            log(f"未找到当日快照：{rp} —— 请先用正常模式跑一次抓取")
            return 1
        sp = os.path.join(OUTPUT_DIR, "_source_status.json")
        statuses = []
        if os.path.exists(sp):
            statuses = json.load(open(sp, encoding="utf-8")).get("sources") or []
        run_dt = now_utc()
        if args.as_of:
            run_dt = datetime.fromisoformat(args.as_of).replace(
                tzinfo=timezone.utc) + timedelta(hours=12)
        merged.sort(key=lambda x: (x.get("published") or ""), reverse=True)
        cross = build_crosscheck(merged)
        weekly = build_weekly(merged)
        refresh_gate_audit(merged)
        # 快照内条数同样要重算（否则重新渲染时这一列会沿用上一次运行的旧值）
        _snap2: Dict[str, int] = {}
        for r in merged:
            sid_ = r.get("source_id") or ""
            _snap2[sid_] = _snap2.get(sid_, 0) + 1
        for s in statuses:
            s["in_snapshot"] = _snap2.get(s["id"], 0)
        window_desc = f"最近 {max(1, LOOKBACK_HOURS // 24)} 天"
        digest = render_digest(merged, statuses, cross, weekly, run_dt, None,
                               len(merged), window_desc)
        os.makedirs(base, exist_ok=True)
        with open(os.path.join(base, f"iran-digest-{day}.md"),
                  "w", encoding="utf-8") as f:
            f.write(digest)
        with open(os.path.join(base, f"crosscheck-{day}.md"),
                  "w", encoding="utf-8") as f:
            f.write(render_crosscheck_md(cross, run_dt))
        if not args.out_dir:
            with open(os.path.join(OUTPUT_DIR, "LATEST.md"),
                      "w", encoding="utf-8") as f:
                f.write(digest)
            with open(os.path.join(OUTPUT_DIR, "LATEST-crosscheck.md"),
                      "w", encoding="utf-8") as f:
                f.write(render_crosscheck_md(cross, run_dt))
            with open(os.path.join(OUTPUT_DIR, "_gate_audit.json"),
                      "w", encoding="utf-8") as f:
                json.dump(GATE_AUDIT, f, ensure_ascii=False, indent=1)
        log(f"已重新渲染（未联网）：{base}")
        log(f"快照 {len(merged)} 条｜交叉验证 "
            f"{len(cross.get('pairs', []))} 组｜折叠分类 "
            f"{GATE_AUDIT.get('_folded_breakdown')}")
        return 0

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)

    run_dt = now_utc()
    if args.as_of:
        # ★ 跨天落盘验证专用：只改「今天是哪天」，不改系统时钟。
        # 必须配合 --out-dir 使用，避免污染正式产出目录。
        d = datetime.fromisoformat(args.as_of).replace(
            tzinfo=timezone.utc) + timedelta(hours=12)
        run_dt = d
    log("=" * 92)
    log(f"iran-escalation-monitor v{VERSION}　·　{run_dt.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    log(f"实际出口代理: {proxy_report()}")
    if BACKFILL:
        log(f"模式: 回填（窗口放宽到历史起点 {(CFG.get('history') or {}).get('start')}）")
        window_desc = f"回填至 {(CFG.get('history') or {}).get('start')}"
    else:
        window_desc = f"最近 {LOOKBACK_HOURS // 24} 天"
        log(f"模式: 日常增量（窗口 {window_desc}）")
    log("=" * 92)

    sources = CFG.get("sources", [])
    if args.sources:
        want = {s.strip() for s in args.sources.split(",") if s.strip()}
        sources = [s for s in sources if s["id"] in want]

    all_recs: List[Dict[str, Any]] = []
    statuses: List[Dict[str, Any]] = []
    for i, src in enumerate(sources):
        try:
            recs, st = collect_source(src, quiet=args.quiet, from_file=args.from_file)
        except Exception as e:  # noqa: BLE001
            recs, st = [], {"id": src.get("id"), "party": src.get("party"),
                            "channel": src.get("channel"),
                            "publisher": src.get("publisher"),
                            "tier": src.get("tier"), "enabled": True,
                            "state": "fail-parse", "entries": 0, "latest": None,
                            "age_days": None, "http_status": None, "server": "",
                            "note": f"未捕获异常: {type(e).__name__}: {e}",
                            "url_used": src.get("url"), "tls": "verified",
                            "attempts": 0}
        all_recs.extend(recs)
        statuses.append(st)
        icon = STATUS_ICON.get(st["state"], "❓")
        log(f"  {icon} [{st['party']:5}] {st['id']:22} status={st['http_status']} "
            f"entries={st['entries']:3} 命中={len(recs):3} "
            f"最新={(st['latest'] or '')[:10] or '—'}"
            + (f"  ⚠️ {st['note'][:60]}" if st["note"] and st["state"].startswith("fail") else ""))
        if i < len(sources) - 1:
            time.sleep(float(REQ.get("delay_between_sources_sec", 1.2)))

    # 去重（同一 id 只留一条）
    dedup: Dict[str, Dict[str, Any]] = {}
    for r in all_recs:
        dedup[r["id"]] = r
    records_all = list(dedup.values())

    # ★ 跨源去重：同一条报道会被多个聚合检索式、以及聚合器与原始源同时抓到。
    # 只按 id 去重是不够的（id 含 source_id，故必然不同）——结果同一条新闻
    # 会在交叉矩阵里出现两次、并把计数抬高。按「归一化标题 + 日期」去重，
    # 保留等级更高的那份，并记录它还在哪些源里出现过。
    TIER_ORDER = {"T1": 0, "T2": 1, "T3": 2, "T4": 3}
    best: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in records_all:
        key = (title_key(r.get("title", "")), (r.get("published") or "")[:10])
        cur = best.get(key)
        if cur is None:
            best[key] = r
            continue
        if TIER_ORDER.get(r.get("tier"), 9) < TIER_ORDER.get(cur.get("tier"), 9):
            r["also_seen_in"] = sorted(set(
                (cur.get("also_seen_in") or []) + [cur.get("source_id", "")]))
            best[key] = r
        else:
            cur["also_seen_in"] = sorted(set(
                (cur.get("also_seen_in") or []) + [r.get("source_id", "")]))
    cross_dedup = len(records_all) - len(best)
    records_all = sorted(best.values(), key=lambda x: x.get("published") or "")

    log("")
    # ★ 三个条数**必须分别标注，且口径要能对上**：
    #  历史 bug：这里用 `len(records_all) - len(disp)` 算「被折叠」，
    #  但 `records_all` 是**本轮抓取**、`disp` 是从**窗口快照**筛出来的 ——
    #  两个数字不同源，日志于是报「84 进入 / 611 折叠」（84+611=695=本轮抓取数），
    #  而报告 §1 写的是「折叠 1019 条」（基于快照 1103）。日志与报告互相打脸。
    log(f"本轮抓取：{len(records_all)} 条（跨源去重合并 {cross_dedup} 条）")
    res = write_outputs(records_all, records_all, statuses, run_dt, window_desc)
    write_index(run_dt)

    log(f"窗口快照：{res['merged']} 条（本轮新增 {res['fresh']} 条）")
    log(f"展示折叠：{res['displayed']} 条进入报告，{res['folded']} 条被折叠"
        f"（{res['displayed']} + {res['folded']} = {res['merged']}）")
    log(f"交叉验证：{len(res['cross'].get('pairs', []))} 组同期对照，"
        f"{len(res['cross'].get('anchors', {}))} 个议题锚点有命中")
    log(f"累积总档：{res['accumulated']} 条"
        + (f"（本轮词表回填 {BACKFILLED} 条）" if BACKFILLED else ""))
    log(f"产出目录：{res['dir']}")

    if args.quiet:
        print(json.dumps({"day": res["day"], "records": res["merged"],
                          "fresh": res["fresh"], "displayed": res["displayed"],
                          "pairs": len(res["cross"].get("pairs", [])),
                          "accumulated": res["accumulated"]},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
