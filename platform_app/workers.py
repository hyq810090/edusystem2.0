import asyncio
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from dataclasses import dataclass

import httpx
from fastapi import HTTPException
from platform_app import store
from platform_app.worker import ENV_KEYS

def write_private(path,value):
    """Replace a file holding model keys with one owned by this process and mode 0600.

    Writing a fresh file and renaming it also works when the existing file was
    copied in by another user (e.g. a restored data folder), which chmod alone refuses.
    """
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    temporary.chmod(0o600)
    os.replace(temporary,path)

@dataclass
class Worker:
    process: subprocess.Popen
    port: int
    secret: str
    config_hash: str
    log: object
    last_used: float

class Workers:
    def __init__(self):
        self.workers={}
        self.locks={}
        self.client=httpx.AsyncClient(trust_env=False,timeout=httpx.Timeout(900,connect=5))

    async def get(self,space):
        space_id=space['id']
        lock=self.locks.setdefault(space_id,asyncio.Lock())
        async with lock:
            book=store.one('SELECT * FROM textbooks WHERE id=?',(space['textbook_id'],))
            ai={k:v for k,v in store.effective_config(space).items() if k in ENV_KEYS}
            config={'ai':ai,'metadata':json.loads(book['metadata']),'version':book['engine_version'],'subject':book['subject'],'stage':book['stage']}
            digest=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
            worker=self.workers.get(space_id)
            if worker and worker.process.poll() is None:
                if digest!=worker.config_hash:
                    result=await self.call(worker,'POST','/internal/config',json=config)
                    if result.status_code==200:
                        worker.config_hash=digest
                        write_private(store.DATA/'banks'/str(space_id)/'worker-config.json',config)
                worker.last_used=time.time()
                return worker
            if len([w for w in self.workers.values() if w.process.poll() is None])>=int(os.environ.get('MATHBANK_MAX_WORKERS','24')):
                await self.evict_idle()
            runtime=store.DATA/'banks'/str(space_id)
            runtime.mkdir(parents=True,exist_ok=True)
            write_private(runtime/'worker-config.json',config)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0))
                port=sock.getsockname()[1]
            secret=secrets.token_urlsafe(32)
            env=os.environ.copy()
            env.update(MATHBANK_WORKER_ROOT=str(runtime),MATHBANK_WORKER_SECRET=secret,
                       MATHBANK_WORKER_PORT=str(port),PYTHONUTF8='1')
            env.pop('PYTEST_CURRENT_TEST',None)
            log=(runtime/'worker.log').open('ab')
            process=subprocess.Popen([sys.executable,'-m','platform_app.worker'],cwd=store.ROOT,env=env,
                 stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            worker=Worker(process,port,secret,digest,log,time.time())
            self.workers[space_id]=worker
            for _ in range(100):
                if process.poll() is not None:
                    log.close()
                    raise HTTPException(503,'MathBank 空间启动失败，请检查该空间的 worker.log。')
                try:
                    response=await self.call(worker,'GET','/healthz')
                    if response.status_code==200:
                        return worker
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(.2)
            raise HTTPException(503,'MathBank 启动中，请稍后重试。')

    async def evict_idle(self):
        """Stop the least recently used worker that holds no request or import task."""
        for space_id,worker in sorted(self.workers.items(),key=lambda item:item[1].last_used):
            if worker.process.poll() is not None or self.locks[space_id].locked():
                continue
            try:
                idle=(await self.call(worker,'GET','/internal/idle')).json().get('idle')
            except (httpx.HTTPError,ValueError):
                continue
            if idle:
                await self.stop(self.workers.pop(space_id))
                return
        raise HTTPException(503,'同时打开的空间已达上限，且都有进行中的导入任务，请稍后重试。')

    async def stop(self,worker):
        if worker.process.poll() is None:
            # Only processes created by this platform instance are stopped.
            worker.process.terminate()
            try:
                await asyncio.to_thread(worker.process.wait,15)
            except subprocess.TimeoutExpired:
                worker.process.kill()
        worker.log.close()

    async def call(self,worker,method,path,**kwargs):
        headers=kwargs.pop('headers',{})
        headers.update({'X-Worker-Token':worker.secret,'X-Local-Token':worker.secret})
        return await self.client.request(method,f'http://127.0.0.1:{worker.port}{path}',headers=headers,**kwargs)

    async def close(self):
        for worker in self.workers.values():
            await self.stop(worker)
        await self.client.aclose()
