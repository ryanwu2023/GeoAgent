# -*- coding: utf-8 -*-
"""
taco.py —— TACO 压力指数计算（东吴宏观口径的公开数据复现）

公式来源：东吴宏观《TACO 压力指数》数据底稿，已逐格反解，
          用底稿原始输入复算，S 列 max_abs_diff=1.22e-09、T 列 1.75e-10。
          （校验脚本见 tools/verify_report.py 的 --against-workbook）

计算链（底稿列名 -> 本模块变量）：
  C = 10Y 美债收益率            (USGG10YR)
  D = 1Y 通胀互换               (USSWIT1；本项目用 T5YIE 代理)
  E = 特朗普支持率              (RCPPTAPP)
  G = 道琼斯指数                (INDU)
  H = 布伦特原油                (CO1)

  I = C - C[-31]                31 日变动
  J = D - D[-31]
  K = -(E - E[-31])             支持率【取负】：支持率升 = 压力降
  L = 1/(G/G[-31] - 1)          道指涨跌幅的【倒数】：跌得越狠 = 压力越高
  M = H - H[-31]
  N..R = expanding z(I..M)      扩张窗口 z 分数
  S = mean(N..R)                5 因子等权合成
  T = 7-row MA of S             TACO 指数

【三个致命细节，少一个就复现不出来】
  1. 网格是【日历日】不是交易日：周末/假期沿用前一交易日值（前值填充）。
     底稿行号差 31 即日历 31 天。
  2. z 分数是【扩张窗口】：起点固定在第 31 个索引，窗口随时间右扩，
     不是滚动窗口。用滚动窗口会得到完全不同（且更好看）的曲线。
  3. 7 日 MA 必须【跳过空值】（Excel AVERAGE 语义）。工作簿 S 列因支持率
     分项起始晚而从第 60 行（idx 55）才有值，MA 窗口起点随之是 55。
     若用"窗口内任一为空则整窗为空"的语义，复现误差会跳到 0.4 量级。

【第 6 因子（本项目扩展，不是复刻）】
  东吴底稿的 TACO 只用 5 个因子：数据底稿 F 列（TRHBCCCD 霍尔木兹通行量）
  虽有数据，但 0 个公式引用它，反解确认它不进指数。本模块按用户要求把它
  作为第 6 因子并行并入，产出 TACO-6；原 I..M / N..R / S / T 一个字节都不动，
  所以已验证的 5 因子复刻结果（max_abs_diff < 1e-9）依然成立。

      F  = TRHBCCCD 通行量（总艘次，ffill 到既有日历网格）
      Nt = -(F - F[-31])          通行量降 = 供给压力升，故取负
      Ntz = expanding z(Nt)
      S6 = mean(N..R, Ntz)        6 因子等权
      T6 = 7-row MA of S6         TACO-6

【第二种口径：水位（level）—— 因序列水位断裂而并列输出】
  「31 日变动」在水位断裂的序列上会退化成断点噪声，甚至给出与水位相反的方向。
  公开数据无法区分「真实断裂」与「数据假象」，故再并列产出一套**水位口径**：

      Ltz = -z(F)                 水位直接进 z（取负：通行量低 = 压力贡献高）
      S6L = mean(N..R, Ltz)
      T6L = 7-row MA of S6L       TACO-6L

  注意 z 对负号等变（z(-x) = -z(x)），所以「先取负再 z」与「先 z 再取负」等价。
  两套口径**不互相替代**，报告里必须并列展示。

  **纪律**：第 6 因子必须 ffill 到既有网格，绝不参与网格构造。若它带来 E 列
  没有的日期，面板长度会变，扩张窗口 z 分数整体漂移，5 因子结果就被改掉了。

【第 7 因子（本项目扩展）：Bonbast USD 卖出价 / 里亚尔】
  数据源 Bonnast 德黑兰自由市场，取 USD 对里亚尔的**卖出价**。见 tacolib/bonbast.py。

      F2  = BONBAST USD 卖出价（里亚尔，ffill 到既有日历网格）
      Nt2 =  (F2 - F2[-31])       【正号】汇率上行 = 里亚尔贬值 = 压力升
      Ntz2= expanding z(Nt2)
      S7  = mean(N..R, Ntz, Ntz2)     7 因子等权（5 基础 + 霍尔木兹 + 里亚尔）
      T7  = 7-row MA of S7            TACO-7

  水位口径同构：

      Ltz2 =  z(F2)               水位高 = 里亚尔弱 = 压力贡献高，故【正号】
      S7L  = mean(N..R, Ltz, Ltz2)
      T7L  = 7-row MA of S7L         TACO-7L

  **符号为什么是正号**（与第 6 因子的取负相反）：本模块全部因子的统一约定是
  「压力上升 -> 因子上升」。霍尔木兹是通行量**下降**代表压力上升，故取负；
  里亚尔是汇率**上升**（贬值）代表压力上升，故不取负。两者方向相反、约定一致，
  不能因为「第 6 因子取了负」就照抄。

  **已知硬约束：只有近 60 天**。Bonnast 的 /graph 接口固定返回 60 天，实测
  ``?range=1y`` 无效、``/graph/usd/365`` 404。ffill 到既有网格后：
    水位口径  T7L  约从第 1 天起可用（z 只需 2 个点）
    变动口径  T7   需 F2 与 F2[-31] 同时存在 -> 最早约 T-60+31 起可用
  所以第 7 因子的有效观测数**远小于** 5/6 因子，报告与落盘都必须披露实际
  有效起止日，不许让它看起来和 5 因子一样长。

【观察指标模式（2026-09-23 起为默认）：霍尔木兹通行量】observe_factor
  按用户要求，TACO 回到**东吴原始的 5 因子口径**，霍尔木兹通行量降级为
  **观察指标**：抓取、前值填充、落盘原始水位列、在看板与报告里单独展示，
  但**不加权、不取 z、不进 S、不进 T**。

      F = TRHBCCCD 通行量（总艘次，ffill 到既有日历网格）   <- 只此一个派生量

  与 composite 模式（第 6 因子）的区别只有一点：**少了 Nt/Ntz/S6/T6 与
  Ltz/S6L/T6L 这六个派生数组**。TACO 本身在两种模式下都完全一致 ——
  观察指标唯一的风险是「偷偷进了合成」，所以 verify 里有一条独立断言：
  observe 模式下 S / T / 5 个 z 必须与纯 5 因子逐点相等。

  为什么不做成因子：底稿 F 列虽有该序列，但 0 个公式引用它（东吴 TACO 只用
  5 个因子）；且该序列 2026-03 出现结构性水位断裂（约 78 → 约 3 艘次/日，
  至今未恢复），任何「N 日变动」类变换都会退化成断点噪声。

  互斥：observe_factor 与 extended_factor 不能同时给（一个序列不能既进又不进
  指数）。同时给会直接抛 ValueError，不做静默优先级 —— 静默优先级正是
  「以为没进指数、其实进了」这类事故的温床。
"""

