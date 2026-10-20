# GeoAgent 独立 MCP → PiChat 实施账本

版本：v1.0

创建日期：2026-10-03（北京时间）

状态：A3 本地 Gate 已完成；已完成 PiChat 装配探针，但按用户修正，项目级接入尚未作为正式验收前置完成；此前 B2 对话仅作探索性验证，正式业务验收暂停

关联文档：

- [接入分析](./01-GeoAgent-PiChat-MCP-接入分析-20261003.md)
- [构建计划](./02-GeoAgent-PiChat-MCP-构建计划-20261003.md)

## 1. 目标与硬边界

目标：先独立完成 GeoAgent MCP Server，再在 MCP 前置 Gate 通过后接入 PiChat Agent。

运行链路：

```text
PiChat Agent Runtime
        ↓（后置阶段 B）
GeoAgent MCP Server /mcp
        ↓
GeoAgent business/store
```

LCF/Plane 仅作为参考材料，不是本项目 MCP 的运行时依赖。当前只做只读能力，不实现采集执行、快照切换、简报写入或 Plane 写入。

## 2. 阶段账本

| 阶段 | 内容 | 状态 | 进入条件 | 退出证据 |
| --- | --- | --- | --- | --- |
| A0 | 合同、认证、scope、审计和验收 fixture 冻结 | 已完成（首版） | 已完成代码/数据审计 | SDK、协议、scope、错误码和测试边界已冻结 |
| A1 | 独立 Streamable HTTP MCP Server 骨架 | 已完成 | A0 决策冻结 | `/mcp`、`/healthz`、启动脚本、官方 Client smoke |
| A2 | v1 只读工具和协议测试 | 已完成（11/11 工具） | A1 可启动 | 工具 live 调用、provenance、分页和错误测试 |
| A3 | 独立 MCP 前置 Gate | 已完成（本地范围） | A2 通过 | 本地 endpoint、dev credential、工具目录、live Gate 报告齐备 |
| B0 | 当前 GeoAgent 项目接入 PiChat 的项目级连接与身份闭环 | 待完成 | A3 本地通过 | 项目 endpoint/身份映射、项目级调用回执、数据 provenance 和错误边界在 PiChat 侧可追溯 |
| B1 | PiChat site connection、credential、工具白名单 | 已完成（本地、指定 Agent） | A3 本地通过 | 本地站点注册、Secret Store credential binding、11 工具目录和 Agent 可见性 |
| B2 | 基于当前项目接入的 PiChat Agent 真实只读调用 | 暂缓（此前仅探索性调用） | B0、B1 通过 | 新会话中 Agent 通过项目连接调用业务 MCP，并完成三类业务场景验收 |
| C | 性能、稳定性、泄露和重启验收 | 未开始 | B2 通过 | P95、错误矩阵、脱敏和恢复报告 |

原则：本地 A3 已通过，但必须先完成 B0 项目级接入，再进行 B2 Agent 业务验收；在 B0/B1/B2 正式证据齐备前，不报告“本项目已接入 PiChat”。生产部署安全不作为本项目当前阻塞条件。

## 3. 已冻结决策

- MCP 传输：官方 Python MCP SDK `mcp>=1.30,<2` + Streamable HTTP；暂不追随 2.x API 迁移。
- 独立服务：建议 `scripts/start_mcp.py`，默认 `127.0.0.1:8401/mcp`，健康检查 `/healthz`。
- 认证：A 阶段先实现 Bearer credential 接口；本地 dev token 仅允许 loopback；生产使用短期/OIDC JWT；token 只存 hash，支持过期/撤销。
- scope：GeoAgent 自有 `principal_id`、`agent_id`、`geo_workspace_id`、allowed snapshot/topic/dimension/tool、`read_only`；默认拒绝。
- 输出：统一 `ok/request_id/tool/data/provenance/warnings/audit_event_id` envelope。
- 数据边界：冻结快照与 live collector 状态分离；`geo_collector_status` 必须标注 `live`。
- PiChat：仅在 A3 后由服务端 `site:` MCP connection 接入；浏览器不持有 token。

## 4. 施工任务

### 已完成：A0/A1

- [x] 选择并锁定 Python MCP SDK 依赖版本，更新依赖和启动方式。
- [x] 建立 `backend/geo_mcp/` 模块边界。
- [x] 建立工具合同、错误码、认证上下文和 provenance 类型。
- [x] 实现 `/healthz` 与 `/mcp` 最小进程入口。
- [x] 增加启动脚本和最小启动/关闭测试。

### 已完成：A2

- [x] 按工具目录实现 v1 只读工具：snapshot、workspace summary、evidence、metric、analysis、brief、collector status 共 11 个工具。
- [x] 增加首版 scope allow/deny、分页上限和服务错误测试。
- [x] 用工作快照和历史快照做直接 MCP live 验证，并区分 snapshot/live provenance。
### 当前：A3

- [x] 建立 hash-only 多 principal scope 文件格式，支持过期与撤销。
- [x] 增加多 principal、revoked、expired credential 单元测试。
- [x] 增加可重复执行的 `scripts/verify_mcp_gate.py`，使用官方 MCP Client 验证 health、握手、11 工具、快照、业务维度、live collector、错误和脱敏。
- [x] 对当前 `127.0.0.1:8401/mcp` 执行 A3 本地 live Gate，working snapshot、5 个指标、5 条证据和 11 个工具均通过。
- [x] 用本地 dev credential 和 loopback endpoint 完成 A3 Gate；本地 Gate 允许 broad allowlist，不把生产级网络安全作为退出条件。
- [x] 形成 A3 本地通过报告（见 `04-GeoAgent-MCP-A3-Gate-验收报告-20261003.md`）。

### 已完成：B1 本地 PiChat 接入

- [x] PiChat `site:` registration：本地站点连接 `geoagent` 指向 `http://127.0.0.1:8401/mcp`。
- [x] 使用 PiChat Runtime 自有 Secret Store 保存本地开发凭据；浏览器不持有凭据。
- [x] PiChat 重新检测发现 GeoAgent 的 11 个工具，站点工具策略为显式常驻 11 项。
- [x] 已绑定并实测 `研报合规审计` Agent；未将“指定 Agent 已通”扩大解释为任意 Agent 自动可用。
- [ ] 主研究 Agent（`kc-research-assistant` / `深度投研专家`）当前 composition 明确为 `mcp: false`，是否改为本地 MCP Agent 需单独决策，不作为本次 B1 完成条件。

### 已完成：B2 本地真实调用

- [x] 通过 PiChat 新建会话，要求只使用 GeoAgent 本地 MCP，实际调用 `geo_snapshot_list`、`geo_workspace_summary`、`geo_metric_catalog`。
- [x] 返回工作快照 `0b18ddf4468552d911eba06e`、2 个主题及其维度/记录数、指标目录前 5 项（总计 70 项）。
- [x] PiChat 会话明确回执“全部结果来自 MCP 回执，未使用任何网页搜索”。

### 后置：C

- [ ] 性能、脱敏、重启和历史快照复现验收。

## 5. 证据与测试记录

### 2026-10-03：开工前审计

- GeoAgent 当前 REST 服务可达：`http://127.0.0.1:8011`。
- 当前 OpenAPI 没有 `/mcp` 路径，说明 MCP 尚未实现。
- 现有依赖包含 FastAPI/uvicorn/httpx/pytest；已新增 `mcp>=1.30,<2`。
- 当前 working snapshot：`0b18ddf4468552d911eba06e`。
- 当前最新 snapshot：`43836b35386004368b320086`。
- 现有业务能力可复用，但 REST 无远程用户认证、GeoAgent scope 和 MCP 审计合同。
- `mcp>=1.30,<2` 已加入 requirements，当前虚拟环境安装 `mcp==1.30.0`；FastMCP import、ASGI route 和真实进程 smoke 已完成。

### 测试记录

| 时间 | 命令/场景 | 结果 | 证据/备注 |
| --- | --- | --- | --- |
| 2026-10-03 | `curl http://127.0.0.1:8011/api/status` | 通过 | 本地 REST 状态可读 |
| 2026-10-03 | `curl http://127.0.0.1:8011/openapi.json` 检查 `/mcp` | 通过 | 当前不存在 MCP 路径，作为实施前基线 |
| 2026-10-03 | Markdown diff/空白检查 | 通过 | 计划与账本文档无空白错误 |
| 2026-10-03 | 安装 `mcp==1.30.0`、FastMCP import、ASGI route smoke | 通过 | `/mcp`、`/healthz` 已建立 |
| 2026-10-03 | `pytest -q tests/test_geo_mcp_server.py` | 通过（3 passed） | 官方 MCP Client initialize/list/call、Bearer 和 tool allowlist |
| 2026-10-03 | `python -m compileall backend/geo_mcp scripts/start_mcp.py` | 通过 | A1 Python 语法基线 |
| 2026-10-03 | `pytest -q tests/test_geo_mcp_server.py`（指标工具扩展） | 通过（3 passed） | 官方 MCP Client 实际调用 `geo_metric_catalog`/`geo_metric_series`，验证目录分页和 `max_points` 上限 |
| 2026-10-03 | `pip check` | 通过 | mcp 依赖无冲突 |
| 2026-10-03 | `pytest -q` | 通过（151 passed） | MCP 新增测试与既有采集/快照/研究回归全部通过 |
| 2026-10-03 | `pytest -q`（新增 legacy token 绕过防护） | 通过（152 passed） | scope 文件与 legacy token/hash 同时配置会启动失败，避免绕过 principal scope |
| 2026-10-03 | 真实独立进程 MCP Client probe | 通过 | `127.0.0.1:8401/mcp`；列出 11 工具；working snapshot `0b18ddf4468552d911eba06e`；summary/live collector 调用成功 |
| 2026-10-03 | collector live 事件脱敏断言 | 通过 | 不返回 `payload` 原文、`DEMO_KEY` 或 `/Users/` 本地路径 |
| 2026-10-03 | snapshot/topic/dimension scope MCP 测试 | 通过 | 越权历史 snapshot 和未授权 dimension 均返回 `GEO_SCOPE_DENIED` |
| 2026-10-03 | 多 principal scope 文件测试 | 通过 | hash-only credential、`revoked`、`expires_at` 均返回预期认证结果 |
| 2026-10-03 | 未建立 snapshot 映射测试 | 通过 | 返回独立错误码 `GEO_SCOPE_MAPPING_REQUIRED` |
| 2026-10-03 | 非 loopback 开发 token 防护测试 | 通过 | 非 loopback host 无 scope 文件时拒绝启动 |
| 2026-10-03 | scope example 加载探针 | 通过 | `GEOAGENT_MCP_REQUIRE_SCOPE_FILE=true` 可加载 hash-only 多 principal 配置 |
| 2026-10-03 | `GEOAGENT_MCP_VERIFY_TOKEN=... .venv/bin/python scripts/verify_mcp_gate.py` | 通过 | 官方 MCP Client；health/错误 Bearer 拒绝；11 工具；6 个可见快照；working snapshot `0b18ddf4468552d911eba06e`；5 个指标、5 条证据；live collector；结构化错误和脱敏断言 |

## 6. 变更日志

| 时间 | 阶段 | 变更 | 证据 |
| --- | --- | --- | --- |
| 2026-10-03 | A0 | 创建实施账本，确认 MCP 必须先于 PiChat 完成 | 本账本、构建计划 v0.4 |
| 2026-10-03 | A0/A1 | 锁定 `mcp>=1.30,<2` 并安装当前版本 `1.30.0` | `requirements.txt`、`.venv` |
| 2026-10-03 | A1/A2 | 建立独立 `/mcp` 服务和 5 个只读工具，完成官方 Client smoke | `backend/geo_mcp/`、`scripts/start_mcp.py`、`tests/test_geo_mcp_server.py` |
| 2026-10-03 | A1/A2 | 完成首轮 Bearer、scope、分页、统一 envelope、provenance、审计事件和 3 个协议测试 | `backend/geo_mcp/{auth,scope,contracts,provenance,audit}.py`、`tests/test_geo_mcp_server.py` |
| 2026-10-03 | A2 | 新增 `geo_metric_catalog` 和 `geo_metric_series`，复用 `research.metrics`，限制目录/序列输出并补官方客户端真实调用 | `backend/geo_mcp/server.py`、`tests/test_geo_mcp_server.py` |
| 2026-10-03 | A2 | 补齐 `geo_analysis_view`、`geo_brief_list/get`、`geo_collector_status`，采集事件只返回脱敏 bounded projection，并标注 `live` provenance | `backend/geo_mcp/server.py`、`tests/test_geo_mcp_server.py` |
| 2026-10-03 | A2 | 修复历史报告状态被 `historical` ledger 占位记录覆盖的问题；真实生命周期状态才覆盖兼容报告 | `backend/collector_monitor.py`、`tests/test_backend.py` |
| 2026-10-03 | A2 | 全量回归与独立进程 live 探针通过：11 个工具、working snapshot、历史 snapshot、workspace summary、collector status | `pytest -q`（151 passed）、`127.0.0.1:8401/mcp` |
| 2026-10-03 | A3 | 建立 Gate 验收报告草案，明确已验证项与未通过的生产边界；不进入 PiChat | `04-GeoAgent-MCP-A3-Gate-验收报告-20261003.md` |
| 2026-10-03 | A3 groundwork | 增加 hash-only 多 principal scope 文件加载，支持 credential 过期/撤销 | `backend/geo_mcp/auth.py`、`config/geoagent-mcp-scope.example.json`、`tests/test_geo_mcp_server.py` |
| 2026-10-03 | A3 groundwork | 非 loopback host 禁止无 scope 文件的明文开发 token，支持 `GEOAGENT_MCP_REQUIRE_SCOPE_FILE=true` | `backend/geo_mcp/auth.py`、`tests/test_geo_mcp_server.py` |
| 2026-10-03 | A3 local Gate | 增加独立的可重复 live 验收脚本，不把 `healthz` 或手写 HTTP 探针当作 MCP 合同证明 | `scripts/verify_mcp_gate.py`；当前 live Gate passed |
| 2026-10-03 | A3 security | scope 文件与 legacy Bearer/hash 同时存在时拒绝启动，消除旧 token 绕过 scope 的歧义路径 | `backend/geo_mcp/auth.py`、`tests/test_geo_mcp_server.py` |
| 2026-10-03 | A3 → B1 | 根据验收边界调整，A3 以本地 Gate 为完成标准；生产 secret provider、TLS、跨主机网络和轮换运维移出当前阻塞项 | 本账本 v0.6、A3 报告 v0.3、构建计划 |
| 2026-10-03 13:50（北京时间） | B1 | 将 GeoAgent 注册为 PiChat 本地 site MCP，写入本地 Secret Store，显式暴露 11 个只读工具，并完成站点重新检测 | PiChat 数据连接页显示“检测通过/发现 11 个工具”；runtime `data/mcp/site-servers.json`、`catalogs/geoagent.json` |
| 2026-10-03 13:58（北京时间） | B2 | 在 PiChat `研报合规审计` Agent 会话中完成真实中文 MCP 调用；返回工作快照、主题覆盖和指标目录，未使用网页搜索 | PiChat 会话回执列出 `geo_snapshot_list`、`geo_workspace_summary`、`geo_metric_catalog`；耗时约 20 秒 |
| 2026-10-03 15:02（北京时间） | B2 ordinary Chat | 在 `http://127.0.0.1:9172/chat?view=ordinary` 普通对话页发起第二轮真实调用；返回工作快照、5 项资产关联、3 项情景分析，未使用网页搜索 | PiChat 普通 Chat 回执列出 `geo_snapshot_list`、`geo_analysis_view`；工具链 5 步约 3.4 秒 |
| 2026-10-03 15:10（北京时间） | B2 semantic acceptance | 以真实业务问题“本周哪些指标发生变化？当前判断有哪些反证？还缺什么数据？”验收用户意图到业务 tool 的匹配；Agent 先拆解意图，再实际调用 `geo_snapshot_list`、`geo_metric_catalog`、`geo_metric_series`、`geo_analysis_view`、`geo_evidence_search`、`geo_evidence_get`，给出事实/假设/缺口分层回答，未使用网页搜索 | PiChat 普通 Chat 回执；16 步、约 77 秒。成功识别 working/latest 快照、按本周取时序、从 `counter` 与证据检索分层找反证，并从 `missing_data/gaps/freshness` 给出缺口；发现一次 `geo_metric_catalog(limit=70)` 越界后自动按上限 50 分段恢复 |
| 2026-10-04 07:17（北京时间） | B2 Agent assembly recheck | 在 PiChat 管理台核对独立 Agent `geo-research-assistant` 与 GeoAgent API：Agent 已启用、公开可选；有效装配显示 MCP 常驻连接 1 条，明确为 `site:geoagent`，延迟 0 条；应用 API 显示默认运行身份为 `geo-research-assistant` | `/admin/agents/geo-research-assistant?tab=assembly` 显示当前 revision `4da3637b1db49ca641882de3`、MCP 常驻 `1`、`site:geoagent`；API 应用页显示 GeoAgent API 启用且调用数仍为 0，说明配置已落地但外部 API live call 尚未验收 |
| 2026-10-04 07:17（北京时间） | 验收顺序修正 | 根据用户确认，撤销“先在 PiChat Agent 中做正式业务验收”的表述；新增 B0 项目级接入作为 B2 前置，既有 PiChat 对话结果降级为探索性证据 | 账本阶段表与风险项已更新；后续先验证 GeoAgent 项目到 PiChat 的连接/身份/provenance 闭环 |

## 7. 阻塞与风险

- MCP SDK 已暂定锁定为 `mcp>=1.30,<2`，当前安装 `1.30.0`；A1 smoke、`pip check` 已通过。
- GeoAgent 已支持外部 hash-only 多 principal scope 文件；生产 secret provider、轮换/撤销运维流程仍未配置和验证。
- A2 的 11 个工具和 A3 本地 live Gate 已完成；生产 secret provider、TLS、跨主机网络和轮换运维不属于当前本地验收阻塞项。
- 本地 MCP 可使用 dev credential 和 broad allowlist；仍保留只读工具、数据投影、脱敏和 scope 代码，避免把本地便利误写成生产能力。
- B1/B2 的本地 PiChat registration、credential binding、assignment 和 Runtime tool loading 已由指定 Agent 实测通过。
- B2 业务语义验收已通过：用户自然语言中的“本周变化/反证/缺口”能够分别映射到快照口径、指标目录/时序、规则反证/证据检索和缺口字段；Agent 能在 tool 失败（目录 limit 越界）后按 MCP 错误语义修正参数，而不是编造结果。
- 独立 `GeoAgent 地缘数据分析助手` 已完成 Agent 级装配复核：Agent 启用且公开可选，最终生效装配明确包含 1 条 MCP 常驻连接 `site:geoagent`；这证明 MCP 已进入该 Agent 的有效能力面，不再只是站点注册记录。
- `GeoAgent API` 应用已启用并将默认运行身份指向 `geo-research-assistant`，管理台调用数仍为 0；本轮已通过项目内嵌面板完成真实 Agent→MCP→回答闭环，因此剩余的是独立 API 入口计量/路由验收，不阻塞嵌入接入。原始密钥不应粘贴到聊天或账本。
- 用户已明确修正验收顺序：`geo-research-assistant` 不能先在 PiChat 里单独验收；必须先证明当前 GeoAgent 项目已经接入 PiChat，项目级身份、数据边界和回执链路成立后，才启动 B2 三类业务问题验收。
- 语义验收暴露两项必须保留的业务约束：working snapshot 可能落后于 latest snapshot，“本周”不能默认使用 working；“反证”同时包含 `geo_analysis_view.counter` 的规则层和 `geo_evidence_search/get` 的事实层，不能只检索证据文本。此次 live 还验证了第三项：连续批量 tool 调用可能触发平台无进展熔断，Agent 必须停止扩张查询并交付已确证部分及缺口。
- 当前工具描述仍可继续业务化：验收中 Agent 已正确选择工具，但 `geo_metric_catalog` 的 50 条分页上限和 latest/working 选择规则需要进入更明确的 MCP tool 描述/参数 schema，减少一次越界和快照口径歧义。
- `深度投研专家`（`kc-research-assistant`）当前 composition 为 `mcp: false`，因此不能宣称所有 PiChat Agent 或任意 Agent 已自动获得 GeoAgent；如需该主研究 Agent 使用，必须单独打开其本地 MCP 装配并复验。
- C 阶段的性能、脱敏、重启和历史快照复现仍未完成；这些不阻塞当前本地接入结论。

## 8. 更新规则

每完成一个阶段、关键代码变更、测试命令或阻塞变化，必须同步更新本账本的：阶段状态、施工任务、证据/测试记录、变更日志和阻塞风险。最终交付必须能从账本追溯到代码、命令和验收结果。

## 9. 2026-10-04 B0 开工记录

- 已将本账本迁入 GeoAgent 工作区，源账本仍保留在 Plane 文档目录；后续以本文件同步本地施工证据。
- 已核对 PiChat 本地运行时配置：`site:geoagent` 指向 `http://127.0.0.1:8401/mcp`，站点已启用，显式暴露 11 个只读工具；`geo-research-assistant` 的 `composition.yaml` 将该站点作为唯一 MCP 常驻连接。
- 已核对 GeoAgent 嵌入身份闭环：`http://127.0.0.1:8011/api/pichat/embed-identity` 返回短期嵌入身份，Agent 路由为 `geo-research-assistant`，浏览器面板加载 `work-embed/geo-research-assistant` 并显示当前工作快照。
- 已发现项目级 API 与嵌入身份是两条不同能力：当前 GeoAgent 进程只配置了 `PICHAT_EMBED_*`，该密钥可签发嵌入身份但不具备 `chat:invoke`；直接调用 PiChat `/api/v1/chat` 返回 403 scope denied。未将密钥写入代码、浏览器或账本。
- B0 当前只证明身份嵌入可用，尚未证明面板发送消息后的 Agent→MCP→回答闭环。`chat:invoke` 缺失只阻塞备用 API 探针，不是嵌入面板的必需权限；不能据此要求扩大嵌入密钥权限。后续优先沿用户指定的项目嵌入面板验收。
- 新增可重复项目级探针 `scripts/verify_pichat_project.py`：密钥仅从进程环境读取，输出只包含 HTTP 状态、session/agent 摘要、观察到的 `geo_*` 工具名和截断回答预览；当前无 `PICHAT_API_KEY` 时按 `blocked` 退出，不伪造通过。
- 现场业务验证首次暴露真实阻塞：嵌入 Agent 能运行，但本地 legacy principal 未配置 snapshot scope，`geo_snapshot_get`、`geo_workspace_summary`、`geo_metric_catalog` 等快照级工具统一返回 `GEO_SCOPE_MAPPING_REQUIRED`。该问题不是 PiChat iframe、网络或站点注册故障，而是 GeoAgent MCP 本地 principal 的 scope 默认值缺失。
- 按“本地 MCP 不受控访问可接受”的约束修复：loopback 且未使用 scope 文件时，legacy principal 默认允许 `snapshot/topic/dimension=*`；显式环境变量仍优先，非 loopback 仍要求 scope 文件。scope 代码和只读工具边界保留。
- 修复后通过嵌入面板真实业务问题：Agent 先读取快照/指标目录，再调用 `geo_metric_series`，并在部分序列拉取触发平台无进展熔断时如实交付有限结果；回答明确区分“可判定变化”与“无法判定”，未把工具失败或空数据编造成零变化。

### B0 修复后 live 验证

| 时间 | 命令/场景 | 结果 | 证据/备注 |
| --- | --- | --- | --- |
| 2026-10-04 10:12–10:16（北京时间） | GeoAgent 嵌入面板提交“本周哪些指标发生变化？请基于当前快照给出证据和缺口。” | 通过（嵌入链路） | `work-embed/geo-research-assistant` 完成 2 轮 Agent 运行；修复后不再出现 `GEO_SCOPE_MAPPING_REQUIRED`，实际调用 `geo_snapshot_list`、`geo_metric_catalog`、`geo_metric_series`，返回 70 指标目录、快照版本和带 audit 落点的变化结果 |
| 2026-10-04 | 修复后的 loopback 默认 principal | 通过 | 未设置 `GEOAGENT_MCP_ALLOWED_SNAPSHOTS/TOPICS/DIMENSIONS` 仍可读取 7 个快照；显式 `GEOAGENT_MCP_ALLOWED_TOOLS` 继续限制为 11 个只读工具 |
| 2026-10-04 | `GEOAGENT_MCP_VERIFY_TOKEN=… .venv/bin/python scripts/verify_mcp_gate.py --url http://127.0.0.1:8401` | 通过 | health、Bearer 拒绝、initialize/tools、snapshot、workspace、metrics、evidence、analysis、brief、collector live、结构化错误全部通过 |
| 2026-10-04 | `./.venv/bin/pytest -q` | 通过（152 passed） | scope 默认修复未破坏既有显式 scope、越权和非 loopback 防护测试 |

### B0 当日验证

| 时间 | 命令/场景 | 结果 | 证据/备注 |
| --- | --- | --- | --- |
| 2026-10-04 | `curl http://127.0.0.1:8011/api/pichat/embed-identity?agent_id=geo-research-assistant` | 通过 | 服务端签发短期嵌入身份；浏览器加载 `work-embed/geo-research-assistant` |
| 2026-10-04 | PiChat 管理运行时配置核对 | 通过 | `site:geoagent` → `http://127.0.0.1:8401/mcp`；11 个显式只读工具；Agent 唯一 MCP 常驻连接为该站点 |
| 2026-10-04 | 当前嵌入密钥调用 PiChat `/api/v1/chat` | 按预期拒绝 | HTTP 403，`chat:invoke` scope denied；证明嵌入密钥不能冒充项目级 API 密钥 |
| 2026-10-04 | `./.venv/bin/python scripts/verify_pichat_project.py` | 阻塞 | 当前 GeoAgent 工作区未配置 `PICHAT_API_KEY`；探针按 blocked 退出 |
| 2026-10-04 | `./.venv/bin/pytest -q` | 通过（152 passed） | 新增项目探针语法检查及既有 MCP/采集/快照回归 |
| 2026-10-04 | `npm run build` 与四端口 HTTP smoke | 通过 | 前端构建通过；8010/8011/8401/9172 均可达；本地 PiChat 默认地址统一为 `9172` |

