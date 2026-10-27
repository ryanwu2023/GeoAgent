import type {Meeting,MeetingPerson} from "./types";

export type MeetingCalendarDay={date:string;day:number;inMonth:boolean};

const ymdPattern=/^(\d{4})-(\d{2})-(\d{2})$/;
const pad=(value:number)=>String(value).padStart(2,"0");
const toYmd=(value:Date)=>`${value.getFullYear()}-${pad(value.getMonth()+1)}-${pad(value.getDate())}`;
const parseYmd=(value:string)=>{
  const match=ymdPattern.exec(value||"");
  if(!match)return null;
  const date=new Date(Number(match[1]),Number(match[2])-1,Number(match[3]));
  return Number.isNaN(date.getTime())?null:date;
};

export function buildMeetingMonth(month:string):MeetingCalendarDay[]{
  const match=/^(\d{4})-(\d{2})$/.exec(month||"");
  if(!match)return [];
  const year=Number(match[1]),monthIndex=Number(match[2])-1;
  const first=new Date(year,monthIndex,1);
  if(Number.isNaN(first.getTime()))return [];
  const mondayOffset=(first.getDay()+6)%7;
  const gridStart=new Date(year,monthIndex,1-mondayOffset);
  return Array.from({length:42},(_,index)=>{
    const date=new Date(gridStart.getFullYear(),gridStart.getMonth(),gridStart.getDate()+index);
    return {date:toYmd(date),day:date.getDate(),inMonth:date.getMonth()===monthIndex};
  });
}

export function shiftMeetingMonth(month:string,delta:number){
  const match=/^(\d{4})-(\d{2})$/.exec(month||"");
  if(!match)return month;
  const date=new Date(Number(match[1]),Number(match[2])-1+delta,1);
  return `${date.getFullYear()}-${pad(date.getMonth()+1)}`;
}

export function formatMeetingMonthLabel(month:string){
  const match=/^(\d{4})-(\d{2})$/.exec(month||"");
  return match?`${match[1]}年${Number(match[2])}月`:month;
}

export function meetingDates(meeting:Pick<Meeting,"date_start"|"date_end"|"precision">,limit=120){
  const start=parseYmd(meeting.date_start);
  if(!start)return [];
  if(meeting.precision!=="range"||!meeting.date_end)return [meeting.date_start];
  const end=parseYmd(meeting.date_end);
  if(!end||end<start)return [meeting.date_start];
  const dates:string[]=[];
  for(let index=0;index<limit;index+=1){
    const date=new Date(start.getFullYear(),start.getMonth(),start.getDate()+index);
    if(date>end)break;
    dates.push(toYmd(date));
  }
  return dates;
}

export function meetingOccursOn(meeting:Pick<Meeting,"date_start"|"date_end"|"precision">,date:string){
  return meetingDates(meeting).includes(date);
}

export const statusOrder=["confirmed","scheduled","arranged","proposed","reported","unconfirmed","held","postponed","canceled","denied","lapsed"];
export const meetingStatusMeta=(status:string)=>({
  confirmed:{label:"已确认",tone:"confirmed"},scheduled:{label:"日期已定",tone:"confirmed"},arranged:{label:"已安排",tone:"arranged"},
  proposed:{label:"已预告",tone:"proposed"},reported:{label:"媒体报道",tone:"reported"},unconfirmed:{label:"暂无确认日期",tone:"reported"},
  held:{label:"已举行",tone:"inactive"},postponed:{label:"已推迟",tone:"warning"},canceled:{label:"已取消",tone:"inactive"},
  denied:{label:"已否认",tone:"inactive"},lapsed:{label:"过期未证实",tone:"warning"},
} as Record<string,{label:string;tone:string}>)[status]||{label:status||"状态未知",tone:"reported"};

export function meetingDateLabel(meeting:Pick<Meeting,"date_start"|"date_end"|"precision"|"date_raw">){
  if(!meeting.date_start)return meeting.date_raw||"日期待定";
  if(meeting.precision==="month")return `${meeting.date_start.slice(0,7)}（月内）`;
  if(meeting.precision==="week")return `${meeting.date_start} 起（当周）`;
  if(meeting.precision==="range"&&meeting.date_end)return `${meeting.date_start}—${meeting.date_end}`;
  return meeting.date_start;
}

export const meetingIsMapVisible=(meeting:Meeting)=>!meeting.inactive&&!meeting.non_meeting&&meeting.points.length>0;

export function meetingPersonLabel(person:Pick<MeetingPerson,"name"|"role">){
  return `${person.name||"人物信息待补充"}（${person.role||"职务待补充"}）`;
}

export function sortMeetings(rows:Meeting[]){
  return [...rows].sort((left,right)=>(left.date_start||"9999-12-31").localeCompare(right.date_start||"9999-12-31")||statusOrder.indexOf(left.status)-statusOrder.indexOf(right.status)||left.id.localeCompare(right.id));
}
