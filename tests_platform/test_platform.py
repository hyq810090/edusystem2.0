from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import io
import json
import sqlite3
import sys
import zipfile

import httpx
import pytest
from PIL import Image
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from platform_app import migrations, store
from platform_app.app import app

@pytest.fixture(scope='module')
def system(tmp_path_factory):
    store.DATA=tmp_path_factory.mktemp('mathbank-platform')
    with TestClient(app) as client:
        password=(store.DATA/'initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：',1)[1]
        assert client.post('/platform/api/login',json={'username':'admin','password':password}).status_code==200
        me=client.get('/platform/api/me').json()
        client.headers['X-Local-Token']=me['csrf']
        yield client

def create_user(client,name,team=1,role='member'):
    response=client.post('/platform/api/admin/users',json={'username':name,'display_name':name,'password':'Platform-Test-12345','team_id':team,'role':role})
    assert response.status_code==200,response.text
    return response.json()['id']

def login_user(name,system,password='Platform-Test-12345'):
    client=TestClient(app)
    client.portal=system.portal
    assert client.post('/platform/api/login',json={'username':name,'password':password}).status_code==200
    client.headers['X-Local-Token']=client.get('/platform/api/me').json()['csrf']
    return client

def new_space(client,name,kind='personal',book=1):
    response=client.post('/platform/api/spaces',json={'name':name,'kind':kind,'textbook_id':book})
    assert response.status_code==200,response.text
    return next(s for s in client.get('/platform/api/spaces').json() if s['id']==response.json()['id'])

def add_book(client,subject,stage,name='测试教材'):
    metadata=client.get('/platform/api/admin/textbooks/1').json()['metadata']
    r=client.post('/platform/api/admin/textbooks',json={'subject':subject,'stage':stage,'name':name,'metadata':metadata})
    assert r.status_code==200,r.text
    return r.json()['id']

def question(client,space,content='求函数 $y=x^2$ 的导数。',**extra):
    data={'content':content,'question_type':'detailed_answer','difficulty':'normal',**extra}
    return client.post(f'/bank/{space}/api/questions',data=data)

def test_physics_worker_types_export_and_math_isolation(system):
    metadata=json.loads((store.ROOT/'resources/textbooks/高中物理人教版.metadata.json').read_text(encoding='utf-8'))
    r=system.post('/platform/api/admin/textbooks',json={'stage':'高中','subject':'物理','name':'物理集成验收','metadata':metadata})
    assert r.status_code==200,r.text
    space=new_space(system,'物理集成验收',book=r.json()['id'])['id']
    config=system.get(f'/bank/{space}/api/config/metadata').json()
    assert any(v['value']=='experiment' for v in config['question_types'])
    content=r'实验测得电流为 $I=2.00\,\mathrm{mA}$，说明串联电路的测量步骤。'
    q=question(system,space,content=content,question_type='experiment',category_compulsory='必修 第一册',category_chapter='第一章 运动的描述')
    assert q.status_code==200,q.text
    q=q.json()['question'];assert q['question_type']=='experiment' and q['content']==content
    payload={'title':'物理实验验收','paper_type':'exam','questions':[{'id':q['id'],'score':12}]}
    saved=system.post(f'/bank/{space}/api/paper/save',json=payload);assert saved.status_code==200,saved.text
    exported=system.post(f'/bank/{space}/api/paper/export/tex',json=payload)
    assert exported.status_code==200,exported.text[:400]
    with zipfile.ZipFile(io.BytesIO(exported.content)) as z:
        tex='\n'.join(z.read(n).decode('utf-8') for n in z.namelist() if n.endswith('.tex'))
    assert r'\subject{物理}' in tex and '实验题' in tex and 'mA' in tex
    assert r'\subject{数学}' not in tex
    math_config=system.get('/bank/1/api/config/metadata').json()
    assert not any(v['value']=='experiment' for v in math_config['question_types'])
    # Restart only this isolated test worker and ensure startup does not rename physics volumes.
    worker=app.state.workers.workers[space];worker.process.terminate();worker.process.wait(timeout=15)
    fetched=system.get(f'/bank/{space}/api/questions/{q["id"]}').json()
    assert fetched['category_compulsory']=='必修 第一册' and fetched['content']==content

