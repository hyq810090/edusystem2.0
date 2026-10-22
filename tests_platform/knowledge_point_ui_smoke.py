"""考查知识点 in a real browser: bank cards show a question's points; the editor adds points from
the section's common list and by search, switches main/auxiliary, adopts an AI suggestion (against a
local fake model) and stores each change at once; a new question keeps its points through its first
save; the bank list and the paper question stream filter by point; phone width does not overflow."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import threading
import time

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ['MATHBANK_TEST_URL']
DATA = Path(os.environ['MATHBANK_TEST_DATA']).resolve()
assert DATA != (ROOT/'data').resolve(), 'Never test against business data'
password = (DATA/'initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：', 1)[1]
suffix = str(time.time_ns())[-8:]
CONTENT = f'已知集合 $A=\\{{1,2\\}}$，判断 $1$ 与 $A$ 的关系。（{suffix}）'


class FakeModel:
    def __init__(self, codes):
        self.requests = []
        model = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                model.requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                reply = {'knowledge_points': [{'code': codes['集合中元素的确定性、互异性、无序性'], 'role': 'auxiliary',
                                               'evidence_source': 'content', 'evidence': '判断 $1$ 与 $A$ 的关系', 'reason': '依据元素特征'},
                                              {'code': 'invented', 'role': 'main', 'evidence_source': 'content', 'evidence': '已知集合'}]}
                data = json.dumps({'choices': [{'message': {'content': json.dumps(reply, ensure_ascii=False)},
                                                'finish_reason': 'stop'}]}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


api = httpx.Client(base_url=BASE, trust_env=False, timeout=120)
assert api.post('/platform/api/login', json={'username': 'admin', 'password': password}).is_success
api.headers['X-Local-Token'] = api.get('/platform/api/me').json()['csrf']
space = api.post('/platform/api/spaces', json={'name': '知识点' + suffix, 'kind': 'personal', 'textbook_id': 1}).json()['id']
bank = f'/bank/{space}/api'
codes = {p['name']: p['code'] for p in api.get(f'{bank}/platform/knowledge-points').json()['points']}
SET, ELEMENT, TRAITS = codes['集合的含义'], codes['元素与集合的关系'], codes['集合中元素的确定性、互异性、无序性']
fake = FakeModel(codes)
assert api.post('/platform/api/ai-settings', json={'scope': 'team', 'config': {
    'zhongzhan_gpt_key': 'local-test', 'zhongzhan_gpt_base_url': f'http://127.0.0.1:{fake.server.server_port}/v1',
    'prefer_classify_model': 'ZHONGZHAN_GPT/fake-classify'}}).is_success
tree = api.get(f'{bank}/categories').json()
BOOK, CHAPTER = '必修一', next(iter(tree['必修一']))
SECTION = tree[BOOK][CHAPTER][0]


def create(text, section=SECTION):
    response = api.post(f'{bank}/questions', data={'content': text, 'question_type': 'detailed_answer', 'difficulty': '2',
                                                    'category_compulsory': BOOK, 'category_chapter': CHAPTER,
                                                    'category_knowledge': section})
    assert response.status_code == 200, response.text
    return response.json()['question']['id']


def points(question):
    found = api.get(f'{bank}/platform/knowledge-points/questions', params={'ids': question}).json()['questions'][str(question)]
    return [(p['code'], p['role'], p['origin']) for p in found]


tagged = create(f'已标注的题（{suffix}）：写出集合 $\\{{x\\mid x>1\\}}$ 的一个元素。')
plain = create(f'未标注的题（{suffix}）：求 $1+1$。')
first = create(CONTENT)
assert api.put(f'{bank}/platform/knowledge-points/questions/{tagged}',
               json={'points': [{'code': SET, 'role': 'main'}, {'code': ELEMENT, 'role': 'auxiliary'}]}).is_success

checks, errors, requests = [], [], []
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 960})
        page.on('pageerror', lambda e: errors.append(str(e)[:300]))
        page.on('request', lambda r: requests.append(r.url) if '/api/questions?' in r.url else None)
        page.goto(BASE)
        page.locator('#loginForm').get_by_label('账号', exact=True).fill('admin')
        page.locator('#loginForm').get_by_label('密码', exact=True).fill(password)
        page.get_by_role('button', name='登录', exact=True).click()
        page.wait_for_function("document.documentElement.dataset.accountReady==='true'")
        page.goto(BASE + f'/workbench/{space}')
        page.wait_for_function("document.documentElement.dataset.accountReady==='true'")
        page.evaluate("selectWorkspace('bank');loadQuestions()")
        card = page.locator(f'#questionsList [data-id="{tagged}"] .mb-kp-card')
        card.wait_for()
        assert card.inner_text().split() == ['集合的含义', '元素与集合的关系'], card.inner_text()
        assert page.locator(f'#questionsList [data-id="{plain}"] .mb-kp-card').count() == 0
        checks.append('bank cards show a question\'s points, main first')

        page.locator(f'#questionsList [data-id="{tagged}"]').click()
        page.wait_for_function(f"window.EditorState.questionId === {tagged}")
        detail = page.locator('#previewSection .mb-kp-detail')
        detail.locator('.mb-kp-chip').nth(1).wait_for()
        assert detail.inner_text().split() == ['主', '集合的含义', '辅', '元素与集合的关系'], detail.inner_text()
        page.screenshot(path=str(ROOT/'test-results/knowledge-points-detail.png'))
        checks.append('the question detail shows its points')

        page.locator(f'#questionsList [data-id="{first}"]').click()
        panel = page.locator('.mb-kp')
        page.wait_for_function(f"window.EditorState.questionId === {first}")
        assert detail.is_hidden()
        page.locator('#editQuestionFromPreviewBtn').click()
        page.evaluate("switchQuestionEditorPanel('classification')")
        panel.locator('.mb-kp-empty').wait_for()
        common = panel.locator('[data-kp-near]')
        common.get_by_role('button', name='＋集合的含义').click()
        panel.locator('.mb-kp-status', has_text='已保存').wait_for()
        assert points(first) == [(SET, 'main', 'manual')]
        search = page.locator('#mbKpEditorSearch')
        search.fill('元素与集合')
        page.locator('#mbKpEditorResults [role=option]', has_text='元素与集合的关系').wait_for()
        search.press('Enter')
        page.wait_for_function(f"document.querySelectorAll('.mb-kp [data-kp-chips] .mb-kp-chip').length === 2")
        panel.locator('.mb-kp-status', has_text='已保存').wait_for()
        page.wait_for_timeout(300)
        assert points(first) == [(SET, 'main', 'manual'), (ELEMENT, 'auxiliary', 'manual')]
        panel.locator(f'.mb-kp-chip[data-code="{ELEMENT}"] [data-kp-role]').click()
        page.wait_for_timeout(500)
        assert points(first) == [(SET, 'main', 'manual'), (ELEMENT, 'main', 'manual')]
        checks.append('the editor adds points from the section and by search, switches roles, and saves each change')

        panel.locator('[data-kp-ai]').click()
        suggestions = panel.locator('[data-kp-suggestions]')
        offered = suggestions.locator('ul:not(.mb-kp-warnings) > li')
        offered.first.wait_for()
        assert offered.count() == 1 and '1 条建议未采用' in suggestions.inner_text()
        assert '判断 $1$ 与 $A$ 的关系' in suggestions.inner_text()
        page.screenshot(path=str(ROOT/'test-results/knowledge-points-editor.png'))
        suggestions.get_by_role('button', name='采用', exact=True).click()
        page.wait_for_timeout(500)
        assert points(first)[-1] == (TRAITS, 'auxiliary', 'ai')
        assert panel.locator(f'.mb-kp-chip[data-code="{TRAITS}"] .mb-kp-ai-mark').count() == 1
        sent = json.loads(fake.requests[-1]['messages'][1]['content'])
        assert sent['content'] == CONTENT, sent
        panel.locator(f'.mb-kp-chip[data-code="{TRAITS}"] [data-kp-remove]').click()
        page.wait_for_timeout(500)
        assert [code for code, *_ in points(first)] == [SET, ELEMENT]
        checks.append('an AI suggestion with a verbatim quote can be adopted; invented codes are dropped')

        # A new question keeps its points through the first save.
        page.evaluate('startManualQuestion()')
        page.wait_for_function('window.EditorState.questionId === null')
        page.evaluate("switchQuestionEditorPanel('classification')")
        page.wait_for_function("!document.querySelector('.mb-kp [data-kp-chips] .mb-kp-chip')")
        page.evaluate('''text => { const box = document.getElementById('editContent'); box.value = text;
            box.dispatchEvent(new Event('input', {bubbles: true})); }''', f'新题（{suffix}）：写出集合 $B$ 的一个元素。')
        page.select_option('#editCompulsory', BOOK)
        page.select_option('#editChapter', CHAPTER)
        page.select_option('#editKnowledge', SECTION)
        page.select_option('#editQType', 'detailed_answer')
        search.fill('集合的含义')
        page.locator('#mbKpEditorResults [role=option]', has_text='集合的含义').first.wait_for()
        search.press('Enter')
        panel.locator('.mb-kp-status', has_text='保存题目时一并保存').wait_for()
        page.evaluate('saveQuestion()')
        page.wait_for_function('window.EditorState.questionId !== null', timeout=30000)
        created = page.evaluate('window.EditorState.questionId')
        assert points(created) == [(SET, 'main', 'manual')]
        page.wait_for_function(f"document.querySelector('.mb-kp-chip[data-code=\"{SET}\"]')")
        checks.append('a new question\'s points are saved with it')

        # Bank filter: by point (main only by default), with auxiliary uses, and untagged questions.
        page.evaluate('closeQuestionEditorModal()')
        page.locator('.bank-filter-toggle').click()
        bank_filter = page.locator('.mb-kp-filter[data-kp-scope="bank"]')
        bank_filter.locator('input[type=search]').fill('元素与集合的关系')
        bank_filter.locator('[role=option]', has_text='元素与集合的关系').first.wait_for()
        bank_filter.locator('input[type=search]').press('Enter')
        ids = lambda: sorted(int(i) for i in page.eval_on_selector_all('#questionsList .bank-question-card[data-id]', 'cs => cs.map(c => c.dataset.id)'))
        page.wait_for_function(f"document.querySelectorAll('#questionsList .bank-question-card').length === 1")
        assert ids() == [first]
        bank_filter.get_by_label('含辅助方法').check()
        page.wait_for_function(f"document.querySelectorAll('#questionsList .bank-question-card').length === 2")
        assert ids() == sorted([first, tagged])
        page.screenshot(path=str(ROOT/'test-results/knowledge-points-bank-filter.png'))
        bank_filter.get_by_label('只看未标注').check()
        page.wait_for_function(f"document.querySelector('#questionsList .bank-question-card[data-id=\"{plain}\"]')")
        assert ids() == [plain]
        page.evaluate('clearBankFilters()')
        page.wait_for_function("document.querySelectorAll('#questionsList .bank-question-card').length === 4")
        assert not page.locator('.mb-kp-filter[data-kp-scope="bank"] .mb-kp-chip').count()
        checks.append('the bank list filters by point, with auxiliary uses, and by untagged')

        # Paper question stream.
        page.evaluate("selectWorkspace('paper')")
        page.wait_for_function("document.querySelector('#paperAdvancedFilters')")
        if page.locator('#paperAdvancedFilters').is_hidden():
            page.evaluate('togglePaperFilterBar()')
        paper_filter = page.locator('.mb-kp-filter[data-kp-scope="paper"]')
        paper_filter.locator('input[type=search]').fill('集合的含义')
        paper_filter.locator('[role=option]', has_text='集合的含义').first.wait_for()
        paper_filter.locator('input[type=search]').press('Enter')
        page.wait_for_function("window.PaperStore.bankQuestions.length === 3 && !window.PaperStore.streamPagination.all.loading")
        assert any('knowledge_point_codes=' + SET in url for url in requests if 'page=' in url)
        page.screenshot(path=str(ROOT/'test-results/knowledge-points-paper-filter.png'))
        page.evaluate('clearPaperFilters()')
        page.wait_for_function("window.PaperStore.bankQuestions.length === 4")
        checks.append('the paper question stream filters by point and clears with its filters')

        page.evaluate("selectWorkspace('bank')")
        page.locator(f'#questionsList [data-id="{first}"]').click()
        page.wait_for_function(f"window.EditorState.questionId === {first}")
        page.locator('#editQuestionFromPreviewBtn').click()
        page.evaluate("switchQuestionEditorPanel('classification')")
        page.evaluate('toggleDarkMode()')
        page.wait_for_function("document.documentElement.classList.contains('dark')")
        panel.locator('.mb-kp-chip').first.wait_for()
        page.wait_for_timeout(600)
        page.screenshot(path=str(ROOT/'test-results/knowledge-points-editor-dark.png'), animations='disabled')
        page.evaluate('toggleDarkMode()')
        page.set_viewport_size({'width': 390, 'height': 844})
        page.wait_for_timeout(300)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        page.set_viewport_size({'width': 1440, 'height': 960})
        checks.append('no horizontal overflow at phone width')
        assert not errors, errors
        browser.close()
finally:
    fake.server.shutdown()
    api.post('/platform/api/ai-settings', json={'scope': 'team', 'config': {
        'zhongzhan_gpt_key': '', 'zhongzhan_gpt_base_url': '', 'prefer_classify_model': ''}})

for check in checks:
    print('PASS:', check)
