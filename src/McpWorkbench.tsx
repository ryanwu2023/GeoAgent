import {useEffect, useRef, useState} from "react";

type Schema = {type?:string; description?:string; default?:unknown; enum?:unknown[]; properties?:Record<string,Schema>; required?:string[]};
type Tool = {name:string; description?:string; inputSchema:Schema; outputSchema?:unknown; annotations?:Record<string,unknown>};
type Call = {created_at:string; tool:string; status:string; duration_ms:number; audit_event_id:string};
type Catalog = {tools:Tool[]; instructions:string; calls:Call[]; statistics_basis:string; model_view_basis:string};
const json = (value:unknown)=>JSON.stringify(value,null,2);
const tabs = ["模型视角","参数字段","试调用 / 返回","调用实况"];

export default function McpWorkbench({onClose,context}:{onClose:()=>void;context:{snapshot_id:string;topic:string;dimension:string;start:string;end:string}}) {
  const [catalog,setCatalog]=useState<Catalog|null>(null);
  const [selected,setSelected]=useState("");
  const [query,setQuery]=useState("");
  const [tab,setTab]=useState(tabs[0]);
  const [args,setArgs]=useState("{}");
  const [result,setResult]=useState("");
  const [businessResult,setBusinessResult]=useState("");
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const abort=useRef<AbortController|null>(null);
  const tool=catalog?.tools.find(item=>item.name===selected);
  async function load(signal?:AbortSignal) {
    const response=await fetch("/api/integrations/mcp-workbench",{cache:"no-store",signal});
    const data=await response.json();
    if(!response.ok)throw new Error(data.detail||"工作台加载失败");
    setCatalog(data); setSelected(current=>current||data.tools[0]?.name||"");
  }
  useEffect(()=>{const controller=new AbortController(); void load(controller.signal).catch(e=>{if(e.name!=="AbortError")setError(e.message)});return()=>{controller.abort();abort.current?.abort()}},[]);
  async function reload(){setError("");try{await load()}catch(e){setError(e instanceof Error?e.message:"加载失败")}}
  function choose(item:Tool){abort.current?.abort();setBusy(false);setSelected(item.name);setResult("");setBusinessResult("");setError("");const properties=item.inputSchema.properties||{};setArgs(json({...Object.fromEntries(Object.entries(properties).filter(([,s])=>s.default!==undefined).map(([k,s])=>[k,s.default])),...Object.fromEntries(Object.entries(context).filter(([key])=>key in properties))}))}
  async function run(){
    setError("");setResult("");setBusinessResult("");let argumentsValue:unknown;
    try{argumentsValue=JSON.parse(args);if(!argumentsValue||Array.isArray(argumentsValue)||typeof argumentsValue!=="object")throw new Error("参数必须是 JSON 对象")}catch(e){setError(e instanceof Error?e.message:"JSON无效");return}
    setBusy(true);const controller=new AbortController();abort.current=controller;
    try{
      const response=await fetch("/api/integrations/mcp-workbench/call",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({tool:selected,arguments:argumentsValue}),signal:controller.signal});
      const data=await response.json();if(controller.signal.aborted)return;setResult(json(data));
      setBusinessResult((data.result?.content||[]).filter((c:{type:string})=>c.type==="text").map((c:{text:string})=>{try{return json(JSON.parse(c.text))}catch{return c.text}}).join("\n\n"));
      if(!response.ok)setError(`HTTP ${response.status}：试调用失败，详见返回`);
      else if(data.error||data.result?.isError)setError("MCP 返回工具错误，详见返回");
      else {const content=data.result?.content;try{if(content?.some((c:{text?:string})=>c.text&&JSON.parse(c.text).ok===false))setError("业务返回失败，详见返回")}catch{/* Preserve the raw protocol result. */}}
      await load();
    }catch(e){if(e instanceof Error&&e.name!=="AbortError")setError(e.message)}finally{if(abort.current===controller)setBusy(false)}
  }
  const calls=catalog?.calls.filter(call=>call.tool===selected)||[];
  const durations=calls.map(call=>call.duration_ms).sort((a,b)=>a-b);
  const p95=durations.length?durations[Math.ceil(durations.length*.95)-1]:null;
  return <div className="integration-overlay"><section className="mcp-workbench" role="dialog" aria-modal="true" aria-label="MCP 工作台">
    <header><div><h2>MCP 工作台</h2><p>只读业务工具 · {window.location.origin}/mcp</p></div><button onClick={onClose} aria-label="关闭 MCP 工作台">×</button></header>
    <div className="mcp-access"><button onClick={()=>void reload()}>刷新工具与记录</button></div>
    {error?<p className="mcp-error" role="alert">{error}</p>:null}
    <div className="mcp-workbench-grid"><aside className="mcp-tool-list"><input aria-label="搜索 MCP 工具" placeholder="按工具名称 / 业务说明搜索" value={query} onChange={e=>setQuery(e.target.value)}/><small>{catalog?.tools.length||0} 个工具 · 描述字数 / 调用数</small>
      {catalog?.tools.filter(item=>(item.name+item.description).toLowerCase().includes(query.toLowerCase())).map(item=><button key={item.name} className={selected===item.name?"selected":""} disabled={busy} onClick={()=>choose(item)}><strong>{item.name}</strong><span>{item.description}</span><small>{item.description?.length||0} 字 · {catalog.calls.filter(c=>c.tool===item.name).length} 次</small></button>)}
      {catalog&&!catalog.tools.some(item=>(item.name+item.description).toLowerCase().includes(query.toLowerCase()))?<p>没有匹配的工具</p>:null}
    </aside><main className="mcp-tool-detail">{tool?<><h3>{tool.name}</h3><p>近7天可见记录：{calls.length} 次 · 错误 {calls.filter(call=>call.status!=="ok").length} · P95 {p95===null?"—":`${p95} ms`}</p>
      <div className="mcp-tabs" role="tablist">{tabs.map(name=><button role="tab" aria-selected={tab===name} key={name} onClick={()=>setTab(name)}>{name}</button>)}</div>
      <div role="tabpanel">{tab===tabs[0]?<><p className="mcp-note">{catalog?.model_view_basis}</p><h4>description · MCP 原样返回（{tool.description?.length||0} 字）</h4><pre>{tool.description}</pre><h4>inputSchema · JSON Schema 原样</h4><pre>{json(tool.inputSchema)}</pre><details><summary>服务 instructions</summary><pre>{catalog?.instructions}</pre></details><details><summary>annotations / outputSchema</summary><pre>{json({annotations:tool.annotations,outputSchema:tool.outputSchema})}</pre></details><p className="mcp-note">不推测 PiChat 的描述截断阈值或模型最终可见字段。请在 PiChat 侧核对实际适配结果。</p></>:null}
      {tab===tabs[1]?<div className="mcp-table-scroll"><table><thead><tr><th>参数</th><th>类型 / 必填</th><th>默认值</th><th>业务说明</th></tr></thead><tbody>{Object.entries(tool.inputSchema.properties||{}).map(([name,schema])=><tr key={name}><td>{name}</td><td>{schema.type||"见 Schema"} / {tool.inputSchema.required?.includes(name)?"是":"否"}</td><td>{schema.default===undefined?"—":json(schema.default)}</td><td>{schema.description||"—"}{schema.enum?<pre>{json(schema.enum)}</pre>:null}</td></tr>)}</tbody></table>{!Object.keys(tool.inputSchema.properties||{}).length?<p>此工具无需业务参数。</p>:null}</div>:null}
      {tab===tabs[2]?<><p className="mcp-note">工作台调试免填口令和 Key，通过隔离的进程内 MCP 协议调用同一套只读业务工具；对外 /mcp 仍需 Bearer Key。此处不验证外部 Agent 的 Key 或权限配置。</p><label>arguments · JSON<textarea aria-label="MCP 参数 JSON" spellCheck={false} value={args} onChange={e=>setArgs(e.target.value)}/></label><button className="primary" disabled={busy} onClick={()=>void run()}>{busy?"执行中…":"执行只读工具"}</button><h4>本次真实返回（非静态样例）</h4><pre aria-label="MCP 返回结果">{businessResult||result||"尚未执行"}</pre>{result?<details><summary>原始 MCP 协议回执</summary><pre>{result}</pre></details>:null}</>:null}
      {tab===tabs[3]?<><p className="mcp-note">{catalog?.statistics_basis} 最多展示最近2000条全工具记录。</p><div className="mcp-table-scroll"><table><thead><tr><th>时间</th><th>状态</th><th>耗时</th><th>回执 ID</th></tr></thead><tbody>{calls.map((call,index)=><tr key={index}><td>{new Date(call.created_at).toLocaleString()}</td><td>{call.status}</td><td>{call.duration_ms} ms</td><td>{call.audit_event_id}</td></tr>)}</tbody></table></div>{!calls.length?<p>尚无可见调用记录，不代表历史上未调用。</p>:null}</>:null}</div>
    </>:<p>加载配置后选择工具。</p>}</main></div>
  </section></div>;
}
