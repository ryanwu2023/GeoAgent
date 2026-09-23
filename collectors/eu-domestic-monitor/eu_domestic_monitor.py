#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
eu_domestic_monitor.py — 欧盟内政×俄乌战争监测
（法国2027总统大选预备/勒庞国民联盟民调 / 意大利大选与执政联盟 /
 各国对乌军事援助态度 / 各国对俄政策立场 / 欧盟机构运作与成员国博弈 /
 中国视角（新华社·中欧关系））
骨架承继 russia-domestic-monitor：配置驱动源 + 主题锚定（并入各桶全部词表）+
实体归属 + tier 分级 + 事实/观点分类 + 立场词表（否定翻转；语义=援助收缩/软化
vs 援助扩张/强化）+ flags（民调/竞选筹备/援助承诺/援助限制/对俄接触）+
意向守卫 + html_listing 解析器（新华社英文首页）+
**法语/意大利语词形族（英语 SUF + rx: 精确正则）** +
累积档回填（cfg_ver 含代码哈希）+ 离线自检。

用法:
  python eu_domestic_monitor.py                # 抓取+渲染
  python eu_domestic_monitor.py --selftest     # 离线自检（不联网）
  python eu_domestic_monitor.py --as-of 2026-09-21 --out-dir output/_xday-verify
  python eu_domestic_monitor.py --render-only  # 仅从累积档重渲染
  python eu_domestic_monitor.py --from-file corpus.json  # 离线退路
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
           "Accept-Language": "en-US,en;q=0.9,fr;q=0.8,it;q=0.7", "Accept-Encoding": "gzip, deflate",
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
    - `rx:` 前缀 = 原生正则（法/意语屈折复杂场景的精确出口）
    - 含西里尔字母 = 前缀词干（本项目预留，法意语不用）
    - 其余 = 英语法语通用文法：词尾 e/y 还原 + SUF 后缀族
      （sondage→sondages / élection→élections / candidate→candidates）"""
    if term.startswith("rx:"):
        return re.compile(term[3:], re.I)
    if re.search(r"[а-яё]", term, re.I):
        words = [re.escape(w) + r"\w*" for w in term.split()]
        return re.compile(r"\b" + r"\s+".join(words), re.I)
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

# 锚定基词（欧盟/成员国国别与机构层；各桶词表运行时并入）
# 法/意官方机构词（élysée/governo/quirinale）天然内政，单独出现即可过锚定；
# 英语泛词（government/minister 等）不入锚定，靠桶二维门控。
ANCHOR_BASE = [
    "rx:\\beurop\\w*", "european union", "rx:\\beu\\b",
    "france", "french", "rx:\\bfranç\\w*", "rx:\\bparis\\b",
    "macron", "le pen", "rx:\\ble pen\\b", "bardella", "rx:\\brassemblement\\b",
    "rx:\\bélysée\\b", "elysee",
    "italy", "italian", "rx:\\bitali\\w*", "rx:\\broma\\b", "rx:\\brome\\b",
    "meloni", "salvini", "rx:\\bconte\\b", "rx:\\bquirinale\\b", "rx:\\bgoverno\\b",
    "brussels", "rx:\\bbruxelles\\b", "rx:\\bberlaymont\\b",
    "von der leyen", "rx:\\bkallas\\b",
    "rx:\\bhungar\\w*", "rx:\\bmagyar\\w*", "orbán", "orban",
    "rx:\\bpoland\\b", "rx:\\bwarsaw\\b", "rx:\\bvarsovie\\b",
    "germany", "german", "rx:\\bberlin\\b", "rx:\\bmerz\\b",
    "spain", "spanish", "rx:\\bmadrid\\b", "rx:\\bsánchez\\b", "sanchez"
]

class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        # 锚定 = 欧盟/成员国基词 + 各桶全部词表（法国报道可能只有
        # 「Élysée 宣布」「il governo」这类无国名主语，锚定须并入桶词）
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
        # 完成态事件动词：官方发布/选举/批准/经济动作（英+法分词+意分词）
        self.event_re = re.compile(
            r"\b(?:met|meets?|received|visit(?:ed|s|ing)?|held|hosts?|hosted|"
            r"announced|confirmed|approved|adopted|ratified|signed|signed into|"
            r"voted|elected|appointed|published|reported|released|disclosed|"
            r"issued|imposed|lifted|eased|extended|resumed|halted|banned|"
            r"raised|lowered|hiked|cut(?:\s+\w+)?\s+rate|increased|reduced|"
            r"rose|fell|dropped|surged|plunged|climbed|slid|grew|declined|"
            r"worsened|improved|revised|allocated|disbursed|submitted|"
            r"registered|opened|closed|counted"
            r"|annoncé|déclaré|adopté|promis|voté|approuvé|nommé|publié|"
            r"présenté|proposé|estimé|salué|condamné|réaffirmé|lancé|ouvert|"
            r"signé|entériné|confirmé|révélé|examiné|réuni"
            r"|annunciato|approvato|adottato|votato|dichiarato|pubblicato|"
            r"firmato|aperto|confermato|rivelato"
            r")\b", re.I)
        self.quote_re = re.compile(
            r"\b(?:said|told|stated|declared|argued|warned|accused|vowed|promised|"
            r"insisted|urged|calls?|called|denied|acknowledged|claimed|stressed|"
            r"writes|noted|added|believes|according to"
            r"|selon|estime|affirme|juge|souligne|considère|rapporte|déclare"
            r"|secondo|dichiara|afferma|sottolinea"
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
            r"urged?|appeal\w*|about\s+to|intend\w*"
            r"|va\b|veut\w*|souhaite\w*|prévoit\w*|envisage\w*|compte\b|"
            r"devrait|pourrait|pourra|voudra"
            r"|vuole|prevede|intende|potrebbe|dovrebbe|deve\b|punta"
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
        """援助动能（收缩/软化 vs 扩张/强化两族，否定翻转；只看方向不看强度）"""
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
            kind = "fact_event"           # 完成态事件（公布/批准/选举/上涨/下跌…）
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

CHINA_SOURCE_IDS = {"xinhua-home", "gn-xinhua", "gn-china-eu", "chinanews-scroll"}

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
STANCE_LABEL = {"escalation_signal": "收缩/软化信号", "deescalation_signal": "扩张/强化信号",
                "mixed_signal": "双向混存", "unclassified": "未分类"}
FLAG_LABEL = {"polling_data": "民调数据", "campaign_prep": "竞选筹备",
              "aid_commitment": "援助承诺/批准", "aid_restriction": "援助限制/反对",
              "russia_engagement": "对俄接触/制裁松动"}
BUCKET_ORDER = ["france_2027", "italy_election", "ua_aid_attitude",
                "russia_policy", "eu_politics", "china_xinhua"]

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
    W.append(f"# 欧盟内政×俄乌战争监测日报（{dt}）")
    W.append("")
    W.append("## 0. 阅读须知（先读，再读结论）")
    W.append("")
    W.append("- **事实/观点三分类**：每条记录标注 `kind`：")
    W.append("  - `fact_event` 完成态事件=**事实**（公布/批准/选举/民调发布等已发生的动作；")
    W.append("    注意『完成』指报道的动作为完成时态，**数字仍是单一来源口径**）；")
    W.append("  - `opinion_statement` 表态=**『他说了X』是事实，X 本身是观点/立场**，附人物归属；")
    W.append("  - `opinion_analysis` 分析解读=**观点**。")
    W.append("- **民调口径**：投票意向≠实际投票；2022 两轮民调普遍低估 RN；机构间方法")
    W.append("  差异大（Ifop/Elabe/BVA/Harris/Cluster17），**禁横向平均**；`polling_data` 逐条标注。")
    W.append("- **传导链条**：选举→政府组成→援助政策是间接链条。法国外交与军队由**总统**掌握，")
    W.append("  意大利援助由**内阁**决定；本监测记录报道中的立场表态，**不预测选举结果**，")
    W.append("  不把民调领先外推为『欧盟援助能力受限』的实现。")
    W.append("- **EU 一致性门槛**：共同外交（含对俄制裁）需 27 国一致（unanimity），")
    W.append("  单一成员国即可阻挠；欧盟层面政策与成员国双边立场分开读。")
    W.append("- **立场动能词表是启发式**：扩张/强化 vs 收缩/软化，否定翻转，只看方向不看强度；")
    W.append("  支持/反对常以引语转述出现，实体归属经 entities 标注，混排引语可能错位。")
    W.append(f"- 本轮配置指纹 `cfg_ver={stats['cfg_ver'][:12]}…`；快照内唯一（闸门断言）。")
    W.append("")
    W.append(f"## 1. 快照头部（{dt}）")
    W.append("")
    W.append(f"- 抓取源 {stats['n_enabled']} 个（T1 {stats['n_t1']} / T2 {stats['n_t2']} / 其余 T3；停用留档 {stats['n_disabled']}）")
    W.append(f"- 本次新增 **{stats['n_new']}** 条；当前快照（窗口 {cfg['window_days']} 天）**{len(records)}** 条")
    W.append(f"- 窗口内信号桶计数：`" + "、".join(
        f"{cfg['buckets'][b]['label']} {n}" for b, n in by_bucket.most_common()) + "`")
    W.append(f"- 事实/观点：`" + "、".join(f"{KIND_LABEL[k]} {n}" for k, n in by_kind.most_common() if k in KIND_LABEL) + "`")
    W.append(f"- 扩张/收缩分布（启发式）：`" + "、".join(f"{STANCE_LABEL[s]} {n}" for s, n in by_stance.most_common()) + "`")
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
    sec_map = [("france_2027", "3", "法国2027总统大选预备（勒庞/国民联盟民调）"),
               ("italy_election", "4", "意大利大选（2027-12 前）与执政联盟"),
               ("ua_aid_attitude", "5", "各国对乌军事援助态度"),
               ("russia_policy", "6", "各国对俄政策与战争立场"),
               ("eu_politics", "7", "欧盟机构运作与成员国博弈"),
               ("china_xinhua", "8", "中国视角（新华社/中欧关系）")]
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
        W.append(f"\n共 {len(undated)} 条；纪元零值已按『日期未知』处理；delors 源的活动日期（含未来）也落本节。")
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
    W.append("- **T1** 官方一手（法国总统府 élysée.fr/feed、意大利 governo.it/rss.xml、")
    W.append("  欧洲议会 europarl top-stories；欧委会 presscorner=JS 壳、欧盟理事会=403 已留档——")
    W.append("  理事会/欧委会一手先经媒体转述（T3），出口变更后复测）；")
    W.append("  **T2** 机构分析（Jacques Delors Institute；ECFR/Bruegel/CEPS/Montaigne 本出口")
    W.append("  不可达已留档）；**T3** 媒体转述（法国：Le Figaro 政治/France24 FR/franceinfo/")
    W.append("  BFMTV×2；意大利：ANSA×2/Il Sole/Repubblica；泛欧：EUobserver；匈牙利窗口：")
    W.append("  Telex EN；中文窗口：新华社首页/中新社；聚合层：Google News 6 通道）；")
    W.append("  **T4** 自媒体——不采用。")
    W.append("- 事实（完成态事件）≠ 表态（『他说了X』是事实，X 是立场）≠ 启发式判断（扩张/收缩词表）。")
    W.append("- 法/意语匹配用词形族启发式（英语 SUF + rx: 精确正则），屈折覆盖为启发式。")
    W.append("")
    return "\n".join(W)

# ---------------------------------------------------------------- main
def write_outputs(cfg, records, undated, audit, stats, as_of, outdir, verify_note):
    dd = os.path.join(outdir, as_of.isoformat())
    os.makedirs(dd, exist_ok=True)
    rep = render_report(cfg, records, undated, audit, stats, as_of, verify_note, outdir)
    p_rep = os.path.join(dd, f"eudom-{as_of.isoformat()}.md")
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

    # --- 法/意语词形族 / rx: 正则 ---
    ck("词形族 sondage→sondages", term_re("sondage").search("les sondages"))
    ck("词形族 élection→élections", term_re("élection").search("élections législatives"))
    ck("词形族 présidentielle→présidentielles", term_re("présidentielle").search("présidentielles"))
    ck("词形族 candidate→candidates", term_re("candidate").search("candidates"))
    ck("rx: le pen 精确", term_re("rx:\\ble pen\\b").search("Le Pen") and not term_re("rx:\\ble pen\\b").search("Netherlands"))
    ck("rx: blezion 意语复数", term_re("rx:\\belezion\\w*").search("elezioni politiche"))
    ck("rx: bsondagg 意语复数", term_re("rx:\\bsondagg\\w*").search("nei sondaggi"))
    ck("英语 SUF 族不回归", term_re("election").search("elections") and term_re("weapon").search("weapons"))
    # --- 日期 ---
    ck("RFC822 全称", parse_any_date("Sat, 13 Sep 2026 09:00:00 +0000") == ("2026-09-13", False))
    ck("两位数年", parse_any_date("Wed, 29 Jul 26 12:37:14 +0200") == ("2026-07-29", False))
    ck("纪元零值=未知", parse_any_date("Thu, 01 Jan 1970 00:00:00 +0000") == (None, True))
    ck("未来日期拒绝（delors 活动日）", parse_any_date("Tue, 08 Dec 2026 00:00:00 +0000") == (None, False))
    ck("ISO", parse_any_date("2026-09-12T10:00:00Z")[0] == "2026-09-12")
    ck("纯 ISO（html_listing 直给）", parse_any_date("2026-09-19") == ("2026-09-19", False))
    # --- html_listing 解析器（新华社首页结构）---
    fake_html = ('<div class="img"><a href="https://english.news.cn/20260919/abc123def/c.html">'
                 '<img src="x.jpg"></a></div>'
                 '<div class="tit"><a href=\'https://english.news.cn/20260919/abc123def/c.html\'>'
                 'EU leaders meet on Ukraine aid</a></div>'
                 '<a href="https://english.news.cn/20260919/def456abc/c.html">China, France deepen ties</a>'
                 '<a href="https://english.news.cn/20260919/ghi789jkl/c.html">About Us</a>')
    fake_src = {"link_pattern": "<a[^>]+href=[\"'](https://english\\.news\\.cn/20\\d{6}/[0-9a-f]+/c\\.html)[\"'][^>]*>(.*?)</a>",
                "date_re": "/(20\\d{6})/"}
    li = parse_html_listing(fake_html, fake_src)
    ck("html_listing 去重 img/标题双锚", len(li) == 2)
    ck("html_listing 标题取文本锚", li and li[0]["title"] == "EU leaders meet on Ukraine aid")
    ck("html_listing URL 抽日期", li and li[0]["pub"] == "2026-09-19")
    # --- 桶命中：英语 ---
    r1 = eng.classify("Poll of polls: Le Pen's National Rally still ahead for 2027 presidential election",
                      "The Ifop survey shows voting intention for the 2027 presidential race.")
    ck("法国大选→france_2027", r1 and "france_2027" in r1["buckets"])
    r2 = eng.classify("Meloni's coalition stays ahead in polls ahead of 2027 election",
                      "The survey shows the Brothers of Italy leading voting intentions.")
    ck("意大利大选→italy_election", r2 and "italy_election" in r2["buckets"])
    r3 = eng.classify("EU approves new military aid package for Ukraine",
                      "The package includes weapons and ammunition deliveries to Kyiv.")
    ck("对乌援助→ua_aid_attitude", r3 and "ua_aid_attitude" in r3["buckets"])
    r4 = eng.classify("France's Le Pen calls for dialogue with Russia, questions sanctions",
                      "The National Rally leader criticized the EU policy toward Moscow.")
    ck("对俄立场→russia_policy", r4 and "russia_policy" in r4["buckets"])
    r5 = eng.classify("European Council summit: Hungary threatens veto over Ukraine budget",
                      "The member state blocked unanimity at the Brussels summit.")
    ck("欧盟机构→eu_politics", r5 and "eu_politics" in r5["buckets"])
    r6 = eng.classify("China reiterates position on Ukraine crisis, Wang Yi says",
                      "Beijing calls for peace talks, Xinhua reported.")
    ck("中国视角→china_xinhua", r6 and "china_xinhua" in r6["buckets"])
    # --- 桶命中：法语 ---
    rf1 = eng.classify("Sondage présidentiel : Le Pen loin devant pour 2027",
                       "L'institut Ifop publie une enquête sur l'intention de vote pour la présidentielle.")
    ck("法语民调→france_2027", rf1 and "france_2027" in rf1["buckets"])
    rf2 = eng.classify("L'Élysée annonce une réunion sur l'aide militaire à l'Ukraine",
                       "Le président a annoncé un soutien renforcé aux forces ukrainiennes.")
    ck("法语援助→ua_aid_attitude", rf2 and "ua_aid_attitude" in rf2["buckets"])
    rf3 = eng.classify("Le Pen plaide pour une normalisation avec la Russie",
                       "La leader du Rassemblement national veut rouvrir le dialogue avec Moscou.")
    ck("法语对俄→russia_policy", rf3 and "russia_policy" in rf3["buckets"])
    # --- 桶命中：意大利语 ---
    ri1 = eng.classify("Meloni al vertice: il governo conferma il sostegno all'Ucraina",
                       "La coalizione di Fratelli d'Italia resta unita sulle armi a Kiev.")
    ck("意语援助→ua_aid_attitude", ri1 and "ua_aid_attitude" in ri1["buckets"])
    ri2 = eng.classify("Salvini riapre al dialogo con la Russia sulle sanzioni",
                       "Il leader della Lega parla di negoziazione con Mosca.")
    ck("意语对俄→russia_policy", ri2 and "russia_policy" in ri2["buckets"])
    # --- 真实语料负例（宽词污染回归用例）---
    rn1 = eng.classify("Regional election in Spain scheduled for October",
                       "The Spanish interior ministry approved the campaign calendar.")
    ck("负例: 西班牙选举不进 france_2027/italy_election",
       rn1 is None or not ({"france_2027", "italy_election"} & set(rn1.get("buckets") or [])))
    rn2 = eng.classify("Rugby match between France and Italy ends in a draw",
                       "The Six Nations game finished level after extra time.")
    ck("负例: 橄榄球赛不进任何桶", rn2 is None or rn2.get("anchor_only") is True or not rn2["buckets"])
    rn3 = eng.classify("Le Pen criticizes EU climate policy at party congress",
                       "The National Rally leader denounced the green deal rules.")
    ck("负例: 无选举/民调语境的勒庞新闻不进 france_2027",
       rn3 is None or "france_2027" not in (rn3.get("buckets") or []))
    rn4 = eng.classify("German chancellor defends defense spending at NATO meeting",
                       "Berlin raised its budget target, Merz told reporters.")
    ck("负例: 德国军费不进 eu_politics/ua_aid_attitude",
       rn4 is None or not ({"eu_politics", "ua_aid_attitude"} & set(rn4.get("buckets") or [])))
    rn5 = eng.classify("Morning recap", "EU approves aid; Le Pen leads poll; Meloni wins vote.")
    ck("Morning recap 不进任何桶", rn5 is None or rn5.get("anchor_only") or not rn5["buckets"])
    r15 = eng.classify("Quarterly earnings beat expectations", "Tech shares rallied.")
    ck("无锚定=不收", r15 is None)
    # --- 真实语料回归（2026-09-19 首轮人工核对后的负例）---
    rn6 = eng.classify("XIX Giornata Nazionale SLA, Palazzo Chigi si illumina di verde",
                       "La Presidenza del Consiglio dei Ministri ha aderito all'iniziativa di AISLA.")
    ck("负例: 意政府仪式新闻（SLA日）不进 italy_election",
       rn6 is None or "italy_election" not in (rn6.get("buckets") or []))
    rn7 = eng.classify("Il Presidente Meloni alle celebrazioni per il 500° anniversario della Fabbrica Beretta",
                       "Meloni ha partecipato alle celebrazioni a Gardone Val Trompia.")
    ck("负例: Beretta 周年不进 italy_election/eu_politics",
       rn7 is None or not ({"italy_election", "eu_politics"} & set(rn7.get("buckets") or [])))
    rn8 = eng.classify("SNA, nomina commissione esaminatrice procedura selettiva 30 posti cat. B F3",
                       "Nomina Commissione esaminatrice relativa all'avviso pubblico per l'assunzione di personale.")
    ck("负例: 公务招聘公告不进 eu_politics",
       rn8 is None or "eu_politics" not in (rn8.get("buckets") or []))
    rn9 = eng.classify("Trump annuncia l'accordo sulla Groenlandia: 'Controlleremo la sua sicurezza'",
                       "L'intesa impedisce a Cina, Russia e ad altri Paesi non Nato di creare basi in Groenlandia.")
    ck("负例: 格陵兰协定（无欧洲语境）不进 china_xinhua",
       rn9 is None or "china_xinhua" not in (rn9.get("buckets") or []))
    # --- flags ---
    rf4 = eng.classify("Ifop poll shows National Rally at 34% for 2027",
                       "The survey measured voting intention among 1,500 respondents.")
    ck("民调 polling_data", rf4 and rf4["flags"].get("polling_data") is True)
    rf5 = eng.classify("Bardella announces his candidacy for the presidential primary",
                       "The RN president launched his campaign in Marseille.")
    ck("竞选筹备 campaign_prep", rf5 and rf5["flags"].get("campaign_prep") is True)
    rf6 = eng.classify("EU approves a new aid package for Kyiv",
                       "The commitment covers artillery shells and air defense.")
    ck("援助承诺 aid_commitment", rf6 and rf6["flags"].get("aid_commitment") is True)
    rf7 = eng.classify("Le Pen says she opposes sending more weapons to Ukraine",
                       "The RN leader questioned the military aid program, calling it limitless.")
    ck("援助限制 aid_restriction", rf7 and rf7["flags"].get("aid_restriction") is True)
    rf8 = eng.classify("Macron floats easing sanctions after a peace deal",
                       "Dialogue with Moscow could resume, the president said.")
    ck("对俄接触 russia_engagement", rf8 and rf8["flags"].get("russia_engagement") is True)
    # --- kind 顺序 ---
    re1 = eng.classify("L'Élysée a annoncé le versement d'une aide militaire à l'Ukraine",
                       "Le palais a confirmé son soutien aux forces ukrainiennes.")
    ck("法语完成态发布→fact_event", re1 and re1["kind"] == "fact_event"
       and "ua_aid_attitude" in re1["buckets"])
    rs1 = eng.classify("Le Rassemblement national reste en tête des sondages, selon un analyste",
                       "La trajectoire pour la présidentielle ne laisse pas d'espace aux rivaux.")
    ck("法语引语 selon→opinion_statement", rs1 and rs1["kind"] == "opinion_statement")
    ra1 = eng.classify("Il centrosinistra resta indietro nei sondaggi",
                       "La coalizione non riesce a decollare.")
    ck("意语分析→opinion_analysis", ra1 and ra1["kind"] == "opinion_analysis")
    # --- 意向语态守卫 ---
    ri3 = eng.classify("EU leaders will meet on Thursday to discuss aid",
                       "The summit agenda covers weapons support for Kyiv.")
    ck("will meet ≠ 完成态", ri3 is None or ri3["kind"] != "fact_event")
    ri4 = eng.classify("Meloni expected to be confirmed as candidate before the election",
                       "The decision is expected next week, party officials said.")
    ck("expected to ≠ 完成态", ri4 is None or ri4["kind"] != "fact_event")
    ri5 = eng.classify("Il governo ha approvato il sostegno militare all'Ucraina",
                       "Il consiglio dei ministri ha adottato il decreto.")
    ck("真实完成态不被意向守卫误杀", ri5 and ri5["kind"] == "fact_event"
       and "ua_aid_attitude" in ri5["buckets"])
    # --- 立场/否定 ---
    ck("立场收缩 oppose", eng.stance_of("Le Pen opposes sending more weapons to Ukraine.") == "escalation_signal")
    ck("立场扩张 pledge", eng.stance_of("Macron pledged continued support for Ukraine.") == "deescalation_signal")
    ck("否定翻转 no plans to lift sanctions",
       eng.stance_of("Officials see no plans to lift sanctions on Moscow.") == "deescalation_signal")
    ck("双向混存", eng.stance_of("She opposes tanks but voiced support for Ukraine.") == "mixed_signal")
    # --- 锚定 ---
    ck("锚定：英语国别词", eng.anchor_hit("France and Italy agreed") and eng.anchor_hit("EU leaders"))
    ck("锚定：法/意机构词", eng.anchor_hit("L'Élysée a confirmé") and eng.anchor_hit("il governo ha approvato"))
    ck("锚定：人物词", eng.anchor_hit("Le Pen said") and eng.anchor_hit("Meloni warned"))
    ck("锚定：无国别=不收", not eng.anchor_hit("Quarterly earnings beat expectations"))
    # --- gnews 后缀折叠/链接归一 ---
    ck("gnews 后缀折叠", title_key("Le Pen en tête - Le Figaro") ==
       title_key("Le Pen en tête - BFMTV"))
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
    _bp = os.path.join(os.environ.get("TEMP", "/tmp"), "_prune_test_eudom.jsonl")
    ck("prune 超龄删除（含备份）",
       prune_archive(_tmp_prune, date(2026, 9, 19), 120, backup_path=_bp) == 1
       and _tmp_prune["b"]["published"] == "2026-09-01"
       and "2020-01-01" in open(_bp, encoding="utf-8").read())
    # --- 回填掉桶 → excluded ---
    arch2 = {"x": {"id": "x", "published": "2026-09-15", "date_unknown": False,
                   "title": "Chess tournament results in Spain",
                   "summary": "The Madrid Open concluded with an upset.",
                   "kind": "fact_event", "buckets": ["france_2027"], "cfg_ver": "OLD"},
             "y": {"id": "y", "published": "2026-09-16", "date_unknown": False,
                   "title": "Weather report from the Alps",
                   "summary": "Rain expected across the region this weekend.",
                   "kind": "fact_event", "buckets": ["eu_politics"], "cfg_ver": "OLD"}}
    backfill_archive(cfg, eng, arch2)
    ck("回填掉桶(anchor_only)→excluded", arch2["x"]["kind"] == "excluded" and arch2["x"]["buckets"] == [])
    ck("回填掉桶(无锚定)→excluded", arch2["y"]["kind"] == "excluded" and arch2["y"]["buckets"] == [])
    win2, _ = window_filter(list(arch2.values()), date(2026, 9, 19), 30)
    ck("excluded 不进快照", len(win2) == 0)
    # --- 配置不变量 ---
    ck("全桶 ≥2 维", all(len(b["require_all"]) >= 2 for b in cfg["buckets"].values()))
    ck("全桶六桶齐备", set(cfg["buckets"].keys()) == set(BUCKET_ORDER))
    ck("全桶有标签", all(b.get("label") for b in cfg["buckets"].values()))
    ck("每启用源 tier 合法", all(s.get("tier") in ("T1", "T2", "T3")
       for s in cfg["sources"] if s.get("enabled")))
    ck("无启用 T4 源", all(s.get("tier") != "T4" for s in cfg["sources"] if s.get("enabled")))
    ck("停用源有留档", all(s.get("note") for s in cfg["sources"] if not s.get("enabled")))
    ck("T1 官方一手 ≥3 在启用（elysee/governo/europarl）",
       sum(1 for s in cfg["sources"] if s.get("enabled") and s["tier"] == "T1") >= 3)
    ck("T2 智库在启用（delors）",
       sum(1 for s in cfg["sources"] if s.get("enabled") and s["tier"] == "T2") >= 1)
    ck("法语主力源 ≥4 家（lefigaro/france24/franceinfo/bfmtv）",
       sum(1 for s in cfg["sources"] if s.get("enabled")
           and "(FR)" in s.get("publisher", "")) >= 4)
    ck("意语主力源 ≥3 家（ansa/ilsole/repubblica）",
       sum(1 for s in cfg["sources"] if s.get("enabled")
           and "(IT)" in s.get("publisher", "")) >= 3)
    ck("gnews ≥5 通道在用",
       sum(1 for s in cfg["sources"] if s.get("enabled")
           and s.get("publisher") == "Google News") >= 5)
    ck("新华社通道在用（xinhua-home, html_listing）",
       any(s["id"] == "xinhua-home" and s.get("enabled") and s.get("type") == "html_listing"
           for s in cfg["sources"]))
    ck("Corriere 停更假活 feed 已留档停用",
       any(s["id"] == "corriere" and not s.get("enabled") and "停更" in (s.get("note") or "")
           for s in cfg["sources"]))
    ck("flags 键全部可编译", all(k in eng.flag_res for k in
       ("polling_data", "campaign_prep", "aid_commitment", "aid_restriction", "russia_engagement")))
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
