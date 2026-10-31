# 同事本地升级：SQLite → PostgreSQL

适用版本：包含 GitHub `4da4020` 及其前面的 19 个新提交，并整合 PostgreSQL、独立登录页、MCP 同步与采集修复的版本。不要把旧版 SQLite 服务和新版 PostgreSQL 服务同时运行在同一个数据目录上。

## 迁移什么

| 原位置 | 目标 | 内容 |
|---|---|---|
| `data/research.sqlite3` | PostgreSQL 17 | 快照、证据版本、复核、问答、分析、简报与模板、翻译、工作快照选择、MCP Key 哈希与审计、QA 运行/步骤/证据产物 |
| `data/collection-runs/ledger.sqlite3`（可选） | PostgreSQL 17 | 采集批次、事件、发布门禁及复核记录 |
| `data/ht-macro/messages.sqlite3`（可选） | PostgreSQL 17 | 内部消息、标签、采集记录；也支持旧 `HT_MACRO_DB_PATH` 和 `--internal-message-db` 指定的其他位置 |
| `data` 中的 JSON、附件、归档、采集输出 | 保持原文件位置，完整备份 | 模型/连接配置、Agent 预算设置、原始证据和采集日志等，不需要改成数据库表 |

迁移保留原始快照 ID、历史回答 JSON（包括费用信息）、日期、中文、MCP Key 哈希和工作快照选择。会晤日历若原快照已有 `meetings` 字段，会原样保留；若原来没有，迁移不会凭空生成日程。

新增的 Agent 配置使用 `data/qa-agent-settings.json`；问答运行、步骤、证据产物仍经统一 PostgreSQL 存储层保存。运行时不使用 SQLite。迁移工具保留 Python 自带的 `sqlite3`，专门读取旧数据库，这是必要的迁移依赖。

## 1. 停止旧环境，保留回退点

1. 记录旧代码提交：`git rev-parse HEAD`。
2. Windows 在旧项目目录运行 `stop.bat`；确认额外启动的 Web、MCP、采集器、翻译任务和内部消息服务也已停止。Linux/macOS 停止对应服务或进程。
3. 保留旧 `.env`，不要用 `.env.example` 覆盖。把新代码更新到本地；不要删除旧 `data` 或 `collectors/*/output`。
4. 暂时不要双击新版本 `start.bat`。必须先完成数据库迁移，避免新服务初始化业务数据，导致目标库不再为空。

迁移脚本会额外复制整个 `data` 目录。采集器源码目录下尚未发布的 `collectors/*/output`、项目根目录 `.env` 和外部路径的模型配置，需要自己另行保留；它们不在 `data` 目录备份范围内。

## 2. 准备本地 PostgreSQL

已有 PostgreSQL 17 时：用管理员创建一个**新的空数据库和专用用户**，不要选正在使用的正式库，跳到第 3 步。

没有时，可以使用 Docker Desktop 的 Linux 容器。在项目根目录现有 `.env` 中加入以下配置（密码自行设置，不要提交到 Git）：

```dotenv
GEO_LOCAL_PG_PASSWORD="替换成自己的数据库密码"
GEO_DATABASE_URL="postgresql://geoagent:URL编码后的密码@127.0.0.1:55432/geoagent"
GEO_DATABASE_SCHEMA="public"
GEO_BACKGROUND_TASKS_ENABLED="false"
```

连接 URL 中的密码需要 URL 编码，尤其是 `@`、`:`、`/`、`#`、`%`。`GEO_LOCAL_PG_PASSWORD` 保留原始密码。应用读取的是 `GEO_DATABASE_URL`。

启动数据库并等待状态变为 `healthy`：

```text
docker compose --env-file .env -f deploy/compose.postgresql-local.yaml up -d --wait
docker compose --env-file .env -f deploy/compose.postgresql-local.yaml ps
```

数据库只监听本机 `127.0.0.1:55432`；数据保存在 Docker 命名卷里。不要执行 `down -v`，那会删除数据库卷。初始化后仅修改 `.env` 密码不会修改已有数据库账号密码。

## 3. 安装新版 Python 依赖

要求 Python 3.11 或 3.12，推荐 3.12。下列命令均在项目根目录执行。

Windows PowerShell：

