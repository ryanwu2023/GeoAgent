# GeoAgent → PiChat Agent MCP 构建计划

版本：v0.6

日期：2026-10-03（北京时间）

状态：A3 本地 Gate 已通过；B0/B1 已完成本地接入与装配；B2 已完成嵌入面板业务链路 live 验证，剩余独立 API 入口与更完整指标覆盖验收

前置分析：[01-GeoAgent-PiChat-MCP-接入分析-20261003.md](./01-GeoAgent-PiChat-MCP-接入分析-20261003.md)

## 1. 构建目标与非目标

### 目标

让 PiChat Agent 直接通过 GeoAgent 自有 MCP Server，在独立认证、snapshot scope、审计和只读权限边界内读取 GeoAgent 的冻结快照和结构化业务分析：

- 快照版本与当前工作快照；
- 主题/业务维度覆盖；
- 证据检索与详情；
- 指标目录和受限时间序列；
- 五类资产关联和三类情景条件分析；
- 已生成简报的元数据和内容；
- 必要时读取采集运行状态，但不将运行态当作研究快照。

### 非目标

首阶段不做：

- Agent 直接访问 GeoAgent REST、SQLite 或文件系统；MCP Server 是唯一入口；
- Agent 直接访问 Plane；
- 依赖 LCF Gateway、LCF BFF 或 Plane Adapter；
- 采集执行、采集配置修改、快照生成、工作快照切换；
- 通过 Agent 自动写入研究员研判、简报或 Plane Work Item；
- 把最新快照自动设为工作快照；
- 用 GeoAgent 内嵌模型调用替代 PiChat Agent 推理。

## 2. 阶段 A：独立 GeoAgent MCP（前置阶段）

### A0：合同、权限和威胁模型冻结

目标：先冻结可验证边界，避免“REST 有返回”被误报成 MCP 已接入。

交付：

- `geo-mcp/v1` 工具目录和 JSON Schema；
- snapshot/workspace/topic/dimension 语义；
- GeoAgent principal、credential 与 allowed snapshot 映射；
- request_id、agent_run_id、audit_event_id、expected scope 字段；
- 错误码与限流/分页约束；
- secret、Plane Cookie/PAT、PiChat 应用密钥的禁止流向说明；LCF 只作为参考，不出现在运行时链路。

验收：合同评审通过；所有工具均标注只读/写入、数据来源和快照 lineage。

#### A0 开工前必须冻结的最小决策

以下决策不依赖 PiChat，可以在 GeoAgent 内部直接确定；冻结后即可开始 A1 编码：

1. **协议与进程**：采用官方 Python MCP SDK 的 Streamable HTTP；MCP 独立进程默认监听 `127.0.0.1:8401`，入口 `/mcp`，健康检查 `/healthz`，不复用现有 REST 路由。
2. **认证**：A 阶段先实现 Bearer credential 接口；本地只允许 loopback dev token，生产 token 采用短期/OIDC JWT 方案；token 只存 hash，支持过期和撤销。mTLS 暂不作为 A 阶段必选项。
3. **scope 数据模型**：GeoAgent 自己维护 `principal_id`、`agent_id`、`geo_workspace_id`、allowed snapshot/topic/dimension/tool 和 `read_only`；默认拒绝，不能从工具参数伪造身份。授权配置与研究快照数据分离。
4. **工具合同**：冻结每个 `geo_*` 工具的 JSON Schema、必填参数、`limit/max_bytes` 上限、统一成功/错误 envelope 和错误码；`geo_collector_status` 明确标注 `live`，不得混入冻结快照 provenance。
5. **审计**：统一记录 `request_id`、`principal_id`、`agent_id`、tool、snapshot、status、duration、`audit_event_id`；禁止记录 token、Cookie、PAT、原始 secret 和不必要的本地路径。
6. **验收数据集**：选定当前工作快照和一个历史/最新快照作为固定 fixture，准备允许、拒绝、空结果、超限和不可达五类协议测试；A2/A3 的 live 验证只依赖 GeoAgent MCP client，不依赖 PiChat。

