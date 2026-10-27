# 地缘政治智能体 · 研究工作台 V6

React / TypeScript + MapLibre GL / deck.gl + FastAPI / SQLite。双主题、十三维度、五类资产、冻结快照、引用问答和简报档案。

[产品设计文档 V6](docs/design-v6.md) · [适配器协议](docs/adapter-contract.md)

[抓取说明与采集器维护手册](docs/抓取说明/README.md)

[构建、正式发布与回滚](docs/发布与回滚.md)

页面恢复 V5 原型的顶部主题切换、中央地图与功能页签、右侧常驻研判布局。支持全球/主题聚焦、图层开关和点位到证据联动；地图在桌面首屏完整显示。综合研判默认展示资产关联矩阵。

## 运行

Windows PowerShell，在本目录执行：

```powershell
.\.venv\Scripts\python.exe -m scripts.bootstrap_env
npm.cmd ci
npm.cmd run build
.\start.ps1
```

打开 http://127.0.0.1:8000 。API 文档为 `/docs`。仅绑定本机；此版本没有多用户认证，不部署到公网。

`start.ps1` 是单进程生产式本地服务入口，默认端口为 8000；需要换端口时可执行
`.\start.ps1 -Port 8011`。脚本会先检查端口和 `/api/status`，不会停止未被它记录的进程。

macOS / Linux：

```bash
.venv/bin/python -m scripts.bootstrap_env
npm ci && npm run build
.venv/bin/python -m scripts.serve
```

开发联调（当前推荐环境）可由一个跨平台入口同时启动前后端：

```bash
.venv/bin/python -m scripts.start_local
```

默认打开 http://127.0.0.1:8010/，Vite 将 `/api` 转发到 http://127.0.0.1:8011。Windows 使用 `.venv\Scripts\python.exe -m scripts.start_local`。可通过 `GEOAGENT_API_PORT`、`GEOAGENT_WEB_PORT` 或命令行参数覆盖端口：

```bash
.venv/bin/python -m scripts.start_local --api-port 8121 --web-port 8120
```

只启动 API：`.venv/bin/python -m scripts.start_local --no-frontend`；查看命令但不启动：`.venv/bin/python -m scripts.start_local --dry-run`。按 `Ctrl-C` 会同时停止该入口拉起的两个子进程。

如果只需要单进程页面，继续使用 `.venv/bin/python -m scripts.serve`；它默认监听 8000，也支持 `--port` 或 `GEOAGENT_API_PORT`。不要让 Vite 的 8010 代理指向被 Colima/SSH 占用的 8000。
Windows `start.ps1` 正常启动会在后台按注册表顺序逐个执行全部爬虫；每个项目优先使用 `run_new.bat`，没有时使用 `run.bat`。单个任务失败不会阻塞后续任务，全部结束后自动导入并生成冻结快照，但不会自动切换用户当前工作快照。运行进度、最近结果和失败日志可在页面“数据源”查看，详细日志保存在 `data/crawler-runs`。上述 macOS/Linux 入口不会启动这套 Windows 批处理。开发调试且不希望启动爬虫时使用：

```powershell
.\start.ps1 -NoCrawlers
```

停止服务会同时停止仍在运行的爬虫进程树：

```powershell
.\stop.ps1
```

开发：一个终端运行 `python -m uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000`，另一个运行 `npm.cmd run dev`。

## 接入现有数据

默认读取项目内 `data/collector-output`，不再依赖 WorkBuddy 绝对路径。可在 `.env` 修改 `CRAWLER_ROOT`。点击“更新快照”重新读取：

- `naval-monitor/output/ALL-records.jsonl`
- `defense-spend-monitor/output/ALL-records.jsonl` 及最新日期目录下的 `metrics-*.json`
- `readiness-monitor/output/ALL-records.jsonl` 及 `_source_status.json`

数据存于 `data/research.sqlite3`；原始文件按内容归档至 `data/archive`。相同输入复用快照，变化产生新快照，旧快照不变。备份整个 `data` 目录即可保留历史、问答和简报。

地图底图已本地打包，无需外部瓦片或地图密钥。地点标记来自爬虫公开记录，默认只表示文章提及，不证明部署。

## Pi Chat Agent / 大模型

复制 `.env.example` 为 `.env`。本地 PiChat 使用 `PICHAT_BASE_URL=http://127.0.0.1:9172`，并配置绑定 `geo-research-assistant` 的 `PICHAT_API_KEY`；后端会调用 PiChat 的 `POST /api/v1/chat`，复用该 Key 绑定的 Agent、工具与治理能力。应用 Key 需要 `chat:invoke` scope，且只由 GeoAgent 后端读取，不会发送到浏览器。仅有嵌入身份权限的 Key 不能替代项目级 API Key。

