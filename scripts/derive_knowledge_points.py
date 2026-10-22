"""Derive the platform's 高中数学 knowledge-point catalogue from MathBank 3.0's.

MathBank 3.0 ships mathbank/resources/knowledge_points.json (3 MB with its
source-site crawl mappings). The platform keeps only what tagging needs: each
point's stable code, name, aliases and the textbook sections of every edition
where it is taught. Codes are unchanged, so questions tagged here stay valid
against the upstream catalogue.

    python scripts/derive_knowledge_points.py <MathBank 3.0>/mathbank/resources/knowledge_points.json
"""
from pathlib import Path
import hashlib
import json
import sys

TARGET = Path(__file__).resolve().parents[1] / 'platform_app' / 'resources' / 'knowledge_points.json'
UPSTREAM_VERSION = '3.0.1'


def derive(source: Path) -> dict:
    data = source.read_bytes()
    catalogue = json.loads(data)
    if catalogue.get('schema_version') != 'mathbank-knowledge-points-v1':
        raise SystemExit('不是 MathBank 3.0 的知识点目录（schema_version 不符）')
    points = []
    for point in catalogue['points']:
        names = [point['name'], point.get('original_name', ''), *point.get('aliases', [])]
        aliases = [n for n in dict.fromkeys(names) if n and n != point['name']]
        paths = {}
        for path in point['curriculum_paths']:
            paths.setdefault(path['version_code'], []).append([path['compulsory'], path['chapter'], path['knowledge']])
        points.append({'code': point['code'], 'name': point['name'], 'aliases': aliases, 'paths': paths})
    return {
        'schema': 'mathbank-platform-knowledge-points-v1',
        'derived_from': {'project': 'MathBank', 'version': UPSTREAM_VERSION,
                         'file': 'mathbank/resources/knowledge_points.json', 'sha256': hashlib.sha256(data).hexdigest()},
        'source': catalogue['source'],
        'provenance': catalogue['provenance'],
        'points': points,
    }


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    result = derive(Path(sys.argv[1]))
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_bytes(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    print(f'{len(result["points"])} 个知识点 → {TARGET}')
