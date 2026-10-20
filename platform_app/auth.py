"""Sessions, login and the access checks shared by every route."""
import hashlib
import secrets
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from platform_app import store

router = APIRouter(prefix='/platform/api')
SESSION_SECONDS = 43200
failures = {}


def actor(request):
    token = request.cookies.get('mb_session', '')
    session = store.one('''SELECT s.csrf,u.* FROM sessions s JOIN users u ON u.id=s.user_id
      WHERE s.token=? AND s.expires>? AND u.enabled=1''', (hashlib.sha256(token.encode()).hexdigest(), time.time()))
    if not session:
        raise HTTPException(401, '请先登录')
    return session


def public_user(user):
    value = {k: user[k] for k in ('id', 'username', 'display_name', 'role', 'team_id', 'csrf')}
    team = store.one('SELECT name FROM teams WHERE id=?', (user['team_id'],))
    value['team_name'] = team['name'] if team else '未分配团队'
    return value


def require_admin(user, platform=False):
    if user['role'] not in ({'super_admin'} if platform else {'super_admin', 'team_admin'}):
        raise HTTPException(403, '需要管理员权限')


def space_access(user, space_id, write=False):
    space = store.get_space(space_id)
    grant = store.permission(user, space) if space else None
    if not grant:
        raise HTTPException(404, '空间不存在或没有访问权限')
    if write and grant not in {'owner', 'editor'}:
        raise HTTPException(403, '该空间仅允许查看')
    return space, grant


def can_manage(user, space, grant):
    return grant == 'owner' or (space['kind'] == 'team' and space['team_id'] == user['team_id']
                                and user['role'] == 'team_admin')


def require_space_manager(user, space, grant):
    if not can_manage(user, space, grant):
        raise HTTPException(403, '只有空间所有者或所属团队管理员可管理空间')


@router.post('/login')
async def login(request: Request):
    body = await request.json()
    name = str(body.get('username', ''))[:100]
    key = (request.client.host, name)
    now = time.time()
    recent = [t for t in failures.get(key, []) if t > now - 300]
    if len(recent) >= 10:
        raise HTTPException(429, '登录尝试过多，请五分钟后重试')
    user = store.one('SELECT * FROM users WHERE username=? AND enabled=1', (name,))
    if not user or not store.check_password(str(body.get('password', ''))[:1000], user['password']):
        failures[key] = recent + [now]
        raise HTTPException(401, '账号或密码错误')
    failures.pop(key, None)
    token = secrets.token_urlsafe(32)
    with store.db() as c:
        c.execute('DELETE FROM sessions WHERE expires<?', (now,))
        c.execute('INSERT INTO sessions VALUES (?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), user['id'],
                                                            secrets.token_urlsafe(32), now + SESSION_SECONDS))
    response = JSONResponse({'status': 'success'})
    response.set_cookie('mb_session', token, httponly=True, samesite='strict',
                        secure=request.url.scheme == 'https', max_age=SESSION_SECONDS)
    return response


@router.post('/logout')
def logout(request: Request):
    with store.db() as c:
        c.execute('DELETE FROM sessions WHERE token=?', (hashlib.sha256(request.cookies.get('mb_session', '').encode()).hexdigest(),))
    response = JSONResponse({'status': 'success'})
    response.delete_cookie('mb_session')
    return response


@router.get('/me')
def me(request: Request):
    return public_user(actor(request))


@router.post('/password')
async def password(request: Request):
    user = actor(request)
    body = await request.json()
    if not store.check_password(str(body.get('old', '')), user['password']):
        raise HTTPException(400, '原密码错误')
    new = str(body.get('new', ''))
    if not 10 <= len(new) <= 200:
        raise HTTPException(400, '密码需要 10–200 个字符')
    with store.db() as c:
        c.execute('UPDATE users SET password=? WHERE id=?', (store.password_hash(new), user['id']))
        c.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
    return {'status': 'success'}
