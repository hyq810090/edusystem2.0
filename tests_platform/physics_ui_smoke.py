"""Browser acceptance in an isolated platform, without model calls."""
from pathlib import Path
import json,os,time
import httpx
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]
base=os.environ.get('MATHBANK_TEST_URL','http://127.0.0.1:3012')
data=Path(os.environ.get('MATHBANK_TEST_DATA',root/'test-results/physics-ui-data'))
password=(data/'initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：',1)[1]
with httpx.Client(base_url=base,trust_env=False,timeout=45) as c:
    assert c.post('/platform/api/login',json={'username':'admin','password':password}).is_success
    c.headers['X-Local-Token']=c.get('/platform/api/me').json()['csrf']
    metadata=json.loads((root/'resources/textbooks/高中物理人教版.metadata.json').read_text(encoding='utf-8'))
    result=c.post('/platform/api/admin/textbooks',json={'subject':'物理','stage':'高中','name':'物理界面验收','metadata':metadata});assert result.is_success,result.text
    sid=c.post('/platform/api/spaces',json={'name':'物理界面验收 '+str(time.time_ns()),'textbook_id':result.json()['id']}).json()['id']
    lane=sid
    q=c.post(f'/bank/{lane}/api/questions',data={'content':r'实验测得电流 $I=2.00\,\mathrm{mA}$。请画出测量电路，并说明误差来源。','question_type':'experiment','difficulty':'normal','category_compulsory':'必修 第三册','category_chapter':'第十一章 电路及其应用','category_knowledge':'3. 实验：导体电阻率的测量','answer_markdown':r'保持电路连接和量程正确，读数带单位。'});assert q.is_success,q.text
    q=q.json()['question']
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(base);page.locator('#loginForm [name=username]').fill('admin');page.locator('#loginForm [name=password]').fill(password);page.get_by_role('button',name='登录',exact=True).click();page.wait_for_function("document.documentElement.dataset.accountReady==='true'")
    page.goto(base+f'/workbench/{lane}');page.wait_for_load_state('networkidle')
    page.get_by_role('button',name='手动录入题目',exact=True).click()
    assert page.locator('#editorTitle').inner_text()=='录入新物理题'
    page.locator('[data-editor-panel-target="classification"]').click()
    options=page.locator('#editQType option').all_text_contents()
    assert all(label in options for label in ['实验题','计算题','简答题']),options
    page.locator('#editQType').select_option('experiment')
    page.screenshot(path=str(root/'test-results/physics-editor.png'),full_page=True,animations='disabled')
    page.keyboard.press('Escape')
    # Changing only type might prompt to keep edits; reload this empty test editor safely.
    page.goto(base+f'/workbench/{lane}');page.wait_for_load_state('networkidle');page.locator('#appNavPaper').click()
    page.evaluate('(q)=>{PaperStore.questionsMap[q.id]=q;addToCart(q.id);}',q)
    page.wait_for_function("document.querySelector('.paper-page-footer')?.textContent.includes('物理')")
    assert page.evaluate('PaperStore.meta.paper_type')=='exam'
    page.locator('#togglePaperMetaBtn').click()
    assert '19题高考' not in page.locator('#paperMetaType').inner_text()
    assert '高中物理' in page.locator('#paperMetaTitle').input_value()
    assert '实验题' in page.locator('.paper-preview-column').inner_text()
    assert '物 理' in page.locator('.paper-preview-column').inner_text()
    assert '数 学' not in page.locator('.paper-preview-column').inner_text()
    assert page.locator('.paper-preview-column .katex').count()>0
    page.screenshot(path=str(root/'test-results/physics-paper.png'),full_page=True,animations='disabled')
    page.evaluate('toggleDarkMode()');page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path=str(root/'test-results/physics-mobile.png'),full_page=True,animations='disabled')
    page.goto(base+'/workbench/1');page.wait_for_load_state('networkidle');page.set_viewport_size({'width':1440,'height':1000})
    page.locator('#appNavPaper').click();assert page.evaluate('PaperStore.meta.paper_type')=='exam_19'
    assert '数学' in page.evaluate('PaperStore.meta.title')
    assert not errors,errors
    browser.close()
print('Physics UI: configured types, editor, exam defaults, units preview, subject footer, mobile/dark and math isolation passed.')
