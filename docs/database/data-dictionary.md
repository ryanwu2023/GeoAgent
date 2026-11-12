# 数据字典

数据库为 PostgreSQL，schema 由 `GEO_DATABASE_SCHEMA` 指定（默认 `public`），结构定义在 `backend/schema.sql`，应用启动时幂等执行。时间字段统一 `timestamptz`，按 UTC 存储。

本字典目前只收录按公共字段规范新建的表；`schema.sql` 中的存量表尚未改造，未在此列出。

## 公共字段

| 字段 | 类型 | 可空 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `created_by` | text | 是 | — | 创建人账号；未知时为空 |
| `created_at` | timestamptz | 否 | `CURRENT_TIMESTAMP` | 记录首次建立时间，更新不重置 |
| `updated_by` | text | 是 | — | 最近修改人账号 |
| `updated_at` | timestamptz | 是 | — | 最近实际修改时间，由应用写入层在 UPDATE 时显式设置 |
| `data_timestamp` | timestamptz | 否 | `CURRENT_TIMESTAMP` | 业务数据生成时间 |
| `is_deleted` | boolean | 否 | `false` | 逻辑删除标记 |

## web_users

网页登录的普通用户，由管理员在页面创建（见 [用户管理](../用户管理.md)）。管理员账号来自 htpasswd 文件，不在此表。主键 `id`；无外键；删除策略为逻辑删除，记录永久保留。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `id` | bigint identity | 否 | 自增 | 用户记录唯一标识 |
| `username` | text | 否 | — | 登录账号；CHECK `^[A-Za-z0-9_.@-]{2,64}$`；有效记录内唯一 |
| `password_hash` | text | 否 | — | SHA512-crypt 哈希（5000 轮），不存明文 |
| `product_user_id` | text | 否 | — | 发给 PiChat 的 productUserId，`geo-`+32 位随机十六进制，全局唯一、永不复用 |
| `phone` | text | 否 | — | 发给 PiChat 的 E.164 手机号；CHECK `^\+[1-9][0-9]{7,14}$`；未填写时为按 `product_user_id` 生成的 `+999` 占位号；有效记录内唯一 |
| `pichat_user_id` | text | 是 | — | PiChat 物化的嵌入影子用户 ID；同步成功后在同一事务写入，已提交的记录均有值 |
| `created_by` | text | 是 | — | 创建该用户的管理员账号 |
| `created_at` | timestamptz | 否 | `CURRENT_TIMESTAMP` | 创建时间 |
| `updated_by` | text | 是 | — | 最近改密或删除该用户的管理员账号 |
| `updated_at` | timestamptz | 是 | — | 最近改密或删除时间 |
| `data_timestamp` | timestamptz | 否 | `CURRENT_TIMESTAMP` | 本系统生成的数据，取创建时刻；改密、删除不改变 |
| `is_deleted` | boolean | 否 | `false` | `false` 可登录，`true` 已删除 |

索引：

| 名称 | 类型 | 列 | 条件 | 用途 |
| --- | --- | --- | --- | --- |
| `web_users_pkey` | 唯一 btree | `id` | — | 主键 |
| `web_users_active_username` | 唯一 btree | `username` | `WHERE NOT is_deleted` | 登录查找与“有效账号不重名”；删除后允许重新创建同名账号 |
| `web_users_active_phone` | 唯一 btree | `phone` | `WHERE NOT is_deleted` | 有效用户手机号不重复（PiChat 同一应用内手机号唯一） |
| `web_users_product_user_id_key` | 唯一 btree | `product_user_id` | — | PiChat 身份标识历史全局唯一，删除后也不复用 |

`web_sessions.username` 按账号名关联本表或 htpasswd 文件，不设外键；会话有效性每次通过当前密码哈希指纹校验，改密或删除后旧会话自动失效。

## collection_pipelines

