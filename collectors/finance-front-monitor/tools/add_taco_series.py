#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""add_taco_series.py —— 把 TACO 五因子的原始序列接进 finance-front-monitor。

背景
----
用户在 `taco-monitor`（东吴宏观 TACO 压力指数五因子复刻）里已经爬过五个指标。
本次要求：「如果已经爬了的需要核对源头是否一致，如果没爬的需要补充爬虫」。

核对结论（用的是**双方已落盘的原始载荷**逐日对表，脚本见
`tools/compare_taco_sources.py`，输出 `tools/_taco_source_check.json`）：

  因子 C  10Y 美债     本侧=财政部 par yield CSV  vs  taco=FRED DGS10
                       → 181/181 个共有交易日**逐日完全相同**（同底层，不同发布途径）
  因子 G  道指         本侧=Yahoo ^DJI            vs  taco=FRED DJIA
                       → 501 天共有，22 天完全相同，最大差 0.001875 点（FRED 取两位小数）
  因子 H  布伦特原油   本侧=Yahoo BZ=F            vs  taco=Yahoo BZ=F（同一 symbol）
                       → 503 天中 502 天完全相同；唯当日差 0.33
                         ★ 原因是本侧保留了**未收盘的盘中价**。这正是
                           drop_inprogress_session 要解决的问题 → 本次一并开启。
  因子 D  5Y 盈亏平衡  本侧=**缺失**               vs  taco=FRED T5YIE      → 补爬
  因子 E  特朗普支持率 本侧=**缺失**               vs  taco=Datawrapper kSCt4 → 补爬

因此本脚本做四件事：
  1) 新增 2 条序列（因子 D / E）——「没爬的需要补充爬虫」；
  2) 给 3 个已爬的因子挂上 taco 侧同源/近源作为**交叉源**（主源保持不变）
     —— 「已经爬了的需要核对源头是否一致」，而且核对要**持续**做，
     所以链条里留着对方那一源，每天自动对一次；
  3) 打开 `drop_inprogress_session`，把口径对齐到「只取完整交易日收盘」
     （用户 2026-09-23 选定的口径）；
  4) 写一个 `taco_factors` 对照块，把「哪个序列对应哪个因子、对侧用什么源、
     一致性实测到什么程度」固化进配置，将来换源时有据可依。

设计取舍
--------
* **只搬数据，不合成指数**（用户选定）。指数仍由 taco-monitor 负责：
  两套实现同时算同一个指数，最后一定会互相打架，且没人知道该信哪个。
* 不改动现有 40 条序列的主源，只**追加**交叉源 —— 追加是幂等的、可回滚的；
  改主源会让历史数据出现口径断层。
* 幂等：重复运行结果不变（按 id / provider+ref 判重，不是盲目 append）。

用法
----
    python tools/add_taco_series.py               # 预演：打印将要做的改动
    python tools/add_taco_series.py --apply       # 写回（原文件备份为 .bak-taco-<时间戳>）
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIG = os.path.join(ROOT, "config.json")

# ★ 必须与 tools/probe_sources.py 的 build_targets() 逐字一致，
#   否则「探测证据」和「实际抓取」指向两个不同的 URL —— 最隐蔽的一类假证据。
FRED_URL = ("https://fred.stlouisfed.org/graph/fredgraph.csv"
            "?id={sid}&cosd=2016-01-01")
# ★ FRED 走 Akamai，对「客户端类型 + 自称的 UA 名字」双重指纹：
#   curl + Chrome UA → 超时；curl + curl/8.19.0 → 200。见 probe_sources.FRED_UA。
FRED_UA = "curl/8.19.0"


def fred_entry(sid: str, label: str) -> dict:
    return {
        "provider": "fred",
        "ref": sid,
        "tier": "official",
        "url": FRED_URL.format(sid=sid),
        "accept": "text/csv,*/*",
        "transport": "curl",
        "ua": FRED_UA,
        "label": label,
    }


