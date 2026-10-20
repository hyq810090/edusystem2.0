"""我的 workspace in a real browser: accounts, spaces, members, Q&A, dark and phone layouts."""
from pathlib import Path
import json
import os
import time

from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
base = os.environ.get('MATHBANK_TEST_URL', 'http://127.0.0.1:3012')
data = Path(os.environ.get('MATHBANK_TEST_DATA', root/'test-results/ui-data'))
out = root/'test-results'
password = (data/'initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：', 1)[1]
suffix = str(time.time_ns())[-10:]
username = 'ui_teacher_'+suffix
teacher_password = 'Browser-test-'+suffix
shared_name = '界面共享空间-'+suffix


def login(page, name, pw):
    page.locator('#loginForm').get_by_label('账号', exact=True).fill(name)
    page.locator('#loginForm').get_by_label('密码', exact=True).fill(pw)
    page.get_by_role('button', name='登录', exact=True).click()
    page.wait_for_function("document.documentElement.dataset.accountReady==='true'")


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(base)
    page.wait_for_load_state('networkidle')
    page.screenshot(path=str(out/'integrated-login.png'), full_page=True, animations='disabled')
    login(page, 'admin', password)
    # Opening 我的 never reloads the page or drops unsaved import input.
    page.locator('#appNavImport').click()
    source_title = page.locator('#importPaperTitle')
    source_title.fill('未提交的导入标题')
    original_url = page.url
    page.locator('#appNavMine').click()
    assert page.url == original_url
    page.get_by_role('heading', name='我的', exact=True).wait_for()
    page.screenshot(path=str(out/'integrated-my-account.png'), full_page=True, animations='disabled')
    page.locator('#appNavImport').click()
    assert source_title.input_value() == '未提交的导入标题'
    source_title.fill('')
    page.locator('#appNavMine').click()
    page.get_by_role('button', name='空间', exact=True).click()
    page.screenshot(path=str(out/'integrated-spaces.png'), full_page=True, animations='disabled')

    page.get_by_role('button', name='团队与账号', exact=True).click()
    page.get_by_role('button', name='创建账号', exact=True).click()
    form = page.locator('#userForm')
    form.get_by_label('账号', exact=True).fill(username)
    form.get_by_label('姓名', exact=True).fill('界面验收教师')
    form.get_by_label('初始密码', exact=True).fill(teacher_password)
    form.get_by_role('button', name='保存', exact=True).click()
    page.locator('#users .mb-list-row').filter(has_text=username).wait_for()
    page.screenshot(path=str(out/'integrated-team.png'), full_page=True, animations='disabled')

    page.get_by_role('button', name='空间', exact=True).click()
    page.get_by_role('button', name='新建空间', exact=True).click()
    page.get_by_label('空间名称', exact=True).fill(shared_name)
    page.get_by_label('空间类型', exact=True).select_option('team')
    page.get_by_label('学科 · 学段 · 教材', exact=True).select_option('1')
    page.get_by_role('button', name='创建并进入', exact=True).click()
    page.wait_for_url(lambda u: '/workbench/' in u and u != original_url)
    page.wait_for_function("document.documentElement.dataset.accountReady==='true'")
    assert page.locator('.mb-space-switch strong').inner_text() == shared_name
    page.locator('#appNavMine').click()
    page.get_by_role('button', name='空间', exact=True).click()
    card = page.locator('.mb-space-card').filter(has=page.get_by_role('heading', name=shared_name, exact=True))
    card.get_by_role('button', name='成员权限').click()
    member = page.get_by_label('界面验收教师的权限', exact=True)
    assert member.input_value() == 'viewer'
    with page.expect_response(lambda r: r.url.endswith('/members') and r.request.method == 'POST' and r.ok):
        member.select_option('editor')
    page.keyboard.press('Escape')
    page.locator('#memberDialog').wait_for(state='hidden')

    page.locator('#appNavQa').click()
    page.get_by_role('heading', name='Q&A · 使用帮助').wait_for()
    page.get_by_role('button', name='草稿与保存', exact=True).click()
    page.get_by_placeholder('搜索账号、空间、草稿、导入…').fill('浏览器')
    assert page.locator('.qa-question:visible').count() == 1
    page.locator('.qa-question:visible summary').click()
    assert '不会跨电脑' in page.locator('.qa-question:visible').inner_text()
    assert '启用学段' not in page.locator('#qaWorkspaceSection').inner_text()
    page.screenshot(path=str(out/'integrated-qa.png'), full_page=True, animations='disabled')
    page.locator('#qaResetFilters').click()
    assert page.locator('.qa-question:visible').count() == 31

    page.locator('#appNavMine').click()
    page.get_by_role('button', name='我的账号', exact=True).click()
    page.evaluate('toggleDarkMode()')
    page.wait_for_function("document.documentElement.classList.contains('dark')")
    page.screenshot(path=str(out/'integrated-my-dark.png'), full_page=True, animations='disabled')
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('#appNavMine').click()
    page.screenshot(path=str(out/'integrated-my-mobile.png'), full_page=True, animations='disabled')
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'mobile horizontal overflow'
    page.get_by_role('button', name='空间', exact=True).click()
    page.get_by_role('button', name='新建空间', exact=True).click()
    page.get_by_label('空间名称', exact=True).fill('取消的空间')
    page.keyboard.press('Escape')
    assert not page.locator('#spaceDialog').is_visible()
    page.locator('#appNavQa').click()
    page.get_by_role('heading', name='Q&A · 使用帮助').wait_for()
    page.screenshot(path=str(out/'integrated-qa-mobile.png'), full_page=True, animations='disabled')
    page.locator('#appNavMine').click()
    page.get_by_role('button', name='我的账号', exact=True).click()
    page.get_by_role('button', name='退出登录').click()
    page.locator('#loginForm').wait_for()
    assert 'dark' in page.locator('html').get_attribute('class')
    page.screenshot(path=str(out/'integrated-login-mobile.png'), full_page=True, animations='disabled')

    # A teacher sees no management tabs and holds the granted edit permission.
    teacher = browser.new_context(viewport={'width': 1440, 'height': 1000})
    tp = teacher.new_page()
    tp.on('pageerror', lambda e: errors.append(str(e)))
    tp.goto(base)
    login(tp, username, teacher_password)
    tp.locator('#appNavMine').click()
    assert not tp.get_by_role('button', name='团队与账号', exact=True).is_visible()
    assert not tp.get_by_role('button', name='教材', exact=True).is_visible()
    tp.get_by_role('button', name='空间', exact=True).click()
    shared = tp.locator('.mb-space-card').filter(has=tp.get_by_role('heading', name=shared_name, exact=True))
    assert shared.locator('.mb-badge', has_text='编辑').count() == 1
    assert shared.get_by_role('button', name='成员权限').count() == 0 and shared.get_by_role('button', name='重命名').count() == 0
    # Changing the password ends the session; the new password logs in.
    tp.get_by_role('button', name='我的账号', exact=True).click()
    pf = tp.locator('#passwordForm')
    pf.get_by_label('原密码', exact=True).fill(teacher_password)
    pf.get_by_label('新密码', exact=True).fill(teacher_password+'x')
    pf.get_by_role('button', name='修改并重新登录').click()
    tp.locator('#loginForm').wait_for()
    login(tp, username, teacher_password+'x')
    assert not errors, errors
    report = {'passed': True, 'qa_entries': 31, 'errors': errors,
              'checks': ['login', 'my workspace without navigation', 'unsaved import title retained', 'create account in dialog',
                         'create shared space', 'per-member permission saved', 'FAQ search/category/reset', 'dark mode',
                         '390px navigation and dialog', 'teacher permission UI', 'change password and login']}
    (out/'account-ui-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    browser.close()
