"""Download public FRED observations into the existing geo.v6 adapter contract."""
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import httpx
import inspect
import sys
from urllib.request import getproxies

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.paths import collector_data_root

ROOT = collector_data_root() / 'market-context-monitor' / 'output'
SERIES = [('BAMLH0A0HYM2','hy_oas','美国高收益债期权调整利差','基点',100),
          ('BAMLC0A0CM','ig_oas','美国投资级债期权调整利差','基点',100),
          ('T10YIE','breakeven10_fred','美国十年期盈亏平衡通胀率','%',1),
          ('DEXUSEU','eurusd','欧元兑美元','美元/欧元',1)]

def fetch(spec):
    code, series, name, unit, scale = spec
    proxy=getproxies().get('https') or getproxies().get('http')
    options={("proxy" if "proxy" in inspect.signature(httpx.get).parameters else "proxies"):proxy} if proxy else {}
    response = httpx.get('https://fred.stlouisfed.org/graph/fredgraph.csv', params={'id':code,'cosd':'2025-01-01'}, follow_redirects=True, timeout=45, **options)
    response.raise_for_status()
    rows=list(csv.DictReader(io.StringIO(response.text)))
    history=[{'date':r['observation_date'],'value':float(r[code])*scale if r[code] not in ('','.','NA') else None} for r in rows]
    if not history: raise ValueError('没有观测：'+code)
    ROOT.mkdir(parents=True,exist_ok=True)
    (ROOT/(code+'.csv')).write_bytes(response.content)
    stamp=datetime.now(timezone.utc).isoformat()
    license_note='ICE 指数数据仅用于内部研究，公开分发需核对授权。' if code.startswith('BAML') else '来源及口径见 FRED 序列说明。'
    record={'id':series,'title':name+' · 官方历史序列','summary':license_note+' 市场背景数据，不归因于单一冲突。','url':'https://fred.stlouisfed.org/series/'+code,'source_name':'圣路易斯联储经济数据库','fetched_at':stamp,'topics':[],'dimensions':{},'nature':'市场观测','verification':'来源已定位，未完成交叉验证'}
    obs={'id':series,'series':series,'name':name,'unit':unit,'frequency':'日频（交易日）','scope':'美国市场背景' if series!='eurusd' else '欧元兑美元现汇','method':'FRED 原始序列；利差百分比乘以100换算基点' if scale==100 else 'FRED 原始序列','boundary':license_note,'history':history,'source_ids':[series],'shared_market':True,'bucket':'rates' if series=='breakeven10_fred' else 'credit' if scale==100 else 'fx','dimension':'financial'}
    return record,obs

if __name__=='__main__':
    # Commit the bundle only when all sources succeed, preserving the previous successful bundle on failure.
    with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(fetch,SERIES))
    payload={'schema_version':'geo.v6/1','name':'市场传导补充数据','as_of':datetime.now(timezone.utc).isoformat(),'records':[r for r,o in results],'observations':[o for r,o in results]}
    ROOT.mkdir(parents=True,exist_ok=True)
    target=ROOT/'geo-v6.json'
    tmp=target.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.replace(target)
    print(json.dumps({o['series']:len(o['history']) for r,o in results}))