def test_upstream_changes_are_declared(system):
    manifest=json.loads((store.ROOT/'upstream-manifest.json').read_text())
    adaptations=json.loads((store.ROOT/'platform-adaptations.json').read_text())
    for name,digest in manifest.items():
        digest=adaptations.get(name,digest)
        assert hashlib.sha256((store.ROOT/'engine'/name).read_bytes()).hexdigest()==digest,name
    assert not (store.ROOT/'engine/.env').exists()
    assert not (store.ROOT/'engine/math_question_bank.db').exists()

def test_collapsed_sidebar_is_served_without_a_flash(system):
    assert 'data-nav="collapsed"' not in system.get('/workbench/1').text
    system.cookies.set('mb_nav','collapsed')
    try:
        assert '<html lang="zh-CN" data-nav="collapsed">' in system.get('/workbench/1').text
    finally:
        system.cookies.delete('mb_nav')

def test_auth_csrf_and_no_self_registration(system):
    anonymous=TestClient(app)
    assert anonymous.get('/platform/api/spaces').status_code==401
    assert anonymous.get('/bank/1/api/questions').status_code==401
    assert anonymous.post('/platform/api/register',json={}).status_code==401
    assert system.post('/platform/api/spaces',json={},headers={'X-Local-Token':'wrong'}).status_code==403
    assert system.post('/platform/api/spaces',json={},headers={'Origin':'https://hostile.example'}).status_code==403

def test_original_workbench_and_internal_worker_protected(system):
    result=system.get('/workbench/1')
    assert result.status_code==200
    assert '/static/js/api.js' in result.text and 'importWorkspaceSection' in result.text
    assert '<title>我的备课空间 · 高中数学 · MathBank</title>' in result.text
    worker=app.state.workers.workers[1]
    assert worker.secret not in result.text
    with httpx.Client(trust_env=False) as c:
        assert c.get(f'http://127.0.0.1:{worker.port}/api/questions').status_code==403

def test_home_opens_last_used_space(system):
    second=new_space(system,'最近使用')['id']
    assert system.get(f'/workbench/{second}').status_code==200
    assert system.get('/',follow_redirects=False).headers['location']==f'/workbench/{second}'
    assert system.get('/workbench/1').status_code==200
    assert system.get('/',follow_redirects=False).headers['location']=='/workbench/1'

def test_independent_spaces_questions_assets_and_duplicates(system):
    left=new_space(system,'独立一')['id']
    right=new_space(system,'独立二')['id']
    image=io.BytesIO();Image.new('RGB',(100,60),'white').save(image,format='PNG')
    upload=system.post(f'/bank/{left}/api/upload',files={'file':('diagram.png',image.getvalue(),'image/png')})
    assert upload.status_code==200,upload.text
    asset=upload.json()['file_path']
    assert asset.startswith(f'/static/uploads/{left}/')
    created=question(system,left,'已知三角形，求其面积。',image_paths=json.dumps([asset]))
    assert created.status_code==200,created.text
    qid=created.json()['question']['id']
    assert system.get(f'/bank/{left}/api/questions/{qid}').json()['image_paths']==[asset]
    assert system.get(asset).status_code==200
    assert system.get(asset.replace(f'/uploads/{left}/',f'/uploads/{right}/')).status_code==404
    assert system.get(f'/bank/{right}/api/questions?page=1').json()['total']==0
    duplicate=system.post(f'/bank/{left}/api/questions/check-duplicates',json={'items':[{'content':'已知三角形，求其面积。','question_type':'detailed_answer','image_paths':[asset]}]})
    assert duplicate.status_code==200,duplicate.text
    snapshot=duplicate.json()['items'][0]['snapshot_hash']
    assert question(system,left,'已知三角形，求其面积。',image_paths=json.dumps([asset]),duplicate_snapshot_hash=snapshot).status_code==409
    assert question(system,right,'已知三角形，求其面积。').status_code==200