### 页面连接配置与 MCP Key

顶部「连接配置」支持配置 PiChat 根地址（也接受 `/chat` 并自动去除）、Agent ID、嵌入用户手机号、入口 entry、产品用户 ID 和应用 Key。保存后嵌入面板立即重连，无需构建或重启；应用 Key 留空表示保持原值，读取接口不返回应用 Key。此处配置用于嵌入面板，不修改生成简报使用的 `PICHAT_API_KEY`。

MCP 已随主后端启动：开发页面地址为 `http://127.0.0.1:8010/mcp`，后端直连为 `http://127.0.0.1:8011/mcp`；单进程发布后为 `https://<项目域名>/mcp`。在页面创建 MCP Key 后，把地址和 Bearer Token 配到可信 Agent 的 MCP 来源中。Key 授权本项目全部只读工具/快照；仅创建时显示原文，数据库只存 SHA-256 哈希，撤销立即生效。`/healthz` 返回 MCP 健康信息，但协议验收应使用带 Key 的 initialize、tools/list、tools/call。

独立公网部署需设置强随机 `GEOAGENT_SETTINGS_TOKEN`，页面输入管理口令后加载/保存。Sealos 单用户部署沿用网关 Basic 登录：显式启用 `GEOAGENT_TRUST_AUTHENTICATED_GATEWAY=true`，仅信任 loopback 网关覆盖写入的身份头；应用端口必须隔离，使用 `scripts.serve` 禁用代理客户端地址改写。未启用这两种方式时，管理只允许无转发头的本机请求。网关仅对 `/mcp` 取消 Basic 登录，后端仍强制 Bearer Key；其他路径继续保护。PiChat 用户身份签发不等同于管理权限。配置与 Key 存于 `GEO_DATA_DIR/research.sqlite3`，务必持久挂载该数据目录、限制文件权限，并随数据备份保护其中的 PiChat 应用密钥（服务端存储，不加密）。

本项目外网预期 MCP 地址为 `https://geoagentd40d.sealosgzg.site/mcp`，PiChat 服务根地址配置为 `https://chat.lhotsetech.com`。部署网关必须允许 Agent 的 Bearer 请求到达 `/mcp`，不能用前端 SPA 回退或交互式登录页替代 MCP 响应。PiChat 应用还需允许 GeoAgent 外网域名作为嵌入来源，并配置相应 entry/用户权限。

独立调试入口 `backend.geo_mcp.server` 的 8401 服务仍可使用，但不再是项目启动的前置条件；其 legacy loopback token 与 scope-file 模式保留兼容。项目统一 `/mcp` 支持页面管理的 Key；不要把本地开发 token 用作公网凭证。

未配置 Pi Chat 时，也可以配置 `LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY`，使用兼容 `/chat/completions` 的模型。默认 `LLM_AGENT_MODE=tools`：本地模型先制定问题计划，再在后端边界内自主选择快照概览、公开证据检索与全文、指标目录与序列、资产/情景分析，以及 `geo_internal_message_stats`、`geo_internal_message_search`、`geo_internal_message_get` 三个华泰内部消息工具，共九个只读工具。后端执行工具并锁定快照、主题、日期、证据与上下文预算；模型不能直接访问文件、数据库或网络。

未配置模型时，使用真实的本地检索、数值计算及模板简报，并明确标注，不伪装成模型输出。工具代理采用动态证据预算，先宽检索并混合重排序，再读取少量关键证据全文；复杂问题最多补充检索 20 条华泰内部消息、读取 6 条正文。华泰消息在回答中单独列示为“内部消息、待外部核验”，不与公开证据混为同一引用。最终正文是自由 Markdown，引用通过独立 `citation_map` 映射并逐句检查。单句数字或引用失败只删除该句，最终始终保留可读回答。设置 `LLM_AGENT_MODE=legacy` 可临时使用旧三轮兼容管线，`LLM_QA_ROUNDS=1..5` 仅作用于该兼容管线。PiChat Agent 继续自行管理工具循环。

本地 `tools` 模式使用可恢复的单 Agent Harness。运行依次经过问题规划、宽检索、关键正文读取、复核补查、答案综合和引用校验；每个阶段与工具批次写入 `qa_runs`、`qa_steps` 和 `qa_artifacts`。完整工具结果保存在本地数据库，模型只接收结构化预览和研究记忆，避免截断损坏 JSON。只读且互不依赖的调用有界并发执行，单个工具失败不会丢弃同批成功结果。模型超时或引用校验失败时，系统从最近检查点重试或生成确定性降级回答，不能返回空白。

