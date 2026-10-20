"""MathBank platform: accounts and spaces around unchanged MathBank workers."""
from contextlib import asynccontextmanager
import html
import json
import secrets

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from platform_app import admin, auth, bank, handouts, imports, previews, spaces, store
from platform_app.auth import actor, public_user, space_access
from platform_app.workers import Workers

WEB = store.ROOT / 'platform_app/web'


@asynccontextmanager
async def lifespan(app):
    store.initialize()
    app.state.workers = Workers()
    yield
    await app.state.workers.close()


app = FastAPI(title='MathBank 账号与空间版', lifespan=lifespan, docs_url=None, redoc_url=None)
for module in (auth, admin, spaces, imports, previews, handouts, bank):
    app.include_router(module.router)


def rejected(detail, status, headers=None):
    return JSONResponse({'detail': detail}, status_code=status, headers=headers)


@app.middleware('http')
async def security(request, call_next):
    path = request.url.path
    api = path.startswith(('/platform/api/', '/bank/'))
    # A tab's storage scope belongs to the session that rendered it. After a
    # login in another tab, never deliver the new account's reads into the
    # previous account's page and local drafts.
    if request.method in {'GET', 'HEAD'} and api and request.headers.get('x-local-token'):
        try:
            user = actor(request)
        except HTTPException as exc:
            return rejected(exc.detail, exc.status_code, {'Cache-Control': 'no-store'})
        if not secrets.compare_digest(request.headers['x-local-token'], user['csrf']):
            return rejected('登录账号或会话已变更，请重新打开工作台', 409,
                            {'X-MathBank-Session-Changed': '1', 'Cache-Control': 'no-store'})
    if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        origin = request.headers.get('origin')
        if (origin and origin.rstrip('/') != f'{request.url.scheme}://{request.headers.get("host", "")}') \
                or request.headers.get('sec-fetch-site') == 'cross-site':
            return rejected('跨站请求已拒绝', 403)
        if path != '/platform/api/login':
            try:
                user = actor(request)
            except HTTPException as exc:
                return rejected(exc.detail, exc.status_code)
            if not secrets.compare_digest(request.headers.get('x-local-token', ''), user['csrf']):
                return rejected('页面已过期，请刷新后重试', 403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    if not path.startswith('/static/lib/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/healthz')
def health():
    return {'ready': True, 'product': 'MathBank 账号与空间版'}


def account_workspace():
    return (WEB / 'account.html').read_text(encoding='utf-8')


@app.get('/')
def home(request: Request):
    """Open the last used space, else the oldest accessible one, else onboarding."""
    try:
        user = actor(request)
    except HTTPException:
        user = None
    if user:
        available = store.accessible_spaces(user)
        chosen = next((s for s in available if s['id'] == user['last_space_id']),
                      min(available, key=lambda s: s['id'], default=None))
        if chosen:
            return RedirectResponse(f'/workbench/{chosen["id"]}')
    content = (WEB / 'home.html').read_text(encoding='utf-8')
    return HTMLResponse(content.replace('<!-- ACCOUNT_WORKSPACE -->', account_workspace()))


def inject(content, user, space, grant, secret, nav_collapsed=False):
    """Mount the platform into the native MathBank page without editing it."""
    if nav_collapsed:  # applied server-side so the page never paints the wide sidebar first
        content = content.replace('<html lang="zh-CN">', '<html lang="zh-CN" data-nav="collapsed">', 1)
    context = {**public_user(user), 'space': space, 'permission': grant,
               'can_manage': auth.can_manage(user, space, grant)}
    head = ('<script>window.MathBankPlatform=' + json.dumps(context, ensure_ascii=False).replace('<', '\\u003c') +
            ';</script>' + ''.join(f'<script src="/platform/web/{name}.js"></script>'
                                   for name in ('connection-status', 'difficulty', 'bridge')))
    title = html.escape(f"{space['name']} · {space['stage']}{space['subject']} · MathBank")
    replacements = [
        (secret, user['csrf']),
        ('<title>判桃MATH</title>', f'<title>{title}</title>'),
        ('常规题数量', '已定级题目'), ('易错题数量', '易错标签题数'),
        ('挑战题数量', '挑战标签题数'), ('强基题数量', '强基标签题数'),
        ('录入新数学题', '录入新' + html.escape(space['subject']) + '题'),
        ('/static/js/qa-data.js', '/platform/web/qa-data.js'), ('/static/js/qa.js', '/platform/web/qa.js'),
        ('上传 PDF、Word、TeX 或 MD 文件，或展开文本输入区选择格式并粘贴内容，再点击“开始解析”。',
         '选好试卷后点击“开始解析”。拆出的题目会显示在这里，可与左侧原卷逐题对照，再勾选入库。'),
    ]
    for old, new in replacements:
        content = content.replace(old, new)
    for marker, addition in [('<head>', '<head>' + head),
                             ('</head>', '<link rel="stylesheet" href="/platform/web/platform.css"></head>'),
                             ('</main>', account_workspace() + '</main>'),
                             ('</body>', ''.join(f'<script src="/platform/web/{name}.js"></script>'
                                                 for name in ('space-picker', 'account', 'paper-tools', 'import-view', 'mineru', 'handouts')) + '</body>')]:
        content = content.replace(marker, addition, 1)
    return content


@app.get('/workbench/{space_id}')
async def workbench(space_id: int, request: Request):
    try:
        user = actor(request)
    except HTTPException:
        return RedirectResponse('/')
    space, grant = space_access(user, space_id)
    if user['last_space_id'] != space_id:
        with store.db() as c:
            c.execute('UPDATE users SET last_space_id=? WHERE id=?', (space_id, user['id']))
    workers = request.app.state.workers
    worker = await workers.get(space)
    upstream = await workers.call(worker, 'GET', '/')
    return HTMLResponse(inject(upstream.text, user, space, grant, worker.secret,
                               request.cookies.get('mb_nav') == 'collapsed'))


app.mount('/platform/web', StaticFiles(directory=WEB), name='platform-web')
# Uploads are served per space by the bank router above; this snapshot holds
# no private uploads or databases. All product libraries are original MathBank.
app.mount('/static', StaticFiles(directory=store.ROOT / 'engine/static'), name='static')