# ---------------------------------------------------------------- 新增序列
NEW_SERIES = [
    {
        "id": "taco_breakeven5y",
        "label": "5Y 通胀预期（TACO 因子 D）",
        "bucket": "taco",
        "unit": "%",
        "decimals": 2,
        "cadence": "daily",
        "polarity": 1,
        "role": "headline",
        "div_tol": 0.01,
        "chain": [fred_entry("T5YIE", "FRED T5YIE 5Y breakeven")],
        "note": (
            "对侧 taco-monitor 的因子 D 用的是 «1Y 通胀互换»，公开源不可得，"
            "它以 FRED `T5YIE`（5 年盈亏平衡通胀率）代理。本侧采用**同一个代理源、"
            "同一个 series id**，因此这一因子两侧**完全同源**，不需要容差谈判。"
        ),
        "caveat": (
            "T5YIE 是**国债市场的盈亏平衡通胀率**（名义 − TIPS），"
            "不等于通胀互换报价：前者含 TIPS 流动性溢价与通胀风险溢价，"
            "后者含交易对手与抵押品因素。用它代理 1Y 互换是**期限也换了**"
            "（5Y 而不是 1Y）的近似，只能看方向与拐点，不能当水平值引用。"
        ),
    },
    {
        "id": "taco_trump_approval",
        "label": "特朗普支持率（TACO 因子 E）",
        "bucket": "taco",
        "unit": "%",
        "decimals": 2,
        "cadence": "daily",
        "polarity": -1,
        "role": "headline",
        "div_tol": 0.02,
        "chain": [{
            "provider": "datawrapper",
            "ref": "trump_approval",
            "tier": "aggregator",
            "url": "https://static.dwcdn.net/data/kSCt4.csv",
            "accept": "text/csv,*/*",
            "transport": "curl",
            "ua": FRED_UA,     # 该站对 Python-urllib UA 返回 403，curl 风格稳定
            "date_col": "modeldate",
            "value_col": "approve",
            "date_format": "%m/%d/%Y",
            "label": "Silver Bulletin / Datawrapper kSCt4",
        }],
        "note": (
            "★ 按**列名**取 `approve`，不能按位置取："
            "表头是 `modeldate,approve,disapprove,approve_lo,...`，"
            "`disapprove` 也在第 2 列且同样是小数字 —— 按位置会「成功地」取到反向那一列，"
            "而且只要表结构不变就永远发现不了。解析器见 finance_monitor.parse_named_csv。"
        ),
        "caveat": (
            "对侧 taco-monitor 的因子 E 是**净支持率**（RCP 手工均值 poll 平均），"
            "本侧是 **Silver Bulletin 的模型值 approve**："
            "① 一个是「原始民调均值」、一个是「建模后的日频序列」，口径不同；"
            "② 一个可能已扣 disapprove、一个是单侧支持率，**水平值不可直接比**；"
            "③ 日期是 M/D/YYYY（月在前），解析器已显式指定格式而不是靠猜测。"
            "结论：这一因子两侧**不同源**，只可用于**方向性**对照，"
            "不适合跨项目拼接成一条连续序列。"
        ),
    },
]

# ------------------------------------------------- 给已爬因子补交叉源（核对用）
CROSS_SOURCES = {
    "dgs10": {
        "entry": fred_entry("DGS10", "FRED DGS10"),
        "note": " ★ 已实证：与主源（财政部 par yield）在 181 个共有交易日上**逐日完全相同**，"
                "属于「同底层数据、不同发布途径」——留作免费的双通道校验。",
    },
    "djia": {
        "entry": fred_entry("DJIA", "FRED DJIA"),
        "note": " ★ 已实证：与 Yahoo `^DJI` 在 501 个共有日上 22 天完全相同，"
                "最大差 0.001875 点（FRED 只保留两位小数），容差 1% 足够。",
    },
}

# 评估过但**明确拒绝**入链的交叉源。
# 记在这里而不是悄悄删掉：下一个人看到 `data_gaps.dropped_unverified` 里的
# FRED:DCOILBRENTEU 时，需要知道「不是没试，是试过并否了」，否则会重复劳动。
REJECTED_CROSS = {
    "brent": {
        "ref": "DCOILBRENTEU",
        "why": (
            "★ 试过，**否了**：FRED `DCOILBRENTEU`（欧洲 Brent **现货**）与主源 "
            "Yahoo `BZ=F`（Brent 近月**期货**）在 490 个共有日上相对差中位数 1.69%、"
            "最大 22.4% —— 但最近一周扩大到 −7.7% → −16.9%（现货升水），"
            "且 FRED 该序列当时已滞后 8 天。"
            "① 这是**真实的期限结构**（近端供给冲击下现货高于期货），不是数据错误；"
            "② 它偏巧是 taco-monitor 因子 H **没有**用的那个源（对侧用的就是 Yahoo BZ=F）；"
            "③ 交叉源的作用是抓「解析错、单位错、取错列」，不是抓基差 ——"
            "把基差放进 4% 容差里，等于让它每天报一次警，真正的数据错会被淹掉。"
            "结论：不入链。基差本身有信息量，但那是另一个指标，不该伪装成校验。"
        ),
    },
}

