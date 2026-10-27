# GeoAgent 会晤日历整合实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `meeting-calendar-monitor` 纳入 GeoAgent 统一采集和冻结快照，并提供可筛选、可溯源、可地图联动的未来会晤日历。

**Architecture:** 采集器作为原生 `meeting-calendar` 类型注册，保留自身事件和状态变化模型。新的后端适配器把会晤转换为快照内的 `meetings` 结构，同时生成标准证据记录；专用只读接口服务前端日历，问答和简报复用标准证据检索链路。

**Tech Stack:** Python 3、FastAPI、JSONL、React、TypeScript、MapLibre GL、pytest、Node test runner、Vite。

**Spec:** `docs/superpowers/specs/2026-10-25-meeting-calendar-integration-design.md`

## Global Constraints

- 默认展示当前日期起未来 120 天；不得为月份、当周、区间和待定日期伪造具体某天。
- 状态保留采集器原语义；T3 聚合源只作发现线索，不能自动抬升为已确认。
- 地图点必须标注“计划会晤地点，不代表实时位置”，并与军事行动图层分开。
- 所有接口显式使用 `snapshot_id`，不得跨快照回退到最新数据。
- PiChat、本地九个正式工具及已有新闻、指标和地图记录协议保持兼容。
- 采集失败保留旧快照和历史状态，不用空结果覆盖已有会晤。

## Review Focus

- 会晤只有月份、当周或待定日期时，排序可稳定但展示不得变成具体日期；由任务二的精度测试覆盖。
- 同一会晤同时存在官方否认与媒体预告时，接口须保留冲突和双方依据；由任务二和任务三的冲突测试覆盖。
- 非会晤政治日程基线不得显示成领导人会晤；由任务二的 `non_meeting` 测试覆盖。
- 地点是国家、地区或未定时不得生成精确地图点；由任务二和任务四的地图测试覆盖。
- 工作快照切换期间旧接口响应不得进入当前会晤页面；由任务四的异步范围测试覆盖。

---

### Task 1: 原生采集器与统一注册

**Files:**
- Create: `collectors/meeting-calendar-monitor/meeting_monitor.py`
- Create: `collectors/meeting-calendar-monitor/config.json`
- Create: `collectors/meeting-calendar-monitor/run.bat`
- Create: `collectors/meeting-calendar-monitor/tools/verify_report.py`
- Create: `collectors/meeting-calendar-monitor/README.md`
- Modify: `collectors/collectors.json`
- Modify: `scripts/integrate_collectors.py`
- Modify: `tests/collectors/test_registry.py`
- Modify: `tests/collectors/test_selftests.py`

**Interfaces:**
- Consumes: 原项目 `meeting-calendar-monitor` 的代码、配置和 `--selftest` 合同。
- Produces: 注册项 `id=meeting-calendar-monitor`、`output_kind=meeting-calendar`；运行后输出 `output/LATEST.jsonl` 和来源健康文件。

- [x] **Step 1: 增加失败的注册与自检测试**

断言注册表接受 `meeting-calendar`，入口位于采集器目录，`--selftest` 在隔离输出目录返回 0，并生成或验证会晤 JSONL 合同。

- [x] **Step 2: 运行采集器合同测试并确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/collectors/test_registry.py tests/collectors/test_selftests.py -q`

Expected: 因输出类型和采集器尚未注册而失败。

- [x] **Step 3: 迁移采集器并注册输出类型**

完整复制可执行源码和配置，不复制 `_trial`、历史 `output` 和运行时 `state`；保留源码生成这些目录的能力。在 `scripts/integrate_collectors.py` 的允许类型中加入 `meeting-calendar`。

- [x] **Step 4: 运行注册、自检和凭据扫描**

Run: `.venv\Scripts\python.exe -m pytest tests/collectors/test_registry.py tests/collectors/test_selftests.py -q`

Expected: PASS；采集器源码和配置不包含访问令牌、账号密码或本机绝对输出路径。

- [x] **Step 5: 提交任务一**

```bash
git add collectors/meeting-calendar-monitor collectors/collectors.json scripts/integrate_collectors.py tests/collectors
git commit -m "feat: integrate meeting calendar collector"
```

### Task 2: 会晤适配器与冻结快照

**Files:**
- Create: `backend/meeting_adapter.py`
- Modify: `backend/adapters.py`
- Modify: `backend/store.py`
- Create: `tests/test_meeting_adapter.py`
- Modify: `tests/test_backend.py`

**Interfaces:**
- Consumes: `load_meetings(root: Path, read: Callable, manifests: list, records: dict, sources: list) -> list[dict]` 读取 `meeting-calendar-monitor/output/LATEST.jsonl`。
- Produces: `snapshot["meetings"]: list[dict]` 和标准 `snapshot["records"]` 证据记录；会晤对象以 `evidence_id` 关联证据。

- [x] **Step 1: 编写适配器失败测试**

覆盖稳定 ID、主题映射、人物与职务、日期精度、状态中文名、来源等级、口径冲突、状态变化、`non_meeting`、地点未定和明确城市坐标。

- [x] **Step 2: 运行适配器测试并确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_meeting_adapter.py -q`

