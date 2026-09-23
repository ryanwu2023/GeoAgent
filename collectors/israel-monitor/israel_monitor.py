#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
opinion_monitor.py — 美伊战争·美国民意监测（民调 / 意见领袖 / 国会 / 街头）
骨架承继 readiness-monitor（表态类）：配置驱动源 + 主题锚定 + 实体归属 +
tier 分级 + 事实/观点四分类 + 累积档回填 + 离线自检。

用法:
  python opinion_monitor.py                # 抓取+渲染
  python opinion_monitor.py --selftest     # 离线自检（不联网）
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
        alts = ["israel(?:i|is)?", "idf", "jerusalem", r"tel\s+aviv", "knesset",
                "netanyahu", "hezbollah", "hamas", r"west\s+bank", "gaza",
                r"leban(?:on|ese)", r"syri(?:a|an)", "iran(?:ian)?", "tehran",
                r"middle\s+east", "mideast"]
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
        self.anti = [term_re(t) for t in cfg["stance"]["anti"]]
        self.pro = [term_re(t) for t in cfg["stance"]["pro"]]
        self.vote_re = re.compile(
            r"\b(?:votes?|voted|voting|roll\s+calls?|passed|blocks?|blocked|"
            r"rejects?|rejected|defeats?|defeated|approves?|approved|fails?|failed|"
            r"cloture|filibuster|tally|motion)\b", re.I)
        self.event_re = re.compile(
            r"\b(?:resigns?|resigned|quits?|stepped\s+down|fired|ousted|ouster|"
            r"meetings?|met|gathering|gathered|summit|investigations?|investigated|"
            r"subpoena|indicts?|arrests?|arrested|confab|march(?:es|ed|ing)?|rall(?:y|ied|ies)|"
            r"strikes?|struck|airstrikes?|shelling|shelled|launch(?:es|ed|ing)?|"
            r"killed|injur(?:e|es|ed)|detain(?:s|ed)?|dismantl(?:e|es|ed)|"
            r"seiz(?:e|es|ed)|raid(?:ed|s)?|operat(?:e|es|ed|ing)|stormed|"
            r"evacuat(?:e|es|ed)|blockade[sd]?|clamped)\b", re.I)
        self.results_re = re.compile(
            r"\b(?:exit\s+polls?|final\s+results?|results?|ballots?\s+(?:counted|tallied)|"
            r"seat\s+projections?|mandates?|coalition\s+(?:talks|agreement|agreements|negotiations)|"
            r"concedes?|conceded)\b", re.I)
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
        self.pollsters = ["Lazar", "Channel 12", "Channel 13", "Channel 14", "Kan",
                          "Maariv", "Israel Democracy Institute", "Ynet", "Walla",
                          "Mano", "Panel4U", "Smith Consulting"]
        lex = " ".join(t for b in cfg["buckets"].values()
                       for g in b.get("require_all", []) for t in g)
        lex += " " + " ".join(cfg["stance"]["anti"] + cfg["stance"]["pro"])
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
        text = text.replace(chr(0x2019), "'").replace(chr(0x2018), "'")
        ap, an = self._eff(self.anti, text)
        pp, pn = self._eff(self.pro, text)
        a = (ap + pn) > 0
        p = (pp + an) > 0
        if a and not p: return "anti_gov_signal"
        if p and not a: return "pro_gov_signal"
        if a and p: return "mixed_signal"
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
        if "election_polls" in buckets and polls:
            kind = "fact_poll"
        elif ("election_polls" in buckets or "election_campaign" in buckets) \
                and self.results_re.search(text):
            kind = "fact_vote"
        elif self.event_re.search(text):
            kind = "fact_event"
        elif ents or self.quote_re.search(text):
            kind = "opinion_statement"
        else:
            kind = "opinion_analysis"
        stance = "unclassified"
        if kind in ("opinion_statement", "fact_event", "fact_vote"):
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
KIND_LABEL = {"fact_poll": "事实·民调", "fact_vote": "事实·结果", "fact_event": "事实·事件",
              "opinion_statement": "观点·表态", "opinion_analysis": "观点·分析",
              "excluded": "已排除"}
