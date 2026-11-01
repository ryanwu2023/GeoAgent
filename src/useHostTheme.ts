import {useEffect, type RefObject} from 'react';
import {hostThemeMessage, wantsHostTheme} from './hostTheme';
import {trustedViewSource} from './hostViewContext';

export function useHostTheme(frame: RefObject<HTMLIFrameElement | null>, origin: string,
  agentId: string, connectionVersion: number, active: boolean) {
  useEffect(()=>{
    if(!active) return;
    const receive=(event:MessageEvent)=>{
      if(!trustedViewSource(event.origin,event.source,origin,frame.current?.contentWindow)
        || !wantsHostTheme(event.data,agentId)) return;
      frame.current?.contentWindow?.postMessage(hostThemeMessage(),origin);
    };
    window.addEventListener('message',receive);
    return ()=>window.removeEventListener('message',receive);
  },[frame,origin,agentId,connectionVersion,active]);
}
