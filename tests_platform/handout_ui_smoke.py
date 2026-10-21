"""讲义 workspace in a real browser: lesson folders, creating and writing a handout, numbering,
question snapshots from the bank, figures, autosave across reloads, student/teacher preview,
Word export, read-only viewers, dark mode and the phone layout."""
from pathlib import Path
import json
import os
import time

import httpx
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ['MATHBANK_TEST_URL']
DATA = Path(os.environ['MATHBANK_TEST_DATA']).resolve()
assert DATA != (ROOT/'data').resolve(), 'Never test against business data'
password = (DATA/'initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：', 1)[1]
suffix = str(time.time_ns())[-8:]
ready = "document.documentElement.dataset.accountReady==='true' && document.documentElement.dataset.handoutsReady==='true'"
title = '圆的概念' + suffix
figure = DATA/'handout-figure.png'
Image.new('RGB', (420, 300), (235, 240, 250)).save(figure)

with httpx.Client(base_url=BASE, trust_env=False, timeout=120) as api:
    assert api.post('/platform/api/login', json={'username': 'admin', 'password': password}).is_success
    api.headers['X-Local-Token'] = api.get('/platform/api/me').json()['csrf']
    space = api.post('/platform/api/spaces', json={'name': '讲义'+suffix, 'kind': 'personal', 'textbook_id': 1}).json()['id']
    for content, answer in ((r'已知 $a+b=3$，求 $(a+b)^2$ 的值。', '9'), (r'计算 $2+3$。', '5'), (r'化简 $x+x$。', '$2x$')):
        created = api.post(f'/bank/{space}/api/questions', data={'content': content, 'question_type': 'detailed_answer',
                                                                  'difficulty': '2', 'answer_markdown': answer})
        assert created.status_code == 200, created.text
    folder = api.post(f'/platform/api/spaces/{space}/folders', json={'name': '第一章'+suffix, 'kind': 'lesson'}).json()['id']
    shared = api.post('/platform/api/spaces', json={'name': '共享讲义'+suffix, 'kind': 'team', 'textbook_id': 1}).json()['id']
    api.post(f'/platform/api/spaces/{shared}/handouts', json={'title': '只读讲义', 'document': {
        'sections': [{'title': '概念', 'blocks': [{'kind': 'definition', 'title': '集合', 'body': '对象的全体。'}]}]}})
    viewer = 'handout_viewer'+suffix
    assert api.post('/platform/api/admin/users', json={'username': viewer, 'display_name': '只读教师', 'password': 'Platform-Test-12345',
                                                       'team_id': 1, 'role': 'member'}).is_success

checks, errors = [], []


