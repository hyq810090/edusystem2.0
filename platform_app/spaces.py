"""Spaces, their members, paper folders and cross-space question references."""
import asyncio
import json
import re
import time

from fastapi import APIRouter, HTTPException, Request

from platform_app import store
from platform_app.auth import actor, require_admin, require_space_manager, space_access
from platform_app.assets import rewrite_assets

router = APIRouter(prefix='/platform/api/spaces')
KINDS = {'personal', 'team', 'public'}
# References into one space run one at a time, so racing requests for the
# same source question still produce a single copy.
reference_locks = {}


def space_name(value):
    name = str(value or '').strip()
    if not name or len(name) > 80:
        raise HTTPException(400, '空间名称需为 1–80 个字符')
    return name


@router.get('')
def spaces(request: Request):
    return store.accessible_spaces(actor(request))


@router.post('')
async def create_space(request: Request):
    user = actor(request)
    body = await request.json()
    name = space_name(body.get('name'))
    kind = body.get('kind', 'personal')
    if kind not in KINDS:
        raise HTTPException(400, '空间类型无效')
    if kind != 'personal':
        require_admin(user, platform=kind == 'public')
    book = store.one('SELECT id FROM textbooks WHERE id=? AND enabled=1', (body.get('textbook_id'),))
    if not book:
        raise HTTPException(400, '请选择学科、学段与教材')
    with store.db() as c:
        space_id = c.execute('INSERT INTO spaces(name,kind,owner_id,team_id,textbook_id,created) VALUES (?,?,?,?,?,?)',
                             (name, kind, user['id'], user['team_id'] if kind != 'public' else None,
                              book['id'], time.time())).lastrowid
    store.audit(user, 'create_space', space_id)
    return {'id': space_id}


@router.patch('/{space_id}')
async def rename_space(space_id: int, request: Request):
    user = actor(request)
    space, grant = space_access(user, space_id)
    require_space_manager(user, space, grant)
    name = space_name((await request.json()).get('name'))
    with store.db() as c:
        c.execute('UPDATE spaces SET name=? WHERE id=?', (name, space_id))
    store.audit(user, 'rename_space', space_id)
    return {'status': 'success'}


@router.get('/{space_id}/members')
def members(space_id: int, request: Request):
    user = actor(request)
    space, grant = space_access(user, space_id)
    require_space_manager(user, space, grant)
    if space['kind'] != 'team':
        raise HTTPException(400, '仅团队共享空间支持成员权限')
    result = store.rows('SELECT id,username,display_name,role,team_id,enabled FROM users WHERE team_id=? ORDER BY id',
                        (space['team_id'],))
    for target in result:
        target['permission'] = store.permission(target, space)
    return result


@router.post('/{space_id}/members')
async def set_member(space_id: int, request: Request):
    user = actor(request)
    space, grant = space_access(user, space_id)
    require_space_manager(user, space, grant)
    body = await request.json()
    target = store.one('SELECT * FROM users WHERE id=?', (body.get('user_id'),))
    if space['kind'] != 'team' or not target or target['team_id'] != space['team_id'] or body.get('permission') not in {'editor', 'viewer'}:
        raise HTTPException(400, '仅支持同一团队共享空间的查看/编辑权限')
    with store.db() as c:
        c.execute('INSERT OR REPLACE INTO members VALUES (?,?,?)', (space_id, target['id'], body['permission']))
    store.audit(user, 'set_member', f"{space_id}:{target['id']}:{body['permission']}")
    return {'status': 'success'}


def folder_reference(value, space_id, *, kind='paper', allow_unfiled=False):
    """Validate a folder id from a request; None (or 0 when allowed) means unfiled."""
    if value is None or (allow_unfiled and type(value) is int and value == 0):
        return None
    if type(value) is not int or value < 1:
        raise HTTPException(400, '目录 ID 必须是正整数，顶层或未归档请使用 null')
    if not store.one('SELECT id FROM folders WHERE id=? AND space_id=? AND kind=?', (value, space_id, kind)):
        raise HTTPException(400, '目录不属于当前空间和类型')
    return value


MAX_FOLDER_DEPTH = 2


def folder_subtree(space_id, folder_id):
    """The folder and every folder below it (older installs may hold deeper trees)."""
    children = {}
    for row in store.rows('SELECT id,parent_id FROM folders WHERE space_id=?', (space_id,)):
        children.setdefault(row['parent_id'], []).append(row['id'])
    found, pending = set(), [folder_id]
    while pending:
        current = pending.pop()
        if current not in found:
            found.add(current)
            pending.extend(children.get(current, []))
    return found


def folder_depth(folder_id):
    depth, seen = 0, set()
    while folder_id and folder_id not in seen:
        seen.add(folder_id)
        depth += 1
        folder_id = store.one('SELECT parent_id FROM folders WHERE id=?', (folder_id,))['parent_id']
    return depth


def checked_folder_name(body, space_id, kind, parent, own_id=None):
    name = str(body.get('name', '')).strip()
    if not name or len(name) > 80:
        raise HTTPException(400, '目录名称无效')
    if store.one('SELECT 1 FROM folders WHERE space_id=? AND kind=? AND parent_id IS ? AND name=? AND id IS NOT ?',
                 (space_id, kind, parent, name, own_id)):
        raise HTTPException(409, '同一级已有同名目录')
    return name