def test_team_viewer_editor_and_other_team_isolation(system):
    member=create_user(system,'teacher_one')
    other_team=system.post('/platform/api/admin/teams',json={'name':'其他团队'}).json()['id']
    create_user(system,'teacher_other',other_team)
    shared=new_space(system,'团队共享','team')['id']
    viewer=login_user('teacher_one',system); outsider=login_user('teacher_other',system)
    assert viewer.get(f'/bank/{shared}/api/questions').status_code==200
    assert question(viewer,shared).status_code==403
    assert outsider.get(f'/workbench/{shared}').status_code==404
    assert viewer.get('/bank/1/api/questions').status_code==404
    assert viewer.post('/platform/api/spaces',json={'name':'非法共享','kind':'team','textbook_id':1}).status_code==403
    assert system.post(f'/platform/api/spaces/{shared}/members',json={'user_id':member,'permission':'editor'}).status_code==200
    assert question(viewer,shared).status_code==200
    assert viewer.post(f'/bank/{shared}/api/settings/save',data={'deepseek_key':'bad'}).status_code==403

def test_space_has_one_fixed_textbook(system):
    assert system.post('/platform/api/spaces',json={'name':'未选教材'}).status_code==400
    assert system.post('/platform/api/spaces',json={'name':'旧格式','textbook_ids':[1,2]}).status_code==400
    space=new_space(system,'B版空间',book=2)
    assert (space['subject'],space['stage'],space['textbook'])==('数学','高中','人教B版')
    metadata=system.get(f'/bank/{space["id"]}/api/config/metadata').json()
    assert metadata['curriculum']==json.loads(store.one('SELECT metadata FROM textbooks WHERE id=2')['metadata'])['curriculum']
    assert question(system,space['id'],expected_version_code='A').status_code==409
    assert question(system,space['id'],expected_version_code='B').status_code==200
    assert system.post(f'/bank/{space["id"]}/api/config/metadata',json=metadata).status_code==200
    metadata['curriculum']={}
    assert system.post(f'/bank/{space["id"]}/api/config/metadata',json=metadata).status_code==403
    with pytest.raises(sqlite3.IntegrityError):
        with store.db() as c:c.execute('UPDATE spaces SET textbook_id=1 WHERE id=?',(space['id'],))

def test_rename_space_requires_manager(system):
    uid=create_user(system,'rename_editor')
    shared=new_space(system,'改名前','team')['id']
    system.post(f'/platform/api/spaces/{shared}/members',json={'user_id':uid,'permission':'editor'})
    editor=login_user('rename_editor',system)
    assert editor.patch(f'/platform/api/spaces/{shared}',json={'name':'编辑者改名'}).status_code==403
    assert system.patch(f'/platform/api/spaces/{shared}',json={'name':''}).status_code==400
    assert system.patch(f'/platform/api/spaces/{shared}',json={'name':'改名后'}).status_code==200
    assert next(s for s in editor.get('/platform/api/spaces').json() if s['id']==shared)['name']=='改名后'

def test_disabled_textbook_hidden_from_creation_but_space_keeps_working(system):
    book=add_book(system,'数学','初中','停用测试教材')
    space=new_space(system,'停用前创建',book=book)['id']
    assert system.patch(f'/platform/api/admin/textbooks/{book}',json={'enabled':False}).status_code==200
    assert system.post('/platform/api/spaces',json={'name':'停用后','textbook_id':book}).status_code==400
    create_user(system,'book_viewer')
    assert all(b['id']!=book for b in login_user('book_viewer',system).get('/platform/api/textbooks').json())
    assert system.get(f'/bank/{space}/api/questions?page=1').status_code==200
    summary=next(b for b in system.get('/platform/api/textbooks').json() if b['id']==book)
    assert summary['enabled']==0 and summary['spaces']==1 and 'metadata' not in summary
    assert system.patch(f'/platform/api/admin/textbooks/{book}',json={'enabled':True}).status_code==200

def test_platform_default_and_team_override_without_key_leak(system):
    assert system.post('/platform/api/ai-settings',json={'scope':'platform','config':{'deepseek_key':'platform-test-secret','prefer_solve_model':'platform-model'}}).status_code==200
    settings=system.get('/bank/1/api/settings').json()
    assert settings['prefer_solve_model']=='platform-model'
    assert settings['deepseek_key']!='platform-test-secret'
    assert system.post('/platform/api/ai-settings',json={'scope':'team','config':{'prefer_solve_model':'team-model'}}).status_code==200
    settings=system.get('/bank/1/api/settings').json()
    assert settings['prefer_solve_model']=='team-model'
    assert system.get('/bank/1/api/settings?platform_default=1').json()['prefer_solve_model']=='platform-model'
    assert system.post('/platform/api/ai-settings',json={'scope':'team','config':{'prefer_solve_model':''}}).status_code==200
    assert system.get('/bank/1/api/settings').json()['prefer_solve_model']=='platform-model'

