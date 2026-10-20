"""Accounts, teams, textbooks and model settings."""
import json
import re
import sqlite3

from fastapi import APIRouter, HTTPException, Request

from engine.mathbank.difficulty import fix_metadata
from platform_app import physics_metadata, store
from platform_app.auth import actor, require_admin
from platform_app.worker import ENV_KEYS

router = APIRouter(prefix='/platform/api')
ROLES = {'member', 'team_admin'}
# Settings used by the platform itself; ENV_KEYS are handed to the workers.
PLATFORM_KEYS = {'mineru_key'}


def scoped(user, sql, column):
    """Restrict an admin listing to the caller's team unless super admin."""
    if user['role'] == 'super_admin':
        return store.rows(sql)
    return store.rows(sql + f' WHERE {column}=?', (user['team_id'],))


@router.get('/admin/users')
def users(request: Request):
    user = actor(request)
    require_admin(user)
    return scoped(user, 'SELECT id,username,display_name,role,team_id,enabled FROM users', 'team_id')


@router.get('/admin/teams')
def teams(request: Request):
    user = actor(request)
    require_admin(user)
    return scoped(user, 'SELECT * FROM teams', 'id')


@router.post('/admin/teams')
async def create_team(request: Request):
    user = actor(request)
    require_admin(user, True)
    name = str((await request.json()).get('name', '')).strip()
    if not name or len(name) > 80:
        raise HTTPException(400, '团队名称无效')
    try:
        with store.db() as c:
            team_id = c.execute('INSERT INTO teams(name) VALUES (?)', (name,)).lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(409, '团队名称已存在')
    return {'id': team_id}


def valid_password(value):
    if not 10 <= len(value) <= 200:
        raise HTTPException(400, '密码需要 10–200 个字符')
    return store.password_hash(value)


@router.post('/admin/users')
async def create_user(request: Request):
    user = actor(request)
    require_admin(user)
    body = await request.json()
    role = body.get('role', 'member')
    team = body.get('team_id') if user['role'] == 'super_admin' else user['team_id']
    if role not in ROLES or (user['role'] != 'super_admin' and role != 'member'):
        raise HTTPException(403, '不能授予此角色')
    username = str(body.get('username', '')).strip()
    if not re.fullmatch(r'[A-Za-z0-9_.-]{3,50}', username):
        raise HTTPException(400, '账号需 3–50 位字母、数字或 ._-')
    password = valid_password(str(body.get('password', '')))
    if not store.one('SELECT id FROM teams WHERE id=?', (team,)):
        raise HTTPException(400, '团队不存在')
    try:
        with store.db() as c:
            uid = c.execute('INSERT INTO users(username,display_name,password,role,team_id) VALUES (?,?,?,?,?)',
                            (username, str(body.get('display_name') or username)[:80], password, role, team)).lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(409, '账号已存在或团队已不可用')
    store.audit(user, 'create_user', uid)
    return {'id': uid}


@router.patch('/admin/users/{user_id}')
async def update_user(user_id: int, request: Request):
    """Rename, reset password, enable/disable; role and team for super admins."""
    user = actor(request)
    require_admin(user)
    target = store.one('SELECT * FROM users WHERE id=?', (user_id,))
    if not target or (user['role'] != 'super_admin' and target['team_id'] != user['team_id']):
        raise HTTPException(404, '账号不存在')
    if user['role'] != 'super_admin' and target['role'] != 'member':
        raise HTTPException(403, '团队管理员只能管理本团队教师账号')
    body = await request.json()
    changes = {}
    if 'display_name' in body:
        name = str(body['display_name']).strip()
        if not name or len(name) > 80:
            raise HTTPException(400, '姓名无效')
        changes['display_name'] = name
    if 'password' in body:
        changes['password'] = valid_password(str(body['password']))
    if 'enabled' in body:
        changes['enabled'] = 1 if body['enabled'] else 0
    if 'role' in body or 'team_id' in body:
        require_admin(user, True)
        if target['role'] == 'super_admin' or body.get('role', target['role']) not in ROLES:
            raise HTTPException(403, '不能修改此角色')
        if not store.one('SELECT id FROM teams WHERE id=?', (body.get('team_id', target['team_id']),)):
            raise HTTPException(400, '团队不存在')
        changes['role'] = body.get('role', target['role'])
        changes['team_id'] = body.get('team_id', target['team_id'])
    if target['id'] == user['id'] and ('enabled' in changes or 'role' in changes):
        raise HTTPException(400, '不能停用或调整自己的账号')
    if not changes:
        raise HTTPException(400, '没有需要保存的修改')
    with store.db() as c:
        c.execute('UPDATE users SET ' + ','.join(f'{k}=?' for k in changes) + ' WHERE id=?', (*changes.values(), user_id))
        if {'password', 'enabled', 'role', 'team_id'} & set(changes):
            c.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
    store.audit(user, 'update_user', f'{user_id}:{",".join(sorted(changes))}')
    return {'status': 'success'}