# 上一次（2026-09-23 首版）误挂到 brent 上的交叉源说明，需要擦掉。
LEGACY_BRENT_NOTE = (" ★ 已实证：与 Yahoo `BZ=F` 在 503 个共有日中 502 天完全相同。"
                     "注意这是**期货 vs 现货**，价差属正常期限结构（容差 4%），不是源错。")

TACO_FACTORS = {
    "note": (
        "TACO 五因子的**原始日频序列**在本工程的落地位置。"
        "本工程只提供数据，**不合成 TACO 指数**——指数由 taco-monitor 负责，"
        "避免两套实现互相打架。"
    ),
    "counterpart": "taco-monitor（东吴宏观 TACO 压力指数五因子复刻）",
    "formula_ref": ("31 日差分 → expanding z（start_index=31, min_obs=2, ddof=1）"
                    "→ 等权均值 S → 7 日 MA = TACO"),
    "alignment_summary": {
        "同源": 2, "同底层不同途径": 2, "不同源（仅方向性）": 1,
        "method": "用双方**已落盘的原始载荷**逐日对表，脚本 tools/compare_taco_sources.py",
        "report": "tools/_taco_source_check.json",
    },
    "factors": [
        {"code": "C", "name": "10Y 美债收益率", "series": "dgs10",
         "taco_ref": "FRED DGS10 (USGG10YR)",
         "align": "同底层数据、不同发布途径",
         "evidence": "181/181 个共有交易日逐日完全相同；本侧主源为财政部 par yield，"
                     "FRED 已作为交叉源入链"},
        {"code": "D", "name": "1Y 通胀互换（以 5Y 盈亏平衡代理）",
         "series": "taco_breakeven5y", "taco_ref": "FRED T5YIE",
         "align": "同源同 ID（FRED T5YIE）",
         "evidence": "本次新增；两侧使用完全相同的 series id，无需容差"},
        {"code": "E", "name": "特朗普净支持率", "series": "taco_trump_approval",
         "taco_ref": "RCP 手工均值（净支持率）",
         "align": "不同源：建模日频值 vs 民调均值，且单侧 vs 净额",
         "evidence": "本次新增；只可用于方向性对照，**不可跨项目拼接**"},
        {"code": "G", "name": "道琼斯工业指数", "series": "djia",
         "taco_ref": "FRED DJIA (INDU)",
         "align": "同底层数据、不同发布途径",
         "evidence": "501 天共有，22 天完全相同，最大差 0.001875 点（FRED 取两位小数）；"
                     "FRED 已作为交叉源入链"},
        {"code": "H", "name": "布伦特原油", "series": "brent",
         "taco_ref": "Yahoo BZ=F（同一 symbol）",
         "align": "同源同 symbol",
         "evidence": "503 天中 502 天完全相同，唯当日差 0.33 —— 本侧当时保留了"
                     "未收盘盘中价；已由 drop_inprogress_session 修复口径。"
                     "★ 另试过把 FRED 现货 DCOILBRENTEU 挂作交叉源，**否了**："
                     "它与期货的基差最近一周扩大到 −17%（真实现货升水），"
                     "放进 4% 容差会天天报警，把真正的数据错淹掉。"},
    ],
}