@router.get('/{space_id}/folders')
def folders(space_id: int, request: Request):
    space_access(actor(request), space_id)
    return {'folders': store.rows('SELECT id,kind,name,parent_id FROM folders WHERE space_id=? ORDER BY name,id', (space_id,)),
            'papers': {r['paper_id']: r['folder_id'] for r in
                       store.rows('SELECT paper_id,folder_id FROM paper_folders WHERE space_id=?', (space_id,))}}


@router.post('/{space_id}/folders')
async def create_folder(space_id: int, request: Request):
    user = actor(request)
    space_access(user, space_id, True)
    body = await request.json()
    kind = body.get('kind', 'paper')
    if kind not in {'paper', 'lesson'}:
        raise HTTPException(400, '目录名称或类型无效')
    parent = folder_reference(body.get('parent_id'), space_id, kind=kind)
    if parent and folder_depth(parent) >= MAX_FOLDER_DEPTH:
        raise HTTPException(400, '目录最多两级')
    name = checked_folder_name(body, space_id, kind, parent)
    with store.db() as c:
        folder_id = c.execute('INSERT INTO folders(space_id,kind,name,parent_id) VALUES (?,?,?,?)',
                              (space_id, kind, name, parent)).lastrowid
    return {'id': folder_id}


def own_folder(space_id, folder_id):
    folder = store.one('SELECT * FROM folders WHERE id=? AND space_id=?', (folder_id, space_id))
    if not folder:
        raise HTTPException(404, '目录不存在')
    return folder


@router.patch('/{space_id}/folders/{folder_id}')
async def rename_folder(space_id: int, folder_id: int, request: Request):
    space_access(actor(request), space_id, True)
    folder = own_folder(space_id, folder_id)
    name = checked_folder_name(await request.json(), space_id, folder['kind'], folder['parent_id'], folder_id)
    with store.db() as c:
        c.execute('UPDATE folders SET name=? WHERE id=?', (name, folder_id))
    return {'status': 'success'}


@router.delete('/{space_id}/folders/{folder_id}')
def delete_folder(space_id: int, folder_id: int, request: Request):
    """Remove a folder and its subfolders; their papers move up to its parent (or unfiled)."""
    user = actor(request)
    space_access(user, space_id, True)
    folder = own_folder(space_id, folder_id)
    removed = folder_subtree(space_id, folder_id)
    marks = ','.join('?' * len(removed))
    with store.db() as c:
        moved = c.execute(f'UPDATE paper_folders SET folder_id=? WHERE space_id=? AND folder_id IN ({marks})',
                          (folder['parent_id'], space_id, *removed)).rowcount
        moved += c.execute(f'UPDATE handouts SET folder_id=? WHERE space_id=? AND folder_id IN ({marks})',
                           (folder['parent_id'], space_id, *removed)).rowcount
        c.execute(f'UPDATE folders SET parent_id=NULL WHERE id IN ({marks})', tuple(removed))
        c.execute(f'DELETE FROM folders WHERE id IN ({marks})', tuple(removed))
    store.audit(user, 'delete_folder', f'{space_id}:{folder_id}:{len(removed)}:{moved}')
    return {'status': 'success', 'folders': len(removed), 'papers_moved': moved}


@router.post('/{space_id}/papers/{paper_id}/folder')
async def move_paper(space_id: int, paper_id: int, request: Request):
    user = actor(request)
    space, _ = space_access(user, space_id, True)
    folder = folder_reference((await request.json()).get('folder_id'), space_id, allow_unfiled=True)
    workers = request.app.state.workers
    result = await workers.call(await workers.get(space), 'GET', f'/api/papers/{paper_id}')
    if result.status_code != 200:
        raise HTTPException(404, '试卷不存在')
    with store.db() as c:
        c.execute('INSERT OR REPLACE INTO paper_folders VALUES (?,?,?)', (space_id, paper_id, folder))
    return {'status': 'success'}


def compatible_sources(user, target):
    """Other readable spaces whose questions can be referenced into target."""
    return [s for s in store.accessible_spaces(user)
            if s['id'] != target['id'] and (s['subject'], s['stage']) == (target['subject'], target['stage'])]


@router.get('/{space_id}/sources')
def sources(space_id: int, request: Request):
    user = actor(request)
    target, _ = space_access(user, space_id)
    return compatible_sources(user, target)


