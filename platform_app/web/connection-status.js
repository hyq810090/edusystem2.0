/* Probe the current bank, not merely the platform's liveness endpoint. */
(() => {
    let pending = null;
    let failed = false;
    const delays = [2000, 5000, 10000];
    function show(text, state) {
        const indicator = document.getElementById('apiStatusIndicator');
        if (!indicator) return;
        indicator.textContent = text;
        indicator.className = 'flex items-center space-x-1.5 px-3 py-1.5 rounded-full text-xs font-medium border ' +
            (state === 'ready' ? 'bg-green-50 text-green-700 border-green-200' : 'bg-red-50 text-red-700 border-red-200');
        indicator.dataset.connectionState = state;
        indicator.setAttribute('role', 'button');
        indicator.setAttribute('tabindex', '0');
        indicator.setAttribute('aria-label', text + '，点击重新检查连接');
        indicator.onclick = () => window.checkBankConnection();
        indicator.onkeydown = e => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); window.checkBankConnection(); }
        };
    }
    async function probe() {
        for (let attempt = 0; attempt <= delays.length; attempt++) {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 10000);
            try {
                const response = await fetch('/api/questions?page=1&page_size=1', {signal:controller.signal, cache:'no-store'});
                if (!response.ok) throw new Error('Bank unavailable');
                const data = await response.json();
                if (!Array.isArray(data) && (!data || !Array.isArray(data.items))) throw new Error('Invalid bank response');
                failed = false;
                show('题库就绪', 'ready');
                return true;
            } catch (_) {
                failed = true;
                if (attempt === delays.length) {
                    show('后台连接中断 · 点击重试', 'failed');
                    return false;
                }
                show('后台暂不可用，正在重连…', 'retrying');
            } finally {
                clearTimeout(timeout);
            }
            await new Promise(resolve => setTimeout(resolve, delays[attempt]));
        }
    }
    window.checkBankConnection = () => {
        if (!pending) pending = probe().finally(() => { pending = null; });
        return pending;
    };
    window.addEventListener('online', () => { if (failed) window.checkBankConnection(); });
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden && failed) window.checkBankConnection();
    });
})();
