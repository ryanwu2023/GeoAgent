# 会晤日历采集范围扩展实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让会晤采集器在过去 21 天至未来 120 天范围内，稳定采集重点人物及职务白名单人物的会晤、参会、出访、电话、视频通话和状态更新。

**Architecture:** 保留单采集器和现有输出协议，在配置层声明职务与事件词典，在解析层完成动态人物识别和双门禁准入，在事件层沿用稳定指纹、状态合并和现有导入链路。固定人物优先，自动发现人物仅作为可审计补充。

**Tech Stack:** Python 3、JSON 配置、现有 GeoAgent 采集与快照适配器、内置离线自检。

**Spec:** `docs/superpowers/specs/2026-10-28-meeting-calendar-coverage-design.md`

## Global Constraints

- 时间窗口固定为历史 21 天、未来 120 天。
- 固定人物优先；自动人物仅限国家元首、政府首脑、外长、防长、国家安全负责人及正式特使。
- 必须同时命中合格人物和外交活动动作才能准入。
- 无明确日期不得猜测，无中文译名不得臆造。
- 取消、推迟和否认作为状态更新保留，不删除原始证据。
- 输出继续兼容现有导入、日历、地图和 Excel 导出。

## Review Focus

- 职务词出现在评论或历史背景中时不得误建人物或日程；由 Task 2 的负例测试约束。
- “愿意通话”不得当成已安排电话；由 Task 2 的情态词测试约束。
- 同姓或无姓名的部长表述不得错误合并；由 Task 1 的稳定 ID 与缺名回退测试约束。
- 跨年未来日期和历史边界不得被错误过滤；由 Task 3 的窗口边界测试约束。
- 取消、推迟、否认与原计划必须合并为状态更新；由 Task 3 的生命周期测试约束。

---

### Task 1: 配置人物职务与事件类型

**Files:**
- Modify: `collectors/meeting-calendar-monitor/config.json`
- Modify: `collectors/meeting-calendar-monitor/meeting_monitor.py:1782-1808`
- Test: `collectors/meeting-calendar-monitor/meeting_monitor.py:1928-2220`

**Interfaces:**
- Consumes: 现有 `persons`、`families`、`status_lexicon` 配置。
- Produces: `role_whitelist`、扩展后的事件家族配置，以及可供解析层使用的角色词典。

- [ ] **Step 1: 在 `selftest()` 增加配置失败测试**

断言职务白名单包含国家元首、政府首脑、外长、防长、安全负责人和正式特使；事件词典包含会晤、参会、出访、电话和视频通话。

- [ ] **Step 2: 运行离线自检并确认失败**

Run: `.venv\Scripts\python.exe collectors\meeting-calendar-monitor\meeting_monitor.py --selftest`

Expected: FAIL，提示缺少职务或事件配置。

- [ ] **Step 3: 扩展 `config.json` 并在 `bind_globals(cfg: dict) -> None` 绑定配置**

配置英文及常用中文职务别名、事件动作、排除情态表达和事件类型中文标签；保持 `horizon_days=120`、`retention_days=21`。

- [ ] **Step 4: 运行离线自检并确认通过新增配置测试**

Run: `.venv\Scripts\python.exe collectors\meeting-calendar-monitor\meeting_monitor.py --selftest`

Expected: 新增配置断言 PASS。

- [ ] **Step 5: Commit**

```bash
git add collectors/meeting-calendar-monitor/config.json collectors/meeting-calendar-monitor/meeting_monitor.py
git commit -m "feat: 扩展会晤人物职务与外交活动配置"
```

### Task 2: 动态人物识别与外交活动双门禁

**Files:**
- Modify: `collectors/meeting-calendar-monitor/meeting_monitor.py:584-707`
- Modify: `collectors/meeting-calendar-monitor/meeting_monitor.py:921-1070`
- Test: `collectors/meeting-calendar-monitor/meeting_monitor.py:1928-2220`

**Interfaces:**
- Consumes: Task 1 的角色词典和事件类型配置。
- Produces: `extract_dynamic_persons(text: str) -> list[dict]`、`detect_event_type(text: str) -> str | None`，并为信号写入 `event_type`、`person_details` 和 `discovery_method`。

