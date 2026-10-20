"""Subject-specific rules, source preservation, and safe template migration."""
from pathlib import Path
import importlib
import json
import re
import sys
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'engine'))
sys.path.insert(0,str(ROOT))
from mathbank import subject_profile as profile
from platform_app.physics_metadata import upgrade_defaults, migrate

@pytest.fixture
def physics():
    metadata=json.loads((ROOT/'resources/textbooks/高中物理人教版.metadata.json').read_text(encoding='utf-8'))
    profile.configure('物理','高中',metadata)
    import mathbank.prompts as prompts
    import mathbank.pdf_source_verify as verify
    importlib.reload(prompts);importlib.reload(verify)
    yield metadata,prompts,verify
    profile.reset();importlib.reload(prompts);importlib.reload(verify)

def test_physics_rules_keep_current_protocols_and_verbatim_user_data(physics):
    metadata,prompts,_=physics
    source='原文中写着高中数学；$U=2\\,\\mathrm{mV}$，保持原字。'
    system,user=prompts.build_ai_solve_prompts('experiment',source)
    assert source in user and '实验题' in user
    assert '高中物理' in system and '允许使用普通高中课程中的导数' not in system
    assert all(s in system for s in ('研究对象','量纲','有效数字','误差来源'))
    parse=prompts.build_pdf_parse_system_prompt(metadata['curriculum'],False)
    assert all(s in parse for s in ('experiment','short_answer','normal','MBM_','category_knowledge','评分','禁止','[EXTRACTED_ORIGINAL]'))
    assert '单位大小写' in prompts.COMMON_OCR_PROMPT
    assert '电路连接' in prompts.SINGLE_OCR_FIGURE_REVIEW_INSTRUCTIONS
    assert source in prompts.build_tikz_draw_prompt(source)
    for result in (prompts.build_tikz_draw_prompt(source),prompts.build_tikz_correction_prompt('code')):
        assert all(s in result for s in ('电路图','光路图','受力方向','导线连接'))
    for result in (prompts.build_pdf_source_verification_prompt([]),prompts.build_docx_source_verification_prompt([])):
        assert all(s in result for s in ('电路连接','有效数字','checks','same_question'))

@pytest.mark.parametrize('before,after',[
    ('电阻串联，电流为2 A。','电阻并联，电流为2 A。'),
    ('电池正极接A。','电池负极接A。'),
    ('物体匀速运动。','物体加速运动。'),
    ('电压为2 V。','电压为2 mV。'),
    ('磁场方向向左。','磁场方向向右。'),
    ('电流为2 mA。','电流为2 MA。'),
    ('电压为2.00 V。','电压为2.0 V。'),
])
def test_physics_condition_changes_remain_visible(physics,before,after):
    assert physics[2]._local_block_reason('1. '+before,'1. '+after)=='condition_difference'
    assert physics[2]._local_block_reason('1. '+before,'1. '+before) is None

def test_physics_prefix_cleanup_requires_matching_type(physics):
    from mathbank.content_locks import _question_metadata_layout
    for title,code in [('实验题','experiment'),('计算题','detailed_answer'),('简答题','short_answer')]:
        source=f'1. （{title}）测量电流。'
        assert title not in _question_metadata_layout(source,{'content':'1. 测量电流。','question_type':code})
        assert title in _question_metadata_layout(source,{'content':'1. 测量电流。','question_type':'single_choice'})

def test_missing_inline_physics_formulas_detected_without_regressing_retention(physics):
    from mathbank.pdf_inspector_helper import _has_multiple_inline_formula_images
    items=[]
    for i in range(3):
        items.extend([SimpleNamespace(item_type='text',x=30,y=80+i*40,width=50,height=10,text='已知加速度'),
            SimpleNamespace(item_type='image',x=84,y=79+i*40,width=28,height=12,text=''),
            SimpleNamespace(item_type='text',x=116,y=80+i*40,width=70,height=10,text='，求位移。')])
    assert _has_multiple_inline_formula_images(items)

def test_stage_and_configured_enums_are_respected(physics):
    metadata,prompts,_=physics
    metadata['question_types']=[{'value':'lab_custom','label':'探究实验'}]
    metadata['difficulties']=[{'value':'basic','label':'基础'}]
    profile.configure('物理','初中',metadata)
    system,_=prompts.build_ai_solve_prompts('lab_custom','实验题')
    assert '初中物理' in system and '初中题不得套用高中' in system
    parse=prompts.build_pdf_parse_system_prompt({},False)
    assert 'lab_custom' in parse and 'basic' in parse
    assert 'single_choice / multi_choice' not in parse and 'easy_error / challenge' not in parse
    assert profile.infer_type('选择一道探究实验题',metadata)=='lab_custom'
    profile.configure('数学','初中',metadata)
    system,_=prompts.build_ai_solve_prompts('lab_custom','题目')
    assert '不使用导数' in system and not profile.physics()

def test_default_math_and_custom_configuration_remain_intact():
    import mathbank.prompts as prompts
    profile.reset();importlib.reload(prompts)
    system,_=prompts.build_ai_solve_prompts('detailed_answer','题目')
    assert '高中数学' in system and '允许使用普通高中课程中的导数' in system
    custom={'curriculum':{},'question_types':[{'value':'lab','label':'校本实验题'}],'difficulties':[]}
    assert upgrade_defaults(custom)==custom

def test_physics_migration_backup_and_idempotence(tmp_path,monkeypatch):
    from platform_app import store
    monkeypatch.setattr(store,'DATA',tmp_path);store.initialize()
    original=json.loads(store.one('SELECT metadata FROM textbooks WHERE id=1')['metadata'])
    with store.db() as c:
        bid=c.execute('INSERT INTO textbooks(stage,subject,name,metadata) VALUES (?,?,?,?)',('高中','物理','既有物理',json.dumps(original))).lastrowid
    migrate(store)
    result=json.loads(store.one('SELECT metadata FROM textbooks WHERE id=?',(bid,))['metadata'])
    assert result['curriculum']==original['curriculum'] and result['difficulties']==original['difficulties']
    assert {v['value'] for v in result['question_types']}=={v['value'] for v in original['question_types']}|{'experiment','short_answer'}
    assert len(list((tmp_path/'migration_backups').glob('before-physics-types-*.db')))==1
    migrate(store)
    assert len(list((tmp_path/'migration_backups').glob('before-physics-types-*.db')))==1
    assert json.loads(store.one('SELECT metadata FROM textbooks WHERE id=1')['metadata'])==original