采集管线：可配置（网页/RSS）或内置采集任务，含启停、调度与最近一次运行摘要。主键 `id`；被其余四张管线表以外键引用（`ON DELETE RESTRICT`）；删除策略为逻辑删除，删除后同名重建得到新 `id`，旧台账不会串入。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| ``id`` | text | 否 | — | 管线唯一标识，由应用生成 |
| ``kind`` | text | 否 | — | CHECK：`configurable` 用户配置管线 / `builtin` 内置采集器 |
| ``name`` | text | 否 | — | 管线名称，有效记录内唯一 |
| ``description`` | text | 否 | `''` | 管线说明 |
| ``topic`` | text | 否 | — | 所属主题，目前仅 `usiran`、`ukraine`（由应用校验） |
| ``enabled`` | boolean | 否 | `false` | 是否启用定时调度 |
| ``current_version`` | integer | 否 | — | 当前生效配置版本号，对应 `collection_pipeline_versions.version` |
| ``interval_sec`` | integer | 是 | — | 固定间隔调度（秒），600~604800，与 `daily_times` 二选一（由应用校验） |
| ``daily_times`` | text[] | 否 | `'{}'` | 每日定点时刻 `HH:MM`，最多 24 个，按 `timezone` 解释 |
| ``timezone`` | text | 否 | `'Asia/Shanghai'` | 定点调度时区（IANA） |
| ``next_run_at`` | timestamptz | 是 | — | 下次计划运行时间，未启用或无调度为空 |
| ``last_run_id`` | text | 是 | — | 最近一次运行 `run_id` |
| ``last_status`` | text | 是 | — | 最近一次运行状态，取值同 `collection_pipeline_runs.status` |
| ``last_finished_at`` | timestamptz | 是 | — | 最近一次运行结束时间 |
| ``last_new_items`` | integer | 否 | `0` | 最近一次运行新增条目数 |
| ``owner`` | text | 否 | — | 所有者账号，非管理员只能修改自己的管线 |
| `created_by` | text | 是 | — | 创建该管线的操作者账号（未启用网页登录为 `local`） |
| `created_at` | timestamptz | 否 | `CURRENT_TIMESTAMP` | 记录首次写入时间，更新不重置 |
| `updated_by` | text | 是 | — | 最近修改该管线的操作者 |
| `updated_at` | timestamptz | 是 | — | 最近一次实际修改时间 |
| `data_timestamp` | timestamptz | 否 | `CURRENT_TIMESTAMP` | 管线由本系统生成，取创建时刻 |
| `is_deleted` | boolean | 否 | `false` | 逻辑删除标记，`false` 有效、`true` 已删除 |

`data_timestamp` 来源：管线本系统生成，取创建时刻；后续修改不改变。

索引与约束：

| 名称 | 类型 | 列 | 条件 | 用途 |
| --- | --- | --- | --- | --- |
| ``collection_pipelines_pkey`` | 唯一 btree | `id` | — | 主键 |
| ``collection_pipelines_due`` | btree | `enabled, next_run_at` | `WHERE NOT is_deleted` | 调度器扫描到期的已启用管线 |
| ``collection_pipelines_active_name`` | 唯一 btree | `name` | `WHERE NOT is_deleted` | 有效管线不重名；删除后允许重用名称 |

## collection_pipeline_versions

采集管线配置版本：每次保存配置追加一个版本，保留完整历史。主键 `(pipeline_id, version)`；`pipeline_id` 外键引用 `collection_pipelines.id`（`ON DELETE RESTRICT`）；删除策略为逻辑删除，正常不删除。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| ``pipeline_id`` | text | 否 | — | 所属管线 |
| ``version`` | integer | 否 | — | 版本号，同一管线内从 1 递增 |
| ``config`` | jsonb | 否 | — | 该版本完整配置（来源、锚词、强锚词、排除词、抓取限制等） |
| ``change_note`` | text | 否 | `''` | 变更说明 |
| ``source_channel`` | text | 否 | — | CHECK：`web` 页面 / `mcp` MCP 工具 / `migration` 内置管线迁移 |
| `created_by` | text | 是 | — | 保存该版本的操作者账号 |
| `created_at` | timestamptz | 否 | `CURRENT_TIMESTAMP` | 记录首次写入时间，更新不重置 |
| `updated_by` | text | 是 | — | 最近修改版本记录的操作者（版本内容不可变，通常为空） |
| `updated_at` | timestamptz | 是 | — | 最近一次实际修改时间（通常为空） |
| `data_timestamp` | timestamptz | 否 | `CURRENT_TIMESTAMP` | 版本由本系统生成，取创建时刻 |
| `is_deleted` | boolean | 否 | `false` | 逻辑删除标记，`false` 有效、`true` 已删除 |

