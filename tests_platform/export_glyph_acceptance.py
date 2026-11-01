"""Real PDF/Word exports in disposable platform data; no model requests."""
from pathlib import Path
import io
import json
import sys
import tempfile
import zipfile

import pymupdf
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from platform_app import store  # noqa: E402
from platform_app.app import app  # noqa: E402


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mathbank-export-glyphs-') as temporary:
        store.DATA = Path(temporary)
        with TestClient(app) as client:
            password = (store.DATA / 'initial-admin.txt').read_text().splitlines()[1].split('：', 1)[1]
            assert client.post('/platform/api/login', json={'username': 'admin', 'password': password}).is_success
            client.headers['X-Local-Token'] = client.get('/platform/api/me').json()['csrf']
            space = client.get('/platform/api/spaces').json()[0]['id']
            base = f'/bank/{space}/api'
            original = r'在△ABC中，∠A=30°，已知 $a=2$，求 $b$。'
            saved = client.post(base + '/questions', data={
                'content': original, 'question_type': 'detailed_answer', 'difficulty': '3',
                'answer_markdown': r'由 $b=2a$ 得 $b=4$，∠B=60°。',
            })
            assert saved.is_success, saved.text
            question = saved.json()['question']
            payload = {'title': '几何字形导出验收', 'paper_type': 'exam',
                       'show_secret': False, 'show_notice': False,
                       'questions': [{'id': question['id'], 'score': 12}]}
            report = {}
            for answers in (False, True):
                payload['include_answers'] = answers
                label = 'teacher' if answers else 'student'
                for kind in ('pdf', 'word', 'bundle'):
                    response = client.post(base + '/paper/export/' + kind, json=payload)
                    assert response.is_success, (label, kind, response.text[:1000])
                    suffix = 'pdf' if kind == 'pdf' else 'zip'
                    (output / f'{label}-{kind}.{suffix}').write_bytes(response.content)
                    report[f'{label}-{kind}'] = len(response.content)
                    if kind == 'pdf':
                        with pymupdf.open(stream=response.content, filetype='pdf') as document:
                            assert len(document) >= 1
                            text = ''.join(page.get_text() for page in document)
                            assert 'ABC' in text and '\ufffd' not in text
                            document[0].get_pixmap(matrix=pymupdf.Matrix(1.2, 1.2)).save(output / f'{label}.png')
                    elif kind == 'word':
                        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                            documents = [n for n in archive.namelist() if n.endswith('.docx')]
                            assert documents
                            for name in documents:
                                with zipfile.ZipFile(io.BytesIO(archive.read(name))) as docx:
                                    assert b'oMath' in docx.read('word/document.xml')
            fetched = client.get(base + f'/questions/{question["id"]}').json()
            assert fetched['content'] == original
            bad = client.post(base + '/questions', data={
                'content': '此题有字体不支持的字符 🦄。', 'question_type': 'detailed_answer', 'difficulty': '3',
            }).json()['question']
            payload['questions'] = [{'id': bad['id'], 'score': 5}]
            blocked = client.post(base + '/paper/export/pdf', json=payload)
            assert blocked.status_code == 400, blocked.text[:1000]
            assert blocked.json()['diagnostic']['kind'] == 'missing_glyph', blocked.json()
            report['missing-glyph-blocked'] = True
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    run(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'test-results/export-glyphs')
