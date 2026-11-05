# 通过 MCP 向客户提供采集数据

本服务负责采集、导入和提供数据。客户决定何时拉取、多久拉取一次，以及如何写入其 PostgreSQL 或其他数据库。本改动不安装客户定时任务，不迁移本项目数据库，也不修改现有页面。

## 服务端准备

1. 部署包含 `geo_sync_manifest`、`geo_sync_chunk` 的新版应用，沿用现有 `/mcp` 服务，不需要另开端口。
2. 在能够访问新闻来源的服务器上运行现有采集任务，并完成快照导入。只有抓取文件、尚未导入时不会产生新同步版本。采集频率仍由现有采集服务负责，不由客户调用触发。
3. 在项目的 MCP 工作台为每个客户创建独立 MCP Key，记录客户用途。通过安全渠道交付地址和 Key；失效或终止合作后撤销 Key。
4. 客户网络必须能够访问你方 HTTPS 地址。例如现有部署的地址是 `https://geoagentd40d.sealosgzg.site/mcp`；新工具在发布新版之前不会出现在当前线上服务。客户无需访问原始境外新闻网站。

认证沿用 `Authorization: Bearer <MCP Key>`，不是页面的 Basic Auth 用户名和密码。现有网关的 `/mcp` 认证由后端负责，其他页面认证不变。保持 HTTPS 证书校验，不在 URL 或日志中放密钥。

目前页面创建的 MCP Key 具有项目全部只读工具/快照权限，并非仅允许下载某个客户的数据。若要求隔离快照或仅允许同步工具，应使用现有独立 MCP 服务的 scope-file 授权配置。同步工具要求 `allowed_topics` 和 `allowed_dimensions` 都包含 `*`，并继续检查 `allowed_snapshot_ids` 和 `allowed_tools`。部分主题授权会拒绝完整下载，不会扩大为全量权限。

## 两个新增工具

### geo_sync_manifest

参数：

```json
{"snapshot_id": "", "known_sha256": ""}
```

- snapshot_id 为空时选择客户可见的最新已导入快照，按导入时间及 ID 排序，**不等于页面当前工作快照**。
- 可以传明确的历史快照 ID；用已有 `geo_snapshot_list` 查询版本。
- known_sha256 填客户上次完整接收的数据哈希；相同则 unchanged=true，无需下载正文。
- 没有可见的已导入快照时 available=false，不用演示目录替代，也不启动爬虫。
- source_created_at 是源快照导入时间，不是最后一条新闻发布时间，不保证采集新鲜度或所有来源成功。

成功响应沿用现有 MCP 业务封装 `{"ok":true,"data":{...},"provenance":{...}}`。data 示例：

```json
{
  "available": true,
  "unchanged": false,
  "schema_version": "geo-sync/v1",
  "snapshot_id": "服务端返回的快照ID",
  "source_created_at": "2026-10-26T12:00:00+00:00",
  "sha256": "完整JSON文件的SHA256",
  "total_bytes": 1234567,
  "chunk_bytes": 262144,
  "encoding": "base64",
  "media_type": "application/json",
  "counts": {"records": 1000, "observations": 20, "sources": 30, "projects": 10}
}
```

### geo_sync_chunk

```json
{"snapshot_id": "manifest返回的固定ID", "offset": 0}
```

每次最多返回 256 KiB 原始字节，以 Base64 放在 data.content；单次 MCP 响应约 350 KiB，不会把完整大快照塞进一个响应，也不截断单条新闻正文。

| 字段 | 含义 |
| --- | --- |
| snapshot_id / sha256 / total_bytes | 固定版本、全文件校验和及大小 |
| offset / returned_bytes | 当前块起始位置和实际字节数 |
| chunk_sha256 | 本块原始字节的 SHA256 |
| content / encoding | Base64 编码数据 |
| has_more / next_offset | 是否还有后续块、下一块起点；结束时 next_offset 为 null |

每个请求重新验证 Key 和 scope；缓存不能绕过权限。下载期间有新版本发布，旧 snapshot_id 对应数据不会变化。若客户使用 `working` 动态授权而工作快照发生切换，后续旧快照下载可能被拒绝；稳定下载宜使用明确快照授权或 `*`。

## 客户接入流程

1. 使用 MCP Streamable HTTP 客户端连接 `/mcp` 并初始化，tools/list 确认两个新工具存在。
2. 调用 manifest，带上本地已成功保存的 known_sha256。无数据或 unchanged 时结束本轮。
3. 固定本轮 snapshot_id，从 offset=0 开始调用 chunk。
4. 检查业务封装的 ok、snapshot_id、全量哈希、offset；Base64 解码，校验 chunk_sha256，再顺序写入临时文件。按 next_offset 继续。
5. 中断后保留已验证字节数、snapshot_id 和 sha256，从该 offset 重试；若授权撤销或源版本已删除，应报错，保留本地已发布版本。
6. 下载完成，检查总字节数和整个文件 sha256，再解析 UTF-8 JSON。**不要对各块分别解码 UTF-8**，多字节字符可能跨块。
7. 客户数据库事务内导入完成后，才切换客户当前版本并记录成功的 sha256。失败时保留原版本。

这是**快照版本级增量检查**：未变化不传输正文；有新版本则完整下载该版本。不提供逐条变更日志，也不要求客户补拉中间所有版本。

## 数据内容与边界

下载得到 `geo-sync/v1` JSON，包含：

- source_snapshot_id、source_created_at：源版本信息。
- records：结构化新闻/证据，保留原始 ID、正文、时间、主题、维度、地理点、出处、核验标签；不会把“已采集”改成“已核验”。
- observations：指标定义、完整时间序列、source_ids；有效零值和空值分别保留。
- sources：该快照的来源状态。
- projects：该快照的采集项目统计。

