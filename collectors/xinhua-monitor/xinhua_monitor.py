"""新华中文国际列表 + 正文；自动发现与用户补采分别记录。"""
import concurrent.futures
from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import urllib.request
from urllib.parse import urljoin, urlsplit

ROOT=Path(__file__).resolve().parent
LIST='https://www.news.cn/world/jsxw/index.html'
TARGET='https://www.news.cn/20260920/c335a9f5a0b14dfda0656203402aff32/c.html'
TARGET_TITLE='美媒：美军不会对胡塞武装发动进攻性打击'

def read(url):
    if urlsplit(url).hostname!='www.news.cn':raise ValueError('仅允许新华网正文')
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'GeoResearch/1.0'}),timeout=25) as r:
        return r.read().decode('utf-8')

def clean(value):
    return html.unescape(re.sub('<[^>]+>','',value)).strip()

def parse_article(source):
    title=re.search(r'<h1[^>]*>(.*?)</h1>',source,re.S)
    body=re.search(r'<span[^>]+id=["\']detailContent["\'][^>]*>(.*?)</span>\s*<div',source,re.S)
    if not title or not body:raise ValueError('标题或正文结构未识别')
    paragraphs=[clean(p) for p in re.findall(r'<p[^>]*>(.*?)</p>',body[1],re.S)]
    timestamp=re.search(r'20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}',source)
    if not paragraphs or not timestamp:raise ValueError('正文或发布时间为空')
    published=datetime.fromisoformat(timestamp[0]).replace(tzinfo=timezone(timedelta(hours=8))).isoformat()
    return clean(title[1]),'\n'.join(paragraphs),published

def bindings(title):
    topics=[];dimensions={}
    if re.search('伊朗|美伊|胡塞|霍尔木兹|沙特|红海|以色列|也门|卡塔尔',title):
        topics.append('usiran');dimensions['usiran']='diplomacy' if re.search('谈判|协议|外交|停火',title) else 'regional'
    if re.search('俄乌|乌克兰|俄罗斯|俄军|乌军|基辅|泽连斯基|普京',title):
        topics.append('ukraine');dimensions['ukraine']='diplomacy' if re.search('谈判|协议|外交|停火',title) else 'frontline'
    return topics,dimensions

def main():
    stamp=datetime.now(timezone.utc);out=ROOT/'output';out.mkdir(parents=True,exist_ok=True)
    run=out/stamp.strftime('%Y%m%dT%H%M%S');run.mkdir()
    listing=read(LIST);(run/'listing.html').write_text(listing,encoding='utf-8')
    ds=re.search(r'id="content-list"[^>]+data="datasource:([a-f0-9]+)"',listing)
    if not ds:raise ValueError('列表数据源未识别')
    list_url=urljoin(LIST,'ds_'+ds[1]+'.json');raw=read(list_url);(run/'listing.json').write_text(raw,encoding='utf-8')
    rows=json.loads(raw)['datasource'];cutoff=(stamp-timedelta(days=7)).strftime('%Y-%m-%d')
    candidates={}
    for row in rows:
        title=clean(row.get('showTitle') or row.get('title',''))
        if row.get('publishTime','')[:10]>=cutoff and bindings(title)[0]:
            url=urljoin(LIST,row['publishUrl']).replace('http://','https://')
            if urlsplit(url).hostname=='www.news.cn':candidates[url]={'discovery':'中文列表自动发现','title':title}
    discovered=[u for u,r in candidates.items() if r['title']==TARGET_TITLE]
    candidates.setdefault(TARGET,{'discovery':'用户指定链接补采','title':TARGET_TITLE})
    prior=out/'geo-v6.json';records={r['id']:r for r in json.loads(prior.read_text(encoding='utf-8'))['records']} if prior.exists() else {}
    errors=[];captured=[]
    def fetch(item):
        url,meta=item
        try:
            source=read(url);title,body,published=parse_article(source)
            uid=hashlib.sha256(url.encode()).hexdigest()[:24]
            (run/(uid+'.html')).write_text(source,encoding='utf-8')
            topics,dimensions=bindings(title)
            record={'id':uid,'title':title,'summary':body,'title_zh':title,'summary_zh':body,'link':url,'published':published,'fetched_at':stamp.isoformat(),'source_id':'xinhua-zh','publisher':'新华网','tier':'T2','topics':topics,'dimensions':dimensions,'nature':'媒体报道','verification':'原文已采集，报道内容待核验','discovery':meta['discovery'],'origin_id':'xinhua:'+hashlib.sha256((title+published[:10]).encode()).hexdigest()[:24]}
            return record,None
        except Exception as exc:return None,{'url':url,'error':type(exc).__name__+': '+str(exc)[:180]}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for record,error in pool.map(fetch,candidates.items()):
            if error:errors.append(error)
            else:captured.append(record)
    # Same-title same-day mirrors share one evidence record, preserving aliases in the archive.
    by_origin={r['origin_id']:r for r in records.values()}
    for r in captured:
        existing=by_origin.get(r['origin_id'])
        if existing:
            r['aliases']=sorted(set(existing.get('aliases',[])+[existing['link'],r['link']]))
        by_origin[r['origin_id']]=r
    payload={'schema_version':'geo.v6/1','name':'新华网中文国际动态','as_of':stamp.isoformat(),'records':list(by_origin.values()),'observations':[]}
    (run/'geo-v6.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    temp=out/'geo-v6.tmp';temp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(prior)
    report={'run_at':stamp.isoformat(),'listing_url':list_url,'listed':len(rows),'candidate_count':len(candidates),'captured':len(captured),'errors':errors,'target_discovered_urls':discovered,'target_direct_captured':any(r['link']==TARGET for r in captured),'target_title_captured':any(r['title']==TARGET_TITLE for r in captured),'archive':str(run)}
    (out/'_collection_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'LATEST.md').write_text('# 新华中文采集\n\n'+json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)
    return 0 if captured else 1

def selftest():
    source='<h1><span>伊朗谈判</span></h1>2026-09-20 23:00:07<span id="detailContent"><p>报道正文</p></span><div>'
    title,body,published=parse_article(source)
    assert title=='伊朗谈判' and body=='报道正文' and published.endswith('+08:00')
    assert bindings(title)==(['usiran'],{'usiran':'diplomacy'})
    assert bindings('俄军袭击乌克兰多地')[0]==['ukraine']
    try:parse_article('<html>加载失败</html>')
    except ValueError:pass
    else:raise AssertionError('不得把空页面计为成功')
    print('新华中文采集自检通过')
    return 0

if __name__=='__main__':sys.exit(selftest() if '--selftest' in sys.argv else main())
