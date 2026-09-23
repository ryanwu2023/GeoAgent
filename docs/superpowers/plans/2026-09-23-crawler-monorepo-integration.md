# Crawler Monorepo Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将两个 WorkBuddy 目录的 19 个采集器、研究资料和现有数据整合进当前地缘政治智能体，并使仓库可在不依赖原绝对路径的情况下运行和准备上传 GitHub。

**Architecture:** 采集器源码进入 `collectors/`，运行输出进入 Git 忽略的 `data/collector-output/`，应用通过统一路径模块和采集器注册表发现数据。TACO 使用独立适配器转换为现有指标对象；统一运行器负责选择采集器、隔离失败和输出机器可读报告。

**Tech Stack:** Python 3.11+、FastAPI、SQLite、pytest、React、TypeScript、PowerShell、JSON/JSONL。

**Spec:** `docs/superpowers/specs/2026-09-23-crawler-monorepo-integration.md`

## Global Constraints

- 原 WorkBuddy 目录只读，迁移不得修改或删除原文件。
- `.env`、密钥、令牌、本机代理、数据库、模型、日志、缓存和完整历史输出不得进入版本控制。
- 采集器默认路径必须从仓库位置计算，同时保留 `CRAWLER_ROOT` 环境变量覆盖能力。
- TACO 的霍尔木兹通行量保持 `observe` 角色，不得描述为五因子指数成分。
- 本次不替用户选择开源许可证。

## Review Focus

- 仓库路径包含中文和空格：路径解析与子进程参数必须使用 `Path` 和参数数组，测试在含空格临时目录运行。
- 单个采集器失败或超时：统一运行器继续处理其他项目并在汇总中保留返回码或 `timeout`。
- GitHub 克隆后没有历史输出：后端应回退到演示数据或返回明确的未接入状态，而不是引用 WorkBuddy。
- TACO 部分序列为空或日期不一致：适配器只导入有限数值并保留各序列自身日期。
- 配置或源码含个人绝对路径、密钥样式或超大文件：发布检查必须返回非零状态并列出相对路径，不输出密钥值。

---

### Task 1: Portable repository paths

**Files:**
- Create: `backend/paths.py`
- Modify: `backend/app.py`
- Modify: `backend/adapters.py`
- Modify: `scripts/refresh_crawlers.py`
- Modify: `scripts/supplement_market.py`
- Modify: `.env.example`
- Test: `tests/test_paths.py`

**Interfaces:**
- Produces: `project_root() -> Path`, `collector_source_root() -> Path`, `collector_data_root() -> Path`, `collector_read_root() -> Path`.
- Consumes: optional environment variables `CRAWLER_ROOT` and `COLLECTOR_DATA_ROOT`.

- [ ] **Step 1: Write the failing path tests**

```python
def test_default_paths_are_inside_repository(monkeypatch):
    monkeypatch.delenv("CRAWLER_ROOT", raising=False)
    monkeypatch.delenv("COLLECTOR_DATA_ROOT", raising=False)
    from backend.paths import collector_data_root, collector_source_root, project_root
    assert collector_source_root() == project_root() / "collectors"
    assert collector_data_root() == project_root() / "data" / "collector-output"

def test_crawler_root_override_is_preserved(monkeypatch, tmp_path):
    monkeypatch.setenv("CRAWLER_ROOT", str(tmp_path / "legacy data"))
    from backend.paths import collector_read_root
    assert collector_read_root() == tmp_path / "legacy data"
```

- [ ] **Step 2: Run the tests and verify import failure**

Run: `python -m pytest tests/test_paths.py -q`

Expected: FAIL because `backend.paths` does not exist.

- [ ] **Step 3: Implement the path module and replace absolute defaults**

```python
from pathlib import Path
import os

def project_root() -> Path:
    return Path(__file__).resolve().parents[1]

def collector_source_root() -> Path:
    return project_root() / "collectors"

def collector_data_root() -> Path:
    return Path(os.getenv("COLLECTOR_DATA_ROOT", project_root() / "data" / "collector-output"))

def collector_read_root() -> Path:
    return Path(os.getenv("CRAWLER_ROOT", collector_data_root()))
```

