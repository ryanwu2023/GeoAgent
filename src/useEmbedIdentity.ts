import {useCallback,useEffect,useRef,useState} from 'react';
import type {RefObject} from 'react';
import {EmbedIdentity} from './embedIdentity';
import {trustedViewSource} from './hostViewContext';

export function useEmbedIdentity(frame: RefObject<HTMLIFrameElement|null>, origin: string,
  agentId: string, userId: string, connectionVersion: number, active: boolean) {
  const machine = useRef<EmbedIdentity|null>(null);
  const [ready,setReady] = useState(false);
  const [loading,setLoading] = useState(false);
  const [error,setError] = useState('');
  const request = useCallback((force = true) => machine.current?.request(force),[]);
  useEffect(() => {
    setReady(false);setLoading(false);setError('');
    const target = frame.current?.contentWindow;
    if (!active || !target) return;
    const current = new EmbedIdentity({
      now:()=>Date.now(), schedule:(fn,ms)=>window.setTimeout(fn,ms),cancel:id=>window.clearTimeout(id),
      fetchToken:async signal=>{
        const response=await fetch(`/api/pichat/embed-identity?agent_id=${encodeURIComponent(agentId)}`,
          {signal,cache:'no-store'});
        const body=await response.json();
        if (!response.ok || typeof body.token !== 'string' || !body.token) throw new Error('Identity rejected');
        return body.token;
      },
      deliver:token=>{
        if (frame.current?.contentWindow !== target) return;
        target.postMessage({type:'pi-chat:embed-identity',agentId,token},origin);
        target.postMessage({type:'finance:auth:token',token,user:{id:userId,username:userId,
          role:'user',status:'active',userType:'embed',accounts:[],qqBindings:[],externalBindings:[]}},origin);
      },
      status:(state,message)=>{
        setReady(state==='ready');setLoading(state==='fetching');setError(message||'');
      },
    });
    machine.current=current;
    const onMessage=(event:MessageEvent)=>{
      if (!trustedViewSource(event.origin,event.source,origin,target) || frame.current?.contentWindow!==target) return;
      const data=event.data;
      if (!data || typeof data!=='object' || (data.agentId && data.agentId!==agentId)) return;
      if (data.type==='finance:auth:request') void current.request();
      else if (data.type==='finance:auth:required' ||
        (data.type==='pi-chat:embed-identity-required' && data.agentId===agentId)) void current.request(true);
      else if (data.type==='finance:chat:ready') current.ready();
    };
    window.addEventListener('message',onMessage);
    void current.request();
    return ()=>{
      window.removeEventListener('message',onMessage);current.dispose();
      if (machine.current===current) machine.current=null;
    };
  },[frame,origin,agentId,userId,connectionVersion,active]);
  return {ready,loading,error,request,setError};
}
