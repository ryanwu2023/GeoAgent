# -*- coding: utf-8 -*-
"""
emit.py —— 产物生成

产物清单（对齐之前项目 finance-front-monitor 的约定）：
  output/{date}/raw/<id>__<provider>.<ext>   原始载荷（可 --from-file 离线重放）
  output/{date}/taco-records-{date}.jsonl    序列 + 派生量记录（run_tag 自含）
  output/{date}/taco-composite-{date}.json   TACO 明细
  output/{date}/taco-{date}.csv              全量日频表
  output/{date}/taco-digest-{date}.md        报告
  output/LATEST.md / taco-latest.csv / taco-dashboard.html / taco_index.xlsx
  output/history/<id>.jsonl                  累积历史（资产，永不淘汰）
  output/ALL-observations.jsonl              跨轮观察账本
  output/_source_status.json                 每源状态、实际出口、生效 UA
  output/_verify_last.json                   回源对账留档

纪律：
  * 全部原子写入（tmp + os.replace）。
  * 数值历史是资产：history 只增不减，绝不因单轮回溯短而删点。
  * 原始载荷按 provider 命名，逐轮留档，便于离线重放与事后追责。
"""
import csv
import html as _html
import io
import json
import os

from . import net
from . import taco

# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------

def ensure_dir(p):
    if p and not os.path.isdir(p):
        os.makedirs(p, exist_ok=True)
    return p


