/* One ordinal scale across spaces; teaching-purpose tags never affect it. */
(() => {
    const levels = ['1', '2', '3', '4', '5'];
    const names = ['基础', '简单', '中等', '较难', '困难'];
    const colors = ['#10b981', '#0ea5e9', '#f59e0b', '#f97316', '#f43f5e'];
    const legacy = {easy_error:'易错', qiangji:'强基', competition:'竞赛', contest:'竞赛', challenge:'挑战', normal:'原难度：常规题', easy:'原难度：简单', medium:'原难度：中等', hard:'原难度：困难'};
    function normalize(q) {
        if (!q || typeof q !== 'object') return q;
        const value = String(q.difficulty || '').trim();
        if (levels.includes(value) || value === 'unrated') { q.difficulty = value; return q; }
        const tags = String(q.tags || '').split(/[,，]/).map(t => t.trim()).filter(Boolean);
        const labels = typeof systemMetadata !== 'undefined' ? systemMetadata.legacy_difficulty_labels || {} : {};
        const previous = labels[value] || value;
        const tag = value ? legacy[value] || ['易错','强基','竞赛','挑战'].find(name=>[name,name+'题'].includes(previous)) || '原难度：' + previous : '';
        if (tag && !tags.includes(tag)) tags.push(tag);
        q.difficulty = 'unrated'; q.tags = tags.join(',');
        return q;
    }
    function summarize(questions) {
        const counts = levels.map(level => questions.filter(q => String(q?.difficulty) === level).length);
        const rated = counts.reduce((a,b) => a+b, 0);
        const exact = counts.map(n => rated ? n * 100 / rated : 0);
        const percentages = exact.map(Math.floor);
        const remainder = rated ? 100 - percentages.reduce((a,b) => a+b, 0) : 0;
        const order = levels.map((_,i) => i).sort((a,b) => (exact[b]-percentages[b])-(exact[a]-percentages[a]));
        for (let i=0; i<remainder; i++) percentages[order[i]]++;
        return {counts, percentages, rated, unrated:questions.length-rated};
    }
    function render(questions) {
        const s = summarize(questions);
        return `<div id="platformDifficultyRatio" class="text-xs text-slate-500 dark:text-slate-300" style="max-width:100%" title="按已定级题数计算；待确认题目不计入分母，标签不参与计算">
            <div>难度比（按题数） · 已定级 ${s.rated} 题 · 待确认 ${s.unrated} 题</div>
            <div style="display:flex;flex-wrap:wrap;gap:4px 10px;margin-top:4px">${levels.map((level,i)=>`<span><span style="color:${colors[i]}">●</span> ${level}级 ${s.percentages[i]}%（${s.counts[i]}题）</span>`).join('')}</div>
            <div style="display:flex;height:4px;border-radius:4px;overflow:hidden;background:#e2e8f0;margin-top:5px">${levels.map((_,i)=>`<span style="background:${colors[i]};width:${s.percentages[i]}%"></span>`).join('')}</div></div>`;
    }
    window.DifficultyScale = {levels, names, normalize, summarize, render};
    document.addEventListener('DOMContentLoaded', () => {
        // Establish the default before the editor records its initial clean
        // snapshot; async metadata loading must not create a phantom edit.
        const select = document.getElementById('editDifficulty');
        if (select) {
            select.replaceChildren(new Option('待确认（不计难度）', 'unrated'),
                ...levels.map((level,i) => new Option(`${level}级 · ${names[i]}`, level)));
        }
        const input = document.getElementById('editTags');
        if (input) {
            input.placeholder = '如：易错, 强基, 竞赛（逗号分隔，不计入难度）';
            const row = document.createElement('div'); row.className = 'flex flex-wrap gap-2 mt-2';
            for (const tag of ['易错','强基','竞赛','挑战']) {
                const button = document.createElement('button'); button.type = 'button';
                button.className = 'text-xs px-2 py-1 rounded border border-slate-200 dark:border-slate-700';
                button.textContent = '+ ' + tag;
                button.onclick = () => {
                    const tags = input.value.split(/[,，]/).map(t=>t.trim()).filter(Boolean);
                    input.value = (tags.includes(tag) ? tags.filter(t=>t!==tag) : [...tags,tag]).join(',');
                    input.dispatchEvent(new Event('input', {bubbles:true}));
                    input.dispatchEvent(new Event('change', {bubbles:true}));
                };
                row.append(button);
            }
            input.after(row);
        }
    });
})();
