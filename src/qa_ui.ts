import type { AnalysisRound, Answer, ResearchFacetCoverage } from "./types";

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export function qaRunStorageKey(
  snapshotId: string,
  topic: string,
  dimension: string,
  start: string,
  end: string,
  question: string,
) {
  return `geoagent:qa-run:${snapshotId}:${topic}:${dimension}:${start}:${end}:${question}`;
}

export function storedRunId(storage: StorageLike, key: string) {
  try { return storage.getItem(key) || ""; } catch { return ""; }
}

export function saveRunId(storage: StorageLike, key: string, runId: string) {
  try { storage.setItem(key, runId); } catch { /* A run can continue even when storage is unavailable. */ }
}

export function clearRunId(storage: StorageLike, key: string) {
  try { storage.removeItem(key); } catch { /* Private/storage-limited browsers must remain usable. */ }
}

export function mergeAnalysisTrace(current: AnalysisRound[], item: AnalysisRound) {
  const identity = (value: AnalysisRound) => value.call_id
    ? `call:${value.call_id}`
    : value.kind === "agent" && value.task_id
      ? `agent-task:${value.task_id}`
    : `step:${value.round}`;
  const existing = current.find(value => identity(value) === identity(item));
  const merged = existing ? {...existing, ...item} : item;
  const next = current.filter(value => identity(value) !== identity(item));
  next.push(merged);
  return next.sort((left, right) => (left.sequence ?? left.round) - (right.sequence ?? right.round));
}

const AGENT_ROLES: Record<string,string> = {
  orchestrator: "主协调 Agent",
  event: "事件 Agent",
  market: "市场 Agent",
  counterevidence: "反证 Agent",
};

export function agentRoleName(role?:string) {
  return AGENT_ROLES[role || ""] || role || "研究 Agent";
}

export type AgentTraceGroup = {
  agentId:string;
  role:string;
  name:string;
  parentAgentId?:string;
  taskId?:string;
  taskSummary?:string;
  status:AnalysisRound["status"];
  durationMs:number;
  items:AnalysisRound[];
  toolCalls:AnalysisRound[];
  lifecycle?:AnalysisRound;
};

const STATUS_WEIGHT: Record<AnalysisRound["status"],number> = {completed:0,scheduled:1,running:2,degraded:3,failed:4};

function summarizeAgentStatus(items:AnalysisRound[], lifecycle?:AnalysisRound):AnalysisRound["status"] {
  if (lifecycle?.status === "failed" || lifecycle?.status === "degraded") return lifecycle.status;
  const observed = items.reduce<AnalysisRound["status"]>((status,item)=>STATUS_WEIGHT[item.status]>STATUS_WEIGHT[status]?item.status:status,lifecycle?.status||"completed");
  if (lifecycle?.status === "completed") return observed === "failed" ? "degraded" : "completed";
  return observed;
}

/** Builds the coordinator/specialist hierarchy while treating old traces as one coordinator. */
export function groupAgentTrace(rounds:AnalysisRound[]) {
  const hasAgentMetadata = rounds.some(item=>!!item.agent_id||!!item.agent_role||item.kind==="agent");
  const map = new Map<string,AgentTraceGroup>();
  const ensure = (item:AnalysisRound) => {
    const agentId = item.agent_id || "main";
    let group = map.get(agentId);
    if (!group) {
      const role = item.agent_role || (agentId === "main" ? "orchestrator" : "specialist");
      group = {agentId,role,name:item.agent_name||agentRoleName(role),parentAgentId:item.parent_agent_id,taskId:item.task_id,taskSummary:item.task_summary,status:"completed",durationMs:0,items:[],toolCalls:[]};
      map.set(agentId,group);
    }
    return group;
  };
  for (const item of rounds) {
    const group = ensure(item);
    group.items.push(item);
    if (item.call_id || item.kind === "tool") group.toolCalls.push(item);
    if (item.kind === "agent") {
      group.lifecycle = item;
      group.taskId = item.task_id || group.taskId;
      group.taskSummary = item.task_summary || item.summary || group.taskSummary;
      group.parentAgentId = item.parent_agent_id || group.parentAgentId;
      group.role = item.agent_role || group.role;
      group.name = item.agent_name || agentRoleName(group.role);
    }
  }
  if (!map.has("main")) {
    const firstOrchestrator = Array.from(map.values()).find(group=>group.role==="orchestrator" || !group.parentAgentId);
    if (!firstOrchestrator) map.set("main",{agentId:"main",role:"orchestrator",name:agentRoleName("orchestrator"),status:"completed",durationMs:0,items:[],toolCalls:[]});
  }
  for (const group of map.values()) {
    group.status = summarizeAgentStatus(group.items,group.lifecycle);
    group.durationMs = group.lifecycle?.duration_ms ?? group.toolCalls.reduce((total,item)=>total+(item.duration_ms||0),0);
  }
  const all = Array.from(map.values());
  const main = all.find(group=>group.role==="orchestrator") || map.get("main") || all[0];
  const specialists = all.filter(group=>group!==main);
  return {main,specialists,multiAgent:hasAgentMetadata && specialists.length>0};
}

const TOOL_ACTIONS: Record<string,string> = {
  geo_workspace_summary: "读取看板范围",
  geo_evidence_search: "搜索公开证据",
  geo_evidence_get: "读取证据全文",
  geo_metric_catalog: "查询量化指标目录",
  geo_metric_series: "读取指标序列",
  geo_analysis_view: "读取情景与资产分析",
  geo_internal_message_stats: "读取华泰消息概况",
  geo_internal_message_search: "搜索华泰内部消息",
  geo_internal_message_get: "读取华泰消息正文",
};