def test_native_paper_save_and_export(system):
    space=new_space(system,'组卷验证')['id']
    created=question(system,space,answer_markdown='$2x$')
    assert created.status_code==200,created.text
    q=created.json()['question']
    payload={'title':'原版组卷测试','paper_type':'exam','questions':[{'id':q['id'],'score':10}]}
    result=system.post(f'/bank/{space}/api/paper/save',json=payload)
    assert result.status_code==200,result.text
    paper=system.get(f'/bank/{space}/api/papers/{result.json()["paper_id"]}').json()['data']
    assert paper['questions'][0]['question']['answer_markdown']=='$2x$'
    export=system.post(f'/bank/{space}/api/paper/export/tex',json={**payload,'questions':[{**q,'score':10}]})
    assert export.status_code==200,export.text
    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
        assert any('原版组卷测试' in archive.read(name).decode('utf-8') for name in archive.namelist() if name.endswith('.tex'))

def test_tasks_and_host_operations_not_exposed(system):
    assert system.get('/bank/1/api/tasks/another-user-task/status').status_code==404
    assert system.post('/bank/1/api/shutdown').status_code==403
    assert system.post('/bank/1/api/runtime/pandoc/install').status_code==403

def test_cross_space_reference_keeps_images_origin_and_source_independent(system):
    source=new_space(system,'跨空间题目来源')['id']
    target=new_space(system,'跨空间组卷目标')['id']
    junior=new_space(system,'初中目标',book=add_book(system,'数学','初中','跨空间初中'))['id']
    image=io.BytesIO();Image.new('RGB',(48,48),'blue').save(image,format='PNG')
    uploaded=system.post(f'/bank/{source}/api/upload',files={'file':('figure.png',image.getvalue(),'image/png')}).json()['file_path']
    created=question(system,source,'跨空间图文题：求蓝色正方形面积。',image_paths=json.dumps([uploaded])).json()['question']
    sources={s['id'] for s in system.get(f'/platform/api/spaces/{target}/sources').json()}
    assert source in sources and target not in sources and junior not in sources
    assert system.get(f'/platform/api/spaces/{target}/search?source={target}').status_code==404
    assert system.get(f'/platform/api/spaces/{junior}/search?source={source}').status_code==404
    found=system.get(f'/platform/api/spaces/{target}/search?source={source}&q=蓝色').json()
    assert found['total']==1 and found['items'][0]['referenced_as'] is None
    assert found['items'][0]['image_paths'][0].startswith(f'/static/uploads/{source}/')
    payload={'source_space':source,'question_id':created['id']}
    assert system.post(f'/platform/api/spaces/{junior}/reference',json=payload).status_code==400
    with ThreadPoolExecutor(max_workers=4) as pool:
        copies=list(pool.map(lambda _: system.post(f'/platform/api/spaces/{target}/reference',json=payload),range(4)))
    assert all(r.status_code==200 for r in copies),[r.text for r in copies]
    assert len({r.json()['id'] for r in copies})==1
    copied=copies[0].json()
    assert copied['image_paths'][0].startswith(f'/static/uploads/{target}/')
    assert system.get(copied['image_paths'][0]).status_code==200
    origins=system.get(f'/platform/api/spaces/{target}/origins').json()
    assert len(origins)==1 and origins[0]['source_space_id']==source
    assert system.get(f'/bank/{target}/api/questions/{copied["id"]}').json()['origin']['source_question']==created['id']
    assert system.get(f'/platform/api/spaces/{target}/search?source={source}&q=蓝色').json()['items'][0]['referenced_as']==copied['id']
    assert system.delete(f'/bank/{source}/api/questions/{created["id"]}').status_code==200
    assert system.get(f'/bank/{target}/api/questions/{copied["id"]}').status_code==200
    assert system.get(copied['image_paths'][0]).status_code==200
    # Deleting the copy drops its origin link, so a reused id never inherits it.
    assert system.delete(f'/bank/{target}/api/questions/{copied["id"]}').status_code==200
    assert system.get(f'/platform/api/spaces/{target}/origins').json()==[]

