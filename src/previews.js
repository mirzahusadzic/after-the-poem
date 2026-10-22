'use strict';
(function () {
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
    'Not recorded. This page replays a recording and runs nothing; to try your own commands, clone the repository and run npm run dev on macOS.';
  // The language a native world runs in; registrations name it, and the first native worlds were Zsh and Ruby.
  function medium(entry) {
    return entry.medium || { console: 'Zsh', server: 'Ruby' }[entry.kind];
  }
  function nativeNotice(entry) {
    return (
      (entry.kind === 'console'
        ? 'This world runs in ' + medium(entry)
        : 'This world runs on its own ' + medium(entry) + ' server') +
      ', so it opens only from a local copy of this study: clone the repository and run npm run dev on macOS, ' +
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
    $('console-panel').hidden = true;
    $('preview-handoff').hidden = true;
    $('preview-handoff-link').removeAttribute('href');
    $('preview-workspace').hidden = true;
    $('open-preview').disabled = false;
    $('preview-status').hidden = true;
    $('preview-status').textContent = '';
    if (terminal) {
      terminal.dispose();
      terminal = null;
      fit = null;
    }
    $('terminal').replaceChildren();
    if (wasOpen) $('open-preview').focus({ preventScroll: true });
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

  function mountTerminal(replay) {
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
  }

  async function openPreview() {
    closePreview();
    const requestGeneration = generation,
      entry = report.artefacts[currentRun];
    if (entry.kind === 'none') return;
    $('preview-workspace').hidden = false;
    setFullWindow(true);
    $('preview-workspace').focus({ preventScroll: true });
    if (entry.kind === 'browser') {
      if (location.protocol === 'file:') {
        showPreviewNotice(browserServerNotice, true);
        return;
      }
      $('artefact-frame').setAttribute('sandbox', 'allow-scripts allow-forms allow-downloads');
      $('artefact-frame').src = entry.preview;
      $('artefact-frame').hidden = false;
      showPreviewNotice('Press Esc to exit', false);
      $('artefact-frame').focus();
      return;
    }
    if (entry.kind === 'console' && recording()) {
      session = REPLAY;
      mountTerminal(true);
      await execute(entry.initial_command);
      showPreviewNotice('Press Esc to exit', false);
      return;
    }
    if (!localServer) {
      showPreviewNotice('This preview needs the local runner. Run npm run dev, then open http://127.0.0.1:4317.', true);
      return;
    }
    $('open-preview').disabled = true;
    showPreviewNotice('Opening a fresh local copy…', true);
    try {
      const response = await fetch('/_preview/status');
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
        mountTerminal();
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
        $('artefact-frame').src = result.url;
        $('artefact-frame').hidden = false;
        $('artefact-frame').focus();
      }
      showPreviewNotice('Press Esc to exit', false);
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
              '. This page replays those answers and runs nothing; to try your own commands, clone the repository and run npm run dev on macOS.'
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
      img.src = picture.path;
      img.alt = picture.caption;
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
  $('open-preview').addEventListener('click', openPreview);
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
