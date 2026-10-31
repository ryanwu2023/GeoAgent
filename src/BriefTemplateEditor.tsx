import {sessionFetch as fetch} from "./sessionFetch";
import { useEffect, useState } from "react";
import type { BriefTemplate } from "./types";

async function request<T>(path:string,method="GET",body?:unknown):Promise<T>{
  const response=await fetch(`/api${path}`,{method,headers:{"Content-Type":"application/json"},body:body===undefined?undefined:JSON.stringify(body),cache:"no-store"});
  const raw=await response.text();
  if(!response.ok){let message=raw||"模板请求失败";try{const parsed=JSON.parse(raw);message=parsed.detail||message}catch{}throw new Error(message)}
  return (raw?JSON.parse(raw):{}) as T;
}

export default function BriefTemplateEditor({onClose,onChanged}:{onClose:()=>void;onChanged:(templates:BriefTemplate[])=>void}){
  const [templates,setTemplates]=useState<BriefTemplate[]>([]);
  const [selected,setSelected]=useState("");
  const [draft,setDraft]=useState<BriefTemplate|null>(null);
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState("");
  const [baseline,setBaseline]=useState("");
  const dirty=Boolean(draft&&!draft.is_system&&JSON.stringify(draft)!==baseline);
  async function load(preferred=""){
    const rows=await request<BriefTemplate[]>("/brief-templates");
    setTemplates(rows);onChanged(rows);
    const retained=rows.some(row=>row.id===selected)?selected:"";
    const id=preferred||retained||rows.find(row=>row.is_default)?.id||rows[0]?.id||"";
    const next=rows.find(row=>row.id===id)||null;
    setSelected(id);setDraft(next);setBaseline(next?JSON.stringify(next):"");
  }
  useEffect(()=>{void load().catch(error=>setMessage(error.message))},[]);
  function update<K extends keyof BriefTemplate>(key:K,value:BriefTemplate[K]){if(draft)setDraft({...draft,[key]:value})}
  function move(index:number,offset:number){if(!draft)return;const target=index+offset;if(target<0||target>=draft.sections.length)return;const rows=[...draft.sections];[rows[index],rows[target]]=[rows[target],rows[index]];update("sections",rows)}
  function copy(){if(!draft)return;const next={...draft,id:"",name:`${draft.name}（副本）`,version:0,is_default:false,is_system:false,sections:draft.sections.map(item=>({...item}))};setSelected("");setDraft(next);setBaseline(JSON.stringify(draft));setMessage("副本尚未保存，可先调整模板内容")}
  function close(){if(dirty&&!window.confirm("当前模板有未保存的修改，确定关闭吗？"))return;onClose()}
  function selectTemplate(id:string){if(dirty&&!window.confirm("切换模板将放弃未保存的修改，确定继续吗？"))return;const next=templates.find(row=>row.id===id)||null;setSelected(id);setDraft(next);setBaseline(next?JSON.stringify(next):"");setMessage("")}
  async function action(fn:()=>Promise<void>){setBusy(true);setMessage("");try{await fn()}catch(error){setMessage(error instanceof Error?error.message:"模板操作失败")}finally{setBusy(false)}}
  async function save(){if(!draft)return;await action(async()=>{const saved=await request<BriefTemplate>(draft.id?`/brief-templates/${draft.id}`:"/brief-templates",draft.id?"PUT":"POST",{config:draft});await load(saved.id);setMessage("模板已保存")})}
  async function makeDefault(){if(!draft?.id)return;await action(async()=>{await request(`/brief-templates/${draft.id}/default`,"PUT",{});await load(draft.id);setMessage("已设为默认模板")})}
  async function remove(){if(!draft?.id||draft.is_system)return;await action(async()=>{await request(`/brief-templates/${draft.id}`,"DELETE");await load();setMessage("模板已删除")})}
  return <div className="drawer-backdrop" onClick={close}><aside className="drawer wide brief-template-editor" onClick={event=>event.stopPropagation()} role="dialog" aria-modal="true" aria-label="简报模板配置">
    <div className="drawer-heading template-editor-heading"><div><h2>简报模板</h2><small>控制研究范围、证据窗口和最终呈现顺序。</small></div><div className="template-heading-actions">{dirty&&<span>有未保存修改</span>}<button onClick={close} aria-label="关闭">×</button></div></div>
    <label className="template-field"><span>正在编辑</span><select value={selected} onChange={event=>selectTemplate(event.target.value)}>{!selected&&<option value="">未保存的新模板</option>}{templates.map(row=><option key={row.id} value={row.id}>{row.name}{row.is_default?" · 默认":""}</option>)}</select></label>
    {draft&&<>
      <div className="template-context"><div><span className={`template-kind ${draft.is_system?"system":"custom"}`}>{draft.is_system?"系统模板":"自定义模板"}</span>{draft.is_default&&<span className="template-default">当前默认</span>}</div><small>{draft.is_system?"系统模板保持只读。复制后可调整章节和参数。":"修改仅影响之后生成的简报，历史档案保持原样。"}</small></div>
      <div className="template-actions"><button className={draft.is_system?"primary":""} onClick={copy}>复制并编辑</button><button onClick={()=>void makeDefault()} disabled={!draft.id||draft.is_default||busy}>设为默认</button>{!draft.is_system&&draft.id&&<button className="danger-quiet" onClick={()=>{if(window.confirm(`确定删除模板“${draft.name}”吗？`))void remove()}} disabled={busy}>删除模板</button>}</div>
      <div className="template-grid">
        <label><span>模板名称</span><input value={draft.name} disabled={draft.is_system} onChange={event=>update("name",event.target.value)}/></label>
        <label><span>适用主题</span><select value={draft.topic_scope} disabled={draft.is_system} onChange={event=>update("topic_scope",event.target.value as BriefTemplate["topic_scope"])}><option value="all">美伊与俄乌</option><option value="usiran">仅美伊</option><option value="ukraine">仅俄乌</option></select></label>
        <label><span>初始回看小时</span><input type="number" min="1" max="168" value={draft.lookback_hours} disabled={draft.is_system} onChange={event=>update("lookback_hours",Number(event.target.value))}/></label>
        <label><span>最大扩展天数</span><input type="number" min="1" max="30" value={draft.max_lookback_days} disabled={draft.is_system} onChange={event=>update("max_lookback_days",Number(event.target.value))}/></label>
        <label><span>每主题证据上限</span><input type="number" min="2" max="30" value={draft.evidence_limit} disabled={draft.is_system} onChange={event=>update("evidence_limit",Number(event.target.value))}/></label>
      </div>
      <div className="template-section-heading"><div><h3>章节结构</h3><small>按基金经理的阅读顺序排列；证据与信源始终保留。</small></div><span>{draft.sections.filter(section=>section.enabled).length} 个章节</span></div>
      <div className="template-sections">{draft.sections.map((section,index)=><article key={section.id}>
        <div className="template-section-title"><span>{String(index+1).padStart(2,"0")}</span><label className="template-check"><input type="checkbox" checked={section.enabled} disabled={draft.is_system||section.id==="sources"} onChange={event=>update("sections",draft.sections.map((item,i)=>i===index?{...item,enabled:event.target.checked}:item))}/><b>{section.title}</b></label></div>
        <div className="template-order"><button aria-label={`上移${section.title}`} title="上移" disabled={draft.is_system||index===0} onClick={()=>move(index,-1)}>↑</button><button aria-label={`下移${section.title}`} title="下移" disabled={draft.is_system||index===draft.sections.length-1} onClick={()=>move(index,1)}>↓</button></div>
        {!draft.is_system&&<><input aria-label={`${section.title}名称`} value={section.title} onChange={event=>update("sections",draft.sections.map((item,i)=>i===index?{...item,title:event.target.value}:item))}/><textarea aria-label={`${section.title}写作要求`} value={section.instruction} onChange={event=>update("sections",draft.sections.map((item,i)=>i===index?{...item,instruction:event.target.value}:item))}/></>}
      </article>)}</div>
      <section className="template-preview" aria-label="模板结构预览">
        <b>结构预览</b>
        <p>{draft.sections.filter(section=>section.enabled).map(section=>section.title).join(" → ")}</p>
        <small>保存后回到研究简报，选择此模板并点击“按模板生成”即可测试真实数据和模型输出。</small>
      </section>
      {!draft.is_system&&<div className="template-savebar"><span>{dirty?"修改尚未保存":"所有修改已保存"}</span><button className="primary" disabled={busy||!dirty||!draft.name.trim()} onClick={()=>void save()}>{busy?"保存中…":draft.id?"保存修改":"创建模板"}</button></div>}
    </>}
    {message&&<p className="mcp-message" role="status">{message}</p>}
  </aside></div>
}
