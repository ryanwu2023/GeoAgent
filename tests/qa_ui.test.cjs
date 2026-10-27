const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/qa_ui.ts'), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS},
}).outputText;
const moduleValue = {exports: {}};
vm.runInNewContext(transpiled, {module: moduleValue, exports: moduleValue.exports, require}, {filename: 'qa_ui.js'});
const ui = moduleValue.exports;

test('QA run key changes with every immutable question scope field', () => {
  const base = ['snapshot-a', 'usiran', 'diplomacy', '2026-10-01', '2026-10-24', '最新进展'];
  const key = ui.qaRunStorageKey(...base);
  base.forEach((_, index) => {
    const changed = [...base];
    changed[index] += '-changed';
    assert.notEqual(ui.qaRunStorageKey(...changed), key);
  });
});

test('QA storage helpers do not prevent answering when browser storage is unavailable', () => {
  const denied = {
    getItem() { throw new Error('denied'); },
    setItem() { throw new Error('denied'); },
    removeItem() { throw new Error('denied'); },
  };
  assert.equal(ui.storedRunId(denied, 'run'), '');
  assert.doesNotThrow(() => ui.saveRunId(denied, 'run', 'qa-1'));
  assert.doesNotThrow(() => ui.clearRunId(denied, 'run'));
});

test('analysis progress keeps concurrent tool calls and replaces an updated step', () => {
  const first = {round: 2, call_id: 'search-a', title: 'A'};
  const second = {round: 3, call_id: 'search-b', title: 'B'};
  let trace = ui.mergeAnalysisTrace([], first);
  trace = ui.mergeAnalysisTrace(trace, second);
  assert.deepEqual(trace.map(item => item.call_id), ['search-a', 'search-b']);
  trace = ui.mergeAnalysisTrace(trace, {...first, title: 'A completed'});
  assert.equal(trace.length, 2);
  assert.equal(trace[0].title, 'A completed');
});

test('tool trace updates merge one lifecycle without losing the running timestamp', () => {
  const running = {round: 2, call_id: 'search-a', status: 'running', started_at: '2026-10-24T01:00:00Z', arguments_summary: {q: 'Iran'}};
  const completed = {round: 3, call_id: 'search-a', status: 'completed', finished_at: '2026-10-24T01:00:01Z', result_count: 8};
  const trace = ui.mergeAnalysisTrace(ui.mergeAnalysisTrace([], running), completed);
  assert.equal(trace.length, 1);
  assert.equal(trace[0].status, 'completed');
  assert.equal(trace[0].started_at, running.started_at);
  assert.equal(trace[0].result_count, 8);
});

test('tool labels and default expansion make failures visible without opening old successes', () => {
  assert.equal(ui.toolActionName('geo_evidence_search'), '搜索公开证据');
  assert.equal(ui.toolActionName('geo_internal_message_get'), '读取华泰消息正文');
  assert.equal(ui.shouldExpandToolCall({status: 'completed'}), false);
  assert.equal(ui.shouldExpandToolCall({status: 'running'}), true);
  assert.equal(ui.shouldExpandToolCall({status: 'degraded'}), true);
  assert.equal(ui.shouldExpandToolCall({status: 'failed'}), true);
});

test('aggregate tool receipts are described as summaries instead of zero results', () => {
  assert.equal(ui.toolResultLabel({tool:'geo_workspace_summary',status:'completed',result_count:0}), '范围摘要');
  assert.match(ui.toolResultSummary({tool:'geo_workspace_summary',result_summary:'返回 0 条结果'}), /范围摘要已读取/);
});

test('repeated evidence searches distinguish total matches from newly returned evidence', () => {
  const repeated = {tool:'geo_evidence_search',status:'completed',result_count:0,matched_total:18,matched_already_seen:18,returned_new:0,cache_reused:true};
  assert.equal(ui.toolResultLabel(repeated), '命中 18 · 新增 0');
  assert.match(ui.toolResultSummary({...repeated,result_summary:'返回 0 条结果'}), /命中 18 条/);
  assert.match(ui.toolResultSummary({...repeated,result_summary:'返回 0 条结果'}), /本轮新增 0 条/);
  assert.doesNotMatch(ui.toolResultSummary({...repeated,result_summary:'返回 0 条结果'}), /没有资料/);
});

test('research coverage keeps the newest state for each facet', () => {
  const coverage = ui.researchCoverage([
    {round:1,status:'completed',title:'first',coverage:[{facet:'military',label:'军事',status:'searched',reason:'已检索'}]},
    {round:2,status:'completed',title:'second',readiness:{ready:false,facets:[{facet:'military',label:'军事',status:'full_text_supported',reason:'已精读',evidence_count:2},{facet:'diplomacy',label:'外交',status:'gap_confirmed',reason:'缺少官方文本'}]}},
  ]);
  assert.equal(coverage.length, 2);
  assert.equal(coverage[0].status, 'full_text_supported');
  assert.equal(coverage[1].facet, 'diplomacy');
});

