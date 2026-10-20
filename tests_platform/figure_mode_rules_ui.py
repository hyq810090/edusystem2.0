"""Isolated end-to-end coverage of type/option gating and cross-space defaults."""
from pathlib import Path
import io, json, time, os
import httpx
from PIL import Image
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
base = 'http://127.0.0.1:3012'
data = Path(os.environ.get('MATHBANK_TEST_DATA', root/'test-results/figure-rules-ui-data'))
password = (data/'initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：', 1)[1]
with httpx.Client(base_url=base, trust_env=False, timeout=60) as client:
    client.post('/platform/api/login', json={'username':'admin', 'password':password}).raise_for_status()
    client.headers['X-Local-Token'] = client.get('/platform/api/me').json()['csrf']
    def space(name):
        return client.post('/platform/api/spaces', json={'name':name+str(time.time_ns()), 'textbook_id':1}).json()['id']
    lane = space('排版规则')
    other = space('独立来源')
    buffer = io.BytesIO()
    Image.new('RGB', (240, 160), 'navy').save(buffer, format='PNG')
    def upload(target):
        return client.post(f'/bank/{target}/api/upload', files={'file':('figure.png', buffer.getvalue(), 'image/png')}).json()['file_path']
    image, other_image = upload(lane), upload(other)
    options = r'\begin{choices}\item $x=1$\item $x=2$\end{choices}'
    cases = [('single_choice', options, True), ('multi_choice', options, True),
             ('detailed_answer', options, False), ('fill_in_blank', options, False),
             ('single_choice', '', False), ('multi_choice', r'\begin{choices}\end{choices}', False)]
    questions = []
    for index, (kind, opts, expected) in enumerate(cases):
        content = f'规则验收{index}，求 $x$。\n{opts}\n![]({image})'
        result = client.post(f'/bank/{lane}/api/questions', data={'content':content, 'question_type':kind,
            'difficulty':'normal', 'image_paths':json.dumps([image]), 'figure_align':'choices_right',
            'figure_align_custom':'true', 'figure_size':'small'})
        result.raise_for_status()
        questions.append(result.json()['question'])
    foreign = client.post(f'/bank/{other}/api/questions', data={
        'content':f'独立左对齐图\n![]({other_image})', 'question_type':'fill_in_blank',
        'difficulty':'normal','figure_align':'bottom_left','figure_size':'large',
        'figure_align_custom':'true','image_paths':json.dumps([other_image])}).json()['question']
    assert foreign['id'] == questions[0]['id']  # Space IDs must not share layout state.
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width':1500,'height':1050})
        errors = []
        page.on('pageerror', lambda e:errors.append(str(e)))
        page.goto(base)
        page.locator('#loginForm [name=username]').fill('admin')
        page.locator('#loginForm [name=password]').fill(password)
        page.get_by_role('button', name='登录', exact=True).click()
        page.wait_for_function("document.documentElement.dataset.accountReady==='true'")
        page.goto(base+f'/workbench/{lane}')
        page.wait_for_load_state('networkidle')
        page.evaluate("selectWorkspace('bank');loadQuestions()")
        for q, (_, _, expected) in zip(questions, cases):
            page.evaluate('(id)=>selectQuestionById(id)', q['id'])
            page.wait_for_function('(id)=>EditorState.questionId===id', arg=q['id'])
            page.wait_for_function('(expected)=>Boolean(document.querySelector("#paperContent .choice-figure-columns"))===expected', arg=expected)
            if not expected:
                page.wait_for_function('()=>{const img=document.querySelector("#paperContent img[data-editor-figure-layout]");return img && img.parentElement.style.textAlign==="right"}')
                assert page.evaluate('(q)=>formatBankQuestionContent(q)===formatBankQuestionContent({...q,figure_align:"bottom_right"})',q)
            assert page.evaluate('FigureLayoutState.align') == 'choices_right'
        # Changing the type refreshes the layout without editing the text.
        page.evaluate('(id)=>selectQuestionById(id)', questions[0]['id'])
        page.wait_for_function('(id)=>EditorState.questionId===id', arg=questions[0]['id'])
        page.locator('#paperContent .choice-figure-columns').wait_for()
        page.evaluate("document.getElementById('editQType').value='detailed_answer';document.getElementById('editQType').dispatchEvent(new Event('change'))")
        page.wait_for_function('()=>!document.querySelector("#paperContent .choice-figure-columns")')
        page.reload()
        page.wait_for_load_state('networkidle')
        page.locator('#appNavPaper').click()
        page.evaluate('(qs)=>{PaperStore.cart=[];for(const q of qs){PaperStore.questionsMap[q.id]=q;addToCart(q.id)};PaperStore.meta.paper_type="exam";renderPaperCanvas()}',questions)
        assert page.locator('.paper-preview-column .choice-figure-columns').count()==2
        for q, (_, _, expected) in zip(questions, cases):
            image_node = page.locator(f'.paper-preview-column img[data-figure-align-qid="{q["id"]}"]').first
            assert image_node.get_attribute('data-figure-align') == ('choices_right' if expected else 'bottom_right')
        # Read-only cross-space previews use each record's own defaults.
        page.get_by_role('button',name='跨空间选题',exact=True).click()
        modal = page.locator('#crossSpacePicker')
        modal.get_by_label('来源空间').select_option(str(other))
        card = modal.locator('.mb-xs-item').filter(has_text='独立左对齐图')
        card.wait_for()
        assert card.locator('img').get_attribute('data-figure-align')=='bottom_left'
        assert card.locator('img').get_attribute('data-figure-size')=='large'
        card.locator('img').evaluate('(img)=>img.decode()')
        assert card.locator('img').evaluate('(img)=>img.complete && img.naturalWidth>0')
        page.goto(base+f'/workbench/{other}')
        page.wait_for_function("document.documentElement.dataset.accountReady==='true'")
        page.locator('#appNavPaper').click()
        page.get_by_role('button',name='跨空间选题',exact=True).click()
        modal.get_by_label('来源空间').select_option(str(lane))
        modal.locator('.mb-xs-item').filter(has_text='规则验收5').wait_for()
        assert modal.locator('.choice-figure-columns').count()==2
        assert modal.locator('img[data-figure-align-qid]').count()==0
        for i, (_, _, expected) in enumerate(cases):
            card = modal.locator('.mb-xs-item').filter(has_text=f'规则验收{i}')
            assert card.locator('img[data-figure-align]').get_attribute('data-figure-align')==('choices_right' if expected else 'bottom_right')
            card.locator('img').evaluate('(img)=>img.decode()')
            assert card.locator('img').evaluate('(img)=>img.complete && img.naturalWidth>0')
        page.screenshot(path=str(root/'test-results/figure-rules-cross-space.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        assert modal.evaluate('(e)=>e.scrollWidth<=e.clientWidth+2')
        assert not errors, errors
        browser.close()
    for q in questions:
        stored = client.get(f'/bank/{lane}/api/questions/{q["id"]}').json()
        assert stored['content']==q['content'] and stored['figure_align']=='choices_right'
print('Type/option rules, editor type changes, paper controls, cross-space defaults/sizes and ID isolation passed.')