def patch(cfg: dict) -> tuple[dict, list[str]]:
    log: list[str] = []

    # ---- 1) 新桶 ----
    buckets = cfg.setdefault("buckets", {})
    if "taco" not in buckets:
        buckets["taco"] = {
            "name": "TACO 五因子（东吴宏观口径）",
            "order": 9,
            "caveat": (
                "★ 本桶是**跨项目对接区**，不是美伊冲突的专属指标。"
                "五个因子来自 taco-monitor（东吴宏观 TACO 压力指数复刻），"
                "其中「特朗普支持率」两侧**口径不同源**（建模日频值 vs 民调均值），"
                "只可作方向性对照。这里只放原始序列，**不算 TACO 指数**。"
            ),
        }
        log.append("新增桶 taco（order 9）")
    else:
        log.append("桶 taco 已存在，保持不变")

    # ---- 2) 新增序列 ----
    series = cfg.setdefault("series", [])
    by_id = {s["id"]: s for s in series}
    for sr in NEW_SERIES:
        if sr["id"] in by_id:
            log.append(f"序列 {sr['id']} 已存在，跳过")
        else:
            series.append(json.loads(json.dumps(sr, ensure_ascii=False)))
            log.append(f"新增序列 {sr['id']}（bucket=taco）")

    # ---- 3) 给已爬因子补交叉源 ----
    for sid, spec in CROSS_SOURCES.items():
        sr = by_id.get(sid)
        if sr is None:
            log.append(f"！未找到序列 {sid}，跳过交叉源")
            continue
        chain = sr.setdefault("chain", [])
        ref = spec["entry"]["ref"]
        if any(e.get("provider") == "fred" and e.get("ref") == ref for e in chain):
            log.append(f"{sid} 已有 fred:{ref} 交叉源，跳过")
            continue
        chain.append(json.loads(json.dumps(spec["entry"], ensure_ascii=False)))
        sr["note"] = (sr.get("note", "").rstrip() + spec["note"]).strip()
        log.append(f"{sid} 追加交叉源 fred:{ref}（主源不变，交叉源排在第 {len(chain)} 位）")

    # ---- 3bis 移除被否掉的交叉源（brent 的 FRED 现货）----
    for sid, rej in REJECTED_CROSS.items():
        sr = by_id.get(sid)
        if sr is None:
            continue
        before = len(sr.get("chain", []))
        sr["chain"] = [e for e in sr.get("chain", [])
                       if not (e.get("provider") == "fred" and e.get("ref") == rej["ref"])]
        if len(sr["chain"]) != before:
            log.append(f"{sid} 移除交叉源 fred:{rej['ref']}"
                       f"（{before} → {len(sr['chain'])} 个条目）—— 理由见 caveat")
        note = sr.get("note", "")
        if LEGACY_BRENT_NOTE in note:
            note = note.replace(LEGACY_BRENT_NOTE, "")
            log.append(f"{sid} 擦除上一版误写的交叉源说明")
        add = (" ★ 评估过 FRED 现货（DCOILBRENTEU）能否作交叉源：**否**。"
               f"{rej['why']}")
        if add not in note:
            note = note.rstrip() + add
            log.append(f"{sid} 写入「拒绝理由」（防止下一个人重复评估）")
        sr["note"] = note.strip()

    # ---- 4) 口径：只取完整交易日收盘 ----
    req = cfg.setdefault("request", {})
    if req.get("drop_inprogress_session") is not True:
        req["drop_inprogress_session"] = True
        req["drop_inprogress_session_note"] = (
            "★ 全库只取**完整交易日收盘**：日期 ≥ 运行日的会话一律丢弃。"
            "理由不是洁癖：日线序列的最后一根在交易时段内是**实时变动的盘中价**，"
            "把它当收盘价会让「最新值」在同一天内不可复算 —— "
            "复核的人半小时后重跑会得到不同的数字，而两边都「没错」。"
            "实测例证：布伦特 BZ=F 在 taco-monitor 与本侧曾出现「503 天里 502 天全同、"
            "唯当日差 0.33」，原因就是本侧留了未收盘的那一根。"
            "口径与 taco-monitor 的 _drop_from = run_date 对齐。"
        )
        log.append("request.drop_inprogress_session = true（口径对齐 taco-monitor）")
    else:
        log.append("drop_inprogress_session 已是 true，保持不变")

    # ---- 5) TACO 对照块 ----
    if cfg.get("taco_factors") != TACO_FACTORS:
        cfg["taco_factors"] = json.loads(json.dumps(TACO_FACTORS, ensure_ascii=False))
        log.append("写入 taco_factors 对照块（因子 ↔ 序列 ↔ 对侧源 ↔ 实证结论）")

    # ---- 6) 修 data_gaps：已恢复的交叉源不能再挂在「无实测可用记录」下 ----
    dg = cfg.setdefault("data_gaps", {})
    restored = {spec["entry"]["ref"] for spec in CROSS_SOURCES.values()}
    dg["restored_after_fred_ua_fix"] = {
        "reason": "此前 FRED **整链**被判为不可用，真因是 Akamai 的双重指纹："
                  "「客户端类型」+「自称的 UA 名字」都被看 —— curl 自称 Chrome UA 会超时，"
                  "改自称 curl/8.19.0 后 200。UA 一旦写对，FRED 全量 28/31 可用。",
        "restored": [
            {"series": "dgs10", "cross": "fred:DGS10",
             "evidence": "181/181 个共有交易日逐日完全相同（同底层，不同发布途径）"},
            {"series": "djia", "cross": "fred:DJIA",
             "evidence": "501 个共有日，最大差 0.001875 点（FRED 只留两位小数）"},
        ],
        "evaluated_and_rejected": [
            {"series": "brent", "cross": "fred:DCOILBRENTEU",
             "why": REJECTED_CROSS["brent"]["why"]},
        ],
        "evidence_file": "tools/_probe_round10.json",
    }
    still = []
    for item in dg.get("dropped_unverified", []):
        r = item.get("reason", "")
        if any(f"fred({f})" in r or f":{f})" in r
               for f in restored | {REJECTED_CROSS["brent"]["ref"]}):
            continue           # 本轮已处理（恢复或明确拒绝）→ 从「丢弃」清单里摘掉
        if "WCSSTUS1" in r or "WCESTUS1" in r:
            item = dict(item)
            item["reason"] = ("FRED **已下线该序列**：2026-09-23 实测 http=404（连 series 页也是 404），"
                              "不是可达性问题。spr / crude_stocks 的 FRED 交叉源因此**永久不可恢复**，"
                              "需要另找源（EIA 需 key，本项目不用密钥接口）。")
        elif "无实测可用记录" in r and "fred(" in r:
            fid = r.split("fred(")[-1].split(")")[0]
            item = dict(item)
            item["reason"] = (f"{r}；★ 该源现已实测可用（tools/_probe_round10.json），"
                              f"属于「可恢复但本轮未启用」——TACO 五因子只用到 "
                              f"DGS10/DJIA/DCOILBRENTEU/T5YIE，其余交叉源待用户确认后再开，"
                              f"以免每日多出十几次 FRED 请求、把 Akamai 限速触发概率抬高。")
        still.append(item)
    if still != dg.get("dropped_unverified"):
        dg["dropped_unverified"] = still
        log.append(f"重写 data_gaps.dropped_unverified（{len(still)} 条，"
                   f"已摘除本轮处理的 {len(restored) + 1} 项）")
    dg["taco_gaps"] = [
        {"series": "taco_trump_approval",
         "gap": "对侧 taco-monitor 用的是 RCP 民调净支持率；本侧只能用 Silver Bulletin "
                "建模值（Datawrapper kSCt4）。**两侧不同源**，水平值不可比。"},
        {"series": "taco_breakeven5y",
         "gap": "对侧因子 D 本想用 «1Y 通胀互换»，公开源不可得，两侧都用 FRED T5YIE 代理。"
                "同源，但「5Y 盈亏平衡 ≠ 1Y 互换」，期限与工具都不是同一个东西。"},
    ]

    # ---- 7) 元信息 ----
    cfg["topic"] = "美伊冲突 · 金融战线监测（九个桶 × 可复算口径 + TACO 五因子对接）"
    # patched_by 要**按 tool 去重**：否则重复 --apply 会把它堆成历史流水账，
    # 而这份配置是给人读的，「谁改过」留最后一条就够，历史在 .bak-* 里。
    rec = {
        "tool": "tools/add_taco_series.py",
        "at": datetime.now().strftime("%Y-%m-%d"),
        "what": "接入 TACO 五因子原始序列（C/D/E/G/H）；dgs10/djia/brent 追加 FRED 交叉源；"
                "全库开启 drop_inprogress_session",
        "scope_note": "只搬数据、不合成指数；主源一律不变，只追加交叉源",
    }
    pb = [x for x in cfg.get("patched_by", []) if x.get("tool") != rec["tool"]]
    if rec not in pb:
        pb.append(rec)
        log.append("更新 patched_by（按 tool 去重）")
    cfg["patched_by"] = pb
    if "TACO" not in cfg.get("topic", ""):
        log.append("更新 topic")
    return cfg, log


def main() -> int:
    ap = argparse.ArgumentParser(description="把 TACO 五因子接进 config.json")
    ap.add_argument("--apply", action="store_true", help="写回（默认只预演）")
    a = ap.parse_args()

    raw = open(CONFIG, "rb").read()
    cfg = json.loads(raw.decode("utf-8"))

    before_series = len(cfg.get("series", []))
    cfg, log = patch(cfg)
    after_series = len(cfg.get("series", []))

    print("=" * 74)
    for ln in log:
        print("  •", ln)
    print("=" * 74)
    print(f"序列 {before_series} → {after_series}，桶 {len(cfg['buckets'])} 个")

    out = json.dumps(cfg, ensure_ascii=False, indent=2).replace("\n", "\r\n") + "\r\n"
    if not a.apply:
        print("\n[预演] 未写回。加 --apply 落盘。")
        return 0

    bak = CONFIG + f".bak-taco-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(CONFIG, bak)
    with open(CONFIG, "wb") as f:
        f.write(out.encode("utf-8"))
    print(f"\n[已写回] 备份 → {os.path.basename(bak)}")
    print(f"          {len(raw)} → {len(out.encode('utf-8'))} 字节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
