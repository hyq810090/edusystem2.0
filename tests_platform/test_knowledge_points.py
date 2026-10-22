"""考查知识点 (platform_app/knowledge_points.py): catalogue per edition, tagging, list filters,
cleanup with the question, and AI suggestions checked against the question text."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading

import pytest

os.environ['NO_PROXY'] = os.environ['no_proxy'] = '127.0.0.1,localhost'
from test_platform import add_book, new_space, system  # noqa: E402,F401
from platform_app import knowledge_points  # noqa: E402

CODES = {p['name']: p['code'] for p in knowledge_points.catalogue()['points']}
SET, ELEMENT = CODES['集合的含义'], CODES['元素与集合的关系']


def books(client):
    return {(b['stage'], b['subject'], b['name']): b['id'] for b in client.get('/platform/api/textbooks').json()}


def add_question(client, space, text):
    tree = client.get(f'/bank/{space}/api/categories').json()
    book = next(iter(tree))
    chapter = next(iter(tree[book]))
    created = client.post(f'/bank/{space}/api/questions', data={
        'content': text, 'question_type': 'detailed_answer', 'difficulty': '2',
        'category_compulsory': book, 'category_chapter': chapter, 'category_knowledge': tree[book][chapter][0]})
    assert created.status_code == 200, created.text
    return created.json()['question']['id']


def listed(client, space, **params):
    response = client.get(f'/bank/{space}/api/questions', params={'page': 1, 'page_size': 50, **params})
    assert response.status_code == 200, response.text
    return sorted(item['id'] for item in response.json()['items'])


def test_each_edition_places_points_in_its_own_sections(system):  # noqa: F811
    available = books(system)
    for edition in ('人教A版', '苏教版', '北师大版'):
        space = new_space(system, '目录' + edition, book=available[('高中', '数学', edition)])['id']
        data = system.get(f'/bank/{space}/api/platform/knowledge-points').json()
        assert data['enabled'] and len(data['points']) == 773 and '智学网' in data['source']
        tree = system.get(f'/bank/{space}/api/categories').json()
        placed = [path for point in data['points'] for path in point['paths']]
        assert len(placed) > 1000
        assert all(chapter in tree[book] and (not section or section in tree[book][chapter])
                   for book, chapter, section in placed)
        concept = next(p for p in data['points'] if p['code'] == SET)
        assert any(section for _, _, section in concept['paths'])
    physics = add_book(system, '物理', '高中', '知识点测试物理')
    space = new_space(system, '物理空间', book=physics)['id']
    assert system.get(f'/bank/{space}/api/platform/knowledge-points').json() == {'enabled': False}
    assert system.get(f'/bank/{space}/api/questions', params={'knowledge_point_codes': SET}).status_code == 400


def test_tagging_filters_and_cleanup(system):  # noqa: F811
    space = new_space(system, '知识点标注')['id']
    first, second, third = (add_question(system, space, f'知识点测试题 {n}：判断 $1\\in A$。') for n in (1, 2, 3))
    put = lambda q, points: system.put(f'/bank/{space}/api/platform/knowledge-points/questions/{q}', json={'points': points})
    saved = put(first, [{'code': SET, 'role': 'main'}, {'code': ELEMENT, 'role': 'auxiliary'}])
    assert saved.status_code == 200 and [p['name'] for p in saved.json()['points']] == ['集合的含义', '元素与集合的关系']
    assert put(second, [{'code': SET, 'role': 'auxiliary'}, {'code': ELEMENT, 'role': 'main'}]).status_code == 200

    shown = system.get(f'/bank/{space}/api/platform/knowledge-points/questions', params={'ids': f'{first},{third}'}).json()
    assert [(p['code'], p['role'], p['origin']) for p in shown['questions'][str(first)]] == [
        (SET, 'main', 'manual'), (ELEMENT, 'auxiliary', 'manual')]
    assert shown['questions'][str(third)] == []

    assert listed(system, space, knowledge_point_codes=SET) == [first]
    assert listed(system, space, knowledge_point_codes=SET, knowledge_point_role='all') == [first, second]
    both = {'knowledge_point_codes': [SET, ELEMENT], 'knowledge_point_role': 'all'}
    assert listed(system, space, **both, knowledge_point_mode='all') == [first, second]
    assert listed(system, space, knowledge_point_codes=[SET, ELEMENT], knowledge_point_mode='all') == []
    assert listed(system, space, knowledge_point_unlabeled='true') == [third]

    for bad in ([{'code': 'no-such-code'}], [{'code': SET}, {'code': SET}], [{'code': SET, 'role': 'other'}],
                [{'code': SET, 'evidence_source': 'title'}], 'x'):
        assert put(first, bad).status_code == 400
    assert put(999999, [{'code': SET}]).status_code == 404

    # Deleting a question removes its points; an id reused later starts empty.
    assert system.delete(f'/bank/{space}/api/questions/{third}').status_code == 200
    assert put(second, []).status_code == 200
    assert system.delete(f'/bank/{space}/api/questions/{first}').status_code == 200
    reused = add_question(system, space, '知识点测试题 4：新题。')
    shown = system.get(f'/bank/{space}/api/platform/knowledge-points/questions', params={'ids': f'{first},{reused}'}).json()
    assert all(points == [] for points in shown['questions'].values())


class FakeModel:
    def __init__(self, reply):
        self.reply, self.requests = reply, []
        model = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                model.requests.append(body)
                data = json.dumps({'choices': [{'message': {'content': json.dumps(model.reply, ensure_ascii=False)},
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


def test_ai_suggestions_keep_only_verbatim_catalogue_points(system):  # noqa: F811
    question = {'content': '已知集合 $A=\\{1,2\\}$，判断 $1$ 与 $A$ 的关系。', 'answer_markdown': '因为 $1\\in A$。'}
    model = FakeModel({'knowledge_points': [
        {'code': ELEMENT, 'role': 'main', 'evidence_source': 'content', 'evidence': '判断 $1$ 与 $A$ 的关系', 'reason': '元素与集合'},
        {'code': SET, 'role': 'auxiliary', 'evidence_source': 'answer_markdown', 'evidence': '集合是一组对象', 'reason': '编造的依据'},
        {'code': 'invented', 'role': 'main', 'evidence_source': 'content', 'evidence': '已知集合', 'reason': ''}]})
    try:
        saved = system.post('/platform/api/ai-settings', json={'scope': 'team', 'config': {
            'zhongzhan_gpt_key': 'local-test', 'zhongzhan_gpt_base_url': f'http://127.0.0.1:{model.server.server_port}/v1',
            'prefer_classify_model': 'ZHONGZHAN_GPT/fake-model'}})
        assert saved.status_code == 200, saved.text
        space = new_space(system, '知识点建议')['id']
        result = system.post(f'/bank/{space}/api/platform/knowledge-points/suggest', json=question)
        assert result.status_code == 200, result.text
        body = result.json()
        assert [(p['code'], p['role'], p['origin'], p['name']) for p in body['points']] == [
            (ELEMENT, 'main', 'ai', '元素与集合的关系')]
        assert len(body['warnings']) == 2
        prompt = model.requests[-1]['messages'][0]['content']
        assert SET in prompt and '集合的含义' in prompt
        assert json.loads(model.requests[-1]['messages'][1]['content'])['content'] == question['content']
        empty = system.post(f'/bank/{space}/api/platform/knowledge-points/suggest', json={'content': ' '})
        assert empty.status_code == 400
    finally:
        model.server.shutdown()