若这六项未冻结，只能做目录、接口和测试骨架，不能宣称 A 阶段可以完成；它们冻结后，阶段 A 已足够进入实现。

### A1：GeoAgent 独立只读 MCP Server 骨架

目标：在 GeoAgent 中建立独立于 React UI 的 MCP 服务层，复用既有业务函数，不复制业务逻辑。

建议文件：

```text
/Users/yujunjun/opensource/GeoAgent/backend/geo_mcp/
  __init__.py
  server.py          # 独立 Streamable HTTP MCP 入口
  contracts.py       # 输入、输出、错误码、版本
  auth.py             # Bearer/mTLS principal 校验
  scope.py           # GeoAgent workspace / allowed snapshot 校验
  provenance.py      # snapshot/rule/source lineage
  audit.py           # request_id/audit_event_id，禁止记录 secret
```

实现要点：

- MCP endpoint 只绑定服务端网络接口；不暴露 SQLite 或原始目录；
- 默认拒绝没有合法 credential 和 scope 的远程调用；
- 本地开发可用仅限 `127.0.0.1` 的显式测试 token，不能作为生产认证；
- 生产使用短期 Bearer/OAuth token 或 mTLS，禁止把 token 放在 URL 或前端代码；
- 适配 `store.get_snapshot`、`snapshot_summaries`、`research.metrics`、`research.analysis`、证据查询函数；
- 所有结果带 `snapshot_id`、`created_at`、`rule_version`、`working/latest` 标志；
- 搜索和序列强制服务端上限，避免把 `/api/workspace` 全量 records 作为一次工具结果返回。

测试文件：

```text
/Users/yujunjun/opensource/GeoAgent/tests/test_geo_mcp_contract.py
/Users/yujunjun/opensource/GeoAgent/tests/test_geo_mcp_scope.py
/Users/yujunjun/opensource/GeoAgent/tests/test_geo_mcp_tools.py
```

### A2：实现 v1 只读工具

按以下顺序实现，先验证最小闭环再扩展：

1. `geo_snapshot_list`
2. `geo_snapshot_get`
3. `geo_workspace_summary`
4. `geo_evidence_search`
5. `geo_evidence_get`
6. `geo_metric_catalog`
7. `geo_metric_series`
8. `geo_analysis_view`
9. `geo_brief_list`
10. `geo_brief_get`
11. `geo_collector_status`

每个工具都要有：

- 合法输入测试；
- 不存在快照、错误维度、日期倒置、超限分页测试；
- scope 不允许时拒绝测试；
- 返回快照 lineage 和 provenance 测试；
- 空结果与服务错误区分测试。

`geo_workspace_summary` 只返回摘要、覆盖、指标、资产、情景和受限 records 计数；证据正文必须由 `geo_evidence_search/get` 按需读取。

阶段 A2 的完成边界必须明确：

- 代表 GeoAgent MCP Server 的工具合同、业务投影、认证/scope 校验和测试身份下的协议调用已经可用；
- 可以用本地 MCP client 或协议测试脚本进行 live 验证，确认返回的数据、`snapshot_id`、`rule_version`、`evidence_ids` 和分页/错误语义满足需求；
- 不代表“所有数据”已经开放。每次调用仍受 `principal_id`、`geo_workspace_id`、allowed snapshot/topic/dimension、allowed tools、`read_only` 和服务端 page/size 上限约束；默认拒绝未授权访问；
- 不代表任意 Agent 可以使用，也不代表 PiChat Runtime 已登记、绑定 credential、分配工具或实际加载工具；
- 不代表已经完成跨主机部署、TLS、生产 credential 或用户级权限验收。

因此，阶段 A2 的 live 验证是“GeoAgent MCP 数据合同和工具结果是否满足需求”的验证；PiChat Agent 的真实使用验证属于后置阶段 B。

### A3：独立 MCP 完成验收（前置 Gate）