def test_multilevel_paper_folders_are_space_bound(system):
    sid=new_space(system,'多级试卷目录')['id']
    top=system.post(f'/platform/api/spaces/{sid}/folders',json={'name':'高一','kind':'paper'}).json()['id']
    child=system.post(f'/platform/api/spaces/{sid}/folders',json={'name':'第一学期','kind':'paper','parent_id':top}).json()['id']
    assert system.post('/platform/api/spaces/1/folders',json={'name':'非法','parent_id':top,'kind':'paper'}).status_code==400
    q=question(system,sid).json()['question']
    saved=system.post(f'/bank/{sid}/api/paper/save',json={'title':'目录试卷','paper_type':'exam','questions':[{'id':q['id'],'score':10}],'platform_folder_id':child})
    assert saved.status_code==200,saved.text
    pid=saved.json()['paper_id']
    assert system.get(f'/bank/{sid}/api/papers?folder_id={child}').json()['data'][0]['folder_id']==child
    # A top-level folder lists the papers of its subfolders too.
    assert [p['id'] for p in system.get(f'/bank/{sid}/api/papers?folder_id={top}').json()['data']]==[pid]
    assert system.get(f'/bank/{sid}/api/papers?folder_id=0').json()['data']==[]
    assert system.get(f'/platform/api/spaces/{sid}/folders').json()['papers']=={str(pid):child}
    # Two levels; names are unique among siblings.
    third=system.post(f'/platform/api/spaces/{sid}/folders',json={'name':'第三级','kind':'paper','parent_id':child})
    assert third.status_code==400 and '两级' in third.json()['detail']
    assert system.post(f'/platform/api/spaces/{sid}/folders',json={'name':'第一学期','parent_id':top}).status_code==409
    assert system.post(f'/platform/api/spaces/{sid}/folders',json={'name':'第一学期'}).status_code==200
    second=system.post(f'/platform/api/spaces/{sid}/folders',json={'name':'第二学期','parent_id':top}).json()['id']
    assert system.patch(f'/platform/api/spaces/{sid}/folders/{second}',json={'name':'第一学期'}).status_code==409
    assert system.patch(f'/platform/api/spaces/{sid}/folders/{child}',json={'name':'上学期'}).status_code==200
    assert system.patch(f'/platform/api/spaces/1/folders/{child}',json={'name':'越界'}).status_code==404
    # Deleting moves the papers up one level instead of refusing.
    gone=system.delete(f'/platform/api/spaces/{sid}/folders/{child}').json()
    assert gone['papers_moved']==1 and system.get(f'/platform/api/spaces/{sid}/folders').json()['papers']=={str(pid):top}
    gone=system.delete(f'/platform/api/spaces/{sid}/folders/{top}').json()
    assert gone=={'status':'success','folders':2,'papers_moved':1}
    assert [p['id'] for p in system.get(f'/bank/{sid}/api/papers?folder_id=0').json()['data']]==[pid]
    assert {f['name'] for f in system.get(f'/platform/api/spaces/{sid}/folders').json()['folders']}=={'第一学期'}
    # Deleting the paper drops its folder link (engine ids can be reused).
    assert system.delete(f'/bank/{sid}/api/papers/{pid}').status_code==200
    assert system.get(f'/platform/api/spaces/{sid}/folders').json()['papers']=={}

def test_manual_crop_task_ownership_checked_before_engine(system):
    with store.db() as c:
        c.execute('INSERT INTO tasks VALUES (?,?,?)',('00000000-0000-4000-8000-000000000001',1,2))
    assert system.post('/bank/1/api/ai/manual-crop-pdf',json={'task_id':'00000000-0000-4000-8000-000000000001'}).status_code==404

