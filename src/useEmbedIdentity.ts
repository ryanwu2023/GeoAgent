import {useCallback,useEffect,useRef,useState} from 'react';
import type {RefObject} from 'react';
import {EmbedIdentity} from './embedIdentity';
import {trustedViewSource} from './hostViewContext';
import {embedAuthPayload} from './embedAuthPayload';

export function useEmbedIdentity(frame: RefObject<HTMLIFrameElement|null>, origin: string,
  agentId: string, userId: string, connectionVersion: number, active: boolean) {
  const machine = useRef<EmbedIdentity|null>(null);
  const [ready,setReady] = useState(false);
  const [loading,setLoading] = useState(false);
  const [error,setError] = useState('');
  const frameLoaded = useCallback(() => machine.current?.frameLoaded(),[]);
  useEffect(() => {
    setReady(false);setLoading(false);setError('');
    const target = frame.current?.contentWindow;
    if (!active || !target) return;
    let identity: ReturnType<typeof embedAuthPayload> | null = null;
    const current = new EmbedIdentity({
      now:()=>Date.now(), schedule:(fn,ms)=>window.setTimeout(fn,ms),cancel:id=>window.clearTimeout(id),
      fetchToken:async signal=>{
        const response=await fetch(`/api/pichat/embed-identity?agent_id=${encodeURIComponent(agentId)}`,
          {signal,cache:'no-store'});
        const body=await response.json();
        if (!response.ok) throw new Error('Identity rejected');
        const issued = embedAuthPayload(body);
        if (signal.aborted) throw new Error('Identity cancelled');
        identity = issued;
        return issued.token;
      },
      deliver:token=>{
        if (frame.current?.contentWindow !== target || !identity || identity.token !== token) return;
        target.postMessage({type:'pi-chat:embed-identity',agentId,token},origin);
        target.postMessage({type:'finance:auth:token',token,user:identity.user},origin);
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
  return {ready,loading,error,frameLoaded,setError};
}