Update API status, snapshot import and scripts to call these functions; `.env.example` documents optional overrides without a personal path.

- [ ] **Step 4: Run focused and backend tests**

Run: `python -m pytest tests/test_paths.py tests/test_backend.py -q`

Expected: PASS.

### Task 2: Collector registry and safe migration

**Files:**
- Create: `collectors/collectors.json`
- Create: `scripts/integrate_collectors.py`
- Create: `tests/test_integrate_collectors.py`
- Create: `knowledge/README.md`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `load_registry(path: Path) -> list[dict]` and `copy_collector(source: Path, destination: Path, output_destination: Path) -> dict`.
- Produces registry fields: `id`, `name_zh`, `topics`, `entrypoint`, `selftest_args`, `output_kind`, `enabled`.

- [ ] **Step 1: Write migration exclusion tests**

```python
def test_copy_collector_separates_source_and_runtime_output(tmp_path):
    source = tmp_path / "source" / "sample-monitor"
    (source / "output").mkdir(parents=True)
    (source / "__pycache__").mkdir()
    (source / "monitor.py").write_text("print('ok')", encoding="utf-8")
    (source / "output" / "ALL-records.jsonl").write_text("{}\n", encoding="utf-8")
    (source / "__pycache__" / "monitor.pyc").write_bytes(b"cache")
    result = copy_collector(source, tmp_path / "collectors" / "sample-monitor", tmp_path / "data" / "sample-monitor")
    assert (tmp_path / "collectors/sample-monitor/monitor.py").exists()
    assert not (tmp_path / "collectors/sample-monitor/output/ALL-records.jsonl").exists()
    assert (tmp_path / "data/sample-monitor/output/ALL-records.jsonl").exists()
    assert not (tmp_path / "collectors/sample-monitor/__pycache__").exists()
    assert result["source_files"] == 1
```

- [ ] **Step 2: Run the test and verify failure**

Run: `python -m pytest tests/test_integrate_collectors.py -q`

Expected: FAIL because migration functions do not exist.

- [ ] **Step 3: Implement deterministic migration**

The migration script accepts two repeatable `--source-root` arguments, `--project-root`, and `--dry-run`. It copies source files while excluding `.workbuddy`, `.git`, `output`, `logs`, `__pycache__`, `.pytest_cache`, probe directories, `*.pyc`, `*.log`, and backup suffixes; it copies each real `output/` tree to `data/collector-output/<id>/output/`. Existing identical files are retained and changed files are overwritten only inside the current repository.

Create registry entries for all 19 projects and copy the five root research Markdown files into `knowledge/` with provenance recorded in `knowledge/README.md`.

- [ ] **Step 4: Expand Git ignores for runtime material**

Add rules for `collectors/*/output/`, `data/`, `.env`, `node_modules/`, `dist/`, model files, SQLite files, logs, probes and Python caches while retaining `examples/demo-data/`.

- [ ] **Step 5: Run migration tests and dry-run inventory**

Run: `python -m pytest tests/test_integrate_collectors.py -q`

Run: `python scripts/integrate_collectors.py --source-root "C:\Users\chongwu26001\WorkBuddy\2026-09-17-15-16-18" --source-root "C:\Users\chongwu26001\WorkBuddy\2026-09-21-16-22-20" --project-root . --dry-run`

Expected: PASS and a 19-project summary without filesystem changes.

### Task 3: Unified collector runner

**Files:**
- Create: `scripts/collect.py`
- Create: `tests/test_collect_runner.py`
- Modify: `start.ps1`
- Modify: `README.md`

**Interfaces:**
- Consumes: `collectors/collectors.json` and source/data roots from `backend.paths`.
- Produces: `run_collectors(ids: list[str], topic: str | None, timeout: int) -> list[dict]` and JSON reports in `data/collection-runs/<timestamp>/results.json`.

- [ ] **Step 1: Write runner isolation tests**

```python
def test_runner_continues_after_failure(fake_registry, monkeypatch):
    codes = iter([1, 0])
    monkeypatch.setattr("scripts.collect.run_one", lambda *args, **kwargs: {"returncode": next(codes)})
    results = run_collectors(["first", "second"], topic=None, timeout=10, registry=fake_registry)
    assert [item["returncode"] for item in results] == [1, 0]

def test_topic_filter_only_selects_matching_collectors(fake_registry):
    selected = select_collectors(fake_registry, ids=[], topic="ru_ua")
    assert all("ru_ua" in item["topics"] for item in selected)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/test_collect_runner.py -q`

