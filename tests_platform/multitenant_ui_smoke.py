"""Real-browser account/space tests. Requires an explicitly isolated server."""
from pathlib import Path
import json
import os
import time

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ['MATHBANK_TEST_URL']
DATA = Path(os.environ['MATHBANK_TEST_DATA']).resolve()
assert DATA != (ROOT/'data').resolve(), 'Never test against business data'
OUT = ROOT/'test-results'
password = (DATA/'initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：', 1)[1]
suffix = str(time.time_ns())[-10:]
PW = 'Tenant-ui-'+suffix
checks = []
errors = []
failures = []
ready = "document.documentElement.dataset.accountReady==='true'"


def login_api(name, pw=PW):
    client = httpx.Client(base_url=BASE, trust_env=False, timeout=60)
    assert client.post('/platform/api/login', json={'username': name, 'password': pw}).status_code == 200
    client.headers['X-Local-Token'] = client.get('/platform/api/me').json()['csrf']
    return client


admin = login_api('admin', password)
team_a = admin.post('/platform/api/admin/teams', json={'name': '浏览器团队A'+suffix}).json()['id']
team_b = admin.post('/platform/api/admin/teams', json={'name': '浏览器团队B'+suffix}).json()['id']
users = {}
for name, team, role in [('manager', team_a, 'team_admin'), ('editor', team_a, 'member'), ('viewer', team_a, 'member'), ('outsider', team_b, 'team_admin')]:
    username = name+'_'+suffix
    r = admin.post('/platform/api/admin/users', json={'username': username, 'display_name': name, 'password': PW, 'team_id': team, 'role': role})
    assert r.status_code == 200, r.text
    users[name] = {'id': r.json()['id'], 'username': username, 'api': login_api(username)}
metadata = admin.get('/platform/api/admin/textbooks/1').json()['metadata']
junior = admin.post('/platform/api/admin/textbooks', json={'name': '浏览器初中'+suffix, 'stage': '初中', 'subject': '数学', 'metadata': metadata}).json()['id']
spaces = {}
for key, client, kind, book in [('shared', users['manager']['api'], 'team', 1), ('shared_junior', users['manager']['api'], 'team', junior),
                                ('personal', users['editor']['api'], 'personal', 1), ('foreign', users['outsider']['api'], 'team', 1),
                                ('public', admin, 'public', 1)]:
    r = client.post('/platform/api/spaces', json={'name': key+'_'+suffix, 'kind': kind, 'textbook_id': book})
    assert r.status_code == 200, r.text
    spaces[key] = next(s for s in admin.get('/platform/api/spaces').json() if s['id'] == r.json()['id'])
sid = spaces['shared']['id']
junior_sid = spaces['shared_junior']['id']
assert admin.post(f'/platform/api/spaces/{sid}/members', json={'user_id': users['editor']['id'], 'permission': 'editor'}).status_code == 200
q = users['editor']['api'].post(f'/bank/{sid}/api/questions', data={'content': '浏览器隔离题目'+suffix, 'question_type': 'detailed_answer', 'difficulty': '3'}).json()['question']
parent = None
for depth in range(2):
    parent = admin.post(f'/platform/api/spaces/{sid}/folders', json={'name': f'层{depth}', 'parent_id': parent}).json()['id']
