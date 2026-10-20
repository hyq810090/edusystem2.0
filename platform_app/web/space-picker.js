/* Sidebar: the space switcher (one card in the sidebar, a header button on
   phones), a searchable picker grouped by stage and subject, navigation that
   never silently drops an unsaved import or edit, and collapsing the sidebar
   to icons for a wider workspace (kept in a cookie the server applies). */
(() => {
    'use strict';
    const ctx = window.MathBankPlatform;
    if (!ctx) return;
    const $ = id => document.getElementById(id);
    const KINDS = {personal: '个人空间', team: '团队共享', public: '公共空间'};
    const GRANTS = {owner: '管理', editor: '编辑', viewer: '只读'};
    let spaces = [], anchor = null;

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
    const emblem = space => el('span', Array.from(space.subject)[0] || '·', 'mb-emblem');

    function mountCollapse() {
        const root = document.documentElement;
        const toggle = el('button', '', 'mb-nav-collapse');
        toggle.type = 'button';
        const apply = collapsed => {
            root.dataset.nav = collapsed ? 'collapsed' : 'expanded';
            toggle.replaceChildren(icon(collapsed ? 'fa-angles-right' : 'fa-angles-left'));
            toggle.setAttribute('aria-label', collapsed ? '展开侧边栏' : '收起侧边栏');
            toggle.title = toggle.getAttribute('aria-label');
            toggle.setAttribute('aria-expanded', String(!collapsed));
            if (!collapsed) document.querySelectorAll('#appNavigation [data-nav-title]').forEach(item => {
                item.removeAttribute('title');
                delete item.dataset.navTitle;
            });
        };
        // Icons alone need their names as tooltips (items such as 我的 are added later).
        $('appNavigation').addEventListener('mouseover', e => {
            const item = e.target.closest('.app-nav-item, .mb-space-switch');
            if (!item || root.dataset.nav !== 'collapsed' || item.title) return;
            item.title = item.querySelector('span:not(.app-nav-badge), strong')?.textContent || '';
            item.dataset.navTitle = '1';
        });
        toggle.onclick = () => {
            const collapsed = root.dataset.nav !== 'collapsed';
            document.cookie = `mb_nav=${collapsed ? 'collapsed' : 'expanded'}; path=/; max-age=31536000; SameSite=Lax`;
            apply(collapsed);
            // Paper pagination and option grids measure their width on resize.
            window.dispatchEvent(new Event('resize'));
        };
        document.querySelector('.app-nav-brand').append(toggle);
        apply(root.dataset.nav === 'collapsed');
    }

    function start() {
        const space = ctx.space;
        const trigger = el('button', '', 'mb-space-switch');
        trigger.type = 'button';
        trigger.setAttribute('aria-haspopup', 'dialog');
        trigger.setAttribute('aria-expanded', 'false');
        trigger.setAttribute('aria-label', `当前空间：${space.name}，${space.stage}${space.subject}，点击切换空间`);
        const copy = el('span');
        copy.append(el('strong', space.name), el('small', `${space.stage}${space.subject} · ${space.textbook}`),
            el('small', `${KINDS[space.kind]} · ${GRANTS[ctx.permission]}`));
        trigger.append(emblem(space), copy, icon('fa-chevron-down'));
        document.querySelector('.app-nav-brand').after(trigger);
        mountCollapse();

        const mobile = el('button', '', 'mb-space-switch-mobile');
        mobile.type = 'button';
        mobile.setAttribute('aria-haspopup', 'dialog');
        mobile.setAttribute('aria-expanded', 'false');
        mobile.setAttribute('aria-label', '切换空间');
        mobile.append(el('strong', space.name), el('span', `${space.stage}${space.subject}`), icon('fa-chevron-down'));
        document.getElementById('appLogoMark')?.after(mobile);

        const dialog = el('dialog', '', 'mb-space-picker');
        dialog.setAttribute('aria-labelledby', 'spacePickerTitle');
        // Keep the native modal manager's global key handling out of this dialog.
        dialog.addEventListener('keydown', e => { if (['Tab', 'Escape', 'Esc'].includes(e.key)) e.stopPropagation(); });
        const heading = el('div', '', 'mb-picker-heading');
        const title = el('h2', '切换空间');
        title.id = 'spacePickerTitle';
        title.tabIndex = -1;
        const close = el('button', '', 'mb-icon-button');
        close.type = 'button';
        close.setAttribute('aria-label', '关闭');
        close.append(icon('fa-xmark'));
        close.onclick = () => dialog.close();
        heading.append(title, close);
        const searchBox = el('label', '', 'mb-picker-search');
        const search = el('input');
        search.type = 'search';
        search.placeholder = '搜索空间、学科、学段或教材';
        search.setAttribute('aria-label', '搜索空间');
        searchBox.append(icon('fa-magnifying-glass'), search);
        const list = el('div', '', 'mb-picker-list');
        list.setAttribute('role', 'list');
        const footer = el('div', '', 'mb-picker-footer');
        const manage = el('button', '管理空间', 'mb-button');
        manage.type = 'button';
        manage.prepend(icon('fa-sliders'));
        manage.onclick = () => { dialog.close(); window.openMyWorkspace?.('spaces'); };
        const create = el('button', '新建空间', 'mb-button mb-primary');
        create.type = 'button';
        create.prepend(icon('fa-plus'));
        create.onclick = () => { dialog.close(); window.openMyWorkspace?.('spaces'); $('newSpace')?.click(); };
        footer.append(manage, create);
        dialog.append(heading, searchBox, list, footer);
        document.body.append(dialog);
        dialog.addEventListener('close', () => [trigger, mobile].forEach(t => t.setAttribute('aria-expanded', 'false')));
        dialog.addEventListener('click', e => {
            const r = dialog.getBoundingClientRect();
            if (e.target === dialog && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)) dialog.close();
        });

        function position() {
            if (innerWidth > 768 && anchor) {
                const r = anchor.getBoundingClientRect();
                dialog.style.left = Math.min(r.right + 12, innerWidth - 392) + 'px';
                dialog.style.top = Math.max(12, Math.min(r.top, innerHeight - 640)) + 'px';
            } else {
                dialog.style.left = dialog.style.top = '';
            }
        }
        function render() {
            const query = search.value.trim().toLocaleLowerCase();
            const visible = spaces.filter(s => !query ||
                `${s.name} ${s.subject} ${s.stage} ${s.textbook} ${KINDS[s.kind]}`.toLocaleLowerCase().includes(query));
            list.replaceChildren();
            let group = '';
            for (const item of visible) {
                const key = `${item.stage} · ${item.subject}`;
                if (key !== group) list.append(el('h3', group = key));
                const choice = el('button', '', 'mb-picker-item');
                choice.type = 'button';
                choice.setAttribute('role', 'listitem');
                const text = el('span');
                text.append(el('strong', item.name), el('small', `${item.textbook} · ${KINDS[item.kind]} · ${GRANTS[item.permission]}`));
                choice.append(emblem(item), text);
                if (item.id === space.id) {
                    choice.setAttribute('aria-current', 'true');
                    choice.append(icon('fa-check'));
                }
                choice.onclick = () => { dialog.close(); window.navigatePlatformSpace(item.id); };
                list.append(choice);
            }
            if (!visible.length) list.append(el('p', spaces.length ? '没有匹配的空间。' : '正在加载空间…', 'mb-status'));
        }
        async function refresh() {
            try {
                const response = await fetch('/platform/api/spaces');
                if (!response.ok) throw new Error();
                spaces = await response.json();
                render();
            } catch (_) {
                list.replaceChildren(el('p', '空间加载失败，请关闭后重试。', 'mb-status'));
            }
        }
        function open(source) {
            anchor = source;
            search.value = '';
            render();
            position();
            dialog.showModal();
            source.setAttribute('aria-expanded', 'true');
            search.focus();
            refresh();
        }
        trigger.onclick = () => open(trigger);
        mobile.onclick = () => open(mobile);
        search.oninput = render;
        window.addEventListener('resize', () => { if (dialog.open) position(); });
        window.addEventListener('platform-spaces-changed', refresh);
        window.openSpacePicker = open;

        // Show where imports and edits will be saved.
        function destination() {
            const bar = el('div', '', 'mb-entry-destination');
            const change = el('button', '切换空间', 'mb-entry-switch');
            change.type = 'button';
            change.onclick = () => open(change);
            bar.append(icon('fa-location-dot'), el('span', '保存到'), el('strong', space.name),
                el('span', `${space.stage}${space.subject}`, 'mb-badge'), el('small', space.textbook), change);
            return bar;
        }
        document.querySelector('#importWorkspaceSection .import-workspace-shell')?.prepend(destination());
        document.querySelector('#editorSection .question-editor-modal-header')?.after(destination());
        refresh();
    }

    function hasImportContent() {
        if (typeof parsedQuestionsData !== 'undefined' && parsedQuestionsData.length) return parsedQuestionsData.some(q => !q.saved);
        return ['importPaperTitle', 'importLatexContent'].some(id => $(id)?.value.trim()) ||
            Boolean(window.currentPdfFile || window.currentDocxFile) ||
            Boolean(document.querySelector('#importWorkspaceSection input[type=file]')?.files?.length);
    }
    function confirmLeave(leave) {
        const modal = el('dialog', '', 'mb-dialog');
        modal.setAttribute('aria-labelledby', 'switchConfirmTitle');
        modal.addEventListener('keydown', e => { if (['Tab', 'Escape', 'Esc'].includes(e.key)) e.stopPropagation(); });
        const heading = el('div', '', 'mb-dialog-heading');
        const title = el('h2', '离开当前录入内容？');
        title.id = 'switchConfirmTitle';
        title.tabIndex = -1;
        heading.append(title);
        const body = el('div', '', 'mb-dialog-body');
        const actions = el('div', '', 'mb-actions');
        const stay = el('button', '返回保存', 'mb-button mb-primary');
        const proceed = el('button', '仍然切换', 'mb-button');
        stay.type = proceed.type = 'button';
        stay.onclick = () => modal.close();
        proceed.onclick = () => { modal.close(); leave(); };
        actions.append(proceed, stay);
        body.append(el('p', '导入中心还有未入库的内容。请先保存草稿或完成入库；切换后本页未保存的内容可能丢失，已保存的草稿留在原空间。'), actions);
        modal.append(heading, body);
        document.body.append(modal);
        modal.addEventListener('close', () => modal.remove(), {once: true});
        modal.showModal();
        title.focus();
    }
    window.navigatePlatformSpace = spaceId => {
        spaceId = Number(spaceId);
        if (!Number.isInteger(spaceId) || spaceId < 1 || spaceId === ctx.space.id) return;
        const busy = window.isQuestionSaveInFlight?.() || ($('importLoadingState') && !$('importLoadingState').classList.contains('hidden'));
        if (busy) {
            window.showToast?.('正在保存或解析题目，请完成后再切换空间。', 'info');
            return;
        }
        const leave = () => {
            const go = () => location.assign('/workbench/' + spaceId);
            if (typeof window.checkAndSwitch === 'function') window.checkAndSwitch(go);
            else if (!window.isEditorModified?.()) go();
        };
        if (hasImportContent()) confirmLeave(leave); else leave();
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true}); else start();
})();
