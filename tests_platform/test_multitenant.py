"""Account/space hierarchy, live permission changes and concurrent API regressions.

All writes go to pytest's temporary directory, never the running instance.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from platform_app import store
from platform_app.app import app

PASSWORD = 'Multitenant-Test-2026'


@pytest.fixture(scope='module')
def tenant_system(tmp_path_factory):
    previous = store.DATA
    store.DATA = tmp_path_factory.mktemp('multitenant')
    clients = {}
    try:
        with TestClient(app, raise_server_exceptions=False) as admin:
            password = (store.DATA/'initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：', 1)[1]
            assert admin.post('/platform/api/login', json={'username':'admin', 'password':password}).status_code == 200
            admin.headers['X-Local-Token'] = admin.get('/platform/api/me').json()['csrf']
            clients['root'] = admin
            teams = [1, admin.post('/platform/api/admin/teams', json={'name':'第二团队'}).json()['id']]
            ids = {}
            for name, team, role in [('a_admin',teams[0],'team_admin'), ('a_owner',teams[0],'member'),
                                     ('a_editor',teams[0],'member'), ('a_viewer',teams[0],'member'),
                                     ('b_admin',teams[1],'team_admin'), ('b_member',teams[1],'member')]:
                r = admin.post('/platform/api/admin/users', json={'username':name,'display_name':name,'password':PASSWORD,'team_id':team,'role':role})
                assert r.status_code == 200, r.text
                ids[name] = r.json()['id']
                client = TestClient(app, raise_server_exceptions=False)
                client.portal = admin.portal
                assert client.post('/platform/api/login', json={'username':name,'password':PASSWORD}).status_code == 200
                client.headers['X-Local-Token'] = client.get('/platform/api/me').json()['csrf']
                clients[name] = client
            metadata = admin.get('/platform/api/admin/textbooks/1').json()['metadata']
            junior = admin.post('/platform/api/admin/textbooks', json={'name':'初中验收教材','subject':'数学','stage':'初中','metadata':metadata}).json()['id']
            spaces = {}
            for name, creator, kind, book in [('a_personal','a_owner','personal',1), ('a_shared','a_admin','team',1),
                                              ('a_junior','a_admin','team',junior),
                                              ('b_personal','b_member','personal',1), ('b_shared','b_admin','team',1),
                                              ('public','root','public',1)]:
                r = clients[creator].post('/platform/api/spaces', json={'name':name,'kind':kind,'textbook_id':book})
                assert r.status_code == 200, r.text
                spaces[name] = next(s for s in admin.get('/platform/api/spaces').json() if s['id'] == r.json()['id'])
            sid = spaces['a_shared']['id']
            assert admin.post(f'/platform/api/spaces/{sid}/members', json={'user_id':ids['a_editor'],'permission':'editor'}).status_code == 200
            yield {'clients':clients,'spaces':spaces,'ids':ids,'teams':teams,'junior':junior}
            for name, client in clients.items():
                if name != 'root':
                    client.close()
    finally:
        store.DATA = previous


EXPECTED = {
    'root': {'a_personal':'owner','a_shared':'owner','a_junior':'owner','b_personal':'owner','b_shared':'owner','public':'owner'},
    'a_admin': {'a_shared':'owner','a_junior':'owner','public':'viewer'},
    'a_owner': {'a_personal':'owner','a_shared':'viewer','a_junior':'viewer','public':'viewer'},
    'a_editor': {'a_shared':'editor','a_junior':'viewer','public':'viewer'},
    'a_viewer': {'a_shared':'viewer','a_junior':'viewer','public':'viewer'},
    'b_admin': {'b_shared':'owner','public':'viewer'},
    'b_member': {'b_personal':'owner','b_shared':'viewer','public':'viewer'},
}


@pytest.mark.parametrize('name', EXPECTED)
def test_exact_account_space_hierarchy(tenant_system, name):
    s = tenant_system
    response = s['clients'][name].get('/platform/api/spaces')
    assert response.status_code == 200
    visible = {v['name']:v for v in response.json()}
    if name == 'root':
        default = visible.pop('我的备课空间')
        assert default['permission'] == 'owner' and default['stage'] == '高中'
    assert set(visible) == set(EXPECTED[name])
    for key, grant in EXPECTED[name].items():
        space = visible[key]
        assert space['permission'] == grant
        assert (space['subject'], space['stage']) == ('数学', '初中' if key == 'a_junior' else '高中')
        assert 'password' not in space and 'member_permission' not in space


@pytest.mark.parametrize('name', EXPECTED)
def test_account_tree_admin_boundaries(tenant_system, name):
    s = tenant_system
    client = s['clients'][name]
    for resource in ['users','teams']:
        r = client.get('/platform/api/admin/'+resource)
        if name not in {'root','a_admin','b_admin'}:
            assert r.status_code == 403
        elif name != 'root':
            assert r.status_code == 200
            team = s['teams'][name.startswith('b_')]
            assert all(v['team_id' if resource == 'users' else 'id'] == team for v in r.json())
            assert all('password' not in v and 'csrf' not in v for v in r.json())


@pytest.mark.parametrize('name', EXPECTED)
@pytest.mark.parametrize('space_name', ['a_personal','a_shared','a_junior','b_personal','b_shared','public'])
def test_folder_read_write_permission_matrix(tenant_system, name, space_name):
    s = tenant_system
    client = s['clients'][name]
    path = f"/platform/api/spaces/{s['spaces'][space_name]['id']}/folders"
    grant = EXPECTED[name].get(space_name)
    assert client.get(path).status_code == (200 if grant else 404)
    assert client.post(path, json={'name':name+'目录'}).status_code == (200 if grant in {'owner','editor'} else 403 if grant else 404)


def test_team_admin_cannot_escalate_or_create_in_other_team(tenant_system):
    s = tenant_system
    client = s['clients']['a_admin']
    for role in ['team_admin','super_admin']:
        assert client.post('/platform/api/admin/users', json={'username':'escalate','password':PASSWORD,'role':role,'team_id':s['teams'][1]}).status_code == 403
    r = client.post('/platform/api/admin/users', json={'username':'forced_team','password':PASSWORD,'role':'member','team_id':s['teams'][1]})
    assert r.status_code == 200
    assert next(u for u in client.get('/platform/api/admin/users').json() if u['id'] == r.json()['id'])['team_id'] == s['teams'][0]
    assert client.post('/platform/api/admin/teams', json={'name':'非法团队'}).status_code == 403
    assert client.post('/platform/api/spaces', json={'name':'非法公共','kind':'public','textbook_id':1}).status_code == 403


def test_live_grant_revoke_and_member_management_boundaries(tenant_system):
    s = tenant_system
    admin = s['clients']['a_admin']
    editor = s['clients']['a_editor']
    sid = s['spaces']['a_shared']['id']
    member_path = f'/platform/api/spaces/{sid}/members'
    folder_path = f'/platform/api/spaces/{sid}/folders'
    assert editor.post(member_path, json={'user_id':s['ids']['a_viewer'],'permission':'editor'}).status_code == 403
    assert s['clients']['b_admin'].post(member_path, json={'user_id':s['ids']['b_member'],'permission':'editor'}).status_code == 404
    assert admin.post(member_path, json={'user_id':s['ids']['b_member'],'permission':'editor'}).status_code == 400
    assert admin.post(member_path, json={'user_id':s['ids']['a_editor'],'permission':'viewer'}).status_code == 200
    assert editor.post(folder_path, json={'name':'撤权后拒绝'}).status_code == 403
    assert next(v for v in editor.get('/platform/api/spaces').json() if v['id'] == sid)['permission'] == 'viewer'
    assert admin.post(member_path, json={'user_id':s['ids']['a_editor'],'permission':'editor'}).status_code == 200
    assert editor.post(folder_path, json={'name':'恢复授权'}).status_code == 200


def test_duplicate_team_is_conflict_not_server_error(tenant_system):
    admin = tenant_system['clients']['root']
    assert admin.post('/platform/api/admin/teams', json={'name':'重复团队'}).status_code == 200
    assert admin.post('/platform/api/admin/teams', json={'name':'重复团队'}).status_code == 409


def test_concurrent_same_username_is_one_success_and_conflicts(tenant_system):
    admin = tenant_system['clients']['root']
    def create(_):
        return admin.post('/platform/api/admin/users', json={'username':'concurrent_teacher','password':PASSWORD,'team_id':1}).status_code
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(create, range(6)))
    assert sorted(results) == [200,409,409,409,409,409], results
    assert len([v for v in admin.get('/platform/api/admin/users').json() if v['username']=='concurrent_teacher']) == 1


@pytest.mark.parametrize('parent', [0, -1, 'bad', [], {}])
def test_invalid_folder_parent_returns_400(tenant_system, parent):
    s = tenant_system
    path = f"/platform/api/spaces/{s['spaces']['a_shared']['id']}/folders"
    assert s['clients']['root'].post(path, json={'name':'非法上级','parent_id':parent}).status_code == 400


def test_two_level_folder_tree_and_cross_space_references(tenant_system):
    s = tenant_system
    admin = s['clients']['root']
    sid = s['spaces']['a_shared']['id']
    path = f"/platform/api/spaces/{sid}/folders"
    top = admin.post(path, json={'name':'一级'}).json()['id']
    child = admin.post(path, json={'name':'二级','parent_id':top}).json()['id']
    assert admin.post(path, json={'name':'三级','parent_id':child}).status_code == 400
    # Older installs may hold deeper trees: they stay listed, filterable and deletable.
    with store.db() as c:
        legacy = c.execute('INSERT INTO folders(space_id,kind,name,parent_id) VALUES (?,?,?,?)', (sid, 'paper', '旧三级', child)).lastrowid
    folders = {f['id']:f for f in admin.get(path).json()['folders']}
    assert folders[child]['parent_id'] == top and folders[legacy]['parent_id'] == child
    assert admin.post(path, json={'name':'类型错配','kind':'lesson','parent_id':top}).status_code == 400
    other = f"/platform/api/spaces/{s['spaces']['b_shared']['id']}/folders"
    assert admin.post(other, json={'name':'越界目录','parent_id':top}).status_code == 400
    assert admin.delete(f'{path}/{top}').json()['folders'] == 3


def test_shared_bank_folders_and_reference_isolation(tenant_system):
    s = tenant_system
    admin = s['clients']['root']; editor = s['clients']['a_editor']; viewer = s['clients']['a_viewer']
    sid = s['spaces']['a_shared']['id']
    junior = s['spaces']['a_junior']['id']
    other = s['spaces']['b_shared']['id']
    base = f'/bank/{sid}/api'
    r = editor.post(base+'/questions', data={'content':'团队A专有题目','question_type':'detailed_answer','difficulty':'3'})
    assert r.status_code == 200, r.text
    q = r.json()['question']
    assert viewer.get(base+f"/questions/{q['id']}").json()['content'] == q['content']
    assert viewer.delete(base+f"/questions/{q['id']}").status_code == 403
    assert s['clients']['b_member'].get(base+'/questions').status_code == 404
    for candidate in [junior,other]:
        for endpoint in ['questions','categories','stats','papers']:
            assert admin.get(f'/bank/{candidate}/api/{endpoint}').status_code == 200
        assert admin.get(f'/bank/{candidate}/api/questions?page=1').json()['total'] == 0
    folder = admin.post(f'/platform/api/spaces/{sid}/folders', json={'name':'保存目录'}).json()['id']
    foreign = admin.post(f"/platform/api/spaces/{s['spaces']['b_shared']['id']}/folders", json={'name':'其他团队目录'}).json()['id']
    payload = {'title':'隔离试卷','paper_type':'exam','questions':[{'id':q['id'],'score':10}],'platform_folder_id':folder}
    assert editor.post(base+'/paper/save', json={**payload,'platform_folder_id':foreign}).status_code == 400
    saved = editor.post(base+'/paper/save', json=payload)
    assert saved.status_code == 200, saved.text
    pid = saved.json()['paper_id']
    assert viewer.get(base+f'/papers?folder_id={folder}').json()['data'][0]['id'] == pid
    assert viewer.post(f'/platform/api/spaces/{sid}/papers/{pid}/folder', json={'folder_id':None}).status_code == 403
    assert editor.post(f'/platform/api/spaces/{sid}/papers/{pid}/folder', json={'folder_id':foreign}).status_code == 400
    assert editor.post(f'/platform/api/spaces/{sid}/papers/{pid}/folder', json={'folder_id':None}).status_code == 200
    assert viewer.get(base+f'/papers?folder_id={folder}').json()['data'] == []
    assert viewer.get(base+'/papers?folder_id=0').json()['data'][0]['id'] == pid
    assert viewer.post(f'/platform/api/spaces/{sid}/reference', json={'source_space':other,'question_id':q['id']}).status_code == 403
    assert editor.post(f'/platform/api/spaces/{sid}/reference', json={'source_space':other,'question_id':q['id']}).status_code == 404
    assert editor.get(f'/platform/api/spaces/{sid}/search?source={other}').status_code == 404
    assert admin.post(f'/platform/api/spaces/{junior}/reference', json={'source_space':sid,'question_id':q['id']}).status_code == 400
    assert {v['id'] for v in editor.get(f'/platform/api/spaces/{sid}/sources').json()} == {s['spaces']['public']['id']}


def test_task_ownership_and_session_csrf(tenant_system):
    s = tenant_system
    owner = s['clients']['a_editor']; other = s['clients']['a_viewer']
    sid = s['spaces']['a_shared']['id']
    task = '11111111-1111-4111-8111-111111111111'
    with store.db() as c:
        c.execute('INSERT INTO tasks VALUES (?,?,?)', (task,sid,s['ids']['a_editor']))
    assert other.get(f'/bank/{sid}/api/tasks/{task}/status').status_code == 404
    assert other.post(f'/bank/{sid}/api/tasks/{task}/cancel').status_code == 403
    assert owner.post(f'/bank/{sid}/api/tasks/{task}/retain', headers={'X-Local-Token':other.headers['X-Local-Token']}).status_code == 403


def test_parallel_users_writing_shared_bank_do_not_lose_or_leak_questions(tenant_system):
    s = tenant_system
    sid = s['spaces']['a_shared']['id']
    clients = [s['clients']['root'],s['clients']['a_admin'],s['clients']['a_editor']]
    def create(i):
        r = clients[i%3].post(f'/bank/{sid}/api/questions', data={'content':f'parallel-write-{i}','difficulty':'3','question_type':'detailed_answer'})
        assert r.status_code == 200, r.text
        return r.json()['question']['id']
    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(create, range(12)))
    assert len(set(ids)) == 12
    r = s['clients']['a_viewer'].get(f'/bank/{sid}/api/questions?page=1&q=parallel-write-')
    assert r.status_code == 200 and r.json()['total'] == 12
    other = s['spaces']['b_shared']['id']
    assert s['clients']['root'].get(f'/bank/{other}/api/questions?page=1&q=parallel-write-').json()['total'] == 0


def test_stale_tab_cannot_read_new_account_data(tenant_system):
    s = tenant_system
    client = s['clients']['a_viewer']
    stale_token = s['clients']['a_editor'].headers['X-Local-Token']
    for path in ['/platform/api/spaces','/platform/api/me']:
        assert client.get(path, headers={'X-Local-Token':stale_token}).status_code == 409


def test_member_permissions_are_current_scoped_and_private(tenant_system):
    s = tenant_system
    path = f"/platform/api/spaces/{s['spaces']['a_shared']['id']}/members"
    r = s['clients']['a_admin'].get(path)
    assert r.status_code == 200
    values = {u['id']:u for u in r.json()}
    assert values[s['ids']['a_editor']]['permission'] == 'editor'
    assert values[s['ids']['a_viewer']]['permission'] == 'viewer'
    assert all(u['team_id']==s['teams'][0] and 'password' not in u and 'csrf' not in u for u in values.values())
    assert s['clients']['a_editor'].get(path).status_code == 403
    assert s['clients']['b_admin'].get(path).status_code == 404


@pytest.mark.parametrize('value', [[], {}, True, -1, '1'])
def test_invalid_paper_folder_rejected_before_worker(tenant_system, value):
    s = tenant_system
    sid = s['spaces']['a_shared']['id']
    client = s['clients']['a_editor']
    assert client.post(f'/bank/{sid}/api/paper/save', json={'platform_folder_id':value}).status_code == 400
    assert client.post(f'/platform/api/spaces/{sid}/papers/1/folder', json={'folder_id':value}).status_code == 400


def test_password_change_invalidates_all_sessions(tenant_system):
    s = tenant_system
    client = s['clients']['b_member']
    second = TestClient(app, raise_server_exceptions=False); second.portal = s['clients']['root'].portal
    try:
        assert second.post('/platform/api/login', json={'username':'b_member','password':PASSWORD}).status_code == 200
        assert client.post('/platform/api/password', json={'old':PASSWORD,'new':PASSWORD+'-new'}).status_code == 200
        assert client.get('/platform/api/me').status_code == 401
        assert second.get('/platform/api/me').status_code == 401
        assert second.post('/platform/api/login', json={'username':'b_member','password':PASSWORD}).status_code == 401
        assert second.post('/platform/api/login', json={'username':'b_member','password':PASSWORD+'-new'}).status_code == 200
    finally:
        second.close()


def test_disabled_account_loses_login_and_existing_session(tenant_system):
    s = tenant_system
    uid = s['ids']['a_viewer']
    client = s['clients']['a_viewer']
    try:
        with store.db() as c:
            c.execute('UPDATE users SET enabled=0 WHERE id=?',(uid,))
        assert client.get('/platform/api/me').status_code == 401
        assert client.get('/platform/api/spaces').status_code == 401
        assert client.post('/platform/api/login',json={'username':'a_viewer','password':PASSWORD}).status_code == 401
    finally:
        with store.db() as c:
            c.execute('UPDATE users SET enabled=1 WHERE id=?',(uid,))
