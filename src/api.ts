import type {MeetingDetail,MeetingListResponse,ToolTraceDetail} from "./types";

export async function api<T>(path:string, body?:unknown, signal?:AbortSignal):Promise<T> {
  const response=await fetch('/api'+path, body === undefined ? {signal} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal});
  const raw=await response.text();
  if(!response.ok){let detail='请求失败';try{const data=JSON.parse(raw);detail=typeof data.detail==='string'?data.detail:JSON.stringify(data.detail)}catch{if(raw) detail=raw}throw new Error(detail)}
  return (raw?JSON.parse(raw):{}) as T;
}
export function getToolCallDetail(runId:string,callId:string,signal?:AbortSignal) {
  return api<ToolTraceDetail>(`/qa-runs/${encodeURIComponent(runId)}/tool-calls/${encodeURIComponent(callId)}`,undefined,signal);
}
export function apiMeetings(params:{snapshot_id:string;topic?:string;track?:string;status?:string;person?:string;start?:string;end?:string;include_inactive?:boolean;limit?:number},signal?:AbortSignal){
  const query=new URLSearchParams();
  Object.entries(params).forEach(([key,value])=>{if(value!==undefined&&value!=="")query.set(key,String(value))});
  return api<MeetingListResponse>(`/meetings?${query.toString()}`,undefined,signal);
}
export function apiMeetingDetail(snapshotId:string,eventId:string,signal?:AbortSignal){
  return api<MeetingDetail>(`/meetings/${encodeURIComponent(eventId)}?snapshot_id=${encodeURIComponent(snapshotId)}`,undefined,signal);
}
export async function apiMeetingExport(params:{snapshot_id:string;topic?:string;track?:string;status?:string;person?:string;start?:string;end?:string;include_inactive?:boolean}){
  const query=new URLSearchParams();
  Object.entries(params).forEach(([key,value])=>{if(value!==undefined&&value!=="")query.set(key,String(value))});
  const response=await fetch(`/api/meetings/export.xlsx?${query.toString()}`,{headers:{Accept:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}});
  if(!response.ok){const raw=await response.text();let detail='导出失败';try{const data=JSON.parse(raw);detail=typeof data.detail==='string'?data.detail:JSON.stringify(data.detail)}catch{if(raw)detail=raw}throw new Error(detail)}
  const disposition=response.headers.get('Content-Disposition')||'';
  const encoded=disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  let filename='GeoAgent会晤日历.xlsx';
  if(encoded){try{filename=decodeURIComponent(encoded)}catch{/* 使用安全的默认文件名 */}}
  return {blob:await response.blob(),filename};
}
export async function apiPut<T>(path:string, body:unknown):Promise<T> {
  const response=await fetch('/api'+path,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const raw=await response.text();
  if(!response.ok){let detail='请求失败';try{const data=JSON.parse(raw);detail=typeof data.detail==='string'?data.detail:JSON.stringify(data.detail)}catch{if(raw) detail=raw}throw new Error(detail)}
  return (raw?JSON.parse(raw):{}) as T;
}
export async function apiStream<TProgress,TFinal>(path:string,body:unknown,onProgress:(item:TProgress)=>void,onRun?:(item:{run_id:string;phase:string;status:string})=>void):Promise<TFinal> {
  const response=await fetch('/api'+path,{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/x-ndjson'},body:JSON.stringify(body)});
  if(!response.ok){const raw=await response.text();let detail='请求失败';try{const data=JSON.parse(raw);detail=typeof data.detail==='string'?data.detail:JSON.stringify(data.detail)}catch{if(raw)detail=raw}throw new Error(detail)}
  if(!response.body)throw new Error('浏览器未提供流式响应');
  const reader=response.body.getReader(),decoder=new TextDecoder();
  let buffer='',answer:TFinal|undefined;
  while(true){
    const {done,value}=await reader.read();
    buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});
    const lines=buffer.split('\n');buffer=lines.pop()||'';
    for(const line of lines){
      if(!line.trim())continue;
      const event=JSON.parse(line);
      if(event.event==='run')onRun?.(event.data);
      else if(event.event==='progress')onProgress(event.data as TProgress);
      else if(event.event==='answer')answer=event.data as TFinal;
      else if(event.event==='error')throw new Error(event.data?.message||'问答处理失败');
    }
    if(done)break;
  }
  if(buffer.trim()){
    const event=JSON.parse(buffer);
    if(event.event==='run')onRun?.(event.data);
    else if(event.event==='progress')onProgress(event.data as TProgress);
    else if(event.event==='answer')answer=event.data as TFinal;
    else if(event.event==='error')throw new Error(event.data?.message||'问答处理失败');
  }
  if(!answer)throw new Error('问答服务未返回最终结果');
  return answer;
}
export const dateText=(date:string|null|undefined)=>{if(!date)return '时间未披露';const zone=date.match(/(Z|[+-]\d{2}:\d{2})$/)?.[1];return date.replace('T',' ').slice(0,16)+(zone?(zone==='Z'||zone==='+00:00'?' 世界时':' 世界时'+zone):'');};
export const num=(value:number|null|undefined,unit='')=>value==null?'—':unit==='USD'?(value/1e8).toFixed(2)+' 亿美元':value.toLocaleString('zh-CN',{maximumFractionDigits:Math.abs(value)<10?4:2});