Expected: `backend.meeting_adapter` 不存在或快照缺少 `meetings`。

- [x] **Step 3: 实现 `load_meetings(...) -> list[dict]`**

解析 `LATEST.jsonl`，加载人物配置，保留 claims、sources、updates 和精度；只对地点字典中的明确城市或场馆生成 `precision=explicit/area` 的计划会晤点。

- [x] **Step 4: 在 `load_bundle` 中写入会晤与证据**

调用适配器并在返回对象增加 `meetings`。证据记录使用外交类目，绑定美伊或俄乌外交维度；多边条目可绑定两个主题；非会晤基线保留 `non_meeting=true`。

- [x] **Step 5: 验证精度、冲突和重复导入**

Run: `.venv\Scripts\python.exe -m pytest tests/test_meeting_adapter.py tests/test_backend.py -q`

Expected: 相同 `EV-*` 不重复；月份或当周精度不被改成某日；官方否认和媒体预告同时保留；无明确地点时 points 为空。

- [x] **Step 6: 提交任务二**

```bash
git add backend/meeting_adapter.py backend/adapters.py backend/store.py tests/test_meeting_adapter.py tests/test_backend.py
git commit -m "feat: add meeting calendar snapshot adapter"
```

### Task 3: 会晤日历只读接口

**Files:**
- Modify: `backend/app.py`
- Create: `tests/test_meeting_api.py`

**Interfaces:**
- Consumes: `snapshot["meetings"]` 和每条对象的 `evidence_id`。
- Produces: `GET /api/meetings`、`GET /api/meetings/{event_id}`。

- [x] **Step 1: 编写列表和详情接口失败测试**

测试 topic、track、status、person、日期、`include_inactive`、分页、状态统计和详情来源；测试未知快照、未知事件和跨快照事件返回 404。

