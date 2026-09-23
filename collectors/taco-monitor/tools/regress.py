# -*- coding: utf-8 -*-
"""口径变更回归比对 —— 证明「换口径没动过历史读数」。

为什么需要这个脚本（2026-09-23 实测踩到的判据陷阱）：

    把改动前/后的两份产物直接逐点相减，差异不一定来自代码。
    源数据本身在动 —— 实测口径回退后 TACO 在全区间出现 max|Δ|=1.175e-02，
    看着像「新代码改了历史值」；但按「5 个原始输入是否逐字相同」切分日期后：
        输入完全相同的 608 天 -> max|Δ| = 0.000e+00（bit 级相同）
        输入有变化的  1 天（最新）-> 1.175e-02（USGG10YR / RCPPTAPP 被上游修订）
    即代码改动的影响是**零**。

    这个陷阱特别毒在于：源修订影响的多是最新一两天，而「新代码只在尾部出错」
    （新字段暖机 / ffill 边界 / 最新值未落定）症状完全重合，光看差异分布分不出来。

所以判据是「**输入相同日必须逐点相等**」，不是「全区间最大差 ≈ 0」。

用法：
    python tools/regress.py <旧产物.csv> <新产物.csv>
    python tools/regress.py output/_archive/taco-latest.20260922-7factor.csv \\
                            output/taco-latest.csv
    python tools/regress.py --list          # 列出可用的归档快照

退出码：0 = 通过（输入相同日逐点一致）；1 = 输入相同日出现实质差异。
"""
import csv
import os
import sys

# 入指数的 5 个原始输入 —— 判定「两次运行读到的是不是同一批数」只看这几列。
INPUT_COLS = ["USGG10YR", "USSWIT1_proxy_T5YIE", "RCPPTAPP_approve",
              "INDU", "CO1_Brent"]
# 派生量：这些必须在「输入相同日」上逐点 bit 级相等。
DERIVED_COLS = ["z_10Y", "z_swap", "z_approval", "z_DJIA", "z_Brent",
                "composite_S", "TACO_Index_T"]
# 容忍：浮点比较。要求严格相等（0.0），但留一个极小的显式容差以吸收
# 「同一算法在不同平台的最后一个 ulp」这类无意义差异 —— 它会打印出来。
TOL = 1e-12


def load(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return {r["Date"]: r for r in csv.DictReader(fh) if r.get("Date")}


def _num(row, col):
    v = (row.get(col) or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _maxdiff(rows_old, rows_new, dates, col):
    worst, worst_d, n = 0.0, None, 0
    for d in dates:
        a, b = _num(rows_old[d], col), _num(rows_new[d], col)
        if a is None or b is None:
            continue
        n += 1
        x = abs(a - b)
        if x > worst:
            worst, worst_d = x, d
    return worst, worst_d, n


def main(argv):
    if "--list" in argv:
        for root in ("output", "output/_archive", "output/history"):
            if not os.path.isdir(root):
                continue
            for f in sorted(os.listdir(root)):
                if f.endswith(".csv"):
                    print("  %s" % os.path.join(root, f))
        return 0
    if len(argv) < 3:
        print(__doc__)
        return 2

    old_p, new_p = argv[1], argv[2]
    for p in (old_p, new_p):
        if not os.path.exists(p):
            print("!! 找不到文件: %s" % p)
            return 2

    a, b = load(old_p), load(new_p)
    common = sorted(set(a) & set(b))
    print("=" * 72)
    print("口径回归比对")
    print("  旧: %s  (%d 行)" % (old_p, len(a)))
    print("  新: %s  (%d 行)" % (new_p, len(b)))
    print("  重叠 %d 天: %s .. %s" % (len(common), common[0], common[-1]))
    print("=" * 72)

    if not common:
        print("!! 无重叠日期，无法比对")
        return 2

    # ---- 关键一步：按「原始输入是否逐字相同」切分日期 ----
    same, changed = [], []
    for d in common:
        ok = True
        for c in INPUT_COLS:
            va, vb = (a[d].get(c) or "").strip(), (b[d].get(c) or "").strip()
            if va != vb:
                ok = False
                break
        (same if ok else changed).append(d)

    print()
    print("切分（判据核心：源数据在动，不能全区间一把比）")
    print("  原始输入完全相同的日期: %4d 天   <-- 只在这里要求逐点相等"
          % len(same))
    print("  原始输入有变化的日期  : %4d 天 %s"
          % (len(changed),
             ("-> " + ", ".join(changed[:6])
              + (" ..." if len(changed) > 6 else "")) if changed else ""))

    if changed:
        print()
        print("  变化明细（前 5 天）:")
        for d in changed[:5]:
            parts = []
            for c in INPUT_COLS:
                va, vb = (a[d].get(c) or "").strip(), (b[d].get(c) or "").strip()
                if va != vb:
                    parts.append("%s %s->%s" % (c, va, vb))
            print("    %s  %s" % (d, "; ".join(parts)))

    # ---- 在「输入相同日」上比对派生量 ----
    print()
    print("派生量比对")
    print("  %-14s %-24s %-24s" % ("列", "输入相同日 max|Δ|", "输入变化日 max|Δ|"))
    print("  " + "-" * 66)
    fails = []
    for c in DERIVED_COLS:
        if c not in (a[common[0]].keys() if common else []):
            print("  %-14s (两版均无此列，跳过)" % c)
            continue
        w1, d1, n1 = _maxdiff(a, b, same, c)
        w2, d2, n2 = _maxdiff(a, b, changed, c)
        flag = ""
        if n1 and w1 > TOL:
            flag = "   <== FAIL"
            fails.append((c, w1, d1))

        def fmt(w, d, n):
            if not n:
                return "无共同有效值"
            if w == 0.0:
                return "0（逐点一致）"
            return "%.3e @ %s" % (w, d)

        print("  %-14s %-24s %-24s%s"
              % (c, fmt(w1, d1, n1), fmt(w2, d2, n2), flag))

    print()
    print("=" * 72)
    if fails:
        print("!! 不通过：以下列在「原始输入完全相同」的日期上仍有差异 ——")
        for c, w, d in fails:
            print("     %s  max|Δ|=%.3e  @ %s" % (c, w, d))
        print("   这说明差异**不是**源修订造成的，需要排查代码。")
        print("=" * 72)
        return 1
    print("通过：在 %d 个「原始输入完全相同」的日期上，" % len(same))
    print("      全部派生量逐点一致（max|Δ| = 0）——")
    print("      即本次改动**没有改动任何历史读数**。")
    if changed:
        print("      其余 %d 天的差异可归因为上游源修订，非代码问题。"
              % len(changed))
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
