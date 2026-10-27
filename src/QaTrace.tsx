import {useEffect, useRef, useState} from "react";
import {AlertTriangle, CheckCircle2, ChevronRight, ExternalLink, LoaderCircle, Users, X} from "lucide-react";
import {getToolCallDetail} from "./api";
import {agentRoleName, createToolTraceRequestGuard, groupAgentTrace, groupAnalysisTrace, researchCoverage, shouldExpandToolCall, stageDisplayName, toolActionName, toolResultLabel, toolResultSummary} from "./qa_ui";
import type {AnalysisRound, ToolTraceDetail} from "./types";
import type {AgentTraceGroup} from "./qa_ui";

type EvidenceHandler = (id:string)=>void;

const statusText = (status:AnalysisRound["status"]) => ({scheduled:"等待执行",running:"运行中",completed:"已完成",degraded:"降级完成",failed:"失败"})[status];
const mcpName = (value?:string) => value === "geoagent" || !value ? "GeoAgent 地缘数据与分析" : value;
const elapsed = (milliseconds?:number) => milliseconds == null ? "" : milliseconds < 1000 ? `${milliseconds}ms` : `${(milliseconds/1000).toFixed(milliseconds < 10000 ? 1 : 0)}s`;
const jsonText = (value:unknown) => JSON.stringify(value ?? {}, null, 2);
const hasArguments = (value:unknown) => !!value && typeof value === "object" && Object.keys(value as Record<string,unknown>).length > 0;

function StatusIcon({status}:{status:AnalysisRound["status"]}) {
  if (status === "running" || status === "scheduled") return <LoaderCircle className="tool-call-spin" size={14}/>;
  if (status === "failed" || status === "degraded") return <AlertTriangle size={14}/>;
  return <CheckCircle2 size={14}/>;
}

function LegacyStep({item,onEvidence,openingEvidenceId}:{item:AnalysisRound;onEvidence:EvidenceHandler;openingEvidenceId:string}) {
  return <article className={`analysis-round ${item.status}`}>
    <header><b>{item.stage === "synthesis" ? "收敛" : stageDisplayName(item.stage)}</b><strong>{item.title}</strong><span>{statusText(item.status)}</span></header>
    <p>{item.summary}</p>
    {(item.details||[]).length > 0 && <ul>{item.details.map((detail,index)=><li key={`${item.round}-detail-${index}`}>{detail}</li>)}</ul>}
    {(item.search_queries||[]).length > 0 && <div className="analysis-searches"><label>内部补查</label>{item.search_queries.map(value=><code key={value}>{value}</code>)}</div>}
    {(item.evidence_ids||[]).length > 0 && <EvidenceButtons ids={item.evidence_ids} onEvidence={onEvidence} openingEvidenceId={openingEvidenceId}/>}
  </article>;
}

function EvidenceButtons({ids,onEvidence,openingEvidenceId}:{ids:string[];onEvidence:EvidenceHandler;openingEvidenceId:string}) {
  return <div className="citation-buttons">{ids.slice(0,6).map((id,index)=><button type="button" className="citation" key={id} aria-busy={openingEvidenceId===id} disabled={openingEvidenceId===id} onClick={()=>onEvidence(id)}>{openingEvidenceId===id?"正在读取…":`查看依据 ${index+1}`}</button>)}</div>;
}

