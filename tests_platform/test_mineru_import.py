"""MinerU import: Markdown rebuilding from MinerU blocks and the import job API."""
import io
import json
import time
import zipfile

import httpx
from PIL import Image

from test_platform import create_user, login_user, new_space, system  # noqa: F401
from platform_app import imports, store
from platform_app.mineru_client import MinerUClient
from platform_app.mineru_markdown import convert, picture_options


def text(value, box, page=0, level=None):
    block = {'type': 'text', 'text': value, 'bbox': box, 'page_idx': page}
    if level:
        block['text_level'] = level
    return block


def figure(name, box, page=0, caption=()):
    return {'type': 'image', 'img_path': f'images/{name}.jpg', 'bbox': box, 'page_idx': page,
            'image_caption': list(caption), 'image_footnote': []}


def test_question_numbers_are_normalised_and_headings_lose_numbers():
    markdown, _, report = convert([
        text('1 如图，求面积。', [90, 30, 800, 45]),
        text('2. 准确完成', [100, 50, 300, 70], level=2),
        text('2 一个几何体的全面积为____。', [90, 150, 800, 170]),
        text('例 1. 一物体做匀加速运动。', [90, 200, 800, 220]),
        text('10 m/s 的速度继续运动', [90, 230, 800, 250]),
        text('1 2 3 k k+1', [90, 260, 300, 280]),
        {'type': 'page_number', 'text': '第1页', 'bbox': [450, 960, 550, 980], 'page_idx': 0},
    ])
    assert '### 准确完成' in markdown and '2. 准确完成' not in markdown
    # Numbering continues across a section heading.
    assert '1．如图，求面积。' in markdown and '2．一个几何体' in markdown and '1．一物体' in markdown
    # "10 m/s" does not continue the numbering; digit runs are labels, not questions.
    assert '10 m/s 的速度继续运动' in markdown and '1 2 3 k k+1' in markdown and '第1页' not in markdown
    assert report['questions'] == 3 and report['renumbered'] == 3


def test_labels_inside_or_under_figures_are_dropped():
    markdown, figures, report = convert([
        text('1．如图，杆下落通过窗口。', [90, 100, 700, 120]),
        figure('rod', [700, 130, 880, 300], caption=['窗台']),
        text('窗台', [760, 305, 820, 320]),
        text('O', [710, 200, 720, 210]),
        text('求杆下端到窗台的高度差。', [90, 330, 700, 350]),
    ])
    assert markdown.count('窗台') == 1 and '\n\nO\n' not in markdown
    assert '求杆下端到窗台的高度差。' in markdown and figures == ['images/rod.jpg']
    assert report['figure_labels_removed'] == 2


def test_picture_options_in_a_row_keep_the_odd_figure_with_the_stem():
    row = [figure(f'g{i}', [120 + 150 * i, 500, 250 + 150 * i, 566]) for i in range(4)]
    wedge = figure('wedge', [716, 498, 886, 567])
    markdown, _, report = convert([
        text('2．如图，可能正确描述其速度的图像是（ ）', [90, 400, 900, 480]),
        *row, wedge,
        text('A', [180, 570, 190, 585]), text('B', [330, 570, 340, 585]),
    ])
    stem, options = markdown.split('A．', 1)
    assert '![](images/wedge.jpg)' in stem
    assert [line.split('(')[1][:-1] for line in ('A．' + options).strip().split('\n\n')] == [
        'images/g0.jpg', 'images/g1.jpg', 'images/g2.jpg', 'images/g3.jpg']
    assert report['picture_option_questions'] == 1 and report['stem_figures'] == 1


def test_picture_options_in_a_grid_beside_the_setup_figure():
    # Two rows of option graphs; the setup diagram sits at the end of the first row.
    a, b = figure('a', [128, 551, 301, 655]), figure('b', [413, 554, 581, 660])
    diagram = figure('diagram', [723, 553, 900, 667], caption=['A'])
    c, d = figure('c', [128, 684, 306, 791], caption=['B', 'C']), figure('d', [423, 685, 593, 791], caption=['D'])
    options, stems = picture_options([a, b, diagram, c, d])
    assert [f['img_path'] for f in options] == ['images/a.jpg', 'images/b.jpg', 'images/c.jpg', 'images/d.jpg']
    assert stems == [diagram]


def test_figures_stay_in_place_without_a_choice_blank_or_with_text_options():
    figures = [figure(f'f{i}', [100 + 200 * i, 300, 250 + 200 * i, 400]) for i in range(3)]
    worked, _, _ = convert([text('3．如图，分别求三个图中的面积。', [90, 100, 900, 120]), *figures])
    choice, _, report = convert([text('4．下列说法正确的是（ ） A. 甲 B. 乙 C. 丙 D. 丁', [90, 100, 900, 120]), *figures])
    for markdown in (worked, choice):
        assert 'A．![]' not in markdown and markdown.count('![](') == 3
    assert report['picture_option_questions'] == 0


def result_zip(blocks, images):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('demo_content_list.json', json.dumps(blocks, ensure_ascii=False))
        archive.writestr('demo_content_list_v2.json', '[]')
        archive.writestr('demo_origin.pdf', b'%PDF-1.4 original paper')
        archive.writestr('full.md', '# MinerU 原始输出')
        for name in images:
            png = io.BytesIO()
            Image.new('RGB', (60, 40), 'white').save(png, format='PNG')
            archive.writestr(f'images/{name}.jpg', png.getvalue())
    return buffer.getvalue()


