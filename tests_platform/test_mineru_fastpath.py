"""MinerU fast parse: local split rules, the classification contract, and the whole path
through the platform and a real worker against a local fake model server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import sys
import threading

import pytest

os.environ['NO_PROXY'] = os.environ['no_proxy'] = '127.0.0.1,localhost'
from test_platform import new_space, system  # noqa: E402,F401

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine'))
from platform_app import mineru_fastpath as fast  # noqa: E402

TIPS = '### 二、知识提要\n\n1．计算面积时常连接半径。\n\n2．不规则图形要分割或补全。\n\n'
PRACTICE = ('### 三、练习\n\n1．已知 $x+1=3$，则 $x$ 等于多少？\n\n'
            '2．下列各数中最大的是（）.\n\nA. $1$ B. $2$ C. $3$ D. $4$\n\n'
            '3．求 $\\sqrt{4}$ 的值。\n')
PAPER = '## 第1讲 测试讲义\n\n### 一、教学目标\n\n复习基本运算。\n\n' + TIPS + PRACTICE


def codes(source):
    try:
        items = fast.split_items(source)
    except fast.Fallback as reason:
        return reason.code
    return [(item['section'], item['number']) for item in items]


def test_sections_may_restart_or_continue_their_numbering():
    assert codes(PAPER) == [('二、知识提要', 1), ('二、知识提要', 2), ('三、练习', 1), ('三、练习', 2), ('三、练习', 3)]
    continued = '### 一、选择题\n\n1．甲\n\n2．乙\n\n### 二、填空题\n\n3．丙\n'
    assert codes(continued) == [('一、选择题', 1), ('一、选择题', 2), ('二、填空题', 3)]
    # A line starting with a number inside display maths is not a question.
    assert codes('### 练习\n\n1．计算\n$$\n2. x\n$$\n2．再算\n') == [('练习', 1), ('练习', 2)]


@pytest.mark.parametrize('source, code', [
    ('### 练习\n\n1．甲\n\n3．乙\n', 'numbering_irregular'),
    ('### 练习\n\n1．甲\n\n1．乙\n', 'numbering_irregular'),
    ('### 一\n\n1．甲\n\n### 二\n\n5．乙\n', 'numbering_irregular'),
    ('### 练习\n\n阅读下列材料，回答问题。\n\n1．甲\n', 'shared_material'),
    ('### 练习\n\n![](images/a.jpg)\n\n1．甲\n', 'shared_material'),
    ('### 练习\n\n1．甲\n\n### 参考答案\n\n1．A\n', 'separate_answers'),
    ('### 练习\n\n1．甲\n\n2．同第1题的条件，求乙。\n', 'cross_question_reference'),
    ('只有正文，没有题号。\n', 'no_numbered_items'),
])
def test_uncertain_layouts_fall_back_to_the_whole_paper_parse(source, code):
    assert codes(source) == code


def test_figure_captions_and_worked_example_sections_are_not_risks():
    assert codes('### 一、典例解析\n\n1．如图（第1题图），求面积。\n') == [('一、典例解析', 1)]


def test_each_segment_is_judged_and_too_many_uncertain_ones_fall_back():
    items = fast.split_items(PRACTICE + '4．下列正确的是（）. A. 1 B. 2 A. 3 D. 4\n')
    fast.judge_items(items)
    assert [item['source'] is None for item in items] == [False, False, False, True]
    assert items[1]['source']['has_choices'] and '\\begin{choices}' in items[1]['source']['content']
    assert not items[0]['source']['content'].startswith('1')
    bad = fast.split_items(''.join(f'{n}．选（）. A. 1 A. 2 C. 3 D. 4\n\n' for n in range(1, 5)))
    with pytest.raises(fast.Fallback, match='too_many_uncertain_items'):
        fast.judge_items(bad)


def classification(items, **changes):
    rows = [{'id': item['id'], 'is_question': True, 'question_type': 'single_choice' if item['source']['has_choices'] else 'detailed_answer',
             'category_compulsory': '', 'category_chapter': '', 'category_knowledge': '', 'difficulty': 'normal'}
            for item in items]
    rows[0].update(changes)
    return {'items': rows}


def test_classification_must_cover_every_segment_with_known_values():
    items = fast.split_items(PRACTICE)
    fast.judge_items(items)
    for index, item in enumerate(items):
        item['id'] = f'Q{index + 1}'
    assert set(fast.apply_classification(classification(items), items)) == {'Q1', 'Q2', 'Q3'}
    # Textbook labels are not judged here: the engine's import check aligns or flags them per question.
    for labels in (classification(items, category_compulsory='必修一', category_chapter='第九章'),
                   classification(items, category_chapter='第一章', category_knowledge='第九节'),
                   {'items': [{key: value for key, value in row.items() if not key.startswith('category_')}
                              for row in classification(items)['items']]}):
        assert set(fast.apply_classification(labels, items)) == {'Q1', 'Q2', 'Q3'}
    for bad in (classification(items, id='Q9'), classification(items, question_type='essay'),
                classification(items, difficulty=['3']), classification(items, extra='x'),
                {'items': classification(items)['items'][:2]}, {'items': [], 'extra': 1}):
        with pytest.raises(fast.Fallback):
            fast.apply_classification(bad, items)
    with pytest.raises(fast.Fallback):  # a question with choices cannot be classified as a solution question
        rows = classification(items)
        rows['items'][1]['question_type'] = 'detailed_answer'
        fast.apply_classification(rows, items)


# ---------- Through the platform and a real worker ----------
class FakeModel:
    """An OpenAI-compatible server answering the classification or a parse request."""

    def __init__(self):
        self.requests, self.classify = [], self.answer_classification
        model = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                model.requests.append(body)
                system_prompt = body['messages'][0]['content']
                reply = model.classify(body) if '"is_question"' in system_prompt else model.answer_parse(body)
                data = json.dumps({'choices': [{'message': {'content': json.dumps(reply, ensure_ascii=False)},
                                                'finish_reason': 'stop'}],
                                   'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @staticmethod
    def answer_classification(body):
        rows = []
        for item in json.loads(body['messages'][1]['content'])['items']:
            question = '知识提要' not in item['section']
            kind = 'single_choice' if '\\begin{choices}' in item['content'] else 'detailed_answer'
            rows.append({'id': item['id'], 'is_question': question, 'question_type': kind if question else '',
                         'category_compulsory': '', 'category_chapter': '', 'category_knowledge': '',
                         'difficulty': '3' if question else ''})
        return {'items': rows}

    @staticmethod
    def answer_parse(body):
        text = body['messages'][-1]['content']
        return {'questions': [{'content': '整段拆出：' + text.strip().splitlines()[-1][:12], 'answer_markdown': '',
                               'question_type': 'detailed_answer', 'category_compulsory': '', 'category_chapter': '',
                               'category_knowledge': '', 'difficulty': '3', 'source': '', 'referenced_images': []}]}


@pytest.fixture(scope='module')
def model(system):  # noqa: F811
    fake = FakeModel()
    saved = system.post('/platform/api/ai-settings', json={'scope': 'team', 'config': {
        'zhongzhan_gpt_key': 'local-test', 'zhongzhan_gpt_base_url': f'http://127.0.0.1:{fake.server.server_port}/v1',
        'prefer_parse_model': 'ZHONGZHAN_GPT/fake-model'}})
    assert saved.status_code == 200, saved.text
    yield fake
    fake.server.shutdown()


def parse(system, space, markdown, flag=True):  # noqa: F811
    data = {'latex_content': markdown, 'source_format': 'markdown', 'image_mapping_json': '{}', 'paper_title': '测试讲义'}
    if flag:
        data['platform_fast_path'] = 'mineru'
    response = system.post(f'/bank/{space}/api/ai/parse-paper', files={k: (None, v) for k, v in data.items()})
    assert response.status_code == 200, response.text[:500]
    return response.json()


def test_fast_parse_keeps_the_text_and_asks_only_for_classification(system, model):  # noqa: F811
    space = new_space(system, '快速拆题')['id']
    model.requests.clear()
    result = parse(system, space, PAPER)
    report = result['tex_diagnostics']['fast_path']
    assert report['status'] == 'used' and report['segments'] == 5 and report['questions'] == 3, report
    assert [item['number'] for item in report['skipped']] == [1, 2] and report['calls'] == 1
    assert len(model.requests) == 1 and '"is_question"' in model.requests[0]['messages'][0]['content']
    questions = result['questions']
    assert '$x+1=3$' in questions[0]['content'] and not questions[0]['content'].startswith('1')
    assert '\\begin{choices}' in questions[1]['content'] and questions[1]['question_type'] == 'single_choice'
    assert '\\sqrt{4}' in questions[2]['content'] and questions[2]['difficulty'] == '3'
    assert all(q['source'] == '测试讲义' for q in questions)
    assert not any(q.get('source_review', {}).get('required') for q in questions), questions


def test_uncertain_segments_are_parsed_alone_beside_the_classification(system, model):  # noqa: F811
    space = new_space(system, '快速拆题·部分')['id']
    model.requests.clear()
    result = parse(system, space, PRACTICE + '\n4．下列正确的是（）. A. 1 B. 2 A. 3 D. 4\n')
    report = result['tex_diagnostics']['fast_path']
    assert report['status'] == 'partial' and [u['number'] for u in report['uncertain']] == [4], report
    assert len(model.requests) == 2 and report['calls'] == 2
    assert [q['content'][:5] for q in result['questions']][-1] == '整段拆出：'


def test_uncertain_layouts_and_bad_classifications_use_the_whole_paper_parse(system, model):  # noqa: F811
    space = new_space(system, '快速拆题·退回')['id']
    model.requests.clear()
    result = parse(system, space, '### 练习\n\n1．甲题求值。\n\n3．乙题求值。\n')
    assert result['tex_diagnostics']['fast_path']['reason'] == 'numbering_irregular'
    assert len(model.requests) == 1 and '"is_question"' not in model.requests[0]['messages'][0]['content']
    model.requests.clear()
    model.classify = lambda body: {'items': []}
    try:
        result = parse(system, space, PRACTICE)
    finally:
        model.classify = model.answer_classification
    assert result['tex_diagnostics']['fast_path']['reason'] == 'classification_failed'
    assert len(model.requests) == 2 and result['questions'][0]['content'].startswith('整段拆出')
    model.requests.clear()
    result = parse(system, space, PRACTICE, flag=False)
    assert 'fast_path' not in result['tex_diagnostics'] and len(model.requests) == 1


def catalog_names(system, space):  # noqa: F811
    """One textbook section, a chapter in it and one of its subsections, from the space's own catalog."""
    tree = system.get(f'/bank/{space}/api/categories').json()
    book, chapters = next((book, chapters) for book, chapters in tree.items()
                          if any(sections for sections in chapters.values()))
    chapter = next(chapter for chapter, sections in chapters.items() if sections)
    return book, chapter, chapters[chapter][0]


