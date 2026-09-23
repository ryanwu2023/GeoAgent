# -*- coding: utf-8 -*-
"""打印 PortWatch 海峡日通行量（含分船型拆解），用于与外部来源（彭博/Kpler/straits.live 等）对表。

为什么需要它：PortWatch 的 `n_total` **只数 5 类商船**（集装箱/干散/杂货/滚装/油轮），
军舰、护航、渔船、拖轮、小艇与关闭 AIS 的船一概不计；且一艘船穿过边界只记 1 次、
48 小时内不重复计、每周三才更新一次。因此与媒体实时数字对不上是**口径差**，不是抓取错误。
对表时必须把拆解一起打出来，否则无法判断差异来自「少算了哪一类」还是「真的不同步」。

用法:
  python tools/dump_chokepoint.py                      # 霍尔木兹，最近 15 天
  python tools/dump_chokepoint.py --id chokepoint4 --days 30
  python tools/dump_chokepoint.py --ref 2026-09-06=8 2026-09-09=7   # 附外部参照值并算差
  python tools/dump_chokepoint.py --from 2026-08-01 --to 2026-09-10 \
      --ref-from 2026-08-01 --ref-values "9,3,12,7,..."             # 媒体给的连续序列
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request

SERVICE = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
           "Daily_Chokepoints_Data/FeatureServer/0/query")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")


def egress():
    """取环境代理出口；绝不写死端口。"""
    for k in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY"):
        m = re.search(r"https?://([^/]+)", os.environ.get(k) or "")
        if m:
            return "http://" + m.group(1)
    return None


def get(url, timeout=40):
    p = egress()
    handlers = [urllib.request.ProxyHandler({"http": p, "https": p})] if p else []
    op = urllib.request.build_opener(*handlers)
    r = op.open(urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json,*/*"}), timeout=timeout)
    return json.loads(r.read())


def parse_ref_series(from_date, values):
    """把「从某日起的连续逐日序列」展开成 {date: value}。
    媒体常给一串裸数字（彭博/劳氏/Kpler 的日图标注），必须按日顺次对齐。"""
    out = {}
    if not (from_date and values):
        return out
    d0 = dt.date.fromisoformat(from_date)
    for i, v in enumerate(values):
        v = v.strip()
        if v == "" or v.lower() in ("-", "na", "n/a", "null"):
            continue
        out[str(d0 + dt.timedelta(days=i))] = float(v)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", default="chokepoint6", help="chokepoint6=霍尔木兹 chokepoint4=曼德海峡")
    ap.add_argument("--days", type=int, default=15)
    ap.add_argument("--from", dest="d_from", help="起始日 YYYY-MM-DD（含）")
    ap.add_argument("--to", dest="d_to", help="结束日 YYYY-MM-DD（含）")
    ap.add_argument("--ref", nargs="*", default=[],
                    help="外部参照值，形如 2026-09-06=8")
    ap.add_argument("--ref-from", dest="ref_from",
                    help="参照序列起始日；与 --ref-values 配套")
    ap.add_argument("--ref-values", dest="ref_values", default="",
                    help="逗号分隔的逐日参照值，从 --ref-from 起顺次对齐")
    ap.add_argument("--ref-file", dest="ref_file",
                    help="参照档 CSV（两列：date,value；行首 # 注释），可复用外部序列")
    a = ap.parse_args()

    ref = {}
    for kv in a.ref:
        if "=" in kv:
            d, v = kv.split("=", 1)
            ref[d.strip()] = float(v)
    if a.ref_file:
        with open(a.ref_file, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.split("#", 1)[0].strip()
                if not ln:
                    continue
                parts = re.split(r"[,\t]", ln)
                if len(parts) >= 2 and re.match(r"\d{4}-\d{2}-\d{2}", parts[0].strip()):
                    ref[parts[0].strip()] = float(parts[1].strip())
        print(f"# 已载入参照档 {a.ref_file}（{len(ref)} 个日期）")
    vals = [x for x in (a.ref_values or "").split(",") if x.strip() != ""] \
        if a.ref_values else []
    ref.update(parse_ref_series(a.ref_from, vals))

    where = "portid='%s'" % a.id
    if a.d_from:
        where += " AND date >= timestamp '%s 00:00:00'" % a.d_from
    if a.d_to:
        where += " AND date <= timestamp '%s 23:59:59'" % a.d_to
    url = (SERVICE + "?where=" + urllib.parse.quote(where)
           + "&outFields=*&orderByFields=" + urllib.parse.quote("date ASC")
           + "&resultRecordCount=" + str(2000 if (a.d_from or a.d_to) else max(a.days, 1))
           + "&returnGeometry=false&f=json")
    d = get(url)
    rows = [f["attributes"] for f in (d.get("features") or [])]
    if not rows:
        print("无数据返回：", json.dumps(d)[:300])
        return 1
    rows.sort(key=lambda x: str(x.get("date")))
    if not (a.d_from or a.d_to):
        rows = rows[-max(a.days, 1):]
    name = rows[-1].get("portname")
    print(f"# {name}（{a.id}）· PortWatch · 单位：艘次（仅 5 类商船）")
    hdr = f"{'日期':11s}{'n_total':>8s}{'货船':>5s}{' 油轮':>5s}{'集装箱':>6s}{'干散':>5s}{'杂货':>5s}{'RoRo':>5s}"
    if ref:
        hdr += f"{'参照':>6s}{'差':>5s}{'差%':>7s}"
    print(hdr)
    for r in rows:
        line = (f"{str(r.get('date')):11s}{r.get('n_total'):>8}{r.get('n_cargo'):>5}"
                f"{r.get('n_tanker'):>5}{r.get('n_container'):>6}{r.get('n_dry_bulk'):>5}"
                f"{r.get('n_general_cargo'):>5}{r.get('n_roro'):>5}")
        b = ref.get(str(r.get("date")))
        if ref:
            dv = (r.get("n_total") or 0) - b if b is not None else None
            line += (f"{('%.0f' % b) if b is not None else '-':>6}"
                     f"{(('%+d' % dv) if dv is not None else '-'):>5}"
                     f"{(('%+.0f%%' % (dv / b * 100)) if (dv is not None and b) else '-'):>7}")
        print(line)

    tot = sum(r.get("n_total") or 0 for r in rows)
    print(f"\n{len(rows)} 天合计 {tot} 艘次，日均 {tot/len(rows):.1f}")
    sub = [r for r in rows if str(r.get("date")) in ref]
    if sub:
        s1 = sum(r.get("n_total") or 0 for r in sub)
        s2 = sum(ref[str(r.get("date"))] for r in sub)
        print(f"对表 {len(sub)} 天：本侧 {s1} vs 参照 {s2:.0f}，差 {s1-s2:+.0f}"
              f"（{(s1/s2-1)*100:+.1f}%）")
        signs = [((r.get("n_total") or 0) - ref[str(r.get("date"))]) for r in sub]
        neg = sum(1 for x in signs if x < 0); pos = sum(1 for x in signs if x > 0)
        zero = len(signs) - neg - pos
        print(f"差值符号分布：负 {neg} / 零 {zero} / 正 {pos}"
              f"（{neg/len(signs)*100:.0f}% 为负）")
        # 相关性：口径差通常仍高度同向，抓取错位则相关崩掉
        xs = [ref[str(r.get("date"))] for r in sub]
        ys = [r.get("n_total") or 0 for r in sub]
        n = len(xs)
        if n >= 3:
            mx = sum(xs) / n; my = sum(ys) / n
            cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            vx = sum((x - mx) ** 2 for x in xs) ** 0.5
            vy = sum((y - my) ** 2 for y in ys) ** 0.5
            rho = cov / (vx * vy) if vx and vy else float("nan")
        print(f"皮尔逊相关 r = {rho:.3f}"
              f"（口径差一般仍 >0.6；<0.3 要先查日期错位/漏日）")
        # 平移扫描：判定「日期错位/时区」的唯一硬证据。
        # 真实错位会让 lag≠0 的相关性显著高于 lag=0；反之若 lag=0 一枝独秀，
        # 说明日期已对齐，差异只能来自版本/口径，不是错位。
        series = [(str(r.get("date")), r.get("n_total") or 0) for r in rows]
        print("\n  平移扫描（lag=外部值整体前移的天数；lag=0 应同时取得最大精确相同数与最高 r）")
        scan = []
        for lag in range(-3, 4):
            pairs = []
            for i in range(len(series)):
                j = i + lag
                if 0 <= j < len(series) and series[j][0] in ref:
                    pairs.append((series[i][1], ref[series[j][0]]))
            if len(pairs) < 5:
                continue
            ys2 = [p[0] for p in pairs]
            xs2 = [p[1] for p in pairs]
            same2 = sum(1 for x, y in zip(ys2, xs2) if x == y)
            mae2 = sum(abs(x - y) for x, y in zip(ys2, xs2)) / len(ys2)
            n2 = len(ys2)
            mx2 = sum(xs2) / n2
            my2 = sum(ys2) / n2
            cv = sum((x - mx2) * (y - my2) for x, y in zip(xs2, ys2))
            vx2 = sum((x - mx2) ** 2 for x in xs2) ** 0.5
            vy2 = sum((y - my2) ** 2 for y in ys2) ** 0.5
            r2 = (cv / (vx2 * vy2)) if (vx2 and vy2) else 0.0
            scan.append((lag, n2, same2, mae2, r2))
        # 最优 = 精确相同数最多；并列则取 MAE 最小
        best_lag = max(scan, key=lambda t: (t[2], -t[3]))[0] if scan else 0
        print(f"    {'lag':>4s}{'重叠天':>7s}{'精确相同':>9s}{'MAE':>7s}{'r':>8s}")
        for lag, n2, same2, mae2, r2 in scan:
            mark = "  ← 最优" if lag == best_lag else ""
            print(f"    {lag:>4d}{n2:>7d}{same2:>9d}{mae2:>7.2f}{r2:>8.3f}{mark}")
        if best_lag == 0:
            print("    → lag=0 最优：**日期已对齐，不存在时区/日期错位**；"
                  "差异只可能来自版本(修订/快照)或口径定义。")
        else:
            print(f"    → lag={best_lag} 更优：疑似日期错位/时区差，先查上游的日界定义！")
        # 差额集中度：版本差 vs 覆盖面差的判据
        allp = [(r.get("n_total") or 0, ref[str(r.get("date"))]) for r in rows
                if str(r.get("date")) in ref]
        kind = ""
        if allp:
            ds = sorted(abs(b - o) for o, b in allp)
            tot_abs = sum(ds)
            hi = [x for x in ds if x >= 3]
            lo = [x for x in ds if x < 3]
            if hi and tot_abs:
                hi_m = sum(hi) / len(hi)
                lo_m = (sum(lo) / len(lo)) if lo else 0.0
                ratio = (hi_m / lo_m) if lo_m else float("inf")
                kind = "版本/快照差" if ratio > 3 else "均匀覆盖缺口"
                print(f"    差额集中度：|差|≥3 的 {len(hi)} 天贡献 "
                      f"{sum(hi)/tot_abs*100:.0f}% 的绝对差，日均 {hi_m:.1f}；"
                      f"其余 {len(lo)} 天日均 {lo_m:.2f}（相差 {ratio:.0f} 倍）"
                      f" → {kind}")
        # 判定：把「日期对齐」与「差额形态」两个维度合起来给结论
        print("\n  【判定】", end="")
        if best_lag != 0:
            print("先查日期错位（lag≠0 更优），不要在口径上找原因。")
        elif kind == "版本/快照差":
            print("日期已对齐 + 差额集中在少数日 → **同一数据集的版本差**"
                  "（对方用了未修订的实时快照，或相反）。这不是抓取错误，"
                  "也不完全是口径差——两边大部分日子本来就同源（见「精确相同」天数）。")
        elif kind == "均匀覆盖缺口":
            print("日期已对齐 + 差额均匀分布 → **口径差**"
                  "（对方统计范围多/少一整类船舶，如含军舰、护航、渔船或未做去重）。"
                  "改抓取无用，要比就比趋势与均值。")
        else:
            print("日期已对齐；差额样本不足，无法进一步定性。")
        print("  【实操】不论定性为哪种，对外比较只比**趋势方向与 7 日均值**；"
              "本序列是商船口径的修订定稿值，结构上不含军舰/护航/渔船，"
              "只有下界没有上界，绝不表述为「一艘船都没过」。")
    miss = [str(r.get("date")) for r in rows if (r.get("n_total") is None)]
    if miss:
        print("⚠ 缺值日期：", ", ".join(miss))
    return 0


if __name__ == "__main__":
    sys.exit(main())
