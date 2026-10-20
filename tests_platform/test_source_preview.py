"""Word original-paper previews for 导入中心 (LibreOffice is replaced by a stub here)."""
from test_platform import create_user, login_user, new_space, system  # noqa: F401
from platform_app import previews

DOCX = ('试卷.docx', b'PK\x03\x04 word paper', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')


def test_word_preview_is_converted_once_and_needs_write_access(system, monkeypatch):  # noqa: F811
    space = new_space(system, '原卷预览', 'team')['id']
    path = f'/platform/api/spaces/{space}/source-preview'
    calls = []
    monkeypatch.setattr(previews, 'renderer', lambda: 'soffice')
    monkeypatch.setattr(previews, 'to_pdf', lambda program, data: calls.append(data) or b'%PDF-1.7 preview')
    first = system.post(path, files={'file': DOCX})
    assert first.status_code == 200 and first.content == b'%PDF-1.7 preview'
    assert first.headers['content-type'] == 'application/pdf'
    # The same paper is served from the cache without converting again.
    assert system.post(path, files={'file': DOCX}).content == b'%PDF-1.7 preview' and len(calls) == 1
    assert system.post(path, files={'file': ('试卷.pdf', b'%PDF', 'application/pdf')}).status_code == 400
    assert system.post(path, files={'file': ('坏文件.docx', b'not a zip', 'application/octet-stream')}).status_code == 400
    create_user(system, 'preview_viewer')
    assert login_user('preview_viewer', system).post(path, files={'file': DOCX}).status_code == 403


def test_word_preview_reports_failures(system, monkeypatch):  # noqa: F811
    space = new_space(system, '原卷预览失败')['id']
    path = f'/platform/api/spaces/{space}/source-preview'
    monkeypatch.setattr(previews, 'renderer', lambda: None)
    assert system.post(path, files={'file': DOCX}).status_code == 503
    monkeypatch.setattr(previews, 'renderer', lambda: 'soffice')
    monkeypatch.setattr(previews, 'to_pdf', lambda program, data: None)
    other = ('另一份.docx', b'PK\x03\x04 another paper', DOCX[2])
    assert system.post(path, files={'file': other}).status_code == 422
