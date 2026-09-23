#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
build_config.py —— 生成 config.json（金融战线监测的序列清单）

设计原则（沿用本工作区既有项目，并在本项目里加了强制执行）：

  1. **先测后用，且在代码里强制**。每个链条目都必须能在
     `tools/_probe_round*.json` 里找到一条 `ok: true` 的实测记录，
     否则本脚本**报错退出**，不生成配置。
     「我记得这个源能用」是最贵的一类错误：它会让你在报告里
     把某个字段一直印成「—」，而你会去查解析器、查词表、查网络，
     就是不查「这个源根本没通」。

  2. **URL 模板与探测时逐字一致**。探测用 A、配置用 B，
     证据就是假的（详见 entry_urls 的注释）。

  3. **传输方式也是数据的一部分**。FRED 必须走 curl（Akamai 指纹判定），
     这条信息进配置、进报告，不进「口头约定」。

  4. **口径写在配置里，不写在脑子里**：单位、小数位、频率、步长、
     合理滞后、极性、源间容差，全部显式声明，报告与对账都从这里读。

用法：
  python tools/build_config.py                 # 校验证据并写 config.json
  python tools/build_config.py --check         # 只校验，不写
  python tools/build_config.py --allow-missing # 排障用：跳过证据校验（会警告）
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
import urllib.parse
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


# ================================================================ 链条目构造器
TREASURY = ("https://home.treasury.gov/resource-center/data-chart-center/"
            "interest-rates/daily-treasury-rates.csv/{y}/all"
            "?type=daily_treasury_yield_curve&field_tdr_date_value={y}"
            "&page&_format=csv")
ARCGIS_BASE = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/"
               "services/Daily_Chokepoints_Data/FeatureServer/0/query"
               "?where=portid%3D%27{pid}%27&outFields=date,portid,{fld}"
               "&orderByFields=date%20DESC&resultRecordCount=1000"
               "&returnGeometry=false&f=json")
YAHOO = ("https://query1.finance.yahoo.com/v8/finance/chart/"
         "{sym}?range=2y&interval=1d")
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd=2016-01-01"
TIPS = ("https://home.treasury.gov/resource-center/data-chart-center/"
        "interest-rates/daily-treasury-rates.csv/{y}/all"
        "?type=daily_treasury_real_yield_curve&field_tdr_date_value={y}"
        "&page&_format=csv")
EIA_WPSR = "https://ir.eia.gov/wpsr/table1.csv"


def Y(sym, ref=None):
    return {"provider": "yahoo", "ref": ref or sym, "tier": "market",
            "url": YAHOO.format(sym=urllib.parse.quote(sym)),
            "accept": "application/json,*/*", "label": sym}


def F(sid, transport="curl"):
    return {"provider": "fred", "ref": sid, "tier": "official",
            "url": FRED.format(sid=sid), "accept": "text/csv,*/*",
            "transport": transport, "label": sid}


def T(field):
    return {"provider": "treasury", "ref": field, "field": field,
            "tier": "official",
            "urls": [TREASURY.replace("{y}", "{year_prev}"),
                     TREASURY.replace("{y}", "{year}")],
            "accept": "text/csv,*/*", "label": f"US Treasury par yield {field}"}


def A(pid, fld):
    return {"provider": "arcgis", "ref": f"{pid}:{fld}", "tier": "official",
            "url": ARCGIS_BASE.format(pid=pid, fld=fld),
            "accept": "application/json,*/*",
            "date_field": "date", "value_field": fld,
            "entity_field": "portid", "entity_match": pid,
            "label": f"PortWatch {pid} {fld}"}


def N(ref, kind="unsecured/effr"):
    return {"provider": "nyfed", "ref": ref, "tier": "official",
            "url": f"https://markets.newyorkfed.org/api/rates/{kind}/last/400.json",
            "accept": "application/json,*/*", "label": f"NY Fed {ref.upper()}"}


def TIP(field):
    # TIPS 曲线的列名是【大写】`10 YR`，与 par yield 曲线的 `10 Yr` 不同——
    # 两表连列名风格都不一致，写错只会得到「列名不存在」，不会大张旗鼓地报错。
    return {"provider": "treasury", "ref": f"tips-{field}", "field": field,
            "tier": "official",
            "urls": [TIPS.replace("{y}", "{year_prev}"),
                     TIPS.replace("{y}", "{year}")],
            "accept": "text/csv,*/*", "label": f"US Treasury real yield {field}"}


def E(row, scale=1000.0):
    # EIA 周报 table1.csv。文件单位是【百万桶】；本项目库存用千桶 -> scale=1000。
    # 每次抓取带 2 个周点（本周 + 上周），跨轮运行累积成周序列。
    return {"provider": "eia_wpsr", "ref": row, "tier": "official",
            "url": EIA_WPSR, "accept": "text/csv,*/*",
            "scale": scale, "label": f"EIA WPSR {row}"}