function ToolCallCard({item,onFullDetail,onEvidence,openingEvidenceId}:{item:AnalysisRound;onFullDetail:(item:AnalysisRound)=>void;onEvidence:EvidenceHandler;openingEvidenceId:string}) {
  const [expanded,setExpanded] = useState(()=>shouldExpandToolCall(item));
  const userChanged = useRef(false);
  useEffect(()=>{
    if (!userChanged.current) setExpanded(shouldExpandToolCall(item));
    if (item.status === "failed" || item.status === "degraded") setExpanded(true);
  },[item.status]);
  const source = item.error?.source || `${item.mcp_server||"geoagent"}/${item.tool||"unknown"}`;
  return <article className={`tool-call-card ${item.status}`} data-call-id={item.call_id}>
    <button type="button" className="tool-call-heading" aria-expanded={expanded} onClick={()=>{userChanged.current=true;setExpanded(value=>!value)}}>
      <ChevronRight className="tool-call-chevron" size={14}/>
      <StatusIcon status={item.status}/>
      <span><strong>{statusText(item.status)} · {mcpName(item.mcp_server)}{toolActionName(item.tool)}</strong><small>{item.mcp_server||"geoagent"} · {item.tool||"未标识工具"}{item.duration_ms!=null?` · ${elapsed(item.duration_ms)}`:""}</small></span>
      <em>{toolResultLabel(item)}</em>
    </button>
    {expanded && <div className="tool-call-body">
      {toolResultSummary(item) && <p>{toolResultSummary(item)}</p>}
      <div className="tool-call-badges">
        {(item.attempt??1)>1 && <span>第 {item.attempt} 次尝试</span>}
        {!!item.retry_count && <span className="retry">已重试 {item.retry_count} 次</span>}
        {item.recovered && <span className="recovered">断点恢复</span>}
        {item.truncated && <span className="truncated">预览已截断</span>}
        {item.cache_reused && <span className="cache">复用缓存</span>}
        {item.matched_total!=null && <span>命中 {item.matched_total} 条</span>}
        {item.returned_new!=null && <span>本轮新增 {item.returned_new} 条</span>}
      </div>
      {item.error && <div className="tool-call-error" role="alert"><strong>{source} · {item.error.kind||"工具错误"}</strong><span>{item.error.message}</span>{item.error.preserved_results!=null&&<small>已保留 {item.error.preserved_results} 条结果继续参与回答</small>}</div>}
      <details className="tool-call-technical">
        <summary>技术详情</summary>
        <dl className="tool-call-metadata">
          <div><dt>MCP 服务</dt><dd>{item.mcp_server||"geoagent"}</dd></div>
          <div><dt>工具</dt><dd><code>{item.tool||"—"}</code></dd></div>
          <div><dt>调用 ID</dt><dd><code>{item.call_id}</code></dd></div>
          {item.agent_id&&<div><dt>执行 Agent</dt><dd>{item.agent_name||agentRoleName(item.agent_role)} · <code>{item.agent_id}</code></dd></div>}
          {item.started_at&&<div><dt>开始时间</dt><dd>{item.started_at}</dd></div>}
          {item.finished_at&&<div><dt>结束时间</dt><dd>{item.finished_at}</dd></div>}
        </dl>
        {hasArguments(item.arguments_summary)
          ? <pre className="tool-call-json">{jsonText(item.arguments_summary)}</pre>
          : <p className="tool-call-no-arguments">此工具直接读取当前看板范围，无需输入参数。</p>}
      </details>
      <div className="tool-call-actions">
        {!!item.call_id&&!!item.run_id&&<button type="button" onClick={()=>onFullDetail(item)}><ExternalLink size={12}/>完整详情</button>}
        {(item.evidence_ids||[]).length>0&&<EvidenceButtons ids={item.evidence_ids} onEvidence={onEvidence} openingEvidenceId={openingEvidenceId}/>} 
      </div>
    </div>}
  </article>;
}

