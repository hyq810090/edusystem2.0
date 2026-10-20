"""Authorized proxy from the browser to a space's private MathBank worker."""
import json
import re
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from platform_app import store
from platform_app.admin import masked, save_config, settings_scope
from platform_app.assets import rewrite_assets
from platform_app.auth import actor, require_admin, space_access
from platform_app.spaces import folder_reference, folder_subtree
from platform_app.worker import ENV_KEYS

router = APIRouter()
# POST endpoints of the engine that only compute and never write.
READ_POSTS = {'heartbeat', 'questions/check-duplicates', 'format/fractions'}
MAX_BODY = 120 * 1024 * 1024


@router.api_route('/static/uploads/{space_id}/{path:path}', methods=['GET', 'HEAD'])
async def asset(space_id: int, path: str, request: Request):
    space, _ = space_access(actor(request), space_id)
    if '..' in path.split('/') or '\\' in path:
        raise HTTPException(400, '无效资源路径')
    workers = request.app.state.workers
    result = await workers.call(await workers.get(space), request.method, '/static/uploads/' + path)
    return Response(result.content, status_code=result.status_code, media_type=result.headers.get('content-type'))


async def guard(path, request, user, space, grant):
    """Platform-owned endpoints answered without the engine; None lets the request through."""
    if path == 'shutdown' or (path.startswith('runtime/') and path != 'runtime/pandoc/status') or path == 'version/check-update':
        raise HTTPException(403, '由平台管理员统一维护服务与运行组件')
    if path == 'config/metadata' and request.method != 'GET':
        require_admin(user)
        expected = json.loads(store.one('SELECT metadata FROM textbooks WHERE id=?', (space['textbook_id'],))['metadata'])
        # The original settings form posts unchanged metadata before saving AI
        # settings. Accept that no-op and reject every real catalog change.
        if await request.json() != expected:
            raise HTTPException(403, '教材与分类由超级管理员统一维护；空间创建后教材固定')
        return {'status': 'success'}
    task_id = path.split('/')[1] if path.startswith('tasks/') else None
    if path == 'ai/manual-crop-pdf':
        task_id = (await request.json()).get('task_id')
    if task_id is not None and not store.one('SELECT 1 FROM tasks WHERE id=? AND space_id=? AND user_id=?',
                                             (task_id, space['id'], user['id'])):
        raise HTTPException(404, '任务不存在或不属于当前账号')
    if path == 'settings/save':
        scope = settings_scope(user, space, request.query_params.get('platform_default') == '1')
        save_config(scope, dict(await request.form()))
        store.audit(user, 'save_ai_settings', scope)
        return {'status': 'success', 'message': '配置已保存，正在运行的识别任务继续使用原配置。'}
    return None


async def platform_default_settings(workers, worker):
    value = (await workers.call(worker, 'GET', '/api/settings')).json()
    own = store.config('platform')
    # Never show a team's credentials in the platform default view.
    for key in ENV_KEYS:
        if key.endswith('_key') or key.endswith('_base_url'):
            value[key] = own.get(key, '')
        elif key in own:
            value[key] = own[key]
    return masked(value)


