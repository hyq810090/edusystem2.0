"""Local MinerU trial page: parse a paper with your MinerU API key and review the result.

    python tools/mineru_trial/app.py        then open http://127.0.0.1:3031

The key comes from the MINERU_API_KEY environment variable or the page. It is
kept in memory for the run only and never written to disk. Every run is saved
under test-results/mineru-trial/ so results can be compared later.
"""
from pathlib import Path
import json
import os
import re
import sys
import threading
import time
import uuid

import fitz
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from platform_app.mineru_client import MinerUClient, MinerUError, Options  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RUNS = Path(os.environ.get('MINERU_TRIAL_DIR', ROOT / 'test-results/mineru-trial'))
SUFFIXES = {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.docx', '.pptx'}
PREVIEW_PAGES = 30
POLL_SECONDS = 3

app = FastAPI(title='MinerU 试用', docs_url=None, redoc_url=None)
app.mount('/lib', StaticFiles(directory=ROOT / 'engine/static/lib'), name='lib')
runs = {}
lock = threading.Lock()


def save(run):
    """job.json holds everything shown on the page; never the API key."""
    with lock:
        runs[run['id']] = run
        (RUNS / run['id'] / 'job.json').write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding='utf-8')


def page_numbers(ranges, total):
    if not ranges.strip():
        return list(range(1, total + 1))
    pages = set()
    for part in ranges.split(','):
        start, _, end = part.strip().partition('-')
        if start.isdigit():
            pages.update(range(int(start), int(end) + 1 if end.isdigit() else int(start) + 1))
    return sorted(p for p in pages if 1 <= p <= total)


def previews(source, directory, ranges):
    """Original pages next to the result, and the number of pages sent."""
    if source.suffix.lower() != '.pdf':
        return ([source.name] if source.suffix.lower() in SUFFIXES - {'.docx', '.pptx'} else []), None
    directory.mkdir(exist_ok=True)
    names = []
    with fitz.open(source) as document:
        numbers = page_numbers(ranges, document.page_count)
        for number in numbers[:PREVIEW_PAGES]:
            name = f'pages/page-{number}.png'
            document[number - 1].get_pixmap(dpi=110).save(directory.parent / name)
            names.append(name)
    return names, len(numbers)


def summarize(result_dir):
    markdown = next(result_dir.rglob('full.md'), None)
    if not markdown:
        raise MinerUError('结果中没有 full.md')
    text = markdown.read_text(encoding='utf-8')
    display = re.findall(r'\$\$.+?\$\$', text, re.S)
    inline = re.findall(r'(?<!\$)\$(?!\$)[^$\n]+?\$(?!\$)', re.sub(r'\$\$.+?\$\$', '', text, flags=re.S))
    return {'markdown': markdown.relative_to(result_dir.parent).as_posix(),
            'images_dir': str((markdown.parent / 'images').resolve()),
            'characters': len(text), 'display_formulas': len(display), 'inline_formulas': len(inline),
            'images': len(re.findall(r'!\[[^\]]*\]\([^)]+\)', text)), 'tables': text.count('<table')}


def parse(run, token, source, options):
    started = time.monotonic()
    client = MinerUClient(token)

    def progress(result):
        info = result.get('extract_progress') or {}
        # 'done' is reserved for after the result is downloaded and summarized.
        state = 'downloading' if result['state'] == 'done' else result['state']
        run.update(state=state, extracted=info.get('extracted_pages'), total=info.get('total_pages') or run['total'],
                   elapsed=round(time.monotonic() - started, 1))
        save(run)
    try:
        batch = client.submit(source, options)
        run.update(state='uploaded', upload_seconds=round(time.monotonic() - started, 1))
        save(run)
        result = client.wait(batch, on_progress=progress, interval=POLL_SECONDS)
        client.download(result['full_zip_url'], RUNS / run['id'] / 'result')
        run.update(state='done', summary=summarize(RUNS / run['id'] / 'result'),
                   elapsed=round(time.monotonic() - started, 1))
    except (MinerUError, OSError, ValueError) as error:
        run.update(state='failed', error=str(error), elapsed=round(time.monotonic() - started, 1))
    finally:
        client.close()
        save(run)


@app.get('/', response_class=HTMLResponse)
def index():
    return (HERE / 'index.html').read_text(encoding='utf-8')


@app.get('/api/config')
def config():
    return {'env_key': bool(os.environ.get('MINERU_API_KEY')), 'runs_dir': str(RUNS.resolve())}


@app.get('/api/runs')
def list_runs():
    for path in RUNS.glob('*/job.json'):
        runs.setdefault(path.parent.name, json.loads(path.read_text(encoding='utf-8')))
    return sorted(runs.values(), key=lambda r: r['id'], reverse=True)


def find(run_id):
    if run_id not in runs:
        list_runs()
    if run_id not in runs:
        raise HTTPException(404, '没有这次解析')
    return runs[run_id]


@app.get('/api/runs/{run_id}')
def get_run(run_id: str):
    return find(run_id)


@app.post('/api/runs')
async def create_run(file: UploadFile = File(...), token: str = Form(''), model_version: str = Form('vlm'),
                     is_ocr: bool = Form(False), enable_formula: bool = Form(True), enable_table: bool = Form(True),
                     language: str = Form('ch'), page_ranges: str = Form('')):
    token = token.strip() or os.environ.get('MINERU_API_KEY', '')
    if not token:
        raise HTTPException(400, '请填写 MinerU API Key，或设置环境变量 MINERU_API_KEY')
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in SUFFIXES or model_version not in {'vlm', 'pipeline'}:
        raise HTTPException(400, '请上传 PDF、图片、Word 或 PPT 文件')
    if page_ranges and not re.fullmatch(r'\s*\d+(-\d+)?(\s*,\s*\d+(-\d+)?)*\s*', page_ranges):
        raise HTTPException(400, '页码范围格式如 1-3,5')
    stem = re.sub(r'[^\w一-鿿-]+', '_', Path(file.filename).stem)[:40] or 'paper'
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{stem}-{model_version}-{uuid.uuid4().hex[:4]}"
    directory = RUNS / run_id
    directory.mkdir(parents=True)
    source = directory / f'{stem}{suffix}'
    source.write_bytes(await file.read())
    options = Options(model_version, is_ocr, enable_formula, enable_table, language.strip() or 'ch', page_ranges.strip())
    pages, total = previews(source, directory / 'pages', page_ranges)
    run = {'id': run_id, 'file': file.filename, 'options': options.__dict__, 'state': 'uploading',
           'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'pages': pages, 'total': total}
    save(run)
    threading.Thread(target=parse, args=(run, token, source, options), daemon=True).start()
    return run


@app.get('/runs/{run_id}/{path:path}')
def run_file(run_id: str, path: str):
    find(run_id)
    base = (RUNS / run_id).resolve()
    target = (base / path).resolve()
    if not target.is_relative_to(base) or not target.is_file() or target.name == 'job.json':
        raise HTTPException(404)
    return FileResponse(target)


if __name__ == '__main__':
    import uvicorn
    RUNS.mkdir(parents=True, exist_ok=True)
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('MINERU_TRIAL_PORT', '3031')), log_level='warning')
