"""Real browser recovery with faults injected only into this isolated test tab."""
from pathlib import Path
import time,httpx
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1];base='http://127.0.0.1:3012'
data=root/'test-results/connection-ui-data'
password=(data/'initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：',1)[1]
with httpx.Client(base_url=base,trust_env=False,timeout=30) as c:
    assert c.post('/platform/api/login',json={'username':'admin','password':password}).is_success
    c.headers['X-Local-Token']=c.get('/platform/api/me').json()['csrf']
    sid=c.post('/platform/api/spaces',json={'name':'连接回归 '+str(time.time_ns()),'textbook_id':1}).json()['id']
    lane=sid
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True);page=browser.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(base);page.locator('#loginForm [name=username]').fill('admin');page.locator('#loginForm [name=password]').fill(password);page.get_by_role('button',name='登录',exact=True).click()
    page.wait_for_function("document.documentElement.dataset.accountReady==='true'")
    page.goto(base+f'/workbench/{lane}',wait_until='domcontentloaded')
    indicator=page.locator('#apiStatusIndicator')
    page.wait_for_function("document.getElementById('apiStatusIndicator').dataset.connectionState==='ready'")
    page.evaluate("document.getElementById('editContent').value='尚未保存的题干';document.getElementById('importPaperTitle').value='未保存的导入标题';")
    failures={'left':1,'all':False,'calls':0}
    def route(request):
        if 'page=1&page_size=1' not in request.request.url:return request.continue_()
        failures['calls']+=1
        if failures['all'] or failures['left']:
            failures['left']=max(0,failures['left']-1)
            return request.fulfill(status=500,content_type='application/json',body='{"items":[]}')
        request.continue_()
    page.route('**/api/questions?*',route)
    # Even valid JSON with HTTP 500 must not be mistaken for a healthy bank.
    page.evaluate('void window.checkBankConnection()')
    page.wait_for_function("document.getElementById('apiStatusIndicator').dataset.connectionState==='retrying'")
    page.wait_for_function("document.getElementById('apiStatusIndicator').dataset.connectionState==='ready'")
    assert failures['calls']==2
    failures['all']=True;failures['calls']=0
    assert page.evaluate('window.checkBankConnection()') is False
    assert failures['calls']==4 and '点击重试' in indicator.inner_text()
    failures['all']=False
    indicator.click()
    page.wait_for_function("document.getElementById('apiStatusIndicator').dataset.connectionState==='ready'")
    assert page.locator('#editContent').input_value()=='尚未保存的题干'
    assert page.locator('#importPaperTitle').input_value()=='未保存的导入标题'
    assert not errors,errors
    browser.close()
print('PASS: HTTP 500 rejected; automatic recovery; bounded retries; manual reconnect; unsaved fields retained.')
