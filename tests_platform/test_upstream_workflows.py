"""Real private workers and HTTP fakes: permissions, bounded selection and PDF retry.

These are protocol regressions, not an acceptance test of real model quality.
"""
from collections import Counter
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import hashlib
import io
import json
import threading
import time

from PIL import Image, ImageDraw
import pymupdf
import pytest

from test_platform import create_user, login_user, new_space, question, system  # noqa: F401


class Model:
    def __init__(self):
        self.requests = []
        self.images = {}
        self.counts = Counter()
        self.image_lock = threading.Lock()
        self.recover = False
        self.selected = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append(payload)
                content = payload['messages'][-1]['content']
                images = [part for part in content if part.get('type') == 'image_url'] if isinstance(content, list) else []
                status = 200
                if images:
                    digest = hashlib.sha256(images[0]['image_url']['url'].encode()).hexdigest()
                    with owner.image_lock:
                        index = owner.images.setdefault(digest, len(owner.images) + 1)
                        owner.counts[index] += 1
                    if index == 2 and not owner.recover:
                        status = 503
                        answer = 'temporary page failure'
                    else:
                        begin = 1 if index == 1 else 4
                        answer = '\n\n'.join(f'{i}. 求第{i}个自然数的相邻整数。' for i in range(begin, begin + 3))
                elif payload['model'] == 'test-selection':
                    answer = json.dumps({'selected_ids': owner.selected, 'ai_analysis': '测试协议返回的建议。'})
                else:
                    owner.counts['split'] += 1
                    answer = json.dumps({'questions': [
                        {'content': f'求第{i}个自然数的相邻整数。', 'question_type': 'detailed_answer',
                         'difficulty': '3', 'answer_markdown': '', 'category_compulsory': '',
                         'category_chapter': '', 'category_knowledge': ''} for i in range(1, 7)]})
                body = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': answer}}],
                                   'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def configure(self, client, model):
        result = client.post('/platform/api/ai-settings', json={'scope': 'team', 'config': {
            'zhongzhan_gpt_key': 'local-protocol-test',
            'zhongzhan_gpt_base_url': f'http://127.0.0.1:{self.server.server_port}/v1',
            'zhongzhan_gpt_ocr_model': 'test-ocr', 'prefer_engine': 'zhongzhan_gpt',
            'prefer_solve_model': f'ZHONGZHAN_GPT/{model}', 'prefer_parse_model': 'ZHONGZHAN_GPT/test-split'}})
        assert result.is_success, result.text


def wait(client, base, task):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        response = client.get(f'{base}/tasks/{task}/status')
        assert response.is_success, response.text
        state = response.json()
        if state['status'] in {'completed', 'error', 'cancelled'}:
            return state
        time.sleep(.1)
    raise AssertionError('Temporary PDF task did not finish')


def scanned_pdf():
    with pymupdf.open() as document:
        for number in (1, 2):
            image = Image.new('RGB', (500, 600), 'white')
            ImageDraw.Draw(image).text((50, 50), f'Scanned protocol test page {number}', fill='black')
            data = io.BytesIO(); image.save(data, format='PNG')
            page = document.new_page(width=500, height=600)
            page.insert_image(page.rect, stream=data.getvalue())
        return document.tobytes()


def test_agent_reads_stay_in_space_and_do_not_add_persistent_tables(system):  # noqa: F811
    first = new_space(system, 'Agent 来源')['id']
    other = new_space(system, 'Agent 隔离')['id']
    q = question(system, first, content='Agent 一次性读取 $x=2$。', difficulty='3').json()['question']
    point = '1015'
    saved = system.put(f'/bank/{first}/api/platform/knowledge-points/questions/{q["id"]}',
                       json={'points': [{'code': point, 'role': 'main'}]})
    assert saved.is_success, saved.text
    api = f'/bank/{first}/api/agent'
    capability = system.get(api + '/capabilities')
    assert capability.is_success, capability.text
    assert capability.json()['read_only'] and capability.json()['knowledge_points_available']
    assert {x['value'] for x in capability.json()['difficulties']} == {'1', '2', '3', '4', '5'}
    result = system.get(api + '/questions', params=[('knowledge_point_codes', point), ('include_answers', 'true')])
    assert result.is_success, result.text
    assert [x['id'] for x in result.json()['items']] == [q['id']]
    assert result.json()['items'][0]['knowledge_points'][0]['code'] == point
    catalogue = system.get(api + '/knowledge-points', params={'q': '#1015'})
    assert catalogue.is_success, catalogue.text
    assert catalogue.json()['items'][0]['name'] == '集合的含义'
    by_name = system.get(api + '/questions', params={'knowledge_query': '集合的含义'})
    assert by_name.is_success and by_name.json()['total'] == 1, by_name.text
    multiple = system.get(api + '/questions', params=[('knowledge_point_codes', point),
        ('knowledge_point_codes', 'no-such-code'), ('knowledge_point_mode', 'all')])
    assert multiple.is_success and multiple.json()['total'] == 0, multiple.text
    assert system.get(f'/bank/{other}/api/agent/questions/{q["id"]}').status_code == 404
    assert system.get(api + '/questions', params={'page_size': 101}).status_code == 422
    assert system.get(api + '/questions', params={'version_code': 'B'}).status_code == 400
    assert system.post(api + '/questions', json={}).status_code == 405
    create_user(system, 'agent-viewer')
    viewer = login_user('agent-viewer', system)
    assert viewer.get(api + '/capabilities').status_code in {403, 404}
    # SQLite inspection is only of this disposable local test dataset.
    from platform_app import store
    import sqlite3
    with closing(sqlite3.connect(store.DATA / 'banks' / str(first) / 'math_question_bank.db')) as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not {'knowledge_points', 'question_knowledge_points', 'knowledge_point_curriculums'} & names
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 11


