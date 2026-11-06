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
