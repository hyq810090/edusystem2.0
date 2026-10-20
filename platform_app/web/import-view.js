/* 导入中心 layout. Before a paper is chosen the left pane offers one upload area
   (file, pasted text or MinerU) with the parse settings below it. Once a paper
   is chosen the left pane shows it in full — PDF and Word pages, TeX/Markdown
   source, or the paper behind a MinerU result — and the settings move to the
   still-empty right pane; after parsing, the right pane lists the questions to
   review against the paper. Native controls are moved, never copied, so the
   import code keeps reaching them by id. */
(() => {
    'use strict';
    const ctx = window.MathBankPlatform;
    if (!ctx) return;
    const $ = id => document.getElementById(id);
    // The import code writes this into #texFileName whenever no file is chosen.
    const NO_FILE = '点击或拖放 .tex / .md / .pdf / .docx 试卷文件';
    const KIND_LABELS = {pdf: 'PDF 原卷', docx: 'Word 原卷（按原文件排版生成）', tex: 'TeX 源码 · 可直接修改',
        markdown: 'Markdown 源码 · 可直接修改', paste: 'LaTeX 或 Markdown，可选择格式后直接粘贴'};
    // Pages fit the pane width; the thumbnail strip would take a third of it.
    const PDF_VIEW = '#navpanes=0&view=FitH';
    let ui = null, shownKey = '', shownName = '', objectUrl = '', pasting = false, previewTicket = 0, mineruTab = 'origin';

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
    function button(label, iconName, onclick, className = 'mb-source-action') {
        const node = el('button', '', className);
        node.type = 'button';
        if (iconName) node.append(icon(iconName));
        node.append(el('span', label));
        node.onclick = onclick;
        return node;
    }
    const visible = id => $(id) && !$(id).classList.contains('hidden');

    // ---------- The paper in the left pane ----------
    function currentSource() {
        if (window.currentPdfFile) return {kind: 'pdf', file: window.currentPdfFile, name: window.currentPdfFile.name};
        if (window.currentDocxFile) return {kind: 'docx', file: window.currentDocxFile, name: window.currentDocxFile.name};
        const name = $('texFileName')?.textContent.trim() || '';
        if (name && name !== NO_FILE) {
            const origin = ctx.mineru?.origin;
            return {kind: /\.md$/i.test(name) ? 'markdown' : 'tex', name, origin: origin?.textName === name ? origin : null};
        }
        return pasting ? {kind: 'paste', name: '粘贴的文本'} : {kind: 'none'};
    }

    function showFrame(url, type) {
        if (type === 'image') {
            const scroll = el('div', '', 'mb-source-scroll');
            const image = el('img');
            image.src = url;
            image.alt = '原卷图片';
            scroll.append(image);
            ui.doc.replaceChildren(scroll);
            return;
        }
        const frame = el('iframe', '', 'mb-source-media');
        frame.src = url + PDF_VIEW;
        frame.title = '原卷';
        ui.doc.replaceChildren(frame);
    }
    function showMessage(text, busy = false) {
        const note = el('div', '', 'mb-source-message');
        if (busy) note.append(icon('fa-circle-notch fa-spin'));
        note.append(el('span', text));
        ui.doc.replaceChildren(note);
    }
    async function showWord(file) {
        const ticket = ++previewTicket;
        showMessage('正在生成 Word 原卷预览…', true);
        try {
            const data = new FormData();
            data.append('file', file);
            const response = await fetch(`/platform/api/spaces/${ctx.space.id}/source-preview`, {method: 'POST', body: data});
            if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || '原卷预览生成失败');
            const blob = await response.blob();
            if (ticket !== previewTicket) return;
            objectUrl = URL.createObjectURL(blob);
            showFrame(objectUrl, 'pdf');
        } catch (error) {
            if (ticket === previewTicket) showMessage(`${error.message}。不影响解析。`);
        }
    }

    function render(source) {
        previewTicket++;
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        objectUrl = '';
        ui.pane.dataset.source = source.kind;
        if (source.kind === 'none') return;
        const text = ['tex', 'markdown', 'paste'].includes(source.kind) && !(source.origin && mineruTab === 'origin');
        ui.name.textContent = source.origin ? source.origin.name : source.name;
        ui.kind.textContent = source.origin ? `MinerU 识别 · ${source.origin.summary}` : KIND_LABELS[source.kind];
        ui.tabs.hidden = !source.origin;
        ui.tabs.querySelectorAll('button').forEach(tab => tab.setAttribute('aria-pressed', String(tab.dataset.tab === mineruTab)));
        ui.change.hidden = source.kind === 'paste';
        ui.back.hidden = source.kind !== 'paste';
        ui.doc.hidden = text;
        ui.text.hidden = !text;
        if (text) {
            if (source.kind === 'paste') $('importLatexContent').focus();
        } else if (source.origin) {
            showFrame(source.origin.url, source.origin.type);
        } else if (source.kind === 'pdf') {
            objectUrl = URL.createObjectURL(source.file);
            showFrame(objectUrl, 'pdf');
        } else if (source.kind === 'docx') {
            showWord(source.file);
        }
    }

    function refresh() {
        if (!ui) return;
        const source = currentSource();
        const key = [source.kind, source.name, source.file?.size, source.file?.lastModified, source.origin?.url, mineruTab].join('|');
        if (key !== shownKey) {
            shownKey = key;
            retitle(source);
            render(source);
        }
        step();
    }
    // The import code fills an empty title from the file name; a title that still
    // matches the previous file follows the new one instead of staying stale.
    function retitle(source) {
        const stem = name => name.replace(/\.[^/.]+$/, '');
        const title = $('importPaperTitle');
        if (title && source.file && shownName && title.value === stem(shownName)) title.value = stem(source.name);
        shownName = source.kind === 'none' ? '' : source.name;
    }

    // ---------- Progress: steps and where the settings sit ----------
    function step() {
        const chosen = ui.pane.dataset.source !== 'none';
        const current = visible('parsedQuestionsWrapper') ? 4 : visible('importLoadingState') ? 3 : chosen ? 2 : 1;
        document.querySelectorAll('.import-workspace-steps li').forEach((item, index) => {
            item.classList.toggle('is-current', index + 1 === current);
            item.classList.toggle('is-done', index + 1 < current);
        });
        // Settings stay under the upload area until a paper is chosen, then take the empty right pane.
        const home = chosen ? ui.review : ui.pane;
        if (ui.config.parentNode !== home) {
            if (chosen) home.prepend(ui.config); else home.append(ui.config);
        }
        ui.config.hidden = current > 2;
        ui.review.classList.toggle('mb-configuring', chosen && current === 2);
    }

    // ---------- Building the panes ----------
    function build() {
        const pane = $('importInputPane');
        const review = $('importReviewPane');
        const dropzone = $('texDropzone');
        const textarea = $('importLatexContent');
        if (!pane || !review || !dropzone || !textarea || pane.dataset.source) return null;
        const card = id => $(id)?.closest('.import-config-card') || $(id);
        const uploadCard = card('texDropzone');

        // Upload area: one drop target, then the other ways in.
        uploadCard.classList.add('mb-upload-card');
        uploadCard.querySelector(':scope > label')?.remove();
        dropzone.className = 'mb-dropzone';
        dropzone.setAttribute('role', 'button');
        dropzone.tabIndex = 0;
        dropzone.setAttribute('aria-label', '选择试卷文件');
        dropzone.addEventListener('keydown', e => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('texFileInput').click(); }
        });
        const prompt = el('div', '', 'mb-dropzone-copy');
        const badge = el('span', '', 'mb-dropzone-icon');
        badge.append(icon('fa-file-arrow-up'));
        const formats = el('div', '', 'mb-format-chips');
        for (const [name, hint] of [['PDF', '电子版或扫描件'], ['Word', '.docx'], ['TeX', '.tex 源码'], ['Markdown', '.md']]) {
            const chip = el('span', name);
            chip.title = hint;
            formats.append(chip);
        }
        prompt.append(badge, el('strong', '拖入试卷文件，或点击选择'),
            el('small', '原卷会显示在左侧，方便与右侧拆出的题目逐题对照'), formats);
        dropzone.append(prompt);
        const alternatives = el('div', '', 'mb-upload-alternatives');
        alternatives.id = 'mbImportAlternatives';
        alternatives.append(button('粘贴文本', 'fa-paste', () => { pasting = true; refresh(); }, 'mb-alt-action'));
        uploadCard.append(alternatives);

        // The paper: a header, the pages, and the editable text for TeX/Markdown.
        const source = el('section', '', 'mb-source');
        source.setAttribute('aria-label', '原卷');
        const head = el('header', '', 'mb-source-head');
        const titleBox = el('div', '', 'mb-source-title');
        const name = el('strong');
        const kind = el('small');
        titleBox.append(name, kind);
        const tabs = el('div', '', 'mb-source-tabs');
        tabs.setAttribute('role', 'group');
        tabs.setAttribute('aria-label', '显示内容');
        for (const [value, label] of [['origin', '原卷'], ['text', '识别文本']]) {
            const tab = button(label, '', () => { mineruTab = value; refresh(); }, 'mb-source-tab');
            tab.dataset.tab = value;
            tabs.append(tab);
        }
        const change = button('更换文件', 'fa-arrow-right-arrow-left', () => $('texFileInput').click());
        const back = button('改为上传文件', 'fa-arrow-left', () => { pasting = false; refresh(); });
        head.append(titleBox, tabs, change, back);
        const doc = el('div', '', 'mb-source-doc');
        const text = el('div', '', 'mb-source-text');
        const format = $('importSourceFormat')?.closest('div');
        if (format) text.append(format);
        if ($('importMarkdownNote')) text.append($('importMarkdownNote'));
        text.append(textarea);
        textarea.placeholder = '在此粘贴或编辑试卷的 LaTeX / Markdown 源文…';
        source.append(head, doc, text);

        // Parse settings with the start and reset buttons.
        const config = el('section', '', 'mb-import-config');
        config.setAttribute('aria-labelledby', 'mbImportConfigTitle');
        const configHead = el('header', '', 'mb-import-config-head');
        const configTitle = el('strong', '解析设置');
        configTitle.id = 'mbImportConfigTitle';
        configHead.append(icon('fa-sliders'), configTitle, el('small', '确认标题和识别方式后开始解析'));
        const body = el('div', '', 'mb-import-config-body');
        for (const node of [card('importPaperTitle'), $('pdfPageRangeContainer'), $('docxVerificationContainer'),
            $('texImagesSection'), card('importGenerateAnswers')]) {
            if (node) body.append(node);
        }
        config.append(configHead, body);
        const actions = pane.querySelector('.import-primary-actions');
        if (actions) config.append(actions);

        pane.classList.add('mb-import-pane');
        pane.prepend(uploadCard, source);
        return {pane, review, name, kind, tabs, change, back, doc, text, config};
    }

    // Drag the divider between the paper and the questions; the width is kept per browser.
    function splitter() {
        const content = $('importWorkspaceContent');
        if (!content || content.querySelector('.mb-import-splitter')) return;
        const handle = el('div', '', 'mb-import-splitter');
        handle.setAttribute('role', 'separator');
        handle.setAttribute('aria-orientation', 'vertical');
        handle.setAttribute('aria-label', '调整原卷与题目宽度');
        handle.title = '拖动调整宽度，双击恢复';
        handle.tabIndex = 0;
        const set = percent => {
            const value = Math.min(70, Math.max(28, Math.round(percent)));
            content.style.setProperty('--mb-source-width', value + '%');
            handle.setAttribute('aria-valuenow', String(value));
            return value;
        };
        let width = set(Number(localStorage.getItem('import-source-width')) || 46);
        const save = () => {
            localStorage.setItem('import-source-width', String(width));
            window.dispatchEvent(new Event('resize'));
        };
        handle.addEventListener('pointerdown', event => {
            event.preventDefault();
            handle.setPointerCapture(event.pointerId);
            content.classList.add('mb-resizing');
            const box = content.getBoundingClientRect();
            const move = e => { width = set((e.clientX - box.left) / box.width * 100); };
            const stop = () => {
                handle.removeEventListener('pointermove', move);
                content.classList.remove('mb-resizing');
                save();
            };
            handle.addEventListener('pointermove', move);
            handle.addEventListener('pointerup', stop, {once: true});
            handle.addEventListener('pointercancel', stop, {once: true});
        });
        handle.addEventListener('keydown', event => {
            if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
            event.preventDefault();
            width = set(width + (event.key === 'ArrowRight' ? 2 : -2));
            save();
        });
        handle.addEventListener('dblclick', () => { width = set(46); save(); });
        ui.review.before(handle);
    }

    function start() {
        ui = build();
        if (!ui) return;
        splitter();
        // Clearing the inputs also leaves pasting and forgets a MinerU result.
        const clear = window.clearAllImportInputs;
        if (typeof clear === 'function') {
            window.clearAllImportInputs = function (...args) {
                const result = clear.apply(this, args);
                pasting = false;
                mineruTab = 'origin';
                ctx.mineru = null;
                refresh();
                return result;
            };
        }
        new MutationObserver(refresh).observe($('texFileName'), {childList: true, characterData: true, subtree: true});
        for (const id of ['importLoadingState', 'parsedQuestionsWrapper']) {
            if ($(id)) new MutationObserver(step).observe($(id), {attributes: true, attributeFilter: ['class']});
        }
        $('texFileInput').addEventListener('change', () => setTimeout(refresh));
        $('texDropzone').addEventListener('drop', () => setTimeout(refresh));
        window.addEventListener('mb-import-source', refresh);
        refresh();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true}); else start();
})();
