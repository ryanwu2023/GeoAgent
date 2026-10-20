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

# 标题发现使用“主体/地理锚点 + 冲突领域”二维门控，避免把伊朗体育、
# 以色列文化等同名实体新闻带入地缘主题。正文抓取后再以全文复核一次。
USIRAN_STRONG = re.compile(
    r'美伊(?:冲突|局势|战争|谈判|关系)|美以伊|霍尔木兹|'
    r'(?:胡塞|安萨尔真主).{0,14}(?:沙特|也门|红海|曼德)|'
    r'(?:沙特|也门|红海|曼德).{0,14}(?:胡塞|安萨尔真主)|'
    r'(?:伊朗|德黑兰|革命卫队).{0,14}(?:美国|美军|以色列|以军)|'
    r'(?:美国|美军|以色列|以军).{0,14}(?:伊朗|德黑兰|革命卫队)|'
    r'(?:以色列|以军).{0,14}(?:黎巴嫩|真主党)|'
    r'(?:黎巴嫩|真主党).{0,14}(?:以色列|以军)')
# 美国、特朗普、以色列本身不是主题锚点；否则俄乌报道或西岸一般新闻会因
# 正文顺带出现“美国/冲突/协议”而误入美伊主题。
USIRAN_ACTOR = re.compile(r'伊朗|德黑兰|革命卫队|哈梅内伊|佩泽希齐扬|胡塞|安萨尔真主|沙特|也门|真主党|伊拉克民兵')
USIRAN_DOMAIN = re.compile(r'冲突|战争|袭击|打击|空袭|导弹|无人机|拦截|部署|增援|战备|停火|谈判|斡旋|协议|制裁|次级制裁|关税|豁免|格雷厄姆法案|核问题|核设施|航运|商船|油轮|船只|通行|护航|封锁|水雷|地雷|红海|曼德|波斯湾|阿曼湾|霍尔木兹|油气设施|机场')

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

def bindings(title, summary=''):
    text=' '.join((title,summary))
    topics=[];dimensions={}
    # 主题归属以标题为准；正文只帮助判维度。这样可避免俄乌正文中出现
    # “格雷厄姆制裁俄罗斯和伊朗法案”时把整篇俄乌报道重复归入美伊。
    if USIRAN_STRONG.search(title) or (USIRAN_ACTOR.search(title) and USIRAN_DOMAIN.search(title)):
        topics.append('usiran')
        if re.search('谈判|协议|外交|停火|斡旋|提议|方案',text): dimensions['usiran']='diplomacy'
        elif re.search('航运|商船|油轮|船只|通行|护航|封锁|水雷|地雷|红海|曼德|波斯湾|阿曼湾|霍尔木兹',text): dimensions['usiran']='financial'
        # The shared schema deliberately has no generic ``military`` bucket.
        # Mixed regional reporting belongs in ``regional`` unless the article
        # supplies enough actor detail for a narrower readiness dimension.
        elif re.search('美军|美国军方|五角大楼|中央司令部',text): dimensions['usiran']='us_readiness'
        elif re.search('伊朗军方|伊朗武装|革命卫队|伊朗导弹|伊朗无人机',text): dimensions['usiran']='iran_readiness'
        elif re.search('部署|增援|战备|导弹|无人机|空袭|打击|袭击|拦截',text): dimensions['usiran']='regional'
        else: dimensions['usiran']='regional'
    if re.search('俄乌|乌克兰|俄罗斯|俄军|乌军|基辅|泽连斯基|普京',title):
        topics.append('ukraine');dimensions['ukraine']='diplomacy' if re.search('谈判|协议|外交|停火',text) else 'frontline'
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
    # 词表升级后重算累积档，避免旧口径污染永久残留在快照中。
    cleaned={}
    for rid,r in records.items():
        topics,dimensions=bindings(r.get('title',''),r.get('summary',''))
        if topics:
            r['topics']=topics;r['dimensions']=dimensions;cleaned[rid]=r
    records=cleaned
    errors=[];captured=[]
    def fetch(item):
        url,meta=item
        try:
            source=read(url);title,body,published=parse_article(source)
            uid=hashlib.sha256(url.encode()).hexdigest()[:24]
            (run/(uid+'.html')).write_text(source,encoding='utf-8')
            topics,dimensions=bindings(title,body)
            if not topics:
                return None,None
            record={'id':uid,'title':title,'summary':body,'title_zh':title,'summary_zh':body,'link':url,'published':published,'fetched_at':stamp.isoformat(),'source_id':'xinhua-zh','publisher':'新华网','tier':'T2','topics':topics,'dimensions':dimensions,'nature':'媒体报道','verification':'原文已采集，报道内容待核验','discovery':meta['discovery'],'origin_id':'xinhua:'+hashlib.sha256((title+published[:10]).encode()).hexdigest()[:24]}
            return record,None
        except Exception as exc:return None,{'url':url,'error':type(exc).__name__+': '+str(exc)[:180]}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for record,error in pool.map(fetch,candidates.items()):
            if error:errors.append(error)
            elif record:captured.append(record)
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
    assert bindings('伊朗男排击败对手')[0]==[]
    assert bindings('胡塞武装袭击沙特机场')[1]['usiran']=='regional'
    assert bindings('霍尔木兹商船通行受阻')[1]['usiran']=='financial'
    assert bindings('美军向伊朗周边增援部署')[1]['usiran']=='us_readiness'
    assert bindings('伊朗革命卫队举行无人机演习')[1]['usiran']=='iran_readiness'
    assert bindings('俄军袭击乌克兰多地')[0]==['ukraine']
    try:parse_article('<html>加载失败</html>')
    except ValueError:pass
    else:raise AssertionError('不得把空页面计为成功')
    print('新华中文采集自检通过')
    return 0

if __name__=='__main__':sys.exit(selftest() if '--selftest' in sys.argv else main())