def test_selection_uses_quotas_exclusions_and_complete_long_question_review(system):  # noqa: F811
    fake = Model()
    fake.configure(system, 'test-selection')
    try:
        sid = new_space(system, '选题增强')['id']
        api = f'/bank/{sid}/api'
        created = []
        for kind, count in [('single_choice', 4), ('fill_in_blank', 3)]:
            for index in range(count):
                response = question(system, sid, content=f'求{kind}第{index}项的值。', question_type=kind, difficulty='3')
                assert response.is_success, response.text
                created.append(response.json()['question'])
        result = system.post(api + '/paper/ai-select', json={'prompt': '', 'limit': 30,
            'type_counts': {'single_choice': 3, 'fill_in_blank': 2}, 'exclude_ids': [created[0]['id']],
            'reference_mode': 'none'})
        assert result.is_success, result.text
        body = result.json()
        assert body['count'] == 5 and body['fallback']
        assert created[0]['id'] not in {q['id'] for q in body['data']}
        assert body['selection_summary']['type_counts'] == {'single_choice': 3, 'fill_in_blank': 2}
        assert system.post(api + '/paper/ai-select', json={'prompt': '抽取31题'}).status_code == 400
        text = ('这是需要完整阅读的条件。' * 400) + '\n\n（1）求值。\n\n（2）说明理由。'
        long_q = question(system, sid, content=text, question_type='detailed_answer', difficulty='3').json()['question']
        fake.selected = [long_q['id'], 999999, True]
        selected = system.post(api + '/paper/ai-select', json={'prompt': '抽取1道解答题', 'limit': 1, 'reference_mode': 'none'})
        assert selected.is_success, selected.text
        body = selected.json()
        assert [q['id'] for q in body['data']] == [long_q['id']]
        assert body['selection_reading']['model_calls'] == 2, body
        assert body['selection_reading']['selected'][0]['basis'] == 'ai_full_review', body
        assert len(fake.requests) == 2
    finally:
        fake.server.shutdown()


def test_pdf_resume_reuses_successful_pages_and_keeps_task_ownership(system):  # noqa: F811
    fake = Model()
    fake.configure(system, 'test-selection')
    try:
        sid = new_space(system, 'PDF 恢复')['id']
        base = f'/bank/{sid}/api'
        uploaded = system.post(base + '/upload/pdf-task', files={'file': ('resume.pdf', scanned_pdf(), 'application/pdf')},
                               data={'pdf_strategy': 'force_ocr'})
        assert uploaded.is_success, uploaded.text
        task = uploaded.json()['task_id']
        failed = wait(system, base, task)
        assert failed['status'] == 'error' and failed.get('pdf_resume_available'), failed
        assert len(failed['data']) >= 1, failed
        assert all(q['source_review']['required'] and q['difficulty'] == 'unrated' for q in failed['data'])
        first_cards = [q['pdf_source_card_id'] for q in failed['data']]
        fake.recover = True
        create_user(system, 'resume-other')
        other = login_user('resume-other', system)
        assert other.post(base + f'/tasks/{task}/resume').status_code in {403, 404}
        recovered = system.post(base + f'/tasks/{task}/resume')
        assert recovered.is_success, recovered.text
        new_task = recovered.json()['task_id']
        assert new_task != task
        assert other.get(base + f'/tasks/{new_task}/status').status_code in {403, 404}
        finished = wait(system, base, new_task)
        assert finished['status'] == 'completed', finished
        assert finished['pdf_recovery']['preserved_cards'] == len(first_cards)
        assert [q['pdf_source_card_id'] for q in finished['data'][:len(first_cards)]] == first_cards
        reuse = system.get(base + f'/tasks/{new_task}/ocr-reuse').json()
        assert reuse['reused'], reuse
        assert fake.counts[1] == 1 and fake.counts[2] == 3, fake.counts
        assert fake.counts['split'] == 1
        assert system.post(base + f'/tasks/{task}/resume').status_code == 409
        assert system.get(base + '/questions?page=1').json()['total'] == 0
    finally:
        fake.server.shutdown()
