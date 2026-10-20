/* MinerU in 导入中心: send a PDF/image to MinerU (or reuse a MinerU result zip),
   then load the rebuilt Markdown into the native import as an .md source while
   import-view.js keeps showing the original paper. The figures are already
   uploaded; bridge.js adds their mapping to the parse request, so review and
   saving stay in the native flow. Admins set the MinerU key in 系统设置 next
   to the model keys. */
(() => {
    'use strict';
    const ctx = window.MathBankPlatform;
    if (!ctx) return;
    const $ = id => document.getElementById(id);
    const base = `/platform/api/spaces/${ctx.space.id}/mineru`;
    const canEdit = ['owner', 'editor'].includes(ctx.permission);
    const isAdmin = ['super_admin', 'team_admin'].includes(ctx.role);
    let configured = false, dialog = null;

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
    async function json(response) {
        const value = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(value.detail || '请求失败，请重试');
        return value;
    }

    // ---------- Entry in the 导入中心 upload area (import-view.js) ----------
    function mount(anchor) {
        const open = el('button', '', 'mb-alt-action');
        open.type = 'button';
        open.id = 'mineruOpenBtn';
        open.append(icon('fa-wand-magic-sparkles'), el('span', 'MinerU 识别'), el('span', '扫描件、图片题', 'mb-badge'));
        open.onclick = openDialog;
        const note = el('p', '', 'mb-upload-note');
        note.id = 'mineruNote';
        anchor.append(open, note);
    }
    function describe() {
        $('mineruNote').textContent = configured
            ? 'MinerU 按版面整理题号、配图和图片选项，适合扫描件和拍照件。'
            : `MinerU 尚未配置 API Key${isAdmin ? '（系统设置 → 模型接口）' : '，请联系管理员'}，仍可上传 mineru.net 下载的结果包（.zip）。`;
    }

    // ---------- Parse dialog ----------
    function openDialog() {
        if (!dialog) dialog = buildDialog();
        dialog.form.reset();
        dialog.status.textContent = '';
        dialog.submit.disabled = false;
        dialog.node.showModal();
    }
    function buildDialog() {
        const node = el('dialog', '', 'mb-dialog');
        node.setAttribute('aria-labelledby', 'mineruDialogTitle');
        node.addEventListener('keydown', e => { if (['Tab', 'Escape', 'Esc'].includes(e.key)) e.stopPropagation(); });
        const heading = el('div', '', 'mb-dialog-heading');
        const text = el('div');
        const h2 = el('h2', 'MinerU 解析');
        h2.id = 'mineruDialogTitle';
        h2.prepend(icon('fa-wand-magic-sparkles'));
        text.append(h2, el('p', '文件会发送到 mineru.net 识别；结果包（.zip）直接在本地整理。'));
        const close = el('button', '', 'mb-icon-button');
        close.type = 'button';
        close.setAttribute('aria-label', '关闭');
        close.append(icon('fa-xmark'));
        close.onclick = () => node.close();
        heading.append(text, close);
        const form = el('form');
        const fileLabel = el('label', '试卷文件（PDF、图片，或 MinerU 结果包 .zip）');
        const file = el('input');
        file.type = 'file';
        file.required = true;
        file.accept = '.pdf,.png,.jpg,.jpeg,.webp,.bmp,.zip';
        fileLabel.append(file);
        const pair = el('div', '', 'mb-field-pair');
        const rangeLabel = el('label', '页码范围');
        const range = el('input');
        range.placeholder = '全部，或 1-6,8';
        rangeLabel.append(range);
        const modelLabel = el('label', '模型');
        const model = el('select');
        model.setAttribute('aria-label', '模型');
        for (const [value, label] of [['vlm', 'vlm（推荐）'], ['pipeline', 'pipeline']]) {
            const option = el('option', label);
            option.value = value;
            model.append(option);
        }
        modelLabel.append(model);
        pair.append(rangeLabel, modelLabel);
        const ocr = el('input');
        ocr.type = 'checkbox';
        ocr.checked = true;
        const ocrLabel = el('label', '', 'mb-check');
        ocrLabel.append(ocr, '强制 OCR（扫描件建议勾选，可避开 PDF 里错误的隐藏文字层）');
        // Fast parse (platform_app/mineru_fastpath.py): remembered per account and space.
        const fast = el('input');
        fast.type = 'checkbox';
        fast.id = 'mineruFastParse';
        const remembered = () => localStorage.getItem('mb-mineru-fast') !== '0';
        fast.checked = remembered();
        fast.onchange = () => localStorage.setItem('mb-mineru-fast', fast.checked ? '1' : '0');
        const fastLabel = el('label', '', 'mb-check');
        fastLabel.append(fast, '快速拆题：题文保留 MinerU 原文，AI 只判断哪些是题并分类（推荐，明显更快；切不准时自动改用整卷拆题）');
        const status = el('p', '', 'mb-status');
        status.setAttribute('role', 'status');
        const actions = el('div', '', 'mb-actions');
        const cancel = el('button', '取消', 'mb-button');
        cancel.type = 'button';
        cancel.onclick = () => node.close();
        const submit = el('button', '开始解析', 'mb-button mb-primary');
        actions.append(cancel, submit);
        form.append(fileLabel, pair, ocrLabel, fastLabel, status, actions);
        form.addEventListener('reset', () => { setTimeout(() => { ocr.checked = true; fast.checked = remembered(); }); });
        form.onsubmit = async event => {
            event.preventDefault();
            const chosen = file.files[0];
            if (!chosen.name.toLowerCase().endsWith('.zip') && !configured) {
                status.textContent = '尚未配置 MinerU API Key，只能上传 MinerU 结果包（.zip）。';
                return;
            }
            submit.disabled = true;
            const data = new FormData();
            data.append('file', chosen);
            data.append('page_ranges', range.value);
            data.append('model_version', model.value);
            data.append('is_ocr', String(ocr.checked));
            try {
                const job = await json(await fetch(base, {method: 'POST', body: data}));
                await follow(job.id, status);
                node.close();
            } catch (error) {
                status.textContent = error.message;
                submit.disabled = false;
            }
        };
        node.append(heading, form);
        document.body.append(node);
        return {node, form, status, submit};
    }
    async function follow(id, status) {
        for (;;) {
            const job = await json(await fetch(`${base}/${id}`));
            status.textContent = `${job.message}${job.elapsed ? `（${Math.round(job.elapsed)} 秒）` : ''}`;
            if (job.state === 'failed') throw new Error(job.message);
            if (job.state === 'done') return load(job);
            await new Promise(resolve => setTimeout(resolve, 2000));
        }
    }
    function load(job) {
        const {markdown, mapping, report, origin} = job.result;
        const textName = `${job.title || 'MinerU'}.md`;
        // The original paper stays beside the questions; 识别文本 shows the Markdown.
        ctx.mineru = {mapping, fast: localStorage.getItem('mb-mineru-fast') !== '0', origin: origin ? {
            name: job.file, textName, url: `${base}/${job.id}/origin`,
            type: /\.(png|jpe?g|webp|bmp)$/i.test(job.file) ? 'image' : 'pdf',
            summary: `${report.questions} 处题号 · ${report.figures} 张配图 · ${report.picture_option_questions} 道图片选项题`,
        } : null};
        const input = $('texFileInput');
        const transfer = new DataTransfer();
        transfer.items.add(new File([markdown], textName, {type: 'text/markdown'}));
        input.files = transfer.files;
        input.dispatchEvent(new Event('change', {bubbles: true}));
        window.dispatchEvent(new Event('mb-import-source'));
        const reused = job.result.cached ? '（复用此前的解析结果，未消耗 MinerU 额度）' : '';
        window.showToast?.(`MinerU 已识别 ${report.questions} 处题号、${report.figures} 张配图${reused}，核对后点击“开始解析”`, 'success');
    }

    // ---------- MinerU key in 系统设置 ----------
    function settingsBlock() {
        const tab = $('settings-tab-api');
        if (!tab || !isAdmin) return;
        const block = el('div', '', 'mb-settings-note');
        const title = el('strong', 'MinerU 文档解析');
        const state = el('span', '', 'mb-badge mb-muted');
        const row = el('div', '', 'mb-inline-form');
        const input = el('input', '', 'glass-input');
        input.type = 'password';
        input.autocomplete = 'off';
        input.placeholder = '在 mineru.net 的 API 管理页生成';
        input.setAttribute('aria-label', 'MinerU API Key');
        const save = el('button', '保存', 'glass-btn');
        save.type = 'button';
        const message = el('span', '', 'mb-status');
        row.append(input, save);
        block.append(title, ' ', state, el('p', '用于导入中心的“MinerU 解析”。团队配置优先，留空表示使用平台默认。'), row, message);
        tab.append(block);
        const scope = () => ($('platformDefaultModel')?.checked ? 'platform' : 'team');
        async function refresh() {
            try {
                const value = await json(await fetch(`/platform/api/ai-settings?scope=${scope()}`));
                state.textContent = value.mineru_key ? '已配置' : '未配置';
            } catch (_) { state.textContent = ''; }
        }
        save.onclick = async () => {
            save.disabled = true;
            try {
                await json(await fetch('/platform/api/ai-settings', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({scope: scope(), config: {mineru_key: input.value}})}));
                input.value = '';
                message.textContent = '已保存';
                await refresh();
                configured = (await json(await fetch(`${base}/status`))).configured;
                describe();
            } catch (error) {
                message.textContent = error.message;
            } finally {
                save.disabled = false;
            }
        };
        $('platformDefaultModel')?.addEventListener('change', refresh);
        refresh();
    }

    async function start() {
        settingsBlock();
        const anchor = $('mbImportAlternatives');
        if (!canEdit || !anchor) return;
        mount(anchor);
        try { configured = (await json(await fetch(`${base}/status`))).configured; } catch (_) { configured = false; }
        describe();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true}); else start();
})();
