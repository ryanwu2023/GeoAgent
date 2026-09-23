from pathlib import Path
import json,time
from playwright.sync_api import sync_playwright
out=Path('docs/qa-20260921');out.mkdir(exist_ok=True)
with sync_playwright() as p:
 b=p.chromium.launch(channel='msedge',headless=True)
 page=b.new_page(viewport={'width':1440,'height':1000});errors=[];requests=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.on('request',lambda r:requests.append(r.url) if '/api/workspace?' in r.url else None)
 page.goto('http://127.0.0.1:8000/')
 page.wait_for_function("document.querySelector('.insight-stats b')?.textContent !== '0' && !document.querySelector('.insight-loading')",timeout=90000)
 page.locator('.maplibregl-canvas').wait_for();page.wait_for_timeout(2500)
 page.screenshot(path=str(out/'map.png'))
 timings={}
 for name in ['量化指标','资产关联','风险监测','证据台账']:
  start=time.perf_counter();page.get_by_role('button',name=name,exact=True).click();page.evaluate('()=>new Promise(requestAnimationFrame)');timings[name]=round((time.perf_counter()-start)*1000)
 page.locator('.evidence-row').first.click();page.get_by_role('heading',name='研究员研判').wait_for();page.screenshot(path=str(out/'review.png'))
 assert page.get_by_role('button',name='确认当前分类').is_disabled()
 result={'menu_ms':timings,'workspace_requests':len(requests),'errors':errors}
 (out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False));b.close()