function ToolCallDrawer({selection,onClose}:{selection:AnalysisRound|null;onClose:()=>void}) {
  const [detail,setDetail] = useState<ToolTraceDetail|null>(null);
  const [error,setError] = useState("");
  const guard = useRef(createToolTraceRequestGuard());
  useEffect(()=>{
    if (!selection?.run_id || !selection.call_id) { guard.current.close(); setDetail(null); setError(""); return; }
    const key = `${selection.run_id}:${selection.call_id}`;
    const generation = guard.current.begin(key);
    const controller = new AbortController();
    setDetail(null); setError("");
    getToolCallDetail(selection.run_id,selection.call_id,controller.signal).then(value=>{
      if (guard.current.isCurrent(generation,key)) setDetail(value);
    }).catch(reason=>{
      if (!controller.signal.aborted && guard.current.isCurrent(generation,key)) setError((reason as Error).message||"完整详情读取失败");
    });
    return ()=>controller.abort();
  },[selection?.run_id,selection?.call_id]);
  if (!selection) return null;
  const shown = detail || selection;
  const result = detail?.artifact ?? detail?.result;
  return <div className="tool-call-drawer-layer" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget)onClose()}}>
    <aside className="tool-call-drawer" role="dialog" aria-modal="true" aria-labelledby="tool-call-drawer-title">
      <header><div><span>工具调用详情</span><h3 id="tool-call-drawer-title">{toolActionName(selection.tool)}</h3></div><button type="button" aria-label="关闭工具详情" onClick={onClose}><X size={17}/></button></header>
      <div className="tool-call-drawer-content">
        <section><h4>归属</h4><dl className="tool-call-detail-grid"><div><dt>运行</dt><dd><code>{selection.run_id}</code></dd></div><div><dt>调用</dt><dd><code>{selection.call_id}</code></dd></div>{shown.agent_id&&<div><dt>执行 Agent</dt><dd>{shown.agent_name||agentRoleName(shown.agent_role)} · <code>{shown.agent_id}</code></dd></div>}<div><dt>MCP 服务</dt><dd>{shown.mcp_server||"geoagent"}</dd></div><div><dt>工具</dt><dd><code>{shown.tool}</code></dd></div></dl></section>
        {!detail&&!error&&<div className="tool-call-loading"><LoaderCircle className="tool-call-spin" size={16}/>正在读取持久化回执…</div>}
        {error&&<div className="tool-call-error" role="alert"><strong>完整详情读取失败</strong><span>{error}</span><small>卡片中的流式摘要仍可用于定位调用。</small></div>}
        {detail&&<>
          <section><h4>时间线</h4><dl className="tool-call-detail-grid"><div><dt>状态</dt><dd>{statusText(detail.status)}</dd></div><div><dt>尝试</dt><dd>{detail.attempt??1}</dd></div><div><dt>开始</dt><dd>{detail.started_at||"—"}</dd></div><div><dt>结束</dt><dd>{detail.finished_at||"—"}</dd></div><div><dt>耗时</dt><dd>{elapsed(detail.duration_ms)||"—"}</dd></div><div><dt>结果数</dt><dd>{detail.result_count??"—"}</dd></div></dl></section>
          <section><h4>参数（已脱敏）</h4>{hasArguments(detail.arguments_summary||detail.arguments)
            ? <pre className="tool-call-json">{jsonText(detail.arguments_summary||detail.arguments)}</pre>
            : <p className="tool-call-no-arguments">无需参数；范围来自当前工作快照和看板筛选。</p>}</section>
          {detail.error&&<section><h4>错误</h4><pre className="tool-call-json error">{jsonText(detail.error)}</pre></section>}
          <section><h4>完整回执（已脱敏）</h4>{detail.truncated&&<p className="tool-call-note">流式预览曾被截断；这里展示持久化详情。</p>}<pre className="tool-call-json result">{jsonText(result ?? {summary:detail.result_summary,evidence_ids:detail.evidence_ids})}</pre></section>
        </>}
      </div>
    </aside>
  </div>;
}

