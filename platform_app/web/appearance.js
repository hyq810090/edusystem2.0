/* Shared appearance only; account drafts retain their separate namespaces. */
(() => {
    try {
        const theme=localStorage.getItem('mb-platform:theme-color')||'violet';
        if (/^[a-z]+$/.test(theme)) document.documentElement.classList.add('theme-'+theme);
        document.documentElement.classList.toggle('dark',localStorage.getItem('mb-platform:dark-mode')==='true');
    } catch (_) {}
})();
