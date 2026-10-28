# 线上 PostgreSQL 数据导入本地空库

本次直接提供 PostgreSQL 原生 SQL，不需要 SQLite，也不需要运行 SQLite 迁移脚本。

## 下载

[下载 SQL、校验文件和导出清单](https://github.com/ryanwu2023/GeoAgent/releases/tag/data-export-20261028)

下载 `geoagent-production-20261028.sql`（约 296 MiB）。该文件包含表结构、数据、主外键、索引和序列，不包含创建数据库或创建用户的命令。

## 导入已有空库

建议使用 PostgreSQL 17.11，并使用配套的 `psql` 客户端执行。不要直接粘贴到普通 SQL 查询编辑器：文件包含 PostgreSQL 的 `COPY` 数据和 psql 命令。

先停止指向目标库的 GeoAgent，避免应用启动时创建表；目标库必须是空库。使用该库的所有者账号执行以下命令，将主机、端口、用户、数据库名和文件路径替换为实际值：

```sh
psql -X -h 127.0.0.1 -p 5432 -U geoagent -d geoagent -W -v ON_ERROR_STOP=1 --single-transaction -f geoagent-production-20261028.sql
```

密码按提示输入。Windows 可以在 PowerShell 中使用 `psql.exe` 的完整安装路径执行同样参数。`ON_ERROR_STOP` 和单事务确保导入失败时回滚，不留下半导入数据。

导入成功后检查：

```sql
SELECT count(*) FROM snapshots;     -- 12
SELECT count(*) FROM records;       -- 82307（记录及版本，非当天新增量）
SELECT count(*) FROM translations;  -- 28902
SELECT value FROM workspace_state WHERE key = 'working_snapshot_id';
-- 445a64d159572d0c55315496
```

然后将应用的 `GEO_DATABASE_URL` 配置为本地库的连接信息，再启动 GeoAgent。

## 数据范围

- 来源：2026-10-28 从线上 PostgreSQL 以 pg_dump 一致性快照导出。
- 包含：线上业务表、12 个快照、证据记录、翻译、简报、页面缓存及采集运行历史。完整表计数和文件 SHA-256 见 `manifest.json`。
- 不携带线上连接配置、PiChat 集成配置、MCP 访问密钥、登录会话及鉴权审计记录；这些配置需在本地重新设置。相关表结构保留。
- 仅导出数据库，不包含服务器磁盘上的原始采集文件、附件、归档目录或部署环境变量。依赖这些磁盘文件的下载/查看功能需另行复制相应目录；数据库中的快照与记录已包含在 SQL 内。
- 已在独立空库实际导入，核对全部 26 张表的行数和内容摘要，并核对索引、序列。该文件是可分享的业务数据副本，不是包含线上凭据的整机备份。

GitHub 仓库及其 Release 是公开的，本次交付的数据文件也可公开下载。