不导出账号、配置、API 密钥、用户问答、简报、人工复核表、翻译缓存、运行日志及原始附件。raw_refs 和 documents 为空，防止出现指向服务端磁盘的无效下载链接。白名单之外的内部扩展字段不属于 v1 契约，这不是数据库备份或页面所有状态的镜像。

记录属于指定版本的完整数据集。客户按新快照替换当前集合时，旧版本存在但新版本不再包含的记录应退出当前集合；不要只追加，否则删除/排除的数据会继续展示。可保留历史版本支持审计。

已有 geo_evidence_search、geo_evidence_get、geo_metric_series 等仍用于 Agent 查询；新工具用于机器下载，不建议模型读取大量 Base64。页面的“MCP 新闻导入”使用另一套 service_status/knowledge_search 契约，不会自动把本下载协议当成新闻搜索。

## 运维与验收

数据包首次请求时由冻结快照生成，缓存到 `GEO_DATA_DIR/mcp-exports`，后续按块读取，不重复解析大快照。缓存按格式版本和快照 ID 隔离；业务字段变化必须升级格式版本。

为缓存预留磁盘空间，可在无下载任务时清理旧缓存（数据库快照保留即可重新生成）；保留原始数据库备份。首次大版本生成会占用内存和磁盘，发布前需按真实数据量验证。API 主事件循环沿用现有线程池隔离。

验收命令：`python -m pytest tests/test_mcp_sync.py -q`。覆盖完整正文/指标下载、中文跨块、校验、版本固定、断点偏移、空库、未授权及受限 scope、MCP HTTP 工具发现和调用。

# GeoAgent 部署间远端采集与镜像

用于没有境外出口的 GeoAgent（如 HFT 内网）让 Sealos 正式环境采集并拉回快照。设计见 `docs/superpowers/specs/2026-11-05-远端采集与快照镜像设计.md`。接收端是 GeoAgent 自身的 `remote` 模式，不需要另写客户端；下文供排错和其他 GeoAgent 部署接入参考。

## 采集端（Sealos）配置

1. 在 MCP 工作台为接收端创建专用 Key，记下 Key 的 ID（列表中的 `id`），通过安全渠道交付 Key 原文。
2. 设置 `GEO_REMOTE_COLLECT_PRINCIPALS=<Key ID>`（多个用逗号分隔）。未设置时不注册 `geo_collect_request`，其他工具仍全部只读。
3. 可选 `GEO_REMOTE_COLLECT_MIN_INTERVAL_SEC`（默认 1800）：两次远端触发新批次的最短间隔。

撤销该 Key 即同时停止接收端的触发和拉取。

## 接收端（HFT）配置

在 `/etc/geoagent/geoagent.env` 增加（Key 用占位符示意，不要写入仓库）：

```text
GEO_COLLECTION_MODE=remote
GEO_REMOTE_MCP_URL=https://geoagent.lhotsetech.com/mcp
GEO_REMOTE_MCP_KEY=geo_<采集端交付的Key>
GEO_REMOTE_SYNC_INTERVAL_SEC=1800
```

接收端只出站访问采集端 HTTPS 443，校验证书。页面“立即执行”改为请求远端采集，进度、事件实时显示；完成后自动拉取同一快照并设为工作快照。“立即同步”可随时拉取采集端最新快照。门禁、日志、调度配置在采集端处理。

## geo_collect_request

参数 `{"ids": [], "topic": ""}`；`ids` 为空表示全部启用采集器。返回 `data`：

```json
{"run_id": "manual-20261105T080000Z-abcd1234", "request_id": "…", "reused": false,
 "submitted_at": "2026-11-05T08:00:00+00:00", "min_interval_sec": 1800}
```

- 已有批次运行时返回该批次，`reused=true`，不新起批次。
- 新批次以“部分源失败也入库”提交；失败源在 `geo_collector_status` 中可见。
- 错误：`GEO_TOOL_NOT_ALLOWED`（不在白名单）、`GEO_RATE_LIMITED`（`details.retry_after_sec`、上一批 `run_id`）、`GEO_INVALID_ARGUMENT`（未知、停用或排除的采集器）、`GEO_COLLECTOR_BUSY`。

进度用已有 `geo_collector_status(run_id)` 轮询，`run_status` 离开 `running` 后看 `import_status` 与 `snapshot_id`。

## geo_mirror_manifest / geo_mirror_chunk（geo-mirror/v1）

调用方式与 `geo_sync_*` 相同（manifest → 按 `next_offset` 分块 → 校验每块与全量 sha256）。区别：

- 文件是 gzip 压缩的 JSON（`media_type: application/gzip`），需整体校验后再解压；
- 内容为 `{"schema_version":"geo-mirror/v1","snapshot_id","source_created_at","payload","translations"}`，`payload` 是快照原始载荷，接收方对其做规范化 JSON（键排序、紧凑分隔、UTF-8）SHA256 取前 24 位，必须等于 `snapshot_id`；
- `translations` 只含该快照标题、正文、来源名等文本的已有中文译文 `{hash, chinese, method}`，`hash` 为原文同样算法的摘要；
- 与 `geo-sync/v1` 不同，镜像保留原始字段（含 `raw_refs`），只用于受信任的 GeoAgent 部署之间，不作为客户数据契约。

验收命令：`python -m pytest tests/test_mcp_mirror.py tests/test_remote_collect_mcp.py tests/test_remote_collection.py -q`。
