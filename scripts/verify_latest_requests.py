"""Rendered checks for fixed map markers, topic metrics, Huatai source and movement colors."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright, expect

OUT=Path(__file__).resolve().parents[1]/'docs'/'qa-20260921-latest'
OUT.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,channel='msedge')
    page=browser.new_page(viewport={'width':1440,'height':1000},locale='zh-CN')
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:8000/')
    page.wait_for_function("document.querySelector('.insight-stats b')?.textContent !== '0'",timeout=90000)
    page.locator('.maplibregl-canvas').wait_for();page.wait_for_timeout(1500)
    expect(page.get_by_text('点位大小固定；数字为报道数，同地点错开展示')).to_be_visible()
    page.screenshot(path=str(OUT/'fixed-map-points.png'))
    page.get_by_role('button',name='量化指标',exact=True).click()
    us=set(page.locator('.metric-label').all_text_contents())
    expect(page.get_by_text('红色表示高于前值，绿色表示低于前值；只表示数值方向，不代表资产利好或利空。')).to_be_visible()
    assert page.locator('.metric-delta.up').count()>0 and page.locator('.metric-delta.down').count()>0
    page.screenshot(path=str(OUT/'usiran-metrics.png'))
    page.get_by_role('button',name='俄乌冲突',exact=True).click()
    page.wait_for_function("!document.querySelector('.insight-loading')")
    page.get_by_role('button',name='量化指标',exact=True).click()
    ua=set(page.locator('.metric-label').all_text_contents())
    assert us!=ua and '霍尔木兹海峡 日通行艘次（全部船型）' in us and '欧洲天然气 TTF 近月' in ua
    page.screenshot(path=str(OUT/'ukraine-metrics.png'))
    page.get_by_role('button',name='美伊局势',exact=True).click()
    page.wait_for_function("!document.querySelector('.insight-loading')")
    page.get_by_role('button',name='数据源',exact=True).click()
    expect(page.get_by_text('华泰政策 · 中东简报（高可信研究资料）',exact=True)).to_be_visible()
    page.screenshot(path=str(OUT/'huatai-source.png'))
    result={'usiran_metric_count':len(us),'ukraine_metric_count':len(ua),'usiran_only':sorted(us-ua),'ukraine_only':sorted(ua-us),'page_errors':errors}
    (OUT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    browser.close()
assert not errors,errors
print(json.dumps(result,ensure_ascii=False))
