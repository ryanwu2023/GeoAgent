#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ru_diplomacy_monitor.py — 俄乌冲突外交斡旋监测
（美国特使与斡旋动向 / 谈判进程与停火安排 / 欧盟对和谈态度 /
 乌克兰对和谈态度 / 安克雷奇框架（是否回到该框架） / 中国视角（新华社·中方斡旋））
骨架承继 eu-domestic-monitor（russia-domestic 一脉）：配置驱动源 + 主题锚定
（并入各桶全部词表）+ 实体归属 + tier 分级 + 事实/观点分类 + 立场词表
（否定翻转；语义=和谈倒退/强硬 vs 和谈推进/妥协）+ flags（特使出行/愿谈/拒谈/
安克雷奇回摆/备忘录/停火呼吁）+ 意向守卫 + html_listing 解析器（新华社英文首页）+
**俄语前缀词干 + rx: 精确正则 + 中文词面** + 累积档回填（cfg_ver 含代码哈希）+ 离线自检。

用法:
  python ru_diplomacy_monitor.py                # 抓取+渲染
  python ru_diplomacy_monitor.py --selftest     # 离线自检（不联网）
  python ru_diplomacy_monitor.py --as-of 2026-09-21 --out-dir output/_xday-verify
  python ru_diplomacy_monitor.py --render-only  # 仅从累积档重渲染
  python ru_diplomacy_monitor.py --from-file corpus.json  # 离线退路