`data_timestamp` 来源：版本由本系统生成，取创建时刻。

索引与约束：

| 名称 | 类型 | 列 | 条件 | 用途 |
| --- | --- | --- | --- | --- |
| ``collection_pipeline_versions_pkey`` | 唯一 btree | `pipeline_id, version` | — | 主键；同时支撑按管线取各版本历史 |

## collection_pipeline_runs

采集管线运行记录：一次调度、手动或 MCP 触发的运行及其状态、统计与产出快照。主键 `run_id`；`pipeline_id` 外键引用 `collection_pipelines.id`（`ON DELETE RESTRICT`）；删除策略为逻辑删除。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| ``run_id`` | text | 否 | — | 运行唯一标识，由应用生成 |
| ``pipeline_id`` | text | 否 | — | 所属管线 |
| ``version`` | integer | 否 | — | 运行所用配置版本号 |
| ``trigger`` | text | 否 | — | CHECK：`schedule` 定时 / `manual` 页面手动 / `mcp` MCP 工具 |
| ``status`` | text | 否 | — | CHECK：`queued` 排队 / `running` 运行中 / `success` 成功 / `partial` 部分成功 / `failed` 失败 / `cancelled` 已取消 |
| ``started_at`` | timestamptz | 否 | — | 运行开始时间 |
| ``finished_at`` | timestamptz | 是 | — | 运行结束时间，未结束为空 |
| ``stats`` | jsonb | 否 | `'{}'` | 运行统计（抓取、命中、新增、各来源错误数等） |
| ``snapshot_id`` | text | 是 | — | 本次运行产出的数据快照标识 |
| ``error`` | text | 是 | — | 失败或部分失败的错误摘要 |
| `created_by` | text | 是 | — | 触发该运行的操作者；定时调度为服务身份或空 |
| `created_at` | timestamptz | 否 | `CURRENT_TIMESTAMP` | 记录首次写入时间，更新不重置 |
| `updated_by` | text | 是 | — | 最近更新该运行记录的操作者或服务身份 |
| `updated_at` | timestamptz | 是 | — | 最近一次实际更新时间（如状态变化） |
| `data_timestamp` | timestamptz | 否 | —（应用显式写入） | 运行数据生成时间，取 `started_at` |
| `is_deleted` | boolean | 否 | `false` | 逻辑删除标记，`false` 有效、`true` 已删除 |

`data_timestamp` 来源：取运行开始时间 `started_at`，由应用写入。

索引与约束：

| 名称 | 类型 | 列 | 条件 | 用途 |
| --- | --- | --- | --- | --- |
| ``collection_pipeline_runs_pkey`` | 唯一 btree | `run_id` | — | 主键 |
| ``collection_pipeline_runs_by_pipeline`` | btree | `pipeline_id, started_at DESC` | — | 按管线倒序列出运行历史并分页 |

## pipeline_seen_items

