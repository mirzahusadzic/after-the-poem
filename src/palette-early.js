'use strict';
// Runs before the first paint, so a page reloaded in a stored palette never shows the default one first: browsers
// read the window tint from the page as it loads. app.js keeps both in step afterwards.
(function () {
  const palettes = ['dusk', 'daylight', 'play'];
  let id = new URLSearchParams(location.search).get('palette');
  if (palettes.indexOf(id) < 0) {
    try {
      id = localStorage.getItem('after-the-poem-palette');
    } catch (_) {
      id = null;
    }
  }
  if (palettes.indexOf(id) >= 0) document.documentElement.dataset.palette = id;
  const tint = document.querySelector('meta[name="theme-color"]');
  const background = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
  if (tint && background) tint.content = background;
})();
