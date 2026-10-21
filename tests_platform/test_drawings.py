"""绘图: storage, permissions, the cleaned GeoGebra file and PNG, and the AI turn through a real worker."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import base64
import io
import json
import os
import sqlite3
import threading
import zipfile

import pytest
from PIL import Image

os.environ['NO_PROXY'] = os.environ['no_proxy'] = '127.0.0.1,localhost'
from test_platform import create_user, login_user, new_space, system  # noqa: E402,F401
from platform_app import drawings as drawing_module, migrations, store  # noqa: E402


def drawings(space):
    return f'/platform/api/spaces/{space}/drawings'


def png(size=(400, 300), box=(150, 100, 250, 200), fmt='PNG'):
    image = Image.new('RGB', size, 'white')
    image.paste((0, 0, 0), box)
    out = io.BytesIO()
    image.save(out, fmt)
    return base64.b64encode(out.getvalue()).decode()


def ggb(xml='<geogebra><construction><element type="point" label="A"/></construction></geogebra>', script='alert(1)'):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr('geogebra.xml', xml)
        archive.writestr('geogebra_javascript.js', script)
        archive.writestr('geogebra_thumbnail.png', base64.b64decode(png()))
    return base64.b64encode(out.getvalue()).decode()


def test_drawings_save_with_revisions_and_clean_files(system):  # noqa: F811
    space = new_space(system, '绘图存取')['id']
    created = system.post(drawings(space), json={'title': '  '}).json()
    assert created['title'] == '未命名绘图' and created['revision'] == 1 and not created['has_image'] and created['ggb'] == ''
    did = created['id']
    assert system.get(f'{drawings(space)}/{did}/image.png').status_code == 404
    xml = ('<geogebra><construction><element type="point" label="A"><javascript val="alert(2)" onUpdate="alert(3)"/>'
           '<ggbscript val="SetValue(a,1)"/></element></construction></geogebra>')
    chat = [{'role': 'user', 'text': '画三角形'}, {'role': 'assistant', 'text': '已画好', 'commands': ['A=(0,0)']}]
    saved = system.put(f'{drawings(space)}/{did}', json={'title': '三角形', 'revision': 1, 'ggb': ggb(xml), 'png': png(),
                                                         'chat': chat})
    assert saved.status_code == 200, saved.text
    assert saved.json()['revision'] == 2 and saved.json()['has_image']
    # Another browser opens this file: no JavaScript survives, GeoGebra scripts do.
    full = system.get(f'{drawings(space)}/{did}').json()
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(full['ggb']))) as archive:
        assert archive.read('geogebra_javascript.js') == b'function ggbOnInit() {}'
        stored = archive.read('geogebra.xml').decode()
    assert 'alert' not in stored and '<ggbscript val="SetValue(a,1)"/>' in stored
    assert full['chat'] == chat
    # White margins are trimmed to a small border around the drawing.
    image = system.get(f'{drawings(space)}/{did}/image.png')
    assert image.headers['content-type'] == 'image/png'
    width, height = Image.open(io.BytesIO(image.content)).size
    assert (width, height) == (100 + 2 * drawing_module.TRIM_PADDING,) * 2
    assert (full['width'], full['height']) == (width, height)
    # A stale revision never overwrites a newer save.
    stale = system.put(f'{drawings(space)}/{did}', json={'title': '旧', 'revision': 1, 'ggb': '', 'png': ''})
    assert stale.status_code == 409
    for bad in ({'png': png(fmt='JPEG')}, {'png': 'not base64!'}, {'ggb': base64.b64encode(b'plain').decode()},
                {'ggb': ggb().replace('A', 'B', 0)[:-8]}, {'title': 'x' * 81}, {'chat': [{'role': 'system', 'text': 'x'}]}):
        response = system.put(f'{drawings(space)}/{did}', json={'title': 't', 'revision': 2, **bad})
        assert response.status_code == 400, bad
    renamed = system.patch(f'{drawings(space)}/{did}', json={'title': '等腰三角形'}).json()
    assert renamed['title'] == '等腰三角形' and renamed['revision'] == 2
    copied = system.post(f'{drawings(space)}/{did}/copy').json()
    assert copied['title'] == '等腰三角形 副本' and copied['has_image']
    assert [d['title'] for d in system.get(drawings(space)).json()] == ['等腰三角形 副本', '等腰三角形']
    assert 'ggb' not in system.get(drawings(space)).json()[0]
    assert system.delete(f'{drawings(space)}/{copied["id"]}').json() == {'status': 'success'}
    assert system.get(f'{drawings(space)}/{copied["id"]}').status_code == 404
    other = new_space(system, '绘图存取二')['id']
    assert system.get(f'{drawings(other)}/{did}').status_code == 404


def test_viewers_see_drawings_but_cannot_change_them_or_use_ai(system):  # noqa: F811
    space = new_space(system, '绘图权限', 'team')['id']
    did = system.post(drawings(space), json={'title': '共享绘图', 'png': png()}).json()['id']
    create_user(system, 'drawing_viewer')
    viewer = login_user('drawing_viewer', system)
    assert [d['id'] for d in viewer.get(drawings(space)).json()] == [did]
    assert viewer.get(f'{drawings(space)}/{did}').status_code == 200
    assert viewer.get(f'{drawings(space)}/{did}/image.png').status_code == 200
    assert viewer.post(drawings(space), json={'title': '越权'}).status_code == 403
    assert viewer.put(f'{drawings(space)}/{did}', json={'title': 'x', 'revision': 1}).status_code == 403
    assert viewer.patch(f'{drawings(space)}/{did}', json={'title': 'x'}).status_code == 403
    assert viewer.post(f'{drawings(space)}/{did}/copy').status_code == 403
    assert viewer.delete(f'{drawings(space)}/{did}').status_code == 403
    assert viewer.post(drawings(space) + '/ai', json={'messages': [{'role': 'user', 'text': '画圆'}]}).status_code == 403
    assert viewer.get(drawings(new_space(system, '绘图私有')['id'])).status_code == 404


class FakeModel:
    """An OpenAI-compatible drawing model answering with GeoGebra commands."""

    def __init__(self):
        self.requests, self.answer = [], {
            'reply': '已画出三角形。', 'reset': True, 'view': {'axes': False, 'xmin': -1, 'xmax': 7, 'ymin': -1, 'ymax': 5, 'bad': 1},
            'commands': ['A=(0,0)', 'B=(4,0)\nC=(1,3)', 'SetClickScript(A, "x")', 'RunJavaScript("alert(1)")', 7,
                         'Execute({"D=(1,1)"})', 'polyABC=Polygon(A,B,C)']}
        model = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                model.requests.append(body)
                data = json.dumps({'choices': [{'message': {'content': json.dumps(model.answer, ensure_ascii=False)},
                                                'finish_reason': 'stop'}],
                                   'usage': {'prompt_tokens': 30, 'completion_tokens': 20, 'total_tokens': 50}}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def test_ai_turn_returns_safe_geogebra_commands_from_the_drawing_model(system):  # noqa: F811
    fake = FakeModel()
    saved = system.post('/platform/api/ai-settings', json={'scope': 'team', 'config': {
        'zhongzhan_gpt_key': 'local-test', 'zhongzhan_gpt_base_url': f'http://127.0.0.1:{fake.server.server_port}/v1',
        'prefer_draw_model': 'ZHONGZHAN_GPT/fake-draw'}})
    assert saved.status_code == 200, saved.text
    try:
        space = new_space(system, '绘图 AI')['id']
        reference = 'data:image/png;base64,' + png()
        response = system.post(drawings(space) + '/ai', json={
            'messages': [{'role': 'user', 'text': '画三角形 ABC'}, {'role': 'assistant', 'text': '好的', 'commands': ['A=(0,0)']},
                         {'role': 'user', 'text': '再按参考图画一遍'}],
            'construction': 'A（point）：(0, 0)', 'image': reference})
        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer['commands'] == ['A=(0,0)', 'B=(4,0)', 'C=(1,3)', 'polyABC=Polygon(A,B,C)']
        assert answer['dropped'] == 4 and answer['reset'] is True and answer['reply'] == '已画出三角形。'
        assert answer['view'] == {'axes': False, 'xmin': -1, 'xmax': 7, 'ymin': -1, 'ymax': 5}
        assert answer['usage']['total_tokens'] == 50
        sent = fake.requests[-1]
        assert sent['model'] == 'fake-draw' and sent['messages'][0]['role'] == 'system'
        assert 'GeoGebra' in sent['messages'][0]['content'] and '单个小写字母' in sent['messages'][0]['content']
        assert sent['messages'][2]['content'].endswith('A=(0,0)）')  # earlier commands go back as context
        last = sent['messages'][-1]['content']
        assert last[1] == {'type': 'image_url', 'image_url': {'url': reference}}
        assert 'A（point）：(0, 0)' in last[0]['text'] and '再按参考图画一遍' in last[0]['text']
        for bad in ({'messages': []}, {'messages': [{'role': 'assistant', 'text': 'x'}]},
                    {'messages': [{'role': 'user', 'text': 'x'}], 'image': 'data:text/plain;base64,eA=='},
                    {'messages': [{'role': 'user', 'text': 'x'}], 'image': 'data:image/png;base64,' + base64.b64encode(b'no').decode()},
                    {'messages': [{'role': 'user', 'text': 'x'}], 'construction': 'x' * 30001}):
            assert system.post(drawings(space) + '/ai', json=bad).status_code == 400, bad
        fake.answer = 'not json'
        failed = system.post(drawings(space) + '/ai', json={'messages': [{'role': 'user', 'text': '画圆'}]})
        assert failed.status_code == 502 and '指令' in failed.json()['detail']
    finally:
        fake.server.shutdown()
        system.post('/platform/api/ai-settings', json={'scope': 'team', 'config': {
            'zhongzhan_gpt_key': '', 'zhongzhan_gpt_base_url': '', 'prefer_draw_model': ''}})


def test_geogebra_files_are_cached_and_the_page_loads_the_workspace(system):  # noqa: F811
    space = new_space(system, '绘图页面')['id']
    page = system.get(f'/workbench/{space}').text
    assert '/platform/web/drawings.js' in page
    bundle = store.ROOT / 'platform_app/vendor/geogebra/5.4.933/deployggb.js'
    if not bundle.exists():
        pytest.skip('GeoGebra bundle not fetched (scripts/fetch_geogebra.py)')
    response = system.get('/platform/geogebra/5.4.933/deployggb.js')
    assert response.status_code == 200 and 'GGBApplet' in response.text
    assert response.headers['cache-control'] == 'public, max-age=31536000, immutable'
    assert system.get('/platform/web/drawings.js').headers['cache-control'] == 'no-store'
    script = (store.ROOT / 'platform_app/web/drawings.js').read_text(encoding='utf-8')
    assert f"'/platform/geogebra/{drawing_module.GEOGEBRA_VERSION}/'" in script


def test_drawings_table_added_to_an_existing_install(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    store.initialize()
    with sqlite3.connect(tmp_path / 'platform.db') as c:
        c.execute('DROP TABLE drawings')
        c.execute("DELETE FROM platform_migrations WHERE name='drawings-v1'")
    store.initialize()
    assert store.rows('SELECT count(*) AS n FROM drawings') == [{'n': 0}]
    assert {r['name'] for r in store.rows('SELECT name FROM platform_migrations')} == set(migrations.NAMES)
    assert len(list((tmp_path / 'migration_backups').glob('before-add-drawings-*.db'))) == 1
