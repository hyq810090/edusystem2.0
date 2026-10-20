"""Run the original core regressions in a disposable source copy."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
tests=['test_asset_lifecycle.py','test_asset_security.py','test_question_duplicate_api.py',
       'test_pdf_layout.py','test_pdf_raster_guard.py','test_pdf_source_scopes.py',
       'test_pdf_source_verify.py','test_pdf_native_math.py','test_docx_source_scopes.py',
       'test_docx_source_verify.py','test_import_review.py','test_import_frontend.py',
       'test_source_metadata.py','test_content_locks.py','test_ai_json.py',
       'test_math_preview_regressions.py','test_image_layout.py','test_task_manager.py',
       'test_paper.py','test_paper_pagination_regressions.py','test_database_migrations.py','test_word_export.py','test_custom_paper_types.py','test_startup_knowledge_preservation.py']
tests=[name for name in tests if (root/'engine/tests'/name).exists()]
with tempfile.TemporaryDirectory(prefix='mathbank-core-') as temporary:
    source=Path(temporary)/'engine'
    shutil.copytree(root/'engine',source,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','*.db*','uploads','test_uploads','data_backup','.system_generated'))
    env=os.environ.copy();env['PYTHONUTF8']='1';env['PYTHONPATH']=str(source)
    result=subprocess.run([sys.executable,'-m','pytest','-q','--tb=short',*[str(Path('tests')/name) for name in tests]],cwd=source,env=env)
    sys.exit(result.returncode)
