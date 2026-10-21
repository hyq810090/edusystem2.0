/* 讲义 workspace: textbook-style handouts of the current space.
   - library: lesson folders (two levels, as in 试卷管理) beside the handout list;
   - editor: an outline, the page drawn with the PDF template's structure and
     per-section numbering (each kind counts on its own), and an inspector for the selected item.
   Questions are inserted as snapshots; edits autosave against the server
   revision so two editors never overwrite each other. Exports (PDF/Word,
   student/teacher) are made on the server from the saved document. */
(() => {
    'use strict';
    const ctx = window.MathBankPlatform;
    if (!ctx) return;
    const $ = id => document.getElementById(id);
    const space = ctx.space;
    const canEdit = ['owner', 'editor'].includes(ctx.permission);
    const BASE = '/platform/api/spaces/' + space.id;
    const LIST_ITEM = /^\s*(?:([-*•])|(\d{1,2})[.、])\s+(.*)$/;
    const KINDS = {
        text: ['正文', 'fa-align-left'], proof: ['证明', 'fa-square-check'], summary: ['本节小结', 'fa-list-check'],
        definition: ['定义', 'fa-book'], theorem: ['定理', 'fa-landmark'], property: ['性质', 'fa-shapes'],
        formula: ['公式', 'fa-square-root-variable'], corollary: ['推论', 'fa-arrow-turn-down'],
        note: ['注', 'fa-circle-info'], method: ['方法', 'fa-lightbulb'], warning: ['易错', 'fa-triangle-exclamation'],
        example: ['例题', 'fa-pen-to-square'], exercises: ['练习', 'fa-list-ol'], figure: ['插图', 'fa-image'],
    };
    const BOXES = ['definition', 'theorem', 'property', 'formula', 'corollary'];
    const NOTES = ['note', 'method', 'warning'];
    const TEXTUAL = ['text', 'proof', 'summary', ...BOXES, ...NOTES];
    const NUMBERED = new Set([...BOXES, 'example', 'figure']);
    const PRINTED = {example: '例', figure: '图'};
    const MENU = [['正文', ['text', 'proof', 'summary']], ['概念', BOXES], ['提示', NOTES], ['题目', ['example', 'exercises']], ['插图', ['figure']]];
    const FIGURE_SIZES = {small: '小', medium: '中', large: '大'};
    const HANDOUT_DRAG = 'application/x-mathbank-handout';
    const OUTLINE_DRAG = 'application/x-mathbank-handout-item';

    let section = null, pane = null, listBox = null, searchBox = null;
    let folders = [], handouts = [], filter = '', loaded = false;
    const collapsed = new Set();
    let editingFolder = null;
    // The open handout: {id, title, revision, folder_id, document}.
    let doc = null, selection = {type: 'heading'}, view = 'teacher', changes = {changed: [], deleted: []};
    let dirty = false, saveTimer = null, saving = null, conflict = false;
    const ui = {};

    // ---------- Helpers ----------
    function el(tag, text = '', className = '') {
        const node = document.createElement(tag);
        if (text) node.textContent = text;
        if (className) node.className = className;
        return node;
    }
    function icon(name, style = 'fa-solid') {
        const node = el('i', '', style + ' ' + name);
        node.setAttribute('aria-hidden', 'true');
        return node;
    }
    function button(text, className, onclick, iconName) {
        const node = el('button', '', className);
        node.type = 'button';
        if (iconName) node.append(icon(iconName));
        if (text) node.append(document.createTextNode(text));
        if (onclick) node.onclick = onclick;
        return node;
    }
    function iconButton(name, label, onclick, className = 'mb-folder-icon') {
        const node = button('', className, onclick, name);
        node.title = label;
        node.setAttribute('aria-label', label);
        return node;
    }
    function option(value, label) {
        const node = el('option', label);
        node.value = value;
        return node;
    }
    function field(label, control, hint) {
        const node = el('label', '', 'mb-ho-field');
        node.append(el('span', label), control);
        if (hint) node.append(el('small', hint));
        return node;
    }
    function input(value, placeholder, oninput, maxLength = 120) {
        const node = el('input');
        node.value = value || '';
        node.placeholder = placeholder;
        node.maxLength = maxLength;
        node.disabled = !canEdit;
        node.oninput = () => oninput(node.value);
        return node;
    }
    function textarea(value, placeholder, oninput, rows = 8) {
        const node = el('textarea');
        node.value = value || '';
        node.placeholder = placeholder;
        node.rows = rows;
        node.maxLength = 20000;
        node.disabled = !canEdit;
        node.oninput = () => oninput(node.value);
        return node;
    }
    async function api(path, method = 'GET', body) {
        const response = await fetch(BASE + path, {
            method, headers: body === undefined ? {} : {'Content-Type': 'application/json'},
            body: body === undefined ? undefined : JSON.stringify(body)});
        const value = await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = new Error(value.detail || '操作失败，请重试');
            error.status = response.status;
            throw error;
        }
        return value;
    }
    function toast(message, type = 'info') {
        if (window.showToast) window.showToast(message, type);
    }
    const newId = prefix => prefix + Array.from(crypto.getRandomValues(new Uint8Array(5)), b => b.toString(16).padStart(2, '0')).join('');
    function when(seconds) {
        const date = new Date(seconds * 1000);
        const today = new Date();
        const time = date.toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'});
        if (date.toDateString() === today.toDateString()) return '今天 ' + time;
        return date.toLocaleDateString('zh-CN', {month: 'numeric', day: 'numeric'}) + ' ' + time;
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
        const close = iconButton('fa-xmark', '关闭', () => dialog.close(), 'mb-icon-button');
        heading.append(text, close);
        const body = el('div', '', 'mb-dialog-body');
        dialog.append(heading, body);
        document.body.append(dialog);
        dialog.addEventListener('close', () => dialog.remove(), {once: true});
        return {dialog, body, focus: () => h2.focus()};
    }
    function confirmDialog(title, message, action, danger = true) {
        const shell = dialogShell('handoutConfirm', title, message, danger ? 'fa-trash-can' : 'fa-circle-question');
        const actions = el('div', '', 'mb-actions');
        let answer = false;
        actions.append(button('取消', 'mb-button', () => shell.dialog.close()),
            button(action, 'mb-button ' + (danger ? 'mb-danger' : 'mb-primary'), () => { answer = true; shell.dialog.close(); }));
        shell.body.append(actions);
        return new Promise(resolve => {
            shell.dialog.addEventListener('close', () => resolve(answer), {once: true});
            shell.dialog.showModal();
            shell.focus();
        });
    }

    // ---------- Rendering text the way the template prints it ----------
    function inlineHtml(text) {
        const parse = window.parseMarkdownWithMath;
        return String(text || '').split('**').map((part, index) => {
            let html = parse ? parse(part) : part.replace(/[&<>]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'})[c]);
            // ____ prints as an answer line (\fillin in the PDF); only outside tags.
            html = html.replace(/(^|>)([^<]*)/g, (all, close, chunk) => close + chunk.replace(/_{3,}/g, '<span class="mb-ho-blank"></span>'));
            return index % 2 ? '<strong>' + html + '</strong>' : html;
        }).join('');
    }
    function fill(node, text) {
        node.innerHTML = inlineHtml(text);
        return node;
    }
    // Blank-line separated paragraphs; '- ' and '1. ' lines become lists (as handout_export.lists).
    function prose(text, className = 'mb-ho-prose') {
        const box = el('div', '', className);
        for (const chunk of String(text || '').replace(/\r\n/g, '\n').trim().split(/\n\s*\n/)) {
            let list = null, lines = [];
            const flush = () => {
                if (lines.join('').trim()) box.append(fill(el('p'), lines.join(' ').trim()));
                lines = [];
            };
            for (const line of chunk.split('\n')) {
                const match = LIST_ITEM.exec(line);
                if (match) {
                    flush();
                    const tag = match[1] ? 'UL' : 'OL';
                    if (!list || list.tagName !== tag) box.append(list = el(tag.toLowerCase()));
                    list.append(fill(el('li'), match[3]));
                } else {
                    list = null;
                    lines.push(line.trim());
                }
            }
            flush();
        }
        return box;
    }
    // A label runs into the first paragraph, as \quad after the label in the PDF.
    function labelled(label, body) {
        const first = body.firstElementChild;
        if (first && first.tagName === 'P') first.prepend(label);
        else body.prepend(label);
        return body;
    }
    function label(text, title, className = 'mb-ho-label') {
        const node = el('span', '', className);
        node.append(el('b', text));
        if (title) node.append(el('strong', title));
        return node;
    }
    function renderMath(node) {
        try {
            // Questions and answers are rendered by the engine's own previews.
            window.renderMathInElement?.(node, {delimiters: [
                {left: '$$', right: '$$', display: true}, {left: '$', right: '$', display: false},
                {left: '\\(', right: '\\)', display: false}, {left: '\\[', right: '\\]', display: true}],
            ignoredClasses: ['katex', 'mb-ho-question', 'mb-ho-answer'], throwOnError: false});
        } catch (err) { console.error(err); }
    }
    function questionPreview(question) {
        const box = el('div', '', 'mb-ho-question');
        try {
            window.renderBankQuestionPreview(box, question);
        } catch (err) {
            box.textContent = question.content || '';
        }
        return box;
    }
    function answerPreview(text) {
        const box = el('div', '', 'mb-ho-answer');
        if (window.renderQuestionPreviewContent) window.renderQuestionPreviewContent(box, text);
        else box.textContent = text;
        return box;
    }
    const shortText = (text, size = 18) => {
        const plain = String(text || '').replace(/!\[[^\]]*\]\([^)]*\)/g, '［图］').replace(/\\begin\{choices\}[\s\S]*/, '')
            .replace(/\$+([^$]*)\$+/g, '$1').replace(/\*\*/g, '').replace(/\s+/g, ' ').trim();
        return plain.length > size ? plain.slice(0, size) + '…' : plain;
    };

    // ---------- Workspace shell and navigation ----------
    function mount() {
        const main = document.querySelector('#appContentShell > main');
        if (!main || $('handoutWorkspaceSection')) return false;
        section = el('section', '', 'mb-handouts hidden');
        section.id = 'handoutWorkspaceSection';
        section.setAttribute('aria-label', '讲义');
        ui.library = el('div', '', 'mb-ho-library');
        pane = el('aside', '', 'mb-folder-pane');
        pane.setAttribute('aria-label', '讲义目录');
        const panel = el('div', '', 'mb-ho-list-panel');
        const head = el('header', '', 'mb-ho-list-head');
        const titles = el('div');
        titles.append(el('h1', '讲义'), el('p', `${space.stage}${space.subject} · ${space.textbook || ''}　教材式讲义，统一版式，导出 PDF 与 Word。`));
        searchBox = el('input');
        searchBox.type = 'search';
        searchBox.placeholder = '搜索讲义';
        searchBox.setAttribute('aria-label', '搜索讲义');
        searchBox.oninput = renderList;
        head.append(titles, searchBox);
        if (canEdit) head.append(button('新建讲义', 'mb-button mb-primary', createHandout, 'fa-plus'));
        listBox = el('div', '', 'mb-ho-list');
        listBox.setAttribute('role', 'list');
        panel.append(head, listBox);
        ui.library.append(pane, panel);
        ui.editor = el('div', '', 'mb-ho-editor');
        ui.editor.hidden = true;
        section.append(ui.library, ui.editor);
        main.append(section);

        const item = el('button', '', 'app-nav-item');
        item.id = 'appNavHandouts';
        item.type = 'button';
        item.dataset.appNavTarget = 'handouts';
        item.append(icon('fa-book-open'), el('span', '讲义'));
        item.onclick = () => window.selectWorkspace('handouts', '讲义');
        ($('appNavRecords') || $('appNavPaper'))?.after(item);

        const previous = window.selectWorkspace;
        window.selectWorkspace = function (id, name) {
            if (id !== 'handouts') {
                section.classList.add('hidden');
                if (dirty) flush();
                return previous(id, name);
            }
            document.documentElement.classList.remove('init-ws-dashboard', 'init-ws-paper');
            document.querySelectorAll('#appContentShell > main > [id$="WorkspaceSection"]').forEach(s => s.classList.add('hidden'));
            section.classList.remove('hidden');
            if (window.PaperStore) window.PaperStore.activeWorkspace = 'handouts';
            document.body.dataset.workspace = 'handouts';
            window.setAppNavigationActive?.('handouts');
            if ($('currentWorkspaceName')) $('currentWorkspaceName').textContent = '讲义';
            if (!loaded) loadLibrary();
        };
        // Leaving the space waits for the last edit to be saved.
        const navigate = window.navigatePlatformSpace;
        if (navigate) window.navigatePlatformSpace = async id => { await flush(); navigate(id); };
        window.addEventListener('beforeunload', e => {
            if (dirty || saving || conflict) { flush(); e.preventDefault(); e.returnValue = ''; }
        });
        document.addEventListener('click', e => {
            if (ui.exportWrap && !ui.exportWrap.contains(e.target)) ui.exportMenu.hidden = true;
        });
        window.openHandoutWorkspace = () => window.selectWorkspace('handouts', '讲义');
        return true;
    }

    // ---------- Library: lesson folders and the handout list ----------
    const byName = (a, b) => a.name.localeCompare(b.name, 'zh-CN');
    const lessonFolders = () => folders.filter(f => f.kind === 'lesson');
    function childrenOf(id) {
        const known = new Set(lessonFolders().map(f => f.id));
        return lessonFolders().filter(f => (known.has(f.parent_id) ? f.parent_id : null) === id).sort(byName);
    }
    function treeRows(id = null, depth = 1, seen = new Set(), open = false) {
        return childrenOf(id).filter(f => !seen.has(f.id) && seen.add(f.id)).flatMap(f =>
            [[f, depth], ...(open && collapsed.has(f.id) ? [] : treeRows(f.id, depth + 1, seen, open))]);
    }
    const subtree = id => new Set([id, ...treeRows(id).map(([f]) => f.id)]);
    function folderPath(id) {
        const names = [];
        const seen = new Set();
        for (let current = folders.find(f => f.id === id); current && !seen.has(current.id);
            current = folders.find(f => f.id === current.parent_id)) {
            seen.add(current.id);
            names.unshift(current.name);
        }
        return names.join(' / ') || '未归档';
    }
    function folderOptions(select, current) {
        select.replaceChildren(option('0', '未归档'), ...treeRows().map(([f]) => option(f.id, folderPath(f.id))));
        select.value = String(current || 0);
    }
    async function loadLibrary() {
        loaded = true;
        listBox.replaceChildren(el('p', '正在载入…', 'mb-status'));
        try {
            const [folderData, list] = await Promise.all([api('/folders'), api('/handouts')]);
            folders = folderData.folders;
            handouts = list;
        } catch (err) {
            loaded = false;
            listBox.replaceChildren(el('p', err.message, 'mb-empty'));
            return;
        }
        renderPane();
        renderList();
    }
    function inFilter(handout) {
        if (filter === '') return true;
        if (filter === '0') return !handout.folder_id;
        return subtree(Number(filter)).has(handout.folder_id);
    }
    function renderPane() {
        const head = el('div', '', 'mb-folder-pane-head');
        head.append(el('h3', '讲义目录'));
        if (canEdit) head.append(iconButton('fa-folder-plus', '新建一级目录', () => startFolderEdit({parent: null})));
        const list = el('div', '', 'mb-folder-list');
        list.setAttribute('role', 'list');
        list.append(folderRow({value: '', name: '全部讲义', iconName: 'fa-layer-group', count: handouts.length}),
            folderRow({value: '0', name: '未归档', iconName: 'fa-inbox', count: handouts.filter(h => !h.folder_id).length}));
        for (const [folder, depth] of treeRows(null, 1, new Set(), true)) {
            const ids = subtree(folder.id);
            list.append(editingFolder?.folder === folder.id
                ? folderEditor(depth, folder.name, name => api(`/folders/${folder.id}`, 'PATCH', {name}))
                : folderRow({value: String(folder.id), name: folder.name, iconName: 'fa-folder', depth, folder,
                    count: handouts.filter(h => ids.has(h.folder_id)).length}));
            if (editingFolder?.parent === folder.id) {
                list.append(folderEditor(depth + 1, '', name => api('/folders', 'POST', {name, parent_id: folder.id, kind: 'lesson'})));
            }
        }
        if (editingFolder && editingFolder.parent === null) {
            list.append(folderEditor(1, '', name => api('/folders', 'POST', {name, parent_id: null, kind: 'lesson'})));
        }
        pane.replaceChildren(head, list);
        if (canEdit) pane.append(el('p', '目录最多两级，例如“第一章 / 第 1 节”；把讲义拖到目录上即可归档。', 'mb-folder-hint'));
    }
    function startFolderEdit(state) {
        editingFolder = state;
        if (state.parent) collapsed.delete(state.parent);
        renderPane();
    }
    function folderEditor(depth, initial, submit) {
        const form = el('form', '', 'mb-folder-item mb-folder-edit');
        form.style.setProperty('--depth', depth);
        const name = el('input');
        name.value = initial;
        name.maxLength = 80;
        name.required = true;
        name.placeholder = depth > 1 ? '子目录名称' : '目录名称';
        name.setAttribute('aria-label', name.placeholder);
        const save = el('button', '保存', 'mb-button mb-small mb-primary');
        const cancel = button('取消', 'mb-button mb-small', () => { editingFolder = null; renderPane(); });
        name.onkeydown = e => {
            if (e.key !== 'Escape') return;
            e.preventDefault();
            e.stopPropagation();
            cancel.onclick();
        };
        form.onsubmit = async e => {
            e.preventDefault();
            save.disabled = true;
            try {
                await submit(name.value.trim());
                editingFolder = null;
                folders = (await api('/folders')).folders;
                renderPane();
                renderList();
            } catch (err) {
                toast(err.message, 'error');
                save.disabled = false;
                name.focus();
            }
        };
        form.append(name, save, cancel);
        requestAnimationFrame(() => { name.focus(); name.select(); });
        return form;
    }
    function folderRow({value, name, iconName, count, depth = 0, folder = null}) {
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
        const pick = button('', 'mb-folder-pick', () => { filter = value; renderPane(); renderList(); });
        if (filter === value) pick.setAttribute('aria-current', 'true');
        pick.append(icon(iconName), el('span', name, 'mb-folder-name'), el('small', String(count)));
        item.append(pick);
        if (folder && canEdit) {
            const actions = el('span', '', 'mb-folder-actions');
            if (depth === 1) actions.append(iconButton('fa-folder-plus', `在“${name}”下新建子目录`, () => startFolderEdit({parent: folder.id})));
            actions.append(iconButton('fa-pen', `重命名“${name}”`, () => startFolderEdit({folder: folder.id})),
                iconButton('fa-trash-can', `删除“${name}”`, () => removeFolder(folder)));
            item.append(actions);
        }
        if (canEdit && value !== '') {
            item.addEventListener('dragover', e => {
                if (!e.dataTransfer.types.includes(HANDOUT_DRAG)) return;
                e.preventDefault();
                item.classList.add('mb-drop');
            });
            item.addEventListener('dragleave', () => item.classList.remove('mb-drop'));
            item.addEventListener('drop', e => {
                e.preventDefault();
                item.classList.remove('mb-drop');
                moveHandout(Number(e.dataTransfer.getData(HANDOUT_DRAG)), Number(value) || null);
            });
        }
        return item;
    }
    async function removeFolder(folder) {
        const ids = subtree(folder.id);
        const count = handouts.filter(h => ids.has(h.folder_id)).length;
        const parent = folders.find(f => f.id === folder.parent_id);
        const effects = [ids.size > 1 ? `其下 ${ids.size - 1} 个子目录一并删除` : '',
            count ? `${count} 份讲义移到“${parent ? parent.name : '未归档'}”` : '目录中没有讲义'];
        if (!await confirmDialog(`删除目录“${folder.name}”？`, effects.filter(Boolean).join('，') + '。讲义本身不会删除。', '删除目录')) return;
        try {
            await api(`/folders/${folder.id}`, 'DELETE');
            if (ids.has(Number(filter))) filter = '';
            await loadLibrary();
            toast('目录已删除', 'success');
        } catch (err) { toast(err.message, 'error'); }
    }
    async function moveHandout(id, folderId) {
        const handout = handouts.find(h => h.id === id);
        if (!handout || (handout.folder_id || null) === folderId) return;
        try {
            const moved = await api(`/handouts/${id}`, 'PATCH', {folder_id: folderId || 0});
            handout.folder_id = moved.folder_id;
            toast(`已移到“${folderPath(moved.folder_id)}”`, 'success');
            renderPane();
            renderList();
        } catch (err) { toast(err.message, 'error'); }
    }
    function renderList() {
        const words = searchBox.value.trim().toLowerCase();
        const visible = handouts.filter(h => inFilter(h) && (!words || (h.title + ' ' + h.summary.kicker).toLowerCase().includes(words)));
        if (!visible.length) {
            const empty = el('div', '', 'mb-empty mb-ho-empty');
            empty.append(icon('fa-book-open'), el('p', handouts.length ? '这里没有符合条件的讲义。' : '还没有讲义。'));
            if (canEdit && !handouts.length) {
                empty.append(el('p', '讲义按教材的样子编排：定义、定理、例题、练习按小节自动编号，导出时版式统一。'),
                    button('新建第一份讲义', 'mb-button mb-primary', createHandout, 'fa-plus'));
            }
            listBox.replaceChildren(empty);
            return;
        }
        listBox.replaceChildren(...visible.map(handoutCard));
    }
    function handoutCard(handout) {
        const card = el('article', '', 'mb-ho-card');
        card.setAttribute('role', 'listitem');
        const open = button('', 'mb-ho-card-open', () => openHandout(handout.id));
        if (handout.summary.kicker) open.append(el('span', handout.summary.kicker, 'mb-ho-card-kicker'));
        open.append(el('strong', handout.title));
        open.append(el('span', `${handout.summary.sections} 个小节 · ${handout.summary.blocks} 个内容块 · ${when(handout.updated)}`
            + (handout.updated_by_name ? ` · ${handout.updated_by_name}` : ''), 'mb-ho-card-meta'));
        const side = el('div', '', 'mb-ho-card-side');
        const place = el('span', '', 'mb-card-folder');
        place.append(icon('fa-folder'));
        if (canEdit) {
            const select = el('select', '', 'glass-select');
            select.setAttribute('aria-label', '讲义所在目录');
            folderOptions(select, handout.folder_id);
            select.onchange = () => moveHandout(handout.id, Number(select.value) || null);
            place.append(select);
            card.draggable = true;
            card.ondragstart = e => {
                e.dataTransfer.setData(HANDOUT_DRAG, String(handout.id));
                e.dataTransfer.effectAllowed = 'move';
            };
        } else {
            place.append(el('span', folderPath(handout.folder_id)));
        }
        side.append(place);
        if (canEdit) {
            side.append(iconButton('fa-copy', `复制“${handout.title}”`, () => copyHandout(handout)),
                iconButton('fa-trash-can', `删除“${handout.title}”`, () => deleteHandout(handout)));
        }
        card.append(open, side);
        return card;
    }
    function createHandout() {
        const shell = dialogShell('handoutCreate', '新建讲义', '先起个标题，内容在编辑器里按小节添加。', 'fa-book-open');
        const form = el('form');
        const title = el('input');
        title.required = true;
        title.maxLength = 120;
        title.placeholder = '例如：圆的有关概念与计算';
        const kicker = el('input');
        kicker.maxLength = 40;
        kicker.placeholder = '例如：第 19 讲（可不填）';
        const place = el('select');
        folderOptions(place, filter && filter !== '0' ? Number(filter) : 0);
        const actions = el('div', '', 'mb-actions');
        const submit = el('button', '创建并编辑', 'mb-button mb-primary');
        actions.append(button('取消', 'mb-button', () => shell.dialog.close()), submit);
        form.append(field('讲义标题', title), field('讲次', kicker), field('所在目录', place), actions);
        form.onsubmit = async e => {
            e.preventDefault();
            submit.disabled = true;
            try {
                const content = {kicker: kicker.value.trim(), sections: [{id: newId('s'), title: '知识梳理', blocks: []}]};
                const created = await api('/handouts', 'POST', {title: title.value.trim(), folder_id: Number(place.value) || null, document: content});
                shell.dialog.close();
                handouts.unshift(created);
                openHandout(created.id, created);
            } catch (err) {
                toast(err.message, 'error');
                submit.disabled = false;
            }
        };
        shell.body.append(form);
        shell.dialog.showModal();
        title.focus();
    }
    async function copyHandout(handout) {
        try {
            const copy = await api(`/handouts/${handout.id}/copy`, 'POST');
            handouts.unshift(copy);
            renderPane();
            renderList();
            toast('已复制为“' + copy.title + '”', 'success');
        } catch (err) { toast(err.message, 'error'); }
    }
    async function deleteHandout(handout) {
        if (!await confirmDialog(`删除讲义“${handout.title}”？`, '删除后无法恢复；题库中的题目不受影响。', '删除讲义')) return;
        try {
            await api(`/handouts/${handout.id}`, 'DELETE');
            handouts = handouts.filter(h => h.id !== handout.id);
            renderPane();
            renderList();
            toast('讲义已删除', 'success');
        } catch (err) { toast(err.message, 'error'); }
    }

    // ---------- Editor ----------
    async function openHandout(id, preloaded) {
        try {
            doc = preloaded && preloaded.document ? preloaded : await api(`/handouts/${id}`);
        } catch (err) {
            toast(err.message, 'error');
            return;
        }
        selection = {type: 'heading'};
        changes = {changed: [], deleted: []};
        dirty = conflict = false;
        buildEditor();
        ui.library.hidden = true;
        ui.editor.hidden = false;
        renderAll();
        if (canEdit) api(`/handouts/${doc.id}/changes`).then(result => { changes = result; renderChanges(); }).catch(() => {});
    }
    async function closeEditor() {
        await flush();
        if (dirty || conflict) return;
        doc = null;
        ui.editor.hidden = true;
        ui.library.hidden = false;
        loadLibrary();
    }
    function buildEditor() {
        const bar = el('header', '', 'mb-ho-toolbar');
        const back = button('讲义列表', 'mb-button mb-small', closeEditor, 'fa-arrow-left');
        ui.title = el('input', '', 'mb-ho-title-input');
        ui.title.value = doc.title;
        ui.title.maxLength = 120;
        ui.title.setAttribute('aria-label', '讲义标题');
        ui.title.disabled = !canEdit;
        ui.title.oninput = () => {
            doc.title = ui.title.value;
            ui.page.querySelector('.mb-ho-heading h1').textContent = doc.title;
            touch();
        };
        ui.status = el('span', '', 'mb-ho-save');
        ui.status.setAttribute('role', 'status');
        ui.changes = button('', 'mb-button mb-small mb-ho-changes', openChanges, 'fa-rotate');
        ui.changesText = el('span');
        ui.changes.append(ui.changesText);
        ui.changes.hidden = true;
        const views = el('div', '', 'mb-ho-views');
        views.setAttribute('role', 'group');
        views.setAttribute('aria-label', '预览版本');
        for (const [value, text] of [['teacher', '教师版'], ['student', '学生版']]) {
            const choice = button(text, 'mb-ho-view', () => {
                view = value;
                views.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b === choice)));
                renderPage();
            });
            choice.setAttribute('aria-pressed', String(view === value));
            views.append(choice);
        }
        const exportWrap = el('div', '', 'mb-ho-export');
        const exportButton = button('导出', 'mb-button mb-small mb-primary', () => { menu.hidden = !menu.hidden; }, 'fa-file-arrow-down');
        exportButton.setAttribute('aria-haspopup', 'menu');
        const menu = el('div', '', 'mb-ho-menu');
        menu.hidden = true;
        menu.setAttribute('role', 'menu');
        for (const [format, version, text, iconName] of [['pdf', 'student', 'PDF · 学生版', 'fa-file-pdf'], ['pdf', 'teacher', 'PDF · 教师版', 'fa-file-pdf'],
            ['docx', 'student', 'Word · 学生版', 'fa-file-word'], ['docx', 'teacher', 'Word · 教师版', 'fa-file-word']]) {
            const item = button(text, 'mb-ho-menu-item', () => { menu.hidden = true; exportHandout(format, version, exportButton); }, iconName);
            item.setAttribute('role', 'menuitem');
            menu.append(item);
        }
        ui.exportWrap = exportWrap;
        ui.exportMenu = menu;
        exportWrap.append(exportButton, menu);
        bar.append(back, ui.title, ui.status, ui.changes, views, exportWrap);
        ui.notice = el('div', '', 'mb-ho-conflict');
        ui.notice.hidden = true;
        const body = el('div', '', 'mb-ho-body');
        ui.outline = el('nav', '', 'mb-ho-outline');
        ui.outline.setAttribute('aria-label', '讲义大纲');
        const canvas = el('div', '', 'mb-ho-canvas');
        ui.page = el('article', '', 'mb-ho-page');
        canvas.append(ui.page);
        ui.inspector = el('aside', '', 'mb-ho-inspector');
        ui.inspector.setAttribute('aria-label', '内容设置');
        body.append(ui.outline, canvas, ui.inspector);
        ui.editor.replaceChildren(bar, ui.notice, body);
        setStatus();
    }
    function renderAll() {
        renderPage();
        renderOutline();
        renderInspector();
    }
    const sections = () => doc.document.sections;
    function find(id) {
        for (const part of sections()) {
            const index = part.blocks.findIndex(b => b.id === id);
            if (index >= 0) return {section: part, index, block: part.blocks[index]};
        }
        return null;
    }
    // Per-section numbering, one counter per kind: 定义 1.1, 1.2 …; 例 1.1, 1.2 …; 性质 1.1 ….
    function numbering() {
        const tags = {};
        sections().forEach((part, s) => {
            const counters = {};
            for (const block of part.blocks) {
                if (!NUMBERED.has(block.kind)) continue;
                counters[block.kind] = (counters[block.kind] || 0) + 1;
                tags[block.id] = `${s + 1}.${counters[block.kind]}`;
            }
        });
        return tags;
    }
    const printedName = block => PRINTED[block.kind] || KINDS[block.kind][0];

    function renderPage() {
        const tags = numbering();
        const page = ui.page;
        page.replaceChildren();
        page.classList.toggle('mb-ho-readonly', !canEdit);
        const d = doc.document;
        const heading = el('header', '', 'mb-ho-heading mb-ho-selectable');
        heading.dataset.select = 'heading';
        heading.addEventListener('click', select);
        if (d.kicker) heading.append(el('p', d.kicker, 'mb-ho-kicker'));
        heading.append(el('h1', doc.title));
        if (d.subtitle) heading.append(el('p', d.subtitle, 'mb-ho-subtitle'));
        heading.append(el('p', `${space.stage}${space.subject}${space.textbook ? ' · ' + space.textbook : ''} · ${view === 'teacher' ? '教师版' : '学生版'}`, 'mb-ho-meta'));
        const objectives = (d.objectives || []).filter(item => item.trim());
        if (objectives.length) {
            const panel = el('div', '', 'mb-ho-objectives');
            const list = el('ol');
            objectives.forEach(item => list.append(fill(el('li'), item)));
            panel.append(el('h4', '学习目标'), list);
            heading.append(panel);
        } else if (canEdit) {
            heading.append(el('p', '点此填写讲次、副标题和学习目标', 'mb-ho-hint'));
        }
        page.append(heading);
        const answers = [];
        sections().forEach((part, s) => {
            const node = el('section', '', 'mb-ho-section');
            const title = el('h2', '', 'mb-ho-section-title mb-ho-selectable');
            title.dataset.select = 'section:' + part.id;
            title.addEventListener('click', select);
            title.append(el('span', String(s + 1), 'mb-ho-num'), document.createTextNode(part.title || '未命名小节'));
            node.append(title);
            part.blocks.forEach((block, index) => {
                if (canEdit) node.append(inserter(part, index));
                node.append(blockNode(block, tags[block.id]));
                if (block.kind === 'exercises' && block.questions.length) answers.push([`${s + 1}　${block.title || '练习'}`, block.questions]);
            });
            if (canEdit) {
                const add = button('添加内容', 'mb-ho-add', e => kindMenu(e.currentTarget, part, part.blocks.length), 'fa-plus');
                node.append(add);
            }
            page.append(node);
        });
        if (canEdit) page.append(button('添加小节', 'mb-ho-add mb-ho-add-section', addSection, 'fa-plus'));
        if (view === 'teacher' && answers.length) {
            const block = el('section', '', 'mb-ho-answers');
            block.append(el('h2', '参考答案'));
            for (const [title, questions] of answers) {
                block.append(el('h4', title));
                const list = el('ol');
                questions.forEach(question => {
                    const item = el('li');
                    item.append(question.answer_markdown?.trim() ? answerPreview(question.answer_markdown) : el('span', '（暂无答案）', 'mb-ho-muted'));
                    list.append(item);
                });
                block.append(list);
            }
            page.append(block);
        }
        renderMath(page);
        markSelection();
    }
    function blockNode(block, tag) {
        const node = el('div', '', 'mb-ho-block mb-ho-selectable mb-ho-' + block.kind);
        node.dataset.select = 'block:' + block.id;
        node.dataset.block = block.id;
        const kind = block.kind;
        if (kind === 'text') {
            node.append(block.body.trim() ? prose(block.body, 'mb-ho-prose mb-ho-indent') : placeholder('正文'));
        } else if (BOXES.includes(kind)) {
            node.classList.add(kind === 'definition' ? 'mb-ho-box-tint' : 'mb-ho-box-frame');
            node.append(labelled(label(`${KINDS[kind][0]} ${tag}`, block.title), block.body.trim() ? prose(block.body) : prose('　')));
        } else if (kind === 'proof') {
            const body = prose(block.body || '　');
            const last = body.lastElementChild || body;
            last.append(el('span', '□', 'mb-ho-qed'));
            node.append(labelled(label('证明', '', 'mb-ho-label mb-ho-label-muted'), body));
        } else if (NOTES.includes(kind)) {
            node.append(labelled(label(KINDS[kind][0], '', 'mb-ho-label mb-ho-label-small'), prose(block.body || '　')));
        } else if (kind === 'summary') {
            node.append(el('h4', '本节小结'), block.body.trim() ? prose(block.body) : placeholder('小结'));
        } else if (kind === 'figure') {
            if (block.image) {
                const image = el('img');
                image.src = block.image;
                image.alt = block.caption || '插图';
                image.dataset.size = block.size;
                node.append(image);
            } else {
                node.append(placeholder('插图（在右侧上传图片）'));
            }
            node.append(el('p', `图 ${tag}` + (block.caption ? '　' + block.caption : ''), 'mb-ho-caption'));
        } else if (kind === 'example') {
            const lead = label(`例 ${tag}`, block.title, 'mb-ho-label mb-ho-example-label');
            node.append(lead, questionPreview(block.question));
            if (view === 'teacher' || doc.document.settings.student_solutions === 'show') {
                const solution = el('div', '', 'mb-ho-solution');
                const body = block.solution?.trim() ? prose(block.solution)
                    : block.question.answer_markdown?.trim() ? answerPreview(block.question.answer_markdown) : prose('（暂无答案）');
                solution.append(labelled(label('解', '', 'mb-ho-label'), body));
                node.append(solution);
            } else if (block.space) {
                const gap = el('div', '', 'mb-ho-space');
                gap.style.height = block.space + 'cm';
                gap.title = `学生作答留白 ${block.space} cm`;
                node.append(gap);
            }
        } else if (kind === 'exercises') {
            node.append(el('h3', block.title || '练习'));
            if (block.questions.length) {
                const list = el('ol');
                block.questions.forEach(question => {
                    const item = el('li');
                    item.append(questionPreview(question));
                    list.append(item);
                });
                node.append(list);
            } else {
                node.append(placeholder('还没有题目，在右侧“添加题目”'));
            }
        }
        node.addEventListener('click', select);
        return node;
    }
    function placeholder(text) {
        return el('p', canEdit ? '点此编辑' + text : '', 'mb-ho-hint');
    }
    function inserter(part, index) {
        const node = el('div', '', 'mb-ho-insert');
        const add = iconButton('fa-plus', '在此插入内容', e => kindMenu(e.currentTarget, part, index), 'mb-ho-insert-button');
        node.append(add);
        return node;
    }
    function select(e) {
        if (e.target.closest('a, button, input, select, textarea')) return;
        const target = e.currentTarget.dataset.select;
        e.stopPropagation();
        setSelection(target);
    }
    function setSelection(value, {scroll = false, focus = false} = {}) {
        const [type, id] = value.split(':');
        selection = {type, id};
        markSelection();
        renderOutline();
        renderInspector();
        if (scroll) ui.page.querySelector(`[data-select="${value}"]`)?.scrollIntoView({block: 'center', behavior: 'smooth'});
        if (focus) ui.inspector.querySelector('textarea, input:not([type=file]):not([type=number])')?.focus();
    }
    const selectionKey = () => selection.type === 'heading' ? 'heading' : `${selection.type}:${selection.id}`;
    function markSelection() {
        ui.page.querySelectorAll('.mb-ho-selected').forEach(node => node.classList.remove('mb-ho-selected'));
        ui.page.querySelector(`[data-select="${selectionKey()}"]`)?.classList.add('mb-ho-selected');
    }
    let pageTimer = null;
    function renderPageSoon() {
        clearTimeout(pageTimer);
        pageTimer = setTimeout(renderPage, 200);
    }
    // Typing re-renders only that block, so long handouts stay responsive.
    let refreshTimer = null;
    function refreshBlock(id) {
        clearTimeout(refreshTimer);
        refreshTimer = setTimeout(() => {
            const found = find(id);
            const old = ui.page.querySelector(`[data-block="${id}"]`);
            if (!found || !old) return renderPage();
            const fresh = blockNode(found.block, numbering()[id]);
            old.replaceWith(fresh);
            renderMath(fresh);
            markSelection();
        }, 160);
    }

    // ---------- Outline ----------
    function outlineName(block, tags) {
        if (NUMBERED.has(block.kind)) return `${printedName(block)} ${tags[block.id]}` + (block.title ? '　' + block.title : (block.kind === 'figure' && block.caption ? '　' + block.caption : ''));
        if (block.kind === 'exercises') return `${block.title || '练习'}（${block.questions.length} 题）`;
        return `${KINDS[block.kind][0]}　${shortText(block.body, 12) || '（空）'}`;
    }
    function renderOutline() {
        const tags = numbering();
        const list = el('ol', '', 'mb-ho-outline-list');
        const top = button('标题与学习目标', 'mb-ho-outline-item mb-ho-outline-top', () => setSelection('heading', {scroll: true}), 'fa-heading');
        if (selection.type === 'heading') top.setAttribute('aria-current', 'true');
        sections().forEach((part, s) => {
            const item = el('li', '', 'mb-ho-outline-section');
            const head = button('', 'mb-ho-outline-item mb-ho-outline-head', () => setSelection('section:' + part.id, {scroll: true}));
            head.append(el('span', String(s + 1), 'mb-ho-num'), el('span', part.title || '未命名小节', 'mb-ho-outline-text'));
            if (selection.type === 'section' && selection.id === part.id) head.setAttribute('aria-current', 'true');
            dragSource(head, {section: part.id});
            dropTarget(head, {section: part.id});
            const blocks = el('ol');
            part.blocks.forEach(block => {
                const row = el('li');
                const entry = button('', 'mb-ho-outline-item', () => setSelection('block:' + block.id, {scroll: true}), KINDS[block.kind][1]);
                entry.append(el('span', outlineName(block, tags), 'mb-ho-outline-text'));
                if (selection.type === 'block' && selection.id === block.id) entry.setAttribute('aria-current', 'true');
                dragSource(entry, {block: block.id});
                dropTarget(entry, {block: block.id});
                row.append(entry);
                blocks.append(row);
            });
            item.append(head, blocks);
            list.append(item);
        });
        ui.outline.replaceChildren(el('h3', '大纲'), top, list);
        if (canEdit) {
            ui.outline.append(button('添加小节', 'mb-button mb-small mb-ho-outline-add', addSection, 'fa-plus'),
                el('p', '拖动条目可调整顺序；把内容拖到小节标题上会移到该小节末尾。', 'mb-folder-hint'));
        }
    }
    function dragSource(node, value) {
        if (!canEdit) return;
        node.draggable = true;
        node.addEventListener('dragstart', e => {
            e.dataTransfer.setData(OUTLINE_DRAG, JSON.stringify(value));
            e.dataTransfer.effectAllowed = 'move';
        });
    }
    function dropTarget(node, target) {
        if (!canEdit) return;
        node.addEventListener('dragover', e => {
            if (!e.dataTransfer.types.includes(OUTLINE_DRAG)) return;
            e.preventDefault();
            node.classList.add('mb-drop');
        });
        node.addEventListener('dragleave', () => node.classList.remove('mb-drop'));
        node.addEventListener('drop', e => {
            e.preventDefault();
            node.classList.remove('mb-drop');
            let source;
            try { source = JSON.parse(e.dataTransfer.getData(OUTLINE_DRAG)); } catch (err) { return; }
            if (source.block && target.block) moveBlockBefore(source.block, target.block);
            else if (source.block && target.section) moveBlockToSection(source.block, target.section);
            else if (source.section && target.section) moveSectionBefore(source.section, target.section);
        });
    }
    function moveBlockBefore(id, beforeId) {
        if (id === beforeId) return;
        const from = find(id);
        from.section.blocks.splice(from.index, 1);
        const to = find(beforeId);
        to.section.blocks.splice(to.index, 0, from.block);
        structural();
    }
    function moveBlockToSection(id, sectionId) {
        const from = find(id);
        from.section.blocks.splice(from.index, 1);
        sections().find(s => s.id === sectionId).blocks.push(from.block);
        structural();
    }
    function moveSectionBefore(id, beforeId) {
        if (id === beforeId) return;
        const list = sections();
        const [moved] = list.splice(list.findIndex(s => s.id === id), 1);
        list.splice(list.findIndex(s => s.id === beforeId), 0, moved);
        structural();
    }
    function structural() {
        touch();
        renderAll();
    }

    // ---------- Inserting ----------
    function kindMenu(anchor, part, index) {
        document.querySelector('.mb-ho-kind-menu')?.remove();
        const menu = el('div', '', 'mb-ho-menu mb-ho-kind-menu');
        menu.setAttribute('role', 'menu');
        for (const [group, kinds] of MENU) {
            menu.append(el('p', group, 'mb-ho-menu-group'));
            const row = el('div', '', 'mb-ho-menu-row');
            for (const kind of kinds) {
                const item = button(KINDS[kind][0], 'mb-ho-menu-item', () => { menu.remove(); insertBlock(kind, part, index); }, KINDS[kind][1]);
                item.setAttribute('role', 'menuitem');
                row.append(item);
            }
            menu.append(row);
        }
        document.body.append(menu);
        const box = anchor.getBoundingClientRect();
        const width = menu.offsetWidth, height = menu.offsetHeight;
        menu.style.left = Math.max(8, Math.min(box.left, innerWidth - width - 8)) + 'px';
        menu.style.top = (box.bottom + height + 8 < innerHeight ? box.bottom + 6 : Math.max(8, box.top - height - 6)) + 'px';
        menu.querySelector('button').focus();
        const close = e => {
            if (e.type === 'keydown' && e.key !== 'Escape') return;
            if (e.type === 'mousedown' && menu.contains(e.target)) return;
            menu.remove();
            document.removeEventListener('mousedown', close, true);
            document.removeEventListener('keydown', close, true);
        };
        document.addEventListener('mousedown', close, true);
        document.addEventListener('keydown', close, true);
    }
    async function insertBlock(kind, part, index) {
        const block = {id: newId('b'), kind};
        if (TEXTUAL.includes(kind)) Object.assign(block, {title: '', body: ''});
        if (kind === 'figure') Object.assign(block, {image: '', caption: '', size: 'medium'});
        if (kind === 'example') {
            const picked = await pickQuestions('example');
            if (!picked?.length) return;
            Object.assign(block, {title: '', question: picked[0], solution: '', space: 4});
        }
        if (kind === 'exercises') {
            const picked = await pickQuestions('exercises');
            if (!picked) return;
            Object.assign(block, {title: '基础练习', questions: picked});
        }
        part.blocks.splice(index, 0, block);
        structural();
        setSelection('block:' + block.id, {scroll: true, focus: TEXTUAL.includes(kind)});
    }
    function addSection() {
        const part = {id: newId('s'), title: '新小节', blocks: []};
        sections().push(part);
        structural();
        setSelection('section:' + part.id, {scroll: true, focus: true});
    }

    // ---------- Inspector ----------
    function renderInspector() {
        const box = ui.inspector;
        box.replaceChildren();
        if (selection.type === 'block' && !find(selection.id)) selection = {type: 'heading'};
        if (selection.type === 'section' && !sections().some(s => s.id === selection.id)) selection = {type: 'heading'};
        if (selection.type === 'heading') return headingInspector(box);
        if (selection.type === 'section') return sectionInspector(box, sections().find(s => s.id === selection.id));
        return blockInspector(box, find(selection.id));
    }
    function inspectorHead(box, iconName, title, note) {
        const head = el('div', '', 'mb-ho-inspector-head');
        const text = el('h3', title);
        text.prepend(icon(iconName));
        head.append(text);
        if (note) head.append(el('p', note));
        box.append(head);
    }
    function headingInspector(box) {
        const d = doc.document;
        inspectorHead(box, 'fa-heading', '标题与学习目标', '标题在上方工具栏修改。');
        const refresh = () => { touch(); renderPageSoon(); };
        box.append(field('讲次', input(d.kicker, '例如：第 19 讲', v => { d.kicker = v; refresh(); }, 40)),
            field('副标题', input(d.subtitle, '一句话说明本讲内容', v => { d.subtitle = v; refresh(); })),
            field('学习目标', textarea((d.objectives || []).join('\n'), '每行一条，最多 12 条', v => {
                d.objectives = v.split('\n').map(line => line.trim()).filter(Boolean).slice(0, 12);
                refresh();
            }, 5), '每行一条；可写公式，如 $S=\\pi r^2$。'));
        const solutions = el('select');
        solutions.disabled = !canEdit;
        solutions.append(option('blank', '例题下留白，学生作答'), option('show', '显示解答（用作预习或复习资料）'));
        solutions.value = d.settings.student_solutions;
        solutions.onchange = () => { d.settings.student_solutions = solutions.value; refresh(); };
        box.append(field('学生版例题', solutions, '教师版始终显示解答，练习答案统一附在讲义末尾。'));
        box.append(syntaxHelp());
    }
    function sectionInspector(box, part) {
        const index = sections().indexOf(part);
        inspectorHead(box, 'fa-bookmark', `第 ${index + 1} 节`, '小节标题按 1、2、3 自动编号，节内的定义、例题等编号随之更新。');
        box.append(field('小节标题', input(part.title, '例如：圆的有关概念', v => {
            part.title = v;
            touch();
            const heading = ui.page.querySelector(`[data-select="section:${part.id}"]`);
            if (heading) heading.lastChild.textContent = v || '未命名小节';
            renderOutline();
        })));
        if (!canEdit) return;
        const actions = el('div', '', 'mb-ho-inspector-actions');
        actions.append(button('上移', 'mb-button mb-small', () => shiftSection(part, -1), 'fa-arrow-up'),
            button('下移', 'mb-button mb-small', () => shiftSection(part, 1), 'fa-arrow-down'),
            button('删除小节', 'mb-button mb-small mb-danger', () => removeSection(part), 'fa-trash-can'));
        box.append(actions);
    }
    function shiftSection(part, step) {
        const list = sections();
        const index = list.indexOf(part);
        if (index + step < 0 || index + step >= list.length) return;
        list.splice(index, 1);
        list.splice(index + step, 0, part);
        structural();
    }
    async function removeSection(part) {
        if (sections().length === 1) return toast('讲义至少保留一个小节', 'info');
        if (part.blocks.length && !await confirmDialog(`删除小节“${part.title || '未命名小节'}”？`, `其中 ${part.blocks.length} 个内容块一并删除。`, '删除小节')) return;
        sections().splice(sections().indexOf(part), 1);
        selection = {type: 'heading'};
        structural();
    }
    function syntaxHelp() {
        const help = el('details', '', 'mb-ho-help');
        help.append(el('summary', '书写格式'));
        const list = el('ul');
        for (const line of ['$…$ 行内公式，$$…$$ 独立公式', '**文字** 加粗', '行首“- ”为项目列表，“1. ”为编号列表', '空一行开始新段落', '____（四个下划线）为填空横线'])
            list.append(el('li', line));
        help.append(list);
        return help;
    }
    function blockInspector(box, found) {
        const {block, section: part} = found;
        const tag = numbering()[block.id];
        inspectorHead(box, KINDS[block.kind][1], KINDS[block.kind][0] + (tag ? ' ' + tag : ''),
            NUMBERED.has(block.kind) ? '编号按小节自动排列，移动后自动更新。' : '');
        const update = () => { touch(); refreshBlock(block.id); renderOutlineSoon(); };
        if (TEXTUAL.includes(block.kind)) {
            if (canEdit) {
                const kind = el('select');
                for (const [group, kinds] of MENU.slice(0, 3)) {
                    const optgroup = el('optgroup');
                    optgroup.label = group;
                    kinds.forEach(k => optgroup.append(option(k, KINDS[k][0])));
                    kind.append(optgroup);
                }
                kind.value = block.kind;
                kind.onchange = () => { block.kind = kind.value; structural(); };
                box.append(field('类型', kind));
            }
            if (BOXES.includes(block.kind)) box.append(field('名称', input(block.title, `如：${block.kind === 'definition' ? '圆' : '垂径定理'}（可不填）`, v => { block.title = v; update(); })));
            box.append(field('内容', textarea(block.body, '在此书写内容', v => { block.body = v; update(); }, 12)), syntaxHelp());
        } else if (block.kind === 'figure') {
            figureInspector(box, block, update);
        } else if (block.kind === 'example') {
            exampleInspector(box, block, update);
        } else if (block.kind === 'exercises') {
            exercisesInspector(box, block, update);
        }
        if (!canEdit) return;
        const actions = el('div', '', 'mb-ho-inspector-actions');
        actions.append(button('上移', 'mb-button mb-small', () => shiftBlock(block, -1), 'fa-arrow-up'),
            button('下移', 'mb-button mb-small', () => shiftBlock(block, 1), 'fa-arrow-down'),
            button('复制', 'mb-button mb-small', () => duplicateBlock(block), 'fa-copy'),
            button('删除', 'mb-button mb-small mb-danger', () => removeBlock(block), 'fa-trash-can'));
        box.append(actions);
    }
    let outlineTimer = null;
    function renderOutlineSoon() {
        clearTimeout(outlineTimer);
        outlineTimer = setTimeout(renderOutline, 300);
    }
    function figureInspector(box, block, update) {
        const picker = el('input');
        picker.type = 'file';
        picker.accept = 'image/png,image/jpeg,image/webp,image/gif';
        picker.hidden = true;
        picker.onchange = async () => {
            const file = picker.files[0];
            if (!file) return;
            const data = new FormData();
            data.append('file', file);
            try {
                const response = await fetch(`/bank/${space.id}/api/upload`, {method: 'POST', body: data});
                const value = await response.json().catch(() => ({}));
                if (!response.ok || !value.file_path) throw new Error(value.message || value.detail || '图片上传失败');
                block.image = value.file_path;
                update();
                renderInspector();
            } catch (err) { toast(err.message, 'error'); }
        };
        if (block.image) {
            const preview = el('img', '', 'mb-ho-inspector-image');
            preview.src = block.image;
            preview.alt = '';
            box.append(preview);
        }
        if (canEdit) box.append(picker, button(block.image ? '更换图片' : '上传图片', 'mb-button mb-small', () => picker.click(), 'fa-upload'));
        box.append(field('图注', input(block.caption, '如：圆心角与弧（可不填）', v => { block.caption = v; update(); })));
        const size = el('select');
        size.disabled = !canEdit;
        Object.entries(FIGURE_SIZES).forEach(([value, text]) => size.append(option(value, text)));
        size.value = block.size;
        size.onchange = () => { block.size = size.value; update(); };
        box.append(field('大小', size));
    }
    function exampleInspector(box, block, update) {
        box.append(field('例题名称', input(block.title, '如：基础（可不填）', v => { block.title = v; update(); })));
        const question = el('div', '', 'mb-ho-picked');
        question.append(el('span', `题库 #${block.question.source_id}`, 'mb-badge mb-muted'), el('p', shortText(block.question.content, 60)));
        question.append(syncNote(block.question.source_id));
        box.append(question);
        if (canEdit) {
            box.append(button('更换题目', 'mb-button mb-small', async () => {
                const picked = await pickQuestions('example');
                if (!picked?.length) return;
                block.question = picked[0];
                structural();
            }, 'fa-arrow-right-arrow-left'));
        }
        box.append(field('解答', textarea(block.solution, '留空则使用题库答案', v => { block.solution = v; update(); }, 8),
            '教师版显示；学生版按“标题与学习目标”中的设置留白或显示。'));
        const room = el('input');
        room.type = 'number';
        room.min = 0;
        room.max = 15;
        room.step = 0.5;
        room.value = block.space;
        room.disabled = !canEdit;
        room.oninput = () => {
            const value = Number(room.value);
            if (Number.isFinite(value) && value >= 0 && value <= 15) { block.space = value; update(); }
        };
        box.append(field('学生版留白（厘米）', room));
    }
    function exercisesInspector(box, block, update) {
        box.append(field('标题', input(block.title, '如：基础练习', v => { block.title = v; update(); })));
        const list = el('ol', '', 'mb-ho-picked-list');
        block.questions.forEach((question, index) => {
            const row = el('li');
            const text = el('div');
            text.append(el('span', `#${question.source_id}`, 'mb-badge mb-muted'), el('p', shortText(question.content, 40)), syncNote(question.source_id));
            row.append(text);
            if (canEdit) {
                const tools = el('span', '', 'mb-ho-row-tools');
                tools.append(iconButton('fa-arrow-up', '上移', () => { if (index) { block.questions.splice(index - 1, 0, ...block.questions.splice(index, 1)); structural(); } }),
                    iconButton('fa-arrow-down', '下移', () => { if (index < block.questions.length - 1) { block.questions.splice(index + 1, 0, ...block.questions.splice(index, 1)); structural(); } }),
                    iconButton('fa-xmark', '移出本组', () => { block.questions.splice(index, 1); structural(); }));
                row.append(tools);
            }
            list.append(row);
        });
        box.append(el('h4', `题目（${block.questions.length}）`, 'mb-ho-inspector-sub'), list);
        if (canEdit) {
            box.append(button('添加题目', 'mb-button mb-small', async () => {
                const picked = await pickQuestions('exercises', block.questions.map(q => q.source_id));
                if (!picked?.length) return;
                block.questions.push(...picked);
                structural();
            }, 'fa-plus'));
        }
        box.append(el('p', '答案在教师版末尾“参考答案”中按小节列出。', 'mb-folder-hint'));
    }
    function syncNote(id) {
        if (changes.changed.includes(id)) return el('span', '题库已修改', 'mb-badge mb-warn');
        if (changes.deleted.includes(id)) return el('span', '题库已删除，保留快照', 'mb-badge mb-muted');
        return document.createTextNode('');
    }
    function shiftBlock(block, step) {
        const {section: part, index} = find(block.id);
        const list = sections();
        const s = list.indexOf(part);
        part.blocks.splice(index, 1);
        if (index + step < 0) {
            // Past the top: end of the previous section.
            if (s > 0) list[s - 1].blocks.push(block); else part.blocks.unshift(block);
        } else if (index + step > part.blocks.length) {
            if (s < list.length - 1) list[s + 1].blocks.unshift(block); else part.blocks.push(block);
        } else {
            part.blocks.splice(index + step, 0, block);
        }
        structural();
    }
    function duplicateBlock(block) {
        const {section: part, index} = find(block.id);
        const copy = JSON.parse(JSON.stringify(block));
        copy.id = newId('b');
        part.blocks.splice(index + 1, 0, copy);
        structural();
        setSelection('block:' + copy.id, {scroll: true});
    }
    async function removeBlock(block) {
        const empty = TEXTUAL.includes(block.kind) ? !block.body.trim() : block.kind === 'figure' && !block.image;
        if (!empty && !await confirmDialog(`删除这个${KINDS[block.kind][0]}？`, '删除后无法撤销；题目只从讲义中移除，题库不受影响。', '删除')) return;
        const {section: part, index} = find(block.id);
        part.blocks.splice(index, 1);
        selection = {type: 'section', id: part.id};
        structural();
    }

    // ---------- Question picker: bank search or the 组卷 basket ----------
    function pickQuestions(mode, exclude = []) {
        const single = mode === 'example';
        const shell = dialogShell('handoutPicker', single ? '选择例题' : '添加练习题',
            '从本空间题库选题。插入时保存题目快照，之后题库修改不会改动讲义，可在“题库更新”中同步。', 'fa-magnifying-glass');
        shell.dialog.classList.add('mb-wide');
        const tabs = el('div', '', 'mb-tabs');
        const cartIds = (window.PaperStore?.cart || []).map(item => Number(item.id)).filter(Number.isInteger);
        const tabBank = button('题库检索', '', () => switchTab('bank'));
        const tabCart = button(`组卷题篮（${cartIds.length}）`, '', () => switchTab('cart'));
        tabs.append(tabBank, tabCart);
        const controls = el('form', '', 'mb-xs-controls');
        const keyword = el('input');
        keyword.type = 'search';
        keyword.placeholder = '搜索题干、来源或标签';
        keyword.setAttribute('aria-label', '搜索题目');
        const type = el('select');
        type.setAttribute('aria-label', '题型');
        type.append(option('', '全部题型'), ...((window.systemMetadata || {}).question_types || []).map(t => option(t.value, t.label)));
        controls.append(keyword, type);
        const status = el('p', '', 'mb-status');
        status.setAttribute('role', 'status');
        const list = el('div', '', 'mb-xs-list');
        const footer = el('div', '', 'mb-xs-pager');
        const previous = button('上一页', 'mb-button mb-small', () => load(page - 1));
        const next = button('下一页', 'mb-button mb-small', () => load(page + 1));
        const pageText = el('span', '', 'mb-status');
        const confirm = button('', 'mb-button mb-primary', () => finish());
        footer.append(previous, pageText, next);
        if (!single) footer.append(confirm);
        shell.body.append(tabs, controls, status, list, footer);
        const chosen = new Map();
        let page = 1, sequence = 0, timer = null, tab = 'bank', result = null, done = false;
        const refreshConfirm = () => {
            confirm.textContent = chosen.size ? `加入 ${chosen.size} 题` : '加入所选题目';
            confirm.disabled = !chosen.size;
        };
        function card(question) {
            const item = el('article', '', 'mb-xs-item');
            const header = el('header');
            header.append(el('span', `#${question.seq_num ?? question.id}`));
            if (exclude.includes(question.id)) header.append(el('span', '已在本组', 'mb-badge mb-muted'));
            const action = button('', 'mb-button mb-small ' + (single ? 'mb-primary' : ''));
            const mark = () => {
                action.textContent = single ? '选用' : chosen.has(question.id) ? '已选' : '选择';
                action.setAttribute('aria-pressed', String(chosen.has(question.id)));
                item.classList.toggle('mb-ho-chosen', chosen.has(question.id));
            };
            action.onclick = () => {
                if (single) { chosen.set(question.id, question); return finish(); }
                if (chosen.has(question.id)) chosen.delete(question.id); else chosen.set(question.id, question);
                mark();
                refreshConfirm();
            };
            mark();
            header.append(action);
            const preview = el('div', '', 'mb-xs-preview');
            try { window.renderBankQuestionPreview(preview, question); } catch (err) { preview.textContent = question.content; }
            item.append(header, preview);
            return item;
        }
        async function load(target) {
            const current = ++sequence;
            page = target;
            status.textContent = '正在查询…';
            previous.disabled = next.disabled = true;
            try {
                if (tab === 'cart') {
                    if (!cartIds.length) {
                        list.replaceChildren();
                        status.textContent = '组卷题篮是空的。可在题库中把题目加入题篮，再回到这里一次插入。';
                        pageText.textContent = '';
                        return;
                    }
                    const value = await fetch(`/bank/${space.id}/api/paper/questions?ids=${cartIds.join(',')}`).then(r => r.json());
                    if (current !== sequence) return;
                    list.replaceChildren(...(value.data || []).map(card));
                    status.textContent = `题篮中 ${(value.data || []).length} 题`;
                    pageText.textContent = '';
                    return;
                }
                const params = new URLSearchParams({page, page_size: 8});
                if (keyword.value.trim()) params.set('q', keyword.value.trim());
                if (type.value) params.set('question_type', type.value);
                const response = await fetch(`/bank/${space.id}/api/questions?${params}`);
                const value = await response.json();
                if (!response.ok) throw new Error(value.detail || '查询失败');
                if (current !== sequence) return;
                list.replaceChildren(...value.items.map(card));
                status.textContent = value.total ? `共 ${value.total} 题` : '没有符合条件的题目。';
                pageText.textContent = `第 ${value.page} / ${Math.max(1, value.total_pages)} 页`;
                previous.disabled = value.page <= 1;
                next.disabled = value.page >= value.total_pages;
            } catch (err) {
                if (current === sequence) status.textContent = err.message;
            }
        }
        function switchTab(value) {
            tab = value;
            for (const [node, name] of [[tabBank, 'bank'], [tabCart, 'cart']]) {
                if (name === value) node.setAttribute('aria-current', 'page'); else node.removeAttribute('aria-current');
            }
            controls.hidden = value !== 'bank';
            load(1);
        }
        async function finish() {
            if (!chosen.size || done) return;
            done = true;
            confirm.disabled = true;
            try {
                result = await api('/handouts/snapshots', 'POST', {ids: [...chosen.keys()]});
                shell.dialog.close();
            } catch (err) {
                done = false;
                refreshConfirm();
                toast(err.message, 'error');
            }
        }
        controls.onsubmit = e => { e.preventDefault(); load(1); };
        type.onchange = () => load(1);
        keyword.oninput = () => { clearTimeout(timer); timer = setTimeout(() => load(1), 300); };
        refreshConfirm();
        return new Promise(resolve => {
            shell.dialog.addEventListener('close', () => resolve(result), {once: true});
            shell.dialog.showModal();
            shell.focus();
            switchTab(!single && cartIds.length ? 'cart' : 'bank');
        });
    }

    // ---------- Bank changes since insertion ----------
    function renderChanges() {
        const count = changes.changed.length + changes.deleted.length;
        ui.changes.hidden = !count;
        ui.changesText.textContent = `题库更新 ${count}`;
        if (selection.type === 'block') renderInspector();
    }
    async function openChanges() {
        const shell = dialogShell('handoutChanges', '题库更新', '插入后题库中有改动的题目。讲义保留插入时的样子，需要时再同步。', 'fa-rotate');
        const list = el('ul', '', 'mb-ho-change-list');
        const where = id => {
            const places = [];
            const tags = numbering();
            sections().forEach((part, s) => part.blocks.forEach(block => {
                if (block.kind === 'example' && block.question.source_id === id) places.push(`例 ${tags[block.id]}`);
                if (block.kind === 'exercises' && block.questions.some(q => q.source_id === id)) places.push(`${s + 1} ${block.title || '练习'}`);
            }));
            return places.join('、');
        };
        for (const id of changes.changed) list.append(el('li', `#${id}　${where(id)}　题库已修改`));
        for (const id of changes.deleted) list.append(el('li', `#${id}　${where(id)}　题库已删除，讲义保留原题`));
        const actions = el('div', '', 'mb-actions');
        actions.append(button('暂不同步', 'mb-button', () => shell.dialog.close()));
        if (changes.changed.length && canEdit) {
            const sync = button(`同步 ${changes.changed.length} 题为题库最新版本`, 'mb-button mb-primary', async () => {
                sync.disabled = true;
                try {
                    const fresh = await api('/handouts/snapshots', 'POST', {ids: changes.changed});
                    const byId = new Map(fresh.map(q => [q.source_id, q]));
                    for (const part of sections()) for (const block of part.blocks) {
                        if (block.kind === 'example' && byId.has(block.question.source_id)) block.question = byId.get(block.question.source_id);
                        if (block.kind === 'exercises') block.questions = block.questions.map(q => byId.get(q.source_id) || q);
                    }
                    changes.changed = changes.changed.filter(id => !byId.has(id));
                    shell.dialog.close();
                    structural();
                    renderChanges();
                    toast('已同步为题库最新版本；例题的自写解答保持不变', 'success');
                } catch (err) {
                    sync.disabled = false;
                    toast(err.message, 'error');
                }
            });
            actions.append(sync);
        }
        shell.body.append(list, actions);
        shell.dialog.showModal();
        shell.focus();
    }

    // ---------- Saving ----------
    function touch() {
        if (!canEdit || conflict) return;
        dirty = true;
        setStatus();
        clearTimeout(saveTimer);
        saveTimer = setTimeout(save, 1000);
    }
    function setStatus(state) {
        if (!ui.status) return;
        const text = conflict ? '未保存：讲义已在别处修改' : state === 'error' ? '保存失败，稍后重试'
            : saving ? '正在保存…' : dirty ? '有未保存的修改' : canEdit ? '已保存' : '只读';
        ui.status.textContent = text;
        ui.status.dataset.state = conflict || state === 'error' ? 'error' : saving || dirty ? 'pending' : 'saved';
    }
    // One request at a time; edits made meanwhile go in the next one. The body is
    // serialised when the request starts, so later typing never mixes into it.
    function save() {
        clearTimeout(saveTimer);
        saveTimer = null;
        if (!doc || !dirty || conflict) return saving || Promise.resolve();
        if (saving) return saving.then(save);
        const sent = doc;
        dirty = false;
        let failed = false;
        const body = {title: sent.title.trim() || '未命名讲义', document: sent.document, revision: sent.revision};
        saving = api(`/handouts/${sent.id}`, 'PUT', body).then(result => {
            sent.revision = result.revision;
            const listed = handouts.find(h => h.id === sent.id);
            if (listed) Object.assign(listed, {title: result.title, revision: result.revision, updated: result.updated, summary: result.summary});
        }).catch(err => {
            if (sent !== doc) return;
            dirty = failed = true;
            if (err.status === 409) {
                conflict = true;
                showConflict();
            } else {
                toast(err.message, 'error');
                saveTimer = setTimeout(save, 5000);
            }
        }).finally(() => {
            saving = null;
            setStatus(failed && !conflict ? 'error' : undefined);
            if (dirty && !conflict && !saveTimer) saveTimer = setTimeout(save, 1000);
        });
        setStatus();
        return saving;
    }
    async function flush() {
        if (!doc) return;
        clearTimeout(saveTimer);
        saveTimer = null;
        if (dirty && !conflict) await save();
        else if (saving) await saving;
    }
    function showConflict() {
        setStatus();
        ui.notice.hidden = false;
        ui.notice.replaceChildren(icon('fa-triangle-exclamation'),
            el('span', '这份讲义已在别处修改（同事或另一个标签页），你的修改尚未保存。'),
            button('另存为新讲义', 'mb-button mb-small mb-primary', async () => {
                try {
                    const copy = await api('/handouts', 'POST', {title: (doc.title.trim() || '未命名讲义') + '（我的修改）', folder_id: doc.folder_id, document: doc.document});
                    conflict = dirty = false;
                    ui.notice.hidden = true;
                    handouts.unshift(copy);
                    openHandout(copy.id, copy);
                    toast('已另存为新讲义', 'success');
                } catch (err) { toast(err.message, 'error'); }
            }),
            button('放弃修改并载入最新', 'mb-button mb-small', () => {
                conflict = dirty = false;
                ui.notice.hidden = true;
                openHandout(doc.id);
            }));
    }

    // ---------- Export ----------
    async function exportHandout(format, version, trigger) {
        await flush();
        if (dirty || conflict) return toast('请先处理未保存的修改再导出', 'error');
        trigger.disabled = true;
        trigger.setAttribute('aria-busy', 'true');
        toast(`正在生成${format === 'pdf' ? ' PDF' : ' Word'}（${version === 'teacher' ? '教师版' : '学生版'}）…`, 'info');
        try {
            const response = await fetch(`${BASE}/handouts/${doc.id}/export?format=${format}&version=${version}`);
            if (!response.ok) {
                const value = await response.json().catch(() => ({}));
                throw new Error(value.detail || '导出失败');
            }
            const blob = await response.blob();
            const header = response.headers.get('Content-Disposition') || '';
            const name = decodeURIComponent((/filename\*=UTF-8''([^;]+)/i.exec(header) || [])[1] || `讲义.${format}`);
            const link = el('a');
            link.href = URL.createObjectURL(blob);
            link.download = name;
            document.body.append(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(link.href), 30000);
        } catch (err) {
            toast(err.message.slice(0, 300), 'error');
        } finally {
            trigger.disabled = false;
            trigger.removeAttribute('aria-busy');
        }
    }

    function start() {
        if (!mount()) return;
        document.documentElement.dataset.handoutsReady = 'true';
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true}); else start();
})();
