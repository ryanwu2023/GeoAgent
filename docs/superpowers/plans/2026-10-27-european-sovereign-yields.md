# 欧洲主权债收益率接入实施计划

> **执行要求：** 按任务逐项实施和验证，使用复选框跟踪进度。

**目标：** 停止俄罗斯国家杜马选举的日常采集，并为俄乌主题增加德国、法国、欧元区10年期收益率和法德利差。

**架构：** 保留 `ukraine-aid-monitor` 作为注册采集器，在同一目录增加独立的 `european_yields.py`，发布 `output/geo-v6.json`；现有适配器会将该文件与 `ALL-records.jsonl` 一起导入。TradingView日频历史为主数据，FRED月频仅作交叉核验，法德利差只使用同日有效观测计算。

**技术栈：** Python 3、TradingView Scanner HTTP 接口、FRED CSV、geo.v6扩展协议、FastAPI快照适配器、React/TypeScript/Vite。

**设计文档：** `docs/superpowers/specs/2026-10-27-european-sovereign-yields-design.md`

## 全局约束

- 保留全部历史杜马选举记录，只停止未来选举专用轮询和当前日报重点展示。
- 只发布TradingView已完成交易日的日线，不把FRED月频值前向填充为日频。
- 明确区分缺失、失败和正常无新增；不得用零替代缺失收益率。
- 四项指标绑定 `topic=ukraine`、`dimension=aid`、`bucket=rates`、`shared_market=false`。
- 法德利差公式为 `(FR10Y-DE10Y)×100`，单位为基点，且只使用同日有效观测。
- 不修改或删除用户未纳入Git的Excel文件。

## 重点复核风险

- TradingView仅部分品种成功时，保留成功序列并标记部分成功；德国或法国任一缺失时不计算利差。
- TradingView返回当天未收盘蜡烛时，不能进入已完成日线历史。
- FRED与TradingView最新月份不一致时，只写核验信息，不覆盖或延长日频序列。
- 非交易日没有新蜡烛属于正常无新增，不应标记失败。
- 停用选举源后，既有杜马选举记录仍可正常归一化和回放。

---

### 任务一：停止国家杜马选举日常采集

**修改文件：**

- `collectors/russia-domestic-monitor/config.json`
- `collectors/russia-domestic-monitor/russia_domestic_monitor.py`
- `collectors/russia-domestic-monitor/README.md`
- `tests/test_russia_domestic_retirement.py`

- [x] 编写选举专用来源停用、日报栏目移除、历史分类仍可用的失败测试。
- [x] 运行测试并确认修改前失败。
- [x] 仅停用选举专用来源；当前日报排序不再包含 `duma_election`；保留历史桶定义。
- [x] 更新README、标题和过期边界说明。
- [x] 运行新测试与采集器离线自检。

### 任务二：增加TradingView日频收益率和FRED月频核验

**新增或修改文件：**

- 新增 `collectors/ukraine-aid-monitor/european_yields.py`
- 修改 `collectors/ukraine-aid-monitor/ukraine_aid_monitor.py`
- 修改 `collectors/ukraine-aid-monitor/README.md`
- 修改 `collectors/collectors.json`
- 新增 `tests/test_european_yields_collector.py`

**对外接口：**

- `fetch_tradingview_history(symbol: str, bars: int = 800) -> tuple[bytes, list[dict]]`：读取 TradingView 最新完整日频观测，并与本地历史序列合并积累
- `fetch_fred_monthly(series_id: str) -> tuple[bytes, list[dict]]`
- `same_day_spread(france: list[dict], germany: list[dict]) -> list[dict]`
- `build_payload(output_root: Path, today: date | None = None) -> dict`
- `main(argv: list[str] | None = None) -> int`

- [x] 编写三条日频序列、收盘日过滤、同日利差、部分失败、FRED仅核验以及缺失值处理测试。
- [x] 实现 `TVC:DE10Y`、`TVC:FR10Y`、`TVC:EU10Y` 日频序列，并通过每日运行逐步积累历史。
- [x] 接入 `IRLTLT01DEM156N`、`IRLTLT01FRM156N`、`IRLTLT01EZM156N` 月频核验。
- [x] 输出 `geo-v6.json`、来源状态和原始归档，并由主采集器入口统一调用。
- [x] 将注册表输出类型升级为 `records-and-metrics`，不引入额外运行依赖。
- [x] 运行采集器测试、注册表测试和离线自检。

### 任务三：导入、排序和前端展示

**修改文件：**

- `backend/workbench.py`
- `src/metric_category.ts`
- `tests/test_european_yield_integration.py`
- `tests/metric_category.test.cjs`

- [x] 编写双输出导入、仅俄乌主题可见、来源状态保留的后端测试。
- [x] 编写四项指标均归类为“经济”的前端分类测试。
- [x] 将四项序列加入俄乌重点指标优先级，并做通过测试所需的最小导入调整。
- [x] 运行后端指标与前端分类测试。

### 任务四：真实采集与端到端验证

- [x] 真实运行采集器，检查四项观测无重复、日期有序且来源状态正确。
- [x] 构建新快照，确认俄乌指标接口包含四项、美伊接口不包含。
- [x] 执行Vite生产构建，复用既有指标卡的坐标、悬停值、当前值、前值和主题筛选能力。
- [x] 运行采集器协议、适配器、后端、指标分类和前端构建回归测试。
- [x] 执行 `git diff --check`，确认用户Excel文件未被触碰。