STANCE_LABEL = {"anti_gov_signal": "反内塔/倒阁", "pro_gov_signal": "挺内/挺政府",
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
    W.append(f"# 美伊冲突·以色列动向监测日报（{dt}）")
    W.append("")
    W.append("## 0. 阅读须知（先读，再读数字）")
    W.append("")
    W.append("- **事实/观点四分类**：每条记录标注 `kind`：")
    W.append("  - `fact_poll` 民调/席位预测数字=**事实**（发布值，程序只定位不加权）；")
    W.append("  - `fact_vote` 开票/组阁结果=**事实**（10-27 投票日之前不存在结果，只有预测）；")
    W.append("  - `fact_event` 可核验事件=**事实**（打击/突袭/逮捕/会晤/宣判）；")
    W.append("  - `opinion_statement` 表态=**『他说了X』是事实，X 本身是观点**，逐条附人物归属与上下文；")
    W.append("  - `opinion_analysis` 分析解读=**观点**。")
    W.append("- **信源等级**：T1=原始出处（UN 新闻稿等官方一手）；T3=媒体转述（含 IDF 声明的转述层）；")
    W.append("  T4（社交平台自媒体）不采集——不可核验。IDF 官网为 JS 渲染站无法直连，其声明均经 T3 转述。")
    W.append("- **伤亡/损失数字是当事方口径**：以方（IDF 声明）与黎方（卫生部长）/巴方（哈马斯卫生部）")
    W.append("  可差数倍，逐条标注归属；本程序不做核实只做并列。")
    W.append("- **立场词表是启发式**：只看方向不看强度（反内塔/倒阁 vs 挺内/挺政府），逐条附上下文可复核。")
    W.append(f"- 本轮配置指纹 `cfg_ver={stats['cfg_ver'][:12]}…`；快照内唯一（闸门断言）。")
    W.append("")
    W.append(f"## 1. 快照头部（{dt}）")
    W.append("")
    W.append(f"- 抓取源 {stats['n_enabled']} 个（T1 {stats['n_t1']} / T3 {stats['n_t3']}；停用留档 {stats['n_disabled']}）")
    W.append(f"- 本次新增 **{stats['n_new']}** 条；当前快照（窗口 {cfg['window_days']} 天）**{len(records)}** 条")
    W.append(f"- 窗口内信号桶计数：`" + "、".join(
        f"{cfg['buckets'][b]['label']} {n}" for b, n in by_bucket.most_common()) + "`")
    W.append(f"- 事实/观点：`" + "、".join(f"{KIND_LABEL[k]} {n}" for k, n in by_kind.most_common() if k in KIND_LABEL) + "`")
    W.append(f"- 立场分布（启发式）：`" + "、".join(f"{STANCE_LABEL[s]} {n}" for s, n in by_stance.most_common()) + "`")
    W.append(f"- 民调/席位数字抽取：**{polls_n}** 个（§2 逐个列示）")
    W.append(f"- 回源对账：{verify_note}")
    W.append("")
    # §2 民调
    W.append("## 2. 民调/席位预测证据板（fact_poll）")
    W.append("")
    W.append("| 日期 | 机构 | 数值·方向 | 口径句（context） | 来源/等级 | 原文 |")
    W.append("|---|---|---|---|---|---|")
    # 凡抽出数字的记录都列示（含非 fact_poll 记录的数字，标注 kind 供甄别口径）
    poll_recs = [x for x in records if x.get("polls")]
    for r in sorted(poll_recs, key=lambda x: x.get("published") or "", reverse=True):
        for p in (r.get("polls") or []):
            ctx = p["context"].replace("|", "/").replace("\n", " ")
            W.append(f"| {r['published'][:10] if r['published'] else '—'} "
                     f"| {p['pollster'] or r['publisher']} "
                     f"| **{p['value']}%** { {'anti':'反内塔/倒阁','pro':'挺内/挺政府','other':'—'}[p['direction']] } "
                     f"| {ctx} | {KIND_LABEL[r['kind']]} ｜ {r['publisher']} / {r['tier']} | {_row_link(r)} |")
    if not poll_recs:
        W.append("| — | 本窗口未捕获民调报道 | — | — | — | — |")
    W.append("")
    # §3 大选
    W.append("## 3. 大选与组阁（第 26 届议会，2026-10-27 投票）")
    W.append("")
    W.append("### 3.1 选举基线（手工快照，含民调均值与关键事实）")
    W.append("")
    W.append("| 日期 | 事项 | 数值/内容 | 等级 | 来源 | 备注 |")
    W.append("|---|---|---|---|---|---|")
    for b in cfg.get("election_baseline", []):
        W.append(f"| {b['date']} | {b['item']} | {b['value']} | {b['tier']} "
                 f"| {b['src'][:60]} | {b.get('note','')} |")
    W.append("")
    W.append("> ⚠️ 基线为 T3 转写快照（聚合/媒体渠道，2026-09-19 录入）；")
    W.append("> 投票日后须以中央选举委员会官方结果替换，逐条回源。")
    W.append("")
    W.append("### 3.2 窗口内新增大选/组阁动向")
    W.append("")
    elec = [r for r in records if r["buckets"] and
            ("election_campaign" in r["buckets"] or r["kind"] == "fact_vote")]
    if elec:
        for r in sorted(elec, key=lambda x: x.get("published") or "", reverse=True)[:30]:
            ents = "、".join(dict.fromkeys(e for g in r["entities"].values() for e in g)) or "—"
            st = STANCE_LABEL.get(r.get("stance"), "—")
            W.append(f"- `{r['published'][:10] if r['published'] else '—'}` "
                     f"**[{KIND_LABEL[r['kind']]}·{st}]** {r['title']} — 人物: {ents} "
                     f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
    else:
        W.append("（本窗口无大选/组阁条目——『无条目≠无活动』，历史见累积档 ALL-records.jsonl）")
    W.append("")
    # §4 内塔尼亚胡
    W.append("## 4. 内塔尼亚胡：司法与政治处境")
    W.append("")
    W.append("### 4.1 审判/法律基线（手工快照）")
    W.append("")
    W.append("| 日期 | 事项 | 内容 | 等级 | 备注 |")
    W.append("|---|---|---|---|---|")
    for b in cfg.get("netanyahu_baseline", []):
        W.append(f"| {b['date']} | {b['item']} | {b['value']} | {b['tier']} | {b.get('note','')} |")
    W.append("")
    W.append("### 4.2 窗口内新增司法/政治动向")
    W.append("")
    trial = [r for r in records if "netanyahu_trial" in r["buckets"]]
    if trial:
        for r in sorted(trial, key=lambda x: x.get("published") or "", reverse=True)[:25]:
            st = STANCE_LABEL.get(r.get("stance"), "—")
            W.append(f"- `{r['published'][:10] if r['published'] else '—'}` "
                     f"[{KIND_LABEL[r['kind']]}·{st}] {r['title']} "
                     f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
    else:
        W.append("（本窗口无司法/政治条目——『无条目≠无活动』，历史见累积档 ALL-records.jsonl）")
    W.append("")
    # §5 四域军事
    W.append("## 5. 四域军事动向")
    W.append("")
    W.append("### 5.1 军事基线（四域关键事实快照）")
    W.append("")
    W.append("| 域 | 日期 | 事项 | 内容 | 等级 | 备注 |")
    W.append("|---|---|---|---|---|---|")
    DOMAINS = [("lebanon", "黎巴嫩"), ("syria", "叙利亚"), ("westbank", "约旦河西岸"), ("gaza", "加沙")]
    for b in cfg.get("military_baseline", []):
        dn = dict(DOMAINS).get(b["domain"], b["domain"])
        W.append(f"| {dn} | {b['date']} | {b['item']} | {b['value']} | {b['tier']} | {b.get('note','')} |")
    W.append("")
    for did, dlabel in DOMAINS:
        W.append(f"### 5.{DOMAINS.index((did, dlabel))+2} {dlabel}（窗口内新增）")
        W.append("")
        rows = [r for r in records if did in r["buckets"]]
        if rows:
            for r in sorted(rows, key=lambda x: x.get("published") or "", reverse=True)[:25]:
                st = STANCE_LABEL.get(r.get("stance"), "—")
                W.append(f"- `{r['published'][:10] if r['published'] else '—'}` "
                         f"[{KIND_LABEL[r['kind']]}·{st}] {r['title']} "
                         f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
        else:
            W.append(f"（本窗口无{dlabel}条目——『无条目≠无活动』，历史见累积档 ALL-records.jsonl）")
        W.append("")
    # §6 对伊第二战线
    W.append("## 6. 对伊第二战线（伊朗/胡塞方向）")
    W.append("")
    irf = [r for r in records if "iran_front" in r["buckets"]]
    if irf:
        for r in sorted(irf, key=lambda x: x.get("published") or "", reverse=True)[:25]:
            st = STANCE_LABEL.get(r.get("stance"), "—")
            W.append(f"- `{r['published'][:10] if r['published'] else '—'}` "
                     f"[{KIND_LABEL[r['kind']]}·{st}] {r['title']} "
                     f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
    else:
        W.append("（本窗口无对伊条目——『无条目≠无活动』，历史见累积档 ALL-records.jsonl）")
    W.append("")
    # §7 街头
    W.append("## 7. 街头行动/抗议（以色列国内）")
    W.append("")
    prot = [r for r in records if "protest" in r["buckets"]]
    if prot:
        for r in sorted(prot, key=lambda x: x.get("published") or "", reverse=True)[:25]:
            W.append(f"- `{r['published'][:10] if r['published'] else '—'}` {r['title']} "
                     f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
    else:
        W.append("（本窗口无街头行动条目——『无条目≠无活动』，历史见累积档 ALL-records.jsonl）")
    W.append("")
    # §8 无日期
    W.append("## 8. 无日期条目（不跨轮累积，仅本轮披露）")
    W.append("")
    if undated:
        for r in undated[:40]:
            W.append(f"- （日期未知）{r['title']} ｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
        W.append(f"\n共 {len(undated)} 条；`published=Thu, 01 Jan 1970` 类纪元零值已按『日期未知』处理。")
    else:
        W.append("（无）")
    W.append("")
    # §9 已知偏差
    W.append("## 9. 已知偏差与局限")
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
    for s in cfg["sources"]:
        st = "启用" if s.get("enabled") else "停用"
        W.append(f"| {s['id']} | {s['tier']} | {s['publisher']} | {st} | {(s.get('note') or '')[:120]} |")
    W.append("")
    W.append("## 附录B 等级定义与分类学")
    W.append("")
    W.append("- **T1** 原始出处（UN 新闻稿等官方一手）；**T2** 官方/机构估算（本项目暂无）；")
    W.append("  **T3** 媒体转述（含 Google News 聚合、IDF/真主党声明的转述层）；**T4** 自媒体——不采用。")
    W.append("- 事实（发布值/结果/事件）≠ 机械抽取（百分比定位）≠ 启发式判断（立场词表）。")
    W.append("- 『某人物主张X』是关于该人物的**事实**；X 是**观点**。本报告同时保留两层。")
    W.append("")
    return "\n".join(W)

# ---------------------------------------------------------------- main
def write_outputs(cfg, records, undated, audit, stats, as_of, outdir, verify_note):
    dd = os.path.join(outdir, as_of.isoformat())
    os.makedirs(dd, exist_ok=True)
    rep = render_report(cfg, records, undated, audit, stats, as_of, verify_note, outdir)
    p_rep = os.path.join(dd, f"israel-digest-{as_of.isoformat()}.md")
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

    R = term_re
    # --- 词形族 ---
    for w, forms in [("strike", ["strikes", "striking"]),
                     ("launch", ["launches", "launched", "launching"]),
                     ("coalition", ["coalitions"]),
                     ("protest", ["protests", "protested", "protesting", "protester"]),
                     ("oppose", ["opposes", "opposed", "opposing"]),
                     ("condemn", ["condemns", "condemned", "condemnation"]),
                     ("resign", ["resigns", "resigned", "resignation"]),
                     ("march", ["marches", "marched", "marching"])]:
        rx = R(w)
        ck(f"词形族 {w}", all(rx.search(f) for f in [w] + forms))
    ck("无半截词根", not any(re.search(r"(ed|ing)$", t) for t in
       cfg["stance"]["anti"] + cfg["stance"]["pro"]))
    # --- 实体陷阱 ---
    ck("Yair Golan 不误中 Golan Heights", not alias_re("Yair Golan").search("IDF operates near the Golan Heights"))
    ck("Eisenkot 双拼写都命中", any(r.search("Eizenkot said") for r in
       [alias_re(a) for a in cfg["entities"]["campaign"]["Gadi Eisenkot"]]))
    ck("Netanyahu 命中所有格", any(r.search("Netanyahu's coalition") for r in
       [alias_re(a) for a in cfg["entities"]["campaign"]["Benjamin Netanyahu"]]))
    ck("President Aoun 命中", any(r.search("President Aoun urged Washington") for r in
       [alias_re(a) for a in cfg["entities"]["officials"]["Joseph Aoun"]]))
    ck("disapprove 不误中 approve", not alias_re("approve").search("43% disapprove"))
    # --- 日期 ---
    ck("RFC822 全称", parse_any_date("Sat, 13 Sep 2026 09:00:00 +0000") == ("2026-09-13", False))
    ck("两位数年", parse_any_date("Wed, 29 Jul 26 12:37:14 +0200") == ("2026-07-29", False))
    ck("纪元零值=未知", parse_any_date("Thu, 01 Jan 1970 00:00:00 +0000") == (None, True))
    ck("ISO", parse_any_date("2026-09-12T10:00:00Z")[0] == "2026-09-12")
    ck("美式 Mon d, YYYY", parse_any_date("September 5, 2026")[0] == "2026-09-05")
    ck("年份离谱判失败", parse_any_date("13 Sep 99 10:00:00 +0000") == (None, False))
    ck("垃圾判失败", parse_any_date("not a date") == (None, False))
    # --- 民调抽取 ---
    t1 = "45% of Israelis want Netanyahu to resign while 27% back him, the Lazar poll found."
    ex = eng.extract_polls(t1)
    ck("民调抽取 2 个", len(ex) == 2)
    ck("方向 anti（resign 最近）", ex and ex[0]["direction"] == "anti" and ex[0]["value"] == 45)
    ck("方向 pro（back 最近）", len(ex) > 1 and ex[1]["direction"] == "pro" and ex[1]["value"] == 27)
    ck("机构识别 Lazar", ex and ex[0]["pollster"] == "Lazar")
    t2 = "The poll of 800 respondents had a margin of error of 3 percentage points."
    ck("误差边际排除", all(p["value"] != 3 for p in eng.extract_polls(t2)) or not eng.extract_polls(t2))
    # --- 分类 ---
    r1 = eng.classify("Israel poll: 45% want Netanyahu to resign, 27% back him",
                      "The Lazar survey found 45 percent want him to go.")
    ck("大选民调→fact_poll", r1 and r1["kind"] == "fact_poll")
    ck("民调记录实体含 Netanyahu", r1 and "Benjamin Netanyahu" in r1["entities"].get("campaign", []))
    r2 = eng.classify("Netanyahu tells court the cases against him are fabricated",
                      "In the trial hearing, the PM said the prosecution relies on flawed evidence.")
    ck("审判听证→opinion_statement", r2 and r2["kind"] == "opinion_statement"
       and "netanyahu_trial" in r2["buckets"])
    r3 = eng.classify("IDF strikes Hezbollah infrastructure in southern Lebanon after drone attack",
                      "The military said the strikes targeted weapons storage in Nabatieh.")
    ck("黎巴嫩打击→fact_event", r3 and r3["kind"] == "fact_event" and "lebanon" in r3["buckets"])
    r4 = eng.classify("Israel operates in security zone near Golan Heights, Syrian media report",
                      "IDF troops operated near Mount Hermon overnight.")
    ck("叙利亚→fact_event", r4 and r4["kind"] == "fact_event" and "syria" in r4["buckets"])
    ck("Golan Heights 不进人物实体", r4 and "Yair Golan" not in r4["entities"].get("campaign", []))
    r5 = eng.classify("Israel warns Iran over nuclear rebuild",
                      "Officials vowed to prevent Tehran from reconstituting enrichment.")
    ck("对伊表态→opinion_statement", r5 and r5["kind"] == "opinion_statement"
       and "iran_front" in r5["buckets"])
    r6 = eng.classify("Thousands rally in Tel Aviv against the government ahead of the election",
                      "Protesters gathered demanding early elections.")
    ck("集会→fact_event·反政府", r6 and r6["kind"] == "fact_event"
       and r6["stance"] == "anti_gov_signal" and "protest" in r6["buckets"])
    r7 = eng.classify("Exit poll: Likud wins 30 seats in Israel election", "Channel 12 exit poll published.")
    ck("开票→fact_vote", r7 and r7["kind"] == "fact_vote")
    r8 = eng.classify("IDF raid in the West Bank village of Talfit",
                      "Weapons were seized and three suspects arrested, the military said.")
    ck("西岸突袭→fact_event", r8 and "westbank" in r8["buckets"] and r8["kind"] == "fact_event")
    r9 = eng.classify("Gaza ceasefire holds as IDF controls buffer zone",
                      "The army said it struck a Hamas cell after an alleged violation.")
    ck("加沙停火→fact_event", r9 and "gaza" in r9["buckets"])
    r10 = eng.classify("Fed minutes signal rate path", "Inflation and employment discussion.")
    ck("无锚定=不收", r10 is None)
    r11 = eng.classify("Middle East envoy discusses regional security", "Talks continued in Doha.")
    ck("锚定无桶=拦截候选", r11 and r11.get("anchor_only") is True)
    # --- 桶结构不变量 ---
    ck("election_polls 二维", len(cfg["buckets"]["election_polls"]["require_all"]) == 2)
    ck("所有桶有标签", all(b.get("label") for b in cfg["buckets"].values()))
    ck("每源有 tier", all(s.get("tier") in ("T1", "T2", "T3") for s in cfg["sources"]))
    ck("无 T4 源", all(s.get("tier") != "T4" for s in cfg["sources"]))
    ck("停用源有留档", all("note" in s and s["note"] for s in cfg["sources"] if not s.get("enabled")))
    ck("选举基线字段完整", all(b.get("date") and b.get("item") and b.get("tier") in ("T1", "T2", "T3")
       and b.get("src") for b in cfg["election_baseline"]))
    ck("司法基线字段完整", all(b.get("date") and b.get("item") and b.get("tier") in ("T1", "T2", "T3")
       for b in cfg["netanyahu_baseline"]))
    ck("军事基线四域合法", all(b["domain"] in ("lebanon", "syria", "westbank", "gaza")
       and b.get("date") and b.get("item") for b in cfg["military_baseline"]))
    # --- title_key 折叠 ---
    ck("gnews 后缀折叠", title_key("Likud leads poll - Reuters") ==
       title_key("Likud leads poll - The Business Standard"))
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
    # --- 月份 March ≠ 抗议动词 march（opinion-monitor 实测假阳性，守卫迁移）---
    rm = eng.classify("Deaths reported in March 2026 during the Israel-Iran war",
                      "A service member died of undetermined causes on March at a base.")
    ck("March 月份不进抗议桶",
       rm is None or rm.get("anchor_only") or "protest" not in rm["buckets"])
    rm2 = eng.classify("Thousands march against the government in Tel Aviv",
                       "Protesters rally demanding elections now.")
    ck("动词 march 进抗议桶",
       rm2 is not None and not rm2.get("anchor_only") and "protest" in rm2["buckets"])
    # --- 审计修复 2026-09-19：否定翻转 / only 翻转 / 抗议排除 / 四域军事组 ---
    ex2 = eng.extract_polls("63% of Israelis don't support the government's Gaza policy.")
    ck("否定翻转 don't support→anti", ex2 and ex2[0]["direction"] == "anti")
    ex3 = eng.extract_polls("Only 24 percent say the Gaza operation was worth the cost.")
    ck("only+worth→anti", ex3 and ex3[0]["direction"] == "anti")
    ex4 = eng.extract_polls("Only 15 percent oppose the ceasefire deal.")
    ck("only+oppose→pro", ex4 and ex4[0]["direction"] == "pro")
    ck("立场否定 not support→anti_gov",
       eng.stance_of("The minister does not support the Gaza operation.") == "anti_gov_signal")
    rk = eng.classify("Mamdani says he won't join any anti-Netanyahu protests at UNGA", "")
    ck("won't join protests 不进抗议桶",
       rk is None or rk.get("anchor_only") or "protest" not in rk["buckets"])
    rp = eng.classify("Thousands rally in Tel Aviv demanding elections now",
                      "Protesters gathered in front of the Knesset.")
    ck("真集会仍进抗议桶", rp is not None and not rp.get("anchor_only") and "protest" in rp["buckets"])
    rg1 = eng.classify("Israeli strike on motorcycle kills Palestinian in Gaza City", "")
    ck("军事词+gaza 进 gaza 桶",
       rg1 is not None and not rg1.get("anchor_only") and "gaza" in rg1["buckets"])
    rg2 = eng.classify("Mural of Macklemore wrapped in Palestinian flag painted on Gaza ruins", "")
    ck("文化报道不进 gaza 桶",
       rg2 is None or rg2.get("anchor_only") or "gaza" not in rg2["buckets"])
    _tmp_prune = {"a": {"published": "2020-01-01", "t": "x"},
                  "b": {"published": "2026-09-01"}, "c": {}}
    _bp = os.path.join(os.environ.get("TEMP", "/tmp"), "_prune_test.jsonl")
    ck("prune 超龄删除（含备份）",
       prune_archive(_tmp_prune, date(2026, 9, 19), 120, backup_path=_bp) == 1
       and _tmp_prune["b"]["published"] == "2026-09-01"
       and "2020-01-01" in open(_bp, encoding="utf-8").read())
    # --- gnews summary 复读导致同数字双抽 → 去重 ---
    dd = eng.extract_polls("Poll: 71% distrust Netanyahu. Poll: 71% distrust the government.")
    ck("同数字复读去重", len(dd) == 1)
    # --- 数字实体解码 ---
    ck("数字实体解码", strip_tags("died &#8230; later") == "died … later")
    # --- y→ied 变位 ---
    ck("justify 词形族", term_re("justify").search("justified") and
       term_re("justify").search("justifies"))
    # --- 回填掉桶 → excluded，不进快照（防『保留旧桶只刷指纹』的混龄档案）---
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
    # --- 核心结论能产出（防『全绿但结论恒空』）---
    ck("核心结论: 民调/表态/事件/结果 四类都能产出",
       r1["kind"] == "fact_poll" and r2["kind"] == "opinion_statement"
       and r3["kind"] == "fact_event" and r7["kind"] == "fact_vote")
    ok = sum(1 for _, c in P if c)
    for name, c in P:
        print(f"  {'✅' if c else '❌'} {name}")
    print(f"[selftest] {ok}/{len(P)} 通过")
    return 0 if ok == len(P) else 1



if __name__ == "__main__":
    main()