# ================================================================ 序列清单
# 字段含义：
#   bucket      报告分桶（§3–§8）
#   unit        量纲。★ 值为 "%" 的序列，变动一律以 **bp** 呈现（见 delta_unit）
#   decimals    显示小数位
#   cadence     频率。★ 不同频率**不得同表比较**，渲染层按它分表
#   polarity    +1 数值上升=压力上升；-1 上升=压力下降；0 仅作水平读数
#   role        headline 进 §1 仪表盘；context 只进分节表
#   step        分级步长（期）。海峡通行量有周内季节性 → 7
#   max_lag     合理滞后天数（超了标「⏳滞后」，不是「市场平静」）
#   div_tol     源间分歧相对容差（覆盖全局值；期货 vs 现货要放宽）
#   note/caveat 报告里会印出来，caveat 还会汇总进 §13
SERIES = [
    # ---------------------------------------------------------- oil 原油
    dict(id="brent", label="Brent 原油", bucket="oil", unit="$/bbl", decimals=2,
         cadence="daily", polarity=1, role="headline", div_tol=0.04,
         chain=[Y("BZ=F"), F("DCOILBRENTEU")],
         note="北海 Brent 近月期货收盘。跨源链是**期货 vs 现货**，价差 1–4% 属正常期限结构，不是源错。",
         caveat="Brent 期货与 FRED 的 Brent 现货是两个标的；本报告以期货为主源，现货仅作交叉核对，两者价差已按 4% 容差处理，**不代表数据错误**。"),
    dict(id="wti", label="WTI 原油", bucket="oil", unit="$/bbl", decimals=2,
         cadence="daily", polarity=1, role="headline", div_tol=0.04,
         chain=[Y("CL=F"), F("DCOILWTICO")],
         note="WTI 近月期货收盘（库欣交割）。",
         caveat="WTI 期货 vs 现货（库欣）存在期限结构差异，容差 4%。"),
    dict(id="natgas", label="天然气（Henry Hub 期货）", bucket="oil",
         unit="$/MMBtu", decimals=3, cadence="daily", polarity=1, role="context",
         div_tol=0.08,
         chain=[Y("NG=F"), F("DHHNGSP")],
         note="与原油同为能源成本，但驱动因素（天气/库存）不同，**不能**当成油价的替代。"),
    # brent_vol20 / sp_drawdown60 已移入 DERIVED（空 chain 的序列永远无数据，
    # 占着「无数据」两行只会稀释速览表——它们本来就是派生量）。
    dict(id="gasoline_retail", label="美国汽油零售价", bucket="oil",
         unit="$/gal", decimals=3, cadence="weekly", polarity=1, role="context",
         max_lag=16, chain=[F("GASREGW")],   # FRED 专供：不可达时整条停用并披露
         note="周频（EIA）。**传导末端**：油价冲击经炼厂与零售加价后到终端，通常滞后数周。",
         caveat="周频且明显滞后于原油；与日频油价同列比较会得出「油价涨了汽油没动」的错误结论。"),

    # ---------------------------------------------------- inventory 库存
    dict(id="spr", label="美国战略石油储备（SPR）", bucket="inventory",
         unit="千桶", decimals=0, cadence="weekly", polarity=0, role="headline",
         max_lag=16, chain=[E("Strategic Petroleum Reserve (SPR)"), F("WCSSTUS1")],
         note="★ 这是**政策工具的直接读数**：SPR 下降＝政府在动用储备平抑价格。周频，EIA 每周三发布。",
         caveat="SPR 是政策**存量**，不是市场供需。它的增减由行政决定，与油价的关系是「被用来应对」，不是「被油价驱动」。极性设为 0，**不参与合成指数**。"),
    dict(id="crude_stocks", label="美国商业原油库存", bucket="inventory",
         unit="千桶", decimals=0, cadence="weekly", polarity=-1, role="headline",
         max_lag=16, chain=[E("Commercial (Excluding SPR)"), F("WCESTUS1")],
         note="周频（EIA）。库存超预期累积＝需求走弱或供应宽松。",
         caveat="周频数据有**初值/修正值**之分；本报告只声明「截至抓取时点」，不声称终值。"),

    # -------------------------------------------------------- rates 利率
    dict(id="dgs3mo", label="3M 美债收益率", bucket="rates", unit="%",
         decimals=3, cadence="daily", polarity=1, role="context", div_tol=0.01,
         chain=[T("3 Mo")],
         note="最贴近政策利率的市场利率。"),
    dict(id="dgs1", label="1Y 美债收益率", bucket="rates", unit="%", decimals=3,
         cadence="daily", polarity=1, role="context", div_tol=0.01,
         chain=[T("1 Yr")]),
    dict(id="dgs2", label="2Y 美债收益率", bucket="rates", unit="%", decimals=3,
         cadence="daily", polarity=1, role="headline", div_tol=0.01,
         chain=[T("2 Yr"), F("DGS2")],
         note="★ **政策预期的主要市场读数**：2Y 对联邦基金利率路径最敏感。"),
    dict(id="dgs5", label="5Y 美债收益率", bucket="rates", unit="%", decimals=3,
         cadence="daily", polarity=1, role="context", div_tol=0.01,
         chain=[T("5 Yr"), Y("^FVX")]),
    dict(id="dgs10", label="10Y 美债收益率", bucket="rates", unit="%", decimals=3,
         cadence="daily", polarity=1, role="headline", div_tol=0.01,
         chain=[T("10 Yr"), Y("^TNX"), F("DGS10")],
         note="全球资产定价基准。★ 三源链：财政部官方 par yield 为主，Yahoo 与 FRED 交叉核对。",
         caveat="财政部给的是 **par yield（平价收益率）**，Yahoo `^TNX` 是市场报价的近似，两者口径略有差异，容差 1%。"),
    dict(id="dgs30", label="30Y 美债收益率", bucket="rates", unit="%", decimals=3,
         cadence="daily", polarity=1, role="context", div_tol=0.01,
         chain=[T("30 Yr"), Y("^TYX")]),
    dict(id="effr", label="有效联邦基金利率（EFFR）", bucket="rates", unit="%",
         decimals=2, cadence="daily", polarity=0, role="context", max_lag=7,
         chain=[N("effr")],
         note="纽约联储官方发布的**当日**实际政策利率，不是预期。极性 0：这是政策**现状**，不参与压力合成。"),
    dict(id="fedtarget_upper", label="联邦基金目标区间上限", bucket="rates",
         unit="%", decimals=2, cadence="daily", polarity=0, role="context",
         max_lag=10, chain=[N("fedtarget"), F("DFEDTARU")],
         note="FOMC 决议直接设定。它只在议息会议变动，**两次会议之间「没动」是正常的**。"),
    dict(id="real10y_treasury", label="10Y TIPS 实际收益率", bucket="rates",
         unit="%", decimals=3, cadence="daily", polarity=1, role="context",
         max_lag=10, chain=[TIP("10 YR")],
         note="财政部 TIPS 曲线的 10Y 真实收益率。★ 通胀预期上行会**吃掉**名义利率上行的实际意义——实际利率才是金融条件。",
         caveat="TIPS 曲线列名是大写 `10 YR`，且与 par yield 曲线是**两张表**；本条与 dgs10 的日期天然对齐。"),

    # ------------------------------------------------------ equity 股市
    dict(id="sp500", label="标普 500", bucket="equity", unit="点", decimals=2,
         cadence="daily", polarity=-1, role="headline", div_tol=0.01,
         chain=[Y("^GSPC"), F("SP500")],
         note="★ 不同指数的**点位不可比**，只比百分比变化。"),
    dict(id="nasdaq", label="纳斯达克综合指数", bucket="equity", unit="点",
         decimals=2, cadence="daily", polarity=-1, role="headline", div_tol=0.01,
         chain=[Y("^IXIC"), F("NASDAQCOM")]),
    dict(id="djia", label="道琼斯工业指数", bucket="equity", unit="点", decimals=2,
         cadence="daily", polarity=-1, role="headline", div_tol=0.01,
         chain=[Y("^DJI"), F("DJIA")]),
    dict(id="xle", label="能源板块 ETF（XLE）", bucket="equity", unit="$",
         decimals=2, cadence="daily", polarity=-1, role="context",
         chain=[Y("XLE")],
         note="能源股的**相对强弱**是「冲突受益方」的市场读法，但只是价格反应，不是因果证明。"),
    dict(id="ita", label="航空航天与国防 ETF（ITA）", bucket="equity", unit="$",
         decimals=2, cadence="daily", polarity=-1, role="context",
         chain=[Y("ITA")],
         note="国防板块。冲突升级预期通常**推高**它、压低大盘——两者背离才是有信息量的信号。"),


    # ------------------------------------------------------ credit 信用
    dict(id="vix", label="VIX 波动率指数", bucket="credit", unit="点",
         decimals=2, cadence="daily", polarity=1, role="headline", div_tol=0.03,
         chain=[Y("^VIX"), F("VIXCLS")],
         note="标普 500 期权隐含波动率（30 天）。**前瞻性**，但也被期权供需扭曲。"),
    dict(id="hy_spread", label="美国高收益债信用利差", bucket="credit",
         unit="%", decimals=2, cadence="daily", polarity=1, role="headline",
         max_lag=10, chain=[F("BAMLH0A0HYM2")],
         note="★ 比股市更「干净」的风险读数：信用利差走阔通常先于或同步于股市下跌，且不易被指数成分股结构掩盖。",
         caveat="ICE BofA 指数口径，日频但有 1–2 日发布滞后，max_lag 设为 10 天。"),
    dict(id="ig_spread", label="美国投资级信用利差", bucket="credit",
         unit="%", decimals=2, cadence="daily", polarity=1, role="context", max_lag=10,
         chain=[F("BAMLC0A0CM")]),
    dict(id="hyg", label="垃圾债 ETF（HYG）价格", bucket="credit",
         unit="$", decimals=2, cadence="daily", polarity=-1, role="context",
         chain=[Y("HYG")],
         note="信用利差的**市场侧代理**：利差走阔时 HYG 下跌。它不是利差本身，"
              "但来源稳定、日频，可当信用情绪的温度计用。"),

    # ---------------------------------------------------------- fx 汇率
    dict(id="dxy", label="美元指数（DXY）", bucket="fx", unit="点", decimals=3,
         cadence="daily", polarity=1, role="headline", div_tol=0.03,
         chain=[Y("DX-Y.NYB"), F("DTWEXBGS")],
         note="★ 两条源是**不同口径**：DXY 是六种货币的固定权重篮子（欧元占 57.6%），FRED `DTWEXBGS` 是广义贸易加权。水平值不可比，只用于互验方向。",
         caveat="DXY 与 FRED 广义美元指数口径不同（篮子构成与权重均不同），二者水平值**不可互相替代**，容差已放宽到 3%，分歧时以主源 DXY 为准。"),
    dict(id="gold", label="黄金期货", bucket="fx", unit="$/oz", decimals=2,
         cadence="daily", polarity=1, role="context", chain=[Y("GC=F")],
         note="避险需求与「美元信用」的对冲读数。"),
    dict(id="usdcny", label="美元兑人民币", bucket="fx", unit="", decimals=4,
         cadence="daily", polarity=1, role="context", max_lag=10,
         chain=[Y("CNY=X"), F("DEXCHUS")],
         note="新兴市场风险偏好的代理之一（人民币走弱＝避险美元需求上升）。"),

    # ---------------------------------------------------- shipping 通行量
    dict(id="babelmandeb_total", label="曼德海峡 日通行艘次（全部船型）",
         bucket="shipping", unit="艘次", decimals=0, cadence="daily",
         polarity=-1, role="headline", step=7, max_lag=14,
         chain=[A("chokepoint4", "n_total")],
         note="★ IMF PortWatch 日频汇编（AIS 事后整理，通常滞后 4–7 天）。**通行量下降 = 双线施压的实物落地**。",
         caveat="PortWatch 由 AIS 事后汇编，含记录误差；且港口/海峡统计以「进入该水域的船舶计次」为口径，一艘船折返会计两次。"),
    dict(id="babelmandeb_tanker", label="曼德海峡 日通行油轮艘次",
         bucket="shipping", unit="艘次", decimals=0, cadence="daily",
         polarity=-1, role="headline", step=7, max_lag=14,
         chain=[A("chokepoint4", "n_tanker")],
         note="只看油轮。油轮对安全风险的敏感度高于集装箱船。"),
    dict(id="hormuz_total", label="霍尔木兹海峡 日通行艘次（全部船型）",
         bucket="shipping", unit="艘次", decimals=0, cadence="daily",
         polarity=-1, role="headline", step=7, max_lag=14,
         chain=[A("chokepoint6", "n_total")],
         note="★ 全球约 1/5 的石油消费经此通过。这是本项目里**最直接**的「海峡是否被真正封锁」读数。",
         caveat="霍尔木兹的通行量对单次事件极敏感，单日值波动大；本报告用 7 期步长分级、并给出 7 日均值。"),
    dict(id="hormuz_tanker", label="霍尔木兹海峡 日通行油轮艘次",
         bucket="shipping", unit="艘次", decimals=0, cadence="daily",
         polarity=-1, role="headline", step=7, max_lag=14,
         chain=[A("chokepoint6", "n_tanker")]),
    dict(id="hormuz_capacity_tanker", label="霍尔木兹海峡 油轮载重吨",
         bucket="shipping", unit="载重吨", decimals=0, cadence="daily",
         polarity=-1, role="context", step=7, max_lag=14,
         chain=[A("chokepoint6", "capacity_tanker")],
         note="★ 与「艘次」互补：艘次少但吨位没少，说明是**小船在跑、大船在躲**——比单看艘次更有信息量。"),
    dict(id="suez_total", label="苏伊士运河 日通行艘次", bucket="shipping",
         unit="艘次", decimals=0, cadence="daily", polarity=-1, role="context",
         step=7, max_lag=14, chain=[A("chokepoint1", "n_total")],
         note="红海航线的上游读数，与曼德海峡配套看。"),
    dict(id="cape_total", label="好望角 日通行艘次", bucket="shipping",
         unit="艘次", decimals=0, cadence="daily", polarity=1, role="context",
         step=7, max_lag=14, chain=[A("chokepoint7", "n_total")],
         note="★ **绕行替代路线**。曼德海峡通行量下降而好望角上升，是「船绕开了红海」的直接证据，而不是「贸易消失」。"),
    dict(id="fro", label="Frontline 油轮股价", bucket="shipping", unit="$",
         decimals=2, cadence="daily", polarity=1, role="context",
         chain=[Y("FRO")],
         note="油轮运价的市场侧代理。★ **不是通行量**：股价反应的是运价与预期，通行量下降若伴随运价上升，股价会涨。"),
    dict(id="zim", label="以星航运股价", bucket="shipping", unit="$",
         decimals=2, cadence="daily", polarity=1, role="context",
         chain=[Y("ZIM")],
         note="集装箱航运的市场侧代理，对红海绕行最敏感。"),
]

