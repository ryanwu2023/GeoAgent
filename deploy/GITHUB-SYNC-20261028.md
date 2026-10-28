# GitHub / GitLab 代码整合（2026-10-28）

## 基线与历史

- GitLab `origin/main`：`3921ac5`。
- GitHub `main`：`4da4020`，已经包含 GitLab 上述提交，并另外包含 19 个提交。
- 历史记录显示，yujunjun 采用保留双方历史的合并方式；2026-10-27 的 `f32f9d3` 合并 GitHub 更新至 GitLab 生产线，随后由 `codex/github-production-sync` 合入 GitLab 主分支。没有必要强推或覆盖另一边历史。
- 本次在 `codex/github-postgres-login-sync` 中先快进到 GitHub 基线，再恢复本地 PostgreSQL、登录、MCP 同步、采集修复及部署文档改动。保留 GitHub 19 个提交，无冲突标记。

## 保留的 GitHub 新功能

- 单采集器重跑。
- 本地问答 Agent 超时、并发、费用上限配置及费用回执。
- 取消任务后释放预算预留。
- 子 Agent 输出/证据参数、指标前值、证据映射和数字校验兼容修复。
- 问答 HTML 报告按问题生成业务核心判断及长度限制。

这些提交仍以 SQLite 版代码为基础。本次整合后，问答存储、历史回答及采集账本等统一使用 PostgreSQL；新增 Agent 预算配置继续使用原有 JSON 配置文件，没有新增 SQLite 业务数据库。新 Agent 设置页补接网页登录失效处理。

## 本地迁移交付

- `scripts/migrate_sqlite_to_postgres.py`：检查、备份后导入、独立复核；明确停写确认，拒绝非空目标；原始 SQLite 不覆盖。
- `deploy/LOCAL-SQLITE-TO-POSTGRES.md`：Windows/Linux/macOS 操作步骤、回退边界和验收清单。
- `deploy/compose.postgresql-local.yaml`：可选 PostgreSQL 17.11 本地实例，只监听 127.0.0.1:55432。
- JSON 配置、附件、归档等随整个 data 目录备份；项目根 .env 和采集器源码目录下的原始 output 需另行保留。

迁移演练使用 GitHub `4da4020` 的真实旧版初始化代码创建三套 SQLite 数据库，24 张业务表各放入测试行。新脚本迁入独立 PostgreSQL schema，逐表行数/内容复核通过；已有会晤 JSON、问答费用信息、Agent 设置文件均保留。演练仅使用测试数据，结束后删除隔离目标 schema。

## 验证及发布边界

前端 134 项测试通过，TypeScript/Vite 构建通过。迁移专项 16 项通过。完整后端 803 项通过、2 项跳过。

本次是仓库整合与本地迁移交付，没有为合并后的版本重新构建和发布 Sealos 镜像；线上仍为此前已验证的登录/采集修复镜像。GitLab 生产发布需按其独立流程处理，不能把 GitHub 推送成功等同于线上发布成功。
