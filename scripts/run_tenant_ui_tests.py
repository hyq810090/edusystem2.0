"""Run browser regressions with fresh data, then gracefully close all workers.

Each script has a time limit (MATHBANK_UI_TEST_TIMEOUT seconds, default 600). A
script past it is stopped with its browser and reported, and the rest still run:
Playwright waits without limit for its driver when a page has crashed.
"""
import asyncio
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = float(os.environ.get('MATHBANK_UI_TEST_TIMEOUT', '600'))


def stop_tree(process):
    """Stop a test script together with its Playwright driver and browser."""
    if os.name == 'nt':
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(process.pid)], capture_output=True)
    else:
        os.killpg(process.pid, signal.SIGKILL)


async def run_script(script):
    started = time.monotonic()
    options = {} if os.name == 'nt' else {'start_new_session': True}
    process = await asyncio.create_subprocess_exec(sys.executable, str(ROOT/'tests_platform'/script), cwd=ROOT, **options)
    try:
        result = await asyncio.wait_for(process.wait(), TIMEOUT)
    except asyncio.TimeoutError:
        stop_tree(process)
        await process.wait()
        result = 'timeout'
    print(f'[{script}] {"passed" if result == 0 else f"FAILED ({result})"} in {time.monotonic() - started:.0f} s', flush=True)
    return result


async def run():
    with tempfile.TemporaryDirectory(prefix='mathbank-tenant-ui-') as temporary:
        os.environ['MATHBANK_PLATFORM_DATA'] = temporary
        os.environ['MATHBANK_TEST_DATA'] = temporary
        os.environ['PYTHONUTF8'] = '1'
        sys.path.insert(0,str(ROOT))
        from platform_app import store
        store.DATA = Path(temporary)
        from platform_app.app import app
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); sock.listen(128); sock.setblocking(False)
            os.environ['MATHBANK_TEST_URL'] = f'http://127.0.0.1:{sock.getsockname()[1]}'
            server = uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False))
            serving = asyncio.create_task(server.serve(sockets=[sock]))
            try:
                while not server.started:
                    if serving.done():
                        await serving
                        raise RuntimeError('Isolated server failed to start')
                    await asyncio.sleep(.05)
                # The empty-account test runs before public spaces are created.
                # Arguments run only the named scripts (e.g. workspace_ui_smoke.py).
                scripts = sys.argv[1:] or ['onboarding_ui_smoke.py','account_ui_smoke.py','multitenant_ui_smoke.py','workspace_ui_smoke.py','handout_ui_smoke.py','classification_review_ui_smoke.py','drawing_ui_smoke.py','knowledge_point_ui_smoke.py']
                failed = [script for script in scripts if await run_script(script) != 0]
                if failed:
                    raise RuntimeError('failed: ' + ', '.join(failed))
            finally:
                server.should_exit = True
                await serving


if __name__ == '__main__':
    asyncio.run(run())