function AnalysisStageGroup({phase,items,defaultOpen,onFullDetail,onEvidence,openingEvidenceId}:{phase:string;items:AnalysisRound[];defaultOpen:boolean;onFullDetail:(item:AnalysisRound)=>void;onEvidence:EvidenceHandler;openingEvidenceId:string}) {
  const [expanded,setExpanded] = useState(defaultOpen);
  const manuallyChanged = useRef(false);
  const risky=items.some(item=>item.status==="running"||item.status==="scheduled"||item.status==="failed"||item.status==="degraded");
  useEffect(()=>{if(risky)setExpanded(true);else if(!manuallyChanged.current)setExpanded(defaultOpen)},[risky,defaultOpen]);
  const duration=items.reduce((sum,item)=>sum+(item.duration_ms||0),0);
  return <details className={`analysis-stage ${risky?"active":""}`} open={expanded} onToggle={event=>{
    const next=(event.currentTarget as HTMLDetailsElement).open;
    if(next!==expanded){manuallyChanged.current=true;setExpanded(next)}
  }}>
    <summary><span>{stageDisplayName(phase)}</span><small>{items.length} 步{duration?` · ${elapsed(duration)}`:""}</small></summary>
    <div>{items.map(item=>item.call_id?<ToolCallCard key={item.call_id} item={item} onFullDetail={onFullDetail} onEvidence={onEvidence} openingEvidenceId={openingEvidenceId}/>:<LegacyStep key={`${item.round}-${item.title}`} item={item} onEvidence={onEvidence} openingEvidenceId={openingEvidenceId}/>)}</div>
  </details>;
}

function AgentGroupCard({agent,live,onFullDetail,onEvidence,openingEvidenceId}:{agent:AgentTraceGroup;live:boolean;onFullDetail:(item:AnalysisRound)=>void;onEvidence:EvidenceHandler;openingEvidenceId:string}) {
  const risky=agent.status==="running"||agent.status==="scheduled"||agent.status==="failed"||agent.status==="degraded";
  const [expanded,setExpanded]=useState(risky||live);
  const manuallyChanged=useRef(false);
  useEffect(()=>{if(risky)setExpanded(true);else if(!manuallyChanged.current)setExpanded(live)},[risky,live]);
  const lifecycle=agent.lifecycle;
  const groups=groupAnalysisTrace(agent.items.filter(item=>item!==lifecycle));
  return <details className={`agent-trace-card ${agent.status}`} open={expanded} onToggle={event=>{
    const next=(event.currentTarget as HTMLDetailsElement).open;
    if(next!==expanded){manuallyChanged.current=true;setExpanded(next)}
  }}>
    <summary>
      <span className="agent-trace-icon"><StatusIcon status={agent.status}/></span>
      <span className="agent-trace-title"><strong>{agent.name||agentRoleName(agent.role)}</strong><small>{agent.taskSummary||lifecycle?.summary||"按主任务分工检索与复核"}</small></span>
      <span className="agent-trace-meta">{statusText(agent.status)}{agent.durationMs?` · ${elapsed(agent.durationMs)}`:""} · {agent.toolCalls.length} 次工具调用</span>
      <ChevronRight className="agent-trace-chevron" size={15}/>
    </summary>
    <div className="agent-trace-body">
      {(agent.status==="failed"||agent.status==="degraded")&&<div className="agent-degradation" role="status"><AlertTriangle size={14}/><span><strong>失败降级</strong>{lifecycle?.degraded_reason||lifecycle?.error?.message||"该子 Agent 未完整完成；主 Agent 将使用已保留结果和其他子 Agent 结果继续综合。"}</span></div>}
      {(lifecycle?.details||[]).length>0&&<ul className="agent-task-details">{lifecycle!.details.map((detail,index)=><li key={`${agent.agentId}-detail-${index}`}>{detail}</li>)}</ul>}
      <div className="agent-tool-call-list">
        {groups.length>0?groups.map((group,index)=><AnalysisStageGroup key={`${agent.agentId}-${group.phase}`} phase={group.phase} items={group.items} defaultOpen={risky||live&&index===groups.length-1} onFullDetail={onFullDetail} onEvidence={onEvidence} openingEvidenceId={openingEvidenceId}/>):<p className="agent-empty">等待子 Agent 调用研究工具。</p>}
      </div>
    </div>
  </details>;
}