### B2 业务场景用例基线（2026-10-04）

- 按菜单建立 8 个可重复业务问题：量化指标、情景推演、数据源、证据台账、风险监测、采集管线监控、资产关联、研究简报；每个用例明确预期工具、答案字段和失败规则。
- 新增机器可读夹具 `tests/fixtures/geoagent_business_cases.json` 与数据侧验证器 `scripts/verify_business_cases.py`；验证器只证明 MCP 数据路径，不冒充 PiChat Agent 语义验收。
- 新增业务测试说明 `docs/agent/04-GeoAgent-PiChat-业务场景测试用例-20261004.md`，固定“数据层 → 路由层 → 回答层”三层验收及 working/latest、counter/evidence、live/historical、空简报等边界。

| 时间 | 命令/场景 | 结果 | 证据/备注 |
| --- | --- | --- | --- |
| 2026-10-04 | `GEOAGENT_MCP_VERIFY_TOKEN=... .venv/bin/python scripts/verify_business_cases.py` | 通过（8/8） | 11 个只读工具均可满足菜单用例所需字段；working snapshot `0b18ddf4468552d911eba06e`；collector provenance 为 `live` |
| 2026-10-04 | PiChat Agent live：`snapshot-change` | 通过 | 嵌入面板真实调用 `geo_snapshot_list`、`geo_metric_catalog`、`geo_metric_series`；回答区分可判定变化、无可比历史和快照口径 |
| 2026-10-04 10:34–10:37（北京时间） | PiChat Agent live：`counterevidence` | 通过（部分取数、正确降级） | Agent 实际调用 `geo_analysis_view`、`geo_evidence_search`、`geo_evidence_get`；区分规则层 `counter` 与新闻证据 `evidence_ids`，并明确 5 条正文已取、escalate/ease/commodity 其余正文被无进展熔断拦截，未把未取到写成无数据 |
| 2026-10-04 10:36–10:40（北京时间） | PiChat Agent live：`missing-data` | 通过（部分取数、正确降级） | Agent 实际按指标、证据、覆盖范围、时效四维组织；识别 3 条断裂指标链、68/70 缺前值、15 条正文未取、2 topic/8 维度部分覆盖、working/latest 落差和逐源失败；明确“未取到”不等于“无数据” |
| 2026-10-04 | GeoAgent 嵌入 Agent 主题一致性修复 | 通过 | iframe URL 显式传入 `theme=dark`；刷新 8010 后实际 URL 含该参数，PiChat work-embed 面板由浅色切换为与 GeoAgent 宿主一致的深色 UI；`npm run build` 通过 |

### 2026-10-04 嵌入页“本轮进展”右侧面板修复

- 根因：`TodoTimelineCard` 点击“本轮进展”会通过 `openResearchProgress` 更新 `useUiStore.workbenchBySession`，但 `AgentEmbedPage` 的嵌入布局未挂载 `WorkbenchSidePanel`；状态已变更而没有渲染宿主，故用户表现为点击无响应、右侧打不开。
- 修复：在 `packages/web/src/pages/AgentEmbedPage.tsx` 的嵌入 flex 布局中挂载 `WorkbenchSidePanel`，保持普通 Chat 与嵌入 Agent 的工作面板能力一致。
- 验证：PiChat web chat 构建、TypeScript 检查通过；`AgentEmbedPage.runtime.test.tsx` 16/16 通过，新增回归断言确认嵌入会话挂载工作面板；GeoAgent 8010 页面可加载新构建，嵌入 iframe 中“本轮进展”按钮和 `4/8` 状态可见。当前 CUA 对跨域 iframe 的 click 落点仍返回不可用，故已完成代码/测试/加载验证，浏览器最终展开视觉证据需用户刷新后点击确认。

### 2026-10-04 进展入口可见性修复

- 现象：长回答滚动到正文后，消息流中的 `本轮进展` 卡片仍存在但离开视口，用户无法快速定位。
- 修复：折叠态工作面板在检测到本会话存在执行进展时，显示带业务语义的固定入口“本轮进展”；点击直接打开 `progress` 视图，不再只有无语义的折叠图标。
- 验证：`WorkbenchSidePanel.test.tsx` 与 `AgentEmbedPage.runtime.test.tsx` 共 40 项通过；PiChat web chat 构建及 bundle size gate 通过。

### 2026-10-04 13:30–13:36（北京时间）三条业务问题 live 回归

| 输入 | 轮次/状态 | 实际 MCP 证据 | 业务回答核对 |
| --- | --- | --- | --- |
| `本周哪些指标发生变化？` | 第 6 轮，已研判 | Agent 先读取已有快照口径，再通过 `site:geoagent` 调用指标处理工具；回执明确 latest 快照 `841ac1061f43bcdfd7b83e51`、70 个指标、41 个有可比前值、29 个无前值（其中 3 个完全无值） | 与右侧面板的资产方向一致：利率债“债价承压”、信用债“信用风险溢价扩大”；回答给出 34 个确认变化指标，并明确本周实际可观测窗口为 09-28–10-03、主要集中在 10-01→10-02；布伦特由旧 working 快照的 -8.22% 更正为 latest 的 -0.06 |
| `当前判断有哪些反证？` | 第 7 轮，已研判（部分取数） | 实际调用 `geo_analysis_view`、`geo_evidence_search`、`geo_evidence_get`；规则层与新闻证据层均返回，补最后 8 条时触发平台“无进展熔断” | 回答区分三类情景/五类资产的预置 counter 与已取新闻证据；指出“缓和收束”的意向声明与实际行动未下降构成唯一量化反证触发，并以市场数据（VIX 下行、HY OAS 走阔等）对升级叙事做交叉反证；未把未取正文写成“无数据” |
| `还缺什么数据？` | 第 8 轮，已研判 | 会话来源仍为 `site:geoagent`（healthy，11 工具）；基于 latest 的指标目录、证据、覆盖和时效字段组织缺口 | 回答明确四层缺口：指标 29/70 无前值；证据正文 8 条未取且 0 条已核验；仅 2 个主题、每资产 6 条证据的覆盖不足；指标止于 10-02、航运类滞后至 09-27，并区分“未取到”与“无数据” |

结论：三条自然语言业务指令均能在嵌入面板中形成新轮次并关联到业务化 MCP 工具；第 6/7/8 轮均能产出可追溯回答。平台无进展熔断仍会在批量补数时触发，Agent 当前行为符合降级要求（交付已确证部分、列出缺口、不编造）。

### 2026-10-04 13:39（北京时间）后续菜单验收记录修正

- 尝试执行 `risk-overview`（风险监测）时，独立 Playwright 调试上下文的 `/auth/ws-ticket` 连续返回 HTTP 401；同一时刻用户当前 8010 嵌入面板仍可正常显示并完成已有轮次。
- 该结果判定为 **自动化上下文失效**，不是 PiChat 产品会话、GeoAgent MCP 或业务数据失败；风险监测问题未成功提交，因此不计入用例通过。
- 后续从当前可用嵌入会话重新提交 `risk-overview`，再执行证据台账、采集管线、资产关联和研究简报的剩余验收。

### 2026-10-12 页面连接配置、MCP Key 管理与统一启动

范围：用户授权将 PiChat 连接和 MCP Key 管理放到项目页面，配置持久化、保存即生效，MCP 随项目启动；本轮完成本地验收，未发布外网。

实现：

- 顶部新增「连接配置」深色面板，支持 PiChat 服务地址、Agent ID、用户手机号、产品入口 entry、产品用户 ID、应用 Key。`/chat` 地址自动归一到根地址；应用 Key 留空保留原值，读取接口不回传原文；保存后嵌入面板重新连接。公开配置只包含 iframe 所需的根地址、Agent ID、产品用户 ID，不包含手机号/应用 Key。
- 配置存于 `GEO_DATA_DIR/research.sqlite3`；页面保存优先于进程环境变量。实际通过页面保存本地已有配置，随后不注入 PiChat 临时环境变量重启，身份签发仍然成功。
- 页面支持 MCP Key 创建、仅一次显示原文、列表和确认撤销。Key 使用随机 256-bit token，数据库只存 SHA-256 哈希；授权本项目全部只读工具、快照、主题和维度，适用于可信 Agent，不宣称细粒度租户隔离。撤销按请求即时检查。
- 主后端统一提供 `/mcp` Streamable HTTP 和 `/healthz`，主 lifespan 启停 MCP SDK session manager；本地启动脚本无需单独拉起 8401。独立 MCP 调试入口保留兼容，主项目路径位于前端静态回退之前。
- 公网管理必须提供环境变量 `GEOAGENT_SETTINGS_TOKEN`，页面输入口令后才能管理；未配置时只允许无转发头的 loopback 管理请求。该机制不是完整用户登录，项目访问及身份签发仍需部署入口保护。PiChat 应用 Key 在服务端数据库存储（未加密），须保护持久卷、文件权限及备份。

自动化与 live 证据：

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 后端完整回归 | 通过 | `pytest -q`：165 项通过；新增连接保存/保留密钥/脱敏、地址校验、公网管理鉴权、同源防护、真实 MCP initialize/list/call/revoke、动态身份签发路由测试 |
| 前端构建 | 通过 | `npm run build`，TypeScript 与 Vite 构建完成；`git diff --check` 通过 |
| 页面配置保存 | 通过 | Playwright 在 8010 输入 `http://127.0.0.1:9172/chat` 并点击保存；后端归一为根地址，保留已有应用 Key；重启后仍可连接 |
| MCP 页面创建与业务取数 | 通过 | 页面创建临时 `live-verification-20261012` Key；通过同域 `/mcp` initialize=200、tools/list=11 工具、geo_snapshot_list=200/ok=true/10 快照 |
| MCP 页面撤销 | 通过 | 点击撤销并确认，同一原始 Key 再请求 tools/list 返回 401；测试 Key 已撤销，包含一次性 Key 的临时快照已清理 |
| PiChat 真实连接 | 通过 | 页面「验证 PiChat 连接」身份签发 200；`/auth/ws-ticket` 200；实际 `/embed-ws` WebSocket 建连成功；嵌入页面显示历史会话与输入框，无连接遮罩 |
| 公网 Host 的协议与管理 | 测试通过，未外网部署 | TestClient 使用非 loopback 客户端及公网 Host：无管理口令拒绝、有口令同源创建成功、带 MCP Key 调用成功；不能替代 Sealos 部署验收 |

live 中发现并修复：Vite 字符串代理默认 `changeOrigin=true`，使保存请求的 Host 被改写，触发同源防护误报 403；改为显式 `changeOrigin=false`。同时原后端写操作仅允许固定本地 Origin，已兼容项目外网同源地址，管理接口继续独立检查口令与同源关系。

视觉证据（不包含密钥原文）：`output/playwright/integration-settings-20261012.png`、`output/playwright/pichat-connected-20261012.png`。

外网交付前置（未执行）：发布本轮代码；设置管理口令和持久化 `GEO_DATA_DIR`；页面填写 `https://chat.lhotsetech.com` 及该环境 PiChat 应用 Key/用户/entry；创建 MCP Key 并绑定到目标 Agent。预期 MCP 地址 `https://geoagentd40d.sealosgzg.site/mcp`，需确保网关允许 Bearer 协议访问且 PiChat 允许该嵌入来源。本地连接配置保留 9172，未误改为外网环境。本轮没有提交新的业务问答，不将历史会话内容计为本轮 Agent 分析结果验收。

### 2026-10-12：GitLab main 外网发布（部署与 MCP 通过，PiChat 配置阻塞）

- 用户确认沿用 GitLab main → 受保护生产 tag → 手动 deploy 的 Sealos 发布链路，不使用本地替代部署。
- 发布配置将 PiChat 根地址设置为 `https://chat.lhotsetech.com`，保留生产既有应用密钥、用户和 entry，持久化配置优先于环境变量。
- 网关项目页面保留 Basic 登录；仅 `/mcp` 使用后端强制 Bearer。已登录的隔离 loopback 网关可管理连接配置及 Key（显式 opt-in，身份头覆盖，应用端口隔离，服务禁用代理客户端地址改写）。
- 发布任务增加两个公网域名的真实 TLS / MCP initialize、tools/list、geo_snapshot_list 验证；测试 Key 使用后撤销，不落日志或产物。
- 发布前后端回归 192 项通过，前端构建通过。外网部署、协议与嵌入结果待流水线和浏览器验收，不预先记为完成。

发布与 live 结果：

