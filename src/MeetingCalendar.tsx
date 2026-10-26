import {useEffect,useMemo,useState} from "react";
import {AlertTriangle,CalendarDays,ChevronLeft,ChevronRight,Clock3,Download,ExternalLink,MapPin,RefreshCw,Users,X} from "lucide-react";
import {apiMeetingDetail,apiMeetingExport,apiMeetings} from "./api";
import {buildMeetingMonth,formatMeetingMonthLabel,meetingDateLabel,meetingDates,meetingOccursOn,meetingPersonLabel,meetingStatusMeta,shiftMeetingMonth,sortMeetings} from "./meeting_calendar";
import type {Meeting,MeetingDetail,MeetingListResponse} from "./types";

type Props={snapshotId:string;topic:"all"|"usiran"|"ukraine";onEvidence:(id:string)=>void};

const daysBetween=(start:string,end:string)=>Math.round((Date.parse(end)-Date.parse(start))/86400000);
const weekLabels=["周一","周二","周三","周四","周五","周六","周日"];

export default function MeetingCalendar({snapshotId,onEvidence}:Props){
  const [payload,setPayload]=useState<MeetingListResponse|null>(null);
  const [selected,setSelected]=useState<MeetingDetail|null>(null);
  const [loading,setLoading]=useState(false),[exporting,setExporting]=useState(false),[error,setError]=useState("");
  const [includeInactive,setIncludeInactive]=useState(false),[status,setStatus]=useState(""),[person,setPerson]=useState("");
  // 外交日历是跨主题产品，不应被顶部的美伊/俄乌工作台范围静默裁掉。
  // 保留当前主题作为显式可选项，但默认展示全部外交日程。
  const [calendarTopic,setCalendarTopic]=useState<"all"|"usiran"|"ukraine">("all");
  const [refresh,setRefresh]=useState(0),[viewMonth,setViewMonth]=useState(""),[selectedDate,setSelectedDate]=useState(""),[focusedId,setFocusedId]=useState("");

  useEffect(()=>{
    if(!snapshotId){setPayload(null);return}
    const controller=new AbortController();setLoading(true);setError("");setSelected(null);
    apiMeetings({snapshot_id:snapshotId,topic:calendarTopic,include_inactive:includeInactive,limit:500},controller.signal)
      .then(value=>{setPayload(value);setViewMonth(current=>current||value.range.start?.slice(0,7)||value.as_of?.slice(0,7)||"")})
      .catch(reason=>{if(reason?.name!=="AbortError")setError(reason instanceof Error?reason.message:"会晤日历加载失败")})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false)});
    return()=>controller.abort();
  },[snapshotId,calendarTopic,includeInactive,refresh]);

  const persons=useMemo(()=>Array.from(new Map((payload?.records||[]).flatMap(item=>item.persons).map(item=>[item.id,item])).values()).sort((a,b)=>a.name.localeCompare(b.name,"zh-CN")),[payload]);
  const rows=useMemo(()=>sortMeetings((payload?.records||[]).filter(item=>(!status||item.status===status)&&(!person||item.persons.some(value=>value.id===person)))),[payload,status,person]);
  const visibleRows=useMemo(()=>selectedDate?rows.filter(item=>meetingOccursOn(item,selectedDate)):rows,[rows,selectedDate]);
  const calendarDays=useMemo(()=>buildMeetingMonth(viewMonth),[viewMonth]);
  const eventsByDate=useMemo(()=>{
    const result=new Map<string,Meeting[]>();
    rows.forEach(item=>meetingDates(item).forEach(date=>result.set(date,[...(result.get(date)||[]),item])));
    return result;
  },[rows]);
  const baseMonth=(payload?.range.start||payload?.as_of||"").slice(0,7);
  const today=(payload?.as_of||"").slice(0,10);
  const counts=useMemo(()=>{
    const start=payload?.range.start||"";
    const within=(days:number)=>rows.filter(item=>item.date_start&&daysBetween(start,item.date_start)>=0&&daysBetween(start,item.date_start)<=days).length;
    return {week:within(7),month:within(30),horizon:within(120),confirmed:rows.filter(item=>["confirmed","scheduled"].includes(item.status)).length};
  },[rows,payload]);

  useEffect(()=>{
    if(!focusedId)return;
    const timer=window.setTimeout(()=>document.getElementById(`meeting-${focusedId}`)?.scrollIntoView({behavior:"smooth",block:"center"}),40);
    return()=>window.clearTimeout(timer);
  },[focusedId,selectedDate]);

  async function open(item:Meeting){
    try{setSelected(await apiMeetingDetail(snapshotId,item.id))}catch(reason){setError(reason instanceof Error?reason.message:"会晤详情加载失败")}
  }
  function changeMonth(delta:number){setViewMonth(value=>shiftMeetingMonth(value,delta));setSelectedDate("");setFocusedId("")}
  function chooseDate(date:string){setSelectedDate(value=>value===date?"":date);setFocusedId("")}
  function focusMeeting(item:Meeting,date:string){setSelectedDate(date);setFocusedId(item.id)}
  async function exportExcel(){
    setExporting(true);setError("");
    try{
      const range=selectedDate?{start:selectedDate,end:selectedDate}:{};
      const {blob,filename}=await apiMeetingExport({snapshot_id:snapshotId,topic:calendarTopic,status,person,include_inactive:includeInactive,...range});
      const url=URL.createObjectURL(blob),link=document.createElement("a");
      link.href=url;link.download=filename;document.body.appendChild(link);link.click();link.remove();
      window.setTimeout(()=>URL.revokeObjectURL(url),0);
    }catch(reason){setError(reason instanceof Error?reason.message:"会晤日历导出失败")}
    finally{setExporting(false)}
  }

  return <section className="meeting-calendar">
    <header className="meeting-heading"><div><span><CalendarDays size={15}/> 外交日程</span><h2>关键人物会晤日历</h2><p>按日期查看即将发生的外交事项，并核对事件、人物、地点、时间和来源确认状态。</p></div><div className="meeting-heading-actions"><button type="button" onClick={()=>void exportExcel()} disabled={loading||exporting||!visibleRows.length}><Download className={exporting?"spin":""} size={15}/>{exporting?"导出中…":"导出 Excel"}</button><button type="button" onClick={()=>setRefresh(value=>value+1)} disabled={loading}><RefreshCw className={loading?"spin":""} size={15}/>刷新</button></div></header>
    <div className="meeting-summary">
      <article><small>未来 7 天</small><strong>{counts.week}</strong><span>项相关日程</span></article>
      <article><small>未来 30 天</small><strong>{counts.month}</strong><span>项相关日程</span></article>
      <article><small>未来 120 天</small><strong>{counts.horizon}</strong><span>项相关日程</span></article>
      <article><small>确认或定档</small><strong>{counts.confirmed}</strong><span>项较强依据</span></article>
    </div>
    <div className="meeting-filters">
      <select aria-label="筛选会晤主题范围" value={calendarTopic} onChange={event=>setCalendarTopic(event.target.value as "all"|"usiran"|"ukraine")}>
        <option value="all">全部外交日程</option>
        <option value="usiran">美伊相关日程</option>
        <option value="ukraine">俄乌相关日程</option>
      </select>
      <select aria-label="筛选会晤状态" value={status} onChange={event=>setStatus(event.target.value)}><option value="">全部状态</option>{Object.keys(payload?.status_counts||{}).map(value=><option value={value} key={value}>{meetingStatusMeta(value).label}（{payload?.status_counts[value]}）</option>)}</select>
      <select aria-label="筛选会晤人物" value={person} onChange={event=>setPerson(event.target.value)}><option value="">全部人物</option>{persons.map(value=><option value={value.id} key={value.id}>{meetingPersonLabel(value)}</option>)}</select>
      <label><input type="checkbox" checked={includeInactive} onChange={event=>setIncludeInactive(event.target.checked)}/>包含取消、推迟、否认、过期和已举行</label>
    </div>
    {payload&&<p className="meeting-basis">当前范围：{calendarTopic==="all"?"全部外交日程":calendarTopic==="usiran"?"美伊相关日程":"俄乌相关日程"} · 数据截至 {payload.as_of?.replace("T"," ").slice(0,16)} · {payload.basis}</p>}
    {error&&<div className="meeting-error" role="alert">{error}</div>}
    {loading&&!payload&&<div className="meeting-empty"><RefreshCw className="spin"/>正在读取冻结快照中的会晤日历…</div>}

    {!!payload&&!!calendarDays.length&&<section className="meeting-month-panel" aria-label="月历视图">
      <header className="meeting-month-toolbar">
        <div><span>月历总览</span><h3>{formatMeetingMonthLabel(viewMonth)}</h3></div>
        <nav aria-label="切换月份">
          <button type="button" aria-label="上一个月" onClick={()=>changeMonth(-1)}><ChevronLeft size={16}/></button>
          <button type="button" className="meeting-month-today" onClick={()=>{setViewMonth(baseMonth);setSelectedDate("");setFocusedId("")}}>回到当前</button>
          <button type="button" aria-label="下一个月" onClick={()=>changeMonth(1)}><ChevronRight size={16}/></button>
        </nav>
      </header>
      <div className="meeting-month-scroll">
        <div className="meeting-month-grid">
          {weekLabels.map(label=><div className="meeting-weekday" key={label}>{label}</div>)}
          {calendarDays.map(day=>{const events=eventsByDate.get(day.date)||[];const active=selectedDate===day.date;return <div className={`meeting-day${day.inMonth?"":" is-outside"}${day.date===today?" is-today":""}${active?" is-selected":""}`} key={day.date}>
            <button type="button" className="meeting-day-number" aria-label={`筛选 ${day.date} 日程`} aria-pressed={active} onClick={()=>chooseDate(day.date)}><span>{day.day}</span>{events.length>0&&<em>{events.length}项</em>}</button>
            <div className="meeting-day-events">{events.slice(0,3).map(item=>{const meta=meetingStatusMeta(item.status);return <button type="button" className={`meeting-event-chip tone-${meta.tone}`} title={`${item.title}｜${meetingDateLabel(item)}｜${item.location||"地点未定"}`} onClick={()=>focusMeeting(item,day.date)} key={item.id}><span>{item.title}</span></button>})}{events.length>3&&<button type="button" className="meeting-event-more" onClick={()=>chooseDate(day.date)}>另有 {events.length-3} 项</button>}</div>
          </div>})}
        </div>
      </div>
      <footer><span><i className="tone-confirmed"/>已确认/日期已定</span><span><i className="tone-arranged"/>已安排</span><span><i className="tone-proposed"/>预告/待确认</span><span>点击日期筛选下方台账</span></footer>
    </section>}

    {!!payload&&<section className="meeting-agenda">
      <header><div><span>详细台账</span><h3>{selectedDate?`${selectedDate} 的日程`:`全部相关日程`}</h3></div><div>{selectedDate&&<button type="button" onClick={()=>{setSelectedDate("");setFocusedId("")}}>清除日期筛选</button>}<small>共 {visibleRows.length} 项</small></div></header>
      {!loading&&!visibleRows.length&&<div className="meeting-empty"><CalendarDays/>当前筛选范围没有会晤日程</div>}
      <div className="meeting-list">{visibleRows.map(item=>{const meta=meetingStatusMeta(item.status);return <article id={`meeting-${item.id}`} key={item.id} className={`meeting-row tone-${meta.tone}${focusedId===item.id?" is-focused":""}`}>
        <div className="meeting-row-date"><CalendarDays size={15}/><time>{meetingDateLabel(item)}</time><span>{item.precision==="day"?"明确日期":"日期范围或待确认"}</span></div>
        <div className="meeting-row-content"><span className="meeting-track">{item.track_label}</span><h4>{item.title}</h4><div className="meeting-row-facts">
          <p><span><Clock3 size={13}/>日程时间</span><strong>{meetingDateLabel(item)}</strong></p>
          <p><span><Users size={13}/>相关人物</span><strong>{item.persons.length?item.persons.map(meetingPersonLabel).join("、"):"参与人待披露"}</strong></p>
          <p><span><MapPin size={13}/>会晤地点</span><strong>{item.location||"地点未定"}{item.points.length?" · 可在地图定位":""}</strong></p>
        </div></div>
        <aside><em>{meta.label}</em><small>{item.sources.length} 个来源</small>{item.conflict&&<span><AlertTriangle size={12}/>口径冲突</span>}{item.non_meeting&&<span>政治日程，非会晤</span>}<button type="button" onClick={()=>void open(item)}>查看详情</button></aside>
      </article>})}</div>
    </section>}

    {selected&&<div className="meeting-drawer-layer" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget)setSelected(null)}}><aside className="meeting-drawer" role="dialog" aria-modal="true" aria-label="会晤详情">
      <header><div><span>{selected.track_label} · {meetingStatusMeta(selected.status).label}</span><h3>{selected.title}</h3></div><button type="button" aria-label="关闭会晤详情" onClick={()=>setSelected(null)}><X size={17}/></button></header>
      <dl><div><dt>日程时间</dt><dd>{meetingDateLabel(selected)}</dd></div><div><dt>会晤地点</dt><dd>{selected.location||"地点未定"}</dd></div><div><dt>日期精度</dt><dd>{selected.precision}</dd></div><div><dt>确认状态</dt><dd>{selected.status_label}</dd></div></dl>
      {selected.points.length>0&&<p className="meeting-location-note"><MapPin size={14}/>计划会晤地点，不代表实时位置。</p>}
      <section><h4>相关人物</h4>{selected.persons.length?selected.persons.map(value=><article key={value.id}><strong>{meetingPersonLabel(value)}</strong><span>{value.country||"国家待补充"}</span></article>):<p>参与人待披露。</p>}</section>
      {selected.conflict&&<p className="meeting-conflict"><AlertTriangle size={14}/>不同来源存在口径冲突，请逐条核对依据。</p>}
      <section><h4>依据与原始来源</h4>{selected.sources.length?selected.sources.map((source,index)=><article key={`${source.url}-${index}`}><strong>{source.name||source.outlet||"公开来源"}</strong><span>{source.published||"发布时间未披露"} · {source.tier?`T${source.tier}`:"未分级"}</span>{source.quote&&<p>{source.quote}</p>}{source.url&&<a href={source.url} target="_blank" rel="noreferrer">查看原始来源 <ExternalLink size={12}/></a>}</article>):<p>该条目来自人工日程基线，尚无公开来源确认。</p>}</section>
      {!!selected.updates.length&&<section><h4>状态变化</h4>{selected.updates.map((update,index)=><pre key={index}>{JSON.stringify(update,null,2)}</pre>)}</section>}
      <button type="button" className="primary" onClick={()=>onEvidence(selected.evidence_id)}>查看关联证据</button>
    </aside></div>}
  </section>
}
