# Meeting Calendar Excel Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为关键人物会晤日历增加按当前筛选结果导出专业 Excel 台账的能力。

**Architecture:** 后端提取可复用的会晤筛选函数，并由独立 `meeting_export` 模块在内存中生成 XLSX；FastAPI 返回附件响应。前端只传递当前筛选状态并下载二进制文件，不自行重建数据。

**Tech Stack:** Python、FastAPI、openpyxl、React、TypeScript、pytest、Node test

**Spec:** `docs/superpowers/specs/2026-10-26-meeting-calendar-excel-export-design.md`

## Global Constraints

- 导出必须使用当前冻结快照和与页面一致的主题、状态、人物、日期及非活动事项筛选。
- 工作簿包含“会晤台账”和“导出说明”，不写入内部快照 ID。
- Excel 不包含公式；防止公式注入，仅允许 HTTP(S) 超链接。
- 当前筛选无数据时返回409，不生成空文件。
- 保持原有会晤列表、详情、月历和地图接口兼容。

## Review Focus

- 选中日期时，跨多日事项也应包含在导出结果中。
- 人物筛选按 ID 或中文名匹配，导出人物显示中文名（职务）。
- 取消/推迟/否认/过期/已举行事项只在勾选后导出。
- 恶意单元格前缀和非 HTTP(S) 链接不得成为公式或可点击链接。
- 多来源、长备注、空日期和未知人物不能破坏工作簿。

---

### Task 1: 可复用筛选与工作簿生成

**Files:**
- Create: `backend/meeting_export.py`
- Modify: `backend/app.py`
- Modify: `requirements.txt`
- Test: `tests/test_meeting_export.py`

**Interfaces:**
- Produces: `filter_meetings(snapshot, *, topic, track, status, person, start, end, include_inactive) -> tuple[list[dict], str, str]`
- Produces: `render_meeting_workbook(rows: list[dict], metadata: dict) -> bytes`

- [x] 写失败测试，覆盖筛选一致性、两张工作表、列顺序、人物职务、自动筛选、冻结窗格、超链接、公式注入和说明页不含快照 ID。
- [x] 运行 `python -m pytest tests/test_meeting_export.py -q`，确认失败。
- [x] 添加固定版本的 `openpyxl` 依赖并实现筛选与内存工作簿。
- [x] 再次运行测试，确认通过。

### Task 2: Excel 下载接口

**Files:**
- Modify: `backend/app.py`
- Test: `tests/test_meeting_api.py`

**Interfaces:**
- Consumes: Task 1 的筛选及工作簿函数
- Produces: `GET /api/meetings/export.xlsx`

- [x] 写失败测试，覆盖当前筛选、文件名、MIME、409空结果和不泄漏快照 ID。
- [x] 在动态详情路由之前注册导出路由，并让列表接口复用同一筛选函数。
- [x] 运行 `python -m pytest tests/test_meeting_api.py tests/test_meeting_export.py -q`，确认通过。

### Task 3: 前端导出操作

**Files:**
- Modify: `src/api.ts`
- Modify: `src/MeetingCalendar.tsx`
- Modify: `src/style.css`
- Test: `tests/meeting_calendar.test.cjs`

**Interfaces:**
- Produces: `apiMeetingExport(params) -> Promise<{blob: Blob; filename: string}>`
- Consumes: 当前 `calendarTopic`、`status`、`person`、`includeInactive`、`selectedDate`

- [x] 写失败测试，断言导出按钮、当前筛选参数、无结果禁用及下载清理逻辑存在。
- [x] 实现二进制下载 API 与“导出 Excel”按钮；选中日期时传同一天作为 start/end。
- [x] 增加导出中和失败状态样式，保持现有页面视觉。
- [x] 运行 `npm test`，确认通过。

### Task 4: 完整验收

**Files:**
- Verify: `backend/meeting_export.py`
- Verify: `backend/app.py`
- Verify: `src/MeetingCalendar.tsx`

**Interfaces:**
- Consumes: 完整导出链路
- Produces: 可由 Excel 打开的 `.xlsx` 下载

- [x] 运行相关 Python 测试和完整 Node 测试。
- [x] 运行 `npm run build`。
- [x] 通过真实本地接口下载当前筛选工作簿，并用 openpyxl 重新读取，核对工作表、行数、列数、筛选与超链接。
- [x] 重启服务并验证下载响应为200及正确 MIME。