LAG = 31          # 日历日差分步长（底稿用日历 31 天，不是自然月）
FIRST = 31        # 第一个能算出 31 日差分的索引
MA_WINDOW = 7     # TACO 的移动平均窗口
Z_DDOF = 1        # STDEV.S = 样本标准差（ddof=1）


def build_calendar_panel(series_by_name):
    """把多个 {date: value} 拼成日历日面板，各序列前值填充。

    series_by_name: {"C": {...}, "D": {...}, ...}
    返回 (dates, cols) —— dates 为排序后的日历日列表，cols 为 {name: [v|None]}
    """
    nonempty = {k: v for k, v in series_by_name.items() if v}
    if not nonempty:
        return [], {}
    start = max(min(s) for s in nonempty.values())
    all_dates = set()
    for s in nonempty.values():
        all_dates |= set(s)
    dates = sorted(d for d in all_dates if d >= start)

    cols = {}
    for name, s in series_by_name.items():
        out, last = [], None
        for d in dates:
            if d in s:
                last = s[d]
            out.append(last)
        cols[name] = out
    return dates, cols


def ffill_series(series, dates):
    """单序列前值填充到给定日期网格。"""
    out, last = [], None
    for d in dates:
        if d in series:
            last = series[d]
        out.append(last)
    return out


def diff_lag(a, n, lag=LAG, first=FIRST):
    """a[i] - a[i-lag]，索引 < first 或任一端为空则 None。"""
    return [None if (i < first or a[i] is None or a[i - lag] is None)
            else a[i] - a[i - lag] for i in range(n)]


def negate(a):
    return [None if x is None else -x for x in a]


def reciprocal_pct_change(a, n, lag=LAG, first=FIRST):
    """1/(a[i]/a[i-lag] - 1)。分母为 0 或任一端为空则 None。"""
    out = [None] * n
    for i in range(n):
        if i < first or a[i] is None or a[i - lag] in (None, 0):
            continue
        g = a[i] / a[i - lag] - 1
        if g == 0:
            continue
        out[i] = 1.0 / g
    return out