export default function AnalysisProcess({rounds,planned,onEvidence,openingEvidenceId="",live=false}:{rounds:AnalysisRound[];planned:number;onEvidence:EvidenceHandler;openingEvidenceId?:string;live?:boolean}) {
  const [selected,setSelected] = useState<AnalysisRound|null>(null);
  const finished=rounds.filter(item=>item.stage!=="synthesis"&&item.status!=="running"&&item.status!=="scheduled").length;
  const synthesis=rounds.find(item=>item.stage==="synthesis");
  const harness=rounds.some(item=>!!item.kind||!!item.tool||["planning","discovering","reading","reviewing","synthesizing","validating"].includes(item.stage||""));
  const phases=new Set(rounds.filter(item=>item.status!=="running"&&item.stage&&!['completed','degraded','synthesis'].includes(item.stage)).map(item=>item.phase||item.stage)).size;
  const progress=harness?`已完成 ${finished} 步 · ${phases} 个阶段`:`${finished}/${planned} 轮`;
  const groups=groupAnalysisTrace(rounds);
  const agents=groupAgentTrace(rounds);
  const mainGroups=agents.multiAgent?groupAnalysisTrace(agents.main.items):[];
  const coverage=researchCoverage(rounds);
  return <>
    <details className="analysis-process" open>
      <summary><span>分析过程</span><small>可核验摘要 · {progress}{synthesis?` · 最终复核${synthesis.status==="running"?"中":"完成"}`:""}</small></summary>
      <p className="analysis-boundary">展示问题理解、检索范围、工具动作、回执摘要、复核问题和引用校验。这里是可审计的工作记录，不展示模型隐藏推理；工具结果和阶段草稿不等于最终事实。</p>
      {coverage.length>0&&<section className="research-coverage" aria-label="研究覆盖">
        <header><strong>研究覆盖</strong><small>{coverage.filter(item=>!["unstarted","searched"].includes(item.status)).length}/{coverage.length} 已形成结论或明确缺口</small></header>
        <div>{coverage.map(item=><article key={item.facet} className={`coverage-${item.status}`}><span>{item.label||item.facet}</span><b>{({unstarted:"未开始",searched:"已检索",full_text_supported:"正文支持",conflicted:"存在冲突",gap_confirmed:"明确缺口",budget_exhausted:"预算耗尽"} as Record<string,string>)[item.status]||item.status}</b>{item.reason&&<small>{item.reason}</small>}</article>)}</div>
      </section>}
      {agents.multiAgent?<div className="multi-agent-trace">
        <section className="main-agent-task">
          <header><span><Users size={14}/>主任务</span><strong>{agents.main.taskSummary||agents.main.lifecycle?.summary||"理解问题、分配研究任务并综合最终答案"}</strong><small>{statusText(agents.main.status)}{agents.main.durationMs?` · ${elapsed(agents.main.durationMs)}`:""}</small></header>
          {mainGroups.length>0&&<div className="main-agent-stages">{mainGroups.map((group,index)=><AnalysisStageGroup key={`main-${group.phase}`} phase={group.phase} items={group.items} defaultOpen={live&&index===mainGroups.length-1} onFullDetail={setSelected} onEvidence={onEvidence} openingEvidenceId={openingEvidenceId}/>)}</div>}
        </section>
        <section className="specialist-agents" aria-label="子 Agent 执行情况">
          <header><span>子 Agent</span><small>{agents.specialists.filter(agent=>agent.status==="completed").length}/{agents.specialists.length} 已完成</small></header>
          <div>{agents.specialists.map((agent,index)=><AgentGroupCard key={agent.agentId} agent={agent} live={live&&index===agents.specialists.length-1} onFullDetail={setSelected} onEvidence={onEvidence} openingEvidenceId={openingEvidenceId}/>)}</div>
        </section>
      </div>:<div className="analysis-stage-list">
        {groups.map((group,index)=><AnalysisStageGroup key={group.phase} phase={group.phase} items={group.items} defaultOpen={live&&index===groups.length-1} onFullDetail={setSelected} onEvidence={onEvidence} openingEvidenceId={openingEvidenceId}/>)}
      </div>}
    </details>
    <ToolCallDrawer selection={selected} onClose={()=>setSelected(null)}/>
  </>;
}
