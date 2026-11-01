"""Manual PDF recovery using server-owned input and validated page OCR reuse.

Tasks stay in the native bounded queue. Recovery never certifies source text,
skips review, caches paper splitting, or writes questions to the bank.
"""
from copy import deepcopy
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import uuid

from fastapi import HTTPException

TTL = 3600
MAX_INPUTS = 64


def register(engine, config):
    from mathbank import paths
    from mathbank.pdf_partial import complete_boundary_drafts, merge_partial_drafts, merge_recovered_questions
    from mathbank.processing_limits import LIMITS

    root = paths.SYSTEM_GENERATED_DIR / 'pdf_resume'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = threading.RLock()
    inputs, previous, children = {}, {}, {}
    native_run = engine.run_pdf_parsing_task
    native_fail = engine.DOCUMENT_TASKS.fail
    native_complete = engine.DOCUMENT_TASKS.complete
    # Native task records do not survive a worker restart. Remove only this
    # adapter's now-unusable source copies, never native evidence or uploads.
    for source in root.glob('*.pdf'):
        if re.fullmatch(r'[0-9a-f-]{36}\.pdf', source.name):
            source.unlink(missing_ok=True)

    def metadata_hash():
        return hashlib.sha256(json.dumps(engine.METADATA_CACHE, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def prune(reserve=False):
        now = time.time()
        with lock:
            inactive = []
            for task in inputs:
                state = engine.DOCUMENT_TASKS.snapshot(task)
                if not state or state.get('status') in {'completed', 'cancelled', 'error'}:
                    inactive.append(task)
            expired = [task for task in inactive if now - inputs[task]['created'] > TTL]
            live = sorted((task for task in inactive if task not in expired), key=lambda task: inputs[task]['created'])
            capacity = MAX_INPUTS - (1 if reserve else 0)
            expired.extend(live[:max(0, len(inputs) - len(expired) - capacity)])
            for task in expired:
                inputs.pop(task, None)
                previous.pop(task, None)
                children.pop(task, None)
                (root / f'{task}.pdf').unlink(missing_ok=True)
            for task in list(previous):
                state = engine.DOCUMENT_TASKS.snapshot(task)
                if task not in inputs and (not state or state.get('status') == 'cancelled'):
                    previous.pop(task, None)

    def run(task_id, file_bytes, filename, generate_answers=False, page_range=None,
            pdf_strategy='native_preferred', pdf_verify_suspicions=True):
        prune(reserve=True)
        record = {'created': time.time(), 'sha256': hashlib.sha256(file_bytes).hexdigest(),
                  'metadata': metadata_hash(), 'filename': Path(filename).name,
                  'options': (generate_answers, page_range, pdf_strategy, pdf_verify_suspicions)}
        source = root / f'{task_id}.pdf'
        try:
            source.write_bytes(file_bytes)
            source.chmod(0o600)
        except OSError:
            # Recovery is optional; let the original importer report its own
            # storage failures through the normal native terminal state.
            return native_run(task_id, file_bytes, filename, generate_answers, page_range,
                              pdf_strategy, pdf_verify_suspicions)
        with lock:
            inputs[task_id] = record
        try:
            return native_run(task_id, file_bytes, filename, generate_answers, page_range,
                              pdf_strategy, pdf_verify_suspicions)
        finally:
            state = engine.DOCUMENT_TASKS.snapshot(task_id) or {}
            if state.get('status') == 'error':
                with lock:
                    record['created'] = time.time()
            if state.get('status') in {'completed', 'cancelled'}:
                source.unlink(missing_ok=True)
                with lock:
                    inputs.pop(task_id, None)
                    previous.pop(task_id, None)

    def failed(task_id, error, **result):
        with lock:
            record = inputs.get(task_id)
            delivered = deepcopy(previous.get(task_id, []))
        if record and result.get('document_type') == 'pdf':
            pages = result.get('pdf_source_pages') or []
            cards, isolated = complete_boundary_drafts(pages, source_sha256=record['sha256'],
                                                       paper_title=Path(record['filename']).stem)
            for card in cards:
                card['difficulty'] = 'unrated'
            cards = merge_partial_drafts(delivered, cards)
            failed_pages = [p['page_number'] for p in result.get('diagnostics', {}).get('pdf_failed_pages', [])]
            result.update(data=cards, pdf_resume_available=True,
                          pdf_recovery={'failed_pages': failed_pages, 'delivered_cards': len(cards),
                                        'quarantined_ranges': isolated, 'preserve_edits': True})
        return native_fail(task_id, error, **result)

    def complete(task_id, **result):
        with lock:
            delivered = deepcopy(previous.get(task_id, []))
            record = inputs.get(task_id)
        if record and delivered and result.get('document_type') == 'pdf':
            state = engine.DOCUMENT_TASKS.snapshot(task_id) or {}
            pages = result.get('pdf_source_pages') or state.get('pdf_source_pages') or []
            source = '\n\n'.join(page.get('markdown', '') for page in pages)
            if not source:
                source = str(state.get('pdf_source_cache', {}).get('source_markdown', ''))
            merged, isolated = merge_recovered_questions(delivered, result.get('data') or [], source,
                                                         source_sha256=record['sha256'])
            # Keep positional evidence tied to the right card after the merge.
            # Unmatched evidence is archived, never attached to a different draft.
            incoming = result.get('data') or []
            locations = {}
            for index, card in enumerate(merged):
                locations.setdefault((card.get('content'), card.get('answer_markdown')), []).append(index)
            mapping = {}
            for index, card in enumerate(incoming):
                matches = locations.get((card.get('content'), card.get('answer_markdown')), [])
                if len(matches) == 1:
                    mapping[index] = matches[0]
            orphaned = []

            def remap(value):
                if isinstance(value, list):
                    return [mapped for item in value if (mapped := remap(item)) is not None]
                if not isinstance(value, dict):
                    return value
                if 'question_index' in value:
                    index = value['question_index']
                    if type(index) is not int or index not in mapping:
                        orphaned.append(deepcopy(value))
                        return None
                    value = {**value, 'question_index': mapping[index]}
                return {key: mapped for key, item in value.items() if (mapped := remap(item)) is not None}

            result['diagnostics'] = remap(deepcopy(result.get('diagnostics') or {}))
            if orphaned:
                result['diagnostics']['pdf_recovery_unmatched_evidence'] = orphaned
            result['data'] = merged
            result['pdf_recovery'] = {'preserved_cards': len(delivered), 'quarantined_ranges': isolated,
                                      'preserve_edits': True}
            result['partial'] = bool(isolated) or result.get('partial', False)
            result['log'] = f'恢复完成，保留已交付题稿，当前共 {len(merged)} 道；请核对题稿及未匹配原文。'
        return native_complete(task_id, **result)

    @engine.app.post('/api/tasks/{task_id}/resume')
    def resume(task_id: str):
        if not re.fullmatch(r'[0-9a-f-]{36}', task_id):
            raise HTTPException(404, '任务不存在')
        prune()
        with lock:
            task = engine.DOCUMENT_TASKS.snapshot(task_id)
            record = inputs.get(task_id)
            if not task or not record:
                raise HTTPException(404, '恢复输入已过期，请重新上传原 PDF；已识别页面仍可使用缓存')
            if task.get('status') != 'error' or not task.get('pdf_resume_available'):
                raise HTTPException(409, '仅支持恢复已中断的 PDF 任务')
            if task_id in children:
                raise HTTPException(409, '此任务已发起恢复，请查看新的任务')
            if record['metadata'] != metadata_hash():
                raise HTTPException(409, '教材配置已变化，请重新上传并核对分类')
            source = root / f'{task_id}.pdf'
            if not source.is_file() or source.is_symlink() or source.stat().st_size > LIMITS.document_upload_bytes:
                raise HTTPException(409, '原 PDF 已不可用，请重新上传')
            data = source.read_bytes()
            if hashlib.sha256(data).hexdigest() != record['sha256']:
                raise HTTPException(409, '原 PDF 校验失败，请重新上传')
            new_id = str(uuid.uuid4())
            previous[new_id] = deepcopy(task.get('data') or [])
            engine.DOCUMENT_TASKS.create(new_id, status='pending', document_type='pdf', temp_assets=[],
                                         log='正在恢复 PDF；复用已识别页面，后续拆题与核验照常执行。')
            try:
                engine.DOCUMENT_TASKS.submit(new_id, run, new_id, data, record['filename'], *record['options'])
            except engine.TaskQueueFull:
                engine.DOCUMENT_TASKS.remove(new_id)
                previous.pop(new_id, None)
                raise HTTPException(429, '后台任务繁忙，请稍后恢复')
            children[task_id] = new_id
            return {'status': 'success', 'task_id': new_id, 'resumed_from': task_id}

    engine.run_pdf_parsing_task = run
    engine.DOCUMENT_TASKS.fail = failed
    engine.DOCUMENT_TASKS.complete = complete
    original_lifespan = engine.app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        stop = threading.Event()

        def maintain():
            while not stop.wait(60):
                try:
                    prune()
                except OSError:
                    pass  # Permission/storage errors must not stop native task cleanup.

        thread = threading.Thread(target=maintain, name='mathbank-pdf-recovery-cleanup', daemon=True)
        thread.start()
        try:
            async with original_lifespan(app):
                yield
        finally:
            stop.set()
            thread.join(timeout=2)

    engine.app.router.lifespan_context = lifespan
