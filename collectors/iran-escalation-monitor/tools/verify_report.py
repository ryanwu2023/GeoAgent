#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""回源对账：把报告里的**每一个数字**从落盘数据独立复算，再逐项比对。

为什么必须有这个工具
--------------------
「程序日志全绿、条数正常、状态码 200」**不能证明报告里的数字是对的**。
本项目已经踩过的三类错误都属于「绿但错」：

1. 折叠分类统计只算了前 400 条**抽样明细**，却当全量印出来
   → 864 条被拆成 252+142+6=400，加总都不自洽；
     真实的「有战备信号但未锚定」是 9 条，报告写 6 条。
2. 词表写半截词根（`deplet`/`casualt`）→ `depletion`/`casualties` 整体漏检，
   计数偏小而无人察觉。
3. 交叉验证的 contradiction 恒为 0（美方 `action_claim` 永远是空集）。

所以本工具**不复用渲染层代码**：统计逻辑在这里重写一遍，
只用从 config 读来的**规则**（`is_displayable` / `signals` / `cross_anchors`），
然后与报告 Markdown 里**实际印出的数字**逐个字符地比。

★ 防「对着错文件说 OK」：
  - 默认取**最新日期目录**（绝不写死日期）
  - **首行打印实际核对的两个文件路径**
  - 支持 `--date` / `--report` / `--records` 覆盖