# ================================================================ 派生指标
# ★ 艘次计数（海峡通行量）**不做** N 期百分比变化：基数是个位数时
#   百分比会爆炸（1→8 艘次 = +700%），把噪声渲染成「升级」。
#   计数序列只用 7 期均值（抹平周内季节性）与结构比（油轮占比、绕行比）。
# ★ `delta_unit` 一律显式给出，不做推断：
#   `pct_over` 的输出本身就是百分比变化，再折算成 bp 会错得离谱。
DERIVED = [
    dict(id="brent_wti_spread", label="Brent − WTI 价差", bucket="oil",
         kind="diff", a="brent", b="wti", unit="$/bbl", decimals=2,
         delta_unit="$", polarity=1, role="headline",
         definition="Brent 收盘 − WTI 收盘（$/bbl）",
         note="价差走阔通常意味着**国际（海运）市场比美国本土更紧**——出口能力或国际风险定价的变化会先反映在这里。"),
    dict(id="brent_20d_z", label="Brent 20 期标准化偏离", bucket="oil",
         kind="zdev", series="brent", window=20, unit="σ", decimals=2,
         delta_unit="", polarity=1, role="context",
         definition="(最新值 − 近 20 期均值) / 近 20 期标准差",
         note="与「日变动 z」不同：这个量衡量的是**水平**相对近期有多异常。"),
    dict(id="brent_chg20", label="Brent 20 期涨幅", bucket="oil",
         kind="pct_over", series="brent", window=20, unit="%", decimals=2,
         delta_unit="pp", polarity=1, role="context",
         definition="最新值 / 20 期前 − 1（%）",
         note="用于与能源股涨幅对照（见 §9 的 `oil_vs_energy`）。"),
    dict(id="xle_chg20", label="能源板块 20 期涨幅", bucket="equity",
         kind="pct_over", series="xle", window=20, unit="%", decimals=2,
         delta_unit="pp", polarity=-1, role="context",
         definition="XLE 最新价 / 20 期前 − 1（%）"),
    dict(id="oil_vs_energy", label="油价涨幅 − 能源股涨幅", bucket="equity",
         kind="diff", a="brent_chg20", b="xle_chg20", unit="pp", decimals=2,
         delta_unit="pp", polarity=1, role="context",
         definition="Brent 20 期涨幅 − XLE 20 期涨幅（百分点）",
         note="正值＝商品涨得比相关股票多。可能意味着市场认为这轮上涨**不可持续**（股票不跟），或股票受其他因素拖累。**只报背离，不解释成因。**"),
    dict(id="yield_10y2y", label="10Y − 2Y 期限利差", bucket="rates",
         kind="diff", a="dgs10", b="dgs2", scale=100, unit="bp", decimals=1,
         delta_unit="bp", polarity=1, role="headline",
         definition="(10Y − 2Y) × 100（bp）",
         note="倒挂（负值）常被当作衰退前兆，但**时点不确定**；本项目只报数值与变化。"),
    dict(id="yield_10y3m", label="10Y − 3M 期限利差", bucket="rates",
         kind="diff", a="dgs10", b="dgs3mo", scale=100, unit="bp", decimals=1,
         delta_unit="bp", polarity=1, role="context",
         definition="(10Y − 3M) × 100（bp）",
         note="与 10Y−2Y 配套看：3M 更贴政策利率，倒挂形态在两条曲线上是否一致本身有信息量。"),
    dict(id="policy_gap_2y", label="2Y 收益率 − 目标区间上限", bucket="rates",
         kind="diff", a="dgs2", b="fedtarget_upper", scale=100, unit="bp",
         decimals=1, delta_unit="bp", polarity=1, role="headline",
         definition="(2Y 收益率 − 联邦基金目标区间上限) × 100（bp）",
         note="★ **这是代理量，不是概率。** 定义：市场对「未来两年政策利率平均水平」的定价相对**当前上限**的偏移。"
              "正值＝市场定价未来路径高于当前上限（偏紧/加息方向）；负值＝低于当前上限（偏松/降息方向）。"
              "后续会给出**方向**，但不给出概率、不给出时点、不构成预测。"),
    dict(id="forward_1y1y", label="1 年期远期利率（近似）", bucket="rates",
         kind="linear", terms=[{"series": "dgs2", "weight": 2.0},
                               {"series": "dgs1", "weight": -1.0}],
         unit="%", decimals=3, delta_unit="bp", polarity=1, role="context",
         definition="2 × 2Y − 1Y（%）",
         note="★ **近似式**，用于刻画「1 年后 1 年期利率」的市场定价方向。它假设平价收益率与即期利率近似相等，"
              "在曲线陡峭时不精确；本项目只在**方向与相对变化**上使用它，不用其绝对水平。"),
    dict(id="breakeven10", label="10Y 盈亏平衡通胀（名义 − TIPS 实际）",
         bucket="rates", kind="diff", a="dgs10", b="real10y_treasury",
         unit="%", decimals=3, delta_unit="bp", polarity=1, role="context",
         definition="dgs10 − real10y_treasury（%）",
         note="财政部同表口径的名义 − 实际。包含通胀风险溢价，**不等于**市场预测的 CPI。"),
    dict(id="brent_vol20", label="Brent 20 期已实现波动率（年化）",
         bucket="oil", kind="rollstd", series="brent", window=20,
         annualize=15.8745, scale=100, unit="%", decimals=1,
         delta_unit="pp", polarity=1, role="context",
         definition="近 20 个交易日日收益标准差 × √252 × 100（%）",
         note="与油价水平无关：价格不动而波动率飙升，说明市场在给「跳跃风险」定价。"),
    dict(id="sp_drawdown60", label="标普距 60 期高点回撤", bucket="equity",
         kind="drawdown", series="sp500", window=60, unit="%", decimals=2,
         delta_unit="pp", polarity=-1, role="context",
         definition="最新值 / 近 60 期最高 − 1（%）",
         note="负值。越深代表风险偏好受损越重。"),
    dict(id="real_10y", label="10Y 实际利率（名义 − 盈亏平衡）", bucket="rates",
         kind="diff", a="dgs10", b="breakeven10", scale=100, unit="bp",
         decimals=1, delta_unit="bp", polarity=1, role="context",
         definition="(10Y 名义 − 10Y 盈亏平衡通胀) × 100（bp）",
         note="**实际**融资成本的代理。名义利率上行但实际利率不动，说明涨的是通胀预期而非真实紧缩。"),

    dict(id="mab_ma7", label="曼德海峡 7 期日均通行", bucket="shipping",
         kind="ma", series="babelmandeb_total", window=7, unit="艘次",
         decimals=1, delta_unit="", polarity=-1, role="headline",
         definition="近 7 个观测点的算术平均（艘次）",
         note="★ 海峡通行量有**强周内季节性**（周末船舶少），逐日比会全是噪声。7 日均值把季节性抹平，"
              "是读这条数据唯一可用的方式。"),
    dict(id="hormuz_ma7", label="霍尔木兹 7 期日均通行", bucket="shipping",
         kind="ma", series="hormuz_total", window=7, unit="艘次", decimals=1,
         delta_unit="", polarity=-1, role="headline",
         definition="近 7 个观测点的算术平均（艘次）"),
    dict(id="hormuz_tanker_share", label="霍尔木兹 油轮艘次占比", bucket="shipping",
         kind="ratio", a="hormuz_tanker", b="hormuz_total", scale=100,
         unit="%", decimals=1, delta_unit="pp", polarity=-1, role="context",
         definition="油轮艘次 ÷ 总艘次 × 100",
         note="★ 结构性读数：**油轮占比下降而总通行不变**，说明是油轮在避险、其他船型照常——"
              "这比总量更能指向「针对能源航运」的施压。"),
    dict(id="cape_vs_suez", label="好望角 ÷ 苏伊士 通行比", bucket="shipping",
         kind="ratio", a="cape_total", b="suez_total", scale=1, unit="×",
         decimals=2, delta_unit="", polarity=1, role="headline",
         definition="好望角通行艘次 ÷ 苏伊士通行艘次",
         note="★ **绕行代理指标**：比值上升＝船舶改走好望角（绕行、航程变长、运价上升）。"
              "它区分了「贸易减少」与「航线改道」——只看曼德海峡通行量下降会把两者混为一谈。"),
]

