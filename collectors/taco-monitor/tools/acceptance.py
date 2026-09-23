# -*- coding: utf-8 -*-
"""端到端验收：selftest + 真实抓取 + 幂等性 + 产物完整性 + 对账。"""
import json
import os
import subprocess
import sys
import time

PY = sys.executable
# 以本文件位置定位项目根，而不是 cwd —— 从别处调用时也不会跑错目录
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "output")
sys.path.insert(0, HERE)
from tacolib import net  # noqa: E402

results = []


def sh(label, args, timeout=400):
    t0 = time.time()
    r = subprocess.run([PY, "-u", "taco_monitor.py"] + args,
                       capture_output=True, timeout=timeout, cwd=HERE)
    el = time.time() - t0
    out = r.stdout.decode("utf-8", "replace")
    err = r.stderr.decode("utf-8", "replace")
    results.append({"label": label, "rc": r.returncode, "elapsed": el,
                    "out": out, "err": err})
    return r.returncode, out, err


def effective(path):
    """返回实际生效的文件路径（与对账环节共用 net.resolve_actual）。

    目标文件被 WPS/Excel 占用时，程序会退写 `<名>.locked-<时间戳><ext>`。
    此时标准名上留着的是**上一轮的旧内容** —— 直接校验标准名会拿到过期数据，
    报出"列缺失"这种假故障。所以这里取「最新的那份」：标准名与降级名比 mtime。
    """
    return net.resolve_actual(path)


print("=" * 72)
print("端到端验收")
print("=" * 72)

# 1. selftest
rc, out, err = sh("selftest", ["--selftest"], timeout=120)
print("\n[1] selftest  rc=%s  %.1fs" % (rc, results[-1]["elapsed"]))
for ln in out.strip().splitlines()[-3:]:
    print("    " + ln)

# 2. 真实跑
rc, out, err = sh("real-run", [], timeout=400)
print("\n[2] real run  rc=%s  %.1fs" % (rc, results[-1]["elapsed"]))
for ln in out.strip().splitlines():
    if any(k in ln for k in ("TACO =", "TACO-6 =", "TACO-7", "项通过", "[OK]",
                             "源就绪", "xlsx", "水位断裂", "霍尔木兹",
                             "第 7 因子", "Bonnast", "观察指标", "已移除",
                             "真实观测日", "角色")):
        print("    " + ln.strip())

# 3. 幂等性（再跑一次，history 应为 new=0 revised=0）
rc, out, err = sh("idempotent", [], timeout=400)
print("\n[3] idempotency  rc=%s  %.1fs" % (rc, results[-1]["elapsed"]))
for ln in out.strip().splitlines():
    if "history" in ln and "TACO_INDEX" in ln:
        print("    " + ln.strip())

# 4. 产物完整性
print("\n[4] artifacts")
need = [
    "LATEST.md", "taco-latest.csv", "taco-dashboard.html", "taco_index.xlsx",
    "_source_status.json", "_verify_last.json", "ALL-observations.jsonl",
]
for f in need:
    p = os.path.join(OUT, f)
    ok = os.path.exists(p)
    sz = os.path.getsize(p) if ok else 0
    eff, fb = effective(p)
    note = ""
    if fb:
        note = "  [被占用 -> 看 %s, %s bytes]" % (
            os.path.basename(eff), os.path.getsize(eff))
    print("    %-28s %s  %s bytes%s"
          % (f, "OK" if ok else "MISSING", sz, note))

# 4b. 列契约产物 + CSV 列检查
# schema_problems 计入最终 ALL STEPS 判定：列名齐 ≠ 列里有值（2026-09-22 实测
# TRHBCCCD_n_total 整列写空而列名全在），光打印会漏掉。
schema_problems = []

# 期望列**由 config 推导**，不写死 —— 否则切模式（composite <-> observe）时
# 验收脚本会拿旧模式清单去对，报出「列缺失」这种假故障。
_cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
_ef = _cfg.get("extended_factor") or {}
_ef2 = _cfg.get("extended_factor_2") or {}
_mode = str(_ef.get("mode", "composite")).lower()
_hormuz_obs_only = (_mode == "observe" and _ef.get("enabled", True))
_ext_on = (_mode == "composite" and _ef.get("enabled", True))
_ext2_on = bool(_ef2.get("enabled", True))

print("\n[4b] 列契约 / 文件产物")
print("    模式: extended_factor.mode=%s（霍尔木兹 %s）；"
      "extended_factor_2.enabled=%s（汇率）"
      % (_mode,
         "只观察、不进指数" if _hormuz_obs_only else "并入合成",
         _ext2_on))