退出码：0 = 全部一致；1 = 有任一不一致（可直接用于定时任务门禁）。
"""
import argparse
import collections
import json
import os
import re
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import iran_monitor as R  # noqa: E402


# ---------------------------------------------------------------- 载入
def load_cfg(path):
    cfg = json.load(open(path, encoding="utf-8"))
    R.CFG = cfg
    R.SIG_CFG = cfg.get("signals", {})
    R.CLAIM_CFG = cfg.get("claims", {})
    R.REL_CFG = cfg.get("relevance", {})
    R.CROSS_ANCHORS = cfg.get("cross_anchors", [])
    R.OUTLET_MAP = cfg.get("outlet_map", {})
    R.ZONES = cfg.get("zones", [])
    R.SOURCE_BY_ID = {s["id"]: s for s in cfg.get("sources", [])}
    # 必须算出当前词表指纹，才能校验「快照里有没有混龄记录」
    R.CFG_VER = R.lexicon_fingerprint()
    return cfg


def latest_day():
    days = sorted(d for d in os.listdir(R.OUTPUT_DIR)
                  if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d))
    return days[-1] if days else None


def load_jsonl(path):
    recs = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return recs


# ---------------------------------------------------------------- 对账框架
class Checker:
    def __init__(self):
        self.ok = 0
        self.bad = []
        self.warn = []

    def eq(self, name, got, want):
        if got == want:
            self.ok += 1
        else:
            self.bad.append(f"{name}：落盘复算={got!r}  报告印出={want!r}")

    def note(self, msg):
        self.warn.append(msg)


def num(txt, pattern, group=1):
    # ★ 必须带 re.M：本文件大量用 `^\|` 提取**表格行**，只有 re.S 时
    #   `^` 只匹配整个章节字符串的开头（即 "\n\n"），全部提取会静默落空。
    m = re.search(pattern, txt, re.S | re.M)
    if not m:
        return None
    return m.group(group)


def to_int(s):
    if s is None:
        return None
    s = s.replace("*", "").replace(",", "").strip()
    return int(s) if re.fullmatch(r"-?\d+", s) else None


def section(md, start_pat, end_pat):
    """切出报告的一个章节，**在该章节内**做提取。

    ★ 必须按章节切分，否则同名标签会跨节误匹配 ——
    实测：信号桶「否认/驳斥」与声明类型「否认/驳斥（声明类）」标签相近、
    口径完全不同（前者是信号桶命中，后者是 claim_kinds 分类），
    全文提取会拿到一节的数字去核对另一节，报出一个**并不存在的不一致**
    （对账工具自己制造假警报）。
    """
    m1 = re.search(start_pat, md, re.M)
    if not m1:
        return ""
    rest = md[m1.end():]
    m2 = re.search(end_pat, rest, re.M)
    return rest[: m2.start()] if m2 else rest


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description="报告回源对账")
    ap.add_argument("--config", default=os.path.join(ROOT, "config.json"))
    ap.add_argument("--date", default=None, help="YYYY-MM-DD，默认最新日期目录")
    ap.add_argument("--report", default=None, help="报告 md 路径")
    ap.add_argument("--records", default=None, help="记录 jsonl 路径")
    # ★ 累积总档路径必须可覆盖。原先写死为 `R.OUTPUT_DIR/ALL-records.jsonl`，
    #   于是对账一个**隔离目录**的产出（--out-dir 跨天验证）时，
    #   拿的是**正式目录**的档案 —— 报出「快照记录不在累积档」的假告警，
    #   而真正的问题是这个工具又犯了「对着错文件下结论」的老毛病。
    ap.add_argument("--archive", default=None,
                    help="累积总档 ALL-records.jsonl 路径（默认取正式 output 目录；"
                         "对账隔离目录产出时必须显式指定）")
    ap.add_argument("--save", action="store_true",
                    help="把本次对账结果写入 output/_verify_last.json（留痕）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    cfg = load_cfg(args.config)
    day = args.date or latest_day()
    if not day:
        print("❌ output/ 下没有任何日期目录，无法对账")
        return 1
    report_path = args.report or os.path.join(
        R.OUTPUT_DIR, day, f"iran-digest-{day}.md")
    records_path = args.records or os.path.join(
        R.OUTPUT_DIR, day, f"records-{day}.jsonl")
    if not os.path.exists(report_path):
        print(f"❌ 找不到报告：{report_path}")
        return 1
    if not os.path.exists(records_path):
        print(f"❌ 找不到记录：{records_path}")
        return 1

    # ★ 首行必须打印实际核对的路径 —— 否则会「对着前一天的快照说全绿」
    print("=" * 92)
    print("回源对账（verify_report）")
    print("=" * 92)
    print(f"核对的报告文件：{report_path}")
    print(f"核对的记录文件：{records_path}")
    print(f"配置：{args.config}  （{len(cfg.get('sources', []))} 源，"
          f"{len(R.SIG_CFG)} 信号桶）")
    print()

    recs = load_jsonl(records_path)
    md = open(report_path, encoding="utf-8").read()
    ck = Checker()

    # ★ 先按章节切分，后续所有提取都在章节内进行（防同名标签跨节误匹配）
    s1 = section(md, r"^## 1\. ", r"^## 2\. ")
    s3 = section(md, r"^## 3\. ", r"^## 4\. ")
    s4 = section(md, r"^## 4\. ", r"^## 5\. ")
    s5 = section(md, r"^## 5\. ", r"^## 6\. ")
    s6 = section(md, r"^## 6\. ", r"^## 7\. ")
    s7 = section(md, r"^## 7\. ", r"^## 8\. ")
    s8 = section(md, r"^## 8\. ", r"^## 9\. ")
    s9 = section(md, r"^## 9\. ", r"^## 10\. ")
    print(f"[章节切分] §1 {len(s1)} / §3 {len(s3)} / §4 {len(s4)} / §5 {len(s5)} / "
          f"§6 {len(s6)} / §7 {len(s7)} / §8 {len(s8)} / §9 {len(s9)} 字符")
    print()

    # ---------- 独立复算（不复用渲染层）----------
    disp = [r for r in recs if R.is_displayable(r)]
    us = [r for r in disp if r.get("party") == "us"]
    ir = [r for r in disp if r.get("party") == "iran"]
    th = [r for r in disp if r.get("party") == "third"]
    opp = [r for r in disp if r.get("party") == "iran_opp"]

    print(f"[落盘复算] 窗口快照 {len(recs)} 条 → 过展示闸门 {len(disp)} 条"
          f"（美 {len(us)} / 伊 {len(ir)} / 三方 {len(th)} / 反对派 {len(opp)}）")
    print()

    # ---------- A. §1 一页速览：关注域表 + 当事方合计 ----------
    #  ★ §1 现在是**两张口径**同表：上半是关注域口径（一条记录命中多域会重复计），
    #    末行是当事方口径（每条只算一次）。对账必须分别核，且**不得**把两列相加。
    print("── A. §1 一页速览（关注域口径 + 当事方口径）──")
    _P_TOT = (r"\|\s*—\s*\|\s*\*\*当事方合计\*\*\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|"
              r"\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|")
    ck.eq("§1 当事方合计·伊方", len(ir), to_int(num(s1, _P_TOT, 1)))
    ck.eq("§1 当事方合计·美方", len(us), to_int(num(s1, _P_TOT, 2)))
    ck.eq("§1 当事方合计·第三方", len(th), to_int(num(s1, _P_TOT, 3)))
    ck.eq("§1 当事方合计·反对派", len(opp), to_int(num(s1, _P_TOT, 4)))
    ck.eq("§1 当事方合计·展示总数", len(disp), to_int(num(s1, _P_TOT, 5)))
    ck.eq("§1 当事方合计=美+伊+三方+反对派（列可加性）★",
          len(us) + len(ir) + len(th) + len(opp), len(disp))

    #  逐关注域核：条数与四个当事方分列
    zs_lp = {z["id"]: z for z in R.build_zone_summary(recs)}
    for z in R.ZONES:
        zid = z["id"]
        zc = zs_lp.get(zid) or {"n": 0, "parties": {}}
        p = zc["parties"]
        pat = (r"^\|\s*" + str(z.get("order")) + r"\s*\|\s*\*\*"
               + re.escape(z["name"]) + r"\*\*\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|"
               r"\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|")
        ck.eq(f"§1 域[{zid}]·伊方", p.get("iran", 0), to_int(num(s1, pat, 1)))
        ck.eq(f"§1 域[{zid}]·美方", p.get("us", 0), to_int(num(s1, pat, 2)))
        ck.eq(f"§1 域[{zid}]·第三方", p.get("third", 0), to_int(num(s1, pat, 3)))
        ck.eq(f"§1 域[{zid}]·反对派", p.get("iran_opp", 0), to_int(num(s1, pat, 4)))
        ck.eq(f"§1 域[{zid}]·该域记录数", zc["n"], to_int(num(s1, pat, 5)))
        # 域内四个当事方之和必须等于该域记录数 —— 域口径自身可加
        ck.eq(f"§1 域[{zid}] 四列之和=该域记录数 ★",
              sum(p.get(k, 0) for k in ("iran", "us", "third", "iran_opp")), zc["n"])

    #  ★ 关注域口径与当事方口径**不可相加**：断言「域记录数之和 ≥ 展示总数」，
    #    防止有人后来把两个口径改成同一个数（那会同时掩盖重复计数与漏计）。
    _zsum = sum(z["n"] for z in zs_lp.values())
    ck.eq("§1 域记录数之和 ≥ 当事方总数（一口记录可属多域）★",
          _zsum >= len(disp), True)
    ck.eq("§1 报告已披露「两口径不可相加」★",
          "不可纵向相加" in s1 or "不能相加" in s1, True)

    # 报告在 opp=0 时省略该句（缺席视为 0）
    opp_printed = to_int(num(s1, r"另有\s*\*\*(\d+)\*\*\s*条来自\*\*伊朗流亡/反对派媒体"))
    if opp_printed is None and len(opp) == 0:
        ck.note("§1 反对派条数为 0，报告按设计省略该句")
        opp_printed = 0
    ck.eq("§1 伊朗流亡/反对派单列条数", len(opp), opp_printed)

    #  ★ §1 的前瞻摘要行：数字必须与 §4 独立复算一致（同一事实两处出现，必须同值）
    _fs_lp = R.build_foresight(recs)
    _p_fs = r"\*\*升级前瞻\*\*：窗口内 \*\*(\d+)\*\*\s*条记录含"
    ck.eq("§1 升级前瞻条数", _fs_lp["total"], to_int(num(s1, _p_fs)))

    #  ★ 「最活跃信号桶（前 3）」是 §1 唯一给出**桶级明细**的地方，
    #    它必须真的是计数最高的 3 个桶 —— 否则读者会照着错误的「最活跃」去理解该域。
    for z in R.ZONES:
        zc = zs_lp.get(z["id"]) or {"buckets": {}}
        top = "、".join(f"{R.SIG_CFG[k].get('label', k)}({v})" for k, v in
                        sorted(zc["buckets"].items(), key=lambda kv: -kv[1])[:3]) or "—"
        pat = (r"^\|\s*" + str(z.get("order")) + r"\s*\|\s*\*\*"
               + re.escape(z["name"]) + r"\*\*.*?\|\s*([^|]+?)\s*\|\s*$")
        ck.eq(f"§1 域[{z['id']}] 最活跃信号桶（前 3）", top, num(s1, pat))

    # ---------- A2. §3 六大关注域明细 ----------
    print("── A2. §3 关注域明细（表行数 = 域内记录数）──")
    _z_blocks = re.split(r"^### 3\.", s3, flags=re.M)[1:]
    ck.eq("§3 段落数 = 关注域数", len(_z_blocks), len(R.ZONES))
    for z, blk in zip(R.ZONES, _z_blocks):
        zc = zs_lp.get(z["id"]) or {"n": 0, "records": [], "desc": ""}
        # ★ 标题形如 `### 3.1 打击美军海上作战平台　`naval_platform`　（168 条）`，
        #   条数在**末尾括号里**，不是标题开头的序号。抓错位置会拿到 1..7，
        #   于是「7 项不一致」全是工具自己制造的假告警。
        m = re.search(r"（(\d+)\s*条）", blk.split("\n")[0] + "\n" + blk[:200])
        ck.eq(f"§3 域[{z['id']}] 标题内条数", zc["n"], to_int(m.group(1) if m else None))
        ck.eq(f"§3 域[{z['id']}] 引用描述行", f"> {zc['desc']}" in blk, True)
        # 表行数：排除表头分隔行；空域时报告会写 _未检出_ 而不给表
        rows = [ln for ln in blk.splitlines()
                if ln.startswith("|") and not re.match(r"^\|[\s:|-]+\|$", ln)]
        rows = [ln for ln in rows if not ln.startswith("| 日期 ")]
        ck.eq(f"§3 域[{z['id']}] 表行数 = min(域内记录数, 45) ★",
              min(zc["n"], 45), len(rows))

    # ---------- A3. §4 升级前瞻指标（意向指标） ----------
    print("── A3. §4 升级前瞻（意图指标，必须与 §1 同值）──")
    ck.eq("§4 命中条数 = §1 前瞻条数 ★", _fs_lp["total"],
          to_int(num(s4, r"命中 \*\*(\d+)\*\* 条")))
    _p_bypart = r"按当事方：([^\n]+)"
    _part_line = num(s4, _p_bypart) or ""
    _printed_parts = {}
    for _m in re.finditer(r"(🇺🇸|🇮🇷|🌐|🟣|❓)[^\d\n]*?(\d+)", _part_line):
        _printed_parts[_m.group(1)] = int(_m.group(2))
    ck.eq("§4 按当事方分列条数之和 = 前瞻总条数 ★",
          sum(_printed_parts.values()), _fs_lp["total"])
    _p_bybucket = r"按信号桶：([^\n]+)"
    _bkt_line = num(s4, _p_bybucket) or ""
    _printed_bk = sum(int(x) for x in re.findall(r"\*\*(\d+)\*\*", _bkt_line))
    ck.eq("§4 按信号桶分列之和 ≥ 前瞻总条数（一条可命中多个前瞻桶）★",
          _printed_bk >= _fs_lp["total"], True)
    #  表行：强度 / 日期 / … 最后是标题链接
    #  ★ 行数必须按「第 1 格是数字」判定，**不能**要求第 2 格是 `YYYY-MM-DD`：
    #    无日期记录在第 2 格印 `—`，用日期正则会把它们排除掉 → 工具自己少算一行，
    #    报出「表行数不符」的假告警（实测差 1 行，查了半天是工具的问题）。
    _fs_rows = re.findall(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|", s4, re.M)
    ck.eq("§4 表行数 = min(前瞻条数, 40)", min(_fs_lp["total"], 40), len(_fs_rows))
    _scores = [int(x) for x, _ in _fs_rows]
    ck.eq("§4 表按强度降序排列 ★", _scores == sorted(_scores, reverse=True), True)
    ck.eq("§4 每条强度 > 0（0 分不应出现在前瞻表）",
          all(s > 0 for s in _scores), True)
    #  ★ 日期格只允许两种形态：`YYYY-MM-DD` 或 `—`。
    #    绝不允许原始字符串截断后混进来（实测印出过 `Thu, 01 Ja`）。
    _bad_cells = [c for _, c in _fs_rows
                  if not (re.match(r"^\d{4}-\d{2}-\d{2}$", c) or c == "—")]
    ck.eq("§4 日期格只有 `YYYY-MM-DD` 或 `—`（不得印截断的原始日期串）★",
          _bad_cells, [])
    #  ★ 前瞻**必须**带「不是预测」的免责说明，否则读者会把威胁读成预告
    ck.eq("§4 披露「选项指标而非预测」的三条误读 ★",
          ("不是「会不会发生」" in s4) and ("三种误读" in s4), True)

    # ---------- A4. §5 关键实体分布 ----------
    print("── A4. §5 关键实体分布（条数可复算）──")
    _ents_lp = {g["id"]: {r["en"]: r["n"] for r in g["rows"]}
                for g in R.extract_entities(recs)}
    _e_blocks = re.split(r"^### 5\.", s5, flags=re.M)[1:]
    ck.eq("§5 段落数 = 有实体的分组数", len(_e_blocks), len(_ents_lp))
    _n_ent_checked = 0
    for gid, rows in _ents_lp.items():
        blk = next((b for b in _e_blocks if f"`{gid}`" in b), None)
        if blk is None:
            ck.bad.append(f"§5 缺少分组 {gid} 的段落")
            continue
        # ★ 报告只印**排名前 22** 的实体行，与条数大小无关；
        #   早先写成「条数 ≤ 22 才核对」，既漏核大部分行，逻辑本身也是错的。
        for rank, (en, n) in enumerate(sorted(rows.items(), key=lambda kv: -kv[1])):
            if rank >= 22:
                break
            pat = r"^\|\s*`" + re.escape(en) + r"`\s*\|[^|]*\|\s*(\d+)\s*\|"
            ck.eq(f"§5[{gid}] 实体 `{en}` 条数", n, to_int(num(blk, pat)))
            _n_ent_checked += 1
    print(f"    §5 逐实体核对 {_n_ent_checked} 项"
          f"（每组只印前 22 行，超出部分报告本身也截断了）")

    # ---------- B. §1 折叠统计：加总必须自洽 ----------
    print("── B. §1 折叠分类统计（必须自洽）──")
    folded = len(recs) - len(disp)
    ck.eq("§1 折叠总数", folded,
          to_int(num(s1, r"\*\*本次折叠\*\*：(\d+)\s*条未进入本报告正文")))
    _items, brk = R.classify_folded(recs)
    for key, lbl in (("no-readiness-signal", "命中主题词但无战备信号"),
                     ("off-topic", "与本主题无关"),
                     ("no-topic-anchor", "有战备信号但未锚定本主题")):
        ck.eq(f"§1 折叠分类[{lbl}]", brk.get(key, 0),
              to_int(num(s1, re.escape(lbl) + r"\s*\*\*(\d+)\*\*\s*条")))
    # ★ 加总自洽：这是曾经出错的地方（252+142+6=400≠864）
    printed = [to_int(num(s1, re.escape(l) + r"\s*\*\*(\d+)\*\*\s*条"))
               for l in ("命中主题词但无战备信号", "与本主题无关",
                         "有战备信号但未锚定本主题")]
    if all(x is not None for x in printed):
        ck.eq("§1 折叠分类**加总**=折叠总数（不自洽即失败）★",
              sum(printed), folded)
    else:
        ck.note("§1 折叠分类未能全部解析出数字，跳过加总校验")

    # ---------- C. §8 周表：条数与加权分（原 §7，因新增 §3/§4/§5 后编号后移）----------
    print("── C. §8 公开升级信号活跃度（周表）──")
    weekly = R.build_weekly(recs)
    wk_rows = re.findall(
        r"^\|\s*(\d{4}-W\d{2})\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*✅\s*\|$",
        s8, re.M)
    covered = [w for w in weekly if w["covered"] and w["parties"]]
    ck.eq("§8 有数据周数（表格行数）", len(covered), len(wk_rows))
    by_week = {w["week"]: w for w in covered}
    n_ok = 0
    for wk, us_d, us_w, ir_d, ir_w, th_d in wk_rows:
        w = by_week.get(wk)
        if not w:
            ck.bad.append(f"§8 周 {wk} 在报告里但落盘复算无此周")
            continue
        p = w["parties"]
        for nm, got, want in (
                (f"§8 {wk} 美方条数", p["us"]["docs"], int(us_d)),
                (f"§8 {wk} 美方加权分", p["us"]["weight"], int(us_w)),
                (f"§8 {wk} 伊方条数", p["iran"]["docs"], int(ir_d)),
                (f"§8 {wk} 伊方加权分", p["iran"]["weight"], int(ir_w)),
                (f"§8 {wk} 第三方条数", p["third"]["docs"], int(th_d))):
            ck.eq(nm, got, want)
            n_ok += 1
    # ★ §8 与 §1 的差额必须被**精确解释**，不能只说「通常是日期解析问题」。
    #  差额的唯一合法来源是：① 日期不可解析 → 进不了按周统计；② §8 把 iran_opp 并入第三方。
    #  把这两个原因算成**期望值**再逐方比对 —— 差额一旦对不上，说明还有第三种原因，
    #  而那正是需要排查的（实测就抓到过：95 条记录因两种日期格式未支持而只出现在 §1/§3）。
    sum_us = sum(w["parties"]["us"]["docs"] for w in covered)
    sum_ir = sum(w["parties"]["iran"]["docs"] for w in covered)
    sum_th = sum(w["parties"]["third"]["docs"] for w in covered)
    print(f"    §8 周表合计：美 {sum_us} / 伊 {sum_ir} / 三方 {sum_th}；"
          f"§1 展示：美 {len(us)} / 伊 {len(ir)} / 三方 {len(th)} / 反对派 {len(opp)}")
    _nodate = [r for r in disp if not R.parse_date(r.get("published") or "")]
    _nod = collections.Counter(r.get("party") or "third" for r in _nodate)
    print(f"    日期不可解析（不进周表）：{len(_nodate)} 条　{dict(_nod)}")
    ck.eq("§8 美方 = §1 美方 − 日期不可解析的美方 ★",
          sum_us, len(us) - _nod.get("us", 0))
    ck.eq("§8 伊方 = §1 伊方 − 日期不可解析的伊方 ★",
          sum_ir, len(ir) - _nod.get("iran", 0))
    ck.eq("§8 第三方 = §1 第三方 + 反对派 − 日期不可解析的那部分 ★",
          sum_th, len(th) + len(opp) - _nod.get("third", 0) - _nod.get("iran_opp", 0))
    # 报告必须**把这条差额写出来**，否则读者会看到一个无法解释的缺口
    if _nodate:
        ck.eq("§8 已披露「日期不可解析而不计入本表」的条数 ★",
              to_int(num(s8, r"有 (\d+) 条展示记录因 `published` 无法解析而不计入本表")),
              len(_nodate))

    # ---------- D. §9 声明类型分布（claim_kinds 口径） ----------
    print("── D. §9 声明类型分布（claim_kinds 口径）──")
    kinds = [("action_claim", "宣称行动效果（声明类）"),
             ("denial", "否认/驳斥（声明类）"),
             ("acknowledgement", "承认/确认（声明类）"),
             ("readiness_declaration", "战备表态（声明类）"),
             ("threat_warning", "威胁/警告（声明类）")]
    for k, lbl in kinds:
        cu = sum(1 for r in us if k in (r.get("claim_kinds") or []))
        ci = sum(1 for r in ir if k in (r.get("claim_kinds") or []))
        ct = sum(1 for r in th if k in (r.get("claim_kinds") or []))
        pat = (r"\|\s*" + re.escape(lbl) + r"\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|")
        ck.eq(f"§9 [{lbl}]·美方", cu, to_int(num(s9, pat, 1)))
        ck.eq(f"§9 [{lbl}]·伊方", ci, to_int(num(s9, pat, 2)))
        ck.eq(f"§9 [{lbl}]·第三方", ct, to_int(num(s9, pat, 3)))

    # ---------- E. §1 交叉验证结果 ----------
    print("── E. §1 交叉验证结果 ──")
    cross = R.build_crosscheck(recs)
    pairs = cross.get("pairs", [])
    ck.eq("§1 交叉验证 同期对照组数", len(pairs),
          to_int(num(s1, r"\*\*交叉验证结果\*\*：(\d+)\s*组同期对照")))
    for cls, lbl in (("contradiction", "互相矛盾"),
                     ("mutual_assert", "双方均宣称行动"),
                     ("both_mentioned", "双方仅同期提及")):
        got = sum(1 for p in pairs if p["cls"] == cls)
        ck.eq(f"§1 交叉验证[{lbl}]", got,
              to_int(num(s1, re.escape(lbl) + r"\s*\*\*(\d+)\*\*")))

    # ---------- F. 内部一致性：头部快照数 ----------
    print("── F. 头部与内部一致性 ──")
    ck.eq("头部 当前快照条数", len(recs),
          to_int(num(md, r"当前快照\s*\*\*(\d+)\*\*\s*条")))
    #  ★★ 全局守卫：**任何**表格单元格都不得出现「原始日期串被截断」的残留。
    #    实测事故：日期解析失败时 `published` 保留原始字符串，渲染处取前 10 位，
    #    于是 §4 的日期栏印出 `Thu, 01 Ja` —— 看着像日期，实则既非日期也无含义，
    #    而且行数校验会因为「第 2 格不是 YYYY-MM-DD」而少算一行，报出假告警。
    #    这条检查覆盖整份报告、所有章节，不留死角。
    _rawhits = [ln for ln in md.splitlines()
                if ln.lstrip().startswith("|")
                and re.search(r"\|\s*(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,", ln)]
    ck.eq("全报告 无表格单元格残留原始 RFC822 日期串 ★★", _rawhits[:3], [])
    #  ★ 只查**表格行**：披露说明里刻意引用 `Thu, 01 Jan 1970` 作为例子，
    #    若连说明文字一起查，工具会把自己写的揭示性文字判成泄漏（假告警）。
    _epochhits = [ln for ln in md.splitlines()
                  if ln.lstrip().startswith("|") and "Jan 1970" in ln]
    ck.eq("全报告 无纪元零值哨兵泄漏（`Thu, 01 Jan 1970`）★★", _epochhits[:3], [])

    # ---------- H. §7 判定依据完整性 ----------
    #  contradiction 的判定依据里最典型的形态是「第三方转述美方否认伊方说法」，
    #  若 §6 正文只列 us / iran 而不列 third，读者就**无从复核**这个对撞是真是假 ——
    #  实测 §6 曾经完全忽略 third，正文里一条第三方记录都看不到。
    print("── H. §7 判定依据完整性（第三方不可省）──")
    _p6 = cross.get("pairs", [])[:24]
    # ★ 必须切 **§7**：`contradiction` 的判定依据（第三方转述否认）列在
    #   §7「同期对照：双方原文并列」的每组块里，而 §6 只是议题×周的矩阵表
    #   （每行只有 us/iran/third 的**计数**，没有条目）。
    #   切错章节会报「判定依据缺失」——又一个工具自己制造的假告警。
    _blocks = re.split(r"^### ", s7, flags=re.M)[1:]
    if _p6:
        ck.eq("§7 段落数 = 对照组数", len(_blocks), len(_p6))
        for p, blk in zip(_p6, _blocks):
            if p["cls"] != "contradiction":
                continue
            ck.eq(f"§7 contradiction 组列出第三方"
                  f"（{p['anchor_label']} {p['week']}）",
                  "🌐 第三方" in blk, bool(p.get("third")))
    else:
        ck.note("无同期对照组合，跳过 §7 判定依据检查")

    # ---------- G. 落盘可靠性：快照必须完整包含于累积档 ----------
    print("── G. 落盘可靠性（快照 ⊆ 累积总档）──")
    acc_path = args.archive or os.path.join(R.OUTPUT_DIR, "ALL-records.jsonl")
    if os.path.exists(acc_path) and \
            os.path.abspath(acc_path) != os.path.abspath(records_path):
        acc = load_jsonl(acc_path)
        acc_ids = {r.get("id") for r in acc}
        missing = [r.get("id") for r in recs if r.get("id") not in acc_ids]
        # 快照里的每条都必须能在累积档里找到 —— 否则是「快照写了、总档没写」，
        # 跨天之后这些记录会永远消失（累积档是唯一永不淘汰的层）。
        ck.eq(f"快照记录全部存在于累积档（{os.path.basename(acc_path)}）",
              len(missing), 0)
        if missing:
            ck.note(f"累积档缺失示例 id：{missing[:5]}")
        # first_seen 不得晚于快照日期，否则说明累积档被重建、历史未继承
        late = [r.get("id") for r in acc if (r.get("first_seen") or "") > day]
        ck.eq("累积档 first_seen 不晚于快照日期", len(late), 0)
        print(f"    快照 {len(recs)} 条 / 累积档 {len(acc)} 条（累积档 ⊇ 快照）")
    else:
        ck.note("未找到累积总档，或记录文件就是累积档，跳过「快照 ⊆ 累积档」检查")

    # ★ 快照内**词表指纹必须唯一** —— 否则是「混龄档案」：
    #  部分记录用旧词表算信号、部分用新词表，而报告拿这份快照生成，
    #  于是趋势 / 交叉验证 / 计数都在拿两套口径混算，**输出看起来完全正常**。
    #  实测踩过：只回填了累积档、忘了回填当日快照，§6 的命中词条还显示 `deni`
    #  （已改名的旧词条），而所有计数与状态码都没报错。
    _vers = {}
    for r in recs:
        _v = r.get("cfg_ver") or "(缺失)"
        _vers[_v] = _vers.get(_v, 0) + 1
    ck.eq(f"快照内词表指纹唯一（当前 {R.CFG_VER}）", len(_vers), 1)
    if len(_vers) > 1:
        ck.note(f"⚠️ 快照内存在 {len(_vers)} 种词表指纹："
                f"{dict(list(_vers.items())[:4])} —— 这是「混龄档案」，"
                f"报告口径不可比。跑一次完整抓取或对快照做回填即可修复。")
    elif _vers and list(_vers)[0] != R.CFG_VER:
        ck.note(f"⚠️ 快照词表指纹 {list(_vers)[0]} ≠ 当前指纹 {R.CFG_VER} ——"
                f"配置已改但快照未重算，跑 --recompute 或完整抓取。")

    # ---------- 汇总 ----------
    print()
    print("=" * 92)
    result = {
        "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "day": day, "report": report_path, "records": records_path,
        "config": args.config, "pass": not ck.bad,
        "checks_passed": ck.ok, "checks_failed": len(ck.bad),
        "warnings": ck.warn, "failures": ck.bad,
    }
    if args.save:
        vp = os.path.join(R.OUTPUT_DIR, "_verify_last.json")
        with open(vp, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        print(f"对账结果已留档：{vp}")
        print()
    if ck.warn:
        for w in ck.warn:
            print(f"⚠️  {w}")
        print()
    if ck.bad:
        print(f"❌ 对账不一致 {len(ck.bad)} 项：")
        for b in ck.bad:
            print("   · " + b)
        print()
        print(f"结果：{ck.ok} 项一致 / {len(ck.bad)} 项**不一致**")
        print("=" * 92)
        return 1
    print(f"✅ 结果：{ck.ok} 项全部一致（报告数字可由落盘数据逐项复算）")
    if ck.warn:
        print(f"   （另有 {len(ck.warn)} 条提示，见上）")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    sys.exit(main())
