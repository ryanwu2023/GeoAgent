# 采集器测试

该目录验证 `collectors/collectors.json` 注册的采集器：

- `test_registry.py`：注册表字段、入口文件、README、路径边界和 Python 语法。
- `test_selftests.py`：逐个启动注册表中的 `selftest_args`，使用临时输出目录，不执行在线抓取。

运行全部采集器测试：

```bash
.venv/bin/pytest -q tests/collectors
```

运行统一入口的离线验证：

```bash
.venv/bin/python scripts/collect.py --all --verify-only --timeout 240
```

在线抓取不属于该测试目录；需要单独确认网络、代理、上游接口和真实输出落盘。