def test_unmatched_textbook_labels_are_flagged_per_question_not_sent_back_to_the_whole_paper_parse(system, model):  # noqa: F811
    space = new_space(system, '快速拆题·分类核对')['id']
    book, chapter, section = catalog_names(system, space)
    spaced = chapter.replace(' ', '') if ' ' in chapter else chapter[:1] + ' ' + chapter[1:]
    labels = [(book, spaced, ''), (book, '第九十九章 不存在的章', ''), (book, chapter, section)]

    def classify(body):
        reply = FakeModel.answer_classification(body)
        for row, (compulsory, name, knowledge) in zip(reply['items'], labels):
            row.update(category_compulsory=compulsory, category_chapter=name, category_knowledge=knowledge)
        return reply

    model.requests.clear()
    model.classify = classify
    try:
        result = parse(system, space, PRACTICE)
    finally:
        model.classify = model.answer_classification
    report = result['tex_diagnostics']['fast_path']
    assert report['status'] == 'used' and report['calls'] == 1 and len(model.requests) == 1, report
    aligned, unknown, exact = result['questions']
    assert (aligned['category_compulsory'], aligned['category_chapter']) == (book, chapter)
    assert aligned['classification_review']['required'] is False
    assert (unknown['category_compulsory'], unknown['category_chapter']) == (book, '')
    assert unknown['classification_review']['required'] is True
    assert unknown['classification_review']['original']['category_chapter'] == '第九十九章 不存在的章'
    assert '$x+1=3$' in aligned['content'] and '\\sqrt{4}' in exact['content']
    assert (exact['category_chapter'], exact['category_knowledge']) == (chapter, section)
    assert 'classification_review' not in exact
    assert result['tex_diagnostics']['import_classification']['review_required'] == 1


