(() => {
    let current = null;
    function mount() {
        const parent = document.getElementById('importLoadingState');
        if (!parent) return;
        let button = document.getElementById('platformPdfResume');
        if (!button) {
            button = document.createElement('button');
            button.id = 'platformPdfResume';
            button.className = 'glass-btn records-secondary-action';
            button.textContent = '恢复未完成步骤（保留已编辑题稿）';
            button.addEventListener('click', async () => {
                if (!current || button.disabled) return;
                button.disabled = true;
                try {
                    await window.resumePdfImportTask(current.taskId);
                    current = null;
                } catch (error) {
                    window.showToast?.(error.message, 'error');
                } finally {
                    button.disabled = false;
                    mount();
                }
            });
            parent.append(button);
        }
        button.hidden = !current?.available || current.taskId !== window.currentPdfTaskId;
    }
    window.addEventListener('mathbank:pdf-recovery', event => {
        current = event.detail;
        mount();
    });
    window.addEventListener('DOMContentLoaded', () => {
        const parent = document.getElementById('importLoadingState');
        if (parent) new MutationObserver(mount).observe(parent, {childList: true});
        mount();
    });
})();