@router.get('/{space_id}/search')
async def search(space_id: int, request: Request):
    """Page through one compatible source space for cross-space selection."""
    user = actor(request)
    target, _ = space_access(user, space_id)
    params = request.query_params
    source = next((s for s in compatible_sources(user, target) if str(s['id']) == params.get('source')), None)
    if not source:
        raise HTTPException(404, '来源空间不存在或与当前学科学段不一致')
    try:
        page = max(1, min(int(params.get('page', '1')), 10000))
    except ValueError:
        raise HTTPException(400, '页码无效')
    workers = request.app.state.workers
    result = await workers.call(await workers.get(source), 'GET', '/api/questions', params={
        'page': page, 'page_size': 10, 'q': params.get('q', ''),
        'question_type': params.get('question_type', ''), 'difficulty': params.get('difficulty', '')})
    if result.status_code != 200:
        raise HTTPException(503, '来源空间检索失败，请稍后重试')
    value = result.json()
    referenced = {r['source_question']: r['question_id'] for r in store.rows(
        'SELECT source_question,question_id FROM origins WHERE space_id=? AND source_space_id=?', (space_id, source['id']))}
    for question in value['items']:
        question['referenced_as'] = referenced.get(question['id'])
    return {**rewrite_assets(value, source['id']), 'source': {k: source[k] for k in ('id', 'name', 'textbook')}}


@router.get('/{space_id}/origins')
def origins(space_id: int, request: Request):
    space_access(actor(request), space_id)
    return store.rows('''SELECT question_id,source_space_id,source_question,source_space_name,created
      FROM origins WHERE space_id=? ORDER BY created DESC''', (space_id,))


COPIED_FIELDS = ('content', 'question_type', 'difficulty', 'source', 'answer_markdown', 'review', 'tikz_code',
                 'category_compulsory', 'category_chapter', 'category_knowledge', 'sub_knowledge', 'image_paths',
                 'tikz_reference_image_path', 'content_tikz_assets', 'answer_tikz_assets', 'figure_align',
                 'figure_align_custom', 'figure_size', 'image_layouts', 'tags')


@router.post('/{space_id}/reference')
async def reference(space_id: int, request: Request):
    """Copy a question (with its images) from another space and remember where it came from."""
    user = actor(request)
    target, _ = space_access(user, space_id, True)
    body = await request.json()
    try:
        source_id, qid = int(body.get('source_space', 0)), int(body.get('question_id', 0))
    except (TypeError, ValueError):
        raise HTTPException(400, '来源题目无效')
    source, _ = space_access(user, source_id)
    if source_id == space_id or (source['subject'], source['stage']) != (target['subject'], target['stage']):
        raise HTTPException(400, '只能从同一学科学段的其他空间引用题目')
    async with reference_locks.setdefault(space_id, asyncio.Lock()):
        return await copy_question(request.app.state.workers, user, target, source, qid)


async def copy_question(workers, user, target, source, qid):
    space_id, source_id = target['id'], source['id']
    target_worker = await workers.get(target)
    existing = store.one('SELECT question_id FROM origins WHERE space_id=? AND source_space_id=? AND source_question=?',
                         (space_id, source_id, qid))
    if existing:
        result = await workers.call(target_worker, 'GET', f'/api/questions/{existing["question_id"]}')
        if result.status_code == 200:
            return rewrite_assets(result.json(), space_id)
    source_worker = await workers.get(source)
    found = await workers.call(source_worker, 'GET', f'/api/questions/{qid}')
    if found.status_code != 200:
        raise HTTPException(404, '来源题目不存在')
    serialized = json.dumps(found.json(), ensure_ascii=False)
    replacements = {}
    for asset in set(re.findall(r'/static/uploads/[A-Za-z0-9_./-]+\.(?:png|jpg|jpeg|gif|webp)', serialized, re.I)):
        download = await workers.call(source_worker, 'GET', asset)
        if download.status_code != 200:
            raise HTTPException(409, '来源题图缺失，未创建不完整的引用题目')
        uploaded = await workers.call(target_worker, 'POST', '/api/upload', files={
            'file': (asset.rsplit('/', 1)[-1], download.content, download.headers.get('content-type', 'image/png'))})
        if uploaded.status_code != 200:
            raise HTTPException(409, '题图复制失败，未保存题目')
        replacements[asset] = uploaded.json()['file_path']
    for old, new in replacements.items():
        serialized = serialized.replace(old, new)
    question = json.loads(serialized)
    # Another textbook's catalog labels must not pose as this textbook's classification.
    if source['textbook_id'] != target['textbook_id']:
        for field in ('category_compulsory', 'category_chapter', 'category_knowledge'):
            question[field] = ''
        question['sub_knowledge'] = []
    # Hidden TikZ reference images are not in upstream display_image_paths.
    question['image_paths'] = list(set(question.get('image_paths', [])) | set(replacements.values()))
    data = {key: json.dumps(question[key], ensure_ascii=False) if isinstance(question.get(key), (list, dict, bool))
            else str(question.get(key, '') or '') for key in COPIED_FIELDS}
    created = await workers.call(target_worker, 'POST', '/api/questions', data=data)
    if created.status_code != 200:
        raise HTTPException(created.status_code, '引用题目保存失败')
    copied = created.json()['question']
    with store.db() as c:
        c.execute('INSERT OR REPLACE INTO origins VALUES (?,?,?,?,?,?,?)',
                  (space_id, copied['id'], source_id, qid, source['name'], user['id'], time.time()))
    store.audit(user, 'reference_question', f'{source_id}:{qid}->{space_id}:{copied["id"]}')
    return rewrite_assets(copied, space_id)