def expanding_z(x, n, first=FIRST, ddof=Z_DDOF, min_obs=2):
    """扩张窗口 z 分数（底稿 N:R 列）。

    z[i] = (x[i] - mean(x[first..i])) / stdev_s(x[first..i])
    窗口起点固定为 first，右端随 i 扩张。空值被跳过但不阻断窗口。
    """
    out = [None] * n
    for i in range(n):
        win = [x[k] for k in range(first, i + 1) if x[k] is not None]
        if x[i] is None or len(win) < min_obs:
            continue
        m = sum(win) / len(win)
        var = sum((v - m) ** 2 for v in win) / (len(win) - ddof)
        sd = var ** 0.5
        out[i] = (x[i] - m) / sd if sd else None
    return out


def moving_average_skip_blanks(x, n, window=MA_WINDOW):
    """7 项移动平均，跳过空值（Excel AVERAGE 语义）。"""
    out = [None] * n
    for i in range(n):
        win = [x[k] for k in range(max(0, i - window + 1), i + 1)
               if x[k] is not None]
        if win:
            out[i] = sum(win) / len(win)
    return out


def compute(series_by_name, lag=LAG, first=FIRST, ma_window=MA_WINDOW,
            z_ddof=Z_DDOF, extended_factor=None, extended_factor_2=None,
            observe_factor=None):
    """主入口。

    series_by_name: {"C": {...}, "D": {...}, "E": {...}, "G": {...}, "H": {...}}
                    键名沿用底稿列名，便于逐格对账。
    extended_factor: 可选的第 6 个因子 {date: value}（本项目为霍尔木兹通行量）。
                    **东吴底稿的 TACO 只用 5 个因子**（数据底稿 F 列被完全跳过），
                    所以这是本项目的方法论扩展，不是复刻。它会额外产出
                    Nt/Ntz/S6/T6，而 I..M、Nz..Rz、S、T 一律保持原样。
    observe_factor: 可选的**观察指标** {date: value}（同一条霍尔木兹序列）。
                    只 ffill 到既有网格并落盘原值（F），**不产出任何派生 z/S/T**。
                    与 extended_factor **互斥** —— 见模块 docstring。

    纪律：扩展/观察序列都必须**前值填充到既有日期网格**，绝不参与网格构造。
    否则一旦它带来 E 列没有的日期，面板长度变化会让扩张窗口的 z 分数整体漂移，
    把已验证的 5 因子复刻结果改掉。
    返回 dict：
      dates, I..M (差分), N..R (z), S (合成), T (TACO), coverage
      [composite 时] Nt, Ntz, S6, T6, Ltz, S6L, T6L
      [observe 时]   F（原始水位）
    """
    if observe_factor and extended_factor:
        raise ValueError(
            "observe_factor 与 extended_factor 互斥：同一条序列不能既作为"
            "第 6 因子进入合成、又作为观察指标不进合成。请二选一"
            "（config.extended_factor.mode = observe | composite）。")
    dates, cols = build_calendar_panel(series_by_name)
    n = len(dates)
    if n == 0:
        return {"dates": [], "T": [], "coverage": {}}

    C = cols.get("C", [None] * n)
    D = cols.get("D", [None] * n)
    E = cols.get("E", [None] * n)
    G = cols.get("G", [None] * n)
    H = cols.get("H", [None] * n)

    I = diff_lag(C, n, lag, first)
    J = diff_lag(D, n, lag, first)
    K = negate(diff_lag(E, n, lag, first))
    L = reciprocal_pct_change(G, n, lag, first)
    M = diff_lag(H, n, lag, first)

    Nz = expanding_z(I, n, first, z_ddof)
    Oz = expanding_z(J, n, first, z_ddof)
    Pz = expanding_z(K, n, first, z_ddof)
    Qz = expanding_z(L, n, first, z_ddof)
    Rz = expanding_z(M, n, first, z_ddof)

    S = [None] * n
    for i in range(n):
        vals = [z[i] for z in (Nz, Oz, Pz, Qz, Rz) if z[i] is not None]
        if len(vals) == 5:
            S[i] = sum(vals) / 5.0

    T = moving_average_skip_blanks(S, n, ma_window)

    def _cov(name, arr):
        v = [i for i in range(n) if arr[i] is not None]
        return {
            "n_valid": len(v),
            "first_date": dates[v[0]] if v else None,
            "last_date": dates[v[-1]] if v else None,
            "last_value": arr[v[-1]] if v else None,
            "name": name,
        }

    coverage = {
        "I_10Y_diff": _cov("10Y 31d chg", I),
        "J_swap_diff": _cov("1Y swap 31d chg", J),
        "K_approval_diff": _cov("approval 31d chg (neg)", K),
        "L_djia_recip": _cov("DJIA pct-chg reciprocal", L),
        "M_brent_diff": _cov("Brent 31d chg", M),
        "S_composite": _cov("composite (5-factor mean)", S),
        "T_taco": _cov("TACO index", T),
    }

    out = {
        "dates": dates,
        "cols": cols,
        "I": I, "J": J, "K": K, "L": L, "M": M,
        "Nz": Nz, "Oz": Oz, "Pz": Pz, "Qz": Qz, "Rz": Rz,
        "S": S, "T": T,
        "coverage": coverage,
        "lag": lag, "first": first, "ma_window": ma_window, "z_ddof": z_ddof,
        "n_days": n,
    }

    # ---- 观察指标（只落盘原值，不进任何合成）----
    #
    # 位置刻意放在第 6 因子之前：观察模式是**更简单**的那一支，先落地它，
    # 后面 composite 那一大段就完全不受影响（它们都挂在 extended_* 标记上）。
    if observe_factor:
        Fx = ffill_series(observe_factor, dates)
        # 记下**原始源的最后一个真实观测日**。ffill 之后 F 的末值日期 = 网格
        # 末日，看起来「今天就有数」；而霍尔木兹是 AIS 事后汇编、滞后 4~7 天，
        # 真实观测日总是更早。不把这两个日期分开，读者会把「前值填充」当成
        # 「今天的水位」——这正是 staleness 配置里 max_lag=14 想拦住的事。
        _src_dates = [d for d, v in (observe_factor or {}).items()
                      if v is not None]
        out.update({"F": Fx, "observe": True,
                    "observe_src_last": max(_src_dates) if _src_dates else None,
                    "observe_src_n": len(_src_dates)})
        coverage["F_transit_observe"] = _cov(
            "Hormuz transit (observation only, NOT in index)", Fx)

    # ---- 可选第 6 因子（本项目：霍尔木兹通行量；东吴底稿不含此项）----
    if extended_factor:
        # 前值填充到**既有**网格，不参与 dates 构造（见 docstring 的纪律）
        Fx = ffill_series(extended_factor, dates)
        # 通行量下降 = 供给压力上升，故取负（与支持率 K 同一处理方向）
        Nt = negate(diff_lag(Fx, n, lag, first))
        Ntz = expanding_z(Nt, n, first, z_ddof)

        S6 = [None] * n
        for i in range(n):
            vals = [z[i] for z in (Nz, Oz, Pz, Qz, Rz, Ntz)
                    if z[i] is not None]
            if len(vals) == 6:
                S6[i] = sum(vals) / 6.0
        T6 = moving_average_skip_blanks(S6, n, ma_window)

        # F 是底稿列名（底稿 F 列即 TRHBCCCD），存 ffill 到网格后的原值便于落盘
        out.update({"F": Fx, "Nt": Nt, "Ntz": Ntz, "S6": S6, "T6": T6,
                    "extended": True})
        coverage["Nt_transit_diff"] = _cov("Hormuz transit 31d chg (neg)", Nt)
        coverage["S6_composite"] = _cov("composite (6-factor mean)", S6)
        coverage["T6_taco"] = _cov("TACO-6 index", T6)

        # ---- 同序列的第二种口径：水位（level）口径 ----
        # 为什么要有第二套：当序列**水位断裂**时（口径变更 / 区域性信号丢失），
        # 「31 日变动」会退化成断点噪声，甚至给出与水位**相反**的方向
        # （实测霍尔木兹 2026-03 从 ~78 断到 ~3 艘次/日，变动口径读作「压力下降」）。
        # 公开数据无法区分「真实断裂」与「数据假象」，所以两套并列输出，
        # 不替使用者挑口径。
        #
        #   水位口径第 6 因子 Ltz = -z(F)
        #
        # 取负后「通行量低 = 压力贡献高」。注意 z 对负号是等变的（z(-x) = -z(x)），
        # 所以「先取负再 z」与「先 z 再取负」完全等价，这里写成先 z 再 negate 更直观。
        Fz = expanding_z(Fx, n, first, z_ddof)
        Ltz = negate(Fz)
        S6L = [None] * n
        for i in range(n):
            vals = [z[i] for z in (Nz, Oz, Pz, Qz, Rz, Ltz) if z[i] is not None]
            if len(vals) == 6:
                S6L[i] = sum(vals) / 6.0
        T6L = moving_average_skip_blanks(S6L, n, ma_window)

        out.update({"Ltz": Ltz, "S6L": S6L, "T6L": T6L,
                    "extended_level": True})
        coverage["Ltz_transit_level"] = _cov("Hormuz transit level z (neg)", Ltz)
        coverage["S6L_composite"] = _cov("composite (6-factor level mean)", S6L)
        coverage["T6L_taco"] = _cov("TACO-6L index (level wording)", T6L)

    # ---- 第 7 因子（本项目：Bonbast USD 卖出价 / 里亚尔）----
    # 与前 6 因子同一套纪律：ffill 到**既有**网格，不参与网格构造。
    # 符号与前 6 因子相反（不取负）—— 见模块 docstring 的符号约定说明。
    # 第 7 因子只在第 6 因子就绪时并入，否则 7 因子等于「5+里亚尔」而缺霍尔木兹，
    # 那是一个没人验证过、也说不清口径的混合体，不如干脆不出。
    if extended_factor_2 and result_has_6(out):
        Fx2 = ffill_series(extended_factor_2, dates)

        # --- 口径 A（变动）：正号 ---
        Nt2 = diff_lag(Fx2, n, lag, first)
        Ntz2 = expanding_z(Nt2, n, first, z_ddof)
        S7 = [None] * n
        for i in range(n):
            vals = [z[i] for z in (Nz, Oz, Pz, Qz, Rz, Ntz, Ntz2)
                    if z[i] is not None]
            if len(vals) == 7:
                S7[i] = sum(vals) / 7.0
        T7 = moving_average_skip_blanks(S7, n, ma_window)

        # --- 口径 B（水位）：正号 ---
        Fz2 = expanding_z(Fx2, n, first, z_ddof)
        Ltz2 = Fz2
        S7L = [None] * n
        for i in range(n):
            vals = [z[i] for z in (Nz, Oz, Pz, Qz, Rz, Ltz, Ltz2)
                    if z[i] is not None]
            if len(vals) == 7:
                S7L[i] = sum(vals) / 7.0
        T7L = moving_average_skip_blanks(S7L, n, ma_window)

        out.update({"F2": Fx2, "Nt2": Nt2, "Ntz2": Ntz2, "S7": S7, "T7": T7,
                    "Ltz2": Ltz2, "S7L": S7L, "T7L": T7L,
                    "extended_2": True, "extended_level_2": True})
        coverage["Nt2_rial_diff"] = _cov("Rial USD-sell 31d chg (pos)", Nt2)
        coverage["S7_composite"] = _cov("composite (7-factor mean)", S7)
        coverage["T7_taco"] = _cov("TACO-7 index", T7)
        coverage["Ltz2_rial_level"] = _cov("Rial USD-sell level z (pos)", Ltz2)
        coverage["S7L_composite"] = _cov("composite (7-factor level mean)", S7L)
        coverage["T7L_taco"] = _cov("TACO-7L index", T7L)

    return out