export function toolActionName(tool?: string) {
  return TOOL_ACTIONS[tool || ""] || tool || "处理研究资料";
}

export function toolResultLabel(item:Pick<AnalysisRound,"tool"|"status"|"result_count"|"matched_total"|"returned_new">) {
  if (item.status === "running" || item.status === "scheduled") return "运行中";
  if (item.status === "failed") return "失败";
  if (item.status === "degraded") return "降级完成";
  if (item.tool === "geo_workspace_summary") return "范围摘要";
  if (item.tool === "geo_internal_message_stats") return "统计摘要";
  if (item.tool === "geo_analysis_view") return "分析框架";
  if (item.tool === "geo_evidence_search" && item.matched_total != null) return `命中 ${item.matched_total} · 新增 ${item.returned_new??item.result_count??0}`;
  const count=item.result_count??0;
  const unit=({geo_evidence_search:"条证据",geo_evidence_get:"条全文",geo_metric_catalog:"项指标",geo_metric_series:"个序列",geo_internal_message_search:"条消息",geo_internal_message_get:"条正文"} as Record<string,string>)[item.tool||""]||"条结果";
  return count?`${count} ${unit}`:"未命中";
}

export function toolResultSummary(item:Pick<AnalysisRound,"tool"|"result_summary"|"summary"|"matched_total"|"matched_already_seen"|"returned_new"|"cache_reused">) {
  const staleZero = /^返回\s*0\s*条结果$/.test(item.result_summary||"");
  if (item.tool === "geo_evidence_search" && item.matched_total != null && (staleZero || !item.result_summary)) {
    const seen = item.matched_already_seen??Math.max(0,item.matched_total-(item.returned_new??0));
    return `实际命中 ${item.matched_total} 条，其中已在证据池 ${seen} 条，本轮新增 ${item.returned_new??0} 条${item.cache_reused?"；本次复用缓存。":"。"}`;
  }
  if (!staleZero) return item.result_summary||item.summary||"";
  if (item.tool === "geo_workspace_summary") return "范围摘要已读取；记录数、来源数与指标数请在完整回执中核对。";
  if (item.tool === "geo_internal_message_stats") return "华泰消息库统计摘要已读取。";
  if (item.tool === "geo_analysis_view") return "资产与情景分析框架已读取。";
  return item.result_summary||item.summary||"";
}

/** Merge coverage receipts in trace order; later readiness checks win. */
export function researchCoverage(rounds:AnalysisRound[]):ResearchFacetCoverage[] {
  const facets = new Map<string,ResearchFacetCoverage>();
  for (const item of rounds) {
    const values = [...(item.coverage||[]),...(item.readiness?.facets||[])];
    for (const value of values) if (value?.facet) facets.set(value.facet,value);
  }
  return Array.from(facets.values());
}

/** Repair Markdown punctuation that compatible models sometimes escape wholesale. */
export function normalizeAnswerMarkdown(value:string) {
  return String(value||"").replace(/(^|\n)\\(#{1,6}\s)/g,"$1$2").replace(/\\([*_|`>\[\]])/g,"$1");
}

export function stageDisplayName(value?: string) {
  return ({planning:"问题规划",discovering:"宽检索",reading:"全文读取",reviewing:"复核补查",synthesizing:"答案综合",validating:"引用校验",completed:"已完成",degraded:"降级完成",analysis:"分析",synthesis:"最终结论"} as Record<string,string>)[value || ""] || value || "分析";
}

export function shouldExpandToolCall(item: Pick<AnalysisRound,"status">) {
  return item.status === "running" || item.status === "scheduled" || item.status === "degraded" || item.status === "failed";
}

export function groupAnalysisTrace(rounds: AnalysisRound[]) {
  const groups: {phase:string;items:AnalysisRound[]}[] = [];
  const byPhase = new Map<string,{phase:string;items:AnalysisRound[]}>();
  for (const item of rounds) {
    const phase = item.phase || item.stage || "analysis";
    let group = byPhase.get(phase);
    if (!group) {
      group = {phase,items:[]};
      byPhase.set(phase,group);
      groups.push(group);
    }
    group.items.push(item);
  }
  return groups;
}

/** Monotonic guard used by the details drawer to ignore stale fetches. */
export function createToolTraceRequestGuard() {
  let generation = 0;
  let activeKey = "";
  return {
    begin(key:string) { activeKey = key; generation += 1; return generation; },
    close() { activeKey = ""; generation += 1; },
    isCurrent(candidate:number,key:string) { return generation === candidate && activeKey === key; },
  };
}

/** Keep archived answers from older schemas readable in the current UI. */
export function normalizeAnswer(answer: Answer): Answer {
  const trace = Array.isArray(answer.analysis_trace)
    ? answer.analysis_trace.reduce<AnalysisRound[]>((current,item)=>mergeAnalysisTrace(current,item),[])
    : [];
  return {
    ...answer,
    message: answer.message || "已返回当前快照中的可用资料。",
    facts: Array.isArray(answer.facts) ? answer.facts : [],
    inferences: Array.isArray(answer.inferences) ? answer.inferences : [],
    citations: Array.isArray(answer.citations) ? answer.citations : [],
    gaps: Array.isArray(answer.gaps) ? answer.gaps : [],
    steps: Array.isArray(answer.steps) ? answer.steps : [],
    analysis_trace: trace,
    internal_sources: Array.isArray(answer.internal_sources) ? answer.internal_sources : [],
  };
}
