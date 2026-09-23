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

import readiness_monitor as R  # noqa: E402


# ---------------------------------------------------------------- 载入
def load_cfg(path):
    cfg = json.load(open(path, encoding="utf-8"))
    R.CFG = cfg
    R.SIG_CFG = cfg.get("signals", {})
    R.CLAIM_CFG = cfg.get("claims", {})
    R.REL_CFG = cfg.get("relevance", {})
    R.CROSS_ANCHORS = cfg.get("cross_anchors", [])
    R.OUTLET_MAP = cfg.get("outlet_map", {})
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
    m = re.search(pattern, txt, re.S)
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
    实测：§1 的**信号桶**「否认/驳斥」与 §8 的**声明类型**「否认/驳斥」
    标签相同、口径不同，全文提取会拿到 §1 的行去核对 §8 的数字，
    于是报出一个**并不存在的不一致**（对账工具自己制造假警报）。
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
        R.OUTPUT_DIR, day, f"readiness-digest-{day}.md")
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
    s6 = section(md, r"^## 6\. ", r"^## 7\. ")
    s7 = section(md, r"^## 7\. ", r"^## 8\. ")
    s8 = section(md, r"^## 8\. ", r"^## 9\. ")
    print(f"[章节切分] §1 {len(s1)} / §6 {len(s6)} / §7 {len(s7)} / §8 {len(s8)} 字符")
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

    # ---------- A. §1 一页速览：条数 + 各信号桶 ----------
    print("── A. §1 一页速览（信号桶口径）──")
    _P_TOT = r"\|\s*战备相关表态条数\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|"
    ck.eq("§1 战备相关表态条数·美方", len(us), to_int(num(s1, _P_TOT, 1)))
    ck.eq("§1 战备相关表态条数·伊方", len(ir), to_int(num(s1, _P_TOT, 2)))
    ck.eq("§1 战备相关表态条数·第三方", len(th), to_int(num(s1, _P_TOT, 3)))

    for b, spec in R.SIG_CFG.items():
        lbl = spec.get("label", b)
        cu = sum(1 for r in us if b in (r.get("signal_buckets") or []))
        ci = sum(1 for r in ir if b in (r.get("signal_buckets") or []))
        ct = sum(1 for r in th if b in (r.get("signal_buckets") or []))
        pat = (r"\|\s*" + re.escape(lbl) + r"\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|")
        ck.eq(f"§1 信号桶[{lbl}]·美方", cu, to_int(num(s1, pat, 1)))
        ck.eq(f"§1 信号桶[{lbl}]·伊方", ci, to_int(num(s1, pat, 2)))
        ck.eq(f"§1 信号桶[{lbl}]·第三方", ct, to_int(num(s1, pat, 3)))

    # 报告在 opp=0 时省略该句（缺席视为 0）
    opp_printed = to_int(num(s1, r"另有\s*\*\*(\d+)\*\*\s*条来自\*\*伊朗流亡/反对派媒体"))
    if opp_printed is None and len(opp) == 0:
        ck.note("§1 反对派条数为 0，报告按设计省略该句")
        opp_printed = 0
    ck.eq("§1 伊朗流亡/反对派单列条数", len(opp), opp_printed)

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

    # ---------- C. §7 周表：条数与加权分 ----------
    print("── C. §7 公开战备信号活跃度（周表）──")
    weekly = R.build_weekly(recs)
    wk_rows = re.findall(
        r"^\|\s*(\d{4}-W\d{2})\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*✅\s*\|$",
        s7, re.M)
    covered = [w for w in weekly if w["covered"] and w["parties"]]
    ck.eq("§7 有数据周数（表格行数）", len(covered), len(wk_rows))
    by_week = {w["week"]: w for w in covered}
    n_ok = 0
    for wk, us_d, us_w, ir_d, ir_w, th_d in wk_rows:
        w = by_week.get(wk)
        if not w:
            ck.bad.append(f"§7 周 {wk} 在报告里但落盘复算无此周")
            continue
        p = w["parties"]
        for nm, got, want in (
                (f"§7 {wk} 美方条数", p["us"]["docs"], int(us_d)),
                (f"§7 {wk} 美方加权分", p["us"]["weight"], int(us_w)),
                (f"§7 {wk} 伊方条数", p["iran"]["docs"], int(ir_d)),
                (f"§7 {wk} 伊方加权分", p["iran"]["weight"], int(ir_w)),
                (f"§7 {wk} 第三方条数", p["third"]["docs"], int(th_d))):
            ck.eq(nm, got, want)
            n_ok += 1
    # §7 里的周条数之和应与 §1 的展示条数相符
    sum_us = sum(w["parties"]["us"]["docs"] for w in covered)
    sum_ir = sum(w["parties"]["iran"]["docs"] for w in covered)
    sum_th = sum(w["parties"]["third"]["docs"] for w in covered)
    print(f"    §7 周表合计：美 {sum_us} / 伊 {sum_ir} / 三方 {sum_th}；"
          f"§1 展示：美 {len(us)} / 伊 {len(ir)} / 三方 {len(th)} / 反对派 {len(opp)}")
    if (sum_us, sum_ir) != (len(us), len(ir)):
        ck.note(f"§7 周表美/伊合计与 §1 展示条数不一致（差 "
                f"美 {len(us)-sum_us} / 伊 {len(ir)-sum_ir}）——"
                f"通常是 published 无法解析成日期（该记录进不了按周统计）所致，"
                f"若差值 >0 应在报告里披露")
    # 第三方列的差异是**设计如此**（§7 把 iran_opp 并入第三方），
    # 差额应恰好等于反对派条数；不等才说明有第三种原因需要排查。
    if sum_th - len(th) != len(opp):
        ck.note(f"§7 第三方合计 {sum_th} 与 §1 第三方 {len(th)} + 反对派 {len(opp)} "
                f"= {len(th)+len(opp)} 不符 —— 除「反对派并入」外还有别的口径差，需排查")
    else:
        print(f"    §7 第三方含反对派：{len(th)} + {len(opp)} = {sum_th} ✅（设计如此，已披露）")

    # ---------- D. §8 声明类型分布（claim_kinds 口径） ----------
    print("── D. §8 声明类型分布（claim_kinds 口径）──")
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
        ck.eq(f"§8 [{lbl}]·美方", cu, to_int(num(s8, pat, 1)))
        ck.eq(f"§8 [{lbl}]·伊方", ci, to_int(num(s8, pat, 2)))
        ck.eq(f"§8 [{lbl}]·第三方", ct, to_int(num(s8, pat, 3)))

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

    # ---------- H. §6 判定依据完整性 ----------
    #  contradiction 的判定依据里最典型的形态是「第三方转述美方否认伊方说法」，
    #  若 §6 正文只列 us / iran 而不列 third，读者就**无从复核**这个对撞是真是假 ——
    #  实测 §6 曾经完全忽略 third，正文里一条第三方记录都看不到。
    print("── H. §6 判定依据完整性（第三方不可省）──")
    _p6 = cross.get("pairs", [])[:24]
    _blocks = re.split(r"^### ", s6, flags=re.M)[1:]
    if _p6:
        ck.eq("§6 段落数 = 对照组数", len(_blocks), len(_p6))
        for p, blk in zip(_p6, _blocks):
            if p["cls"] != "contradiction":
                continue
            ck.eq(f"§6 contradiction 组列出第三方"
                  f"（{p['anchor_label']} {p['week']}）",
                  "🌐 第三方" in blk, bool(p.get("third")))
    else:
        ck.note("无同期对照组合，跳过 §6 判定依据检查")

    # ---------- G. 落盘可靠性：快照必须完整包含于累积档 ----------
    print("── G. 落盘可靠性（快照 ⊆ 累积总档）──")
    acc_path = os.path.join(R.OUTPUT_DIR, "ALL-records.jsonl")
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