def result_has_6(result):
    """第 6 因子（变动+水位两套）是否都已就绪。

    单独成函数是为了让「第 7 因子并入的前置条件」只有一处定义 ——
    否则迟早会出现「算 T7 时以为 Ltz 在、其实不在」这类静默错配。
    """
    if not result:
        return False
    return bool(result.get("extended") and result.get("extended_level"))


def _first_valid(dates, arr):
    """返回某数组第一个非空值的日期与索引；全空返回 (None, None)。"""
    for i, v in enumerate(arr or []):
        if v is not None:
            return (dates[i] if i < len(dates) else None), i
    return None, None


def latest(result):
    """返回最新有效的 TACO 读数 dict。"""
    dates, T = result.get("dates") or [], result.get("T") or []
    for i in range(len(dates) - 1, -1, -1):
        if T[i] is not None:
            row = {"date": dates[i], "TACO": T[i], "index": i}
            for k, key in (("Nz", "z_10Y"), ("Oz", "z_swap"),
                           ("Pz", "z_approval"), ("Qz", "z_DJIA"),
                           ("Rz", "z_Brent")):
                arr = result.get(k) or []
                row[key] = arr[i] if i < len(arr) else None
            S = result.get("S") or []
            row["composite_S"] = S[i] if i < len(S) else None

            # 6 因子扩展（若启用）：单独找它自己的最新有效读数，
            # 因为多一个因子的暖机窗口更长，末值日期可能晚于 5 因子一步。
            if result.get("extended"):
                T6 = result.get("T6") or []
                for j in range(len(dates) - 1, -1, -1):
                    if j < len(T6) and T6[j] is not None:
                        row["TACO6"] = T6[j]
                        row["date6"] = dates[j]
                        row["index6"] = j
                        arr = result.get("Ntz") or []
                        row["z_transit"] = arr[j] if j < len(arr) else None
                        S6 = result.get("S6") or []
                        row["composite_S6"] = S6[j] if j < len(S6) else None
                        break

            # 水位口径（TACO-6L）：同样单独扫最新有效值
            if result.get("extended_level"):
                T6L = result.get("T6L") or []
                for j in range(len(dates) - 1, -1, -1):
                    if j < len(T6L) and T6L[j] is not None:
                        row["TACO6L"] = T6L[j]
                        row["date6L"] = dates[j]
                        row["index6L"] = j
                        arr = result.get("Ltz") or []
                        row["z_transit_level"] = arr[j] if j < len(arr) else None
                        S6L = result.get("S6L") or []
                        row["composite_S6L"] = (
                            S6L[j] if j < len(S6L) else None)
                        break

            # 第 7 因子（里亚尔）：同样单独扫自己的最新有效值。
            # 它的历史比前 6 个因子短得多（见 docstring），所以日期常常更早，
            # 必须把「各自的最新有效日」一并带出去，不能只报一个 date。
            for _tag, _tkey, _zkey, _dkey in (
                    ("7", "T7", "Ntz2", "z_rial"),
                    ("7L", "T7L", "Ltz2", "z_rial_level")):
                arr_t = result.get(_tkey) or []
                j = None
                for k in range(len(dates) - 1, -1, -1):
                    if k < len(arr_t) and arr_t[k] is not None:
                        j = k
                        break
                if j is None:
                    continue
                row["TACO" + _tag] = arr_t[j]
                row["date" + _tag] = dates[j]
                row["index" + _tag] = j
                arr_z = result.get(_zkey) or []
                row[_dkey] = arr_z[j] if j < len(arr_z) else None
                arr_s = result.get("S" + _tag) or []
                row["composite_S" + _tag] = arr_s[j] if j < len(arr_s) else None

            # 观察指标（observe 模式）：只带原始水位。
            #
            # 注意它**不走上面那套「各自扫最新有效日」的逻辑** —— 那套是因为
            # 扩展因子暖机更慢、末值日期可能比 TACO 晚一天。观察指标恰恰相反：
            # 它是原始输入（霍尔木兹 AIS 事后汇编，滞后 4~7 天），**末值一定比
            # TACO 更早**。所以这里取「不超过 TACO 读数日」的最后一个有效观测，
            # 与 base 序列对齐，避免拿一个「未来」的水位去解释「过去」的指数。
            if result.get("observe"):
                F = result.get("F") or []
                k = next((j for j in range(min(i, len(F) - 1), -1, -1)
                          if F[j] is not None), None)
                if k is not None:
                    row["transit"] = F[k]
                    row["date_transit"] = dates[k]
                    # 网格内滞后（对 ffill 后的值而言通常是 0）
                    row["transit_lag_days"] = i - k
                # 真实观测日 vs 网格末日：这个差才是「数据有多旧」。
                # 前值填充会把滞后藏起来，必须单独报。
                _s = result.get("observe_src_last")
                if _s and _s in dates:
                    js = dates.index(_s)
                    row["date_transit_raw"] = _s
                    row["transit_raw_lag_days"] = i - js
                    row["transit_ffilled_days"] = max(0, k - js) if k else 0
            return row
    return None


