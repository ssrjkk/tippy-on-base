/* Loaded synchronously in <head> so the saved theme lands before first paint. */
(function () {
  var root = document.documentElement;
  var saved = localStorage.getItem('theme');
  if (saved === 'dark' || (saved !== 'light' && window.matchMedia &&
      window.matchMedia('(prefers-color-scheme: dark)').matches)) {
    root.setAttribute('data-theme', 'dark');
  }
  document.addEventListener('click', function (e) {
    if (!e.target.closest('[data-act="toggleTheme"]')) return;
    var dark = root.getAttribute('data-theme') === 'dark';
    if (dark) root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', 'dark');
    localStorage.setItem('theme', dark ? 'light' : 'dark');
  });
})();
