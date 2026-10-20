"""Run browser regressions with fresh data, then gracefully close all workers."""
import asyncio
import os
from pathlib import Path
import socket
import sys
import tempfile

import uvicorn

ROOT = Path(__file__).resolve().parents[1]


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
                scripts = sys.argv[1:] or ['onboarding_ui_smoke.py','account_ui_smoke.py','multitenant_ui_smoke.py','workspace_ui_smoke.py','handout_ui_smoke.py','classification_review_ui_smoke.py']
                for script in scripts:
                    process = await asyncio.create_subprocess_exec(sys.executable,str(ROOT/'tests_platform'/script),cwd=ROOT)
                    result = await process.wait()
                    if result:
                        raise RuntimeError(f'{script} failed ({result})')
            finally:
                server.should_exit = True
                await serving


if __name__ == '__main__':
    asyncio.run(run())