def series_for_output(result):
    """把结果摊平成 [{Date, ...}] 便于写 CSV。

    启用第 6 因子时额外附带 F（ffill 后的通行量原值）、Nt/Ntz/S6/T6（变动口径），
    以及 Ltz/S6L/T6L（水位口径）；启用第 7 因子时再附带
    F2/Nt2/Ntz2/S7/T7 与 Ltz2/S7L/T7L。
    观察模式（observe）只附带 F 一个派生量 —— 没有 z、没有 S、没有 T。
    """
    dates = result["dates"]
    keys = ["I", "J", "K", "L", "M", "Nz", "Oz", "Pz", "Qz", "Rz", "S", "T"]
    if result.get("observe"):
        keys += ["F"]
    if result.get("extended"):
        keys += ["F", "Nt", "Ntz", "S6", "T6"]
    if result.get("extended_level"):
        keys += ["Ltz", "S6L", "T6L"]
    if result.get("extended_2"):
        keys += ["F2", "Nt2", "Ntz2", "S7", "T7"]
    if result.get("extended_level_2"):
        keys += ["Ltz2", "S7L", "T7L"]
    rows = []
    for i, d in enumerate(dates):
        row = {"Date": d}
        for k in keys:
            row[k] = result[k][i]
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# 落盘列契约（CSV 与 Excel 共用同一份定义、同一份取数）
# ---------------------------------------------------------------------------
# 为什么要把这张表提到这里（2026-09-22 实测踩坑）：
#   CSV 与 Excel 原来各自写了一遍「列名 -> 数组」的映射。两处都把第 6 因子的
#   **原始水位列**取成了 result["cols"]（那是原始输入 C/D/E/G/H 所在的字典），
#   而 ffill 后的通行量在 result["F"] 里 —— 于是 TRHBCCCD_n_total 整列写空。
#   更阴的是：z_transit / TACO-6 全都正常，报告读起来毫无破绽，
#   只有低头去数 CSV 的列才发现是空的。两份产物同源出错的概率远高于只错一份，
#   所以列定义与取数只留一处，禁止各写一遍。
#
# 取数纪律：**原始输入**（C/D/E/G/H）在 result["cols"]；**所有派生量**
# （含第 6 因子 ffill 后的 F）在 result 顶层。这里按 key 是否存在自动选源。
_COL_SOURCE = [
    ("Date",                None),
    ("USGG10YR",            "C"),
    ("USSWIT1_proxy_T5YIE", "D"),
    ("RCPPTAPP_approve",    "E"),
    ("INDU",                "G"),
    ("CO1_Brent",           "H"),
    ("z_10Y",               "Nz"),
    ("z_swap",              "Oz"),
    ("z_approval",          "Pz"),
    ("z_DJIA",              "Qz"),
    ("z_Brent",             "Rz"),
    ("composite_S",         "S"),
    ("TACO_Index_T",        "T"),
    # ---- 以下为本项目第 6 因子扩展（东吴底稿的 TACO 不含）----
    # ---- 霍尔木兹通行量（底稿 F 列）----
    #   观察模式：本行是**最后一列**（列数 14），且它是唯一一个从 F 取的列。
    #   composite 模式：本行是第 6 因子的原料列，后面还跟着 6 个派生列。
    ("TRHBCCCD_n_total",    "F"),
    ("z_transit",           "Ntz"),
    ("composite_S6",        "S6"),
    ("TACO6_Index_T6",      "T6"),
    ("z_transit_level",     "Ltz"),
    ("composite_S6L",       "S6L"),
    ("TACO6L_Index_T6L",    "T6L"),
    # ---- 第 7 因子扩展：Bonbast USD 卖出价 / 里亚尔（含两套口径）----
    ("BONBAST_USD_sell_rial", "F2"),
    ("z_rial",              "Ntz2"),
    ("composite_S7",        "S7"),
    ("TACO7_Index_T7",      "T7"),
    ("z_rial_level",        "Ltz2"),
    ("composite_S7L",       "S7L"),
    ("TACO7L_Index_T7L",    "T7L"),
]
_COL_KEY = dict(_COL_SOURCE)