Expected: FAIL because the runner module does not exist.

- [ ] **Step 3: Implement list, select, run and verify modes**

Use `subprocess.run([sys.executable, entrypoint, *args], cwd=collector_dir, timeout=timeout)` without shell interpolation. Pass `COLLECTOR_OUTPUT_ROOT` to compatible projects and sync legacy `output/` results into the runtime data root after successful runs. CLI flags: `--list`, `--id`, `--topic`, `--all`, `--verify-only`, `--timeout`.

- [ ] **Step 4: Run tests and list integrated collectors**

Run: `python -m pytest tests/test_collect_runner.py -q`

Run: `python scripts/collect.py --list`

Expected: PASS and exactly 19 collector rows.

### Task 4: TACO data adapter

**Files:**
- Create: `backend/taco_adapter.py`
- Modify: `backend/adapters.py`
- Create: `tests/fixtures/taco/taco-latest.csv`
- Create: `tests/fixtures/taco/taco-records.jsonl`
- Create: `tests/test_taco_adapter.py`

**Interfaces:**
- Produces: `load_taco(root: Path, records: dict, observations: list, sources: list) -> None`.
- Consumes: `taco-monitor/output/taco-latest.csv`, dated `taco-records-*.jsonl`, and `_source_status.json` when present.

- [ ] **Step 1: Write role and date-preservation tests**

```python
def test_taco_adapter_keeps_hormuz_as_observation_only(taco_root):
    records, observations, sources = {}, [], []
    load_taco(taco_root, records, observations, sources)
    taco = next(x for x in observations if x["id"] == "taco:TACO_INDEX")
    hormuz = next(x for x in observations if x["id"] == "taco:HORMUZ_TRANSIT")
    assert taco["method"].startswith("5 因子")
    assert "不进入 TACO" in hormuz["boundary"]
    assert hormuz["history"][-1]["date"] != ""

def test_taco_adapter_discards_non_finite_values(taco_root):
    _, observations, _ = load_fixture(taco_root, extra_value="NaN")
    assert all(math.isfinite(point["value"]) for item in observations for point in item["history"] if point["value"] is not None)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/test_taco_adapter.py -q`

Expected: FAIL because `backend.taco_adapter` does not exist.

- [ ] **Step 3: Implement TACO conversion**

Map five inputs and composite index to economic/market indicators. Map `HORMUZ_TRANSIT` to an observation indicator with its real observation date, source lag and `boundary="观察指标；不进入 TACO 五因子合成"`. Create source records for FRED, Silver Bulletin, Yahoo Finance and IMF PortWatch only when their identifiers occur in the TACO output.

- [ ] **Step 4: Register the adapter and run tests**

Run: `python -m pytest tests/test_taco_adapter.py tests/test_backend.py -q`

Expected: PASS.

### Task 5: Demo data and offline fallback

**Files:**
- Create: `scripts/build_demo_data.py`
- Create: `examples/demo-data/README.md`
- Create: `examples/demo-data/manifest.json`
- Modify: `backend/paths.py`
- Test: `tests/test_demo_fallback.py`

**Interfaces:**
- Produces: `build_demo(source_root: Path, destination: Path, max_records: int) -> dict`.
- Updates `collector_read_root()` to use demo data only when runtime data contains no recognizable collector output.

- [ ] **Step 1: Write clean-clone fallback test**

```python
def test_empty_runtime_root_uses_demo_data(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLECTOR_DATA_ROOT", str(tmp_path / "empty runtime"))
    monkeypatch.delenv("CRAWLER_ROOT", raising=False)
    assert collector_read_root().name == "demo-data"
```

- [ ] **Step 2: Run test and verify failure**

Run: `python -m pytest tests/test_demo_fallback.py -q`

Expected: FAIL because the current resolver returns the empty runtime root.

- [ ] **Step 3: Build a bounded demo package**

