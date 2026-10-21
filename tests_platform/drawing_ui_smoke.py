"""绘图 workspace in a real browser, offline: the self-hosted GeoGebra loads with no outside request,
the AI conversation draws (with one automatic fix of a failed command), a point dragged by hand keeps
the construction's relations, an AI step can be undone, autosave survives a reload, files export, and
the question editor inserts a saved drawing as its own image."""
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
assert (ROOT/'platform_app/vendor/geogebra/5.4.933/deployggb.js').exists(), 'Run scripts/fetch_geogebra.py first'
password = (DATA/'initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：', 1)[1]
suffix = str(time.time_ns())[-8:]
ready = "document.documentElement.dataset.accountReady==='true' && document.documentElement.dataset.drawingsReady==='true'"
applet = "window.mbDrawingApplet && typeof window.mbDrawingApplet.evalCommand === 'function'"

FIRST = {'reply': '画好了△ABC 和中线 AD。', 'reset': True, 'view': None,
         'commands': ['A=(0,0)', 'B=(6,0)', 'C=(2,4)', 'polyABC=Polygon(A,B,C)', 'D=Midpoint(B,C)', 'segAD=Segment(A,D)',
                      'SetColor(segAD, "Black")', 'SetLineStyle(segAD, 1)', 'ShowLabel(segAD, false)', 'E=Foo(A)']}
FIX = {'reply': '改用中点。', 'reset': False, 'view': None, 'commands': ['E=Midpoint(A,B)']}
MOVE = {'reply': '已把 C 移到 (2, 5)。', 'reset': False, 'view': None, 'commands': ['SetCoords(C, 2, 5)']}


