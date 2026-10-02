# GeoAgent 启动环境与 Phase 1 记录

日期：2026-10-02  
项目：`/Users/yujunjun/opensource/GeoAgent`

## 一、启动环境归档

当前推荐的开发联调环境是前后端分离：

| 组件 | 地址 | 说明 |
| --- | --- | --- |
| Vite Web | `http://127.0.0.1:8010/` | 浏览器入口，`/api` 由 Vite 代理 |
| FastAPI | `http://127.0.0.1:8011/` | GeoAgent API，健康检查 `/api/status` |
| 旧单进程兼容 | `http://127.0.0.1:8000/` | `scripts.serve`/Windows `start.ps1` 默认入口 |

8000 曾被本机 Colima SSH 监听，不能假设为空闲。8010 不能把 `/api` 代理到陌生的 8000 服务；当前代理通过 `GEOAGENT_API_TARGET` 或 `VITE_API_TARGET` 配置。

推荐启动：

```bash
cd /Users/yujunjun/opensource/GeoAgent
.venv/bin/python -m scripts.start_local
```

启动入口会：

1. 检查 API/Web 端口是否已被占用；
2. 启动 API 和 Vite 子进程；
3. 等待 `/api/status` 和 Web 根路径可访问；
4. 注入 `GEOAGENT_API_TARGET=http://127.0.0.1:8011`；
5. 收到 `Ctrl-C` 时回收本入口创建的子进程。

调试选项：

```bash
.venv/bin/python -m scripts.start_local --dry-run
.venv/bin/python -m scripts.start_local --no-frontend
.venv/bin/python -m scripts.start_local --api-port 8121 --web-port 8120
```

Windows：`.venv\Scripts\python.exe -m scripts.start_local` 用于开发联调；`start.ps1 -Port 8011` 仍是单进程后台式入口。停止脚本只停止自己记录且身份匹配的进程，不停止陌生监听进程。

## 二、已验证证据

- 临时 API 端口 8122 真实启动成功，`/api/status` 返回 HTTP 200，`rule_version=v6.3`，可读到 3 个快照；`Ctrl-C` 后进程正常退出。
- 当前 8010 代理到 8011 返回 HTTP 200；采集监控接口返回 `collection-run-report`、运行号 `20261001T142420Z`、19 个采集器、0 个失败。
- 内嵌浏览器已看到真实快照、地图和采集管线监控；外部 CDP/Playwright 调试浏览器已关闭。
- 当前全量 pytest：130 passed，保留 1 个 Starlette/httpx 弃用警告；前端 `npm run build` 通过。
- 临时目录真实执行 `execute_once`（2 个模拟结果）得到 `partial`，账本事件顺序为 `run_started → collector_finished → collector_finished → run_finished`，失败采集器事件为 `failed`；没有写入项目真实采集数据目录。

## 三、Phase 1 当前状态

本次归档后已完成 Phase 1 的第一小步：

- `backend/collector_ledger.py` 提供本地 SQLite 账本，表为 `collector_runs` 和 `collector_events`；
- `scripts/collector_service.py` 每轮运行前写入 `run_started`，每个结果写入 `collector_finished`，正常结束写入 `run_finished`，异常写入 `run_failed`；
- 服务锁使用跨平台的原子创建，记录 PID 和创建时间；同一报告根目录的并发服务会被拒绝，确认 PID 已不存在的遗留锁才会自动清理；
- 原有 `state.json`/`results.json` 输出保持兼容，账本是旁路运行层记录，不把进程退出码直接等同于来源内容质量。

本轮测试已覆盖账本事件、服务锁冲突/释放、runner 异常记录和中断运行恢复。事件读取 API、手动运行 POST 和失败项重试 POST 已提供；自动导入快照和本机 LaunchAgent 已接入，取消运行和日志/来源 API 仍按重构计划推进。

追加进展：`scripts/collect.py` 已增加进程级重试/指数退避和 staging 发布。默认常驻服务最多尝试 2 次，只有成功且有产物时才交换到 `data/collector-output/<id>/output`；失败、超时或空产物不覆盖上次发布目录。当前旧采集器仍有一部分直接写自身 `collectors/<id>/output`，因此 staging 保护的是“发布到运行数据根”的边界，采集器级输出目录迁移仍是后续工作。

