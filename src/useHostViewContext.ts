import {useEffect, useRef, useState, type RefObject} from 'react';
import {HostViewBridge, trustedViewSource, type GeoView} from './hostViewContext';

export function useHostViewContext(frame: RefObject<HTMLIFrameElement | null>, origin: string,
  agentId: string, connectionVersion: number, active: boolean, view: GeoView) {
  const [status,setStatus] = useState('未同步：等待面板就绪');
  const latest = useRef(view);
  latest.current = view;
  const sendRef = useRef<()=>void>(()=>{});
  const resetRef = useRef<()=>void>(()=>{});
  useEffect(()=>{
    const bridge = new HostViewBridge(agentId,()=>crypto.randomUUID());
    let timer: ReturnType<typeof setTimeout> | undefined;
    const clear = () => {clearTimeout(timer); timer=undefined;};
    const reset = () => {clear(); bridge.reset(); setStatus('未同步：等待面板就绪');};
    const send = () => {
      clear();
      const message=bridge.send(latest.current);
      if(!message) {setStatus('未同步：等待有效范围与面板绑定'); return;}
      setStatus(`范围版本 ${message.revision}：等待接收（仅供下一轮）`);
      frame.current?.contentWindow?.postMessage(message,origin);
      timer=setTimeout(()=>{bridge.pending=null;setStatus(`范围版本 ${message.revision}：未同步（回执超时）`);},10000);
    };
    sendRef.current=send;
    resetRef.current=reset;
    reset();
    if(!active) return () => {clear();sendRef.current=()=>{};resetRef.current=()=>{};};
    const receive=(event:MessageEvent)=>{
      if(!trustedViewSource(event.origin,event.source,origin,frame.current?.contentWindow)
        || !event.data || typeof event.data!=='object') return;
      if(bridge.ready(event.data)) {send();return;}
      const revision=bridge.pending?.revision;
      const result=bridge.result(event.data);
      if(result) {clear();setStatus(result==='received'
        ? `范围版本 ${revision}：已接收，待下轮使用（不是模型已使用）`
        : `范围版本 ${revision}：未同步（面板拒绝）`);}
    };
    window.addEventListener('message',receive);
    return ()=>{clear();bridge.reset();window.removeEventListener('message',receive);sendRef.current=()=>{};resetRef.current=()=>{};};
  },[frame,origin,agentId,connectionVersion,active]);
  useEffect(()=>{if(active)sendRef.current();},[active,view.snapshot_id,view.topic,view.dimension,view.start,view.end]);
  return {status,reset:()=>resetRef.current()};
}
