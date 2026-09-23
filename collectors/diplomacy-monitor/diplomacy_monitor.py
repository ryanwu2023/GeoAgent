#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
diplomacy_monitor.py — 美伊冲突·外交斡旋监测
（卡塔尔/阿曼/巴基斯坦等协调方动向 / 特朗普谈判意愿与能力(TACO) / 军事降温与再升级 /
 伊斯兰堡备忘录动向 / 谈判渠道状态 / 联大周外交窗口）
骨架承继 iran-domestic-monitor（表态/事件类，无数值抽取）：配置驱动源 + 主题锚定 +
实体归属 + tier 分级 + 事实/观点分类 + 立场词表（否定翻转）+ 桶级子信号 flags +
累积档回填（cfg_ver 含代码哈希）+ 离线自检。

用法:
  python diplomacy_monitor.py                # 抓取+渲染
  python diplomacy_monitor.py --selftest     # 离线自检（不联网）
  python diplomacy_monitor.py --as-of 2026-09-20 --out-dir output/_xday-verify
  python diplomacy_monitor.py --render-only  # 仅从累积档重渲染
  python diplomacy_monitor.py --from-file corpus.json  # 离线退路
"""
import argparse, hashlib, html, json, os, re, sys, time, gzip, zlib
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict
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
        if w.endswith("e") and len(w) > 3:
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
        # 锚定：伊朗侧 + 美方侧（斡旋报道通常双提；纯美方事务靠桶词门控）
        alts = ["iran(?:ian)?", "tehran", "farsi", "persian(?:s)?", "irgc", "pasdaran",
                r"revolutionary\s+guards?", "araghchi", "pezeshkian", "khamenei",
                "houthi", "ansar\s+allah",
                "trump", "rubio", "witkoff", r"white\s+house", "washington",
                r"state\s+department", "pentagon", r"u\.s\.", r"united\s+states",
                "american"]
        self.anchor = re.compile(r"\b(?:" + "|".join(alts) + r")\b", re.I)
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
        self.event_re = re.compile(
            r"\b(?:met|meets?|received|visit(?:ed|s|ing)?|travel(?:ed|s|ling)?|"
            r"arriv(?:ed|es|al)?|held|hosts?|hosted|hosting|brokered|brokering|"
            r"signed|signs?|approved|granted|denied|rejected|postponed|postponing|"
            r"issued|delivered|relayed|conveyed|transmitted|resumed|resuming|"
            r"halted|halts?|suspended|launched|struck|attacked|imposed|lifted|"
            r"eased|extended|announced|confirmed|exchanged|proposed|submitted|"
            r"returned|flew|departed|landed|concluded|adjourned|voted|passed)\b", re.I)
        self.quote_re = re.compile(
            r"\b(?:said|told|stated|declared|argued|warned|accused|vowed|promised|"
            r"insisted|urged|calls?|called|denied|acknowledged|claimed|stressed|"
            r"writes|noted|added)\b", re.I)
        self.neg_re = re.compile(r"\b(?:not|no|never|without|neither|nor)\b|n't\b", re.I)
        # 纯简报类标题（MEE 'Morning recap' 实测混入温度桶）：排除只看标题
        self.temp_excl = re.compile(
            r"^(?:good\s+)?(?:morning|evening|daily|today)?\s*"
            r"(?:recap|brief|briefing|round-?up|digest|newsletter|live\s+blog)\b"
            r"|\brecap\s*[:：]", re.I)
        # 无关地理域标题（Trump-Greenland/Denmark 交易实测混入 trump/talks 桶，
        # 其正文会捎带 'amid Iran war' 语境词）：排除只看标题
        self.geo_excl = re.compile(
            r"\bgreenland\b|\bdenmark\b|\barctic\b", re.I)
        # ---- 桶级子信号（flags）----
        self.talk_open_re = re.compile(
            r"\b(?:open\s+to|openness|willing|ready\s+to|prepared\s+to|hopeful|"
            r"want(?:s)?\s+a\s+deal|seek(?:s)?\s+a\s+deal|considering\s+(?:a\s+)?deal)\b", re.I)
        self.talk_close_re = re.compile(
            r"\bnot\s+(?:currently\s+)?consider\w*|won'?t|refus\w+|rules?\s+out|"
            r"no\s+plans|not\s+the\s+time|reject\w*|not\s+what\s+we'?re\s+considering", re.I)
        self.memo_return_re = re.compile(
            r"\breturn\s+to\b|\breviv\w+|\bhonou?r\w*|\bfulfil\w*|"
            r"\bback\s+to\s+the\s+table\b|\bcompl\w+|\bimplement\s+the\b", re.I)
        self.memo_breach_re = re.compile(
            r"\bcollaps\w+|\bbreach\w*|\bviolat\w+|\breneg\w+|walked\s+away|"
            r"\bderail\w*|\bsabotag\w+|\bshredd\w*", re.I)
        self.meeting_re = re.compile(
            r"\bvisit(?:ed|s|ing)?|met|meeting|travel(?:ed|ling)?|arriv\w+|"
            r"\btrip\b|\bmission\b|delegation|talks?\s+with\b", re.I)
        # 愿谈/拒谈的域词：命中点前后 60 字符内须出现谈判词（防 'willing to vote'、
        # “they won't” 等泛命中；真实语料 2026-09-19 审计轮回归用例）
        self.domain_talk = re.compile(
            r"\btalks?|\bnegotiat\w+|\bdialogue\b|\bdeal\b|\bdiplomacy\b", re.I)
        # 完成态意向守卫：事件动词命中点前 32 字符含意向词 → 非完成态
        # （承 mideast intent_re；防 'expected to meet' / 'to visit soon' 误判 fact_event）
        self.intent_re = re.compile(
            r"\b(?:will|to|consider\w*|could|should|would|if|whether|plan\w*|"
            r"seek\w*|offer\w*|propose\w*|expect\w*|aim\w*|set\s+to|due\s+to|"
            r"urged?|appeal\w*|call\w*\s+on|about\s+to)\b", re.I)
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
        其摘要里的打击/停火词会污染温度桶——排除只作用于标题区间（骨架纪律）。"""
        hits = []
        for bid, b in self.buckets.items():
            if title and self.temp_excl.search(title):
                continue   # 纯新闻简报标题无信息量（事件在专门文章中另行捕获）
            if title and self.geo_excl.search(title):
                continue   # Greenland/Denmark 交易不是美伊外交（全桶；俄制裁类靠伊朗语境词组门控）
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
        """升级/缓解两族信号（否定翻转；只看方向）"""
        text = text.replace(chr(0x2019), "'").replace(chr(0x2018), "'")
        ep, en = self._eff(self.esc, text)
        dp, dn = self._eff(self.dec, text)
        e = (ep + dn) > 0
        d = (dp + en) > 0
        if e and not d: return "escalation_signal"
        if d and not e: return "deescalation_signal"
        if e and d: return "mixed_signal"
        return "unclassified"

    def _cooccur(self, rx, text, span=60):
        """rx 命中点前后 span 字符内须出现 domain_talk 域词（双向窗口）。"""
        for m in rx.finditer(text):
            lo, hi = max(0, m.start() - span), min(len(text), m.end() + span)
            if self.domain_talk.search(text[lo:hi]):
                return True
        return False

    def flags_of(self, text, buckets):
        fl = {}
        text = text.replace(chr(0x2019), "'").replace(chr(0x2018), "'")
        if "trump_signals" in buckets:
            fl["talk_open"] = self._cooccur(self.talk_open_re, text)
            fl["talk_close"] = self._cooccur(self.talk_close_re, text)
        if "memo_track" in buckets:
            fl["memo_return"] = bool(self.memo_return_re.search(text))
            fl["memo_breach"] = bool(self.memo_breach_re.search(text))
        if any(b in buckets for b in ("qatar_mediation", "oman_mediation",
                                      "pakistan_mediation", "other_mediators")):
            fl["meeting"] = bool(self.meeting_re.search(text))
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
            kind = "fact_event"          # 完成态事件（访问/会见/签证/签署/推迟/传达）
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
KIND_LABEL = {"fact_event": "事实·事件", "opinion_statement": "观点·表态",
              "opinion_analysis": "观点·分析", "excluded": "已排除"}