assert admin.post(f'/platform/api/spaces/{sid}/folders', json={'name': '层2', 'parent_id': parent}).status_code == 400


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    def login_browser(name):
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = context.new_page()
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(BASE)
        page.wait_for_load_state('networkidle')
        page.locator('#loginForm').get_by_label('账号', exact=True).fill(users[name]['username'])
        page.locator('#loginForm').get_by_label('密码', exact=True).fill(PW)
        page.get_by_role('button', name='登录', exact=True).click()
        page.wait_for_function(ready)
        page.wait_for_load_state('networkidle')
        return context, page

    manager_context, manager = login_browser('manager')
    manager.locator('#appNavMine').click()
    manager.get_by_role('button', name='空间', exact=True).click()
    public_card = manager.locator('.mb-space-card').filter(has=manager.get_by_role('heading', name=spaces['public']['name'], exact=True))
    if public_card.get_by_role('button', name='重命名', exact=True).count():
        failures.append('Team admin can try to rename a read-only public space')
    checks.append('public space management UI follows permissions')

    shared_card = manager.locator('.mb-space-card').filter(has=manager.get_by_role('heading', name=spaces['shared']['name'], exact=True))
    shared_card.get_by_role('button', name='成员权限').click()
    editor_select = manager.get_by_label('editor的权限', exact=True)
    editor_select.wait_for()
    if editor_select.input_value() != 'editor':
        failures.append('Existing editor permission incorrectly shown as viewer')
    if manager.get_by_label('viewer的权限', exact=True).input_value() != 'viewer':
        failures.append('Viewer permission not loaded')
    manager.keyboard.press('Escape')
    checks.append('existing member permissions load for every member')
    manager.get_by_role('button', name='团队与账号', exact=True).click()
    assert users['outsider']['username'] not in manager.locator('#users').inner_text()
    checks.append('team account listing excludes other teams')

    editor_context, editor = login_browser('editor')
    viewer_context, viewer = login_browser('viewer')
    outsider_context, outsider = login_browser('outsider')
    assert outsider.request.get(BASE+f'/workbench/{sid}').status == 404
    outsider.locator('#appNavMine').click()
    outsider.get_by_role('button', name='空间', exact=True).click()
    assert spaces['shared']['name'] not in outsider.locator('#spaces').inner_text()
    checks.append('separate browser accounts cannot see or enter another team')

    editor.goto(BASE+f'/workbench/{sid}')
    editor.wait_for_function(ready)
    viewer.goto(BASE+f'/workbench/{sid}')
    viewer.wait_for_function(ready)
    viewer.locator('#appNavMine').click()
    viewer.get_by_role('button', name='空间', exact=True).click()
    assert viewer.get_by_role('button', name='成员权限').count() == 0
    assert viewer.get_by_role('button', name='重命名').count() == 0
    assert viewer.request.post(BASE+f'/bank/{sid}/api/questions', headers={'X-Local-Token': viewer.evaluate('MathBankPlatform.csrf')}, form={'content': '拒绝写入'}).status == 403
    viewer.locator('#appNavPaper').click()
    assert viewer.locator('#platformCrossSpaceBtn').count() == 0 and viewer.locator('#platformSaveFolder').count() == 0
    checks.append('viewer writes rejected and write/management controls hidden')

    editor.evaluate("""q=>{PaperStore.questionsMap[q.id]=q;addToCart(q.id);localStorage.setItem('mathbank_local_drafts',JSON.stringify([{id:'draft-a',content:'仅editor可见'}]));}""", q)
    assert viewer.evaluate("localStorage.getItem('mathbank_local_drafts')") is None
    assert viewer.evaluate('PaperStore.cart.length') == 0
    editor.goto(BASE+f'/workbench/{junior_sid}')
    editor.wait_for_function(ready)
    assert editor.evaluate("localStorage.getItem('mathbank_local_drafts')") is None
    assert editor.evaluate('PaperStore.cart.length') == 0
    editor.goto(BASE+f'/workbench/{sid}')
    editor.wait_for_function(ready)
    assert editor.evaluate('PaperStore.cart[0].id') == q['id']
    assert json.loads(editor.evaluate("localStorage.getItem('mathbank_local_drafts')"))[0]['id'] == 'draft-a'
    checks.append('draft and cart separated by account and space, restored on return')

    # Two accounts share the same browser storage after logging out and back in.
    stale = editor_context.new_page()
    stale.on('pageerror', lambda e: errors.append(str(e)))
    stale.goto(BASE+f'/workbench/{sid}')
    stale.wait_for_function(ready)
    stale.wait_for_load_state('networkidle')
    # Hold background polling so this assertion drives the first stale read.
    stale.route('**/bank/**', lambda route: route.continue_() if 'stale-session-probe' in route.request.url else route.abort())
    editor.locator('#appNavMine').click()
    editor.get_by_role('button', name='退出登录', exact=True).click()
    editor.locator('#loginForm').get_by_label('账号', exact=True).fill(users['viewer']['username'])
    editor.locator('#loginForm').get_by_label('密码', exact=True).fill(PW)
    editor.get_by_role('button', name='登录', exact=True).click()
    editor.wait_for_function(ready)
    assert editor.evaluate("localStorage.getItem('mathbank_local_drafts')") is None
    assert editor.evaluate('PaperStore.cart.length') == 0
    checks.append('same-browser account switch does not inherit drafts or cart')
    with stale.expect_response(lambda r: '/bank/' in r.url and '/api/questions' in r.url and r.status == 409) as changed:
        stale.evaluate("void fetch('/api/questions?page=1&q=stale-session-probe')")
    assert changed.value.headers.get('x-mathbank-session-changed') == '1'
    stale.unroute('**/bank/**')
    stale.wait_for_function("id=>window.MathBankPlatform?.id===id && document.documentElement.dataset.accountReady==='true'", arg=users['viewer']['id'])
    assert stale.evaluate("localStorage.getItem('mathbank_local_drafts')") is None
    assert stale.evaluate('PaperStore.cart.length') == 0
    checks.append('stale tab rejects new-account reads and reloads into the current account')

    manager.goto(BASE+f'/workbench/{sid}')
    manager.wait_for_function(ready)
    manager.locator('#appNavPaper').click()
    leaf_option = manager.locator(f'#platformSaveFolder option[value="{parent}"]')
    leaf_option.wait_for(state='attached')
    if leaf_option.text_content() != '层0 / 层1':
        failures.append('Second-level folder path does not show its parent')
    checks.append('two-level folder path in the save folder, third level rejected')
    for workspace in ['bank', 'paper', 'import', 'records', 'qa', 'dashboard']:
        manager.evaluate('workspace=>selectWorkspace(workspace,workspace)', workspace)
        manager.locator('#appNavMine').click()
        assert manager.locator('#mineWorkspaceSection').is_visible()
        visible = manager.locator('#appContentShell > main > [id$="WorkspaceSection"]:visible').evaluate_all('(nodes)=>nodes.map(n=>n.id)')
        assert visible == ['mineWorkspaceSection'], (workspace, visible)
    checks.append('entering My from all six workspaces hides the previous workspace')
    manager.locator('#appNavMine').click()
    manager.get_by_role('button', name='空间', exact=True).click()
    manager.evaluate('toggleDarkMode()')
    manager.set_viewport_size({'width': 390, 'height': 844})
    assert manager.evaluate('document.documentElement.scrollWidth<=innerWidth')
    manager.screenshot(path=str(OUT/'multitenant-mobile.png'), full_page=True, animations='disabled')
    checks.append('390px dark account and space layout')
    assert not errors, errors
    assert not failures, failures
    report = {'passed': True, 'accounts': 5, 'teams': 2, 'spaces': 5, 'checks': checks, 'page_errors': errors}
    (OUT/'multitenant-ui-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    browser.close()

for user in users.values():
    user['api'].close()
admin.close()