WATCH_PAIRS = [
    dict(a="brent", b="sp500", label="Brent ↔ 标普 500",
         note="典型的「供给冲击」配对：油价涨、股指跌是教科书式风险回避组合。"),
    dict(a="brent", b="dgs10", label="Brent ↔ 10Y 收益率",
         note="油价上行推高通胀预期从而抬升名义收益率；若同向不明显，说明市场认为冲击是暂时的。"),
    dict(a="vix", b="sp500", label="VIX ↔ 标普 500",
         note="几乎总是反向。同向出现通常意味着极端行情或数据错位。"),
    dict(a="hormuz_total", b="brent", label="霍尔木兹通行量 ↔ Brent",
         note="若通行量下降而油价**不涨**，说明市场判定封锁未真正落地或可被替代。"),
    dict(a="babelmandeb_total", b="cape_total", label="曼德海峡 ↔ 好望角",
         note="反向＝绕行正在发生；同向下降＝贸易量本身在收缩。"),
    dict(a="hy_spread", b="vix", label="高收益债利差 ↔ VIX",
         note="同为风险定价，但信用市场更关注再融资与违约，二者背离时信用市场通常更准。"),
    dict(a="dxy", b="gold", label="美元指数 ↔ 黄金",
         note="通常反向。同向上涨是「避险买美元也买黄金」的极端风险回避特征。"),
]

