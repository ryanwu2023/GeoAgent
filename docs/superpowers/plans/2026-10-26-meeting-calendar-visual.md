# Meeting Calendar Visual Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将会晤台账升级为可按月浏览、按日期联动并完整呈现事件/人物/地点/时间的双视图页面。

**Architecture:** 在纯函数模块中完成日期与月份网格计算，React 页面只管理月份、选中日期和详情状态。现有接口与数据模型保持不变，月历和列表共享同一份筛选结果。

**Tech Stack:** React 19、TypeScript、Lucide React、CSS、Node test

**Spec:** `docs/superpowers/specs/2026-10-26-meeting-calendar-visual-design.md`

## Global Constraints

- 不改变后端接口和既有会晤数据语义。
- 不引入新的前端依赖，不使用渐变。
- 保留当前抽屉中的来源追溯和关联证据入口。
- 日期按本地自然日计算，避免 UTC 转换造成日期偏移。

## Review Focus

- 月首为周日、跨年月份应正确生成六周网格。
- 跨日事件应出现在范围内每一天，异常长范围需受控。
- 日期待定事件不能从详情台账消失。
- 同日事件超过三条时仍可从详情列表看到全部。
- 窄屏不得把人物、地点或状态挤成逐字换行。

---

### Task 1: 日期与月历纯函数

**Files:**
- Modify: `src/meeting_calendar.ts`
- Test: `tests/meeting_calendar.test.cjs`

**Interfaces:**
- Produces: `buildMeetingMonth(month)`, `meetingOccursOn(meeting,date)`, `meetingDates(meeting)` 与中文月份标签。

- [x] 写月首偏移、跨月补位、日期范围和待定日期测试。
- [x] 运行单文件测试并确认失败。
- [x] 实现不依赖 UTC 字符串解析的日期工具。
- [x] 运行单文件测试并确认通过。

### Task 2: 月历与详情联动

**Files:**
- Modify: `src/MeetingCalendar.tsx`
- Test: `tests/meeting_calendar.test.cjs`

**Interfaces:**
- Consumes: Task 1 的月历函数。
- Produces: 月份导航、日期选择、事件定位以及完整日程卡片。

- [x] 添加结构测试，固定月历导航、核心字段和可访问标签。
- [x] 实现筛选结果共享、日期选择和卡片定位。
- [x] 确保日期待定记录保留在台账中。
- [x] 运行前端测试。

### Task 3: 视觉与响应式

**Files:**
- Modify: `src/style.css`

**Interfaces:**
- Consumes: Task 2 的 className 结构。
- Produces: 深色月历网格、状态色、详情卡片和窄屏横向滚动。

- [x] 实现桌面七列网格与状态视觉。
- [x] 实现窄屏月历滚动及卡片纵向布局。
- [x] 运行 `npm run build`。

### Task 4: 运行页面验收

**Files:**
- No source changes unless verification exposes a defect.

- [x] 重启本地服务并打开现有项目页面。
- [x] 检查月份导航、日期筛选、详情抽屉和来源链接。
- [x] 用窄视口确认无逐字换行与关键字段缺失。
- [x] 修复发现的问题并复跑测试与构建。
