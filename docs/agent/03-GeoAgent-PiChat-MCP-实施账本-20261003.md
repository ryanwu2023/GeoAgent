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
