# 翻译进度前端展示实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在数据源页面直接展示当前快照已翻译、待翻译、总数、完成率和后台进程状态。

**Architecture:** 复用 `/api/status` 已返回的 `translation.ready/pending/total/worker_running`，由独立 React 展示组件计算百分比并渲染，不新增后端查询或全量扫描。数据源抽屉和研究工作区状态栏共享同一投影函数。

**Tech Stack:** React、TypeScript、现有 CSS、Node 内置测试、Vite。

**Spec:** 用户于 2026-10-28 明确要求将翻译数字放在前端展示。

## Global Constraints

- 不新增状态接口或额外轮询。
- 数量必须绑定当前选中快照；快照不一致时显示“正在核对”。
- `total=0` 时完成率为 0，不出现 `NaN`。
- 后台进程存在但活动快照未知时，如实显示“进程运行中”，不得伪称当前快照正在翻译。
- 页面文案全部使用中文。

## Review Focus

- `total=0` 时进度条和百分比必须稳定；由 Task 1 测试约束。
- 历史快照与最新快照切换时不得沿用旧数字；由 Task 2 测试约束。
- `worker_running=true`、`running=false` 时不得显示“正在翻译当前快照”；由 Task 1 测试约束。
- pending 为 0 时显示已完成；由 Task 1 测试约束。
- 小屏幕不得因四个数字横向溢出；由 Task 2 CSS 和构建验证约束。

---

### Task 1: 翻译进度投影组件

**Files:**
- Create: `src/TranslationProgress.tsx`
- Create: `src/translation_progress.ts`
- Test: `tests/translation_progress.test.cjs`

**Interfaces:**
- Consumes: `TranslationStatus`。
- Produces: `translationProgress(status?: TranslationStatus) -> {ready:number;pending:number;total:number;percent:number;stateLabel:string}` 和 `TranslationProgress` 组件。

- [ ] **Step 1: 写失败测试**

覆盖 39958/367/40325 得到 99.09%、总数为零、已完成、工作进程运行但当前快照未知四种状态。

- [ ] **Step 2: 运行测试并确认失败**

Run: `node --test tests/translation_progress.test.cjs`

Expected: FAIL，模块尚不存在。

- [ ] **Step 3: 实现纯投影函数和展示组件**

组件展示已翻译、待翻译、总数、百分比、进度条和状态说明；对缺失数据使用零值和“正在核对”。

- [ ] **Step 4: 运行测试并确认通过**

Run: `node --test tests/translation_progress.test.cjs`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/TranslationProgress.tsx src/translation_progress.ts tests/translation_progress.test.cjs
git commit -m "feat: 增加翻译进度展示组件"
```

### Task 2: 接入数据源页面与响应式样式

**Files:**
- Modify: `src/App.tsx:1813-1817`
- Modify: `src/App.tsx:2063-2066`
- Modify: `src/style.css`
- Test: `tests/translation_progress.test.cjs`

**Interfaces:**
- Consumes: Task 1 的 `TranslationProgress` 和当前快照状态。
- Produces: 数据源抽屉的完整进度卡，以及研究工作区的紧凑进度文案。

- [ ] **Step 1: 添加接入失败测试**

断言数据源区域渲染 `TranslationProgress`，仅在 `status.translation.snapshot_id===sid` 时传入状态，并存在紧凑模式。

- [ ] **Step 2: 运行测试并确认失败**

Run: `node --test tests/translation_progress.test.cjs`

Expected: FAIL，App 尚未接入组件。

- [ ] **Step 3: 接入组件并增加样式**

完整卡展示在“后台模型与翻译状态”下；研究工作区使用紧凑文案。CSS 使用自适应网格，小屏幕自动换行。

- [ ] **Step 4: 运行前端测试与构建**

Run: `npm test`

Expected: 0 failed。

Run: `npm run build`

Expected: TypeScript 与 Vite 构建成功。

- [ ] **Step 5: Commit**

```bash
git add src/App.tsx src/style.css tests/translation_progress.test.cjs
git commit -m "feat: 在前端展示翻译完成与待处理数量"
```
