export async function api<T>(path:string, body?:unknown):Promise<T> {
  const response=await fetch('/api'+path, body === undefined ? undefined : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok){let detail='请求失败';try{const data=await response.json();detail=typeof data.detail==='string'?data.detail:JSON.stringify(data.detail)}catch{detail=await response.text()}throw new Error(detail)}
  return response.json();
}
export const dateText=(date:string|null|undefined)=>{if(!date)return '时间未披露';const zone=date.match(/(Z|[+-]\d{2}:\d{2})$/)?.[1];return date.replace('T',' ').slice(0,16)+(zone?(zone==='Z'||zone==='+00:00'?' 世界时':' 世界时'+zone):'');};
export const num=(value:number|null|undefined,unit='')=>value==null?'—':unit==='USD'?(value/1e8).toFixed(2)+' 亿美元':value.toLocaleString('zh-CN',{maximumFractionDigits:Math.abs(value)<10?4:2});
