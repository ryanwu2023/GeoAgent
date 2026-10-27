#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
verify_report.py — meeting-calendar-monitor 对账

从落盘数据（state/events.json 与 LATEST.jsonl）**独立复算**报告与 CSV 里的数字，
绕开渲染层。渲染层自己算自己，永远是对的；这里要能抓到「表里少了一行」
「状态标签和状态码不一致」「地点印成了 1970」这类问题。

任一项不符 → 退出码非零。
"""
import argparse
import csv
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CST = timezone(timedelta(hours=8))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_jsonl(p):
    out = []
    if not os.path.exists(p):
        return out
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def md_section(md, start_pat, stop_pat=r"\n## "):
    m = re.search(start_pat, md)
    if not m:
        return ""
    rest = md[m.end():]
    n = re.search(stop_pat, rest)
    return rest[: n.start()] if n else rest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(BASE, "output"))
    ap.add_argument("--state", default=os.path.join(BASE, "state", "events.json"))
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--save", action="store_true", help="留档对账结果到 output/_verify_last.json")
    args = ap.parse_args()

    cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
    p_md = os.path.join(args.out_dir, "LATEST.md")
    p_csv = os.path.join(args.out_dir, "LATEST.csv")
    p_jsonl = os.path.join(args.out_dir, "LATEST.jsonl")
    p_health = os.path.join(args.out_dir, args.date, "_source_health.json")

    print(f"[verify] 报告: {p_md}")
    print(f"[verify] 记录: {p_jsonl}")
    print(f"[verify] CSV : {p_csv}")

    missing = [p for p in (p_md, p_csv, p_jsonl) if not os.path.exists(p)]
    if missing:
        print(f"[verify] ✗ 缺少文件：{missing}")
        return 2

    md = open(p_md, encoding="utf-8").read()
    events = load_jsonl(p_jsonl)
    with open(p_csv, encoding="utf-8-sig") as f:
        csv_rows = list(csv.DictReader(f))
    state = json.load(open(args.state, encoding="utf-8")) if os.path.exists(args.state) else {}

    today = date.fromisoformat(args.date)
    horizon = cfg["horizon_days"]
    checks = []

    def chk(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    def dt(s):
        try:
            return date.fromisoformat((s or "")[:10])
        except ValueError:
            return None

    # ---------- 1. 基本一致性
    chk("CSV 行数与 JSONL 事件数一致", len(csv_rows) == len(events),
        f"csv={len(csv_rows)} jsonl={len(events)}")
    ids = [e["id"] for e in events]
    chk("事件 ID 唯一", len(ids) == len(set(ids)), f"重复 {len(ids) - len(set(ids))} 个")
    chk("CSV 事件 ID 与 JSONL 完全一致",
        sorted(r["事件ID"] for r in csv_rows) == sorted(ids))

    # ---------- 2. 主表
    # ★ 已否认/已推迟/已取消 不进日历表（它们在 §6.1 单列）。对账必须用同一把尺子，
    #   否则报告把「取消」移出表是对的，这里却会判成「少了一行」。
    NEG = ("canceled", "postponed", "denied")
    main = md_section(md, r"## 2\. 会晤日程表[^\n]*\n")
    rows = [ln for ln in main.splitlines() if ln.startswith("|") and "---" not in ln]
    rows = [r for r in rows if not r.startswith("| 日期 ")]
    upcoming = [e for e in events if e.get("date_start") and dt(e["date_start"]) >= today
                and e.get("status") not in NEG]
    tbd = [e for e in events if not e.get("date_start") and e.get("status") not in NEG]
    chk("主表行数 = 未来项 + 待定项", len(rows) == len(upcoming) + len(tbd),
        f"表={len(rows)} 期望={len(upcoming) + len(tbd)}")
    dropped = [e for e in events if e.get("status") in NEG]
    sec6 = md_section(md, r"### 6\.1 [^\n]*\n", r"\n### ")
    drow = [ln for ln in sec6.splitlines() if ln.startswith("|") and "---" not in ln]
    drow = [r for r in drow if not r.startswith("| 状态 ")]
    chk("§6.1 未成行条数与数据一致", len(drow) == min(len(dropped), 20),
        f"表={len(drow)} 数据={len(dropped)}")

    # 表头统计数字
    m = re.search(r"未来\s*(\d+)\s*天内会晤/会议\s*\|\s*\*\*(\d+)\*\*\s*项（另有日期待定\s*(\d+)\s*项）", md)
    chk("速览里的未来/待定计数与数据一致",
        bool(m) and int(m.group(2)) == len(upcoming) and int(m.group(3)) == len(tbd),
        f"报告={m.groups() if m else None} 实际=({len(upcoming)},{len(tbd)})")

    # 主表日期必须升序，待定在最后。
    # ★ 不能直接 cell[:10]：月份精度的单元格是「2026-11（月内）」，
    #   截 10 个字符会切进全角括号，字符串比较立刻乱序 —— 这是对账工具的
    #   假告警（报告本身是好的）。必须用正则取出年-月-日再补全。
    def date_key(cell):
        m = re.match(r"(\d{4})-(\d{2})(?:-(\d{2}))?", cell)
        if not m:
            return None
        return f"{m.group(1)}-{m.group(2)}-{m.group(3) or '01'}"

    dates = []
    for r in rows:
        cell = r.split("|")[1].strip()
        dates.append(None if cell in ("待定", "—") else date_key(cell))
    seq = [x for x in dates if x]
    chk("主表按日期升序", seq == sorted(seq), f"{seq[:5]}…")
    chk("待定项排在主表末尾",
        all(x is not None for x in dates[:len(seq)]) and all(x is None for x in dates[len(seq):]))

    # ---------- 3. 状态标签与状态码
    labels = {k: v["label"] for k, v in cfg["status_lexicon"].items()}
    labels["unconfirmed"] = "暂无确认日期"
    labels["lapsed"] = "过期未证实"
    bad_status = []
    for e in events:
        cell = [r.split("|")[4].strip() for r in rows if e["title"][:14] in r]
        if not cell:
            continue
        lab = cell[0].replace(" ⚠口径冲突", "")
        base = re.sub(r"（.*?）", "", lab).strip()
        if base and base not in labels.values():
            bad_status.append((e["id"], lab))
    chk("主表确认情况均为合法标签", not bad_status, str(bad_status[:3]))

    # ---------- 4. 每条都要有依据
    noev = [e["id"] for e in upcoming + tbd if not (e.get("sources") or e.get("baseline"))]
    chk("每条未来项都有来源或明确标为基线", not noev, str(noev[:5]))

    # ---------- 5. 时间边界
    over = [e["id"] for e in events
            if e.get("date_start") and (dt(e["date_start"]) - today).days > horizon]
    chk(f"无超出 {horizon} 天视野的事件", not over, str(over[:5]))

    # ---------- 6. 脏值守卫
    chk("报告无 1970 纪元哨兵", "1970-01-01" not in md and "Jan 1970" not in md)
    chk("报告无 RFC822 原始日期串", re.search(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun),", md) is None)
    chk("CSV 无空日期列", all(r["日期"].strip() for r in csv_rows))
    chk("CSV 每个人物列非空", all(r["关键人物"].strip() for r in csv_rows),
        str([r["事件ID"] for r in csv_rows if not r["关键人物"].strip()][:5]))

    # ---------- 7. 已举行 / 过期 分段计数
    held = [e for e in events if e["status"] == "held"]
    lapsed = [e for e in events if e["status"] == "lapsed"]
    mh = re.search(r"## 7\. 已举行（近 \d+ 天，(\d+) 项）", md)
    chk("已举行段落计数与状态一致", bool(mh) and int(mh.group(1)) == len(held),
        f"报告={mh.group(1) if mh else None} 实际={len(held)}")
    if lapsed:
        ml = re.search(r"以下 \*\*(\d+)\*\* 项的日期已过", md)
        chk("过期未证实计数一致", bool(ml) and int(ml.group(1)) == len(lapsed),
            f"报告={ml.group(1) if ml else None} 实际={len(lapsed)}")

    # ---------- 8. 源状态
    if os.path.exists(p_health):
        health = json.load(open(p_health, encoding="utf-8"))
        ok_n = sum(1 for h in health if h.get("ok"))
        ms = re.search(r"源可用 \*\*(\d+)/(\d+)\*\*", md)
        chk("源可用计数与 _source_health 一致",
            bool(ms) and int(ms.group(1)) == ok_n and int(ms.group(2)) == len(health),
            f"报告={ms.groups() if ms else None} 实际=({ok_n},{len(health)})")

    # ---------- 9. 基线核验披露
    base = [e for e in events if e.get("baseline")]
    if base:
        chk("报告披露了基线条目", "基线条目核验" in md)

    # ---------- 10. 状态变化台账
    trans_path = os.path.join(BASE, "state", "transitions.jsonl")
    if os.path.exists(trans_path):
        trans = load_jsonl(trans_path)
        chk("变更台账每条都有 from/to",
            all(t.get("from") and t.get("to") for t in trans),
            str([t for t in trans if not (t.get("from") and t.get("to"))][:2]))

    # ---------- 输出
    ok = sum(1 for _, passed, _ in checks if passed)
    for name, passed, detail in checks:
        if not passed:
            print(f"  ✗ {name}  {detail}")
    print(f"[verify] {ok}/{len(checks)} 项一致")
    if args.save:
        os.makedirs(args.out_dir, exist_ok=True)
        with open(os.path.join(args.out_dir, "_verify_last.json"), "w", encoding="utf-8") as f:
            json.dump({"at": datetime.now(CST).isoformat(timespec="seconds"),
                       "date": args.date, "ok": ok, "total": len(checks),
                       "failed": [n for n, p, _ in checks if not p]},
                      f, ensure_ascii=False, indent=1)
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
