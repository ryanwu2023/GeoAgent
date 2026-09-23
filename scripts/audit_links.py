"""Visit every unique public HTTP URL in a snapshot; never mark accessibility as truth."""
import asyncio
from collections import Counter, defaultdict
from datetime import datetime,timezone
import ipaddress,json,re,socket,sys,inspect
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import getproxies
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.store import snapshots,get_snapshot,data_dir

async def main():
    sid=sys.argv[1] if len(sys.argv)>1 else snapshots()[0]['id']
    records=get_snapshot(sid)['records'];urls=sorted({r['url'] for r in records if r.get('url','').startswith(('http://','https://'))})
    folder=data_dir()/'link-audits';folder.mkdir(exist_ok=True)
    output=folder/(sid+'.jsonl');done={}
    if output.exists():
        for line in output.read_text(encoding='utf-8').splitlines():
            try:item=json.loads(line);done[item['url']]=item
            except (ValueError,KeyError):pass
    if len(sys.argv)>2:
        seed=folder/(sys.argv[2]+'.jsonl')
        if seed.exists():
            wanted=set(urls)
            with output.open('a',encoding='utf-8') as f:
                for line in seed.read_text(encoding='utf-8').splitlines():
                    item=json.loads(line)
                    if item['url'] in wanted and item['url'] not in done:
                        item['reused_from_snapshot']=sys.argv[2]
                        done[item['url']]=item;f.write(json.dumps(item,ensure_ascii=False)+'\n')
    hosts=defaultdict(lambda:asyncio.Semaphore(6));global_limit=asyncio.Semaphore(32)
    proxy=getproxies().get('https') or getproxies().get('http')
    async with httpx.AsyncClient(**({("proxy" if "proxy" in inspect.signature(httpx.AsyncClient).parameters else "proxies"):proxy} if proxy else {}),timeout=12,follow_redirects=False,headers={'User-Agent':'GeoResearchLinkAudit/1.0'},limits=httpx.Limits(max_connections=32)) as client:
        async def visit(url):
            if url in done:return
            item={'url':url,'checked_at':datetime.now(timezone.utc).isoformat()}
            async with hosts[urlsplit(url).netloc],global_limit:
                try:
                    target=url
                    for _ in range(6):
                        parts=urlsplit(target)
                        if parts.scheme not in ('http','https') or parts.username or parts.password:raise ValueError('non_public_url')
                        addresses=await asyncio.get_running_loop().getaddrinfo(parts.hostname,parts.port or 443,type=socket.SOCK_STREAM)
                        if any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):raise ValueError('non_public_url')
                        async with client.stream('GET',target) as response:
                            item.update(http_status=response.status_code,final_url=str(response.url))
                            if response.is_redirect:
                                target=str(response.url.join(response.headers['location']));continue
                            body=bytearray()
                            async for chunk in response.aiter_bytes():
                                body.extend(chunk)
                                if len(body)>=160000:break
                            text=body.decode(response.encoding or 'utf-8',errors='replace')
                            title=re.search(r'<title[^>]*>(.*?)</title>',text,re.I|re.S)
                            item['page_title']=re.sub(r'\s+',' ',title[1]).strip()[:300] if title else ''
                            challenge=bool(re.search(r'just a moment|access denied|verify you are human|captcha',item['page_title'],re.I))
                            item['status']='访问受限' if response.status_code in (401,403,429) or challenge else '链接失效' if response.status_code in (404,410) else '已访问，内容待核对' if 200<=response.status_code<300 else '访问异常'
                            break
                    else:item['status']='重定向过多'
                except Exception as exc:item.update(status='访问失败',error=type(exc).__name__)
            done[url]=item
            with output.open('a',encoding='utf-8') as f:f.write(json.dumps(item,ensure_ascii=False)+'\n')
            if len(done)%100==0:print(f'{len(done)}/{len(urls)}',flush=True)
        await asyncio.gather(*(visit(url) for url in urls))
    summary={'snapshot_id':sid,'unique_urls':len(urls),'checked':len(done),'statuses':dict(Counter(r['status'] for r in done.values()))}
    (folder/(sid+'-summary.json')).write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':asyncio.run(main())
