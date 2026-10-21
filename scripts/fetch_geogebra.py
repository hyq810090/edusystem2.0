"""Fetch the self-hosted GeoGebra Math Apps bundle used by the drawing workspace.

The files are not kept in Git (about 115 MB unpacked). Run this once on a new
machine before `docker compose up -d --build`; it downloads the pinned version
from GeoGebra, checks its SHA-256 and unpacks it to
platform_app/vendor/geogebra/<version>/. GeoGebra is free for non-commercial
use only; see README.txt in that folder and https://www.geogebra.org/license.

    python scripts/fetch_geogebra.py            # download and unpack
    python scripts/fetch_geogebra.py bundle.zip # unpack an already downloaded copy
"""
from pathlib import Path
import hashlib
import sys
import tempfile
import urllib.request
import zipfile

VERSION = '5.4.933'
URL = 'https://download.geogebra.org/installers/5.4/geogebra-math-apps-bundle-5-4-933-1.zip'
SHA256 = '409095947e065bce5bde20b8a6d56a60e2b3cb3cce6947e817a802513515ec0c'
TARGET = Path(__file__).resolve().parents[1] / 'platform_app' / 'vendor' / 'geogebra' / VERSION


def unpack(bundle: Path) -> int:
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    if digest != SHA256:
        raise SystemExit(f'SHA-256 不符：{digest}，应为 {SHA256}')
    target = TARGET.resolve()
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(bundle) as archive:
        for info in archive.infolist():
            name = info.filename
            if name == 'README.txt':
                relative = 'README.txt'
            elif name.startswith('GeoGebra/'):
                relative = name[len('GeoGebra/'):]
            else:
                continue
            path = (target / relative).resolve()
            if path != target and target not in path.parents:
                raise SystemExit(f'压缩包含有不安全的路径：{name}')
            if name.endswith('/'):
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(info))
            count += 1
    return count


def main():
    if len(sys.argv) > 1:
        print('unpacked', unpack(Path(sys.argv[1])), 'files to', TARGET)
        return
    with tempfile.TemporaryDirectory() as temporary:
        bundle = Path(temporary) / 'geogebra-math-apps-bundle.zip'
        print('downloading', URL)
        urllib.request.urlretrieve(URL, bundle)
        print('unpacked', unpack(bundle), 'files to', TARGET)


if __name__ == '__main__':
    main()
