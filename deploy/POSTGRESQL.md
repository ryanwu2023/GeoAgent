# PostgreSQL 部署与旧数据迁移

主业务数据库使用 PostgreSQL，部署目标为 PostgreSQL 17。业务数据、翻译、复核、简报、问答（含 QA 运行、步骤、产物）、连接配置、MCP Key 哈希与审计、采集账本放在同一个库；`GEO_DATA_DIR` 继续保存原始归档、采集输出、日志及模型配置文件。当前仍采用单应用实例、单采集调度器，数据库切换不代表已经实现应用多活。

全部业务数据库已统一使用 PostgreSQL，包括内部宏观消息、标签和采集运行记录。运行时不再创建 SQLite；SQLite 仅用于读取历史文件的迁移工具和迁移测试。首次线上切换见 [Sealos 首次 PostgreSQL 上线步骤](SEALOS-POSTGRES-CUTOVER.md)。

## 新环境

1. 创建独立数据库和账号，授予该库内目标 schema 的建表及读写权限。
2. 安装 `requirements.txt`，为 Web、MCP、采集和翻译进程统一配置 `GEO_DATABASE_URL=postgresql://geoagent:密码@数据库主机:5432/geoagent`。密码中的特殊字符需要 URL 编码；跨主机连接按部署要求增加 `sslmode=verify-full` 等参数。所有启动方式都必须把变量传入进程。
3. 可选 `GEO_DATABASE_SCHEMA`，默认 `public`。首次数据库访问自动初始化 schema；未配置 URL 会明确失败，不会隐式创建本地数据库。
4. Kubernetes 将 URL 保存到 `geoagent-postgresql` Secret 的 `GEO_DATABASE_URL` 字段；`deploy/sealos.yaml` 已引用该 Secret。不要将真实凭据提交仓库。

## 已有 SQLite 数据

同事本地升级（含 Windows、Docker PostgreSQL、停机、完整文件备份和独立复核）请按 [本地 SQLite → PostgreSQL 操作手册](LOCAL-SQLITE-TO-POSTGRES.md) 执行。

先停止 Web、MCP、采集、翻译及其他写入进程，对整个旧 `data` 目录做离线备份。迁移到新建的空库或空 schema：

```bash
export GEO_DATABASE_URL='postgresql://geoagent:密码@数据库主机:5432/geoagent'
python -m scripts.migrate_sqlite_to_postgres --data-dir /原来的/data --check-only
python -m scripts.migrate_sqlite_to_postgres --data-dir /原来的/data --backup-dir /独立的全新备份目录 --source-stopped
```

迁移命令只读打开 `research.sqlite3` 、可选的 `collection-runs/ledger.sqlite3` 和 `ht-macro/messages.sqlite3`（自定义路径用 `--internal-message-db` 或旧 `HT_MACRO_DB_PATH` 指定；显式路径不存在时会报错），在一个 PostgreSQL 事务中导入映射表（包含 `qa_runs`、`qa_steps`、`qa_artifacts`）、校验每表行数与内容校验和、修复自增序列。未知表、非空目标表、完整性错误或外键错误将使迁移失败并回滚数据。缺失的历史可选表留空，旧 Key 表缺失的 `deleted_at` 使用 NULL。原 SQLite 文件不会修改或删除。

核对迁移输出、快照数量、工作快照、连接配置、Key 状态、历史问答/简报和采集日志，再启动新版本。首次数据库切换须在维护窗口完成；旧版运行容器无法执行 PostgreSQL 备份，不能直接套用原来的滚动发布流程。切换后产生新写入时，不能只回退旧镜像，否则旧版会读取过期的 SQLite 数据。

## 备份与恢复

安装与服务器同版本或更高版本的 PostgreSQL 客户端。应用镜像包含 PostgreSQL 17 客户端，适用于服务器 17 及以下版本。

```bash
python -m scripts.ci.backup 123
# 自定义目录可通过 Python 调用 backup(Path('/数据目录'), '123')。
# 恢复到新建的空库，连接 URL 不放在命令行参数里：
export PGHOST='数据库主机' PGPORT='5432' PGUSER='geoagent' PGDATABASE='geoagent_restore'
# PGPASSWORD 从受控环境注入；不要把 URL 直接设置为 PGDATABASE。
pg_restore --dbname="$PGDATABASE" --exit-on-error --single-transaction --no-owner --no-acl /备份/database.dump
```

备份脚本生成一致的 PostgreSQL custom-format dump，并校验目录可读取；恢复可用性仍须通过实际恢复演练确认。数据库备份覆盖库内所有 schema。`data` 中的附件、归档和模型配置另行备份；需要跨文件与数据库一致的恢复点时停写后一起备份。`GEO_DATA_DIR` 的复制不能代替数据库备份。恢复前停止应用，恢复后重启进程清空缓存。

## 测试

测试必须指定 `GEO_TEST_DATABASE_URL`，不会继承生产 `GEO_DATABASE_URL`。每个测试创建随机 schema 并在结束时删除。

```bash
export GEO_TEST_DATABASE_URL='postgresql://geoagent:密码@127.0.0.1:5432/geoagent_test'
python -m pytest -q
```

测试账号需能创建和删除自己的 schema。GitLab 验证阶段自动启动临时 PostgreSQL 17 容器与独立网络，结束后清理。所有业务模块都通过统一 PostgreSQL 连接层读写；内部消息的毫秒时间戳使用 BIGINT，采集批次使用事务锁防止并发去重冲突。演练时设置 `GEO_BACKGROUND_TASKS_ENABLED=false` 禁用自动翻译，并且不启动独立采集服务。
