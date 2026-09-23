# -*- coding: utf-8 -*-
"""离线诊断：用真实语料重算 crosscheck，并逐周打印「双方各有什么」。

为什么不直接跑完整流水线：抓取有随机性（同一周可能抓多抓少），
而定位「为什么 contradiction 恒为 0」需要**可复现的固定输入**。
本工具把 claim_kinds / anchors / signals 全部按当前词表重算，
保证「词表改动」是唯一变量。

用法：python tools/diag_crosscheck.py [--anchor warship] [--week 2026-W37] [--all]
"""
import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import readiness_monitor as R  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--anchor", default=None)
    ap.add_argument("--week", default=None)
    ap.add_argument("--all", action="store_true", help="打印全部周的明细")
    ap.add_argument("--records", default=None,
                    help="默认取**最新日期目录**下的快照 records-<day>.jsonl；"
                         "传 'accumulated' 则用 output/ALL-records.jsonl")
    a = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    R.SIG_CFG = cfg["signals"]
    R.REL_CFG = cfg["relevance"]
    R.CLAIM_CFG = cfg["claims"]
    R.CROSS_ANCHORS = cfg["cross_anchors"]
    R.OUTLET_MAP = cfg["outlet_map"]
    R.SOURCE_BY_ID = {s["id"]: s for s in cfg["sources"]}

    # ★ 默认取**最新日期目录**的快照，而不是累积总档。
    #  两者不是一回事：累积总档永不淘汰、且会保留历次运行遗留的重复，
    #  拿它算交叉验证会得到与正式产出不一致的结论（实测多出 10 条 DVIDS 图片重复）。
    #  路径一律打印出来，杜绝「对着错文件说 OK」。
    if a.records == "accumulated":
        path = os.path.join(R.OUTPUT_DIR, "ALL-records.jsonl")
    elif a.records:
        path = a.records
    else:
        days = sorted(d for d in os.listdir(R.OUTPUT_DIR)
                      if os.path.isdir(os.path.join(R.OUTPUT_DIR, d)))
        if not days:
            print("没有日期目录")
            return 1
        path = os.path.join(R.OUTPUT_DIR, days[-1], f"records-{days[-1]}.jsonl")
    print("诊断文件:", os.path.abspath(path))
    if not os.path.exists(path):
        print("文件不存在")
        return 1
    raw = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]

    # 按当前词表重算派生字段（词表是唯一变量）
    for r in raw:
        t, s = r.get("title") or "", r.get("summary") or ""
        txt = t + " \n " + s
        r["signals"] = R.signals_of(t, s)
        r["claim_kinds"] = R.claims_of(txt)
        r["anchors"] = R.anchors_of(txt)
        r["relevance_parts"] = R.relevance_of(t, s)

    usable = [r for r in raw if R.is_displayable(r)]
    print(f"语料 {len(raw)} 条 → 通过展示闸门 {len(usable)} 条")
    print("claim_kinds 分布:", dict(Counter(k for r in raw
                                            for k in (r.get("claim_kinds") or []))))
    print()

    cc = R.build_crosscheck(raw)
    print("=== 各锚点分类统计 ===")
    for aid, st in sorted(cc["stats"].items(), key=lambda kv: -sum(kv[1].values())):
        tot = sum(st.values())
        print(f"  {aid:<22} 总计{tot:>3}  " +
              "  ".join(f"{k}={v}" for k, v in sorted(st.items())))
    print()
    print("=== pairs 分类汇总 ===")
    print(dict(Counter(p["cls"] for p in cc["pairs"])))
    print()

    sel = cc["pairs"]
    if a.anchor:
        sel = [p for p in sel if p["anchor"] == a.anchor]
    if a.week:
        sel = [p for p in sel if p["week"] == a.week]
    if not (a.all or a.anchor or a.week):
        sel = [p for p in sel if p["cls"] in ("contradiction", "mutual_assert",
                                              "mutual_denial")]

    for p in sel:
        print("=" * 88)
        print(f"[{p['cls']}] 锚点 {p['anchor']} ({p['anchor_label']})  周 {p['week']}")
        for side in ("us", "iran", "third"):
            items = p.get(side) or []
            if not items:
                continue
            print(f"  -- {side} ({len(items)})")
            for it in items:
                print(f"     · [{it['tier']}|{it['publisher']}] "
                      f"claims={it['claim_kinds']} {it['title'][:82]}")
    print()
    print("=== 周 × 锚点全表（contradiction 用 ★ 标记）===")
    for aid, info in sorted(cc["anchors"].items()):
        for row in info["rows"]:
            mark = "★" if row["cls"] == "contradiction" else " "
            print(f" {mark} {aid:<20} {row['week']}  {row['cls']:<14} "
                  f"us={row['us']:>2} iran={row['iran']:>2} third={row['third']:>2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