| 项目 | 结果与证据 |
| --- | --- |
| GitLab main | 已推送 `eaed746f818b452e7088f9b5477911f7bca8cd62`；生产 tag `geoagent-prod-v1.0.5` |
| 生产验证与部署 | 流水线 [5272](https://gitlab.lz.internal/yujunjun/GeoAgent/-/pipelines/5272)；verify [27702](https://gitlab.lz.internal/yujunjun/GeoAgent/-/jobs/27702) 与 deploy [27703](https://gitlab.lz.internal/yujunjun/GeoAgent/-/jobs/27703) 均成功；CI 191 passed / 1 skipped；镜像 digest `sha256:f91438d682979b0426278358fb1bd4e8947ed2516f1d1e8fec31241eeeada069` |
| 生产持久配置 | 发布任务成功设置 `https://chat.lhotsetech.com`，保留生产既有 Key / 用户 / entry；Chrome 已登录页面「连接配置」实际读取根地址及 `geo-research-assistant`，无需额外管理口令 |
| 公网 MCP | `https://geoagentd40d.sealosgzg.site/mcp` 和 `https://geoagent.lhotsetech.com/mcp` 均通过真实 TLS 请求：匿名 401、initialize 200、tools/list 200（至少 11 工具）、geo_snapshot_list 200 / ok=true |
| 测试凭证清理 | 发布任务创建的 release-live Key 已撤销；Chrome 页面列表显示已撤销；原文未写入日志和发布产物 |
| 页面 live | 用户在 Chrome 完成现有 Basic 登录后，实际点击「智能问答」「连接配置」，页面及业务数据正常、配置读取成功；无前端框架错误遮罩。Chrome 自动化仅原生可访问，未取得浏览器控制台，故不宣称 console 无错误 |
| PiChat 身份签发 | **未通过**：实际嵌入显示 `PiChat 身份签发失败: HTTP 401`；当前生产 Key 不被目标 PiChat 身份接口接受，未覆盖或重签凭证 |
| PiChat iframe | **未通过**：Chrome 显示连接被拒绝；实测 `/work-embed/geo-research-assistant` 返回 HTTP 200，但 `Content-Security-Policy: frame-ancestors` 仅含 self、StockLink 及本地地址，不含两个 GeoAgent 域名 |

下一步必须由生产 PiChat 配置完成：提供该环境有效应用 Key（在 GeoAgent 页面填写即可，无需重新发布），允许两个 GeoAgent HTTPS 父域嵌入，并确认该应用 entry / Agent / 用户权限；之后重跑身份 → iframe → WebSocket → 新业务问答。当前不宣称外网 Agent 已连通，也不将 MCP 协议通过等同于 Agent 业务验收。未修改其他项目的生产访问策略。

### 2026-10-12：生产 PiChat 专用应用配置接续（尚未提交）

- 用户澄清已有 GeoAgent 智能体，授权创建独立 API 嵌入应用及专用 Key，仅允许两个 GeoAgent HTTPS 来源，不修改 StockLink / LCF 应用。
- 已进入生产 API 管理页，列表当前仅显示 StockLink 与 LCF；新建表单可切换到产品嵌入工作台，支持命名入口、精确 Origin、面板 Token TTL（默认 1800 秒）。
- 管理会话解锁后，表单未提交即再次退回 `/chat`；本轮未创建应用、未签发密钥、未更改 GeoAgent 生产凭证。该现象不能判定为 GeoAgent MCP 故障，也未确认 PiChat 会话退出的根因。
- Chrome 的独立管理会话仍要求密码验证，待用户在 Chrome 解锁后改用原生 Chrome 完成配置与外网嵌入验收；本轮不新增业务用例通过记录。

### 2026-10-12 23:31–23:35：生产专用应用已配置，业务 live 定位 MCP 装配缺口

- 用户登录 `https://chat.test/admin/api` 后，已创建 `GeoAgent` 独立产品嵌入应用；仅一个 `dock` 紧凑对话入口，绑定现有 `geo-research-assistant`，仅允许 `https://geoagentd40d.sealosgzg.site` 与 `https://geoagent.lhotsetech.com` 两个 Origin，面板 Token TTL 1800 秒。未更改 StockLink / LCF。
- 专用应用 Key 已签发（仅 `embed:identity`、90 天有效期），通过项目连接配置保存到生产 GeoAgent 后端；未保存到浏览器密码管理器，未将原文写入代码、日志或账本。PiChat 地址仍为 `https://chat.lhotsetech.com`。
- 首次保存后验证仍返回 401；重新明确填入并保存后，页面「验证 PiChat 连接」显示身份签发通过。只记录最终成功，不将首次失败隐藏。
- 两个 PiChat 域名的实际 HTTP 响应 CSP 已包含两个 GeoAgent Origin。Chrome 外网项目实际显示 GeoAgent 欢迎页、模型与可编辑输入框，原 iframe 拒绝连接已解除。
- 在外网嵌入面板提交新轮次：指定工作快照 `57fb9e48f6a0fa1f10f8502d`，要求美伊局势升级/持续/缓和线索、范围记录数及候选维度覆盖。面板基线为 **227 / 68 / 139、3979、13/13**。
- 新轮次实际进入生成并完成（23:34，耗时约 27 秒）；回答报告 `mcp_connection list` 无 GeoAgent 连接、工具搜索为空，明确 unsupported，未产出上述数字，未换快照、未编造。故 **嵌入交互通过，MCP 业务取数未通过**，不能以欢迎页替代业务验收。
- PiChat 管理页站点 MCP 连接清单也未发现 GeoAgent；已准备专用连接表单，目标 `https://geoagentd40d.sealosgzg.site/mcp`、Streamable HTTP、Bearer。尚未创建 MCP Key 或保存该连接，待行动时确认仅向 GeoAgent Agent 授予项目只读数据访问后继续。

### 2026-10-12 23:36 起：专用 MCP 授权完成，真实问答发现计数契约缺口

- 用户行动时确认后，在生产项目创建 `PiChat GeoAgent production` MCP Key（ID `844e2fc5615a3ebd`），只读访问全部快照和业务工具；原文仅保存到 PiChat 专用连接凭证，不写入仓库、截图或账本。
- PiChat 新建 `GeoAgent 地缘数据与分析`，运行 ID `site:geoagent`，Streamable HTTP 地址 `https://geoagentd40d.sealosgzg.site/mcp`，Bearer 共用凭证。检测得到 11 个工具（约 614 ms），保存时健康检查 1/1 用户、11 工具通过。仅绑定 `geo-research-assistant`，其余 Agent 未扩权；未来新增工具默认不开放。截图 `output/live-production/mcp-assignment-20261012.jpg`。
- 外网嵌入第 2 轮真实问答（23:38–23:39，约 59 秒）实际调用 6 次 GeoAgent 工具，读取指定工作快照，返回美伊记录 3979、主题候选维度 8 个；但将升级/持续/缓和报道计数误答为情景候选样本各 5 条，**风险概览业务验收未通过**。截图 `output/live-production/risk-overview-before-20261012.jpg`。
- 面板口径澄清：227 / 68 / 139 是当前美伊筛选内的报道分类数；3979 为范围内全部记录；13/13 是全部主题的框架候选维度，不是美伊单主题（8 个）。覆盖 API 的 3957 排除 22 条补充报告，不应呈现为无解释的数据冲突。情景候选 IDs 会随筛选变化，不据此声称同一输入不稳定。
- 根因：`geo_workspace_summary` 未暴露面板 signal_counts；返回 coverage 未说明全主题及排除 supplemental 的范围。已补上靠前返回的分类计数、范围记录数、补充报告数、全框架覆盖及业务描述，计数采用最新研究员复核，避免与面板自动分类/复核结果偏离。
- 新增测试覆盖主题/日期/维度筛选、复核版本顺序与快照隔离、补充报告差异、零记录、计数守恒；官方 MCP 客户端真实调用也断言计数与全框架 13 维度。更新风险概览测试用例，防止再次把协议连通误记为业务通过。后端全量 **193 passed**，MCP 专项 **10 passed**；外网修复版本尚待发布与再问答验收。

#### v1.0.6 发布后复测：仍未通过，追加历史快照读取投影修复

- GitLab main `6c235c3e00134fb4db6a831f71c55a8351f9460f` → `geoagent-prod-v1.0.6`，流水线 [5275](https://gitlab.lz.internal/yujunjun/GeoAgent/-/pipelines/5275)，verify 27706 / deploy 27707 通过；两个外网域名匿名 401、MCP initialize/list/snapshot-call 200，临时 release-live Key 已撤销。
- 外网嵌入第 3 轮（23:47–23:48，约 43 秒）不提供预期数字，Agent 自行调用 workspace_summary，纠正了每类 5 条的旧答案，也正确解释 3979/3957、22 条补充报告及全框架 13/13。但三类 signal_counts 均为 0、unknown 为 3979，与面板不符，**不记为通过**。
- 追加根因：历史快照原始记录未持久化 situation_signal；REST 地图面板经 `event_metadata` 在读取时分类，首次 MCP 修复直接读取原字段。现改为复用同一 `event_metadata`，再应用最新人工复核；新增无预计算字段的升级/持续/缓和标题样本回归，防止新格式 fixture 掩盖历史数据投影问题。该补丁仍需独立发布后外网复测。

#### v1.0.7 发布与外网最终取数：计数通过，分析解释保留缺陷

- 本地真实旧快照 `0b18ddf4468552d911eba06e`，直接比较面板 `present_map_record` 与 MCP 投影：升级 92 / 持续 54 / 缓和 135 / 待研判 2020，总记录 2301，完全一致；全量 193 项测试再次通过。
- GitLab main `a3c4cb9aa6fabc30392a662ec9419559e3a13f4d` → `geoagent-prod-v1.0.7`，流水线 [5277](https://gitlab.lz.internal/yujunjun/GeoAgent/-/pipelines/5277)，verify [27711](https://gitlab.lz.internal/yujunjun/GeoAgent/-/jobs/27711) / deploy [27712](https://gitlab.lz.internal/yujunjun/GeoAgent/-/jobs/27712) 均通过。生产滚动更新、两个公网域名 MCP initialize/list/snapshot-call 成功，匿名 401，临时探针 Key 已撤销；正式专用连接与凭证保留。
- Chrome 外网嵌入第 4 轮（23:52，约 24 秒）输入自然问题，不提供预期数字，要求重新实时读取相同快照。实际执行 3 次 MCP 调用：`geo_workspace_summary`（usiran / all）及 `geo_snapshot_get`。美伊回答 **227 / 68 / 139 / 待研判 3545、3979、全框架 13/13**，与右侧面板逐项相符；能解释 22 条 supplemental、候选覆盖不等于数据完整、情景样本数不是报道数。取数与范围口径验收通过。截图 `output/live-production/risk-overview-matched-20261012.jpg`。
- **仍有分析解释缺陷，不标整轮完全通过**：Agent 将上轮 0/0/0 → 当前值变化确定归因为人工复核更新；工具仅说明“复核可以变化”，并未返回发生过复核的证据。本次真实原因是代码投影修复，不能用能力描述推断已发生事件。Agent 另将 coverage.count 3957 口语化为“维度合计”，实际该值是去补充报告后的主题记录数，维度计数不可相加。
- 下一步：独立执行未通过生产验收的业务用例；补充“相同快照数值变化不能无依据归因”“主题记录数与多标签维度合计”反例，并验证 Agent 不把候选覆盖、可变字段描述写成事实。当前交付仅确认专用授权、外网嵌入、真实 MCP 取数及本用例核心计数通过，不宣称所有业务分析问题已验收。
- QA 边界：本轮未改前端；实际 Chrome 嵌入可输入、生成、显示结果。原生 Chrome 无控制台读取能力，不宣称浏览器 console 零错误。Sealos 部署仍存在既有 PodSecurity restricted 警告，本轮未修改容器权限或安全策略。

### 2026-10-13：无证据归因契约修复与剩余生产业务验收（进行中）

- 删除“复核可更新”这一易被当成实际原因的孤立说明；摘要靠前返回当前筛选实际应用复核条数/最新时间、分类投影版本及 `change_attribution.status=not_established`。单次读取不具备前后同口径审计证据，即使存在复核也不能确认差异由复核造成。
- 补充 Server instructions / 工具业务描述，明确事实、规则与假设边界；`coverage[].count` 是主题记录数，维度多标签计数不能加总，候选覆盖不代表数据完整。
- 回归断言最新复核版本、其他快照和筛选外复核排除、无复核/零记录，以及有复核仍不确立变更原因。后端全量 **193 passed**。待发布后在外网嵌入重新提问，不能仅凭合同测试将 Agent 归因缺陷标为修复完成。

#### v1.0.8 外网逐项结果与追加修复

- `94ac4eed` → `geoagent-prod-v1.0.8`，GitLab pipeline 5280，verify 27716 / deploy 27717 已通过；两个公网域名 MCP 探针匿名 401、initialize/list/snapshot-call 200。
- 轮次 5（00:11，35 秒）：重新读取摘要及同口径复读，核心计数 227/68/139/3545、3979、13/13 与面板一致；实际复核为 0，Agent 明确具体原因未证实、不能归因人工复核，且纠正主题记录数≠维度合计。但仍推断“版本出现与数值转正同时发生”，混淆首次看到字段与实际发布时点，追加边界说明。截图 `/tmp/geoagent-attribution-live-20261013.jpg`。
- 轮次 6（00:14，40 秒）：指标目录 + 四条序列取数；布伦特 103.47/104.28/-0.81/-0.78%，美债 5.22/5.28/-6bp，黄金 4217.7/4157/+60.7/+1.46%，HY 利差 309/303/+6bp，日期与量化面板匹配。**时间口径未通过**：把本周解释为今天所在周，而项目约定按所选快照导入日期。追加 provenance.relative_time_basis（本快照 10-05 至 10-09），测试历史快照不跟随 latest/系统日期。截图 `/tmp/geoagent-metrics-live-20261013.jpg`。
- 轮次 7（00:15，43 秒）：三情景规则与面板一致，实际检索并读取 4 条新闻，明确来源/时间/待核验。**分析未完全通过**：把“不披露谈判细节”说成“坐实缓和失效条件”，将专家预测映射为行动局限已发生；结尾免责声明不能抵消正文确定性。追加结构化 analysis_evidence_boundary，区分候选、规则条件、已核验反证（目前未评估），禁止用缺乏证据证明事件不存在。
- 以上修复未修改 PiChat 其他 Agent 或权限；数值正确、归因正确、时间口径正确、规则映射正确分别验收，不合并为一个通过标志。

- `fba5370a` → `geoagent-prod-v1.0.9`，pipeline 5282 / deploy 27721 已通过。新增快照相对周口径与反证边界，尚待嵌入回答复测。
- 轮次 8（UI 完成时间 00:18，42 秒）：四类缺口实际调用摘要两次、指标目录三段、证据检索及快照详情。覆盖数字与已读面板一致，明确部分材料被截断；**未通过**：把有效零值归入“没有数据”、把 supplemental 排除口径当成缺失、把 topic=null 当未绑定，且沿用错误本周口径。摘要新增靠前的五类 `asset_data_gaps` 和 `metric_quality_basis`，避免长响应中段截断，并显式禁止这些错误推断。
- 轮次 9（UI 完成时间 00:22，8 秒）：先独立打开研究简报面板，显示“点击生成双主题简报创建首份档案”，随后嵌入查询 `geo_brief_list` 两次（指定快照 / 全部可见），都为 total=0。**简报空结果核心业务通过**：无档案、无正文、即时回答不是档案，未伪造简报正文。面板截图 `/tmp/geoagent-brief-baseline-20261013.jpg`。
- 另记录跨用例质量问题：Agent 自写“取数时点”偶有与 UI 轮次时间不一致（如 00:22 完成却写约 00:26）。新增服务端 `read_at` 与缓存时间口径，不能用模型估计时间替代回执；尚待复测。上述新增修复后全量 **194 passed**，官方 MCP 客户端断言五类缺口完整与时区时间字段。
- Mac 锁屏后原生 Chrome 返回无法自动解锁；已请用户手动解锁，未尝试绕过。外网嵌入的剩余 pipeline-live、asset-impact、独立 evidence-verification，以及修复后的指标/反证/缺口/归因复测仍待继续，不把锁屏后未执行的用例记为通过。IAB 的 GitLab 只读检查仍可用，确认 v1.0.9 发布完成。
- `0033c00a` → `geoagent-prod-v1.0.10`，[pipeline 5284](https://gitlab.lz.internal/yujunjun/GeoAgent/-/pipelines/5284)；verify 27724、[deploy 27725](https://gitlab.lz.internal/yujunjun/GeoAgent/-/jobs/27725) 均已通过。滚动更新成功，两个公网域名 MCP 匿名 401、initialize/list/snapshot-call 200；这只确认发布与协议探针，不能替代锁屏后未完成的嵌入业务复测。

#### 解锁后继续外网嵌入验收

- Chrome 刷新项目并重新建立嵌入身份，未更改权限或凭证。轮次 10（UI 完成 05:10）输入四项指标的自然问题，不提供预期数值或周起止日期；实际四次 `geo_metric_series`，展开首条技术回执确认 `finance:brent`、指定快照、查询窗口 09-28→10-13、`read_at=2026-10-12T21:10:20.618986+00:00`。
- **本周口径修复通过**：回答采用快照周 10-05→10-09，另列今天所在周无观测但不称无变化；四项最近一期数值、前值、变化及日期与此前独立量化面板基线一致。周内首末变化另列，并明确为 history 计算、不同指标末观测日不同（10-07/08/09），不冒充同步截止日。`read_at` 与实际回执相符，不再臆写未来取数时点。
- 周内首值四项为 100.32 / 5.31 / 4156.80 / 312，回答周变化 +3.15 / -9bp / +60.90 / -3bp；该部分来自工具 history，尚不将其记为已独立逐点核对图表的通过项。截图 `/tmp/geoagent-metrics-retest-20261013.jpg`。原生 Chrome 无 console 读取，控制台检查仍未测。
- 轮次 11（05:12，37 秒）：摘要 + 三次证据检索；规则与情景面板相符，声明/预测不再称“坐实”。**仍未完全通过**：用 not_evaluated 支持否定判断；有限已读材料推为整个快照无数据；单方行动通报口语化为行动已持续；旧轮次 evidence_get 未明确标记为旧回执。追加 assessment_scope / retrieval_scope，明确未评估不等于否定、抽样不等于全量、单方通报非独立行动核实、不同战区不可直接替代。新增回归，全量 195 passed。截图 `/tmp/geoagent-counter-retest-20261013.jpg`。
- 轮次 12（05:14，69 秒）：摘要两次 + 指标目录两段，共 31 项；有效零值不列缺失、隔离不称无原始数据、topic=null 不称未绑定、22 条 supplemental 不称缺数据、五类 asset_data_gaps 完整，与面板基线匹配。**缺口仍部分通过**：全主题 7 项 warnings 未明确标为全主题，可能被当作美伊当前缺口；不在目录又被推断为不能读取原始序列。追加 query_scope / quality_warning_scope（含告警数量），及目录缺席不证明原始数据不存在的边界。截图 `/tmp/geoagent-gaps-retest-20261013.jpg`。
- 独立打开管线菜单，加载完成后报告批次 manual-20261009T072655Z-701df12e、全部完成、4 个采集器、导入成功、报告 10-09 15:30+08；源日志仍有 gn-mecca-pact 超时、lwj-feed 403，不能以 0 个失败采集器解释为所有来源成功。未点击立即执行，不产生新采集。
- 轮次 13（05:15，18 秒）：实际 geo_collector_status 一次，批次/4 采集器/导入快照与管线菜单一致，能识别 source 日志失败不等于批次失败、报告日期不等于今天。**部分通过**：有界 events 仍被写成“此后无新事件”，且账本字段 records=0 与抓取日志 1123 的口径未明。MCP 增 interpretation_basis，明确 live 是读取账本而非心跳、有限日志不能证明无后续事件、默认计数不可当实际零。截图 `/tmp/geoagent-pipeline-live-20261013.jpg`。
- 轮次 14（05:17，39 秒）：analysis_view 三次 + workspace_summary 一次，五资产市场方向/关键条件/反证/缺口基本与矩阵相符，明确 HY +6bp 不能由同期观测归因冲突。**仍部分通过**：模型自行加周/financial 筛选后，把信用债路径无候选混入无日期维度筛选面板；独立信用债详情实际为“有近期线索，待核验”。MCP 追加 analysis query_scope 和精简 asset_overview，工具描述要求用户未指定时不自行收窄、不同筛选不可拼接。截图 `/tmp/geoagent-assets-live-20261013.jpg`。
- 独立读取第二条证据详情：7ec7704882f0a3c108b18386，新华网，发布时间 10-09 11:20+08、采集 06:59Z、二级来源、原文已采集报道待核验、链接未检查；正文包含特朗普不打伊朗声明、Axios 重启行动准备报道，以及中央司令部所称林肯号返回母港，不能直接等同停火执行或持续撤军。
- v1.0.11（5288）和 v1.0.12（5290）只做 verify，未触发手动部署；继续发现的修复将合并为下一标签部署，避免在问答生成中重复滚动更新。
- 轮次 15（05:18，25 秒）：实际 search 三次、get 两次（回答自称“4 次”但明细 3+2，应记 5 次）；两条 ID、新华网、发布时间、原文 URL、verification 待核验、link_health=null 均与独立详情相符，不把声明/单舰返港解释为停火落实或持续撤军。**数据字段缺陷**：面板有 fetched_at，而 MCP _record_summary 未投影，Agent 如实报采集时间缺失；现补 fetched_at/first_seen/event_at/tier，保留 null，不暴露 raw_refs。新增回归。末句仍将两条材料不足扩为整个快照无数据，随 retrieval_scope 修复复测。截图 `/tmp/geoagent-evidence-live-20261013.jpg`。
- 轮次 16（05:20）：重新摘要 all/usiran 两次；当前风险数与面板一致，明确 not_established、不能根据版本字段首次出现判断部署或回填时间、不能排序原因。**归因核心复测通过**，但附带沿用管线“此后无新事件/无新动作”旧错误；不将跨用例解释标为全通过。截图 `/tmp/geoagent-risk-retest-20261013.jpg`。
- 合并全部补丁后全量 **197 passed**。一次并发启动的测试因测试服务端口生命周期冲突出现连接失败，结束重叠进程后串行全量复跑 197/197，通过结果以最终串行复跑为准。main `6a95f96f` → v1.0.13，pipeline 5292；等待 verify 27742 / 手动 deploy 27743。尚未把发布等同业务复测通过。
- v1.0.13 的 verify 27742 / deploy 27743 均已通过；滚动更新完成，两个公网域名匿名 401、initialize/list/snapshot-call 200，发布临时 Key 已撤销。随后在生产嵌入面板发起轮次 17，继续业务复测，不以探针替代问答验收。
- 轮次 17（05:26，15 秒）：重新 evidence_get 两次，两条 fetched_at 均 2026-10-09T06:59:29.712607Z；来源、发布/采集时间、T2、待核验、link_health=null 与面板吻合。字段修复通过；**分析仍部分通过**：单方返港报道称为事实，结尾又以两条材料泛化“快照内都没有”。发现 evidence_search/get 未返回新增 retrieval_scope，现补边界到这两个实际读取路径并加官方 MCP 客户端断言。截图 `/tmp/geoagent-evidence-v13-20261013.jpg`。
- 轮次 18（05:29，70 秒）：analysis_view 两次 + summary 一次，无日期无维度，query_scope 与面板一致；五资产方向/条件/反证/缺口、信用债“有近期线索，待核验”均匹配。**同筛选核心通过，但归因仍未通过**：将旧差异归因 limit；代码 limit 只截取结果列表，不参与 topic_paths，补 limit_basis 明确禁止该推断。截图 `/tmp/geoagent-assets-v13-20261013.jpg`。
- 轮次 19：原生输入中文丢失仅发出标点，立即停止，不作为业务验收。后续先检查完整输入再发送。
- 轮次 20（05:30，5 秒）：collector_status 实读，四采集器/批次/导入匹配；明确进程与后续事件均未知，来源失败不等于批次失败。但首段仍写“此后约3.7天无更新”，与后段边界矛盾，**仍未完全通过**。新增 activity_assessment：process_running/events_after_latest_visible=unknown，no_updates_since_last_event=not_established。截图 `/tmp/geoagent-pipeline-v13-20261013.jpg`。
- 补 evidence 边界后串行全量 197 passed（53.76 秒）；此前单跑 MCP 文件出现 ReadTimeout（13 passed/1 failed），全量通过不能抹除该偶发，未确认原因，不称其已修复。最终 activity_assessment/limit_basis 回归将再跑后发布。
- 轮次 21（05:32，13 秒）：analysis_view + summary 两次；三情景规则与面板匹配，明确候选不是条件满足，旧正文标明沿用旧回执。但标题仍写“已核验反证——没有”，实际 not_evaluated 只能是未评估、是否存在未知；未完全通过。补 verified_counterevidence_available=unknown 与 assessment_summary。截图 `/tmp/geoagent-counter-v13-20261013.jpg`。
- 轮次 22（05:33，18 秒）：摘要 usiran/all 两次，展开技术详情确认指定工作快照、topic=usiran、limit=1 等实际参数。**告警范围/缺口核心通过**：美伊 count=0、全主题 count=7；候选覆盖不是完整覆盖，22 条 supplemental 排除不是缺失，有效零值/隔离/目录未枚举不再当无数据。覆盖、时效、证据核验缺口与独立面板基线匹配；未新读证据正文，旧材料不作为本轮重新核验。截图 `/tmp/geoagent-gaps-v13-20261013.jpg`。
- 测试启动器使用 stdout/stderr PIPE 却不读取，长会话存在日志反压卡死风险；改临时日志文件保留诊断，避免依赖无人消费的管道。该项是测试设施修复，不作为生产连接修复或 Agent 验收结果。
- 最终补丁冻结后全量 **197 passed（54.92秒）**，MCP 专项再跑 **14 passed（24.48秒）**。`78a90c1a` / `geoagent-prod-v1.0.14` 已推送 main；pipeline 5294 verify 27746 通过，手动 deploy 27747 已启动，等待外网复测。
- v1.0.14 deploy 27747 已通过，两个公网域名匿名 401 / initialize、list、snapshot-call 200，临时发布 Key 撤销。继续生产嵌入轮次23起复测。
- 轮次 23（05:37，9秒）：analysis_view 重新取数，明确 not_evaluated/unknown、不能说没有反证；情景规则、候选和旧正文分层正确，**反证未知状态核心通过**。末尾仍把 limit_basis 误解为旧假阴性解释，跨项归因未通过。截图 `/tmp/geoagent-counter-v14-20261013.jpg`。
- 轮次 24（05:38，8秒）：collector_status 新回执，最近可见批次/四采集器/源失败/导入与管线面板一致；明确 process_running=unknown、events_after_latest_visible=unknown、no_updates_since_last_event=not_established，不再断言几天无更新，**管线主用例核心通过**。截图 `/tmp/geoagent-pipeline-v14-20261013.jpg`。
- 轮次 25（05:39，10秒）：新 get 两条正文，采集/发布/核验/链接状态匹配，未再泛化整个快照无数据；情景失效是否成立保持未知。**证据字段与有限样本范围通过**，但“单舰返港这一事实由中央司令部发布”措辞仍易将被报道通报读为独立核验事实，保留解释风险。截图 `/tmp/geoagent-evidence-v14-20261013.jpg`。
- 轮次 26（05:40，18秒）：针对错误 limit 归因的追问，真实 analysis_view 两次、limit=5/1；确认低 limit 只有 equity 项，不改变 credit 路径匹配。Agent 撤回旧归因，指出先前带 financial 筛选不同、具体原因仍未证实，**被挑战后的纠错通过，不等于未经提醒的稳定通过**。截图 `/tmp/geoagent-attribution-limit-v14-20261013.jpg`。
- 用户再次解锁后检查生产专用 Agent 管理页：revision 956844beddc967c40e2820b2；基础/执行策略/工具能力/装配页未见系统提示词编辑入口，未更改描述、模型、权限、其他 Agent 或站点默认值。不能把“配置可改善”当作已执行指令修复；继续原始自然问题复测。
- 轮次 27（05:42，13秒）：只输入原始问题“当前判断有哪些反证？”，实际 analysis_view / workspace_summary 两次新读取（read_at 21:42:39Z / 21:42:42Z）。正文正确引用 not_evaluated、available=unknown 及“尚未评估，是否存在未知”，但标题仍写“系统核验状态：尚无已核验反证”，容易误读为已核查后的否定判断；结尾的正确限定不能消除标题歧义。**反证口径稳定性未通过**，不能用轮次23或纠错追问的通过替代。截图 `/tmp/geoagent-counter-natural-v14-20261013.jpg`。
- 本轮判定：连接与数据字段修复、问答分析稳定性分别记账。连接配置（URL/Key/MCP assignment）不是这一语义问题的修复点；专用分析指令可作为下一修复方向，但管理页未找到编辑入口，尚未实际修改或验证。所有复测仍在既有长会话，未完成独立新会话验收；不得宣称生产全套完成。

### 2026-10-13：MCP 工作台与已撤销 Key 清理（本地实现）

- 根据用户两张参考图新增顶部独立「MCP 工作台」，沿用深色系统 UI；工具搜索、描述字数、原始 description/inputSchema、参数字段表、服务 instructions、annotations/outputSchema、JSON 试调用与真实返回、近7天调用记录和 P95。
- 模型视角明确限定为 MCP 原始契约，不宣称等同 PiChat 加工后的上下文，不照抄参考图中的500字/50000字符等未核实阈值；返回区来自真实试调用，不伪造静态样例。
- 工作台 catalog/call 均受现有项目管理鉴权保护；试调用由后端固定转发至同项目 `/mcp`，仍经真实 Bearer 授权与 SDK 参数校验，不支持任意 URL 或权限绕过。用户提供的试调用 Key 只在面板内存使用，不持久化，不进入调用统计。
- 统计从本功能启用开始，保留7天，仅记录工具名、时间、状态、耗时、回执ID；最多展示最近2000条。认证失败与进入工具前的协议错误未进入统计，历史零记录不解释为从未调用。
- 已撤销 Key 增加「删除记录」，仅从管理列表移除，后端保留撤销 tombstone，不能恢复访问。启用 Key 删除返回409，必须先撤销；原有启用连接不受清理影响。
- 本地 `npm run build` 通过；integration 专项15项通过，覆盖清理幂等、禁止删除启用Key、删除后认证仍拒绝、管理403、真实MCP成功/失效401、未知工具与超大参数422、SDK缺参数错误及统计不存Key。
- 本地服务8010/8011已启动（MCP随后端）；经真实HTTP检查前端200、health ok、工作台11个工具和instructions。现有IAB标签绑定被浏览器安全策略阻止，未绕过、未另换浏览器；页面视觉/点击/响应式/console live验收未执行，不把构建/API通过当作UI通过。未发布外网，未删除生产Key记录。
- 最终串行全量回归 **199 passed（51.12秒）**。本轮浏览器专用技能不可用，原计划使用现有CUA页面验证；该路径被策略阻止后停止，未改用终端浏览器自动化绕过。

### 2026-10-13：工作台免 Key 调试与本地 UI live 验收

- 本节更新上一节的调试方式与 UI 验收状态：管理员试调用不再输入 MCP Key。后端通过隔离的进程内 MCP transport 调用相同业务工具，保留 SDK 参数校验；临时凭证不持久化、不下发前端。项目管理鉴权仍保留，对外 `/mcp` 仍要求 Bearer Key，匿名访问401。该调试路径不替代外部 Agent 的 Key/权限验收。
- 在右侧本地内嵌浏览器 `http://127.0.0.1:8010/` 实际操作：中文描述搜索「快照」、工具名搜索 `geo_snapshot_get` 和无匹配搜索均符合预期；参数表显示必填 snapshot_id 及摘要工具默认值，与原始 inputSchema 对照一致。非法 JSON 在前端提示，空参数触发 SDK 缺必填错误，合法快照参数真实返回成功。
- `geo_workspace_summary` 指定工作快照 `0b18ddf4468552d911eba06e`、topic=usiran、limit=1，返回风险计数92/54/135、记录2301，与右侧面板逐项匹配。不存在的快照返回 `GEO_SNAPSHOT_NOT_FOUND`，界面显示业务失败而非成功。
- 调用实况核对成功/业务失败的时间、状态、耗时和 audit_event_id，计数随实际调用增加；非法 JSON 和进入业务工具前的 SDK 错误不计入业务统计。修复固定请求 ID 导致回执 ID 重复的问题，连续两次成功调用已得到不同 ID；既有历史记录不回写。
- 返回区改为格式化业务 JSON，原始 MCP 协议回执仍可展开查看，避免嵌套转义文本难以阅读。工作台明示管理调试与外部认证的边界。
- 实际点击删除本地已撤销 `final-restart-check`（ID c2590c3fd8c1cdb1）的记录，刷新及服务重启后均不再显示，另一条已撤销记录仍在；后端保留撤销 tombstone。未删除生产 Key，未创建额外启用 Key；启用 Key 禁止删除409由回归测试验证。
- live 发现并修复旧 Key 表并发迁移的 `duplicate column name: deleted_at` 500：获取 SQLite 写锁后重新检查列再迁移；新增8线程/16次并发回归。配置接口非 JSON 错误也改为可读提示。
- 冻结补丁后串行全量 **200 passed（30.35秒）**，`npm run build` 通过，`git diff --check` 通过。最终本地页面 error/warn console 记录为空；桌面截图已检查，移动端未测。证据：`/tmp/geoagent-mcp-workbench-keyless-20261013.png`、`/tmp/geoagent-mcp-workbench-calls-20261013.png`、`/tmp/geoagent-mcp-key-deleted-20261013.png`。
- 本地8010/8011与随项目运行的 MCP 保持启动，右侧页面保持打开。仅完成本地工作台验收，未提交/推送或发布外网，不将其记为生产 Agent 剩余业务用例已通过。

### 2026-10-13：按用户要求移除独立项目管理口令

- 移除连接配置、MCP工作台的管理口令输入、前端请求头及后端 `GEOAGENT_SETTINGS_TOKEN` 校验；本地和公网均不要求该额外口令。旧环境变量不再参与校验，环境示例同步删除。部署层已有站点登录不变；仍保留同源 Origin 检查。这不是新增用户权限系统：能够访问项目接口的人可管理连接/签发撤销Key并通过工作台读取业务数据，无站点登录的部署会开放这些能力。
- 对外 `/mcp` Bearer认证不变，跨站管理请求仍403；原始应用Key不返回给前端。公网来源TestClient无需管理口令即可读取配置、执行工作台调用及创建MCP Key，匿名直接访问 `/mcp` 仍401。
- 本地重启8010/8011，在右侧页面实际打开连接配置，自动加载成功且无口令框；切换工作台加载11个工具，无口令执行 `geo_snapshot_list` 返回ok=true。页面身份正确、无空白/框架错误覆盖，最终error/warn日志为空。截图 `/tmp/geoagent-no-management-password-20261013.png`，桌面1610×1314，移动端未测。
- integration专项16 passed；全量200 passed（28.56秒）；前端构建和diff检查通过。React技能用于清理失效状态/请求头，前端验证技能用于实际交互检查；Browser插件不可用，采用现有CUA内Playwright接口。仅本地生效，未提交/推送/外网发布。

### 2026-10-13：05业务语义重构首批实施（未全部完成）

- R0：新增06业务映射与验收文档，覆盖九菜单读取路径和27条自然问题。地图地区/图层范围、链接健康实时复核等缺口明确列出，细项全量验收仍待进行。
- R1：指标解释抽取至config/metric-guides.json，前端MetricGuide与backend.semantic共用；指标详情补含义、作用、边界、比较周期及展示单位。证据子串检索、事件分类/人工复核投影统一；16个工具全部参数附业务description，新增主题/维度字典，日期无效或倒置返回结构化错误。
- R2：新增geo_business_catalog、geo_metric_changes、geo_source_health、geo_collector_runs、geo_brief_compare。区间首末有效观测由程序计算，零值/隔离/缺观测与基点转换分开；分析工具支持asset_id/scenario_id按需详情，情景证据关系明确candidate_only/not_evaluated。概览compact参数为可选，默认保留旧字段，未做破坏性删减。
- 工作台按当前看板范围预填Schema支持的参数；不宣称这已解决嵌入Agent上下文。
- R3证据：全量207 passed（30.01s），前端build通过。官方SDK初始化/列工具、参数描述、新增4工具调用、日期拒绝与既有权限测试通过；真实简报双版本比较未验收。随后仅增加说明字段/文档及前端预填，build再次通过。
- 本地live：工作台16工具，搜索“本周”能定位变化工具。快照0b18ddf4/usiran/limit=2回执workbench-6959187d96c84bdea9f6013450e7bee2返回ok=true，范围09-28至09-30、total31、有后续页；月度财政序列两条无有效观测，正确返回insufficient_observations。不能将前两条结果说成全体没有变化。
- S08阻塞：只读核对PiChat AgentEmbedPage消息接收，当前缺GeoAgent业务上下文消费路径。跨项目修改不在本轮默认实施范围，未伪造已传递/已消费。需授权配合修改PiChat或由其Agent实现后，再验收快照/主题/日期切换。
- R4未通过：本轮尚未完成逐菜单独立新会话自然问答，不把MCP协议或工作台测试算Agent验收；地图地区聚合、历史批次结构化比较等仍有剩余工作，见06。按React技能抽取共享配置并保持增量兼容，按前端验证技能做真实工作台交互。
- 本地服务保持8010/8011，MCP随后端启动。未提交/推送/发布外网。
- 补充UI试调用：搜索“来源健康”，选择工具自动预填所选快照，回执workbench-3aba2d13a41d4535b78436c493aa87eb成功返回项目状态与缺发布时间/采集时间。截图/tmp/geoagent-semantic-source-health-20261013.png；最终专项回归37 passed。

### 2026-10-13：GeoAgent剩余语义重构与本地工作台逐类live（PiChat另侧实施）

- 本轮只修改GeoAgent；PiChat上下文桥接由用户指定另一Agent处理，不修改其仓库、不操作生产权限、不发布。
- 地图新增geo_risk_map，17工具均标记readOnlyHint/destructiveHint；沿用看板分类、复核和地点投影，提供图层/信号/地点名称筛选、分类数、可定位数、无地点数与地区关联报道。一个报道关联多地，地区数不能相加当总记录；不声称读取视口、证明部署或确认事件。
- 链接健康读取与REST详情相同的检查档案，区分尚未检查、检查时间与HTTP状态；不会因为读取工具就声称实时复核。证据正文以UTF-8字节限制并明示截断。来源项目中文名称提取config/project-labels.json由前后端共用，来源缺时间字段补业务语义，错误提示脱敏。
- 历史批次按run_id在limit前定位，补compare_run_id与结构化比较；未知字段不置零，不编造失败原因。修复特定旧批次因最近20条边界而找不到的问题。
- 指标区间结果增加全匹配集summary，不用当前页推断全体；共享metric_view精度修复5.26%-5.24%出现1.999999…基点的问题，返回2基点。审计事件ID改为独立UUID，避免不同MCP会话复用JSON-RPC请求ID时碰撞；既有历史不重写。工作台切换工具中止旧请求并忽略迟到结果。
- 自动验证：串行全量216 passed（28.72s），npm run build与git diff --check通过。新增隔离数据测试涵盖25批次历史查询、两个简报UTF-8差异截断、地图去重/人工复核、空图层/非法参数、链接检查档案、基点精度与审计唯一性；没有用模拟结果替代以下真实UI。
- 本地右侧内嵌浏览器实际操作http://127.0.0.1:8010/：搜索/选择/JSON参数/试调用/返回/调用记录，工作快照0b18ddf4468552d911eba06e、美伊、全部维度、无日期。概览92/54/135、范围2301，全快照4384；地图分类2279/定位1831/无地点448与面板一致。geo_risk_map修复后审计geo-audit-21ef4ac9e95447f5874feaa8ef2ba4e8。
- 来源面板与工具均显示defense-spend-monitor缺发布时间7、缺采集时间144，未把字段缺失冒充来源失败。周范围09-28至09-30共31指标，13可比较且变化、18观测不足；10年美债5.24%→5.26%/+2基点与量化面板一致，audit geo-audit-c61efe00977a46b8a6fc2e00d20621de。
- 情景escalate的成立/反向条件及缺口与面板一致，状态仍为研究假设、未判定成立；候选关系candidate_only/not_evaluated，不报告“没有反证”。信用债返回信用利差302基点/+9基点、VIX16.04/-0.03及条件和缺口，不把条件机制当冲突归因。
- 当前批次manual-20261012T085105Z-b9fd1a5f为partial，18采集器/1失败，导入skipped_collection_status；对比manual-20261009T023209Z-52f88f68的success/0失败/succeeded，UI及工具一致。未触发新采集。非法地图参数GEO_INVALID_FILTER在UI与调用记录均显示失败。
- 切俄乌/军事战线后，工作台自动预填ukraine/frontline，返回分类611/定位432与地图一致。运行中切换工具两次检查新工具均“尚未执行”，无旧结果串入。日期原生控件的自动输入未可靠触发筛选状态，日期UI传递不记通过；协议日期校验已有测试。输入无效超长年份时后端拒绝，清除后恢复，未修改业务数据。
- 本地只有1份真实归档简报，同ID比较无差异通过；两份不同真实档案未验收，双版本计算仅由隔离测试覆盖。桌面页面无空白/框架覆盖，最终error/warn日志为空；移动端未测。证据/tmp/geoagent-semantic-live-results-20261013.json、/tmp/geoagent-semantic-map-live-20261013.png。
- 当前结论：本轮GeoAgent工具层与本地工作台重构增量通过；R4独立新会话、自然问题→自主选工具→最终回答仍未通过，不能拿上述工作台试调用替代。待PiChat侧完成S08后联验上下文切换、无证据归因及逐菜单首问。服务8010/8011与随项目MCP保持运行，未提交/推送/发布。

### 2026-10-13：日期控件同步与真实双简报比较收尾

- 范围：仅GeoAgent本地；PiChat不处理。本轮按前端验证技能进行实际页面操作，Browser插件未提供，采用现有CUA内Playwright接口；未修改日期控件代码，没有将自动化填值问题包装成应用修复。
- 日期同步：在0b18ddf4468552d911eba06e、美伊、全部维度下，用原生date控件键盘事件设定2026-09-28至09-30。仅fill改变DOM值而未触发React状态；补真实ArrowDown/ArrowUp事件后看板筛选确实更新。打开工作台选择geo_risk_map，自动JSON的start/end正确，未手改参数直接执行。看板与返回均分类534/定位398/无地点136、风险线索升级3/持续19/缓和7。回执workbench-158be2da075c427bbabdf0df1d174044，audit geo-audit-70705595c1174ec5a649a409daebc7c3。
- 清除与恢复：实际点击“清除”，看板恢复分类2279/定位1831、范围2301；后续恢复原快照0b18ddf4并重新打开工作台，自动参数start/end为空、snapshot_id正确。日期同步由“待验收”改为本地UI通过，不代表PiChat消费日期上下文已通过。
- 真实档案：通过页面“生成双主题简报”分别从0b18ddf4和9163e282生成两份简报，非测试插入。before=b530cd7437c29264a7bbdb70（2026-10-13T02:19:54.276434Z，原文64712字节），after=3d33806bd844f547bbce381a（02:20:26.554124Z，原文71916字节）。模式均“本地证据简报（含模型状态说明）”，不声称模型生成成功。两份档案重启后均可见，保留供复核，不删除旧档案。
- 修复发现：研究简报REST比较使用localize_brief_markdown后的展示正文，而MCP compare/get用原文，可能出现面板与Agent差异。增加chinese.compare_brief_markdown供REST/MCP共用；MCP正文读取也使用看板展示投影，说明来源链接标签翻译可更新，原始归档不改写。测试检查USD展示为美元且数据库原文保持USD。
- 真实差异113358 UTF-8字节，原单次30KB返回无法读完整。geo_brief_compare新增可选offset_chars及has_more/next_offset_chars/total_chars/total_bytes/returned_bytes，按完整UTF-8字符边界续读；小到装不下一个字符时返回结构化错误，避免无进度循环。默认参数兼容，描述提示Agent不得以首段概括全文。
- 页面“比较简报→比较版本”取得完整差异，再在工作台真实输入同一对ID分4次试调用，拼接全文与面板逐字完全一致（113358字节）；快照、时间、模式一致。回执依次workbench-24f23b70d0834e35825bf7e0c629d059、workbench-4bf22336541644f49ebdfd221424e4d5、workbench-92295df877ac4abd97928d7f6a9a81f9、workbench-8bab3071b8c04cd99e254c3e3393d8e2。正文可见俄乌摘要/市场观测及证据引用发生变化，不自动推断因果或判断被证实。
- 自动测试扩充：隔离简报REST/MCP差异完全一致、展示正文与不可变原文分离、分页续读拼回完整文本。专项4 passed；前端build、diff检查通过。首轮全量216 passed（33.45s）；分页最终代码一轮全量215 passed/1 SDK ReadTimeout（25.48s），该SDK用例独立复测1 passed（12.85s），继续串行全量复测，结果追加于下。没有把超时隐藏为通过。
- 现场页面身份http://127.0.0.1:8010/、V6研究工作台正确，无空白/框架覆盖，最终error/warn日志为空；桌面验证，移动端未测。证据/tmp/geoagent-date-brief-live-receipts-20261013.json、/tmp/geoagent-date-sync-live-20261013.png、/tmp/geoagent-real-brief-panel-20261013.png、/tmp/geoagent-real-brief-mcp-20261013.png。
- 结论：本轮两个指定剩余项本地live通过，保留新生成的两份真实档案。本地8010/8011及MCP运行，工作快照恢复0b18ddf4、无日期过滤；不修改PiChat，不提交/推送/发布，R4独立Agent问答仍另行验收。
- 最终冻结代码串行全量回归216 passed（29.47s），前端build与git diff --check通过；上述SDK超时复测未再出现，不据此推断已查明超时根因。

### 2026-10-13：根据PiChat任务委托配合5585上下文桥（联合验收待准备）

- 收到用户授权的跨任务对接：阅读PiChat主检出host-view-context.md，在GeoAgent实现ready/set/result，禁止改PiChat/9172/生产，暂不进行模型问答。实现、恢复与准备条件归档07文档。
- 新增独立状态机及React hook，监听器按origin/当前iframe窗口校验；按panelId/Agent/session/scope重绑，UUID requestId与递增revision，只同步快照/主题/维度/日期。成功回执须匹配当前绑定、请求、revision、范围、received/modelConsumed=false/next_user_turn；旧回执忽略，失败/超时显示未同步。无隐藏提问、无会话强制创建、无聊天输入操作、无看板localStorage。
- workspaceScope固定为项目scope，快照改变只经数据桥、不再重载iframe。实际本地切0b18ddf4→9163e282时src不变，再恢复基线。地图等筛选未接入；不把received说成模型已使用。
- 通过只允许回环地址的临时环境覆盖，把本地iframe与BFF身份目标一并改为5585。原持久配置/Key原样保留，不复用到5585；缺独立GEOAGENT_LOCAL_PICHAT_APP_KEY时BFF503且不发出凭证请求。覆盖开启时配置保存409防止误覆盖。恢复方式见07，未改生产/9172配置或进程。
- 状态机Node4 passed（strict TS/noEmitOnError）；Python集成18 passed；全量218 passed（29.78s），随后仅补配置写保护及稳定scope，专项18再次通过，最终全量结果追加。前端build通过，git diff --check通过。
- 当前5585未监听，内嵌连接拒绝，GeoAgent正确显示“未同步：等待面板就绪”和独立Key未配置。截图/tmp/geoagent-host-view-awaiting-5585-20261013.png。页面非空、无框架错误覆盖；当前不是成功握手/模型使用证明，移动端未测。未发起真实模型问题，未输出凭证。
- 待PiChat侧准备：启动含新协议的5585版本及其独立GeoAgent应用凭证（宿主8010、Agent及入口/用户许可），双方运行版本一致后统一内嵌浏览器验收provider独立上下文块、MCP参数及看板回答。仅GeoAgent宿主实现/本地准备完成，S08/R4仍不能标完成。
- 最终全量218 passed（31.71s），严格TS状态机测试4 passed（含发送字段白名单、不夹带额外token字段）；前端build及diff检查通过。当前GeoAgent进程使用5585临时覆盖保持运行，原配置可按07恢复；没有真实received回执或模型调用，不将单元测试计为联合live成功。

### 2026-10-13：旧快照会话scope最小兼容修正

- 更正上一条“固定项目scope”：PiChat历史列表按scope精确匹配，父scope不能展示旧快照scope的会话。未删除历史，不进行迁移、合并或扩大列表权限。App三个打开入口统一在明确进入时捕获旧`.geo-research-assistant/<snapshot_id>`，面板存续期间固定；范围变化仅更新view桥。
- 本地实际UI：0b18ddf4打开→切9163e282，iframe src保持完全一致；关闭重入进入9163e282旧scope；关闭→选0b18ddf4→重入恢复原scope。空快照不进入，不静默填latest。Node新增未完成请求期间切范围scope不变及明确重入各旧scope测试，最终5 passed；npm run build与git diff --check通过。此次仅前端增量，上一轮Python218 passed不冒充本次重新运行。
- 页面8010/V6正确、有内容、无框架错误覆盖，error/warn日志为空；Browser插件未提供，使用CUA内Playwright。截图/tmp/geoagent-snapshot-scope-compat-20261013.png。5585连接拒绝和缺独立Key仍为已知联合准备条件；未发真实模型、未验证旧会话内容恢复，不把URL证据当运行中模型冻结证据。移动端未测。
- 07同步修正兼容方案及明确恢复入口。日期控件同步、两份真实简报逐字比较仍按上方已完成证据；真实简报保留。页面最终停留简报档案、工作快照0b18ddf4、无日期过滤。未修改PiChat、9172、生产配置，未提交/推送/发布。

### 2026-10-13：5585专用本地凭证准备

- 依据用户授权的PiChat任务委托，只重启GeoAgent8010/8011，使用5585独立App Key环境覆盖，原DB配置/凭证不变。phone/product user/entry已有配置，不输出其值。当前身份BFF502、上游签发403，待PiChat侧核对应用配置；未发真实模型问答，不声明嵌入已成功。
- 新建本地24小时MCP只读scope凭证，绑定geo-research-assistant/local；仓库外目录0700及文件0600，未输出密钥，未复用旧Key。载入后官方SDK initialize/list_tools/geo_snapshot_list成功，17工具全部只读；未认证8010/mcp与8011/mcp均401。有效期至2026-10-14T04:16:48Z。恢复/条件见07，未改PiChat、9172、生产、源码或业务数据，未提交/发布。

### 2026-10-13：采集恢复与旧快照边界补强（未重启，待生效）

- 用户要求只改代码及账本，不影响当前联合Live。新增共享collection_snapshot_boundary，进入所有快照型工具的provenance，以及geo_collector_status详情、geo_collector_runs列表data。明确采集恢复不等于补齐旧快照；采集成功/导入成功/覆盖补齐分开；旧原始归档不自动回填，复核及翻译等展示投影更新不代表新增材料；须确认导入snapshot_id并读同范围指标/证据，不能未读就称完整一周或可比项增多。
- 增量返回字段，不改变计数、查询范围或旧数据，不将Agent答复错误硬编码为Host业务判定。当前8011不是reload模式，未重启，因此当前联合Live尚未载入新字段；须待Live结束协调重启后，另行验证实际Provider消费。
- 测试覆盖共享快照语义、采集success/partial/failed状态仍不能证明回填，以及官方SDK实际快照/来源/地图/批次返回字段。首轮14 passed（19.38s）；追加SDK字段断言后一轮13 passed/1 httpx.ReadTimeout，正在串行复测，不隐藏超时。
- 其他答复问题及验收要求归档08移交提示词：霍尔木兹5写6、HY单期与周区间混用、未读10/9就断言覆盖改善。PiChat回报L1–L4轨迹与部分数字正确，但答复质量仍有未通过项；此为联调侧证据，不冒充GeoAgent独立Provider验证。
- 联调侧报告13:26 L5连接失败。只读检查BFF PID52843启动于12:17:56、前端PID52867于12:17:57，当前仍同PID，本轮未在13:22–13:28重启，也未修改筛选。原24小时Token以新独立MCP会话initialize/geo_snapshot_list成功；没有检验或改变PiChat原连接会话/熔断状态，不能据此判定原连接仍有效或确定故障根因。未操作当前UI、未发问、未改PiChat、9172或生产，未提交/发布。
- 最终回归：新增SDK断言后两轮分别13 passed/1 ReadTimeout（11.96s、12.28s）；仅将隔离SDK契约测试httpx timeout从默认5秒改为30秒，最终14 passed（20.97s），git diff --check通过。未修改服务超时，未查明两次超时根因，不据此声明L5故障修复。当前运行仍未载入本轮代码，需协调重启后另验收。

### 2026-10-13：嵌入身份续期最小修复（前端，未重启）

- PiChat侧委托定位：finance:auth:request复用非空旧Token，finance:auth:required只置loading；BFF身份200、TTL1800秒，不是服务停止。只修Geo宿主，不改PiChat/权限/TTL/原配置，不发模型问答。
- 新增embedIdentity.ts状态机与useEmbedIdentity.ts生命周期Hook，App移除旧缓存/无清理投递逻辑并加入“重试连接”。检查exp及30秒临期，失效重新签发、并发single-flight；15秒请求超时及10秒ready超时给恢复反馈。关闭/连接变更中止请求、清定时器/监听，generation阻止旧投递，过期/畸形响应不缓存；固定origin/窗口校验复用既有trustedViewSource。
- 红绿：旧逻辑行为适配器6项全红，修复6绿；补清理和临期用例最终8 passed。装载缺模块的失败不计业务红测。范围桥5 passed、strict TS编译通过；前端build及diff检查最终结果追加于下。没有修改workspaceScope、会话API、筛选或输入行为。
- 生效边界：未重启8010/8011（PID52867/52843自12:17仍运行），Vite自动热更新前端，新增Hook可能使FastRefresh重置开发页组件状态。只读浏览器观察本地6/7标签均为风险监测、10/9快照，无嵌入面板；未刷新、重开、切筛选或发问，不能声明原会话/未发送输入保持已实测。只读标签6控制台error/warn为空、页面有内容且无框架覆盖；Browser插件未提供，使用CUA只读接口。真实空闲续期/重试UI/历史会话恢复仍待PiChat侧联合验收，不把单元测试或BFF200当通过。
- 后端采集语义新增仍未生效，继续等待联合Live结束协调重启；无提交/推送/发布。07同步续期契约和限制。
- 最终npm run build通过（2.43s），git diff --check通过；本轮不计真实续期UI验收通过。

### 2026-10-13：身份恢复联合UI部分复验（PiChat侧证据）

- PiChat联调任务回报：8010正常打开助手，不再卡获取身份；关闭助手→选原09-30快照→打开后，原会话第5轮内容及未发送草稿恢复。保持同一iframe切回9163e282/usiran/us_readiness/2026-10-08～10-09，宿主/iframe revision8一致，右侧26/2/0/282。证据/tmp/geoagent-identity-recovered-20261013.png。这是PiChat侧实际UI证据，GeoAgent本轮未重复操作或独立复查。
- PiChat补丁fad683402a处理embed API401 epoch guard与WS ticket401父页续期，定向51项/类型/构建通过，据其回报5585静态bundle已更新；未推送/MR。与Geo宿主8项身份状态机、5项范围桥验证分开记录。
- 只确认本次面板进入、旧会话/草稿恢复和范围同步；自然超过30分钟空闲续期仍未验，不标通过。跨域iframe自动发送不可用，等待用户点击原草稿；不代发、不重开、不改变当前页面/筛选、不重启8010/8011。后端采集语义新增仍待协调生效。混合工作树未整批提交，避免夹带前序重构。

### 2026-10-13：L5恢复轮同范围取数通过（PiChat侧联合Live证据）

- 用户在原8010 iframe实际发送；会话s-871cdebaf82c第6轮，run-b19c9e84-a479-4b41-b309-dae996f008a4，新panel20337b01-5ba1-4db4-a4eb-47873553b67e、revision10。PiChat侧回报真实模型输入及summary/risk_map两次调用均为9163e282/usiran/us_readiness/2026-10-08～10-09，2调用0失败，回答升级26，与看板26/2/0/282一致；原会话保留。截图/tmp/geoagent-l5-recovery-live-20261013.png。GeoAgent未重复操作或独立复查Provider轨迹。
- L5恢复后的同范围取数验收通过，不抹去原13:26失败。Q5仍未通过：实际2次调用却称4次成功；不能宣布全部答复质量通过。14:50截图至15:25发送虽超过30分钟，但没有捕获自动续期握手，长期空闲自动换发仍未单独验收。
- 未再改Key/连接配置、未重启，当前场景保持。后端采集边界新增仍待协调重启生效；不自动补发L6或其他问题。

### 2026-10-14：本地连接已发布chat.test，使用公网MCP验收

- 用户明确确认现有GeoAgent应用增加http://127.0.0.1:8010来源，并确认签发独立嵌入Key保存本地。通过chat.test管理UI只改GeoAgent应用：保留两公网来源、dock入口、geo-research-assistant和1800秒TTL，增加第三个来源8010。独立Key仅embed:identity、7天，原公网Key未撤销。凭证和原配置备份仅在仓库外0700目录/0600文件及本地持久配置，不记明文、不提交。未修改StockLink/LCF。
- 本地保存origin=https://chat.test、entry=dock，保留既有phone/product user映射；取消旧5585环境覆盖并重启8010/8011使配置生效。未改PiChat运行进程，未发布Geo代码；此次后端重启也载入此前collection_snapshot_boundary，但公网MCP仍旧v1.0.14，不能认为它已收到新字段。原24小时本地scope凭证未续期或转授远端。
- TLS发现及修复：Python3.14首先不信任内部CA，之后严格扩展验证拒绝Luozi Internal CA缺Key Usage。从系统钥匙串读取既有Luozi Internal CA，并与公共certifi根合并；新增显式GEOAGENT_PICHAT_CA_FILE，仅PiChat httpx请求使用SSLContext标准链验证（去掉VERIFY_X509_STRICT扩展约束），CERT_REQUIRED/check_hostname仍开启，不verify=False、不忽略浏览器警告。默认未配置时行为不变。测试test_integration_settings.py 19 passed（1.56s），diff检查通过。
- 本地启动：.venv/bin/python /tmp/geoagent-start-chat-test-20261014.py，CA文件与启动脚本在仓库外；保存配置是持久的，CA环境变量仍需本地启动器载入。身份签发HTTP200、token_present=true、Agent正确；无输出Token/手机号。iframe实际origin=chat.test，范围r1/r2 received。不得认为域名映射后来源策略自动适用于所有域名，未来chat.lhotsetech.com须另核对TLS及部署映射。
- 语音装配只读核对：已发布GeoAgent已有固定常驻“声音生成”，speech_generate/speech_operation/speech_reply在包内，无需重复配置/扩大其他权限。实际第二轮speech_generate失败SPEECH_PROVIDER_NOT_CONFIGURED；chat.test配置中心的streamingSpeech、glmTts及tencentTts Provider/credentialRef未设置。没有生成音频，不宣称语音通过；语音输入控件存在但未录音测试，需PiChat侧提供可用Provider及凭据再验收，不擅自改站点共享模型/凭据。
- 真实UI首问（00:40北京时间）：要求列公网快照并核对宿主0b18ddf4，MCP list/get两次调用，公网可见e6f55aa2、57fb9e48、fb975ea1，目标返回GEO_SNAPSHOT_NOT_FOUND。Agent没有静默切latest；但“看起来是本地看板标识而非落盘研究快照”归因不准确，该快照确在本地归档，仅不在公网数据源，另记质量缺口。
- 真实第二问明确指定57fb9e48f6a0fa1f10f8502d/usiran/空维度/空日期覆盖宿主：Agent工具返回与答复升级227/持续68/缓和139、范围3979，与Chrome已登录公网项目同快照风险面板一致。该通过只属于公网MCP连接/指定范围取数；本地面板0b18ddf4仍92/54/135/2301，不能把异源快照说成同看板数据通过。本地→远端PiChat→公网GeoMCP链真实调用成功，不是API代发或模型模拟。未独立复查Provider完整轨迹，未验全部业务用例、长期空闲续期或真实语音音频。
- 证据：/tmp/geoagent-chat-test-origin-saved-20261014.png（密钥弹窗关闭后）、/tmp/geoagent-chat-test-live-20261014.png、/tmp/geoagent-public-counts-20261014.png。右侧本地tab保留，未触发采集/修改公网工作快照/公开本地MCP。Geo远端main仍78a90c1a，当前本地重构未提交/推送/发布。

### 2026-10-14：整理本地重构并提交 GitLab MR

- 范围：业务语义共用配置、MCP 业务工具及工作台、撤销 Key 列表清理、取消额外管理口令（部署层仍负责站点访问控制，对外 MCP 保留 Bearer 认证）、嵌入身份续期与宿主范围/日期同步、采集恢复与旧快照材料边界、内部 CA 连接兼容及回归测试。
- 本地验证：`.venv/bin/python -m pytest -q` 全量219项通过（30.59秒）；`npm run build` TypeScript与生产构建通过（仅现有大分块提示）；`npm test` 状态机13项通过。首次直接运行前端测试因未设置临时编译路径失败，已补内存编译测试入口，无临时JS产物；Docker构建也执行该测试入口。
- 整理约束：`output/` 加入Git与Docker忽略，不提交截图、`.env`、数据库、CA、密钥或临时脚本；只提交源代码、共享业务配置、测试及设计/验收/移交文档。实际配置与凭据仍在本地后端数据目录或外部临时目录。
- 交付方式：独立 `codex/geoagent-mcp-business-refactor` 分支，目标main，通过push建立MR；不直接推main、不合并、不发布、不重启当前live。MR及流水线回执在创建后补记。
- 提交回执：代码提交94c6ded已推送origin同名分支。现有glab凭据为其他项目机器人，对GeoAgent API返回404；不更换凭据或扩大权限，改用现有SSH权限的GitLab push option创建MR。
- MR已创建：[GitLab !6](https://gitlab.lz.internal/yujunjun/GeoAgent/-/merge_requests/6)，目标main。现有API权限无法读取项目流水线，CI状态未核实，不记为通过；本地测试通过与CI/合并/部署保持分离。MR未合并，公网未发布。
- 验收边界：本地自动测试通过不等于全部公网业务场景通过；当前语音生成通过但嵌入播放失败，仍按下节及08移交跟进，不在本MR中宣称修复PiChat播放器。

### 2026-10-14：本地嵌入语音生成与播放Live复验（播放未通过）

- 用户已在chat.test配置腾讯TTS。管理页面只读显示已配置、鉴权及真实出音已验证，最近生成验证00:49:35、M4A42.2KB/5秒；这是管理Provider验证，不替代GeoAgent嵌入播放。本轮未重复Provider验证或改凭据。
- 保留本地8010原嵌入会话、看板0b18ddf4/usiran/空维度日期。已有第3轮用户“用语音和我说个你好”在配置前speech_reply失败，不抹去历史。本轮第4轮通过UI自然发送明确公网57fb9e48f6a0fa1f10f8502d/usiran/空维度日期覆盖宿主，要求重新取数及speech_generate；未用API代发、未静默切latest或写工作快照。
- 本轮可见调用回执两步：geo_workspace_summary（约3.8s，audit geo-audit-ddbff61f62688df7d8ee）及speech_generate。展开真实MCP回执核对snapshot_id/topic/query_scope，升级227/持续68/缓和139、范围3979，与Chrome已登录公网同快照看板一致。答复及实际“查看讲稿”数字同样一致；本地旁栏92/54/135/2301是另一个本地快照，不算同面板一致性通过。
- 腾讯语音operation speech-66289721-290d-4df5-bc42-64f08ba98009从accepted/playable=false推进至“已生成”，音频M4A479.4KB、55秒、默认音色；讲稿为本轮数字及限制说明。生成通过。两次点击“播放声音”均短暂显示暂停播放/00:00，随后“播放中断，点击播放可重试读取”，不能记播放通过。卡片有data-first-audio-at标记，仅说明播放器报告过首音频事件，不证明实际可听或完整播完；没有听觉完整核验。
- 未为掩盖问题重复生成。按下载UI一次尝试读取同一制品，12秒未收到浏览器download事件，不能判文件下载成功或确定服务端失败。只读检查宿主日志error/warn为空，iframe DOM没有普通audio元素；PiChat源码显示header-token客户端使用认证PCM/AudioWorklet，cookie客户端可走完整文件Range播放器。此为候选调查方向，不是已确定网络、权限、解码或缓冲根因；未放宽iframe权限、未修改PiChat源码/部署。
- 额外答复质量缺口：把待研判约89%解释为“自动分类尚未覆盖大部分记录”，unknown信号不直接等于分类覆盖缺失；MCP该计数本身正确。讲稿同步带入了此推断，须PiChat侧另核对，不能因数字一致宣称全部分析正确。
- 证据/tmp/geoagent-speech-generated-playback-failed-20261014.png及/tmp/geoagent-public-counts-verified-20261014.png。账本状态：MCP指定范围读数通过、语音生成通过、嵌入播放失败、下载未验证、真实语音输入/长期空闲续期未验。保留原会话和失败音频，没有存入报告库或触发采集/重启/发布；后续须PiChat排查相同operation的PCM请求状态、Worklet错误及播放器事件，再用同制品复测播放，不新增生成掩盖问题。

### 2026-10-14：MR !6 合并、v1.0.15 发布及公网嵌入 Live 验收

- 用户授权核对流水线、通过后合并及发布。实际检查时[MR !6](https://gitlab.lz.internal/yujunjun/GeoAgent/-/merge_requests/6)已由yujunjun合并，合并提交cddbe70a7f36daa22ee757e95d5f815b7eb309f4；未重复合并。[MR流水线5350](https://gitlab.lz.internal/yujunjun/GeoAgent/-/pipelines/5350)及[main流水线5352](https://gitlab.lz.internal/yujunjun/GeoAgent/-/pipelines/5352)均Passed，取代此前“API权限不足未核实”状态。通过已登录GitLab UI核对，无新增凭据或权限。
- 同步main并创建保护生产标签geoagent-prod-v1.0.15，指向上述合并提交。[生产流水线5354](https://gitlab.lz.internal/yujunjun/GeoAgent/-/pipelines/5354)验证作业28088通过，CI报告219项测试；镜像前端测试/构建层使用已通过缓存。手动执行部署作业28089，最终Job succeeded、流水线Passed；北京约05:53完成。沿用备份、已验证镜像、滚动更新和健康检查流程，未执行回滚。
- 发布日志确认两个公网域名https://geoagent.lhotsetech.com及https://geoagentd40d.sealosgzg.site：匿名MCP=401，Bearer初始化/工具列表/geo_snapshot_list真实调用=200。发布临时release-live Key已撤销，未写入仓库/账本。站点API仍要求部署层登录；未修改既有Agent授权或工作快照。
- 用户在Chrome完成公网站点登录后刷新，页面出现MCP工作台和新版范围同步状态。智能问答嵌入实际连接https://chat.lhotsetech.com/work-embed/geo-research-assistant；身份加载结束、可输入，宿主r2与嵌入“下轮看板范围”一致：57fb9e48f6a0fa1f10f8502d/usiran/全部维度/空起止日期。“已接收”不是模型已使用证明，后续以真实用户轮次取数核验。
- 在公网嵌入面板自然输入发布后连接验收问题（第28轮），要求重新调用MCP核对风险计数和采集恢复/旧快照边界。实际geo_workspace_summary调用参数limit=1、snapshot_id=57fb9e48f6a0fa1f10f8502d、topic=usiran；工具详情sourceExecutionStatus=succeeded、sourceCompleteness=complete，call_00_HVHjDPg5wMOK9FBPJBzh6059。两步调用13.9秒，整轮27秒，北京05:55完成；不是API代发或沿用旧回答。
- 新答复升级227/持续68/缓和139、范围3979、候选维度13/13，与同屏当前看板一致；答复明确采集成功、导入成功、目标范围补齐不同，旧快照材料不会自动回填，复核/翻译展示更新不等于新增材料。保持原57fb9e48工作快照，未触发采集或保存简报。额外说明最新快照是e6f55aa2不作为本轮切换。
- 状态：MR/main CI通过、生产发布通过、两个公网MCP认证及协议探测通过、公网Agent嵌入连接与本轮真实取数通过。未验全部业务场景、长期续期或语音播放；此前语音播放失败仍未解决，不能因本轮连接通过宣称语音通过。答复超过用户200字目标，简洁度仍需PiChat跟进。
- 现场证据：/tmp/geoagent-prod-v1.0.15-pipeline-20261014.png、/tmp/geoagent-prod-v1.0.15-embedded-live-20261014.png。临时截图不提交；仅账本文档追加提交，不重新发布文档提交。

### 2026-10-14：公网三个自然业务问题 Live（含首次失败和恢复）

- 环境：v1.0.15、公网Chrome嵌入chat.lhotsetech.com；固定57fb9e48f6a0fa1f10f8502d/usiran/全部维度/空日期，原历史会话继续使用。依次自然输入三个原问句，不用API代发或预填答案。页面重新加载后目录有轮次重编号，下列以输入、时点、调用ID标识，不用显示轮次当档案ID。
- Q1“本周哪些指标发生变化？”（北京05:58–05:59）：实际18调用（workspace_summary、两页metric_catalog、snapshot_get、14次metric_series）；正确导入周10-05至10-09，未切latest。布伦特调用finance:brent/start=10-05/end=10-09/max_points=8，同快照，call_00_1Od3idRI80ZZovqGhqwA4832，read_at=2026-10-13T21:58:50.748985+00:00。首次仅抽核四项，随后补齐14项：真实看板图表Increment/Decrement定位首点并对照末值，全部日期与显示值一致（显示舍入不等于原值差异）。
- Q1首→末：布伦特100.32→103.47、WTI89.43→90.89、黄金4156.8→4217.7、美元102.17→102.062（面板舍入102.06）、美债5.31→5.22%、实际收益率2.95→2.87%、两路盈亏平衡2.36→2.35%、HY312→309基点、道指51267.9→51231.64、XLE63.45→65.24、ITA206.98→204.88、VIX15.52→15.41、TA1254063.58→3934.01。收益率变化用基点，不代算null百分比；17项末观测在窗口前不冒充无变化。所有图表选点已恢复末值，未改全局日期。内容通过；18调用、72秒、未使用geo_metric_changes记效率问题，不推断该工具一定未装配。
- Q2“当前判断有哪些反证？”（北京06:01–06:02）：重新analysis_view(limit=5)和workspace_summary(limit=1)，同快照/usiran。analysis调用call_00_p4DCb8xLOhHUwlOQpgT49709，完整详情sourceExecutionStatus=succeeded/sourceCompleteness=complete，原文126221字符。看板三情景“研究假设，未判定成立”及失效条件均匹配：交付中断/撤出/停火、行动局限/夸大战果/无持续后续、仅意向/未降温/协议破裂。答复正确区分not_evaluated/unknown与无反证、规则与事实、不同战区线索。32秒完成；新闻引用明确沿用旧回执，不能记本轮正文独立重读通过。返回体12.6万字符是体积风险，不能单凭大小证明超时根因。
- 菜单复核数据源后重进嵌入出现“未确认身份”，UI重试未恢复；保留截图后刷新页面重进恢复，旧对话仍在。未改Key/权限或重启服务；加载/网络/握手根因未定位，不能记为已修复。
- Q3“还缺什么数据？”（北京06:04–06:05）：usiran概览成功，read_at=2026-10-13T22:05:19Z；另外3次（all概览及两页目录）30秒超时，额外重试no healthy upstream。答复把未取到与缺失分开、标注旧回执；首次部分通过。独立匿名诊断MCP仍401、PiChat页面200，仅证明入口响应，非完整认证路径健康。
- 恢复输入：“还缺什么数据？请按当前看板范围重新读取刚才失败的目录和全主题告警，区别本轮结果与旧回执；不要切换快照、启动采集或保存简报。”（北京06:06–06:07）。顺序调用目录offset0/limit16、offset16/limit15（usiran，同快照，1.3s/1.4s）及all概览limit1（21.8s，call_00_ET_BvZK6yB5pmP0bGMxH3939596），三次均成功、整轮41秒。补齐31项目录与全主题7项告警（6无有效值、1隔离），未把当前主题0告警混成全主题7；hormuz油轮/载重吨/占比有效零值不是缺失。
- Q3核对：数据源美伊8维度全部部分候选，记录1650/1205/97/274/625/789/456/1046匹配；全框架13/13不是数据完整。指标菜单31项战备7/金融24；14项行情、海峡10-04、库存10-02、财政08-31时效分层一致，未列指标不等于无原始材料。复核0/反证未评估/时间轴及独立核验不足正确，五类资产缺口是规则建议，不作因果事实。Q3复测核心内容通过，不抹掉初次失败，也不算间歇根因已修复。
- 本轮未确认数值/业务范围计算缺陷，没有盲改业务代码或发布。余项：PiChat工具装配/选择效率、响应体精简专项、重新进入身份超时与上游失败链定位；新会话、日期切换、新闻正文独立重读及语音仍须另验。浏览器完整详情核对参数/执行状态，AX长文本截断下未独立导出全部JSON，不声称逐字比对所有原始回包。
- 证据仅/tmp：geoagent-q1-public-live-20261014.png、geoagent-q2-public-live-20261014.png、geoagent-q3-public-partial-20261014.png、geoagent-q3-public-recovered-20261014.png、geoagent-panel-reentry-timeout-20261014.png；14项首末点AX、工具及答复文本同目录保存。保留历史失败，不用恢复成功覆盖。

### 2026-10-14：超时及大返回体专项定位与修复（v1.0.17已发布，限定回归通过）

- 实际SSH只读检查chat.test解析到192.168.100.206，Nginx配置server_name同时含chat.test/chat.lhotsetech.com，转发127.0.0.1:9292。未把公网映射误当另一个PiChat实例，未改该端源码、凭据或配置。
- PiChat实际runner日志06:05:34三条对应geo_workspace_summary和两次geo_metric_catalog：mcp.request.idle上限30秒、由本端掐断；06:05:40记录Streamable HTTP POST no healthy upstream。专用Agent装配日志显示site:geoagent白名单连接18→1，不能据未选择新工具断言没有装配。
- GeoAgent公网上工作台读取后端持久调用审计：概览05:55:12=13439ms、05:58:36=13840ms、06:01:50=13933ms、06:05:19=14414ms、06:05:41=22207ms、06:07:02=21539ms；均ok。06:05:41回执geo-audit-8c731901808d4e8a92e5cf4323cbf16b晚于PiChat截断，说明客户端超时不等于工具未执行。当前无Sealos kube上下文，未获取Pod/OOM/Ingress日志，不能断言无健康上游由Pod重启造成。
- 本地真实快照剖析_workspace_counts：4384记录反复event_metadata共39456次、136万次正则搜索。修复为每记录一次，信号应用最新复核与事件分类分开保留；与旧实现完整投影一致的比较回归另记。所有同步MCP投影经线程池执行，避免SQLite/JSON/分类阻塞ASGI健康及身份路由；不通过单纯扩大PiChat30秒上限掩盖问题。
- 大返回体确定缺陷：analysis important_metrics继承完整history，真实本地5项122894字节。改为有界摘要9152字节（减少92.6%），保留current/previous/delta/unit/quality/source及业务语义，明确history未包含、点数非连续覆盖、详情工具与来源截断标记；不修改原始快照，不做裸字符串截断或丢掉证据边界。补参数描述引导周变化批量工具。
- 身份手动重试原来只重新发送同一iframe的Token，不能恢复未加载/异常frame；改用户点击重试才更换iframe连接版本，保留原scope与持久会话，不静默刷新正常面板。身份签发/ack/WS票据仍分层，PiChat侧401需按调用者和域名关联，不能混用其他浏览器的失败日志当本轮根因。
- 回归新增：概览单次分类/复核覆盖、不带10000点历史且不改缓存对象、同步投影不阻塞事件循环，原协议测试验证授权和上下文在线程池中仍正确。不更换Key、不放宽TLS/源权限，不记录日志正文或Token。
- 本地同一真实快照旧/新投影逐字段一致，单次计数1.853s→0.210s；此为本机复现，非生产速度保证。后端全量222项通过（29.56s）、前端13项及生产构建通过、diff检查通过。将按独立MR/保护生产标签发布再回归，不把本地测试当外网修复完成。
- MR !7通过流水线5357（222测试）合并b3d9c966；生产v1.0.16/5359验证、部署28096成功，双公网MCP真实认证调用通过。外网06:27真实新轮次4并行调用全部成功，目录16/total31/has_more=true，概览美伊3979及227/68/139、all6978及318/92/196分开；情景口径未改变。analysis调用call_03_UjpIzduvfISAWP8uJOTP9743同快照/usiran/limit5，完整回执字符126221→23480、字节137831→37312，未截断/执行成功。并行后端概览审计11556ms和13475ms，无30秒超时，但并未降至理想水平；不能将本机8倍改善宣称生产8倍。
- 二次定位：多个工具仅为latest lineage却调用snapshot_summaries()，每次解码全部历史快照完整payload；本地10快照约0.912s。改只读取store.snapshots()的ID/created_at元数据，保留真正列表/统计工具的完整计算；不会将latest替代用户所选快照。新增禁止lineage读取归档payload的测试，后端223项通过（22.09s），待再次发布回归。
- MR !8/c30c9fb4通过5361（223项）后合并751b0419；保护标签geoagent-prod-v1.0.17/流水线5363验证28106、部署28107通过。06:35双公网域名initialize/list/snapshot-call=200、匿名401，发布临时Key已撤销，现有身份及应用Key保留；未改PiChat端配置或凭据。
- 06:36公网原嵌入会话自然输入第二轮并发回归，实际4调用：美伊概览7.9s、all概览8.4s、目录第一页5.1s、情景分析8.1s；全轮24s结束，4项均成功、无30秒截断。范围仍57fb9e48/usiran/空维度日期，另一次all明确为用户要求的对照；概览3979/227/68/139、all6978/318/92/196、目录16/total31/has_more=true、资产5/情景3与面板口径一致。没有切快照、触发采集或保存简报。
- 新情景调用call_03_ET_292aA08JM86zDpgQLdN36272的真实回执sourceExecutionStatus=succeeded/sourceCompleteness=complete、23480字符/37312字节，保持有界摘要；不是沿用06:27旧回执。服务端持久审计06:36:18=6911ms（geo-audit-33136fb46d1244ed926e6f66823d4ec6）、06:36:19=8221ms（geo-audit-7916e2f50f52476b93d56225b9963c6c），较v16的11556/13475ms下降；浏览器耗时与审计口径不同，不以累加29.6s当墙钟24s。206 runner-error近期日志未出现06:35–06:39对应tool-failure/timeout/healthy-upstream；只证明此限定窗口，不是长期稳定性证明。
- 上线后刷新一次正常连接，随后“数据源→智能问答”“情景推演→智能问答”两次重进均正常：输入框可用、r2范围已接收、原持久对话保留。未再次触发原身份超时，因此手动重建iframe修复有代码/构建证据，异常握手恢复仍待PiChat联合故障复测；不能宣称身份超时根因已全部闭环。
- 尚未关闭：无Sealos Pod/Ingress故障期日志，no healthy upstream根因仍待关联；PiChat deadline/并发重连与Host/Origin/WS握手移交08。最终答案仍带“与看板一致，无覆盖差异”和“本轮无任何counter被核验为已发生”易混措辞，已将应分scope及未完成核验的边界交给PiChat。此次总上下文显示约83万输入词元（429440缓存+402939非缓存），单次情景返回缩小不等于历史上下文已压缩；不在GeoAgent侧删对话掩盖问题。
- 证据仅/tmp：geoagent-v17-parallel-live-20261014.txt/png、geoagent-v17-server-audit-20261014.txt；密钥、数据库、临时日志与截图均不提交。后续应在PiChat修复后联合复测冷启动、重进异常恢复、自然周变化路由及连续并发；本轮不扩大权限、不增加超时、不关闭TLS。

### 2026-10-14：GitHub main 双向历史整合（仅代码同步，不发布）

- 用户要求先合并GitHub独有改动、解决冲突并测试，再将本地main同步至GitHub。整合前本地cea3706、GitHub f40534a，共同祖先4a5c786；GitHub有7个独有提交。未强推或丢弃远端提交，在codex/github-main-integration保留双亲历史。
- 8个冲突文件均逐项整合：保留现有PiChat嵌入、项目MCP认证/17工具、快照选择与轻量地图，同时保留GitHub新闻源配置/导入、Windows批处理编排和报告抽取修复。修正新闻路由mcp_config与现有AuthConfig同名覆盖、首次本地采集产物同步前误选demo、自定义Windows端口未传给导入器；后台采集完成只提示新版本，不静默切当前工作快照。显式新闻导入后的选择沿用workspace-selection同步。
- 保留启动/停止脚本创建时间+命令路径的进程身份校验，为新增爬虫管理进程同样记录和校验createdAt，避免PID复用误停。补4项合并兼容回归：项目MCP与新闻配置共存、工作快照不被状态替代、首次产物先同步再选根目录、Windows脚本身份及端口契约。
- 最终后端231 passed、1 skipped（19.00s；Windows真实cmd批处理在macOS跳过），前端13 passed及tsc/Vite生产构建通过；合并差异空白检查通过。只使用隔离测试数据，未调用实际新闻服务、启动采集或重启当前live；Windows进程树实际执行仍需Windows环境验证。未提交密钥、data、临时日志或构建产物。
- 本次目标是本地main与GitHub同步；不推生产标签、不触发公网部署，GitLab origin/main未随此次请求同步。生产继续v1.0.17，不将合并测试当作部署/外网验收。

### 2026-10-14：合并版 GitLab 同步及外网发布（v1.0.18已上线，独立live通过，CI探针初次失败保留）

- 用户追加授权同步GitLab、核对CI、通过后发布与外网live。f5e9e76已推GitLab；5371/28140失败，未发布该失败构建。真实容器报告228 passed/2 failed/2 skipped：Windows脚本契约测试读取/app/start.ps1失败；增量同步同大小且同时间戳内容变化被误跳过。
- 镜像补COPY start.ps1/stop.ps1供静态契约测试，不在Linux启动Windows采集；同步在同大小/时间戳条件下增加分块内容比较，避免仅凭stat忽略真实改写。测试强制同大小同mtime复现，不用sleep或跳过断言掩盖问题。本地全量231 passed/1 skipped（20.86s），待新CI及实际发布/live补记。此前前端13项与构建通过且本次无前端变更。
- 修复提交36ce052同步GitLab后main流水线5372通过；保护标签geoagent-prod-v1.0.18/5373验证28142通过（232项报告）。首部署28143因真实采集锁在备份阶段失败，没有替换线上镜像。公网管线批次manual-20261014T003957Z-bf6a8e94日志持续更新，18源最终完成、08:52:49导入和批次结束，生成d3cfb20d…；未删锁或停止采集。工作快照仍e6f55aa20d3fdc1d0f1a1dae，没有静默切新快照。
- 批次结束后重试28145完成rollout、运行态配置保留原身份/Key、API健康200，v1.0.18已实际应用；其后geoagent.lhotsetech.com匿名401及认证initialize/list/call200。Sealos域名initialize断言失败，原日志未打印实际HTTP状态，无法从AssertionError推断401/502或根因；release-live临时Key已撤销。5373/28145仍保留失败状态，不将后续独立成功改写成CI全绿，也不重复运行已存在release记录的deploy或触发回滚。
- 用户指定复用老Key、不再新建：只读取PiChat现有site:geoagent连接的Authorization，在206内存中按原Secret Store作用域解密并向两个既有GeoAgent公网地址发送只读协议调用，没有输出/落盘Key，也没有写配置/Secret Store或撤销原Key。实际配置endpoint=Sealos域名。两个域名独立anonymous=401、initialize=200、tools/list=200/17工具、geo_snapshot_list真实调用=200/ok；调用耗时约1935/2271ms。这是发布后独立恢复验证，不证明08:54探针失败根因已关闭。
- 09:47–09:49嵌入曾出现“PiChat未确认身份/智能体加载尚未完成”，显式重试仍先失败，随后输入框恢复。PiChat对应时间窗身份签发、work-embed/auth/票据路径可见200，但多个调用者并行，不能把这些状态码视为当前frame握手已完成证据。原失败截图/tmp/geoagent-v18-identity-failure-20261014.png保留，未改PiChat端或放宽来源/TLS。
- 09:52在公网Chrome实际嵌入自然输入发布连接验收，独立新geo_workspace_summary约2.6秒、整轮8秒，read_at=2026-10-14T01:52:54.241158+00:00/request_id=20；参数e6f55aa2/usiran、limit3、compact=true，维度/日期未限定。答案231/60/126、unknown3480、范围3897、补充22与同屏一致，正确区分采集恢复/导入/旧快照补齐，不触发采集或保存简报。回执与截图/tmp/geoagent-v18-agent-live-20261014.txt、geoagent-v18-live-receipt-20261014.txt、geoagent-v18-live-final-20261014.png。新新闻配置只读接口/api/mcp/config已返回配置形状，未经实际配置/导入，不记新闻接入live通过；浏览器曾显示旧布局的缓存/加载差异尚未独立闭环。
- 当前结论：main验证通过、生产镜像已上线、现有Key双域MCP及真实嵌入问答独立通过；部署CI后置探针初次失败与身份间歇问题仍开放。后续应补探针逐阶段HTTP状态/错误码的脱敏观测及PiChat握手关联，不靠增加超时、强行刷新会话或删除失败记录“通过”。

### 2026-10-14：后置 MCP 探针诊断修订（本地完成，Runner验收待执行）

- 原部署流程本身会签发一次性release-live Key、注入探针进程，结束后撤销；28145同一Key在第一个域名通过、第二个域名initialize失败，不能直接归因“没有Key”。现有PiChat长期Key不变，未复制到CI变量或仓库。
- 206节点另用现有Key和Python urllib重现旧探针请求方式（相同User-Agent、重复id及缺少initialized通知），两个域名仍为401/200/200/200，调用约1673/1675ms。因此未在该节点复现08:54失败；206不是GitLab Runner，不能以此关闭网络来源、时点或临时凭据差异。当前214节点从本机不可达，未获得故障期Ingress/Pod证据。
- 已确认探针自身缺陷：HTTPError响应状态和内容类型被丢弃，仅剩断言；初始化通知、协商协议头及会话头未完整处理。修订为逐阶段记录HTTP状态、受限错误码、字节数及耗时，传播会话/协议头、唯一请求id、发送initialized，兼容JSON/SSE并限制返回体。协议成功与业务结果成功分开，任一失败立即退出，不自动重试；不输出凭据、响应正文、Session ID或错误消息。
- 新增7项隔离回归覆盖生命周期、503首错保留、不重试、RPC错误脱敏、SSE匹配、非法/过大返回、业务失败不冒充探针通过。专项7 passed，最终全量238 passed/1 skipped（43.82s），diff检查通过。尚未发布该修订，不声称历史根因已经修复或生产流水线已通过。
- 身份确认仍交PiChat按同一frame关联ready/ack/auth/WS日志，沿用08移交文档；本轮不改其配置或源码、不重启当前live。下一轮Runner必须持有效Key；用户已确认沿用原一次性release-live Key流程，现有PiChat Key不变，随后发布采证。
- 修订67c7a26通过MR !9/流水线5379（239项报告）后合并d906125；main5380通过。保护标签geoagent-prod-v1.0.19/5381验证28173、部署28174均通过，10:25:01 rollout成功，10:25:20 Job succeeded。没有重试部署或自动重试探针，没有回滚。
- Runner .214本次实际双域首轮：匿名401、initialize200、initialized202、list200/17工具、snapshot-call200且业务ok。lhotsetech域调用2152ms，Sealos域1768ms；10:25:19发布一次性Key撤销，PiChat现有应用Key与身份保留。真实阶段日志已打印HTTP状态/内容类型/大小/耗时；不记录正文或凭据。生产流水线5381最终Passed，证据/tmp/geoagent-v19-pipeline-20261014.png。
- 已关闭的是探针协议与可观测性缺陷，并完成新版本Runner首轮验收；旧28145故障时HTTP状态无法追溯，未证实其历史根因，不能说“初始化通知就是旧故障原因”。v18失败历史保留。身份间歇问题不因新发布MCP通过而关闭。
- 10:27重新打开公网Chrome d40d页面→智能问答，身份连接正常、r2范围接收，原两轮对话保留；自然输入新验收问题，要求重新取数、不采集/切快照/存简报。实际geo_workspace_summary调用snapshot_id=e6f55aa20d3fdc1d0f1a1dae/topic=usiran/limit1/compact=true，维度和日期不限；request_id=2/read_at=2026-10-14T02:27:54.023544+00:00，2.6秒，本轮约6秒。回答231/60/126/unknown3480/范围3897/supplemental22与同屏看板一致，未沿用09:52旧回执。此是发布后正常连接与新取数验收，不是原间歇身份故障注入复测。
- 完整详情定位本轮会话s-1571760299a3、调用call_00_0s1bXhfUQwNFYKhpf9lP7214：10:27:51.519 start→10:27:54.088 end/2.57s，结果未截断、外层status=ok、内层ok=true及新read_at。AX结果正文受展示长度限制，未独立导出所有JSON字段，不声称全部回包逐字核对。现场截图/tmp/geoagent-v19-embedded-live-20261014.png；未提交临时文件/密钥，身份专项08移交已补责任边界。

### 2026-10-16：生产 PiChat 身份恢复阻塞修复（v1.0.20已发布，限定回归通过）

- 用户报告生产连接失效。使用授权站点登录后，生产连接配置签发验证成功，但智能问答停在“正在恢复投研工作台”，宿主报“PiChat未确认身份”。07:37 UTC身份签发200，独立原身份Bearer的/auth/me及/api/agents均200且GeoAgent可见；没有以这些200代替frame就绪。未改Key/来源/TLS或PiChat源码。
- 确认本项目错误：useEmbedIdentity丢弃签发响应中的真实user，使用产品绑定ID构造finance:auth:token.user。现行PiChat重复投递去重比较token和user.id，/auth/me已经写入真实用户后，本项目250/750/1500ms重发的伪用户会再次覆盖已验证用户并增加auth epoch，导致验证结果被废弃或sameIdentity=false永久阻挡。产品user ID不是PiChat内部用户ID。
- 改为发送同一次后端签发返回的token与真实user，校验userId=user.id、当前token与当前frame匹配，不允许缺少真实用户时回退伪造身份。原产品绑定只用于后端签发，不承担PiChat用户身份。保留来源校验、重复发送、有界超时、原会话及权限，不通过增加超时掩盖竞态。
- 新增真实用户重复投递不覆盖、缺失/不一致身份失败关闭测试。前端15项测试及tsc/Vite生产构建通过，后端238 passed/1 skipped（21.17s）、diff检查通过，生产发布/live待补。此次修复不宣称旧版本所有间歇握手原因已解释；以新版本冷启动/重进/真实问答验收为准。
- MR !10/432745e首次流水线5575作业28749在镜像依赖安装阶段失败：uvicorn无可用下载版本（07:43 UTC），没有测试报告且未部署。只重试验证作业28750一次，已能下载uvicorn并继续依赖解析；保留首次失败，不能说本轮修复了解决包源可用性的根因。没有绕过测试或对失败镜像发生产标签。
- 重试作业28750实际用约12分钟完成依赖下载，容器237 passed/2 skipped（46.71s），07:56:54 UTC succeeded。MR !10随门禁通过合并fb1058e；main5577通过后推保护生产标签geoagent-prod-v1.0.20。完整生产与live结果继续补记。
- 生产v1.0.20/5579验证28757、部署28758通过（08:00:55 UTC Job succeeded），没有重试生产部署；双域匿名401、initialize200、initialized202、list200/17工具、snapshot-call200/ok，工具调用约2215/1924ms。发布一次性Key撤销，原PiChat应用Key/身份配置保留；没有改PiChat服务或工作快照。截图/tmp/geoagent-v20-pipeline-20261016.png。
- 生产Chrome硬刷新新前端后首次打开智能问答，自动完成握手、r2范围接收、输入框可用；“数据源→智能问答”再次成功，均未点重试、未清会话，原34轮对话保留。当前仍57fb9e48/usiran/全维度/空日期，未为测试切换快照。PiChat进程PID2340531/启动时间仍10:07:42（节点本地时间），本次没有重启其进程。修复前截图/tmp/geoagent-auth-before-20261016.png保留。
- “情景推演→智能问答”第二次重进同样就绪。原会话自然发送第35轮新问题，实际mcp__geoagent__geo_workspace_summary(snapshot_id=57fb9e48f6a0fa1f10f8502d,topic=usiran,limit=1)，调用call_00_ET_YAXsRw2j6uhblePX2dbb7113，sourceCompleteness=complete/sourceExecutionStatus=succeeded，14197字符/22477字节；约2.5秒，整轮11秒，本轮read_at=2026-10-16T08:04:28Z。回答227/68/139/unknown3545/范围3979/supplemental22与同屏看板一致，没有沿用10-14旧回执。未独立导出所有返回字段，不把模型“无覆盖差异”措辞当工具回显query_scope证明；该答复质量边界继续由PiChat改进。
- 本次关闭：本项目伪造用户导致的重复身份投递覆盖缺陷，且完成生产冷启动、两次重进及真实取数回归；不宣称所有历史间歇、长期空闲续期或语音问题全部解决。新截图/tmp/geoagent-v20-embedded-live-20261016.png，AX记录/tmp/geoagent-v20-live-20261016.txt。账本与08移交同步，密钥、密码、临时文件不提交。

### 2026-10-20：客户 1920×1200 聊天高度修复（本地 live 验收）

- 用户反馈客户1920×1200屏幕中对话内容高度不足，要求启动本地并live。检查发现宿主聊天区域使用固定100px顶部偏移，且标题、连接标识与范围状态占多行；浏览器工具栏和系统缩放进一步减少实际CSS视口。改为跟随菜单/筛选栏实际剩余高度的flex布局，隐藏聊天模式下占位内容；标题压到单行优先，详细身份及范围状态保留在“连接与范围”折叠项，没有删除同步状态或改变业务范围。
- 增加“放大对话面板/还原对话面板”，仅切换同一aside的CSS类，不改iframe src/key，不重新签发身份或清理会话。低于900px视口时适度压缩宿主头部/菜单/筛选间距。关闭助手恢复地图并清除放大状态。仅修改本项目src/App.tsx、src/prototype.css，没有修改PiChat内部字体或输入框。
- 本地使用scripts.start_local同时拉起8010前端、8011后端及同后端/mcp。初次嵌入502来自启动环境缺少chat.test内部CA；从系统钥匙串读取已受信任Luozi Internal CA的公开证书，以/tmp/geoagent-local-internal-ca.pem传入GEOAGENT_PICHAT_CA_FILE重启本次本地进程后，嵌入完成身份握手、范围r1已接收、原4轮对话和输入框可用。未换Key、不修改来源权限、不关闭TLS或忽略浏览器警告；临时CA文件不入库。
- 右侧IAB真实渲染测量iframe高度：1920×1200视口普通模式960px；1920×1080（额外模拟浏览器工具栏占用）普通840px、放大1013px；1536×824普通622px；1280×720普通518px、放大653px。后两档主文档无纵向溢出，截图显示输入区和工具栏完整可见。尺寸是CSS视口模拟，不冒称实际客户OS缩放/浏览器工具栏精确复刻。
- live操作展开/收起连接范围、放大/还原，未发送的“布局验收草稿（不发送）”始终保留；结束清除测试草稿，未发送新问题、触发语音或改变工作快照0b18ddf4。恢复浏览器默认尺寸并保留右侧本地页。截图/tmp/geoagent-chat-1920x1200-20261020.png、/tmp/geoagent-chat-live-20261020.png，仅作本机证据，不提交。
- 验证：前端15项通过、tsc/Vite生产构建通过；后端集成配置/MCP服务/工作台针对性回归37 passed（8.04s），git diff --check通过。前端测试非几何断言，实际布局结论以本轮浏览器测量和截图为准。此轮未提交/push/MR或公网部署，生产仍需后续发布才能获得布局修复；客户实体机最终验收尚未执行。
- 用户随后授权push/MR：再次复跑前端15项、tsc/Vite构建及后端37项（7.80s）均通过；基线main与origin/main同为149c7e6。整理到codex/chat-panel-viewport-height，仅提交布局源码和本账本；不提交本地CA、密钥、截图、构建产物或运行数据。MR/CI交付状态以GitLab实际回执为准，此授权不包含合并main或公网发布。

### 2026-10-20：MR !11 合并后的公网核对（新布局尚未生效，验收未通过）

- 用户称已发布并授权站点登录/验证。实际GitLab MR !11已合并为31a24ed，MR流水线5688及main流水线5689均通过；5689列表只有verify阶段，当前可见最近含deploy成功的生产流水线仍为5579、geoagent-prod-v1.0.20。仅核对，没有触发新标签、部署、重试生产任务或改连接配置。
- 使用授权凭据做独立HTTPS只读检查，首页与/healthz均200，认证凭据可用；首页加载index-C032W5lc.js、index-CRDUkFYF.css，CSS没有chat-panel-expanded/chat-panel-heading。健康200不代表PiChat握手或业务取数通过，更不是本轮布局发布证明。
- 右侧IAB因浏览器级Basic认证无法进入，用户追加明确授权Chrome CDP；按此切换既有本机Chrome CDP9333，在独立内存Context中仅对该GeoAgent origin提供HTTP凭据，不改其他标签页/权限，不落盘凭据、不忽略TLS。真实1920×1200页面加载后仍为旧的多行标题，没有“放大对话面板”和chat-panel-heading；iframe实测y294、高893px，与本地新版y227、高960px不同。实测与静态资源、CI层证据相互印证：当前入口尚未提供此次布局修复。
- 同次冷启动页面选择a1a2233b，快照列表可见，但业务区域持续加载，iframe尚未完成加载，随后显示“PiChat身份获取失败或超时”；未发送业务问题，不能宣称嵌入/MCP真实问答验收通过，也不能将此直接归因为PiChat服务。浏览器观测尚无对应失败响应码，进一步需要关联业务API及身份请求日志。一次重试的自动化调用自身超时，不能将工具超时当作服务端根因证据。
- Chrome截图/tmp/geoagent-prod-layout-20261020.png保留旧标题与身份超时的实际状态，仅本机证据，不入库。需先正式部署包含31a24ed的版本，再重复1920×1200/低高度、放大/还原及身份连接验收；客户实体机结果仍未验证。本轮仅核对与本地补账本，没有擅自发布或扩大权限。

### 2026-10-20：连接超时采证与宿主恢复修复（本地通过，待正式发布）

- 按用户要求继续定位，不以自动恢复代替修复完成。公网认证API实测：status 200/5.17s、public配置200/0.51s、embed-identity 200/3.87s。Chrome另一轮身份200/4.49s；workspace首部约4.91s、完整910116字节约26.23s。区分身份签发、静态资源/数据传输和PiChat会话就绪，不将整个加载耗时归因为Key失效。
- 206节点实际Nginx日志：14:35:52身份签发200、14:35:53嵌入/auth/me 200、14:35:54及14:36:04 ws-ticket 200；平台日志14:36:06对应GeoAgent嵌入用户WebSocket connected。宿主期间曾先显示“未确认身份”，随后收到ready并加载原会话。日志未发现本次对应的身份401；不引用其他管理用户或其他Agent的日志作为本轮证据。
- 确认宿主缺陷1：`.pichat-embed-status`继承`pointer-events:none`，错误层里的“重试连接”按钮不能接受真实鼠标点击。Chrome actionability明确报告iframe intercepts pointer events。修复错误层为可交互、窄屏可换行，不使用force click伪装验收。
- 确认宿主缺陷2：iframe onLoad原来通过默认force=true无条件再签发身份，并清空范围桥；load晚于认证/会话ready时会重新轮换Token、重置就绪状态。改独立frameLoaded事件：复用pending或有效已签发身份；已ready时幂等，不重置已确认范围。显式auth-required、过期或用户重建frame仍正常重新签发，未放宽来源/当前frame/服务端user校验。
- 补齐竞态：ready在本次有效Token递送期间到达，不再被未settle的Promise误丢弃；无Token或正在取得新身份时的旧ready仍拒绝；同步ready后不遗留超时定时器。10s未收到chat-ready的提示明确为“身份已签发，但会话尚未就绪”，不是宣称身份失败。15s签发/10s就绪预算均未增加。
- 自动回归18/18前端、tsc/Vite构建、23/23后端集成配置与工作台回归、diff检查通过。新增覆盖pending与已就绪frame-load、有效Token重复递送、显式失效仍更新、同步ready不留定时器、无身份ready拒绝。
- 本地Chrome CDP9334、1920×1200：仅拦截本地身份接口注入一次503（隔离的负向测试，不算公网故障），真实点击“重试连接”后移除注入，真实本地BFF与chat.test身份/me/ws-ticket均200；范围r1已接收、错误消失、无pageerror。就绪后模拟迟到load，身份请求计数保持2（1次注入失败+1次真实成功），范围状态不丢失。截图/tmp/geoagent-session-recovery-local-20261020.png，仅本机。
- 公网旧版在独立Chrome CDP9334中冷启动与重进两次均出现真实chat-ready与范围回执；两次身份至ready分别约3.12s/2.23s。此为当前可恢复性的证据，不是新版已部署证明。原共享CDP9333中的后续跨域文档曾持续pending且未收到HTTP响应，独立实例未复现；尚不能断言其具体浏览器/网络根因。
- 本轮未改PiChat服务/应用Key、未重启生产、未改变工作快照或扩大权限。生产仍是旧资源；新增修复及已合并的高度布局需要正式发布后再做线上闭环。保留此前失败证据，不把后续成功覆盖为首次通过。
- 本地真实短问答第5轮：输入“连接验收：请只回复‘会话已连接’，不要调用工具，也不要生成语音。”，2秒完成并显示“会话已连接”；不是只看历史对话就判通过。1920×1200收起连接详情后iframe960px，输入区完整，alert=0，console error/warn与pageerror均空。此短问答只验收会话，不代表MCP业务分析重新验收。代码位于codex/embed-session-recovery。
- 用户随后明确授权“提交 MR、合并并发布验证”：本轮按完整测试→GitLab MR→通过后合并main→受保护生产标签/人工部署→公网live执行，包含已合并的高度修复。保留现有PiChat Key/身份/权限和工作快照；部署探针沿用此前授权的一次性release-live Key签发/撤销流程。发布结果与验收证据在后续条目另记，不将此授权或本地成功视为公网已修复。
- 提交前复跑：前端18/18、tsc/Vite构建通过；完整后端在独立临时GEO_DATA_DIR/COLLECTOR_DATA_ROOT并导入demo快照后237 passed、2 skipped（66.13s），未读写真实运行数据。仅暂存本轮5个源码/测试文件及账本，不含密钥、环境配置、CA、截图或临时数据。

### 2026-10-20：v1.0.21 已发布；普通布局可用，重绘与间歇连接问题仍开放

- 修复提交bf7a7d4、MR !12、MR流水线5691通过后合并main 7a4f3d3，main流水线5692通过。生产标签geoagent-prod-v1.0.21、流水线5693，验证29361及部署29362均成功；07:09:38 UTC rollout完成，07:09:57 Job succeeded。容器后端237 passed、2 skipped。没有将main验证流水线5692误认为生产部署。
- Runner双公网域名首轮匿名401、initialize200、initialized202、tools/list200（19830字节）、snapshot-call200（2608字节），两次实际工具调用2888/2370ms；07:09:56一次性release-live Key撤销，PiChat原Key/身份/工作快照未变。生产前端确认index-CyVhLxeB.js、index-BBHofx85.css及新版紧凑标题/放大按钮已生效，不只是健康检查成功。
- 根据用户授权使用Chrome CDP独立测试上下文，站点认证仅在内存中；Browser插件不可用，IAB浏览器级认证不可操作，未绕过TLS或关闭浏览器安全检查。冷启动真实身份200发生07:10:28.627，chat-ready发生07:10:45.952，相差17.325秒，超过宿主10秒就绪预算，随后恢复不能算此问题已修复。Chrome实际记录WebSocket `net::ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS`，另一次菜单重进iframe文档也曾延迟约一分钟才加载；不能将两个阶段直接归为同一根因。
- 同机只读解析chat.lhotsetech.com得到fc00::3ed及198.18.5.156，chat.test为192.168.100.206。Chrome155中嵌入文档local-network-access/local-network/loopback-network策略均不允许、权限状态denied；这是当前测试环境证据，不是客户设备证据。未确定代理产品、故障连接最终地址或所有延迟原因，未擅改DNS、代理、权限策略。206 Nginx同窗identity/auth/ws-ticket存在200及embed-ws101；WebSocket访问日志时间是连接结束时间，不能误当建连开始，也不能用不同调用者的200证明当前frame就绪。
- 07:12真实自然问答要求按当前范围调用geo_workspace_summary，不切快照、不生成语音。调用call_00_pAEUGPJX2UV1J8vf3yWF7557、会话s-677fa5bc1f0d，审计03:12:43.879→03:12:46.509（浏览器本地时间），2.63秒；新read_at=2026-10-20T07:12:46.412957+00:00，audit geo-audit-670f3fd1d28744d985be32e0e6dc369d。参数a1a2233be7bb14fec9cf0396/usiran、dimension/start/end为空、compact=true；范围3980、升级250/持续65/缓和122/待研判3543与同屏看板一致，整轮约8秒。审计源complete/succeeded、13199字符/17173字节，但详情展示是首尾8000字符机械预览，未宣称逐字核对全部正文。
- 答复仍有独立语义问题：将3980后的“补充报告22条另行统计、不并入”描述含混；实际3980含22条supplemental，而coverage美伊3958排除22条。已补PiChat移交，不把数值匹配等同全部分析措辞通过。此问题不属于聊天高度或身份故障，未为其盲改MCP口径。
- 用户再次报告就绪超时提示及底部输入框消失，要求自行截图。分别检查历史对话页和欢迎页，未仅凭已连接路径得出结论。真实就绪页1920×1200下iframe y227/h960、输入框绝对y1032/h32；1920×1080、1536×824、1280×720、900×650均可见。生产“放大→还原”草稿保留、身份请求计数1→1，结束已清除未发送草稿；放大时iframe h1133，没有重载frame。
- 欢迎页负向测试仅在独立测试标签暂缓/embed-ws连接，不改服务端：重现相同“身份已签发，但会话尚未就绪”提示，1920×1200输入区仍完整；1920×1080/1536×824/1280×720/900×650的composer高度161/151/151/151px，均位于iframe底部内。低高度正文独立滚动，输入框未被裁切。本测试是故障注入，不冒称复现客户原始网络故障。期间另遇一次自然身份请求超时，正常点击宿主“重试连接”有效；不是force click。此前一次重试定位因提示自行消失未真正点击，不算恢复验收。
- 故障注入标签已关闭，另开无拦截页面复核：07:24:22.287身份200→07:24:29.219 chat-ready（约6.93秒），原两轮对话保留，alert=0，相关console error/warn=0；未重新发送业务问题。此后续成功只作恢复证据，不消除前述首次失败。1920×1200为受控CSS视口、visualViewport.scale=1，不冒充已验证客户实体机100%缩放或完整浏览器工具栏。
- 截图仅本机/tmp：geoagent-v21-clean-live-1920x1200-20261020.png（无拦截实际公网对话/完整输入区）、geoagent-v21-welcome-connection-pending-20261020.png及geoagent-v21-welcome-pending-720-20261020.png（明确标注故障注入欢迎页）、geoagent-v21-identity-timeout-20261020.png（保留真实身份超时）。不入库凭据、截图、临时文件或运行数据。
- 补充视觉边界：独立Chrome CDP模拟视口的一次放大/还原后，DOM与命中测试已恢复1492px宽，但截图仍残留跨域frame的放大绘制范围；1px视口高度变动后重新绘制恢复。不能只凭boundingBox正确宣称放大/还原视觉完全通过，尚未确定是Chrome/CDP合成采集问题还是客户同类绘制问题，未归因VPN。恢复后的正常整页截图为/tmp/geoagent-v21-public-normal-full-20261020.png。
- 本轮状态：MR/发布/MCP探针已通过，正常路径真实问答及普通布局限定验收通过；放大/还原重绘、间歇身份/WS慢连接未关闭，客户输入框消失尚未复现，不宣称再次修好了未定位的问题。08移交补充同frame时序、LNA/DNS证据及欢迎页/历史页分开验收要求。本轮后续未改生产配置或PiChat源码，未通过延长预算、删除提示、扩大权限伪造成功。

### 2026-10-20：参照 StockLink 连接修复，核查 VPN/代理路径（只读）

- 用户指定参照/Users/yujunjun/luozi/stocklink-product。只读检查其AGENTS.md、当前源码及提交，确认0147021f、da9c73c0、527a71f7、09284447均包含在当前检出00b81a97中；没有切分支、改其文件、启动/发布StockLink或读取应用密钥。结论基于当前源码，未把历史部署记录当作当前实例验收。
- StockLink的useEmbedStartup统一identity/waiting/ready/failed：身份请求连同body有截止时间；取消/owner/generation隔离过期结果；auth request/required合并、强制失效可替换在途请求；只接受当前frame/origin的chat-ready；健康frame续期与scope切换不重建。Dock收起也不卸载iframe。09284447额外以opt-in embed-startup/v1串联attemptId/frameId及identity/frame/auth/ready/visible事件，不输出凭据。
- GeoAgent v1.0.21已有身份连body截止、当前frame/origin校验、真实签发user、迟到load幂等和显式重试重建；不把sessions更新当ready。但切业务菜单仍会卸载iframe，重新进入会再次签发/建连；缺少StockLink同级的跨端启动关联轨迹，当前request(force)遇到pending仍返回原Promise。后者是生命周期差异，尚无本次现场证据证明它触发此次超时。常驻frame须保留本项目“显式重进时按快照捕获scope、面板内筛选只同步view”的会话契约，不能直接复制StockLink的scope实现。
- 本机网络只读证据：v2rayN/Xray/sing-box和wireguard-go同时运行。v2rayN的sing-box配置明确tun interface_name=utun81，Fake-IP范围198.18.0.0/15、fc00::/18。chat.lhotsetech.com解析198.18.5.156/fc00::3ed，两地址均路由utun81；chat.test的192.168.100.206路由utun4。系统HTTP代理设置为空不代表没经过TUN。
- DNS-over-HTTPS公开查询chat.lhotsetech.com返回A=139.224.61.228、AAAA为空；本机合成IPv6不是该域的公网AAAA。相同/chat无凭据探针：IPv4经Fake-IP返回HTTP200，TLS0.127秒/总0.186秒；IPv6连续两次在10.007秒、8.003秒超时且TLS尚未完成。指定真实公网IPv4的单请求也8.005秒超时，该IP路由仍为utun81，不能称其为“已断VPN直连测试”，也不能断言只排除Fake-DNS即可解决。
- 结论：VPN/代理路径确实参与，已确认本机合成IPv6路径不可用，与Chrome LNA拦截形成明确排查方向；尚未完成同页面关闭TUN前后A/B，未证明所有冷启动、身份超时及客户设备问题同因。未关闭VPN、改分流/DNS、开浏览器本地网络权限或关闭TLS。下一步若用户授权，短暂只停v2rayN TUN、保留公司WireGuard，做同版本/同账号/同scope的冷启动、重进与WS事件对照后恢复原状态；代码生命周期改进与网络验证分别验收。

### 2026-10-20：保留 TUN，宿主有限自动恢复与聊天布局修订（本地验收，未发布）

- 用户明确要求不暂停 v2rayN TUN；撤销上一条中的网络 A/B 下一步。本轮没有改代理、VPN、DNS、浏览器权限、TLS、PiChat 配置或 Key，只修改 GeoAgent 宿主。Browser 插件不可用，沿用已授权的 Chrome/CDP 进行本地真实嵌入验收。
- 身份 fetch+body 的单次总预算由 15 秒调为 30 秒；会话就绪由 10 秒调为 60 秒。第 10 秒显示中性的慢连接状态，不提前报失败；第 60 秒仍未收到可信的 chat-ready 才报超时，保留 iframe 和可操作的重试按钮，允许迟到的真实回执清除提示。后端上游单次 10 秒预算未修改，不将前端预算冒称上游预算。
- 网络/408/429/500/502/504 类失败最多自动重试两次，分别退避 2/5 秒；子面板 auth 轮询不能跳过退避或重启已耗尽预算。GeoAgent 接口直接返回的配置缺失 503、401/403，以及非法 JSON/JWT/签发用户不自动重试；后端仍将上游错误折叠为 502，因此这些 502 也最多重试两次，不宣称已区分所有上游拒绝原因。online/页面恢复可恢复已失败连接；健康连接不重置。显式 auth-required 可取消普通在途签发，重复失效合并到同一恢复请求，旧请求即便迟到也不能覆盖新身份。
- 同一快照切菜单/收起助手不再卸载 iframe，隐藏面板 inert 且不可聚焦；范围桥保留绑定，隐藏期间不推送新的看板范围，重新打开后同步最新范围。保持旧契约：面板已打开时修改筛选不导航，显式重进且工作快照变更才重建对应 scope；只有用户明确“重试连接”会重载当前 iframe。不为自动恢复清空草稿或创建新会话。
- 布局改为“稳定占位容器 + 标题自动行高 + minmax(0,1fr) 内容区”，iframe 绝对填满真实内容区，局部 layout/paint containment 防止放大画面残留。窄屏聊天模式取消 480px 固定最小高度，以 100dvh 和实际顶栏剩余空间为准；窄屏研判栏暂不占聊天高度。没有缩小 PiChat 字体、强行缩放 iframe 或变更跨域 PiChat DOM/CSS。
- 最终状态机前端 26/26 测试、tsc/Vite build、git diff --check 通过；完整后端 238 passed、1 skipped（62.91 秒）。覆盖有限退避/耗尽停止、失效取消在途请求、迟到结果拒绝、重复事件不延长就绪截止、慢连接提示、迟到 ready、卸载清理。未增加依赖。
- 本地 http://127.0.0.1:8010 → https://chat.test 真实会话：1920×1200、1920×1080、1536×824、1280×720、900×650、390×640 共六种 CSS 视口，输入框全部在视口内，页面无横/纵向外溢。其中 1920×1200 iframe y227/h960；1536×824 y193/h622；1280×720 y193/h518；390×640 y305.59/h323.41。输入框及底部操作栏截图逐项检查；这不是客户实体机或所有浏览器缩放比例的验证。
- 真实交互：“证据台账→智能问答”“量化指标→智能问答”“风险监测→智能问答”保留同一 iframe 文档和未发送草稿，身份请求 1→1；连续三次放大/还原后完整截图没有此前越界残影，未靠额外 1px 调整强制修复。验收后清空草稿。真实输入“本地恢复验收：请只回复‘本地连接正常’，不要调用工具，也不要生成语音。”，收到新答复“本地连接正常”，无宿主错误提示。此为连接验收，不冒称重新验收 MCP 业务分析或 TTS。
- 独立测试标签页注入一次身份 502：2.14 秒后自动重试真实接口 200，随后真实 PiChat 就绪，无需点击重试。另一个独立标签暂缓 iframe 文档约 25.5 秒：第 10 秒仅慢连接提示、无 error；释放后在身份 200 后约 28.02 秒收到真实 chat-ready，提示消失，只有一次身份签发。这些是明确的故障注入验收，不是本机 VPN 根因已被消除的证据。
- 截图仅存 /tmp，不进入 Git：geoagent-recovery-1920x1200.png、geoagent-recovery-1536x824.png、geoagent-recovery-1280x720.png、geoagent-recovery-390x640.png、geoagent-restored-2.png、geoagent-slow-recovered.png、geoagent-recovery-live-1920x1080.png。剩余边界：硬网络阻断无法靠宿主代码保证成功；PiChat 自身的 WebSocket 运行期恢复仍由 PiChat 负责；公网尚未发布本轮改动，客户机器需发布后再复验，不宣称公网/客户问题已关闭。
- 另外修复初始化窗口：工作快照未加载时，“智能问答”禁用并提示等待，防止先选中 chat 菜单、却因 scope 为空而无面板/输入框。独立标签暂缓 /api/status 时按钮 disabled=true、iframe=0，释放真实响应后自动 enabled=true。最终无拦截干净页面 URL/title 正确、非空、无框架覆盖层，身份 200、真实 chat-ready、console error/warn 与 pageerror 均空；完整截图 /tmp/geoagent-recovery-final-1920x1200.png。
- 用户随后要求“push mr 生效”：提交前再次执行前端 26/26、完整后端 238 passed/1 skipped（61.25 秒）、tsc/Vite build 和 diff 检查，均通过。仅提交上述源码/测试及两份账本文档；新增行的常见凭据模式扫描无命中，不包含环境文件、运行数据或截图。沿用 GitLab MR→main→受保护生产标签→人工部署及一次性 MCP 探针流程，实际交付结果另记。

### 2026-10-20：v1.0.22 已发布，慢连接恢复与聊天高度修复公网验收

- 修复提交 `7a9f302` 已 push，GitLab MR !13 的流水线 5694 通过后合并到 main `5b0519e`；main 流水线 5695 通过。生产标签 `geoagent-prod-v1.0.22` 的流水线 5696，验证作业 29366 及人工部署作业 29367 均成功。08:15:30 UTC rollout 完成，08:15:52 Job succeeded；没有把 main 流水线当部署证据，未直接改集群绕过发布流程。
- Runner 首轮双公网 `geoagent.lhotsetech.com` / `geoagentd40d.sealosgzg.site` 均匿名 401、initialize 200、initialized 202、tools/list 200（17 工具/19830 字节）、snapshot-call 200（2608 字节，2725/2632ms）。08:15:51 一次性 release-live Key 已撤销，原 PiChat Key 和签发用户未替换。公网实际资源 `index-AfAUuMDv.js`、`index-Bb9AqKcp.css` 与本轮构建一致。
- Browser 插件不可用，使用已授权的 Chrome/CDP 独立验收页；保留 v2rayN TUN、DNS、浏览器安全策略及 PiChat 配置。浏览器连接重新绑定后，站点认证仅通过内存中的 origin 限定凭据完成，不保存到源码或日志。公网 Sealos 页面→公网 `chat.lhotsetech.com/work-embed/geo-research-assistant`：08:16:54.340 身份 200，08:16:58.548 可信 chat-ready，间隔 4.208 秒；无宿主错误提示。
- 第 3 轮实际输入“v1.0.22 发布验收”问题，要求按当前看板调用 `geo_workspace_summary(compact=true)`、不换快照、不生成语音。调用 `call_00_jGQEnjYQwd4xMEHnKDo39327` 的技术详情确认参数 snapshot_id=`a1a2233be7bb14fec9cf0396`、topic=`usiran`、compact=true，源 complete/succeeded、耗时约 3.3 秒；新 read_at=`2026-10-20T08:17:25.645286+00:00`，audit=`geo-audit-c542bd89e6414aaea1134d3329e47c43`，不是复述历史回答。返回 3980、升级250/持续65/缓和122/待研判3543 与同屏看板一致。审计源13199字符/17173字节，UI为首尾8000字符预览；本次只核对目标字段，不宣称检查完整正文或重验全部业务用例。
- 新答复仍将22条补充报告表述为“另有……另行统计”，可能让人误读为在3980之外；这是已知 PiChat 措辞问题，08移交继续保留，不能把连接和数字匹配称为全部答复语义验收通过。本轮没有扩大范围修改 PiChat。
- 公网五种 CSS 视口（visualViewport.scale=1）：1920×1200、1920×1080、1536×824、1280×720、390×640 均无页面横/纵向外溢，输入框位于 iframe 内。1920×1200 iframe y227/h960、输入框 y1032/h32；1536×824 iframe y193/h622、输入框 y660/h32。已查看完整桌面、低高度及窄屏截图，底部输入和操作栏可见；不等同客户实体屏幕/浏览器工具栏/所有缩放比例验收。
- 公网“证据台账→智能问答”后 iframe `performance.timeOrigin` 不变，未发送草稿保留，身份请求1→1；隐藏期间 inert=true、visibility=hidden。连续三次“放大→还原”仍同一 iframe，放大 h1133、还原 h960，截图无此前越界残影，未用1px视口变化补救。测试草稿已清空。最终 URL/title 正确、非空、无框架错误覆盖层，alert=0、console error/warn=0、pageerror=0。
- 截图在本机 `/tmp/geoagent-v22-public-final-1920x1200.png`、`geoagent-v22-public-1536x824.png`、`geoagent-v22-public-390x640.png`、`geoagent-v22-restored-2.png`，不进入 Git。限定结论：本轮代码、MR、部署、双公网 MCP 和本机公网真实嵌入/布局验收已通过；本地此前的502退避/25秒延迟注入与本次正常公网路径分开记录。无法承诺硬网络阻断自动恢复，未证明客户设备或所有历史间歇超时同因。

### 2026-10-20：确认原型后落地右侧独立研究工作区（本地完成，未发布）

- 按用户确认的原型实施：左侧保留八个业务菜单，移除左侧“智能问答”；右侧从工作区顶部开始独立占满高度，默认选中“智能问答”，可切换“研判”。右栏默认宽度42%，分隔栏支持拖动36%—54%、方向键/Home/End和双击复位；“放大/还原”只覆盖全局标题以下的工作区。保留真实地图、真实业务数据和实际PiChat界面，不将原型示例数据或模拟回答写入产品。
- iframe在右侧常驻；左右菜单切换、主题/维度/日期变更和放大还原不改其src/key。会话scope在首次有效工作快照到达时捕获，右栏切换不再充当“关闭后重进”；之后范围通过现有下一轮view-context协议同步。显式“重试连接”仍保留原有重建能力。未修改PiChat内部字体、输入框、身份签发/权限、MCP配置、VPN、DNS或TLS；本地开发服务沿用现有进程。
- 布局实现采用独立CSS Grid、实际剩余高度和稳定内容容器。窄于760px时上下堆叠，工作区可滚动；窄屏“放大”可独占剩余高度。没有声称窄屏普通堆叠模式能将两个完整面板同时塞进首屏。来源设置、资产/证据详情分别约束在对应左/右栏内，不覆盖全局标题和右栏标签。
- live发现并修复研判旧子元素`visibility:visible`穿透隐藏父层、叠到聊天的问题：隐藏pane同时opacity=0、inert、禁用指针，但仍保持iframe尺寸和挂载。缩窄左栏时采集来源行四个子项落入三列，产物时间撑宽图标列并挤压来源名；已固定图标列、调整时间跨列，并在较窄容器内将批次/来源/详情纵向排列。不是缩小字号掩盖挤压。
- 独立回归审查发现证据请求晚返回会重新覆盖已切换的右栏：新增独立详情请求版本，关闭详情、切标签、切工作范围、选资产均使旧请求失效；过期成功/错误/翻译回调不能重开抽屉、清除新资产或弹旧错误。未复用问答selectionVersion。另修复“已有快照但未确认工作快照”一直显示加载的问题：明确提示并提供用户主动确认按钮，不自动更改工作快照契约。
- 前端测试47/47、TypeScript/Vite生产构建、`git diff --check`通过。新增宽度/键盘/边界、常驻iframe结构与scope测试，以及执行实际App函数/回调的延迟Promise与空态渲染测试。结构与模拟异步测试不冒充浏览器几何/完整生命周期证明；本轮没有后端代码变化，也未将历史后端测试计作本轮重新执行。
- 本地`http://127.0.0.1:8010/`→`https://chat.test`真实嵌入：左侧证据台账、量化指标、数据源、采集管线监控、研究简报、风险监测切换；右侧研判/智能问答切换；资产详情打开关闭；放大/还原；分隔栏实际拖到50%、键盘54%/36%及双击42%均完成。上述交互后未发送草稿保留，iframe仍单个且src不变。没有采集身份请求计数，故不声称本轮已证明“身份请求1→1”。
- 原生日期控件输入2026-09-28至2026-09-30后，真实PiChat范围条回显相同起止；清除恢复不限日期。维度美国民意、俄乌/美伊主题切换也回显到下一轮范围。结束恢复美伊、全部维度、空日期、42%普通布局，工作快照仍`0b18ddf4468552d911eba06e`。未触发采集、导入、简报生成或快照写入；未发送的验收草稿已清空。
- 新发第7轮“右侧工作区布局验收：请只回复‘右侧会话正常’，不要调用工具，也不要生成语音。”收到真实新答复“右侧会话正常”，界面约2秒完成。此项只证明嵌入连接可用，不冒称重新验收公网MCP业务取数或TTS；历史对话中的旧快照数字不作为本轮数据验收证据。
- CSS视口验收：1920×1200时iframe y169/h1018；1536×824时y156.5/h658.5；1280×720时y156.5/h554.5。三档主文档无横/纵向外溢，输入框和底部工具栏可见；1105×988亦检查来源/采集页与右侧聊天同时显示。390×640检查普通上下堆叠和放大模式，放大时iframe约386px高、输入框在frame内。尺寸属于本机浏览器受控CSS视口，不等同客户实体机、OS缩放或外置Chrome实机验收。
- 截图采证中，`fullPage:true`在模拟大视口曾出现跨域frame合成错绘，而DOM外框/内部视口一致；改用不改变尺寸的视口截图再次核对，完整对话与输入区正常，放大还原后截图也正常。保留这一采集边界，不仅凭几何断言关闭历史客户绘制问题。最终视口截图：`/tmp/geoagent-right-dock-1920x1200-20261020.jpg`、`/tmp/geoagent-right-dock-restored-20261020.jpg`、`/tmp/geoagent-right-dock-1280x720-20261020.jpg`，仅本机留证不入Git。当前宿主alert=0，所取console error/warn为空。
- 交付状态：本轮仅本地源码、测试与账本；未提交/push/MR、未合并或发布公网。线上仍需后续正式发布和客户窗口复验，不能把本地完成称为公网生效。
- 用户随后授权“push mr”：提交前重新fetch确认本地main与origin/main同为`10e3c90`，创建`codex/right-research-dock`，目标main。复跑前端47/47、TypeScript/Vite构建、完整后端238 passed/1 skipped（29.86秒）和diff检查均通过。仅提交本节账本、App/main、右栏布局组件/样式/纯函数及两份回归测试，共8个文件；不带密钥、运行数据、截图、临时证书或构建产物。MR和流水线结果以GitLab实际回执为准，本次授权不包含合并或生产发布。

### 2026-10-20：v1.0.23 已发布，右侧工作区公网正常路径验收

- 用户确认发布包含 !14 的 v1.0.23。已核对 main `8f86e6c0aadf3be397e6757af769a984cb0816e1` 包含 !14 的实现提交 `143f0e4431b41fc630383970cf53077e1550cc16`，创建并推送生产标签 `geoagent-prod-v1.0.23`，未移动旧生产标签。此前 !14 合并/main 验证通过并不等于生产发布；本节记录的是实际生产发布结果。
- 生产流水线 [5706](https://gitlab.lz.internal/yujunjun/GeoAgent/-/pipelines/5706) 已通过；[verify 29401](https://gitlab.lz.internal/yujunjun/GeoAgent/-/jobs/29401) 与人工执行的 [deploy 29402](https://gitlab.lz.internal/yujunjun/GeoAgent/-/jobs/29402) 均成功。部署日志确认 `geoagent-release-5706`、Deployment rollout 完成、站点 status HTTP 200 和 `Job succeeded`；rollback 29403 保持手动未执行。没有绕过既有 GitLab 生产标签/人工部署流程直接修改集群。
- Runner 双公网 `geoagent.lhotsetech.com/mcp`、`geoagentd40d.sealosgzg.site/mcp` 探针均通过：匿名 401/GEO_AUTH_REQUIRED，initialize 200、initialized 202、tools/list 200（17 工具、19830 字节）、snapshot-call 200（2608 字节，分别 2561/2177ms）。日志明确 `Release live key revoked`。沿用授权的一次性 release-live Key 流程，没有替换 PiChat 长期 Key、签发身份或扩大权限。
- 本次公网 live 使用外置原生 Chrome，站点登录采用用户授权的现有凭据，不保存到代码、账本或截图。IAB 首次受浏览器级站点认证限制未打开，不将该入口失败归为应用故障；随后外置 Chrome 正常登录、刷新实际公网域名后看到新版：左侧八个业务菜单，右侧独立“智能问答/研判”工作区，聊天从右栏顶部开始。真实嵌入地址仍为公网 `chat.lhotsetech.com/work-embed/geo-research-assistant`。
- 在公网嵌入面板真实发送第4轮：“v1.0.23 右侧工作区验收：请只回复‘右侧会话正常’，不要调用工具，也不要生成语音。”收到新答复“右侧会话正常”，界面显示约1秒完成、06:35（本机 America/New_York 时区）。不是只看历史消息判断连接正常；本短问答只验收连接，未重新执行 Agent→MCP 业务分析或 TTS。历史第3轮的业务数字和已知 PiChat 补充报告措辞问题不作为本轮新验收或已修复证据。
- 交互检查：切换左侧量化指标、右侧研判/智能问答，以及放大/还原后，未发送验收草稿仍保留、历史会话仍可见。1920×1200 受控 CSS 视口初轮截图与还原截图中输入框、发送区域和底部工具栏完整可见；这是外置 Chrome 的设备模拟视口，不冒充客户实体机或所有 OS/浏览器缩放比例。未采集 iframe 文档标识及身份请求计数，不声称本轮已证明 DOM 恒等或身份请求 1→1。
- 补测 1280×720 时，原生浏览器尺寸字段、键盘输入与截图出现不同步：AX 一度显示目标尺寸，画面仍停在 1920×1200，后续字段又出现数字追加。因此本次低高度公网补验不计通过，也不能据此归因产品布局失败。相关 `/tmp/geoagent-v123-public-1280x720.png` 仅保留为不可信尺寸控制的诊断记录，不作为该尺寸通过截图。临时设备模拟和 DevTools 已关闭，重新加载正常窗口后，右栏恢复默认42%，新第4轮回答、完整输入区和底部工具栏正常。
- 验收草稿已清空；结束保持美伊、全部维度、空日期、原工作快照 `a1a2233be7bb14fec9cf0396`。未启动采集、导入快照、生成简报，未改 PiChat 配置、VPN/TUN、DNS、TLS 或浏览器安全权限。未在本轮重新验收日期/主题范围传递、长期闲置恢复或全部业务问题。
- 可信截图仅存本机，不进入 Git：`/tmp/geoagent-v123-public-1920x1200-restored.png`（受控视口还原后）、`/tmp/geoagent-v123-public-default-final.png`（关闭模拟并刷新后的常规窗口，含新答复和完整输入区）。本轮结论：包含 !14 的 v1.0.23 正式发布、双公网 MCP 探针、外置 Chrome 公网新问答和右侧工作区正常路径已通过；低高度公网独立补验与客户实体机分辨率/缩放复验仍保留，不将恢复正常画面代替所有历史显示问题已关闭。

### 2026-10-20：GitHub 新采集脚本同步及本地隔离验证（未推送、未发布）

- 用户要求从 GitHub 更新本地并测试。合并前本地 `main` 为 `7f7954f`，工作区干净；获取 GitHub `upstream/main` 至 `76ac2d9680cdf7e2fdc3fe6d4276953d70d7348e`，合入上游独有的7个提交。采用保留双方历史的本地 merge，不重置 GitLab 已有实现，不 force push。该上游版本不只有采集脚本，还包含翻译、页面总结、简报模板、外部 MCP 配置及 MapLibre 依赖升级；这些变更一并纳入回归，但不冒称新功能均已真实模型验收。
- 新增6个采集器，注册表由19项扩至25项：俄罗斯金融压力、欧盟天然气库存、乌克兰粮食、乌克兰外部部门、欧乌贸易与援助、证券时报美伊专题。解决 `App.tsx` 合并冲突，保留 !14 右侧常驻工作区、详情请求失效防护、下一轮范围同步及已有 PiChat 身份恢复机制；接纳上游“未配置嵌入时使用本地问答”的分支，修正其布局为标题/滚动正文/底部输入三行。已配置嵌入不因切到研判而卸载或停止身份生命周期。
- 本地发现并修复：采集/排除请求仍限制19项，改为有界100项并保留注册表、禁用项校验；固定来源脚本兼容文档中的 `--self-test` 与原 `--selftest`；HTTP 成功但没有有效观测不再报采集成功，分别返回 `ok/partial/failed/configuration_required`，无有效数据非零退出、部分有效结果仍允许后续门禁判断；证券时报输出遵守 `COLLECTOR_OUTPUT_ROOT`，只读取所选输出目录的旧归档，不混入默认运行目录。
- 固定来源 curl/urllib 后备请求均使用同一来源专用 CA 策略；仅保留上游随代码提供的俄罗斯中间证书，不安装到系统、不关闭主机名或证书链验证。修复一个测试隔离问题：重试失败采集器测试使用默认配置夹具，不再因本机真实 TACO 排除项而失败。没有修改用户现有排除配置。
- 新来源 live 产物全部写入 `/tmp/geoagent-github-validation-709UWM/live/`；固定来源每个脚本执行一轮，沿用脚本本身的 curl→urllib 后备，不以多轮重跑覆盖首次结果。结果如下：

  | 采集器 | 本轮实际结果 | 有效指标及边界 |
  | --- | --- | --- |
  | 俄罗斯金融压力 | `ok`，约14.17秒 | 4项：汇率、关键利率、Brent及卢布油价代理；最新日期分别至10月20日/20日/19日/16日。预算仅归档入口并发现8个压缩包，未提取预算值。 |
  | 欧乌贸易与援助 | `ok`，约15.44秒 | 5项：4项贸易月序列至2026年8月，实际援助付款日期至10月2日。不是当日实时贸易量。 |
  | 乌克兰外部部门 | `ok`，约15.61秒 | 4项国际收支指标至2026年9月；原始响应约13MB、43567行，仍有采集体积成本。 |
  | 欧盟天然气库存 | `configuration_required`，退出码2 | 本次隔离环境未提供 `GIE_API_KEY`，0项观测；只验证了缺配置的明确失败，不记真实取数通过。 |
  | 乌克兰粮食 | `configuration_required`，退出码2 | 本次隔离环境未提供 `USDA_API_KEY`，0项观测；未用零值、旧数据代替，不记真实取数通过。 |
  | 证券时报美伊专题 | 有界列表检查完成，目标主题0命中 | 首轮限制3个频道各1页、最多3个正文候选，实际无正文请求；补查每频道各1次，解析10/10/30条列表项，共50条、相关候选0。不是解析器失效的证据，也不等于30天全量覆盖或正文抓取验收。 |

- 对上述固定来源产物真实调用 `backend.adapters.load_bundle`，得到13项有效指标、适配 `errors=[]`；核对序列日期有序且唯一、数值有限、来源 ID 存在。来源说明记录不等于新闻正文证据，缺 Key 的来源状态不算有效观测。仅做隔离适配，没有写入 SQLite、生成业务快照、切换工作快照或补齐历史快照。
- 自动测试：完整 Python 回归在独立 `GEO_DATA_DIR/COLLECTOR_DATA_ROOT` 中先导入 demo 夹具，最终 **317 passed、2 skipped**（26.35秒）；未导入夹具的初轮官方 MCP 集成测试失败不当作产品问题。新增容量和采集契约测试覆盖配置缺失、空响应、部分结果、输出隔离与 TLS 后备。依赖通过 `npm ci` 按更新后的 lockfile 安装，TypeScript/Vite 构建通过；独立简报展示测试2项通过。源码审计 `scripts/audit_repository.py` 返回 `ok=true`、无发现；最终前端回归结果见本节补记。
- 提交前独立复核还发现新 `/map-records` 请求缺少失效防护：切范围后旧请求晚返回可能使地图与当前看板/Agent 范围不一致。已增加 effect cleanup 失效标记，旧成功、错误、finally 均不可写回；离开地图、清空快照、命中缓存正确结束 loading。新增7项执行真实 effect 的延迟 Promise 回归，修复前5失败/2通过、修复后全部通过。最终前端 **56项通过**、独立简报2项通过、TypeScript/Vite 构建通过；这些异步回归不是实际网络乱序的浏览器故障注入。
- 重启的仅是本地开发服务，沿用已有后端配置及来源专用 CA，`8010` 前端、`8011` API/MCP 已加载合并代码。本地公共配置显示 `chat.test`、`embed_configured=true`；匿名 `/mcp` 仍返回401。未创建、撤销或替换任何 Key，未改 VPN/TUN、DNS、系统 TLS、生产服务或生产调度。
- 本地浏览器 live：采集管线监控的定时配置可见6个新增采集器，原 TACO 排除项保持；未点击应用配置。右侧智能问答/研判切换后原会话和底部输入区可见，当前原生视口为1087×988，日志中所取 error/warn 为空。未发送新问题，不能把历史回答当作本轮 Agent→MCP 新问答或语音验收，也未宣称本轮重新通过1920×1200。
- 重启前后仍为10个业务快照，工作快照 `0b18ddf4468552d911eba06e` 未变。本轮没有启动 `crawler_manager` 守护/自动导入或保存调度配置；上游包含采集完成后自动导入、推进工作快照的新行为，正式启用前仍需配置缺失来源凭据并单独验收调度/导入门禁，不能把手动脚本成功视为定时任务已通过。
- 临时证据均不入 Git：该 `/tmp` 目录下 `pytest.xml`、`fixed-live-validation.json`、`stcn-live-validation.json`、`live/stcn-usiran-monitor/list-diagnostic/diagnostic.json`、`source-audit.json`、`local-workbench.jpg`。仅将代码、回归测试和本账本完成本地合并；未 push GitHub/GitLab、未建立 MR、未更新生产版本。

### 2026-10-20：统一配置入口、纠正翻译状态并恢复民调展示（本地生效，未提交/发布）

- 用户确认按前述诊断修复。本轮基于本地合并提交 `2707406`，修改 GeoAgent 本项目；未修改 PiChat、生产配置、凭据、代理/VPN 或系统 TLS。使用 React 审查和前端验收流程，除自动回归外逐项操作本地页面；没有把源码、截图或历史会话当作新的 Agent 问答验证。
- **外部 MCP 单一配置源**：数据源中的旧“服务地址/Authorization/保存配置”表单移除，改为从 MCP 工作台的外部服务注册表选择新闻来源，统一入口为“管理外部 MCP 连接”。后端旧 `/api/mcp/config` 保留兼容门面，但读写同一注册表；有效旧连接一次性迁移，保留原 TLS 策略，空注册表也持久化，防止删除后被旧文件复活。多连接必须明确 `server_id`，不静默切到另一服务；新闻导入实际校验 `service_status`、`knowledge_search` 工具合同，区分“连接成功”和“支持新闻导入”。公开响应不返回 Authorization，错误继续脱敏。未创建真实服务连接、签发 Key 或执行新闻导入。
- **嵌入与后台模型分离**：“连接配置”拆分为 PiChat 嵌入连接、后台模型（翻译/摘要）、GeoAgent 对外 MCP 三页；数据源“大模型连接”改为直达“后台模型状态”。后台模型页读取真实公开状态并提供后端配置说明，不复用嵌入 App Key，不新增凭据保存接口；实际模型测试须显式点击，未配置时禁用。本机后台模型仍未配置，不能声称中文翻译或模型分析已恢复。
- **翻译状态如实展示**：缺译标题/摘要直接显示原文，标注“原文 · 翻译未配置”，不再用“中文译文准备中”掩盖内容。新增标题、正文及整体翻译状态；详情在完全未翻译时不再误报“译文含较长外文片段”或已有机器译文，只对实际译文做质量检查，保留术语纠错和数字差异提示。状态默认绑定工作快照，显式快照只反映该快照的进度；未配置模型时不启动空翻译任务。地图标题翻译增加完整范围的过期结果防护，不能把旧快照回执写到当前页面或另一快照缓存；移除已离开范围的旧聚合点记录。
- **特朗普支持率恢复**：数据并未丢失，是共享金融指标中的 `taco_trump_approval` / `RCPPTAPP_approve` 被美伊主题白名单排除。补齐别名准入并保持去重、主题来源优先以及日期/维度过滤，不把新闻报道的百分比拼成支持率序列。对旧导入的通用行情说明仅在已知支持率别名且匹配原默认文案时，投影为“共享民意背景”“日频更新不等于每天开展独立调查”；原快照和自定义来源方法不改写。
- **验收发现的同口径回归一并修复**：GitHub 的 workspace 轻量返回 `records=[]`，右侧仍用数组长度及过滤计算，导致范围记录和态势数量错误为0。REST workspace 与 MCP 现在共用范围聚合逻辑，返回 `records_count`、`signal_counts`、`event_category_counts`，右侧只读这些字段；仍保留轻量响应。统计覆盖所选主题/维度/日期内全部记录，应用该快照最新人工复核，不借用只含地图点位的1831条或受搜索影响的证据分页。
- **自动验证**：最终完整 Python 回归在独立临时数据目录中导入 demo 夹具，**375 passed、2 skipped**（27.69秒）；前端 **69项通过**，独立简报展示 **2项通过**；TypeScript/Vite 构建、`git diff --check`、仓库审计通过（`ok=true`、无发现）。覆盖配置迁移/多连接选择/能力门禁、缺配置与部分翻译、异步范围隔离、支持率别名/过滤/方法保护、REST full/light/MCP 统计一致、人工复核/无坐标/补充记录以及实际 JSX 数字渲染。模拟服务和异步夹具不冒充真实外部新闻导入或付费模型验收。
- **本地 live**：重启本地 `8010/8011` 加载最终代码，沿用既有 `chat.test` 嵌入及来源专用 CA。刷新后检查数据源→统一外部 MCP 页、后台模型状态页、地图聚合点原始标题、证据详情的原文及禁用翻译按钮、量化指标→民调。当前工作快照仍为 `0b18ddf4468552d911eba06e`（9月30日冻结快照），总快照仍10个；民调为1项，特朗普支持率为该快照9月29日观测 **37.43%**（原值37.43222），显示近60/617期，不借用新抓取数据。右侧研判恢复 **2301条、升级92/持续54/缓和135/待研判2020**；地图1831条为有点位投影，不能混用。翻译状态为该快照总4384条、ready163/pending4221、available=false/running=false。
- 浏览器当前原生视口1087×988，右侧历史会话与底部输入区可见，未发送新问题、未验收新语音或 Agent→公网 MCP 问答。日期范围覆盖自动测试；本轮浏览器日期填入未可靠触发控件提交，不计该项 live 通过。开发热更新期间出现过 effect 依赖长度变更提示，最终通过完整刷新验收，不据此宣称客户所有分辨率或生产链路通过。
- 本地证据仅存 `/tmp/geoagent-source-fix-TyqCQJ/`：`news-import.jpg`、`mcp-registry.jpg`、`model-status.jpg`、`map-original.jpg`、`poll-and-counts-final.jpg`、测试/构建日志等，不进入 Git。未启动采集、生成或切换业务快照、配置模型/Key、push、创建 MR 或发布公网；真实翻译仍待配置可用后台模型后单独验收。

### 2026-10-20：后台模型页面配置、DeepSeek 接入及底层翻译同源验收（本地，未提交/发布）

- 用户指出上一版“后台模型”只有状态、不能填写配置，要求修复并验证 DeepSeek 与底层翻译实际使用的模型。本轮新增提供方、API 地址、模型、超时及密码框，可分别测试草稿和保存。提供方显式选择 `disabled / deepseek / openai-compatible / pichat`；PiChat 后台由应用权限路由 Agent，不显示无效模型选择器，不改右侧嵌入 Key、对外 MCP Key 或外部新闻源配置。
- **正确配置**：页面“连接配置 → 后台模型（翻译/摘要）”，DeepSeek 官方 API 预设为 `https://api.deepseek.com`、`deepseek-flash`、90秒（可设5–300秒）、用户自己的 DeepSeek API Key。后端请求 `/chat/completions`，`stream:false`、`thinking:{"type":"disabled"}`。依据本日核对的 [DeepSeek 官方接入说明](https://api-docs.deepseek.com/) 与 [Chat Completions 接口](https://api-docs.deepseek.com/api/create-chat-completion/)；不把 PiChat 嵌入 App Key 或 GeoAgent MCP Key 用作模型凭据。`.env.example` 已补页面配置优先级和费用提醒。
- **持久化与边界**：新增 `/api/integrations/model` GET/PUT 和 `/test` POST。配置热读 `GEO_DATA_DIR/model-settings.json`，0600 原子替换并同步目录；页面保存的提供方（含禁用）优先，只有无保存文件才兼容旧环境变量。空 Key 仅在同提供方、规范化后的同一调用端点保留；换地址/提供方不继承旧密钥，不自动跟随重定向，远程新配置要求 HTTPS。公开配置不返回密钥，参数验证/上游错误不回显输入，响应 `no-store`；继续沿用项目既有站点保护和同源限制。损坏配置不回退到旧凭据，可用完整新配置恢复。临时假 Key 只在隔离目录，不进实际配置或 Git。
- **翻译代码统一**：页面小样本、证据标题/正文、地图按需标题和 `scripts/translate_snapshot_llm.py::translate_batch` 共用 `translation_service.translate_items → llm.complete`。统一 JSON 合同 `items/id/text`，检查遗漏/重复 ID、非中文、数字变更与术语；模型响应截断、空白、结构错误不得报成功。证据详情需标题/正文整体通过后才更新已有译文。草稿只使用本次配置，不写配置/缓存或启动任务；后台脚本每次调用读取同一生效配置。模型/提供方/端点变更隔离摘要缓存；已有按原文 digest 保存的中文译文不会因换模型被自动清空重译。
- **自动验证终版**：独立临时数据目录导入 demo 快照，完整后端 **468 passed、2 skipped**（27.24秒）；跳过的是 Windows 批处理及需要多历史快照的 scope 用例。前端 **81/81**、独立简报展示 **2/2**、TypeScript/Vite 构建和仓库源码审计均通过。新增测试覆盖保存/重载/热读/显式禁用、凭据隔离、写入失败恢复、脱敏、5–300秒边界、取消旧测试、DeepSeek 请求合同，以及真实脚本和证据 API 走同配置。模拟网络测试不是外网供应商验收。
- **隔离浏览器 live**：使用临时 `18031`、独立数据目录及完全模拟的 HTTP transport，实际点击选择 DeepSeek→填写测试凭据→试译→保存→重载留空 Key 再试译；验证编辑立即废弃旧结果、401 明确失败、换地址清 Key 并禁止提交。首次请求被既有 Origin 白名单拒绝，修正的仅是临时验收进程允许来源，不放宽真实项目安全配置。隔离页面和进程结束后已关闭。
- **真实 DeepSeek live**：本地页面随后已有保存的真实 DeepSeek 配置（不读取或回显 Key）；实际在 `8010` 点击小样本测试，返回 `deepseek-flash` 和“报告列出了10月20日的12艘船。”，原文中的12、20和10月保持一致。另从当前工作快照取公开标题 `Iran’s naval power protecting security of shipping lines`，直接调用真实后台脚本 `translate_batch`，返回“伊朗海军力量保护航运线路安全”，约 **2.42秒**；该次脚本试译不写缓存、不改快照。两次有界调用证明保存的模型凭据和实际底层翻译路径可用，不将小样本基础校验等同于所有语言/长文的语义验收。
- **如实记录启动副作用**：第二次本地重载前已有真实模型配置保存，既有 `lifespan → start_translation` 自动处理最新快照，日志至36批，累计接受 **93个文本项**、拒绝15项（不是93篇报道）。发现后停止该 pipeline/worker，保留已通过校验的译文，不删除数据；当前 `worker_running=false`。页面已明确提示“保存/测试不启动批量，但后续重启或导入可能启动最新快照翻译”。本轮没有实现新的自动翻译开关，也不能声称从未发生批量调用；完整批次并未跑完。当前选定历史快照与最新快照范围不同，不能据此宣称历史缺译已补齐。
- 本地 `8010/8011` 已加载最终代码，PiChat 嵌入原配置保持；工作快照仍 `0b18ddf4468552d911eba06e`，总快照仍10个，未采集或导入新快照。实际模型配置文件已核对为0600且被 Git 忽略。证据 `/tmp/geoagent-model-config-svelxR/deepseek-real-translation.jpg`、隔离验收截图/日志、构建日志仅本机留存；当前真实配置页保留小样本通过结果。未 push、建 MR 或发布公网。

### 2026-10-20：本轮修复生产发布门禁（发布进行中）

- 用户授权修复后发布生产。本轮将本地已合并的 GitHub 采集器更新，以及上述配置统一、缺译状态、民调/统计修复、后台模型页面配置一并纳入 GitLab MR；沿用受保护版本标签、同一候选镜像制品及手动生产部署流程，不将 main 流水线通过等同于上线。
- 发布前独立复核补齐可选模型配置故障隔离：损坏 JSON、符号链接及不可读配置返回安全的未配置状态和 `config_error`，不回显文件内容；模型配置页可用完整新配置或禁用恢复。正常公开合同仍为8字段。Web 启动、健康状态和非执行缓存身份不受该可选配置故障拖累；真实模型调用仍拒绝执行，不回退环境凭据或继承损坏文件内的 Key。
- 最终隔离回归：后端 **473 passed、2 skipped**（22.77秒），前端 **82/82**，独立简报 **2/2**，TypeScript/Vite 构建、仓库审计（`ok=true`、无发现）、diff 检查均通过。测试禁用真实供应商网络和翻译子进程，未重启本地服务。临时证据 `/tmp/geoagent-release-final-uhpPjP/` 不入 Git。
- 本机 `.env`、运行数据、`model-settings.json`、密钥和临时截图不提交或打包；不自动把本地 DeepSeek Key 迁移到生产。生产模型是否配置、实际调用与 Agent 嵌入验收需单独记录，不能用本地小样本成功代替线上验证。
- **当前交付阻断**：代码提交 `d3cae03`，分支 `codex/backend-model-and-source-config`；执行创建 MR 的 SSH push 时，`192.168.100.208:22` 建连超时。随后有界复核 SSH `ls-remote` 及 GitLab HTTPS 的 IPv4/IPv6 均超时，已登录 GitLab 浏览器刷新也未完成。该证据只能确认当前到 GitLab 的网络不可达，不能断言是服务故障或 VPN 根因；未修改 VPN/TUN、路由、DNS 或认证。未成功 push、未创建 MR/版本标签、未触发部署，生产尚未接收本轮修复；待 GitLab 可达后继续既定门禁。

### 2026-10-20：v1.0.24 生产发布、DeepSeek 配置与试译路径补验

- 网络恢复后已 push 并建立 [MR !15](https://gitlab.lz.internal/yujunjun/GeoAgent/-/merge_requests/15)，MR 流水线5713通过；用户在 GitLab 合并为 `8658cc0599c1f74ad50bbb5edec40184ac94d7ab`。main流水线5714通过，保护标签 `geoagent-prod-v1.0.24` 的[流水线5715](https://gitlab.lz.internal/yujunjun/GeoAgent/-/pipelines/5715)验证29412、生产部署29413均通过。部署完成于 `2026-10-20T13:47:27Z`，镜像摘要 `sha256:383628c3757eaf9ecd3d511c3a15b205b5c5768cdb32bb98781ea6d07fc8430c`。未触发回滚。
- 双公网域名的发布探针均通过：匿名401、initialize200、initialized202、tools/list200（17工具）、真实快照tools/call200。一次性 release-live Key 已撤销，PiChat 原应用 Key、公开地址及绑定身份保持；这不替代浏览器内真实问答验收。
- 用户单独授权“同步本地 DeepSeek 配置并试译”后，通过生产后台 API 保存已验证的本地 DeepSeek 配置：`https://api.deepseek.com`、`deepseek-flash`、90秒。密钥只从已忽略的本地0600文件在内存中传至授权生产配置接口，不回显、不加入 Git。生产重读公开配置为 `source=saved / configured=true / api_key_configured=true`。
- **保存配置试译通过**：生产 `POST /api/model/check` 经共享 `translation_service → llm.complete` 实际返回“报告列出了10月20日的12艘船只。”，原文为“The report lists 12 ships on 20 October.”，基础校验无问题。前后工作快照均 `a1a2233be7bb14fec9cf0396`、快照数5、批量翻译running=false；没有因本次保存/试译主动启动批量或重启。页面按需标题翻译属于另一条调用路径，不把全站模型调用数描述为只有一次。
- **UI 504 单独保留**：随后外置 Chrome 的“测试草稿”页面出现“后台模型服务返回非 JSON 响应（HTTP 504）”。不能以 `/api/model/check` 的成功关闭该问题：UI 调用 `/api/integrations/model/test`，生产网关该 location 未设置读超时，落入 Nginx 默认60秒；已保存配置试译走另一个显式300秒 location。DeepSeek默认允许90秒，两个路径存在确定的不一致。未取得当次生产gateway错误日志，因此不将此推断写成该次日志已证实的根因。
- **最小修复及可重复证据**：仅为 `/api/integrations` 配齐 `proxy_read_timeout 300s`，不改认证、凭据、TLS或供应商超时。生产同版 Nginx 1.28.0 对同一65秒本地模拟上游，旧配置 **60.052秒 → 504 text/html**（日志 `upstream timed out ... while reading response header`），新配置 **65.033秒 → 200 application/json**。两组匿名401、可信身份头覆盖伪造输入、MCP清空身份头均通过；26项局部回归通过。CI仅挂载gateway单个公开配置文件供回归测试，不将运行数据/密钥或整个仓库带入镜像。模拟容器及虚构凭据已清理，不产生供应商费用。此补丁待后续MR/发布与真实UI路径验收，不将本地A/B当作生产已修复。
- 外置 Chrome 接管后观察到嵌入从“正在获取身份”自动恢复为ready、范围r2已接收、底部输入区可用，历史4轮保留；已发送第5轮 `v1.0.24 acceptance` 只读取数问题，最终工具回执与回答仍待核对。原生浏览器控制曾被人工操作/窗口不可用打断，未将仅看到历史回答当作本轮通过。证据和配置试译公开回执位于本机 `/tmp/geoagent-v124-live-awacyL/`，不进入Git。

### 2026-10-20：公网第5轮取数核对通过；v1.0.25 被采集锁阻断，尚未上线

- **补齐真实新问答证据**：上述第5轮随后返回“v1.0.24 验收：通过”，展示实际 `mcp__geoagent__geo_workspace_summary` 调用，`compact=true`、美伊主题、当前快照，调用 ID `call_00_Ihu3WyrI29NZSagC4o2j0583`；工具界面约6.7秒、整轮约12秒。回答使用本次 `read_at=2026-10-20T14:08:23Z`，快照 `a1a2233be7bb14fec9cf0396`、范围记录3980、升级250/持续65/缓和122/待研判3543，区分报道线索数量与情景概率，没有宣称变化归因成立；未生成语音、未切换快照。这是新发送问题后的新答复，不是历史问答复用。
- **独立对账**：只读生产 `/api/integrations/mcp-workbench` 记录同一时间的 `geo_workspace_summary` 为 `ok`、5969ms，审计 ID `geo-audit-87f4520808254d8bb625098c4057bbb8`；`/api/workspace?topic=usiran&include_records=false` 的记录数和四类数量与回答一致。该对账证明正常取数链路，不将 PiChat“完整详情”弹层一直加载描述为已成功读到全部原始回包。补充22条的表述仍需遵守既有08移交口径，不据数字一致关闭所有措辞问题。
- **保留验收边界**：当时右侧新答复和底部输入区可见，但未在本轮重新设置1920×1200，不冒称客户分辨率已复验。量化页的模型总结另出现“引用材料中不存在的数值”校验提示，程序分析仍可用；这是独立的模型输出质量问题，不因连接或小样本试译通过而算已修复。生产只读状态仍为5个快照、原工作快照、后台翻译 `running=false / worker_running=false`，保存的 DeepSeek 配置正确，不代表历史快照6807条缺译已补齐。
- **超时补丁交付**：[MR !16](https://gitlab.lz.internal/yujunjun/GeoAgent/-/merge_requests/16) 的提交 `a624563eac3a56c5c457448bc64237671be80824` 经独立审查、38项局部测试及MR流水线5717通过，合并为 `5a9e0cff388f8d0443e9e088a8af09b3f8355b28`；main流水线5718通过。创建并推送 `geoagent-prod-v1.0.25`，生产[流水线5719](https://gitlab.lz.internal/yujunjun/GeoAgent/-/pipelines/5719)的验证29418通过，随后按授权执行部署29419。
- **真实部署未成功**：[部署29419](https://gitlab.lz.internal/yujunjun/GeoAgent/-/jobs/29419) 在 `2026-10-20T14:31:47Z` 由 `backup.py` 拒绝：`Collector lock exists; finish/recover collection before deploying`。候选镜像已上传，摘要 `sha256:191fc57a132a0ba3136956e3955698a292249bfa9605f65804848d37042b3144`；但按脚本顺序，锁检查早于备份目录创建、release ConfigMap、gateway ConfigMap和Deployment更新，故本次没有把v1.0.25应用到生产。没有盲目重试、回滚、清锁、杀采集进程或绕过门禁；本次未改变生产部署，最近已确认成功部署的版本仍为v1.0.24。
- **锁的只读核查**：生产报告为 `manual-20261020T134858Z-bad24c4a`，13:48:58Z开始；发布前乌克兰援助14:31:40Z完成，发布失败后TACO14:32:20Z结束。14:37:34Z再次读取报告/事件，乌克兰外部部门采集仍标记running（14:21:00Z开始），全批次import pending；不能凭锁存在或报告状态推断陈旧，也不能凭共用的 `run_kind=scheduled-service` 判定由CronJob触发。尚未取得持有PVC容器的实际PID/Pod归属证据。已观察6个来源失败，包含缺GIE/USDA配置、来源超时及TACO缺curl等，未将这些扩大为本轮修改或重新采集。
- **当前剩余门禁**：继续只读观察现有运行记录，确认采集完成且锁释放后再重试同一标签的部署；若记录停滞或仍有锁，须先核对实际容器/PID归属，不能自动删除。v1.0.25未上线前，页面草稿试译504仍未关闭；之后还需真实点击该路径、检查模型配置持久化和发布后的嵌入。用户允许接管后，外置Chrome操作又因Mac锁屏中止，已请求用户解锁；没有改用其他手段绕过锁屏。已保存安全回执、采集报告/事件和部署截图到本机临时证据目录，不将密码、Key、原始运行数据或临时文件提交Git。

### 2026-10-20：模型综合判断数值误报修复及本地真实验证

- 用户标记本地量化指标页“模型总结包含引用材料中不存在的数值”。核对当前冻结快照 `0b18ddf4468552d911eba06e`：采购原始金额 `14765526933.65 USD` 对应页面 `147.66 亿美元`，程序涨幅 `6.8616783743%` 对应 `6.86%`。旧校验只认来源原文及拼接的当前/前值/变化额，遗漏 `pct`，也不识别金额换单位与四舍五入，故会把合法展示数字误判为无依据。但旧报错中的 `977.88 亿美元` 并不合理：军事人员原值 `9778765264.93 USD` 应为 `97.79 亿美元`；这类十倍换算错误仍需拦截。
- 页面总结现在向模型提供程序生成的 `metrics.display`：当前/前值及日期、变化额及单位、涨跌幅与方向；USD明确折算为亿美元，数量按有界的小数精度展示。校验只接受当前被引用指标的原值及这些明确展示值，不使用宽松数值误差范围，也不把未引用指标的数字加入允许集合。提示词要求直接引用展示值、保留单位/方向及来源ID，缺少前值或变化率时明确缺失；总结缓存版本已更新。量化、情景和资产页共用此修复，其他问答及简报校验未放宽。
- 同时使页面总结的新闻数字核对范围与实际发给模型的1200字符摘录一致，不再借用未发出的正文尾部来证明数字有依据。数值校验仍只是基础校验，不等同完整的单位、方向、因果或引用语义核验。
- 自动回归 **38 passed**（1.07秒），覆盖三个页面的合法换算/涨跌幅、精确两位与小数四位展示、错误小数、错误来源、十倍金额错误及摘录外数字拒绝，并重跑简报模板与民调范围测试。`git diff --check` 通过。没有前端源码变化；本轮未重复生产构建或声称已验收公网。
- 使用本地保存的真实 `deepseek-flash` 配置，对原工作快照生成一次新总结，返回 `status=ready / cached=false`，时间 `2026-10-20T14:50:49Z`。实际输出包括总支出679.23亿美元、减少181.26亿美元（-21.06%）、采购147.66亿美元（+6.86%）、军事人员97.79亿美元（-56.37%）、布伦特95.74美元/桶（-6.68%）、黄金涨幅0.81%；与当前指标及程序计算展示值核对。生成结果写入新版本缓存，未修改冻结快照内容。
- 本地8010/8011重载后，在现有内嵌浏览器实际进入量化指标，页面 `.page-model-summary` 为ready并显示刚生成的缓存内容；点击“引用1”打开当前快照的“国防部军事项目合计·月度净支出”来源，再关闭详情并恢复“民调1”筛选。页面标题/URL正确、非空且无框架错误覆盖；控制台保留修复前14:44的PiChat身份过期记录，不将其描述为本轮全链路无错误。右侧历史会话和底部输入区可见，本轮未发送新的PiChat问题或验证公网MCP。
- 重载期间用已有 `translation.lock` 的实际排他锁防止自动批量翻译；日志确认工作进程取得锁失败、两轮待译数均为32627，进程结束后已释放维护锁。首次重载漏继承本地PiChat专用CA导致临时502，随后恢复既有 `/tmp/geoagent-local-internal-ca.pem` 启动环境，身份范围r2重新接收、输入可用，TLS链及主机名验证保持。最终后台翻译 `running=false / worker_running=false`，工作快照不变，总快照仍10个。
- 临时证据 `/tmp/geoagent-summary-fix-gh6XY9/` 包含模型输入/输出、实际结果、前后截图及本地重载日志，不进Git，未读取或回显模型Key。Browser插件/skill未提供，本地页面通过已有CUA内嵌浏览器的Playwright/AX操作完成；视口为1015×988，本轮未重复客户分辨率测试。当前仅本地修复及账本更新，未提交、push或发布；上一节v1.0.25采集锁发布阻断仍为独立未完成事项。

### 2026-10-20：v1.0.25 解锁发布通过，同路径真实试译与身份接口补验

- 用户回复可接管后，15:14:27Z只读核对生产采集报告：原批次 `manual-20261020T134858Z-bad24c4a` 已于14:51:01Z结束，`run_status=partial`、7个来源失败、无运行中来源，`import_status=skipped_collection_status`、没有生成新快照。没有清锁或调用恢复操作；待运行结束后，仅重试原生产标签的部署，让已有备份门禁再次判断锁状态。
- 原GitLab标签停在网络错误页，自动读取被错误页协议策略拒绝；未读取该页或切换控制方式。新建正常HTTPS标签直接访问已授权GitLab作业后成功。原部署29419的重试生成[部署29422](https://gitlab.lz.internal/yujunjun/GeoAgent/-/jobs/29422)，备份锁检查通过，gateway ConfigMap更新、Deployment rollout及发布后健康检查完成。[流水线5719](https://gitlab.lz.internal/yujunjun/GeoAgent/-/pipelines/5719)最终**已通过**，测试报告478项，verify29418和deploy29422均通过；手动rollback29420未执行。失败29419仍保留，未用重试结果抹去原阻断。
- 实际发布仍为 `geoagent-prod-v1.0.25` / `5a9e0cff388f8d0443e9e088a8af09b3f8355b28`，镜像摘要 `sha256:191fc57a132a0ba3136956e3955698a292249bfa9605f65804848d37042b3144`。Runner日志确认两个公网域名匿名401、initialize/list/snapshot-call200、工具17个，以及 `Release live key revoked / Job succeeded`。这是已测试候选镜像的部署，不包含仍未提交的本地总结数值校验修复。
- 发布后重读生产配置，DeepSeek地址、`deepseek-flash`、90秒、`source=saved / configured=true / api_key_configured=true`仍正确。只读workspace仍为原快照 `a1a2233be7bb14fec9cf0396`、5个快照、范围记录3980、升级250/持续65/缓和122/待研判3543。PiChat现有Key和身份绑定未改。
- **此前出504的同一路径实测通过**：使用公开配置构造同端点草稿，Key留空由后端按同提供方/同地址保留，不读取生产Key；`POST /api/integrations/model/test` 于15:35:32Z返回HTTP200、`application/json`、4.252秒，`ok=true / source=draft / model=deepseek-flash`。译文“报告列出了10月20日的12艘船。”，基础问题列表为空。该请求走已发布的integration网关路径及共享底层翻译链路；与前述同版Nginx65秒A/B共同作为超时配置修复证据，不把4秒请求本身说成跨60秒测试。
- 发布后的 `/api/pichat/embed-identity` 于15:38:17Z返回HTTP200、约7.118秒、Agent正确且含token；token仅在请求进程内检查是否存在，不保存/打印，也未独立校验签名。此项证明身份签发接口正常返回，不替代浏览器ready握手或新一轮Agent→MCP问答。
- **自动翻译副作用实际发生**：生产重启按既有流程自动处理最新快照，发布后观测ready由245增至319、pending由6807降至6733，`running=true / worker_running=true`。用户此前已知晓并授权迁移配置后重启可能启动自动翻译及计费；本轮没有主动发起批量接口或停止生产任务。小样本回执“尚未启动批量翻译”只描述试译操作，不作为全站没有后台翻译的证据。
- 外置Chrome重新可读后，旧第5轮工具“完整详情”曾从加载中变为未截断成功回执（同一旧调用ID）；这是补齐旧轮证据，没有当成发布后新问答。原页面随后显示PiChat身份获取超时，配置页可见正确DeepSeek设置；页面试译点击后尚未取得可确认的结果，Mac再次自动锁屏，已请求用户解锁。故当前**流水线、部署、双公网MCP、实际草稿接口和身份接口通过；发布后的浏览器试译结果、ready状态、新问答及输入框验收仍待补齐**。未重新验证1920×1200，也未关闭所有历史身份间歇超时问题。
- 安全证据位于本机 `/tmp/geoagent-v124-live-awacyL/`：`v125-pipeline-passed.png`、`collector-progress-resumed.json`、`pre-v125-resumed.json`、`post-v125.json`、`v125-draft-preview.json`、`v125-identity-public.json`；不提交临时文件或任何凭据。当前账本记录更新与本地未提交的数值修复代码分别保留。

### 2026-10-20：v1.0.25 外置 Chrome 页面试译及新问答验收补齐

- 用户再次解锁后，外置Chrome的生产后台模型页已显示前次实际点击的“草稿小样本翻译通过”，模型 `deepseek-flash`、译文“报告列出了10月20日的12艘船。”；与前节同路径API实测一起核对，没有为截图重复调用模型。公开配置仍为DeepSeek官方地址、90秒、留空Key保留现有凭据，页面同时如实显示后台正在翻译当前快照。
- 返回公网嵌入后，范围r3已接收，底部输入可操作；实际粘贴并发送新第6轮 `v1.0.25 acceptance`，要求按当前看板范围调用 `geo_workspace_summary(compact=true)`、返回新读取时间和五项计数、不切快照不生成语音。收到新的“v1.0.25验收：通过”答复；工具界面约3.2秒、整轮约9秒。实际工具ID `mcp__geoagent__geo_workspace_summary`，调用 `call_00_ET_SALLxuCtF0M6OaVNdyBi6273`，参数明确 `compact=true / snapshot_id=a1a2233be7bb14fec9cf0396 / topic=usiran`；PiChat结果上下文标记complete/succeeded、13266字符/17268字节，不冒称本轮独立读取了全部展开正文。
- 新答复的 `read_at=2026-10-20T17:25:11Z`，快照仍 `a1a2233be7bb14fec9cf0396`、维度/起止为空；范围记录3980、升级250/持续65/缓和122/待研判3543。生产工作台随后新增同时间成功审计 `geo-audit-a88e00623a2946a3a7db07207a185337`、服务端2798ms，与PiChat3.2秒的端到端工具时间分别记录。独立workspace读取确认同组数量；没有拿旧第5轮当作发布后成功证据。
- 回答能区分线索数量与情景概率，说明单次读取无法确认变更归因、反证未评估不等于不存在。**仍未关闭的PiChat措辞问题**：它继续写“3980（另有supplemental补充报告22条，单独统计，不并入该口径）”。源码与既有合同中records_count含这22条，而coverage美伊3958排除这22条；此处不能记为全部业务语义验收通过。已将本次新调用回归证据补入08移交，未改PiChat源码、指令或权限。
- 左侧切回风险监测、收起范围详情后，同一新第6轮仍在，普通外置Chrome窗口中完整底部输入框、发送区域和工具栏可见。页面截图含地图3238个有点位投影，不能与全范围3980混用。当前未设置受控1920×1200，未重新做客户实体机/全部缩放验收，也未对身份冷启动与长期续期做完整故障注入；初次超时及锁屏中断历史继续保留。
- 验收前后工作快照未变，总快照仍5个。生产自动翻译继续运行，观察ready2567→2642、pending4485→4410；不是本轮问答启动了批量，也不表示旧快照自动补齐。最终草稿为空、未生成语音，凭据及VPN/TUN未变。
- **本轮发布闭环结论**：v1.0.25部署/流水线、双公网MCP、真实页面试译、发布后新Agent→MCP问答和普通窗口输入区验收已完成；已知补充报告措辞、历史间歇身份超时全场景、客户分辨率独立复验仍按各自边界保留。本地模型总结数值修复仍未提交及发布，不属于v1.0.25。截图 `v125-ui-draft-passed.png`、`v125-agent-new-turn.png` 与前后API对账文件仅存本机临时证据目录，不进入Git。
