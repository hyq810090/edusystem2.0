"""The platform proxy must outlast its private worker's configured AI calls."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_nondefault_limits_reach_platform_and_worker_without_starting_a_bank():
    root = Path(__file__).resolve().parents[1]
    script = '''
import json
from engine.mathbank.processing_limits import LIMITS
from platform_app.previews import MAX_BYTES, TIMEOUT
from platform_app.bank import MAX_BODY
from platform_app.workers import Workers
import asyncio
async def run():
    workers = Workers()
    print(json.dumps([LIMITS.pdf_pages, MAX_BYTES, TIMEOUT, MAX_BODY, workers.client.timeout.read]))
    await workers.client.aclose()
asyncio.run(run())
'''
    env = os.environ.copy()
    env.update(MATHBANK_MAX_DOCUMENT_MIB='200', MATHBANK_MAX_PDF_PAGES='90',
               MATHBANK_DOCUMENT_AI_TIMEOUT_SECONDS='2400', MATHBANK_AI_REQUEST_TIMEOUT_SECONDS='1200')
    result = subprocess.run([sys.executable, '-c', script], cwd=root, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    pages, preview_bytes, preview_time, body_bytes, proxy_wait = json.loads(result.stdout)
    assert (pages, preview_bytes, preview_time) == (90, 200 * 1048576, 300)
    assert body_bytes > preview_bytes
    assert proxy_wait > 2400