浏览器收到 `run_id` 后按快照、主题、维度、日期和原始问题保存续跑标识；连接中断时后端继续写检查点，同范围再次提交会复用成功结果。`GET /api/qa-runs/{run_id}` 可读取脱敏的阶段、步骤、工具成功数和恢复状态，不返回完整工具工件。以下变量控制上下文预算：

```dotenv
QA_CONTEXT_MAX_TOKENS=32000
QA_CONTEXT_SOFT_TOKENS=24000
QA_CONTEXT_OUTPUT_RESERVE=6000
QA_TOOL_PREVIEW_TOKENS=1800
```

这套 Harness 只作用于本地 OpenAI-compatible 模型。PiChat 的请求协议、Agent 工具循环以及 GeoAgent MCP 对外工具保持原样；九个本地工具仍是原来的只读注册对象。

可以分别配置 `LLM_FAST_MODEL` 与 `LLM_REASONING_MODEL`。快速模型用于问题规划和检索辅助，推理模型用于工具选择与最终研究回答；留空时均回退到 `LLM_MODEL`，状态接口会显示是否发生回退。模型名必须是 `/models` 实际返回的 ID。当前模型服务如果只提供 Flash 模型，代码不会虚构或自动选择不存在的推理模型。

固定问答评测集位于 `config/qa-benchmark.json`。服务启动后运行 `python scripts/evaluate_qa.py --config config/qa-benchmark.json --label local-agent`，可保存事实覆盖、时间范围、引用可打开率、反证、因果边界、工具成功率、重试、上下文压缩与延迟结果；切换到 PiChat 配置后使用不同 `--label` 再跑，可在同一问题集上比较，而不是仅比较回答篇幅。

“分析资产影响”可生成当前主题/筛选/快照范围内的有条件资产与情景解释并保存版本；模型判断始终待研究员核验。无模型或无证据时，资产矩阵显示研究规则及证据不足，不机械输出涨跌。

## MCP 新闻源

在页面“数据源 → MCP 新闻源配置”填写名称、HTTP 地址和 Authorization。凭据只保存在本机且已被 Git 忽略的 `data/mcp-config.json`，后端接口和页面都不会回显原值。保存后可测试连接并发现工具，再按主题、研究维度和日期检索新闻。

MCP 结果会先保存原始响应，再转换成统一记录并直接生成新快照。页面统一标记为“MCP 导入，待核验”，保留原始链接、发布时间、采集时间和服务返回的稳定 ID；MCP 内容不会被当作系统指令执行。

当前实现支持 Streamable HTTP JSON-RPC，并优先调用 `knowledge_search`。更换其他 MCP 服务时，该服务需要提供兼容的检索工具或在 `backend/mcp_news.py` 增加字段适配。

## 华泰宏观内部消息源

项目可通过内部 HTTP 接口每天三次采集宏观群消息，并在本地 SQLite 中稳定去重。真实账号和密码只写入本地 `.env`；参考 `.env.example` 中的 `HT_MACRO_*` 配置。`start.bat` 会同时启动长驻消息服务，服务启动后立即补采，之后按北京时间 `08:00、14:00、20:00` 抓取消息列表，并每 27 分钟刷新访问令牌。运行状态可通过 `GET /api/internal-messages/status` 查看。

外部 Agent 使用原有 GeoAgent MCP 地址和 Bearer Key，通过以下只读工具访问本地数据：

- `geo_internal_message_search`：按关键词、日期、群聊显示名、标签和文本分类检索短摘要，每页最多20条；
- `geo_internal_message_get`：读取单条标准化消息；
- `geo_internal_message_stats`：读取记录量、最近消息、最近采集批次与分类统计。

MCP 不返回上游账号、访问令牌、群聊内部 ID、发送人内部 ID、接收人内部 ID 或本地数据库路径。消息及上游 AI 摘要仍属于待核验材料。
使用时应先搜索短摘要，再对少量相关ID读取详情；不要分页遍历全库并把全部正文同时提交给模型。

## 新爬虫扩展

在任何子项目下输出 `output/geo-v6.json`，使用 `geo.v6/1` 协议。参见 [接入协议](docs/adapter-contract.md) 及 `examples/geo-v6.example.json`。示例是协议测试数据，默认不导入真实快照。

项目当前内置 26 个采集器的统一注册表（原有 25 个，后新增俄乌相关民调采集器）。证券时报采集器固定读取要闻、新闻和 7×24 快讯，补充美伊冲突、胡塞武装、霍尔木兹海峡、俄乌冲突及资产传导报道；五个俄乌资产传导采集器使用俄罗斯央行与联邦国库局、GIE AGSI、USDA PS&D、乌克兰国家银行、Eurostat 与欧盟委员会等固定渠道。常用命令：

