# 地缘政治智能体 · 研究工作台 V6

React / TypeScript + MapLibre GL / deck.gl + FastAPI / SQLite。双主题、十三维度、五类资产、冻结快照、引用问答和简报档案。

[产品设计文档 V6](docs/design-v6.md) · [适配器协议](docs/adapter-contract.md)

页面恢复 V5 原型的顶部主题切换、中央地图与功能页签、右侧常驻研判布局。支持全球/主题聚焦、图层开关和点位到证据联动；地图在桌面首屏完整显示。综合研判默认展示资产关联矩阵。

## 运行

Windows PowerShell，在本目录执行：

```powershell
python -m pip install -r requirements.txt
npm.cmd ci
npm.cmd run build
.\start.ps1
```

打开 http://127.0.0.1:8000 。API 文档为 `/docs`。仅绑定本机；此版本没有多用户认证，不部署到公网。

开发：一个终端运行 `python -m uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000`，另一个运行 `npm.cmd run dev`。

## 接入现有数据

默认读取项目内 `data/collector-output`，不再依赖 WorkBuddy 绝对路径。可在 `.env` 修改 `CRAWLER_ROOT`。点击“更新快照”重新读取：

- `naval-monitor/output/ALL-records.jsonl`
- `defense-spend-monitor/output/ALL-records.jsonl` 及最新日期目录下的 `metrics-*.json`
- `readiness-monitor/output/ALL-records.jsonl` 及 `_source_status.json`

数据存于 `data/research.sqlite3`；原始文件按内容归档至 `data/archive`。相同输入复用快照，变化产生新快照，旧快照不变。备份整个 `data` 目录即可保留历史、问答和简报。

地图底图已本地打包，无需外部瓦片或地图密钥。地点标记来自爬虫公开记录，默认只表示文章提及，不证明部署。

## 大模型

复制 `.env.example` 为 `.env`，配置 `LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY` 后重启服务。兼容 `/chat/completions` 接口。请求在用户提问或生成简报时发起，发送的是检索到的原文片段、计算结果和研究规则。

未配置模型时，使用真实的本地检索、数值计算及模板简报，并明确标注，不伪装成模型输出。模型输出需要 JSON，引用不存在、出现未经授权的新数值、接口失败或超时都会保留本地结果。引用校验不代表语义已经验证。

“分析资产影响”可生成当前主题/筛选/快照范围内的有条件资产与情景解释并保存版本；模型判断始终待研究员核验。无模型或无证据时，资产矩阵显示研究规则及证据不足，不机械输出涨跌。

## 新爬虫扩展

在任何子项目下输出 `output/geo-v6.json`，使用 `geo.v6/1` 协议。参见 [接入协议](docs/adapter-contract.md) 及 `examples/geo-v6.example.json`。示例是协议测试数据，默认不导入真实快照。

项目内置 19 个采集器的统一注册表。常用命令：

```powershell
python scripts/collect.py --list
python scripts/collect.py --id xinhua-monitor
python scripts/collect.py --topic ukraine
python scripts/collect.py --all
```

完整运行数据保存在 `data/collector-output` 并由 Git 忽略；`examples/demo-data` 是克隆仓库后的有限演示快照。公开发布前运行 `python scripts/audit_repository.py`，检查环境文件、疑似密钥、个人绝对路径和超大文件。本项目尚未选择开源许可证，公开仓库前需由仓库所有者确定。

## 验证

```powershell
python -m pytest -q
npm.cmd run build
```

测试覆盖不可变快照、重复导入、修订、缺失/零值、错误舰位、适配器扩展、同源转载、日期范围、引用约束、模型失败/超时、模型资产结果隔离、简报下载与三轮 API 演练。当前 25 项测试通过；模型测试使用模拟接口，不代表真实模型已经联调。

## 研究边界