Select a deterministic, recent set covering both themes, TACO and at least one source/indicator per major page. Strip local absolute paths and raw page bodies; retain stable IDs, titles, summaries, published/fetched times, public URLs, units and source attribution. Write SHA-256 values and record counts to `manifest.json`.

- [ ] **Step 4: Run fallback and import tests**

Run: `python -m pytest tests/test_demo_fallback.py tests/test_backend.py -q`

Expected: PASS with the WorkBuddy directories temporarily unavailable through environment configuration.

### Task 6: GitHub publication audit

**Files:**
- Create: `scripts/audit_repository.py`
- Create: `tests/test_repository_audit.py`
- Modify: `README.md`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `audit_repository(root: Path, max_file_mb: int = 20) -> list[dict]` and CLI exit code 1 when findings exist.

- [ ] **Step 1: Write secret, path and large-file tests**

```python
def test_audit_reports_sensitive_material_without_value(tmp_path):
    (tmp_path / "bad.env").write_text("API_KEY=super-secret-value", encoding="utf-8")
    findings = audit_repository(tmp_path)
    assert findings[0]["path"] == "bad.env"
    assert "super-secret-value" not in json.dumps(findings)

def test_audit_reports_personal_absolute_path(tmp_path):
    (tmp_path / "config.py").write_text("ROOT = 'C:/Users/person/WorkBuddy/x'", encoding="utf-8")
    assert any(x["kind"] == "personal_path" for x in audit_repository(tmp_path))
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/test_repository_audit.py -q`

Expected: FAIL because the audit module does not exist.

- [ ] **Step 3: Implement tracked-content audit**

Scan the intended source set while excluding ignored runtime directories. Report relative path, rule and size only. Detect `.env`, common token assignments, private keys, `C:/Users/` paths, SQLite/model/archive extensions, and files larger than 20MB.

- [ ] **Step 4: Update repository documentation**

Document installation, unified collection commands, data directory, demo fallback, model configuration, Git initialization and GitHub upload preparation. State that no open-source license has been selected.

- [ ] **Step 5: Run audit tests**

Run: `python -m pytest tests/test_repository_audit.py -q`

Expected: PASS.

### Task 7: Execute migration and full acceptance

**Files:**
- Modify: files copied beneath `collectors/`, `knowledge/`, and local ignored `data/collector-output/`.
- Modify: `docs/integration-report-2026-09-23.md`

**Interfaces:**
- Consumes all previous tasks.
- Produces a migration report with collector count, copied source/output counts, excluded categories, test results and remaining publication decisions.

- [ ] **Step 1: Execute the approved migration**

Run:

```powershell
python scripts/integrate_collectors.py `
  --source-root "C:\Users\chongwu26001\WorkBuddy\2026-09-17-15-16-18" `
  --source-root "C:\Users\chongwu26001\WorkBuddy\2026-09-21-16-22-20" `
  --project-root .
```

Expected: 19 registered projects; source files under `collectors/`; runtime outputs under `data/collector-output/`; source roots unchanged.

- [ ] **Step 2: Remove migrated personal paths from runnable source**

Replace the fixed Python executable in TACO acceptance and silent-run scripts with `sys.executable` or the existing `RM_PY` convention. Confirm any remaining personal paths occur only in copied research provenance, not executable configuration.

- [ ] **Step 3: Run collector self-tests that do not require live network**

Run: `python scripts/collect.py --verify-only --all --timeout 180`

Expected: each project reports pass or `not_provided`; failures are recorded without aborting the report.

- [ ] **Step 4: Run full application checks**

Run: `python -m pytest -q`

Run: `npm.cmd run build`

Run: `python scripts/audit_repository.py`

Expected: tests and build pass; repository audit has zero release-blocking findings.

- [ ] **Step 5: Smoke-test the packaged application**

Run `start.ps1`, request `/api/health`, `/api/status`, the current snapshot endpoint and the main page, then run `stop.ps1`. Confirm status reports the repository-local data root and the UI can display both themes plus TACO indicators.

- [ ] **Step 6: Write the integration report**

Record exact collector inventory, local data size, demo snapshot size, validation commands/results, known upstream source failures, and the unresolved choice of public license. Do not claim external sources succeeded unless a live collection was executed in this task.

