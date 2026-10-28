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
