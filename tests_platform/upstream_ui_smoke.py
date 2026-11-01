"""Second-batch workflows with a local model and disposable platform data."""
from pathlib import Path
import json
import os
import time

import httpx
from playwright.sync_api import sync_playwright

from test_upstream_workflows import Model, scanned_pdf

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ['MATHBANK_TEST_URL']
DATA = Path(os.environ['MATHBANK_TEST_DATA']).resolve()
assert DATA != (ROOT / 'data').resolve()
password = (DATA / 'initial-admin.txt').read_text().splitlines()[1].split('：', 1)[1]
fake = Model()
errors, checks = [], []

try:
    with httpx.Client(base_url=BASE, trust_env=False, timeout=120) as api:
        assert api.post('/platform/api/login', json={'username': 'admin', 'password': password}).is_success
        api.headers['X-Local-Token'] = api.get('/platform/api/me').json()['csrf']
        fake.configure(api, 'test-selection')
        sid = api.post('/platform/api/spaces', json={
            'name': '上游工作流' + str(time.time_ns())[-8:], 'textbook_id': 1, 'kind': 'personal'}).json()['id']
        bank = f'/bank/{sid}/api'
        for index in range(6):
            assert api.post(bank + '/questions', data={'content': f'求第{index}项的平方。',
                'question_type': 'single_choice', 'difficulty': '3'}).is_success
        before = api.get(bank + '/questions?page=1').json()['total']
        (DATA / 'resume.pdf').write_bytes(scanned_pdf())

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(BASE)
        page.locator('#loginForm').get_by_label('账号', exact=True).fill('admin')
        page.locator('#loginForm').get_by_label('密码', exact=True).fill(password)
        page.get_by_role('button', name='登录', exact=True).click()
        page.wait_for_function("document.documentElement.dataset.accountReady==='true'")
        page.goto(BASE + f'/workbench/{sid}')
        page.wait_for_function("document.documentElement.dataset.accountReady==='true'")
        page.locator('#appNavPaper').click()
        page.locator('#togglePaperAiBtn').click()
        page.locator('#platformSelectionCount').wait_for()
        page.locator('#platformSelectionCount').fill('3')
        page.locator('#platformSelectionCount').dispatch_event('change')
        page.locator('#platformSelectionReference').select_option('none')
        page.locator('#paperAiPromptInput').fill('抽取3道单选题')
        page.locator('button[onclick="triggerAiPaperSelect()"]').click()
        page.wait_for_function('window.PaperStore.cart.length===3')
        page.locator('#paperAiPromptInput').fill('抽取2道单选题')
        page.locator('button[onclick="triggerAiPaperSelect()"]').click()
        page.wait_for_function('window.PaperStore.cart.length===5')
        checks.append('selection controls survive rerender and exclude the existing basket')

        page.locator('#appNavImport').click()
        page.locator('#texFileInput').set_input_files(str(DATA / 'resume.pdf'))
        page.locator('input[name="pdfStrategy"][value="force_ocr"]').check()
        page.locator('#runParseBtn').click()
        page.locator('#platformPdfResume').wait_for(state='visible', timeout=60000)
        card = page.locator('#parsed-card-0')
        card.wait_for()
        text = card.locator('.card-content-textarea')
        edited = text.input_value() + '\n老师补充：保留这个编辑。'
        text.fill(edited)
        answer = card.locator('.card-answer-textarea')
        answer.fill('老师手工答案，请保留。')
        card.locator('.card-qtype').select_option('detailed_answer')
        card.locator('.card-difficulty').select_option('3')
        checkbox = card.locator('.card-select-checkbox')
        checked = os.environ.get('MATHBANK_RESUME_CHECKED') == '1'
        checkbox.set_checked(checked)
        fake.recover = True
        page.locator('#platformPdfResume').click()
        page.wait_for_function("document.querySelector('#importLoadingState').classList.contains('hidden')", timeout=60000)
        assert page.locator('#parsed-card-0 .card-content-textarea').input_value() == edited
        assert page.locator('#parsed-card-0 .card-answer-textarea').input_value() == '老师手工答案，请保留。'
        assert page.locator('#parsed-card-0 .card-select-checkbox').is_checked() == checked
        assert page.locator('#parsed-card-0 .card-difficulty').input_value() == '3'
        assert fake.counts[1] == 1 and fake.counts[2] == 3, fake.counts
        checks.append('failed page recovery preserves text, answer, difficulty and checkbox, reusing successful OCR')
        for width in (1440, 390):
            page.set_viewport_size({'width': width, 'height': 1000})
            page.locator('#appNavPaper').click()
            page.locator('#platformSelectionCount').wait_for(state='visible')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.evaluate("document.documentElement.setAttribute('data-theme','dark')")
        checks.append('phone and dark controls use native theme without horizontal overflow')
        assert not errors, errors
        browser.close()
    with httpx.Client(base_url=BASE, trust_env=False) as api:
        api.post('/platform/api/login', json={'username': 'admin', 'password': password})
        assert api.get(bank + '/questions?page=1').json()['total'] == before
    print(json.dumps({'passed': True, 'checks': checks, 'errors': errors}, ensure_ascii=False))
finally:
    fake.server.shutdown()
