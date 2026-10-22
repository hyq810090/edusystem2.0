"""PDF imports reuse page recognition (platform_app/pdf_ocr_cache.py): after a failed page,
starting over sends only the missing page, and importing the same scan again sends none.
Runs through the platform and a real worker against a local fake model server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import io
import json
import os
import threading
import time

import pytest
from PIL import Image, ImageDraw

os.environ['NO_PROXY'] = os.environ['no_proxy'] = '127.0.0.1,localhost'
from test_platform import create_user, login_user, new_space, system  # noqa: E402,F401

MARKERS = {1: (220, 30, 30), 2: (30, 30, 220)}


def scanned_pdf():
    """Two image-only pages; a coloured square tells the fake model which page it sees."""
    pages = []
    for number, colour in MARKERS.items():
        page = Image.new('RGB', (1240, 1754), 'white')
        draw = ImageDraw.Draw(page)
        draw.rectangle((80, 80, 380, 380), fill=colour)
        for line in range(6):
            draw.text((100, 480 + line * 60), f'{number}. Solve x + {line} = 3 for x.', fill='black')
        pages.append(page)
    buffer = io.BytesIO()
    pages[0].save(buffer, 'PDF', save_all=True, append_images=pages[1:], resolution=150)
    return buffer.getvalue()


class FakeModel:
    """Answers page OCR by the page's marker colour and the paper split with one question per page."""

    def __init__(self):
        self.ocr_pages, self.fail_pages, self.splits = [], set(), 0
        model = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                images = [part['image_url']['url'] for message in body['messages']
                          if isinstance(message['content'], list) for part in message['content']
                          if part.get('type') == 'image_url']
                if images:
                    page = model.page_of(images[0])
                    model.ocr_pages.append(page)
                    if page in model.fail_pages:
                        return self.reply(400, {'error': {'message': 'test failure'}})
                    content = f'{page}．第 {page} 页：解方程 $x+{page}=3$。'
                else:
                    model.splits += 1
                    text = body['messages'][-1]['content']
                    content = json.dumps({'questions': [
                        {'content': f'第 {page} 页：解方程 $x+{page}=3$。', 'answer_markdown': '',
                         'question_type': 'detailed_answer', 'category_compulsory': '', 'category_chapter': '',
                         'category_knowledge': '', 'difficulty': '3', 'source': '', 'referenced_images': []}
                        for page in (1, 2) if f'第 {page} 页' in text]}, ensure_ascii=False)
                self.reply(200, {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
                                 'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})

            def reply(self, status, value):
                data = json.dumps(value, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @staticmethod
    def page_of(url):
        image = Image.open(io.BytesIO(base64.b64decode(url.split(',', 1)[1]))).convert('RGB')
        red, _, blue = image.getpixel((image.width * 230 // 1240, image.height * 230 // 1754))
        return 1 if red > blue else 2


@pytest.fixture(scope='module')
def model(system):  # noqa: F811
    fake = FakeModel()
    base = f'http://127.0.0.1:{fake.server.server_port}/v1'
    saved = system.post('/platform/api/ai-settings', json={'scope': 'team', 'config': {
        'zhongzhan_gpt_key': 'local-test', 'zhongzhan_gpt_base_url': base, 'prefer_engine': 'zhongzhan_gpt',
        'zhongzhan_gpt_ocr_model': 'fake-vision', 'prefer_parse_model': 'ZHONGZHAN_GPT/fake-model'}})
    assert saved.status_code == 200, saved.text
    yield fake
    fake.server.shutdown()


def run_import(client, space, pdf):
    created = client.post(f'/bank/{space}/api/upload/pdf-task', files={'file': ('scan.pdf', pdf, 'application/pdf')})
    assert created.status_code == 200, created.text
    task = created.json()['task_id']
    deadline = time.time() + 120
    while time.time() < deadline:
        status = client.get(f'/bank/{space}/api/tasks/{task}/status').json()
        if status['status'] in {'completed', 'error', 'cancelled'}:
            break
        time.sleep(0.3)
    report = client.get(f'/bank/{space}/api/tasks/{task}/ocr-reuse')
    assert report.status_code == 200, report.text
    return task, status, report.json()


def test_pages_recognised_once_are_not_sent_again(system, model):  # noqa: F811
    space = new_space(system, 'PDF 复用')['id']
    pdf = scanned_pdf()

    model.fail_pages = {2}
    _, status, report = run_import(system, space, pdf)
    assert status['status'] == 'error', status
    assert sorted(model.ocr_pages) == [1, 2] and report == {'reused': [], 'saved': [1]}

    # Starting over: page 1 comes from the first attempt, only page 2 goes to the model.
    model.fail_pages, model.ocr_pages, model.splits = set(), [], 0
    _, status, report = run_import(system, space, pdf)
    assert status['status'] == 'completed', status.get('error') or status.get('log')
    assert model.ocr_pages == [2] and report == {'reused': [1], 'saved': [2]}
    assert model.splits == 1 and len(status['data']) == 2

    # The same scan again: no page request at all; the paper split is still asked.
    model.ocr_pages, model.splits = [], 0
    task, status, report = run_import(system, space, pdf)
    assert status['status'] == 'completed' and model.ocr_pages == [] and model.splits == 1
    assert report == {'reused': [1, 2], 'saved': []}

    # Another account cannot read someone else's task.
    create_user(system, 'ocr-reuse-other')
    other = login_user('ocr-reuse-other', system)
    assert other.get(f'/bank/{space}/api/tasks/{task}/ocr-reuse').status_code in {403, 404}


def test_a_changed_page_or_model_is_recognised_again(tmp_path):
    from platform_app import pdf_ocr_cache as cache_module

    class Provider:
        api_base, model_name, provider_code = 'http://model', 'vision-a', 'zhongzhan'

    class Response:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    calls = []

    def original(provider, payload, *, post, validate, label, stage, page_index=None, **options):
        calls.append(payload['page'])
        body = post(provider, payload).json()
        return {**validate(body), 'visual_calls': 1}

    wrapped = cache_module.cached(original, cache_module.Cache(tmp_path))
    post = lambda provider, payload: Response({'text': f"page {payload['page']}"})
    ask = lambda provider, page, stage='page_ocr': wrapped(provider, {'page': page}, post=post, label='t', stage=stage,
                                                            validate=lambda body: {'text': body['text']}, page_index=0)
    assert ask(Provider, 'a')['visual_calls'] == 1
    reused = ask(Provider, 'a')
    assert reused == {'text': 'page a', 'attempts': [], 'visual_calls': 0, 'usage': {}, 'usage_complete': True,
                      'reused': True}
    ask(Provider, 'b')
    Provider.model_name = 'vision-b'
    ask(Provider, 'a')
    ask(Provider, 'a', stage='paper_split')
    ask(Provider, 'a', stage='paper_split')
    assert calls == ['a', 'b', 'a', 'a', 'a']