这是 PiChat 接入前必须通过的独立阶段。它不依赖 PiChat，也不能用 PiChat 页面或普通 REST 请求替代。

MCP 阶段完成条件：

- 独立 MCP Server 可以启动，提供 `/mcp` 和健康检查接口；
- Streamable HTTP 协议握手、工具列表和工具调用通过；
- 所有 v1 `geo_*` 工具的合同、认证、scope、分页、provenance、审计和错误测试通过；
- 使用受控测试 principal 进行 live MCP 调用，结果满足业务需求；
- 提供可重复执行的 MCP Gate 脚本，至少覆盖 health、官方 Client 握手、11 个工具目录、working snapshot、业务分析维度、live collector、结构化错误和脱敏断言；
- 能证明未授权 principal、越权 snapshot、越权 topic/dimension 和未授权工具都会被拒绝；
- 输出不泄露 token、Cookie、PAT、数据库路径或原始内部文件；
- 形成 MCP endpoint、credential、scope 配置、工具目录和测试报告这组交付物。
- 当前本地验收允许使用 loopback dev credential 和 broad allowlist；生产 secret provider、TLS、跨主机网络和轮换运维属于后续部署验收，不作为 A3 本地退出条件。

本地 A3 通过后可开始项目接入准备；必须先完成 B0 项目级接入，再进入 B1/B2。当前 B0/B1 已满足本地退出条件，嵌入面板已证明本项目接入后的 Agent→MCP→回答闭环。

## 3. 阶段 B：PiChat 接入（MCP 完成后）

### B0：当前 GeoAgent 项目接入 PiChat

目标：先证明“本项目”已经进入 PiChat 的运行时数据链路，再测试 `geo-research-assistant`。Agent 不是项目接入的替代物；`geo-research-assistant` 只能作为项目接入完成后的业务验收载体。

退出条件：

- 当前 GeoAgent MCP endpoint、项目身份和 PiChat credential 的映射可追溯；
- PiChat 侧能确认请求来自本项目，并保留 GeoAgent `request_id` / `audit_event_id` / provenance；
- 使用项目级最小只读请求完成一次端到端回执，错误不被压扁成“无结果”；
- 项目级连接证据写入实施账本。

### B1：独立 MCP Server 部署与 PiChat 注册/工具加载验收

目标：将已经通过 A3 的 GeoAgent MCP Server 部署到 PiChat 可达环境，并让 PiChat Runtime 直接登记、绑定和加载它；LCF/Plane 不参与运行时调用链。

GeoAgent 侧任务：

- 启动独立 MCP 进程或独立端口，同机联调使用 `127.0.0.1:8401/mcp`；本地 B1 不要求 TLS/reverse proxy；
- 暴露 Streamable HTTP MCP 协议和单独的 `/healthz`，不把 `/api/*` 当作 MCP；
- 配置 credential 校验、`principal_id`、`geo_workspace_id`、allowed snapshot 白名单和只读权限；
- 记录 GeoAgent 自己的 `request_id`、`agent_run_id`、`audit_event_id`，日志脱敏；
- 首阶段只向 PiChat 提供 `geo_*` 只读工具；
- 本地 B1 可直接使用 loopback endpoint 和 dev credential，不直接暴露数据库和原始文件。

PiChat 侧任务：

- 由 PiChat 服务端 MCP `site:` connection 直接登记 GeoAgent MCP endpoint；本地联调允许使用 loopback/dev credential，浏览器不直接持有 token；
- 完成 PiChat credential binding，确认 credential 只存于服务端；
- 配置 GeoAgent MCP 的工具白名单和 `allowedAgentIds`，将它分配给目标 Agent；
- 确认 Runtime assembly 实际加载 `geo_*` 工具；
- 不在浏览器保存 GeoAgent token，也不要求 Plane/LCF 凭据；`PICHAT_BASE_URL` 等 GeoAgent→PiChat 模型出站配置不能当作 MCP 入站证明。

