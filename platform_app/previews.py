"""Original-paper previews for 导入中心: a Word paper is shown as LibreOffice's PDF
of the unchanged file, so teachers compare parsed questions with the real pages.
PDFs, images and text sources are shown by the browser without a server step."""
import asyncio
import hashlib
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from platform_app import store
from platform_app.auth import actor, space_access

router = APIRouter(prefix='/platform/api/spaces/{space_id}/source-preview')
MAX_BYTES = 60 * 1024 * 1024
KEEP_SECONDS = 3 * 86400
TIMEOUT = 120
lock = asyncio.Lock()  # one LibreOffice conversion at a time


def renderer():
    return shutil.which('soffice') or shutil.which('libreoffice')


def to_pdf(program, data):
    with tempfile.TemporaryDirectory(prefix='mb-preview-') as folder:
        work = Path(folder)
        (work / 'paper.docx').write_bytes(data)
        # A private profile keeps this conversion apart from any other LibreOffice use.
        subprocess.run([program, f'-env:UserInstallation={(work / "profile").as_uri()}', '--headless', '--norestore',
                        '--convert-to', 'pdf', '--outdir', str(work), str(work / 'paper.docx')],
                       capture_output=True, timeout=TIMEOUT, check=False)
        output = work / 'paper.pdf'
        return output.read_bytes() if output.exists() else None


def cleanup(root):
    for path in root.glob('*.pdf'):
        if path.stat().st_mtime < time.time() - KEEP_SECONDS:
            path.unlink(missing_ok=True)


@router.post('')
async def preview(space_id: int, request: Request, file: UploadFile = File(...)):
    space_access(actor(request), space_id, True)
    if not (file.filename or '').lower().endswith('.docx'):
        raise HTTPException(400, '只有 Word（.docx）需要生成预览')
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or not data.startswith(b'PK'):
        raise HTTPException(400, 'Word 文件无效或超过 60 MB')
    program = renderer()
    if not program:
        raise HTTPException(503, '服务器未安装 LibreOffice，暂时无法预览 Word 原卷')
    root = store.DATA / 'previews'
    root.mkdir(parents=True, exist_ok=True)
    cached = root / f'{hashlib.sha256(data).hexdigest()}.pdf'
    if not cached.exists():
        async with lock:
            cleanup(root)
            try:
                pdf = await asyncio.to_thread(to_pdf, program, data)
            except subprocess.TimeoutExpired:
                pdf = None
            if not pdf:
                raise HTTPException(422, 'Word 原卷预览生成失败，可直接开始解析')
            cached.write_bytes(pdf)
    return FileResponse(cached, media_type='application/pdf')