def test_account_management_boundaries(system):
    lead=create_user(system,'acct_lead',role='team_admin')
    teacher=create_user(system,'acct_teacher')
    other_team=system.post('/platform/api/admin/teams',json={'name':'账号边界团队'}).json()['id']
    stranger=create_user(system,'acct_stranger',team=other_team)
    admin=login_user('acct_lead',system)
    victim=login_user('acct_teacher',system)
    assert admin.patch(f'/platform/api/admin/users/{stranger}',json={'display_name':'越权'}).status_code==404
    assert admin.patch(f'/platform/api/admin/users/{lead}',json={'enabled':False}).status_code==403
    assert admin.patch(f'/platform/api/admin/users/{teacher}',json={'role':'team_admin'}).status_code==403
    assert admin.patch(f'/platform/api/admin/users/{teacher}',json={'password':'short'}).status_code==400
    assert admin.patch(f'/platform/api/admin/users/{teacher}',json={'display_name':'新名字','password':'Reset-Pass-2026'}).status_code==200
    assert victim.get('/platform/api/me').status_code==401
    renamed=login_user('acct_teacher',system,'Reset-Pass-2026')
    assert renamed.get('/platform/api/me').json()['display_name']=='新名字'
    assert admin.patch(f'/platform/api/admin/users/{teacher}',json={'enabled':False}).status_code==200
    assert renamed.get('/platform/api/me').status_code==401
    me=system.get('/platform/api/me').json()['id']
    assert system.patch(f'/platform/api/admin/users/{me}',json={'enabled':False}).status_code==400
    assert system.patch(f'/platform/api/admin/users/{teacher}',json={'enabled':True,'team_id':other_team}).status_code==200
    assert next(u for u in system.get('/platform/api/admin/users').json() if u['id']==teacher)['team_id']==other_team

def test_non_teacher_roles_get_no_space_access():
    space={'id':1,'owner_id':5,'kind':'public','team_id':None}
    assert store.permission({'id':9,'role':'member','team_id':1},space)=='viewer'
    assert store.permission({'id':9,'role':'student','team_id':1},space) is None
    assert store.permission({'id':5,'role':'student','team_id':1},space) is None

def test_idle_worker_is_evicted_when_pool_is_full(system,monkeypatch):
    spaces=[new_space(system,f'回收{i}')['id'] for i in range(2)]
    for space in spaces:
        assert system.get(f'/bank/{space}/api/questions?page=1').status_code==200
    running=len([w for w in app.state.workers.workers.values() if w.process.poll() is None])
    monkeypatch.setenv('MATHBANK_MAX_WORKERS',str(running))
    third=new_space(system,'回收触发')['id']
    assert system.get(f'/bank/{third}/api/questions?page=1').status_code==200
    alive=[s for s,w in app.state.workers.workers.items() if w.process.poll() is None]
    assert len(alive)==running and third in alive
    assert system.get(f'/bank/{spaces[0]}/api/questions?page=1').status_code==200