for hf in (["TACO_INDEX.jsonl"] +
           (["TACO6_INDEX.jsonl", "TACO6L_INDEX.jsonl"] if _ext_on else []) +
           (["TACO7_INDEX.jsonl", "TACO7L_INDEX.jsonl",
             "BONBAST_USD_sell_rial.jsonl"] if _ext2_on else [])):
    hp = os.path.join(OUT, "history", hf)
    print("    %-28s %s" % ("history/" + hf,
                            "OK" if os.path.exists(hp) else "MISSING"))

want = ["TRHBCCCD_n_total"]
if _ext_on:
    want += ["z_transit", "composite_S6", "TACO6_Index_T6",
             "z_transit_level", "composite_S6L", "TACO6L_Index_T6L"]
if _ext2_on:
    want += ["BONBAST_USD_sell_rial", "z_rial", "composite_S7",
             "TACO7_Index_T7", "z_rial_level", "composite_S7L",
             "TACO7L_Index_T7L"]
# 反向断言：**不该在的列必须不在**。观察模式下 TACO-6/7 的列若出现，
# 说明「说好不进指数却算了」，比列缺失更严重。
forbidden = []
if _hormuz_obs_only:
    forbidden += ["z_transit", "composite_S6", "TACO6_Index_T6",
                  "z_transit_level", "composite_S6L", "TACO6L_Index_T6L"]
if not _ext2_on:
    forbidden += ["BONBAST_USD_sell_rial", "z_rial", "composite_S7",
                  "TACO7_Index_T7", "z_rial_level", "composite_S7L",
                  "TACO7L_Index_T7L"]

csvp = os.path.join(OUT, "taco-latest.csv")
eff, fell_back = effective(csvp)
if os.path.exists(eff):
    import csv as _csv
    with open(eff, encoding="utf-8-sig", newline="") as fh:
        _rows = list(_csv.DictReader(fh))
    hdr = list(_rows[0].keys()) if _rows else []
    miss = [c for c in want if c not in hdr]
    extra = [c for c in forbidden if c in hdr]
    print("    校验对象 %s%s"
          % (os.path.basename(eff),
             "（标准名被占用，已改看降级文件）" if fell_back else ""))
    print("    csv 列数 %d（5 因子 13 + 观察 %d）"
          % (len(hdr), 1 if _hormuz_obs_only else 0))
    print("    csv 应有列 %s -> %s"
          % (want, "OK" if not miss else "MISSING %s" % miss))
    if miss:
        schema_problems.append("csv 缺列 %s" % miss)
    if extra:
        schema_problems.append("csv 出现不该有的列 %s（说不进指数却算了）" % extra)
        print("    !! csv 不應出现的列 -> %s" % extra)
    else:
        print("    csv 不应出现的列 -> OK（未混入未启用的因子）")
    # 光有列名不算数，必须真有值 —— 2026-09-22 实测 TRHBCCCD_n_total 整列写空
    # （取数取成了原始输入字典），而列名是全的。
    for c in want:
        if c in hdr:
            n = sum(1 for r in _rows if (r.get(c) or "").strip() != "")
            flag = "OK" if n > 0 else "**整列为空**"
            if n <= 0:
                schema_problems.append("csv 列 %s 整列为空" % c)
            print("    csv 列有值 %-20s %5d / %d  %s" % (c, n, len(_rows), flag))

# 4c. Excel 同名列也必须真有值（曾经 CSV 与 Excel 同源一起写空）
xlp = os.path.join(OUT, "taco_index.xlsx")
ex, _ = effective(xlp)
if os.path.exists(ex):
    try:
        import openpyxl as _ox
        _wb = _ox.load_workbook(ex, read_only=True, data_only=True)
        _ws = _wb["TACO 指数（全量）"]
        _it = _ws.iter_rows(values_only=True)
        _h = list(next(_it))
        _colv = {c: [] for c in want if c in _h}
        for r in _it:
            for c in _colv:
                _colv[c].append(r[_h.index(c)])
        _wb.close()
        for c in want:
            if c not in _h:
                schema_problems.append("xlsx 列 %s 缺失" % c)
                print("    xlsx 列缺失               %-20s MISSING" % c)
            else:
                n = sum(1 for v in _colv[c] if v is not None and str(v) != "")
                if n <= 0:
                    schema_problems.append("xlsx 列 %s 整列为空" % c)
                print("    xlsx 列有值 %-20s %5d  %s"
                      % (c, n, "OK" if n > 0 else "**整列为空**"))
    except Exception as e:  # noqa: BLE001
        schema_problems.append("xlsx 校验异常: %s" % e)
        print("    xlsx 校验跳过: %s" % e)

