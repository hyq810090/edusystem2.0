"""Integration boundaries for the selected upstream capacity/security fixes."""
import io
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from mathbank.asset_security import InvalidImageError, normalize_raster_image
from mathbank import paper_helper


def test_malformed_exif_is_a_rejected_upload(monkeypatch):
    image = io.BytesIO()
    Image.new('RGB', (8, 8), 'white').save(image, format='JPEG')

    def damaged_exif(_source):
        raise SyntaxError('corrupt EXIF')

    monkeypatch.setattr('mathbank.asset_security.ImageOps.exif_transpose', damaged_exif)
    with pytest.raises(InvalidImageError, match='有效且安全'):
        normalize_raster_image(image.getvalue())


@pytest.mark.parametrize('bad_pass', [1, 2])
def test_successful_compiler_exit_with_missing_glyph_never_returns_or_caches_pdf(monkeypatch, bad_pass):
    calls = []

    def compiler(_cmd, *, cwd, **kwargs):
        calls.append(cwd)
        Path(cwd, 'paper.pdf').write_bytes(b'incomplete pdf')
        # Deliberately place the glyph warning in the log, outside stdout.
        log = ('Missing character: There is no 🦄 (U+1F984) in font XITS!\n'
               if len(calls) % 2 == bad_pass % 2 else 'Output written on paper.pdf\n')
        Path(cwd, 'paper.log').write_text(log, encoding='utf-8')
        return SimpleNamespace(returncode=0, stdout='compiler succeeded', stderr='')

    monkeypatch.setattr(paper_helper.subprocess, 'run', compiler)
    paper_helper.clear_pdf_cache()
    source = rf'\documentclass{{article}}\begin{{document}}glyph test {bad_pass}\end{{document}}'
    data, error = paper_helper.compile_tex_to_pdf(source)
    assert data is None and 'U+1F984' in error
    assert len(calls) == bad_pass
    assert not paper_helper._PDF_CACHE


def test_explicit_question_scopes_cover_answers_without_mutating_source():
    question = {'id': 42, 'content': '求 $x$。', 'answer_markdown': '答案 $x=1$。',
                'question_type': 'detailed_answer', 'difficulty': '3'}
    original = dict(question)
    tex = paper_helper.build_latex_document('范围核验', '', '', [{'question': question}], include_answers=True)
    assert '% MathBank-Question-Number: 1' in tex
    assert '% MathBank-Field: answer_markdown' in tex
    assert tex.index('% MathBank-Question-End') > tex.index(r'\end{solution}')
    assert question == original