@router.api_route('/bank/{space_id}/api/{path:path}', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD'])
async def bank(space_id: int, path: str, request: Request):
    user = actor(request)
    is_read = request.method in {'GET', 'HEAD'} or path in READ_POSTS
    space, grant = space_access(user, space_id, not is_read)
    answered = await guard(path, request, user, space, grant)
    if answered is not None:
        return answered
    workers = request.app.state.workers
    worker = await workers.get(space)
    if path == 'settings' and request.query_params.get('platform_default') == '1':
        require_admin(user, True)
        return await platform_default_settings(workers, worker)
    body = await request.body()
    if len(body) > MAX_BODY:
        raise HTTPException(413, '文件超过 120 MB 限制')
    content_type = request.headers.get('content-type', '')
    folder_id = None
    if path == 'paper/save' and body:
        try:
            payload = json.loads(body)
        except ValueError:
            raise HTTPException(400, '试卷数据无效')
        folder_id = folder_reference(payload.get('platform_folder_id') if isinstance(payload, dict) else None,
                                     space_id, allow_unfiled=True)
    if body and 'multipart/form-data' in content_type:
        # Rebuild the form so only text fields get asset prefixes rewritten;
        # uploaded file bytes pass through untouched.
        parts = []
        for key, value in (await request.form()).multi_items():
            if hasattr(value, 'read'):
                parts.append((key, (value.filename, await value.read(), value.content_type)))
            else:
                parts.append((key, (None, rewrite_assets(value, space_id, False))))
        result = await workers.call(worker, request.method, '/api/' + path, params=request.query_params, files=parts)
    else:
        if body and 'application/json' in content_type:
            body = json.dumps(rewrite_assets(json.loads(body), space_id, False), ensure_ascii=False).encode()
        elif body and 'application/x-www-form-urlencoded' in content_type:
            body = urlencode([(k, rewrite_assets(v, space_id, False))
                              for k, v in parse_qsl(body.decode(), keep_blank_values=True)]).encode()
        result = await workers.call(worker, request.method, '/api/' + path, params=request.query_params,
                                    content=body, headers={'content-type': content_type} if content_type else {})
    if not is_read and result.status_code < 400:
        record_write(user, space_id, request.method, path, result, folder_id)
    if path == 'papers' and result.status_code == 200:
        return filed_papers(result.json(), space_id, request.query_params.get('folder_id'))
    return proxy_result(result, path, space_id)


def record_write(user, space_id, method, path, result, folder_id):
    """Keep platform-side links consistent with engine writes and log the change."""
    value = result.json() if 'application/json' in result.headers.get('content-type', '') else {}
    with store.db() as c:
        if path == 'paper/save' and value.get('paper_id'):
            c.execute('INSERT OR REPLACE INTO paper_folders VALUES (?,?,?)', (space_id, value['paper_id'], folder_id))
        elif path in {'upload/pdf-task', 'upload/docx-task'} and value.get('task_id'):
            c.execute('INSERT INTO tasks VALUES (?,?,?)', (value['task_id'], space_id, user['id']))
        # Engine ids can be reused after deleting the newest row; drop stale links.
        elif method == 'DELETE' and re.fullmatch(r'papers/\d+', path):
            c.execute('DELETE FROM paper_folders WHERE space_id=? AND paper_id=?', (space_id, int(path.split('/')[1])))
        elif method == 'DELETE' and re.fullmatch(r'questions/\d+', path):
            c.execute('DELETE FROM origins WHERE space_id=? AND question_id=?', (space_id, int(path.split('/')[1])))
    if not path.startswith('tasks/'):
        store.audit(user, f'bank:{method} {path}', space_id)


def filed_papers(value, space_id, selected):
    """Attach each paper's folder; optionally keep one folder with its subfolders (0 = unfiled)."""
    mappings = {r['paper_id']: r['folder_id'] for r in store.rows('SELECT * FROM paper_folders WHERE space_id=?', (space_id,))}
    for paper in value.get('data', []):
        paper['folder_id'] = mappings.get(paper['id'])
    if selected:
        try:
            folder = folder_reference(int(selected), space_id, allow_unfiled=True)
        except ValueError:
            raise HTTPException(400, '目录 ID 必须是整数')
        kept = folder_subtree(space_id, folder) if folder else {None}
        value['data'] = [p for p in value['data'] if p['folder_id'] in kept]
    return JSONResponse(rewrite_assets(value, space_id))


def proxy_result(result, path, space_id):
    if 'application/json' in result.headers.get('content-type', ''):
        value = result.json()
        if re.fullmatch(r'questions/\d+', path) and isinstance(value, dict) and value.get('id'):
            value['origin'] = store.one('''SELECT source_space_id,source_question,source_space_name,created
              FROM origins WHERE space_id=? AND question_id=?''', (space_id, value['id']))
        return JSONResponse(rewrite_assets(value, space_id), status_code=result.status_code)
    headers = {k: v for k, v in result.headers.items()
               if k.lower() in {'content-type', 'content-disposition'} or k.lower().startswith('x-word-')}
    return Response(result.content, status_code=result.status_code, headers=headers)
