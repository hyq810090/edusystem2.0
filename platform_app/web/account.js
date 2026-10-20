/* 我的 workspace: own account, spaces, team accounts and textbooks.
   Runs inside the native workbench (window.MathBankPlatform is set) or on the
   no-space portal. It never touches question, draft or basket state. */
(() => {
    'use strict';
    const ctx = window.MathBankPlatform;
    const $ = id => document.getElementById(id);
    const ROLES = {super_admin: '超级管理员', team_admin: '团队管理员', member: '教师'};
    const KINDS = {personal: '个人空间', team: '团队共享空间', public: '平台公共空间'};
    const GRANTS = {owner: '管理', editor: '编辑', viewer: '查看'};
    let me = ctx, books = [], spaces = [], teams = [], users = [];

    function el(tag, text = '', className = '') {
        const node = document.createElement(tag);
        node.textContent = text;
        if (className) node.className = className;
        return node;
    }
    function icon(name) {
        const node = el('i', '', 'fa-solid ' + name);
        node.setAttribute('aria-hidden', 'true');
        return node;
    }
    function option(value, label) {
        const node = el('option', label);
        node.value = value;
        return node;
    }
    function button(label, onClick, className = 'mb-button mb-small') {
        const node = el('button', label, className);
        node.type = 'button';
        node.onclick = onClick;
        return node;
    }
    const isAdmin = () => ['super_admin', 'team_admin'].includes(me.role);
    const canManage = space => space.permission === 'owner' ||
        (space.kind === 'team' && space.team_id === me.team_id && me.role === 'team_admin');
    const spaceLine = space => `${space.stage} · ${space.subject} · ${space.textbook}`;

    async function api(path, method = 'GET', body) {
        const headers = {'X-Local-Token': me?.csrf || ''};
        if (body !== undefined) headers['Content-Type'] = 'application/json';
        const response = await fetch('/platform/api/' + path,
            {method, headers, body: body === undefined ? undefined : JSON.stringify(body)});
        const value = await response.json().catch(() => ({}));
        if (!response.ok) {
            if (response.status === 401 && ctx) location.assign('/');
            throw new Error(value.detail || '操作失败，请重试');
        }
        return value;
    }

    function notice(message) {
        $('accountNotice').textContent = message;
        $('accountNotice').hidden = !message;
    }
    function bindForm(id, action) {
        const form = $(id);
        form.addEventListener('submit', async event => {
            event.preventDefault();
            const submit = form.querySelector('button:not([type]), button[type=submit]');
            const error = form.querySelector('.mb-dialog-error') || (id === 'loginForm' ? $('loginError') : null);
            if (error) error.hidden = true; else notice('');
            submit.disabled = true;
            submit.setAttribute('aria-busy', 'true');
            try {
                await action(Object.fromEntries(new FormData(form)), form);
            } catch (err) {
                if (error) { error.textContent = err.message; error.hidden = false; } else notice(err.message);
            } finally {
                submit.disabled = false;
                submit.removeAttribute('aria-busy');
            }
        });
    }
    function openDialog(id) {
        const dialog = $(id);
        dialog.querySelectorAll('.mb-dialog-error').forEach(e => { e.hidden = true; });
        dialog.showModal();
        dialog.querySelector('h2').focus();
    }

    // ---------- Navigation ----------
    function setTab(tab) {
        const allowed = ['profile', 'spaces', ...(isAdmin() ? ['team'] : []), ...(me.role === 'super_admin' ? ['books'] : [])];
        const active = allowed.includes(tab) ? tab : 'profile';
        document.querySelectorAll('[data-mine-tab]').forEach(b => {
            b.hidden = !allowed.includes(b.dataset.mineTab);
            if (b.dataset.mineTab === active) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
        });
        document.querySelectorAll('[data-mine-panel]').forEach(p => { p.hidden = p.dataset.minePanel !== active; });
    }
    function openMine(tab) {
        window.selectWorkspace('mine', '我的');
        if (tab) setTab(tab);
    }
    function mountNavigation() {
        const item = el('button', '', 'app-nav-item');
        item.id = 'appNavMine';
        item.type = 'button';
        item.dataset.appNavTarget = 'mine';
        item.append(el('i', '', 'fa-regular fa-user'), el('span', '我的'));
        item.firstChild.setAttribute('aria-hidden', 'true');
        item.onclick = () => openMine();
        $('appNavPrimary').append(item);
        if (ctx) {
            const native = window.selectWorkspace;
            window.selectWorkspace = function (id, name) {
                $('mineWorkspaceSection').classList.add('hidden');
                if (id !== 'mine') return native(id, name);
                // Hide every native workspace container (sections and divs),
                // keeping their editing and basket state intact.
                document.documentElement.classList.remove('init-ws-dashboard', 'init-ws-paper');
                document.querySelectorAll('#appContentShell > main > [id$="WorkspaceSection"]').forEach(s => s.classList.add('hidden'));
                $('mineWorkspaceSection').classList.remove('hidden');
                window.PaperStore.activeWorkspace = 'mine';
                document.body.dataset.workspace = 'mine';
                window.setAppNavigationActive('mine');
                if ($('currentWorkspaceName')) $('currentWorkspaceName').textContent = '我的';
            };
            document.querySelector('#appNavQa span').textContent = 'Q&A';
        } else {
            window.selectWorkspace = id => {
                $('qaWorkspaceSection').classList.toggle('hidden', id !== 'qa');
                $('mineWorkspaceSection').classList.toggle('hidden', id !== 'mine');
                document.querySelectorAll('[data-app-nav-target]').forEach(b => {
                    if (b.dataset.appNavTarget === id) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
                });
            };
            $('appNavQa').onclick = () => window.selectWorkspace('qa');
        }
        $('mineHelp').onclick = () => window.selectWorkspace('qa', 'Q&A · 使用帮助');
        document.querySelectorAll('[data-mine-tab]').forEach(b => { b.onclick = () => setTab(b.dataset.mineTab); });
        document.querySelectorAll('[data-open-spaces]').forEach(b => { b.onclick = () => setTab('spaces'); });
        window.openMyWorkspace = openMine;
    }
    function go(spaceId) {
        if (window.navigatePlatformSpace) window.navigatePlatformSpace(spaceId);
        else location.assign('/workbench/' + spaceId);
    }

    // ---------- Profile ----------
    function renderProfile() {
        $('accountName').textContent = me.display_name;
        $('accountAvatar').textContent = Array.from(me.display_name)[0] || '我';
        $('accountIdentity').textContent = '账号：' + me.username;
        $('accountRole').textContent = ROLES[me.role] || me.role;
        $('accountTeam').textContent = me.team_name || '未分配团队';
        const space = ctx?.space;
        $('accountSpace').textContent = space ? `${space.name} · ${GRANTS[ctx.permission]}` : '尚未进入空间';
        $('accountSubject').textContent = space ? `${space.stage} · ${space.subject}` : '—';
        $('accountTextbook').textContent = space ? space.textbook : '—';
    }

    // ---------- Spaces ----------
    function spaceCard(space) {
        const current = ctx?.space.id === space.id;
        const card = el('article', '', 'mb-card mb-space-card');
        if (current) card.setAttribute('aria-current', 'true');
        const header = el('header');
        const text = el('div');
        text.append(el('h3', space.name), el('p', spaceLine(space)));
        header.append(el('span', Array.from(space.subject)[0] || '·', 'mb-emblem'), text);
        if (current) header.append(el('span', '当前', 'mb-badge'));
        const facts = el('div', '', 'mb-row');
        facts.append(el('span', KINDS[space.kind], 'mb-badge mb-muted'), el('span', GRANTS[space.permission], 'mb-badge mb-muted'));
        if (space.owner_id !== me.id && space.owner_name) facts.append(el('small', '创建者：' + space.owner_name));
        const actions = el('div', '', 'mb-row');
        const enter = button(current ? '回到工作台' : '进入', () => current ? window.selectWorkspace('dashboard', '工作台') : go(space.id),
            'mb-button mb-small mb-primary');
        actions.append(enter);
        if (canManage(space)) {
            actions.append(button('重命名', () => {
                const form = $('renameForm');
                form.elements.space_id.value = space.id;
                form.elements.name.value = space.name;
                openDialog('renameDialog');
            }));
            if (space.kind === 'team') actions.append(button('成员权限', () => openMembers(space)));
        }
        card.append(header, facts, actions);
        return card;
    }
    async function renderSpaces() {
        spaces = await api('spaces');
        const root = $('spaces');
        root.replaceChildren();
        if (!spaces.length) {
            root.append(el('div', '还没有可访问的空间。点击“新建空间”，选择学科、学段与教材后开始备课。', 'mb-empty'));
            return;
        }
        for (const kind of Object.keys(KINDS)) {
            const list = spaces.filter(s => s.kind === kind);
            if (!list.length) continue;
            const group = el('section', '', 'mb-space-group');
            const grid = el('div', '', 'mb-space-grid');
            grid.append(...list.map(spaceCard));
            group.append(el('h3', `${KINDS[kind]}（${list.length}）`), grid);
            root.append(group);
        }
    }
    async function openMembers(space) {
        $('memberSpaceName').textContent = `${space.name} · ${spaceLine(space)}`;
        const list = $('memberList');
        list.replaceChildren(el('div', '正在读取当前权限…', 'mb-list-row'));
        openDialog('memberDialog');
        const error = $('memberDialog').querySelector('.mb-dialog-error');
        try {
            const members = await api(`spaces/${space.id}/members`);
            list.replaceChildren();
            for (const member of members.filter(m => m.enabled)) {
                const row = el('div', '', 'mb-list-row');
                const info = el('div');
                info.append(el('strong', member.display_name), el('small', `${member.username} · ${ROLES[member.role]}`));
                row.append(info);
                if (member.permission === 'owner') {
                    row.append(el('span', '管理', 'mb-badge'));
                } else {
                    const select = el('select');
                    select.setAttribute('aria-label', member.display_name + '的权限');
                    select.append(option('viewer', '查看'), option('editor', '编辑'));
                    select.value = member.permission;
                    select.onchange = async () => {
                        select.disabled = true;
                        error.hidden = true;
                        try {
                            await api(`spaces/${space.id}/members`, 'POST', {user_id: member.id, permission: select.value});
                        } catch (err) {
                            error.textContent = err.message;
                            error.hidden = false;
                            select.value = member.permission;
                        } finally {
                            member.permission = select.value;
                            select.disabled = false;
                        }
                    };
                    row.append(select);
                }
                list.append(row);
            }
            if (!list.children.length) list.append(el('div', '团队中还没有其他可用账号。', 'mb-list-row'));
        } catch (err) {
            list.replaceChildren();
            error.textContent = err.message;
            error.hidden = false;
        }
    }
    function fillSpaceForm() {
        const kinds = me.role === 'super_admin' ? ['personal', 'team', 'public'] : me.role === 'team_admin' ? ['personal', 'team'] : ['personal'];
        $('spaceKind').replaceChildren(...kinds.map(k => option(k, KINDS[k])));
        const select = $('spaceBook');
        select.replaceChildren(option('', books.length ? '请选择' : '暂无可用教材，请联系超级管理员'));
        const groups = new Map();
        for (const book of books.filter(b => b.enabled)) {
            const key = `${book.stage} · ${book.subject}`;
            if (!groups.has(key)) groups.set(key, el('optgroup'));
            groups.get(key).label = key;
            groups.get(key).append(option(book.id, `${book.stage}${book.subject} · ${book.name}`));
        }
        select.append(...groups.values());
    }

    // ---------- Team accounts ----------
    function manageable(user) {
        if (me.role === 'super_admin') return true;
        return user.team_id === me.team_id && user.role === 'member';
    }
    function renderUsers() {
        const root = $('users');
        root.replaceChildren();
        for (const team of teams) {
            const members = users.filter(u => u.team_id === team.id);
            if (teams.length > 1) root.append(el('div', `${team.name}（${members.length}）`, 'mb-list-group'));
            for (const user of members) {
                const row = el('div', '', 'mb-list-row' + (user.enabled ? '' : ' mb-disabled'));
                const info = el('div');
                info.append(el('strong', user.display_name), el('small', user.username + (user.id === me.id ? ' · 当前账号' : '')));
                row.append(info, el('span', ROLES[user.role] || user.role, 'mb-badge mb-muted'));
                if (!user.enabled) row.append(el('span', '已停用', 'mb-badge mb-warn'));
                if (manageable(user)) row.append(button('编辑', () => openUser(user)));
                root.append(row);
            }
        }
        if (!users.length) root.append(el('div', '还没有账号。', 'mb-list-row'));
    }
    function openUser(user) {
        const form = $('userForm');
        form.reset();
        const editing = Boolean(user);
        const self = editing && user.id === me.id;
        form.elements.id.value = editing ? user.id : '';
        form.elements.display_name.value = editing ? user.display_name : '';
        form.elements.username.required = !editing;
        form.elements.password.required = !editing;
        form.elements.enabled.checked = editing ? Boolean(user.enabled) : true;
        form.querySelectorAll('[data-create-only]').forEach(n => { n.hidden = editing; });
        form.querySelectorAll('[data-edit-only]').forEach(n => { n.hidden = !editing || self; });
        form.querySelectorAll('[data-super-only]').forEach(n => {
            n.hidden = me.role !== 'super_admin' || self || user?.role === 'super_admin';
        });
        $('teamSelect').replaceChildren(...teams.map(t => option(t.id, t.name)));
        $('teamSelect').value = editing ? user.team_id : me.team_id;
        form.elements.role.value = editing && user.role !== 'super_admin' ? user.role : 'member';
        $('userDialogTitle').lastChild.textContent = editing ? '编辑账号' : '创建账号';
        $('userDialogHint').textContent = editing ? `${user.username} · ${ROLES[user.role]}` : '';
        $('userPasswordLabel').textContent = editing ? '重置密码' : '初始密码';
        $('userPasswordHint').textContent = editing ? '留空则不修改；重置后该账号需要重新登录' : '至少 10 个字符，请线下交给本人';
        openDialog('userDialog');
    }
    async function saveUser(body, form) {
        const superFields = !form.querySelector('[data-super-only]').hidden;
        if (!body.id) {
            const payload = {username: body.username, display_name: body.display_name, password: body.password};
            if (superFields) Object.assign(payload, {team_id: Number(body.team_id), role: body.role});
            await api('admin/users', 'POST', payload);
            notice('账号已创建。请将账号与初始密码交给本人。');
        } else {
            const payload = {display_name: body.display_name};
            if (body.password) payload.password = body.password;
            if (!form.querySelector('[data-edit-only]').hidden) payload.enabled = form.elements.enabled.checked;
            if (superFields) Object.assign(payload, {team_id: Number(body.team_id), role: body.role});
            await api('admin/users/' + body.id, 'PATCH', payload);
            notice('账号已更新。');
        }
        $('userDialog').close();
        await loadTeam();
    }
    async function loadTeam() {
        if (!isAdmin()) return;
        [teams, users] = await Promise.all([api('admin/teams'), api('admin/users')]);
        renderUsers();
        $('teamForm').hidden = me.role !== 'super_admin';
    }

    // ---------- Textbooks ----------
    function renderBooks() {
        const root = $('books');
        root.replaceChildren();
        let group = '';
        for (const book of books) {
            const key = `${book.subject} · ${book.stage}`;
            if (key !== group) root.append(el('div', group = key, 'mb-list-group'));
            const row = el('div', '', 'mb-list-row' + (book.enabled ? '' : ' mb-disabled'));
            const info = el('div');
            info.append(el('strong', book.name),
                el('small', `${book.volumes} 册 · ${book.chapters} 章 · 题型：${book.question_types.join('、') || '未设置'}`));
            row.append(info, el('span', book.spaces ? `${book.spaces} 个空间` : '未使用', 'mb-badge mb-muted'));
            if (!book.enabled) row.append(el('span', '已停用', 'mb-badge mb-warn'));
            row.append(button(book.enabled ? '停用' : '启用', async () => {
                try {
                    await api('admin/textbooks/' + book.id, 'PATCH', {enabled: !book.enabled});
                    await loadBooks();
                } catch (err) { notice(err.message); }
            }));
            root.append(row);
        }
    }
    async function loadBooks() {
        books = await api('textbooks');
        fillSpaceForm();
        if (me.role !== 'super_admin') return;
        renderBooks();
        $('bookTemplate').replaceChildren(option('', '空白模板'), ...books.map(b => option(b.id, `${b.stage}${b.subject} · ${b.name}`)));
    }
    async function loadTemplate() {
        const id = $('bookTemplate').value;
        const metadata = id ? (await api('admin/textbooks/' + id)).metadata : {curriculum: {}, question_types: [], grades: []};
        $('bookMetadata').value = JSON.stringify(metadata, null, 2);
    }

    // ---------- Boot ----------
    async function boot() {
        if (!ctx) {
            bindForm('loginForm', async body => { await api('login', 'POST', body); location.assign('/'); });
            try {
                me = await api('me');
            } catch (_) {
                $('login').hidden = false;
                return;
            }
            $('portalShell').hidden = false;
            $('portalTheme').onclick = () => {
                const dark = document.documentElement.classList.toggle('dark');
                try { localStorage.setItem('mb-platform:dark-mode', String(dark)); } catch (_) { /* private mode */ }
            };
        }
        mountNavigation();
        renderProfile();
        setTab('profile');
        $('logoutButton').onclick = async () => {
            try { await api('logout', 'POST', {}); location.assign('/'); } catch (err) { notice(err.message); }
        };
        document.querySelectorAll('#mineWorkspaceSection [data-close]').forEach(b => { b.onclick = () => b.closest('dialog').close(); });
        $('memberDialog').addEventListener('close', () => renderSpaces().catch(err => notice(err.message)));
        $('newSpace').onclick = () => { $('spaceForm').reset(); openDialog('spaceDialog'); };
        $('newUser').onclick = () => openUser(null);
        $('newBook').onclick = async () => {
            $('bookForm').reset();
            $('bookTemplate').value = books[0]?.id || '';
            await loadTemplate().catch(err => notice(err.message));
            openDialog('bookDialog');
        };
        $('bookTemplate').onchange = () => loadTemplate().catch(err => notice(err.message));
        bindForm('spaceForm', async body => {
            const created = await api('spaces', 'POST', {name: body.name, kind: body.kind, textbook_id: Number(body.textbook_id)});
            $('spaceDialog').close();
            window.dispatchEvent(new Event('platform-spaces-changed'));
            go(created.id);
        });
        bindForm('renameForm', async body => {
            await api('spaces/' + body.space_id, 'PATCH', {name: body.name});
            $('renameDialog').close();
            await renderSpaces();
            window.dispatchEvent(new Event('platform-spaces-changed'));
        });
        bindForm('passwordForm', async body => { await api('password', 'POST', body); location.assign('/'); });
        bindForm('userForm', saveUser);
        bindForm('teamForm', async (body, form) => {
            await api('admin/teams', 'POST', body);
            form.reset();
            await loadTeam();
            notice('团队已创建，可以为该团队创建管理员账号。');
        });
        bindForm('bookForm', async body => {
            let metadata;
            try { metadata = JSON.parse($('bookMetadata').value); } catch (_) { throw new Error('教材 JSON 格式有误'); }
            await api('admin/textbooks', 'POST', {stage: body.stage, subject: body.subject, name: body.name, metadata});
            $('bookDialog').close();
            await loadBooks();
            notice('教材已加入，可在新建空间时选择。');
        });
        try {
            await Promise.all([loadBooks(), loadTeam(), renderSpaces()]);
        } catch (err) {
            notice(err.message);
        }
        if (!ctx) openMine('spaces');
        else if (location.hash.startsWith('#mine')) openMine(location.hash.slice(6) || 'profile');
        document.documentElement.dataset.accountReady = 'true';
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => boot().catch(e => notice(e.message)), {once: true});
    else boot().catch(e => notice(e.message));
})();