- [x] **Step 2: 运行接口测试并确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_meeting_api.py -q`

Expected: 路由返回 404。

- [x] **Step 3: 实现 `meeting_list` 与 `meeting_detail`**

列表返回 `{snapshot_id, as_of, total, status_counts, records, basis}`；默认日期窗口为快照日期起 120 天，默认排除 inactive 状态。详情返回 claims、sources、updates、地图点和 `evidence_id`。

- [x] **Step 4: 验证过滤、范围和错误边界**

Run: `.venv\Scripts\python.exe -m pytest tests/test_meeting_api.py tests/test_qa_tool_trace_api.py -q`

Expected: 全部通过，且不会影响现有审计接口。

- [x] **Step 5: 提交任务三**

```bash
git add backend/app.py tests/test_meeting_api.py
git commit -m "feat: expose snapshot meeting calendar api"
```

### Task 4: 会晤日历页面和地图图层

**Files:**
- Create: `src/MeetingCalendar.tsx`
- Create: `src/meeting_calendar.ts`
- Modify: `src/api.ts`
- Modify: `src/types.ts`
- Modify: `src/App.tsx`
- Modify: `src/MapView.tsx`
- Modify: `src/style.css`
- Create: `tests/meeting_calendar.test.cjs`
- Modify: `tests/research_layout.test.cjs`

**Interfaces:**
- Consumes: `MeetingListResponse` 与 `MeetingDetail`，通过 `apiMeetings(params, signal)` 和 `apiMeetingDetail(snapshotId, eventId, signal)` 获取。
- Produces: 独立“会晤日历”菜单、摘要、筛选、时间线、详情抽屉和 `MeetingMapRecord[]` 计划会晤图层。

- [x] **Step 1: 编写状态展示和异步范围失败测试**

测试状态顺序、日期精度文案、inactive 分区、冲突标记、非会晤标签、地图可见性及旧快照响应不能覆盖当前范围。

- [x] **Step 2: 运行前端测试并确认失败**

Run: `node --test tests/meeting_calendar.test.cjs tests/research_layout.test.cjs`

Expected: 组件、菜单和辅助函数不存在。

- [x] **Step 3: 实现类型、API 和纯函数**

在 `src/meeting_calendar.ts` 实现 `meetingDateLabel`、`meetingStatusMeta`、`meetingIsMapVisible` 和按日期精度排序；类型字段与任务三接口一致。

- [x] **Step 4: 实现 `MeetingCalendar` 页面**

加入 7/30/120 天摘要、主题/人物/状态/日期筛选、active 与 inactive 分区、详情抽屉、来源外链及关联证据按钮。

- [x] **Step 5: 增加地图计划会晤图层**

仅传递具有受控坐标且状态可见的会晤；使用独立图标、图例和开关，详情固定显示“计划会晤地点，不代表实时位置”。

- [x] **Step 6: 验证前端测试和构建**

Run: `npm test`

Run: `npm run build`

Expected: 全部测试通过，TypeScript 和 Vite 构建成功。

- [x] **Step 7: 提交任务四**

```bash
git add src/MeetingCalendar.tsx src/meeting_calendar.ts src/api.ts src/types.ts src/App.tsx src/MapView.tsx src/style.css tests/meeting_calendar.test.cjs tests/research_layout.test.cjs
git commit -m "feat: add meeting calendar workspace"
```

### Task 5: 问答、简报与真实管线验收

**Files:**
- Modify: `backend/research.py`
- Modify: `backend/geo_mcp/server.py`
- Modify: `tests/test_geo_mcp_server.py`
- Modify: `tests/test_answer_report.py`
- Create: `docs/qa/meeting-calendar-acceptance.md`

**Interfaces:**
- Consumes: 任务二生成的标准证据记录和任务三的会晤对象。
- Produces: 问答可检索会晤记录；简报“未来外交日程”；现有 `geo_evidence_search/get` 返回会晤证据，不新增破坏兼容性的正式工具。

- [x] **Step 1: 编写问答与简报失败测试**

断言“未来一周有哪些会晤”能检索会晤证据；报告区分已确认、已安排和媒体报道；取消/过期条目不会进入默认未来日程；引用可打开。

- [x] **Step 2: 运行研究链路测试并确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_geo_mcp_server.py tests/test_answer_report.py -q`

Expected: 缺少未来外交日程或会晤检索内容。

- [x] **Step 3: 接入问答和简报上下文**

将会晤证据纳入既有检索；简报根据快照日期选择近期 active 会晤，最多展示 8 条，附状态、日期精度、地点和证据 ID。

- [x] **Step 4: 运行采集器真实抓取和自动导入**

通过统一管线只运行 `meeting-calendar-monitor`，核对来源健康、`LATEST.jsonl`、快照 `meetings`、证据记录、工作快照和翻译触发状态。

- [x] **Step 5: 完成浏览器视觉与交互验收**

检查美伊、俄乌、多边筛选；打开至少一条会晤详情和来源；验证计划会晤地图点、无地点记录、取消/过期分区、窄屏布局和加载错误状态。

- [x] **Step 6: 运行全量回归**

Run: `.venv\Scripts\python.exe -m pytest -q`

Run: `npm test`

Run: `npm run build`

Expected: 全部通过；服务重启后 `/api/status`、`/api/meetings`、问答、报告和证据详情正常。

- [x] **Step 7: 记录验收结果并提交任务五**

```bash
git add backend/research.py backend/geo_mcp/server.py tests/test_geo_mcp_server.py tests/test_answer_report.py docs/qa/meeting-calendar-acceptance.md
git commit -m "feat: connect meeting calendar to research workflow"
```