本阶段不使用旧移交材料中的地址、站点名或 LCF endpoint；endpoint、credential 和 scope 都以本次独立 GeoAgent MCP 部署为准。

### B2：基于当前项目接入的 PiChat Agent 真实只读联调与错误验收

当前状态：B0/B1 已通过；嵌入面板新会话已完成真实业务问题验证，证明的是“当前项目接入后”的 Agent 业务链路，而不是单独验证 Agent 壳。独立 `/api/v1/chat` 入口仍因当前工作区未配置 `chat:invoke` 项目 key 而未验收，不影响嵌入链路结论。

启动本地服务后，必须通过真实嵌入 PiChat Agent 发起请求：

> 请读取当前确认工作快照，说明美伊局势中金融战线的可用指标，并列出两条证据来源。

保存脱敏验收证据：

- `principal_id`、`pichat_user_id`、`agent_id`、session 脱敏值；
- `geo_workspace_id`、allowed snapshot 映射和只读权限；
- PiChat 中的 MCP registration、credential binding、Agent assignment、Runtime tool list；
- 实际 `geo_snapshot_get` / `geo_workspace_summary` / `geo_metric_catalog` / `geo_evidence_search` 工具调用；
- `request_id`、GeoAgent `audit_event_id` 和服务端日志关联；
- 返回的 `snapshot_id`、`rule_version`、指标和 evidence IDs；
- PiChat 中文最终回复。

错误矩阵必须逐项验证并保持原语义：

| 场景 | 预期结果 |
| --- | --- |
| 没有 PiChat assignment | `MCP_ASSIGNMENT_REQUIRED` |
| credential 缺失或失效 | `GEO_AUTH_REQUIRED` |
| 没有 GeoAgent scope | `GEO_SCOPE_DENIED` |
| GeoAgent workspace 无快照映射 | `GEO_SCOPE_MAPPING_REQUIRED` |
| snapshot 不存在 | `GEO_SNAPSHOT_NOT_FOUND` |
| 维度不存在 | `GEO_INVALID_DIMENSION` |
| GeoAgent endpoint 不可达 | `GEO_MCP_UNAVAILABLE`，不转成无结果 |
| 工具没有只读授权 | `GEO_TOOL_NOT_ALLOWED` |
| 工具未加载 | `MCP_TOOL_NOT_LOADED` |

完成标准：只有 GeoAgent MCP registration、credential binding、PiChat assignment、Runtime 工具、真实调用、真实快照数据、scope 校验、审计链和中文 UI 全部成立，才能报告“GeoAgent 已接入 PiChat MCP”。

## 4. 阶段 C：性能、稳定性和数据泄露验收

阶段 B 完成后，再进入持续运行验收。目标：确保 Agent 能长期使用，而不是只通过一次 demo。

验收内容：

- 快照摘要响应大小和 P95 延迟；
- 证据搜索分页、详情按需加载；
- 同一 request_id 重试的幂等/去重语义；
- 并发只读调用不会改变 working snapshot；
- 日志不出现 token、Cookie、PAT、原文 secret；
- 旧 snapshot 仍可复现，最新 snapshot 不自动替换 working snapshot；
- 采集运行中、采集失败、快照缺失、导入失败均保留独立状态。

## 5. 建议的实现优先级

P0：scope/auth/audit、snapshot list/get、workspace summary、evidence search/get。

P1：metric catalog/series、analysis view、brief list/get、错误矩阵。

P2：collector status/events、可观测性、限流和性能优化。

完成阶段 C 后才讨论写入：Agent 只能生成变更建议，GeoAgent 自有确认界面或明确的 PiChat 用户确认后才允许执行，写入必须携带 `expected_version`、`idempotency_key`、`object_id`、用户和 scope，并重读验证版本。

## 6. 文件与仓库分工

