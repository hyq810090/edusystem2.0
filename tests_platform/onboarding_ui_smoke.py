"""A new account without spaces lands on 我的 → 空间 and creates its first space."""
from pathlib import Path
import json
import os
import time

import httpx
from playwright.sync_api import sync_playwright

base = os.environ.get('MATHBANK_TEST_URL', 'http://127.0.0.1:3012')
password = (Path(os.environ.get('MATHBANK_TEST_DATA', 'test-results/ui-data'))/'initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：', 1)[1]
suffix = str(time.time_ns())[-10:]
username, pw = 'new_'+suffix, 'New-teacher-'+suffix
with httpx.Client(base_url=base, trust_env=False) as c:
    c.post('/platform/api/login', json={'username': 'admin', 'password': password})
    c.headers['X-Local-Token'] = c.get('/platform/api/me').json()['csrf']
    team = c.post('/platform/api/admin/teams', json={'name': '新团队'+suffix}).json()['id']
    assert c.post('/platform/api/admin/users', json={'username': username, 'display_name': '新教师', 'password': pw, 'role': 'member', 'team_id': team}).status_code == 200
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page(viewport={'width': 390, 'height': 844})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(base)
    page.locator('#loginForm').get_by_label('账号', exact=True).fill(username)
    page.locator('#loginForm').get_by_label('密码', exact=True).fill(pw)
    page.get_by_role('button', name='登录', exact=True).click()
    page.wait_for_function("document.documentElement.dataset.accountReady==='true'")
    assert page.url.rstrip('/') == base
    assert page.get_by_role('heading', name='空间', exact=True).is_visible()
    assert page.get_by_text('还没有可访问的空间').is_visible()
    assert not page.locator('#qaWorkspaceSection').is_visible()
    page.locator('[data-app-nav-target=qa]').click()
    assert page.locator('#qaWorkspaceSection').is_visible() and not page.locator('#mineWorkspaceSection').is_visible()
    page.locator('#appNavMine').click()
    page.get_by_role('button', name='新建空间', exact=True).click()
    page.get_by_label('空间名称', exact=True).fill('第一次备课')
    assert page.locator('#spaceKind option').count() == 1
    page.get_by_label('学科 · 学段 · 教材', exact=True).select_option('1')
    page.screenshot(path='test-results/integrated-onboarding-mobile.png', full_page=True, animations='disabled')
    page.get_by_role('button', name='创建并进入', exact=True).click()
    page.wait_for_url('**/workbench/*')
    page.wait_for_function("document.documentElement.dataset.accountReady==='true'")
    assert page.evaluate('MathBankPlatform.space.name') == '第一次备课'
    assert not errors, errors
    print(json.dumps({'onboarding': True, 'no_space_qa': True, 'create_first_space': True, 'errors': errors}))
    b.close()
