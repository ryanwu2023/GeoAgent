#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
iran_domestic_monitor.py — 美伊冲突·伊朗内政监测
（里亚尔汇率 / 物价食品 / 青年失业 / 原油出口 / 燃料限电 / 文官政府vs革命卫队 / 最高领袖权威）
骨架承继 mideast-monitor（表态/事件类）：配置驱动源 + 主题锚定 + 实体归属 +
tier 分级 + 事实/观点分类 + 数值机械抽取（fx/pct/bbl）+ 累积档回填 + 离线自检。

用法:
  python iran_domestic_monitor.py                # 抓取+渲染
  python iran_domestic_monitor.py --selftest     # 离线自检（不联网）
  python iran_domestic_monitor.py --as-of 2026-09-20 --out-dir output/_xday-verify
  python iran_domestic_monitor.py --render-only  # 仅从累积档重渲染
  python iran_domestic_monitor.py --from-file corpus.json  # 离线退路
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
    "janvier":"jan","fevrier":"feb","mars":"mar","avril":"apr","juin":"jun",
    "juillet":"jul","aout":"aug","septembre":"sep","octobre":"oct","novembre":"nov",
    "decembre":"dec","décembre":"dec","février":"feb","août":"aug",
    "januar":"jan","februar":"feb","marz":"mar","märz":"mar","juni":"jun",
    "juli":"jul","oktober":"oct","dezember":"dec",
    "gennaio":"jan","febbraio":"feb","aprile":"apr","maggio":"may",
    "giugno":"jun","luglio":"jul","settembre":"sep","ottobre":"oct",
    "novembre":"nov","dicembre":"dec",
    "janeiro":"jan","fevereiro":"feb","março":"mar","junho":"jun","julho":"jul",
    "setembro":"sep","outubro":"oct","novembro":"nov","dezembro":"dec",
    # 波斯历月份英文转写（转述层常见）：只做别名化，日期以转述语境为准
    "farvardin":"mar","ordibehesht":"apr","khordad":"jun","tir":"jun",
    "mordad":"aug","shahrivar":"sep","mehr":"oct","aban":"oct",
    "azar":"nov","dey":"jan","bahman":"feb","esfand":"feb",
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
    words = term.split()
    parts = []
    for i, w in enumerate(words):
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
        # 锚定：伊朗全域专名（rial/toman 故意不入锚——阿曼里亚尔同拼形）
        alts = ["iran(?:ian)?", "tehran", "farsi", "persian(?:s)?",
                "isfahan", "tabriz", "mashhad", "shiraz", r"bandar\s+abbas",
                "khamenei", "pezeshkian", "irgc", "pasdaran",
                r"revolutionary\s+guards?"]
        self.anchor = re.compile(r"\b(?:" + "|".join(alts) + r")\b", re.I)
        self.buckets = {}
        # 『march』月份语境排除（承继 mideast：in/on/by/of/since March ≠ 抗议动词）
        self.march_re = re.compile(
            r"(?<!\bin )(?<!\bon )(?<!\bby )(?<!\bof )(?<!\bsince )(?<!\bearly )"
            r"(?<!\blate )(?<!\bthis )(?<!\buntil )(?<!\bfrom )"
            r"\bmarch(?:es|ed|ing)?\b(?!\s*(?:\d{1,2}(?:st|nd|rd|th)?\b|\d{4}|20\d\d))", re.I)
        for bid, b in cfg["buckets"].items():
            groups = [[self.march_re if t == "march" else term_re(t)
                       for t in g] for g in b.get("require_all", [])]
            self.buckets[bid] = {"groups": groups, "label": b.get("label", bid)}
        self.entities = {}
        for grp, ents in cfg["entities"].items():
            for name, aliases in ents.items():
                self.entities.setdefault(grp, []).append(
                    (name, [alias_re(a) for a in aliases]))
        self.esc = [term_re(t) for t in cfg["stance"]["escalate"]]
        self.dec = [term_re(t) for t in cfg["stance"]["deescalate"]]
        self.event_re = re.compile(
            r"\b(?:rose|risen|fell|dropped|dropping|jumped|surged|plunged|slumped|"
            r"hiked|hik(?:es|ing)?|raised|raise|doubles?|doubled|triples?|tripled|"
            r"hit|reached|climbed|surpass(?:es|ed)?|crossed|passed|topped|"
            r"launch(?:es|ed|ing)?|resumed|halted|halts?|suspend(?:ed|s)?|"
            r"arrests?|arrested|detain(?:s|ed)?|raid(?:ed|s)?|crackdown|"
            r"closed|closes?|closure|shut|cut|cuts|slashed|slash|"
            r"began|ended|implemented|took\s+effect|entered\s+into\s+force|"
            r"announced|releas(?:es|ed|ing)?|publish(?:es|ed)?|issued|"
            r"queues?|queued|queueing|ration(?:s|ed|ing)?|imposed|extended|"
            r"met|received|addressed|appeared|attended|visit(?:ed|s)?|"
            r"dismissed|resigned|resigns?|appointed|named|summon(?:ed|s)?|"
            r"impeach(?:ed|es|ment)?|censured|blacklist(?:ed|s)?|strikes?|struck)\b", re.I)
        # 统计/数据发布语境（+数值在场 → fact_data）
        self.data_re = re.compile(
            r"\b(?:statistical\s+center|statistics\s+center|statistics\s+show|"
            r"data\s+show(?:s|ed)?|data\s+released|figures\s+show(?:s|ed)?|"
            r"figures\s+released|report\s+show(?:s|ed)?|monthly\s+report|"
            r"cpi\b|consumer\s+price\s+index|inflation\s+rate|"
            r"unemployment\s+rate|official\s+statistics)\b", re.I)
        self.quote_re = re.compile(
            r"\b(?:said|told|stated|declared|argued|warned|accused|vowed|promised|"
            r"insisted|writes|called|acknowledged)\b", re.I)
        # 领袖域子信号
        self.appear_re = re.compile(
            r"\b(?:met|meets?|received|speech|addressed|address|appeared|appearance|"
            r"attended|publicly|in\s+public|friday\s+prayers?|led\s+prayers?|"
            r"visit(?:ed|s)?|seen)\b", re.I)
        self.succession_re = re.compile(
            r"\b(?:success(?:or|ion)|health|frail|absent|mojtaba|"
            r"assembly\s+of\s+experts|authority|legitimacy|reshap(?:e|ing|ed))\b", re.I)
        self.neg_re = re.compile(r"\b(?:not|no|never|without|neither|nor)\b|n't\b", re.I)
        self.only_re = re.compile(r"\b(?:only|just)\b", re.I)
        # 汇率排除：海湾里亚尔（omani/qatari/yemeni rial 同拼形）
        # + 兑换工具页（MEXC 等加密交易所 P2P 页实测误入）
        # 排除语义收紧：convert 必须连着「to IRR/Iranian Rial」结构，裸 convert 不排除
        self.fx_excl = re.compile(
            r"\b(?:omani|qatari|yemeni|saudi|moroccan|krona)\s+rials?\b"
            r"|\bconvert\w*\s+[^;.\n]{0,30}?\bto\s+(?:irr\b|iranian\s+rials?)"
            r"|\blive\s+price\s+in\s+(?:irr|toman)\b"
            r"|\bprice\s+in\s+(?:irr|toman)\b|\bcalculator\b", re.I)
        # 燃料/限电桶排除：war-spillover 能源危机（孟加拉实测误入）。
        # 只列与伊朗能源事务无交集的国家；巴基斯坦等伊朗邻国不列（防误杀走私/贸易报道）
        self.fuel_excl = re.compile(
            r"\b(?:bangladesh(?:i)?|dhaka|sri\s+lanka)\b", re.I)
        # 抗议桶排除（承继 mideast：辞职抗议/宣布不参加 ≠ 街头集会；
        # 新增：rallying cry/rally around（效忠口号）≠ 集会——军演报道实测误入）
        self.protest_excl = re.compile(
            r"resign\w*[^;\n]{0,80}?\bin protest|protest\s+crackdown"
            r"|rall(?:y|ying)\s+(?:cry|around|behind|to)"
            r"|(?:won'?t|will\s+not|not\s+(?:to\s+)?attend|doesn'?t\s+plan\s+to|"
            r"didn'?t|rules?\s+out|declin\w+|refus\w+|skip\w*|avoid\w*)"
            r"[^;\n]{0,80}?\bprotests?\b", re.I)
        # ---------- 数值机械抽取 ----------
        # 百分比（通胀/失业等；上限放宽到 300——食品通胀 127.5% 是真信号）
        self.pct_re = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d+)?)\s*(?:%|percent|percentage\s+points?)", re.I)
        self.pct_topic = re.compile(
            r"\b(inflation|unemployment|food|price[sd]?|participation|growth|"
            r"deficit|poverty|wage|salary|devaluation)\b", re.I)
        # 汇率：数字 + thousand/million/billion? + rial(s)/toman(s)，±60 字符内须有 dollar
        self.money_re = re.compile(
            r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
            r"\s*(thousand|million|billion)?\s*(rials?|tomans?)\b", re.I)
        self.dollar_kw = re.compile(r"\b(?:dollars?|usd)\b", re.I)
        self.mult = {"thousand": 1e3, "million": 1e6, "billion": 1e9, None: 1.0}
        # 桶装油量：N (million|thousand)? barrels (per day)?
        self.bbl_re = re.compile(
            r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
            r"\s*(million|thousand)?\s*barrels?\b", re.I)
        self.perday_re = re.compile(r"\b(?:per\s+day|a\s+day|/day|bpd|mb/d|bd)\b", re.I)
        self.persd_re = re.compile(r"\b(?:per\s+second|per\s+capita|km|kg|kilogram|liter|litre|hectare)\b", re.I)
        # 汇率合理性：归一后 里亚尔/USD 在 [1e4, 1e10]
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
        """title 专供排除项：工具页/spillover 文章的国名与 convert 结构几乎必在标题，
        正文提及不排除（排除语义=只覆盖标题区间，防误杀真信号）"""
        hits = []
        for bid, b in self.buckets.items():
            if bid == "protest" and self.protest_excl.search(text):
                continue
            if bid == "rial_fx" and title and self.fx_excl.search(title):
                continue   # 海湾里亚尔/兑换工具页不是伊朗汇率
            if bid == "fuel_power" and title and self.fuel_excl.search(title):
                continue   # 邻国 war-spillover 能源危机不是伊朗内政
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
        """压力/缓解两族信号（否定翻转；只看方向）"""
        text = text.replace(chr(0x2019), "'").replace(chr(0x2018), "'")
        ep, en = self._eff(self.esc, text)
        dp, dn = self._eff(self.dec, text)
        e = (ep + dn) > 0
        d = (dp + en) > 0
        if e and not d: return "escalation_signal"
        if d and not e: return "deescalation_signal"
        if e and d: return "mixed_signal"
        return "unclassified"

    @staticmethod
    def _num(s):
        return float(s.replace(",", ""))

    def extract_metrics(self, text):
        """机械抽取三类数值：fx（里亚尔汇率）、pct（百分比）、bbl（桶量）。
        只定位与归一，不加权不推断；每条带上下文句供人工复核。"""
        out = []
        text = text or ""
        text = text.replace(chr(0x2019), "'").replace(chr(0x2018), "'")
        sents = re.split(r"(?<=[.!?])\s+", text)
        for s in sents:
            low = s.lower()
            # --- fx ---
            for mt in self.money_re.finditer(s):
                pre = s[max(0, mt.start() - 60):mt.start()].lower()
                post = s[mt.end():mt.end() + 60].lower()
                if not (self.dollar_kw.search(pre) or self.dollar_kw.search(post)):
                    continue   # 非美元语境的里亚尔金额（如面包每块价格）不收
                if self.fx_excl.search(s):
                    continue   # 海湾里亚尔
                val = self._num(mt.group(1)) * self.mult[mt.group(2)]
                unit = "toman" if mt.group(3).lower().startswith("toman") else "rial"
                rial = val * 10 if unit == "toman" else val
                if not (1e4 <= rial <= 1e10):
                    continue   # 汇率合理性兜底
                out.append({"kind": "fx", "value_raw": mt.group(0).strip(),
                            "unit": unit, "rial_per_usd": rial,
                            "context": s.strip()[:200]})
            # --- pct ---
            for mt in self.pct_re.finditer(s):
                val = float(mt.group(1))
                if val > 300 or val < 1:
                    continue
                pre = s[max(0, mt.start() - 40):mt.start()].lower()
                if "margin" in pre:
                    continue
                tps = [m.group(1).lower() for m in self.pct_topic.finditer(low)]
                # 取距百分比最近的主题词
                topic = ""
                if tps:
                    poss = [m.start(1) for m in self.pct_topic.finditer(low)]
                    i = mt.start()
                    topic = tps[min(range(len(poss)), key=lambda k: abs(poss[k] - i))]
                out.append({"kind": "pct", "value": val, "topic": topic,
                            "keyword": mt.group(0).strip(),
                            "context": s.strip()[:200]})
            # --- bbl ---
            for mt in self.bbl_re.finditer(s):
                val = self._num(mt.group(1)) * self.mult[mt.group(2)]
                tail = s[mt.end():mt.end() + 24].lower()
                if self.persd_re.search(tail) and not self.perday_re.search(tail):
                    continue   # 每秒/人均/公里等非日流量
                has_rate = bool(self.perday_re.search(tail))
                out.append({"kind": "bbl", "value": val, "per_day": has_rate,
                            "context": s.strip()[:200]})
        # 去重（gnews 标题复读）
        seen, dedup = set(), []
        for p in out:
            k = (p["kind"], p.get("value_raw") or p.get("value"), p.get("unit"))
            if k in seen:
                continue
            seen.add(k)
            dedup.append(p)
        return dedup

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
        metrics = self.extract_metrics(text)
        if self.data_re.search(text) and (metrics or self.pct_re.search(text)):
            kind = "fact_data"           # 官方统计/数据发布（附数值在场）
        elif self.event_re.search(text):
            kind = "fact_event"          # 完成态事件（涨价/停电/被捕/会见/发布）
        elif ents or self.quote_re.search(text):
            kind = "opinion_statement"
        else:
            kind = "opinion_analysis"
        stance = "unclassified"
        if kind in ("opinion_statement", "fact_event", "fact_data"):
            stance = self.stance_of(text)
        flags = {}
        if "khamenei_leader" in buckets:
            flags["appearance"] = bool(self.appear_re.search(text))
            flags["succession"] = bool(self.succession_re.search(text))
        return {"buckets": buckets, "entities": ents, "metrics": metrics,
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
                       metrics=cls["metrics"], kind=cls["kind"], stance=cls["stance"],
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
                r["metrics"] = cls["metrics"]; r["kind"] = cls["kind"]
                r["stance"] = cls["stance"]; r["flags"] = cls["flags"]
            else:
                r["buckets"] = []; r["entities"] = {}
                r["metrics"] = []; r["kind"] = "excluded"; r["stance"] = None
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
KIND_LABEL = {"fact_data": "事实·数据", "fact_event": "事实·事件",
              "opinion_statement": "观点·表态", "opinion_analysis": "观点·分析",
              "excluded": "已排除"}
STANCE_LABEL = {"escalation_signal": "压力信号", "deescalation_signal": "缓解信号",
                "mixed_signal": "双向混存", "unclassified": "未分类"}

def _row_link(r):
    return f"[原文]({r['url']})" if r.get("url") else "—"

def _metric_cell(p):
    if p["kind"] == "fx":
        return f"**{p['value_raw']}** ({p['unit']}，≈{p['rial_per_usd']:,.0f} 里亚尔/USD)"
    if p["kind"] == "pct":
        t = f"/{p['topic']}" if p.get("topic") else ""
        return f"**{p['value']}%**{t}"
    pd = "/日" if p.get("per_day") else ""
    return f"**{p['value']:,.0f} 桶**{pd}" if p["value"] >= 100 else f"**{p['value']} 桶**{pd}"

def render_report(cfg, records, undated, audit, stats, as_of, verify_note, outdir):
    W = []
    dt = as_of.isoformat()
    by_bucket = Counter(b for r in records for b in r["buckets"])
    by_kind = Counter(r["kind"] for r in records)
    by_stance = Counter(r["stance"] for r in records if r.get("stance") != "unclassified")
    metrics_n = sum(len(r.get("metrics") or []) for r in records)
    # 桶 × tier 交叉表（3.8：某桶全 T3 要警告）
    cross = {bid: Counter() for bid in cfg["buckets"]}
    for r in records:
        for b in r["buckets"]:
            cross[b][r["tier"]] += 1
    all_t3 = [bid for bid, c in cross.items() if c and sum(v for k, v in c.items() if k != "T3") == 0]
    W.append(f"# 美伊冲突·伊朗内政监测日报（{dt}）")
    W.append("")
    W.append("## 0. 阅读须知（先读，再读数字）")
    W.append("")
    W.append("- **事实/观点四分类**：每条记录标注 `kind`：")
    W.append("  - `fact_data` 官方统计/数据发布=**事实**（须数值在场；经 T3 转写的官方数字仍是转写，")
    W.append("    伊朗官方一手渠道本监测期全部不可达——见已知偏差 1）；")
    W.append("  - `fact_event` 完成态事件=**事实**（涨价执行/停电/被捕/会见/配给开始）；")
    W.append("  - `opinion_statement` 表态=**『他说了X』是事实，X 本身是观点**，附人物归属与上下文；")
    W.append("  - `opinion_analysis` 分析解读=**观点**。")
    W.append("- **信源等级**：本项目探测期 **T1 直接源为零**（官方站全部不可达，不绕过）；")
    W.append("  T2=机构分析（Atlantic Council IranSource）；T3=媒体转述（含 Tehran Times 官媒窗口与 Google News 聚合层）。")
    W.append("- **数值口径警告**：里亚尔官方价 vs 自由市场价是两套口径禁止混用；托曼=10 里亚尔；")
    W.append("  年度通胀 vs 点对点通胀 vs 环比是三个不同口径，数字并存非冲突。")
    W.append("- **单方口径**：原油出口『归零』（美方/跟踪机构）vs『未中断』（伊朗官媒）并列披露不采信；")
    W.append("  IRGC 未领饷是美方官员单方说法。")
    W.append("- **立场词表是启发式**：内政语境=压力(escalation) vs 缓解(deescalation)，只看方向不看强度。")
    W.append(f"- 本轮配置指纹 `cfg_ver={stats['cfg_ver'][:12]}…`；快照内唯一（闸门断言）。")
    W.append("")
    W.append(f"## 1. 快照头部（{dt}）")
    W.append("")
    W.append(f"- 抓取源 {stats['n_enabled']} 个（T2 {stats['n_t2']} / 其余 T3；停用留档 {stats['n_disabled']}）")
    W.append(f"- 本次新增 **{stats['n_new']}** 条；当前快照（窗口 {cfg['window_days']} 天）**{len(records)}** 条")
    W.append(f"- 窗口内信号桶计数：`" + "、".join(
        f"{cfg['buckets'][b]['label']} {n}" for b, n in by_bucket.most_common()) + "`")
    W.append(f"- 事实/观点：`" + "、".join(f"{KIND_LABEL[k]} {n}" for k, n in by_kind.most_common() if k in KIND_LABEL) + "`")
    W.append(f"- 压力/缓解分布（启发式）：`" + "、".join(f"{STANCE_LABEL[s]} {n}" for s, n in by_stance.most_common()) + "`")
    W.append(f"- 数值机械抽取：**{metrics_n}** 个（§2 逐个列示供甄别）")
    W.append(f"- 回源对账：{verify_note}")
    W.append("")
    W.append("### 1.1 信号桶 × 信源等级交叉表")
    W.append("")
    W.append("| 信号桶 | T1 | T2 | T3 | 警告 |")
    W.append("|---|---|---|---|---|")
    for bid, b in cfg["buckets"].items():
        c = cross[bid]
        warn = "⚠️ 全 T3（官方一手缺失）" if bid in all_t3 else ""
        W.append(f"| {b['label']} | {c.get('T1', 0)} | {c.get('T2', 0)} | {c.get('T3', 0)} | {warn} |")
    W.append("")
    # §2 数值证据板
    W.append("## 2. 数值证据板（凡抽出数字都列示，kind 供甄别口径）")
    W.append("")
    W.append("| 日期 | 数值 | 口径句（context） | kind ｜ 来源/等级 | 原文 |")
    W.append("|---|---|---|---|---|")
    met_recs = [x for x in records if x.get("metrics")]
    for r in sorted(met_recs, key=lambda x: x.get("published") or "", reverse=True):
        for p in (r.get("metrics") or []):
            ctx = p["context"].replace("|", "/").replace("\n", " ")
            W.append(f"| {r['published'][:10] if r['published'] else '—'} "
                     f"| {_metric_cell(p)} | {ctx} "
                     f"| {KIND_LABEL[r['kind']]} ｜ {r['publisher']} / {r['tier']} | {_row_link(r)} |")
    if not met_recs:
        W.append("| — | 本窗口未捕获含数值的报道 | — | — | — |")
    W.append("")
    # §3 汇率
    W.append("## 3. 里亚尔与汇率")
    W.append("")
    W.append("### 3.1 汇率基线（手工快照，2026-09-19 录入；官方/自由市场两套口径禁止混用）")
    W.append("")
    W.append("| 日期 | 事项 | 等级 | 来源 |")
    W.append("|---|---|---|---|")
    for b in cfg.get("fx_baseline", []):
        W.append(f"| {b['date']} | {b['event'][:90]} | {b['tier']} | {b['source'][:40]} |")
    W.append("")
    W.append("### 3.2 窗口内新增汇率动向")
    W.append("")
    fx = [r for r in records if "rial_fx" in r["buckets"]]
    _sect(W, fx, "fx")
    # §4 物价
    W.append("## 4. 物价与民生（食品/药品/工资）")
    W.append("")
    W.append("### 4.1 物价基线（手工快照）")
    W.append("")
    W.append("| 日期 | 事项 | 等级 | 来源 |")
    W.append("|---|---|---|---|")
    for b in cfg.get("cpi_baseline", []):
        W.append(f"| {b['date']} | {b['event'][:90]} | {b['tier']} | {b['source'][:40]} |")
    W.append("")
    W.append("### 4.2 窗口内新增物价动向")
    W.append("")
    pf = [r for r in records if "prices_food" in r["buckets"]]
    _sect(W, pf, "prices")
    # §5 就业
    W.append("## 5. 就业与失业（含青年失业）")
    W.append("")
    W.append("### 5.1 就业基线（手工快照）")
    W.append("")
    W.append("| 日期 | 事项 | 等级 | 来源 |")
    W.append("|---|---|---|---|")
    for b in cfg.get("jobs_baseline", []):
        W.append(f"| {b['date']} | {b['event'][:90]} | {b['tier']} | {b['source'][:40]} |")
    W.append("")
    W.append("### 5.2 窗口内新增就业动向")
    W.append("")
    jb = [r for r in records if "unemployment" in r["buckets"]]
    _sect(W, jb, "jobs")
    # §6 原油出口
    W.append("## 6. 原油出口（两套口径并列：美方/跟踪机构 vs 伊朗官媒）")
    W.append("")
    W.append("### 6.1 出口基线（手工快照）")
    W.append("")
    W.append("| 日期 | 事项 | 等级 | 来源 |")
    W.append("|---|---|---|---|")
    for b in cfg.get("oil_baseline", []):
        W.append(f"| {b['date']} | {b['event'][:90]} | {b['tier']} | {b['source'][:40]} |")
    W.append("")
    W.append("### 6.2 窗口内新增出口动向")
    W.append("")
    ol = [r for r in records if "oil_exports" in r["buckets"]]
    _sect(W, ol, "oil")
    # §7 燃料限电
    W.append("## 7. 燃料短缺与限电")
    W.append("")
    W.append("### 7.1 燃料/限电基线（手工快照）")
    W.append("")
    W.append("| 日期 | 事项 | 等级 | 来源 |")
    W.append("|---|---|---|---|")
    for b in cfg.get("fuel_baseline", []):
        W.append(f"| {b['date']} | {b['event'][:90]} | {b['tier']} | {b['source'][:40]} |")
    W.append("")
    W.append("### 7.2 窗口内新增燃料/限电动向")
    W.append("")
    fp = [r for r in records if "fuel_power" in r["buckets"]]
    _sect(W, fp, "fuel")
    # §8 权力结构
    W.append("## 8. 文官政府 vs 革命卫队（权力结构稳定性）")
    W.append("")
    W.append("### 8.1 权力结构基线（手工快照）")
    W.append("")
    W.append("| 日期 | 事项 | 等级 | 来源 |")
    W.append("|---|---|---|---|")
    for b in cfg.get("power_baseline", []):
        W.append(f"| {b['date']} | {b['event'][:90]} | {b['tier']} | {b['source'][:40]} |")
    W.append("")
    W.append("### 8.2 窗口内新增权势结构动向")
    W.append("")
    pw = [r for r in records if "irgc_power" in r["buckets"]]
    if pw:
        for r in sorted(pw, key=lambda x: x.get("published") or "", reverse=True)[:30]:
            ents = "、".join(dict.fromkeys(e for g in r["entities"].values() for e in g)) or "—"
            st = STANCE_LABEL.get(r.get("stance"), "—")
            W.append(f"- `{r['published'][:10] if r['published'] else '—'}` "
                     f"[{KIND_LABEL[r['kind']]}·{st}] {r['title']} — 实体: {ents} "
                     f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
    else:
        W.append("（本窗口无权势结构条目——『无条目≠无活动』，历史见累积档 ALL-records.jsonl）")
    W.append("")
    # §9 领袖
    W.append("## 9. 最高领袖：公开露面 / 权威重塑 / 继承")
    W.append("")
    W.append("> 公开露面（appearance）与继承/权威（succession）为机械子信号，逐条附原文核对；")
    W.append("> 领袖官网（khamenei.ir）经当前出口不可达，露面记录全经 T3 转述，完整性有缺口。")
    W.append("")
    kh = [r for r in records if "khamenei_leader" in r["buckets"]]
    if kh:
        for r in sorted(kh, key=lambda x: x.get("published") or "", reverse=True)[:30]:
            fl = []
            if (r.get("flags") or {}).get("appearance"): fl.append("露面")
            if (r.get("flags") or {}).get("succession"): fl.append("继承/权威")
            ents = "、".join(dict.fromkeys(e for g in r["entities"].get("iran_figures", []) for e in g)) or "—"
            W.append(f"- `{r['published'][:10] if r['published'] else '—'}` "
                     f"[{KIND_LABEL[r['kind']]}] {r['title']} — 人物: {ents} "
                     f"｜ 子信号: {'/'.join(fl) or '—'} "
                     f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
    else:
        W.append("（本窗口无领袖条目——『无条目≠无活动』，历史见累积档 ALL-records.jsonl）")
    W.append("")
    # §10 街头
    W.append("## 10. 街头与抗议")
    W.append("")
    pr = [r for r in records if "protest" in r["buckets"]]
    _sect(W, pr, "protest")
    # §11 无日期
    W.append("## 11. 无日期条目（不跨轮累积，仅本轮披露）")
    W.append("")
    if undated:
        for r in undated[:40]:
            W.append(f"- （日期未知）{r['title']} ｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
        W.append(f"\n共 {len(undated)} 条；纪元零值已按『日期未知』处理。")
    else:
        W.append("（无）")
    W.append("")
    # §12 已知偏差
    W.append("## 12. 已知偏差与局限")
    W.append("")
    for i, x in enumerate(cfg["report"]["known_limitations"], 1):
        W.append(f"{i}. {x}")
    W.append(f"{len(cfg['report']['known_limitations'])+1}. 停用源留档见附录A；"
             f"被锚定拦截的候选 {len(audit.get('anchor_no_bucket', []))} 条已留档 `_filter_audit.json`。")
    W.append("")
    # 附录
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
    W.append("- **T1** 原始出处（本监测期零可用——官方站全被拦）；**T2** 机构分析（Atlantic Council）；")
    W.append("  **T3** 媒体转述（Tehran Times 官媒窗口/国际媒体/Google News 聚合层）；**T4** 自媒体——不采用。")
    W.append("- 事实（数据发布/完成态事件）≠ 机械抽取（数值定位）≠ 启发式判断（压力/缓解词表）。")
    W.append("- 『某方声称X』是关于该声称的**事实**；X 本身真伪是另一层（单方口径需回源）。")
    W.append("- 波斯历月份（Mordad=7/23-8/22、Shahrivar=8/23-9/21）按转述语境对应公历月，基线表逐条标注。")
    W.append("")
    return "\n".join(W)

def _sect(W, rows, tag):
    if rows:
        for r in sorted(rows, key=lambda x: x.get("published") or "", reverse=True)[:30]:
            ents = "、".join(dict.fromkeys(e for g in r["entities"].values() for e in g)) or "—"
            st = STANCE_LABEL.get(r.get("stance"), "—")
            W.append(f"- `{r['published'][:10] if r['published'] else '—'}` "
                     f"[{KIND_LABEL[r['kind']]}·{st}] {r['title']} — 实体: {ents} "
                     f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
    else:
        W.append(f"（本窗口无{tag}条目——『无条目≠无活动』，历史见累积档 ALL-records.jsonl）")
    W.append("")

# ---------------------------------------------------------------- main
def write_outputs(cfg, records, undated, audit, stats, as_of, outdir, verify_note):
    dd = os.path.join(outdir, as_of.isoformat())
    os.makedirs(dd, exist_ok=True)
    rep = render_report(cfg, records, undated, audit, stats, as_of, verify_note, outdir)
    p_rep = os.path.join(dd, f"iran-domestic-{as_of.isoformat()}.md")
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

def stats_header(cfg, eng, recs, enabled, archive, as_of):
    ens = enabled if enabled else [s for s in cfg["sources"] if s.get("enabled")]
    return {"n_enabled": len(ens),
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
            r.setdefault("metrics", []); r.setdefault("kind", "opinion_analysis")
            r.setdefault("stance", "unclassified"); r.setdefault("tier", "T3")
            r.setdefault("publisher", "?"); r.setdefault("url", "")
            r.setdefault("flags", {})
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
    for r in recs + undated:
        r.setdefault("buckets", []); r.setdefault("entities", {})
        r.setdefault("metrics", []); r.setdefault("kind", "opinion_analysis")
        r.setdefault("stance", "unclassified"); r.setdefault("tier", "T3")
        r.setdefault("publisher", "?"); r.setdefault("url", ""); r.setdefault("title", "")
        r.setdefault("flags", {})
    stats = stats_header(cfg, eng, recs, enabled, archive, as_of)
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

    # --- 词形族 ---
    for w, forms in [("plunge", ["plunges", "plunged", "plunging"]),
                     ("ration", ["rations", "rationed", "rationing"]),
                     ("blackout", ["blackouts"]),
                     ("crackdown", ["crackdowns"]),
                     ("arrest", ["arrests", "arrested", "arresting"]),
                     ("shortage", ["shortages"]),
                     ("devaluation", ["devaluations"])]:
        rx = term_re(w)
        ck(f"词形族 {w}", all(rx.search(f) for f in [w] + forms))
    # --- 日期 ---
    ck("RFC822 全称", parse_any_date("Sat, 13 Sep 2026 09:00:00 +0000") == ("2026-09-13", False))
    ck("两位数年", parse_any_date("Wed, 29 Jul 26 12:37:14 +0200") == ("2026-07-29", False))
    ck("纪元零值=未知", parse_any_date("Thu, 01 Jan 1970 00:00:00 +0000") == (None, True))
    ck("ISO", parse_any_date("2026-09-12T10:00:00Z")[0] == "2026-09-12")
    ck("年份离谱判失败", parse_any_date("13 Sep 99 10:00:00 +0000") == (None, False))
    # --- 数值抽取 fx ---
    ex = eng.extract_metrics("The dollar hit 2.3 million rials on the free market, traders said.")
    ck("fx 2.3M rial", ex and any(m["kind"] == "fx" and abs(m["rial_per_usd"] - 2.3e6) < 1 for m in ex))
    ex = eng.extract_metrics("USD sold at 230,600 toman in Tehran's market.")
    ck("fx 230600 toman→2.306M rial", ex and any(
        m["kind"] == "fx" and m["unit"] == "toman" and abs(m["rial_per_usd"] - 2.306e6) < 1 for m in ex))
    ex = eng.extract_metrics("The official rate stands at 1,374,600 rials per dollar.")
    ck("fx 1,374,600 逗号数字", ex and any(
        m["kind"] == "fx" and abs(m["rial_per_usd"] - 1374600) < 1 for m in ex))
    ex = eng.extract_metrics("Bread costs 10,000 rials a loaf in Isfahan bakeries.")
    ck("fx 非美元语境不收", not any(m["kind"] == "fx" for m in ex))
    ex = eng.extract_metrics("The Omani rial rose against the dollar as Iran talks continued.")
    ck("fx 海湾里亚尔排除", not any(m["kind"] == "fx" for m in ex))
    # --- 数值抽取 pct ---
    ex = eng.extract_metrics("Iran's annual inflation reached 69.9 percent in Mordad.")
    ck("pct 69.9 通胀", any(m["kind"] == "pct" and m["value"] == 69.9 and m["topic"] == "inflation" for m in ex))
    ex = eng.extract_metrics("Food inflation hit 127.5 percent, double the headline rate.")
    ck("pct 127.5 食品（>100 放行）", any(m["kind"] == "pct" and m["value"] == 127.5 for m in ex))
    ex = eng.extract_metrics("Youth unemployment stands at 23.4%, the center said.")
    ck("pct 23.4 失业", any(m["kind"] == "pct" and m["value"] == 23.4 and m["topic"] == "unemployment" for m in ex))
    # --- 数值抽取 bbl ---
    ex = eng.extract_metrics("Iran shipped 1.5 million barrels per day before the war.")
    ck("bbl 1.5M 桶/日", any(m["kind"] == "bbl" and m["per_day"] and abs(m["value"] - 1.5e6) < 1 for m in ex))
    ex = eng.extract_metrics("Tehran holds roughly 30 million barrels of unsold crude, Bessent said.")
    ck("bbl 30M 存量（无/日标注）", any(m["kind"] == "bbl" and not m["per_day"] and abs(m["value"] - 3e7) < 1 for m in ex))
    # --- 桶命中 ---
    r1 = eng.classify("Iranian rial slumps to record low against US dollar", "")
    ck("里亚尔→rial_fx", r1 and "rial_fx" in r1["buckets"])
    r1b = eng.classify("Omani rial steady as Iran talks resume in Muscat", "")
    ck("海湾里亚尔不进 rial_fx", r1b is None or r1b.get("anchor_only") or "rial_fx" not in r1b["buckets"])
    r1c = eng.classify("P2P to IRR: Convert P2P to Iranian Rial | Live P2P Price in IRR", "")
    ck("兑换工具页不进 rial_fx", r1c is None or r1c.get("anchor_only") or "rial_fx" not in r1c["buckets"])
    r1d = eng.classify("Iran rial plunges to record low", "Convert worries grow among traders.")
    ck("正文裸 convert 不误杀真汇率", r1d and "rial_fx" in r1d["buckets"])
    r1e = eng.classify("Iran gasoline rationing tightens in Tehran", "Families in Dhaka also feel the war's toll.")
    ck("孟加拉提及不误杀伊朗燃料报道", r1e and "fuel_power" in r1e["buckets"])
    r1f = eng.classify("The energy crisis hits Bangladesh as Iran war disrupts gas imports", "Dhaka endures daily power cuts.")
    ck("孟加拉主语排除出 fuel_power", r1f is None or r1f.get("anchor_only") or "fuel_power" not in r1f["buckets"])
    r2 = eng.classify("Iran food inflation hits 128% as bread prices soar", "Households cut meat purchases, officials said.")
    ck("物价→prices_food（标题数字完成态→fact_event）",
       r2 and "prices_food" in r2["buckets"] and r2["kind"] == "fact_event")
    r3 = eng.classify("Iran youth unemployment reaches 23.4 percent, Statistical Center says", "Spring labor force survey released.")
    ck("失业→unemployment", r3 and "unemployment" in r3["buckets"])
    r4 = eng.classify("Iran crude exports near zero as TankerTrackers reports no barrels crossing blockade", "Shadow fleet tankers idle, oil revenue collapse.")
    ck("原油出口→oil_exports（二维）", r4 and "oil_exports" in r4["buckets"])
    r4b = eng.classify("IRGC commander killed in US strike on Kharg island", "")
    ck("纯军事打击不进 oil_exports", r4b is None or r4b.get("anchor_only") or "oil_exports" not in r4b["buckets"])
    r5 = eng.classify("Long queues at gas stations after Iran gasoline price hike", "Fuel rationing quotas tightened in Tehran.")
    ck("燃料→fuel_power", r5 and "fuel_power" in r5["buckets"] and r5["kind"] == "fact_event")
    r6 = eng.classify("IRGC members unpaid for months as economy buckles, US officials say", "Crackdown feared as Guards lose payroll.")
    ck("IRGC 权势→irgc_power（二维）", r6 and "irgc_power" in r6["buckets"])
    r6b = eng.classify("IRGC navy claims mine stopped tanker in Hormuz", "")
    ck("纯军事 IRGC 不进权势桶", r6b is None or r6b.get("anchor_only") or "irgc_power" not in r6b["buckets"])
    r7 = eng.classify("Khamenei receives officials in rare public appearance", "Supreme leader attended a ceremony in Tehran.")
    ck("领袖→khamenei_leader + 露面子信号", r7 and "khamenei_leader" in r7["buckets"]
       and r7["flags"].get("appearance") is True)
    r7b = eng.classify("Debate over Khamenei successor intensifies as Mojtaba rises", "")
    ck("继承子信号", r7b and r7b["flags"].get("succession") is True)
    r8 = eng.classify("Thousands protest in Tehran over water and power cuts", "")
    ck("抗议→protest", r8 and not r8.get("anchor_only") and "protest" in r8["buckets"])
    r9 = eng.classify("Quarterly futures market commentary", "Brent crude technical analysis.")
    ck("无锚定=不收", r9 is None)
    r10 = eng.classify("Iranian cinema wins festival award", "The film received acclaim from Persian critics.")
    ck("锚定无桶=拦截候选", r10 and r10.get("anchor_only") is True)
    # --- kind 顺序 ---
    rd = eng.classify("Iran's Statistical Center said annual inflation reached 69.9 percent in Mordad",
                      "Point-to-point inflation was 89 percent.")
    ck("数据发布→fact_data", rd and rd["kind"] == "fact_data")
    re_ = eng.classify("Iran doubles third-tier gasoline price to 10,000 toman", "Motorcycle couriers hit by fuel price rise.")
    ck("涨价执行→fact_event（fuel 桶）", re_ and re_["kind"] == "fact_event" and "fuel_power" in re_["buckets"])
    rs = eng.classify("Economist warns Iran approaching hyperinflation territory", "Masoud Nili said chronic inflation has worsened.")
    ck("学者警告→opinion_statement", rs and rs["kind"] == "opinion_statement")
    ra = eng.classify("Iran's currency crisis reflects deeper structural decay", "The rial's trajectory mirrors institutional erosion.")
    ck("分析→opinion_analysis", ra and ra["kind"] == "opinion_analysis")
    # --- 立场/否定 ---
    ck("立场压力 plunge", eng.stance_of("The rial plunged to a record low.") == "escalation_signal")
    ck("立场缓解 stabilize", eng.stance_of("The market began to stabilize and recover.") == "deescalation_signal")
    ck("否定翻转 not crisis", eng.stance_of("Officials insist the economy is not in crisis.") == "deescalation_signal")
    # --- March 月份守卫 / 抗议排除 ---
    rm = eng.classify("Casualties reported in March 2026 during the war", "A service member died on March at a base near Isfahan.")
    ck("March 月份不进抗议桶", rm is None or rm.get("anchor_only") or "protest" not in rm["buckets"])
    rm2 = eng.classify("Thousands march in Tehran against fuel price hike", "Protesters rallied in Isfahan.")
    ck("动词 march 进抗议桶", rm2 is not None and not rm2.get("anchor_only") and "protest" in rm2["buckets"])
    rk = eng.classify("Minister says he won't join protests during summit in Tehran", "")
    ck("won't join protests 不进抗议桶", rk is None or rk.get("anchor_only") or "protest" not in rk["buckets"])
    # --- gnews 后缀折叠/链接归一 ---
    ck("gnews 后缀折叠", title_key("Rial slumps - Reuters") ==
       title_key("Rial slumps - The Business Standard"))
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
    # --- 修剪备份 ---
    _tmp_prune = {"a": {"published": "2020-01-01", "t": "x"},
                  "b": {"published": "2026-09-01"}, "c": {}}
    _bp = os.path.join(os.environ.get("TEMP", "/tmp"), "_prune_test_irandom.jsonl")
    ck("prune 超龄删除（含备份）",
       prune_archive(_tmp_prune, date(2026, 9, 19), 120, backup_path=_bp) == 1
       and _tmp_prune["b"]["published"] == "2026-09-01"
       and "2020-01-01" in open(_bp, encoding="utf-8").read())
    # --- gnews 复读双抽去重 ---
    dd = eng.extract_metrics("Inflation hit 69.9 percent. Inflation hit 69.9 percent.")
    ck("同数值复读去重", sum(1 for m in dd if m["kind"] == "pct" and m["value"] == 69.9) == 1)
    # --- 数字实体解码 / 词形 ---
    ck("数字实体解码", strip_tags("died &#8230; later") == "died … later")
    ck("justify 词形族", term_re("justify").search("justified"))
    # --- 回填掉桶 → excluded ---
    arch2 = {"x": {"id": "x", "published": "2026-09-15", "date_unknown": False,
                   "title": "Iranian film discussion",
                   "summary": "Cinema talk continued in Isfahan.",
                   "kind": "fact_event", "buckets": ["protest"], "cfg_ver": "OLD"},
             "y": {"id": "y", "published": "2026-09-16", "date_unknown": False,
                   "title": "Regional summit minutes",
                   "summary": "Talks concluded.",
                   "kind": "fact_event", "buckets": ["protest"], "cfg_ver": "OLD"}}
    backfill_archive(cfg, eng, arch2)
    ck("回填掉桶(anchor_only)→excluded", arch2["x"]["kind"] == "excluded" and arch2["x"]["buckets"] == [])
    ck("回填掉桶(无锚定)→excluded", arch2["y"]["kind"] == "excluded" and arch2["y"]["buckets"] == [])
    win2, _ = window_filter(list(arch2.values()), date(2026, 9, 19), 14)
    ck("excluded 不进快照", len(win2) == 0)
    # --- 配置不变量 ---
    ck("oil_exports 二维", len(cfg["buckets"]["oil_exports"]["require_all"]) == 2)
    ck("irgc_power 二维", len(cfg["buckets"]["irgc_power"]["require_all"]) == 2)
    ck("所有桶有标签", all(b.get("label") for b in cfg["buckets"].values()))
    ck("每启用源 tier 合法", all(s.get("tier") in ("T1", "T2", "T3")
       for s in cfg["sources"] if s.get("enabled")))
    ck("无启用 T4 源", all(s.get("tier") != "T4" for s in cfg["sources"] if s.get("enabled")))
    ck("停用源有留档", all(s.get("note") for s in cfg["sources"] if not s.get("enabled")))
    for bl in ("fx_baseline", "cpi_baseline", "jobs_baseline", "oil_baseline",
               "fuel_baseline", "power_baseline"):
        ck(f"{bl} 字段完整",
           all(b.get("date") and b.get("event") and b.get("tier") in ("T1", "T2", "T3")
               and b.get("source") for b in cfg[bl]))
    # --- 核心结论能产出（防全绿但结论恒空）---
    ck("核心结论: 数据/事件/表态/分析 四类都能产出",
       rd["kind"] == "fact_data" and re_["kind"] == "fact_event"
       and rs["kind"] == "opinion_statement" and ra["kind"] == "opinion_analysis")

    ok = sum(1 for _, c in P if c)
    for name, c in P:
        print(f"  {'✅' if c else '❌'} {name}")
    print(f"[selftest] {ok}/{len(P)} 通过")
    return 0 if ok == len(P) else 1

if __name__ == "__main__":
    main()