管线去重台账：记录某管线已见过的条目，防止重复入库。主键 `(pipeline_id, record_id)`；`pipeline_id` 外键引用 `collection_pipelines.id`（`ON DELETE RESTRICT`）；删除策略为逻辑删除，但软删除仍保留防重证据，去重判断不过滤 `is_deleted`。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| ``pipeline_id`` | text | 否 | — | 所属管线；管线删除后重建得到新 id，台账天然隔离 |
| ``record_id`` | text | 否 | — | 条目稳定标识（由规范化 URL 等生成） |
| ``first_run_id`` | text | 否 | — | 首次看到该条目的运行 `run_id` |
| `created_by` | text | 是 | — | 写入该条目的操作者或服务身份 |
| `created_at` | timestamptz | 否 | `CURRENT_TIMESTAMP` | 记录首次写入时间，更新不重置 |
| `updated_by` | text | 是 | — | 最近修改台账记录的操作者（通常为空） |
| `updated_at` | timestamptz | 是 | — | 最近一次实际修改时间（通常为空） |
| `data_timestamp` | timestamptz | 否 | `CURRENT_TIMESTAMP` | 首次看到该条目的时刻 |
| `is_deleted` | boolean | 否 | `false` | 逻辑删除标记，`false` 有效、`true` 已删除 |

`data_timestamp` 来源：首次看到该条目的时刻（写入时的当前时间）。

索引与约束：

| 名称 | 类型 | 列 | 条件 | 用途 |
| --- | --- | --- | --- | --- |
| ``pipeline_seen_items_pkey`` | 唯一 btree | `pipeline_id, record_id` | — | 主键；按管线判断条目是否已见过 |

## collection_pipeline_run_items

运行新增条目：每次运行中首次出现的条目明细，用于“本次新增”展示。主键 `(run_id, seq)`；`run_id` 外键引用 `collection_pipeline_runs.run_id`，`pipeline_id` 外键引用 `collection_pipelines.id`（均 `ON DELETE RESTRICT`）；删除策略为逻辑删除。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| ``run_id`` | text | 否 | — | 所属运行 |
| ``seq`` | integer | 否 | — | 运行内序号，从 1 起 |
| ``pipeline_id`` | text | 否 | — | 所属管线（冗余，便于按管线查询） |
| ``record_id`` | text | 否 | — | 条目标识，对应 `pipeline_seen_items.record_id` |
| ``evidence_id`` | text | 是 | — | 入库后对应的证据标识，未入库为空 |
| ``source_id`` | text | 否 | — | 来源标识，对应配置中的来源 id |
| ``source_type`` | text | 否 | — | CHECK：`web` / `rss` / `search`（搜索源后续期开放） |
| ``title`` | text | 否 | — | 条目标题 |
| ``published_at`` | timestamptz | 是 | — | 源站发布时间，未知为空 |
| ``language`` | text | 否 | `''` | 语种代码，未识别为空串 |
| ``matched_terms`` | jsonb | 否 | `'[]'` | 命中的锚词、强锚词（JSON 数组） |
| ``dimension`` | text | 是 | — | 归入的研究维度，未归类为空 |
| ``summary`` | text | 否 | `''` | 条目摘要，截断到 1000 字，无摘要为空串 |
| `created_by` | text | 是 | — | 写入该条目的操作者或服务身份 |
| `created_at` | timestamptz | 否 | `CURRENT_TIMESTAMP` | 记录首次写入时间，更新不重置 |
| `updated_by` | text | 是 | — | 最近修改的操作者（通常为空） |
| `updated_at` | timestamptz | 是 | — | 最近一次实际修改时间（通常为空） |
| `data_timestamp` | timestamptz | 否 | —（应用显式写入） | 源数据时间：取 `published_at`，缺失回退为运行开始时间 |
| `is_deleted` | boolean | 否 | `false` | 逻辑删除标记，`false` 有效、`true` 已删除 |

`data_timestamp` 来源：取 `published_at`；缺失时回退为所属运行 `started_at`，由应用显式写入。

索引与约束：

| 名称 | 类型 | 列 | 条件 | 用途 |
| --- | --- | --- | --- | --- |
| ``collection_pipeline_run_items_pkey`` | 唯一 btree | `run_id, seq` | — | 主键；按运行顺序列出本次新增 |
| ``collection_pipeline_run_items_by_pipeline`` | btree | `pipeline_id, run_id` | — | 按管线查看各次运行的新增条目 |
