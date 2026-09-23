#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_report.py —— finance-front-monitor 回源对账（独立复算每个展示数字）。

原则（与 readiness/iran 两个项目的对账工具同源）：
  1. 程序日志全绿也可能每个数字都错 —— 数字必须由**另一段代码**从落盘的
     records jsonl 独立重算，再与报告逐格比对。
  2. 格式化助手（fmt_num / delta_cell / grade_mark 等）从主脚本 import，
     **复算的是数字本身**；如果格式化函数有 bug，报告与复算会一起错，
     但这类 bug 由主脚本自检的「无脏值」用例兜底。
  3. ★ 凡是**参与比对**的路径一个都不能写死日期：默认取 output/ 下**最新**
     的日期目录，支持 --date/--out-dir/--report/--records/--archive 覆盖，
     并把实际读取的路径**打印在输出首行**——对着错文件下结论比没有校验更危险。

用法：
  python tools/verify_report.py            # 对最新日期目录对账
  python tools/verify_report.py --save     # 结果留档 output/_verify_last.json
  python tools/verify_report.py --report ... --records ... --archive ...
"""
import argparse
import glob
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import finance_monitor as R  # noqa: E402


class CK:
    """断言收集器：永不抛异常，全收齐后统一判定。"""

    def __init__(self) -> None:
        self.items: list = []

    def eq(self, name, got, want):
        ok = got == want
        self.items.append((ok, name, got, want))
        return ok

    def close(self, name, got, want, tol=1e-9):
        if got is None or want is None:
            ok = got is want
        else:
            ok = abs(float(got) - float(want)) <= tol
        self.items.append((ok, name, got, want))
        return ok

    def true(self, name, cond, detail=""):
        self.items.append((bool(cond), name, cond, detail))
        return bool(cond)

    @property
    def n(self):
        return len(self.items)

    @property
    def fails(self):
        return [(n, g, w) for ok, n, g, w in self.items if not ok]

    def report(self, md_lines: list) -> None:
        for ok, name, got, want in self.items:
            mark = "✅" if ok else "❌"
            md_lines.append(f"{mark} {name}")
            if not ok:
                md_lines.append(f"    got  = {got!r}")
                md_lines.append(f"    want = {want!r}")


def to_int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def to_float(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def section(md: str, header_pat: str) -> str:
    m = re.search(header_pat, md, re.M)
    if not m:
        return ""
    rest = md[m.start():]
    nxt = re.search(r"^## ", rest[3:], re.M)
    return rest[:3 + nxt.start()] if nxt else rest


def latest_day_dir(out_root: str):
    cands = sorted(glob.glob(os.path.join(out_root, "[0-9]" * 4 + "-" +
                                          "[0-9]" * 2 + "-" + "[0-9]" * 2)))
    return cands[-1] if cands else None


def grade_from_z(z: float, g: dict) -> str:
    az = abs(z)
    return ("升级" if az >= float(g.get("z_escalation", 3.0))
            else "警戒" if az >= float(g.get("z_alert", 2.0))
            else "关注" if az >= float(g.get("z_attention", 1.0))
            else "平稳")


def bucket_row_expected(r: dict, cfg: dict, bucket_name: str) -> str:
    """复刻渲染端分桶汇总行的每个单元格。"""
    return (f"| {bucket_name} | {r['n_series']} | "
            f"{R.grade_mark(r['top_grade'])} {r['top_grade']} | "
            f"{r['z_cell']} | {r['names']} |")


def series_bucket_row(rec: dict, cfg: dict) -> str:
    """§3–§8 里每条序列的 11 列行（从 jsonl 值复算）。"""
    return ("| {lab} | {v} | {d} | {d1} | {sp} | {d5} | {d20} | {sg} | {z} | {g} | {st} |"
            .format(lab=R.md_escape(rec.get("label", "")),
                    v=R.fmt_num(rec.get("value"), int(rec.get("decimals", 2))),
                    d=rec.get("date") or "—",
                    d1=R.delta_cell(rec, "d1"),
                    sp=R.span_cell(rec, "d1"),
                    d5=R.delta_cell(rec, "d5"),
                    d20=R.delta_cell(rec, "d20"),
                    sg=("—" if rec.get("sigma_used") is None
                        else R.fmt_num(rec["sigma_used"],
                                       max(2, int(rec.get("decimals", 2))))),
                    z=R.z_cell(rec),
                    g=R.grade_mark(rec.get("grade", "")) + " " + rec.get("grade", ""),
                    st=R.flags_cell(rec) or "正常"))


def headline_row(rec: dict, cfg: dict) -> str:
    return R.series_row(rec, cfg)


def derived_row(rec: dict) -> str:
    return (f"| {R.md_escape(rec.get('label', ''))} | "
            f"{R.md_escape(rec.get('definition', ''))} | "
            f"{R.fmt_num(rec.get('value'), int(rec.get('decimals', 2)))} | "
            f"{rec.get('unit') or '—'} | {R.delta_cell(rec, 'd1')} | "
            f"{R.z_cell(rec)} | {R.grade_mark(rec.get('grade', ''))} "
            f"{rec.get('grade', '')} | {rec.get('n', 0)} |")


def derived_bucket_row(rec: dict) -> str:
    return (f"| {R.md_escape(rec.get('label', ''))} | "
            f"{R.fmt_num(rec.get('value'), int(rec.get('decimals', 2)))} | "
            f"{rec.get('unit') or '—'} | {R.delta_cell(rec, 'd1')} | "
            f"{R.z_cell(rec)} | {R.grade_mark(rec.get('grade', ''))} "
            f"{rec.get('grade', '')} |")


def detail_block_expected(rec: dict, cfg: dict) -> list:
    """§11 每序列明细块中应出现的行片段。"""
    dec = int(rec.get("decimals", 2))
    seg = ""
    pts = rec.get("window") or []
    if pts:
        seg = " · ".join(f"{p[0]}={R.fmt_num(p[1], dec)}" for p in pts)
    head = (f"### {rec['series']} · {R.md_escape(rec.get('label', ''))}"
            f"（{rec.get('unit') or '无量纲'}，{R.cadence_label(rec.get('cadence'))}）")
    info = (f"- 累积点数 **{rec.get('hist_points', rec.get('n', 0))}**"
            f"（{rec.get('hist_first') or '—'} → {rec.get('date') or '—'}）")
    return [head, info, seg]


def main() -> int:
    ap = argparse.ArgumentParser(description="finance-front-monitor 回源对账")
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "output"))
    ap.add_argument("--date", default=None, help="日期目录名（默认取最新）")
    ap.add_argument("--report", default=None)
    ap.add_argument("--records", default=None)
    ap.add_argument("--archive", default=None,
                    help="ALL-observations.jsonl 路径（默认 <out-dir>/ALL-observations.jsonl）")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    day = args.date
    ddir = os.path.join(args.out_dir, day) if day else latest_day_dir(args.out_dir)
    if not ddir or not os.path.isdir(ddir):
        print(f"❌ 找不到输出目录：{args.out_dir}（最新={ddir}）")
        return 2
    day = os.path.basename(ddir)
    report_p = args.report or os.path.join(ddir, f"finance-digest-{day}.md")
    records_p = args.records or os.path.join(ddir, f"records-{day}.jsonl")
    composite_p = os.path.join(ddir, f"composite-{day}.json")
    archive_p = args.archive or os.path.join(args.out_dir, "ALL-observations.jsonl")

    print(f"核对报告 : {os.path.relpath(report_p, ROOT)}")
    print(f"核对记录 : {os.path.relpath(records_p, ROOT)}")
    print(f"核对合成 : {os.path.relpath(composite_p, ROOT)}")
    print(f"累积账本 : {os.path.relpath(archive_p, ROOT)}"
          f"{'（存在）' if os.path.isfile(archive_p) else '（不存在，跳过档案核对）'}")
    print("=" * 78)

    ck = CK()
    md = io.open(report_p, encoding="utf-8").read()
    recs = [json.loads(l) for l in io.open(records_p, encoding="utf-8")
            if l.strip()]
    srec = [r for r in recs if not r.get("derived")]
    drec = [r for r in recs if r.get("derived")]
    comp = json.load(io.open(composite_p, encoding="utf-8")) \
        if os.path.isfile(composite_p) else {}
    cfg = {}
    cfg_p = os.path.join(ROOT, "config.json")
    if os.path.isfile(cfg_p):
        cfg = json.load(io.open(cfg_p, encoding="utf-8"))
    if not cfg:
        cfg = R._mini_cfg()
        print("⚠ 未找到 config.json，分级阈值用自检 mini 配置近似")

    gthr = cfg.get("grading") or {}

    # ---------- F 头部 ----------
    head = md.split("## 0.", 1)[0]
    def g1(pat):
        m = re.search(pat, head)
        return to_int(m.group(1)) if m else None

    ck.eq("头部 序列条数", len(srec), g1(r"\*\*序列\*\*：(\d+) 条"))
    ck.eq("头部 派生条数", len(drec), g1(r"\*\*派生指标\*\*：(\d+) 条"))
    ck.eq("头部 有值序列数", sum(1 for r in srec if r.get("value") is not None),
          g1(r"有值 (\d+) /"))
    ck.eq("头部 无数据序列数", sum(1 for r in srec if r.get("value") is None),
          g1(r"无数据 (\d+)）"))
    tp = re.search(r"\*\*观测点合计\*\*：([\d,]+)", head)
    ck.eq("头部 观测点合计 = Σ n(srec)",
          sum(int(r.get("n") or 0) for r in srec),
          to_int(tp.group(1).replace(",", "")) if tp else None)
    ck.true("头部 出口代理已注明", "**出口代理**：" in head)
    ck.true("头部 抓取时点已注明", "**抓取时点**：" in head)

    # ---------- §1 ----------
    s1 = section(md, r"^## 1\. 一页速览")
    ck.true("§1 存在", bool(s1))
    mscore = re.search(
        r"\*\*(.+?) = ([+\-]?[\d.]+|—) (\w+)\*\*（覆盖 \*\*(\d+)/(\d+)\*\* 个分量）",
        s1)
    ck.true("§1 合成指数行存在", mscore is not None)
    if mscore and comp:
        want_score = ("—" if comp.get("score") is None
                      else f"{comp['score']:+.3f}".replace("+", "+"))
        disp = mscore.group(2)
        if comp.get("score") is None:
            ck.eq("§1 合成指数 = —（无有效分量）", disp, "—")
        else:
            ck.close("§1 合成指数值", to_float(disp), round(comp["score"], 3),
                     tol=5e-4)
        ck.eq("§1 合成指数覆盖", f"{comp.get('n_used')}/{comp.get('n_total')}",
              f"{mscore.group(4)}/{mscore.group(5)}")
        ck.eq("§1 合成指数单位", comp.get("unit", ""), mscore.group(3))

    # 档位分布
    grades_cnt: dict = {}
    for r in recs:
        gname = r.get("grade", "无数据")
        grades_cnt[gname] = grades_cnt.get(gname, 0) + 1
    dist_line = re.search(r"档位分布：(.+)", s1)
    ck.true("§1 档位分布行存在", dist_line is not None)
    if dist_line:
        disp = {}
        for tok in dist_line.group(1).split(" · "):
            m2 = re.match(r".+? (\S+) (\d+)$", tok.strip())
            if m2:
                disp[m2.group(1)] = disp.get(m2.group(1), 0) + int(m2.group(2))
        want = {g: c for g, c in grades_cnt.items() if g in R.GRADE_ORDER and c}
        ck.eq("§1 档位分布计数", disp, want)
        ck.eq("§1 档位分布合计 = 全部记录数", sum(disp.values()),
              sum(1 for r in recs
                  if r.get("grade") in R.GRADE_ORDER and grades_cnt.get(r["grade"])))

    # 1.1 分桶
    rows11 = re.findall(r"^\| (.+?) \| (\d+) \| (.+?) \| (.+?) \| (.+?) \|$",
                        s1, re.M)
    bmap = {r["bucket"]: [x for x in recs if x["bucket"] == r["bucket"]]
            for r in recs}
    ok_rows11 = 0
    for bname, n_str, topg_cell, z_cell_v, _names in rows11:
        bid = next((b for b, d in (cfg.get("buckets") or {}).items()
                    if d.get("name") == bname), None)
        if bid is None:
            continue
        rs = bmap.get(bid) or []
        ck.eq(f"§1.1 桶[{bname}] 序列数", len(rs), to_int(n_str))
        if rs:
            gr = [r for r in rs if r.get("grade") in R.GRADE_ORDER]
            topg = (min((R.GRADE_ORDER.index(r["grade"]) for r in gr),
                        default=99) if gr else 99)
            topg_name = R.GRADE_ORDER[topg] if topg < 99 else "无数据"
            ck.eq(f"§1.1 桶[{bname}] 最高档",
                  topg_cell.split(" ", 1)[-1], topg_name)
            zs = [abs(r["z"]) for r in rs if r.get("z") is not None]
            best = f"{max(zs):.2f}" if zs else "—"
            ck.eq(f"§1.1 桶[{bname}] 最大|z|", z_cell_v, best)
        ok_rows11 += 1
    ck.eq("§1.1 分桶行数 = 出现的桶数", ok_rows11, len(bmap))

    # 1.2 头条
    heads = [r for r in recs if r.get("role") == "headline"]
    missing_h = [r["series"] for r in heads
                 if headline_row(r, cfg) not in s1]
    ck.eq("§1.2 头条行逐条在场", missing_h, [])
    n_tbl = len([l for l in s1.splitlines()
                 if l.startswith("| ") and l.count("|") == 11])
    # 起始 `| ` 计入表头；分隔行 `|---|` 不以 `| ` 开头、不计入 → 期望 = 表头1 + 数据行
    ck.eq("§1.2 表行数（表头+数据）", n_tbl, len(heads) + 1)

    # ---------- §2 ----------
    s2 = section(md, r"^## 2\. 数据源与抓取状态")
    n_rows2 = len([l for l in s2.splitlines()
                   if l.startswith("| ") and not l.startswith("| 序列 ")
                   and not l.startswith("|---")])
    ck.eq("§2 源状态表行数 = 序列数", n_rows2, len(srec))

    # ---------- §3–§8 分桶章节 ----------
    for sec, title, bks, _blurb in R.BUCKET_SECTIONS:
        stxt = section(md, rf"^## {sec}\. {re.escape(title)}")
        ck.true(f"§{sec} 存在", bool(stxt))
        if not stxt:
            continue
        rs = [r for r in srec if r["bucket"] in bks]
        drs = [r for r in drec if r["bucket"] in bks]
        # 逐条 11 列行
        miss = [r["series"] for r in rs
                if series_bucket_row(r, cfg) not in stxt]
        ck.eq(f"§{sec} 序列行逐格一致", miss, [])
        # 日频子表行数
        daily = [r for r in rs if r.get("cadence") == "daily"]
        n_daily_rows = len([l for l in stxt.splitlines()
                            if l.startswith("| ") and not l.startswith("| 指标 ")
                            and not l.startswith("|---")
                            and not l.startswith("| 派生指标 ")
                            and not l.startswith("| 候选源 ")])
        want_rows = len(rs) + len(drs) + (0 if daily else 0)
        ck.eq(f"§{sec} 数据行数 = 序列+派生", n_daily_rows, want_rows)
        # 派生行
        miss_d = [r["series"] for r in drs
                  if derived_bucket_row(r) not in stxt]
        ck.eq(f"§{sec} 派生行逐格一致", miss_d, [])

    # ---------- §9 ----------
    s9 = section(md, r"^## 9\. 派生指标与跨市场联动").split("### 9.1", 1)[0]
    miss9 = [r["series"] for r in drec if derived_row(r) not in s9]
    ck.eq("§9 派生行逐格一致", miss9, [])
    n_rows9 = len([l for l in s9.splitlines() if l.startswith("| ")
                   and not l.startswith("| 派生指标 ")
                   and not l.startswith("|---")
                   and not l.startswith("| 配对 ")])
    ck.eq("§9 派生行数", n_rows9, len(drec))

    # ---------- §10 ----------
    s10 = section(md, r"^## 10\. 合成指数与分项贡献")
    comps = comp.get("components") or []
    miss10 = []
    for c in comps:
        want_row = (f"| {R.md_escape(c['label'])} | {c['weight']} | "
                    f"{'↑压力' if c['polarity'] > 0 else '↓压力'} | "
                    f"{c['z']:+.2f} | {c['contribution']:+.3f} |")
        if want_row not in s10:
            miss10.append(c["label"])
    ck.eq("§10 分量行逐格一致", miss10, [])
    ck.eq("§10 分量行数", len(re.findall(r"^\| .+ \| [\d.]+ \| (?:↑|↓)压力 \|", s10, re.M)),
          len(comps))
    if comps:
        sw = sum(float(c["weight"]) for c in comps)
        acc = sum(float(c["weight"]) * float(c["polarity"]) * float(c["z"])
                  for c in comps)
        if sw > 0 and comp.get("score") is not None:
            ck.close("§10 得分 = Σ(权重×极性×z)/Σ权重",
                     comp["score"], round(acc / sw, 3), tol=2e-3)
        ck.eq("§10 覆盖度文本",
              f"{comp.get('coverage', 0) * 100:.0f}%",
              (re.search(r"覆盖度 (\d+)%", s10).group(1) + "%"
               if re.search(r"覆盖度 (\d+)%", s10) else None))
        missm = [m for m in (comp.get("missing") or []) if f"`{m}`" not in s10]
        ck.eq("§10 缺分量披露", missm, [])

    # ---------- §11 ----------
    s11 = section(md, r"^## 11\. 序列明细与原始窗口")
    n_blocks = len(re.findall(r"^### [a-z0-9_]+ · .+（", s11, re.M))
    ck.eq("§11 明细块数 = 序列数", n_blocks, len(srec))
    miss11 = []
    for r in srec:
        for frag in detail_block_expected(r, cfg):
            if frag and frag not in s11:
                miss11.append((r["series"], frag[:48]))
                break
    ck.eq("§11 明细块逐块一致", miss11, [])

    # ---------- §12/§13 ----------
    ck.true("§12 合规章节在场", "## 12. 数据源与合规" in md)
    ck.true("§13 已知偏差章节在场", "## 13. 已知偏差与误读边界" in md)

    # ---------- 记录内部一致性 ----------
    bad_z, bad_d1, bad_pct, bad_step = [], [], [], []
    for r in recs:
        z, gr = r.get("z"), r.get("grade")
        if z is not None:
            want_g = grade_from_z(z, gthr)
            if gr != want_g:
                bad_z.append((r["series"], gr, want_g))
        elif gr in ("升级", "警戒", "关注", "平稳"):
            # 无数据 / 样本不足没有 z 是**正常**的；能定级的四档必须有 z
            bad_z.append((r["series"], gr, "z 为 None"))
        if (r.get("value") is not None and r.get("prev_value") is not None
                and r.get("d1") is not None):
            if abs((r["value"] - r["prev_value"]) - r["d1"]) > 1e-9:
                bad_d1.append((r["series"], r["d1"],
                               r["value"] - r["prev_value"]))
        p = r.get("percentile")
        if p is not None and not (0.0 <= p <= 100.0):
            bad_pct.append((r["series"], p))
        st_ = r.get("step")
        if st_ is not None and int(st_) < 1:
            bad_step.append((r["series"], st_))
    ck.eq("记录 z 与档位自洽（配置阈值）", bad_z, [])
    ck.eq("记录 d1 = value − prev_value", bad_d1, [])
    ck.eq("记录 分位在 [0,100]", bad_pct, [])
    ck.eq("记录 步长 ≥ 1", bad_step, [])

    # step==1 时 gdelta 必须等于 d1
    bad_gd = [(r["series"],) for r in recs
              if int(r.get("step") or 1) == 1 and r.get("gdelta") is not None
              and r.get("d1") is not None
              and abs(r["gdelta"] - r["d1"]) > 1e-12]
    ck.eq("记录 步长1时 gdelta = d1", bad_gd, [])

    # ---------- 时间校验：未来日期 / 值日期不得晚于运行日 ----------
    # 教训：日期口径一旦失控（解析失败绕过时间窗、first_seen 跨午夜），
    # 报告会「看起来正常」地把未来日期或过期条目混进快照。
    bad_future = [(r["series"], r.get("date"), r.get("d1_date"))
                  for r in recs
                  for d in (r.get("date"), r.get("d1_date"))
                  if isinstance(d, str) and d[:10] > day]
    ck.eq("记录值日期 ≤ 运行日（无未来日期）", bad_future[:6], [])

    # ---------- 单位校验：单位字段与量纲要自洽 ----------
    bad_unit = [(r["series"], r.get("unit"))
                for r in recs
                if not r.get("unit") or r.get("unit") in ("?", "", None)]
    ck.eq("每条序列都声明了单位", bad_unit[:6], [])
    # 百分比序列的变动必须以 bp/pp 表述，不能以 % 表述（4.15%→4.23% 是 +8bp 不是 +1.93%）
    bad_pct_unit = [(r["series"], r.get("unit")) for r in recs
                    if r.get("unit") == "%" and r.get("d1") is not None
                    and r.get("delta_unit") not in (None, "bp", "pp")]
    ck.eq("百分比序列变动单位 ∈ {bp,pp}", bad_pct_unit[:6], [])

    # ---------- 计算校验：年化波动率必须在合理量级 ----------
    # 教训（2026-09-20）：rollstd 用价格**差值**而非**收益率**算 Brent 年化波动率，
    # 差值 σ（~$1）未除以价格水平（~$70）再 × √252 × 100 → 算出 4,634%（实际 ~20-40%）。
    # 差两个数量级却不会报错，只能靠量级断言兜住。
    vol_cfg = set()
    for d in (cfg.get("derived") or []):
        if d.get("kind") == "rollstd" and d.get("return_base"):
            vol_cfg.add(d.get("id"))
    bad_vol = [(r["series"], r.get("value"))
               for r in recs
               if r.get("series") in vol_cfg and r.get("value") is not None
               and not (0.5 <= float(r["value"]) <= 300.0)]
    ck.eq("年化波动率量级合理 ∈ [0.5%, 300%]", bad_vol[:6], [])
    # 兜底：任何以 % 为单位的数值都不该超过 10000（量纲错误会一眼看出来）
    bad_pct_val = [(r["series"], r.get("value")) for r in recs
                   if r.get("unit") == "%" and r.get("value") is not None
                   and abs(float(r["value"])) > 10000.0]
    ck.eq("百分比数值 ≤ 10000（量纲兜底）", bad_pct_val[:6], [])

    # ---------- 脏值守卫 ----------
    dirty = re.findall(
        r"(?<![A-Za-z])(NaN|nan|Infinity|None|1970-01|epoch|1970-)(?![A-Za-z])",
        md)
    ck.eq("全报告无脏值（NaN/None/纪元零值）★★", dirty[:6], [])

    # ---------- 累积账本 ----------
    if os.path.isfile(archive_p):
        acc = {}
        for line in io.open(archive_p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            acc[(o.get("run_tag", ""), o.get("series", ""))] = o
        miss_acc = [r["series"] for r in recs
                    if (r.get("run_tag"), r["series"]) not in acc]
        ck.eq("累积账本含本轮全部记录", miss_acc, [])
        diff_acc = []
        for r in recs:
            o = acc.get((r.get("run_tag"), r["series"]))
            if o and r.get("value") is not None and o.get("value") is not None:
                if abs(float(o["value"]) - float(r["value"])) > 1e-9:
                    diff_acc.append((r["series"], o.get("value"), r.get("value")))
        ck.eq("累积账本数值一致", diff_acc, [])
    else:
        print("（跳过累积账本核对：文件不存在）")

    # ---------- 汇总 ----------
    print("=" * 78)
    fails = ck.fails
    lines: list = []
    ck.report(lines)
    print("\n".join(lines))
    print("=" * 78)
    verdict = (f"结果：{ck.n - len(fails)}/{ck.n} 项一致"
               + ("" if not fails else f"，{len(fails)} 项不一致"))
    print(("✅ " if not fails else "❌ ") + verdict)
    if args.save:
        out = {"checked_at": R.now_utc().isoformat(timespec="seconds"),
               "report": os.path.relpath(report_p, ROOT),
               "records": os.path.relpath(records_p, ROOT),
               "total": ck.n, "failed": len(fails),
               "failures": [{"check": n, "got": repr(g), "want": repr(w)}
                            for n, g, w in fails]}
        save_p = os.path.join(args.out_dir, "_verify_last.json")
        with io.open(save_p, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"已留档 → {os.path.relpath(save_p, ROOT)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