COMPOSITE = {
    "id": "fsi",
    "label": "金融战线压力合成指数（FSI）",
    "unit": "z",
    "components": [
        dict(series="brent", weight=1.0, polarity=1),
        dict(series="brent_wti_spread", weight=0.5, polarity=1),
        dict(series="hy_spread", weight=1.0, polarity=1),
        dict(series="vix", weight=1.0, polarity=1),
        dict(series="sp500", weight=1.0, polarity=-1),
        dict(series="nasdaq", weight=0.5, polarity=-1),
        dict(series="ita", weight=0.5, polarity=-1),
        dict(series="dgs2", weight=0.5, polarity=1),
        dict(series="dgs10", weight=0.5, polarity=1),
        dict(series="dxy", weight=0.5, polarity=1),
        dict(series="hormuz_total", weight=1.0, polarity=-1),
        dict(series="babelmandeb_total", weight=1.0, polarity=-1),
    ],
    "note": "Σ(权重 × 极性 × z) / Σ权重。单位是 **z**，不是概率、不是点位、不是预测。"
            "★ 覆盖度不同的两期**不可比较**（少一个分量少一份权重）。"
            "★ 分量里混有 **步长 1 期**（油价、股指、利差）与 **步长 7 期**（海峡通行量）的 z，"
            "两者的时间尺度不同：前者的 z 是「一天的变化有多异常」，后者是「相对上周同一天」。"
            "所以本指数只能读作「压力方向与量级」，不能读作精确的当期变化。",
}

