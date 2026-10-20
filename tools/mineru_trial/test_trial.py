"""MinerU client and trial page against a simulated MinerU API (no key or network needed).

    python -m pytest -q tools/mineru_trial
"""
from pathlib import Path
import io
import json
import sys
import time
import zipfile

import fitz
import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import app as trial  # noqa: E402
from platform_app.mineru_client import MinerUClient, MinerUError, Options  # noqa: E402

MARKDOWN = '# 第一次月考\n\n1. 已知 $x^2=4$，求 $x$。\n\n$$\\int_0^1 x\\,dx=\\frac12$$\n\n![](images/fig.jpg)\n\n<table><tr><td>1</td></tr></table>\n'


def result_zip(extra=()):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('full.md', MARKDOWN)
        archive.writestr('images/fig.jpg', b'jpeg')
        archive.writestr('demo_content_list.json', '[]')
        for name in extra:
            archive.writestr(name, 'x')
    return buffer.getvalue()


def fake_mineru(polls=1, zip_bytes=None, token='good-key'):
    """Mimic the documented batch flow: presign, PUT, poll, zip."""
    seen = {'requests': [], 'uploaded': None, 'polls': 0}

    def handler(request):
        seen['requests'].append(request)
        url = str(request.url)
        if url.startswith('https://mineru.net/api/v4/'):
            if request.headers.get('authorization') != f'Bearer {token}':
                return httpx.Response(200, json={'code': 'A0202', 'msg': 'token error'})
            if request.url.path.endswith('/file-urls/batch'):
                seen['submitted'] = json.loads(request.content)
                return httpx.Response(200, json={'code': 0, 'data': {'batch_id': 'b1', 'file_urls': ['https://oss.example/upload/b1']}})
            if request.url.path.endswith('/extract-results/batch/b1'):
                seen['polls'] += 1
                state = 'done' if seen['polls'] > polls else 'running'
                item = {'file_name': 'paper.pdf', 'state': state, 'extract_progress': {'extracted_pages': 1, 'total_pages': 2}}
                if state == 'done':
                    item['full_zip_url'] = 'https://cdn.example/result.zip'
                return httpx.Response(200, json={'code': 0, 'data': {'batch_id': 'b1', 'extract_result': [item]}})
        if url == 'https://oss.example/upload/b1' and request.method == 'PUT':
            assert 'authorization' not in request.headers and 'content-type' not in request.headers
            seen['uploaded'] = request.content
            return httpx.Response(200)
        if url == 'https://cdn.example/result.zip':
            return httpx.Response(200, content=zip_bytes or result_zip())
        return httpx.Response(404)
    return httpx.MockTransport(handler), seen


def test_client_runs_the_documented_flow(tmp_path):
    transport, seen = fake_mineru(polls=2)
    source = tmp_path/'paper.pdf'
    source.write_bytes(b'%PDF-1.4 test')
    client = MinerUClient('good-key', transport=transport)
    batch = client.submit(source, Options(is_ocr=True, page_ranges='1-2'))
    progress = []
    result = client.wait(batch, on_progress=lambda r: progress.append(r['state']), interval=0)
    out = client.download(result['full_zip_url'], tmp_path/'result')
    assert seen['uploaded'] == b'%PDF-1.4 test'
    assert seen['submitted'] == {'files': [{'name': 'paper.pdf', 'is_ocr': True, 'page_ranges': '1-2'}], 'model_version': 'vlm',
                                 'enable_formula': True, 'enable_table': True, 'language': 'ch'}
    assert progress == ['running', 'running', 'done']
    assert (out/'full.md').read_text(encoding='utf-8') == MARKDOWN and (out/'images/fig.jpg').exists()


def test_client_explains_bad_key_and_rejects_unsafe_zip(tmp_path):
    transport, _ = fake_mineru(token='other')
    source = tmp_path/'paper.pdf'
    source.write_bytes(b'%PDF')
    with pytest.raises(MinerUError, match='API Key 无效'):
        MinerUClient('good-key', transport=transport).submit(source, Options())
    # The live service answers a bad key with a bare HTTP 401.
    unauthorized = httpx.MockTransport(lambda request: httpx.Response(401, json={'msg': 'user authenticate failed'}))
    with pytest.raises(MinerUError, match='API Key 无效或已过期'):
        MinerUClient('bad', transport=unauthorized).status('b1')
    transport, _ = fake_mineru(zip_bytes=result_zip(['../escape.txt']))
    with pytest.raises(MinerUError, match='非法路径'):
        MinerUClient('good-key', transport=transport).download('https://cdn.example/result.zip', tmp_path/'result')
    assert not (tmp_path/'escape.txt').exists()


def test_trial_page_parses_and_serves_results(tmp_path, monkeypatch):
    transport, seen = fake_mineru()
    monkeypatch.setattr(trial, 'RUNS', tmp_path)
    monkeypatch.setattr(trial, 'MinerUClient', lambda token: MinerUClient(token, transport=transport))
    monkeypatch.setattr(trial, 'POLL_SECONDS', 0)
    monkeypatch.delenv('MINERU_API_KEY', raising=False)
    document = fitz.open()
    for _ in range(3):
        document.new_page().insert_text((72, 72), 'page')
    pdf = document.tobytes()
    client = TestClient(trial.app)
    assert client.post('/api/runs', files={'file': ('月考.pdf', pdf)}).status_code == 400
    assert client.post('/api/runs', data={'token': 'k', 'page_ranges': '1-'}, files={'file': ('a.pdf', pdf)}).status_code == 400
    response = client.post('/api/runs', data={'token': 'good-key', 'page_ranges': '2-3', 'is_ocr': 'true', 'enable_table': 'false'},
                           files={'file': ('月考.pdf', pdf, 'application/pdf')})
    assert response.status_code == 200, response.text
    run = response.json()
    assert run['pages'] == ['pages/page-2.png', 'pages/page-3.png'] and run['total'] == 2
    for _ in range(100):
        run = client.get(f"/api/runs/{run['id']}").json()
        if run['state'] in {'done', 'failed'}:
            break
        time.sleep(.05)
    assert run['state'] == 'done', run
    assert seen['submitted']['files'][0] == {'name': '月考.pdf', 'is_ocr': True, 'page_ranges': '2-3'}
    assert seen['submitted']['enable_table'] is False
    assert run['summary'] | {'images_dir': None} == {'markdown': 'result/full.md', 'images_dir': None, 'characters': len(MARKDOWN),
                                                     'display_formulas': 1, 'inline_formulas': 2, 'images': 1, 'tables': 1}
    assert client.get(f"/runs/{run['id']}/result/full.md").text == MARKDOWN
    assert client.get(f"/runs/{run['id']}/pages/page-2.png").status_code == 200
    assert client.get(f"/runs/{run['id']}/job.json").status_code == 404
    assert 'good-key' not in (tmp_path/run['id']/'job.json').read_text(encoding='utf-8')
    assert [r['id'] for r in client.get('/api/runs').json()] == [run['id']]
