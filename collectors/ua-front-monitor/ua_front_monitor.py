#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ua_front_monitor.py — 俄乌冲突·军事战线监测
（顿巴斯（顿涅茨克/波克罗夫斯克为主）与哈尔科夫方向等地面战线 / 无人机战 /
 导弹滑翔弹远程打击 / 黑海-亚速海海上战线 / 乌克兰防空能力 / 双方战场声称与核实）
骨架承继 diplomacy-monitor（表态/事件类）：配置驱动源 + 主题锚定 +
实体归属 + tier 分级 + 事实/观点分类 + 立场词表（否定翻转）+ 声称/核实 flags +
意向守卫 + 累积档回填（cfg_ver 含代码哈希）+ 离线自检。

用法:
  python ua_front_monitor.py                # 抓取+渲染
  python ua_front_monitor.py --selftest     # 离线自检（不联网）
  python ua_front_monitor.py --as-of 2026-09-20 --out-dir output/_xday-verify
  python ua_front_monitor.py --render-only  # 仅从累积档重渲染
  python ua_front_monitor.py --from-file corpus.json  # 离线退路
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
        # 锚定：俄乌双方 + 指挥官 + 各桶第一维地理专名（战线报道常只有地名主语）
        alts = ["russia(?:n)?", "moscow", "kremlin", "ukrain(?:e|ian)", "kyiv", "kiev",
                "zelensk(?:y|yy|ii)", "putin", "syrsk(?:iy|y)", "budanov",
                "gerasimov", "afu", "general staff", "wagner", "ukr war"]
        for b in cfg["buckets"].values():
            if b.get("require_all"):
                alts.extend(b["require_all"][0])
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
            r"arriv(?:ed|es|al)?|held|hosts?|hosted|hosting|signed|signs?|"
            r"approved|granted|denied|rejected|postponed|issued|delivered|"
            r"resumed|halted|halts?|suspended|imposed|lifted|eased|extended|"
            r"announced|confirmed|voted|passed|"
            r"launched|struck|struck|attacked|shelled|shelling|stormed|repelled|"
            r"repulsed|downed|intercepted|destroyed|damaged|killed|wounded|injured|"
            r"seized|captured|recaptured|liberated|infiltrated|advanced|crossed|"
            r"hit|hit|targeted|sank|sunk|crashed|shot down|blew up|detonated)\b", re.I)
        self.quote_re = re.compile(
            r"\b(?:said|told|stated|declared|argued|warned|accused|vowed|promised|"
            r"insisted|urged|calls?|called|denied|acknowledged|claimed|stressed|"
            r"writes|noted|added)\b", re.I)
        self.neg_re = re.compile(r"\b(?:not|no|never|without|neither|nor)\b|n't\b", re.I)
        # 纯简报类标题（'Morning recap' 类）：排除只看标题
        self.temp_excl = re.compile(
            r"^(?:good\s+)?(?:morning|evening|daily|today)?\s*"
            r"(?:recap|brief|briefing|round-?up|digest|newsletter|live\s+blog)\b"
            r"|\brecap\s*[:：]", re.I)
        # ---- 声称/核实子信号（flags）：从 config["flags"] 编译（模式即正则）----
        self.flag_res = {k: [re.compile(p, re.I) for p in pats]
                         for k, pats in cfg.get("flags", {}).items()}
        # 完成态意向守卫：事件动词命中点前 32 字符含意向词 → 非完成态
        # （承 mideast intent_re；防 'expected to meet' / 'to visit soon' 误判 fact_attack）
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
        其摘要里的打击词会污染信号桶——排除只作用于标题区间（骨架纪律）。"""
        hits = []
        for bid, b in self.buckets.items():
            if title and self.temp_excl.search(title):
                continue   # 纯新闻简报标题无信息量（事件在专门文章中另行捕获）
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

    def flags_of(self, text, buckets):
        """声称/核实子信号：config["flags"] 中的模式族任一命中即真（全局信号，
        不按桶门控——战线报道的声称纪律与桶无关）。"""
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
            kind = "fact_attack"          # 完成态事件（访问/会见/签证/签署/推迟/传达）
        elif ents or self.quote_re.search(text):
            kind = "opinion_statement"
        else:
            kind = "opinion_analysis"
        stance = "unclassified"
        if kind in ("opinion_statement", "fact_attack"):
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
KIND_LABEL = {"fact_attack": "事实·事件", "opinion_statement": "观点·表态",
              "opinion_analysis": "观点·分析", "excluded": "已排除"}
STANCE_LABEL = {"escalation_signal": "升温信号", "deescalation_signal": "降温信号",
                "mixed_signal": "双向混存", "unclassified": "未分类"}
FLAG_LABEL = {"ru_claim": "俄方声称", "ua_claim": "乌方声称",
              "unverified": "未经核实", "encirclement_alert": "包围/被围表述"}

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
    W.append(f"# 俄乌冲突·军事战线监测日报（{dt}）")
    W.append("")
    W.append("## 0. 阅读须知（先读，再读结论）")
    W.append("")
    W.append("- **事实/观点三分类**：每条记录标注 `kind`：")
    W.append("  - `fact_attack` 完成态军事事件=**事实**（打击/交战/推进/拦截/港口遇袭等；")
    W.append("    注意『完成』指报道的动作为完成时态，其**战果数字仍是单一来源声称**）；")
    W.append("  - `opinion_statement` 表态=**『他说了X』是事实，X 本身是观点/声称**，附人物归属；")
    W.append("  - `opinion_analysis` 分析解读=**观点**。")
    W.append("- **战场声称纪律**：俄乌双方战报均为己方口径（flags 标注 ru_claim/ua_claim），")
    W.append("  推进/包围类表述几乎必然未经独立核实；本报告只并列记录、不裁决，")
    W.append("  ISW 等第三方评估（T2）单独标级，绝不与当事方声称混排。")
    W.append("- **拦截率口径**：『拦截 90%』与『拦截 60%』可能同时为真——分母不同")
    W.append("  （全部来袭目标 vs 喷气式 Geran 单独口径），报告逐条保留原文，禁止相加平均。")
    W.append("- **立场词表是启发式**：升级(escalation) vs 缓解(deescalation)，否定翻转，只看方向不看强度。")
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

    # ---- §2 地面战线 ----
    W.append("## 2. 地面战线（顿巴斯 / 哈尔科夫 / 其他方向，三向并列）")
    W.append("")
    W.append("### 2.1 战线基线（手工快照，2026-09-19 录入；随各期滚动更新）")
    W.append("")
    W.append("| 日期 | 事项 | 等级 | 来源 |")
    W.append("|---|---|---|---|")
    for b in cfg.get("baseline", {}).get("rows", []):
        W.append(f"| {b['date']} | {b['event'][:110]} | {b['tier']} | {b['source'][:40]} |")
    W.append("")
    for bid, name in [("donbas_front", "顿巴斯/堡垒带"), ("kharkiv_front", "哈尔科夫方向"),
                      ("other_fronts", "其他方向")]:
        rows = [r for r in records if bid in r["buckets"]]
        W.append(f"### 2.{2 + ['donbas_front','kharkiv_front','other_fronts'].index(bid)} "
                 f"{name}（{len(rows)} 条）")
        W.append("")
        _sect(W, rows)
    # ---- §3 空中战役 ----
    W.append("## 3. 空中战役：无人机战与远程打击（并列披露，不裁决）")
    W.append("")
    W.append("### 3.1 空中战役基线（手工快照）")
    W.append("")
    W.append("| 日期 | 事项 | 等级 | 来源 |")
    W.append("|---|---|---|---|")
    for b in cfg.get("baseline", {}).get("air_rows", []):
        W.append(f"| {b['date']} | {b['event'][:110]} | {b['tier']} | {b['source'][:40]} |")
    W.append("")
    dw = [r for r in records if "drone_war" in r["buckets"]]
    lr = [r for r in records if "long_range" in r["buckets"]]
    W.append(f"### 3.2 无人机战（{len(dw)} 条）")
    W.append("")
    _sect(W, dw)
    W.append(f"### 3.3 导弹/滑翔弹远程打击（{len(lr)} 条）")
    W.append("")
    _sect(W, lr)
    # ---- §4 海上战线 ----
    W.append("## 4. 黑海/亚速海海上战线")
    W.append("")
    bs = [r for r in records if "black_sea" in r["buckets"]]
    W.append(f"### 4.1 海上战线条目（{len(bs)} 条）")
    W.append("")
    _sect(W, bs)
    # ---- §5 防空 ----
    W.append("## 5. 乌克兰防空能力（拦截率口径逐条保留，禁止相加平均）")
    W.append("")
    ad = [r for r in records if "air_defense" in r["buckets"]]
    W.append(f"### 5.1 防空条目（{len(ad)} 条）")
    W.append("")
    _sect(W, ad)
    # ---- §6 声称与核实 ----
    W.append("## 6. 战场声称与核实（信息战线：双方声称并列，第三方评估单独标级）")
    W.append("")
    ci = [r for r in records if "claims_intel" in r["buckets"]]
    W.append(f"### 6.1 声称/核实条目（{len(ci)} 条）")
    W.append("")
    _sect(W, ci)
    # ---- §7 无日期 ----
    W.append("## 7. 无日期条目（不跨轮累积，仅本轮披露）")
    W.append("")
    if undated:
        for r in undated[:40]:
            W.append(f"- （日期未知）{r['title']} ｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
        W.append(f"\n共 {len(undated)} 条；纪元零值已按『日期未知』处理。")
    else:
        W.append("（无）")
    W.append("")
    # ---- §9 已知偏差 ----
    W.append("## 8. 已知偏差与局限")
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
    W.append("- **T1** 官方一手（本监测期仅 UN 新闻稿；乌总参战报无直接 RSS，经 T3 转述层覆盖）；")
    W.append("  **T2** 机构分析/官方通讯社转写（ICG；Ukrinform 停用留档）；**T3** 媒体转述")
    W.append("  （含 TASS/Moscow Times 俄方窗口、Kyiv Post 乌方窗口、Google News 聚合层）；**T4** 自媒体——不采用。")
    W.append("- 事实（完成态军事事件）≠ 表态（『他说了X』是事实，X 是观点）≠ 启发式判断（立场词表）。")
    W.append("- 『某方声称X』是关于该声称的**事实**；X 本身真伪是另一层（单方口径需回源）；")
    W.append("  flags（ru_claim/ua_claim/unverified/encirclement_alert）是机械词表命中，逐条附原文核对，非结论。")
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
    p_rep = os.path.join(dd, f"uafront-{as_of.isoformat()}.md")
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
    for w, forms in [("storm", ["storms", "stormed", "storming"]),
                     ("intercept", ["intercepts", "intercepted", "intercepting", "interception"]),
                     ("encircl", ["encircle", "encircled"]),
                     ("pocket", ["pockets"]),
                     ("liberat", ["liberate", "liberated", "liberation"]),
                     ("recaptur", ["recapture", "recaptured", "recapturing"]),
                     ("infiltrat", ["infiltrate", "infiltrated", "infiltration"]) ]:
        rx = term_re(w)
        ck(f"词形族 {w}", all(rx.search(f) for f in [w] + forms))
    # 连字符/变体
    ck("ceasefire 连字符双词", term_re("ceasefire").search("ceasefire") and term_re("cease-fire").search("cease-fire"))
    ck("front line 分合写并列", term_re("front line").search("front line") and term_re("frontline").search("frontline"))
    ck("air defense 英美拼写并列", term_re("air defense").search("air defense") and term_re("air defence").search("air defence"))
    ck("Odesa 敖德萨拼写", term_re("odesa").search("Odesa port"))
    # --- 日期 ---
    ck("RFC822 全称", parse_any_date("Sat, 13 Sep 2026 09:00:00 +0000") == ("2026-09-13", False))
    ck("两位数年", parse_any_date("Wed, 29 Jul 26 12:37:14 +0200") == ("2026-07-29", False))
    ck("纪元零值=未知", parse_any_date("Thu, 01 Jan 1970 00:00:00 +0000") == (None, True))
    ck("ISO", parse_any_date("2026-09-12T10:00:00Z")[0] == "2026-09-12")
    ck("年份离谱判失败", parse_any_date("13 Sep 99 10:00:00 +0000") == (None, False))
    # --- 桶命中：地面战线 ---
    r1 = eng.classify("Russian forces stormed Pokrovsk outskirts, fighting reaches Dobropillia",
                      "Assault groups advanced toward the fortress belt; shelling reported overnight.")
    ck("波克罗夫斯克→donbas_front（二维）", r1 and "donbas_front" in r1["buckets"])
    r2 = eng.classify("Russian assault near Kupiansk repelled, bridgehead holds on Oskil river",
                      "Ukrainian defenders repelled attacks toward Borova and Vovchansk, General Staff said.")
    ck("库皮扬斯克→kharkiv_front", r2 and "kharkiv_front" in r2["buckets"])
    r3 = eng.classify("Zaporizhzhia sector shelled as Kursk border incursion continues",
                      "Artillery struck settlements near Sumy and Belgorod, officials said.")
    ck("南部/边境带→other_fronts", r3 and "other_fronts" in r3["buckets"])
    # --- 桶命中：空中战役 ---
    r4 = eng.classify("Russia launches 453 Shahed drones overnight, 405 downed by air defenses",
                      "Jet-powered Geran drones attacked Kyiv and Odesa; kamikaze drones intercepted en masse.")
    ck("无人机战→drone_war", r4 and "drone_war" in r4["buckets"])
    ck("拦截命中→air_defense", r4 and "air_defense" in r4["buckets"])
    r5 = eng.classify("Iskander ballistic missile strike hits Kharkiv district",
                      "Glide bombs and KN-23 missiles targeted infrastructure, authorities said.")
    ck("导弹/滑翔弹→long_range", r5 and "long_range" in r5["buckets"])
    r6 = eng.classify("Naval drone attacks Black Sea fleet near Sevastopol, Odesa port hit",
                      "Sea Baby drones struck shipping; the Sea of Azov launch areas monitored.")
    ck("黑海/亚速海→black_sea", r6 and "black_sea" in r6["buckets"])
    r7 = eng.classify("Ukraine air defense intercepts 90 percent of drones with Patriot batteries",
                      "NASAMS and mobile fire groups suppressed the rest, Air Force reported.")
    ck("防空能力→air_defense", r7 and "air_defense" in r7["buckets"])
    r8 = eng.classify("Milbloggers claimed encirclement near Vovchansk; ISW assesses no evidence",
                      "Russian military bloggers asserted gains; geolocated footage confirmed otherwise.")
    ck("声称核实→claims_intel", r8 and "claims_intel" in r8["buckets"])
    # --- flags 声称/核实 ---
    rf1 = eng.classify("Pokrovsk almost surrounded, Russian MoD says",
                       "According to the Russian Ministry of Defence, its forces seized the airfield.")
    ck("俄方声称 ru_claim", rf1 and rf1["flags"].get("ru_claim") is True)
    rf2 = eng.classify("General Staff reported 230 combat engagements over past day",
                       "Ukraine says its forces repelled assaults in the Pokrovsk sector.")
    ck("乌方声称 ua_claim", rf2 and rf2["flags"].get("ua_claim") is True)
    rf3 = eng.classify("Both sides claim gains near Kostiantynivka, claims not independently verified",
                       "The claims could not be verified; ISW assessed the front unchanged.")
    ck("未经核实 unverified", rf3 and rf3["flags"].get("unverified") is True)
    rf4 = eng.classify("Ukrainian brigade warns of encirclement risk as Russians assault Pokrovsk",
                       "Soldiers described pockets forming; commanders denied troops are cut off.")
    ck("包围表述 encirclement_alert", rf4 and rf4["flags"].get("encirclement_alert") is True)
    # --- 排除与锚定 ---
    rg3 = eng.classify("Morning recap", "Russian missiles struck Kharkiv; drones downed over Odesa; shelling continued.")
    ck("Morning recap 不进任何桶", rg3 is None or rg3.get("anchor_only") or not rg3["buckets"])
    r15 = eng.classify("Quarterly earnings beat expectations", "Tech shares rallied.")
    ck("无锚定=不收", r15 is None)
    r16 = eng.classify("Kyiv hosted a film festival with European guests", "The program included premieres and panels.")
    ck("锚定无桶=拦截候选", r16 and r16.get("anchor_only") is True)
    # --- kind 顺序 ---
    re1 = eng.classify("Russian missiles struck Kharkiv, authorities said", "Three people were wounded; fires broke out.")
    ck("完成态打击→fact_attack", re1 and re1["kind"] == "fact_attack")
    rs1 = eng.classify("Zelensky says the front is holding despite pressure", "He warned that Russia massed troops near Pokrovsk.")
    ck("表态→opinion_statement", rs1 and rs1["kind"] == "opinion_statement")
    ra1 = eng.classify("Ukraine's Pokrovsk front appears increasingly fragile", "Analysts see Russia's war of attrition grinding on.")
    ck("分析→opinion_analysis", ra1 and ra1["kind"] == "opinion_analysis")
    # --- 意向语态守卫（承 diplomacy 审计轮教训）---
    ri1 = eng.classify("Russia expected to launch new missile wave this winter", "Officials said the attack could target energy grid.")
    ck("expected to launch ≠ 完成态", ri1 is None or ri1["kind"] != "fact_attack")
    ri2 = eng.classify("Ukraine plans to strike Russian refineries with Flamingo missiles", "The campaign aims to cut diesel supply.")
    ck("plans to strike ≠ 完成态", ri2 is None or ri2["kind"] != "fact_attack")
    ri3 = eng.classify("Russia shelled Zaporizhzhia overnight, 405 drones downed", "Geran drones hit energy sites.")
    ck("真实完成态不被意向守卫误杀", ri3 and ri3["kind"] == "fact_attack")
    # --- 立场/否定 ---
    ck("立场降温 ceasefire", eng.stance_of("The two sides observed a brief ceasefire before shelling resumed.") == "deescalation_signal")
    ck("立场升级 massive strike", eng.stance_of("Russia launched a massive strike on the energy grid.") == "escalation_signal")
    ck("否定翻转 no truce", eng.stance_of("Officials insist there will be no truce before winter.") == "escalation_signal")
    ck("双向混存", eng.stance_of("Massive strikes continued even as both sides discussed a pause.") == "mixed_signal")
    # --- gnews 后缀折叠/链接归一 ---
    ck("gnews 后缀折叠", title_key("Pokrovsk battle rages - Reuters") ==
       title_key("Pokrovsk battle rages - The Business Standard"))
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
    _bp = os.path.join(os.environ.get("TEMP", "/tmp"), "_prune_test_uafront.jsonl")
    ck("prune 超龄删除（含备份）",
       prune_archive(_tmp_prune, date(2026, 9, 19), 120, backup_path=_bp) == 1
       and _tmp_prune["b"]["published"] == "2026-09-01"
       and "2020-01-01" in open(_bp, encoding="utf-8").read())
    # --- 回填掉桶 → excluded ---
    arch2 = {"x": {"id": "x", "published": "2026-09-15", "date_unknown": False,
                   "title": "Chess tournament recap",
                   "summary": "The Kyiv Open concluded with an upset.",
                   "kind": "fact_attack", "buckets": ["donbas_front"], "cfg_ver": "OLD"},
             "y": {"id": "y", "published": "2026-09-16", "date_unknown": False,
                   "title": "Regional weather report",
                   "summary": "Rain expected across Kharkiv oblast.",
                   "kind": "fact_attack", "buckets": ["kharkiv_front"], "cfg_ver": "OLD"}}
    backfill_archive(cfg, eng, arch2)
    ck("回填掉桶(anchor_only)→excluded", arch2["x"]["kind"] == "excluded" and arch2["x"]["buckets"] == [])
    ck("回填掉桶(无锚定)→excluded", arch2["y"]["kind"] == "excluded" and arch2["y"]["buckets"] == [])
    win2, _ = window_filter(list(arch2.values()), date(2026, 9, 19), 30)
    ck("excluded 不进快照", len(win2) == 0)
    # --- 配置不变量 ---
    ck("战线八桶全二维", all(len(b["require_all"]) == 2 for b in cfg["buckets"].values()))
    ck("所有桶有标签", all(b.get("label") for b in cfg["buckets"].values()))
    ck("每启用源 tier 合法", all(s.get("tier") in ("T1", "T2", "T3")
       for s in cfg["sources"] if s.get("enabled")))
    ck("无启用 T4 源", all(s.get("tier") != "T4" for s in cfg["sources"] if s.get("enabled")))
    ck("停用源有留档", all(s.get("note") for s in cfg["sources"] if not s.get("enabled")))
    ck("baseline rows 字段完整",
       all(b.get("date") and b.get("event") and b.get("tier") in ("T1", "T2", "T3")
           and b.get("source") for b in cfg["baseline"].get("rows", [])
           + cfg["baseline"].get("air_rows", [])))
    ck("flags 键全部可编译", all(k in eng.flag_res for k in
       ("ru_claim", "ua_claim", "unverified", "encirclement_alert")))
    ck("TASS/俄源在场（官方立场窗口）", any(s["id"] == "tass" and s.get("enabled")
       for s in cfg["sources"]))
    # --- 核心结论能产出（防全绿但结论恒空）---
    ck("核心结论: 事件/表态/分析 三类都能产出",
       re1["kind"] == "fact_attack" and rs1["kind"] == "opinion_statement"
       and ra1["kind"] == "opinion_analysis")

    ok = sum(1 for _, c in P if c)
    for name, c in P:
        print(f"  {'✅' if c else '❌'} {name}")
    print(f"[selftest] {ok}/{len(P)} 通过")
    return 0 if ok == len(P) else 1

if __name__ == "__main__":
    main()
