"""绘图: GeoGebra drawings of a space, made in the self-hosted GeoGebra Classic.

A drawing keeps its construction (.ggb as base64), the AI conversation that
built it, and a PNG the browser exports on save; the server checks the PNG and
trims its white margins. A question uses a drawing by uploading a copy of the
PNG through the bank's own image upload, so later edits to the drawing never
change a stored question.

Another member opens a saved .ggb in their browser, so every stored file is
rebuilt without JavaScript: the global script is reset and object scripts are
removed (GeoGebra scripts stay). The AI turn runs in the space's worker
(platform_app/ggb_ai.py) with the configured drawing model.
"""
import base64
import binascii
import io
import json
import re
import time
import zipfile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from PIL import Image, ImageChops

from platform_app import store
from platform_app.auth import actor, space_access

router = APIRouter(prefix='/platform/api/spaces/{space_id}/drawings')

GEOGEBRA_VERSION = '5.4.933'  # platform_app/vendor/geogebra/<version>, see scripts/fetch_geogebra.py
LIMITS = {'title': 80, 'ggb': 6_000_000, 'png': 8_000_000, 'pixels': 25_000_000, 'unzipped': 30_000_000,
          'entries': 64, 'chat': 40, 'message': 4000, 'commands': 150, 'construction': 30_000, 'image': 8_000_000}
TRIM_PADDING = 16
OBJECT_SCRIPT = re.compile(r'<javascript\b[^>]*/>|<javascript\b[^>]*>.*?</javascript>', re.S | re.I)
IMAGE_URL = re.compile(r'data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)')


def decode(value, field, limit):
    if not isinstance(value, str) or len(value) > limit:
        raise HTTPException(400, f'{field}无效或过大')
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, f'{field}无效') from None


def title(value, default='未命名绘图'):
    if value is None:
        return default
    if not isinstance(value, str) or len(value.strip()) > LIMITS['title']:
        raise HTTPException(400, f'标题应为不超过 {LIMITS["title"]} 字的文本')
    return value.strip() or default


def clean_ggb(value):
    """A GeoGebra file rebuilt with only its own parts and without any JavaScript."""
    if value in (None, ''):
        return ''
    data = decode(value, 'GeoGebra 文件', LIMITS['ggb'])
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        entries = archive.infolist()
        if (len(entries) > LIMITS['entries'] or sum(e.file_size for e in entries) > LIMITS['unzipped']
                or 'geogebra.xml' not in archive.namelist()):
            raise HTTPException(400, 'GeoGebra 文件内容无效')
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as rebuilt:
            for entry in entries:
                name = entry.filename
                if entry.is_dir() or name.startswith('/') or '..' in name.split('/') or '\\' in name:
                    continue
                content = archive.read(entry)
                if name == 'geogebra_javascript.js':
                    content = b'function ggbOnInit() {}'
                elif name.endswith('.xml'):
                    content = OBJECT_SCRIPT.sub('', content.decode('utf-8')).encode('utf-8')
                rebuilt.writestr(name, content)
    except (zipfile.BadZipFile, UnicodeDecodeError, ValueError, KeyError):
        raise HTTPException(400, 'GeoGebra 文件内容无效') from None
    return base64.b64encode(out.getvalue()).decode('ascii')


def clean_png(value):
    """A real PNG, with white margins trimmed to a small even border; returns (bytes, width, height)."""
    data = decode(value, '图片', LIMITS['png'])
    try:
        image = Image.open(io.BytesIO(data))
        if image.format != 'PNG' or image.width * image.height > LIMITS['pixels']:
            raise HTTPException(400, '图片应为大小合适的 PNG')
        image.load()
    except (OSError, Image.DecompressionBombError):
        raise HTTPException(400, '图片无法读取') from None
    if image.mode in ('RGBA', 'LA', 'P'):
        image = image.convert('RGBA')
        flat = Image.new('RGB', image.size, 'white')
        flat.paste(image, mask=image.split()[-1])
        image = flat
    else:
        image = image.convert('RGB')
    # Near-white counts as background, so antialiasing at the view's edge is not kept.
    ink = ImageChops.difference(image, Image.new('RGB', image.size, 'white')).convert('L').point(lambda v: 255 if v > 24 else 0)
    box = ink.getbbox()
    if box:
        left, top, right, bottom = box
        image = image.crop((max(0, left - TRIM_PADDING), max(0, top - TRIM_PADDING),
                            min(image.width, right + TRIM_PADDING), min(image.height, bottom + TRIM_PADDING)))
    out = io.BytesIO()
    image.save(out, 'PNG', optimize=True)
    return out.getvalue(), image.width, image.height


