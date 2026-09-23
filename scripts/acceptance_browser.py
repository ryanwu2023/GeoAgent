"""Local rendered UI acceptance. Uses installed Edge, never the user's profile."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright, expect

OUT = Path(__file__).resolve().parents[1] / 'docs' / 'qa-20260920'
OUT.mkdir(exist_ok=True)
checks, errors = [], []

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel='msedge')
    page = browser.new_page(viewport={'width':1440,'height':1000}, locale='zh-CN')
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.set_default_timeout(30000)
    def shot(name):
        page.screenshot(path=str(OUT / (name+'.png')))
    def tab(name):
        page.get_by_role('button',name=name,exact=True).click()
    def loaded():
        page.wait_for_function("!document.querySelector('.insight-loading') && document.querySelector('.insight-stats b')?.textContent !== '0'",timeout=60000)
    page.goto('http://127.0.0.1:8000/')
    page.locator('.maplibregl-canvas').wait_for();loaded();shot('desktop-after')
    # Map layer controls and empty state.
    boxes=page.locator('.map-legend input')
    for box in boxes.all(): box.uncheck()
    expect(page.get_by_text('当前分类暂无可定位动态，可在证据台账查看全部报道')).to_be_visible()
    for box in boxes.all(): box.check()
    checks.append('地图图层开关与无点位提示')
    tab('证据台账');page.locator('.evidence-row').first.click()
    expect(page.get_by_text('发布时间',exact=True)).to_be_visible()
    expect(page.get_by_text('采集时间',exact=True)).to_be_visible()
    shot('evidence-after');page.keyboard.press('Escape')
    expect(page.get_by_role('button',name='关闭证据详情')).to_have_count(0)
    checks.append('新闻详情时间字段、Esc关闭')
    tab('量化指标');page.get_by_role('textbox',name='搜索量化指标').fill('曼德')
    card=page.locator('.metric-card').filter(has=page.get_by_text('曼德海峡 日通行艘次（全部船型）',exact=True))
    slider=card.get_by_role('slider');old=slider.get_attribute('aria-valuetext')
    slider.press('ArrowLeft');assert slider.get_attribute('aria-valuetext')!=old
    slider.click(position={'x':150,'y':70});shot('chart-interaction')
    card.get_by_role('button',name='全部',exact=True).click()
    assert int(slider.get_attribute('aria-valuemax'))>180
    card.get_by_role('button',name='近60期',exact=True).click()
    card.get_by_role('button',name='指标含义与来源').click()
    expect(page.get_by_role('heading',name='指标含义与来源',exact=True)).to_be_visible()
    shot('metric-guide');page.keyboard.press('Escape')
    checks.append('指标搜索、全部历史与近60期、鼠标点选及方向键、指标含义与来源')
    page.get_by_role('textbox',name='搜索量化指标').fill('无此指标验收')
    expect(page.get_by_text('没有匹配指标，请更换关键词或分类。')).to_be_visible()
    page.get_by_role('textbox',name='搜索量化指标').fill('')
    checks.append('无匹配指标空状态')
    tab('资产关联');shot('assets-after')
    page.locator('.priority-metric').first.click();expect(page.get_by_role('heading',name='指标含义与来源',exact=True)).to_be_visible();page.keyboard.press('Escape')
    page.locator('.asset-table tbody tr').first.click();shot('asset-detail')
    expect(page.get_by_role('heading',name='权益 · 条件传导')).to_be_visible();page.keyboard.press('Escape')
    checks.append('重点指标回溯及资产条件详情')
    tab('俄乌冲突');loaded();tab('资产关联')
    expect(page.get_by_text('俄乌冲突 · 欧洲成本与援助产业链',exact=True)).to_be_visible()
    shot('ukraine-assets');checks.append('俄乌主题切换及独立传导路径')
    tab('情景推演');expect(page.get_by_role('heading',name='情景推演是什么？')).to_be_visible();shot('scenarios-after')
    tab('数据源');expect(page.get_by_role('heading',name='数据源与覆盖')).to_be_visible();shot('sources-after')
    tab('智能问答');expect(page.get_by_role('button',name='发送问题')).to_be_disabled()
    page.get_by_role('textbox',name='研究问题').fill('原油指标变化及关联')
    tab('发送问题');page.locator('.answer-mode').wait_for(timeout=90000);shot('answer')
    checks.append('空问题禁用、现场本地问答返回并展示引用')
    tab('研究简报');loaded();page.locator('.brief-item').first.click()
    expect(page.get_by_role('link',name='导出研究简报')).to_be_visible();shot('brief-after')
    href=page.get_by_role('link',name='导出研究简报').get_attribute('href')
    response=page.request.get("http://127.0.0.1:8000"+href);assert response.ok and '地缘政治研究简报' in response.text()
    checks.append('简报档案阅读与Markdown导出')
    # Invalid range must explain the error, not silently display another range.
    tab('美伊局势');loaded()
    page.get_by_label('开始日期',exact=True).fill('2026-09-21')
    page.get_by_label('结束日期',exact=True).fill('2026-09-01')
    page.get_by_role('alert').wait_for();shot('date-error')
    tab('清除');loaded();expect(page.get_by_role('alert')).to_have_count(0);checks.append('错误日期范围提示及恢复')
    # Fresh mobile page tests the default collapsed layer control.
    mobile=browser.new_page(viewport={'width':390,'height':844},locale='zh-CN')
    mobile.goto('http://127.0.0.1:8000/');mobile.locator('.maplibregl-canvas').wait_for()
    expect(mobile.get_by_role('button',name='事件图层')).to_have_attribute('aria-expanded','false')
    mobile.screenshot(path=str(OUT/'mobile-after.png'))
    mobile.get_by_role('button',name='事件图层').click()
    expect(mobile.locator('.map-legend input').first).to_be_visible()
    mobile.get_by_role('button',name='事件图层').click()
    mobile.get_by_role('button',name='资产关联',exact=True).click()
    mobile.locator('.priority-metric').first.wait_for()
    mobile.screenshot(path=str(OUT/'mobile-assets-after.png'))
    assert mobile.evaluate('document.documentElement.scrollWidth <= innerWidth')
    mobile.locator('.asset-table tbody tr').first.scroll_into_view_if_needed()
    mobile.screenshot(path=str(OUT/'mobile-asset-cards.png'))
    for name in ['量化指标','智能问答','研究简报']:
        mobile.get_by_role('button',name=name,exact=True).click()
        if name=='研究简报':
            mobile.locator('.brief-item').first.wait_for();mobile.locator('.brief-item').first.click()
            mobile.wait_for_function("!document.querySelector('.insight-loading')")
        mobile.screenshot(path=str(OUT/f'mobile-{name}.png'))
        assert mobile.evaluate('document.documentElement.scrollWidth <= innerWidth')
    checks.append('390px窄屏地图、资产卡片、指标、问答、简报无页面横向溢出')
    browser.close()

OUT.joinpath('results.json').write_text(json.dumps({'checks':checks,'page_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
assert not errors, errors
print(json.dumps({'passed':len(checks),'page_errors':errors},ensure_ascii=False))