def login(page, name, secret):
    page.goto(BASE)
    page.locator('#loginForm').get_by_label('账号', exact=True).fill(name)
    page.locator('#loginForm').get_by_label('密码', exact=True).fill(secret)
    page.get_by_role('button', name='登录', exact=True).click()
    page.wait_for_function("document.documentElement.dataset.accountReady==='true'")


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 960}, accept_downloads=True)
    page = context.new_page()
    page.on('pageerror', lambda e: errors.append(str(e)))
    login(page, 'admin', password)
    page.goto(BASE+f'/workbench/{space}')
    page.wait_for_function(ready)
    page.locator('#appNavHandouts').click()
    page.wait_for_selector('.mb-ho-empty')
    assert page.locator('.mb-folder-pane .mb-folder-name', has_text='第一章'+suffix).count() == 1
    page.locator('.mb-folder-pane .mb-folder-pick', has_text='第一章'+suffix).click()
    page.get_by_role('button', name='新建讲义').first.click()
    page.locator('#handoutCreate').get_by_label('讲义标题').fill(title)
    page.locator('#handoutCreate').get_by_label('讲次').fill('第 1 讲')
    page.locator('#handoutCreate').get_by_role('button', name='创建并编辑').click()
    page.wait_for_selector('.mb-ho-page .mb-ho-section')
    assert page.locator('.mb-ho-page h1').inner_text() == title
    checks.append('handout created in a lesson folder and opened')

    # Text blocks: bold, lists, maths and blanks render as in print; numbering is per section.
    page.locator('.mb-ho-add', has_text='添加内容').first.click()
    page.locator('.mb-ho-kind-menu').get_by_role('menuitem', name='定义').click()
    inspector = page.locator('.mb-ho-inspector')
    inspector.get_by_label('名称').fill('集合')
    inspector.get_by_label('内容').fill('把研究对象统称为**元素**，记作 $a\\in A$。\n- 确定性\n- 互异性\n\n填空：____ 。')
    definition = page.locator('.mb-ho-page .mb-ho-definition').first
    page.wait_for_function("document.querySelector('.mb-ho-page .mb-ho-definition .katex')")
    assert '定义 1.1' in definition.inner_text() and definition.locator('strong', has_text='元素').count() == 1
    assert definition.locator('ul li').count() == 2 and definition.locator('.mb-ho-blank').count() == 1
    checks.append('definition renders bold, list, maths and blank with its number')

    # Example and exercises are snapshots of bank questions.
    page.locator('.mb-ho-add', has_text='添加内容').first.click()
    page.locator('.mb-ho-kind-menu').get_by_role('menuitem', name='例题').click()
    picker = page.locator('#handoutPicker')
    page.wait_for_function("document.querySelector('#handoutPicker .mb-status')?.textContent.startsWith('共 3')")
    picker.locator('.mb-xs-item').first.get_by_role('button', name='选用').click()
    page.wait_for_selector('.mb-ho-page .mb-ho-example')
    assert '例 1.1' in page.locator('.mb-ho-page .mb-ho-example').inner_text()  # each kind counts on its own
    inspector.get_by_label('解答').fill('由定义可得结果。')
    page.locator('.mb-ho-add', has_text='添加内容').first.click()
    page.locator('.mb-ho-kind-menu').get_by_role('menuitem', name='练习').click()
    page.wait_for_function("document.querySelector('#handoutPicker .mb-status')?.textContent.startsWith('共 3')")
    picker.locator('.mb-xs-item').nth(1).get_by_role('button', name='选择').click()
    picker.locator('.mb-xs-item').nth(2).get_by_role('button', name='选择').click()
    picker.get_by_role('button', name='加入 2 题').click()
    page.wait_for_selector('.mb-ho-page .mb-ho-exercises li')
    assert page.locator('.mb-ho-page .mb-ho-exercises > ol > li').count() == 2
    assert page.locator('.mb-ho-page .mb-ho-answers li').count() == 2
    checks.append('example and exercises inserted from the bank, answers listed for teachers')

    # A figure, then moved above the example: each kind keeps its own numbers.
    page.locator('.mb-ho-add', has_text='添加内容').first.click()
    page.locator('.mb-ho-kind-menu').get_by_role('menuitem', name='插图').click()
    inspector.locator('input[type=file]').set_input_files(str(figure))
    page.wait_for_selector('.mb-ho-page .mb-ho-figure img')
    inspector.get_by_label('图注').fill('韦恩图')
    for _ in range(2):
        inspector.get_by_role('button', name='上移').click()
    page.wait_for_function("document.querySelector('.mb-ho-page .mb-ho-figure .mb-ho-caption').textContent.startsWith('图 1.1')")
    assert '例 1.1' in page.locator('.mb-ho-page .mb-ho-example').inner_text()
    assert '定义 1.1' in page.locator('.mb-ho-page .mb-ho-definition').first.inner_text()
    checks.append('figure uploaded; definitions, examples and figures are numbered separately')

    # Student view hides solutions and answers and shows the answer space.
    page.get_by_role('button', name='学生版', exact=True).click()
    assert page.locator('.mb-ho-page .mb-ho-solution').count() == 0 and page.locator('.mb-ho-page .mb-ho-answers').count() == 0
    assert page.locator('.mb-ho-page .mb-ho-space').count() == 1
    page.get_by_role('button', name='教师版', exact=True).click()
    checks.append('student and teacher previews')

    # Autosave survives a reload.
    page.wait_for_function("document.querySelector('.mb-ho-save').textContent === '已保存'", timeout=15000)
    page.reload()
    page.wait_for_function(ready)
    page.locator('#appNavHandouts').click()
    page.locator('.mb-ho-card-open', has_text=title).click()
    page.wait_for_selector('.mb-ho-page .mb-ho-figure img')
    assert '由定义可得结果' in page.locator('.mb-ho-page .mb-ho-solution').inner_text()
    checks.append('autosaved across a reload')

    with page.expect_download(timeout=300000) as download:
        page.get_by_role('button', name='导出').click()
        page.get_by_role('menuitem', name='Word · 教师版').click()
    assert download.value.suggested_filename == f'{title}（教师版）.docx'
    checks.append('Word export from the editor')

    page.evaluate('window.toggleDarkMode()')
    assert page.evaluate("getComputedStyle(document.querySelector('.mb-ho-page .mb-ho-definition .katex')).color") == 'rgb(33, 37, 41)'
    page.evaluate('window.toggleDarkMode()')
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    page.get_by_role('button', name='讲义列表').click()
    page.wait_for_selector('.mb-ho-card')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    checks.append('dark mode keeps maths legible; 390px editor and list without overflow')

    # A viewer reads and exports but cannot edit.
    other = browser.new_context(viewport={'width': 1440, 'height': 960})
    reader = other.new_page()
    reader.on('pageerror', lambda e: errors.append(str(e)))
    login(reader, viewer, 'Platform-Test-12345')
    reader.goto(BASE+f'/workbench/{shared}')
    reader.wait_for_function(ready)
    reader.locator('#appNavHandouts').click()
    reader.locator('.mb-ho-card-open', has_text='只读讲义').click()
    reader.wait_for_selector('.mb-ho-page .mb-ho-definition')
    assert reader.get_by_role('button', name='新建讲义').count() == 0 and reader.locator('.mb-ho-add').count() == 0
    assert reader.locator('.mb-ho-title-input').is_disabled() and reader.locator('.mb-ho-save').inner_text() == '只读'
    assert reader.get_by_role('button', name='导出').is_visible()
    checks.append('viewers read and export only')
    other.close()

    assert not errors, errors
    browser.close()

with httpx.Client(base_url=BASE, trust_env=False, timeout=120) as api:
    assert api.post('/platform/api/login', json={'username': 'admin', 'password': password}).is_success
    filed = [h for h in api.get(f'/platform/api/spaces/{space}/handouts').json() if h['title'] == title]
    assert filed and filed[0]['folder_id'] == folder and filed[0]['summary'] == {'kicker': '第 1 讲', 'sections': 1, 'blocks': 4}
print(json.dumps({'passed': True, 'checks': checks}, ensure_ascii=False))