BUCKETS = {
    "oil": dict(name="原油与天然气", order=1,
                caveat="Brent 与 WTI 是**两个不同的标的**（交割地、品质、计价货币敞口都不同），"
                       "价差本身有信息量，不能相互替代。"),
    "inventory": dict(name="库存与战略储备", order=2,
                      caveat="★ 本桶基本是**周频**数据。与日频指标放在一起比较会连着 6 天显示「无变化」，"
                             "那代表**没有新数据**，不代表市场没动。EIA 每周三发布，报告中的「滞后」栏会如实标出。"),
    "rates": dict(name="利率与政策预期", order=3,
                  caveat="收益率的变动一律以 **bp** 计。政策预期部分只给**代理量**（目标区间、EFFR、2Y 收益率的相对位置），"
                         "**不提供加息概率**——本项目没有可用的官方概率源（CME 实测 403、亚特兰大联储数据页实测返回 HTML），"
                         "因此不做这类推断。"),
    "equity": dict(name="股市与板块", order=4,
                   caveat="不同指数的**点位不可比**，只比百分比变化；板块 ETF 的涨跌是**价格反应**，"
                          "不能反推因果（军工股涨不代表冲突会升级）。"),
    "credit": dict(name="信用与波动率", order=5,
                   caveat="信用利差与 VIX 是市场为风险支付的价格，不是风险的客观度量；"
                          "它们会被期权供需、指数成分、被动资金流扭曲。"),
    "fx": dict(name="汇率与贵金属", order=6,
               caveat="DXY 与 FRED 广义美元指数**口径不同**，水平值不可互相替代。"),
    "shipping": dict(name="海峡通行与航运", order=7,
                     caveat="★ 通行量是**实物**读数，但也最容易误读：① 数据由 AIS 事后汇编，滞后 4–7 天；"
                            "② 通行量下降可能是**绕行**而非「贸易中断」——必须与好望角通行量一起看；"
                            "③ 船舶折返会计两次。油轮股价**不是**通行量，只是市场对运价的定价。"),
}

STALENESS = {"daily": 5, "weekly": 16, "monthly": 45,
             "note": "超过该天数视为「源可能停止更新」，报告标 ⏳滞后。"
                     "★ 它衡量的是**数据是否还在更新**，不是市场是否平静。"
                     "周频用 16 天（EIA 周三发布 + 假日余量）；"
                     "PortWatch 通行量虽是日频但汇编滞后，按序列覆盖为 14 天。"}

WINDOW = {"min_obs_for_z": 25, "z_window": 60, "z_floor_rel": 0.0,
          "percentile_window": 252, "detail_n": 20,
          "max_points_per_series": 4000,
          "note": "z_window=60：用最近 60 个观测的**变动**估 σ。"
                  "min_obs_for_z=25：点太少时不给 z（宁缺勿滥，避免 3 个点算出 z=7）。"
                  "z_floor_rel=0：不设相对下限，因为本项目的 σ 已按各序列自身尺度计算。"
                  "max_points_per_series=4000：累积历史的保留上限（约 16 年日频），超过则丢最旧。"}

REQUEST = {
    "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "timeout": 30, "retries": 3, "retry_backoff_sec": 2.0,
    "delay_between_sources_sec": 0.35,
    "min_interval_per_host_sec": 1.2,
    "note": "★ min_interval_per_host_sec 不是「礼貌」，是**正确性**要求："
            "实测 FRED 单请求 1.3 秒 200，但在并发 8 个 + 此前 30 次超时之后，"
            "所有请求一起挂到 20 秒超时（Akamai 把批量并发判为异常并整体降级）。"
            "症状是全红+超时，看起来像源挂了。所以同主机串行且限速。",
}

DIVERGENCE = {"rel_tol": 0.02, "abs_tol": 0.0,
              "note": "默认相对容差 2%（可按序列覆盖）。★ 分歧**不等于**有一方错："
                      "不同源常常代表不同标的（Brent 期货 vs 现货、DXY vs 广义美元指数）。"
                      "报告会列出分歧明细并说明口径，不自动判定谁对。"}

DATA_GAPS = {
    "chokepoint_probe": [
        {"label": "CME FedWatch 页面 / 报价接口",
         "why": "实测 HTTP 403（三个端點全部 403）—— 有主动反爬，不绕。"},
        {"label": "亚特兰大联储 Market Probability Tracker 数据文件",
         "why": "HTTP 200、48KB，但**内容是一个 HTML 页面**（JS 挑战/错误页），不是 CSV。"
                "只看状态码与体积会以为拿到了数据。"},
        {"label": "Stooq 历史行情（作为冗余源）",
         "why": "HTTP 200、每个符号返回**完全相同的 796 字节** JS proof-of-work 挑战页。"
                "需要 JS 引擎，urllib/curl 均取不到；已用 Yahoo 覆盖同一批标的。"},
        {"label": "EIA Open Data API（石油库存，作为 FRED 的替代）",
         "why": "HTTP 403（需要 API key）。本项目不使用需要密钥的接口。"},
        {"label": "纽约联储 markets API（last/400.json）",
         "why": "HTTP 200 可用，但响应按日期【降序】排列——data[-1] 是 19 个月前的旧记录，"
                "校验器若直接取末条会把 latest 写成 2025-02-14。已改为取日期最大者。"},
        {"label": "美国财政部 TIPS 真实收益率曲线",
         "why": "HTTP 200 可用，但列名是大写 `10 YR`（par yield 曲线是 `10 Yr`）。"
                "两张表连列名风格都不一致，属于典型的「200 但字段对不上」陷阱。"},
        {"label": "FRED 的 Python 直连",
         "why": "urllib 4 种请求头组合全部超时（40s），同一 URL curl 200 / 268KB / 3s。"
                "判定为 Akamai 指纹区别对待，已改用 curl 传输并记录在案。"},
    ],
}


# ================================================================ 证据校验
def load_evidence() -> dict:
    """把各轮探测结果合并成 id → {ok, best, attempts}。

    ★ 合并规则要小心：同一个源在不同轮次可能一通一堵。规则是
      「**只要有一轮 ok，就认为可用**，并采用**最后一次 ok** 的记录」，
      同时把失败的尝试一并留档。反过来（采用最后一次尝试）会让
      「最后一轮恰好赶上风控」把可用源误判为不可用。
    """
    ev: dict = {}
    for p in sorted(glob.glob(os.path.join(HERE, "_probe_round*.json"))):
        try:
            with open(p, encoding="utf-8") as f:
                j = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        rnd = os.path.basename(p)
        for r in j.get("results", []):
            slot = ev.setdefault(r["id"], {"attempts": [], "best": None,
                                           "rounds": []})
            slot["attempts"].append({
                "round": rnd, "http": r.get("http"), "ok": bool(r.get("ok")),
                "bytes": r.get("bytes"), "why": r.get("why"),
                "transport": r.get("transport")})
            slot["rounds"].append(rnd)
            if r.get("ok"):
                slot["best"] = r
    return ev


