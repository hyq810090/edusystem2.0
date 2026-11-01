(() => {
    const context = window.MathBankPlatform;
    if (!context?.space) return;
    const seniorMath = context.space.subject === '数学' && context.space.stage === '高中';
    const state = {count: 5, reference: seniorMath ? 'auto' : 'none', counts: {}, references: null, loading: false};
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, init = {}) => {
        const url = new URL(input instanceof Request ? input.url : String(input), location.href);
        let selectedCount = null;
        if (url.origin === location.origin && url.pathname.endsWith('/api/paper/ai-select') && typeof init.body === 'string') {
            try {
                const body = JSON.parse(init.body);
                selectedCount = state.count;
                init = {...init, body: JSON.stringify({...body, limit: state.count,
                    type_counts: state.counts, reference_mode: state.reference === 'none' ? 'none' : 'recent_gaokao',
                    reference_key: state.reference,
                    exclude_ids: (window.PaperStore?.cart || []).map(item => Number(item.id)).filter(id => Number.isSafeInteger(id) && id > 0)})};
            } catch (_) { /* Backend reports malformed requests. */ }
        }
        const pending = originalFetch(input, init);
        if (selectedCount !== null) pending.then(response => response.clone().json()).then(body => {
            const requested = body.selection_summary?.requested;
            if (state.count === selectedCount && Number.isSafeInteger(requested) && requested >= 1 && requested <= 30) {
                state.count = requested;
                const count = document.getElementById('platformSelectionCount');
                if (count) count.value = String(requested);
            }
        }).catch(() => {});
        return pending;
    };

    function label(text, control) {
        const element = document.createElement('label');
        element.className = 'mb-selection-field';
        const caption = document.createElement('span');
        caption.textContent = text;
        element.append(caption, control);
        return element;
    }
    function option(select, value, text) {
        const node = document.createElement('option');
        node.value = value;
        node.textContent = text;
        select.append(node);
    }
    function mount() {
        const entry = document.querySelector('.paper-ai-entry');
        if (!entry || document.getElementById('platformPaperSelection')) return;
        if (!Array.isArray(window.systemMetadata?.question_types)) return;
        const tools = document.createElement('div');
        tools.id = 'platformPaperSelection';
        tools.className = 'mb-selection-tools';
        const count = document.createElement('input');
        count.id = 'platformSelectionCount';
        count.type = 'number'; count.min = '1'; count.max = '30'; count.value = String(state.count);
        count.disabled = Object.values(state.counts).some(value => value > 0);
        count.className = 'glass-input';
        count.addEventListener('change', () => {
            state.count = Math.max(1, Math.min(30, Math.trunc(Number(count.value)) || 5));
            count.value = String(state.count);
        });
        tools.append(label('总题数（1–30）', count));
        if (seniorMath) {
            const reference = document.createElement('select');
            reference.id = 'platformSelectionReference'; reference.className = 'glass-select';
            option(reference, 'auto', '自动参考库内近年高考卷');
            option(reference, 'none', '不使用参考卷');
            for (const paper of state.references || []) {
                option(reference, paper.key, `${paper.title} · ${paper.question_count}题${paper.complete ? '' : '（部分收录）'}`);
            }
            reference.value = state.reference;
            reference.addEventListener('change', () => { state.reference = reference.value; });
            tools.append(label('参考来源（库内标注，需核对）', reference));
        }
        const quotas = document.createElement('details');
        const title = document.createElement('summary'); title.textContent = '指定题型数量（可选）'; quotas.append(title);
        const row = document.createElement('div'); row.className = 'mb-selection-quotas';
        for (const kind of window.systemMetadata?.question_types || []) {
            const input = document.createElement('input');
            input.type = 'number'; input.min = '0'; input.max = '30';
            input.value = String(state.counts[kind.value] || 0); input.className = 'glass-input';
            input.dataset.type = kind.value;
            input.addEventListener('change', () => {
                const before = {...state.counts};
                const value = Math.max(0, Math.min(30, Math.trunc(Number(input.value)) || 0));
                input.value = String(value);
                if (value) state.counts[kind.value] = value; else delete state.counts[kind.value];
                const total = Object.values(state.counts).reduce((a, b) => a + b, 0);
                if (total > 30) {
                    state.counts = before;
                    input.value = String(before[kind.value] || 0);
                    window.showToast?.('题型数量合计最多 30 题。', 'warning');
                    return;
                }
                count.disabled = total > 0;
                if (total) { state.count = total; count.value = String(total); }
            });
            row.append(label(kind.label || kind.value, input));
        }
        quotas.append(row); tools.append(quotas);
        entry.after(tools);
        if (seniorMath && state.references === null && !state.loading) {
            state.loading = true;
            originalFetch('/api/paper/reference-exams').then(r => r.ok ? r.json() : null).then(body => {
                state.references = body?.data || [];
                document.getElementById('platformPaperSelection')?.remove();
                mount();
            }).catch(() => {}).finally(() => { state.loading = false; });
        }
    }
    window.addEventListener('DOMContentLoaded', () => {
        new MutationObserver(mount).observe(document.body, {childList: true, subtree: true});
        mount();
    });
})();