```powershell
# 已有 .venv 则跳过创建。
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Linux/macOS：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

后面的 `python` 需使用这套虚拟环境：Windows 可先运行 `.\.venv\Scripts\Activate.ps1`，或把命令中的 `python` 换成 `.\.venv\Scripts\python.exe`；Linux/macOS 使用 `source .venv/bin/activate`。

## 4. 只检查，不写目标库

```text
python -m scripts.migrate_sqlite_to_postgres --env-file .env --data-dir data --check-only
```

这一步不连接 PostgreSQL，只检查 SQLite 完整性、外键和表映射，列出各源数据库表行数。主 `research.sqlite3` 必须存在且具有快照表；账本和内部消息库可以不存在。遇到未知业务表会停止，不能通过删除未知表来强行通过。

`.env` 里的设置不会覆盖终端已存在的同名环境变量。若之前使用过别的数据库 URL，先清除终端的旧变量或重新打开终端，确保目标正确。

## 5. 正式迁移并保存备份

下面的备份路径位于项目目录外；每次迁移请使用一个**全新、尚不存在的目录**：

```text
python -m scripts.migrate_sqlite_to_postgres --env-file .env --data-dir data --backup-dir ../geoagent-sqlite-backup-20261028 --source-stopped
```

`--source-stopped` 表示你已经停止全部旧/新应用及后台写入任务；脚本不能代替你检查其他电脑或终端上的进程。

脚本会依次：

1. 检查旧数据库和表映射。
2. 备份整个 `data`；如内部消息 SQLite 在外部路径，也备份该数据库及其 WAL/SHM 文件。
3. 为备份文件生成 SHA-256 清单 `backup-manifest.json`。
4. 从**备份副本**读数据，在新 schema 中初始化 PostgreSQL 表。
5. 拒绝向已有业务数据的目标库导入；所有映射表在一个事务中复制。
6. 逐表比较行数和逐行内容摘要，修复自增 ID 序列，全部通过才提交。
7. 保存 `migration-report.json`；成功时退出码为 0 且 `ok=true`、`rows_verified=true`。

任何源数据库、表或行转换失败都会回滚本次业务数据写入，不覆盖旧 SQLite 文件；目标 schema 和空表可能已初始化。不会自动覆盖、清空或重试非空目标数据库。失败报告给出错误类型，修正问题后使用新的备份目录重试。

自定义内部消息库示例（PowerShell、cmd 都支持路径双引号）：

```text
python -m scripts.migrate_sqlite_to_postgres --env-file .env --data-dir data --internal-message-db "D:/old-macro/messages.sqlite3" --backup-dir "D:/GeoAgentBackups/migration-01" --source-stopped
```

## 6. 启动应用前独立复核

保持应用停止，再对照备份验证一次：

```text
python -m scripts.migrate_sqlite_to_postgres --env-file .env --data-dir ../geoagent-sqlite-backup-20261028/data --verify-only --source-stopped --report ../geoagent-sqlite-backup-20261028/verification-report.json
```

若内部消息使用外部路径，补上 `--internal-message-db ../geoagent-sqlite-backup-20261028/external-internal-messages/messages.sqlite3`（文件名以实际备份为准），防止旧 `.env` 的 `HT_MACRO_DB_PATH` 指回原位置。

复核对比源表行数和内容，不再次导入，不修改业务行。应用启动后产生的新问答、审计、翻译会让旧备份与数据库不再相同，所以这个检查应在开放使用前完成。

## 7. 启动与功能验收

Windows 首次只启动 Web，不启动采集和内部消息后台任务：

```powershell
.\start.ps1 -Build -NoCrawlers -NoInternalMessages
```

Linux/macOS：

```bash
npm ci
npm run build
python scripts/serve.py --host 127.0.0.1 --port 8000
```

检查 `http://127.0.0.1:8000/readyz` 返回正常，再核对：

- 快照数量、最新快照 ID、工作快照选择与迁移前一致。
- 原有问答、费用回执、简报、模板和翻译可读取。
- 模型、PiChat、MCP 连接配置仍在；原 MCP Key 可正常调用。
- 采集历史、QA 步骤和证据详情可打开。
- Agent 设置、单采集器重跑入口存在；先核对设置，不立即启动全量采集。
- 新建一个本地测试问答，确认写入 PostgreSQL 后可再次读取。

本地默认不启用网页登录认证（只绑定回环地址）。如果旧 `.env` 开启 `GEO_WEB_AUTH_ENABLED`，必须同时配置本地可读的 `GEO_WEB_AUTH_FILE`，不能照搬 Sealos 容器内的认证文件路径。

确认后恢复 `GEO_BACKGROUND_TASKS_ENABLED=true`，重启服务，再恢复需要的采集/消息任务。仍缺外部 API Key 的采集器会保持缺配置状态；数据库迁移不会修复外部数据源权限。

## 回退边界

尚未开放写入时，可以停止新版，恢复旧代码和旧 `.env`，继续使用未修改的旧 SQLite 数据；先确认 PostgreSQL 新产生的数据不需要保留。

开放使用后如需回退，不能只切回旧代码，否则会丢失新问答等变更。保留新 PostgreSQL 和备份，先评估新数据导出/恢复方案。备份中含业务数据和配置，不要上传 GitHub/GitLab。
