"""Run one unchanged MathBank engine with private persistent paths.

This is the only adapter that imports the upstream application. All imports
occur after path configuration, so background tasks use the same private root.
"""
from pathlib import Path
import json
import os
import secrets
import shutil
import sys

from platform_app.store import ROOT

ENV_KEYS = {
 'deepseek_key':'DEEPSEEK_API_KEY', 'siliconflow_key':'SILICONFLOW_API_KEY',
 'ali_bailian_key':'ALI_BAILIAN_API_KEY', 'zhongzhan_gpt_key':'ZHONGZHAN_GPT_API_KEY',
 'zhongzhan_claude_key':'ZHONGZHAN_CLAUDE_API_KEY',
 'zhongzhan_gpt_base_url':'ZHONGZHAN_GPT_BASE_URL','zhongzhan_claude_base_url':'ZHONGZHAN_CLAUDE_BASE_URL',
 'zhongzhan_gpt_ocr_model':'ZHONGZHAN_GPT_OCR_MODEL','zhongzhan_claude_ocr_model':'ZHONGZHAN_CLAUDE_OCR_MODEL',
 'prefer_engine':'OCR_PREFER_ENGINE','deepseek_model':'DEEPSEEK_OCR_MODEL',
 'siliconflow_model':'SILICONFLOW_OCR_MODEL','ali_bailian_model':'ALI_BAILIAN_OCR_MODEL',
 'prefer_solve_model':'PREFER_SOLVE_MODEL','prefer_parse_model':'PREFER_PARSE_MODEL',
 'prefer_classify_model':'PREFER_CLASSIFY_MODEL','prefer_draw_model':'PREFER_DRAW_MODEL',
}

def configure_environment(config):
    for key, env in ENV_KEYS.items():
        os.environ.pop(env, None)
        if config.get(key):
            os.environ[env]=str(config[key])
    for legacy in ('ZHONGZHAN_API_KEY','ZHONGZHAN_BASE_URL','ZHONGZHAN_OCR_MODEL',
                   'DEEPSEEK_PARSE_MODEL','DEEPSEEK_CLASSIFY_MODEL'):
        os.environ.pop(legacy,None)