| 仓库 | 首阶段责任 |
| --- | --- |
| GeoAgent | 独立 MCP Server、工具合同、快照/业务投影、数据 provenance、只读测试 |
| LCF/Plane | 仅作为参考材料；首阶段不参与 GeoAgent MCP 运行时链路 |
| PiChat Runtime | MCP registration/binding/assignment、Runtime assembly、真实工具调用展示 |
| Plane | 不承载 GeoAgent 快照事实；本计划不依赖其运行时接口 |
| 本目录 `/Users/yujunjun/opensource/plane/docs/agent` | 集成分析、构建计划、验收报告和脱敏证据索引 |

## 7. 当前启动与验收命令

GeoAgent 本地联调：

```bash
cd /Users/yujunjun/opensource/GeoAgent
./.venv/bin/python -m scripts.start_local --web-port 8010 --api-port 8011
curl -fsS http://127.0.0.1:8011/api/status
```

GeoAgent 的真实 MCP 接入前置检查：

```bash
curl -fsS http://127.0.0.1:8011/api/workspace-selection
curl -fsS http://127.0.0.1:8011/api/collector/snapshots
```

独立 MCP Server（A1/A2 开发调试阶段）：

```bash
cd /Users/yujunjun/opensource/GeoAgent
./.venv/bin/python scripts/start_mcp.py --host 127.0.0.1 --port 8401
curl -fsS http://127.0.0.1:8401/healthz
```

A3 本地 live Gate（不记录 token；使用环境变量传入）：

```bash
cd /Users/yujunjun/opensource/GeoAgent
GEOAGENT_MCP_VERIFY_TOKEN='<受控本地 credential>' \\
  .venv/bin/python scripts/verify_mcp_gate.py
```

`/healthz` 只能证明 MCP 进程和数据层就绪；真正的协议、认证、工具加载和 PiChat 调用仍需通过 MCP Runtime 验收。

REST 检查只能证明本地 REST/数据状态；MCP `/healthz` 只能证明进程和数据层就绪。A2/A3 还必须执行真实 MCP 协议调用，B1/B2 才执行 PiChat Runtime 工具加载和真实调用验收。

## 8. 里程碑与退出条件

### M0：A0 分析和合同完成

退出：本分析、工具目录、scope、错误码、快照语义评审通过。

### M1：A1 独立 MCP Server 骨架可启动

退出：本地独立 MCP Server 可启动，`/mcp` 和健康检查可用。

### M2：A2 v1 工具可测

退出：合同、scope、provenance、分页、错误测试通过；测试 principal 可直接进行 live MCP 调用。

### M3：A3 独立 MCP 前置 Gate 通过

退出：MCP endpoint、credential、scope、工具目录和测试报告齐备；未授权请求被拒绝；不依赖 PiChat 即可证明 MCP 满足业务需求。

### M4：B1/B2 PiChat 真实只读调用

退出：当前 Agent Runtime 实际加载并调用至少一个 `geo_*` 工具；结果来自指定 working snapshot；中文 UI 显示；无 secret/越权。当前本地已由 `研报合规审计` Agent 实际调用 3 个工具并返回快照、主题覆盖和指标目录；目标 Agent 范围为显式绑定，不推断为任意 Agent。

### M5：C 阶段可持续运行

退出：性能、日志脱敏、旧快照复现、错误矩阵和重启恢复通过。

## 9. 未决问题（开始编码前必须回答）

1. GeoAgent MCP 的正式部署地址、TLS 和网络访问边界是什么？
2. `geo_workspace_id` 如何映射到 GeoAgent 的允许快照集合？
3. 谁签发 GeoAgent MCP credential？采用短期 Bearer/OAuth 还是 mTLS？
4. PiChat 当前实际 Agent、用户绑定和 assignment 是什么？能否提供脱敏 runtime tool list？
5. 首阶段是否只允许 working snapshot，还是允许显式授予历史 snapshot 白名单？
6. 研究简报是否只读已落盘版本，还是后续要由 Agent 提议生成？后者必须另设人工确认门。

在这些问题回答前，不应实现写入工具，也不应把 GeoAgent MCP 直接暴露到公网或 PiChat 浏览器端。
