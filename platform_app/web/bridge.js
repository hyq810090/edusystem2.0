/* Connects the unchanged MathBank page to its space: routes /api/ calls to
   /bank/<space>/api/, scopes browser storage per account and space, and adds
   the session token. The five native product scripts stay unchanged. */
(() => {
    'use strict';
    const ctx = window.MathBankPlatform;
    const space = ctx.space;
    // Drafts and baskets live in browser storage, one namespace per account and space.
    const prefix = `mb-platform:${ctx.id}:${space.id}:`;
    const SHARED = ['theme-color', 'dark-mode'];

    function scopedStorage(storage) {
        const keys = () => Array.from({length: storage.length}, (_, i) => storage.key(i)).filter(k => k.startsWith(prefix));
        return {
            get length() { return keys().length; },
            key(i) { return keys()[i]?.slice(prefix.length) ?? null; },
            getItem(k) {
                if (!SHARED.includes(k)) return storage.getItem(prefix + k);
                const shared = storage.getItem('mb-platform:' + k);
                if (shared !== null) return shared;
                const previous = storage.getItem(prefix + k);
                if (previous !== null) storage.setItem('mb-platform:' + k, previous);
                return previous;
            },
            setItem(k, v) { storage.setItem(SHARED.includes(k) ? 'mb-platform:' + k : prefix + k, v); },
            removeItem(k) { storage.removeItem(prefix + k); },
            clear() { keys().forEach(k => storage.removeItem(k)); }
        };
    }
    for (const name of ['localStorage', 'sessionStorage']) {
        Object.defineProperty(window, name, {value: scopedStorage(window[name])});
    }

    // Paper folder choices made by paper-tools.js.
    ctx.folders = {filter: '', save: null};
    const nativeFetch = window.fetch.bind(window);
    async function send(input, init, url) {
        const isRequest = input instanceof Request;
        const headers = new Headers(init.headers || (isRequest ? input.headers : undefined));
        headers.set('X-Local-Token', ctx.csrf);
        const response = await nativeFetch(url ? (isRequest ? new Request(url, input) : url) : input, {...init, headers});
        if (response.status === 401 || response.headers.get('X-MathBank-Session-Changed') === '1') location.assign('/');
        return response;
    }
    window.fetch = (input, init = {}) => {
        const url = new URL(input instanceof Request ? input.url : String(input), location.href);
        if (url.origin !== location.origin) return nativeFetch(input, init);
        if (url.pathname.startsWith('/api/')) {
            if (url.pathname === '/api/papers' && ctx.folders.filter) url.searchParams.set('folder_id', ctx.folders.filter);
            if (url.pathname === '/api/paper/save' && typeof init.body === 'string') {
                init = {...init, body: JSON.stringify({...JSON.parse(init.body), platform_folder_id: ctx.folders.save})};
            }
            // Figures of a MinerU import are uploaded already (mineru.js).
            if (url.pathname === '/api/ai/parse-paper' && ctx.mineru && init.body instanceof FormData) {
                const own = JSON.parse(init.body.get('image_mapping_json') || '{}');
                init.body.set('image_mapping_json', JSON.stringify({...ctx.mineru.mapping, ...own}));
            }
            if (/\/settings(\/save)?$/.test(url.pathname) && document.getElementById('platformDefaultModel')?.checked) {
                url.searchParams.set('platform_default', '1');
            }
            url.pathname = `/bank/${space.id}${url.pathname}`;
            return send(input, init, url);
        }
        if (url.pathname.startsWith('/platform/api/') || url.pathname.startsWith('/bank/')) return send(input, init);
        return nativeFetch(input, init);
    };

    window.addEventListener('DOMContentLoaded', () => {
        const note = document.createElement('div');
        note.className = 'mb-settings-note';
        if (['super_admin', 'team_admin'].includes(ctx.role)) {
            note.textContent = '教材与题型由超级管理员维护；难度全平台统一为 1–5 级，易错、强基、竞赛为独立标签。模型按团队配置，空项使用平台默认。';
            if (ctx.role === 'super_admin') {
                const label = document.createElement('label');
                const check = document.createElement('input');
                check.type = 'checkbox';
                check.id = 'platformDefaultModel';
                check.onchange = () => window.openSettingsModal();
                label.append(check, '配置平台默认模型（所有未单独配置的团队使用）');
                note.append(label);
            }
        } else {
            note.textContent = '模型由团队管理员配置；未配置时使用平台默认模型。';
            for (const field of document.querySelectorAll('#settings-tab-api input, #settings-tab-api select, #btnSettingsSave')) field.disabled = true;
        }
        document.getElementById('settings-tab-api')?.prepend(note);
        // Upstream labels textbook volumes “学段”; the real stage is the space's.
        document.getElementById('filterCompulsory')?.setAttribute('aria-label', '教材册别');
        if (space.subject !== '数学' || space.stage !== '高中') {
            document.querySelectorAll('[onclick^="resetMetadataToDefault"]').forEach(b => { b.hidden = true; });
        }
    });
})();