STANCE_LABEL = {"escalation_signal": "升温信号", "deescalation_signal": "降温信号",
                "mixed_signal": "双向混存", "unclassified": "未分类"}
FLAG_LABEL = {"talk_open": "愿谈", "talk_close": "拒谈",
              "memo_return": "重回备忘录", "memo_breach": "破坏/违约",
              "meeting": "会晤/访问"}

def _row_link(r):
    return f"[原文]({r['url']})" if r.get("url") else "—"

def _flag_cell(r):
    fl = [FLAG_LABEL[k] for k in (r.get("flags") or {}) if r["flags"].get(k) and k in FLAG_LABEL]
    return "、".join(fl) or "—"

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
    W.append(f"# 美伊冲突·外交斡旋监测日报（{dt}）")
    W.append("")
    W.append("## 0. 阅读须知（先读，再读结论）")
    W.append("")
    W.append("- **事实/观点三分类**：每条记录标注 `kind`：")
    W.append("  - `fact_event` 完成态事件=**事实**（访问/会见/签证批准/签署/推迟/条件传达）；")
    W.append("  - `opinion_statement` 表态=**『他说了X』是事实，X 本身是观点/意向**，附人物归属；")
    W.append("  - `opinion_analysis` 分析解读=**观点**。")
    W.append("- **斡旋的根本模糊性**：『谈判在进行』与『否认谈判』经常同时为真（不同层级/渠道），")
    W.append("  本报告只并列记录不裁决；幕后再大的动静，落不到公开事件上都标记为转述。")
    W.append("- **TACO 是市场叙事不是官方信号**：Signum Global 等机构模型押注特朗普对市场压力让步，")
    W.append("  指数亮起只说明『市场在赌』，不构成降温证据本身（已列入已知偏差）。")
    W.append("- **立场词表是启发式**：升级(escalation) vs 缓解(deescalation)，否定翻转，只看方向不看强度；")
    W.append("  沙特-胡塞等外溢战线事件可能混入，已用第二维（美/伊主体词）收紧。")
    W.append(f"- 本轮配置指纹 `cfg_ver={stats['cfg_ver'][:12]}…`；快照内唯一（闸门断言）。")
    W.append("")
    W.append(f"## 1. 快照头部（{dt}）")
    W.append("")
    W.append(f"- 抓取源 {stats['n_enabled']} 个（T1 {stats['n_t1']} / T2 {stats['n_t2']} / 其余 T3；停用留档 {stats['n_disabled']}）")
    W.append(f"- 本次新增 **{stats['n_new']}** 条；当前快照（窗口 {cfg['window_days']} 天）**{len(records)}** 条")
    W.append(f"- 窗口内信号桶计数：`" + "、".join(
        f"{cfg['buckets'][b]['label']} {n}" for b, n in by_bucket.most_common()) + "`")
    W.append(f"- 事实/观点：`" + "、".join(f"{KIND_LABEL[k]} {n}" for k, n in by_kind.most_common() if k in KIND_LABEL) + "`")
    W.append(f"- 升温/降温分布（启发式）：`" + "、".join(f"{STANCE_LABEL[s]} {n}" for s, n in by_stance.most_common()) + "`")
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

    # ---- §2 协调方 ----
    W.append("## 2. 协调方动向（卡塔尔 / 阿曼 / 巴基斯坦 / 其他）")
    W.append("")
    W.append("### 2.1 协调方基线（手工快照，2026-09-19 录入）")
    W.append("")
    W.append("| 日期 | 事项 | 等级 | 来源 |")
    W.append("|---|---|---|---|")
    for b in cfg.get("mediator_baseline", []):
        W.append(f"| {b['date']} | {b['event'][:110]} | {b['tier']} | {b['source'][:40]} |")
    W.append("")
    for bid, name in [("qatar_mediation", "卡塔尔"), ("oman_mediation", "阿曼"),
                      ("pakistan_mediation", "巴基斯坦"), ("other_mediators", "其他协调方")]:
        rows = [r for r in records if bid in r["buckets"]]
        W.append(f"### 2.{2 + ['qatar_mediation','oman_mediation','pakistan_mediation','other_mediators'].index(bid)} "
                 f"{name}窗口内动向（{len(rows)} 条）")
        W.append("")
        _sect(W, rows)
    # ---- §3 特朗普 ----
    W.append("## 3. 特朗普谈判意愿与能力（含 TACO 市场叙事）")
    W.append("")
    W.append("### 3.1 特朗普表态基线（手工快照）")
    W.append("")
    W.append("| 日期 | 事项 | 等级 | 来源 |")
    W.append("|---|---|---|---|")
    for b in cfg.get("trump_baseline", []):
        W.append(f"| {b['date']} | {b['event'][:110]} | {b['tier']} | {b['source'][:40]} |")
    W.append("")
    ts = [r for r in records if "trump_signals" in r["buckets"]]
    if ts:
        for r in sorted(ts, key=lambda x: x.get("published") or "", reverse=True)[:30]:
            W.append(f"- `{r['published'][:10] if r['published'] else '—'}` "
                     f"[{KIND_LABEL[r['kind']]}·{STANCE_LABEL.get(r.get('stance'), '—')}] "
                     f"{r['title']} — 子信号: {_flag_cell(r)} "
                     f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
    else:
        W.append("（本窗口无特朗普信号条目——『无条目≠无表态』，历史见累积档）")
    W.append("")
    # ---- §4 备忘录 ----
    W.append("## 4. 伊斯兰堡备忘录动向（2026-06 签署，60 天路线图）")
    W.append("")
    W.append("### 4.1 备忘录基线（手工快照；条款全经转述，文本未公开）")
    W.append("")
    W.append("| 日期 | 事项 | 等级 | 来源 |")
    W.append("|---|---|---|---|")
    for b in cfg.get("memo_baseline", []):
        W.append(f"| {b['date']} | {b['event'][:110]} | {b['tier']} | {b['source'][:40]} |")
    W.append("")
    mm = [r for r in records if "memo_track" in r["buckets"]]
    if mm:
        for r in sorted(mm, key=lambda x: x.get("published") or "", reverse=True)[:30]:
            W.append(f"- `{r['published'][:10] if r['published'] else '—'}` "
                     f"[{KIND_LABEL[r['kind']]}] {r['title']} — 子信号: {_flag_cell(r)} "
                     f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
    else:
        W.append("（本窗口无备忘录条目——『无条目≠无动向』，历史见累积档）")
    W.append("")
    # ---- §5 军事温度 ----
    W.append("## 5. 军事温度：降温 vs 再升级（并列披露，不裁决）")
    W.append("")
    W.append("### 5.1 军事基线（手工快照）")
    W.append("")
    W.append("| 日期 | 事项 | 等级 | 来源 |")
    W.append("|---|---|---|---|")
    for b in cfg.get("military_baseline", []):
        W.append(f"| {b['date']} | {b['event'][:110]} | {b['tier']} | {b['source'][:40]} |")
    W.append("")
    de = [r for r in records if "deescalation" in r["buckets"]]
    re_ = [r for r in records if "reescalation_mil" in r["buckets"]]
    W.append(f"### 5.2 降温信号（{len(de)} 条）")
    W.append("")
    _sect(W, de)
    W.append(f"### 5.3 再升级信号（{len(re_)} 条）")
    W.append("")
    _sect(W, re_)
    # ---- §6 谈判渠道 ----
    W.append("## 6. 谈判渠道状态（直接/间接/否认，矛盾口径并列）")
    W.append("")
    W.append("### 6.1 渠道基线（手工快照）")
    W.append("")
    W.append("| 日期 | 事项 | 等级 | 来源 |")
    W.append("|---|---|---|---|")
    for b in cfg.get("talks_baseline", []):
        W.append(f"| {b['date']} | {b['event'][:110]} | {b['tier']} | {b['source'][:40]} |")
    W.append("")
    tc = [r for r in records if "talks_channels" in r["buckets"]]
    _sect(W, tc)
    # ---- §7 联大 ----
    W.append("## 7. 联大周外交窗口（9-22 一般性辩论起，佩泽希齐扬 9-23 演讲）")
    W.append("")
    ug = [r for r in records if "unga_diplomacy" in r["buckets"]]
    _sect(W, ug)
    # ---- §8 无日期 ----
    W.append("## 8. 无日期条目（不跨轮累积，仅本轮披露）")
    W.append("")
    if undated:
        for r in undated[:40]:
            W.append(f"- （日期未知）{r['title']} ｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
        W.append(f"\n共 {len(undated)} 条；纪元零值已按『日期未知』处理。")
    else:
        W.append("（无）")
    W.append("")
    # ---- §9 已知偏差 ----
    W.append("## 9. 已知偏差与局限")
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
    W.append("- **T1** 官方一手（本监测期仅 APP 巴基斯坦联合通讯社；QNA/ONA/Radio Pakistan 停用留档）；")
    W.append("  **T2** 机构分析（ICG / Atlantic Council）；**T3** 媒体转述（含 Tehran Times 官媒窗口、")
    W.append("  Google News 聚合层）；**T4** 自媒体——不采用。")
    W.append("- 事实（完成态事件）≠ 表态（『他说了X』是事实，X 是观点）≠ 启发式判断（立场词表）。")
    W.append("- 『某方声称X』是关于该声称的**事实**；X 本身真伪是另一层（单方口径需回源）。")
    W.append("- 立场子信号（愿谈/拒谈、重回备忘录/破坏）是机械词表命中，逐条附原文核对，非结论。")
    W.append("")
    return "\n".join(W)

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

# ---------------------------------------------------------------- main
def write_outputs(cfg, records, undated, audit, stats, as_of, outdir, verify_note):
    dd = os.path.join(outdir, as_of.isoformat())
    os.makedirs(dd, exist_ok=True)
    rep = render_report(cfg, records, undated, audit, stats, as_of, verify_note, outdir)
    p_rep = os.path.join(dd, f"diplomacy-{as_of.isoformat()}.md")
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

    # --- 词形族 ---
    for w, forms in [("mediat", ["mediates", "mediated", "mediating", "mediation"]),
                     ("negotiat", ["negotiate", "negotiates", "negotiated", "negotiating", "negotiations"]),
                     ("visit", ["visits", "visited", "visiting"]),
                     ("broker", ["brokers", "brokered", "brokering"]),
                     ("postpone", ["postponed", "postpones", "postponing"]),
                     ("revive", ["revived", "revives", "reviving"]) ]:
        rx = term_re(w)
        ck(f"词形族 {w}", all(rx.search(f) for f in [w] + forms))
    # fulfil 英式双写 l（fulfilled/fulfilling）由 memo_return flag 正则 \bfulfil\w* 覆盖
    ck("词形族 fulfil 基形+flags 全覆盖",
       term_re("fulfil").search("fulfil commitments")
       and eng.memo_return_re.search("Washington must fulfil its commitments")
       and eng.memo_return_re.search("fulfilled promises"))
    # memoranda 不规则复数由 config 词条并列覆盖
    ck("memoranda 词条并列覆盖",
       term_re("memoranda").search("memoranda")
       and "memoranda" in cfg["buckets"]["memo_track"]["require_all"][0])
    ck("词形族 memorandum→memoranda 不强求", term_re("memorandum").search("memorandum of understanding") is not None)
    # 连字符变体须两词并列（cease-fire / back-channel）
    ck("ceasefire 连字符双词", term_re("ceasefire").search("ceasefire") and term_re("cease-fire").search("cease-fire"))
    ck("backchannel 连字符双词", term_re("backchannel").search("backchannel") and term_re("back-channel").search("back-channel"))
    # --- 日期 ---
    ck("RFC822 全称", parse_any_date("Sat, 13 Sep 2026 09:00:00 +0000") == ("2026-09-13", False))
    ck("两位数年", parse_any_date("Wed, 29 Jul 26 12:37:14 +0200") == ("2026-07-29", False))
    ck("纪元零值=未知", parse_any_date("Thu, 01 Jan 1970 00:00:00 +0000") == (None, True))
    ck("ISO", parse_any_date("2026-09-12T10:00:00Z")[0] == "2026-09-12")
    ck("年份离谱判失败", parse_any_date("13 Sep 99 10:00:00 +0000") == (None, False))
    # --- 桶命中：协调方 ---
    r1 = eng.classify("Qatar PM travels to Tehran to revive US-Iran talks", "Sheikh Mohammed met Pezeshkian and Araghchi.")
    ck("卡塔尔→qatar_mediation（二维）", r1 and "qatar_mediation" in r1["buckets"])
    ck("卡塔尔会晤子信号", r1 and r1["flags"].get("meeting") is True)
    r2 = eng.classify("Oman and Iran agree framework for temporary Hormuz corridor", "Muscat hosted weeks of shuttle diplomacy.")
    ck("阿曼→oman_mediation（二维）", r2 and "oman_mediation" in r2["buckets"])
    r3 = eng.classify("Pakistan FM holds talks in Islamabad as OIC acknowledges mediation role", "Backchannel messages exchanged with Washington and Tehran.")
    ck("巴基斯坦→pakistan_mediation（二维）", r3 and "pakistan_mediation" in r3["buckets"])
    r4 = eng.classify("Araghchi meets Wang Yi in Beijing to discuss ways to resolve conflict", "China offered to help de-escalate.")
    ck("中国渠道→other_mediators", r4 and "other_mediators" in r4["buckets"])
    r4b = eng.classify("Omani rial steady as Gulf markets rally", "Currency desk commentary.")
    ck("阿曼汇率市场报道不进斡旋桶", r4b is None or r4b.get("anchor_only") or "oman_mediation" not in r4b["buckets"])
    # --- 桶命中：特朗普 ---
    r5 = eng.classify("Trump says he is open to talks with Iran, seeks a deal", "President told reporters the war could wind down.")
    ck("特朗普→trump_signals", r5 and "trump_signals" in r5["buckets"])
    ck("愿谈子信号 talk_open", r5 and r5["flags"].get("talk_open") is True)
    r6 = eng.classify("Trump: resuming negotiations with Iran is not what we're considering right now", "")
    ck("拒谈子信号 talk_close", r6 and r6["flags"].get("talk_close") is True)
    r7 = eng.classify("Wall Street bets on TACO as Signum index flashes de-escalation", "Markets expect Trump to back off strikes on Iran.")
    ck("TACO 报道→trump_signals", r7 and "trump_signals" in r7["buckets"])
    # --- 桶命中：备忘录 ---
    r8 = eng.classify("Iran urges US to honor commitments under Islamabad memorandum", "Tehran says Washington must return to the June deal terms.")
    ck("备忘录→memo_track（二维）", r8 and "memo_track" in r8["buckets"])
    ck("重回备忘录子信号", r8 and r8["flags"].get("memo_return") is True)
    r9 = eng.classify("The June memorandum collapsed within weeks over disputes on shipping channels", "US officials called it a breach by Tehran.")
    ck("备忘录破坏子信号", r9 and r9["flags"].get("memo_breach") is True)
    r9b = eng.classify("Company signs memorandum with Iranian chamber of commerce on trade fair", "Business delegation visit concluded.")
    ck("无关备忘录（缺第二维）不进 memo_track", r9b is None or r9b.get("anchor_only") or "memo_track" not in r9b["buckets"])
    # --- 桶命中：军事温度 ---
    r10 = eng.classify("US and Iran agree to ceasefire after weeks of indirect talks", "Washington and Tehran step back from the brink.")
    ck("降温→deescalation（二维）", r10 and "deescalation" in r10["buckets"])
    r11 = eng.classify("Gaza ceasefire holds for another week", "Mediators praised the truce extension.")
    ck("加沙停火（无美/伊主体）不进降温桶", r11 is None or r11.get("anchor_only") or "deescalation" not in r11["buckets"])
    r12 = eng.classify("Trump weighs major decision on whether to resume massive strikes against Iran", "Military action options on the table, Pentagon says.")
    ck("再升级→reescalation_mil（二维）", r12 and "reescalation_mil" in r12["buckets"])
    r12b = eng.classify("Iranians attack military targets in retaliation", "Tehran launched drones at bases.")
    ck("伊朗主语打击不进再升级桶（第二维=美方）", r12b is None or r12b.get("anchor_only") or "reescalation_mil" not in r12b["buckets"])
    # --- 桶命中：渠道/联大 ---
    r13 = eng.classify("Iran denies talks with US, says only negotiating with Oman on corridor", "No direct talks scheduled, officials said.")
    ck("渠道→talks_channels", r13 and "talks_channels" in r13["buckets"])
    r14 = eng.classify("US approves visas for Iranian delegation to UN General Assembly", "Pezeshkian to address General Assembly in New York.")
    ck("联大→unga_diplomacy", r14 and "unga_diplomacy" in r14["buckets"])
    # --- 噪声排除（真实语料回归用例）---
    rg1 = eng.classify("Trump 'settled for less' with new US-Denmark Greenland security deal",
                       "The US-Denmark Greenland security deal gives Trump an agreement without delivering his ambition to annex the territory.")
    ck("Greenland 交易不进 trump_signals", rg1 is None or rg1.get("anchor_only")
       or "trump_signals" not in rg1["buckets"])
    rg2 = eng.classify("US and Denmark negotiated Greenland deal in secret for months",
                       "The secret talks were confirmed by officials in Washington and Copenhagen.")
    ck("Greenland 谈判不进 talks_channels", rg2 is None or rg2.get("anchor_only")
       or "talks_channels" not in rg2["buckets"])
    rg3 = eng.classify("Morning recap", "Trump signed legislation extending sanctions on Iran; explosions heard in Riyadh; strikes continued.")
    ck("Morning recap 不进任何桶", rg3 is None or rg3.get("anchor_only") or not rg3["buckets"])
    rg4 = eng.classify("US and Iran agree ceasefire after strikes pause", "Morning recap of the truce.")
    ck("真降温报道标题无简报词不误杀", rg4 and "deescalation" in rg4["buckets"])
    # --- TASS/RS 新源噪声回归（2026-09-19 审计轮）---
    rg5 = eng.classify("This isn't sci-fi, AI is already killing people",
                       "Pentagon officials debated how AI adoption will change military doctrine; proponents argue the military's adoption of AI will reduce attacks on civilians.")
    ck("AI 话题不进温度桶（无伊朗语境）", rg5 is None or rg5.get("anchor_only")
       or "reescalation_mil" not in rg5["buckets"])
    rg6 = eng.classify("Trump signs sweeping Russia sanctions over Ukraine war",
                       "New law allows tariffs of up to 100 percent on major buyers of Russian oil, including China and India.")
    ck("对俄制裁不含伊朗语境不进 trump_signals", rg6 is None or rg6.get("anchor_only")
       or "trump_signals" not in rg6["buckets"])
    rg7 = eng.classify("Trump signs Russia sanctions bill into law: White House",
                       "Law authorizes, expands statutory sanctions, tariffs, prohibitions on Russia and extends existing sanctions on Iran.")
    ck("对俄制裁+摘要含对伊延长=真相关保留", rg7 and "trump_signals" in rg7["buckets"])
    rg8 = eng.classify("Denmark accepts Greenland deal due to lack of defense funds, Rubio says",
                       "\"Denmark and Greenland understand that they don't have the financial means necessary to defend Greenland,\" US Secretary of State Marco Rubio said.")
    ck("Greenland+Rubio 不进温度桶", rg8 is None or rg8.get("anchor_only")
       or "reescalation_mil" not in rg8["buckets"])
    rg9 = eng.classify("US envoy to UN confirms Trump-Rodriguez meeting on General Assembly sidelines",
                       "According to the US Ambassador to the UN Mike Waltz, the meeting will be a casual pull-aside conversation rather than a formal bilateral negotiation.")
    ck("Trump-哥伦比亚会晤不进 trump_signals", rg9 is None or rg9.get("anchor_only")
       or "trump_signals" not in rg9["buckets"])
    rg10 = eng.classify("US and Houthi ceasefire holds for second week", "Shipping resumed through the straits.")
    ck("胡塞降温保留（伊朗语境组内）", rg10 and "deescalation" in rg10["buckets"])
    # --- 锚定 ---
    r15 = eng.classify("Quarterly earnings beat expectations", "Tech shares rallied.")
    ck("无锚定=不收", r15 is None)
    r16 = eng.classify("Washington sounds out allies on trade policy", "Officials toured European capitals.")
    ck("锚定无桶=拦截候选", r16 and r16.get("anchor_only") is True)
    # --- kind 顺序 ---
    re1 = eng.classify("Qatar's PM travelled to Tehran and met Pezeshkian", "The visit concluded on Tuesday.")
    ck("完成态访问→fact_event", re1 and re1["kind"] == "fact_event")
    rs1 = eng.classify("Araghchi says Iran will respond immediately if US returns to June deal", "Foreign minister urged Washington to honor commitments under the memorandum.")
    ck("表态→opinion_statement", rs1 and rs1["kind"] == "opinion_statement")
    # --- 意向语态守卫（2026-09-19 审计轮：83 条 fact_event 可疑的回归用例）---
    ri1 = eng.classify("Japan's Takaichi expected to meet Trump Tuesday ahead of US-China talks", "Officials said the agenda includes Iran war energy costs.")
    ck("expected to meet ≠ 完成态", ri1 is None or ri1["kind"] != "fact_event")
    ri2 = eng.classify("UN chief to visit Pakistan 'soon', hails Islamabad's role in US-Iran mediation", "The plan was welcomed by senior diplomats.")
    ck("to visit soon ≠ 完成态", ri2 is None or ri2["kind"] != "fact_event")
    ri3 = eng.classify("Iran says efforts under way to return to Islamabad agreement", "Tehran urged Washington to honor the June memorandum terms.")
    ck("efforts to return ≠ 完成态", ri3 is None or ri3["kind"] != "fact_event")
    ri4 = eng.classify("Qatar's PM travelled to Tehran and met Pezeshkian", "The visit concluded on Tuesday.")
    ck("真实完成态不被意向守卫误杀", ri4 and ri4["kind"] == "fact_event")
    # --- flags 共现门控（真实语料回归）---
    rf1 = eng.classify("'People don't want Iran to have nuclear weapon': Trump defends higher oil prices",
                       "Trump said the increase in oil prices was a sacrifice worth making. \"I'd be willing to vote that it'd be a massive landslide. And you know what, they won't.\"")
    ck("willing to vote / they won't 不触发 flags",
       rf1 is None or not rf1["flags"].get("talk_open") and not rf1["flags"].get("talk_close"))
    rf2 = eng.classify("Trump says he is open to talks with Iran, seeks a deal", "President told reporters the war could wind down.")
    ck("open to talks 仍触发 talk_open", rf2 and rf2["flags"].get("talk_open") is True)
    ra1 = eng.classify("The US-Iran ceasefire appears increasingly fragile", "The mediation track seems stuck in a holding pattern.")
    ck("分析→opinion_analysis", ra1 and ra1["kind"] == "opinion_analysis")
    # --- 立场/否定 ---
    ck("立场降温 ceasefire", eng.stance_of("The two sides agreed a ceasefire and will resume talks.") == "deescalation_signal")
    ck("立场升级 strike", eng.stance_of("US strikes hit the island before dawn.") == "escalation_signal")
    ck("否定翻转 no talks", eng.stance_of("Officials insist there will be no talks with Washington.") == "escalation_signal")
    ck("双向混存", eng.stance_of("Strikes continued even as both sides discussed a possible deal.") == "mixed_signal")
    # --- gnews 后缀折叠/链接归一 ---
    ck("gnews 后缀折叠", title_key("Qatar urges talks - Reuters") ==
       title_key("Qatar urges talks - The Business Standard"))
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
    _bp = os.path.join(os.environ.get("TEMP", "/tmp"), "_prune_test_diplo.jsonl")
    ck("prune 超龄删除（含备份）",
       prune_archive(_tmp_prune, date(2026, 9, 19), 120, backup_path=_bp) == 1
       and _tmp_prune["b"]["published"] == "2026-09-01"
       and "2020-01-01" in open(_bp, encoding="utf-8").read())
    # --- 回填掉桶 → excluded ---
    arch2 = {"x": {"id": "x", "published": "2026-09-15", "date_unknown": False,
                   "title": "Iranian film discussion",
                   "summary": "Cinema talk continued in Isfahan.",
                   "kind": "fact_event", "buckets": ["deescalation"], "cfg_ver": "OLD"},
             "y": {"id": "y", "published": "2026-09-16", "date_unknown": False,
                   "title": "Regional summit minutes",
                   "summary": "Talks concluded.",
                   "kind": "fact_event", "buckets": ["talks_channels"], "cfg_ver": "OLD"}}
    backfill_archive(cfg, eng, arch2)
    ck("回填掉桶(anchor_only)→excluded", arch2["x"]["kind"] == "excluded" and arch2["x"]["buckets"] == [])
    ck("回填掉桶(无锚定)→excluded", arch2["y"]["kind"] == "excluded" and arch2["y"]["buckets"] == [])
    win2, _ = window_filter(list(arch2.values()), date(2026, 9, 19), 30)
    ck("excluded 不进快照", len(win2) == 0)
    # --- 配置不变量 ---
    ck("协调方四桶全二维", all(len(cfg["buckets"][b]["require_all"]) == 2
       for b in ("qatar_mediation", "oman_mediation", "pakistan_mediation", "other_mediators")))
    ck("军事温度两桶含伊朗语境组", all(len(cfg["buckets"][b]["require_all"]) >= 2
       and any("iran" in t for g in cfg["buckets"][b]["require_all"] for t in g)
       for b in ("deescalation", "reescalation_mil")))
    ck("memo/unga 二维", all(len(cfg["buckets"][b]["require_all"]) == 2
       for b in ("memo_track", "unga_diplomacy")))
    ck("所有桶有标签", all(b.get("label") for b in cfg["buckets"].values()))
    ck("每启用源 tier 合法", all(s.get("tier") in ("T1", "T2", "T3")
       for s in cfg["sources"] if s.get("enabled")))
    ck("无启用 T4 源", all(s.get("tier") != "T4" for s in cfg["sources"] if s.get("enabled")))
    ck("停用源有留档", all(s.get("note") for s in cfg["sources"] if not s.get("enabled")))
    for bl in ("mediator_baseline", "trump_baseline", "memo_baseline",
               "military_baseline", "talks_baseline"):
        ck(f"{bl} 字段完整",
           all(b.get("date") and b.get("event") and b.get("tier") in ("T1", "T2", "T3")
               and b.get("source") for b in cfg[bl]))
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
