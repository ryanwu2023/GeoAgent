import {createContext, useContext, useEffect, useState, type FormEvent, type ReactNode} from 'react';
import {ArrowRight, Eye, EyeOff, Globe2, LoaderCircle, LogOut, ShieldCheck} from 'lucide-react';
import './login.css';

type Session = {enabled:boolean; authenticated:boolean; username:string};
const SessionContext = createContext<{session:Session|null; logout:()=>Promise<void>; busy:boolean}>({session:null,logout:async()=>{},busy:false});
async function auth(path:string, body?:unknown):Promise<Session> {
  const response=await fetch('/api/auth/'+path, body===undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json','X-GeoAgent-Login':'1'},body:JSON.stringify(body),cache:'no-store'});
  const data=await response.json();
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'登录服务暂不可用，请稍后重试');
  return data;
}
export function SessionAccount(){
  const {session,logout,busy}=useContext(SessionContext);
  if(!session?.enabled)return null;
  return <button className="session-account" disabled={busy} onClick={()=>void logout()} title={`当前账号：${session.username} · 退出登录`}><LogOut size={15}/><span>退出登录</span></button>;
}
export default function SessionGate({children}:{children:ReactNode}){
  const [session,setSession]=useState<Session|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [username,setUsername]=useState(''),[password,setPassword]=useState(''),[show,setShow]=useState(false);
  const [checking,setChecking]=useState(true);
  function accept(next:Session){setSession(next);setPassword('');setError('');history.replaceState(null,'',next.authenticated?'/':'/login');}
  async function check(){setChecking(true);setError('');try{accept(await auth('session'))}catch(e){setError((e as Error).message)}finally{setChecking(false)}}
  useEffect(()=>{let live=true;auth('session').then(value=>{if(live)accept(value)}).catch(e=>{if(live)setError(e.message)}).finally(()=>{if(live)setChecking(false)});const expired=()=>{accept({enabled:true,authenticated:false,username:''});setError('登录已过期，请重新登录')};window.addEventListener('geoagent-session-expired',expired);return()=>{live=false;window.removeEventListener('geoagent-session-expired',expired)}},[]);
  async function login(event:FormEvent){event.preventDefault();setBusy(true);setError('');try{accept(await auth('login',{username,password}))}catch(e){setPassword('');setError((e as Error).message)}finally{setBusy(false)}}
  async function logout(){setBusy(true);try{await auth('logout',{});accept({enabled:true,authenticated:false,username:''})}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
  if(session?.authenticated)return <SessionContext.Provider value={{session,logout,busy}}>{error&&<div className="session-error" role="alert">{error}<button onClick={()=>setError('')}>关闭</button></div>}{children}</SessionContext.Provider>;
  return <main className="login-page">
    <section className="login-story" aria-label="GeoAgent 地缘政治研究工作台">
      <a className="login-brand" href="/login"><span><Globe2 size={27}/></span>GeoAgent <small>地缘政治智能体</small></a>
      <div className="login-story-body"><span className="login-eyebrow">地缘政治研究工作台</span><h1>看清变化，<br/>让研究有据可循。</h1><p>汇集全球信息，追踪关键指标，<br/>从每一条证据出发，形成清晰的判断。</p><div className="login-orbit" aria-hidden="true"><Globe2/><span>信息采集</span><span>证据核验</span><span>研究分析</span></div></div>
      <footer>指标变化 · 证据核验 · 资产关联</footer>
    </section>
    <section className="login-panel"><div className="login-card"><span className="login-eyebrow">欢迎使用 GEOAGENT</span><h2>登录研究工作台</h2><p className="login-intro">请输入您的账号和密码，继续开展研究。</p>
      {checking?<div className="login-check" role="status"><LoaderCircle className="spin"/>正在检查登录状态…</div>:!session?<div className="login-unavailable"><p role="alert">{error}</p><button className="login-submit" onClick={()=>void check()}>重新连接</button></div>:<form onSubmit={login}>
        <label htmlFor="login-username">账号</label><input id="login-username" name="username" autoComplete="username" required maxLength={128} value={username} onChange={e=>setUsername(e.target.value)} placeholder="请输入账号" autoFocus disabled={busy}/>
        <label htmlFor="login-password">密码</label><div className="login-password"><input id="login-password" name="password" type={show?'text':'password'} autoComplete="current-password" required maxLength={1024} value={password} onChange={e=>setPassword(e.target.value)} placeholder="请输入密码" disabled={busy}/><button type="button" aria-label={show?'隐藏密码':'显示密码'} onClick={()=>setShow(!show)}>{show?<EyeOff size={19}/>:<Eye size={19}/>}</button></div>
        {error&&<p className="login-error" role="alert">{error}</p>}
        <button className="login-submit" disabled={busy||!username||!password}>{busy?<><LoaderCircle size={18} className="spin"/>正在登录…</>:<>登录<ArrowRight size={18}/></>}</button>
        <p className="login-help">忘记密码或需要账号，请联系系统管理员。</p>
      </form>}
      <div className="login-secure"><ShieldCheck size={16}/>安全登录 · 仅限授权用户访问</div>
    </div><footer>GeoAgent · 地缘政治数据与分析平台</footer></section>
  </main>;
}
