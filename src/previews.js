'use strict';
(function () {
  if (!window.POEM_REPORT || !window.POEM_REPORT.artefacts) return;
  const report = window.POEM_REPORT;
  const $ = function (id) {
    return document.getElementById(id);
  };
  let currentRun = null,
    session = null,
    token = null,
    terminal = null,
    fit = null;
  let fullWindow = false;
  let noticeTimer = null;
  let generation = 0,
    busy = false,
    line = '',
    history = [],
    historyIndex = 0;
  // Palette slots recolour the host prompt without changing archived output.
  const prompt = '\x1b[38;5;14mworld › \x1b[0m';
  function terminalTheme() {
    const style = getComputedStyle($('terminal'));
    const value = function (name) {
      return style.getPropertyValue(name).trim();
    };
    return {
      background: style.backgroundColor,
      foreground: style.color,
      cursor: value('--console-cursor'),
      selectionBackground: value('--console-selection'),
      brightCyan: value('--console-cursor'),
      brightYellow: value('--console-warning'),
      scrollbarSliderBackground: value('--scrollbar-thumb'),
      scrollbarSliderHoverBackground: value('--scrollbar-hover'),
      scrollbarSliderActiveBackground: value('--console-cursor')
    };
  }
  const localServer = location.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(location.hostname);
  const staticSite = Boolean(report.site && report.site.target === 'pages');
  // On the static site a console world with a recording replays its answers; nothing runs.
  const REPLAY = 'replay';
  function recording() {
    return staticSite && report.recordings ? report.recordings[currentRun] : null;
  }
  function recordedDate(record) {
    return new Date(record.recorded_on + 'T12:00:00Z').toLocaleDateString('en-GB', {
      day: 'numeric',
      month: 'long',
      year: 'numeric'
    });
  }
  const NOT_RECORDED =
    'Not recorded. This page replays a recording and runs nothing; to try your own commands, clone the repository and run npm run dev for a local copy.';
  // The language a native world runs in; registrations name it, and the first native worlds were Zsh and Ruby.
  function medium(entry) {
    return entry.medium || { console: 'Zsh', server: 'Ruby' }[entry.kind];
  }
  function nativeNotice(entry) {
    return (
      (entry.kind === 'console'
        ? 'This world runs in ' + medium(entry)
        : 'This world runs on its own ' + medium(entry) + ' server') +
      ', so it opens only from a local copy of this study: clone the repository and run npm run dev, ' +
      'where a sandboxed runner starts a fresh copy. The image shows such a copy.'
    );
  }
  const browserServerNotice =
    'Sandboxed browser previews need a web server. Run python3 scripts/atlas.py serve, then open http://127.0.0.1:4317.';

  function node(tag, text) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    return element;
  }

  async function api(action, data) {
    const response = await fetch('/_preview/' + action, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ token: token }, data)),
      keepalive: action === 'stop'
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'The local preview could not complete this request.');
    return result;
  }

  function stopSession() {
    const previous = session;
    session = null;
    if (previous && previous !== REPLAY) api('stop', { session: previous }).catch(function () {});
  }

  function showPreviewNotice(text, persistent) {
    const status = $('preview-status');
    if (noticeTimer) clearTimeout(noticeTimer);
    noticeTimer = null;
    status.textContent = text;
    status.hidden = false;
    // The notice hides on a timer in every motion setting; reduced motion only
    // removes the fade animation (see the stylesheet), never the information.
    if (!persistent)
      noticeTimer = setTimeout(function () {
        status.hidden = true;
        noticeTimer = null;
      }, 3000);
  }

  function setFullWindow(enabled) {
    fullWindow = enabled;
    const workspace = $('preview-workspace');
    workspace.classList.toggle('is-full-window', enabled);
    document.body.classList.toggle('preview-full-window', enabled);
    const currentFit = fit;
    if (currentFit)
      setTimeout(function () {
        if (fit === currentFit) fit.fit();
      }, 0);
  }

  // A touch screen has no Escape key; it gets the close button in the corner instead.
  function exitHint() {
    return window.matchMedia && window.matchMedia('(hover: none)').matches ? 'Tap × to exit' : 'Press Esc to exit';
  }

  let previewReturn = null;
  function previewTitle() {
    const run = report.runs.find(function (r) {
      return r.id === currentRun;
    });
    return run ? 'Artefact preview \u00b7 ' + run.title : 'Artefact preview';
  }
  function previewFocusables() {
    const root = $('preview-workspace');
    return Array.from(
      root.querySelectorAll('button, [href], input, textarea, select, [tabindex]:not([tabindex="-1"]), iframe')
    ).filter(function (node) {
      if (node.disabled || node.hidden) return false;
      if (node.tagName === 'IFRAME' && node.hidden) return false;
      return node.offsetParent !== null || node === document.activeElement;
    });
  }
  function trapPreviewTab(event) {
    const items = previewFocusables();
    if (!items.length) {
      event.preventDefault();
      $('preview-close').focus({ preventScroll: true });
      return;
    }
    const at = items.indexOf(document.activeElement);
    let next = null;
    if (event.shiftKey) next = at <= 0 ? items[items.length - 1] : items[at - 1];
    else next = at < 0 || at >= items.length - 1 ? items[0] : items[at + 1];
    event.preventDefault();
    next.focus({ preventScroll: true });
  }
  // Background landmarks around the modal preview. The dialog itself lives inside
  // .shell, so inertness applies to its neighbours, never to one of its ancestors.
  function previewBackground() {
    return [
      document.querySelector('.masthead'),
      document.querySelector('footer'),
      document.querySelector('.lobby-toolbar'),
      document.querySelector('.widget-heading'),
      document.querySelector('.radar-panel'),
      document.querySelector('.workspace-tabs'),
      document.querySelector('#view-reading'),
      document.querySelector('#view-scores'),
      document.querySelector('#view-sources'),
      document.querySelector('#view-method')
    ].filter(Boolean);
  }
  function setPreviewInert(on) {
    previewBackground().forEach(function (node) {
      node.inert = on;
    });
  }
  function closePreview() {
    const wasOpen = !$('preview-workspace').hidden;
    generation += 1;
    busy = false;
    stopSession();
    if (noticeTimer) clearTimeout(noticeTimer);
    noticeTimer = null;
    setFullWindow(false);
    $('artefact-frame').removeAttribute('src');
    $('artefact-frame').hidden = true;
    $('artefact-frame').title = 'Artefact preview';
    $('console-panel').hidden = true;
    $('preview-handoff').hidden = true;
    $('preview-handoff-link').removeAttribute('href');
    $('preview-workspace').hidden = true;
    setPreviewInert(false);
    $('open-preview').disabled = false;
    $('preview-status').hidden = true;
    $('preview-status').textContent = '';
    if (terminal) {
      terminal.dispose();
      terminal = null;
      fit = null;
    }
    $('terminal').replaceChildren();
    if (wasOpen) {
      const target =
        previewReturn && !previewReturn.disabled && document.contains(previewReturn)
          ? previewReturn
          : $('open-preview');
      previewReturn = null;
      try {
        target.focus({ preventScroll: true });
      } catch (_) {
        /* The opener may be gone; the preview already returned focus. */
      }
    }
  }

  function redraw() {
    if (terminal) terminal.write('\r\x1b[2K' + prompt + line);
  }

  async function execute(command) {
    if (!session || busy || !command.trim()) return;
    const runGeneration = generation,
      id = session;
    busy = true;
    line = '';
    $('console-command').value = '';
    terminal.write('\r\x1b[2K' + prompt + command + '\r\n');
    history.push(command);
    historyIndex = history.length;
    try {
      const recorded =
        id === REPLAY
          ? recording().entries.find(function (entry) {
              return entry.command.replace(/\s+/g, ' ').trim() === command.replace(/\s+/g, ' ').trim();
            })
          : null;
      const result =
        id === REPLAY
          ? recorded
            ? { output: recorded.output, exit_code: recorded.exit_code }
            : { output: NOT_RECORDED, exit_code: 0 }
          : await api('command', { session: id, command: command });
      if (runGeneration !== generation) return;
      terminal.writeln(result.output.trimEnd());
      if (result.exit_code !== 0) terminal.writeln('\x1b[38;5;11mExit ' + result.exit_code + '\x1b[0m');
    } catch (error) {
      if (runGeneration === generation) terminal.writeln('Preview: ' + error.message);
    } finally {
      if (runGeneration === generation) {
        busy = false;
        terminal.write(prompt);
      }
    }
  }

  // The terminal library loads once, on first use, in its declared order; a failed load can be retried.
  let terminalAssets = null;
  function loadTerminal() {
    if (!terminalAssets)
      terminalAssets = Array.from($('terminal-assets').content.children).reduce(function (ready, declared) {
        return ready.then(function () {
          return new Promise(function (resolve, reject) {
            const asset = document.createElement(declared.tagName.toLowerCase());
            if (asset.tagName === 'LINK') {
              asset.rel = 'stylesheet';
              asset.href = declared.getAttribute('href');
            } else asset.src = declared.getAttribute('src');
            asset.onload = resolve;
            asset.onerror = function () {
              terminalAssets = null;
              reject(new Error('The terminal could not load. Reload the report and try again.'));
            };
            document.head.append(asset);
          });
        });
      }, Promise.resolve());
    return terminalAssets;
  }

  // Resolves false when the preview closed while the terminal was loading.
  async function mountTerminal(replay) {
    const mountGeneration = generation;
    await loadTerminal();
    if (mountGeneration !== generation) return false;
    $('console-panel').hidden = false;
    terminal = new Terminal({
      fontSize: 14,
      lineHeight: 1.3,
      fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace',
      convertEol: true,
      scrollback: 4000,
      cursorBlink: true,
      screenReaderMode: true,
      overviewRuler: { width: 7 },
      theme: terminalTheme()
    });
    fit = new FitAddon.FitAddon();
    terminal.loadAddon(fit);
    terminal.attachCustomKeyEventHandler(function (event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        setTimeout(closePreview, 0);
        return false;
      }
      return true;
    });
    terminal.open($('terminal'));
    fit.fit();
    const title = report.runs.find(function (r) {
      return r.id === currentRun;
    }).title;
    if (replay) {
      terminal.writeln(
        'A recording of ' + title + ', made in the isolated local runner on ' + recordedDate(recording()) + '.'
      );
      terminal.writeln('Choose a recorded command. This page replays its answers and runs nothing.\r\n');
    } else {
      terminal.writeln('A disposable copy of ' + title);
      terminal.writeln('Type help for commands. Your changes stay in this preview.\r\n');
    }
    terminal.write(prompt);
    line = '';
    history = [];
    historyIndex = 0;
    terminal.onData(function (data) {
      if (busy) return;
      if (data === '\r') {
        execute(line);
        return;
      }
      if (data === '\x7f') {
        line = Array.from(line).slice(0, -1).join('');
        redraw();
        return;
      }
      if (data === '\x03') {
        line = '';
        terminal.write('^C\r\n' + prompt);
        return;
      }
      if (data === '\x1b[A' || data === '\x1b[B') {
        historyIndex = Math.max(0, Math.min(history.length, historyIndex + (data === '\x1b[A' ? -1 : 1)));
        line = history[historyIndex] || '';
        redraw();
        return;
      }
      if (data.includes('\x1b')) return;
      const printable = data.replace(/[\r\n\t]/g, ' ').replace(/[\x00-\x1f\x7f]/g, '');
      line = (line + printable).slice(0, 2048);
      redraw();
    });
    $('console-shortcuts').replaceChildren();
    ['help'].concat(report.artefacts[currentRun].shortcuts).forEach(function (command) {
      const button = node('button', command);
      button.type = 'button';
      button.addEventListener('click', function () {
        execute(command);
      });
      $('console-shortcuts').append(button);
    });
    terminal.focus();
    return true;
  }

  async function openPreview() {
    closePreview();
    const requestGeneration = generation,
      entry = report.artefacts[currentRun];
    if (entry.kind === 'none') return;
    previewReturn = document.activeElement;
    $('preview-workspace').hidden = false;
    $('preview-title').textContent = previewTitle();
    $('artefact-frame').title = previewTitle();
    setFullWindow(true);
    // The open preview is modal: the lobby behind it stays visible but inert.
    setPreviewInert(true);
    $('preview-close').focus({ preventScroll: true });
    if (entry.kind === 'browser') {
      if (location.protocol === 'file:') {
        showPreviewNotice(browserServerNotice, true);
        return;
      }
      $('artefact-frame').setAttribute('sandbox', 'allow-scripts allow-forms allow-downloads');
      $('artefact-frame').title = previewTitle();
      $('artefact-frame').src = entry.preview;
      $('artefact-frame').hidden = false;
      showPreviewNotice(exitHint(), false);
      $('artefact-frame').focus();
      return;
    }
    if (entry.kind === 'console' && recording()) {
      session = REPLAY;
      try {
        if (!(await mountTerminal(true))) return;
      } catch (error) {
        showPreviewNotice(error.message, true);
        return;
      }
      await execute(entry.initial_command);
      showPreviewNotice(exitHint(), false);
      return;
    }
    if (!localServer) {
      showPreviewNotice(
        'This preview needs the local runner. Run npm run dev for a local copy, then open the local report.',
        true
      );
      return;
    }
    $('open-preview').disabled = true;
    showPreviewNotice('Opening a fresh local copy…', true);
    try {
      // The page sends no referrer, and old Safari sends no Fetch Metadata: this one same-origin request
      // carries its referrer so the local runner can recognise the report it serves.
      const response = await fetch('/_preview/status', { referrerPolicy: 'same-origin' });
      if (!response.ok) throw new Error('Restart the local report with npm run dev to enable previews.');
      const status = await response.json();
      token = status.token;
      if (!status.enabled) throw new Error(status.message);
      const result = await api('start', { run_id: currentRun });
      if (generation !== requestGeneration) {
        api('stop', { session: result.session }).catch(function () {});
        return;
      }
      session = result.session;
      if (entry.kind === 'console') {
        if (!(await mountTerminal())) return;
        await execute(entry.initial_command);
      } else {
        const url = new URL(result.url);
        if (url.hostname !== '127.0.0.1' || url.origin === location.origin || url.protocol !== 'http:')
          throw new Error('The preview needs its own local origin.');
        if (entry.window === 'tab') {
          // The world refuses to be framed; offer its running copy as a link instead of altering it.
          $('preview-status').hidden = true;
          $('preview-handoff-link').href = url.href;
          $('preview-handoff').hidden = false;
          $('preview-handoff-link').focus();
          return;
        }
        $('artefact-frame').setAttribute('sandbox', 'allow-scripts allow-forms allow-same-origin allow-downloads');
        $('artefact-frame').title = previewTitle();
        $('artefact-frame').src = result.url;
        $('artefact-frame').hidden = false;
        $('artefact-frame').focus();
      }
      showPreviewNotice(exitHint(), false);
    } catch (error) {
      if (generation === requestGeneration) {
        stopSession();
        showPreviewNotice(error.message, true);
      }
    } finally {
      if (generation === requestGeneration) $('open-preview').disabled = false;
    }
  }

  function describeAvailability() {
    const entry = report.artefacts[currentRun];
    const nativeOnly = staticSite && (entry.kind === 'server' || entry.kind === 'console') && !recording();
    $('open-preview').hidden = nativeOnly;
    $('preview-availability').textContent =
      entry.kind === 'browser'
        ? location.protocol === 'file:'
          ? browserServerNotice
          : entry.preview_note
        : entry.kind === 'none'
          ? 'No executable preview is configured for this imported bundle.'
          : recording()
            ? 'Recorded in the isolated local runner on ' +
              recordedDate(recording()) +
              '. This page replays those answers and runs nothing; to try your own commands, clone the repository and run npm run dev for a local copy.'
            : nativeOnly
              ? nativeNotice(entry)
              : 'Opens here in your browser when the local preview runner is available.';
  }
  document.addEventListener('poem:palette', function () {
    if (terminal) terminal.options.theme = terminalTheme();
  });
  document.addEventListener('poem:run', function (event) {
    if (currentRun === event.detail) return;
    closePreview();
    currentRun = event.detail;
    const entry = report.artefacts[currentRun];
    // The static site never offers a native run: those artefacts need the local, sandboxed runner.
    const nativeOnly = staticSite && (entry.kind === 'server' || entry.kind === 'console') && !recording();
    $('artefact-kind').textContent =
      staticSite && entry.kind === 'console' && recording()
        ? medium(entry) + ' · recorded console'
        : nativeOnly
          ? { server: 'Browser + ' + medium(entry) + ' · runs locally', console: medium(entry) + ' · runs locally' }[
              entry.kind
            ]
          : {
              browser: 'Browser artefact',
              server: 'Browser + ' + medium(entry),
              console: medium(entry) + ' · in-browser console',
              none: 'Source record only'
            }[entry.kind];
    $('artefact-description').textContent = entry.description;
    $('open-preview').textContent =
      entry.kind === 'console' ? (recording() ? 'Replay console ↗' : 'Open console ↗') : 'Open preview ↗';
    $('open-preview').disabled = entry.kind === 'none';
    describeAvailability();
    $('artefact-documents').replaceChildren();
    entry.documents.forEach(function (path) {
      const item = node('li'),
        link = node('a', path + ' ↗');
      link.href = 'sources/' + currentRun + '/' + path.split('/').map(encodeURIComponent).join('/') + '.html';
      link.target = '_blank';
      link.rel = 'noopener';
      item.append(link);
      $('artefact-documents').append(item);
    });
    $('artefact-images').replaceChildren();
    entry.images.forEach(function (picture) {
      const figure = node('figure'),
        link = node('a'),
        img = node('img');
      link.href = picture.path;
      link.target = '_blank';
      link.rel = 'noopener';
      link.setAttribute('aria-label', picture.caption + ' (opens the image)');
      img.src = picture.path;
      img.alt = '';
      img.loading = 'lazy';
      link.append(img);
      figure.append(link, node('figcaption', picture.caption));
      $('artefact-images').append(figure);
    });
    if (!entry.images.length) {
      const document = node('article');
      document.className = 'artefact-document';
      document.append(node('h4', 'No representative screenshot retained'));
      document.append(
        node(
          'p',
          'This run has preserved source material' +
            (entry.kind === 'none' ? '.' : ' and may have an interactive preview.') +
            ' The absence of a screenshot is not a judgment about the work.'
        )
      );
      $('artefact-images').append(document);
    }
  });
  if ($('open-preview') && $('preview-availability'))
    $('open-preview').setAttribute('aria-describedby', 'preview-availability');
  // A preview must never carry the study to another origin: block external form posts here,
  // while the frame sandbox (no top navigation) contains the framed world itself.
  document.addEventListener(
    'submit',
    function (event) {
      const form = event.target;
      if (!form || form.tagName !== 'FORM' || form.id === 'console-form') return;
      const action = form.getAttribute('action');
      if (!action || action.startsWith('#')) return;
      let url = null;
      try {
        url = new URL(action, location.href);
      } catch (_) {
        return;
      }
      if (url.origin !== location.origin) event.preventDefault();
    },
    true
  );
  $('preview-workspace').addEventListener('keydown', function (event) {
    if (event.key === 'Tab' && !$('preview-workspace').hidden) {
      event.preventDefault();
      trapPreviewTab(event);
    }
  });
  $('open-preview').addEventListener('click', openPreview);
  $('preview-close').addEventListener('click', closePreview);
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && fullWindow && !$('preview-workspace').hidden) {
      event.preventDefault();
      closePreview();
    }
  });
  window.addEventListener('message', function (event) {
    if (
      fullWindow &&
      event.source === $('artefact-frame').contentWindow &&
      event.data &&
      event.data.type === 'after-the-poem:escape'
    )
      closePreview();
  });
  $('console-form').addEventListener('submit', function (event) {
    event.preventDefault();
    execute($('console-command').value);
  });
  new ResizeObserver(function () {
    if (fit && $('terminal').clientWidth && !$('console-panel').hidden) fit.fit();
  }).observe($('terminal'));
  window.addEventListener('pagehide', function () {
    if (session && token) navigator.sendBeacon('/_preview/stop', JSON.stringify({ token: token, session: session }));
  });
})();
