// Applies this phone's appearance choice (Auto / Light / Dark) before the first
// paint. It is a separate file because the page's security policy allows no
// inline script. "Auto" follows the phone's own light or dark setting.
(function () {
  var meta = document.querySelector('meta[name="theme-color"]');
  var light = window.matchMedia('(prefers-color-scheme: light)');
  function apply() {
    var choice = null;
    try { choice = JSON.parse(localStorage.getItem('pool-theme')); } catch (error) { /* private mode */ }
    var root = document.documentElement;
    if (choice === 'light' || choice === 'dark') root.setAttribute('data-theme', choice);
    else root.removeAttribute('data-theme');
    var dark = choice === 'dark' || (choice !== 'light' && !light.matches);
    if (meta) meta.setAttribute('content', dark ? '#0b1620' : '#eef4f7');
  }
  apply();
  // Installed on the Home Screen: the stylesheet sizes the app shell differently there.
  if (window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone) document.documentElement.classList.add('standalone-pwa');
  if (light.addEventListener) light.addEventListener('change', apply);
  window.poolApplyTheme = apply;
})();