def create_app():
    runtime=Path(os.environ['MATHBANK_WORKER_ROOT']).resolve()
    config=json.loads((runtime/'worker-config.json').read_text(encoding='utf-8'))
    os.environ['MATHBANK_UNIFIED_DIFFICULTY']='1'
    from engine.mathbank.difficulty import fix_metadata
    config['metadata']=fix_metadata(config['metadata'])
    configure_environment(config['ai'])
    # Ship the same exam-zh package as the source project. Distribution TeX
    # packages can be older and reject the original template's settings.
    os.environ['TEXINPUTS']=str(ROOT/'engine/templates/exam-zh')+'//'+os.pathsep+os.environ.get('TEXINPUTS','')
    if not os.environ.get('MATHBANK_PANDOC_PATH') and not shutil.which('pandoc'):
        # Use an already installed private Python Pandoc bundle when available.
        # This changes no PATH or host-wide application configuration.
        try:
            import pypandoc
            bundled=Path(pypandoc.__file__).parent/'files'/('pandoc.exe' if os.name=='nt' else 'pandoc')
            if bundled.is_file():os.environ['MATHBANK_PANDOC_PATH']=str(bundled)
        except ImportError:
            pass
    sys.path.insert(0,str(ROOT/'engine'))
    from mathbank import subject_profile, difficulty
    difficulty.configure_labels(config['metadata'].get('legacy_difficulty_labels', {}))
    subject_profile.configure(config.get("subject", "数学"), config.get("stage", "高中"), config["metadata"])
    import mathbank.paths as paths
    paths.PROJECT_ROOT=runtime
    paths.UPLOADS_DIR=runtime/'static/uploads'
    paths.TEST_UPLOADS_DIR=runtime/'static/test_uploads'
    paths.DATA_BACKUP_DIR=runtime/'data_backup'
    paths.FULL_BACKUP_DIR=paths.DATA_BACKUP_DIR/'snapshots'
    paths.PRE_RESTORE_BACKUP_DIR=paths.DATA_BACKUP_DIR/'pre_restore'
    paths.SCHEMA_SNAPSHOT_DIR=paths.DATA_BACKUP_DIR/'schema_snapshots'
    paths.SYSTEM_GENERATED_DIR=runtime/'.system_generated'
    paths.RETAINED_UPLOADS_DIR=paths.SYSTEM_GENERATED_DIR/'retained_uploads'
    paths.DATABASE_FILE=paths.DATABASE_PATH=runtime/'math_question_bank.db'
    paths.ENV_FILE=runtime/'.env'
    # sqlite_url's default was bound when paths.py was imported.
    paths.sqlite_url=lambda path=None: 'sqlite:///'+str(path or paths.DATABASE_FILE).replace('\\','/')
    for directory in (paths.UPLOADS_DIR,paths.DATA_BACKUP_DIR,paths.SYSTEM_GENERATED_DIR):
        directory.mkdir(parents=True,exist_ok=True)
    metadata_path=paths.DATA_BACKUP_DIR/'custom_metadata.json'
    metadata_path.write_text(json.dumps(config['metadata'],ensure_ascii=False),encoding='utf-8')
    # The platform owns the fixed textbook identity; upstream storage codes stay
    # compatible while the platform supports any admin-defined subject/catalog.
    import mathbank.sub_knowledge as sub
    sub.detect_curriculum_version=lambda metadata,strict=False: config['version']
    import main as engine
    if os.name!='nt':
        # The Windows original uses proprietary Times/Arial/Courier faces.
        # On Linux, substitute installed open fonts only during PDF compilation;
        # editable question text and downloaded TeX source are unchanged.
        original_compile=engine.compile_tex_to_pdf
        font_hook=r'''\ExplSyntaxOn
\AddToHook{package/exam-zh-font/after}{
  \cs_set_protected:Npn \examzh_font_set_font_times: {
    \setmainfont{XITS}
    \setsansfont{Liberation~Sans}[Scale=MatchLowercase]
    \setmonofont{Liberation~Mono}[Scale=MatchLowercase]
  }
}
\ExplSyntaxOff
'''
        def compile_with_platform_fonts(tex_content,image_paths=None):
            return original_compile(' '.join(font_hook.splitlines())+tex_content,image_paths)
        engine.compile_tex_to_pdf=compile_with_platform_fonts
    engine.LOCAL_TOKEN=os.environ['MATHBANK_WORKER_SECRET']
    from platform_app import handout_export
    handout_export.register(engine)
    from starlette.responses import JSONResponse
    active_requests=0
    @engine.app.middleware('http')
    async def internal_boundary(request,call_next):
        nonlocal active_requests
        token=request.headers.get('X-Worker-Token','')
        if not secrets.compare_digest(token,engine.LOCAL_TOKEN):
            return JSONResponse({'detail':'Private worker'},status_code=403)
        if request.url.path=='/internal/idle':
            # Any task record (even finished) may hold an unreviewed import.
            with engine.DOCUMENT_TASKS._lock:
                holding=bool(engine.DOCUMENT_TASKS._records)
            return JSONResponse({'idle':not active_requests and not holding})
        if request.url.path=='/internal/config' and request.method=='POST':
            updated=await request.json()
            with engine.DOCUMENT_TASKS._lock:
                busy=any(r.state.get('status') not in {'completed','error','cancelled'} for r in engine.DOCUMENT_TASKS._records.values())
            if busy or active_requests:
                return JSONResponse({'detail':'正在运行任务，稍后应用配置'},status_code=409)
            updated['metadata']=fix_metadata(updated['metadata'])
            difficulty.configure_labels(updated['metadata'].get('legacy_difficulty_labels', {}))
            configure_environment(updated['ai'])
            config.update(updated)
            subject_profile.configure(config.get('subject', '数学'), config.get('stage', '高中'), updated['metadata'])
            engine.METADATA_CACHE=updated['metadata']
            metadata_path.write_text(json.dumps(updated['metadata'],ensure_ascii=False),encoding='utf-8')
            return JSONResponse({'status':'success'})
        active_requests+=1
        try:
            return await call_next(request)
        finally:
            active_requests-=1
    return engine.app

if __name__=='__main__':
    import uvicorn
    uvicorn.run(create_app(),host='127.0.0.1',port=int(os.environ['MATHBANK_WORKER_PORT']),log_level='warning')
