#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
opinion_monitor.py — 美伊战争·美国民意监测（民调 / 意见领袖 / 国会 / 街头）
骨架承继 readiness-monitor（表态类）：配置驱动源 + 主题锚定 + 实体归属 +
tier 分级 + 事实/观点四分类 + 累积档回填 + 离线自检。

用法:
  python opinion_monitor.py                # 抓取+渲染
  python mideast_monitor.py --selftest     # 离线自检（不联网）
  python opinion_monitor.py --as-of 2026-09-20 --out-dir output/_xday-verify
  python opinion_monitor.py --render-only  # 仅从累积档重渲染
  python opinion_monitor.py --from-file corpus.json  # 离线退路
"""
import argparse, hashlib, html, json, os, re, sys, time, gzip, zlib
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict
from datetime import datetime, timezone, timedelta, date
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE, "config.json")
SUF = r"(?:s|es|ed|ing|d|er|ers|ation|ions?|ism)?"
EPOCH = date(1970, 1, 1)
MIN_YEAR, MAX_YEAR_SLACK = 2000, 1

# ---------------------------------------------------------------- utilities
def load_cfg():
    with open(CFG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def now_utc():
    return datetime.now(timezone.utc)

def run_date_str(as_of=None):
    return (as_of or date.today()).isoformat()

def sha(s):
    return hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()[:16]

def strip_tags(s):
    s = html.unescape(s or "")          # 数字实体 &#8230; / 命名实体一次解码
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)                 # 双重编码的实体再解一轮
    s = re.sub(r"<[^>]+>", " ", s)       # 反转义后再删一轮（嵌套实体标签）
    return re.sub(r"\s+", " ", s).strip()

def clean_text(s, maxlen):
    return strip_tags(s)[:maxlen]

def norm_link(u):
    u = (u or "").split("#")[0]
    u = re.sub(r"([?&])(utm_[^&]*|gclid|fbclid|ocid)=[^&]*", r"\1", u).rstrip("?&")
    return u

def title_key(title):
    """折叠 gnews『 - 媒体名』后缀后取指纹，防同稿多源虚增条数"""
    t = strip_tags(title or "")
    t = re.sub(r"\s+-\s+[^-|]{2,42}$", "", t)
    return re.sub(r"[^a-z0-9]+", "", t.lower())[:80]

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
           "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, deflate",
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
MONTH_ALIAS = {
    "enero":"jan","febrero":"feb","marzo":"mar","abril":"apr","mayo":"may","junio":"jun",
    "julio":"jul","agosto":"aug","septiembre":"sep","setiembre":"sep","octubre":"oct",
    "noviembre":"nov","diciembre":"dec",
    "janvier":"jan","fevrier":"feb","mars":"mar","avril":"apr","mai":"may","juin":"jun",
    "juillet":"jul","aout":"aug","septembre":"sep","octobre":"oct","novembre":"nov",
    "decembre":"dec","décembre":"dec","février":"feb","août":"aug",
    "januar":"jan","februar":"feb","marz":"mar","märz":"mar","mai":"may","juni":"jun",
    "juli":"jul","august":"aug","oktober":"oct","dezember":"dec",
    "gennaio":"jan","febbraio":"feb","marzo":"mar","aprile":"apr","maggio":"may",
    "giugno":"jun","luglio":"jul","agosto":"aug","settembre":"sep","ottobre":"oct",
    "novembre":"nov","dicembre":"dec",
    "janeiro":"jan","fevereiro":"feb","marco":"mar","março":"mar","abril":"apr",
    "junho":"jun","julho":"jul","setembro":"sep","outubro":"oct","novembro":"nov",
    "dezembro":"dec",
}
MONTH_NUM = {m: i + 1 for i, m in enumerate(
    ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"])}

def normalize_datestr(s):
    low = (s or "").lower()
    for k, v in MONTH_ALIAS.items():
        if k in low:
            low = re.sub(re.escape(k), v, low)
    return low

def _year_ok(y):
    return MIN_YEAR <= y <= date.today().year + MAX_YEAR_SLACK

def parse_any_date(s):
    """多语言月份别名化 → 多格式抽取 → 两位数年展开 → 合理性兜底。
    返回 (iso_date|None, date_unknown:bool)。纪元零值=日期未知。"""
    if not s:
        return None, False
    t = normalize_datestr(s)
    today = date.today()
    y = m = d = None
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
        y = 2000 + y                      # 两位数年必须先展开（%Y 宽松陷阱）
    try:
        dt = date(y, m, d)
    except ValueError:
        return None, False
    if dt == EPOCH:                       # 纪元零值=未知，必须先于年份兜底（1970<2000 会被抢拒）
        return None, True
    if not _year_ok(y):                   # 解析成功但年份离谱 → 判失败
        return None, False
    if dt > today + timedelta(days=1):
        return None, False
    return dt.isoformat(), False

# ---------------------------------------------------------------- lexicon
def term_re(term):
    words = term.split()
    parts = []
    for i, w in enumerate(words):
        # -e 结尾去 e 加后缀（vot+ing）；-y 结尾 y/ie 可选（justify/justifies/justified）
        if w.endswith("e"):
            stem = re.escape(w[:-1]) + "(?:e)?"
        elif w.endswith("y"):
            stem = re.escape(w[:-1]) + "(?:y|ie)?"
        else:
            stem = re.escape(w)
        parts.append(stem + SUF if i == len(words) - 1 else stem)
    return re.compile(r"\b" + r"\s+".join(parts) + r"\b", re.I)

def alias_re(alias):
    return re.compile(r"\b" + re.escape(alias) + r"\b", re.I)

class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.anchor = term_re("israel")  # 占位，下面重编
        alts = ["iran(?:ian)?", "tehran", "houthi(?:s)?", r"ansar\s+allah",
                "saudi|riyadh", "iraq(?:i)?", "baghdad", "yemen(?:i)?", "sanaa",
                "hormuz", r"red\s+sea", r"bab\s+el?-?mandeb", "mandeb", "mandab",
                r"\bgulf\b", "kuwait(?:i)?", "bahrain(?:i)?", "qatar(?:i)?", "doha",
                "uae|emirat(?:es|i)?", "oman(?:i)?", "muscat", "jordan(?:ian)?",
                "amman", "turkey|turkiy?e|ankara", "pakistan(?:i)?", "islamabad",
                "egypt(?:ian)?", "cairo", "mecca|makkah", r"middle\s+east", "mideast"]
        self.anchor = re.compile(r"\b(?:" + "|".join(alts) + r")\b", re.I)
        self.buckets = {}
        # 『march』月份语境排除：in/on/by/of/since/early/late March + March 2026/8
        # 不是抗议动词（实测假阳性：Pentagon 伤亡报道 "died ... on March"、Tehran 调查 "in March 2026"）
        self.march_re = re.compile(
            r"(?<!\bin )(?<!\bon )(?<!\bby )(?<!\bof )(?<!\bsince )(?<!\bearly )"
            r"(?<!\blate )(?<!\bthis )(?<!\buntil )(?<!\bfrom )"
            r"\bmarch(?:es|ed|ing)?\b(?!\s*(?:\d{1,2}(?:st|nd|rd|th)?\b|\d{4}|20\d\d))", re.I)
        for bid, b in cfg["buckets"].items():
            groups = [[self.march_re if t == "march" else term_re(t)
                       for t in g] for g in b.get("require_all", [])]
            ent = b.get("entity_group")
            self.buckets[bid] = {"groups": groups, "entity_group": ent,
                                 "label": b.get("label", bid)}
        self.entities = {}
        for grp, ents in cfg["entities"].items():
            for name, aliases in ents.items():
                self.entities.setdefault(grp, []).append(
                    (name, [alias_re(a) for a in aliases]))
        self.esc = [term_re(t) for t in cfg["stance"]["escalate"]]
        self.dec = [term_re(t) for t in cfg["stance"]["deescalate"]]
        # pact_re：协议已签署/加入的完成态（宣布考虑/将签不算——审计教训：宣布≠已发生）
        self.pact_re = re.compile(
            r"\b(?:signed|(?<!\ba )signs?|inked|acced(?:ed|es)|joined|joins?|ratifi(?:ed|es)|"
            r"took\s+effect|entered\s+into\s+force|seals?|formalized|"
            r"announced\s+(?:the\s+)?(?:formation|signing|launch)|establishe[ds]?)\b", re.I)
        self.event_re = re.compile(
            r"\b(?:resigns?|resigned|quits?|stepped\s+down|fired|ousted|ouster|"
            r"meetings?|met|gathering|gathered|summit|investigations?|investigated|"
            r"subpoena|indicts?|arrests?|arrested|confab|march(?:es|ed|ing)?|rall(?:y|ied|ies)|"
            r"strikes?|struck|airstrikes?|shelling|shelled|launch(?:es|ed|ing)?|"
            r"killed|injur(?:e|es|ed)|detain(?:s|ed)?|dismantl(?:e|es|ed)|"
            r"seiz(?:e|es|ed)|raid(?:ed|s)?|operat(?:e|es|ed|ing)|stormed|"
            r"evacuat(?:e|es|ed)|blockade[sd]?|clamped)\b", re.I)
        # 意向语境：前方 32 字符内出现 → pact_re 命中不算完成态（审计教训：宣布将签≠已签）
        self.intent_re = re.compile(
            r"\b(?:will|to|consider|considering|weigh|weighing|stud|studying|debate|"
            r"debating|mull|mulling|eye(?:ing)?|expect|expected|plan|planning|"
            r"vow|vows|seek|seeks|seeking|reject|rejects|refus|hesitat|draft|"
            r"talks?\s+on|discuss|urged?|calls?|could|should|would|might|may|can|"
            r"hasn't|haven't|if|whether)\b", re.I)
        # attack_re：袭击已发生的完成态（claimed responsibility/killed/struck 等）
        self.attack_re = re.compile(
            r"\b(?:killed|wounded|injur(?:ed|es|ies)|struck|hits?|hit|intercepted|"
            r"shot\s+down|claimed\s+responsibility|detonat(?:ed|es|ion)|blast|"
            r"explosion|damag(?:ed|es)|launched|crashed|sank|sunk|"
            r"seiz(?:e|es|ed|ing))\b", re.I)
        self.quote_re = re.compile(
            r"\b(?:said|told|stated|declared|argued|warned|accused|vowed|promised|"
            r"insisted|writes|called)\b", re.I)
        self.pct_re = re.compile(r"(?<![\w.])(\d{1,3})\s*(?:%|percent|percentage\s+points?)", re.I)
        self.anti_num = re.compile(r"\b(?:oppose|opposed|disapprove|against|wrong|mistake|blunder|resign|corruption|distrust)\b", re.I)
        self.pro_num = re.compile(r"\b(?:approve|support|favor|worth|back|right)\b", re.I)
        # 否定词窗口：关键词前 14 字符内出现 → 极性翻转（'don't approve'/'not worth'）
        self.neg_re = re.compile(r"\b(?:not|no|never|without|neither|nor)\b|n't\b", re.I)
        self.only_re = re.compile(r"\b(?:only|just)\b", re.I)
        # 抗议桶排除：辞职抗议/伊朗镇压抗议/宣布不参加抗议 都不是街头集会
        self.protest_excl = re.compile(
            r"resign\w*[^;\n]{0,80}?\bin protest|protest\s+crackdown"
            r"|(?:won'?t|will\s+not|not\s+(?:to\s+)?attend|doesn'?t\s+plan\s+to|"
            r"didn'?t|rules?\s+out|declin\w+|refus\w+|skip\w*|avoid\w*)"
            r"[^;\n]{0,80}?\bprotests?\b", re.I)
        self.pollsters = ["Israel Verdict", "Kan", "Channel 12", "Channel 13",
                          "Lazar", "Maariv", "YouGov", "Gallup"]
        lex = " ".join(t for b in cfg["buckets"].values()
                       for g in b.get("require_all", []) for t in g)
        lex += " " + " ".join(cfg["stance"]["escalate"] + cfg["stance"]["deescalate"])
        self.lex_fp = sha(lex + f"|pv{cfg['parse_ver']}|sv{cfg['schema_ver']}")
        code_fp = hashlib.sha1(open(os.path.abspath(__file__), "rb").read()).hexdigest()
        self.cfg_ver = sha(json.dumps(cfg, sort_keys=True, ensure_ascii=False) + self.lex_fp
                           + code_fp)  # 代码行为变更也换指纹→触发全量回填

    # ---- matching ----
    def anchor_hit(self, text):
        return bool(self.anchor.search(text))

    def bucket_hits(self, text):
        hits = []
        for bid, b in self.buckets.items():
            if bid == "protest" and self.protest_excl.search(text):
                continue  # 'resigned in protest'/'won't join protests' 类不是街头集会
            if b["entity_group"]:
                ents = [n for n, res in self.entities.get(b["entity_group"], [])
                        if any(r.search(text) for r in res)]
                if ents and all(any(r.search(text) for r in g) for g in b["groups"]):
                    hits.append((bid, ents))
            elif all(any(r.search(text) for r in g) for g in b["groups"]):
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
        """(未否定命中数, 被否定命中数)：否定窗口=命中起点前 14 字符"""
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
        """升级/降温两族信号（否定翻转沿用审计修复版）"""
        text = text.replace(chr(0x2019), "'").replace(chr(0x2018), "'")
        ep, en = self._eff(self.esc, text)
        dp, dn = self._eff(self.dec, text)
        e = (ep + dn) > 0
        d = (dp + en) > 0
        if e and not d: return "escalation_signal"
        if d and not e: return "deescalation_signal"
        if e and d: return "mixed_signal"
        return "unclassified"

    def extract_polls(self, text):
        """机械抽取百分比 + 方向 + 上下文句（只定位、不加权）。
        方向按**最近**方向关键词判定（同句出现 anti+pro 数字各算各的）"""
        out = []
        text = text or ""
        text = text.replace(chr(0x2019), "'").replace(chr(0x2018), "'")
        sents = re.split(r"(?<=[.!?])\s+", text)
        for s in sents:
            low = s.lower()
            anti_pos = [m.start() for m in self.anti_num.finditer(low)]
            pro_pos = [m.start() for m in self.pro_num.finditer(low)]
            for mt in self.pct_re.finditer(s):
                val = int(mt.group(1))
                if val > 100:
                    continue
                pre = s[max(0, mt.start() - 40):mt.start()].lower()
                if "margin" in pre:            # 误差边际不是观点数值
                    continue
                i = mt.start()
                # 每个关键词按其前方 14 字符内的否定词翻转极性
                # （审计实测误分：'63% don't approve'→pro、'not worth it'→pro）
                eff = []
                for p in anti_pos:
                    eff.append((abs(p - i), "pro" if self._negated(low, p) else "anti"))
                for p in pro_pos:
                    eff.append((abs(p - i), "anti" if self._negated(low, p) else "pro"))
                if not eff:
                    direction = "other"
                else:
                    dmin = min(e[0] for e in eff)
                    near = {pol for d, pol in eff if d == dmin}
                    direction = near.pop() if len(near) == 1 else "mixed"
                    # “only/just N%”前置 → 低比例本身即反向信号（only 24% say worth it = 反战）
                    if direction in ("anti", "pro") and self.only_re.search(pre[-16:]):
                        direction = "pro" if direction == "anti" else "anti"
                pollster = next((x for x in self.pollsters if x.lower() in low), "")
                out.append({"value": val, "direction": direction,
                            "keyword": (mt.group(0).strip()),
                            "pollster": pollster,
                            "context": s.strip()[:200]})
        # gnews 的 summary=标题复读，同一数字会命中两次 → 按值+方向去重保留首见
        seen, dedup = set(), []
        for p in out:
            k = (p["value"], p["direction"])
            if k in seen:
                continue
            seen.add(k)
            dedup.append(p)
        return dedup

    # ---- classification ----
    def classify(self, title, summary):
        text = f"{title}\n{summary or ''}"
        text = text.replace(chr(0x2019), "'").replace(chr(0x2018), "'")  # 曲撇号归一（Don’t）
        if not self.anchor_hit(text):
            return None
        hits = self.bucket_hits(text)
        if not hits:
            return {"anchor_only": True}
        buckets = [h[0] for h in hits]
        ents = self.entity_hits(text)
        polls = self.extract_polls(text)
        pm = self.pact_re.search(text) if "defence_pact" in buckets else None
        pact_done = pm is not None and not self.intent_re.search(text[max(0, pm.start() - 32):pm.start()])
        if pact_done:
            kind = "fact_pact"           # 协议已签署/加入（完成态；意向语境不算）
        elif self.attack_re.search(text):
            kind = "fact_attack"         # 袭击已发生（完成态）
        elif pm is not None:
            kind = "fact_event"          # 协议相关但非完成态（考虑加入/将签/拒绝启动）
        elif self.event_re.search(text):
            kind = "fact_event"
        elif ents or self.quote_re.search(text):
            kind = "opinion_statement"
        else:
            kind = "opinion_analysis"
        stance = "unclassified"
        if kind in ("opinion_statement", "fact_event", "fact_attack"):
            stance = self.stance_of(text)
        return {"buckets": buckets, "entities": ents, "polls": polls,
                "kind": kind, "stance": stance, "anchor_only": False}

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
        # 宽松退路：块级正则
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
    items = parse_rss(r["body"], cap=int(src.get("per_source_max") or cfg.get("per_source_max") or 200))
    if not items:
        return None, dict(r, err="ok-empty(200 但 0 条)")
    return items, r

def make_records(cfg, eng, fetched, run_dt):
    """fetched: {src_id: [items]}  →  (records, audit)"""
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
                too_old += 1          # gnews 会返回多年前的陈旧文章，超龄不入档
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
                       polls=cls["polls"], kind=cls["kind"], stance=cls["stance"])
            records.append(rec)
        audit["per_source"][sid] = {"fetched": len(items), "kept": kept,
                                    "anchor_no_bucket": blocked_anchor,
                                    "no_anchor": empty, "too_old": too_old}
    # 站内去重：url id + title_key
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
    """词表/解析器改动后，对累积档全量按当前口径重算（3.15/3.18）。
    掉出所有桶的记录显式置 kind=excluded——绝不能保留旧桶只刷 cfg_ver（混龄档案）"""
    n = 0
    for rid, r in archive.items():
        if r.get("cfg_ver") != eng.cfg_ver:
            cls = eng.classify(r.get("title", ""), r.get("summary", ""))
            if cls and not cls.get("anchor_only"):
                r["buckets"] = cls["buckets"]; r["entities"] = cls["entities"]
                r["polls"] = cls["polls"]; r["kind"] = cls["kind"]
                r["stance"] = cls["stance"]
            else:
                r["buckets"] = []; r["entities"] = {}
                r["polls"] = []; r["kind"] = "excluded"; r["stance"] = None
            r["cfg_ver"] = eng.cfg_ver
            n += 1
    return n

def prune_archive(archive, as_of, max_age_days, backup_path=None):
    """删除超龄旧文（gnews 对关键词会返回多年前的文章）；返回删除数。
    删除前把被删记录追加写入 backup_path——修剪必须可恢复，绝不静默消失"""
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
    """累积档按 id 去重合并；无日期记录不跨轮携带（3.17）"""
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
        if r.get("kind") == "excluded":          # 词表收紧后掉桶的记录不进快照
            continue
        if r.get("date_unknown") or not r.get("published"):
            undated.append(r)
        elif start <= r["published"][:10] <= as_of.isoformat():
            out.append(r)
    return out, undated

# ---------------------------------------------------------------- render
KIND_LABEL = {"fact_pact": "事实·协议", "fact_attack": "事实·袭击",
              "fact_event": "事实·事件", "opinion_statement": "观点·表态",
              "opinion_analysis": "观点·分析", "excluded": "已排除"}
STANCE_LABEL = {"escalation_signal": "升级信号", "deescalation_signal": "降温信号",
                "mixed_signal": "双向混存", "unclassified": "未分类"}

def _row_link(r):
    return f"[原文]({r['url']})" if r.get("url") else "—"

def render_report(cfg, records, undated, audit, stats, as_of, verify_note, outdir):
    W = []
    dt = as_of.isoformat()
    by_bucket = Counter(b for r in records for b in r["buckets"])
    by_kind = Counter(r["kind"] for r in records)
    by_stance = Counter(r["stance"] for r in records if r.get("stance") != "unclassified")
    polls_n = sum(len(r.get("polls") or []) for r in records)
    W.append(f"# 美伊冲突·中东各国态势监测日报（{dt}）")
    W.append("")
    W.append("## 0. 阅读须知（先读，再读数字）")
    W.append("")
    W.append("- **事实/观点五分类**：每条记录标注 `kind`：")
    W.append("  - `fact_pact` 协议已签署/加入=**事实**（完成态：signed/joined/ ratified；")
    W.append("    『考虑加入/将签』不算，落 `fact_event` 或 `opinion_statement`）；")
    W.append("  - `fact_attack` 袭击已发生=**事实**（killed/struck/claimed responsibility 等完成态；")
    W.append("    **声称战果≠经核实战果**：民兵组织 Telegram 声称的袭击次数是单方口径，报告只并列不采信；")
    W.append("  - `fact_event` 其他可核验事件=**事实**（警报/军援交付/机场关闭/会晤/决议）；")
    W.append("  - `opinion_statement` 表态=**『他说了X』是事实，X 本身是观点**，附人物归属与上下文；")
    W.append("  - `opinion_analysis` 分析解读=**观点**。")
    W.append("- **信源等级**：T1=原始出处（UN 新闻稿）；T2=机构分析（ICG/Long War Journal）；")
    W.append("  T3=媒体转述（海湾媒体/国际媒体/Google News 聚合层）；T4（社交平台/当事方宣传如 Al-Masirah）不采集。")
    W.append("  **官方源现实**：CENTCOM 403、伊拉克通讯社 SSL 中断、卡塔尔通讯社无 RSS——美军伤亡与官方声明")
    W.append("  全部经 T3 转述，报告逐条披露，不做绕过。")
    W.append("- **伤亡/损失数字是当事方口径**：胡塞/民兵声称 vs 沙特/美方确认可差数倍，逐条标注归属。")
    W.append("- **立场词表是启发式**：升级(escalation) vs 降温(deescalation) 两族信号，只看方向不看强度，")
    W.append("  否定窗口与 only 翻转已内置（Don’t/Not worth 类误分修复），逐条附上下文可复核。")
    W.append(f"- 本轮配置指纹 `cfg_ver={stats['cfg_ver'][:12]}…`；快照内唯一（闸门断言）。")
    W.append("")
    W.append(f"## 1. 快照头部（{dt}）")
    W.append("")
    W.append(f"- 抓取源 {stats['n_enabled']} 个（T1 {stats['n_t1']} / T2+T3 其余；停用留档 {stats['n_disabled']}）")
    W.append(f"- 本次新增 **{stats['n_new']}** 条；当前快照（窗口 {cfg['window_days']} 天）**{len(records)}** 条")
    W.append(f"- 窗口内信号桶计数：`" + "、".join(
        f"{cfg['buckets'][b]['label']} {n}" for b, n in by_bucket.most_common()) + "`")
    W.append(f"- 事实/观点：`" + "、".join(f"{KIND_LABEL[k]} {n}" for k, n in by_kind.most_common() if k in KIND_LABEL) + "`")
    W.append(f"- 升级/降温分布（启发式）：`" + "、".join(f"{STANCE_LABEL[s]} {n}" for s, n in by_stance.most_common()) + "`")
    W.append(f"- 百分比数字抽取：**{polls_n}** 个（§2 逐个列示供甄别）")
    W.append(f"- 回源对账：{verify_note}")
    W.append("")
    # §2 数字抽见
    W.append("## 2. 百分比数字证据板（凡抽出数字都列示，kind 供甄别口径）")
    W.append("")
    W.append("| 日期 | 数值·倾向 | 口径句（context） | kind ｜ 来源/等级 | 原文 |")
    W.append("|---|---|---|---|---|")
    poll_recs = [x for x in records if x.get("polls")]
    for r in sorted(poll_recs, key=lambda x: x.get("published") or "", reverse=True):
        for p in (r.get("polls") or []):
            ctx = p["context"].replace("|", "/").replace("\n", " ")
            W.append(f"| {r['published'][:10] if r['published'] else '—'} "
                     f"| **{p['value']}%** {p['direction']} | {ctx} "
                     f"| {KIND_LABEL[r['kind']]} ｜ {r['publisher']} / {r['tier']} | {_row_link(r)} |")
    if not poll_recs:
        W.append("| — | 本窗口未捕获含百分比数字的报道 | — | — | — |")
    W.append("")
    # §3 麦加协议
    W.append("## 3. 麦加防务协议与区域安全架构")
    W.append("")
    W.append("### 3.1 协议基线（手工快照，2026-09-19 录入）")
    W.append("")
    W.append("| 日期 | 事项 | 等级 | 来源 |")
    W.append("|---|---|---|---|")
    for b in cfg.get("pact_baseline", []):
        W.append(f"| {b['date']} | {b['event'][:90]} | {b['tier']} | {b['source'][:40]} |")
    W.append("")
    W.append("> ⚠️ 基线为 T2/T3 转写快照；成员国变动/新成员加入后须回源官方公报逐条核验。")
    W.append("")
    W.append("### 3.2 窗口内新增协议/安全架构动向")
    W.append("")
    pact = [r for r in records if "defence_pact" in r["buckets"]]
    if pact:
        for r in sorted(pact, key=lambda x: x.get("published") or "", reverse=True)[:30]:
            ents = "、".join(dict.fromkeys(e for g in r["entities"].values() for e in g)) or "—"
            st = STANCE_LABEL.get(r.get("stance"), "—")
            W.append(f"- `{r['published'][:10] if r['published'] else '—'}` "
                     f"**[{KIND_LABEL[r['kind']]}·{st}]** {r['title']} — 实体: {ents} "
                     f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
    else:
        W.append("（本窗口无协议条目——『无条目≠无活动』，历史见累积档 ALL-records.jsonl）")
    W.append("")
    # §4 也门战线
    W.append("## 4. 也门战线（胡塞 vs 沙特/政府军）")
    W.append("")
    W.append("### 4.1 战线基线（手工快照）")
    W.append("")
    W.append("| 日期 | 事项 | 等级 | 来源 |")
    W.append("|---|---|---|---|")
    for b in cfg.get("houthi_baseline", []):
        W.append(f"| {b['date']} | {b['event'][:90]} | {b['tier']} | {b['source'][:40]} |")
    W.append("")
    W.append("### 4.2 窗口内新增战线动向")
    W.append("")
    hw = [r for r in records if "houthi_war" in r["buckets"]]
    if hw:
        for r in sorted(hw, key=lambda x: x.get("published") or "", reverse=True)[:30]:
            st = STANCE_LABEL.get(r.get("stance"), "—")
            W.append(f"- `{r['published'][:10] if r['published'] else '—'}` "
                     f"[{KIND_LABEL[r['kind']]}·{st}] {r['title']} "
                     f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
    else:
        W.append("（本窗口无战线条目——『无条目≠无活动』，历史见累积档 ALL-records.jsonl）")
    W.append("")
    # §5 伊拉克
    W.append("## 5. 伊拉克战线（民兵 vs 驻伊美军）")
    W.append("")
    W.append("### 5.1 伊拉克基线（手工快照）")
    W.append("")
    W.append("| 日期 | 事项 | 等级 | 来源 |")
    W.append("|---|---|---|---|")
    for b in cfg.get("iraq_baseline", []):
        W.append(f"| {b['date']} | {b['event'][:90]} | {b['tier']} | {b['source'][:40]} |")
    W.append("")
    W.append("> ⚠️ 民兵『声称袭击次数』（如 823 次）是单方 Telegram 口径（Meir Amit 汇编），")
    W.append("> 与美方确认的损失是两套数字；ISW 每日 Iran Update 是计数权威源但 RSS 403，")
    W.append("> 本项目靠 gnews 转述层替代——窗口内计数完整性有缺口，方向参考为主。")
    W.append("")
    W.append("### 5.2 窗口内新增伊拉克动向")
    W.append("")
    irq = [r for r in records if "iraq_militia" in r["buckets"]]
    if irq:
        for r in sorted(irq, key=lambda x: x.get("published") or "", reverse=True)[:30]:
            ents = "、".join(dict.fromkeys(e for g in r["entities"].get("militias", []))) or "—"
            st = STANCE_LABEL.get(r.get("stance"), "—")
            W.append(f"- `{r['published'][:10] if r['published'] else '—'}` "
                     f"[{KIND_LABEL[r['kind']]}·{st}] {r['title']} — 民兵: {ents} "
                     f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
    else:
        W.append("（本窗口无伊拉克条目——『无条目≠无活动』，历史见累积档 ALL-records.jsonl）")
    W.append("")
    # §6 海湾国家
    W.append("## 6. 海湾国家遇袭与防务（含航运/双海峡）")
    W.append("")
    W.append("### 6.1 海湾基线（手工快照）")
    W.append("")
    W.append("| 日期 | 事项 | 等级 | 来源 |")
    W.append("|---|---|---|---|")
    for b in cfg.get("gulf_baseline", []):
        W.append(f"| {b['date']} | {b['event'][:90]} | {b['tier']} | {b['source'][:40]} |")
    W.append("")
    for bid, dlabel in (("gulf_front", "海湾国家动向"), ("shipping", "双海峡与红海航运")):
        W.append(f"### 6.{2 if bid == 'gulf_front' else 3} {dlabel}（窗口内新增）")
        W.append("")
        rows = [r for r in records if bid in r["buckets"]]
        if rows:
            for r in sorted(rows, key=lambda x: x.get("published") or "", reverse=True)[:25]:
                st = STANCE_LABEL.get(r.get("stance"), "—")
                W.append(f"- `{r['published'][:10] if r['published'] else '—'}` "
                         f"[{KIND_LABEL[r['kind']]}·{st}] {r['title']} "
                         f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
        else:
            W.append(f"（本窗口无{dlabel}条目——『无条目≠无活动』，历史见累积档 ALL-records.jsonl）")
        W.append("")
    # §7 斡旋
    W.append("## 7. 斡旋与停火外交")
    W.append("")
    med = [r for r in records if "mediation" in r["buckets"]]
    if med:
        for r in sorted(med, key=lambda x: x.get("published") or "", reverse=True)[:25]:
            ents = "、".join(dict.fromkeys(e for g in r["entities"].values() for e in g)) or "—"
            st = STANCE_LABEL.get(r.get("stance"), "—")
            W.append(f"- `{r['published'][:10] if r['published'] else '—'}` "
                     f"[{KIND_LABEL[r['kind']]}·{st}] {r['title']} — 斡旋方: {ents} "
                     f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
    else:
        W.append("（本窗口无斡旋条目——『无条目≠无活动』，历史见累积档 ALL-records.jsonl）")
    W.append("")
    # §8 街头
    W.append("## 8. 街头与内部反应")
    W.append("")
    prot = [r for r in records if "protest" in r["buckets"]]
    if prot:
        for r in sorted(prot, key=lambda x: x.get("published") or "", reverse=True)[:25]:
            W.append(f"- `{r['published'][:10] if r['published'] else '—'}` {r['title']} "
                     f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
    else:
        W.append("（本窗口无街头行动条目——『无条目≠无活动』，历史见累积档 ALL-records.jsonl）")
    W.append("")
    # §9 无日期
    W.append("## 9. 无日期条目（不跨轮累积，仅本轮披露）")
    W.append("")
    if undated:
        for r in undated[:40]:
            W.append(f"- （日期未知）{r['title']} ｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
        W.append(f"\n共 {len(undated)} 条；`published=Thu, 01 Jan 1970` 类纪元零值已按『日期未知』处理。")
    else:
        W.append("（无）")
    W.append("")
    # §10 已知偏差
    W.append("## 10. 已知偏差与局限")
    W.append("")
    for i, x in enumerate(cfg["report"]["known_limitations"], 1):
        W.append(f"{i}. {x}")
    W.append(f"{len(cfg['report']['known_limitations'])+1}. 停用源留档见附录A；"
             f"被锚定拦截的候选 {len(audit.get('anchor_no_bucket', []))} 条已留档 `_filter_audit.json`。")
    W.append("")
    # 附录A
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
    W.append("- **T1** 原始出处（UN 新闻稿）；**T2** 机构分析（ICG/Long War Journal/Meir Amit 转述汇编）；")
    W.append("  **T3** 媒体转述（海湾媒体/国际媒体/Google News 聚合层）；**T4** 自媒体与当事方宣传——不采用。")
    W.append("- 事实（完成态协议/袭击/事件）≠ 机械抽取（百分比定位）≠ 启发式判断（升级/降温词表）。")
    W.append("- 『某方声称X』是关于该声称的**事实**；X 本身真伪是另一层（当事方口径需回源）。")
    W.append("")
    return "\n".join(W)

# ---------------------------------------------------------------- main
def write_outputs(cfg, records, undated, audit, stats, as_of, outdir, verify_note):
    dd = os.path.join(outdir, as_of.isoformat())
    os.makedirs(dd, exist_ok=True)
    rep = render_report(cfg, records, undated, audit, stats, as_of, verify_note, outdir)
    p_rep = os.path.join(dd, f"mideast-digest-{as_of.isoformat()}.md")
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
            r.setdefault("polls", []); r.setdefault("kind", "opinion_analysis")
            r.setdefault("stance", "unclassified"); r.setdefault("tier", "T3")
            r.setdefault("publisher", "?"); r.setdefault("url", "")
        recs, undated = window_filter(allrecs, as_of, cfg["window_days"])
        stats = stats_header(cfg, eng, recs, [], archive, as_of)
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
    # 渲染用：补默认字段（老档案兼容）
    for r in recs + undated:
        r.setdefault("buckets", []); r.setdefault("entities", {})
        r.setdefault("polls", []); r.setdefault("kind", "opinion_analysis")
        r.setdefault("stance", "unclassified"); r.setdefault("tier", "T3")
        r.setdefault("publisher", "?"); r.setdefault("url", ""); r.setdefault("title", "")
    stats = stats_header(cfg, eng, recs, enabled, archive, as_of)
    stats["n_new"] = n_new
    paths = write_outputs(cfg, recs, undated, audit, stats, as_of, outdir,
                          verify_last_note(outdir))
    print(f"[backfill] 回填 {n_bf} 条；新增 {n_new}；快照 {len(recs)}（无日期 {len(undated)}）")
    append_health_alert(paths["latest"], src_stats, outdir)
    append_verification_section(paths["latest"], recs, cfg)
    print(f"[done] {paths['latest']}")

def stats_header(cfg, eng, recs, enabled, archive, as_of):
    ens = enabled if enabled else [s for s in cfg["sources"] if s.get("enabled")]
    return {"n_enabled": len(ens),
            "n_t1": sum(1 for s in ens if s["tier"] == "T1"),
            "n_t3": sum(1 for s in ens if s["tier"] == "T3"),
            "n_disabled": sum(1 for s in cfg["sources"] if not s.get("enabled")),
            "n_new": 0, "cfg_ver": eng.cfg_ver}

# ---------------------------------------------------------------- selftest
def run_selftest(cfg, eng):
    P = []
    def ck(name, cond):
        P.append((name, bool(cond)))

    # --- 词形族（基础形+屈折形）---
    for w, forms in [("strike", ["strikes", "striking", "striker"]),
                     ("seize", ["seizes", "seized", "seizing"]),
                     ("mediate", ["mediates", "mediated", "mediating"]),
                     ("negotiate", ["negotiates", "negotiated", "negotiating"]),
                     ("condemn", ["condemns", "condemned", "condemning"]),
                     ("announce", ["announces", "announced", "announcing"])]:
        rx = term_re(w)
        ck(f"词形族 {w}", all(rx.search(f) for f in [w] + forms))
    ck("disapprove 不误中 approve", not alias_re("approve").search("43% disapprove"))
    # --- 日期 ---
    ck("RFC822 全称", parse_any_date("Sat, 13 Sep 2026 09:00:00 +0000") == ("2026-09-13", False))
    ck("两位数年", parse_any_date("Wed, 29 Jul 26 12:37:14 +0200") == ("2026-07-29", False))
    ck("纪元零值=未知", parse_any_date("Thu, 01 Jan 1970 00:00:00 +0000") == (None, True))
    ck("ISO", parse_any_date("2026-09-12T10:00:00Z")[0] == "2026-09-12")
    ck("年份离谱判失败", parse_any_date("13 Sep 99 10:00:00 +0000") == (None, False))
    ck("垃圾判失败", parse_any_date("not a date") == (None, False))
    # --- 数字抽取 ---
    t1 = "The survey found 54% of Israelis back helping Saudi Arabia while 26% oppose it."
    ex = eng.extract_polls(t1)
    ck("数字抽取 2 个", len(ex) == 2)
    ck("方向 pro（back 最近）", ex and ex[0]["direction"] == "pro" and ex[0]["value"] == 54)
    ck("方向 anti（oppose）", len(ex) > 1 and ex[1]["direction"] == "anti")
    t2 = "Traffic fell 85 percent with a margin of error of 3 percentage points."
    ex2 = eng.extract_polls(t2)
    ck("误差边际排除", all(p["value"] != 3 for p in ex2))
    ck("85% other（无方向词）", any(p["value"] == 85 and p["direction"] == "other" for p in ex2))
    # --- 分类：五类 kind 都能产出 ---
    r1 = eng.classify("Saudi Arabia, Turkiye, Pakistan sign Mecca joint defence pact", "")
    ck("协议签署→fact_pact", r1 and r1["kind"] == "fact_pact" and "defence_pact" in r1["buckets"])
    r1b = eng.classify("Egypt considering legal issues around joining Mecca pact", "")
    ck("考虑加入→不是fact_pact", r1b and r1b["kind"] != "fact_pact" and "defence_pact" in r1b["buckets"])
    r2 = eng.classify("Houthis seize strategic Perim island near Bab el-Mandeb",
                      "Government forces lost the coastal city of Mokha, officials said.")
    ck("胡塞夺岛→fact_attack", r2 and r2["kind"] == "fact_attack" and "houthi_war" in r2["buckets"])
    r3 = eng.classify("Kataib Hezbollah claims drone attack on US base near Baghdad",
                      "The Islamic Resistance in Iraq said it launched the strike.")
    ck("民兵袭美军→fact_attack", r3 and r3["kind"] == "fact_attack" and "iraq_militia" in r3["buckets"])
    ck("民兵实体识别", r3 and "Kataib Hezbollah" in r3["entities"].get("militias", []))
    r4 = eng.classify("Iranian drone strike kills US troops at Kuwait port facility",
                      "Officials confirmed casualties at the civilian port operations centre.")
    ck("海湾遇袭→fact_attack", r4 and "gulf_front" in r4["buckets"]
       and "Kuwait" in r4["entities"].get("gulf_states", []))
    r5 = eng.classify("Vessel struck by unknown projectile in Red Sea shipping lane",
                      "UKMTO reported the tanker was hit while transiting.")
    ck("红海航运→fact_attack", r5 and "shipping" in r5["buckets"])
    r6 = eng.classify("Saudi Arabia proposes two-week ceasefire to Houthis via Oman",
                      "Oman mediated the proposal for direct talks with Riyadh.")
    ck("斡旋→opinion_statement", r6 and "mediation" in r6["buckets"]
       and "Oman" in r6["entities"].get("mediators", []))
    r7 = eng.classify("Thousands protest in Sanaa against Saudi airstrikes", "")
    ck("集会→抗议桶", r7 and not r7.get("anchor_only") and "protest" in r7["buckets"])
    r8 = eng.classify("Quarterly futures market commentary", "Brent crude technical analysis.")
    ck("无锚定=不收", r8 is None)
    r9 = eng.classify("Middle East Quartet discusses regional security framework",
                      "Officials talked about maritime domain awareness in the region.")
    ck("锚定无桶=拦截候选", r9 and r9.get("anchor_only") is True)
    # --- 桶结构不变量 ---
    ck("defence_pact 二维", len(cfg["buckets"]["defence_pact"]["require_all"]) == 2)
    ck("houthi_war 二维", len(cfg["buckets"]["houthi_war"]["require_all"]) == 2)
    ck("iraq_militia 二维", len(cfg["buckets"]["iraq_militia"]["require_all"]) == 2)
    ck("shipping 二维", len(cfg["buckets"]["shipping"]["require_all"]) == 2)
    ck("所有桶有标签", all(b.get("label") for b in cfg["buckets"].values()))
    ck("每启用源 tier 合法", all(s.get("tier") in ("T1", "T2", "T3")
       for s in cfg["sources"] if s.get("enabled")))
    ck("无启用 T4 源", all(s.get("tier") != "T4" for s in cfg["sources"] if s.get("enabled")))
    ck("停用源有留档", all(s.get("note") for s in cfg["sources"] if not s.get("enabled")))
    for bl in ("pact_baseline", "houthi_baseline", "iraq_baseline", "gulf_baseline"):
        ck(f"{bl} 字段完整",
           all(b.get("date") and b.get("event") and b.get("tier") in ("T1", "T2", "T3")
               and b.get("source") for b in cfg[bl]))
    # --- gnews 后缀折叠/链接归一 ---
    ck("gnews 后缀折叠", title_key("Houthis advance - Reuters") ==
       title_key("Houthis advance - The Business Standard"))
    ck("链接归一", norm_link("https://x.com/a?utm_source=1&ocid=2") == "https://x.com/a")
    # --- 窗口/合并 ---
    arch = {"a": {"id": "a", "published": "2026-09-01", "date_unknown": False}}
    recs = [{"id": "a", "published": "2026-09-01", "date_unknown": False, "run_date": "2026-09-19"},
            {"id": "b", "published": "2026-09-18", "date_unknown": False, "run_date": "2026-09-19"},
            {"id": "u", "published": None, "date_unknown": True, "run_date": "2026-09-19"}]
    n = merge_archive(arch, recs)
    ck("合并新增=1（无日期不携带）", n == 1 and "u" not in arch and "b" in arch and "a" in arch)
    win, und = window_filter(list(arch.values()), date(2026, 9, 19), 14)
    ck("窗口过滤（09-01 掉出 14 天窗）", {x["id"] for x in win} == {"b"} and len(und) == 0)
    # --- 月份 March ≠ 抗议动词 march ---
    rm = eng.classify("Casualties reported in March 2026 during the Gulf war",
                      "A service member died on March at a base in the region.")
    ck("March 月份不进抗议桶",
       rm is None or rm.get("anchor_only") or "protest" not in rm["buckets"])
    rm2 = eng.classify("Thousands march in Baghdad against the war",
                       "Protesters rallied demanding an end to strikes.")
    ck("动词 march 进抗议桶",
       rm2 is not None and not rm2.get("anchor_only") and "protest" in rm2["buckets"])
    # --- 审计修复守卫：否定翻转 / only 翻转 / 抗议排除 / 域桶剔泛词 ---
    ex3 = eng.extract_polls("63% of respondents don't support the Saudi operation.")
    ck("否定翻转 don't support→anti", ex3 and ex3[0]["direction"] == "anti")
    ex4 = eng.extract_polls("Only 24 percent say the strikes were worth the cost.")
    ck("only+worth→anti", ex4 and ex4[0]["direction"] == "anti")
    ck("立场否定 not attack→deescalate",
       eng.stance_of("The coalition will not attack first.") == "deescalation_signal")
    ck("立场升级 strike→escalate",
       eng.stance_of("The coalition launched new strikes and vowed more.") == "escalation_signal")
    rk = eng.classify("Minister says he won't join antiwar protests during summit", "")
    ck("won't join protests 不进抗议桶",
       rk is None or rk.get("anchor_only") or "protest" not in rk["buckets"])
    # 域桶剔泛词：military 单独提及不进 houthi_war（须有 houthi/ansar/sanaa 实体词）
    rg1 = eng.classify("Houthi forces seize military camp near Sanaa", "")
    ck("houthi 词+军事词进 houthi_war", rg1 and "houthi_war" in rg1["buckets"])
    rg2 = eng.classify("Regional artists turn war debris into art installations", "")
    ck("文化报道不进战线桶", rg2 is None or rg2.get("anchor_only") or
       not ({"houthi_war", "iraq_militia", "gulf_front"} & set(rg2["buckets"])))
    # 修剪备份
    _tmp_prune = {"a": {"published": "2020-01-01", "t": "x"},
                  "b": {"published": "2026-09-01"}, "c": {}}
    _bp = os.path.join(os.environ.get("TEMP", "/tmp"), "_prune_test_mideast.jsonl")
    ck("prune 超龄删除（含备份）",
       prune_archive(_tmp_prune, date(2026, 9, 19), 120, backup_path=_bp) == 1
       and _tmp_prune["b"]["published"] == "2026-09-01"
       and "2020-01-01" in open(_bp, encoding="utf-8").read())
    # gnews 复读双抽去重
    dd = eng.extract_polls("Poll: 71% distrust the pact. Poll: 71% distrust the alliance.")
    ck("同数字复读去重", len(dd) == 1)
    # 数字实体解码
    ck("数字实体解码", strip_tags("died &#8230; later") == "died … later")
    # y→ied 变位
    ck("justify 词形族", term_re("justify").search("justified") and
       term_re("justify").search("justifies"))
    # 回填掉桶 → excluded
    arch2 = {"x": {"id": "x", "published": "2026-09-15", "date_unknown": False,
                   "title": "Regional discussion in March 2026",
                   "summary": "Middle East policy talk continued.",
                   "kind": "fact_event", "buckets": ["protest"], "cfg_ver": "OLD"},
             "y": {"id": "y", "published": "2026-09-16", "date_unknown": False,
                   "title": "Central bank meeting minutes",
                   "summary": "Rates discussed.",
                   "kind": "fact_event", "buckets": ["protest"], "cfg_ver": "OLD"}}
    backfill_archive(cfg, eng, arch2)
    ck("回填掉桶(anchor_only)→excluded", arch2["x"]["kind"] == "excluded" and arch2["x"]["buckets"] == [])
    ck("回填掉桶(无锚定)→excluded", arch2["y"]["kind"] == "excluded" and arch2["y"]["buckets"] == [])
    win2, _ = window_filter(list(arch2.values()), date(2026, 9, 19), 14)
    ck("excluded 不进快照", len(win2) == 0)
    # --- 协议完成态守卫：宣布将签/考虑加入/拒绝启动 ≠ 已签署（审计教训迁移）---
    rp1 = eng.classify("Pakistan rejects activating Mecca pact to join Saudi war on Yemen", "")
    rp2 = eng.classify("Will Bangladesh join the Mecca defense alliance?", "")
    rp3 = eng.classify("Saudi Arabia, Turkiye, Pakistan sign Mecca joint defence agreement", "")
    rp4 = eng.classify("Egypt joins the Mecca pact after constitutional review", "")
    ck("拒绝启动/将签→非fact_pact", rp1 and rp1["kind"] == "fact_event"
       and rp2 and rp2["kind"] == "fact_event")
    ck("已签署/已加入→fact_pact", rp3 and rp3["kind"] == "fact_pact"
       and rp4 and rp4["kind"] == "fact_pact")

    # --- 第二轮守卫：情态意向/名词 sign/股市 rally/militia 精准 ---
    rq1 = eng.classify("Egypt could join Mecca defence pact, Erdogan says", "")
    rq2 = eng.classify("What Will Happen If Iran, Egypt, or Bangladesh Joins the Mecca Pact?", "")
    rq3 = eng.classify("The Mecca pact is a sign US power is waning in the Middle East", "")
    ck("could join/假设句→非fact_pact", rq1 and rq1["kind"] == "fact_event"
       and rq2 and rq2["kind"] == "fact_event")
    ck("名词 sign 不触发 fact_pact", rq3 and rq3["kind"] == "opinion_analysis")
    rq4 = eng.classify("Most Asian stocks track Wall St rally, yen drops after BoJ rate hike", "")
    ck("股市 rally 不进抗议桶", rq4 is None or rq4.get("anchor_only")
       or "protest" not in rq4["buckets"])
    rq5 = eng.classify("Thousands rally in Sanaa against Saudi airstrikes", "")
    ck("集会 rally in 仍进抗议桶", rq5 and not rq5.get("anchor_only")
       and "protest" in rq5["buckets"])
    rq6 = eng.classify("Houthis and Saudis trade claims of military attacks", "")
    ck("胡塞报道不进伊拉克桶", rq6 is None or rq6.get("anchor_only")
       or "iraq_militia" not in rq6["buckets"])
    rq7 = eng.classify("Iraq's Popular Mobilization Forces says 20 members killed in US-Saudi strikes", "")
    ck("PMF 遇袭进伊拉克桶", rq7 and not rq7.get("anchor_only")
       and "iraq_militia" in rq7["buckets"])

    # --- 核心结论能产出（防『全绿但结论恒空』）---
    ck("核心结论: 协议/袭击/事件/表态 四类都能产出",
       r1["kind"] == "fact_pact" and r2["kind"] == "fact_attack"
       and r7["kind"] in ("fact_event", "opinion_statement") and r6 is not None)
    ok = sum(1 for _, c in P if c)
    for name, c in P:
        print(f"  {'✅' if c else '❌'} {name}")
    print(f"[selftest] {ok}/{len(P)} 通过")
    return 0 if ok == len(P) else 1



if __name__ == "__main__":
    main()