def wait(client, url):
    for _ in range(200):
        job = client.get(url).json()
        if job['state'] in {'done', 'failed'}:
            return job
        time.sleep(.05)
    raise AssertionError(job)


def test_result_zip_import_uploads_all_figures_in_batches(system):  # noqa: F811
    space = new_space(system, 'MinerU 导入')['id']
    names = [f'fig{i}' for i in range(23)]
    blocks = [text('1．如图，求面积。', [90, 50, 900, 70])]
    blocks += [figure(name, [100, 100 + 30 * i, 200, 120 + 30 * i]) for i, name in enumerate(names)]
    response = system.post(f'/platform/api/spaces/{space}/mineru',
                           files={'file': ('第一次月考.zip', result_zip(blocks, names), 'application/zip')})
    assert response.status_code == 200, response.text
    job = wait(system, f"/platform/api/spaces/{space}/mineru/{response.json()['id']}")
    assert job['state'] == 'done', job
    result = job['result']
    assert job['title'] == '第一次月考' and result['report']['figures'] == 23
    assert set(result['mapping']) == {f'{name}.jpg' for name in names}
    path = next(iter(result['mapping'].values()))
    assert path.startswith(f'/static/uploads/{space}/') and system.get(path).status_code == 200
    assert result['markdown'].startswith('1．如图') and result['markdown'].count('![](images/') == 23
    other = system.get(f"/platform/api/spaces/1/mineru/{job['id']}")
    assert other.status_code == 404
    # The original paper inside the result zip is shown beside the questions.
    origin = system.get(f"/platform/api/spaces/{space}/mineru/{job['id']}/origin")
    assert result['origin'] and origin.content == b'%PDF-1.4 original paper' and origin.headers['content-type'] == 'application/pdf'
    assert system.get(f"/platform/api/spaces/1/mineru/{job['id']}/origin").status_code == 404


def test_pdf_needs_a_key_and_viewers_cannot_import(system):  # noqa: F811
    space = new_space(system, 'MinerU 权限', 'team')['id']
    pdf = ('试卷.pdf', b'%PDF-1.4', 'application/pdf')
    store_config = store.config('team:1')
    assert system.get(f'/platform/api/spaces/{space}/mineru/status').json() == {'configured': False}
    assert system.post(f'/platform/api/spaces/{space}/mineru', files={'file': pdf}).status_code == 400
    assert system.post(f'/platform/api/spaces/{space}/mineru', files={'file': ('a.txt', b'x')}).status_code == 400
    create_user(system, 'mineru_viewer')
    viewer = login_user('mineru_viewer', system)
    assert viewer.post(f'/platform/api/spaces/{space}/mineru', files={'file': pdf}).status_code == 403
    assert system.post('/platform/api/ai-settings', json={'scope': 'team', 'config': {'mineru_key': 'team-mineru'}}).status_code == 200
    assert system.get('/platform/api/ai-settings?scope=team').json()['mineru_key'] == '••••••••'
    assert system.get(f'/platform/api/spaces/{space}/mineru/status').json() == {'configured': True}
    # The MinerU key stays with the platform; workers only receive model settings.
    system.get(f'/bank/{space}/api/questions?page=1')
    config = json.loads((store.DATA / 'banks' / str(space) / 'worker-config.json').read_text(encoding='utf-8'))
    assert 'mineru_key' not in config['ai']
    assert store.config('team:1').get('mineru_key') == 'team-mineru' and 'mineru_key' not in store_config


def test_pdf_import_runs_mineru_with_forced_ocr(system, monkeypatch):  # noqa: F811
    space = new_space(system, 'MinerU PDF')['id']
    blocks = [text('例 1. 如图，下列图像可能正确的是（ ）', [90, 50, 900, 70])]
    blocks += [figure(f'g{i}', [100 + 200 * i, 100, 220 + 200 * i, 180]) for i in range(4)]
    seen = {}

    def handler(request):
        if request.url.path.endswith('/file-urls/batch'):
            seen['submitted'] = json.loads(request.content)
            return httpx.Response(200, json={'code': 0, 'data': {'batch_id': 'b', 'file_urls': ['https://oss.example/u']}})
        if request.url.host == 'oss.example':
            return httpx.Response(200)
        if request.url.path.endswith('/extract-results/batch/b'):
            return httpx.Response(200, json={'code': 0, 'data': {'extract_result': [
                {'state': 'done', 'full_zip_url': 'https://cdn.example/r.zip'}]}})
        return httpx.Response(200, content=result_zip(blocks, ['g0', 'g1', 'g2', 'g3']))
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(imports, 'MinerUClient', lambda key: MinerUClient(key, transport=transport))
    system.post('/platform/api/ai-settings', json={'scope': 'team', 'config': {'mineru_key': 'team-mineru'}})
    response = system.post(f'/platform/api/spaces/{space}/mineru', data={'page_ranges': '1-2'},
                           files={'file': ('图像题.pdf', b'%PDF-1.4', 'application/pdf')})
    job = wait(system, f"/platform/api/spaces/{space}/mineru/{response.json()['id']}")
    assert job['state'] == 'done', job
    assert seen['submitted']['files'][0] == {'name': 'source.pdf', 'is_ocr': True, 'page_ranges': '1-2'}
    assert '1．如图' in job['result']['markdown'] and 'D．![](images/g3.jpg)' in job['result']['markdown']