class FakeModel:
    def __init__(self):
        self.requests = []
        model = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                model.requests.append(body)
                last = body['messages'][-1]['content']
                text = last if isinstance(last, str) else last[0]['text']
                answer = FIX if '执行失败' in text else MOVE if '移到' in text else FIRST
                data = json.dumps({'choices': [{'message': {'content': json.dumps(answer, ensure_ascii=False)},
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


fake = FakeModel()
api = httpx.Client(base_url=BASE, trust_env=False, timeout=120)
assert api.post('/platform/api/login', json={'username': 'admin', 'password': password}).is_success
api.headers['X-Local-Token'] = api.get('/platform/api/me').json()['csrf']
space = api.post('/platform/api/spaces', json={'name': '绘图'+suffix, 'kind': 'personal', 'textbook_id': 1}).json()['id']
assert api.post('/platform/api/ai-settings', json={'scope': 'team', 'config': {
    'zhongzhan_gpt_key': 'local-test', 'zhongzhan_gpt_base_url': f'http://127.0.0.1:{fake.server.server_port}/v1',
    'prefer_draw_model': 'ZHONGZHAN_GPT/fake-draw'}}).is_success

checks, errors, outside = [], [], []


def coords(page, name):
    return page.evaluate(f"[mbDrawingApplet.getXcoord('{name}'), mbDrawingApplet.getYcoord('{name}')]")


def close(a, b):
    return all(abs(x - y) < 1e-6 for x, y in zip(a, b))


def screen(page, name):
    """Where a point is drawn: GeoGebra's view properties map coordinates to the graphics canvas."""
    return page.evaluate(f'''() => {{
        const view = JSON.parse(mbDrawingApplet.getViewProperties(1));
        const canvas = [...document.querySelectorAll('#mbDrawingHost canvas')].find(c => c.parentElement.className.includes('EuclidianPanel'));
        const box = canvas.getBoundingClientRect();
        const x = mbDrawingApplet.getXcoord('{name}'), y = mbDrawingApplet.getYcoord('{name}');
        return [box.left + (x - view.xMin) / view.invXscale, box.top + view.height - (y - view.yMin) / view.invYscale];
    }}''')


def wait_answer(page, count):
    page.wait_for_function(f"document.querySelectorAll('.mb-dr-assistant:not(.mb-dr-waiting)').length >= {count}", timeout=60000)


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 960}, accept_downloads=True)
        context.route('**/*', lambda r: r.continue_() if r.request.url.startswith(BASE) or r.request.url.startswith(('data:', 'blob:'))
                      else (outside.append(r.request.url[:120]), r.abort()))
        page = context.new_page()
        page.on('pageerror', lambda e: errors.append(str(e)[:300]))
        page.goto(BASE)
        page.locator('#loginForm').get_by_label('账号', exact=True).fill('admin')
        page.locator('#loginForm').get_by_label('密码', exact=True).fill(password)
        page.get_by_role('button', name='登录', exact=True).click()
        page.wait_for_function("document.documentElement.dataset.accountReady==='true'")
        page.goto(BASE + f'/workbench/{space}')
        page.wait_for_function(ready)

        page.locator('#appNavDrawings').click()
        page.get_by_text('还没有绘图').wait_for()
        page.get_by_role('button', name='新建绘图').click()
        page.wait_for_function(applet, timeout=120000)
        assert page.evaluate("mbDrawingApplet.getAllObjectNames().length") == 0
        checks.append('self-hosted GeoGebra Classic loads with no outside request')

        prompt = page.get_by_label('告诉 AI 要画什么')
        prompt.fill('画三角形 ABC，D 为 BC 中点，连接 AD')
        prompt.press('Enter')
        wait_answer(page, 1)
        assistant = page.locator('.mb-dr-assistant').last
        assert '已自动修正一次' in assistant.inner_text() and '没能执行' not in assistant.inner_text(), assistant.inner_text()
        # Style commands return false from evalCommand but did work; only Foo was sent back for a fix.
        assert page.evaluate("[mbDrawingApplet.getLineStyle('segAD'), mbDrawingApplet.getLabelVisible('segAD')]") == [1, False]
        assert not page.evaluate("mbDrawingApplet.getLabelVisible('a')")  # Polygon's own side names stay hidden
        fix_request = next(r for r in fake.requests if '执行失败' in json.dumps(r['messages'][-1], ensure_ascii=False))
        assert 'E=Foo(A)' in json.dumps(fix_request['messages'][-1], ensure_ascii=False)
        assert 'SetColor' not in json.dumps(fix_request['messages'][-1], ensure_ascii=False)
        names = set(page.evaluate('mbDrawingApplet.getAllObjectNames()'))
        assert {'A', 'B', 'C', 'D', 'E', 'polyABC', 'segAD'} <= names, names
        assert close(coords(page, 'D'), (4, 2)) and close(coords(page, 'E'), (3, 0))
        assert any('执行失败' in json.dumps(r['messages'][-1], ensure_ascii=False) for r in fake.requests)
        first_request = fake.requests[0]['messages']
        assert first_request[0]['role'] == 'system' and '画三角形 ABC' in first_request[-1]['content']
        checks.append('AI draws from a request and fixes a failed command once by itself')

        # By hand: drag B; D stays the midpoint of BC because it was constructed, not placed.
        page.evaluate('mbDrawingApplet.setMode(0)')
        x, y = screen(page, 'B')
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 40, y - 30, steps=8)
        page.mouse.up()
        moved, c, d = coords(page, 'B'), coords(page, 'C'), coords(page, 'D')
        assert not close(moved, (6, 0)), moved
        assert close(d, ((moved[0] + c[0]) / 2, (moved[1] + c[1]) / 2)), (moved, c, d)
        checks.append('a point dragged by hand keeps the constructed relations')
        page.screenshot(path=str(ROOT/'test-results/drawing-workspace.png'))
        page.evaluate('window.toggleDarkMode()')
        page.wait_for_timeout(700)  # let colour transitions finish
        page.screenshot(path=str(ROOT/'test-results/drawing-workspace-dark.png'))
        assert page.evaluate("getComputedStyle(document.querySelector('.mb-dr-toolbar .mb-button')).backgroundColor") != 'rgb(255, 255, 255)'
        page.evaluate('window.toggleDarkMode()')

        prompt.fill('把 C 移到 (2,5)')
        page.get_by_role('button', name='发送').click()
        wait_answer(page, 2)
        assert close(coords(page, 'C'), (2, 5))
        page.locator('.mb-dr-assistant').last.get_by_role('button', name='撤销这一步').click()
        page.wait_for_function("Math.abs(mbDrawingApplet.getYcoord('C') - 4) < 1e-9")
        checks.append('an AI step can be undone')

        page.get_by_label('绘图标题').fill('三角形中线' + suffix)
        page.wait_for_function("document.querySelector('.mb-dr-save').textContent.startsWith('已保存')", timeout=30000)
        # A style edit alone (as from GeoGebra's style bar) is a change that autosaves.
        page.evaluate("mbDrawingApplet.evalCommand('SetColor(segAD, \"Red\")')")
        page.wait_for_function("document.querySelector('.mb-dr-save').textContent.startsWith('已保存') === false")
        page.wait_for_function("document.querySelector('.mb-dr-save').textContent.startsWith('已保存')", timeout=30000)
        before_reload = coords(page, 'B')
        page.reload()
        page.wait_for_function(ready)
        page.locator('#appNavDrawings').click()
        card = page.locator('.mb-dr-card', has_text='三角形中线' + suffix)
        card.wait_for()
        page.wait_for_function("[...document.querySelectorAll('.mb-dr-thumb img')].some(i => i.complete && i.naturalWidth > 0)")
        card.locator('.mb-dr-card-open').click()
        page.wait_for_function(applet, timeout=120000)
        page.wait_for_function("mbDrawingApplet.exists('E')")
        assert close(coords(page, 'B'), before_reload) and close(coords(page, 'C'), (2, 4))
        assert page.evaluate("mbDrawingApplet.getColor('segAD')").upper() == '#FF0000'
        assert page.locator('.mb-dr-assistant').count() == 2 and '（已撤销）' in page.locator('.mb-dr-assistant').last.inner_text()
        checks.append('autosaved drawing, conversation and thumbnail survive a reload')

        with page.expect_download() as download:
            page.get_by_role('button', name='下载 PNG 图片').click()
        assert download.value.suggested_filename == f'三角形中线{suffix}.png'
        with page.expect_download() as download:
            page.get_by_role('button', name='下载 GeoGebra 文件，可用桌面版打开').click()
        assert download.value.suggested_filename.endswith('.ggb')
        checks.append('PNG and .ggb downloads')

        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        page.set_viewport_size({'width': 1440, 'height': 960})
        page.get_by_role('button', name='绘图列表').click()

        # The question editor inserts a copy of the saved drawing.
        page.locator('#appNavBank').click() if page.locator('#appNavBank').count() else None
        page.evaluate('startManualQuestion()')
        page.locator('#editorSection').wait_for(state='visible')
        insert = page.locator('[data-drawing-insert="uploadIllustration"]')
        insert.scroll_into_view_if_needed()
        page.screenshot(path=str(ROOT/'test-results/drawing-insert-button.png'))
        insert.click()
        page.locator('#drawingPicker .mb-dr-card').first.wait_for()
        page.screenshot(path=str(ROOT/'test-results/drawing-picker.png'))
        picker = page.locator('#drawingPicker')
        picker.locator('.mb-dr-card', has_text='三角形中线' + suffix).click()
        page.wait_for_function("document.getElementById('editContent').value.includes('![插图](')", timeout=30000)
        inserted = page.evaluate('uploadedImages[uploadedImages.length - 1]')
        assert inserted and '/static/uploads/' in inserted, inserted
        assert api.get(inserted).status_code == 200
        assert page.locator('[data-drawing-insert="uploadAnswerImage"]').count() == 1
        checks.append('the question editor inserts a saved drawing as its own uploaded image')

        assert not outside, outside[:5]
        assert not errors, errors
        browser.close()
finally:
    fake.server.shutdown()
    api.post('/platform/api/ai-settings', json={'scope': 'team', 'config': {
        'zhongzhan_gpt_key': '', 'zhongzhan_gpt_base_url': '', 'prefer_draw_model': ''}})

for check in checks:
    print('PASS:', check)
