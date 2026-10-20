/* Paper tools added to the native paper and records workspaces:
   - 跨空间选题: pick questions from other spaces of the same stage and subject;
   - paper folders: a two-level folder pane in 试卷管理 (create, rename, delete,
     filter, drag papers in), plus the save folder in 试卷信息.
   Controls are placed inside the native toolbars with native classes; the
   native renderers rebuild those toolbars, so a MutationObserver re-attaches. */
(() => {
    'use strict';
    const ctx = window.MathBankPlatform;
    if (!ctx) return;
    const $ = id => document.getElementById(id);
    const space = ctx.space;
    const canEdit = ['owner', 'editor'].includes(ctx.permission);
    let folders = [], paperFolders = {}, sources = [];

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
    async function api(path, method = 'GET', body) {
        const response = await fetch('/platform/api/spaces/' + space.id + path, {
            method, headers: body === undefined ? {} : {'Content-Type': 'application/json'},
            body: body === undefined ? undefined : JSON.stringify(body)});
        const value = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(value.detail || '操作失败，请重试');
        return value;
    }
    function toast(message, type = 'info') {
        if (window.showToast) window.showToast(message, type);
    }
    function dialogShell(id, title, description, iconName) {
        const dialog = el('dialog', '', 'mb-dialog');
        dialog.id = id;
        dialog.setAttribute('aria-labelledby', id + 'Title');
        dialog.addEventListener('keydown', e => { if (['Tab', 'Escape', 'Esc'].includes(e.key)) e.stopPropagation(); });
        const heading = el('div', '', 'mb-dialog-heading');
        const text = el('div');
        const h2 = el('h2', title);
        h2.id = id + 'Title';
        h2.tabIndex = -1;
        h2.prepend(icon(iconName));
        text.append(h2, el('p', description));
        const close = el('button', '', 'mb-icon-button');
        close.type = 'button';
        close.setAttribute('aria-label', '关闭');
        close.append(icon('fa-xmark'));
        close.onclick = () => dialog.close();
        heading.append(text, close);
        const body = el('div', '', 'mb-dialog-body');
        dialog.append(heading, body);
        document.body.append(dialog);
        return {dialog, body, focus: () => h2.focus()};
    }

    // ---------- Folders: 试卷管理 keeps papers in a two-level folder tree ----------
    const PAPER_DRAG = 'application/x-mathbank-paper';
    const collapsed = new Set();
    let pane = null, editing = null;  // editing: {parent} creates a folder, {folder} renames one

    const byName = (a, b) => a.name.localeCompare(b.name, 'zh-CN');
    const paperFolderList = () => folders.filter(f => f.kind === 'paper');
    function childrenOf(id) {
        const known = new Set(paperFolderList().map(f => f.id));
        // A folder whose parent is gone is shown at the top level.
        return paperFolderList().filter(f => (known.has(f.parent_id) ? f.parent_id : null) === id).sort(byName);
    }
    // Depth-first rows; older installs may hold deeper trees, a visited set guards cycles.
    function treeRows(id = null, depth = 1, seen = new Set(), {open = false} = {}) {
        return childrenOf(id).filter(f => !seen.has(f.id) && seen.add(f.id)).flatMap(f =>
            [[f, depth], ...(open && collapsed.has(f.id) ? [] : treeRows(f.id, depth + 1, seen, {open}))]);
    }
    const subtree = id => new Set([id, ...treeRows(id).map(([f]) => f.id)]);
    const papersIn = ids => Object.values(paperFolders).filter(v => ids.has(v)).length;
    function folderPath(folder) {
        const names = [folder.name];
        const seen = new Set([folder.id]);
        for (let parent = folder.parent_id; parent && !seen.has(parent);) {
            seen.add(parent);
            const node = folders.find(f => f.id === parent);
            if (!node) break;
            names.unshift(node.name);
            parent = node.parent_id;
        }
        return names.join(' / ');
    }
    function paperFolderOptions(select) {
        const current = select.value;
        select.replaceChildren(option('0', '未归档'), ...treeRows().map(([f]) => option(f.id, folderPath(f))));
        select.value = [...select.options].some(o => o.value === current) ? current : '0';
    }
    // A move made while a load is in flight wins over that load's older mapping.
    let foldersVersion = 0;
    async function loadFolders() {
        const version = ++foldersVersion;
        const result = await api('/folders');
        if (version !== foldersVersion) return;
        folders = result.folders;
        paperFolders = result.papers;
        document.querySelectorAll('[data-folder-select]').forEach(paperFolderOptions);
        renderPane();
        decorateRecords();
    }
    const reloadPapers = () => window.openSavedPapersModal();

    async function movePaper(id, folderId) {
        if ((paperFolders[id] || null) === folderId) return;
        try {
            await api(`/papers/${id}/folder`, 'POST', {folder_id: folderId});
            foldersVersion++;
            paperFolders[id] = folderId;
            const folder = folders.find(f => f.id === folderId);
            toast(`已移到“${folder ? folderPath(folder) : '未归档'}”`, 'success');
            renderPane();
            if (ctx.folders.filter) reloadPapers(); else decorateRecords();
        } catch (err) {
            toast(err.message, 'error');
            decorateRecords();
        }
    }

    function confirmDialog(title, message, action) {
        const shell = dialogShell('folderConfirm', title, message, 'fa-folder-minus');
        const actions = el('div', '', 'mb-actions');
        const cancel = el('button', '取消', 'mb-button');
        const ok = el('button', action, 'mb-button mb-danger');
        cancel.type = ok.type = 'button';
        actions.append(cancel, ok);
        shell.body.append(actions);
        return new Promise(resolve => {
            let answer = false;
            cancel.onclick = () => shell.dialog.close();
            ok.onclick = () => { answer = true; shell.dialog.close(); };
            shell.dialog.addEventListener('close', () => { shell.dialog.remove(); resolve(answer); }, {once: true});
            shell.dialog.showModal();
            shell.focus();
        });
    }
    async function removeFolder(folder) {
        const ids = subtree(folder.id);
        const papers = papersIn(ids);
        const parent = folders.find(f => f.id === folder.parent_id);
        const effects = [ids.size > 1 ? `其下 ${ids.size - 1} 个子目录一并删除` : '',
            papers ? `${papers} 份试卷移到“${parent ? parent.name : '未归档'}”` : '目录中没有试卷'];
        if (!await confirmDialog(`删除目录“${folder.name}”？`, effects.filter(Boolean).join('，') + '。试卷本身不会删除。', '删除目录')) return;
        try {
            await api(`/folders/${folder.id}`, 'DELETE');
            if (ids.has(Number(ctx.folders.filter))) ctx.folders.filter = '';
            await loadFolders();
            reloadPapers();
            toast('目录已删除', 'success');
        } catch (err) { toast(err.message, 'error'); }
    }

    function mountRecords() {
        const panel = document.querySelector('#recordsWorkspaceSection .records-list-panel');
        if (!panel || pane) return;
        pane = el('aside', '', 'mb-folder-pane');
        pane.setAttribute('aria-label', '试卷目录');
        panel.classList.add('mb-folder-layout');
        panel.prepend(pane);
        renderPane();
    }
    function iconButton(name, label, onclick) {
        const button = el('button', '', 'mb-folder-icon');
        button.type = 'button';
        button.title = label;
        button.setAttribute('aria-label', label);
        button.append(icon(name));
        button.onclick = onclick;
        return button;
    }
    function startEdit(state) {
        editing = state;
        if (state.parent) collapsed.delete(state.parent);
        renderPane();
    }
    function editorRow(depth, initial, submit) {
        const form = el('form', '', 'mb-folder-item mb-folder-edit');
        form.style.setProperty('--depth', depth);
        const input = el('input');
        input.value = initial;
        input.maxLength = 80;
        input.required = true;
        input.placeholder = depth > 1 ? '子目录名称' : '目录名称';
        input.setAttribute('aria-label', input.placeholder);
        const save = el('button', '保存', 'mb-button mb-small mb-primary');
        const cancel = el('button', '取消', 'mb-button mb-small');
        cancel.type = 'button';
        cancel.onclick = () => { editing = null; renderPane(); };
        input.onkeydown = e => {
            if (e.key !== 'Escape') return;
            e.preventDefault();
            e.stopPropagation();
            cancel.onclick();
        };
        form.onsubmit = async e => {
            e.preventDefault();
            save.disabled = true;
            try {
                await submit(input.value.trim());
                editing = null;
                await loadFolders();
            } catch (err) {
                toast(err.message, 'error');
                save.disabled = false;
                input.focus();
            }
        };
        form.append(input, save, cancel);
        requestAnimationFrame(() => { input.focus(); input.select(); });
        return form;
    }
    function folderRow({value, name, iconName, count = null, depth = 0, folder = null}) {
        const item = el('div', '', 'mb-folder-item');
        item.setAttribute('role', 'listitem');
        item.style.setProperty('--depth', depth);
        if (folder && childrenOf(folder.id).length) {
            const open = !collapsed.has(folder.id);
            const toggle = iconButton(open ? 'fa-chevron-down' : 'fa-chevron-right', `${open ? '收起' : '展开'}“${name}”`, () => {
                collapsed[open ? 'add' : 'delete'](folder.id);
                renderPane();
            });
            toggle.classList.add('mb-folder-toggle');
            toggle.setAttribute('aria-expanded', String(open));
            item.append(toggle);
        } else {
            item.append(el('span', '', 'mb-folder-toggle'));
        }
        const pick = el('button', '', 'mb-folder-pick');
        pick.type = 'button';
        if (ctx.folders.filter === value) pick.setAttribute('aria-current', 'true');
        pick.append(icon(iconName), el('span', name, 'mb-folder-name'));
        if (count !== null) pick.append(el('small', String(count)));
        pick.onclick = () => {
            ctx.folders.filter = value;
            renderPane();
            reloadPapers();
        };
        item.append(pick);
        if (folder && canEdit) {
            const actions = el('span', '', 'mb-folder-actions');
            if (depth === 1) actions.append(iconButton('fa-folder-plus', `在“${name}”下新建子目录`, () => startEdit({parent: folder.id})));
            actions.append(iconButton('fa-pen', `重命名“${name}”`, () => startEdit({folder: folder.id})),
                iconButton('fa-trash-can', `删除“${name}”`, () => removeFolder(folder)));
            item.append(actions);
        }
        if (canEdit && value !== '') {
            item.addEventListener('dragover', e => {
                if (!e.dataTransfer.types.includes(PAPER_DRAG)) return;
                e.preventDefault();
                item.classList.add('mb-drop');
            });
            item.addEventListener('dragleave', () => item.classList.remove('mb-drop'));
            item.addEventListener('drop', e => {
                e.preventDefault();
                item.classList.remove('mb-drop');
                movePaper(Number(e.dataTransfer.getData(PAPER_DRAG)), Number(value) || null);
            });
        }
        return item;
    }
    function renderPane() {
        if (!pane) return;
        const head = el('div', '', 'mb-folder-pane-head');
        head.append(el('h3', '试卷目录'));
        if (canEdit) head.append(iconButton('fa-folder-plus', '新建一级目录', () => startEdit({parent: null})));
        const list = el('div', '', 'mb-folder-list');
        list.setAttribute('role', 'list');
        list.append(folderRow({value: '', name: '全部试卷', iconName: 'fa-layer-group'}),
            folderRow({value: '0', name: '未归档', iconName: 'fa-inbox'}));
        for (const [folder, depth] of treeRows(null, 1, new Set(), {open: true})) {
            list.append(editing?.folder === folder.id
                ? editorRow(depth, folder.name, name => api(`/folders/${folder.id}`, 'PATCH', {name}))
                : folderRow({value: String(folder.id), name: folder.name, iconName: 'fa-folder',
                    count: papersIn(subtree(folder.id)), depth, folder}));
            if (editing?.parent === folder.id) {
                list.append(editorRow(depth + 1, '', name => api('/folders', 'POST', {name, parent_id: folder.id, kind: 'paper'})));
            }
        }
        if (editing && editing.parent === null) {
            list.append(editorRow(1, '', name => api('/folders', 'POST', {name, parent_id: null, kind: 'paper'})));
        }
        pane.replaceChildren(head, list);
        if (canEdit) pane.append(el('p', '目录最多两级；把试卷卡片拖到目录上即可归档。', 'mb-folder-hint'));
    }

    function decorateRecords() {
        for (const card of document.querySelectorAll('#savedPapersListContainer .saved-paper-card')) {
            const id = Number(/loadSavedPaper\((\d+)\)/.exec(card.querySelector('.saved-paper-load-action')?.getAttribute('onclick') || '')?.[1]);
            const meta = card.querySelector('.saved-paper-card-meta');
            if (!id || !meta) continue;
            card.querySelector('.mb-card-folder')?.remove();
            const holder = el('span', '', 'mb-card-folder');
            holder.append(icon('fa-folder'));
            const folder = folders.find(f => f.id === paperFolders[id]);
            if (canEdit) {
                const select = el('select', '', 'glass-select');
                select.dataset.folderSelect = 'move';
                select.setAttribute('aria-label', '试卷所在目录');
                paperFolderOptions(select);
                select.value = String(folder?.id || 0);
                select.onchange = () => movePaper(id, Number(select.value) || null);
                holder.append(select);
                card.draggable = true;
                card.ondragstart = e => {
                    e.dataTransfer.setData(PAPER_DRAG, String(id));
                    e.dataTransfer.effectAllowed = 'move';
                };
            } else {
                holder.append(el('span', folder ? folderPath(folder) : '未归档'));
            }
            meta.append(holder);
        }
    }

    // Paper workspace: archive folder in 试卷信息, cross-space selection button.
    function mountPaperControls() {
        const grid = document.querySelector('#paperMetaDetails .workspace-filter-grid');
        if (canEdit && grid && !$('platformSaveFolder')) {
            const label = el('label', '归档目录');
            const select = el('select', '', 'glass-select');
            select.id = 'platformSaveFolder';
            select.setAttribute('aria-label', '归档目录');
            select.dataset.folderSelect = 'save';
            paperFolderOptions(select);
            select.value = String(ctx.folders.save || 0);
            select.onchange = () => { ctx.folders.save = Number(select.value) || null; };
            label.append(select);
            grid.append(label);
        }
        const bar = document.querySelector('#paperFilterSection .paper-command-bar');
        if (canEdit && sources.length && bar && !$('platformCrossSpaceBtn')) {
            const open = el('button', '', 'glass-btn');
            open.type = 'button';
            open.id = 'platformCrossSpaceBtn';
            open.append(icon('fa-share-nodes'), document.createTextNode(' 跨空间选题'));
            open.onclick = openCrossSpace;
            bar.append(open);
        }
    }

    // ---------- Cross-space selection ----------
    let picker = null;
    function openCrossSpace() {
        if (!picker) picker = buildCrossSpace();
        picker.dialog.showModal();
        picker.focus();
        picker.load(1);
    }
    function buildCrossSpace() {
        const shell = dialogShell('crossSpacePicker', '跨空间选题',
            `从其他${space.stage}${space.subject}空间挑选题目。加入试卷时，题目和题图会复制到本空间并记录来源，之后两边独立修改。`, 'fa-share-nodes');
        shell.dialog.classList.add('mb-wide');
        const metadata = window.systemMetadata || {};
        const controls = el('form', '', 'mb-xs-controls');
        const source = el('select');
        source.setAttribute('aria-label', '来源空间');
        source.append(...sources.map(s => option(s.id, `${s.name} · ${s.textbook}`)));
        const keyword = el('input');
        keyword.type = 'search';
        keyword.placeholder = '搜索题干、来源或标签';
        keyword.setAttribute('aria-label', '搜索题目');
        const type = el('select');
        type.setAttribute('aria-label', '题型');
        type.append(option('', '全部题型'), ...(metadata.question_types || []).map(t => option(t.value, t.label)));
        const difficulty = el('select');
        difficulty.setAttribute('aria-label', '难度');
        difficulty.append(option('', '全部难度'), ...(metadata.difficulties || []).map(d => option(d.value, d.label)));
        controls.append(source, keyword, type, difficulty);
        const status = el('p', '', 'mb-status');
        status.setAttribute('role', 'status');
        const list = el('div', '', 'mb-xs-list');
        const pager = el('div', '', 'mb-xs-pager');
        const previous = el('button', '上一页', 'mb-button mb-small');
        const next = el('button', '下一页', 'mb-button mb-small');
        const pageText = el('span', '', 'mb-status');
        previous.type = next.type = 'button';
        pager.append(previous, pageText, next);
        shell.body.append(controls, status, list, pager);
        let page = 1, sequence = 0, timer = null;

        function item(question, sourceInfo) {
            const card = el('article', '', 'mb-xs-item');
            const header = el('header');
            header.append(el('span', `原题 #${question.seq_num ?? question.id}`), el('span', sourceInfo.textbook, 'mb-badge mb-muted'));
            const action = el('button', '', 'mb-button mb-small mb-primary');
            action.type = 'button';
            const mark = copiedId => {
                header.querySelector('.mb-copied')?.remove();
                if (copiedId) header.insertBefore(el('span', '已复制到本空间', 'mb-badge mb-copied'), action);
                const inCart = copiedId && window.isInCart?.(copiedId);
                action.textContent = inCart ? '已在试卷中' : copiedId ? '加入试卷' : '复制到本空间并加入试卷';
                action.disabled = Boolean(inCart);
            };
            action.onclick = async () => {
                action.disabled = true;
                action.setAttribute('aria-busy', 'true');
                try {
                    const copied = await api('/reference', 'POST', {source_space: sourceInfo.id, question_id: question.id});
                    window.PaperStore.questionsMap[copied.id] = copied;
                    window.addToCart(copied.id);
                    question.referenced_as = copied.id;
                } catch (err) {
                    toast(err.message, 'error');
                } finally {
                    action.removeAttribute('aria-busy');
                    mark(question.referenced_as);
                }
            };
            header.append(action);
            mark(question.referenced_as);
            const preview = el('div', '', 'mb-xs-preview');
            window.renderBankQuestionPreview(preview, question);
            card.append(header, preview);
            return card;
        }
        async function load(target) {
            const current = ++sequence;
            page = target;
            status.textContent = '正在查询…';
            previous.disabled = next.disabled = true;
            const params = new URLSearchParams({source: source.value, q: keyword.value.trim(),
                question_type: type.value, difficulty: difficulty.value, page});
            try {
                const result = await api('/search?' + params);
                if (current !== sequence) return;
                list.replaceChildren(...result.items.map(q => item(q, result.source)));
                status.textContent = result.total ? `共 ${result.total} 题` : '没有符合条件的题目。';
                pageText.textContent = `第 ${result.page} / ${Math.max(1, result.total_pages)} 页`;
                previous.disabled = result.page <= 1;
                next.disabled = result.page >= result.total_pages;
            } catch (err) {
                if (current === sequence) status.textContent = err.message;
            }
        }
        controls.onsubmit = e => { e.preventDefault(); load(1); };
        source.onchange = type.onchange = difficulty.onchange = () => load(1);
        keyword.oninput = () => { clearTimeout(timer); timer = setTimeout(() => load(1), 300); };
        previous.onclick = () => load(page - 1);
        next.onclick = () => load(page + 1);
        return {...shell, load};
    }

    // ---------- Boot ----------
    function observe(selector, callback) {
        const target = document.querySelector(selector);
        if (target) new MutationObserver(callback).observe(target, {childList: true});
    }
    let refreshTimer = null;
    function recordsRendered() {
        decorateRecords();
        // Papers saved since the last load need their current folder.
        clearTimeout(refreshTimer);
        refreshTimer = setTimeout(() => loadFolders().catch(err => toast(err.message, 'error')), 150);
    }
    async function start() {
        observe('#paperFilterSection', mountPaperControls);
        observe('#savedPapersListContainer', recordsRendered);
        mountRecords();
        try {
            [sources] = await Promise.all([canEdit ? api('/sources') : [], loadFolders()]);
        } catch (err) {
            toast(err.message, 'error');
        }
        mountPaperControls();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true}); else start();
})();