def textbook_summary(book):
    metadata = json.loads(book.pop('metadata'))
    curriculum = metadata.get('curriculum', {})
    book['volumes'] = len(curriculum)
    book['chapters'] = sum(len(v) for v in curriculum.values() if isinstance(v, dict))
    book['question_types'] = [t.get('label', '') for t in metadata.get('question_types', [])]
    return book


@router.get('/textbooks')
def textbooks(request: Request):
    user = actor(request)
    books = store.rows('''SELECT t.*, (SELECT count(*) FROM spaces WHERE textbook_id=t.id) AS spaces
      FROM textbooks t ORDER BY subject, stage, id''')
    return [textbook_summary(b) for b in books if b['enabled'] or user['role'] == 'super_admin']


@router.get('/admin/textbooks/{book_id}')
def textbook(book_id: int, request: Request):
    require_admin(actor(request), True)
    book = store.one('SELECT * FROM textbooks WHERE id=?', (book_id,))
    if not book:
        raise HTTPException(404, '教材不存在')
    book['metadata'] = json.loads(book['metadata'])
    return book


@router.post('/admin/textbooks')
async def create_textbook(request: Request):
    user = actor(request)
    require_admin(user, True)
    body = await request.json()
    if any(not str(body.get(k, '')).strip() or len(str(body[k])) > 80 for k in ('stage', 'subject', 'name')):
        raise HTTPException(400, '请填写学段、学科、教材版本名称')
    metadata = body.get('metadata')
    if not isinstance(metadata, dict) or not isinstance(metadata.get('curriculum'), dict):
        raise HTTPException(400, '教材目录必须是对象')
    metadata = fix_metadata(metadata)
    types = metadata.get('question_types')
    if not isinstance(types, list) or not types or any(not isinstance(v, dict) or not v.get('value') or not v.get('label') for v in types):
        raise HTTPException(400, '题型必须包含 value、label')
    if body['subject'].strip() == '物理':
        metadata = physics_metadata.upgrade_defaults(metadata)
    version = body.get('engine_version', 'A')
    if version not in {'A', 'B', 'S', 'H'}:
        raise HTTPException(400, '内部存储版本无效')
    with store.db() as c:
        bid = c.execute('INSERT INTO textbooks(stage,subject,name,engine_version,metadata) VALUES (?,?,?,?,?)',
                        (body['stage'].strip(), body['subject'].strip(), body['name'].strip(), version,
                         json.dumps(metadata, ensure_ascii=False))).lastrowid
    store.audit(user, 'create_textbook', bid)
    return {'id': bid}


@router.patch('/admin/textbooks/{book_id}')
async def update_textbook(book_id: int, request: Request):
    """Catalogs stay fixed once spaces use them; only the label and availability change."""
    user = actor(request)
    require_admin(user, True)
    body = await request.json()
    changes = {}
    if 'name' in body:
        name = str(body['name']).strip()
        if not name or len(name) > 80:
            raise HTTPException(400, '教材名称无效')
        changes['name'] = name
    if 'enabled' in body:
        changes['enabled'] = 1 if body['enabled'] else 0
    if not changes or not store.one('SELECT id FROM textbooks WHERE id=?', (book_id,)):
        raise HTTPException(400, '教材不存在或没有修改')
    with store.db() as c:
        c.execute('UPDATE textbooks SET ' + ','.join(f'{k}=?' for k in changes) + ' WHERE id=?', (*changes.values(), book_id))
    store.audit(user, 'update_textbook', book_id)
    return {'status': 'success'}


def settings_scope(user, space, platform=False):
    require_admin(user, platform)
    if platform:
        return 'platform'
    if space['team_id'] != user['team_id'] and user['role'] != 'super_admin':
        raise HTTPException(403, '不能设置其他团队的模型')
    return 'team:' + str(space['team_id']) if space['team_id'] else 'platform'


def masked(config):
    return {k: ('••••••••' if k.endswith('_key') and v else v) for k, v in config.items()}


def save_config(scope, values):
    config = store.config(scope)
    for key, value in values.items():
        if key not in ENV_KEYS and key not in PLATFORM_KEYS:
            continue
        value = str(value).strip()
        if '•' in value:
            continue
        if '\n' in value or '\r' in value or len(value) > 2000:
            raise HTTPException(400, '模型配置字段无效')
        if value:
            config[key] = value
        else:
            config.pop(key, None)
    with store.db() as c:
        c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (scope, json.dumps(config)))


def request_scope(user, value):
    if value == 'platform':
        require_admin(user, True)
        return 'platform'
    require_admin(user)
    return 'team:' + str(user['team_id'])


@router.get('/ai-settings')
def ai_settings(request: Request):
    user = actor(request)
    return masked(store.config(request_scope(user, request.query_params.get('scope'))))


@router.post('/ai-settings')
async def save_ai_settings(request: Request):
    user = actor(request)
    body = await request.json()
    scope = request_scope(user, body.get('scope'))
    save_config(scope, body.get('config', {}))
    store.audit(user, 'save_ai_settings', scope)
    return {'status': 'success'}
