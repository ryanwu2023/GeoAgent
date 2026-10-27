# Answer Report Reference Style Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将本地模型问答的 HTML 导出改造成用户提供的深色金色研究报告样式，同时保持内容忠实、安全和接口兼容。

**Architecture:** 继续由 `backend.answer_report.render_answer_report` 进行确定性服务端渲染。增加小型内容投影函数，从已有回答中提取报告导语和安全元数据；完整 Markdown、引用台账与缺口仍使用原数据，不再次调用模型。

**Tech Stack:** Python、MarkdownIt、FastAPI、pytest、原生 HTML/CSS

**Spec:** `docs/superpowers/specs/2026-10-26-answer-report-reference-style-design.md`

## Global Constraints

- 导出过程不得再次调用模型，也不得虚构指标、图表或结论。
- 只允许 HTTP(S) 外部链接；Markdown 内嵌 HTML 必须保持禁用。
- 保持现有下载接口、文件名规则与 409/404 行为兼容。
- 使用参考报告的深色、金色、单栏视觉，支持移动端和打印。
- 不泄漏内部回答 ID、快照 ID或证据记录 ID。

## Review Focus

- 总结正文以不同中文标题开头时，导语提取失败也不能删除或重复完整正文。
- 超长标题、长 URL 和宽表格必须换行或滚动，不能撑破页面。
- 无引用、无缺口或缺少时间字段时必须显示明确空状态。
- 恶意 HTML 和 `javascript:` 链接不得进入可执行输出。
- 打印模式必须转为可读的浅色纸面，而不是输出深底耗墨页面。

---

### Task 1: 报告内容投影与安全回归

**Files:**
- Modify: `backend/answer_report.py`
- Test: `tests/test_answer_report.py`

**Interfaces:**
- Consumes: `render_answer_report(answer: dict, generated_at: str | None = None) -> str`
- Produces: `_lead_from_markdown(value: object) -> str` 和保持兼容的 HTML 报告输出

- [x] **Step 1: 写失败测试**
  - 断言导出包含参考样式变量 `--bg:#0b1118`、金色强调和单栏容器。
  - 断言首段结论进入核心判断区，完整正文仍保留。
  - 断言四张信息卡、证据表和限制区存在。
  - 断言打印样式、无引用空状态、长内容布局类和危险内容转义存在。

- [x] **Step 2: 运行测试确认失败**
  - Run: `python -m pytest tests/test_answer_report.py -q`
  - Expected: 新增样式和结构断言失败。

- [x] **Step 3: 实现内容投影和参考样式模板**
  - 在 `backend/answer_report.py` 增加 `_lead_from_markdown(value: object) -> str`，只提取首个有效正文段作为导语，不修改 `summary_html`。
  - 将浅色侧栏模板替换为深色单栏模板；加入顶部元数据、导语、四张信息卡、完整研判、证据台账、限制区和打印样式。
  - 保留 `_safe_url`、HTML 转义、Markdown HTML 禁用和现有异常语义。

- [x] **Step 4: 运行测试确认通过**
  - Run: `python -m pytest tests/test_answer_report.py -q`
  - Expected: PASS。

### Task 2: 导出接口与视觉产物验收

**Files:**
- Modify: `tests/test_answer_report.py`
- Verify: `backend/app.py`

**Interfaces:**
- Consumes: `GET /api/answers/{answer_id}/report.html`
- Produces: 可下载、可打印的 UTF-8 HTML 文件

- [x] **Step 1: 补充接口兼容测试**
  - 断言响应仍为 `text/html`、带 UTF-8 下载文件名，且不包含回答、快照和证据内部 ID。
  - 断言原始来源链接、发布时间与采集时间仍可见。

- [x] **Step 2: 运行后端相关测试**
  - Run: `python -m pytest tests/test_answer_report.py tests/test_backend.py -q`
  - Expected: PASS。

- [x] **Step 3: 生成代表性导出文件并静态审查**
  - 使用测试回答调用 `render_answer_report` 生成临时 HTML。
  - 检查 HTML 标题、章节顺序、表格、空状态、媒体查询和打印媒体查询。

- [x] **Step 4: 运行项目构建与差异检查**
  - Run: `npm test -- --runInBand`（若项目脚本不支持该参数则使用项目现有 Node 测试命令）
  - Run: `npm run build`
  - Run: `git diff --check -- backend/answer_report.py tests/test_answer_report.py`
  - Expected: 全部通过且无空白错误。

