"""Space switcher, leave guard, cross-space selection, paper folders, the collapsible
sidebar and authored option layouts in a real browser."""
from pathlib import Path
import json
import os
import time

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ['MATHBANK_TEST_URL']
DATA = Path(os.environ['MATHBANK_TEST_DATA']).resolve()
assert DATA != (ROOT/'data').resolve(), 'Never test against business data'
password = (DATA/'initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：', 1)[1]
suffix = str(time.time_ns())[-8:]
ready = "document.documentElement.dataset.accountReady==='true'"

with httpx.Client(base_url=BASE, trust_env=False, timeout=120) as api:
    assert api.post('/platform/api/login', json={'username': 'admin', 'password': password}).is_success
    api.headers['X-Local-Token'] = api.get('/platform/api/me').json()['csrf']
    def space(name, book=1, kind='personal'):
        return api.post('/platform/api/spaces', json={'name': name+suffix, 'kind': kind, 'textbook_id': book}).json()['id']
    target, source = space('组卷目标'), space('题源', 2, 'team')
    metadata = api.get('/platform/api/admin/textbooks/1').json()['metadata']
    junior = space('初中', api.post('/platform/api/admin/textbooks', json={'stage': '初中', 'subject': '数学', 'name': '初中'+suffix, 'metadata': metadata}).json()['id'])
    question = api.post(f'/bank/{source}/api/questions', data={
        'content': r'已知 $a+b=3$，求 $(a+b)^2$ 的值。', 'question_type': 'detailed_answer', 'difficulty': '2',
        'category_compulsory': '必修第一册', 'answer_markdown': '9'}).json()['question']
    folder = api.post(f'/platform/api/spaces/{target}/folders', json={'name': '期中'+suffix}).json()['id']