- [ ] **Step 1: 添加动态人物和准入负例测试**

覆盖未配置外长/防长/安全顾问/特使自动准入、普通议员与发言人排除、国内活动排除、“愿意通话”排除、已安排电话与正式访问准入。

- [ ] **Step 2: 运行离线自检并确认失败**

Run: `.venv\Scripts\python.exe collectors\meeting-calendar-monitor\meeting_monitor.py --selftest`

Expected: FAIL，动态人物函数或事件类型尚不存在。

- [ ] **Step 3: 实现 `extract_dynamic_persons` 和 `detect_event_type`**

从姓名与白名单职务的相邻文本构造稳定人物 ID；固定名单命中时不重复建人。无姓名职务只作参与方线索，不创建人物。事件动作按已发生、已安排、拟议三类语境解析。

- [ ] **Step 4: 将双门禁接入 `build_signals(items: list[dict], cfg: dict)`**

要求人物准入和外交动作同时成立；写入审计字段，并保持现有人物、主体、日期、地点及来源逻辑兼容。

- [ ] **Step 5: 运行离线自检**

Run: `.venv\Scripts\python.exe collectors\meeting-calendar-monitor\meeting_monitor.py --selftest`

Expected: 全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add collectors/meeting-calendar-monitor/meeting_monitor.py
git commit -m "feat: 增加会晤动态人物与事件准入"
```

### Task 3: 时间窗口、去重和状态生命周期

**Files:**
- Modify: `collectors/meeting-calendar-monitor/meeting_monitor.py:1194-1457`
- Test: `collectors/meeting-calendar-monitor/meeting_monitor.py:1928-2220`
- Test: `tests/test_meeting_adapter.py`

**Interfaces:**
- Consumes: Task 2 产生的结构化信号。
- Produces: 兼容现有适配器的事件记录，包含新增事件类型和动态人物详情。

- [ ] **Step 1: 添加窗口、合并与生命周期测试**

断言过去 21 天和未来 120 天边界、跨年日期、同事件多来源合并、取消/推迟/否认更新原事件且保留来源。

- [ ] **Step 2: 运行目标测试并确认失败**

Run: `.venv\Scripts\python.exe collectors\meeting-calendar-monitor\meeting_monitor.py --selftest`

Expected: 至少一个新增边界或状态测试 FAIL。

- [ ] **Step 3: 调整事件指纹、匹配和生命周期函数**

将 `event_type` 纳入匹配但允许状态更新与原事件合并；按事件日期执行窗口校验，日期待定正式预告保留，历史背景排除。

- [ ] **Step 4: 验证适配器兼容性**

Run: `.venv\Scripts\python.exe -m pytest tests/test_meeting_adapter.py tests/test_meeting_collector_sync.py -q`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add collectors/meeting-calendar-monitor/meeting_monitor.py tests/test_meeting_adapter.py
git commit -m "feat: 完善外交日程窗口与状态合并"
```

### Task 4: 文档与完整回归

**Files:**
- Modify: `collectors/meeting-calendar-monitor/README.md`
- Test: `tests/test_meeting_api.py`
- Test: `tests/test_meeting_export.py`
- Test: `tests/meeting_calendar.test.cjs`

**Interfaces:**
- Consumes: Tasks 1-3 的最终输出协议。
- Produces: 中文采集规则说明及通过验证的现有页面/API/Excel 兼容性。

- [ ] **Step 1: 更新中文 README**

写明时间窗口、人物范围、事件类型、来源等级、排除规则、输出字段和离线自检命令。

- [ ] **Step 2: 运行会晤后端测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_meeting_adapter.py tests/test_meeting_collector_sync.py tests/test_meeting_api.py tests/test_meeting_export.py -q`

Expected: PASS。

- [ ] **Step 3: 运行前端测试与构建**

Run: `npm test`

Expected: 0 failed。

Run: `npm run build`

Expected: TypeScript 与 Vite 构建成功。

- [ ] **Step 4: Commit**

```bash
git add collectors/meeting-calendar-monitor/README.md
git commit -m "docs: 更新外交日程采集说明"
```