def test_the_whole_paper_parse_keeps_every_question_when_a_label_is_unknown(system, model):  # noqa: F811
    space = new_space(system, '整卷拆题·分类核对')['id']
    book, chapter, _ = catalog_names(system, space)
    original = model.answer_parse

    def answer_parse(body):
        reply = original(body)
        reply['questions'].append({**reply['questions'][0], 'content': '另一题：求 $y$。'})
        reply['questions'][0].update(category_compulsory=book, category_chapter=chapter,
                                     category_knowledge='不存在的小节')
        return reply

    model.answer_parse = answer_parse
    try:
        result = parse(system, space, PRACTICE, flag=False)
    finally:
        model.answer_parse = original
    first, second = result['questions']
    assert (first['category_chapter'], first['category_knowledge']) == (chapter, '')
    assert first['classification_review']['required'] is True
    assert second['classification_review']['required'] is True  # no textbook section at all: the teacher picks one


def test_a_figure_after_the_options_stays_outside_them():
    body, figures = fast.trailing_figures('1．如图，求（）.\n\nA. 1 B. 2 C. 3 D. 4\n\n![](images/f.jpg)')
    assert figures == '![](images/f.jpg)' and 'images' not in body
    # Picture options keep their images; text with a figure in the middle is left alone.
    assert fast.trailing_figures('1．选图\n\nA．![](images/a.jpg)\n\nB．![](images/b.jpg)')[1] == ''
    assert fast.trailing_figures('1．求\n\n![](images/f.jpg)\n\n再求')[1] == ''


