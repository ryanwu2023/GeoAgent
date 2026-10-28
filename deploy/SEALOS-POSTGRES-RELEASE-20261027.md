# GeoAgent PostgreSQL 全量改造与 Sealos 发布记录

日期：2026-10-27（美国洛杉矶时间；集群日志使用 UTC）。状态：已发布，正式入口已恢复。

## 1. 本次完成的范围

全部业务数据库已接入 PostgreSQL：研究快照、证据版本、翻译、复核、简报、问答、QA 运行/步骤/产物、MCP 凭据和审计、采集账本，以及内部宏观消息、标签、采集运行记录。内部消息时间戳使用 BIGINT，并用事务锁保证并发批次的去重和标签更新一致。

运行时代码不再连接 SQLite。SQLite 依赖仅保留在只读历史迁移工具和迁移测试中；旧正式 SQLite 文件保留为迁移资料，经哈希核对，新版启动、查询和重启后文件内容未改变。原始归档、采集文件、日志及模型配置仍保存在应用文件卷，不等于 PostgreSQL 内的数据。

本次基于主分支 `3921ac5` 和本地 PostgreSQL/MCP 改造构建镜像，没有声称远程 main 已包含这些未提交修改。发布用源码归档及校验和保存在本地 `output/postgres-release/`，集群 ConfigMap `geoagent-postgres-cutover-20261027` 记录镜像和源码校验和。

## 2. 正式资源配置

命名空间：`ns-d40dap45`。本次为 GeoAgent 新建独立 PostgreSQL 17.11 单实例；没有复用其他项目数据库，也没有配置主备或自动故障切换。

| 组件 | 资源预留 request | 运行上限 limit | 持久化空间 |
| --- | --- | --- | --- |
| PostgreSQL | 0.5 核、2 GiB 内存 | 2 核、4 GiB 内存 | 20 GiB 数据卷 |
| 每日备份任务 | 0.1 核、128 MiB 内存 | 1 核、512 MiB 内存 | 独立 10 GiB 备份卷 |
| 原 GeoAgent 应用 | 0.1 核、256 MiB 内存 | 2 核、4 GiB 内存 | 保留原 5 GiB 文件卷 |

应用临时空间预留 256 MiB、上限 2 GiB；数据库临时空间预留 256 MiB、上限 1 GiB。持久化数据、WAL 位于数据库 PVC，不能用容器临时磁盘替代。

主要参数：`shared_buffers=1GB`、`effective_cache_size=3GB`、`work_mem=8MB`、`maintenance_work_mem=256MB`、`max_connections=60`、`max_wal_size=2GB`、`min_wal_size=256MB`、`idle_in_transaction_session_timeout=60s`。

