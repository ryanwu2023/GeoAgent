const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const loadTs=require('./load_ts.cjs');

const {buildMeetingMonth,formatMeetingMonthLabel,meetingDateLabel,meetingDates,meetingIsMapVisible,meetingOccursOn,meetingPersonLabel,meetingStatusMeta,shiftMeetingMonth,sortMeetings}=loadTs('src/meeting_calendar.ts');

function meeting(id,date_start,status='confirmed'){
  return {id,date_start,date_end:'',date_raw:'',precision:'day',status,inactive:false,non_meeting:false,points:[{lon:1,lat:2,label:'测试地点'}]};
}

test('meeting calendar preserves date precision and sorts future events',()=>{
  assert.equal(meetingDateLabel({...meeting('m','2026-11-01'),precision:'month'}),'2026-11（月内）');
  assert.equal(meetingDateLabel({...meeting('w','2026-11-02'),precision:'week'}),'2026-11-02 起（当周）');
  assert.deepEqual(sortMeetings([meeting('b','2026-11-03'),meeting('a','2026-11-01')]).map(item=>item.id),['a','b']);
  assert.equal(meetingStatusMeta('postponed').label,'已推迟');
});

test('meeting month grid starts on Monday and always contains six complete weeks',()=>{
  const october=buildMeetingMonth('2026-10');
  assert.equal(october.length,42);
  assert.deepEqual(october[0],{date:'2026-09-28',day:28,inMonth:false});
  assert.deepEqual(october[3],{date:'2026-10-01',day:1,inMonth:true});
  assert.deepEqual(october.at(-1),{date:'2026-11-08',day:8,inMonth:false});
  assert.equal(formatMeetingMonthLabel('2026-10'),'2026年10月');
  assert.equal(shiftMeetingMonth('2026-12',1),'2027-01');
  assert.equal(shiftMeetingMonth('2026-01',-1),'2025-12');
});

test('meeting dates expand bounded ranges without inventing dates for undated records',()=>{
  const ranged={...meeting('range','2026-10-25'),date_end:'2026-10-27',precision:'range'};
  assert.deepEqual(meetingDates(ranged),['2026-10-25','2026-10-26','2026-10-27']);
  assert.equal(meetingOccursOn(ranged,'2026-10-26'),true);
  assert.equal(meetingOccursOn(ranged,'2026-10-28'),false);
  assert.deepEqual(meetingDates({...meeting('unknown',''),date_raw:'待确认'}),[]);
  assert.equal(meetingDates({...meeting('long','2026-01-01'),date_end:'2027-01-01',precision:'range'}).length,120);
});

test('meeting ledger renders Chinese name and role as one readable label',()=>{
  assert.equal(meetingPersonLabel({name:'普京',role:'俄罗斯总统'}),'普京（俄罗斯总统）');
  assert.equal(meetingPersonLabel({name:'',role:''}),'人物信息待补充（职务待补充）');
});

test('only active real meetings with controlled points appear on the meeting map layer',()=>{
  assert.equal(meetingIsMapVisible(meeting('active','2026-11-01')),true);
  assert.equal(meetingIsMapVisible({...meeting('inactive','2026-11-01'),inactive:true}),false);
  assert.equal(meetingIsMapVisible({...meeting('election','2026-11-01'),non_meeting:true}),false);
  assert.equal(meetingIsMapVisible({...meeting('unknown','2026-11-01'),points:[]}),false);
});

test('meeting UI aborts stale requests and map identifies planned locations separately',()=>{
  const calendar=fs.readFileSync('src/MeetingCalendar.tsx','utf8');
  const map=fs.readFileSync('src/MapView.tsx','utf8');
  assert.match(calendar,/new AbortController\(\)/);
  assert.match(calendar,/controller\.abort\(\)/);
  assert.match(calendar,/计划会晤地点，不代表实时位置/);
  assert.match(map,/id:'planned-meetings'/);
  assert.match(map,/meeting-layer-toggle/);
});

test('meeting UI exposes month navigation and the four core schedule fields',()=>{
  const calendar=fs.readFileSync('src/MeetingCalendar.tsx','utf8');
  assert.match(calendar,/aria-label="上一个月"/);
  assert.match(calendar,/aria-label="下一个月"/);
  assert.match(calendar,/meeting-month-grid/);
  assert.match(calendar,/相关人物/);
  assert.match(calendar,/会晤地点/);
  assert.match(calendar,/日程时间/);
  assert.match(calendar,/全部外交日程/);
  assert.match(calendar,/topic:calendarTopic/);
});

test('meeting UI exports the current topic, filters, and selected calendar date',()=>{
  const calendar=fs.readFileSync('src/MeetingCalendar.tsx','utf8');
  const api=fs.readFileSync('src/api.ts','utf8');
  assert.match(calendar,/apiMeetingExport/);
  assert.match(calendar,/selectedDate\?\{start:selectedDate,end:selectedDate\}:\{\}/);
  assert.match(calendar,/status,person,include_inactive:includeInactive/);
  assert.match(calendar,/导出 Excel/);
  assert.match(calendar,/URL\.createObjectURL\(blob\)/);
  assert.match(calendar,/URL\.revokeObjectURL\(url\)/);
  assert.match(calendar,/!visibleRows\.length/);
  assert.match(api,/\/api\/meetings\/export\.xlsx/);
  assert.match(api,/filename\\\*=UTF-8/);
});
