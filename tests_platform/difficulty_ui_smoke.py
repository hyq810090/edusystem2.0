"""Isolated end-to-end checks for five ranks, independent tags and legacy drafts."""
from pathlib import Path
import json, time
import httpx
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]
base='http://127.0.0.1:3012'
data=root/'test-results/difficulty-ui-data'
password=(data/'initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：',1)[1]
with httpx.Client(base_url=base,trust_env=False,timeout=60) as c:
    assert c.post('/platform/api/login',json={'username':'admin','password':password}).is_success
    c.headers['X-Local-Token']=c.get('/platform/api/me').json()['csrf']
    sid=c.post('/platform/api/spaces',json={'name':'五级难度 '+str(time.time_ns()),'textbook_id':1}).json()['id']
    lane=sid
    questions=[]
    for i,level in enumerate(['1','2','3','4','5','unrated']):
        q=c.post(f'/bank/{lane}/api/questions',data={'content':f'五级验收题 {i}', 'question_type':'detailed_answer','difficulty':level,'tags':'易错,强基,竞赛'}).json()['question']
        questions.append(q)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(base);page.locator('#loginForm [name=username]').fill('admin');page.locator('#loginForm [name=password]').fill(password);page.get_by_role('button',name='登录',exact=True).click()
        page.wait_for_function("document.documentElement.dataset.accountReady==='true'")
        page.goto(base+f'/workbench/{lane}');page.wait_for_load_state('networkidle')
        assert page.locator('#editDifficulty option').evaluate_all('(els)=>els.map(e=>e.value)')==['unrated','1','2','3','4','5']
        assert not page.evaluate('isEditorModified()'), page.evaluate('({original:originalQuestionState, difficulty:document.getElementById("editDifficulty").value})')
        page.evaluate('startManualQuestion()');page.locator('#editorSection').wait_for(state='visible');page.locator('[data-editor-panel-target=classification]').click();assert page.locator('#editDifficulty').input_value()=='unrated'
        page.locator('#editDifficulty').select_option('2')
        page.get_by_role('button',name='+ 强基',exact=True).click()
        assert page.locator('#editTags').input_value()=='强基' and page.locator('#editDifficulty').input_value()=='2'
        page.evaluate("setLocalStorageDrafts([{id:'legacy-draft',content:'旧草稿',difficulty:'easy_error',tags:'真题'}]);selectDraft(getLocalStorageDrafts()[0]);")
        assert page.locator('#editDifficulty').input_value()=='unrated'
        assert page.locator('#editTags').input_value()=='真题,易错'
        page.evaluate('finishCloseQuestionEditorModal()');page.locator('#appNavImport').click()
        # Parsed Word/PDF review accepts tags separately and submits them intact.
        page.evaluate("""() => {
            const comp=Object.keys(categoryTree)[0], chapter=Object.keys(categoryTree[comp])[0];
            const records=[{content:'文件导入标签独立验收 x=1937',question_type:'detailed_answer',difficulty:'competition',tags:'真题',category_compulsory:comp,category_chapter:chapter}];
            replaceParsedQuestions(records);renderParsedQuestionsList(records);
        }""")
        assert page.locator('.card-difficulty').input_value()=='unrated'
        assert page.locator('.card-tags').input_value()=='真题,竞赛'
        page.locator('.card-difficulty').select_option('4',force=True)
        page.locator('.card-tags').evaluate("e=>{e.value='真题,竞赛,易错';e.dispatchEvent(new Event('input'));}")
        assert page.evaluate('saveParsedQuestion(0)') is True
        stats=c.get(f'/bank/{lane}/api/stats').json();assert stats['difficulty_counts']['4']==2
        with __import__('sqlite3').connect(data/f'banks/{lane}/math_question_bank.db') as db:
            assert db.execute("SELECT difficulty,tags FROM questions WHERE content LIKE '文件导入%'").fetchone()==('4','真题,竞赛,易错')
        page.evaluate('closeQuestionEditorModal()');page.locator('#appNavPaper').click()
        page.evaluate('(qs)=>{for(const q of qs){PaperStore.questionsMap[q.id]=q;addToCart(q.id);}renderPaperWorkspace();}',questions)
        ratio=page.locator('#platformDifficultyRatio');assert '已定级 5 题 · 待确认 1 题' in ratio.inner_text()
        assert ratio.inner_text().count('20%')==5
        before=ratio.inner_text()
        page.evaluate("Object.values(PaperStore.questionsMap).forEach(q=>q.tags='竞赛');renderPaperWorkspace();")
        assert ratio.inner_text()==before
        summary=page.evaluate("DifficultyScale.summarize([{difficulty:'1'},{difficulty:'2'},{difficulty:'5'},{difficulty:'unrated'}])")
        assert sum(summary['percentages'])==100 and summary['unrated']==1
        assert page.evaluate("DifficultyScale.summarize([{difficulty:'unrated'}]).percentages")==[0,0,0,0,0]
        page.screenshot(path=str(root/'test-results/difficulty-five-levels.png'),full_page=True)
        assert not errors,errors
        browser.close()
print('PASS: five levels, independent tags, legacy draft, parsed import persistence, ratios and rounding; no browser errors.')
