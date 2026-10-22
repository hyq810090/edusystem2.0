/* 考查知识点 (platform_app/knowledge_points.py) in the native page: a picker in the question
   editor, a filter for the bank list and the paper question stream, and chips on bank cards.
   Only 高中数学 spaces have the catalogue. A saved question's points are stored as soon as they
   change; a new question's points go with its first save. Engine scripts stay unchanged. */
(() => {
    'use strict';
    const ctx = window.MathBankPlatform;
    if (ctx.space.subject !== '数学' || ctx.space.stage !== '高中') return;
    const canEdit = ctx.permission !== 'viewer';
    const API = '/api/platform/knowledge-points';
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
    const squeeze = value => String(value || '').replace(/\s+/g, '').toLowerCase();
    const ROLE = {main: '主', auxiliary: '辅'};
    let catalogue = null;
    const ready = fetch(API).then(r => (r.ok ? r.json() : null)).then(data => {
        if (data?.enabled) catalogue = {points: data.points, byCode: new Map(data.points.map(p => [p.code, p]))};
        return catalogue;
    }).catch(() => null);
    const nameOf = code => catalogue?.byCode.get(code)?.name || code;

    async function request(path, options = {}) {
        const response = await fetch(path, options);
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`);
        return body;
    }
    const putPoints = (id, points) => request(`${API}/questions/${id}`, {
        method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({points})});
    const pointsOf = ids => request(`${API}/questions?ids=${ids.join(',')}`).then(body => body.questions || {});

    // ---------- Catalogue lookups ----------
    // 2: taught in this section, 1: in this chapter (chapter-level path), 0: elsewhere.
    function placement(point, [book, chapter, section]) {
        let best = 0;
        for (const [b, c, s] of point.paths) {
            if (b !== book || c !== chapter) continue;
            if (section && s === section) return 2;
            if (!s || !section) best = 1;
        }
        return best;
    }
    function search(text, near) {
        const needle = squeeze(text).replace(/^#/, '');
        if (!needle || !catalogue) return [];
        const hits = [];
        for (const point of catalogue.points) {
            const names = [point.name, ...point.aliases].map(squeeze);
            const rank = point.code === needle || names.includes(needle) ? 0
                : names.some(n => n.startsWith(needle)) ? 1 : names.some(n => n.includes(needle)) ? 2 : -1;
            if (rank >= 0) hits.push({point, score: rank * 3 - (near ? placement(point, near) : 0)});
        }
        return hits.sort((a, b) => a.score - b.score || a.point.name.length - b.point.name.length).slice(0, 12).map(h => h.point);
    }
    function nearby(near) {
        if (!catalogue || !near[1]) return [];
        return catalogue.points.map(point => ({point, rank: placement(point, near)}))
            .filter(h => h.rank > 0).sort((a, b) => b.rank - a.rank).map(h => h.point);
    }

    // A search box with a result list; Enter or a click picks a point.
    function attachSearch(input, list, pick, near = () => null) {
        let shown = [], active = -1;
        const close = () => { list.hidden = true; input.setAttribute('aria-expanded', 'false'); active = -1; };
        const paint = () => {
            list.innerHTML = shown.length ? shown.map((p, i) => `<button type="button" role="option" data-code="${esc(p.code)}"
                aria-selected="${i === active}" class="${i === active ? 'is-active' : ''}">${esc(p.name)}${p.aliases.length
                ? `<small>${esc(p.aliases.slice(0, 2).join('、'))}</small>` : ''}</button>`).join('')
                : '<p class="mb-kp-empty">目录中没有匹配的知识点</p>';
            list.hidden = false;
            input.setAttribute('aria-expanded', 'true');
        };
        input.addEventListener('input', () => { shown = search(input.value, near()); active = shown.length ? 0 : -1; input.value.trim() ? paint() : close(); });
        input.addEventListener('keydown', event => {
            if (list.hidden) return;
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                active = (active + (event.key === 'ArrowDown' ? 1 : -1) + shown.length) % Math.max(shown.length, 1);
                paint();
            } else if (event.key === 'Enter') {
                event.preventDefault();
                if (shown[active]) { pick(shown[active]); input.value = ''; close(); }
            } else if (event.key === 'Escape') close();
        });
        list.addEventListener('mousedown', event => event.preventDefault());
        list.addEventListener('click', event => {
            const code = event.target.closest('[data-code]')?.dataset.code;
            if (code) { pick(catalogue.byCode.get(code)); input.value = ''; close(); input.focus(); }
        });
        input.addEventListener('blur', () => setTimeout(close, 120));
    }

    // ---------- Question editor ----------
    const editor = {questionId: undefined, points: [], queue: Promise.resolve(), carry: null, root: null};
    const field = id => document.getElementById(id)?.value || '';
    const editorNear = () => [field('editCompulsory'), field('editChapter'), field('editKnowledge')];
    const stored = p => ({code: p.code, role: p.role, origin: p.origin || 'manual', evidence_source: p.evidence_source || null,
                          evidence: p.evidence || null, reason: p.reason || null});

    function status(text, problem = false) {
        const node = editor.root?.querySelector('.mb-kp-status');
        if (node) { node.textContent = text; node.classList.toggle('is-problem', problem); }
    }
    function chip(point, removable) {
        const evidence = point.evidence ? `\n依据：${point.evidence}` : '';
        const title = `${nameOf(point.code)}（${point.role === 'main' ? '主要考点' : '辅助方法'}${point.origin === 'ai' ? '，AI 建议' : ''}）${evidence}${point.reason ? `\n理由：${point.reason}` : ''}`;
        return `<span class="mb-kp-chip" data-role="${point.role}" data-code="${esc(point.code)}" title="${esc(title)}">
            ${removable ? `<button type="button" class="mb-kp-role" data-kp-role aria-label="${esc(nameOf(point.code))}：切换主要/辅助">${ROLE[point.role]}</button>`
                        : `<span class="mb-kp-role">${ROLE[point.role]}</span>`}
            <span class="mb-kp-name">${esc(nameOf(point.code))}</span>${point.origin === 'ai' ? '<span class="mb-kp-ai-mark">AI</span>' : ''}
            ${removable ? `<button type="button" class="mb-kp-remove" data-kp-remove aria-label="移除 ${esc(nameOf(point.code))}">×</button>` : ''}</span>`;
    }
    // Read-only chips in the bank's 题目详情 preview, under the engine's badges.
    function renderDetail() {
        const badges = document.getElementById('paperBadges');
        if (!badges) return;
        let detail = badges.parentElement.querySelector('.mb-kp-detail');
        if (!detail) {
            detail = document.createElement('div');
            detail.className = 'mb-kp-detail mb-kp-chips';
            detail.setAttribute('aria-label', '考查知识点');
            badges.after(detail);
        }
        detail.innerHTML = editor.points.map(p => chip(p, false)).join('');
        detail.hidden = !editor.points.length;
    }
    function renderEditor() {
        renderDetail();
        const root = editor.root;
        if (!root) return;
        const chips = root.querySelector('[data-kp-chips]');
        chips.innerHTML = editor.points.length ? editor.points.map(p => chip(p, canEdit)).join('')
            : `<span class="mb-kp-empty">${canEdit ? '未标注。可搜索添加，或点“AI 建议”。' : '未标注考查知识点。'}</span>`;
        const near = root.querySelector('[data-kp-near]');
        const chosen = new Set(editor.points.map(p => p.code));
        const options = canEdit ? nearby(editorNear()).filter(p => !chosen.has(p.code)).slice(0, 10) : [];
        near.hidden = !options.length;
        near.innerHTML = options.length ? `<span>本节常见</span>${options.map(p =>
            `<button type="button" data-kp-add="${esc(p.code)}">＋${esc(p.name)}</button>`).join('')}` : '';
    }
    function changed() {
        renderEditor();
        if (!canEdit) return;
        const id = editor.questionId;
        if (id == null) { status(editor.points.length ? '保存题目时一并保存' : ''); return; }
        const points = editor.points.map(stored);
        status('正在保存…');
        editor.queue = editor.queue.then(() => putPoints(id, points)).then(() => {
            if (editor.questionId === id) status('已保存');
            refreshCards([id]);
        }).catch(error => { if (editor.questionId === id) status(`知识点未保存：${error.message}`, true); });
    }
    function add(point, role, extra = {}) {
        if (!point || editor.points.some(p => p.code === point.code)) return;
        const main = editor.points.some(p => p.role === 'main');
        editor.points.push({code: point.code, role: role || (main ? 'auxiliary' : 'main'), origin: 'manual', ...extra});
        changed();
    }
    function sync() {
        const id = window.EditorState?.questionId ?? null;
        if (id === editor.questionId) return;
        editor.questionId = id;
        hideSuggestions();
        editor.points = [];
        renderEditor();
        status('');
        if (id == null || !editor.root) return;
        editor.queue.then(() => pointsOf([id])).then(found => {
            if (editor.questionId !== id) return;
            editor.points = (found[String(id)] || []).map(stored);
            renderEditor();
        }).catch(() => { if (editor.questionId === id) status('知识点读取失败', true); });
    }

    function hideSuggestions() {
        const box = editor.root?.querySelector('[data-kp-suggestions]');
        if (box) { box.hidden = true; box.innerHTML = ''; }
    }
    async function suggest(button) {
        const generation = window.EditorState?.generation;
        const question = {content: field('editContent'), answer_markdown: field('editAnswerMarkdown'), review: field('editReview')};
        if (!question.content.trim()) { status('请先录入题干，再请 AI 建议。', true); return; }
        button.disabled = true;
        status('AI 正在分析题目…');
        try {
            const body = await request(`${API}/suggest`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(question)});
            if (window.EditorState?.generation !== generation) return;
            status('');
            showSuggestions(body.points || [], body.warnings || []);
        } catch (error) {
            status(`AI 建议失败：${error.message}`, true);
        } finally {
            button.disabled = false;
        }
    }
    function showSuggestions(points, warnings) {
        const box = editor.root.querySelector('[data-kp-suggestions]');
        box.suggestions = points;
        const rows = points.map((p, i) => `<li><button type="button" class="glass-btn" data-kp-adopt="${i}"
                ${editor.points.some(q => q.code === p.code) ? 'disabled' : ''}>采用</button>
            <div><strong>${esc(p.name)}</strong><span class="mb-kp-role-label">${p.role === 'main' ? '主要考点' : '辅助方法'}</span>
            ${p.evidence ? `<q>${esc(p.evidence)}</q>` : ''}${p.reason ? `<small>${esc(p.reason)}</small>` : ''}</div></li>`).join('');
        box.innerHTML = `<div class="mb-kp-suggest-head"><strong>AI 建议</strong>
                ${points.length ? '<button type="button" class="glass-btn" data-kp-adopt-all>全部采用</button>' : ''}
                <button type="button" class="glass-btn" data-kp-close aria-label="关闭 AI 建议">关闭</button></div>
            ${points.length ? `<ul>${rows}</ul>` : '<p class="mb-kp-empty">AI 没有给出可核对的知识点，请手动选择。</p>'}
            ${warnings.length ? `<details><summary>${warnings.length} 条建议未采用</summary><ul class="mb-kp-warnings">${warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul></details>` : ''}
            <p class="mb-kp-note">每条建议都引用了题干、答案或评析中的原文；采用前请核对。</p>`;
        box.hidden = false;
    }
    function adopt(point) {
        add(catalogue.byCode.get(point.code), point.role,
            {origin: 'ai', evidence_source: point.evidence_source, evidence: point.evidence, reason: point.reason});
    }

    function mountEditor() {
        const grid = document.getElementById('editCompulsory')?.closest('.grid');
        if (!grid || editor.root) return;
        const root = document.createElement('section');
        root.className = 'mb-kp';
        root.setAttribute('aria-labelledby', 'mbKpTitle');
        root.innerHTML = `<div class="mb-kp-head"><label id="mbKpTitle" ${canEdit ? 'for="mbKpEditorSearch"' : ''} class="block text-[11px] font-medium text-slate-500">考查知识点</label><span class="mb-kp-status" aria-live="polite"></span>
                ${canEdit ? '<button type="button" class="glass-btn mb-kp-ai" data-kp-ai><i class="fa-solid fa-wand-magic-sparkles" aria-hidden="true"></i> AI 建议</button>' : ''}</div>
            <div class="mb-kp-chips" data-kp-chips></div>
            ${canEdit ? `<div class="mb-kp-search"><input type="search" class="glass-input" id="mbKpEditorSearch" autocomplete="off"
                role="combobox" aria-expanded="false" aria-controls="mbKpEditorResults"
                placeholder="搜索知识点（名称或别名），回车添加"><div class="mb-kp-results" id="mbKpEditorResults" role="listbox" hidden></div></div>` : ''}
            <div class="mb-kp-near" data-kp-near hidden></div>
            <div class="mb-kp-suggestions" data-kp-suggestions hidden></div>`;
        grid.after(root);
        editor.root = root;
        if (canEdit) attachSearch(root.querySelector('input'), root.querySelector('.mb-kp-results'), point => add(point), editorNear);
        root.addEventListener('click', event => {
            const target = event.target.closest('button');
            if (!target || !canEdit) return;
            const code = target.closest('[data-code]')?.dataset.code;
            if (target.hasAttribute('data-kp-role')) {
                const point = editor.points.find(p => p.code === code);
                point.role = point.role === 'main' ? 'auxiliary' : 'main';
                changed();
            } else if (target.hasAttribute('data-kp-remove')) {
                editor.points = editor.points.filter(p => p.code !== code);
                changed();
            } else if (target.dataset.kpAdd) {
                add(catalogue.byCode.get(target.dataset.kpAdd));
            } else if (target.hasAttribute('data-kp-ai')) {
                suggest(target);
            } else if (target.dataset.kpAdopt !== undefined) {
                adopt(root.querySelector('[data-kp-suggestions]').suggestions[Number(target.dataset.kpAdopt)]);
                target.disabled = true;
            } else if (target.hasAttribute('data-kp-adopt-all')) {
                root.querySelector('[data-kp-suggestions]').suggestions.forEach(adopt);
                hideSuggestions();
            } else if (target.hasAttribute('data-kp-close')) {
                hideSuggestions();
            }
        });
        for (const id of ['editCompulsory', 'editChapter', 'editKnowledge']) {
            document.getElementById(id)?.addEventListener('change', renderEditor);
        }
        editor.questionId = undefined;
        sync();
    }

    // Every editor session change (open, new, draft, after save) passes this engine hook.
    const sessionHook = window.invalidateEditorSessionAsyncWork;
    window.invalidateEditorSessionAsyncWork = function (...args) {
        const result = typeof sessionHook === 'function' ? sessionHook.apply(this, args) : undefined;
        setTimeout(sync, 0);
        return result;
    };
    // A new question's points are written right after its first save succeeds, before the
    // editor reopens it as a saved question (see the fetch wrapper below).
    const nativeSave = window.saveQuestion;
    if (typeof nativeSave === 'function') {
        window.saveQuestion = function (...args) {
            const carry = canEdit && editor.questionId == null && editor.points.length ? editor.points.map(stored) : null;
            editor.carry = carry;
            const result = nativeSave.apply(this, args);
            Promise.resolve(result).finally(() => { if (editor.carry === carry) editor.carry = null; });
            return result;
        };
    }

    // ---------- Filters (bank list and paper question stream) ----------
    const filters = {bank: blankFilter(), paper: blankFilter()};
    function blankFilter() { return {codes: [], mode: 'any', auxiliary: false, unlabeled: false}; }
    const active = filter => filter.codes.length || filter.unlabeled;
    function applyFilter(url, filter) {
        if (filter.unlabeled) {
            url.searchParams.set('knowledge_point_unlabeled', 'true');
            return;
        }
        filter.codes.forEach(code => url.searchParams.append('knowledge_point_codes', code));
        url.searchParams.set('knowledge_point_mode', filter.mode);
        url.searchParams.set('knowledge_point_role', filter.auxiliary ? 'all' : 'main');
    }
    function reload(scope) {
        if (scope === 'bank') document.getElementById('filterType')?.dispatchEvent(new Event('change'));
        else window.onPaperFilterChange?.('keyword', window.PaperStore?.filters?.keyword || '');
    }
    function filterControl(scope) {
        const filter = filters[scope];
        const node = document.createElement('div');
        node.className = 'mb-kp-filter';
        node.dataset.kpScope = scope;
        const id = `mbKpFilter-${scope}`;
        node.innerHTML = `<span class="bank-filter-label mb-kp-filter-label">知识点</span>
            <div class="mb-kp-filter-body">
              <div class="mb-kp-chips" data-kp-chips></div>
              <div class="mb-kp-search"><input type="search" class="glass-input" id="${id}" autocomplete="off" role="combobox"
                aria-expanded="false" aria-controls="${id}-results" aria-label="按考查知识点筛选" placeholder="搜索知识点后回车加入筛选">
                <div class="mb-kp-results" id="${id}-results" role="listbox" hidden></div></div>
              <div class="mb-kp-filter-options">
                <select class="glass-select" data-kp-mode aria-label="多个知识点的匹配方式">
                  <option value="any">含任一知识点</option><option value="all">同时含全部知识点</option></select>
                <label><input type="checkbox" data-kp-auxiliary> 含辅助方法</label>
                <label><input type="checkbox" data-kp-unlabeled> 只看未标注</label>
              </div>
            </div>`;
        const paint = () => {
            node.querySelector('[data-kp-chips]').innerHTML = filter.codes.map(code =>
                `<span class="mb-kp-chip" data-code="${esc(code)}"><span class="mb-kp-name">${esc(nameOf(code))}</span>
                 <button type="button" class="mb-kp-remove" data-kp-remove aria-label="取消筛选 ${esc(nameOf(code))}">×</button></span>`).join('');
            node.querySelector('[data-kp-mode]').value = filter.mode;
            node.querySelector('[data-kp-mode]').disabled = filter.codes.length < 2;
            node.querySelector('[data-kp-auxiliary]').checked = filter.auxiliary;
            node.querySelector('[data-kp-auxiliary]').disabled = filter.unlabeled;
            node.querySelector('[data-kp-unlabeled]').checked = filter.unlabeled;
            node.querySelector('input[type=search]').disabled = filter.unlabeled;
            node.classList.toggle('is-active', Boolean(active(filter)));
        };
        const update = change => { change(); paint(); reload(scope); };
        attachSearch(node.querySelector('input[type=search]'), node.querySelector('.mb-kp-results'), point => {
            if (!filter.codes.includes(point.code)) update(() => filter.codes.push(point.code));
        });
        node.addEventListener('click', event => {
            const code = event.target.closest('[data-kp-remove]') && event.target.closest('[data-code]')?.dataset.code;
            if (code) update(() => { filter.codes = filter.codes.filter(c => c !== code); });
        });
        node.querySelector('[data-kp-mode]').addEventListener('change', event => update(() => { filter.mode = event.target.value; }));
        node.querySelector('[data-kp-auxiliary]').addEventListener('change', event => update(() => { filter.auxiliary = event.target.checked; }));
        node.querySelector('[data-kp-unlabeled]').addEventListener('change', event => update(() => {
            filter.unlabeled = event.target.checked;
            if (filter.unlabeled) filter.codes = [];
        }));
        node.paint = paint;
        paint();
        return node;
    }
    function mountFilters() {
        const bank = document.getElementById('bankFilterFields');
        if (bank && !bank.querySelector('.mb-kp-filter')) bank.append(filterControl('bank'));
        const paper = document.getElementById('paperFilterSection');
        if (!paper) return;
        // paper.js re-renders its filter section; put the control back each time, beside the
        // filter grid (whose own input and button styles do not suit it) and shown with it.
        const remount = () => {
            const grid = paper.querySelector('#paperAdvancedFilters');
            if (!grid) return;
            let node = paper.querySelector('.mb-kp-filter[data-kp-scope="paper"]');
            if (!node) {
                node = filterControl('paper');
                grid.after(node);
            }
            if (node.hidden !== grid.hidden) node.hidden = grid.hidden;
        };
        new MutationObserver(remount).observe(paper, {childList: true, subtree: true, attributes: true, attributeFilter: ['hidden']});
        remount();
    }
    for (const [name, scope] of [['clearBankFilters', 'bank'], ['clearPaperFilters', 'paper']]) {
        const original = window[name];
        if (typeof original !== 'function') continue;
        window[name] = function (...args) {
            filters[scope] = blankFilter();
            document.querySelectorAll(`.mb-kp-filter[data-kp-scope="${scope}"]`).forEach(node => node.remove());
            mountFilters();
            return original.apply(this, args);
        };
    }

    const innerFetch = window.fetch;
    window.fetch = async (input, init = {}) => {
        if (typeof input !== 'string' && !(input instanceof URL)) return innerFetch(input, init);
        const url = new URL(String(input), location.href);
        const method = String(init.method || 'GET').toUpperCase();
        if (url.origin !== location.origin || url.pathname !== '/api/questions') return innerFetch(input, init);
        const scope = document.body.dataset.workspace;
        if (method === 'GET' && url.searchParams.has('page') && filters[scope] && active(filters[scope])) {
            applyFilter(url, filters[scope]);
            return innerFetch(url.href, init);
        }
        if (method === 'POST' && editor.carry) {
            const carry = editor.carry;
            const response = await innerFetch(input, init);
            const body = response.ok ? await response.clone().json().catch(() => null) : null;
            const id = body?.status === 'success' ? body.question?.id : null;
            if (id != null) {
                editor.carry = null;
                await putPoints(id, carry).catch(error => window.showToast?.(`题目已保存，但知识点未保存：${error.message}`, 'warning'));
            }
            return response;
        }
        return innerFetch(input, init);
    };

    // ---------- Chips on bank cards ----------
    async function refreshCards(ids) {
        const cards = ids.map(id => document.querySelector(`#questionsList .bank-question-card[data-id="${id}"]`)).filter(Boolean);
        if (!cards.length || !catalogue) return;
        const found = await pointsOf(cards.map(card => card.dataset.id)).catch(() => null);
        if (!found) return;
        for (const card of cards) {
            card.querySelector('.mb-kp-card')?.remove();
            const points = [...(found[card.dataset.id] || [])].sort((a, b) => (a.role === 'main' ? 0 : 1) - (b.role === 'main' ? 0 : 1));
            if (!points.length) continue;
            const row = document.createElement('div');
            row.className = 'mb-kp-card';
            row.title = points.map(p => `${p.name}（${ROLE[p.role]}）`).join('；');
            row.innerHTML = points.slice(0, 2).map(p => `<span data-role="${p.role}">${esc(p.name)}</span>`).join('')
                + (points.length > 2 ? `<span>+${points.length - 2}</span>` : '');
            const meta = card.querySelector('.bank-question-meta');
            meta ? meta.before(row) : card.append(row);
        }
    }
    function watchCards() {
        const list = document.getElementById('questionsList');
        if (!list) return;
        let timer = null;
        new MutationObserver(() => {
            clearTimeout(timer);
            timer = setTimeout(() => {
                const ids = [...list.querySelectorAll('.bank-question-card[data-id]')]
                    .filter(card => !card.dataset.kpChecked).map(card => { card.dataset.kpChecked = '1'; return card.dataset.id; });
                if (ids.length) refreshCards(ids);
            }, 60);
        }).observe(list, {childList: true});
    }

    const start = () => ready.then(found => {
        if (!found) return;
        mountEditor();
        mountFilters();
        watchCards();
        refreshCards([...document.querySelectorAll('#questionsList .bank-question-card[data-id]')].map(card => card.dataset.id));
    });
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