def write_jsonl(path, rows, mode="w"):
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    if mode == "a":
        with open(path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return path
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    return net.atomic_write_text(path, text, "utf-8")


def read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def write_csv(path, rows, header=None):
    if header is None:
        header = list(rows[0].keys()) if rows else []
    buf = []
    import io
    sio = io.StringIO()
    w = csv.writer(sio, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(["" if r.get(k) is None else r.get(k) for k in header])
    buf.append(sio.getvalue())
    return net.atomic_write_text(path, "".join(buf), "utf-8-sig")


# ---------------------------------------------------------------------------
# 累积历史（数值历史是资产）
# ---------------------------------------------------------------------------

def update_history(history_dir, series_id, obs_by_date, max_points=4000,
                   run_date=None):
    """把本轮观测并入累积历史，返回 (path, n_total, n_new, n_revised)。

    规则：
      * 同日期新值覆盖旧值，但【逐条记账】（修订计数）。
      * 只增不减；超过 max_points 丢最旧。
    """
    ensure_dir(history_dir)
    path = os.path.join(history_dir, "%s.jsonl" % series_id)
    old = read_jsonl(path)
    old_map = {}
    for r in old:
        d = r.get("date")
        if d:
            old_map[d] = r

    n_new = 0
    n_revised = 0
    for d, v in obs_by_date.items():
        if d in old_map:
            if old_map[d].get("value") != v:
                n_revised += 1
            old_map[d] = {"date": d, "value": v,
                          "first_seen": old_map[d].get("first_seen", run_date),
                          "last_seen": run_date}
        else:
            n_new += 1
            old_map[d] = {"date": d, "value": v,
                          "first_seen": run_date, "last_seen": run_date}

    merged = [old_map[d] for d in sorted(old_map)]
    if len(merged) > max_points:
        merged = merged[-max_points:]
    write_jsonl(path, merged)
    return path, len(merged), n_new, n_revised


# ---------------------------------------------------------------------------
# 报告（Markdown）
# ---------------------------------------------------------------------------

def _fmt(v, nd=4):
    if v is None:
        return "—"
    if isinstance(v, float):
        return ("%%.%df" % nd) % v
    return str(v)


def build_digest(run_date, cfg, statuses, result, latest, stats,
                 our_vs_dongwu=None, verify=None):
    """生成 Markdown 报告。"""
    L = []
    A = L.append
    proj = cfg.get("project", "taco-monitor")
    title = cfg.get("title", "TACO 压力指数每日监测")

    A("# %s" % title)
    A("")
    A("- **运行日期**：%s" % run_date)
    A("- **工程**：`%s`" % proj)
    A("- **口径**：东吴宏观《TACO 压力指数》数据底稿（已逐格反解）")
    A("- **指数最新读数**：**TACO = %s**（%s）"
      % (_fmt(latest.get("TACO"), 4) if latest else "—",
         latest.get("date") if latest else "—"))
    A("")

    # 0. 阅读须知
    A("## 0. 阅读须知")
    A("")
    A("1. **TACO 的单位是 z，不是概率、不是预测。** 它是 5 个压力因子的等权 z 分数"
      "合成后再取 7 日均线；只能读方向与量级。")
    A("2. **USSWIT1 用的是公开替代源，不是底稿真实值。** 底稿 D 列是彭博终端"
      "专有的 1 年期通胀互换（USSWIT1），无公开日频等价物。本项目改用 "
      "FRED `T5YIE`（5 年期盈亏平衡通胀率），选择依据是 423 个重叠观测上的 "
      "31 日差分相关性（T5YIE = +0.637，优于 T10YIE +0.535 / MICH +0.424 / "
      "EXPINF1YR +0.351 / T5YIFR +0.084 / DGS1 −0.131 / DFII5 −0.509）。")
    A("3. **油价用的是期货口径。** 底稿 H 列是彭博 `CO1 Comdty`（ICE 布伦特"
      "原油期货首月连续），本项目用 **Yahoo Finance `BZ=F`**（NYMEX Brent Last Day，"
      "紧跟 ICE 首月连续）代理；FRED `DCOILBRENTEU`（EIA 现货）与新浪 `OIL`"
      "（另一连续合约，换月节奏不同）**均不入指数**，只作交叉校验，其现货/期货基差"
      "在 §5b 披露。")
    # 霍尔木兹序列本轮的角色：观察指标（不进指数）还是第 6 因子（并入合成）。
    # 报告里所有相关表述都由这一个布尔分支决定 —— 不让「观察」两个字
    # 和「进指数」的措辞混在同一个版本里。
    hor_obs = bool(result.get("observe"))
    A("4. %s" % (
        "**霍尔木兹通行量：只作观察指标，不进入 TACO。** 底稿 F 列（TRHBCCCD）"
        "虽有数据，但 0 个公式引用它 —— 东吴的 TACO 只用 5 个因子。本轮按用户"
        "要求把它作为**观察指标**抓取、落盘、展示（§5 与看板专设面板），"
        "**不加权、不取 z、不进 S、不进 T**。指数里只有 5 个因子。"
        if hor_obs else
        "**霍尔木兹通行量：底稿不含，本项目作为第 6 因子并入。** 底稿 F 列"
        "（TRHBCCCD）虽有数据，但 0 个公式引用它 —— 东吴的 TACO 只用 5 个因子。"
        "本项目按用户要求把它作为第 6 因子并行并入，产出 **TACO-6**；原 5 因子 "
        "TACO 一个字节都不动，两套读数并列给出（见 §1）。"))
    _notice_n = 5
    if result.get("extended_2"):
        _sp = result.get("span_7") or {}
        _g = result.get("span_7", {}).get("grid_n")
        A("5. **里亚尔汇率：本项目第 7 因子，且有效区间【天生很短】。** "
          "新并入 Bonnast 德黑兰自由市场 USD 卖出价（里亚尔），产出 **TACO-7 / "
          "TACO-7L**。**关键限制**：站点 `/graph` 固定只给 **60 天**历史"
          "（`?range=1y` 实测无效），ffill 到 %s 天网格后 —— "
          "TACO-7L（水位口径）约 %s 天有效、TACO-7（31 日变动口径，需回看 31 天）"
          "约 %s 天有效。**它不是一条和 TACO 等长的曲线**，"
          "任何「TACO-7 与 TACO 走势对比」都必须限制在重叠窗口内。"
          % (_g,
             ((_sp.get("t7l") or {}).get("n")),
             ((_sp.get("t7") or {}).get("n"))))
        A("")
        _notice_n = 6
    A("%d. 所有源均无需登录、无需密钥。仅作研究与监测用途，**不构成投资建议**。"
      % _notice_n)
    A("")

    # 1. 一页速览
    A("## 1. 一页速览")
    A("")
    if latest:
        s6 = stats.get("taco6") or {}
        s6l = stats.get("taco6L") or {}
        _brk1 = result.get("level_break_hormuz") or {}
        A("| 项 | 值 |")
        A("|---|---|")
        A("| **TACO 指数**（5 因子，7 日均线）—— 东吴复刻口径 | **%s** |"
          % _fmt(latest.get("TACO"), 4))
        if latest.get("TACO6") is not None:
            _tag = ("⚠️ 降级（第 6 因子水位断裂）" if _brk1.get("ok") is False
                    else "本项目扩展")
            A("| **TACO-6**（+霍尔木兹）· **口径 A：31 日变动** —— %s | **%s** |"
              % (_tag, _fmt(latest.get("TACO6"), 4)))
        if latest.get("TACO6L") is not None:
            _tagL = ("← 同样受水位断裂影响，但方向与 A 相反"
                     if _brk1.get("ok") is False else "本项目扩展")
            A("| **TACO-6L**（+霍尔木兹）· **口径 B：水位** —— %s | **%s** |"
              % (_tagL, _fmt(latest.get("TACO6L"), 4)))
        A("| 合成值 S（未平滑） | %s |" % _fmt(latest.get("composite_S"), 4))
        if latest.get("composite_S6") is not None:
            A("| 合成值 S6（6 因子，变动口径） | %s |"
              % _fmt(latest.get("composite_S6"), 4))
        if latest.get("composite_S6L") is not None:
            A("| 合成值 S6L（6 因子，水位口径） | %s |"
              % _fmt(latest.get("composite_S6L"), 4))
        if latest.get("TACO7") is not None:
            _s7 = stats.get("taco7") or {}
            A("| **TACO-7**（+霍尔木兹+里亚尔）· **口径 A：31 日变动**"
              " —— ⚠️ 仅 %s 天有效（%s .. %s） | **%s** |"
              % (_s7.get("n"), _s7.get("first_date"), _s7.get("last_date"),
                 _fmt(latest.get("TACO7"), 4)))
        if latest.get("TACO7L") is not None:
            _s7l = stats.get("taco7L") or {}
            A("| **TACO-7L**（+霍尔木兹+里亚尔）· **口径 B：水位**"
              " —— 仅 %s 天有效（%s .. %s） | **%s** |"
              % (_s7l.get("n"), _s7l.get("first_date"), _s7l.get("last_date"),
                 _fmt(latest.get("TACO7L"), 4)))
        if latest.get("composite_S7") is not None:
            A("| 合成值 S7（7 因子，变动口径） | %s |"
              % _fmt(latest.get("composite_S7"), 4))
        if latest.get("composite_S7L") is not None:
            A("| 合成值 S7L（7 因子，水位口径） | %s |"
              % _fmt(latest.get("composite_S7L"), 4))
        # 观察指标（observe 模式）：**不进指数的那个数**。
        # 单独放在表格里、并显式标注「不进指数」，是为了防止读者把它当成
        # 第六个因子去和 §1 的分项 z 对照 —— 它没有 z，也不该有。
        if latest.get("transit") is not None:
            _ho = stats.get("hormuz_observe") or {}
            A("| 霍尔木兹通行量（**观察指标 · 不进指数**） | **%s** 艘次/日 |"
              % _fmt(latest.get("transit"), 0))
            A("| └ 该序列的水位分位（历史 %s 个观测） | %.1f%% |"
              % (_ho.get("n", 0),
                 100.0 * (_ho.get("latest_percentile") or 0)))
            if latest.get("date_transit_raw"):
                A("| └ 真实观测日（源滞后 %s 天，网格内由前值填充） | %s |"
                  % (latest.get("transit_raw_lag_days"),
                     latest.get("date_transit_raw")))
        A("| 分位（历史 %s 个观测） | %.1f%% |"
          % (stats.get("n", 0), 100.0 * (stats.get("latest_percentile") or 0)))
        A("| 区间 | min %s / p50 %s / max %s |"
          % (_fmt(stats.get("min"), 3), _fmt(stats.get("p50"), 3),
             _fmt(stats.get("max"), 3)))
        A("")
        if latest.get("TACO6") is not None:
            A("> 三套读数并列：**TACO** 是东吴 5 因子复刻（可对账）；"
              "**TACO-6 / TACO-6L** 是同一序列、两种口径的第 6 因子版本，"
              "**谁都不隐藏**。")
            if latest.get("TACO6L") is not None:
                A(">")
                A("> - 口径 **A（31 日变动）**：TACO-6 = %s —— 回答"
                  "「比一个月前多还是少」"
                  % _fmt(latest.get("TACO6"), 4))
                A("> - 口径 **B（水位）**：TACO-6L = %s —— 回答"
                  "「现在是正常水位的百分之几」"
                  % _fmt(latest.get("TACO6L"), 4))
                A("> - 两者差 **%+.4f**。水位未断裂时两者大体重合；"
                  "**断裂时可能方向相反**（本例即是），此时必须自己判断"
                  "「断点是真实信号还是数据假象」。"
                  % (latest["TACO6L"] - latest["TACO6"]))
            A(">")
            brk = result.get("level_break_hormuz") or {}
            if brk.get("ok") is False:
                A("> ## ⚠️ 第 6 因子水位断裂 —— 两套口径给出【相反方向】")
                A(">")
                A("> 第 6 因子的原始序列（IMF PortWatch 霍尔木兹通行量）**水位已断裂**："
                  "近 %s 日中位数 **%.1f** 艘次/日，而 %s..%s 基准期中位数是 "
                  "**%.1f** 艘次/日（倍数 **%.2f**）。"
                  % (brk.get("n_recent"), brk.get("recent_median"),
                     (brk.get("base_window") or ["", ""])[0],
                     (brk.get("base_window") or ["", ""])[1],
                     brk.get("base_median"), brk.get("ratio")))
                A(">")
                A("> **为什么这会出问题**：水位断到低位并长期停在那里之后，"
                  "「31 日变动」退化成断点之后的小噪声 —— 口径 A 的 z=**%s** "
                  "读作「通行量回升 → 压力下降」；而口径 B（水位）的 z=**%s** "
                  "说的是「通行量只有正常水平的百分之几 → 压力高」。"
                  "**一个说降、一个说升。**"
                  % (_fmt(latest.get("z_transit"), 3),
                     _fmt(latest.get("z_transit_level"), 3)))
                A(">")
                A("> **本项目不替你选口径**：两套都出，并列摆在 §1 / §5a / 看板。"
                  "你需要判断的是**断点本身是真实信号还是数据假象** —— "
                  "公开数据无法区分（详见 §6）。")
                A(">")
                A("> **结论**：`TACO`（5 因子，东吴口径）不受影响，仍可用。"
                  "两套 6 因子读数在水位恢复正常之前**都不要单独当作结论**。")
            A(">")
        if latest.get("TACO7") is not None:
            _s7 = stats.get("taco7") or {}
            _s7l = stats.get("taco7L") or {}
            _sp = result.get("span_7") or {}
            A("> ## ⚠️ 第 7 因子（里亚尔）的有效区间【比其余因子短得多】")
            A(">")
            A("> Bonnast 的 `/graph` 接口**固定只返回 60 天**（`?range=1y` 实测"
              "无效、`/graph/usd/365` 直接 404）。ffill 到 %s 天网格后："
              % _sp.get("grid_n"))
            A(">")
            A("> | 读数 | 有效天数 | 有效区间 | 为什么短 |")
            A("> |---|---|---|---|")
            A("> | TACO-7（31 日变动） | **%s / %s** | %s .. %s | "
              "需 F2 与 F2[−31] 同时存在，白吃 31 天回看 |"
              % (_s7.get("n"), _sp.get("grid_n"),
                 _s7.get("first_date"), _s7.get("last_date")))
            A("> | TACO-7L（水位） | **%s / %s** | %s .. %s | "
              "z 只需 2 个样本，故几乎与数据源等长 |"
              % (_s7l.get("n"), _sp.get("grid_n"),
                 _s7l.get("first_date"), _s7l.get("last_date")))
            A(">")
            A("> **这是数据源硬上限，不是计算失败。** 任何跨指数比较"
              "（例如「TACO-7 比 TACO 高多少」）都必须只在重叠窗口内做；"
              "把 TACO-7 与 TACO 的全长曲线画在一张图上会得到错误的印象。"
              "本地 `history/BONBAST_USD_sell_rial.jsonl` 会随日更不断累积，"
              "这是把历史攒长的唯一途径。")
            A(">")
        if hor_obs and latest.get("transit") is not None:
            _ho = stats.get("hormuz_observe") or {}
            _lbo = result.get("level_break_hormuz") or {}
            A("> **霍尔木兹通行量本轮只作观察，不参与合成。** 上面那个 "
              "%s 艘次/日的水位**没有进入 TACO** —— 指数里只有 5 个因子。"
              % _fmt(latest.get("transit"), 0))
            A(">")
            A("> - 它本身的水位分位是 **%.1f%%**（历史 %s 个网格日）—— "
              "即当前水位**低于历史上 %.1f%% 的观测**。"
              % (100.0 * (_ho.get("latest_percentile") or 0),
                 _ho.get("n", 0),
                 100.0 * (1 - (_ho.get("latest_percentile") or 0))))
            if _lbo.get("ok") is False:
                A("> - ⚠️ **水位断裂仍存在**（近 %s 日中位数 %.1f vs 基准 %.1f，"
                  "倍数 %.2f）—— 但它是**观察事实**，不是指数风险："
                  "TACO 不含这一项，读数不受影响。详见 §5a。"
                  % (_lbo.get("n_recent"), _lbo.get("recent_median"),
                     _lbo.get("base_median"), _lbo.get("ratio")))
            A("> - 为什么不做成因子：东吴底稿的 F 列（同一序列）**0 个公式引用**，"
              "且该序列 2026-03 起水位断裂至今未恢复 —— 任何「N 日变动」类变换"
              "都会退化成断点噪声。当观察指标能看，当因子会污染指数。")
            A(">")
        A("**分项 z 分数（最新）**")
        A("")
        A("| 分项 | z | 含义 |")
        A("|---|---|---|")
        A("| 10Y 美债收益率 31 日变动 | %s | 利率上行 → 压力升 |"
          % _fmt(latest.get("z_10Y"), 3))
        A("| 1Y 通胀互换（T5YIE 代理）31 日变动 | %s | 通胀预期上行 → 压力升 |"
          % _fmt(latest.get("z_swap"), 3))
        A("| 特朗普支持率 31 日变动（取负） | %s | 支持率下行 → 压力升 |"
          % _fmt(latest.get("z_approval"), 3))
        A("| 道指 31 日涨跌幅（取倒数） | %s | 股指下行 → 压力升 |"
          % _fmt(latest.get("z_DJIA"), 3))
        A("| 布伦特原油 31 日变动 | %s | 油价上行 → 压力升 |"
          % _fmt(latest.get("z_Brent"), 3))
        if hor_obs and latest.get("transit") is not None:
            # 刻意**不给 z**：这一行只报原始水位，并写明「不进指数」。
            # 若在这里编一个 z 出来，读者会天然地把它和上面 5 个 z 并列比较，
            # 那就等于在视觉上把它变成第 6 个因子了。
            A("| 霍尔木兹通行量（**观察指标，不进指数，故无 z**）"
              " | %s 艘次/日 | 仅记录水位，见 §5a |"
              % _fmt(latest.get("transit"), 0))
        if latest.get("z_transit") is not None:
            A("| 霍尔木兹通行量 31 日变动（取负）· **口径 A**"
              "<br>*本项目扩展，底稿不含* | %s | 通行量降 → 供给压力升 |"
              % _fmt(latest.get("z_transit"), 3))
        if latest.get("z_transit_level") is not None:
            A("| 霍尔木兹通行量 水位 z（取负）· **口径 B**"
              "<br>*本项目扩展，底稿不含* | %s | 水位低 → 供给压力升 |"
              % _fmt(latest.get("z_transit_level"), 3))
        if latest.get("z_rial") is not None:
            A("| Bonnast USD 卖出价 31 日变动（**不取负**）· **口径 A**"
              "<br>*本项目扩展，底稿不含* | %s | 里亚尔贬值 → 压力升 |"
              % _fmt(latest.get("z_rial"), 3))
        if latest.get("z_rial_level") is not None:
            A("| Bonnast USD 卖出价 水位 z（**不取负**）· **口径 B**"
              "<br>*本项目扩展，底稿不含* | %s | 里亚尔弱 → 压力升 |"
              % _fmt(latest.get("z_rial_level"), 3))
        A("")
    else:
        A("本轮未能算出有效 TACO 读数（见 §2 源状态）。")
        A("")

    # 2. 源状态
    A("## 2. 数据源状态")
    A("")
    A("| 序列 | 角色 | 信源 | 状态 | 观测数 | 最新日期 | 最新值 | 实际出口 | 生效 UA | 耗时 |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for s in statuses:
        A("| `%s` | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
          % (s.get("id"), s.get("role"), s.get("provider"),
             s.get("status"),
             s.get("n") if s.get("n") is not None else "—",
             s.get("last_date") or "—",
             _fmt(s.get("last_value"), 4) if s.get("last_value") is not None else "—",
             s.get("channel") or "—",
             (s.get("ua") or "—").split(")")[0][:34],
             ("%.1fs" % s["elapsed"]) if s.get("elapsed") else "—"))
    A("")
    dec = statuses[0].get("proxy_decision") if statuses else None
    if dec:
        A("**出口决策**：`%s`" % dec.get("reason"))
        if dec.get("proxy"):
            A("（选定代理 `%s`；`tested=%s`）" % (dec.get("proxy"),
                                              dec.get("tested")))
        if dec.get("env_proxy_vars_ignored"):
            A("")
            A("> 已忽略环境变量代理：%s —— 实测该端口仍在监听但无出口，"
              "直连才是通的。详见 §6 已知偏差。"
              % ", ".join("`%s`" % v for v in dec["env_proxy_vars_ignored"]))
        A("")

    # 3. 公式
    A("## 3. 计算口径")
    A("")
    A("```")
    for step in (cfg.get("formula", {}).get("steps") or []):
        A(step)
    A("```")
    A("")
    A("**复现精度**：用底稿原始输入复算，S 列 `max_abs_diff = 1.22e-09`、"
      "T 列 `1.75e-10`（浮点极限，即精确匹配）。")
    A("")
    if result.get("extended"):
        A("> 上面最后 5 行（`F` / `Nt` / `Ntz` / `S6` / `T6`）是**本项目扩展**，"
          "东吴底稿的 TACO 不包含 —— 底稿 F 列虽然有数据，但 0 个公式引用它。"
          "前 8 行才是逐格反解出来的东吴口径，`T` 的对账精度不受第 6 因子影响。")
        A("")
    elif result.get("observe"):
        A("> 上面只有**前 8 行**参与指数计算（`I`..`M` -> `N`..`R` -> `S` -> `T`）。"
          "其后的 `F = TRHBCCCD` 是**观察指标**：只做前值填充与落盘，出现在"
          "§5 / 看板 / CSV 的最后一列，**不产生任何 z、不进 `S`、不进 `T`**。"
          "第 8 行 `T = 7-row MA of S` 的结果与不含观察指标时**逐点完全相同**"
          "（自检里有专门的逐点等值断言）。")
        A("")

    # 4. 与东吴原版对照
    A("## 4. 与东吴原版对照")
    A("")
    if our_vs_dongwu:
        A("| 区间 | 重叠观测 | 相关系数 | 秩相关 | 平均绝对差 |")
        A("|---|---|---|---|---|")
        for w in our_vs_dongwu:
            A("| %s | %s | %s | %s | %s |"
              % (w.get("label"), w.get("n"),
                 _fmt(w.get("corr"), 4), _fmt(w.get("rank_corr"), 4),
                 _fmt(w.get("mean_abs_diff"), 4)))
        A("")
        A("> 差异来源：USSWIT1 用公开替代（T5YIE）、支持率用 Silver Bulletin "
          "代理 RCP 均值、油价用 Yahoo `BZ=F`（紧跟 ICE 首月连续）代理彭博 `CO1 Comdty`。"
          "相关 > 0.92 说明公开数据能还原东吴口径的主要信息。")
    else:
        A("（本轮未做对照：底稿仅作一次性校验，日常运行不读取底稿数据。）")
    A("")

    # 5. 霍尔木兹通行量（角色由本轮模式决定：观察指标 / 第 6 因子）
    ext_on = bool(result.get("extended"))
    A("## 5. 霍尔木兹通行量（%s）"
      % ("观察指标 · **不进入 TACO**" if hor_obs else "第 6 因子，进入 TACO-6"))
    A("")
    if ext_on:
        A("> **这一项不在东吴底稿里。** 底稿 F 列有 TRHBCCCD 数据，但反解确认"
          "0 个公式引用它 —— 东吴 TACO 只用 5 个因子。本项是用户要求增加的第 6 "
          "因子，只进 **TACO-6**，不影响 §1 的 5 因子 TACO。")
        A("")
    elif hor_obs:
        A("> **这一项不在东吴底稿里，本轮也不在指数里。** 底稿 F 列有 TRHBCCCD "
          "数据，但反解确认 0 个公式引用它 —— 东吴 TACO 只用 5 个因子。"
          "本轮按用户要求把它保留为**观察指标**：抓取、前值填充、落盘、"
          "在此展示；**不加权、不取 z、不进 `S`、不进 `T`**。")
        A(">")
        A("> **为什么能看但不能算**：该序列 2026-03 起出现结构性水位断裂"
          "且至今未恢复（见 §5a），任何「N 日变动」类变换都会退化成断点噪声。"
          "把它放进指数会污染读数；放在这里则是一条有价值的地缘观察线索。")
        A("")
    hormuz = None
    for s in statuses:
        if s.get("id") == "TRHBCCCD":
            hormuz = s
    if hormuz and hormuz.get("recent"):
        A("**源的原始记录（最近 %d 条，未经前值填充）**" % len(hormuz["recent"]))
        A("")
        A("| 日期 | 总艘次 | 油轮 | 总载重吨 |")
        A("|---|---|---|---|")
        for r in hormuz["recent"]:
            A("| %s | %s | %s | %s |"
              % (r.get("date"), r.get("n_total"), r.get("n_tanker"),
                 r.get("capacity")))
        A("")
    else:
        A("（本轮未取到霍尔木兹通行量。）")
        A("")

    # 水位序列（两种角色都展示）：断裂是共同事实，不该因为角色不同而改变
    if result.get("F") and (ext_on or hor_obs):
        _dts = result.get("dates") or []
        _F = result.get("F") or []
        _tailF = [i for i in range(len(_dts))
                  if i < len(_F) and _F[i] is not None][-8:]
        if _tailF:
            A("**网格上的水位（最近 8 个有效日；缺日由前值填充补齐）**")
            A("")
            A("| 日期 | 通行量（艘次/日） |")
            A("|---|---|")
            for i in _tailF:
                A("| %s | %s |" % (_dts[i], _fmt(_F[i], 0)))
            A("")
            _hos = stats.get("hormuz_observe") or {}
            A("- 网格覆盖：**%s 天**（%s .. %s）；原始源覆盖 **%s 天**，"
              "末次真实观测 **%s**。"
              % (_hos.get("n"), _hos.get("first_date"), _hos.get("last_date"),
                 _hos.get("raw_n"), _hos.get("raw_last_date")))
            A("- 水位分位：**%.1f%%** —— 即当前水位**低于历史上 %.1f%% 的观测**。"
              % (100.0 * (_hos.get("latest_percentile") or 0),
                 100.0 * (1 - (_hos.get("latest_percentile") or 0))))
            A("- **前值填充纪律**：通行量按 AIS 事后汇编，通常滞后 4–7 天且有缺日，"
              "已前值填充到既有日历网格。它**不参与网格构造** —— 否则面板长度变化"
              "会让扩张窗口 z 整体漂移，把已验证的 5 因子复刻结果改掉。")
            A("")

    if ext_on:
        _dts = result.get("dates") or []
        F = result.get("F") or []
        Nt = result.get("Nt") or []
        Ntz = result.get("Ntz") or []
        Ltz = result.get("Ltz") or []
        tail = [i for i in range(len(_dts)) if Ntz[i] is not None][-6:]
        if tail:
            A("**因子管线（最近 6 个有效日）**")
            A("")
            A("| 日期 | 通行量（艘次，前值填充） | 31 日变动 | 口径 A：Nt | "
              "口径 A：z | 口径 B：水位 z |")
            A("|---|---|---|---|---|---|")
            for i in tail:
                raw = "—"
                if i < len(F) and F[i] is not None:
                    raw = "%g" % F[i]
                chg = "—"
                if i >= 31 and F[i] is not None and F[i - 31] is not None:
                    chg = "%+g" % (F[i] - F[i - 31])
                A("| %s | %s | %s | %s | %s | %s |"
                  % (_dts[i], raw, chg, _fmt(Nt[i] if i < len(Nt) else None, 3),
                     _fmt(Ntz[i], 3),
                     _fmt(Ltz[i] if i < len(Ltz) else None, 3)))
            A("")
            A("> **口径 A（31 日变动）**：`Nt = -(通行量[i] - 通行量[i-31])`，再取扩张窗口 z。"
              "回答「比一个月前多还是少」。")
            A("> **口径 B（水位）**：`Ltz = -z(通行量)`。回答「现在是正常水位的百分之几」。"
              "（z 对负号等变，故与「先取负再 z」等价。）")
            A("> **两套都不隐藏**：水位正常时两者大体重合；**水位断裂后可能方向相反**，"
              "此时「断点是真实信号还是数据假象」只能由使用者判断。")
            A("")

    # 5a. 水位面板：变动量之外必须能看见【水位】
    #
    # 这一段**两种角色都渲染** —— 水位断裂是这条序列的客观事实，
    # 不该因为「这轮它进不进指数」而时有时无。变的只是「断裂意味着什么」
    # 的那句话：当因子时是「TACO-6 降级」，当观察指标时是「指数不受影响，
    # 但这是重要的地缘观察线索」。
    brk = result.get("level_break_hormuz") or {}
    mm = result.get("monthly_medians_hormuz") or []
    if mm:
        A("### 5a. %s" % ("霍尔木兹观察面板（不进指数 · 独立观察）" if hor_obs
                          else "水位面板（变动量看不到的东西）"))
        A("")
        if brk.get("ok") is False:
            A("> ### ⚠️ 检测到水位断裂 —— %s"
              % ("**仅作观察事实，TACO 读数不受影响**" if hor_obs
                 else "本轮 TACO-6 为降级读数"))
            A("")
            A("| 口径 | 值 |")
            A("|---|---|")
            A("| 近 %s 日（%s .. %s）中位数 | **%.1f** 艘次/日 |"
              % (brk.get("n_recent"), (brk.get("recent_window") or ["", ""])[0],
                 (brk.get("recent_window") or ["", ""])[1],
                 brk.get("recent_median")))
            A("| 基准期（%s .. %s）中位数 | **%.1f** 艘次/日 |"
              % ((brk.get("base_window") or ["", ""])[0],
                 (brk.get("base_window") or ["", ""])[1],
                 brk.get("base_median")))
            A("| 倍数（近 / 基准） | **%.2f** |" % brk.get("ratio"))
            A("| 判定阈值 | %.2f ~ %.2f |"
              % ((brk.get("thresholds") or {}).get("lo", 0.4),
                 (brk.get("thresholds") or {}).get("hi", 2.5)))
            A("")
        A("**月度中位数（最近 %d 个月）**" % len(mm))
        A("")
        A("| 月份 | 观测数 | n_total 中位数 | 相对基准 |")
        A("|---|---|---|---|")
        _bm = brk.get("base_median")
        for mo, cnt, med in mm:
            rel = ("%.0f%%" % (100.0 * med / _bm)) if _bm else "—"
            flag = " ⚠️" if (_bm and med / _bm < 0.4) else ""
            A("| %s | %s | %s | %s%s |" % (mo, cnt, _fmt(med, 0), rel, flag))
        A("")
        A("> **为什么单看 31 日变动不够**：变动量只回答「比一个月前多还是少」，"
          "不回答「现在是正常的百分之几」。一条序列从 78 断到 8 再回到 10，"
          "31 日变动是正的（读作压力下降），而水位只有正常的 13%。"
          "本项目因此强制并列展示水位 —— 也正是因为这条序列的水位已断裂，"
          "本轮它才只作观察、不进指数。")
        A("")
    A("> **小基数禁令**：艘次计数不做 N 期百分比变化（1→8 艘次 = +700%，"
      "会把噪声渲染成「升级」）。")
    A("> 海峡通行量是 AIS 事后汇编，滞后 4–7 天，故 `max_lag=14`。")
    A("")

    # 5b. 原油现货/期货基差 —— 入指数的是期货(CO1)，现货(FRED)只用来盯基差
    A("### 5b. 原油：入指数口径 vs 现货（交叉校验）")
    A("")
    fut = next((s for s in statuses if s.get("id") == "CO1"), None)
    sp = next((s for s in statuses if s.get("id") == "CO1_fred_crosscheck"), None)
    if fut and sp and fut.get("last_value") is not None \
            and sp.get("last_value") is not None:
        ld_f, ld_s = fut.get("last_date"), sp.get("last_date")
        d_f, d_s = fut["_data"], sp["_data"]
        A("| 口径 | 信源 | 最新日期 | 最新值 | 入场 |")
        A("|---|---|---|---|---|")
        A("| 期货（ICE 首月连续） | Yahoo `BZ=F` | %s | %s | **是** |"
          % (ld_f, _fmt(fut["last_value"], 2)))
        A("| 现货（EIA 欧洲 FOB） | FRED | %s | %s | 否（仅校验） |"
          % (ld_s, _fmt(sp["last_value"], 2)))
        # 第三条：另一连续合约（新浪 OIL）——换月节奏与 ICE 不同，只作旁证
        sn = next((s for s in statuses if s.get("id") == "CO1_sina_crosscheck"), None)
        if sn and sn.get("last_value") is not None:
            A("| 期货（新浪 `OIL` 连续） | 新浪财经 | %s | %s | 否（仅校验） |"
              % (sn.get("last_date"), _fmt(sn["last_value"], 2)))
        # 在两者都有值的最近日期上算基差，避免拿不同日期的价做差
        common = sorted(set(d_f) & set(d_s) & {ld_f, ld_s})
        if common:
            d0 = common[-1]
            basis = d_s[d0] - d_f[d0]          # 正 = 现货升水
            A("")
            A("- **同日（%s）现货 − 期货基差 = %+0.2f 美元/桶**（正 = 现货升水）。"
              % (d0, basis))
            if abs(basis) > 5:
                A("- **警示**：基差已超过 5 美元。正常时两者相差约 2 美元（现货/期货"
                  "正常的期限结构差）。基差异常放大意味着要么现货市场真的紧张"
                  "（地缘事件推升实货溢价），要么其中一侧数据有问题。当前入指数的是"
                  "**期货**口径——因为它与东吴底稿的 `CO1 Comdty` 口径一致、且每日更新。")
            else:
                A("- 基差处于正常区间（|基差| ≤ 5 美元），两条口径互相印证。")
        A("")
        A("> **为什么保留一个不入指数的源**：现货与期货一旦分道扬镳，"
          "就必须能看见。2026-09 实测该基差从约 +2 扩大到 +22 美元，"
          "若只用其中一条，这种背离会被静默吞掉。")
    else:
        A("（本轮未同时取到两个原油口径。）")
    A("")

    # 5c. 里亚尔汇率（第 7 因子）
    #
    # 2026-09-23 按用户要求整体移除汇率：config 显式禁用时不渲染整节。
    # 这里刻意**不**写「本轮汇率数据不可用」—— 那会让读者以为本来有一个
    # 汇率因子但这轮抓挂了。移除是设计决策，不是故障。
    ext2_on = bool(result.get("extended_2"))
    ext2_cfg_on = bool((cfg.get("extended_factor_2") or {}).get("enabled", True))
    if ext2_on or ext2_cfg_on:
        A("### 5c. Bonnast USD 卖出价 / 里亚尔（第 7 因子，进入 TACO-7）")
        A("")
    if ext2_on:
        b = next((s for s in statuses
                  if s.get("id") == "BONBAST_USD_sell_rial"), None)
        A("> **这一项也不在东吴底稿里。** 是本项目按用户要求增加的第 7 因子"
          "（德黑兰自由市场 USD 卖出价），只进 **TACO-7 / TACO-7L**，"
          "不改动 5 因子 TACO 与 6 因子 TACO-6。")
        A("")
        if b:
            A("| 项 | 值 |")
            A("|---|---|")
            A("| 数据源 | Bonnast（`www.bonbast.com`，德黑兰自由市场） |")
            A("| 抓取方式 | 首页取一次性令牌 → `POST /json` 取快照；"
              "`/graph/usd` 取近 60 天日频 |")
            A("| 单位 | **里亚尔**（接口原值是托曼，页面明写 "
              "`1 Toman = 10 Rials`，故 × 10） |")
            A("| 快照卖出价 | %s 里亚尔 |"
              % (_fmt(b.get("snapshot_rial"), 0)))
            A("| 历史区间 | %s .. %s（%s 天） |"
              % ((b.get("graph_span") or {}).get("first"),
                 (b.get("graph_span") or {}).get("last"),
                 (b.get("graph_span") or {}).get("n")))
            A("| **站点历史硬上限** | **60 天**（`?range=1y` 实测无效、"
              "`/graph/usd/365` 返回 404） |")
            _cc = b.get("crosscheck") or {}
            A("| 两条链路交叉校验 | %s |"
              % (_cc.get("detail") or "—"))
            A("")
            if _cc.get("ok") is False:
                A("> ⚠️ **交叉校验未通过**：快照与图序列的相对差超过 5% 容差，"
                  "两条链路可能不在同一口径。本轮第 7 因子读数应当被怀疑。")
                A("")
        dates7 = result.get("dates") or []
        F2 = result.get("F2") or []
        Nt2 = result.get("Nt2") or []
        Ntz2 = result.get("Ntz2") or []
        Ltz2 = result.get("Ltz2") or []
        tail7 = [i for i in range(len(dates7))
                 if i < len(Ntz2) and Ntz2[i] is not None][-6:]
        if tail7:
            A("**因子管线（最近 6 个有效日）**")
            A("")
            A("| 日期 | 卖出价（里亚尔，前值填充） | 31 日变动 Nt2 | 口径 A：z | "
              "口径 B：水位 z |")
            A("|---|---|---|---|---|")
            for i in tail7:
                raw = _fmt(F2[i], 0) if (i < len(F2)
                                         and F2[i] is not None) else "—"
                # 31 日变动直接用管线里的 Nt2。
                # 注意不要在这里另算一遍 F2[i]-F2[i-31]：那是同一个量，
                # 并列两列会让人以为存在两个不同的变动口径（曾踩过）。
                nt2 = Nt2[i] if i < len(Nt2) else None
                chg = _fmt(nt2, 0)
                if isinstance(nt2, (int, float)) and nt2 > 0:
                    chg = "+" + chg
                A("| %s | %s | %s | %s | %s |"
                  % (dates7[i], raw, chg,
                     _fmt(Ntz2[i] if i < len(Ntz2) else None, 3),
                     _fmt(Ltz2[i] if i < len(Ltz2) else None, 3)))
            A("")
        A("> **符号约定（与第 6 因子相反，最容易抄错）**：本模块统一约定"
          "「压力上升 → 因子上升」。霍尔木兹是通行量**下降**代表压力上升，"
          "故取负；里亚尔是汇率**上升**（贬值）代表压力上升，故**不取负**。"
          "两者方向相反、约定一致。")
        A(">")
        A("> **前值填充纪律**：与第 6 因子相同 —— ffill 到既有日历网格，"
          "**不参与网格构造**，否则扩张窗口 z 会整体漂移。")
        A(">")
        A("> **有效期短是数据源硬上限**：站点只给 60 天。见 §1 的警示框。"
          "本地 `history/` 会随日更累积，这是把历史攒长的唯一途径 —— "
          "而这正是把它做成日更任务的理由。")
        A("")
    elif ext2_cfg_on:
        A("（本轮第 7 因子不可用 —— 见 §2 数据源状态；指数退回 5 因子版本。）")
        A("")

    # 6. 已知偏差
    A("## 6. 已知偏差与不做的推断")
    A("")
    A("- **USSWIT1 无公开等价物**：T5YIE 是 5 年期盈亏平衡，与 1 年期通胀互换"
      "在期限上不同。31 日差分相关 +0.637，是现有公开候选里最好的，但不是同一个量。")
    A("- **支持率口径**：底稿用 RCP 均值，本项目用 Silver Bulletin 贝叶斯平滑均值。"
      "两者都是聚合器，长期水平接近但短期波动不同。")
    A("- **油价口径（2026-09-23 定稿）**：底稿 H 列表头是彭博 `CO1 Comdty` = ICE "
      "布伦特原油**期货首月连续**。入指数的是 **Yahoo Finance `BZ=F`**"
      "（NYMEX Brent Last Day Financial Futures，现金结算、紧跟 ICE 首月连续），"
      "与 Yahoo 源逐点核验 Δ=0。历史换源：FRED `DCOILBRENTEU`（EIA 欧洲**现货** FOB，"
      "口径不符 + 滞后 4–5 交易日）→ 新浪 `OIL`（另一连续合约，换月节奏不同，"
      "深度 backwardation 下与 ICE 首月分叉约 4 美元）→ 现用 `BZ=F`。"
      "近 90 日与东吴相关 0.9861。")
    A("- **原油现货/期货基差**：危机期现货可显著升水期货（2026-09 实测扩大到约 "
      "+22 美元/桶）。入指数的是期货口径；现货序列（FRED `DCOILBRENTEU`）与另一"
      "连续合约（新浪 `OIL`）均保留为交叉校验并在 §5b 披露，"
      "基差走阔时读者能看见，而不是被单一序列吞掉。")
    A("- **原油当日会话**：运行当天那一根 bar 是**未收盘**的盘中价，"
      "已按 `drop_inprogress_session` 丢弃，只用完整交易日的收盘价"
      "（Yahoo 按 `meta.gmtoffset` 定位交易所本地日）。")
    A("- **FRED 封浏览器 UA**：实测 `Mozilla/5.0` / Chrome UA 会被 FRED 判为异常并"
      "返回 0 字节超时，而 `curl/*` 与 `Python-urllib/*` 正常。这是 UA 指纹而非网络"
      "问题，失败表现极易误导。本项目用 UA 阶梯（默认 curl 风格）规避。")
    A("- **环境变量代理陷阱 + 按源出口覆盖**：本机环境注入了 `http_proxy`/`https_proxy` "
      "指向一个已无出口的本地端口，curl/urllib 默认会读它导致全部超时；本项目显式 "
      "`--noproxy '*'` 绕过并实测选择出口。但 **Yahoo 与其余源出口策略相反** ——"
      "直连被网关返回 HTML 拦截页（HTTP 非 200），必须走系统代理，故在配置里按源"
      "声明 `egress`（`proxy` / `direct` / 缺省），拿不到系统代理时显式报错而非静默降级。")
    A("- **霍尔木兹通行量：%s**：底稿 F 列虽有该序列，"
      "但 0 个公式引用 F，东吴 TACO 只用 5 个因子。"
      % ("本轮只作**观察指标**，未进入指数 —— 指数就是 5 因子，"
         "可用于与东吴原版逐格对账" if hor_obs else
         "是「本项目扩展」，不是东吴口径。TACO-6 是本项目加的第 6 因子，"
         "**不可用于与东吴原版逐格对账**；对账只对 5 因子 TACO"))
    if ext_on:
        A("- **第 6 因子有两套口径，并列输出（用户选定）**：")
        A("  - **口径 A（31 日变动）→ TACO-6**：`Nt = -(F - F[-31])`。"
          "回答「比一个月前多还是少」。")
        A("  - **口径 B（水位）→ TACO-6L**：`Ltz = -z(F)`。回答"
          "「现在是正常水位的百分之几」。")
        A("  为什么不全用 A：A 在**水位断裂**的序列上会退化成断点噪声，甚至给出与水位"
          "相反的方向（本例正是如此）。为什么不全用 B：若断裂本身是**数据假象**，"
          "B 会注入一个虚高的压力值。**公开数据无法区分这两种可能**，故两套都出、"
          "不替使用者选。水位未断裂时两者大体重合（可互为交叉校验）。")
        A("  - 若改用油轮艘次、载重吨、7 日均值后再做差分/取 z，读数还会不同。"
          "本项目只固定 A/B 两种，避免挑口径挑出好看的曲线。")
    elif hor_obs:
        A("- **为什么不把霍尔木兹做成因子（本轮的取舍）**：这条序列本身很有观察"
          "价值，但它不满足「能算」的条件 —— 水位已断裂（见下条），"
          "任何「N 日变动」类变换都会退化成断点噪声，甚至给出与水位相反的方向。"
          "把它放进指数等于**给指数注入一个已知不可靠的信号**。"
          "所以保留为观察指标：看得到、查得到、不影响读数。")
    A("- **⚠️ 霍尔木兹通行量在 2026-03 出现结构性水位断裂**：IMF PortWatch 的"
      "`chokepoint6`（Strait of Hormuz）`n_total` 从 2026-02 的约 78 艘次/日"
      "断到 2026-03 的约 3 艘次/日，此后 6 个月一直停在 3–13，**没有恢复**。"
      "同一图层的 `n_tanker` 与 `capacity` **同步**塌掉约 95%（三个字段一起塌 = "
      "口径/覆盖问题，不是运量信号）。")
    A("  - **已排除「全局数据管道故障」**：同期苏伊士、曼德海峡、马六甲、"
      "台湾海峡、巴拿马运河的通行量水位全部平稳，**只有霍尔木兹断了**。"
      "所以这是霍尔木兹单点的问题（区域性 AIS/GPS 干扰导致漏计，"
      "或图层口径变更）—— 属**推断**，本项目无法从公开数据区分这两者。")
    if hor_obs:
        A("- **对指数的影响：本轮为零。** 水位断裂后 31 日变动会退化成断点噪声，"
          "而 TACO 的公式里根本没有这一项 —— 它只在 §5 / §5a / 看板 / "
          "CSV 最后一列出现。**TACO 读数（§1）不受影响，可直接对账。**"
          "本项目仍跑水位断裂守卫（近 45 日中位数 vs 长期基准中位数，"
          "比值超出 0.4~2.5 即判定断裂），把它作为**观察事实**披露，"
          "而不是作为指数风险。")
    else:
        A("- **对指数的影响**：水位断裂后，31 日变动退化成断点后的小噪声。"
          "本轮口径 A 的 z 读作「通行量回升 → 压力下降」，口径 B 的 z 读作"
          "「水位只有正常的几个百分点 → 压力高」，**方向相反**。本项目已加"
          "**水位断裂守卫**（近 45 日中位数 vs 长期基准中位数，比值超出 0.4~2.5 "
          "即判定断裂），断裂时在 §1 与 §5a 显著披露，并在对账里出 WARN。"
          "**两套 6 因子读数在水位恢复正常前都不要单独当作结论；"
          "5 因子 TACO 不受影响。**")
    if not ext2_cfg_on:
        A("- **里亚尔汇率（Bonnast USD 卖出价）已于 2026-09-23 移除**："
          "本轮不抓取、不落盘、不参与合成。移除原因是它需要把指数做成 7 因子，"
          "与「TACO 回到东吴 5 因子口径」的目标冲突。相关定义（含单位证据 "
          "`1 Toman = 10 Rials`、站点 60 天硬上限）保留在 `config.json` 的 "
          "`extended_factor_2` / `bonbast_crawler` 里，改回 `enabled=true` "
          "即可恢复。**注意恢复后指数就不再是 5 因子，不能与东吴原版对账。**")
    A("- **相关 ≠ 因果**：只报同期同向/背离，不写「因为…所以…」。")
    A("- **不做预测**：不给时点、不给概率。TACO 是压力读数，不是择时信号。")
    A("")

    # 7. 产物
    A("## 7. 产物")
    A("")
    A("```")
    A("output/%s/" % run_date)
    A("  raw/                         原始载荷（逐源留档）")
    A("  taco-records-%s.jsonl      序列 + 派生量记录" % run_date)
    A("  taco-composite-%s.json     TACO 明细" % run_date)
    A("  taco-%s.csv                全量日频表" % run_date)
    A("output/LATEST.md               本报告")
    A("output/taco-latest.csv         最新日频表")
    A("output/taco-dashboard.html     可视化看板")
    A("output/taco_index.xlsx         表格版")
    A("output/history/                累积历史（资产，永不淘汰）")
    A("output/ALL-observations.jsonl  跨轮观察账本")
    A("output/_source_status.json     每源状态 + 出口 + 生效 UA")
    A("output/_verify_last.json       回源对账留档")
    A("```")
    A("")

    # 8. 对账
    if verify:
        A("## 8. 回源对账")
        A("")
        A("- 结果：**%s**" % ("通过" if verify.get("ok") else "不一致"))
        A("- 检查项：%s 项，失败 %s 项，警告 %s 项"
          % (verify.get("total"), verify.get("failed"),
             verify.get("warned", 0)))
        for c in (verify.get("checks") or []):
            if not c.get("ok") and c.get("level") != "warn":
                A("  - ❌ `%s`：%s" % (c.get("name"), c.get("detail")))
        warns = [c for c in (verify.get("checks") or [])
                 if not c.get("ok") and c.get("level") == "warn"]
        for c in warns:
            A("  - ⚠️ `%s`：%s" % (c.get("name"), c.get("detail")))
        if warns:
            A("")
            A("> 警告不判 FAIL：数据本身完整，只是需要读者知道的事实。"
              "若把这类也判 FAIL，日常任务会天天报 FAIL，真正的 FAIL 会被当噪音忽略。")
        A("")

    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# 可视化看板（内联 SVG，随 IDE 主题走浅色）
# ---------------------------------------------------------------------------

_T = {
    "bg": "#ffffff", "panel": "#f7f8fa", "line": "#e3e6ea",
    "text": "#1f2328", "muted": "#6b7280", "accent": "#c0392b",
    "up": "#c0392b", "down": "#1a7f37", "grid": "#eceff2",
}


def _spark(pts, w=560, h=140, color=None, ref=None, label="",
           pts2=None, color2=None, pts3=None, color3=None, dashes=None):
    """sparkline SVG。pts = [(date, value)]；pts2/pts3 为可选的第 2/3 条线。

    附加线按**日期**对齐到 pts 的横轴（对齐不上的日期跳过，不插值），
    所有线共用同一 Y 轴。必须同图：把 TACO / TACO-6 / TACO-6L 分成三张图
    各自 autoscale，会让人以为几套读数量级差很多 —— 实际差的是小数位。
    dashes = [第2条, 第3条] 的虚线样式，None 表示实线。
    """
    color = color or _T["accent"]
    if not pts:
        return ('<div style="height:%dpx;display:flex;align-items:center;'
                'justify-content:center;color:%s;font-size:12px">无数据</div>'
                % (h, _T["muted"]))
    n = len(pts)
    idx_of = {p[0]: i for i, p in enumerate(pts)}

    def _align(seq):
        return [(idx_of[d], v) for d, v in (seq or []) if d in idx_of]

    p2 = _align(pts2)
    p3 = _align(pts3)
    vs = [p[1] for p in pts]
    allv = vs + [v for _, v in p2] + [v for _, v in p3]
    lo, hi = min(allv), max(allv)
    if hi == lo:
        hi, lo = hi + 1, lo - 1
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    def X(i):
        return 8 + i * (w - 16) / max(1, n - 1)
    def Y(v):
        return h - 10 - (v - lo) / (hi - lo) * (h - 20)
    svg = ['<svg viewBox="0 0 %d %d" width="100%%" height="%d" '
           'preserveAspectRatio="none" role="img">' % (w, h, h)]
    # grid
    for k in range(1, 4):
        yy = 10 + k * (h - 20) / 4
        svg.append('<line x1="8" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" '
                   'stroke-width="1"/>' % (yy, w - 8, yy, _T["grid"]))
    if ref is not None and lo <= ref <= hi:
        svg.append('<line x1="8" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" '
                   'stroke-width="1" stroke-dasharray="4 3"/>'
                   % (Y(ref), w - 8, Y(ref), _T["muted"]))
    d = " ".join(("%s%.1f,%.1f" % ("M" if i == 0 else "L", X(i), Y(p[1])))
                 for i, p in enumerate(pts))
    svg.append('<path d="%s" fill="none" stroke="%s" stroke-width="1.8" '
               'stroke-linejoin="round"/>' % (d, color))
    svg.append('<circle cx="%.1f" cy="%.1f" r="3" fill="%s"/>'
               % (X(n - 1), Y(vs[-1]), color))
    _dashes = dashes or [None, None]
    for seq, col, dash in ((p2, color2 or _T["muted"], _dashes[0]),
                           (p3, color3 or _T["muted"], _dashes[1])):
        if len(seq) < 2:
            continue
        da = " ".join(("%s%.1f,%.1f" % ("M" if k == 0 else "L", X(i), Y(v)))
                      for k, (i, v) in enumerate(seq))
        st = (' stroke-dasharray="%s"' % dash) if dash else ""
        svg.append('<path d="%s" fill="none" stroke="%s" stroke-width="1.8"%s '
                   'stroke-linejoin="round"/>' % (da, col, st))
        svg.append('<circle cx="%.1f" cy="%.1f" r="3" fill="%s"/>'
                   % (X(seq[-1][0]), Y(seq[-1][1]), col))
    svg.append('</svg>')
    return "".join(svg)


def build_html(run_date, cfg, statuses, result, latest, stats, history_dir):
    """生成自包含 HTML 看板（浅色主题，内联 SVG）。"""
    dates = result.get("dates") or []
    T = result.get("T") or []
    S = result.get("S") or []

    def tail_pts(arr, limit=180):
        out = []
        for i in range(len(dates)):
            if arr[i] is not None:
                out.append((dates[i], arr[i]))
        return out[-limit:]

    taco_pts = tail_pts(T)
    s_pts = tail_pts(S)

    z_items = [
        ("z_10Y", "10Y 美债收益率", "Nz"),
        ("z_swap", "1Y 通胀互换（T5YIE）", "Oz"),
        ("z_approval", "特朗普支持率（取负）", "Pz"),
        ("z_DJIA", "道指涨跌幅（取倒数）", "Qz"),
        ("z_Brent", "布伦特原油", "Rz"),
    ]
    ext_on = bool(result.get("extended"))
    lvl_on = bool(result.get("extended_level"))
    hor_obs = bool(result.get("observe"))
    if ext_on:
        z_items.append(("z_transit", "霍尔木兹通行量 · 口径A 31日变动（取负）· 本项目扩展",
                        "Ntz"))
    if lvl_on:
        z_items.append(("z_transit_level", "霍尔木兹通行量 · 口径B 水位（取负）· 本项目扩展",
                        "Ltz"))
    # 观察模式：霍尔木兹**不进 z_items** —— 它没有 z，也不该出现在「分项 z 分数」
    # 那一组卡片里。给它独立面板（见下方「霍尔木兹观察面板」）。
    # 若把它混进 z_items，视觉上它就成了第 6 个因子 —— 与「不进指数」矛盾。

    h = []
    A = h.append
    A("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">")
    A("<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">")
    A("<title>TACO 压力指数看板 · %s</title>" % run_date)
    A("<style>")
    A("*{box-sizing:border-box}")
    A("body{margin:0;background:%s;color:%s;font:14px/1.6 -apple-system,"
      "BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif}"
      % (_T["bg"], _T["text"]))
    A(".wrap{max-width:1080px;margin:0 auto;padding:28px 20px 60px}")
    A("h1{font-size:22px;margin:0 0 4px;font-weight:650}")
    A("h2{font-size:15px;margin:28px 0 10px;font-weight:600;"
      "padding-bottom:6px;border-bottom:1px solid %s}" % _T["line"])
    A(".sub{color:%s;font-size:13px;margin-bottom:22px}" % _T["muted"])
    A(".kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));"
      "gap:12px;margin-bottom:8px}")
    A(".card{background:%s;border:1px solid %s;border-radius:10px;padding:14px}"
      % (_T["panel"], _T["line"]))
    A(".card .k{font-size:12px;color:%s;margin-bottom:6px}" % _T["muted"])
    A(".card .v{font-size:24px;font-weight:650;font-variant-numeric:tabular-nums}")
    A(".card .n{font-size:12px;color:%s;margin-top:4px}" % _T["muted"])
    A(".grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}")
    A(".box{background:%s;border:1px solid %s;border-radius:10px;padding:14px}"
      % (_T["panel"], _T["line"]))
    A(".box h3{font-size:13px;margin:0 0 8px;font-weight:600}")
    A("table{width:100%%;border-collapse:collapse;font-size:13px}")
    A("th,td{text-align:left;padding:7px 8px;border-bottom:1px solid %s}"
      % _T["line"])
    A("th{color:%s;font-weight:600;font-size:12px}" % _T["muted"])
    A("td.num{text-align:right;font-variant-numeric:tabular-nums}")
    A(".up{color:%s}.down{color:%s}" % (_T["up"], _T["down"]))
    A(".note{background:#fffbf0;border:1px solid #f0e0b8;border-radius:8px;"
      "padding:10px 12px;font-size:12.5px;color:#6b5a2a;margin:10px 0}")
    A(".foot{color:%s;font-size:12px;margin-top:34px;"
      "padding-top:14px;border-top:1px solid %s}" % (_T["muted"], _T["line"]))
    A("@media(max-width:760px){.kpi{grid-template-columns:1fr 1fr}"
      ".grid{grid-template-columns:1fr}}")
    A("</style></head><body><div class=\"wrap\">")

    A("<h1>TACO 压力指数看板</h1>")
    A("<div class=\"sub\">运行日期 %s · 口径：东吴宏观 TACO（公开数据复现，"
      "<b>5 因子</b>）%s · 指数单位是 z，不是概率</div>"
      % (run_date,
         " · 霍尔木兹通行量作<b>观察指标</b>（不进指数）" if hor_obs else ""))

    # KPI
    lv = latest.get("TACO") if latest else None
    pct = (stats.get("latest_percentile") or 0) * 100
    s6 = stats.get("taco6") or {}
    A("<div class=\"kpi\">")
    A("<div class=\"card\"><div class=\"k\">TACO 指数（5 因子 · 7 日均线）</div>"
      "<div class=\"v\">%s</div><div class=\"n\">%s</div></div>"
      % (_fmt(lv, 3), (latest or {}).get("date", "—")))
    if ext_on and latest and latest.get("TACO6") is not None:
        d6 = latest["TACO6"] - (lv or 0.0)
        _brkH = result.get("level_break_hormuz") or {}
        _mark = ("<span style=\"color:#c0392b\">⚠️ 水位断裂</span>"
                 if _brkH.get("ok") is False else "本项目扩展")
        A("<div class=\"card\"><div class=\"k\">TACO-6 · 口径A 31日变动</div>"
          "<div class=\"v\">%s</div><div class=\"n\">%s · 相对 5 因子 %+0.3f · %s"
          "</div></div>"
          % (_fmt(latest["TACO6"], 3), latest.get("date6", "—"), d6, _mark))
    if lvl_on and latest and latest.get("TACO6L") is not None:
        dl = latest["TACO6L"] - (lv or 0.0)
        A("<div class=\"card\"><div class=\"k\">TACO-6L · 口径B 水位</div>"
          "<div class=\"v\">%s</div><div class=\"n\">%s · 相对 5 因子 %+0.3f"
          "</div></div>"
          % (_fmt(latest["TACO6L"], 3), latest.get("date6L", "—"), dl))
    if hor_obs and latest and latest.get("transit") is not None:
        # 观察指标的 KPI 卡：**标题里就写明「不进指数」**。
        # 卡片是最容易被截图转发的部分 —— 只写「霍尔木兹 8 艘次」而不写
        # 「不进指数」，转发出去就会变成「TACO 里含霍尔木兹」。
        _ho = stats.get("hormuz_observe") or {}
        A("<div class=\"card\" style=\"border-color:#d8c48a;background:#fffdf5\">"
          "<div class=\"k\">霍尔木兹水位 · <b>观察指标（不进指数）</b></div>"
          "<div class=\"v\">%s</div>"
          "<div class=\"n\">艘次/日 · %s（真实观测 %s）</div></div>"
          % (_fmt(latest.get("transit"), 0), latest.get("date_transit", "—"),
             latest.get("date_transit_raw", "—")))
        A("<div class=\"card\" style=\"border-color:#d8c48a;background:#fffdf5\">"
          "<div class=\"k\">霍尔木兹水位分位</div>"
          "<div class=\"v\">%.1f%%</div><div class=\"n\">%s 个网格日</div></div>"
          % (100.0 * (_ho.get("latest_percentile") or 0), _ho.get("n", 0)))
    A("<div class=\"card\"><div class=\"k\">历史分位</div>"
      "<div class=\"v\">%.1f%%</div><div class=\"n\">共 %s 个观测</div></div>"
      % (pct, stats.get("n", 0)))
    A("<div class=\"card\"><div class=\"k\">区间 p10 / p50 / p90</div>"
      "<div class=\"v\" style=\"font-size:16px\">%s / %s / %s</div>"
      "<div class=\"n\">min %s · max %s</div></div>"
      % (_fmt(stats.get("p10"), 2), _fmt(stats.get("p50"), 2),
         _fmt(stats.get("p90"), 2), _fmt(stats.get("min"), 2),
         _fmt(stats.get("max"), 2)))
    okc = sum(1 for s in statuses if s.get("status", "").startswith("OK"))
    A("<div class=\"card\"><div class=\"k\">源健康</div>"
      "<div class=\"v\">%d / %d</div><div class=\"n\">成功 / 总计</div></div>"
      % (okc, len(statuses)))
    A("</div>")

    # 水位断裂横幅（第 6 因子断裂 -> 两套口径方向相反）
    _brkH2 = result.get("level_break_hormuz") or {}
    if ext_on and _brkH2.get("ok") is False:
        _zA = _fmt((latest or {}).get("z_transit"), 3)
        _zB = _fmt((latest or {}).get("z_transit_level"), 3)
        A("<div class=\"note\" style=\"background:#fdecea;border-color:#f0b4ac;"
          "color:#8a2b1f\">"
          "<b>⚠️ 第 6 因子水位断裂 —— 两套口径给出相反方向，谁都不要单独当结论。</b><br>"
          "原始序列（IMF PortWatch 霍尔木兹通行量）近 %s 日中位数 "
          "<b>%.1f</b> 艘次/日，基准期（%s .. %s）中位数为 <b>%.1f</b> 艘次/日"
          "（倍数 %.2f）。<br>"
          "<b>口径 A（31 日变动 → TACO-6）</b>z = %s：读作「通行量回升 → 压力下降」。<br>"
          "<b>口径 B（水位 → TACO-6L）</b>z = %s：读作「水位只有正常的百分之几 → "
          "压力高」。<br>"
          "一个说降、一个说升。**断点本身是真实信号还是数据假象，公开数据无法区分**，"
          "所以两套都出。<b>TACO（5 因子，东吴口径）不受影响，仍可用。</b>"
          "</div>"
          % (_brkH2.get("n_recent"), _brkH2.get("recent_median"),
             (_brkH2.get("base_window") or ["", ""])[0],
             (_brkH2.get("base_window") or ["", ""])[1],
             _brkH2.get("base_median"), _brkH2.get("ratio"), _zA, _zB))
    elif hor_obs and _brkH2.get("ok") is False:
        # 观察模式的横幅：**刻意用琥珀色而不是红色**。
        # 红色横幅在这套看板里的语义是「指数读数有问题」；而这里指数是好的，
        # 有问题的只是一条不进指数的观察序列。用红色会让人误判 TACO 降级。
        A("<div class=\"note\" style=\"background:#fffbf0;border-color:#f0e0b8;"
          "color:#6b5a2a\">"
          "<b>ℹ️ 霍尔木兹通行量水位断裂（观察指标 · 不影响 TACO）</b><br>"
          "IMF PortWatch 霍尔木兹通行量近 %s 日中位数 <b>%.1f</b> 艘次/日，"
          "基准期（%s .. %s）中位数为 <b>%.1f</b> 艘次/日（倍数 <b>%.2f</b>）。<br>"
          "这条序列<b>没有进入指数</b> —— TACO 的公式里只有 5 个因子，"
          "所以水位再低也不会改变上面的读数。它作为独立的地缘观察线索展示在"
          "下方「霍尔木兹观察面板」。<br>"
          "把它排除在指数之外的原因正是这次断裂：任何「N 日变动」类变换在断裂"
          "序列上都会退化成断点噪声，甚至给出与水位相反的方向。"
          "</div>"
          % (_brkH2.get("n_recent"), _brkH2.get("recent_median"),
             (_brkH2.get("base_window") or ["", ""])[0],
             (_brkH2.get("base_window") or ["", ""])[1],
             _brkH2.get("base_median"), _brkH2.get("ratio")))
    # 主图
    t6_pts = tail_pts(result.get("T6") or []) if ext_on else []
    t6l_pts = tail_pts(result.get("T6L") or []) if lvl_on else []
    A("<h2>TACO 指数走势（近 180 个交易日）</h2>")
    A("<div class=\"box\">")
    A(_spark(taco_pts, h=180, pts2=t6_pts, color2="#2f6fb2",
             pts3=t6l_pts, color3="#1a7f37", dashes=[None, "2 3"]))
    A("<div style=\"display:flex;justify-content:space-between;color:%s;"
      "font-size:12px;margin-top:6px\"><span>%s</span><span>%s</span></div>"
      % (_T["muted"], taco_pts[0][0] if taco_pts else "—",
         taco_pts[-1][0] if taco_pts else "—"))
    A("</div>")
    if t6_pts or t6l_pts:
        _parts = ["实线 = TACO（5 因子，东吴口径）"]
        if t6_pts:
            _parts.append("长虚线 = TACO-6（口径A 31日变动）")
        if t6l_pts:
            _parts.append("短虚线 = TACO-6L（口径B 水位）")
        A("<div style=\"font-size:12px;color:%s;margin:-4px 0 8px\">%s"
          "　三条线共用同一 Y 轴</div>" % (_T["muted"], " · ".join(_parts)))
    A("<div class=\"note\">TACO = 5 因子 z 分数等权合成（S）后再取 7 日均线。"
      "0 附近 = 历史均值水平；越高 = 压力越大。当前读数 %s，"
      "处于历史 %.0f%% 分位。%s</div>"
      % (_fmt(lv, 3), pct,
         ("TACO-6 与 TACO-6L 是同一序列的两种口径，见下方最后两个分项。"
          if (t6_pts or t6l_pts) else
          ("<b>这条曲线只由 5 个因子构成</b>；霍尔木兹通行量作为观察指标"
           "单列在下方「霍尔木兹观察面板」，未参与本曲线。" if hor_obs else ""))))

    # 合成值
    A("<h2>合成值 S（未平滑）</h2>")
    A("<div class=\"box\">")
    A(_spark(s_pts, h=140, color="#2f6fb2", ref=0.0,
             pts2=(tail_pts(result.get("S6") or []) if ext_on else []),
             color2="#c0392b",
             pts3=(tail_pts(result.get("S6L") or []) if lvl_on else []),
             color3="#1a7f37", dashes=[None, "2 3"]))
    A("</div>")

    # 分项
    A("<h2>%s分项 z 分数（近 180 日）</h2>"
      % {0: "5 个", 1: "6 个", 2: "7 个"}[int(ext_on) + int(lvl_on)])
    A("<div class=\"grid\">")
    for key, label, arrk in z_items:
        arr = result.get(arrk) or []
        pts = tail_pts(arr)
        cur = (latest or {}).get(key)
        cls = "up" if (cur or 0) >= 0 else "down"
        A("<div class=\"box\"><h3>%s <span class=\"%s\" style=\"float:right;"
          "font-variant-numeric:tabular-nums\">%s</span></h3>"
          % (_html.escape(label), cls, _fmt(cur, 3)))
        A(_spark(pts, h=110,
                 color=_T["up"] if (cur or 0) >= 0 else _T["down"]))
        A("</div>")
    A("</div>")

    # ---- 霍尔木兹观察面板（observe 模式专用）----
    #
    # 为什么给独立面板而不是塞进上面的 z 卡片：上面那组是**指数成分**，
    # 这里是**不进指数的观察序列**。混在一起视觉上就变成了「第 6 个因子」。
    # 独立面板 + 标题里写明「不进指数」= 一眼就能分清哪些进了、哪些没进。
    if hor_obs:
        _F = result.get("F") or []
        _ho = stats.get("hormuz_observe") or {}
        _mm = result.get("monthly_medians_hormuz") or []
        _lbo = result.get("level_break_hormuz") or {}
        A("<h2>霍尔木兹观察面板 <span style=\"font-weight:400;font-size:12px;"
          "color:%s\">（不进指数 · 独立观察）</span></h2>" % _T["muted"])
        A("<div class=\"box\" style=\"border-color:#d8c48a;background:#fffdf5\">")
        A("<h3>通行量水位（原始序列，前值填充到日历网格）"
          "<span style=\"float:right;font-variant-numeric:tabular-nums;"
          "font-weight:650\">%s 艘次/日</span></h3>"
          % _fmt((latest or {}).get("transit"), 0))
        A(_spark(tail_pts(_F), h=130, color="#b8860b"))
        A("<div style=\"font-size:12px;color:%s;margin-top:6px\">"
          "网格覆盖 %s 天（%s .. %s）· 原始源 %s 天 · 真实观测日 %s"
          "（源滞后约 %s 天，网格末日由前值填充）</div>"
          % (_T["muted"], _ho.get("n"), _ho.get("first_date"),
             _ho.get("last_date"), _ho.get("raw_n"),
             (latest or {}).get("date_transit_raw"),
             (latest or {}).get("transit_raw_lag_days")))
        A("<div class=\"note\" style=\"margin-top:12px\">"
          "<b>这条线没有参与上面的 TACO 计算。</b> 它通过前值填充对齐到同一条"
          "日历网格，但<b>不进入网格构造、不加权、不取 z、不进 S、不进 T</b>。"
          "自检里有逐点等值断言：去掉它，TACO 的每一个值都完全相同。"
          "</div>")
        if _mm:
            A("<h3 style=\"margin-top:16px\">月度中位数（水位，不是变动量）</h3>")
            A("<table><thead><tr><th>月份</th><th class=\"num\">观测数</th>"
              "<th class=\"num\">n_total 中位数</th><th class=\"num\">相对基准</th>"
              "</tr></thead><tbody>")
            _bm = _lbo.get("base_median")
            for mo, cnt, med in _mm[-14:]:
                rel = ("%.0f%%" % (100.0 * med / _bm)) if _bm else "—"
                _warn = (_bm and med / _bm < 0.4)
                A("<tr><td>%s</td><td class=\"num\">%s</td><td class=\"num\">%s</td>"
                  "<td class=\"num\"%s>%s</td></tr>"
                  % (mo, cnt, _fmt(med, 0),
                     " style=\"color:#8a2b1f;font-weight:650\"" if _warn else "",
                     rel))
            A("</tbody></table>")
            A("<div style=\"font-size:12px;color:%s;margin-top:8px\">"
              "基准期中位数 %s 艘次/日（%s .. %s）。低于基准 40%% 的月份标红。"
              "</div>"
              % (_T["muted"], _fmt(_lbo.get("base_median"), 1),
                 (_lbo.get("base_window") or ["", ""])[0],
                 (_lbo.get("base_window") or ["", ""])[1]))
        A("</div>")

    # 源状态
    A("<h2>数据源状态</h2>")
    A("<table><thead><tr><th>序列</th><th>角色</th><th>信源</th><th>状态</th>"
      "<th class=\"num\">观测</th><th>最新日期</th><th class=\"num\">最新值</th>"
      "</tr></thead><tbody>")
    for s in statuses:
        A("<tr><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td>"
          "<td class=\"num\">%s</td><td>%s</td><td class=\"num\">%s</td></tr>"
          % (s.get("id"), s.get("role"), s.get("provider"), s.get("status"),
             s.get("n") if s.get("n") is not None else "—",
             s.get("last_date") or "—",
             _fmt(s.get("last_value"), 4)
             if s.get("last_value") is not None else "—"))
    A("</tbody></table>")

    # 公式
    A("<h2>计算口径</h2>")
    A("<div class=\"box\" style=\"font-family:ui-monospace,Consolas,monospace;"
      "font-size:12.5px;white-space:pre-wrap;line-height:1.9\">")
    A(_html.escape("\n".join(cfg.get("formula", {}).get("steps") or [])))
    A("</div>")

    # 披露
    A("<h2>关键披露</h2>")
    A("<div class=\"note\">"
      "<b>USSWIT1 用的是公开替代源。</b>底稿 D 列是彭博专有的 1 年期通胀互换，"
      "无公开日频等价物。本项目改用 FRED <code>T5YIE</code>（5 年期盈亏平衡通胀率），"
      "依据是 423 个重叠观测上 31 日差分的相关性排序。<br><br>"
      + (
        "<b>TRHBCCCD（霍尔木兹通行量）本轮只作观察指标，不进入 TACO。</b>"
        "底稿 F 列虽有该数据，但反解确认 0 个公式引用它 —— 东吴 TACO 只用 5 个因子。"
        "本轮按用户要求把 TACO 回到 5 因子口径，该序列保留为观察指标：抓取、"
        "落盘、在「霍尔木兹观察面板」展示，<b>不加权、不取 z、不进 S、不进 T</b>。"
        "<b>指数 = 5 个因子</b>（10Y 美债 · 通胀互换代理 · 支持率 · 道指 · 布伦特）。"
        "因此本看板上的 TACO <b>可直接与东吴原版逐格对账</b>。<br><br>"
        "<b>ℹ️ 该序列在 2026-03 出现结构性水位断裂且未恢复</b>（约 78 → 约 3 艘次/日，"
        "同期苏伊士/曼德海峡/马六甲/台湾海峡/巴拿马均平稳，即只有霍尔木兹断了）。"
        "<b>这正是它不进指数的原因</b>：水位断裂后任何「N 日变动」类变换都会退化成"
        "断点噪声，甚至给出与水位相反的方向 —— 当作因子等于给指数注入一个已知"
        "不可靠的信号。当作观察指标则是一条有价值的地缘线索。"
        "水位断裂守卫仍在运行，但它现在的告警语义是「观察事实」，不是「指数降级」"
        "（看板顶部横幅用琥珀色而非红色，就是为了不让人误判 TACO）。<br><br>"
        if hor_obs else
        "<b>TRHBCCCD（霍尔木兹通行量）是第 6 因子 —— 本项目扩展，不是东吴口径。</b>"
        "底稿 F 列虽有该数据，但反解确认 0 个公式引用它，东吴 TACO 只用 5 个因子。"
        "本项目把它并行并入，<b>两套口径都出</b>：<b>TACO-6</b>（口径 A：31 日变动）与 "
        "<b>TACO-6L</b>（口径 B：水位 z）。5 因子 TACO 一个字节未改，"
        "对账只对 5 因子版本。<br><br>"
        "<b>⚠️ 该序列在 2026-03 出现结构性水位断裂且未恢复</b>（约 78 → 约 3 艘次/日，"
        "同期苏伊士/曼德海峡/马六甲/台湾海峡/巴拿马均平稳，即只有霍尔木兹断了）。"
        "水位断裂后两套口径<b>方向相反</b>（A 说压力降、B 说压力高），"
        "而「断点是真实信号还是数据假象」公开数据无法区分 —— 所以两套都出，"
        "都不隐藏。本项目加装了水位断裂守卫，报警时在看板顶部横幅与报告 §1/§5a "
        "显著披露。<br><br>")
      + "<b>不构成投资建议。</b>所有源均无需登录、无需密钥。"
      "</div>")

    A("<div class=\"foot\">由 <code>taco-monitor</code> 自动生成 · "
      "配置版本 %s · 原始载荷留档于 <code>output/%s/raw/</code></div>"
      % (cfg.get("version", "—"), run_date))
    A("</div></body></html>")
    return "".join(h)


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------

def build_xlsx(path, run_date, cfg, statuses, result, latest, stats,
               our_vs_dongwu=None):
    """生成 xlsx。openpyxl 可选依赖，缺失则跳过（返回 None）。"""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        return None

    wb = Workbook()
    hdr_fill = PatternFill("solid", fgColor="F0F2F5")
    hdr_font = Font(bold=True)

    # READ ME
    ws = wb.active
    ws.title = "READ ME"
    _ob = bool(result.get("observe"))
    _ho = stats.get("hormuz_observe") or {}
    rows = [
        ["TACO 压力指数（公开数据复现东吴宏观口径）", ""],
        ["运行日期", run_date],
        ["配置版本", cfg.get("version", "")],
        ["指数口径", "**5 因子**：10Y 美债 / 通胀互换代理(T5YIE) / 支持率 / 道指 / 布伦特"],
        ["", ""],
        ["指数最新读数", latest.get("TACO") if latest else None],
        ["读数日期", latest.get("date") if latest else None],
        ["合成值 S（未平滑）", latest.get("composite_S") if latest else None],
    ]
    if not _ob:
        rows += [
            ["TACO-6 读数（口径A 31日变动）",
             latest.get("TACO6") if latest else None],
            ["TACO-6 读数日期", latest.get("date6") if latest else None],
            ["TACO-6L 读数（口径B 水位）",
             latest.get("TACO6L") if latest else None],
            ["TACO-6L 读数日期", latest.get("date6L") if latest else None],
            ["合成值 S6（6 因子，口径A）",
             latest.get("composite_S6") if latest else None],
            ["合成值 S6L（6 因子，口径B）",
             latest.get("composite_S6L") if latest else None],
        ]
    else:
        rows += [
            ["霍尔木兹通行量（**观察指标，不进指数**）",
             latest.get("transit") if latest else None],
            ["霍尔木兹 · 网格末日（前值填充）",
             latest.get("date_transit") if latest else None],
            ["霍尔木兹 · 真实观测日",
             latest.get("date_transit_raw") if latest else None],
            ["霍尔木兹 · 水位分位",
             _ho.get("latest_percentile")],
            ["霍尔木兹 · 网格覆盖天数", _ho.get("n")],
            ["霍尔木兹 · 原始源天数", _ho.get("raw_n")],
        ]
    rows += [
        ["历史分位", (stats.get("latest_percentile") or 0)],
        ["观测数", stats.get("n")],
        ["均值", stats.get("mean")],
        ["标准差", stats.get("stdev")],
        ["最小", stats.get("min")],
        ["最大", stats.get("max")],
        ["", ""],
        ["单位", "z（不是概率、不是预测）"],
        ["USSWIT1 处理", "用公开替代源 FRED T5YIE（5Y 盈亏平衡通胀），"
                        "不使用东吴底稿 D 列真实值"],
        ["TRHBCCCD 处理",
         ("**观察指标**：抓取 / 落盘 / 展示，**不加权、不取 z、不进 S、不进 T**。"
          "指数只有 5 个因子，故本表可直接与东吴原版逐格对账"
          if _ob else
          "不入 5 因子 TACO（底稿 0 引用 F 列）；作为本项目扩展的第 6 因子进 TACO-6")],
    ]
    if not _ob:
        rows += [
            ["TACO / TACO-6 / TACO-6L",
             "TACO = 东吴 5 因子复刻（可对账）；TACO-6 = 加霍尔木兹且用【31日变动】口径；"
             "TACO-6L = 加霍尔木兹且用【水位】口径。后两者为本项目扩展，不可对账"],
            ["TACO-6 降级标记",
             ("是 —— 霍尔木兹水位断裂（近/基准 = %.2f），两套口径方向可能相反，"
              "勿单独解读" % ((result.get("level_break_hormuz") or {}).get("ratio") or 0))
             if (result.get("level_break_hormuz") or {}).get("ok") is False
             else "否 —— 霍尔木兹水位体检通过"],
        ]
    else:
        rows += [
            ["霍尔木兹水位断裂",
             ("是 —— 但它是**观察事实**，不进指数，TACO 读数不受影响（近/基准 = %.2f）"
              % ((result.get("level_break_hormuz") or {}).get("ratio") or 0))
             if (result.get("level_break_hormuz") or {}).get("ok") is False
             else "否 —— 霍尔木兹水位体检通过"],
            ["汇率因子（Bonnast / 里亚尔）",
             "已于 2026-09-23 按用户要求移除：不抓取、不落盘、不参与合成"],
        ]
    rows += [
        ["数据来源", "FRED fredgraph.csv / Datawrapper CDN / IMF PortWatch ArcGIS"],
        ["免责", "仅作研究与监测用途，不构成投资建议"],
    ]
    for r in rows:
        ws.append(r)
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 62
    for c in ws["A"]:
        c.font = hdr_font

    # 计算口径
    ws2 = wb.create_sheet("计算口径")
    ws2.append(["步骤"])
    for step in (cfg.get("formula", {}).get("steps") or []):
        ws2.append([step])
    ws2.append([""])
    ws2.append(["复现精度（用底稿原始输入复算）"])
    ws2.append(["S 列 max_abs_diff", "1.22e-09"])
    ws2.append(["T 列 max_abs_diff", "1.75e-10"])
    ws2.column_dimensions["A"].width = 24
    ws2.column_dimensions["B"].width = 70

    # 源状态
    ws3 = wb.create_sheet("数据源状态")
    ws3.append(["序列", "角色", "信源", "状态", "观测数", "最新日期", "最新值",
                "实际出口", "生效 UA"])
    for s in statuses:
        ws3.append([s.get("id"), s.get("role"), s.get("provider"),
                    s.get("status"), s.get("n"), s.get("last_date"),
                    s.get("last_value"), s.get("channel"), s.get("ua")])
    for c in ws3[1]:
        c.font = hdr_font
        c.fill = hdr_fill

    # TACO 全量
    # 列定义与取数统一走 taco.output_row —— 与 CSV 共用同一份契约。
    # 曾经这里也是手写映射、且把第 6 因子的原始水位列取到了 result["cols"]，
    # 整列写空（与 CSV 同源出错）。只有一份契约，就不会两处一起错。
    ws4 = wb.create_sheet("TACO 指数（全量）")
    cols = taco.output_columns(result)
    ws4.append(cols)
    for c in ws4[1]:
        c.font = hdr_font
        c.fill = hdr_fill
    dates = result.get("dates") or []
    for i in range(len(dates)):
        r = taco.output_row(result, i, cols)
        ws4.append([r.get(c) for c in cols])
    ws4.column_dimensions["A"].width = 12
    for k in range(1, len(cols)):
        ws4.column_dimensions[get_column_letter(k + 1)].width = 18

    # 霍尔木兹水位面板（两种角色都出：断裂是共同事实）
    _mm = result.get("monthly_medians_hormuz") or []
    if _mm:
        ws6 = wb.create_sheet("霍尔木兹观察面板" if _ob else "第6因子水位面板")
        brk = result.get("level_break_hormuz") or {}
        ws6.append(["霍尔木兹通行量（n_total）水位 —— 变动量看不到的东西"])
        if _ob:
            ws6.append(["角色", "**观察指标**：不进指数，TACO 读数不受本表影响"])
        ws6.append(["近 %d 日中位数" % brk.get("n_recent"),
                    brk.get("recent_median")])
        ws6.append(["基准期中位数（%s .. %s）"
                    % ((brk.get("base_window") or ["", ""])[0],
                       (brk.get("base_window") or ["", ""])[1]),
                    brk.get("base_median")])
        ws6.append(["倍数（近/基准）", brk.get("ratio")])
        ws6.append(["判定",
                    ("断裂（观察事实，不影响 TACO）" if _ob
                     else "断裂（TACO-6 降级）")
                    if brk.get("ok") is False
                    else ("正常" if brk.get("ok") else "未完成")])
        ws6.append([])
        ws6.append(["月份", "观测数", "n_total 中位数", "相对基准"])
        for mo, cnt, med in _mm:
            rel = (med / brk["base_median"]) if brk.get("base_median") else None
            ws6.append([mo, cnt, med, rel])
        for c in ws6[1]:
            c.font = hdr_font
        for c in ws6[8]:
            c.font = hdr_font
            c.fill = hdr_fill
        ws6.column_dimensions["A"].width = 34
        for col in "BCD":
            ws6.column_dimensions[col].width = 18

    # 对照
    if our_vs_dongwu:
        ws5 = wb.create_sheet("对比东吴原版")
        ws5.append(["区间", "重叠观测", "相关系数", "秩相关", "平均绝对差"])
        for w in our_vs_dongwu:
            ws5.append([w.get("label"), w.get("n"), w.get("corr"),
                        w.get("rank_corr"), w.get("mean_abs_diff")])
        for c in ws5[1]:
            c.font = hdr_font
            c.fill = hdr_fill

    ensure_dir(os.path.dirname(os.path.abspath(path)))
    # 与其它产物统一走 net 的三层写盘纪律。原来这里直接 os.replace：目标被
    # Excel/WPS 打开时会抛 PermissionError[WinError 5]，整轮日更就崩在最后
    # 一步 —— 而用户不过是打开看了一眼表格。atomic_write_bytes 会按
    # 「退避重试 -> 就地覆盖 -> .locked- 降级」逐级兜住，并返回**实际**路径。
    buf = io.BytesIO()
    wb.save(buf)
    return net.atomic_write_bytes(path, buf.getvalue())
