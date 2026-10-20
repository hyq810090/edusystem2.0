from pathlib import Path
import io,json,time
import httpx
from PIL import Image,ImageDraw
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1];out=root/'test-results';base='http://127.0.0.1:3012';data=out/'choices-right-ui-data'
pw=(data/'initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：',1)[1]
with httpx.Client(base_url=base,trust_env=False,timeout=50) as c:
 c.post('/platform/api/login',json={'username':'admin','password':pw});c.headers['X-Local-Token']=c.get('/platform/api/me').json()['csrf']
 sid=c.post('/platform/api/spaces',json={'name':'选项分栏验收 '+str(time.time_ns()),'textbook_id':1}).json()['id'];lane=sid
 im=Image.new('RGB',(280,230),'white');d=ImageDraw.Draw(im);d.line((70,70,150,15,150,170),fill='black',width=3);d.ellipse((55,60,75,80),outline='black',width=2);d.rectangle((30,170,220,185),outline='black',width=2);d.ellipse((55,185,80,210),outline='black',width=2);d.ellipse((170,185,195,210),outline='black',width=2);d.line((10,213,245,213),fill='black',width=2)
 buf=io.BytesIO();im.save(buf,format='PNG');image=c.post(f'/bank/{lane}/api/upload',files={'file':('cart.png',buf.getvalue(),'image/png')}).json()['file_path']
 content=r'如图所示，固定在小车上的支架的斜杆与竖直杆的夹角为 $\theta$，在斜杆下端固定有质量为 $m$ 的小球。下列关于杆对球的作用力 $F$ 的判断中，正确的是（　）'+ '\n'+r'\begin{choices}\item 小车静止时，$F=mg\sin\theta$，方向沿杆向上\item 小车静止时，$F=mg\cos\theta$，方向垂直于杆向上\item 小车向右匀加速运动时，一定有 $F=\frac{ma}{\sin\theta}$\item 小车向右匀速运动时，$F=mg$，方向竖直向上\end{choices}'+'\n![]('+image+')'
 q=c.post(f'/bank/{lane}/api/questions',data={'content':content,'image_paths':json.dumps([image]),'question_type':'single_choice','difficulty':'normal','figure_align':'right','figure_align_custom':'true'}).json()['question'];qid=q['id']
 with sync_playwright() as p:
  b=p.chromium.launch(headless=True);page=b.new_page(viewport={'width':1500,'height':1050});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto(base);page.locator('#loginForm [name=username]').fill('admin');page.locator('#loginForm [name=password]').fill(pw);page.get_by_role('button',name='登录',exact=True).click();page.wait_for_function("document.documentElement.dataset.accountReady==='true'")
  page.goto(base+f'/workbench/{lane}');page.wait_for_load_state('networkidle');page.locator('#appNavPaper').click();page.evaluate('(q)=>{PaperStore.questionsMap[q.id]=q;addToCart(q.id);PaperStore.meta.paper_type="exam";PaperStore.meta.title="选项右侧验收";renderPaperCanvas();}',q)
  page.locator(f'.paper-preview-column [data-figure-align-qid="{qid}"]').first.click();page.get_by_role('button',name='插图位置：选项右侧',exact=True).click();page.evaluate('(id)=>waitForFigureLayoutWrite(id)',qid)
  cols=page.locator('.paper-preview-column .choice-figure-columns').first;cols.wait_for()
  def geometry():
   return cols.evaluate('(el)=>{const l=el.children[0].getBoundingClientRect(),r=el.children[1].getBoundingClientRect(),s=el.previousElementSibling.getBoundingClientRect();return {left:l.x,right:r.x,top:r.y,stemBottom:s.bottom,stemWidth:s.width,columnWidth:el.getBoundingClientRect().width,leftEnd:l.right,figureEnd:r.right};}')
  g=geometry();assert g['right']>=g['leftEnd'] and g['top']>=g['stemBottom'] and abs(g['stemWidth']-g['columnWidth'])<2,g
  assert cols.locator('.choices-grid').evaluate('(e)=>getComputedStyle(e).gridTemplateColumns').count(' ')==0
  page.evaluate('savePaperToDb()');pid=c.get(f'/bank/{lane}/api/papers').json()['data'][0]['id']
  assert c.get(f'/bank/{lane}/api/questions/{qid}').json()['figure_align']=='choices_right'
  page.screenshot(path=str(out/'choices-right-paper.png'),full_page=True)
  page.reload();page.wait_for_load_state('networkidle');page.locator('#appNavPaper').click();page.evaluate('(id)=>loadSavedPaper(id)',pid);assert cols.count()==1
  page.set_viewport_size({'width':390,'height':844});assert cols.count()==1;page.screenshot(path=str(out/'choices-right-mobile.png'),full_page=True)
  page.set_viewport_size({'width':1500,'height':1050});page.goto(base+f'/workbench/{lane}');page.wait_for_load_state('networkidle')
  page.get_by_role('button',name='手动录入题目',exact=True).click();page.locator('#editContent').fill(content)
  page.locator('#paperContent img:visible, #contentPreview img:visible').first.click()
  page.get_by_role('button',name='插图位置：选项右侧',exact=True).click()
  assert page.evaluate('FigureLayoutState.align')=='choices_right'
  page.locator('#paperContent .choice-figure-columns:visible, #contentPreview .choice-figure-columns:visible').first.wait_for()
  assert page.locator('#contentPreview .katex').count()>0
  eg=page.locator('#contentPreview .choice-figure-columns').evaluate('(e)=>({l:e.children[0].getBoundingClientRect().right,r:e.children[1].getBoundingClientRect().left})')
  assert eg['r']>=eg['l'],eg
  page.screenshot(path=str(out/'choices-right-editor.png'),full_page=True)
  # Plain A/B options also enter columns; no options degrades without losing the image.
  assert page.evaluate('(img)=>Boolean(renderChoicesRightLayout("题干\\nA. 甲\\nB. 乙\\nC. 丙\\nD. 丁\\n![]("+img+")",null,"auto",{},false,"single_choice"))',image)
  assert not page.evaluate('(img)=>Boolean(renderChoicesRightLayout("题干\\n![]("+img+")",null,"auto",{},false,"single_choice"))',image)
  assert not errors,errors;b.close()
print('Choice-right UI: menu, full-width stem, vertical options, right figure, persistence, mobile, editor, plain options passed.')