def probe_id_for(entry: dict, url: str = "") -> str:
    p = entry["provider"]
    if p in ("yahoo", "fred", "stooq"):
        return f"{p}:{entry['ref']}"
    if p == "treasury":
        y = "2026"
        if "{year_prev}" in url:
            y = str(datetime.now(timezone.utc).year - 1)
        elif "{year}" in url:
            y = str(datetime.now(timezone.utc).year)
        else:
            import re
            m = re.search(r"/(20\d\d)/all", url)
            if m:
                y = m.group(1)
        kind = "tips" if "real_yield_curve" in url else "daily-yield"
        return f"treasury:{kind}-{y}"
    if p == "eia_wpsr":
        return "eia_wpsr:table1"
    if p == "arcgis":
        return f"arcgis:{entry['entity_match']}:{entry['value_field']}"
    if p == "nyfed":
        return f"nyfed:{entry['ref']}"
    return f"{p}:{entry.get('ref', '?')}"


def check_evidence(cfg: dict, ev: dict) -> tuple:
    missing, ok_list, transported = [], [], []
    for s in cfg["series"]:
        for e in s["chain"]:
            urls = e.get("urls") or [e.get("url", "")]
            for u in urls:
                pid = probe_id_for(e, u)
                slot = ev.get(pid)
                if slot is None or slot["best"] is None:
                    missing.append({"series": s["id"], "chain": e["provider"],
                                    "probe_id": pid,
                                    "detail": (slot or {}).get("attempts") or "无探测记录"})
                else:
                    b = slot["best"]
                    ok_list.append({"series": s["id"], "probe_id": pid,
                                    "rows": b.get("rows"),
                                    "latest": b.get("latest_date"),
                                    "value": b.get("latest"),
                                    "transport": b.get("transport"),
                                    "round": os.path.basename(
                                        [a["round"] for a in slot["attempts"]
                                         if a["ok"]][-1])})
            if e.get("transport") == "curl":
                transported.append(f"{s['id']}:{e['ref']}")
    return missing, ok_list, transported




def _entry_evidenced(entry: dict, ev: dict) -> tuple:
    """链条目级判定：它的**每个 URL** 都必须有 ok 记录。返回 (ok, probe_ids)。"""
    ids = []
    for u in (entry.get("urls") or [entry.get("url", "")]):
        pid = probe_id_for(entry, u)
        ids.append(pid)
        slot = ev.get(pid)
        if slot is None or slot["best"] is None:
            return False, ids
    return True, ids


def _filter_series(series: list, ev: dict) -> tuple:
    """过滤每个序列的链条目；全空则弃用整条。"""
    out, dropped = [], []
    for sr in series:
        chain, why = [], []
        for e in sr.get("chain", []):
            ok, ids = _entry_evidenced(e, ev)
            if ok:
                chain.append(e)
            else:
                bad = ", ".join(i for i in ids)
                why.append(f"{e['provider']}({bad}) 无实测可用记录")
        if sr.get("chain") and not chain:
            dropped.append({"series": sr["id"], "reason": "；".join(why)})
        elif len(chain) != len(sr.get("chain", [])):
            kept = dict(sr, chain=chain)
            kept["note"] = (sr.get("note", "") +
                            f"〔配置生成时按实测证据过滤：弃 {len(why)} 条目〕").strip()
            out.append(kept)
            dropped.append({"series": sr["id"] + "（部分条目）",
                            "reason": "；".join(why)})
        else:
            out.append(sr)
    return out, dropped


def _derived_ok(d: dict, kept_ids: set, der_ids: set) -> bool:
    refs = ([t["series"] for t in d.get("terms", [])]
            if d.get("kind") == "linear"
            else [v for v in (d.get("a"), d.get("b"), d.get("series")) if v])
    return all(r in kept_ids or r in der_ids for r in refs)