test('escaped markdown headings and emphasis are repaired before rendering', () => {
  const value = ui.normalizeAnswerMarkdown('\\## 一、结论\\n\\n\\*\\*局势仍在变化\\*\\*');
  assert.equal(value, '## 一、结论\\n\\n**局势仍在变化**');
});

test('tool calls are grouped by research phase in first-seen order', () => {
  const groups = ui.groupAnalysisTrace([
    {round: 1, phase: 'discovering', call_id: 'a', status: 'completed'},
    {round: 2, phase: 'reading', call_id: 'b', status: 'completed'},
    {round: 3, phase: 'discovering', call_id: 'c', status: 'failed'},
  ]);
  assert.equal(Array.from(groups, group => group.phase).join(','), 'discovering,reading');
  assert.equal(Array.from(groups[0].items, item => item.call_id).join(','), 'a,c');
});

test('multi-agent trace is grouped under coordinator with stable specialist order', () => {
  const groups = ui.groupAgentTrace([
    {round:1,sequence:1,kind:'agent',agent_id:'main',agent_role:'orchestrator',status:'running',title:'制定研究计划'},
    {round:2,sequence:2,kind:'tool',agent_id:'event',agent_role:'event',parent_agent_id:'main',phase:'discovering',call_id:'event-search',status:'completed'},
    {round:3,sequence:3,kind:'tool',agent_id:'market',agent_role:'market',parent_agent_id:'main',phase:'reading',call_id:'market-series',status:'failed'},
    {round:4,sequence:4,kind:'tool',agent_id:'event',agent_role:'event',parent_agent_id:'main',phase:'reading',call_id:'event-read',status:'completed'},
  ]);
  assert.equal(groups.main.agentId, 'main');
  assert.equal(groups.main.role, 'orchestrator');
  assert.equal(Array.from(groups.specialists, item => item.agentId).join(','), 'event,market');
  assert.equal(groups.specialists[0].toolCalls.length, 2);
  assert.equal(groups.specialists[1].status, 'failed');
});

test('legacy single-agent traces stay in the coordinator group', () => {
  const groups = ui.groupAgentTrace([
    {round:1,phase:'discovering',call_id:'search',status:'completed'},
    {round:2,stage:'synthesis',status:'completed',title:'总结'},
  ]);
  assert.equal(groups.main.agentId, 'main');
  assert.equal(groups.main.items.length, 2);
  assert.equal(groups.specialists.length, 0);
  assert.equal(groups.multiAgent, false);
});

test('agent lifecycle updates merge by task without removing its tool calls', () => {
  const running = {round:1,sequence:1,kind:'agent',task_id:'task-event',agent_id:'event',status:'running',title:'事件核验'};
  const tool = {round:2,sequence:2,kind:'tool',agent_id:'event',call_id:'search',status:'completed',title:'搜索'};
  const completed = {...running,round:3,sequence:3,status:'completed',duration_ms:1200};
  const trace = [running,tool,completed].reduce((current,item)=>ui.mergeAnalysisTrace(current,item),[]);
  assert.equal(trace.length, 2);
  assert.equal(trace.find(item=>item.kind==='agent').status, 'completed');
  assert.equal(trace.find(item=>item.call_id==='search').status, 'completed');
});

test('agent role labels are business-readable Chinese', () => {
  assert.equal(ui.agentRoleName('orchestrator'), '主协调 Agent');
  assert.equal(ui.agentRoleName('event'), '事件 Agent');
  assert.equal(ui.agentRoleName('market'), '市场 Agent');
  assert.equal(ui.agentRoleName('counterevidence'), '反证 Agent');
});

test('request generation prevents a late drawer response from replacing a newer call', () => {
  const guard = ui.createToolTraceRequestGuard();
  const first = guard.begin('run-a:call-a');
  const second = guard.begin('run-a:call-b');
  assert.equal(guard.isCurrent(first, 'run-a:call-a'), false);
  assert.equal(guard.isCurrent(second, 'run-a:call-b'), true);
  guard.close();
  assert.equal(guard.isCurrent(second, 'run-a:call-b'), false);
});

test('old archived answers receive empty collections required by current rendering', () => {
  const answer = ui.normalizeAnswer({id: 'old', message: '', query: 'q'});
  for (const key of ['facts', 'inferences', 'citations', 'gaps', 'steps', 'analysis_trace', 'internal_sources'])
    assert.ok(Array.isArray(answer[key]), key);
  assert.match(answer.message, /可用资料/);
});

test('normalizing a persisted answer coalesces running and completed events for one call', () => {
  const answer = ui.normalizeAnswer({id:'answer',message:'ok',analysis_trace:[
    {round:1,call_id:'call-a',status:'running',started_at:'start'},
    {round:2,call_id:'call-a',status:'completed',finished_at:'end'},
  ]});
  assert.equal(answer.analysis_trace.length, 1);
  assert.equal(answer.analysis_trace[0].started_at, 'start');
  assert.equal(answer.analysis_trace[0].finished_at, 'end');
});