```powershell
python scripts/collect.py --list
python scripts/collect.py --id xinhua-monitor
python scripts/collect.py --topic ukraine
python scripts/collect.py --all
.venv/bin/python -m scripts.collector_service --once --auto-import --retry-attempts 2
.venv/bin/python -m scripts.collector_service_config --platform macos --output data/service-config/com.geoagent.collector.plist
.venv/bin/python -m scripts.collector_service_ctl status --platform macos --dry-run
# 明确执行安装/卸载前，先用 --dry-run 核对命令
.venv/bin/python -m scripts.collector_service_ctl install --platform macos --dry-run
.venv/bin/python -m scripts.collector_service_ctl uninstall --platform macos --dry-run
# Windows PowerShell：在 Windows 环境执行
python -m scripts.collector_service_config --platform windows --output data/service-config/GeoAgent-Collector.xml
```

其中欧洲天然气库存和乌克兰粮食供需的官方接口需要免费 API Key。在 `.env` 设置 `GIE_API_KEY` 与 `USDA_API_KEY`；密钥缺失时采集器会返回“需要配置”，不会用零值或历史缓存冒充本次数据。其余新增来源无需密钥。可分别执行：

```powershell
.\.venv\Scripts\python.exe -m scripts.collect --id russia-financial-pressure-monitor
.\.venv\Scripts\python.exe -m scripts.collect --id eu-gas-storage-monitor
.\.venv\Scripts\python.exe -m scripts.collect --id ukraine-grain-monitor
.\.venv\Scripts\python.exe -m scripts.collect --id ukraine-external-sector-monitor
.\.venv\Scripts\python.exe -m scripts.collect --id eu-ukraine-flow-monitor
```

完整运行数据保存在 `data/collector-output` 并由 Git 忽略；`examples/demo-data` 是克隆仓库后的有限演示快照。公开发布前运行 `python scripts/audit_repository.py`，检查环境文件、疑似密钥、个人绝对路径和超大文件。本项目尚未选择开源许可证，公开仓库前需由仓库所有者确定。

## 验证

```powershell
python -m pytest -q
npm.cmd run build
```

测试覆盖不可变快照、重复导入、修订、缺失/零值、错误舰位、适配器扩展、同源转载、日期范围、引用约束、Agent/模型失败与超时、资产结果隔离、简报下载、MCP 契约、API 演练、本地启动入口契约及采集服务账本/锁/重试/staging/事件 API/手动运行/自动导入门禁/跨平台后台配置与控制/项目私有虚拟环境/日志尾部/来源状态投影。当前全量超过 250 项自动化检查；另可运行 `python scripts/acceptance_browser.py` 完成桌面与窄屏浏览器验收。Pi Chat 契约测试使用模拟接口，真实联调仍需要有效的应用 API Key。

## 研究边界

- 现有材料按关键词归入主题候选，分类不等同语义核验；只在来源确实提供时保留信息。
- 俄乌已接入战线、援助、外交及五个固定渠道量化采集器；数据频率和来源覆盖仍需在页面逐项核对。
- 来源等级沿用爬虫口径；T1 并非所有陈述均已证实。
- 报道日期筛选是检索条件，不是历史可知性重建；可重现研究使用真实冻结快照。
- 月度财政指标是美国整体背景，不能直接归因于美伊或俄乌。
- 已接入原油、黄金、利率、信用、外汇等市场背景序列；它们用于同期市场验证，不单独证明地缘事件的因果关系，也不提供收益预测或配置权重。
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

`market-context-monitor` 下载 FRED 的高收益债及投资级债期权调整利差、十年期盈亏平衡通胀率、欧元兑美元历史观测，写入 `output/geo-v6.json`。它已注册为启用的采集器，可随统一采集任务运行，也可单独执行 `python scripts/supplement_market.py`；请求失败不会替换上一份成功数据包。原始 CSV 保留在该目录，冻结快照归档包含完整数列的 JSON。

利差从百分比换算为基点。不同序列最新日期不同，分析逐条保留比较日期。ICE 信用指数仅限内部研究，公开分发前需核对来源授权。民调新闻不直接转成可比支持率；还需要调查日期、机构、样本、题目和选项。

资产页面与简报提供透明的规则研判，不冒充模型分析或冲突因果证明。市场方向标签与条件传导分别展示，数据缺口保留在每类资产详情中。搜索支持名称、单位及统计口径；经济、军事、动态按已接入数据归类，民调目前展示结构化缺口。
