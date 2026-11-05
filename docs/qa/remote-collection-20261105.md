# 远端采集与快照镜像测试报告（2026-11-05）

- 功能：`remote-collection`，设计 `docs/superpowers/specs/2026-11-05-远端采集与快照镜像设计.md`
- 代码基线：`af00ec4` 加本功能未提交改动（工作区）；提交后需以提交 SHA 重新确认
- 环境：macOS 本机，Python 3.12 `.venv`，PostgreSQL 17（临时容器 `geoagent-test-pg`，127.0.0.1:55470），Node 前端构建
- 结论：**已实现，自动化与本机端到端验证通过；尚未部署，RC-23（HFT 连通性）未执行，暂不具备上线条件**

## 执行记录

| 命令 | 结果 |
|---|---|
| `pytest tests/test_mcp_mirror.py tests/test_remote_collect_mcp.py tests/test_remote_collection.py tests/test_mcp_sync.py tests/test_collector_monitor.py tests/test_integration_settings.py tests/test_geo_mcp_server.py` | 75 passed, 1 skipped |
| `pytest`（全量，修正 3 个过期测试后） | 1048 passed, 2 skipped |
| `npm test` | 137 passed |
| `npx tsc --noEmit` / `npm run build` | 通过 |
| 本机双实例端到端（`xinhua-monitor`） | 通过，见下 |

首轮全量有 3 个失败，撤下本功能后在基线代码上同样失败，原因是测试未随早先提交更新：`1cc2cb1` 给 `answer()` 增加 `research_scope` 参数，`test_backend.py` 两个流式问答测试的假 `answer()` 未接收该参数；`f74d947` 在恢复运行时补充空 `claim_ledger`，`test_qa_harness_store.py` 预期值未包含。已只修改测试，业务代码未改，修正后全量通过。

## 用例结果

| ID | 结果 | 证据 |
|---|---|---|
| RC-01 | 通过 | 默认 local 模式下全量回归通过 |
| RC-02 ~ RC-06 | 通过 | `tests/test_remote_collect_mcp.py` |
| RC-07 ~ RC-08 | 通过 | `tests/test_mcp_mirror.py` |
| RC-09 | 通过 | `tests/test_mcp_sync.py` 8 passed，geo-sync/v1 不变 |
| RC-10 ~ RC-21 | 通过 | `tests/test_remote_collection.py`（真实 MCP HTTP 封装，两侧独立 schema） |
| RC-22 | 通过 | 见下 |
| RC-23 | 未执行 | 需在 172.16.91.120 上执行连通性 curl |

## RC-22 端到端

脚本：两个真实 `scripts.serve` 进程，`18801` 为采集端（local 模式，管理页 Key 加入白名单），`18802` 为接收端（remote 模式），各自独立 schema、数据目录、采集产物目录。

- 接收端汇总 `collection_mode=remote`；采集端尚无快照时“立即同步”返回 `unavailable`；
- 接收端提交 `xinhua-monitor` → 202；3 秒内页面接口显示 `xinhua-monitor: running`，16 秒后 `success`；
- 远端批次 `success / succeeded / eca3db0a15ac9593d3ac4fbc`，接收端任务 `success / succeeded / eca3db0a15ac9593d3ac4fbc`，接收端工作快照 `eca3db0a15ac9593d3ac4fbc`，两侧一致；
- 镜像事件 7 条：run_started、collector_started、collector_log、collector_finished、import_started、import_finished、run_finished；
- 再次同步 `unchanged`；30 分钟内再次触发 429“约 30 分钟后可再次触发”；门禁接口 409。

## 上线前未完成项

1. 提交并以提交 SHA 重新执行必要测试；
2. RC-23：HFT 到 `geoagent.lhotsetech.com:443` 连通性；
3. Sealos 发布新镜像，创建 HFT 专用 Key 并设置 `GEO_REMOTE_COLLECT_PRINCIPALS`；HFT 部署新镜像并写入远端配置；发布后在两侧做一次真实采集验收。