# 4b-2. 模式一致性：报告 / 看板里必须出现「本轮角色」的措辞，且不得出现
#       已移除因子的措辞。文字与配置对不上，是最容易被忽略的一类不一致 ——
#        数据全对，但报告告诉读者的是一个已经改掉的旧口径。
print("\n[4b-2] 报告 / 看板与配置的模式一致性")
if _hormuz_obs_only:
    _must = [("观察指标", "声明观察指标"), ("不进", "声明不进指数"),
             ("霍尔木兹观察面板", "观察面板")]
    _must_not = [("TACO-6L", "TACO-6L"), ("TACO7", "TACO-7")]
else:
    _must = [("TACO-6L", "TACO-6L"), ("水位面板", "水位面板")]
    _must_not = []
for fp, label in ((os.path.join(OUT, "LATEST.md"), "报告"),
                  (os.path.join(OUT, "taco-dashboard.html"), "看板")):
    e2, _ = effective(fp)
    if not os.path.exists(e2):
        schema_problems.append("%s 不存在" % label)
        continue
    txt = open(e2, encoding="utf-8").read()
    for key, disp in _must:
        ok = key in txt
        if not ok:
            schema_problems.append("%s 缺 %s 措辞" % (label, key))
        print("    %s含 %-16s %s" % (label, disp, "OK" if ok else "MISSING"))
    for key, disp in _must_not:
        bad = key in txt
        if bad:
            schema_problems.append("%s 仍出现已停用口径 %s" % (label, key))
        print("    %s不含 %-16s %s" % (label, disp, "OK" if not bad else "仍出现"))

# 4b-3. 观察模式下核心恒等式：CSV 里的 composite_S 必须逐行等于 5 个 z 的均值。
#       这是「观察列真的没进指数」的**产物级**证明（不是读内存）。
if _hormuz_obs_only and os.path.exists(eff):
    _zc = ["z_10Y", "z_swap", "z_approval", "z_DJIA", "z_Brent"]
    if all(c in hdr for c in _zc) and "composite_S" in hdr:
        _bad_i = None
        for _i, _r in enumerate(_rows):
            try:
                _zs = [float(_r[c]) for c in _zc]
                _s = float(_r["composite_S"])
            except (TypeError, ValueError):
                continue
            if abs(sum(_zs) / 5.0 - _s) > 1e-6:
                _bad_i = (_i + 2, _r.get("Date"))
                break
        if _bad_i:
            schema_problems.append(
                "csv composite_S 不等于 5 个 z 均值（行%s/%s）—— 观察列可能进了合成"
                % _bad_i)
            print("    csv S=mean(5 个 z) 逐行校验 -> FAIL @ %s" % (_bad_i,))
        else:
            print("    csv S=mean(5 个 z) 逐行校验 -> OK（观察列未进合成）")
    else:
        print("    csv S=mean(5 个 z) 逐行校验 -> 跳过（列不全）")

# 把列契约结论并入总判定（rc 只反映子进程退出码，覆盖不到产物内容）
results.append({"label": "artifact-columns",
                "rc": 1 if schema_problems else 0, "elapsed": 0.0,
                "out": "", "err": "\n".join(schema_problems)})
if schema_problems:
    print("\n    !! 列契约 / 模式一致性问题（计入失败）: %s"
          % "；".join(schema_problems))

# 5. 对账结果
vp = os.path.join(OUT, "_verify_last.json")
if os.path.exists(vp):
    v = json.load(open(vp, encoding="utf-8"))
    print("\n[5] reconciliation: ok=%s  %d/%d passed, %s warned"
          % (v.get("ok"), v.get("total", 0) - v.get("failed", 0)
             - v.get("warned", 0), v.get("total", 0), v.get("warned", 0)))
    for c in v.get("checks", []):
        if not c.get("ok"):
            tag = "WARN" if c.get("level") == "warn" else "FAIL"
            print("    %s: %s - %s" % (tag, c["name"], c["detail"]))

# 6. 最新读数
cp = os.path.join(OUT, "_source_status.json")
if os.path.exists(cp):
    s = json.load(open(cp, encoding="utf-8"))
    print("\n[6] sources: gate_passed=%s" % s.get("gate_passed"))
    for st in s.get("statuses", []):
        print("    %-22s %-18s n=%-5s last=%s"
              % (st.get("id"), st.get("status"), st.get("n"),
                 st.get("last_date")))

# 7. history 累积
hd = os.path.join(OUT, "history")
if os.path.isdir(hd):
    print("\n[7] history files")
    for f in sorted(os.listdir(hd)):
        p = os.path.join(hd, f)
        n = sum(1 for _ in open(p, encoding="utf-8"))
        print("    %-30s %d rows" % (f, n))

print("\n" + "=" * 72)
allok = all(r["rc"] == 0 for r in results)
print("ALL STEPS rc=0: %s" % allok)
print("=" * 72)