# 列数契约：不是「随手截断」的魔法数字，而是四种口径各自的完整定义。
# 每加一种口径，自检里就有对应的断言（列数 + 逐列非空个数），改错了会红。
N_COLS_5F = 13            # 东吴原始口径：5 输入 + 5 个 z + S + T
N_COLS_5F_OBS = 14        # + TRHBCCCD_n_total（观察指标，不进指数）
N_COLS_6F = 17            # + Nt/Ntz/S6/T6（第 6 因子，31 日变动口径）
N_COLS_6F_LEVEL = 20      # + Ltz/S6L/T6L（第 6 因子，水位口径）
N_COLS_7F = 24            # + F2/Nt2/Ntz2/S7/T7（第 7 因子，31 日变动口径）
N_COLS_7F_LEVEL = 27      # + Ltz2/S7L/T7L（第 7 因子，水位口径）


def col_source(name):
    """列名 -> 该列在 result 里的取数键（None 表示日期列）。"""
    return _COL_KEY[name]


def output_columns(result):
    """实际要落盘的列名（按启用到第几层裁剪尾部）。

    判定顺序即「信息量从多到少」：7 因子 > 6 因子水位 > 6 因子变动 >
    **观察（5 因子 + 1 列原始水位）** > 纯 5 因子。

    观察分支放在 extended 之前判断不到是不行的 —— 它们互斥（compute 会抛），
    所以顺序只影响可读性；但**必须在 else 之前**，否则 observe 会掉进 13 列，
    霍尔木兹那一列就静默消失了（列数对不上会在自检里红）。
    """
    if result.get("extended_level_2"):
        n = N_COLS_7F_LEVEL
    elif result.get("extended_level"):
        n = N_COLS_6F_LEVEL
    elif result.get("extended"):
        n = N_COLS_6F
    elif result.get("observe"):
        n = N_COLS_5F_OBS
    else:
        n = N_COLS_5F
    return [c for c, _ in _COL_SOURCE[:n]]


