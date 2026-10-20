"""讲义: textbook-style handouts of a space, stored as one JSON document each.

A handout has a heading (lesson label, title, subtitle, objectives) and numbered
sections of blocks of fixed kinds; layout is the template's, never per block.
Question blocks keep a snapshot of the bank question, so later bank edits do
not rewrite a handout that was handed out; `/changes` reports which snapshots
differ from the bank. Exports run inside the space's worker (handout_export).
"""
import hashlib
import json
import re
import secrets
import time
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from platform_app import store
from platform_app.assets import rewrite_assets
from platform_app.auth import actor, space_access
from platform_app.spaces import folder_reference

router = APIRouter(prefix='/platform/api/spaces/{space_id}/handouts')

BOXES = {'definition', 'theorem', 'property', 'formula', 'corollary'}
NOTES = {'note', 'method', 'warning'}
TEXTS = {'text', 'proof', 'summary'} | NOTES
KINDS = BOXES | TEXTS | {'figure', 'example', 'exercises'}
FIGURE_SIZES = {'small', 'medium', 'large'}
SOLUTIONS = {'blank', 'show'}
ID = re.compile(r'[A-Za-z0-9_-]{1,40}')
LIMITS = {'title': 120, 'text': 20000, 'sections': 60, 'blocks': 300, 'questions': 80, 'objectives': 12,
          'document': 3_000_000}
# What a question snapshot keeps; the hash covers what a reader sees.
SNAPSHOT_FIELDS = ('question_type', 'content', 'answer_markdown', 'image_paths', 'image_layouts', 'figure_align',
                   'figure_size', 'content_tikz_assets', 'answer_tikz_assets', 'tikz_code', 'difficulty', 'source',
                   'category_chapter', 'category_knowledge')
HASHED_FIELDS = SNAPSHOT_FIELDS[:10]


def new_id(prefix):
    return prefix + secrets.token_hex(5)


def text(value, field, limit=LIMITS['text']):
    if value is None:
        return ''
    if not isinstance(value, str) or len(value) > limit:
        raise HTTPException(400, f'{field}应为不超过 {limit} 字的文本')
    return value