4 GiB 是初始运行上限，粗略按共享缓存 1 GiB、连接/查询和维护约 1 GiB、文件缓存和波动余量约 2 GiB 考虑；这些不是彼此独立的硬性配额。`effective_cache_size` 只是查询规划估算，不直接分配 3 GiB 内存；`work_mem` 可按多个算子、多个连接叠加。[PostgreSQL 内存参数说明](https://www.postgresql.org/docs/17/runtime-config-resource.html)

迁移后的正式库约 **204 MiB**。20 GiB 为初始容量，规划上可给数据与索引约 8 GiB、WAL 波动约 4 GiB、维护临时空间约 2 GiB，保留约 6 GiB 空闲。`max_wal_size` 不是 WAL 的绝对硬上限。空间持续超过 70% 时检查归档增长并准备扩容，不等到磁盘写满；当前账号无权读取集群 StorageClass 的扩容能力，因此未承诺可以直接在线扩容 PVC。

## 3. 网络、账号与原系统配置

- PostgreSQL Service 仅为 ClusterIP，没有公网地址和 NodePort。
- 命名空间已有默认拒绝网络策略，新增策略只放行 GeoAgent、演练/迁移任务及备份任务到数据库 5432 和必要的 DNS。
- 应用账号 `geoagent` 不是超级用户，不具备创建数据库和创建角色权限，只管理本项目数据库。
- 连接串及密码通过 Secret `geoagent-postgresql` 注入，不写入源码和报告。
- 原网关登录、域名、PiChat 配置、代理隧道、业务文件卷均保留；应用自动翻译在内部验收期间关闭，正式启动后恢复原行为。

配置文件：`sealos-postgresql.yaml`、`sealos-postgresql-network.yaml`、`sealos-postgresql-backup.yaml`。实际镜像使用固定 digest。

## 4. 迁移与验证结果

先在线提取一致性副本进行演练，再暂停正式入口和旧应用，制作最终离线文件备份并迁移。迁移脚本对所有源表进行完整性检查、行数及内容摘要校验，拒绝未知表、非空目标库和不完整的数据，失败则回滚整个导入事务。

| 项目 | 最终迁移数量 |
| --- | ---: |
| 快照 | 11 |
| 证据版本记录 | 71,437 |
| 翻译 | 27,186 |
| 原有 MCP 凭据记录 | 22 |
| 采集运行 | 19 |
| 采集事件 | 12,074 |
| 采集复核 | 5 |

线上没有发现内部消息历史 SQLite 库，因此此部分没有历史数据需要导入；新表和功能已通过测试。正式 MCP 验收新建了一条测试凭据，验证后撤销并隐藏，保留撤销墓碑；原来的 22 条凭据及哈希没有改写。

验证证据：

- 前端生产构建与 128 项前端测试通过。
- 最终完整后端回归：750 项通过、2 项跳过；跳过项为 Windows 批处理执行测试和缺少历史快照的旧测试场景。
- Linux 发布镜像执行全量测试后发现一项测试 DNS 模拟影响清理连接的问题，已修复测试隔离；最终镜像的 15 项相关回归全部通过。
- 线上副本成功导入演练库；备份恢复到独立空库并核对所有 17 张历史源表。
- 最终正式库再次备份并恢复到独立空库，17 张历史源表数量全部匹配。
- 正式页面静态资源、工作区、证据、地图记录、会晤、内部消息状态及网关认证检查通过。旧 SQLite 文件内容在新版启动和重启后保持不变。
- MCP 22 个工具可发现；manifest 变化判断、分块校验、断点续传、重复读取、撤销后拒绝访问检查通过。
- 证据列表 10 并发、30 次请求全部成功；正式库初次验收 P95 约 0.41 秒。工作区/地图首次请求约 3–4 秒。该小规模测试不代表大模型任务、全部页面或长期压力测试的性能保证。
- 两个公网域名的 `/api/status` 均返回预期的 HTTP 401 登录认证响应；内部接口正常返回 200。

真实外部采集和收费模型调用没有为本次验收额外触发，相关单元测试使用模拟响应；此次没有重置客户同步游标。

## 5. 备份与恢复

独立备份卷：`geoagent-postgresql-backups`，10 GiB。

- 最终离线应用文件备份：`/cutover-20261027/application-data.tar.gz`，约 533 MiB，已完整读取校验压缩包并记录 SHA-256。
- 最终 SQLite 一致性副本、逐表迁移记录和备份清单位于 `/cutover-20261027/`。
- 首次正式 PostgreSQL 备份：`/release-backups/2026102701/database.dump`，有 SHA-256 和恢复验证记录。
- 每天北京时间 **02:15**，CronJob `geoagent-postgresql-backup` 创建数据库备份，保留最近 **7 份成功备份**。已手动触发并成功完成一次。
- 每日任务备份 PostgreSQL；应用附件、原始文件、配置的整卷归档是本次切换时制作的，不能宣称每天也进行整卷备份。
- 集群外副本保存在本地 `output/postgres-release/offcluster-backup/`，应由负责人接管到长期备份存储；本地副本不属于自动异地备份。

每日逻辑备份最坏可能丢失接近 24 小时的数据；本次没有部署持续 WAL 归档、时间点恢复或自动故障切换。数据库备份是时间点一致的，外部文件需要单独保留。[PostgreSQL 逻辑备份说明](https://www.postgresql.org/docs/17/backup-dump.html)

恢复时先暂停写入，创建空的 PostgreSQL 数据库，使用 `pg_restore --exit-on-error --single-transaction --no-owner --no-acl --dbname=目标库 database.dump`，将连接信息通过独立 `PGHOST`、`PGPORT`、`PGUSER`、`PGPASSWORD` 环境变量传入。校验所有表及业务接口后，再切换应用 Secret 并重启。

**正式开放后不能直接换回旧 SQLite 镜像**，否则会读到迁移前的旧数据。应修复 PostgreSQL 版本，或恢复 PostgreSQL 备份并处理备份后的新增数据。本次已更新发布归属标记，旧 GitLab 发布任务不能直接执行所属校验通过的回滚。

## 6. 过程中修复的问题

1. 原应用容器临时盘默认限额仅 100 MiB，首次制作副本时触发 Pod 驱逐，原持久化卷保持完整，服务自动恢复。已增加应用临时盘至 2 GiB，并把后续副本放在持久化卷。
2. 命名空间默认拒绝网络访问，演练应用最初不能连接新库；已添加仅针对本项目的数据库与 DNS 网络策略。
3. 真实备份发现把 URI 直接放入 `PGDATABASE` 不会被 `pg_dump` 按连接串展开；已改为解析并设置独立 libpq 环境变量，补充密码编码/TLS 测试，真实备份和恢复均通过。
4. 修复固定日期测试和 DNS/连接环境变量模拟作用域，避免测试清理误报。

## 7. 发布镜像

应用：`crpi-pkfixdfs4f7qepax.cn-shanghai.personal.cr.aliyuncs.com/stocklink-base/geoagent@sha256:88be9eb85dfc358d8b2965afae79f5b06bb7a66b7fac8e96ecab6f856f390938`

PostgreSQL：`crpi-pkfixdfs4f7qepax.cn-shanghai.personal.cr.aliyuncs.com/stocklink-base/geoagent@sha256:2cd2c0f809604aaeff6f0ec8f40aecbd025613107561015c69369a1a7f1d3416`

测试日志、逐表校验、MCP 验证、恢复记录、源码归档及发布记录见本地 `output/postgres-release/`。其中包含业务备份的目录限制为当前用户访问，请勿上传到公开讲解网站。
