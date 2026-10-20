"""MinerU import: turn a paper into a Markdown source for MathBank's 导入中心.

A job takes a PDF or image (parsed by MinerU with the team's MinerU key) or a
MinerU result zip, rebuilds the Markdown with mineru_markdown, and uploads the
figures into the space's bank in batches the bank accepts. The browser loads
the Markdown into 导入中心 and bridge.js adds the figure mapping to the parse
request, so teachers review and save questions with the native flow. The
original paper stays available (/origin) for side-by-side review.
"""
import asyncio
from dataclasses import asdict
import hashlib
import io
import json
import os
import shutil
import time
import uuid
import zipfile
from pathlib import Path

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from platform_app import store
from platform_app.assets import rewrite_assets
from platform_app.auth import actor, space_access
from platform_app.mineru_client import API, MinerUClient, MinerUError, Options
from platform_app.mineru_markdown import convert

router = APIRouter(prefix='/platform/api/spaces/{space_id}/mineru')
SOURCES = {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.bmp'}
ORIGIN_TYPES = {'.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                '.webp': 'image/webp', '.bmp': 'image/bmp'}
UPLOAD_BATCH = 20          # the bank's /api/upload/batch limit
MAX_BYTES = 120 * 1024 * 1024
KEEP_SECONDS = 3 * 86400
# Result zips of earlier MinerU parses: the same file with the same options is not sent
# (and paid for) again. Kept 30 days, at most 2 GB, least recently used first out.
CACHE_SECONDS = 30 * 86400
CACHE_BYTES = 2 * 1024 ** 3
jobs = {}


def directory():
    return store.DATA / 'mineru'


def cached_result(data: bytes, options: Options) -> Path:
    key = hashlib.sha256(json.dumps({'file': hashlib.sha256(data).hexdigest(), 'options': asdict(options),
                                     'api': API}, sort_keys=True).encode()).hexdigest()
    return store.DATA / 'mineru-cache' / f'{key}.zip'


def extract(data, target):
    target = target.resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            destination = (target / member.filename).resolve()
            if not destination.is_relative_to(target):
                raise MinerUError(f'结果包含非法路径：{member.filename}')
            if not member.is_dir():
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(member))


def content_list(result):
    found = [p for p in result.rglob('*_content_list.json') if not p.name.endswith('_v2.json')]
    if not found:
        raise MinerUError('没有找到 MinerU 的 content_list.json，请上传完整的结果包')
    return found[0], json.loads(found[0].read_text(encoding='utf-8'))


async def upload_figures(workers, space, base, names):
    worker = await workers.get(space)
    mapping = {}
    for start in range(0, len(names), UPLOAD_BATCH):
        batch = names[start:start + UPLOAD_BATCH]
        files = [('files', (Path(name).name, (base / name).read_bytes(), 'image/jpeg')) for name in batch]
        response = await workers.call(worker, 'POST', '/api/upload/batch', files=files)
        value = response.json()
        if response.status_code != 200 or value.get('status') != 'success':
            raise MinerUError(value.get('message') or '配图上传失败')
        mapping.update(value['mapping'])
    return mapping


async def run(job, workers, space, source, options, key):
    def update(**values):
        job.update(values, elapsed=round(time.monotonic() - job['_started'], 1))

    try:
        result = job['_dir'] / 'result'
        cached = None if source.suffix == '.zip' else cached_result(source.read_bytes(), options)
        if source.suffix == '.zip':
            extract(source.read_bytes(), result)
        elif cached.exists():
            update(state='downloading', message='复用此前的 MinerU 解析结果')
            extract(cached.read_bytes(), result)
            os.utime(cached)
            job['cached'] = True
        else:
            client = MinerUClient(key)
            try:
                update(state='uploading', message='正在上传到 MinerU')
                batch = await asyncio.to_thread(client.submit, source, options)

                def progress(item):
                    info = item.get('extract_progress') or {}
                    pages = f"，{info['extracted_pages']}/{info['total_pages']} 页" if info.get('total_pages') else ''
                    update(state='parsing', message=f'MinerU 正在解析{pages}')
                item = await asyncio.to_thread(client.wait, batch, on_progress=progress)
                update(state='downloading', message='正在下载解析结果')
                await asyncio.to_thread(client.download, item['full_zip_url'], result, cached)
            finally:
                client.close()
        path, blocks = content_list(result)
        markdown, figures, report = convert(blocks)
        update(state='uploading_figures', message=f'正在上传 {len(figures)} 张配图')
        mapping = await upload_figures(workers, space, path.parent, figures)
        report['figures'] = len(figures)
        # The original paper for side-by-side review: the upload, or the PDF a result zip carries.
        job['_origin'] = next(result.rglob('*_origin.pdf'), None) if source.suffix == '.zip' else source
        update(state='done', message='已完成（复用此前的解析结果，未再调用 MinerU）' if job.get('cached') else '已完成',
               result={'markdown': markdown, 'report': report, 'origin': bool(job['_origin']),
                       'cached': bool(job.get('cached')), 'mapping': rewrite_assets(mapping, space['id'])})
    except (MinerUError, OSError, ValueError, zipfile.BadZipFile, httpx.HTTPError, HTTPException) as error:
        update(state='failed', message=getattr(error, 'detail', None) or str(error))


def cleanup():
    root = directory()
    for path in root.glob('*') if root.exists() else ():
        if path.is_dir() and path.stat().st_mtime < time.time() - KEEP_SECONDS:
            shutil.rmtree(path, ignore_errors=True)
    cache = store.DATA / 'mineru-cache'
    entries = sorted(cache.glob('*.zip'), key=lambda p: p.stat().st_mtime) if cache.exists() else []
    total = sum(p.stat().st_size for p in entries)
    for path in entries:
        if path.stat().st_mtime >= time.time() - CACHE_SECONDS and total <= CACHE_BYTES:
            continue
        total -= path.stat().st_size
        path.unlink(missing_ok=True)
    for job_id in [j for j, job in jobs.items() if job['_started'] < time.monotonic() - KEEP_SECONDS]:
        del jobs[job_id]


def public(job):
    return {k: v for k, v in job.items() if not k.startswith('_')}


@router.get('/status')
def status(space_id: int, request: Request):
    space, _ = space_access(actor(request), space_id)
    return {'configured': bool(store.effective_config(space).get('mineru_key'))}


@router.post('')
async def start(space_id: int, request: Request, file: UploadFile = File(...), page_ranges: str = Form(''),
                is_ocr: bool = Form(True), model_version: str = Form('vlm')):
    user = actor(request)
    space, _ = space_access(user, space_id, True)
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in SOURCES | {'.zip'} or model_version not in {'vlm', 'pipeline'}:
        raise HTTPException(400, '请上传 PDF、图片，或 MinerU 结果包（.zip）')
    key = store.effective_config(space).get('mineru_key', '')
    if suffix != '.zip' and not key:
        raise HTTPException(400, '管理员尚未配置 MinerU API Key；也可以上传在 mineru.net 下载的结果包（.zip）')
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, '文件超过 120 MB')
    cleanup()
    job_id = uuid.uuid4().hex
    folder = directory() / job_id
    folder.mkdir(parents=True)
    source = folder / f'source{suffix}'
    source.write_bytes(data)
    job = {'id': job_id, 'file': file.filename, 'state': 'queued', 'message': '已排队', 'elapsed': 0,
           'title': Path(file.filename).stem, '_user': user['id'], '_space': space_id, '_dir': folder,
           '_started': time.monotonic()}
    jobs[job_id] = job
    options = Options(model_version=model_version, is_ocr=is_ocr, page_ranges=page_ranges.strip())
    asyncio.create_task(run(job, request.app.state.workers, space, source, options, key))
    store.audit(user, 'mineru_import', f'{space_id}:{file.filename}')
    return public(job)


def own_job(space_id, job_id, request):
    job = jobs.get(job_id)
    if not job or job['_space'] != space_id or job['_user'] != actor(request)['id']:
        raise HTTPException(404, '解析任务不存在')
    return job


@router.get('/{job_id}')
def job_status(space_id: int, job_id: str, request: Request):
    return public(own_job(space_id, job_id, request))


@router.get('/{job_id}/origin')
def job_origin(space_id: int, job_id: str, request: Request):
    origin = own_job(space_id, job_id, request).get('_origin')
    if not origin or not origin.exists():
        raise HTTPException(404, '这次解析没有可显示的原卷')
    return FileResponse(origin, media_type=ORIGIN_TYPES.get(origin.suffix.lower(), 'application/octet-stream'))