def output_row(result, i, columns=None):
    """第 i 行的落盘字典。CSV 与 Excel **必须**都走这里。

    越界或缺失一律给 None（不是省略键），保证每行键集与 output_columns 一致 ——
    否则写 CSV 时不同行长度不同，整张表错位。
    """
    columns = columns or output_columns(result)
    cols = result.get("cols") or {}
    dates = result.get("dates") or []
    row = {}
    for name in columns:
        key = _COL_KEY[name]
        if key is None:
            row[name] = dates[i] if i < len(dates) else None
            continue
        arr = (cols.get(key) if key in cols else result.get(key)) or []
        row[name] = arr[i] if i < len(arr) else None
    return row


def expected_counts(result, columns=None):
    """逐列「应该有多少个非空」——**独立于 output_row** 推导。

    为什么校验不能复用 output_row 来算期望值：写盘与校验如果走同一个函数，
    那么当这个函数的取数映射本身错了（比如把第 6 因子取成了原始输入字典），
    **写出去的是空、算出来的期望也是空**，两边一起错、一起「对上」，
    校验就退化成自证。2026-09-22 的负向测试正是这样骗过了第一版守卫。

    所以期望值只依据两样东西推导：**列契约表**（列名 -> 源数组，声明的是「意图」）
    与**源数组本身**。契约表是唯一声明「这一列应该来自哪里」的地方，
    它错了属于契约写错（自检里有专门的语义断言）；而它与取数的实现是否一致，
    才是这里要盯的。
    """
    columns = columns or output_columns(result)
    cols = result.get("cols") or {}
    dates = result.get("dates") or []
    out = {}
    for name in columns:
        key = _COL_KEY[name]
        if key is None:
            out[name] = len(dates)
            continue
        arr = (cols.get(key) if key in cols else result.get(key)) or []
        out[name] = sum(1 for v in arr if v is not None)
    return out


