"""Import cards in a real browser: the per-question classification review from the import check
(ported from MathBank 3.0.0). A label that matched no catalog entry starts unselected and explains
why; picking a chapter, or keeping the matched chapter whole, selects it again and it saves."""
from pathlib import Path
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
    space = api.post('/platform/api/spaces', json={'name': '分类核对'+suffix, 'kind': 'personal', 'textbook_id': 1}).json()['id']

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
    page.goto(BASE+f'/workbench/{space}')
    page.wait_for_function(ready)
    page.locator('#appNavImport').click()
    page.wait_for_function('typeof categoryTree === "object" && Object.keys(categoryTree).length > 0')
    names = page.evaluate("""() => {
        const book = Object.keys(categoryTree).find(b => Object.values(categoryTree[b]).some(s => s.length));
        const chapter = Object.keys(categoryTree[book]).find(c => categoryTree[book][c].length);
        const other = Object.keys(categoryTree[book]).find(c => c !== chapter) || chapter;
        return {book, chapter, other, section: categoryTree[book][chapter][0]};
    }""")
    page.evaluate("""(n) => {
        const review = (required, original, extra = {}) => ({required, original, message: required
            ? '章节不在已确认的当前目录范围内，请人工选择。' : '章节名称已按当前目录规范化。', reason_codes: [], ...extra});
        const records = [
            {content: '分类核对一：求 $x$。' + n.book, question_type: 'detailed_answer', difficulty: '2',
             category_compulsory: n.book, category_chapter: '', category_knowledge: '',
             classification_review: review(true, {category_compulsory: n.book, category_chapter: '第九十九章 不存在的章', category_knowledge: ''})},
            {content: '分类核对二：求 $y$。', question_type: 'detailed_answer', difficulty: '2',
             category_compulsory: n.book, category_chapter: n.chapter, category_knowledge: '',
             classification_review: review(true, {category_compulsory: n.book, category_chapter: n.chapter, category_knowledge: '不存在的小节'},
                                           {candidates: {category_knowledge: [n.section]}})},
            {content: '分类核对三：求 $z$。', question_type: 'detailed_answer', difficulty: '2',
             category_compulsory: n.book, category_chapter: n.chapter, category_knowledge: n.section,
             classification_review: review(false, {category_compulsory: n.book, category_chapter: n.chapter.replace(' ', ''), category_knowledge: n.section},
                                           {normalized_fields: ['category_chapter']})},
            {content: '分类核对四：求 $w$。', question_type: 'detailed_answer', difficulty: '2',
             category_compulsory: n.book, category_chapter: n.chapter, category_knowledge: ''},
        ];
        replaceParsedQuestions(records);
        appendSourceIntegrityLog({});
        renderParsedQuestionsList(records);
        // What a finished parse does next (import.js): reveal the cards.
        document.getElementById('importLoadingState').classList.add('hidden');
        document.getElementById('parsedQuestionsWrapper').classList.remove('hidden');
    }""", names)
    page.locator('#parsed-card-0').scroll_into_view_if_needed()

    def card(index):
        return page.locator(f'#parsed-card-{index}')

    checked = [card(i).locator('.card-select-checkbox').is_checked() for i in range(4)]
    assert checked == [False, False, True, True], checked
    assert card(3).locator('.card-classification-review').count() == 0
    first = card(0).locator('.card-classification-review')
    assert first.locator('.card-classification-status').inner_text() == '分类待人工选择'
    assert '人工选择' in first.locator('.card-classification-message').inner_text()
    assert first.locator('.card-classification-confirm').is_hidden()  # no chapter yet: nothing to keep
    first.locator('summary').click()
    assert '第九十九章 不存在的章' in first.locator('.card-classification-original').inner_text()
    second = card(1).locator('.card-classification-review')
    assert second.locator('.card-classification-confirm').is_visible()
    assert names['section'] in second.locator('.card-classification-original').text_content()
    third = card(2).locator('.card-classification-review')
    assert third.locator('.card-classification-status').inner_text() == '分类名称已按当前目录对齐'
    assert third.locator('.card-classification-message').is_hidden()
    assert '2 道题的分类对不上当前教材目录' in page.locator('#importLogsConsole').inner_text()
    checks.append('unmatched labels start unselected with the reason, the original and the candidates; aligned ones say so')
    card(1).scroll_into_view_if_needed()
    card(1).screenshot(path=str(ROOT/'test-results/classification-review-pending.png'))
    page.evaluate('window.toggleDarkMode()')
    card(1).screenshot(path=str(ROOT/'test-results/classification-review-pending-dark.png'))
    page.evaluate('window.toggleDarkMode()')

    card(0).locator('.card-chapter').select_option(names['other'])
    assert first.locator('.card-classification-status').inner_text() == '分类已人工选择'
    assert card(0).locator('.card-select-checkbox').is_checked()
    second.locator('.card-classification-confirm').click()
    assert second.locator('.card-classification-status').inner_text() == '分类已人工选择'
    assert card(1).locator('.card-select-checkbox').is_checked()
    assert card(1).locator('.card-chapter').input_value() == names['chapter']
    checks.append('picking a chapter, or keeping the matched chapter whole, resolves the card and selects it')

    page.screenshot(path=str(ROOT/'test-results/classification-review.png'), full_page=False)
    page.evaluate('window.toggleDarkMode()')
    panel = page.evaluate("getComputedStyle(document.querySelector('#parsed-card-0 .card-classification-review')).color")
    page.evaluate('window.toggleDarkMode()')
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    page.set_viewport_size({'width': 1440, 'height': 960})
    checks.append(f'dark mode panel text {panel}; 390px without horizontal overflow')

    assert page.evaluate('saveParsedQuestion(0)') is True
    page.wait_for_function("document.querySelector('#parsed-card-0 .card-select-checkbox').disabled")
    with httpx.Client(base_url=BASE, trust_env=False, timeout=60) as api:
        assert api.post('/platform/api/login', json={'username': 'admin', 'password': password}).is_success
        saved = api.get(f'/bank/{space}/api/questions').json()
    saved = next(q for q in saved if q['content'].startswith('分类核对一'))
    assert (saved['category_compulsory'], saved['category_chapter']) == (names['book'], names['other']), saved
    checks.append('the resolved card saves with the chapter the teacher picked')
    assert not errors, errors
    browser.close()

for check in checks:
    print('PASS:', check)
