"""导出前检查图片（MathBank 3.0 export_preflight）: papers and handouts refuse to export with a missing image."""
from pathlib import Path
import io
import json
import zipfile

from PIL import Image

from test_handouts import handouts, picture, sample_document, snapshots  # noqa: F401
from test_platform import new_space, question, system  # noqa: F401
from platform_app import store


def uploads(space):
    return store.DATA / 'banks' / str(space) / 'static' / 'uploads'


def make_question(client, space, name):
    path = picture(space, name)
    created = question(client, space, content=f'如图，求阴影面积。\n\n![]({path})\n',
                       image_paths=json.dumps([path])).json()['question']
    return created


def test_papers_check_every_image_before_export(system):  # noqa: F811
    space = new_space(system, '导出检查')['id']
    first = make_question(system, space, 'pf-one.png')
    second = make_question(system, space, 'pf-two.png')
    paper = {'title': '检查卷', 'questions': [{'id': first['id'], 'score': 5}, {'id': second['id'], 'score': 5}]}

    passed = system.post(f'/bank/{space}/api/paper/export/tex', json=paper)
    assert passed.status_code == 200, passed.text[:300]
    with zipfile.ZipFile(io.BytesIO(passed.content)) as archive:
        assert {'pf-one.png', 'pf-two.png'} <= {Path(name).name for name in archive.namelist()}
    report_id = passed.headers['x-export-preflight-id']
    record = system.get(f'/bank/{space}/api/paper/export/preflight/{report_id}').json()['preflight']
    assert record['status'] == 'passed' and record['counts']['errors'] == 0
    assert {r['reference'].rsplit('/', 1)[-1] for r in record['resources']} >= {'pf-one.png', 'pf-two.png'}
    assert '如图' not in json.dumps(record, ensure_ascii=False)  # paths and hashes only, no question text

    (uploads(space) / 'pf-two.png').unlink()
    for kind in ('tex', 'pdf', 'bundle', 'word'):
        blocked = system.post(f'/bank/{space}/api/paper/export/{kind}', json=paper)
        assert blocked.status_code == 400, (kind, blocked.status_code, blocked.text[:200])
        body = blocked.json()
        assert '导出预检未通过' in body['message'] and 'pf-two.png' in body['message'], body['message']
        assert f"题 #{second['id']}" in body['message']
        assert [e['reference'].rsplit('/', 1)[-1] for e in body['preflight']['errors']] == ['pf-two.png']
    assert system.get(f'/bank/{space}/api/paper/export/preflight/not-a-report').status_code == 404


def test_handouts_check_their_images_before_export(system):  # noqa: F811
    space = new_space(system, '讲义导出检查')['id']
    example = question(system, space, content='计算 $1+1$。').json()['question']
    taken = snapshots(system, space, [example['id']])
    document = sample_document(taken[0], [])
    figure = picture(space, 'hd-check.png')
    document['sections'][0]['blocks'].append({'kind': 'figure', 'image': figure, 'caption': '示意图', 'size': 'small'})
    hid = system.post(handouts(space), json={'title': '缺图讲义', 'document': document}).json()['id']
    assert system.get(f'{handouts(space)}/{hid}/export', params={'format': 'docx'}).status_code == 200
    (uploads(space) / 'hd-check.png').unlink()
    for fmt in ('docx', 'pdf'):
        response = system.get(f'{handouts(space)}/{hid}/export', params={'format': fmt})
        assert response.status_code == 422, (fmt, response.status_code)
        assert response.json()['detail'].startswith('讲义里有 1 张图片找不到：hd-check.png'), response.json()
    Image.new('RGB', (10, 10), 'white').save(uploads(space) / 'hd-check.png')
    assert system.get(f'{handouts(space)}/{hid}/export', params={'format': 'docx'}).status_code == 200
