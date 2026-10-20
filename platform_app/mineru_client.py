"""Client for the MinerU precise parsing API (https://mineru.net/apiManage).

A local file is parsed in four steps: request a presigned upload URL, PUT the
file there (parsing then starts by itself), poll the batch until the file is
done, and download the result zip (full.md, images/, *_content_list.json,
layout.json). Used by the MinerU import (platform_app/imports.py) and the
standalone trial tool (tools/mineru_trial).
"""
from dataclasses import asdict, dataclass
from pathlib import Path
import io
import time
import zipfile

import httpx

API = 'https://mineru.net/api/v4'
ERRORS = {
    '401': 'API Key 无效或已过期，请检查是否复制完整',
    'A0202': 'API Key 无效，请检查是否复制完整',
    'A0211': 'API Key 已过期，请在 MinerU 网站重新生成',
    '-60002': '文件格式不支持',
    '-60005': '文件超过 200 MB',
    '-60006': '页数超过单文件上限',
    '-60018': '今日解析额度已用完',
}


class MinerUError(RuntimeError):
    pass


@dataclass
class Options:
    model_version: str = 'vlm'      # 'vlm' (recommended) or 'pipeline'
    is_ocr: bool = False            # force OCR, for scanned pages
    enable_formula: bool = True
    enable_table: bool = True
    language: str = 'ch'
    page_ranges: str = ''           # e.g. "1-3,5"; empty means all pages


class MinerUClient:
    def __init__(self, token, *, base=API, transport=None, timeout=120):
        self.api = httpx.Client(base_url=base, headers={'Authorization': f'Bearer {token.strip()}'},
                                timeout=timeout, transport=transport)
        # Presigned upload and result URLs live on storage hosts: no API key there.
        self.storage = httpx.Client(timeout=timeout, transport=transport, follow_redirects=True)

    def close(self):
        self.api.close()
        self.storage.close()

    def _call(self, method, path, **kwargs):
        response = self.api.request(method, path, **kwargs)
        try:
            body = response.json()
        except ValueError:
            raise MinerUError(f'MinerU 返回 HTTP {response.status_code}：{response.text[:200]}')
        if response.status_code != 200 or body.get('code') != 0:
            code = str(body.get('code', response.status_code))
            raise MinerUError(f"{ERRORS.get(code, body.get('msg') or '请求失败')}（{code}）")
        return body['data']

    def submit(self, path: Path, options: Options) -> str:
        """Upload one local file and return the batch id that tracks it."""
        settings = asdict(options)
        file = {'name': path.name, 'is_ocr': settings.pop('is_ocr')}
        if settings.pop('page_ranges'):
            file['page_ranges'] = options.page_ranges
        data = self._call('POST', '/file-urls/batch', json={'files': [file], **settings})
        # The storage signature excludes Content-Type, so none may be sent.
        upload = self.storage.put(data['file_urls'][0], content=path.read_bytes())
        if upload.status_code not in (200, 201):
            raise MinerUError(f'文件上传失败：HTTP {upload.status_code}')
        return data['batch_id']

    def status(self, batch_id: str) -> dict:
        results = self._call('GET', f'/extract-results/batch/{batch_id}').get('extract_result') or []
        return results[0] if results else {'state': 'waiting-file'}

    def wait(self, batch_id: str, *, on_progress=None, interval=3.0, timeout=1800.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            result = self.status(batch_id)
            if on_progress:
                on_progress(result)
            if result['state'] == 'done':
                return result
            if result['state'] == 'failed':
                raise MinerUError(result.get('err_msg') or 'MinerU 解析失败')
            if time.monotonic() > deadline:
                raise MinerUError('等待解析结果超时')
            time.sleep(interval)

    def download(self, zip_url: str, target: Path, keep: Path | None = None) -> Path:
        """Extract the result zip into target, refusing paths that leave it; optionally keep the zip."""
        response = self.storage.get(zip_url)
        if response.status_code != 200:
            raise MinerUError(f'结果下载失败：HTTP {response.status_code}')
        if keep is not None:
            keep.parent.mkdir(parents=True, exist_ok=True)
            partial = keep.with_suffix('.part')
            partial.write_bytes(response.content)
            partial.replace(keep)
        target = target.resolve()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            for member in archive.infolist():
                destination = (target / member.filename).resolve()
                if not destination.is_relative_to(target):
                    raise MinerUError(f'结果包含非法路径：{member.filename}')
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read(member))
        return target