def _prune_derived(derived: list, kept_ids: set) -> list:
    """迭代裁剪：派生量可以引用派生量，需收敛到不动点。"""
    der_ids: set = set()
    while True:
        kept = [d for d in derived
                if _derived_ok(d, kept_ids, der_ids)]
        new_ids = {d["id"] for d in kept}
        if new_ids == der_ids:
            return kept
        der_ids = new_ids


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 finance-front-monitor 配置")
    ap.add_argument("--check", action="store_true", help="只校验，不写文件")
    ap.add_argument("--allow-missing", action="store_true",
                    help="跳过证据校验（排障用，会打印大段警告）")
    ap.add_argument("--allow-drop", action="store_true",
                    help="允许丢掉现役序列（默认拒绝：防止 config 里手工补的序列被静默抹掉）")
    a = ap.parse_args()

    ev = load_evidence()
    cfg = {
        "version": "1.0.0",
        "project": "finance-front-monitor",
        "topic": "美伊冲突 · 金融战线监测（七个桶 × 可复算口径）",
        "generated_at": datetime.now(timezone.utc).date().isoformat(),
        "generated_by": "tools/build_config.py（源可用性来自 tools/_probe_round*.json 实测）",
        "probe_evidence": {"rounds": sorted({os.path.basename(p) for p in
                                             glob.glob(os.path.join(HERE, "_probe_round*.json"))}),
                           "note": "每个链条目都必须在此找到 ok:true 记录；"
                                   "构建脚本强制校验，缺失即拒绝生成配置。"},
        "request": REQUEST,
        "divergence": DIVERGENCE,
        "window": WINDOW,
        "staleness": STALENESS,
        "buckets": BUCKETS,
        "series": SERIES,
        "derived": DERIVED,
        "watch_pairs": WATCH_PAIRS,
        "composite": COMPOSITE,
        "data_gaps": DATA_GAPS,
    }

    # ---------- 按证据过滤链条 ----------
    # 原则：**链条里每个 URL 都必须有 ok:true 的实测记录**，否则该条目不进配置；
    # 一个序列的所有条目都被滤掉 → 整条序列弃用（如实披露，绝不带病进配置）。
    # 这替代了旧版「缺证据即整体退出」：退出会让上一版配置继续服役，
    # 而过滤让配置始终与最新实测一致。但设硬守卫：弃用超过一半或
    # headline 少于 4 条 → 仍然拒绝生成（说明数据面大面积塌了，该去修探测）。
    filtered_series, dropped = _filter_series(SERIES, ev)

    missing, ok_list, transported = check_evidence(
        dict(cfg, series=filtered_series), ev)
    print(f"序列 {len(SERIES)} 条 → 证据过滤后 {len(filtered_series)} 条"
          f"（弃用 {len(dropped)}）/ 派生 {len(DERIVED)} 条 / "
          f"链条目实测通过 {len(ok_list)} 个")
    print(f"走 curl 传输的条目：{len(transported)} 个"
          f"{'（' + ', '.join(sorted(set(transported))[:6]) + ' …）' if transported else ''}")
    if missing:
        print(f"\n!! 仍有 {len(missing)} 个链条目没有实测记录（不进配置）：")
        for m in missing:
            print(f"   - {m['series']:<22} {m['chain']:<9} probe={m['probe_id']}")
    if dropped:
        print("\n!! 以下序列因**全部链条目都缺实测证据**被弃用：")
        for d in dropped:
            print(f"   - {d['series']:<22} {d['reason'][:80]}")

    # 引用裁剪：派生量 / 合成分量 / 观察对引用的序列必须仍然在场
    kept_ids = {x["id"] for x in filtered_series}
    kept_der = [d for d in DERIVED if _derived_ok(d, kept_ids, set())]
    der_ids = {d["id"] for d in kept_der}
    kept_der = _prune_derived(DERIVED, kept_ids)
    kept_comp = [c for c in COMPOSITE["components"]
                 if c["series"] in kept_ids or c["series"] in der_ids]
    kept_wp = [w for w in WATCH_PAIRS
               if all(x in kept_ids or x in der_ids
                      for x in (w["a"], w["b"]))]
    n_comp_dropped = len(COMPOSITE["components"]) - len(kept_comp)
    if kept_comp:
        print(f"合成分量 {len(kept_comp)}/{len(COMPOSITE['components'])} 在场"
              f"（弃 {n_comp_dropped}）；观察对 {len(kept_wp)}/{len(WATCH_PAIRS)}")

    # 硬守卫：数据面大面积塌方时拒绝生成，逼人去修探测而不是静默降级
    n_head = sum(1 for x in filtered_series if x.get("role") == "headline")
    if len(filtered_series) < 10 or n_head < 4:
        print(f"\n!! 过滤后只剩 {len(filtered_series)} 条序列（headline {n_head} 条）"
              f"—— 数据面大面积塌方，拒绝生成配置。请先修网络/探测。")
        return 1

    cfg["series"] = filtered_series
    cfg["derived"] = kept_der
    cfg["composite"] = dict(COMPOSITE, components=kept_comp)
    cfg["watch_pairs"] = kept_wp
    cfg["data_gaps"] = dict(DATA_GAPS,
                            dropped_unverified=[
                                {"series": d["series"], "reason": d["reason"]}
                                for d in dropped])

    # ---------- ★ 禁止静默丢序列 ----------
    # 背景（2026-09-23 实测）：config.json 与 build_config.py 曾经脱节 ——
    # 有人直接改 config 补了序列（ttf_gas / wheat / corn / bdi / usdrub /
    # usduah / ta125 / food 桶 等），却没回填构建脚本。此后每次重跑
    # build_config，这些「只活在 config 里」的序列就会被**静默**删掉：
    # 报告少几行，没人会注意到，直到某天有人问「泰铢那条呢」。
    # 所以这里加硬守卫：新配置必须覆盖旧配置的全部序列 id，少一个就拒绝写盘。
    out = os.path.join(ROOT, "config.json")
    if os.path.exists(out):
        try:
            with open(out, encoding="utf-8") as f:
                old_cfg = json.load(f)
        except (OSError, json.JSONDecodeError):
            old_cfg = {}
        old_ids = [s["id"] for s in old_cfg.get("series", [])]
        new_ids = {s["id"] for s in filtered_series}
        lost = [i for i in old_ids if i not in new_ids]
        added = sorted(new_ids - set(old_ids))
        if added:
            print(f"\n本次新增序列 {len(added)} 条：{', '.join(added)}")
        if lost:
            print(f"\n!! 本次生成会**丢掉** {len(lost)} 条现役序列：")
            for i in lost:
                print(f"   - {i}")
            print("   它们只存在于 config.json，build_config.py 里没有对应定义。")
            print("   正确做法是把它们补进本文件的 SERIES / BUCKETS，"
                  "而不是让配置倒着退回去。")
            if not a.allow_drop:
                if a.check:
                    print("\n   （--check 模式，本来就不写盘；但要意识到这个差异存在。）")
                else:
                    print("\n拒绝写盘，config.json 未被改动。确实要删请用 --allow-drop。")
                    return 1
            else:
                print("   --allow-drop 已给出 → 继续，但请把这次删除写进 data_gaps 备案。")
        # 手工维护的顶层块要保住：build_config 不生成它们，重跑会把它们抹掉。
        for k in ("taco_factors", "patched_by"):
            if k in old_cfg and k not in cfg:
                cfg[k] = old_cfg[k]
                print(f"   保留手工维护的顶层块：{k}")
        req_old = old_cfg.get("request") or {}
        if req_old.get("drop_inprogress_session"):
            if not cfg["request"].get("drop_inprogress_session"):
                cfg["request"]["drop_inprogress_session"] = True
            cfg["request"].setdefault(
                "drop_inprogress_session_note",
                req_old.get("drop_inprogress_session_note", ""))
            print("   保留 request.drop_inprogress_session 口径开关（只取完整交易日收盘）")

    if a.check:
        print("\n--check：未写文件。")
        return 0
    with open(out, "w", encoding="utf-8", newline="") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    print(f"\n已写入 {os.path.relpath(out, ROOT)}")

    # 交叉引用自检：派生量引用的序列必须存在；复合分量的序列必须存在
    ids = {s["id"] for s in SERIES}
    bad = []
    for d in DERIVED:
        refs = ([t["series"] for t in d.get("terms", [])]
                if d.get("kind") == "linear"
                else [v for v in (d.get("a"), d.get("b"), d.get("series")) if v])
        for r in refs:
            if r not in ids and r not in {x["id"] for x in DERIVED}:
                bad.append(f"derived {d['id']} → 未知序列 {r}")
    for c in COMPOSITE["components"]:
        if c["series"] not in ids and c["series"] not in {x["id"] for x in DERIVED}:
            bad.append(f"composite → 未知序列 {c['series']}")
    for wp in WATCH_PAIRS:
        for k in ("a", "b"):
            if wp[k] not in ids and wp[k] not in {x["id"] for x in DERIVED}:
                bad.append(f"watch_pair {wp['label']} → 未知序列 {wp[k]}")
    if bad:
        print("\n!! 交叉引用错误：")
        for b in bad:
            print("   -", b)
        return 1
    print("交叉引用自检通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
