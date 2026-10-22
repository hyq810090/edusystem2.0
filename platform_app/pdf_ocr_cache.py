"""Reuse PDF page recognition, inside the space's worker.

Every page-level vision request of a PDF import (whole-page OCR, joint page
recognition, regional recognition, figure location) goes through the engine's
request_pdf_vision. Here the provider's accepted response is kept, keyed by the
exact request: the same page pixels, prompt, model and endpoint. Importing the
same PDF again with the same settings, or starting over after a failed import,
then skips the pages already recognised; only the rest are sent. A hit is
validated again by the caller's own validator against the current page, and
reports no model call. The paper-splitting request is never cached.

Each PDF task records the span of cache events during its run, so the import
page can say how many pages were reused, or after a failure how many recognised
pages are kept for the next attempt. Two imports running at the same time in
one space share that span; the numbers are a notice, not an accounting.

The idea follows MathBank 3.0 (reuse complete OCR pages of the same PDF and
identical page pixels); the implementation is the platform's own.
"""
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import threading
import time

from fastapi import HTTPException

CACHED_STAGES = {'page_ocr', 'joint_page', 'regions', 'figure_layout'}
MODULES = ('mathbank.pdf_page_vision', 'mathbank.pdf_region_vision', 'mathbank.pdf_figures')
MAX_FILES = 3000
MAX_BYTES = 300 * 1024 * 1024
MAX_AGE = 30 * 86400
_events = deque(maxlen=5000)  # (sequence number, 'reused' | 'saved', page index)
_tasks = {}  # PDF task id -> [first event number, last event number or None while running]
_lock = threading.Lock()
_count = 0


def record(kind, page_index):
    global _count
    with _lock:
        _count += 1
        _events.append((_count, kind, page_index))


def task_report(task_id):
    with _lock:
        span = _tasks.get(task_id)
        if span is None:
            return None
        first, last = span[0], span[1] if span[1] is not None else _count
        events = [(kind, page) for number, kind, page in _events if first < number <= last]
    pages = lambda kind: sorted({page + 1 for k, page in events if k == kind and isinstance(page, int)})
    return {'reused': pages('reused'), 'saved': pages('saved')}


def request_key(stage, provider, payload):
    identity = {'stage': stage, 'base': getattr(provider, 'api_base', ''), 'model': getattr(provider, 'model_name', ''),
                'provider': getattr(provider, 'provider_code', ''), 'payload': payload}
    data = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


class Cache:
    def __init__(self, directory):
        self.directory = Path(directory)

    def path(self, key):
        return self.directory / f'{key}.json'

    def load(self, key):
        path = self.path(key)
        try:
            body = json.loads(path.read_text(encoding='utf-8'))
            os.utime(path)
            return body
        except (OSError, ValueError):
            return None

    def save(self, key, body):
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path(key).with_suffix('.part')
        temporary.write_text(json.dumps(body, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, self.path(key))

    def prune(self):
        if not self.directory.exists():
            return
        entries = sorted((p for p in self.directory.glob('*.json')), key=lambda p: p.stat().st_mtime, reverse=True)
        total, now = 0, time.time()
        for index, path in enumerate(entries):
            stat = path.stat()
            total += stat.st_size
            if index >= MAX_FILES or total > MAX_BYTES or now - stat.st_mtime > MAX_AGE:
                path.unlink(missing_ok=True)


def cached(original, cache):
    def request_pdf_vision(provider, payload, *, post, validate, label, stage, page_index=None, **options):
        if stage not in CACHED_STAGES:
            return original(provider, payload, post=post, validate=validate, label=label, stage=stage,
                            page_index=page_index, **options)
        key = request_key(stage, provider, payload)
        body = cache.load(key)
        if body is not None:
            try:
                result = validate(body)
            except ValueError:
                result = None  # e.g. the validator changed; ask the model again
            if isinstance(result, dict):
                result.update(attempts=[], visual_calls=0, usage={}, usage_complete=True, reused=True)
                record('reused', page_index)
                return result
        last = {}

        def keep_response(*args, **kwargs):
            response = post(*args, **kwargs)
            last['response'] = response
            return response

        result = original(provider, payload, post=keep_response, validate=validate, label=label, stage=stage,
                          page_index=page_index, **options)
        # Reaching here, the last response passed the caller's validation.
        response = last.get('response')
        if response is not None and getattr(response, 'status_code', None) == 200:
            try:
                cache.save(key, response.json())
                record('saved', page_index)
            except (OSError, ValueError, TypeError):
                pass
        return result
    request_pdf_vision.platform_cache = cache
    return request_pdf_vision


def register(engine):
    import importlib
    from mathbank import paths
    cache = Cache(paths.SYSTEM_GENERATED_DIR / 'pdf_ocr_cache')
    try:
        cache.prune()
    except OSError:
        pass
    wrapped = cached(engine.request_pdf_vision, cache)
    engine.request_pdf_vision = wrapped
    for name in MODULES:
        module = importlib.import_module(name)
        if hasattr(module, 'request_pdf_vision'):
            module.request_pdf_vision = wrapped

    original_task = engine.run_pdf_parsing_task

    def run_pdf_parsing_task(task_id, *args, **kwargs):
        with _lock:
            _tasks[task_id] = [_count, None]
            while len(_tasks) > 200:
                del _tasks[next(iter(_tasks))]
        try:
            return original_task(task_id, *args, **kwargs)
        finally:
            with _lock:
                if task_id in _tasks:
                    _tasks[task_id][1] = _count
    # upload_pdf_task looks the runner up in the module when it submits a task.
    engine.run_pdf_parsing_task = run_pdf_parsing_task

    # Under /api/tasks/ so the platform's check that the task belongs to the caller applies (bank.guard).
    @engine.app.get('/api/tasks/{task_id}/ocr-reuse')
    async def reuse(task_id: str):
        """Pages of one PDF task answered from earlier recognition, and pages kept for a later attempt."""
        report = task_report(task_id)
        if report is None:
            raise HTTPException(status_code=404, detail='没有这个 PDF 任务的识别记录。')
        return report