def legacy_database(path):
    """platform.db as written before spaces became single banks."""
    with sqlite3.connect(path) as c:
        c.executescript('''
        CREATE TABLE teams(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
        CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
          password TEXT NOT NULL, role TEXT NOT NULL, team_id INTEGER REFERENCES teams(id), enabled INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE sessions(token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id), csrf TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE textbooks(id INTEGER PRIMARY KEY, stage TEXT NOT NULL, subject TEXT NOT NULL, name TEXT NOT NULL,
          engine_version TEXT NOT NULL DEFAULT 'A', metadata TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE spaces(id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, owner_id INTEGER REFERENCES users(id),
          team_id INTEGER REFERENCES teams(id), created REAL NOT NULL, subject TEXT NOT NULL DEFAULT '');
        CREATE TABLE members(space_id INTEGER REFERENCES spaces(id), user_id INTEGER REFERENCES users(id), permission TEXT NOT NULL, PRIMARY KEY(space_id,user_id));
        CREATE TABLE lanes(id INTEGER PRIMARY KEY, space_id INTEGER NOT NULL REFERENCES spaces(id), textbook_id INTEGER NOT NULL REFERENCES textbooks(id),
          stage TEXT NOT NULL, subject TEXT NOT NULL, UNIQUE(space_id,stage,subject));
        CREATE TABLE settings(scope TEXT PRIMARY KEY, content TEXT NOT NULL);
        CREATE TABLE tasks(id TEXT PRIMARY KEY, lane_id INTEGER NOT NULL REFERENCES lanes(id), user_id INTEGER NOT NULL REFERENCES users(id));
        CREATE TABLE folders(id INTEGER PRIMARY KEY, space_id INTEGER NOT NULL REFERENCES spaces(id), kind TEXT NOT NULL, name TEXT NOT NULL, parent_id INTEGER REFERENCES folders(id));
        CREATE TABLE paper_folders(lane_id INTEGER NOT NULL REFERENCES lanes(id), paper_id INTEGER NOT NULL, folder_id INTEGER REFERENCES folders(id), PRIMARY KEY(lane_id,paper_id));
        CREATE TABLE origins(lane_id INTEGER NOT NULL REFERENCES lanes(id), question_id INTEGER NOT NULL, source_lane INTEGER NOT NULL REFERENCES lanes(id),
          source_question INTEGER NOT NULL, source_space_name TEXT NOT NULL, copied_by INTEGER REFERENCES users(id), created REAL NOT NULL, PRIMARY KEY(lane_id,question_id));
        CREATE TABLE audit(id INTEGER PRIMARY KEY, user_id INTEGER, action TEXT, target TEXT, created REAL);
        CREATE TABLE platform_migrations(name TEXT PRIMARY KEY, created REAL NOT NULL);
        INSERT INTO platform_migrations VALUES ('physics-types-v1',0);
        INSERT INTO teams VALUES (1,'默认教研团队');
        INSERT INTO users VALUES (1,'admin','超级管理员','x:y','super_admin',1,1),(2,'t','教师','x:y','member',1,1);
        INSERT INTO textbooks VALUES (1,'高中','数学','人教A版','A','{"curriculum":{}}',1),(2,'初中','数学','人教版','A','{"curriculum":{}}',1);
        INSERT INTO spaces VALUES (1,'个人','personal',1,1,0,'数学'),(2,'教研组','team',1,1,0,'数学');
        INSERT INTO lanes VALUES (1,1,1,'高中','数学'),(2,2,2,'初中','数学'),(3,2,1,'高中','数学');
        INSERT INTO members VALUES (2,2,'editor');
        INSERT INTO folders VALUES (1,2,'paper','期中',NULL),(2,2,'paper','第一次',1);
        INSERT INTO paper_folders VALUES (2,1,2),(3,1,1),(3,2,NULL);
        INSERT INTO origins VALUES (3,9,1,4,'个人',1,0);
        INSERT INTO tasks VALUES ('task-1',3,2);
        ''')

def test_legacy_stage_lanes_become_spaces_with_same_bank_ids(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path)
    legacy_database(tmp_path/'platform.db')
    marker=tmp_path/'banks/3/keep.txt';marker.parent.mkdir(parents=True);marker.write_text('bank 3')
    store.initialize()
    spaces={s['id']:s for s in store.rows(store.SPACE_SELECT)}
    assert {i:(s['name'],s['stage'],s['kind']) for i,s in spaces.items()}=={
        1:('个人','高中','personal'),2:('教研组（初中）','初中','team'),3:('教研组（高中）','高中','team')}
    assert store.rows('SELECT space_id,user_id,permission FROM members ORDER BY space_id')==[
        {'space_id':2,'user_id':2,'permission':'editor'},{'space_id':3,'user_id':2,'permission':'editor'}]
    folders={(f['space_id'],f['name']):f for f in store.rows('SELECT * FROM folders')}
    assert set(folders)=={(2,'期中'),(2,'第一次'),(3,'期中'),(3,'第一次')}
    assert folders[3,'第一次']['parent_id']==folders[3,'期中']['id']!=folders[2,'期中']['id']
    assert {(p['space_id'],p['paper_id']):p['folder_id'] for p in store.rows('SELECT * FROM paper_folders')}=={
        (2,1):folders[2,'第一次']['id'],(3,1):folders[3,'期中']['id'],(3,2):None}
    assert store.rows('SELECT space_id,question_id,source_space_id FROM origins')==[{'space_id':3,'question_id':9,'source_space_id':1}]
    assert store.rows('SELECT * FROM tasks')==[{'id':'task-1','space_id':3,'user_id':2}]
    assert marker.read_text()=='bank 3'
    assert {r['name'] for r in store.rows('SELECT name FROM platform_migrations')}==set(migrations.NAMES)
    backups=list((tmp_path/'migration_backups').glob('before-single-bank-spaces-*.db'))
    assert len(backups)==1 and backups[0].with_suffix('.json').exists()
    with sqlite3.connect(backups[0]) as c:
        assert c.execute('SELECT count(*) FROM lanes').fetchone()[0]==3
    store.initialize()
    assert len(list((tmp_path/'migration_backups').glob('before-single-bank-spaces-*.db')))==1

