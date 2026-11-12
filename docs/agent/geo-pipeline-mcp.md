# GeoAgent 采集管线 MCP 接入说明

本文面向第三方 Agent（如 PiChat）：通过 GeoAgent MCP 查看、创建、试跑、启停采集管线。

## 入口与鉴权

- 入口：GeoAgent 服务的 `/mcp`（Streamable HTTP）。
- 鉴权：请求头 `Authorization: Bearer <GeoAgent MCP Key>`。Key 由 GeoAgent 管理员签发，不要写入代码库或对话。
- 所有持有有效 Key 的调用方都能看到并调用下列 13 个工具，不区分管理员；操作者在审计中记为 `mcp:<principal_id>`，版本来源渠道记为 `mcp`。
- Key 的工具白名单未包含某个工具时，该工具返回 `GEO_TOOL_NOT_ALLOWED`。

## 开关与模式

- 功能开关 `GEO_PIPELINES_ENABLED=false` 时，全部管线工具返回 `GEO_FEATURE_DISABLED`。
- 远端采集模式（`GEO_COLLECTION_MODE=remote`）下，写入类工具返回 `GEO_REMOTE_READ_ONLY`，只读工具照常；请到采集端修改。

## 配置结构

```json
{
  "row": {"name": "管线名", "description": "说明", "topics": ["usiran"]},
  "config": {
    "sources": [
      {"id": "rss-a", "type": "rss", "name": "示例 RSS", "feed_url": "https://example.com/feed.xml",
       "allowed_hosts": ["example.com"], "language": "en", "tier": "T2", "content_policy": "metadata_only"},
      {"id": "web-a", "type": "web", "name": "示例网页", "entry_urls": ["https://example.com/news/"],
       "allowed_hosts": ["example.com"], "language": "zh", "tier": "T2", "content_policy": "public_fulltext", "link_include_pattern": "/news/\\d+"}
    ],
    "filters": {"include_any": ["iran"], "include_strong": [], "exclude": ["sport"],
                "require_category": true, "lookback_days": 14, "max_candidates_per_source": 80},
    "request": {"timeout_seconds": 25, "rate_limit_seconds": 1.5}
  }
}
```

- `row.topics` 至少 1 个，只能取 `usiran`、`ukraine`；`name` 必填，`description` 可空。
- `sources` 1 到 30 个，每个来源必填：`id`（小写字母、数字、连字符，≤64 位，不重复）、`type`（`web` 或 `rss`）、`name`、`allowed_hosts`（1–10 个小写主机名，不含协议、路径、端口）、`tier`（`T1`/`T2`/`T3`）、`content_policy`（`public_fulltext` 或 `metadata_only`）。
- `type=rss` 另需 `feed_url`，不得带 `entry_urls`；`type=web` 另需 `entry_urls`（1–10 个链接），不得带 `feed_url`。链接的主机必须在 `allowed_hosts` 内。
- 可选：`language`（默认 `en`）、`link_include_pattern`（正则，≤200 字）。
- `filters`、`request` 整块可省略，默认值：`include_any/include_strong/exclude` 为空数组，`require_category=true`，`lookback_days=14`（1–90），`max_candidates_per_source=80`（1–200），`timeout_seconds=25`（1–60），`rate_limit_seconds=1.5`（0–30）。词总数上限 200，单词最长 100 字符。
- 来源地址会做域名与解析地址校验，内网、回环地址会被拒绝。

## 工具

### 只读（6 个）

| 工具 | 输入 | 输出 |
| --- | --- | --- |
| `geo_pipeline_list` | `topic`、`status`、`kind`、`q`、`limit`(1–50，默认 50) | `{items:[{id,name,kind,topics,enabled,source_count,current_version,last_status,last_trigger,last_finished_at,last_new_items}], total}` |
| `geo_pipeline_get` | `pipeline_id` | 管线详情与当前版本完整 `config` |
| `geo_pipeline_versions` | `pipeline_id`、`limit`(1–50，默认 20) | 版本列表（新到旧）：`version,created_at,created_by,change_note,source_channel,source_count,term_count` |
| `geo_pipeline_runs` | `pipeline_id`、`limit`(1–50，默认 10) | 运行列表：`run_id,trigger(batch/trial),status,started_at,finished_at,snapshot_id,error,stats` |
| `geo_pipeline_run_items` | `pipeline_id`、`run_id`(空取最近一次)、`offset`、`limit`(1–50，默认 20) | `{run_id,trigger,total,items:[{title,summary(≤500字),source_id,source_type,published_at,matched_terms,dimensions,evidence_id}]}` |
| `geo_pipeline_recent_results` | `hours`(1–168，默认 24) | 各管线整批采集汇总：`pipeline_id,name,runs,new_items,last_trigger,last_finished_at` |

### 写入（7 个）