def clean_chat(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise HTTPException(400, '对话记录无效')
    messages = []
    for item in value[-LIMITS['chat']:]:
        if not isinstance(item, dict) or item.get('role') not in ('user', 'assistant'):
            raise HTTPException(400, '对话记录无效')
        text = item.get('text', '')
        commands = item.get('commands', [])
        if (not isinstance(text, str) or len(text) > LIMITS['message'] or not isinstance(commands, list)
                or len(commands) > LIMITS['commands'] or not all(isinstance(c, str) and len(c) <= 400 for c in commands)):
            raise HTTPException(400, '对话记录无效')
        message = {'role': item['role'], 'text': text}
        if commands:
            message['commands'] = commands
        if isinstance(item.get('note'), str) and len(item['note']) <= 400:
            message['note'] = item['note']
        messages.append(message)
    return messages


def own(space_id, drawing_id):
    row = store.one('SELECT * FROM drawings WHERE id=? AND space_id=?', (drawing_id, space_id))
    if not row:
        raise HTTPException(404, '绘图不存在')
    return row


def public(row, full=False):
    value = {key: row[key] for key in ('id', 'title', 'revision', 'width', 'height', 'created', 'updated')}
    value['updated_by_name'] = row.get('updated_by_name')
    value['has_image'] = row['png'] is not None
    if full:
        value['ggb'] = row['ggb']
        value['chat'] = json.loads(row['chat'])
    return value


def with_names(space_id, drawing_id):
    row = store.one('''SELECT d.*, u.display_name AS updated_by_name FROM drawings d
        LEFT JOIN users u ON u.id=d.updated_by WHERE d.id=? AND d.space_id=?''', (drawing_id, space_id))
    if not row:
        raise HTTPException(404, '绘图不存在')
    return row


@router.get('')
def list_drawings(space_id: int, request: Request):
    space_access(actor(request), space_id)
    rows = store.rows('''SELECT d.*, u.display_name AS updated_by_name FROM drawings d
        LEFT JOIN users u ON u.id=d.updated_by WHERE d.space_id=? ORDER BY d.updated DESC''', (space_id,))
    return [public(row) for row in rows]


@router.post('/ai')
async def ai_turn(space_id: int, request: Request):
    """One turn of the AI conversation: the drawing model answers with GeoGebra commands."""
    user = actor(request)
    space, _ = space_access(user, space_id, True)
    body = await request.json()
    messages = clean_chat(body.get('messages'))
    if not messages or messages[-1]['role'] != 'user' or not messages[-1]['text'].strip():
        raise HTTPException(400, '请先写下要画什么')
    construction = body.get('construction') or ''
    if not isinstance(construction, str) or len(construction) > LIMITS['construction']:
        raise HTTPException(400, '当前作图内容过多，请先删去不需要的对象')
    image = body.get('image') or ''
    if image:
        match = IMAGE_URL.fullmatch(image) if isinstance(image, str) and len(image) <= LIMITS['image'] else None
        if not match:
            raise HTTPException(400, '参考图应为 PNG、JPEG 或 WebP 图片')
        try:
            Image.open(io.BytesIO(base64.b64decode(match[2], validate=True))).verify()
        except (OSError, binascii.Error, ValueError):
            raise HTTPException(400, '参考图无法读取') from None
    workers = request.app.state.workers
    result = await workers.call(await workers.get(space), 'POST', '/internal/ggb-ai', json={
        'messages': messages[-12:], 'construction': construction, 'image': image})
    store.audit(user, 'drawing_ai', space_id)
    if result.status_code != 200:
        detail = result.json().get('detail', 'AI 绘图失败') if 'json' in result.headers.get('content-type', '') else 'AI 绘图失败'
        raise HTTPException(result.status_code if result.status_code < 500 else 502, detail)
    return result.json()


@router.post('')
async def create_drawing(space_id: int, request: Request):
    user = actor(request)
    space_access(user, space_id, True)
    body = await request.json()
    ggb = clean_ggb(body.get('ggb'))
    png, width, height = clean_png(body['png']) if body.get('png') else (None, None, None)
    now = time.time()
    with store.db() as c:
        drawing_id = c.execute('''INSERT INTO drawings(space_id,title,ggb,png,width,height,chat,revision,created_by,
            updated_by,created,updated) VALUES (?,?,?,?,?,?,?,1,?,?,?,?)''',
            (space_id, title(body.get('title')), ggb, png, width, height,
             json.dumps(clean_chat(body.get('chat')), ensure_ascii=False), user['id'], user['id'], now, now)).lastrowid
    store.audit(user, 'drawing_create', f'{space_id}:{drawing_id}')
    return public(with_names(space_id, drawing_id), full=True)


@router.get('/{drawing_id}')
def get_drawing(space_id: int, drawing_id: int, request: Request):
    space_access(actor(request), space_id)
    return public(with_names(space_id, drawing_id), full=True)


@router.get('/{drawing_id}/image.png')
def drawing_image(space_id: int, drawing_id: int, request: Request):
    space_access(actor(request), space_id)
    row = own(space_id, drawing_id)
    if row['png'] is None:
        raise HTTPException(404, '这张绘图还没有保存图片')
    return Response(row['png'], media_type='image/png')


@router.put('/{drawing_id}')
async def save_drawing(space_id: int, drawing_id: int, request: Request):
    """Save the whole drawing; a stale revision is refused so two editors never overwrite each other."""
    user = actor(request)
    space_access(user, space_id, True)
    own(space_id, drawing_id)
    body = await request.json()
    ggb = clean_ggb(body.get('ggb'))
    png, width, height = clean_png(body['png']) if body.get('png') else (None, None, None)
    with store.db() as c:
        changed = c.execute('''UPDATE drawings SET title=?, ggb=?, png=?, width=?, height=?, chat=?, revision=revision+1,
            updated_by=?, updated=? WHERE id=? AND space_id=? AND revision=?''',
            (title(body.get('title')), ggb, png, width, height, json.dumps(clean_chat(body.get('chat')), ensure_ascii=False),
             user['id'], time.time(), drawing_id, space_id, body.get('revision'))).rowcount
    if not changed:
        raise HTTPException(409, '绘图已被其他人修改，请载入最新版本或另存为新绘图')
    return public(with_names(space_id, drawing_id))


@router.patch('/{drawing_id}')
async def rename_drawing(space_id: int, drawing_id: int, request: Request):
    user = actor(request)
    space_access(user, space_id, True)
    own(space_id, drawing_id)
    body = await request.json()
    with store.db() as c:
        c.execute('UPDATE drawings SET title=? WHERE id=?', (title(body.get('title')), drawing_id))
    return public(with_names(space_id, drawing_id))


@router.post('/{drawing_id}/copy')
def copy_drawing(space_id: int, drawing_id: int, request: Request):
    user = actor(request)
    space_access(user, space_id, True)
    row = own(space_id, drawing_id)
    now = time.time()
    with store.db() as c:
        copied = c.execute('''INSERT INTO drawings(space_id,title,ggb,png,width,height,chat,revision,created_by,updated_by,
            created,updated) VALUES (?,?,?,?,?,?,?,1,?,?,?,?)''',
            (space_id, (row['title'] + ' 副本')[:LIMITS['title']], row['ggb'], row['png'], row['width'], row['height'],
             row['chat'], user['id'], user['id'], now, now)).lastrowid
    store.audit(user, 'drawing_create', f'{space_id}:{copied}')
    return public(with_names(space_id, copied))


@router.delete('/{drawing_id}')
def delete_drawing(space_id: int, drawing_id: int, request: Request):
    user = actor(request)
    space_access(user, space_id, True)
    own(space_id, drawing_id)
    with store.db() as c:
        c.execute('DELETE FROM drawings WHERE id=?', (drawing_id,))
    store.audit(user, 'drawing_delete', f'{space_id}:{drawing_id}')
    return {'status': 'success'}
