import { useEffect, useState } from "react";

type Config = {origin:string; agent_id:string; phone:string; entry:string; product_user_id:string; app_key_configured:boolean};
type Key = {id:string; label:string; created_at:string; revoked_at:string|null};

export default function IntegrationSettings({onClose,onSaved}:{onClose:()=>void;onSaved:()=>void}) {
  const [token,setToken]=useState("");
  const [config,setConfig]=useState<Config|null>(null);
  const [secret,setSecret]=useState("");
  const [keys,setKeys]=useState<Key[]>([]);
  const [label,setLabel]=useState("PiChat GeoAgent");
  const [issued,setIssued]=useState("");
  const [message,setMessage]=useState("");
  const [busy,setBusy]=useState(false);
  async function request<T>(path:string,method="GET",body?:unknown):Promise<T> {
    const response=await fetch(`/api/integrations${path}`,{method,headers:{"Content-Type":"application/json","X-GeoAgent-Admin":token},body:body===undefined?undefined:JSON.stringify(body),cache:"no-store"});
    const data=await response.json();
    if(!response.ok)throw new Error(typeof data.detail==="string"?data.detail:"配置请求失败");
    return data;
  }
  async function action(fn:()=>Promise<void>){setBusy(true);setMessage("");try{await fn()}catch(error){setMessage(error instanceof Error?error.message:"请求失败")}finally{setBusy(false)}}
  async function load(){const [cfg,list]=await Promise.all([request<Config>(""),request<Key[]>("/mcp-keys")]);setConfig(cfg);setKeys(list)}
  useEffect(()=>{void action(load)},[]);
  return <div className="integration-overlay"><section className="integration-dialog" role="dialog" aria-modal="true" aria-label="连接与 MCP 配置">
    <header><h2>连接与 MCP 配置</h2><button onClick={onClose} aria-label="关闭配置">×</button></header>
    <p>配置保存到后端数据目录，立即生效，无需重新发布。密钥不会下发到嵌入面板。</p>
    <div className="integration-unlock"><label>项目管理口令<input type="password" autoComplete="off" value={token} onChange={e=>setToken(e.target.value)} placeholder="本机可留空；公网需配置 GEOAGENT_SETTINGS_TOKEN" /></label><button disabled={busy} onClick={()=>void action(load)}>加载配置</button></div>
    {config ? <><h3>PiChat 连接</h3><div className="integration-fields">
      {([['origin','PiChat 服务地址'],['agent_id','Agent ID'],['phone','嵌入用户手机号'],['entry','产品入口 entry'],['product_user_id','产品用户 ID']] as const).map(([field,title])=><label key={field}>{title}<input value={config[field]} onChange={e=>setConfig({...config,[field]:e.target.value})}/></label>)}
      <label>PiChat 应用 Key<input type="password" autoComplete="new-password" value={secret} onChange={e=>setSecret(e.target.value)} placeholder={config.app_key_configured?"已配置；留空保持原值":"请输入 PiChat 签发的应用 Key"}/></label>
    </div><div className="integration-unlock"><button disabled={busy} className="primary" onClick={()=>void action(async()=>{setConfig(await request<Config>("/pichat","PUT",{...config,app_key:secret}));setSecret("");onSaved();setMessage("PiChat 配置已保存，嵌入面板将重新连接")})}>保存 PiChat 配置</button><button disabled={busy} onClick={()=>void action(async()=>{const response=await fetch("/api/pichat/embed-identity",{cache:"no-store"});const data=await response.json();if(!response.ok||!data.token)throw new Error(data.detail||"连接验证失败");setMessage("PiChat 身份签发验证通过；请关闭配置查看嵌入面板连接（使用已保存配置）")})}>验证 PiChat 连接</button></div></>:null}
    <h3>MCP 服务</h3><p>随项目启动 · Streamable HTTP · 只读业务数据</p>
    <label>MCP 地址<input readOnly value={`${window.location.origin}/mcp`}/></label>
    <p>在 Agent 中填写此地址，认证选择 Bearer Token。Key 授权读取本项目全部快照及业务工具，请只交给可信 Agent。</p>
    <div className="integration-unlock"><label>Key 名称<input value={label} onChange={e=>setLabel(e.target.value)}/></label><button disabled={busy||!config||!label.trim()} onClick={()=>void action(async()=>{const result=await request<{key:string}>("/mcp-keys","POST",{label});setIssued(result.key);setKeys(await request<Key[]>("/mcp-keys"))})}>创建 MCP Key</button></div>
    {issued?<div className="integration-issued"><strong>新 Key 仅显示一次，请立即保存</strong><input aria-label="新 MCP Key" readOnly value={issued}/><button onClick={()=>setIssued("")}>已保存，隐藏</button></div>:null}
    <ul className="integration-keys">{keys.map(key=><li key={key.id}><span>{key.label}<small>{key.id} · {key.revoked_at?"已撤销":"启用"}</small></span><button disabled={busy||!!key.revoked_at} onClick={()=>{if(window.confirm(`撤销 ${key.label}？使用此 Key 的 Agent 将立即失去访问权限。`))void action(async()=>{await request(`/mcp-keys/${key.id}`,"DELETE");setKeys(await request<Key[]>("/mcp-keys"));setIssued("")})}}>撤销</button></li>)}</ul>
    {message?<p role="status">{message}</p>:null}
  </section></div>;
}
