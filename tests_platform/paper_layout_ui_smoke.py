"""Isolated browser regression: A/B papers, reload, revalidation, new defaults, failed writes."""
from pathlib import Path
import io, json, time, os
import httpx
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]
base='http://127.0.0.1:3012'
data=Path(os.environ.get('MATHBANK_TEST_DATA', str(root/'test-results/paper-layout-ui-data')))
pw=(data/'initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：',1)[1]
with httpx.Client(base_url=base,trust_env=False,timeout=60) as c:
    assert c.post('/platform/api/login',json={'username':'admin','password':pw}).is_success
    c.headers['X-Local-Token']=c.get('/platform/api/me').json()['csrf']
    sid=c.post('/platform/api/spaces',json={'name':'排版隔离验收 '+str(time.time_ns()),'textbook_id':1}).json()['id']
    lane=sid
    im=Image.new('RGB',(320,160),'white');ImageDraw.Draw(im).line((30,130,160,25,290,130,30,130),fill='black',width=3)
    buf=io.BytesIO();im.save(buf,format='PNG')
    image=c.post(f'/bank/{lane}/api/upload',files={'file':('triangle.png',buf.getvalue(),'image/png')}).json()['file_path']
    q=c.post(f'/bank/{lane}/api/questions',data={'content':'如图，求三角形的面积。\n![]('+image+')','image_paths':json.dumps([image]),'question_type':'detailed_answer','difficulty':'normal','figure_align':'right','figure_align_custom':'true','figure_size':'small'}).json()['question']
    qid=q['id']
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(base);page.locator('#loginForm [name=username]').fill('admin');page.locator('#loginForm [name=password]').fill(pw);page.get_by_role('button',name='登录',exact=True).click();page.wait_for_function("document.documentElement.dataset.accountReady==='true'")
        page.goto(base+f'/workbench/{lane}');page.wait_for_load_state('networkidle');page.locator('#appNavPaper').click()
        page.evaluate('(q)=>{PaperStore.questionsMap[q.id]=q;addToCart(q.id);PaperStore.meta.paper_type="exam";PaperStore.meta.title="A右侧";}',q)
        page.evaluate('savePaperToDb()')
        papers=c.get(f'/bank/{lane}/api/papers').json()['data'];a=next(x['id'] for x in papers if x['title']=='A右侧')
        # Use the rendered image popover to change the current paper and bank default.
        page.locator(f'.paper-preview-column [data-figure-align-qid="{qid}"]').first.click()
        page.get_by_role('button',name='插图位置：题干下方居中',exact=True).click()
        page.evaluate('(id)=>waitForFigureLayoutWrite(id)',qid)
        page.evaluate('(id)=>setFigureSize(id,"large")',qid);page.evaluate('(id)=>waitForFigureLayoutWrite(id)',qid)
        page.evaluate('PaperStore.meta.title="B居中";savePaperToDb()')
        papers=c.get(f'/bank/{lane}/api/papers').json()['data'];b=next(x['id'] for x in papers if x['title']=='B居中')
        def layout():return page.evaluate('(id)=>({align:PaperStore.questionsMap[id].figure_align,size:PaperStore.questionsMap[id].figure_size,cart:PaperStore.cart[0].figure_layout})',qid)
        page.evaluate('(id)=>loadSavedPaper(id)',a)
        assert layout()['align']=='right' and layout()['size']=='small',layout()
        # Entering workspace and saving re-fetch all question records from the bank.
        page.evaluate('renderPaperWorkspace()');assert layout()['align']=='right',layout()
        page.reload();page.wait_for_load_state('networkidle');page.locator('#appNavPaper').click()
        page.wait_for_function('(id)=>PaperStore.questionsMap[id] && !PaperStore.cartQuestionLoad.loading',arg=qid)
        assert layout()['align']=='right',layout()
        page.evaluate('PaperStore.meta.title="A再次保存";savePaperToDb()')
        aid=next(x['id'] for x in c.get(f'/bank/{lane}/api/papers').json()['data'] if x['title']=='A再次保存')
        assert c.get(f'/bank/{lane}/api/papers/{aid}').json()['data']['questions'][0]['question']['figure_align']=='right'
        # Quick saved PDF payload must use A's snapshot even while B is open.
        page.evaluate('(id)=>loadSavedPaper(id)',b);assert layout()['align']=='center'
        payloads=[]
        page.route('**/api/paper/export/pdf',lambda route:(payloads.append(route.request.post_data_json),route.fulfill(status=400,content_type='application/json',body='{"detail":"test export capture"}')))
        page.evaluate('(id)=>quickExportPaperPdf(id)',a)
        assert payloads[-1]['questions'][0]['figure_align']=='right'
        page.unroute('**/api/paper/export/pdf')
        # A failed default write rolls the current paper back too.
        page.route('**/api/questions/*/figure_layout',lambda route:route.fulfill(status=500,content_type='application/json',body='{"detail":"test failure"}'))
        page.evaluate('(id)=>setFigureAlign(id,"bottom_left")',qid);page.evaluate('(id)=>waitForFigureLayoutWrite(id)',qid)
        assert layout()['align']=='center' and layout()['cart']['figure_align']=='center',layout()
        page.unroute('**/api/questions/*/figure_layout')
        # New selection uses bank's latest default, not the previously loaded A snapshot.
        page.evaluate('(id)=>loadSavedPaper(id)',a)
        page.evaluate('(id)=>{removeFromCart(id);addToCart(id);}',qid)
        assert layout()['align']=='center' and layout()['cart']['figure_align']=='center',layout()
        page.screenshot(path=str(root/'test-results/paper-layout-isolation.png'),full_page=True)
        assert not errors,errors
        browser.close()
print('Paper layout UI: A/B isolation, reload, save revalidation, quick export, failure rollback, new defaults passed.')
