/* 绘图 workspace: drawings of the current space, made in the self-hosted GeoGebra Classic.
   - library: thumbnails of the space's drawings (new, open, rename, copy, delete);
   - editor: GeoGebra with its own tools, menus and input bar (drag to adjust by hand),
     beside an AI conversation whose answers are GeoGebra commands run in place;
     each AI step can be undone, and a failed command is sent back once for a fix;
   - autosave against the server revision, as handouts do.
   The question editor gets "从绘图库插入": the drawing's PNG is uploaded through
   the bank's own image upload, so the question keeps its own copy. */
(() => {
    'use strict';
    const ctx = window.MathBankPlatform;
    if (!ctx) return;
    const $ = id => document.getElementById(id);
    const space = ctx.space;
    const canEdit = ['owner', 'editor'].includes(ctx.permission);
    const BASE = '/platform/api/spaces/' + space.id + '/drawings';
    const GEOGEBRA = '/platform/geogebra/5.4.933/';
    const physics = String(space.subject || '').includes('物理');
    const ui = {};
    let section, drawings = [], loaded = false, current = null, ggb = null, ggbLoader = null;
    let dirty = false, changes = 0, saving = null, saveTimer = null, conflict = false, loading = false, busy = false;
    let pendingImage = '', session = 0, fired = 0;
    const undo = new Map();

    // ---------- Helpers ----------
    function el(tag, text = '', className = '') {
        const node = document.createElement(tag);
        if (text) node.textContent = text;
        if (className) node.className = className;
        return node;
    }
    function icon(name) {
        const node = el('i', '', 'fa-solid ' + name);
        node.setAttribute('aria-hidden', 'true');
        return node;
    }
    function button(text, className, onclick, iconName, label) {
        const node = el('button', '', className);
        node.type = 'button';
        if (iconName) node.append(icon(iconName));
        if (text) node.append(document.createTextNode(text));
        if (label) { node.title = label; node.setAttribute('aria-label', label); }
        if (onclick) node.onclick = onclick;
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
    const toast = (message, type = 'info') => window.showToast && window.showToast(message, type);
    function when(seconds) {
        const date = new Date(seconds * 1000);
        const time = date.toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'});
        if (date.toDateString() === new Date().toDateString()) return '今天 ' + time;
        return date.toLocaleDateString('zh-CN', {month: 'numeric', day: 'numeric'}) + ' ' + time;
    }
    const imageUrl = drawing => `${BASE}/${drawing.id}/image.png?v=${drawing.revision}`;
    function dialogShell(id, title, description, iconName) {
        const dialog = el('dialog', '', 'mb-dialog mb-dr-dialog');
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
        heading.append(text, button('', 'mb-icon-button', () => dialog.close(), 'fa-xmark', '关闭'));
        const body = el('div', '', 'mb-dialog-body');
        dialog.append(heading, body);
        document.body.append(dialog);
        dialog.addEventListener('close', () => dialog.remove(), {once: true});
        return {dialog, body, focus: () => h2.focus()};
    }
    function ask(title, message, action, value) {
        const shell = dialogShell('drawingAsk', title, message, value === undefined ? 'fa-trash-can' : 'fa-pen');
        let field = null;
        if (value !== undefined) {
            const label = el('label');
            field = el('input');
            field.value = value;
            field.maxLength = 80;
            label.append(el('span', '标题'), field);
            shell.body.append(label);
        }
        const actions = el('div', '', 'mb-actions');
        let answer = null;
        const confirm = () => { answer = field ? field.value.trim() : true; shell.dialog.close(); };
        if (field) field.onkeydown = e => { if (e.key === 'Enter') confirm(); };
        actions.append(button('取消', 'mb-button', () => shell.dialog.close()),
            button(action, 'mb-button ' + (field ? 'mb-primary' : 'mb-danger'), confirm));
        shell.body.append(actions);
        return new Promise(resolve => {
            shell.dialog.addEventListener('close', () => resolve(answer), {once: true});
            shell.dialog.showModal();
            (field || shell).focus();
        });
    }

    // ---------- GeoGebra ----------
    function loadGeoGebra() {
        if (window.GGBApplet) return Promise.resolve();
        if (!ggbLoader) {
            ggbLoader = new Promise((resolve, reject) => {
                const script = el('script');
                script.src = GEOGEBRA + 'deployggb.js';
                script.onload = resolve;
                script.onerror = () => { ggbLoader = null; reject(new Error('GeoGebra 尚未安装：请管理员运行 scripts/fetch_geogebra.py 后重建镜像')); };
                document.head.append(script);
            });
        }
        return ggbLoader;
    }
    function startApplet(base64) {
        return new Promise(resolve => {
            ui.stage.replaceChildren();
            const host = el('div', '', 'mb-dr-host');
            host.id = 'mbDrawingHost';
            ui.stage.append(host);
            const box = ui.stage.getBoundingClientRect();
            const params = {
                appName: 'classic', id: 'mbDrawingApplet', language: 'zh',
                width: Math.max(320, Math.floor(box.width)), height: Math.max(320, Math.floor(box.height)),
                showToolBar: canEdit, showMenuBar: canEdit, showAlgebraInput: canEdit, allowStyleBar: canEdit,
                enableRightClick: canEdit, enableLabelDrags: canEdit, enableShiftDragZoom: true, showResetIcon: false,
                errorDialogsActive: false, showKeyboardOnFocus: 'false', useBrowserForJS: false, preventFocus: true,
                borderColor: 'transparent', appletOnLoad: api => resolve(api),
            };
            if (base64) params.ggbBase64 = base64;
            const applet = new window.GGBApplet(params, true);
            applet.setHTML5Codebase(GEOGEBRA + 'HTML5/5.0/web3d/');
            applet.inject('mbDrawingHost');
        });
    }
    function changed() {
        fired += 1;
        if (loading || !current) return;
        dirty = true;
        changes += 1;
        setStatus('有修改未保存');
        scheduleSave();
    }
    // Style edits (colour, line, labels) and view changes reach only the client listener.
    const NOT_A_CHANGE = new Set(['select', 'deselect', 'setMode', 'mouseDown', 'openMenu', 'openDialog', 'closeDialog',
                                  'editorStart', 'editorStop', 'editorKeyTyped', 'undo', 'redo']);
    function clientEvent(event) {
        const type = Array.isArray(event) ? event[0] : event && event.type;
        if (NOT_A_CHANGE.has(type)) { fired += 1; return; }
        changed();
    }
    function listen(api) {
        for (const name of ['registerAddListener', 'registerRemoveListener', 'registerUpdateListener',
                            'registerRenameListener', 'registerClearListener']) {
            if (typeof api[name] === 'function') api[name](changed);
        }
        if (typeof api.registerClientListener === 'function') api.registerClientListener(clientEvent);
    }
    function plain() {
        ggb.setAxesVisible(false, false);
        ggb.setGridVisible(false);
    }
    // What the model sees of the drawing: each object's name, type and definition.
    function describe() {
        const lines = [];
        for (const name of ggb.getAllObjectNames().slice(0, 300)) {
            const definition = ggb.getCommandString(name, false);
            const hidden = ggb.getVisible(name) ? '' : '，隐藏';
            lines.push(`${name}（${ggb.getObjectType(name)}${hidden}）：${definition ? definition + ' ＝ ' : ''}${ggb.getValueString(name)}`);
        }
        const text = lines.join('\n');
        return text.length > 20000 ? text.slice(0, 20000) + '\n…（其余对象略）' : text;
    }
    // Frame every point with even scales, so a fresh AI drawing is in view and undistorted.
    function fitView() {
        const points = ggb.getAllObjectNames('point').filter(n => Number.isFinite(ggb.getXcoord(n)) && Number.isFinite(ggb.getYcoord(n)));
        if (!points.length) return;
        const xs = points.map(n => ggb.getXcoord(n)), ys = points.map(n => ggb.getYcoord(n));
        let [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
        const span = Math.max(x1 - x0, y1 - y0, 2);
        x0 -= span * .2; x1 += span * .2; y0 -= span * .2; y1 += span * .2;
        frame(x0, x1, y0, y1, true);
    }
    // Geometry needs one unit on both axes (a circle must stay round); a function graph may stretch.
    function frame(x0, x1, y0, y1, even) {
        if (!even) { ggb.setCoordSystem(x0, x1, y0, y1); return; }
        let ratio = 1.6;
        try {
            const view = JSON.parse(ggb.getViewProperties(1));
            if (view.width > 0 && view.height > 0) ratio = view.width / view.height;
        } catch (err) { /* older builds: keep the default ratio */ }
        const width = Math.max(x1 - x0, (y1 - y0) * ratio), height = width / ratio;
        const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
        ggb.setCoordSystem(cx - width / 2, cx + width / 2, cy - height / 2, cy + height / 2);
    }
    function applyView(view, reset) {
        if (view && typeof view.axes === 'boolean') ggb.setAxesVisible(view.axes, view.axes);
        if (view && typeof view.grid === 'boolean') ggb.setGridVisible(view.grid);
        if (view && 'xmin' in view) frame(view.xmin, view.xmax, view.ymin, view.ymax, view.axes !== true);
        else if (reset) fitView();
    }
    // evalCommand reports success only for commands that create an object; SetColor, ShowLabel,
    // SetCoords and the like return false even when they work. Every command that does work fires
    // at least one GeoGebra event (client, add, remove, update or rename) and a failed one fires none.
    function run(commands) {
        const before = new Set(ggb.getAllObjectNames());
        const named = new Set();
        const results = commands.map(command => {
            const own = command.match(/^\s*([A-Za-z_][\w']*)\s*(?:\(\s*[A-Za-z_][\w,\s]*\))?\s*[=:]/);
            if (own) named.add(own[1]);
            fired = 0;
            const ok = Boolean(ggb.evalCommand(command));
            return {command, ok: ok || fired > 0};
        });
        // Polygon and similar commands name their sides a, b, c …; those names only clutter a printed figure.
        for (const name of ggb.getAllObjectNames()) {
            if (!before.has(name) && !named.has(name) && ggb.getObjectType(name) !== 'point') ggb.setLabelVisible(name, false);
        }
        return results;
    }

    // ---------- Workspace shell and navigation ----------
    function mount() {
        const main = document.querySelector('#appContentShell > main');
        if (!main || $('drawingWorkspaceSection')) return false;
        section = el('section', '', 'mb-drawings hidden');
        section.id = 'drawingWorkspaceSection';
        section.setAttribute('aria-label', '绘图');
        ui.library = el('div', '', 'mb-dr-library');
        const head = el('header', '', 'mb-dr-head');
        const titles = el('div');
        titles.append(el('h1', '绘图'), el('p', `${space.stage}${space.subject} · 用 GeoGebra 画图，可让 AI 按描述或参考图作图，再拖动调整；保存后可在编辑题目时插入。`));
        ui.search = el('input');
        ui.search.type = 'search';
        ui.search.placeholder = '搜索绘图';
        ui.search.setAttribute('aria-label', '搜索绘图');
        ui.search.oninput = renderList;
        head.append(titles, ui.search);
        if (canEdit) head.append(button('新建绘图', 'mb-button mb-primary', createDrawing, 'fa-plus'));
        ui.list = el('div', '', 'mb-dr-grid');
        ui.list.setAttribute('role', 'list');
        ui.library.append(head, ui.list);
        ui.editor = el('div', '', 'mb-dr-editor');
        ui.editor.hidden = true;
        buildEditor();
        section.append(ui.library, ui.editor);
        main.append(section);

        const item = el('button', '', 'app-nav-item');
        item.id = 'appNavDrawings';
        item.type = 'button';
        item.dataset.appNavTarget = 'drawings';
        item.append(icon('fa-compass-drafting'), el('span', '绘图'));
        item.onclick = () => window.selectWorkspace('drawings', '绘图');
        ($('appNavHandouts') || $('appNavRecords') || $('appNavPaper'))?.after(item);

        const previous = window.selectWorkspace;
        window.selectWorkspace = function (id, name) {
            if (id !== 'drawings') {
                section.classList.add('hidden');
                if (dirty) save();
                return previous(id, name);
            }
            document.documentElement.classList.remove('init-ws-dashboard', 'init-ws-paper');
            document.querySelectorAll('#appContentShell > main > [id$="WorkspaceSection"]').forEach(s => s.classList.add('hidden'));
            section.classList.remove('hidden');
            if (window.PaperStore) window.PaperStore.activeWorkspace = 'drawings';
            document.body.dataset.workspace = 'drawings';
            window.setAppNavigationActive?.('drawings');
            if ($('currentWorkspaceName')) $('currentWorkspaceName').textContent = '绘图';
            if (!loaded) loadLibrary();
            if (ggb && !ui.editor.hidden) requestAnimationFrame(resize);
        };
        const navigate = window.navigatePlatformSpace;
        if (navigate) window.navigatePlatformSpace = async id => { await flush(); navigate(id); };
        window.addEventListener('beforeunload', e => {
            if (dirty || saving || busy) { save(); e.preventDefault(); e.returnValue = ''; }
        });
        window.openDrawingWorkspace = () => window.selectWorkspace('drawings', '绘图');
        return true;
    }

    // ---------- Library ----------
    async function loadLibrary() {
        loaded = true;
        ui.list.replaceChildren(el('p', '正在读取…', 'mb-dr-muted'));
        try {
            drawings = await api('');
            renderList();
        } catch (err) {
            loaded = false;
            ui.list.replaceChildren(el('p', err.message, 'mb-dr-muted'));
        }
    }
    function renderList() {
        const query = ui.search.value.trim().toLowerCase();
        const shown = drawings.filter(d => !query || d.title.toLowerCase().includes(query));
        if (!shown.length) {
            const empty = el('div', '', 'mb-dr-empty');
            empty.append(icon('fa-compass-drafting'), el('p', query ? '没有找到匹配的绘图' : '还没有绘图'),
                el('p', canEdit ? '点“新建绘图”开始：可以直接用 GeoGebra 的工具画，也可以在右侧告诉 AI 要画什么。' : '这个空间里还没有保存的绘图。', 'mb-dr-muted'));
            ui.list.replaceChildren(empty);
            return;
        }
        ui.list.replaceChildren(...shown.map(card));
    }
    function card(drawing) {
        const node = el('div', '', 'mb-dr-card');
        node.setAttribute('role', 'listitem');
        const open = el('button', '', 'mb-dr-card-open');
        open.type = 'button';
        const thumb = el('div', '', 'mb-dr-thumb');
        if (drawing.has_image) {
            const image = el('img');
            image.alt = drawing.title;
            image.loading = 'lazy';
            image.src = imageUrl(drawing);
            thumb.append(image);
        } else {
            thumb.append(icon('fa-compass-drafting'));
        }
        open.append(thumb, el('strong', drawing.title),
            el('span', `${when(drawing.updated)}${drawing.updated_by_name ? ' · ' + drawing.updated_by_name : ''}`, 'mb-dr-muted'));
        open.onclick = () => openDrawing(drawing.id);
        node.append(open);
        if (canEdit) {
            const side = el('div', '', 'mb-dr-card-actions');
            side.append(
                button('', 'mb-icon-button', () => rename(drawing), 'fa-pen', '重命名'),
                button('', 'mb-icon-button', () => copy(drawing), 'fa-copy', '复制'),
                button('', 'mb-icon-button', () => remove(drawing), 'fa-trash-can', '删除'));
            node.append(side);
        }
        return node;
    }
    async function createDrawing() {
        try {
            const created = await api('', 'POST', {title: '未命名绘图'});
            drawings.unshift(created);
            renderList();
            openDrawing(created.id);
        } catch (err) { toast(err.message, 'error'); }
    }
    async function rename(drawing) {
        const value = await ask('重命名绘图', '标题会显示在绘图列表和插图选择中。', '保存', drawing.title);
        if (value === null) return;
        try {
            Object.assign(drawing, await api(`/${drawing.id}`, 'PATCH', {title: value}));
            renderList();
        } catch (err) { toast(err.message, 'error'); }
    }
    async function copy(drawing) {
        try {
            drawings.unshift(await api(`/${drawing.id}/copy`, 'POST'));
            renderList();
            toast('已复制');
        } catch (err) { toast(err.message, 'error'); }
    }
    async function remove(drawing) {
        if (!await ask('删除绘图', `删除“${drawing.title}”？已经插入题目的图片不受影响。`, '删除')) return;
        try {
            await api(`/${drawing.id}`, 'DELETE');
            drawings = drawings.filter(d => d.id !== drawing.id);
            renderList();
        } catch (err) { toast(err.message, 'error'); }
    }

    // ---------- Editor ----------
    function buildEditor() {
        const bar = el('header', '', 'mb-dr-toolbar');
        ui.back = button('绘图列表', 'mb-button mb-small', closeEditor, 'fa-arrow-left');
        ui.title = el('input', '', 'mb-dr-title-input');
        ui.title.maxLength = 80;
        ui.title.setAttribute('aria-label', '绘图标题');
        ui.title.disabled = !canEdit;
        ui.title.oninput = () => { if (current) { current.title = ui.title.value; changed(); } };
        ui.status = el('span', '', 'mb-dr-save');
        ui.status.setAttribute('role', 'status');
        ui.save = button('保存', 'mb-button mb-small mb-primary', () => save(), 'fa-floppy-disk');
        ui.save.hidden = !canEdit;
        const exports = el('div', '', 'mb-dr-exports');
        exports.append(
            button('图片', 'mb-button mb-small', downloadPng, 'fa-image', '下载 PNG 图片'),
            button('.ggb', 'mb-button mb-small', downloadGgb, 'fa-file-arrow-down', '下载 GeoGebra 文件，可用桌面版打开'),
            button('TikZ', 'mb-button mb-small', copyTikz, 'fa-code', '复制 TikZ 代码，可贴进“新增绘图”'));
        bar.append(ui.back, ui.title, ui.status, ui.save, exports);
        ui.conflict = el('div', '', 'mb-dr-conflict');
        ui.conflict.hidden = true;
        const body = el('div', '', 'mb-dr-body');
        ui.stage = el('div', '', 'mb-dr-stage');
        body.append(ui.stage, buildChat());
        ui.editor.append(bar, ui.conflict, body);
        new ResizeObserver(() => resize()).observe(ui.stage);
    }
    function buildChat() {
        const aside = el('aside', '', 'mb-dr-chat');
        aside.setAttribute('aria-label', 'AI 作图');
        const head = el('div', '', 'mb-dr-chat-head');
        head.append(el('strong', 'AI 作图'), el('span', '用文字描述，或附上参考图；AI 写 GeoGebra 指令作图，之后可以直接拖动调整。', 'mb-dr-muted'));
        ui.messages = el('div', '', 'mb-dr-messages');
        ui.messages.setAttribute('aria-live', 'polite');
        const composer = el('div', '', 'mb-dr-composer');
        ui.attachment = el('div', '', 'mb-dr-attachment');
        ui.attachment.hidden = true;
        ui.prompt = el('textarea');
        ui.prompt.rows = 3;
        ui.prompt.maxLength = 3000;
        ui.prompt.placeholder = physics
            ? '例如：画一个倾角 30° 的斜面，斜面上放一个物块，画出重力、支持力和摩擦力'
            : '例如：画△ABC，D 为 BC 中点，连接 AD，并作△ABC 的外接圆';
        ui.prompt.setAttribute('aria-label', '告诉 AI 要画什么');
        ui.prompt.onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } };
        ui.prompt.onpaste = e => {
            const file = [...(e.clipboardData?.files || [])].find(f => f.type.startsWith('image/'));
            if (file) { e.preventDefault(); attach(file); }
        };
        const file = el('input');
        file.type = 'file';
        file.accept = 'image/png,image/jpeg,image/webp';
        file.hidden = true;
        file.onchange = () => { if (file.files[0]) attach(file.files[0]); file.value = ''; };
        const actions = el('div', '', 'mb-dr-composer-actions');
        ui.attach = button('参考图', 'mb-button mb-small', () => file.click(), 'fa-paperclip', '附上一张参考图（也可以直接粘贴截图）');
        ui.send = button('发送', 'mb-button mb-small mb-primary', send, 'fa-paper-plane');
        actions.append(ui.attach, ui.send);
        composer.append(ui.attachment, ui.prompt, file, actions);
        if (!canEdit) {
            ui.prompt.disabled = ui.attach.disabled = ui.send.disabled = true;
            ui.prompt.placeholder = '只读：可以查看和下载，不能修改或使用 AI 作图。';
        }
        aside.append(head, ui.messages, composer);
        return aside;
    }
    function setStatus(text) { ui.status.textContent = text; }
    function resize() {
        if (!ggb || ui.editor.hidden || section.classList.contains('hidden')) return;
        const box = ui.stage.getBoundingClientRect();
        if (box.width > 100 && box.height > 100) ggb.setSize(Math.floor(box.width), Math.floor(box.height));
    }
    async function openDrawing(id) {
        if (dirty) await flush();
        const token = ++session;
        ui.library.hidden = true;
        ui.editor.hidden = false;
        ui.conflict.hidden = true;
        conflict = false;
        undo.clear();
        setStatus('正在打开…');
        ui.stage.replaceChildren(el('p', '正在载入 GeoGebra…', 'mb-dr-muted mb-dr-loading'));
        try {
            const [drawing] = await Promise.all([api(`/${id}`), loadGeoGebra()]);
            if (token !== session) return;
            current = drawing;
            ui.title.value = drawing.title;
            renderMessages();
            loading = true;
            const applet = await startApplet(drawing.ggb);
            if (token !== session) return;
            ggb = applet;
            if (!drawing.ggb) plain();
            listen(ggb);
            setTimeout(() => { loading = false; }, 300);
            dirty = false;
            setStatus(canEdit ? '已保存 ' + when(drawing.updated) : '只读');
            if (canEdit) ui.prompt.focus();
        } catch (err) {
            ui.stage.replaceChildren(el('p', err.message, 'mb-dr-muted mb-dr-loading'));
            setStatus('');
        }
    }
    async function closeEditor() {
        await flush();
        session += 1;
        if (ggb && typeof ggb.remove === 'function') { try { ggb.remove(); } catch (err) { /* already gone */ } }
        ggb = null;
        current = null;
        ui.stage.replaceChildren();
        ui.editor.hidden = true;
        ui.library.hidden = false;
        renderList();
    }

    // ---------- Saving ----------
    function scheduleSave(delay = 2500) {
        if (!canEdit || conflict) return;
        clearTimeout(saveTimer);
        saveTimer = setTimeout(() => { if (!busy) save(); else scheduleSave(); }, delay);
    }
    function snapshotPng() {
        if (!ggb.getAllObjectNames().length) return '';
        return String(ggb.getPNGBase64(2, false, 300) || '').replace(/^data:image\/png;base64,/, '');
    }
    function save() {
        if (!canEdit || !current || !ggb || conflict) return Promise.resolve();
        if (saving) return saving.then(() => (dirty ? save() : undefined));
        clearTimeout(saveTimer);
        const counter = changes;
        const target = current;
        const payload = {title: ui.title.value.trim() || '未命名绘图', revision: target.revision, chat: target.chat || [],
                         ggb: ggb.getBase64(), png: snapshotPng()};
        setStatus('保存中…');
        saving = api(`/${target.id}`, 'PUT', payload).then(saved => {
            target.revision = saved.revision;
            const listed = drawings.find(d => d.id === saved.id);
            if (listed) Object.assign(listed, saved); else drawings.unshift(saved);
            if (changes === counter) { dirty = false; setStatus('已保存 ' + when(saved.updated)); } else scheduleSave(800);
        }).catch(err => {
            if (err.status === 409) {
                conflict = true;
                showConflict();
            } else {
                setStatus('保存失败，稍后重试');
                toast(err.message, 'error');
                scheduleSave(6000);
            }
        }).finally(() => { saving = null; });
        return saving;
    }
    async function flush() {
        clearTimeout(saveTimer);
        if (saving) await saving;
        if (dirty && !conflict) await save();
    }
    function showConflict() {
        setStatus('未保存');
        ui.conflict.replaceChildren(el('span', '这张绘图已被其他人修改。为避免覆盖，自动保存已停止。'),
            button('另存为新绘图', 'mb-button mb-small mb-primary', saveAsNew),
            button('放弃我的修改，载入最新版本', 'mb-button mb-small', () => { dirty = false; openDrawing(current.id); }));
        ui.conflict.hidden = false;
    }
    async function saveAsNew() {
        try {
            const created = await api('', 'POST', {title: (ui.title.value.trim() || '未命名绘图') + '（另存）', chat: current.chat,
                                                  ggb: ggb.getBase64(), png: snapshotPng()});
            drawings.unshift(created);
            dirty = false;
            conflict = false;
            openDrawing(created.id);
        } catch (err) { toast(err.message, 'error'); }
    }

    // ---------- Exports ----------
    function download(name, blob) {
        const link = el('a');
        link.href = URL.createObjectURL(blob);
        link.download = name;
        document.body.append(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(link.href), 30000);
    }
    const bytes = base64 => Uint8Array.from(atob(base64), c => c.charCodeAt(0));
    const fileName = ext => (ui.title.value.trim() || '绘图').replace(/[\\/:*?"<>|]/g, '_') + ext;
    function downloadPng() {
        if (!ggb) return;
        const png = snapshotPng();
        if (!png) { toast('画布是空的', 'warning'); return; }
        download(fileName('.png'), new Blob([bytes(png)], {type: 'image/png'}));
    }
    function downloadGgb() {
        if (ggb) download(fileName('.ggb'), new Blob([bytes(ggb.getBase64())], {type: 'application/vnd.geogebra.file'}));
    }
    function copyTikz() {
        if (!ggb) return;
        ggb.exportPGF(async source => {
            const match = String(source || '').match(/\\begin\{tikzpicture\}[\s\S]*\\end\{tikzpicture\}/);
            const text = match ? match[0] : String(source || '');
            try {
                await navigator.clipboard.writeText(text);
                toast('TikZ 代码已复制，可贴进题目编辑器的“新增绘图”');
            } catch (err) {
                download(fileName('.tex'), new Blob([text], {type: 'text/plain'}));
            }
        });
    }

    // ---------- AI conversation ----------
    function attach(file) {
        if (!/^image\/(png|jpeg|webp)$/.test(file.type) || file.size > 5 * 1024 * 1024) {
            toast('参考图请用 5 MB 以内的 PNG、JPEG 或 WebP', 'warning');
            return;
        }
        const reader = new FileReader();
        reader.onload = () => {
            pendingImage = String(reader.result);
            const preview = el('img');
            preview.src = pendingImage;
            preview.alt = '参考图';
            ui.attachment.replaceChildren(preview, button('', 'mb-icon-button', () => { pendingImage = ''; ui.attachment.hidden = true; }, 'fa-xmark', '去掉参考图'));
            ui.attachment.hidden = false;
        };
        reader.readAsDataURL(file);
    }
    function renderMessages() {
        const chat = current?.chat || [];
        if (!chat.length) {
            const hint = el('div', '', 'mb-dr-hint');
            hint.append(el('p', '可以这样说：'));
            const examples = physics
                ? ['画小球从斜面顶端滑下的示意图，标出初速度方向', '画出 v-t 图像：0～4 s 匀加速到 8 m/s，之后匀速到 6 s', '画两个平行导轨，中间是垂直纸面向里的匀强磁场']
                : ['画△ABC，AB=AC，作 BC 边上的高 AD', '画抛物线 y=x²-2x-3，标出与坐标轴的交点', '把点 C 移到圆上，并连接 OC'];
            for (const text of examples) {
                const example = button(text, 'mb-dr-example', () => { ui.prompt.value = text; ui.prompt.focus(); });
                example.disabled = !canEdit;
                hint.append(example);
            }
            ui.messages.replaceChildren(hint);
            return;
        }
        ui.messages.replaceChildren(...chat.map((message, index) => bubble(message, index)));
        ui.messages.scrollTop = ui.messages.scrollHeight;
    }
    function bubble(message, index) {
        const node = el('div', '', 'mb-dr-message mb-dr-' + message.role);
        node.append(el('p', message.text || (message.role === 'assistant' ? '已作图。' : '')));
        if (message.role === 'assistant') {
            if (message.note) node.append(el('p', message.note, 'mb-dr-note'));
            if (message.commands?.length) {
                const details = el('details');
                details.append(el('summary', `GeoGebra 指令（${message.commands.length} 条）`));
                const code = el('pre');
                code.textContent = message.commands.join('\n');
                details.append(code);
                node.append(details);
            }
            if (undo.has(index) && canEdit) {
                node.append(button('撤销这一步', 'mb-button mb-small', () => revert(index), 'fa-rotate-left'));
            }
        }
        return node;
    }
    function revert(index) {
        const before = undo.get(index);
        if (!before || !ggb) return;
        loading = true;
        ggb.setBase64(before, () => {
            listen(ggb);
            setTimeout(() => { loading = false; changed(); }, 200);
        });
        undo.delete(index);
        current.chat[index].note = (current.chat[index].note ? current.chat[index].note + ' ' : '') + '（已撤销）';
        renderMessages();
    }
    function forModel(extra) {
        return [...(current.chat || []).map(m => ({role: m.role, text: m.text, commands: m.commands || []})), ...extra].slice(-12);
    }
    async function send() {
        if (!canEdit || busy || !ggb || !current) return;
        const text = ui.prompt.value.trim();
        if (!text) { ui.prompt.focus(); return; }
        const token = session;
        busy = true;
        ui.send.disabled = true;
        ui.send.setAttribute('aria-busy', 'true');
        const image = pendingImage;
        current.chat = [...(current.chat || []), {role: 'user', text: image ? text + '（附参考图）' : text}];
        ui.prompt.value = '';
        pendingImage = '';
        ui.attachment.hidden = true;
        renderMessages();
        const waiting = el('div', '', 'mb-dr-message mb-dr-assistant mb-dr-waiting');
        const clock = el('p', 'AI 正在作图…');
        waiting.append(clock);
        ui.messages.append(waiting);
        ui.messages.scrollTop = ui.messages.scrollHeight;
        const started = Date.now();
        const ticker = setInterval(() => { clock.textContent = `AI 正在作图… ${Math.round((Date.now() - started) / 1000)} 秒`; }, 1000);
        try {
            const before = ggb.getBase64();
            const answer = await api('/ai', 'POST', {messages: forModel([]), construction: describe(), image});
            if (token !== session || !ggb) return;
            loading = true;
            let results = [];
            try {
                if (answer.reset) { ggb.newConstruction(); plain(); }
                results = run(answer.commands);
                applyView(answer.view, answer.reset);
            } finally { loading = false; }
            let failed = results.filter(r => !r.ok);
            let note = '';
            if (failed.length) {
                clock.textContent = `有 ${failed.length} 条指令没能执行，正在让 AI 修正…`;
                const fix = await api('/ai', 'POST', {construction: describe(), image: '', messages: forModel([
                    {role: 'assistant', text: answer.reply, commands: answer.commands},
                    {role: 'user', text: '下面这些指令在 GeoGebra 里执行失败，请检查指令名称、参数个数和对象名，'
                        + '只返回需要补执行的修正指令（已经成功的不要重复），reset 必须为 false：\n'
                        + failed.map(r => r.command).join('\n')}])}).catch(() => null);
                if (fix && token === session && ggb) {
                    loading = true;
                    try { results = results.filter(r => r.ok).concat(run(fix.commands)); applyView(fix.view, false); } finally { loading = false; }
                    failed = results.filter(r => !r.ok);
                    note = '已自动修正一次。';
                }
                if (failed.length) note += `有 ${failed.length} 条指令没能执行：${failed.map(r => r.command).join('；').slice(0, 300)}`;
            }
            if (answer.dropped) note += ` 已忽略 ${answer.dropped} 条不安全或过长的指令。`;
            current.chat.push({role: 'assistant', text: answer.reply || '已按要求作图。', commands: results.filter(r => r.ok).map(r => r.command),
                               note: note.trim()});
            undo.set(current.chat.length - 1, before);
            changed();
        } catch (err) {
            if (token === session) {
                current.chat.push({role: 'assistant', text: '没有完成：' + err.message});
                changed();
            }
        } finally {
            clearInterval(ticker);
            busy = false;
            ui.send.disabled = !canEdit;
            ui.send.removeAttribute('aria-busy');
            if (token === session) renderMessages();
        }
    }

    // ---------- 从绘图库插入 (question editor) ----------
    function mountPicker() {
        const targets = [['illustrationDropZone', 'uploadIllustration', '题干'], ['imageAnswerDropZone', 'uploadAnswerImage', '解析']];
        for (const [zoneId, upload, field] of targets) {
            const zone = $(zoneId);
            if (!zone || zone.parentElement.querySelector(`[data-drawing-insert="${upload}"]`)) continue;
            const trigger = button('从绘图库插入', 'glass-btn mb-dr-insert', () => pick(upload, field), 'fa-compass-drafting');
            trigger.dataset.drawingInsert = upload;
            zone.after(trigger);
        }
    }
    async function pick(upload, field) {
        const shell = dialogShell('drawingPicker', '从绘图库插入', `选择一张已保存的绘图，作为图片插入${field}（题目保存一份副本，之后修改绘图不影响题目）。`, 'fa-compass-drafting');
        const search = el('input');
        search.type = 'search';
        search.placeholder = '搜索绘图';
        search.setAttribute('aria-label', '搜索绘图');
        const grid = el('div', '', 'mb-dr-grid mb-dr-picker');
        grid.append(el('p', '正在读取…', 'mb-dr-muted'));
        shell.body.append(search, grid);
        shell.dialog.showModal();
        shell.focus();
        let items = [];
        const render = () => {
            const query = search.value.trim().toLowerCase();
            const shown = items.filter(d => d.has_image && (!query || d.title.toLowerCase().includes(query)));
            if (!shown.length) {
                grid.replaceChildren(el('p', items.length ? '没有可插入的绘图（需要先在绘图区保存过）' : '还没有绘图，可以在侧边栏“绘图”中新建。', 'mb-dr-muted'));
                return;
            }
            grid.replaceChildren(...shown.map(drawing => {
                const choice = el('button', '', 'mb-dr-card-open mb-dr-card');
                choice.type = 'button';
                const thumb = el('div', '', 'mb-dr-thumb');
                const image = el('img');
                image.src = imageUrl(drawing);
                image.alt = drawing.title;
                thumb.append(image);
                choice.append(thumb, el('strong', drawing.title), el('span', when(drawing.updated), 'mb-dr-muted'));
                choice.onclick = () => insert(drawing, upload, shell.dialog, choice);
                return choice;
            }));
        };
        search.oninput = render;
        try {
            items = await api('');
            render();
        } catch (err) {
            grid.replaceChildren(el('p', err.message, 'mb-dr-muted'));
        }
    }
    async function insert(drawing, upload, dialog, choice) {
        const handler = window[upload];
        if (typeof handler !== 'function') { toast('当前页面不支持插入图片', 'error'); return; }
        choice.disabled = true;
        try {
            const response = await fetch(`${BASE}/${drawing.id}/image.png`);
            if (!response.ok) throw new Error('读取绘图图片失败');
            const blob = await response.blob();
            const name = 'drawing-' + drawing.id + '.png';
            handler(new File([blob], name, {type: 'image/png'}));
            dialog.close();
        } catch (err) {
            choice.disabled = false;
            toast(err.message, 'error');
        }
    }

    function start() {
        if (!mount()) return;
        mountPicker();
        document.documentElement.dataset.drawingsReady = 'true';
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true}); else start();
})();