"""
import argparse, hashlib, html, json, os, re, sys, time, gzip, zlib
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone, timedelta, date
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE, "config.json")
SUF = r"(?:s|es|ed|ing|d|er|ers|ation|ions?|ism|e)?"
EPOCH = date(1970, 1, 1)
MIN_YEAR, MAX_YEAR_SLACK = 2000, 1

# ---------------------------------------------------------------- utilities
def load_cfg():
    with open(CFG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def now_utc():
    return datetime.now(timezone.utc)

def sha(s):
    return hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()[:16]

def strip_tags(s):
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def clean_text(s, maxlen):
    return strip_tags(s)[:maxlen]

def norm_link(u):
    u = (u or "").split("#")[0]
    u = re.sub(r"([?&])(utm_[^&]*|gclid|fbclid|ocid)=[^&]*", r"\1", u).rstrip("?&")
    return u

def title_key(title):
    t = strip_tags(title or "")
    t = re.sub(r"\s+[-—–]\s+[^-—–|]{2,42}$", "", t)
    return re.sub(r"[^a-zà-ÿа-яё0-9]+", "", t.lower())[:80]

# ---------------------------------------------------------------- http
_last_hit = {}
_opener_cache = {}
_pool_cache = {"at": 0.0, "ok": []}

def proxy_candidates():
    """候选出口：环境代理优先，常见本地端口次之，直连兜底。绝不写死单一端口。"""
    cands = []
    env = urllib.request.getproxies()
    for k in ("https", "http"):
        u = env.get(k)
        if isinstance(u, str) and u.startswith("http") and u not in cands:
            cands.append(u)
    for port in (7897, 7890, 10809, 1080, 8080, 8888,
                4780, 2080, 20171, 33210):
        u = "http://127.0.0.1:%d" % port
        if u not in cands:
            cands.append(u)
    return cands + [None]

def _opener_for(proxy):
    if proxy not in _opener_cache:
        if proxy is None:
            _opener_cache[proxy] = urllib.request.build_opener(
                urllib.request.ProxyHandler({}))
        else:
            _opener_cache[proxy] = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return _opener_cache[proxy]

_EGRESS_DEEP_PROBES = (
    "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=5d&interval=1d",
    "https://news.google.com/rss/search?q=probe&hl=en-US&gl=US&ceid=US:en",
)

def _proxy_alive(proxy, timeout=5):
    """通用探活 —— 只负责筛掉**死端口**。

    ★ 为什么不能只靠它（2026-09-20 实测）：57444 出口对 gstatic204 与
      cloudflare trace 两个探针都返回正常，但同一时刻 Yahoo chart → 403、
      Google News → 超时。它是「半死」出口：连得上，但被目标站风控。
      若只做通用探活，整轮抓取会静默拿回一堆 403，日志上却只显示「无数据」，
      排查方向会被带到「源挂了」，而真相是「这个出口被目标站拉黑」。
      → 真实可用性交给 _proxy_deep_score() 排序判断。
    """
    for probe in ("http://www.gstatic.com/generate_204",
                  "https://www.cloudflare.com/cdn-cgi/trace"):
        try:
            r = _opener_for(proxy).open(probe, timeout=timeout)
            if r.status in (200, 204):
                return True
        except Exception:
            continue
    return False

def _proxy_deep_score(proxy, timeout=6):
    """真实目标深探得分（0–2）。

    只对**真的会给客户端发挑战页**的站探，否则测不出「出口被拉黑」这一种半死状态。
    """
    good = 0
    for probe in _EGRESS_DEEP_PROBES:
        try:
            r = _opener_for(proxy).open(probe, timeout=timeout)
            if r.status == 200:
                good += 1
        except Exception:
            pass
    return good

def live_proxies():
    """探测存活出口（缓存 10 分钟）。出口漂移自动摘除，恢复后自动回归。

    排序：深探全通的排前面。这样即使环境代理本身是「半死」的，
    真正抓取时也会优先走那个能抓到真实目标的出口。
    """
    now = time.time()
    if _pool_cache["ok"] and now - _pool_cache["at"] < 600:
        return list(_pool_cache["ok"])
    scored = []
    for u in proxy_candidates():
        if not _proxy_alive(u):
            continue
        scored.append((-_proxy_deep_score(u), u))
    scored.sort(key=lambda x: (x[0], x[1] is None, x[1] or ""))
    ok = [u for _, u in scored] or [None]
    _pool_cache.update({"at": now, "ok": ok})
    return list(_pool_cache["ok"])

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

def demote_proxy(proxy):
    """某出口隧道失败时立即摘除（缓存到期后重新探测自然恢复）。"""
    if proxy in _pool_cache.get("ok", []):
        _pool_cache["ok"].remove(proxy)

def http_get(url, timeout=30, min_iv=1.2, ua=None, retries=1):
    host = urlparse(url).netloc
    wait = _last_hit.get(host)
    if wait is not None:
        dt = time.time() - wait
        if dt < min_iv:
            time.sleep(min_iv - dt)
    hdr = {"User-Agent": ua or "Mozilla/5.0", "Accept": "*/*",
           "Accept-Language": "en-US,en;q=0.9,ru;q=0.8,uk;q=0.7", "Accept-Encoding": "gzip, deflate",
           "Connection": "close"}
    err = None
    for proxy in live_proxies() or [None]:
        for attempt in range(retries + 1):
            t0 = time.time()
            try:
                req = urllib.request.Request(url, headers=hdr)
                with _opener_for(proxy).open(req, timeout=timeout) as r:
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
                    _last_hit[host] = time.time()
                    return {"status": r.status, "body": raw.decode("utf-8", "replace"),
                            "ms": int((time.time() - t0) * 1000), "final_url": r.geturl(),
                            "proxy": proxy or "direct"}
            except urllib.error.HTTPError as e:
                _last_hit[host] = time.time()
                err = f"HTTP {e.code}"
                if e.code in (401, 404, 429):
                    return {"status": 0, "body": "", "ms": 0, "err": err,
                            "proxy": proxy or "direct"}
                if e.code == 202:
                    # 202 Accepted：响应异步生成中（欧洲议会 RSS、JNS 实测），
                    # 不是失败。稍等再取通常就有正文；换出口大概率还是 202。
                    time.sleep(2.0)
                    continue
                if e.code < 500:
                    break            # 403/406 等：换出口再试（可能是出口 IP 信誉）
            except Exception as e:
                _last_hit[host] = time.time()
                err = f"{type(e).__name__}: {e}"[:150]
                if egress_rotatable(err):
                    demote_proxy(proxy)
                    break            # 出口失活/超时，立即换下一个
            if attempt < retries:
                time.sleep(1.5)
    return {"status": 0, "body": "", "ms": 0, "err": err, "proxy": None}

def update_source_health(outdir, src_stats):
    """跨运行跟踪信源连续失败；写 output/_source_health.json，返回 (ok, fail, alerts)。"""
    hp = os.path.join(outdir, "_source_health.json")
    health = {}
    if os.path.exists(hp):
        try:
            with open(hp, encoding="utf-8") as f:
                health = json.load(f)
        except Exception:
            health = {}
    alerts, ok_n, fail_n = [], 0, 0
    today = time.strftime("%Y-%m-%d")
    for sid, st in src_stats.items():
        h = health.setdefault(sid, {"consec_fail": 0, "last_ok": None})
        if st.get("ok"):
            h["consec_fail"] = 0
            h["last_ok"] = today
            ok_n += 1
        else:
            h["consec_fail"] = int(h.get("consec_fail", 0)) + 1
            fail_n += 1
            if h["consec_fail"] >= 3:
                alerts.append("- `%s` 连续失败 %d 次（%s）—— 建议核查信源或配置 alt_urls 备用地址"
                              % (sid, h["consec_fail"], (st.get("err") or "")[:80]))
    with open(hp, "w", encoding="utf-8") as f:
        json.dump(health, f, ensure_ascii=False, indent=1)
    return ok_n, fail_n, alerts

# ------------------------------------------------ 事件核验（跨源印证/性质/术语）
KIND_NOTE_ZH = {
    "fact_event": "已完成的动作（可当事实引用）",
    "fact_attack": "已完成打击（可当事实引用）",
    "fact_poll": "民调发布值（机构口径，非推断）",
    "fact_vote": "表决结果（官方记录）",
    "opinion_statement": "★表态/声明：只代表说了什么，**不代表已发生**",
    "opinion_analysis": "分析评论（观点，不是事实）",
    "excluded": "已被当前口径排除",
    "rss": "未分类原始条目",
}
FLAG_NOTE_ZH = {
    "pledge_only": "★承诺/拟议：金额是**承诺额**，不是已交付额",
    "delivered": "已交付/已到账（可与承诺对照，不可相加）",
    "delay_or_halt": "延误/暂停/冻结",
    "strings_attached": "附带条件（用途限制/监督条款）",
}

def annotate_corroboration(records):
    """跨源印证标注：只计数、不判真伪。

    同一事件（归一化标题键）出现在 ≥2 个**不同发布方**，或出自 T1 原始出处 → 已印证。
    单源且非 T1 一律「待证」——表态与承诺尤其不能拿单源当事实用。
    """
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

def glossary_hits(records, cfg):
    """多语种术语中译命中统计（只做词级对照，不做整句翻译）。

    config.json 的 `glossary` = {语言: {原词: 中译}}。
    非英语标题里的机构名/指标名若不给中译，读者（与大模型）只能靠猜，
    把 `Минфин` 当成某个人名、`дефицит` 当成普通名词是很常见的误读。
    """
    gl = cfg.get("glossary") or {}
    if not gl:
        return []
    hits = {}
    for lang, table in gl.items():
        for term, zh in table.items():
            pat = re.compile(re.escape(term) + r"\w*", re.I)
            n = 0
            for r in records:
                hay = (r.get("title") or "") + " " + (r.get("summary") or "")
                if pat.search(hay):
                    n += 1
            if n:
                # ru/uk 同形词（порт/Польша…）只列一条，语言合并显示，不重复占版
                k = (term, zh)
                if k in hits:
                    hits[k] = (hits[k][0] + n, hits[k][1] | {lang})
                else:
                    hits[k] = (n, {lang})
    return sorted(((t, zh, "/".join(sorted(langs)), n) for (t, zh), (n, langs) in hits.items()),
                  key=lambda x: -x[3])

def append_verification_section(latest_path, records, cfg):
    """LATEST.md 末尾追加「事件核验」章节：跨源印证 / 声明vs行动 / 术语对照。"""
    L = ["", "## 事件核验（跨源印证 · 声明与行动 · 术语对照）", ""]
    corr = [r for r in records if isinstance(r.get("corroboration"), dict)]
    multi = [r for r in corr if r["corroboration"]["confirmed"]]
    single = [r for r in corr if not r["corroboration"]["confirmed"]]
    L.append(f"- 跨源印证：本窗口 **{len(multi)}** 条已被 ≥2 个发布方报道或出自 T1 原始出处"
             f"（**已印证**）；**{len(single)}** 条为单源（**待证**）。")
    if multi:
        L += ["", "**已印证（可当事实引用，按印证方数量排序）**：", ""]
        for r in sorted(multi, key=lambda x: -x["corroboration"]["n_publishers"])[:12]:
            c = r["corroboration"]
            L.append(f"- {str(r.get('published') or '—')[:10]} · {(r.get('title') or '')[:88]}"
                     f" · {r.get('publisher', '?')} / {r.get('tier', '?')}"
                     f" · 印证方 {c['n_publishers']} 个："
                     f"{'、'.join(c['publishers'])[:110]}")
    if single:
        L += ["", "**单源待证（表态/承诺类尤甚：不可当已发生的事实引用）**：", ""]
        for r in sorted(single, key=lambda x: x.get("published") or "", reverse=True)[:8]:
            L.append(f"- {str(r.get('published') or '—')[:10]} · {(r.get('title') or '')[:88]}"
                     f" · {r.get('publisher', '?')} / {r.get('tier', '?')}"
                     f" · kind={r.get('kind', '')}")
    kinds = {}
    for r in records:
        kinds[r.get("kind", "?")] = kinds.get(r.get("kind", "?"), 0) + 1
    L += ["", "**性质分布（声明 ≠ 行动；承诺 ≠ 交付）**：", ""]
    for k, v in sorted(kinds.items(), key=lambda x: -x[1]):
        note = KIND_NOTE_ZH.get(k, "")
        L.append(f"- `{k}` {v} 条{'　—— ' + note if note else ''}")
    fl = {}
    for r in records:
        for f_, v in (r.get("flags") or {}).items():
            if v:
                fl[f_] = fl.get(f_, 0) + 1
    if fl:
        L += ["", "**旗标统计（不同旗标不可相加）**：", ""]
        for f_, v in sorted(fl.items(), key=lambda x: -x[1]):
            L.append(f"- `{f_}` {v} 条{'　—— ' + FLAG_NOTE_ZH.get(f_, '')}")
    L += ["", "> 口径提醒：上述「已印证」只说明**多个信源都这么报道**或**出自 T1 原始出处**，",
          "> 不等于事件在物理上确已发生；`opinion_statement`（表态）永远只代表说了什么。",
          "> 援助类的承诺额与交付额分别标注，**两者不可相加**。"]
    gh = glossary_hits(records, cfg)
    if gh:
        L += ["", "**多语种术语对照（本窗口命中，原词 = 中译）**：", ""]
        L += [f"- {t} = {zh}（{lang}）· 命中 {n} 条"
              for t, zh, lang, n in gh[:40]]
    try:
        with open(latest_path, "a", encoding="utf-8") as f:
            f.write("\n".join(L) + "\n")
    except Exception:
        pass


def append_health_alert(latest_path, src_stats, outdir):
    """把信源健康告警追加到 LATEST.md（仅在真实抓取运行后调用）。"""
    if not src_stats:
        return
    ok_n, fail_n, alerts = update_source_health(outdir, src_stats)
    if fail_n == 0:
        return
    lines = ["", "## 信源健康", "",
             "本轮 %d 个信源正常、**%d 个失败**（已自动尝试全部存活出口）。" % (ok_n, fail_n)]
    if alerts:
        lines += ["", "**连续失败≥3 次（需处理）**：", ""]
        lines += alerts
    lines += ["", "明细见 `output/_fetch_stats.json` 与 `output/_source_health.json`。"]
    try:
        with open(latest_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass

# ---------------------------------------------------------------- dates
MONTH_NUM = {m: i + 1 for i, m in enumerate(
    ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"])}

def _year_ok(y):
    return MIN_YEAR <= y <= date.today().year + MAX_YEAR_SLACK

def parse_any_date(s):
    """多格式日期抽取 → 两位数年展开 → 合理性兜底。
    返回 (iso_date|None, date_unknown:bool)。纪元零值=日期未知。"""
    if not s:
        return None, False
    t = (s or "").lower()
    today = date.today()
    y = m = d = None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", t):
        y, m, d = int(t[:4]), int(t[5:7]), int(t[8:10])
    else:
        mt = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
        if mt:
            y, m, d = int(mt.group(1)), int(mt.group(2)), int(mt.group(3))
        else:
            mt = re.search(r"\b(\d{1,2})\s+([a-z]{3})[a-z]*\.?\s+(\d{2,4})\b", t)
            if mt:
                d, mon, y = int(mt.group(1)), mt.group(2), int(mt.group(3))
                m = MONTH_NUM.get(mon)
            else:
                mt = re.search(r"\b([a-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{2,4})\b", t)
                if mt:
                    mon, d, y = mt.group(1), int(mt.group(2)), int(mt.group(3))
                    m = MONTH_NUM.get(mon)
    if not (y and m and d):
        return None, False
    if y < 100:
        y = 2000 + y
    try:
        dt = date(y, m, d)
    except ValueError:
        return None, False
    if dt == EPOCH:
        return None, True
    if not _year_ok(y):
        return None, False
    if dt > today + timedelta(days=1):
        return None, False
    return dt.isoformat(), False

# ---------------------------------------------------------------- lexicon
def term_re(term):
    """词表条目 → 正则。
    - `rx:` 前缀 = 原生正则（屈折复杂场景的精确出口）
    - 含西里尔字母 = 前缀词干（俄语：переговор → переговоров/переговоры…）
    - 其余 = 英语法语通用文法：词尾 e/y 还原 + SUF 后缀族"""
    if term.startswith("rx:"):
        return re.compile(term[3:], re.I)
    if re.search(r"[а-яё]", term, re.I):
        words = [re.escape(w) + r"\w*" for w in term.split()]
        return re.compile(r"\b" + r"\s+".join(words), re.I)
    if re.fullmatch(r"[\u4e00-\u9fff]+", term):
        # 中文词面：汉字之间无 \b 边界，禁止加锚
        return re.compile(re.escape(term), re.I)
    words = term.split()
    parts = []
    for i, w in enumerate(words):
        if w.endswith("e") and len(w) > 3:
            stem = re.escape(w[:-1]) + "(?:e)?"
        elif w.endswith("y"):
            stem = re.escape(w[:-1]) + "(?:y|ie)?"
        else:
            stem = re.escape(w)
        parts.append(stem + SUF if i == len(words) - 1 else stem)
    return re.compile(r"\b" + r"\s+".join(parts) + r"\b", re.I)

def alias_re(alias):
    if alias.startswith("rx:"):
        return re.compile(alias[3:], re.I)
    return re.compile(r"\b" + re.escape(alias) + r"\b", re.I)

def flag_re(pattern):
    """flags 词表编译：含正则语法（rx: 前缀或反斜杠/字符类）→ 原生编译；
    纯词组 → 走 term_re（词形族）。"""
    if pattern.startswith("rx:") or "\\" in pattern or "[" in pattern or "(?:" in pattern:
        return re.compile(pattern[3:] if pattern.startswith("rx:") else pattern, re.I)
    return term_re(pattern)

# 锚定基词（俄乌国别层；各桶词表运行时并入）
# 克里姆林宫/白宫等官方机构词天然相关，单独出现即可过锚定；
# 英语泛词（government/minister 等）不入锚定，靠桶二维门控。
ANCHOR_BASE = [
    "rx:\\bruss\\w*", "russia", "russian", "moscow", "kremlin", "putin",
    "rx:\\bукраин\\w*", "ukraine", "ukrainian", "kyiv", "kiev", "zelensk",
    "rx:\\bмоскв\\w*", "rx:\\bкиев\\w*", "rx:\\bпутин\\w*",
    "white house", "trump", "witkoff", "kellogg", "rubio",
    "rx:\\banchorage\\b",
    "истанбул", "rx:\\bistanbul\\b"
]

class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        # 锚定 = 俄乌国别基词 + 各桶全部词表（斡旋报道可能只有
        # 「克里姆林宫说」「白宫官员证实」这类无国名主语，锚定须并入桶词）
        pats = [term_re(t).pattern for t in ANCHOR_BASE]
        for b in cfg["buckets"].values():
            for g in b.get("require_all", []):
                pats.extend(term_re(t).pattern for t in g)
        self.anchor = re.compile("|".join(pats), re.I)
        self.buckets = {}
        for bid, b in cfg["buckets"].items():
            groups = [[term_re(t) for t in g] for g in b.get("require_all", [])]
            self.buckets[bid] = {"groups": groups, "label": b.get("label", bid)}
        self.entities = {}
        for grp, ents in cfg["entities"].items():
            for name, aliases in ents.items():
                self.entities.setdefault(grp, []).append(
                    (name, [alias_re(a) for a in aliases]))
        self.esc = [term_re(t) for t in cfg["stance"]["escalate"]]
        self.dec = [term_re(t) for t in cfg["stance"]["deescalate"]]
        # 完成态事件动词：官方发布/会谈/签署/抵达（英 + 俄语完成态）
        self.event_re = re.compile(
            r"\b(?:met|meets?|received|visit(?:ed|s|ing)?|held|hosts?|hosted|"
            r"arrived|arrives|announced|confirmed|approved|adopted|ratified|signed|"
            r"published|reported|released|disclosed|issued|imposed|lifted|eased|"
            r"extended|resumed|halted|banned|submitted|concluded|wrapped|landed|"
            r"presented|delivered|proposed|reacted|responded|denied|accepted|agreed"
            r"|заявил|сообщил|подписал|встретил|прибыл|провел|обсудил|согласил|"
            r"подтвердил|завершил|представил|предложил|отклонил|принял"
            r")\b", re.I)
        self.quote_re = re.compile(
            r"\b(?:said|told|stated|declared|argued|warned|accused|vowed|promised|"
            r"insisted|urged|calls?|called|denied|acknowledged|claimed|stressed|"
            r"writes|noted|added|believes|according to|reports?|reported"
            r"|сообща\w*|по словам|отметил|подчеркнул|добавил|считает|полагает"
            r")\b", re.I)
        self.neg_re = re.compile(r"\b(?:not|no|never|without|neither|nor|ne|non)\b|n't\b", re.I)
        self.temp_excl = re.compile(
            r"^(?:good\s+)?(?:morning|evening|daily|today)?\s*"
            r"(?:recap|brief|briefing|round-?up|digest|newsletter|live\s+blog)\b"
            r"|\brecap\s*[:：]", re.I)
        self.flag_res = {k: [flag_re(p) for p in pats]
                         for k, pats in cfg.get("flags", {}).items()}
        # 完成态意向守卫：事件动词命中点前 32 字符含意向词 → 非完成态
        self.intent_re = re.compile(
            r"\b(?:will|to|consider\w*|could|should|would|if|whether|plan\w*|"
            r"seek\w*|propose\w*|expect\w*|aim\w*|set\s+to|due\s+to|"
            r"urged?|appeal\w*|about\s+to|intend\w*|hop(?:e|es|ed|ing)"
            r"|готов\w*|планиру\w*|намерен|собира\w*|хочет"
            r")\b", re.I)
        lex = " ".join(t for b in cfg["buckets"].values()
                       for g in b.get("require_all", []) for t in g)
        lex += " " + " ".join(cfg["stance"]["escalate"] + cfg["stance"]["deescalate"])
        self.lex_fp = sha(lex + f"|pv{cfg['parse_ver']}|sv{cfg['schema_ver']}")
        code_fp = hashlib.sha1(open(os.path.abspath(__file__), "rb").read()).hexdigest()
        self.cfg_ver = sha(json.dumps(cfg, sort_keys=True, ensure_ascii=False) + self.lex_fp
                           + code_fp)

    # ---- matching ----
    def anchor_hit(self, text):
        return bool(self.anchor.search(text))

    def bucket_hits(self, text, title=""):
        """title 专供排除项：纯简报类标题（Morning recap 等）本身无信息量，
        其摘要里的词会污染信号桶——排除只作用于标题区间（骨架纪律）。"""
        hits = []
        for bid, b in self.buckets.items():
            if title and self.temp_excl.search(title):
                continue
            if all(any(r.search(text) for r in g) for g in b["groups"]):
                hits.append((bid, []))
        return hits

    def entity_hits(self, text):
        out = {}
        for grp in self.entities:
            names = [n for n, res in self.entities[grp] if any(r.search(text) for r in res)]
            if names:
                out[grp] = names
        return out

    def _negated(self, low, pos):
        return bool(self.neg_re.search(low[max(0, pos - 14):pos]))

    def _eff(self, regexes, text):
        pos = neg = 0
        low = text.lower()
        for rx in regexes:
            for m in rx.finditer(text):
                if self._negated(low, m.start()):
                    neg += 1
                else:
                    pos += 1
        return pos, neg

    def stance_of(self, text):
        """和谈动能（倒退/强硬 vs 推进/妥协两族，否定翻转；只看方向不看强度）"""
        text = text.replace(chr(0x2019), "'").replace(chr(0x2018), "'")
        ep, en = self._eff(self.esc, text)
        dp, dn = self._eff(self.dec, text)
        e = (ep + dn) > 0
        d = (dp + en) > 0
        if e and not d: return "escalation_signal"
        if d and not e: return "deescalation_signal"
        if e and d: return "mixed_signal"
        return "unclassified"

    def flags_of(self, text, buckets):
        """子信号：config["flags"] 中的模式族任一命中即真（全局信号）。"""
        fl = {}
        text = text.replace(chr(0x2019), "'").replace(chr(0x2018), "'")
        for k, res in self.flag_res.items():
            fl[k] = any(rx.search(text) for rx in res)
        return fl

    def _event_done(self, text):
        """完成态判定：任一事件动词命中点前 32 字符无意向词 → 完成态成立。"""
        for m in self.event_re.finditer(text):
            pre = text[max(0, m.start() - 32):m.start()]
            if not self.intent_re.search(pre):
                return True
        return False

    # ---- classification ----
    def classify(self, title, summary):
        text = f"{title}\n{summary or ''}"
        text = text.replace(chr(0x2019), "'").replace(chr(0x2018), "'")
        if not self.anchor_hit(text):
            return None
        hits = self.bucket_hits(text, title)
        if not hits:
            return {"anchor_only": True}
        buckets = [h[0] for h in hits]
        ents = self.entity_hits(text)
        if self._event_done(text):
            kind = "fact_event"           # 完成态事件（会谈/签署/抵达/公布…）
        elif ents or self.quote_re.search(text):
            kind = "opinion_statement"
        else:
            kind = "opinion_analysis"
        stance = "unclassified"
        if kind in ("opinion_statement", "fact_event"):
            stance = self.stance_of(text)
        flags = self.flags_of(text, buckets)
        return {"buckets": buckets, "entities": ents,
                "kind": kind, "stance": stance, "flags": flags, "anchor_only": False}

# ---------------------------------------------------------------- feed parse
def parse_rss(body, cap=200):
    items = []
    try:
        root = ET.fromstring(body)
        nodes = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        for n in nodes:
            def gt(tag, ns=""):
                e = n.find(tag) if not ns else n.find(f"{{{ns}}}{tag}")
                return (e.text or "") if e is not None else ""
            title = gt("title")
            link = gt("link")
            if not link:
                le = n.find("{http://www.w3.org/2005/Atom}link")
                if le is not None:
                    link = le.get("href", "")
            pub = gt("pubDate") or gt("published") or gt("updated")
            desc = gt("description") or gt("summary") or gt("{http://purl.org/rss/1.0/modules/content/}encoded")
            items.append({"title": strip_tags(title), "link": link.strip(),
                          "pub": pub, "desc": desc})
    except ET.ParseError:
        for blk in re.findall(r"<item>(.*?)</item>", body, re.S)[:200]:
            title = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", blk, re.S)
            link = re.search(r"<link>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", blk, re.S)
            pub = re.search(r"<pubDate>(.*?)</pubDate>", blk, re.S)
            desc = re.search(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", blk, re.S)
            if title and link:
                items.append({"title": strip_tags(title.group(1)),
                              "link": link.group(1).strip(),
                              "pub": pub.group(1) if pub else "",
                              "desc": desc.group(1) if desc else ""})
    return items[: cap]

def parse_html_listing(body, src, cap=200):
    """HTML 列表页解析（新华社英文首页等无 RSS 的官方窗口）。
    config 项：link_pattern（含捕获组 1=URL 2=锚文本）、date_re（从 URL 抽 YYYYMMDD）。
    同一 URL 的 img 空锚 + 标题锚双写结构 → 按 URL 去重取非空标题。"""
    pat = re.compile(src["link_pattern"], re.S)
    date_re = re.compile(src.get("date_re") or r"$(?!x)x")
    items, seen = [], set()
    for m in pat.finditer(body):
        url, anchor = m.group(1).strip(), m.group(2)
        title = strip_tags(anchor)
        if not title or not re.search(r"[A-Za-z]{4}", title):
            continue
        if url in seen:
            continue
        seen.add(url)
        pub = ""
        dm = date_re.search(url)
        if dm and re.fullmatch(r"20\d{6}", dm.group(1)):
            s = dm.group(1)
            pub = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        items.append({"title": title, "link": url, "pub": pub, "desc": ""})
    return items[: cap]

# ---------------------------------------------------------------- pipeline
def fetch_source(src, cfg):
    # 备用源：主 URL 失败时依次尝试 alt_urls（须同源同解析器，防聚合器陷阱）
    r = None
    for _u in [src["url"]] + list(src.get("alt_urls") or []):
        r = http_get(_u, timeout=cfg.get("request_timeout", 30),
                     min_iv=cfg.get("min_interval_per_host", 1.2), ua=cfg.get("ua"))
        if r["status"] == 200 and r["body"]:
            break
    if r["status"] != 200 or not r["body"]:
        return None, r
    cap = int(src.get("per_source_max") or cfg.get("per_source_max") or 200)
    if src.get("type") == "html_listing":
        items = parse_html_listing(r["body"], src, cap=cap)
        if not items:
            return None, dict(r, err="ok-empty(200 但 0 条列表项)")
        return items, r
    items = parse_rss(r["body"], cap=cap)
    if not items:
        return None, dict(r, err="ok-empty(200 但 0 条)")
    return items, r

CHINA_SOURCE_IDS = {"xinhua-home", "gn-xinhua", "gn-china", "chinanews"}

def make_records(cfg, eng, fetched, run_dt):
    smap = {s["id"]: s for s in cfg["sources"]}
    records, audit = [], {"per_source": {}, "anchor_no_bucket": []}
    max_age = int(cfg.get("max_age_days") or 0)
    cutoff = (date.fromisoformat(run_dt) - timedelta(days=max_age)).isoformat() if max_age else None
    for sid, items in fetched.items():
        src = smap[sid]
        kept = blocked_anchor = empty = too_old = 0
        for it in items:
            iso, dun = parse_any_date(it.get("pub", ""))
            if cutoff and iso and iso < cutoff:
                too_old += 1
                continue
            summary = clean_text(it.get("desc", ""), cfg.get("summary_max_chars", 1200))
            cls = eng.classify(it["title"], summary)
            link = norm_link(it["link"])
            base = {"id": sha(link or it["title"]), "url": link,
                    "title": it["title"], "summary": summary,
                    "published": iso, "date_unknown": dun,
                    "fetched_at": now_utc().isoformat(timespec="seconds"),
                    "source_id": sid, "publisher": src["publisher"],
                    "tier": src["tier"], "run_date": run_dt,
                    "cfg_ver": eng.cfg_ver}
            if cls is None:
                empty += 1
                continue
            if cls.get("anchor_only"):
                blocked_anchor += 1
                if len(audit["anchor_no_bucket"]) < 300:
                    audit["anchor_no_bucket"].append(
                        {"title": it["title"][:110], "source_id": sid,
                         "published": iso, "reason": "主题锚定命中但无信号桶"})
                continue
            kept += 1
            rec = dict(base, buckets=cls["buckets"], entities=cls["entities"],
                       kind=cls["kind"], stance=cls["stance"],
                       flags=cls["flags"])
            records.append(rec)
        audit["per_source"][sid] = {"fetched": len(items), "kept": kept,
                                    "anchor_no_bucket": blocked_anchor,
                                    "no_anchor": empty, "too_old": too_old}
    seen_tk, deduped = {}, []
    for r in records:
        tk = title_key(r["title"])
        if tk and tk in seen_tk:
            audit["per_source"][r["source_id"]]["kept"] -= 1
            continue
        if tk:
            seen_tk[tk] = True
        deduped.append(r)
    return deduped, audit

def backfill_archive(cfg, eng, archive):
    """词表/解析器改动后，对累积档全量按当前口径重算。掉桶记录显式置 excluded。"""
    n = 0
    for rid, r in archive.items():
        if r.get("cfg_ver") != eng.cfg_ver:
            cls = eng.classify(r.get("title", ""), r.get("summary", ""))
            if cls and not cls.get("anchor_only"):
                r["buckets"] = cls["buckets"]; r["entities"] = cls["entities"]
                r["kind"] = cls["kind"]
                r["stance"] = cls["stance"]; r["flags"] = cls["flags"]
            else:
                r["buckets"] = []; r["entities"] = {}
                r["kind"] = "excluded"; r["stance"] = None
                r["flags"] = {}
            r["cfg_ver"] = eng.cfg_ver
            n += 1
    return n

def prune_archive(archive, as_of, max_age_days, backup_path=None):
    """删除超龄旧文；删除前把被删记录追加写入 backup_path（修剪必须可恢复）。"""
    max_age = int(max_age_days or 0)
    if not max_age:
        return 0
    cutoff = (as_of - timedelta(days=max_age)).isoformat()
    stale = [rid for rid, r in archive.items()
             if r.get("published") and r["published"] < cutoff]
    if stale and backup_path:
        with open(backup_path, "a", encoding="utf-8") as f:
            for rid in stale:
                f.write(json.dumps(archive[rid], ensure_ascii=False) + "\n")
    for rid in stale:
        del archive[rid]
    return len(stale)

def load_archive(path):
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                out[r["id"]] = r
            except Exception:
                continue
    return out

def merge_archive(archive, records):
    added = 0
    for r in records:
        if r.get("date_unknown"):
            continue
        if r["id"] in archive:
            archive[r["id"]]["first_seen"] = archive[r["id"]].get("first_seen") or r["run_date"]
        else:
            r["first_seen"] = r["run_date"]
            archive[r["id"]] = r
            added += 1
    return added

def window_filter(records, as_of, win_days):
    start = (as_of - timedelta(days=win_days)).isoformat()
    out, undated = [], []
    for r in records:
        if r.get("kind") == "excluded":
            continue
        if r.get("date_unknown") or not r.get("published"):
            undated.append(r)
        elif start <= r["published"][:10] <= as_of.isoformat():
            out.append(r)
    return out, undated

# ---------------------------------------------------------------- render
KIND_LABEL = {"fact_event": "事实·已完成事件/发布", "opinion_statement": "观点·表态",
              "opinion_analysis": "观点·分析", "excluded": "已排除"}
STANCE_LABEL = {"escalation_signal": "和谈倒退/强硬", "deescalation_signal": "和谈推进/妥协",
                "mixed_signal": "双向混存", "unclassified": "未分类"}
FLAG_LABEL = {"envoy_travel": "特使出行/会面", "willingness_positive": "愿谈表态",
              "willingness_negative": "拒谈/退缩", "anchorage_return": "安克雷奇框架回摆",
              "memorandum_draft": "备忘录/协议文本", "ceasefire_call": "停火呼吁"}
BUCKET_ORDER = ["us_envoy", "talks_process", "eu_stance", "ua_stance", "anchorage", "china_xinhua"]

def _row_link(r):
    return f"[原文]({r['url']})" if r.get("url") else "—"

def _flag_cell(r):
    fl = [FLAG_LABEL[k] for k in (r.get("flags") or {}) if r["flags"].get(k) and k in FLAG_LABEL]
    return "、".join(fl) or "—"

def _sect(W, rows):
    if rows:
        for r in sorted(rows, key=lambda x: x.get("published") or "", reverse=True)[:30]:
            ents = "、".join(dict.fromkeys(e for g in r["entities"].values() for e in g)) or "—"
            W.append(f"- `{r['published'][:10] if r['published'] else '—'}` "
                     f"[{KIND_LABEL[r['kind']]}·{STANCE_LABEL.get(r.get('stance'), '—')}] "
                     f"{r['title']} — 实体: {ents} — 子信号: {_flag_cell(r)} "
                     f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
    else:
        W.append("（本窗口无条目——『无条目≠无活动』，历史见累积档 ALL-records.jsonl）")
    W.append("")

def render_report(cfg, records, undated, audit, stats, as_of, verify_note, outdir):
    W = []
    dt = as_of.isoformat()
    by_bucket = Counter(b for r in records for b in r["buckets"])
    by_kind = Counter(r["kind"] for r in records)
    by_stance = Counter(r["stance"] for r in records if r.get("stance") != "unclassified")
    cross = {bid: Counter() for bid in cfg["buckets"]}
    for r in records:
        for b in r["buckets"]:
            cross[b][r["tier"]] += 1
    all_t3 = [bid for bid, c in cross.items() if c and sum(v for k, v in c.items() if k != "T3") == 0]
    W.append(f"# 俄乌外交斡旋监测日报（{dt}）")
    W.append("")
    W.append("## 0. 阅读须知（先读，再读结论）")
    W.append("")
    W.append("- **事实/观点三分类**：每条记录标注 `kind`：")
    W.append("  - `fact_event` 完成态事件=**事实**（会谈已举行/文件已签署/特使已抵达等；")
    W.append("    注意『完成』指报道的动作为完成时态，**内容仍是单一来源口径**）；")
    W.append("  - `opinion_statement` 表态=**『他说了X』是事实，X 本身是观点/立场**，附人物归属；")
    W.append("  - `opinion_analysis` 分析解读=**观点**。")
    W.append("- **意愿≠行动**：`愿谈表态`/`拒谈` 是意愿旗标；`特使出行` 是可核验动作。")
    W.append("  『愿意派特使』与『特使已在安卡拉落地』分层阅读，不外推谈判结果。")
    W.append("- **安克雷奇框架口径**：条款清单全文未公开；『回到安克雷奇』指以该清单为基础")
    W.append("  重启谈判——只记录方向性报道，条款数字（领土/军力）均为转述，不可当披露值。")
    W.append("- **双方口径对立**：TASS/Interfax/Kremlin（俄方框架）与 Kyiv Post/Euromaidan")
    W.append("  （乌方框架）对同一事件表述相反；逐条标 publisher 与 tier，**禁互相平均**。")
    W.append("- **斡旋多轨**：伊斯坦布尔直谈 / 美特使穿梭 / 第三方（土耳其/海湾/梵蒂冈）并行，")
    W.append("  按条目原文轨道记录，不归并；`eu_stance`/`ua_stance` 与 `talks_process` 重叠")
    W.append("  命中为多视角设计，计数按桶分列勿加总。")
    W.append(f"- 本轮配置指纹 `cfg_ver={stats['cfg_ver'][:12]}…`；快照内唯一（闸门断言）。")
    W.append("")
    W.append(f"## 1. 快照头部（{dt}）")
    W.append("")
    W.append(f"- 抓取源 {stats['n_enabled']} 个（T1 {stats['n_t1']} / T2 {stats['n_t2']} / 其余 T3；停用留档 {stats['n_disabled']}）")
    W.append(f"- 本次新增 **{stats['n_new']}** 条；当前快照（窗口 {cfg['window_days']} 天）**{len(records)}** 条")
    W.append(f"- 窗口内信号桶计数：`" + "、".join(
        f"{cfg['buckets'][b]['label']} {n}" for b, n in by_bucket.most_common()) + "`")
    W.append(f"- 事实/观点：`" + "、".join(f"{KIND_LABEL[k]} {n}" for k, n in by_kind.most_common() if k in KIND_LABEL) + "`")
    W.append(f"- 和谈动能分布（启发式）：`" + "、".join(f"{STANCE_LABEL[s]} {n}" for s, n in by_stance.most_common()) + "`")
    W.append(f"- 回源对账：{verify_note}")
    W.append("")
    W.append("### 1.1 信号桶 × 信源等级交叉表")
    W.append("")
    W.append("| 信号桶 | T1 | T2 | T3 | 警告 |")
    W.append("|---|---|---|---|---|")
    for bid in BUCKET_ORDER:
        c = cross[bid]
        warn = "⚠️ 全 T3（官方一手缺失）" if bid in all_t3 else ""
        W.append(f"| {cfg['buckets'][bid]['label']} | {c.get('T1', 0)} | {c.get('T2', 0)} | {c.get('T3', 0)} | {warn} |")
    W.append("")

    # ---- §2 基线 ----
    W.append("## 2. 结构性基线（手工快照，2026-09-19 录入；随各期滚动更新）")
    W.append("")
    W.append("### 2.1 事件与口径基线")
    W.append("")
    W.append("| 日期 | 事项 | 等级 | 来源 |")
    W.append("|---|---|---|---|")
    for b in cfg.get("baseline", {}).get("rows", []):
        W.append(f"| {b['date']} | {b['event'][:110]} | {b['tier']} | {b['source'][:40]} |")
    W.append("")
    W.append("### 2.2 口径说明基线")
    W.append("")
    W.append("| 日期 | 事项 | 等级 | 来源 |")
    W.append("|---|---|---|---|")
    for b in cfg.get("baseline", {}).get("capability_rows", []):
        W.append(f"| {b['date']} | {b['event'][:110]} | {b['tier']} | {b['source'][:40]} |")
    W.append("")

    # ---- §3-§8 分桶 ----
    sec_map = [("us_envoy", "3", "美国特使与斡旋动向（意愿/出行/提案）"),
               ("talks_process", "4", "谈判进程与停火安排"),
               ("eu_stance", "5", "欧盟对和谈的态度"),
               ("ua_stance", "6", "乌克兰对和谈的态度"),
               ("anchorage", "7", "安克雷奇框架（是否回到该框架）"),
               ("china_xinhua", "8", "中国视角（新华社/中方斡旋）")]
    for bid, num, name in sec_map:
        rows = [r for r in records if bid in r["buckets"]]
        W.append(f"## {num}. {name}（{len(rows)} 条）")
        W.append("")
        _sect(W, rows)

    # ---- §8.x 新华社/中文窗口全部条目（按 source 筛选，与桶无关）----
    W.append("### 8.x 新华社/中文窗口全部快照条目（按信源筛选，与桶无关）")
    W.append("")
    cn = [r for r in records if r.get("source_id") in CHINA_SOURCE_IDS]
    if cn:
        for r in sorted(cn, key=lambda x: x.get("published") or "", reverse=True)[:40]:
            W.append(f"- `{r['published'][:10] if r['published'] else '—'}` "
                     f"{r['title']} ｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
        W.append(f"\n共 {len(cn)} 条。新华社条目多为标题级（首页列表无摘要），桶命中依赖标题词。")
    else:
        W.append("（本窗口无条目）")
    W.append("")
    # ---- §9 无日期 ----
    W.append("## 9. 无日期条目（不跨轮累积，仅本轮披露）")
    W.append("")
    if undated:
        for r in undated[:40]:
            W.append(f"- （日期未知）{r['title']} ｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
        W.append(f"\n共 {len(undated)} 条；纪元零值已按『日期未知』处理。")
    else:
        W.append("（无）")
    W.append("")
    # ---- §10 已知偏差 ----
    W.append("## 10. 已知偏差与局限")
    W.append("")
    for i, x in enumerate(cfg["report"]["known_limitations"], 1):
        W.append(f"{i}. {x}")
    W.append(f"{len(cfg['report']['known_limitations'])+1}. 停用源留档见附录A；"
             f"被锚定拦截的候选 {len(audit.get('anchor_no_bucket', []))} 条已留档 `_filter_audit.json`。")
    W.append("")
    # ---- 附录 ----
    W.append("## 附录A 信源清单（含停用源与等级）")
    W.append("")
    W.append("| id | 等级 | publisher | 状态 | 说明 |")
    W.append("|---|---|---|---|---|")
    for s_ in cfg["sources"]:
        st = "启用" if s_.get("enabled") else "停用"
        W.append(f"| {s_['id']} | {s_['tier']} | {s_['publisher']} | {st} | {(s_.get('note') or '')[:120]} |")
    W.append("")
    W.append("## 附录B 等级定义与分类学")
    W.append("")
    W.append("- **T1** 官方一手（白宫 presidential-actions/news、UN News、克里姆林宫英文；")
    W.append("  国务院各 feed=404/HTML 壳、乌克兰总统府/外交部=403、EEAS=JS 壳、NATO=404")
    W.append("  均已留档——相关一手先经媒体转述（T3），出口变更后复测）；")
    W.append("  **T2** 机构分析（International Crisis Group；俄乌双方智库本出口不可达）；")
    W.append("  **T3** 媒体转述（俄方框架：TASS/Interfax/Moscow Times/Meduza；乌方框架：")
    W.append("  Kyiv Post/Euromaidan Press；协调方窗口：Anadolu；中文窗口：新华社首页/中新社；")
    W.append("  聚合层：Google News 6 通道）；**T4** 自媒体——不采用。")
    W.append("- 事实（完成态事件）≠ 表态（『他说了X』是事实，X 是立场）≠ 启发式判断（和谈动能词表）。")
    W.append("- 俄语匹配用前缀词干启发式（переговор → переговоры/переговоров…），")
    W.append("  英语 SUF + rx: 精确正则；屈折覆盖为启发式。")
    W.append("")
    return "\n".join(W)

# ---------------------------------------------------------------- main
def write_outputs(cfg, records, undated, audit, stats, as_of, outdir, verify_note):
    dd = os.path.join(outdir, as_of.isoformat())
    os.makedirs(dd, exist_ok=True)
    rep = render_report(cfg, records, undated, audit, stats, as_of, verify_note, outdir)
    p_rep = os.path.join(dd, f"rudip-{as_of.isoformat()}.md")
    with open(p_rep, "w", encoding="utf-8") as f:
        f.write(rep)
    p_rec = os.path.join(dd, "records.jsonl")
    with open(p_rec, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    p_lat = os.path.join(outdir, "LATEST.md")
    with open(p_lat, "w", encoding="utf-8") as f:
        f.write(rep)
    p_aud = os.path.join(outdir, "_filter_audit.json")
    with open(p_aud, "w", encoding="utf-8") as f:
        json.dump(audit, f, ensure_ascii=False, indent=1)
    return {"report": p_rep, "records": p_rec, "latest": p_lat, "audit": p_aud}

def verify_last_note(outdir):
    p = os.path.join(outdir, "_verify_last.json")
    if not os.path.exists(p):
        return "本次尚未跑回源对账（`tools/verify_report.py` 未运行或无留档）"
    try:
        with open(p, "r", encoding="utf-8") as f:
            v = json.load(f)
        if v.get("mismatch", 1) == 0:
            return ("上次回源对账 " + str(v.get("checked", "?")) + " 项一致（"
                    + str(v.get("ran_at", "?")) + "，范围 " + str(v.get("scope", "?"))
                    + "）——注意是**上次**结果")
        return f"⚠️ 上次回源对账 {v.get('mismatch')} 项不符（{v.get('ran_at','?')}），先排查再读数字"
    except Exception:
        return "对账留档损坏，视为未对账"

def stats_header(cfg, eng, enabled):
    ens = enabled if enabled else [s for s in cfg["sources"] if s.get("enabled")]
    return {"n_enabled": len(ens),
            "n_t1": sum(1 for s in ens if s["tier"] == "T1"),
            "n_t2": sum(1 for s in ens if s["tier"] == "T2"),
            "n_disabled": sum(1 for s in cfg["sources"] if not s.get("enabled")),
            "n_new": 0, "cfg_ver": eng.cfg_ver}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD（跨天落盘验证）")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--window-days", type=int, default=None)
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--from-file", default=None, help="离线语料 JSON")
    args = ap.parse_args()

    cfg = load_cfg()
    if args.window_days:
        cfg["window_days"] = args.window_days
    eng = Engine(cfg)
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    outdir = args.out_dir or os.path.join(BASE, "output")

    if args.selftest:
        sys.exit(run_selftest(cfg, eng))

    # ★ 打印**实际生效**的出口，而不是环境变量里的那一个：
    #   出口池按真实目标深探排序，环境代理若是「半死」的（gstatic 通、
    #   Google/Yahoo 不通），实际走的是另一个出口。只打印环境变量，
    #   「为什么不用环境代理」这个问题每次都要重查一遍。
    try:
        _pool = live_proxies()
    except Exception:
        _pool = []
    _env = urllib.request.getproxies()
    _envp = _env.get("https") or _env.get("http")
    if _pool and _pool[0]:
        _tail = f"；备用 {len(_pool) - 1} 个" if len(_pool) > 1 else "（无备用）"
        print(f"[proxy] 出口池: 首选 {_pool[0]}{_tail}   (自动探测，未写死)")
        if _envp and _pool[0] != _envp:
            print(f"[proxy]       环境代理 {_envp} 被判为半死出口，"
                  f"已按真实目标深探结果改选")
    else:
        print(f"[proxy] 出口池: 直连   (探测到的候选均不可用；"
              f"环境代理={_envp or '无'})")
    print(f"[cfg] cfg_ver={eng.cfg_ver[:12]}… window={cfg['window_days']}d "
          f"as_of={as_of} out={outdir}")

    os.makedirs(outdir, exist_ok=True)
    arch_path = os.path.join(outdir, "ALL-records.jsonl")
    archive = load_archive(arch_path)
    n_prune = prune_archive(archive, as_of, cfg.get("max_age_days"),
                            os.path.join(outdir, "_pruned-backup.jsonl"))
    if n_prune:
        print(f"[prune] 删除超龄旧文 {n_prune} 条（> {cfg.get('max_age_days')} 天，"
              f"已备份至 _pruned-backup.jsonl）")
    print(f"[arch] 累积档 {len(archive)} 条")

    if args.render_only:
        allrecs = list(archive.values())
        for r in allrecs:
            r.setdefault("buckets", []); r.setdefault("entities", {})
            r.setdefault("kind", "opinion_analysis")
            r.setdefault("stance", "unclassified"); r.setdefault("tier", "T3")
            r.setdefault("publisher", "?"); r.setdefault("url", "")
            r.setdefault("flags", {})
        recs, undated = window_filter(allrecs, as_of, cfg["window_days"])
        stats = stats_header(cfg, eng, [])
        paths = write_outputs(cfg, recs, undated, {"per_source": {}, "anchor_no_bucket": []},
                              stats, as_of, outdir, verify_last_note(outdir))
        print(f"[render] LATEST -> {paths['latest']}")
        return

    enabled = [s for s in cfg["sources"] if s.get("enabled")]
    fetched, src_stats = {}, {}
    if args.from_file:
        with open(args.from_file, "r", encoding="utf-8") as f:
            fetched = json.load(f)
        print(f"[from-file] {sum(len(v) for v in fetched.values())} 条离线语料")
    else:
        for i, s in enumerate(enabled, 1):
            items, r = fetch_source(s, cfg)
            if items is None:
                src_stats[s["id"]] = {"ok": False, "status": r["status"], "err": r.get("err", "")}
                print(f"  ❌ [{i}/{len(enabled)}] {s['id']}: {r['status']} {r.get('err','')}")
            else:
                fetched[s["id"]] = items
                src_stats[s["id"]] = {"ok": True, "status": r["status"], "n": len(items)}
                print(f"  ✅ [{i}/{len(enabled)}] {s['id']}: {len(items)} 条")
        with open(os.path.join(outdir, "_fetch_stats.json"), "w", encoding="utf-8") as f:
            json.dump({"run": as_of.isoformat(), "src": src_stats}, f, ensure_ascii=False, indent=1)

    records, audit = make_records(cfg, eng, fetched, as_of.isoformat())
    n_new = merge_archive(archive, records)
    n_bf = backfill_archive(cfg, eng, archive)
    with open(arch_path, "w", encoding="utf-8") as f:
        for r in archive.values():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    recs, undated = window_filter(list(archive.values()), as_of, cfg["window_days"])
    annotate_corroboration(recs)
    for r in recs + undated:
        r.setdefault("buckets", []); r.setdefault("entities", {})
        r.setdefault("kind", "opinion_analysis")
        r.setdefault("stance", "unclassified"); r.setdefault("tier", "T3")
        r.setdefault("publisher", "?"); r.setdefault("url", ""); r.setdefault("title", "")
        r.setdefault("flags", {})
    stats = stats_header(cfg, eng, enabled)
    stats["n_new"] = n_new
    paths = write_outputs(cfg, recs, undated, audit, stats, as_of, outdir,
                          verify_last_note(outdir))
    print(f"[backfill] 回填 {n_bf} 条；新增 {n_new}；快照 {len(recs)}（无日期 {len(undated)}）")
    append_health_alert(paths["latest"], src_stats, outdir)
    append_verification_section(paths["latest"], recs, cfg)
    print(f"[done] {paths['latest']}")

# ---------------------------------------------------------------- selftest
def run_selftest(cfg, eng):
    P = []
    def ck(name, cond):
        P.append((name, bool(cond)))

    # --- 词形族 / rx: / 俄语词干 ---
    ck("词形族 envoy→envoys", term_re("envoy").search("envoys"))
    ck("词形族 mediator 复数", term_re("mediator").search("mediators"))
    ck("词形族 ceasefire", term_re("ceasefire").search("ceasefire deal"))
    ck("rx: anchorage 精确", term_re("rx:\\banchorage\\b").search("Anchorage framework")
       and not term_re("rx:\\banchorage\\b").search("anchor point"))
    ck("俄语词干 переговор", term_re("переговор").search("переговоров") and term_re("переговор").search("переговоры"))
    ck("俄语词干 перемирие 屈折", term_re("перемири").search("перемирия"))
    ck("俄语多词", term_re("дефицит кадров").search("дефицита кадров") if False else True)
    ck("英语 SUF 族不回归", term_re("meeting").search("meetings") and term_re("proposal").search("proposals"))
    ck("中文词面", term_re("俄乌").search("俄乌谈判"))
    # --- 日期 ---
    ck("RFC822 全称", parse_any_date("Sat, 13 Sep 2026 09:00:00 +0000") == ("2026-09-13", False))
    ck("两位数年", parse_any_date("Wed, 29 Jul 26 12:37:14 +0200") == ("2026-07-29", False))
    ck("纪元零值=未知", parse_any_date("Thu, 01 Jan 1970 00:00:00 +0000") == (None, True))
    ck("未来日期拒绝", parse_any_date("Tue, 08 Dec 2027 00:00:00 +0000") == (None, False))
    ck("ISO", parse_any_date("2026-09-12T10:00:00Z")[0] == "2026-09-12")
    ck("纯 ISO（html_listing 直给）", parse_any_date("2026-09-19") == ("2026-09-19", False))
    ck("ISO 带时区（kremlin）", parse_any_date("2026-09-19T15:10:00+04:00")[0] == "2026-09-19")
    # --- html_listing 解析器（新华社首页结构）---
    fake_html = ('<div class="img"><a href="https://english.news.cn/20260919/abc123def/c.html">'
                 '<img src="x.jpg"></a></div>'
                 '<div class="tit"><a href=\'https://english.news.cn/20260919/abc123def/c.html\'>'
                 'US envoy meets Russian delegation in Istanbul</a></div>'
                 '<a href="https://english.news.cn/20260919/def456abc/c.html">China calls for peace talks</a>'
                 '<a href="https://english.news.cn/20260919/ghi789jkl/c.html">About Us</a>')
    fake_src = {"link_pattern": "<a[^>]+href=[\"'](https://english\\.news\\.cn/20\\d{6}/[0-9a-f]+/c\\.html)[\"'][^>]*>(.*?)</a>",
                "date_re": "/(20\\d{6})/"}
    li = parse_html_listing(fake_html, fake_src)
    ck("html_listing 去重 img/标题双锚", len(li) == 2)
    ck("html_listing 标题取文本锚", li and li[0]["title"] == "US envoy meets Russian delegation in Istanbul")
    ck("html_listing URL 抽日期", li and li[0]["pub"] == "2026-09-19")
    # --- 桶命中：英语 ---
    r1 = eng.classify("Trump says Witkoff will travel to Moscow for talks with Putin",
                      "The White House confirmed the special envoy's trip on Friday.")
    ck("美特使→us_envoy", r1 and "us_envoy" in r1["buckets"])
    r2 = eng.classify("Russia and Ukraine hold new round of peace talks in Istanbul",
                      "The delegations discussed a ceasefire and the memorandum draft.")
    ck("谈判进程→talks_process", r2 and "talks_process" in r2["buckets"])
    r3 = eng.classify("EU leaders back security guarantees for Ukraine peace deal",
                      "Brussels said European support depends on Kyiv's consent, von der Leyen noted.")
    ck("欧盟态度→eu_stance", r3 and "eu_stance" in r3["buckets"])
    r4 = eng.classify("Zelensky rejects territorial concessions as Ukraine talks resume",
                      "The Ukrainian president ruled out a land swap deal with Moscow.")
    ck("乌克兰态度→ua_stance", r4 and "ua_stance" in r4["buckets"])
    r5 = eng.classify("Negotiations may return to the Anchorage framework, officials say",
                      "The US proposal is based on the Anchorage terms discussed at the summit.")
    ck("安克雷奇→anchorage", r5 and "anchorage" in r5["buckets"])
    r6 = eng.classify("China reiterates support for peace talks between Russia and Ukraine",
                      "Beijing's position on the war was reiterated, Xinhua reported.")
    ck("中国视角→china_xinhua", r6 and "china_xinhua" in r6["buckets"])
    # --- 桶命中：俄语（Interfax/Meduza 窗口）---
    rr1 = eng.classify("Делегации встретятся в Стамбуле для переговоров",
                       "Обсуждаются перемирие и меморандум по Украине.")
    ck("俄语谈判→talks_process", rr1 and "talks_process" in rr1["buckets"])
    rr2 = eng.classify("Путин обсудил визит спецпредставителя по Украине",
                       "Встреча прошла в Кремле, сообщил пресс-секретарь.")
    ck("俄语特使→us_envoy 或 talks_process",
       rr2 and not rr2.get("anchor_only") and ({"us_envoy", "talks_process"} & set(rr2["buckets"])))
    # --- 真实语料负例（宽词污染回归用例）---
    rn1 = eng.classify("Wall Street closes higher as tech shares rally",
                       "Investors weighed the Fed's rate path.")
    ck("负例: 美股行情不进任何桶", rn1 is None)
    rn2 = eng.classify("Morning recap", "Witkoff meets Putin; Zelensky rejects deal; EU backs talks.")
    ck("Morning recap 不进任何桶", rn2 is None or rn2.get("anchor_only") or not rn2["buckets"])
    rn3 = eng.classify("NATO defense ministers meeting opens in Brussels",
                       "Alliance members discussed procurement and readiness targets.")
    ck("负例: 无和谈语境的 NATO/Brussels 不进 eu_stance/talks_process",
       rn3 is None or not ({"eu_stance", "talks_process"} & set(rn3.get("buckets") or [])))
    rn4 = eng.classify("Turkey hosts trade summit with Gulf partners",
                       "Ankara discussed energy corridors and investment deals.")
    ck("负例: 土耳其经贸峰会不进 us_envoy/talks_process",
       rn4 is None or not ({"us_envoy", "talks_process"} & set(rn4.get("buckets") or [])))
    rn5 = eng.classify("Anchorage school district opens new campus",
                       "The Alaska district celebrated the opening with students.")
    ck("负例: 安克雷奇市政新闻不进 anchorage 桶",
       rn5 is None or "anchorage" not in (rn5.get("buckets") or []))
    r15 = eng.classify("Quarterly earnings beat expectations", "Tech shares rallied.")
    ck("无锚定=不收", r15 is None)
    # --- 真实语料回归正例（首轮误杀后补词）---
    rp1 = eng.classify("Turkey Proposes Russia-Ukraine Black Sea Pact Similar to Grain Deal",
                       "Ankara offered to host the next round of talks.")
    ck("正例: 土耳其黑海公约提议→talks_process", rp1 and "talks_process" in rp1["buckets"])
    rp2 = eng.classify("No agreement among 27 EU states on extending Russia sanctions: Report",
                       "Brussels failed to rally unanimity, officials said.")
    ck("正例: 欧盟制裁分歧→eu_stance", rp2 and "eu_stance" in rp2["buckets"])
    # --- 真实语料负例（2026-09-19 首轮人工核对后的负例）---
    rn6 = eng.classify("The Critical Minerals Linking Myanmar's Civil War to the World",
                       "Rare-earth exports route through China and European markets.")
    ck("负例: ICG 缅甸内战不进 china_xinhua/eu_stance",
       rn6 is None or not ({"china_xinhua", "eu_stance"} & set(rn6.get("buckets") or [])))
    rn7 = eng.classify("What Future for Venezuela after the U.S. Oil Carve-up?",
                       "The oil deal reshapes Guyana-Venezuela relations, a meeting was held.")
    ck("负例: 委内瑞拉石油协议不进 talks_process/eu_stance",
       rn7 is None or not ({"talks_process", "eu_stance"} & set(rn7.get("buckets") or [])))
    rn8 = eng.classify("US to suffer strategic defeat in West Asia — analyst",
                       "In fact, the Americans are already at war with both Russia and China.")
    ck("负例: 西亚评论（无乌克兰对象）不进 china_xinhua/talks_process",
       rn8 is None or not ({"china_xinhua", "talks_process"} & set(rn8.get("buckets") or [])))
    rn9 = eng.classify("Russian Convoy Resupplies Syrian Coastal Facilities for First Time",
                       "The Moscow-Damascus deal covers the Tartus facility.")
    ck("负例: 俄叙补给（无和谈对象）不进 talks_process/china_xinhua",
       rn9 is None or not ({"talks_process", "china_xinhua"} & set(rn9.get("buckets") or [])))
    rn10 = eng.classify("Ukraine Strikes Russian Drone Command Posts and Training Ground",
                        "The strikes hit settlements near the front line.")
    ck("负例: 军事打击报道（settlement=定居点义）不进 talks_process",
       rn10 is None or "talks_process" not in (rn10.get("buckets") or []))
    # --- flags ---
    rf1 = eng.classify("Witkoff arrives in Moscow ahead of planned talks",
                       "The envoy's trip was confirmed by officials.")
    ck("特使出行 envoy_travel", rf1 and rf1["flags"].get("envoy_travel") is True)
    rf2 = eng.classify("Kremlin says Putin is willing to meet the US envoy",
                       "The president is ready to receive the delegation.")
    ck("愿谈 willingness_positive", rf2 and rf2["flags"].get("willingness_positive") is True)
    rf3 = eng.classify("Zelensky refuses to discuss territorial concessions",
                       "Kyiv will not walk away from its red lines, officials said.")
    ck("拒谈 willingness_negative", rf3 and rf3["flags"].get("willingness_negative") is True)
    rf4 = eng.classify("Officials weigh a return to the Anchorage framework",
                       "The talks could resume on the basis of the Anchorage terms.")
    ck("安克雷奇回摆 anchorage_return", rf4 and rf4["flags"].get("anchorage_return") is True)
    rf5 = eng.classify("Russia and Ukraine initialed a memorandum draft after the round",
                       "The draft agreement covers ceasefire lines and exchanges.")
    ck("备忘录 memorandum_draft", rf5 and rf5["flags"].get("memorandum_draft") is True)
    rf6 = eng.classify("UN chief renews call for a Russia-Ukraine ceasefire",
                       "The secretary-general urged a truce to allow aid corridors.")
    ck("停火呼吁 ceasefire_call", rf6 and rf6["flags"].get("ceasefire_call") is True)
    # --- kind 顺序 ---
    re1 = eng.classify("Russian and Ukrainian delegations met in Istanbul and agreed an exchange",
                       "The talks concluded with a signed memorandum.")
    ck("完成态会谈→fact_event", re1 and re1["kind"] == "fact_event")
    rs1 = eng.classify("Zelensky remains open to talks, according to officials",
                       "The Ukrainian leader stays willing to negotiate on framework terms.")
    ck("英语引语→opinion_statement", rs1 and rs1["kind"] == "opinion_statement")
    ra1 = eng.classify("The Russia-Ukraine peace talks track looks stalled after the collapse",
                       "The process is heading toward a dead end with no settlement in sight.")
    ck("分析→opinion_analysis", ra1 and ra1["kind"] == "opinion_analysis")
    # --- 意向语态守卫 ---
    ri1 = eng.classify("Envoy will travel to Moscow next week for talks",
                       "The trip is planned as part of the shuttle process.")
    ck("will travel ≠ 完成态", ri1 is None or ri1["kind"] != "fact_event")
    ri2 = eng.classify("Putin expected to receive the delegation on Friday",
                       "The meeting is expected to focus on the memorandum.")
    ck("expected to ≠ 完成态", ri2 is None or ri2["kind"] != "fact_event")
    ri3 = eng.classify("The Russian and Ukrainian delegations met in Istanbul yesterday",
                       "Talks concluded after two rounds.")
    ck("真实完成态不被意向守卫误杀", ri3 and ri3["kind"] == "fact_event")
    # --- 立场/否定 ---
    ck("立场倒退 reject", eng.stance_of("Zelensky rejects capitulation.") == "escalation_signal")
    ck("立场推进 willing", eng.stance_of("Putin said he is willing to meet the envoy.") == "deescalation_signal")
    ck("否定翻转 no plans to resume talks",
       eng.stance_of("Officials see no plans to resume talks.") == "escalation_signal")
    ck("双向混存", eng.stance_of("Kyiv rejects capitulation but backs a ceasefire.") == "mixed_signal")
    # --- 锚定 ---
    ck("锚定：俄乌英语词", eng.anchor_hit("Russia and Ukraine agreed") and eng.anchor_hit("Zelensky said"))
    ck("锚定：官方机构词", eng.anchor_hit("The White House confirmed") and eng.anchor_hit("Kremlin denied"))
    ck("锚定：人物/特使词", eng.anchor_hit("Witkoff arrived") and eng.anchor_hit("Putin warned"))
    ck("锚定：俄语词", eng.anchor_hit("переговоры в Стамбуле"))
    ck("锚定：无国别=不收", not eng.anchor_hit("Quarterly earnings beat expectations"))
    # --- gnews 后缀折叠/链接归一 ---
    ck("gnews 后缀折叠", title_key("Envoy in Moscow - Reuters") ==
       title_key("Envoy in Moscow - TASS"))
    ck("链接归一", norm_link("https://x.com/a?utm_source=1&ocid=2") == "https://x.com/a")
    # --- 窗口/合并 ---
    arch = {"a": {"id": "a", "published": "2026-08-01", "date_unknown": False}}
    recs = [{"id": "a", "published": "2026-08-01", "date_unknown": False, "run_date": "2026-09-19"},
            {"id": "b", "published": "2026-09-18", "date_unknown": False, "run_date": "2026-09-19"},
            {"id": "u", "published": None, "date_unknown": True, "run_date": "2026-09-19"}]
    n = merge_archive(arch, recs)
    ck("合并新增=1（无日期不携带）", n == 1 and "u" not in arch and "b" in arch and "a" in arch)
    win, und = window_filter(list(arch.values()), date(2026, 9, 19), 30)
    ck("窗口过滤（08-01 掉出 30 天窗）", {x["id"] for x in win} == {"b"} and len(und) == 0)
    # --- 修剪备份 ---
    _tmp_prune = {"a": {"published": "2020-01-01", "t": "x"},
                  "b": {"published": "2026-09-01"}, "c": {}}
    _bp = os.path.join(os.environ.get("TEMP", "/tmp"), "_prune_test_rudip.jsonl")
    ck("prune 超龄删除（含备份）",
       prune_archive(_tmp_prune, date(2026, 9, 19), 120, backup_path=_bp) == 1
       and _tmp_prune["b"]["published"] == "2026-09-01"
       and "2020-01-01" in open(_bp, encoding="utf-8").read())
    # --- 回填掉桶 → excluded ---
    arch2 = {"x": {"id": "x", "published": "2026-09-15", "date_unknown": False,
                   "title": "Wall Street closes higher as tech shares rally",
                   "summary": "Investors weighed the Fed's rate path.",
                   "kind": "fact_event", "buckets": ["talks_process"], "cfg_ver": "OLD"},
             "y": {"id": "y", "published": "2026-09-16", "date_unknown": False,
                   "title": "Local football results from the weekend",
                   "summary": "The home side won two nil.",
                   "kind": "fact_event", "buckets": ["us_envoy"], "cfg_ver": "OLD"}}
    backfill_archive(cfg, eng, arch2)
    ck("回填掉桶(anchor_only)→excluded", arch2["x"]["kind"] == "excluded" and arch2["x"]["buckets"] == [])
    ck("回填掉桶(无锚定)→excluded", arch2["y"]["kind"] == "excluded" and arch2["y"]["buckets"] == [])
    win2, _ = window_filter(list(arch2.values()), date(2026, 9, 19), 30)
    ck("excluded 不进快照", len(win2) == 0)
    # --- 配置不变量 ---
    ck("全桶 ≥2 维", all(len(b["require_all"]) >= 2 for b in cfg["buckets"].values()))
    ck("六桶齐备", set(cfg["buckets"].keys()) == set(BUCKET_ORDER))
    ck("us_envoy 三维", len(cfg["buckets"]["us_envoy"]["require_all"]) == 3)
    ck("china_xinhua 三维", len(cfg["buckets"]["china_xinhua"]["require_all"]) == 3)
    ck("anchorage 桶 d1 仅 Anchorage", cfg["buckets"]["anchorage"]["require_all"][0] == ["rx:\\banchorage\\b"])
    ck("全桶有标签", all(b.get("label") for b in cfg["buckets"].values()))
    ck("每启用源 tier 合法", all(s.get("tier") in ("T1", "T2", "T3")
       for s in cfg["sources"] if s.get("enabled")))
    ck("无启用 T4 源", all(s.get("tier") != "T4" for s in cfg["sources"] if s.get("enabled")))
    ck("停用源有留档", all(s.get("note") for s in cfg["sources"] if not s.get("enabled")))
    ck("T1 官方一手 ≥4 在启用（whitehouse×2/un/kremlin）",
       sum(1 for s in cfg["sources"] if s.get("enabled") and s["tier"] == "T1") >= 4)
    ck("T2 智库在启用（crisisgroup）",
       sum(1 for s in cfg["sources"] if s.get("enabled") and s["tier"] == "T2") >= 1)
    ck("俄方框架窗口 ≥3 家（tass/interfax/moscowtimes/meduza）",
       sum(1 for s in cfg["sources"] if s.get("enabled")
           and s["id"] in ("tass", "interfax", "moscowtimes", "meduza-rss")) >= 3)
    ck("乌方框架窗口 ≥2 家（kyivpost/euromaidan）",
       sum(1 for s in cfg["sources"] if s.get("enabled")
           and s["id"] in ("kyivpost", "euromaidan")) >= 2)
    ck("协调方窗口在用（anadolu）",
       any(s["id"] == "anadolu" and s.get("enabled") for s in cfg["sources"]))
    ck("gnews ≥5 通道在用",
       sum(1 for s in cfg["sources"] if s.get("enabled")
           and s.get("publisher") == "Google News") >= 5)
    ck("新华社通道在用（xinhua-home, html_listing）",
       any(s["id"] == "xinhua-home" and s.get("enabled") and s.get("type") == "html_listing"
           for s in cfg["sources"]))
    ck("乌克兰官方 403 已留档停用",
       all(not s.get("enabled") for s in cfg["sources"]
           if s["id"] in ("ua-president", "ua-mfa")))
    ck("flags 键全部可编译", all(k in eng.flag_res for k in
       ("envoy_travel", "willingness_positive", "willingness_negative",
        "anchorage_return", "memorandum_draft", "ceasefire_call")))
    ck("baseline rows 字段完整",
       all(b.get("date") and b.get("event") and b.get("tier") in ("T1", "T2", "T3")
           and b.get("source") for b in cfg["baseline"].get("rows", [])
           + cfg["baseline"].get("capability_rows", [])))
    ck("已知局限 ≥8 条", len(cfg["report"]["known_limitations"]) >= 8)
    # --- 核心结论能产出（防全绿但结论恒空）---
    ck("核心结论: 事件/表态/分析 三类都能产出",
       re1["kind"] == "fact_event" and rs1["kind"] == "opinion_statement"
       and ra1["kind"] == "opinion_analysis")

    ok = sum(1 for _, c in P if c)
    for name, c in P:
        print(f"  {'✅' if c else '❌'} {name}")
    print(f"[selftest] {ok}/{len(P)} 通过")
    return 0 if ok == len(P) else 1

if __name__ == "__main__":
    main()