def source_hash(question):
    content = json.dumps({key: question.get(key) for key in HASHED_FIELDS}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()


def snapshot(question):
    """The stored copy of a bank question (paths in the bank's own form)."""
    if not isinstance(question, dict) or type(question.get('source_id', question.get('id'))) is not int:
        raise HTTPException(400, '题目快照无效')
    kept = {key: question.get(key) for key in SNAPSHOT_FIELDS}
    for key in ('content', 'answer_markdown', 'tikz_code', 'difficulty', 'source', 'category_chapter', 'category_knowledge'):
        kept[key] = text(kept[key], '题目内容')
    kept['question_type'] = text(kept['question_type'], '题型', 40)
    for key, kind in (('image_paths', list), ('content_tikz_assets', list), ('answer_tikz_assets', list), ('image_layouts', dict)):
        kept[key] = kept[key] if isinstance(kept[key], kind) else kind()
    kept['figure_align'] = text(kept['figure_align'], '配图位置', 20) or 'right'
    kept['figure_size'] = text(kept['figure_size'], '配图尺寸', 20) or 'auto'
    kept['source_id'] = question.get('source_id', question.get('id'))
    kept['source_hash'] = question.get('source_hash') or source_hash(kept)
    return kept


def clean_block(block):
    if not isinstance(block, dict) or block.get('kind') not in KINDS:
        raise HTTPException(400, '讲义内容块的类型无效')
    kind = block['kind']
    cleaned = {'id': block['id'] if isinstance(block.get('id'), str) and ID.fullmatch(block['id']) else new_id('b'),
               'kind': kind}
    if kind in BOXES or kind in {'example', 'exercises'}:
        cleaned['title'] = text(block.get('title'), '标题', LIMITS['title'])
    if kind in BOXES or kind in TEXTS:
        cleaned['body'] = text(block.get('body'), '正文')
    if kind == 'figure':
        cleaned['image'] = text(block.get('image'), '图片地址', 300)
        cleaned['caption'] = text(block.get('caption'), '图注', LIMITS['title'])
        cleaned['size'] = block.get('size') if block.get('size') in FIGURE_SIZES else 'medium'
    if kind == 'example':
        cleaned['question'] = snapshot(block.get('question'))
        cleaned['solution'] = text(block.get('solution'), '解答')
        space = block.get('space', 4)
        cleaned['space'] = space if type(space) in (int, float) and 0 <= space <= 15 else 4
    if kind == 'exercises':
        questions = block.get('questions') or []
        if not isinstance(questions, list) or len(questions) > LIMITS['questions']:
            raise HTTPException(400, f'一组练习最多 {LIMITS["questions"]} 题')
        cleaned['questions'] = [snapshot(question) for question in questions]
    return cleaned


def clean_document(document):
    """Validate a handout document and keep only known fields."""
    if not isinstance(document, dict):
        raise HTTPException(400, '讲义内容无效')
    if len(json.dumps(document, ensure_ascii=False)) > LIMITS['document']:
        raise HTTPException(413, '讲义内容过大')
    objectives = document.get('objectives') or []
    sections = document.get('sections') or []
    if not isinstance(objectives, list) or len(objectives) > LIMITS['objectives']:
        raise HTTPException(400, f'学习目标最多 {LIMITS["objectives"]} 条')
    if not isinstance(sections, list) or len(sections) > LIMITS['sections']:
        raise HTTPException(400, f'讲义最多 {LIMITS["sections"]} 个小节')
    settings = document.get('settings') if isinstance(document.get('settings'), dict) else {}
    cleaned_sections = []
    for section in sections:
        if not isinstance(section, dict) or not isinstance(section.get('blocks', []), list):
            raise HTTPException(400, '讲义小节无效')
        if len(section.get('blocks', [])) > LIMITS['blocks']:
            raise HTTPException(400, f'一个小节最多 {LIMITS["blocks"]} 个内容块')
        cleaned_sections.append({
            'id': section['id'] if isinstance(section.get('id'), str) and ID.fullmatch(section['id']) else new_id('s'),
            'title': text(section.get('title'), '小节标题', LIMITS['title']),
            'blocks': [clean_block(block) for block in section.get('blocks', [])],
        })
    return {
        'version': 1,
        'kicker': text(document.get('kicker'), '讲次', 40),
        'subtitle': text(document.get('subtitle'), '副标题', LIMITS['title']),
        'objectives': [text(item, '学习目标', 300) for item in objectives],
        'settings': {'student_solutions': settings.get('student_solutions') if settings.get('student_solutions') in SOLUTIONS else 'blank'},
        'sections': cleaned_sections,
    }


def empty_document():
    return clean_document({'sections': [{'title': '知识梳理', 'blocks': []}]})


def handout_title(value):
    title = text(value, '讲义标题', LIMITS['title']).strip()
    if not title:
        raise HTTPException(400, '请填写讲义标题')
    return title


def own_handout(space_id, handout_id):
    row = store.one('SELECT * FROM handouts WHERE id=? AND space_id=?', (handout_id, space_id))
    if not row:
        raise HTTPException(404, '讲义不存在')
    return row


def public(row, space_id, with_document=True):
    value = {key: row[key] for key in ('id', 'title', 'folder_id', 'revision', 'created', 'updated')}
    value['updated_by_name'] = row.get('updated_by_name')
    document = json.loads(row['document'])
    value['summary'] = {'kicker': document.get('kicker', ''), 'sections': len(document['sections']),
                        'blocks': sum(len(section['blocks']) for section in document['sections'])}
    if with_document:
        value['document'] = rewrite_assets(document, space_id)
    return value


def questions_in(document):
    for section in document['sections']:
        for block in section['blocks']:
            if block['kind'] == 'example':
                yield block['question']
            elif block['kind'] == 'exercises':
                yield from block['questions']


async def bank_questions(request, space, ids):
    """Current bank questions by id (paths in the bank's own form)."""
    workers = request.app.state.workers
    found = {}
    ids = list(dict.fromkeys(ids))
    for start in range(0, len(ids), 50):
        batch = ids[start:start + 50]
        response = await workers.call(await workers.get(space), 'GET', '/api/paper/questions',
                                      params={'ids': ','.join(map(str, batch))})
        if response.status_code != 200:
            raise HTTPException(502, '读取题库题目失败')
        found.update({question['id']: question for question in response.json().get('data', [])})
    return found


@router.get('')
def list_handouts(space_id: int, request: Request):
    space_access(actor(request), space_id)
    rows = store.rows('''SELECT h.*, u.display_name AS updated_by_name FROM handouts h
        LEFT JOIN users u ON u.id=h.updated_by WHERE h.space_id=? ORDER BY h.updated DESC''', (space_id,))
    return [public(row, space_id, with_document=False) for row in rows]


@router.post('')
async def create_handout(space_id: int, request: Request):
    user = actor(request)
    space_access(user, space_id, True)
    body = await request.json()
    title = handout_title(body.get('title'))
    folder = folder_reference(body.get('folder_id'), space_id, kind='lesson')
    document = clean_document(rewrite_assets(body['document'], space_id, outgoing=False)) if 'document' in body else empty_document()
    now = time.time()
    with store.db() as c:
        handout_id = c.execute('''INSERT INTO handouts(space_id,folder_id,title,document,revision,created_by,updated_by,created,updated)
            VALUES (?,?,?,?,1,?,?,?,?)''', (space_id, folder, title, json.dumps(document, ensure_ascii=False),
                                             user['id'], user['id'], now, now)).lastrowid
    store.audit(user, 'handout_create', f'{space_id}:{handout_id}')
    return public(own_handout(space_id, handout_id), space_id)


@router.get('/{handout_id}')
def get_handout(space_id: int, handout_id: int, request: Request):
    space_access(actor(request), space_id)
    return public(own_handout(space_id, handout_id), space_id)


@router.put('/{handout_id}')
async def save_handout(space_id: int, handout_id: int, request: Request):
    """Save the whole handout; a stale revision is refused so two editors never overwrite each other."""
    user = actor(request)
    space_access(user, space_id, True)
    own_handout(space_id, handout_id)
    body = await request.json()
    title = handout_title(body.get('title'))
    document = clean_document(rewrite_assets(body.get('document'), space_id, outgoing=False))
    with store.db() as c:
        changed = c.execute('''UPDATE handouts SET title=?, document=?, revision=revision+1, updated_by=?, updated=?
            WHERE id=? AND space_id=? AND revision=?''', (title, json.dumps(document, ensure_ascii=False), user['id'],
                                                          time.time(), handout_id, space_id, body.get('revision'))).rowcount
    if not changed:
        raise HTTPException(409, '讲义已被其他人修改，请刷新后再编辑')
    return public(own_handout(space_id, handout_id), space_id)


@router.patch('/{handout_id}')
async def update_handout(space_id: int, handout_id: int, request: Request):
    """Rename or move to another folder without touching the content."""
    user = actor(request)
    space_access(user, space_id, True)
    row = own_handout(space_id, handout_id)
    body = await request.json()
    title = handout_title(body['title']) if 'title' in body else row['title']
    folder = folder_reference(body['folder_id'], space_id, kind='lesson', allow_unfiled=True) if 'folder_id' in body else row['folder_id']
    with store.db() as c:
        c.execute('UPDATE handouts SET title=?, folder_id=? WHERE id=?', (title, folder, handout_id))
    return public(own_handout(space_id, handout_id), space_id, with_document=False)


@router.delete('/{handout_id}')
def delete_handout(space_id: int, handout_id: int, request: Request):
    user = actor(request)
    space_access(user, space_id, True)
    own_handout(space_id, handout_id)
    with store.db() as c:
        c.execute('DELETE FROM handouts WHERE id=?', (handout_id,))
    store.audit(user, 'handout_delete', f'{space_id}:{handout_id}')
    return {'status': 'success'}


@router.post('/{handout_id}/copy')
def copy_handout(space_id: int, handout_id: int, request: Request):
    user = actor(request)
    space_access(user, space_id, True)
    row = own_handout(space_id, handout_id)
    now = time.time()
    with store.db() as c:
        copy_id = c.execute('''INSERT INTO handouts(space_id,folder_id,title,document,revision,created_by,updated_by,created,updated)
            VALUES (?,?,?,?,1,?,?,?,?)''', (space_id, row['folder_id'], (row['title'] + '（副本）')[:LIMITS['title']],
                                             row['document'], user['id'], user['id'], now, now)).lastrowid
    return public(own_handout(space_id, copy_id), space_id, with_document=False)


@router.post('/snapshots')
async def snapshots(space_id: int, request: Request):
    """Snapshots of bank questions to insert as examples or exercises."""
    space, _ = space_access(actor(request), space_id, True)
    ids = (await request.json()).get('ids')
    if not isinstance(ids, list) or not ids or len(ids) > LIMITS['questions'] or any(type(i) is not int for i in ids):
        raise HTTPException(400, f'请选择 1–{LIMITS["questions"]} 道题目')
    found = await bank_questions(request, space, ids)
    return rewrite_assets([snapshot({**found[i], 'source_id': i}) for i in ids if i in found], space_id)


@router.get('/{handout_id}/changes')
async def changes(space_id: int, handout_id: int, request: Request):
    """Snapshots whose bank question has since changed or been deleted."""
    space, _ = space_access(actor(request), space_id)
    document = json.loads(own_handout(space_id, handout_id)['document'])
    kept = {question['source_id']: question['source_hash'] for question in questions_in(document)}
    current = await bank_questions(request, space, list(kept)) if kept else {}
    # Hash the bank side as a fresh snapshot, so defaults (empty figure position and the like) compare equal.
    digests = {i: snapshot({**question, 'source_id': i})['source_hash'] for i, question in current.items()}
    return {'changed': [i for i, digest in kept.items() if i in digests and digests[i] != digest],
            'deleted': [i for i in kept if i not in current]}


@router.get('/{handout_id}/export')
async def export(space_id: int, handout_id: int, request: Request, format: str = 'pdf', version: str = 'student'):
    space, _ = space_access(actor(request), space_id)
    if format not in {'pdf', 'docx'} or version not in {'student', 'teacher'}:
        raise HTTPException(400, '导出格式无效')
    row = own_handout(space_id, handout_id)
    workers = request.app.state.workers
    result = await workers.call(await workers.get(space), 'POST', '/internal/handout', json={
        'title': row['title'], 'document': json.loads(row['document']), 'format': format, 'version': version,
        'subject': space['subject'], 'stage': space['stage'], 'textbook': space['textbook']})
    if result.status_code != 200:
        detail = result.json().get('detail', '导出失败') if 'json' in result.headers.get('content-type', '') else '导出失败'
        raise HTTPException(result.status_code if result.status_code < 500 else 502, detail)
    name = f"{row['title']}（{'教师版' if version == 'teacher' else '学生版'}）.{format}"
    return Response(result.content, media_type=result.headers.get('content-type'),
                    headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(name)})
