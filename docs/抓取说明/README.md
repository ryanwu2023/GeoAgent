# GeoAgent 抓取说明

这里是 GeoAgent 采集器的维护与运行入口。采集器源码仍在仓库根目录的 `collectors/`，本目录只负责说明、索引和运维记录，不复制、不移动采集器代码。

## 先读什么

| 文档 | 用途 |
|---|---|
| [代码目录与采集器清单](./代码目录与采集器清单.md) | 了解 19 个采集器、入口脚本、主题和输出类型 |
| [运行、输出与故障处理](./运行、输出与故障处理.md) | 本地启动、单个/按主题/全量运行、验证和常见失败处理 |
| [清洗、导入与展示数据链路](./清洗、导入与展示数据链路.md) | 了解原始输出如何进入 SQLite 快照，以及前端当前读取什么 |
| [加工管线逻辑分析](./加工管线逻辑分析.md) | 了解采集后的归档、规范化、适配器、去重、快照、展示和数据丢失边界 |
| [加工管线与业务分析审计](./加工管线与业务分析审计.md) | 核对文档与代码实际行为，并追踪快照如何进入 UI、问答、资产/情景分析、简报和人工复核 |
| [采集调度与技术审计](./采集调度与技术审计.md) | 核对定时任务、统一运行入口、真实采集技术、现有测试边界和 Browser fallback 取舍 |
| [新增采集器与变更检查清单](./新增采集器与变更检查清单.md) | 新增或修改采集器时的注册、协议、测试和提交要求 |

## 当前架构

```text
collectors/<collector>/
  ├─ <entrypoint>.py       采集器主程序
  ├─ config.json           信源、关键词、阈值等配置（并非所有项目都有）
  ├─ README.md              项目级口径和命令
  └─ output/                运行时产物；不提交 Git

scripts/collect.py          统一选择、执行、同步输出
scripts/refresh_crawlers.py 旧的批量刷新流程，使用前先看运行说明
backend/adapters.py          归一化、去重、指标和 geo.v6 导入
backend/paths.py             数据目录和 CRAWLER_ROOT 解析
data/collector-output/       统一运行输出；被 Git 忽略
data/research.sqlite3        导入后的不可变快照；被 Git 忽略
```

## 重要边界

- 仓库有采集器源码，不代表当前机器有最新抓取结果。
- `data/collector-output` 不存在或没有可识别产物时，应用回退到 `examples/demo-data`。
- 抓取失败、无数据、过滤为空和导入失败必须分别记录，不能用旧快照冒充本轮成功。
- 关键词分类、来源等级和地图地点都是研究候选或展示信息，不等同于事实核验、实时部署或因果判断。
- 原始记录、中文展示翻译和研究结论分开保存；清洗时不能为了去重丢失来源和冲突信息。

## 快速入口

```bash
cd /Users/yujunjun/opensource/GeoAgent

# 查看注册表中的采集器
.venv/bin/python scripts/collect.py --list

# 只跑一个采集器（会写入 data/collector-output）
.venv/bin/python scripts/collect.py --id xinhua-monitor

# 按主题选择
.venv/bin/python scripts/collect.py --topic ukraine

# 运行全部已启用采集器
.venv/bin/python scripts/collect.py --all

# 导入应用快照
curl -X POST http://127.0.0.1:8000/api/import
```

完整启动、环境变量和故障处理见 [运行、输出与故障处理](./运行、输出与故障处理.md)。