checks, errors = [], []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 960})
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(BASE)
    page.locator('#loginForm').get_by_label('账号', exact=True).fill('admin')
    page.locator('#loginForm').get_by_label('密码', exact=True).fill(password)
    page.get_by_role('button', name='登录', exact=True).click()
    page.wait_for_function(ready)
    page.goto(BASE+f'/workbench/{target}')
    page.wait_for_function(ready)
    assert page.title().startswith('组卷目标'+suffix)
    assert '高中数学 · 人教A版' in page.locator('.mb-space-switch').inner_text()

    # Picker groups by stage and subject; the import guard keeps unsaved input.
    page.locator('.mb-space-switch').click()
    picker = page.locator('.mb-space-picker')
    assert picker.locator('h3', has_text='初中 · 数学').count() == 1
    assert picker.locator('.mb-picker-item[aria-current=true] strong').inner_text() == '组卷目标'+suffix
    picker.get_by_role('searchbox').fill('初中'+suffix)
    assert picker.locator('.mb-picker-item').count() == 1
    picker.get_by_role('button', name='关闭').click()
    page.locator('#appNavImport').click()
    assert '保存到' in page.locator('#importWorkspaceSection .mb-entry-destination').inner_text()
    page.locator('#importPaperTitle').fill('未入库的试卷')
    page.locator('#importWorkspaceSection .mb-entry-switch').click()
    picker.get_by_role('searchbox').fill('初中'+suffix)
    picker.locator('.mb-picker-item').click()
    page.get_by_role('heading', name='离开当前录入内容？').wait_for()
    page.get_by_role('button', name='返回保存', exact=True).click()
    assert page.url.endswith(f'/workbench/{target}') and page.locator('#importPaperTitle').input_value() == '未入库的试卷'
    page.locator('#importPaperTitle').fill('')
    checks.append('picker grouping/search, destination bar and leave guard')

    # Cross-space selection copies into this space and remembers the copy.
    page.locator('#appNavPaper').click()
    page.locator('#platformCrossSpaceBtn').click()
    dialog = page.locator('#crossSpacePicker')
    options = dialog.get_by_label('来源空间').locator('option').all_text_contents()
    assert any(o.startswith('题源'+suffix) for o in options) and not any('初中'+suffix in o for o in options), options
    dialog.get_by_label('来源空间').select_option(str(source))
    item = dialog.locator('.mb-xs-item').first
    item.wait_for()
    assert 'a+b' in item.inner_text() or item.locator('.katex').count()
    item.get_by_role('button', name='复制到本空间并加入试卷').click()
    item.locator('.mb-copied').wait_for()
    assert item.get_by_role('button', name='已在试卷中').is_disabled()
    copied = page.evaluate('PaperStore.cart[0].id')
    origin = page.evaluate(f"fetch('/api/questions/{copied}').then(r=>r.json())")
    assert origin['origin']['source_question'] == question['id'] and origin['category_compulsory'] == ''
    dialog.get_by_role('button', name='关闭').click()
    checks.append('cross-space copy keeps origin and clears other-textbook categories')

    # Save into a folder from 试卷信息; 试卷管理 shows it in the folder pane.
    page.locator('#togglePaperMetaBtn').click()
    page.locator('#platformSaveFolder').select_option(str(folder))
    with page.expect_response(lambda r: r.url.endswith('/api/paper/save') and r.ok):
        page.evaluate('savePaperToDb()')
    page.locator('#appNavRecords').click()
    assert page.locator('#appNavRecords').inner_text().strip() == '试卷管理'
    pane = page.locator('.mb-folder-pane')
    card = page.locator('.saved-paper-card').first
    page.wait_for_function(f"document.querySelector('.saved-paper-card .mb-card-folder select')?.value==='{folder}'")
    top = pane.locator('.mb-folder-item', has_text='期中'+suffix)
    assert top.locator('small').inner_text() == '1'

    # A subfolder is created inline; there is no third level.
    top.hover()
    top.get_by_role('button', name=f'在“期中{suffix}”下新建子目录').click()
    pane.get_by_label('子目录名称').fill('复习')
    pane.get_by_label('子目录名称').press('Enter')
    child = pane.locator('.mb-folder-item', has_text='复习')
    child.wait_for()
    assert child.get_by_role('button', name='在“复习”下新建子目录').count() == 0
    with page.expect_response(lambda r: '/folder' in r.url and r.ok):
        card.locator('.mb-card-folder select').select_option(label=f'期中{suffix} / 复习')
    checks.append('save into a folder, create a subfolder inline, move from the card')

    # The top level lists its subfolder's papers; 未归档 is empty; drag a card back out.
    top.locator('.mb-folder-pick').click()
    page.wait_for_function("document.querySelectorAll('.saved-paper-card').length===1")
    pane.locator('.mb-folder-item', has_text='未归档').locator('.mb-folder-pick').click()
    page.wait_for_function("!document.querySelector('.saved-paper-card')")
    pane.locator('.mb-folder-item', has_text='全部试卷').locator('.mb-folder-pick').click()
    card.wait_for()
    with page.expect_response(lambda r: '/folder' in r.url and r.ok):
        card.drag_to(pane.locator('.mb-folder-item', has_text='未归档'))
    page.wait_for_function("document.querySelector('.saved-paper-card .mb-card-folder select')?.value==='0'")
    checks.append('folder filter includes subfolders; drag a paper onto a folder')

    # Rename inline, then delete the top level with its subfolder after confirming.
    child.hover()
    child.get_by_role('button', name='重命名“复习”').click()
    pane.get_by_label('子目录名称').fill('期末')
    pane.get_by_label('子目录名称').press('Enter')
    pane.locator('.mb-folder-item', has_text='期末').wait_for()
    top.hover()
    top.get_by_role('button', name=f'删除“期中{suffix}”').click()
    confirm = page.locator('#folderConfirm')
    assert '1 个子目录' in confirm.inner_text()
    confirm.get_by_role('button', name='删除目录').click()
    top.wait_for(state='detached')
    checks.append('rename inline and delete a folder with its subfolder')

    # The sidebar collapses to icons, and the server keeps it collapsed on reload.
    page.locator('.mb-nav-collapse').click()
    assert page.evaluate("document.documentElement.dataset.nav") == 'collapsed'
    page.wait_for_function("document.getElementById('appNavigation').getBoundingClientRect().width < 90")
    assert 'data-nav="collapsed"' in page.request.get(BASE+f'/workbench/{target}').text()
    page.locator('.mb-nav-collapse').click()
    page.wait_for_function("document.getElementById('appNavigation').getBoundingClientRect().width > 200")
    checks.append('sidebar collapses to icons and the choice survives reloads')

    # An authored option layout is honoured by the preview and kept by the editor helper.
    layout = page.evaluate(r"""() => {
        const host = document.createElement('div');
        host.style.width = '700px';
        document.body.append(host);
        const source = String.raw`选出正确的一项\begin{choices}[columns=2]\item 甲\item 乙\item 丙\item 丁\end{choices}`;
        renderQuestionPreviewContent(host, source);
        adaptChoicesGridLayout(host);
        const grid = host.querySelector('.choices-grid');
        const result = {labels: [...grid.querySelectorAll('.choices-label')].map(n => n.textContent),
                        columns: grid.dataset.choiceColumns, automatic: ChoiceLayoutTools.set(source, 0)};
        host.remove();
        return result;
    }""")
    assert layout == {'labels': ['A.', 'B.', 'C.', 'D.'], 'columns': '2',
                      'automatic': r'选出正确的一项\begin{choices}\item 甲\item 乙\item 丙\item 丁\end{choices}'}, layout
    checks.append('authored option columns in the preview')

    # 导入中心: a chosen paper replaces the upload area and the settings move to the right pane.
    import pymupdf
    sample = pymupdf.open()
    sample.new_page().insert_text((72, 72), 'Sample paper')
    sample.save(str(DATA / 'import-sample.pdf'))
    (DATA / 'import-sample.tex').write_text('\\begin{question}已知 $x=1$。\\end{question}\n', encoding='utf-8')
    source = lambda: page.evaluate("document.getElementById('importInputPane').dataset.source")
    page.locator('#appNavImport').click()
    assert source() == 'none' and page.locator('#importInputPane .mb-import-config').is_visible()
    page.locator('#texFileInput').set_input_files(str(DATA / 'import-sample.pdf'))
    page.locator('.mb-source-doc iframe').wait_for(state='attached')
    assert source() == 'pdf' and page.locator('#importReviewPane > .mb-import-config').is_visible()
    assert page.evaluate("document.querySelector('.import-workspace-steps li.is-current strong').textContent") == '解析配置'
    page.locator('#texFileInput').set_input_files(str(DATA / 'import-sample.tex'))
    page.wait_for_function("document.getElementById('importLatexContent').value.includes('begin{question}')")
    assert source() == 'tex' and page.locator('#importLatexContent').is_visible()
    page.evaluate('clearAllImportInputs()')
    assert source() == 'none' and page.locator('#importInputPane .mb-import-config').is_visible()
    page.get_by_role('button', name='粘贴文本').click()
    assert source() == 'paste' and page.evaluate('document.activeElement.id') == 'importLatexContent'
    page.get_by_role('button', name='改为上传文件').click()
    assert source() == 'none'
    checks.append('import center shows the chosen paper and moves the settings beside it')

    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('#appNavDashboard').click()
    mobile = page.locator('.mb-space-switch-mobile')
    assert mobile.is_visible() and not page.locator('.mb-space-switch').is_visible()
    mobile.click()
    assert page.locator('.mb-space-picker').is_visible()
    page.keyboard.press('Escape')
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    checks.append('390px header switcher without horizontal overflow')
    assert not errors, errors
    print(json.dumps({'passed': True, 'checks': checks}, ensure_ascii=False))
    browser.close()