def test_source_prefixes_go_to_the_source_field():
    questions = [{'content': '（2019 新课标 3）从地面竖直向上抛出', 'source': '讲义'},
                 {'content': '（结果保留π）求面积', 'source': '讲义'}]
    assert fast.take_source_prefixes(questions) == 1
    assert questions[0] == {'content': '从地面竖直向上抛出', 'source': '2019 新课标 3'}
    assert questions[1]['source'] == '讲义'


def test_fast_parse_figures_and_sources_match_the_whole_paper_parse(system, model):  # noqa: F811
    space = new_space(system, '快速拆题·配图')['id']
    paper = PRACTICE + '\n4．（2019 新课标 3）如图，下列判断正确的是（）.\n\nA. 甲 B. 乙 C. 丙 D. 丁\n\n![](images/f.jpg)\n'
    result = parse(system, space, paper)
    question = result['questions'][3]
    assert result['tex_diagnostics']['fast_path']['sources_taken'] == 1
    assert question['source'] == '2019 新课标 3' and question['content'].startswith('如图')
    assert question['content'].index(r'\end{choices}') < question['content'].index('images/f.jpg')
    assert not any(q.get('source_review', {}).get('required') for q in result['questions'])


def test_a_fill_in_question_without_a_blank_is_pointed_out():
    questions = [{'content': '则勒洛三角形的周长为', 'question_type': 'fill_in_blank'},
                 {'content': r'则 $x=$ \fillin。', 'question_type': 'fill_in_blank'},
                 {'content': '求面积。', 'question_type': 'detailed_answer'}]
    diagnostics = {}
    assert fast.flag_missing_blanks(questions, diagnostics) == 1
    assert questions[0]['source_review']['reasons'] == [fast.MISSING_BLANK] and questions[0]['content'] == '则勒洛三角形的周长为'
    assert 'source_review' not in questions[1] and diagnostics['source_review_notice_count'] == 1