- 现有材料按关键词归入主题候选，分类不等同语义核验；只在来源确实提供时保留信息。
- 俄乌材料来自现有爬虫中的相关报道，不能替代专门的战线、援助及外交采集。
- 来源等级沿用爬虫口径；T1 并非所有陈述均已证实。
- 报道日期筛选是检索条件，不是历史可知性重建；可重现研究使用真实冻结快照。
- 月度财政指标是美国整体背景，不能直接归因于美伊或俄乌。
- 原油、黄金等价格及市场验证未接入，不提供收益预测或配置权重。
- URL 相同的重复记录归并；明确提供 `original_url` 的转述共享原始出处，未知转述关系不推测成已完成的同源识别。

## 地理数据

`public/countries.geojson` 来自 Natural Earth 110m 国家地理数据（公共领域）。原始下载地址：
https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_110m_admin_0_countries.geojson

此项目参考 World Monitor 的交互方向，未复制其应用源码。

## 中文展示与综合态势地图

新闻标题、摘要、来源名、地点、问答引用及简报采用中文阅读层，原始来源链接保留原语言。译文单独存于数据库 `translations` 表，使用原文内容哈希关联，不修改爬虫和已冻结的原始证据。机器译文需结合原始报道核对专名和语义。

本地翻译采用 [赫尔辛基大学英中翻译模型](https://huggingface.co/Helsinki-NLP/opus-mt-en-zh)，并用本地量化推理；新闻文本不上传翻译服务。依赖与准备方式：

```powershell
python -m pip install -r requirements-translation.txt
python -m scripts.prepare_translation
python -m scripts.prepare_persian_translation
python -m scripts.translate_snapshot
```

当前机器已经准备模型后，后端启动和导入新快照会自动补译新增内容。没有译文时显示中文准备状态，不把英文伪装为中文。未来采集器也可直接提供 `title_zh/summary_zh`。

地图按六类事件过滤，支持升级相关、持续相关、缓和相关和待研判线索。跨来源报道只要关联到地理对象即可展示；地区参考点不是精确现场。右侧数量为报道线索，不是冲突强度或概率。

波斯语报道使用 Argos 官方模型先转为英文，再生成中文阅读文本。跨语言转译可能损失细节，原始来源始终保留；无有效中文译文不计入完成数量。

## 2026-09-19：十个爬虫与市场数据接入

现接入目录内十个项目：海军动态、军费补给、战备表态、伊朗升级线索、美国民意、以色列、中东、伊朗内政、外交斡旋、金融战线。点击“更新快照”即可读取最新输出，不启动或改写爬虫。

当前归并6587条记录，57个指标定义（7项财政指标、33条基础金融序列、17条金融派生量）。两项派生量上游无数据；布伦特年化波动率因上游使用价格差而非收益率，已隔离并明确提示。指标数量不表示全部都有可用值。

金融数据保留历史、实际命中来源、原始响应、采集时点、比较步长与修订信息。收益率变动用基点，百分比型非利率指标用百分点；航运量按上游7期比较。资产详情和简报加入市场观测及引用，不自动将同期价格变化归因为冲突影响。

多个爬虫重复收录的同一链接归并，但保留所有研究维度。专题采集结果仍是研究候选，其中可能包含背景或误匹配。新增新闻后台补译，进度在页面显示，翻译缓存不修改冻结原文。

## 市场补充与规则研判（V6.2）

运行 `python scripts/supplement_market.py` 下载 FRED 的高收益债及投资级债期权调整利差、十年期盈亏平衡通胀率、欧元兑美元历史观测，写入爬虫目录 `market-context-monitor/output/geo-v6.json`。随后点击“更新快照”。这是手动刷新任务，尚未设置自动定时更新；请求失败不会替换上一份成功数据包。原始 CSV 保留在该目录，冻结快照归档包含完整数列的 JSON。

利差从百分比换算为基点。不同序列最新日期不同，分析逐条保留比较日期。ICE 信用指数仅限内部研究，公开分发前需核对来源授权。民调新闻不直接转成可比支持率；还需要调查日期、机构、样本、题目和选项。

资产页面与简报提供透明的规则研判，不冒充模型分析或冲突因果证明。市场方向标签与条件传导分别展示，数据缺口保留在每类资产详情中。搜索支持名称、单位及统计口径；经济、军事、动态按已接入数据归类，民调目前展示结构化缺口。