再次追加：`/api/collector/ledger` 和 `/api/collector/runs/{run_id}/events` 已提供只读账本查询；监控详情面板会请求所选采集器的事件，历史兼容报告没有账本时会明确显示“账本事件未提供”。`run_one` 现在还使用 `.collector-locks/<collector-id>.lock` 防止同一采集器并发执行。

本轮继续：`POST /api/collector/runs` 会创建受服务锁和账本约束的后台手动运行，`POST /api/collector/runs/{run_id}/retry` 只提交原报告中的失败采集器；监控页的“立即执行”“补采”“重试”已调用这些接口。验证了未知采集器返回 422、代理 API 返回 200；没有通过接口启动真实网络全量采集。

本轮继续（自动导入门禁）：常驻服务在完整采集成功后调用 `backend/collector_import.py`，把 `snapshot_id`、`import_status` 和导入错误分别写入 `results.json`、`state.json` 与 SQLite 账本；部分成功默认不导入，只有显式允许才进入导入；导入失败不会改变采集状态，也不会覆盖已有快照。监控页轮询后台 job 时展示导入状态和快照 ID 前缀。新增测试覆盖成功导入、部分成功跳过、导入失败、旧账本列迁移及实际发布树导入。

本轮继续（跨平台后台配置）：新增 `scripts/collector_service_config.py`，可生成 macOS `LaunchAgent` plist 或 Windows `Task Scheduler` XML，包含登录启动、失败重启、单实例策略、工作目录和日志路径；命令默认不注册系统任务，只输出或写入指定路径，避免未经确认改变用户系统启动项。

本轮继续（显式控制）：新增 `scripts/collector_service_ctl.py`，提供 `install/status/uninstall` 和 `--dry-run`。本机已将 LaunchAgent 配置写入项目 `data/service-config/` 并加载成功，`launchctl status` 显示 `state = running`，program 指向 `/Users/yujunjun/opensource/GeoAgent/.venv/bin/python`；日志位于项目 `data/service-logs/`。

本轮继续（项目私有 Python）：新增 `scripts/bootstrap_env.py`，统一创建/安装项目 `.venv`；本机已验证 `.venv/bin/python` 的 `sys.prefix` 为项目 `.venv`，`pip check` 无依赖损坏。启动脚本和后台配置均不再依赖系统 Python 承担项目依赖。

安装与管理命令已单独归档到 [`2026-10-02-GeoAgent-本机采集服务安装与管理.md`](./2026-10-02-GeoAgent-本机采集服务安装与管理.md)，后续以该文件为准。

本轮继续（当前执行细节）：新增日志尾部接口和 UI 的 stdout/stderr 切换；日志只返回受限尾部，并在后端脱敏 token、cookie、authorization 等字段；新增来源状态只读 API 和监控详情的来源明细。真实首轮采集已完成并记录为 `partial`，不能把部分完成解释为新快照已导入。

## 四、交接命令

```bash
cd /Users/yujunjun/opensource/GeoAgent
.venv/bin/pytest -q
npm run build
.venv/bin/python -m scripts.start_local --dry-run
.venv/bin/python -m scripts.collector_service --once --id <collector-id>
.venv/bin/python -m scripts.collector_service --once --retry-attempts 2 --retry-backoff-sec 1
.venv/bin/python -m scripts.collector_service --once --auto-import --id <collector-id>
.venv/bin/python -m scripts.collector_service --once --auto-import --allow-partial-import --id <collector-id>
.venv/bin/python -m scripts.collector_service_config --platform macos --output data/service-config/com.geoagent.collector.plist
# Windows：在 Windows PowerShell 执行
python -m scripts.collector_service_config --platform windows --output data/service-config/GeoAgent-Collector.xml
.venv/bin/python -m scripts.collector_service_ctl status --platform macos --dry-run
.venv/bin/python -m scripts.collector_service_ctl status --platform macos
```

执行服务后，账本默认落在 `data/collection-runs/ledger.sqlite3`，锁文件为同目录下的 `.collector-service.lock`；测试或隔离运行可用 `--report-root`、`--ledger-path` 和 `--lock-path` 覆盖。不要把临时测试目录的账本误认为已经完成真实网络采集。

不要使用 `git reset --hard` 或覆盖其他未提交改动；当前工作区同时包含采集器修复、监控 UI、文档和本次启动/Phase 1 改动。