def _desc(arr):
    """单序列描述统计（内部复用）。"""
    v = [x for x in arr if x is not None]
    if not v:
        return {}
    vs = sorted(v)
    m = sum(v) / len(v)
    var = sum((x - m) ** 2 for x in v) / (len(v) - 1) if len(v) > 1 else 0.0

    def pct(p):
        idx = min(len(vs) - 1, max(0, int(round(p * (len(vs) - 1)))))
        return vs[idx]

    return {
        "n": len(v),
        "mean": m,
        "stdev": var ** 0.5,
        "min": vs[0],
        "max": vs[-1],
        "p10": pct(0.10),
        "p50": pct(0.50),
        "p90": pct(0.90),
        "latest": v[-1],
        "latest_percentile": (sum(1 for x in v if x <= v[-1]) / len(v)),
    }


def stats(result):
    """TACO 序列的描述统计（供报告与对账）。

    5 因子 TACO 的键保持在顶层 —— 既有报告 / Excel / 对账都按这些键取数，
    不能被第 6 因子污染。6 因子两套口径分别放在 "taco6"（变动）与
    "taco6L"（水位）子字典里。
    observe 模式下额外放一个 "hormuz_observe" 子字典：它是**观察指标的描述
    统计**（水位本身的 min/max/p50 等），刻意不叫 tacoX —— 它没有 z、不是
    指数读数，叫 taco 会让人以为它也参与了合成。
    """
    out = _desc(result.get("T") or [])
    if out and result.get("observe"):
        o = _desc(result.get("F") or [])
        if o:
            # 与第 7 因子同样的理由：观察序列的覆盖区间是它自己的，
            # 必须带上起止日，否则读者会以为它和 TACO 一样长。
            o["first_date"], _ = _first_valid(result.get("dates") or [],
                                              result.get("F") or [])
            o["last_date"] = _last_valid_date(result.get("dates") or [],
                                              result.get("F") or [])
            o["unit"] = "vessels/day"
            o["in_index"] = False
            # 原始源（未 ffill）的覆盖与末次观测日 —— 与 ffill 后的 o["n"] /
            # o["last_date"] 是两件事，报告里必须分开写。
            o["raw_n"] = result.get("observe_src_n")
            o["raw_last_date"] = result.get("observe_src_last")
            out["hormuz_observe"] = o
    if out and result.get("extended"):
        s6 = _desc(result.get("T6") or [])
        if s6:
            out["taco6"] = s6
    if out and result.get("extended_level"):
        s6l = _desc(result.get("T6L") or [])
        if s6l:
            out["taco6L"] = s6l
    if out and result.get("extended_2"):
        s7 = _desc(result.get("T7") or [])
        if s7:
            # 有效观测数远小于顶层，故把「实际覆盖区间」一并带上：
            # 报告若只印一个 n，读者会以为它和 5 因子一样长。
            s7["first_date"], _ = _first_valid(result.get("dates") or [],
                                               result.get("T7") or [])
            s7["last_date"] = _last_valid_date(result.get("dates") or [],
                                               result.get("T7") or [])
            out["taco7"] = s7
    if out and result.get("extended_level_2"):
        s7l = _desc(result.get("T7L") or [])
        if s7l:
            s7l["first_date"], _ = _first_valid(result.get("dates") or [],
                                                result.get("T7L") or [])
            s7l["last_date"] = _last_valid_date(result.get("dates") or [],
                                                result.get("T7L") or [])
            out["taco7L"] = s7l
    return out


def _last_valid_date(dates, arr):
    for i in range(len(arr or []) - 1, -1, -1):
        if arr[i] is not None:
            return dates[i] if i < len(dates) else None
    return None