def test_saved_paper_layouts_are_independent_and_legacy_migration(system):
    space = new_space(system, '试卷排版隔离')['id']
    q = question(system, space, figure_align='right', figure_align_custom='true', figure_size='small').json()['question']
    qid = q['id']
    base = f'/bank/{space}'
    def save(title, **layout):
        r = system.post(base+'/api/paper/save', json={'title':title,'paper_type':'exam','questions':[{'id':qid,'score':12,**layout}]})
        assert r.status_code == 200, r.text
        return r.json()['paper_id']
    def read(pid):
        return system.get(base+f'/api/papers/{pid}').json()['data']['questions'][0]['question']
    a = save('A右侧小图')
    changed = system.post(base+f'/api/questions/{qid}/figure_layout',data={'figure_align':'center','figure_size':'large'})
    assert changed.status_code == 200
    b = save('B居中大图')
    c = save('C独立覆盖',figure_align='bottom_left',figure_align_custom=True,figure_size='medium')
    assert (read(a)['figure_align'],read(a)['figure_size']) == ('right','small')
    assert (read(b)['figure_align'],read(b)['figure_size']) == ('center','large')
    assert read(c)['figure_align'] == 'bottom_left'
    assert system.get(base+f'/api/questions/{qid}').json()['figure_align'] == 'center'
    bad = system.post(base+'/api/paper/save',json={'title':'无效排版','questions':[{'id':qid,'figure_align':'evil'}]})
    assert bad.status_code == 400
    # Simulate a pre-upgrade saved paper, then restart only the isolated worker.
    worker=app.state.workers.workers[space];worker.process.terminate();worker.process.wait(timeout=15)
    dbpath=store.DATA/'banks'/str(space)/'math_question_bank.db'
    with sqlite3.connect(dbpath) as db:
        db.execute('UPDATE papers SET metadata_json=? WHERE id=?', ('{"show_notice":false}', b))
    assert read(a)['figure_align'] == 'right'
    assert read(b)['figure_align'] == 'center'
    backups=list(dbpath.parent.rglob('before-paper-layouts-*.db'))
    assert len(backups)==1
    with sqlite3.connect(backups[0]) as db:
        assert 'question_figure_layouts_v1' not in db.execute('SELECT metadata_json FROM papers WHERE id=?',(b,)).fetchone()[0]
    system.post(base+f'/api/questions/{qid}/figure_layout',data={'figure_align':'bottom_right','figure_size':'small'})
    assert read(b)['figure_align'] == 'center'
    worker=app.state.workers.workers[space];worker.process.terminate();worker.process.wait(timeout=15)
    assert read(b)['figure_align'] == 'center'
    assert len(list(dbpath.parent.rglob('before-paper-layouts-*.db')))==1


def test_choices_right_layout_survives_save_reload_and_default_changes(system):
    space=new_space(system,'选项右侧')['id']
    q=question(system,space,content=r'判断运动方向。\begin{choices}\item 向左\item 向右\item 向上\item 向下\end{choices}',question_type='single_choice',figure_align='choices_right',figure_align_custom='true').json()['question']
    assert q['figure_align']=='choices_right'
    r=system.post(f'/bank/{space}/api/paper/save',json={'title':'选项分栏','questions':[{'id':q['id'],'score':5}],'paper_type':'exam'})
    assert r.status_code==200,r.text
    pid=r.json()['paper_id']
    r=system.post(f'/bank/{space}/api/questions/{q["id"]}/figure_layout',data={'figure_align':'center','figure_size':'large'})
    assert r.status_code==200,r.text
    saved=system.get(f'/bank/{space}/api/papers/{pid}').json()['data']['questions'][0]['question']
    assert saved['figure_align']=='choices_right' and saved['figure_size']=='auto'