| 工具 | 输入 | 输出 |
| --- | --- | --- |
| `geo_pipeline_preview` | `row`、`config`、`limit`(1–20，默认 5) | 每个来源的候选与过滤结果；不写库、不进快照，可能耗时数十秒 |
| `geo_pipeline_create` | `row`、`config`、`change_note`、`dry_run` | 新管线（默认停用）；`dry_run=true` 只返回 `{dry_run,row,config}`（规范化后），不写库、不检查同名管线 |
| `geo_pipeline_update` | `pipeline_id`、`expected_version`(必填)、`row_patch`、`config`、`change_note`、`dry_run` | 更新后的管线；`dry_run=true` 返回 `{dry_run,current_version,changes:{字段:[旧,新]}}`，不写库；其中 `changes.config` 为完整的旧配置与新配置，不是逐字段差异。配置变化生成新版本 |
| `geo_pipeline_trial` | `pipeline_id` | `{pipeline_id,accepted:true,hint}`；后台运行，通常需要数分钟（最长 15 分钟），可多次用 `geo_pipeline_runs` 查看；产物不进快照、不写去重台账 |
| `geo_pipeline_set_enabled` | `pipeline_id`、`enabled` | 管线。启用后从下一次整批采集起加入全量采集，结果进入快照；停用后旧产物不再进入快照 |
| `geo_pipeline_delete` | `pipeline_id` | `{deleted:true}`（软删除，同时停用） |
| `geo_pipeline_rollback` | `pipeline_id`、`to_version`、`expected_version` | 管线；以历史版本配置生成一个新版本，不能回滚到当前版本 |

内置管线只允许启停；对其更新、删除、回滚、测试运行返回 `GEO_INVALID_ARGUMENT`。

## 错误码

| 错误码 | 含义 |
| --- | --- |
| `GEO_TOOL_NOT_ALLOWED` | Key 白名单不含该工具 |
| `GEO_FEATURE_DISABLED` | 管线功能未启用 |
| `GEO_REMOTE_READ_ONLY` | 远端采集模式，写入被禁止 |
| `GEO_NOT_FOUND` | 管线、版本或运行不存在 |
| `GEO_VERSION_CONFLICT` | `expected_version` 与当前不一致，`details.current_version` 为最新版本，应重新读取后再改 |
| `GEO_INVALID_ARGUMENT` | 参数或配置不合法、内置管线不支持该操作；`details.field` 指出字段 |
| `GEO_CONFLICT` | 该管线已有测试运行在进行 |
| `GEO_UPSTREAM_FAILED` | 试跑时来源抓取失败 |
| `GEO_SCOPE_DENIED` | 无权修改该管线 |

错误 `message` 为中文，可直接展示给用户。`details.field` 只出现在校验类错误中（`GEO_INVALID_ARGUMENT`）；`details.current_version` 只出现在 `GEO_VERSION_CONFLICT`。

参数 JSON 类型错误（例如把 `row` 传成字符串、`limit` 传成文本）会在到达 GeoAgent 之前被 MCP SDK 的参数校验拒绝，返回的是 SDK 的校验错误，而不是 `GEO_INVALID_ARGUMENT`。

工作台试调用（GeoAgent 管理页）中，写入类工具仅限网页管理员调用。

## 限制

- 列表最多 50 条，运行条目每页最多 50 条，试跑每源最多 20 个候选。
- 摘要截断 500 字；不返回源站正文与原始文件。
- 同一管线同时只能有一个测试运行。
- 写入工具不是幂等的：创建重试前先用 `geo_pipeline_list` 确认是否已创建。

## 最小示例

```text
1. geo_pipeline_create   {row, config, change_note:"初版", dry_run:true}   # 先校验
2. geo_pipeline_preview  {row, config, limit:5}                             # 试跑看候选
3. geo_pipeline_create   {row, config, change_note:"初版"}                  # 创建（默认停用）
4. geo_pipeline_trial    {pipeline_id}                                      # 测试运行，立即返回
5. geo_pipeline_runs     {pipeline_id}                                      # 数分钟后查状态，可多次查询
6. geo_pipeline_run_items{pipeline_id}                                      # 展示结果给用户
7. geo_pipeline_set_enabled {pipeline_id, enabled:true}                     # 用户明确同意后才启用
```

修改：`geo_pipeline_get` 取 `current_version` → `geo_pipeline_update`（先 `dry_run:true` 看 `changes`）→ 再正式提交；遇 `GEO_VERSION_CONFLICT` 重新读取。

## 推荐对话流程

新建 → 试跑 → 测试运行 → 展示结果 → 用户同意 → 启用。

1. 与用户确认主题、来源与关键词，组装配置，用 `dry_run` 校验。
2. 试跑，向用户汇报每个来源能否抓到内容、过滤是否合理，必要时调整。
3. 创建（默认停用），发起测试运行，等待完成。
4. 向用户展示最近一次测试运行结果（条目数、示例标题、各来源状态）。
5. 只有在用户明确同意后才调用 `geo_pipeline_set_enabled`；告知启用后从下一次整批采集起生效，结果会进入快照。
6. 停用与删除同样先向用户确认。
