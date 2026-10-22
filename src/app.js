'use strict';
(function () {
  const report = window.POEM_REPORT;
  const axes = report.rubric.axes;
  const scaleValues = report.rubric.scale.slice();
  const scaleMin = scaleValues[0],
    scaleMax = scaleValues[scaleValues.length - 1];
  const runById = function (id) {
    return report.runs.find(function (run) {
      return run.id === id;
    });
  };
  const findCohort = function (id) {
    return report.cohorts.find(function (c) {
      return c.id === id;
    });
  };
  const params = new URLSearchParams(location.hash.slice(1));
  const requestedRun = runById(params.get('run'));
  let selectedCohort = findCohort(params.get('cohort'))
    ? params.get('cohort')
    : requestedRun
      ? requestedRun.cohort
      : report.cohorts[0].id;
  function defaultRun(cohort) {
    const members = report.runs.filter(function (run) {
      return run.cohort === cohort.id;
    });
    return runById(cohort.default_run) ? cohort.default_run : members[members.length - 1].id;
  }
  let selected =
    requestedRun && requestedRun.cohort === selectedCohort ? requestedRun.id : defaultRun(findCohort(selectedCohort));
  let axisId = axes.some(function (a) {
    return a.id === params.get('axis');
  })
    ? params.get('axis')
    : axes[0].id;
  // Pinned readings stay on the radar and in the reading pane while others are selected.
  // Three outlines plus the selected reading stay legible; larger comparisons belong in All scores.
  const MAX_PINS = 3;
  // In pin mode (release view) the comparison reference is just a default pin the reader may remove.
  const pinMode = Boolean(report.comparison && report.comparison.mode === 'pins');
  const pinParam = params.get('pin');
  let pinned = (pinParam !== null ? pinParam : pinMode ? report.comparison.default_pins.join(',') : '')
    .split(',')
    .filter(function (id, index, all) {
      return runById(id) && all.indexOf(id) === index;
    })
    .slice(0, MAX_PINS);
  const $ = function (id) {
    return document.getElementById(id);
  };
  const el = function (tag, text, className) {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    if (className) n.className = className;
    return n;
  };
  const esc = function (text) {
    return String(text).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  const active = function () {
    return report.active_reviews[selected];
  };
  const cohort = function () {
    return findCohort(selectedCohort);
  };
  // Confidence has two parts on the page: the reader's own, and how many independent sessions gave the level.
  function details(id, axis) {
    return report.reading_details && report.reading_details[id] && report.reading_details[id][axis];
  }
  // The score shown for a reading on one question: the mean of its sessions' levels where every session gave one,
  // otherwise the recorded level. The level named beside it stays the one at least two sessions gave.
  function shownScore(id, axis, review) {
    review = review || report.active_reviews[id];
    const value = review.scores[axis].value,
      record = details(id, axis);
    if (
      value === null ||
      review !== report.active_reviews[id] ||
      !record ||
      record.sessions < 2 ||
      record.levels.length !== record.sessions
    )
      return value;
    return (
      record.levels.reduce(function (sum, level) {
        return sum + level;
      }, 0) / record.sessions
    );
  }
  function spreadCaption(id) {
    return axes.some(function (a) {
      const v = shownScore(id, a.id);
      return v !== null && v !== Math.round(v);
    })
      ? 'A point between rings is the mean of the three sessions; 2/3 beside it: two of them gave the level named.'
      : '';
  }
  function confidenceText(score) {
    const agreement = details(selected, axisId);
    if (!agreement || score.value === null) return 'Confidence: ' + score.confidence;
    const own = 'Confidence: ' + (agreement.reader_confidence || score.confidence);
    if (agreement.sessions < 2) return own;
    return (
      own +
      ' · ' +
      (agreement.agreeing === agreement.sessions
        ? 'all ' + agreement.sessions + ' sessions agree'
        : agreement.agreeing + ' of ' + agreement.sessions + ' sessions agree')
    );
  }
  // A reading of several independent sessions says how many, where the record's name says one rubric session.
  function readerLabel(review) {
    const record = report.reading_details && report.reading_details[selected];
    const sessions = record && Object.keys(record).length ? record[Object.keys(record)[0]].sessions : 1;
    if (sessions < 2) return review.reviewer;
    const count = ['', 'one', 'two', 'three', 'four', 'five'][sessions] || String(sessions);
    return review.reviewer.replace(/ · rubric session$/, '') + ' · ' + count + ' sessions';
  }
  // The record appends its agreement to the rationale; the page states it beside the confidence instead.
  // Who can do what in a world explains shared agency and mutual consent, so it is shown on those two.
  const STANDING_AXES = ['relations', 'consent'];
  const STANDING_NAMES = {
    person: 'A person',
    intelligence: 'The intelligence',
    successor: 'A successor',
    material: 'The material',
    other: 'Others'
  };
  function renderStanding() {
    const standing = report.standing && report.standing[selected];
    if (!standing) return;
    const box = el('section', undefined, 'standing');
    box.setAttribute('aria-label', 'Who can do what in this world');
    box.append(el('p', 'Who can do what here', 'passage-label'));
    const list = el('dl');
    standing.participants.forEach(function (participant) {
      list.append(
        el('dt', STANDING_NAMES[participant.kind] || participant.kind),
        el('dd', participant.gestures.length ? participant.gestures.join(', ') : 'no gesture')
      );
    });
    box.append(
      list,
      el(
        'p',
        'Gestures the reader found in at least ' +
          (Math.floor(standing.sessions / 2) + 1) +
          ' of ' +
          standing.sessions +
          ' sessions.',
        'fine'
      )
    );
    $('evidence').append(box);
  }
  function withoutAgreement(text) {
    return text.replace(/\s*Agreement: \d+ samples, [0-9.]+\.$/, '');
  }
  // A run may carry a prompt that differs from its cohort's only in the line naming its harness.
  const promptCohort = function () {
    const run = runById(selected);
    return (run && run.prompt_cohort) || selectedCohort;
  };
  const promptUrl = function () {
    return report.prompts[promptCohort()].url;
  };
  const baselineId = function () {
    return report.baseline;
  };
  const baselineLabel = function () {
    return runById(baselineId()).label + ' · original';
  };
  const baselineReview = function () {
    return (
      report.reviews.find(function (review) {
        return review.id === report.baseline_review_id;
      }) || report.active_reviews[baselineId()]
    );
  };
  const comparisonNote = function () {
    const run = runById(selected),
      reference = runById(baselineId());
    if (run.cohort === reference.cohort) return '';
    return run.prompt_sha256 !== reference.prompt_sha256
      ? 'Different prompts and conditions · interpretive comparison.'
      : 'Different cohorts · equivalent conditions not established.';
  };
  const scoreLabel = function (value) {
    if (value === null) return 'Unassessed';
    // A mean of three sessions is written in thirds: 3⅓ is two sessions at 3 and one at 4.
    const whole = Math.floor(value + 1e-9),
      part = Math.round((value - whole) * 3);
    return part === 0
      ? String(whole)
      : part === 3
        ? String(whole + 1)
        : (whole ? String(whole) : '') + (part === 1 ? '⅓' : '⅔');
  };
  const deltaLabel = function (delta) {
    return delta === 0 ? '0' : (delta > 0 ? '+' : '−') + scoreLabel(Math.abs(delta));
  };
  const conciseRationale = function (text, anchor) {
    const normalized = String(text || '')
      .replace(/\s+/g, ' ')
      .trim();
    const sentences = normalized.match(/.*?[.!?](?=\s|$)|.+$/g) || [normalized];
    const words = (value) =>
      new Set(
        String(value)
          .toLowerCase()
          .match(/[a-z0-9]+/g) || []
      );
    const anchorWords = words(anchor);
    const repeatsAnchor = (sentence) => {
      const candidate = words(sentence.replace(/^Level\s*[0-4]\s*[:.\-]?\s*/i, ''));
      const overlap = [...candidate].filter((word) => anchorWords.has(word)).length;
      return anchorWords.size > 0 && overlap / Math.max(1, Math.min(candidate.size, anchorWords.size)) >= 0.72;
    };
    const substantive = sentences
      .map((sentence) => sentence.trim())
      .filter(Boolean)
      .filter((sentence) => !/^Level\s*[0-4]\b/i.test(sentence) && !repeatsAnchor(sentence));
    let summary = substantive[0] || sentences[0].trim().replace(/^Level\s*[0-4]\s*[:.\-]?\s*/i, '');
    if (summary.length > 240) {
      summary = summary.slice(0, 237);
      const boundary = summary.lastIndexOf(' ');
      if (boundary > 160) summary = summary.slice(0, boundary);
      summary = summary.replace(/[,;:\-–—\s]+$/, '') + '…';
    }
    return summary;
  };
  const scaleIndex = function (value) {
    return scaleValues.indexOf(value);
  };
  const scorePosition = function (value) {
    if (value === null || scaleValues.length < 2) return 0;
    const index = scaleIndex(value);
    if (index >= 0) return index / (scaleValues.length - 1);
    // A mean of sessions sits between two levels of the scale, in proportion.
    for (let k = 0; k < scaleValues.length - 1; k += 1) {
      const low = scaleValues[k],
        high = scaleValues[k + 1];
      if (value > low && value < high) return (k + (value - low) / (high - low)) / (scaleValues.length - 1);
    }
    return 0;
  };
  function chartLabelLines(label) {
    const words = String(label).trim().split(/\s+/),
      split = Math.ceil(words.length / 2);
    return words.length < 2 ? [label, ''] : [words.slice(0, split).join(' '), words.slice(split).join(' ')];
  }
  function radarTicks() {
    const candidates = scaleValues.slice(1);
    if (candidates.length <= 6) return candidates;
    const indexes = [
      0,
      Math.round((candidates.length - 1) * 0.25),
      Math.round((candidates.length - 1) * 0.5),
      Math.round((candidates.length - 1) * 0.75),
      candidates.length - 1
    ];
    return Array.from(
      new Set(
        indexes.map(function (index) {
          return candidates[index];
        })
      )
    );
  }
  const link = function (text, url) {
    const a = el('a', text);
    a.href = url;
    if (!String(url).startsWith('#')) {
      a.target = '_blank';
      a.rel = 'noopener';
    }
    return a;
  };
  function blankDocumentLinks(root) {
    (root || document).querySelectorAll('a[href]').forEach(function (a) {
      const href = a.getAttribute('href');
      if (!href || href.startsWith('#')) return;
      a.target = '_blank';
      a.rel = 'noopener';
    });
  }
  const citeUrl = function (run, c) {
    const path = c.source_path || c.path;
    return 'sources/' + run + '/' + path.split('/').map(encodeURIComponent).join('/') + '.html#L' + c.start;
  };

  const palettes = ['dusk', 'daylight', 'play'];
  function themeDocumentLinks() {
    const palette = document.documentElement.dataset.palette;
    document.querySelectorAll('a[href]').forEach(function (a) {
      const href = a.getAttribute('href');
      if (/^(sources\/|observations\/|licenses[./])/.test(href)) {
        a.setAttribute(
          'href',
          href.replace(/(?:\.(?:daylight|play))?\.html(?=#|$)/, (palette === 'dusk' ? '' : '.' + palette) + '.html')
        );
      }
    });
  }
  function choosePalette(id, remember) {
    document.documentElement.dataset.palette = id;
    document.querySelectorAll('.palette-option').forEach(function (button) {
      button.setAttribute('aria-pressed', String(button.dataset.palette === id));
    });
    if (remember) {
      try {
        localStorage.setItem('after-the-poem-palette', id);
      } catch (_) {
        /* File previews can deny storage. */
      }
      const url = new URL(location.href);
      url.searchParams.set('palette', id);
      try {
        history.replaceState(null, '', url);
      } catch (_) {
        /* Static file previews still switch. */
      }
    }
    themeDocumentLinks();
    document.dispatchEvent(new CustomEvent('poem:palette', { detail: id }));
  }
  let remembered;
  try {
    remembered = localStorage.getItem('after-the-poem-palette');
  } catch (_) {
    /* Storage is optional. */
  }
  const requestedPalette = new URLSearchParams(location.search).get('palette') || remembered;
  choosePalette(palettes.includes(requestedPalette) ? requestedPalette : 'dusk', false);
  document.querySelectorAll('.palette-option').forEach(function (button) {
    button.addEventListener('click', function () {
      choosePalette(button.dataset.palette, true);
    });
  });
  function closePalette() {
    $('palette-study').open = false;
  }
  document.addEventListener('click', function (event) {
    if (!$('palette-study').contains(event.target)) closePalette();
  });
  $('palette-study').addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      closePalette();
      $('palette-study').querySelector('summary').focus();
    }
  });

  function showView(id) {
    document.querySelector('.workspace').dataset.view = id;
    document.querySelectorAll('.workspace-tabs [data-view]').forEach(function (button) {
      const active = button.dataset.view === id;
      button.setAttribute('aria-selected', String(active));
      button.tabIndex = active ? 0 : -1;
      $('view-' + button.dataset.view).hidden = !active;
    });
    document.dispatchEvent(new CustomEvent('poem:view', { detail: id }));
  }
  document.querySelectorAll('.workspace-tabs [data-view]').forEach(function (button, index, buttons) {
    button.addEventListener('click', function () {
      showView(button.dataset.view);
    });
    button.addEventListener('keydown', function (event) {
      const open = Array.from(buttons).filter(function (item) {
          return !item.hidden;
        }),
        at = open.indexOf(button);
      let next;
      if (event.key === 'ArrowRight') next = (at + 1) % open.length;
      if (event.key === 'ArrowLeft') next = (at + open.length - 1) % open.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = open.length - 1;
      if (next !== undefined) {
        event.preventDefault();
        showView(open[next].dataset.view);
        open[next].focus();
      }
    });
  });

  function selectRun(id) {
    if (id !== selected && crossing.assembled && !crossing.announced) {
      crossing.announced = true;
      saveCrossing();
    }
    selected = id;
    selectedCohort = runById(id).cohort;
    document.querySelectorAll('.workspace-view').forEach(function (pane) {
      pane.scrollTop = 0;
    });
    render();
    const control =
      document.querySelector('.reading-button[data-reading="' + id + '"]') ||
      $('run-picker').querySelector('[data-run="' + artefactOf(runById(id)).id + '"]');
    control.focus({ preventScroll: true });
    updateLocation();
  }

  function selectCohort(id) {
    selectedCohort = id;
    selected = defaultRun(findCohort(id));
    render();
    updateLocation();
  }

  function updateLocation() {
    try {
      const url = new URL(location.href);
      url.hash =
        'cohort=' +
        encodeURIComponent(selectedCohort) +
        '&run=' +
        encodeURIComponent(selected) +
        '&axis=' +
        encodeURIComponent(axisId) +
        (pinned.length ? '&pin=' + pinned.map(encodeURIComponent).join(',') : pinMode ? '&pin=' : '');
      history.replaceState(null, '', url);
    } catch (_) {
      /* Static file previews still navigate by controls. */
    }
  }

  function selectAxis(id) {
    axisId = id;
    showView('reading');
    renderRadar();
    renderAxis();
    $('view-reading').scrollTop = 0;
    $('evidence').scrollTop = 0;
    themeDocumentLinks();
    updateLocation();
  }

  // The brand mark returns the workbench to where it opens: its first world, the first question and the
  // default pins (none outside the release view).
  function resetExploration(event) {
    event.preventDefault();
    selectedCohort = report.cohorts[0].id;
    selected = defaultRun(findCohort(selectedCohort));
    if (game) {
      selectedCohort = runById(game.first_world).cohort;
      selected = game.first_world;
    }
    axisId = axes[0].id;
    pinned = (pinMode ? report.comparison.default_pins : [])
      .filter(function (id) {
        return runById(id);
      })
      .slice(0, MAX_PINS);
    focusPin(null);
    render();
    showView('reading');
    try {
      const url = new URL(location.href);
      url.hash = '';
      history.replaceState(null, '', url);
    } catch (_) {
      /* Static-file previews can still reset the in-memory state. */
    }
    window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
    $('run-title').setAttribute('tabindex', '-1');
    $('run-title').focus({ preventScroll: true });
  }
  $('brand-home').addEventListener('click', resetExploration);
  // The scale opens as a small panel; a click elsewhere or Escape closes it.
  document.addEventListener('click', function (event) {
    const scale = document.querySelector('.score-scale');
    if (scale && scale.open && !scale.contains(event.target)) scale.open = false;
  });
  document.addEventListener('keydown', function (event) {
    const scale = document.querySelector('.score-scale');
    if (event.key === 'Escape' && scale && scale.open) {
      scale.open = false;
      scale.querySelector('summary').focus();
    }
  });

  function renderCohort() {
    const count = cohort().runs.length;
    const reference = runById(baselineId());
    const varied = cohort().runs.some(function (id) {
      const run = runById(id);
      return run && run.prompt_cohort;
    });
    $('cohort-scope').textContent = varied
      ? count + ' runs received the same poem and invitation; only the first line, which names the harness, differs.'
      : count === 1
        ? '1 run uses this prompt.'
        : count + ' runs share one prompt.';
    $('baseline-name').textContent = pinMode
      ? reference.label +
        ' (' +
        reference.date +
        '), the earliest run, is pinned first as a chronological reference, not an answer key, and can be unpinned.'
      : reference.label +
        '’s original is the historical comparison reference across cohorts, not an answer key or a perfect score.';
    $('baseline-label').textContent = baselineLabel() + ' · ' + reference.effort;
    $('footer-rubric').textContent = 'Rubric ' + report.rubric.version;
    $('exact-prompt').href = 'prompts/' + encodeURIComponent(promptCohort()) + '.txt';
    $('seed-nav').href = promptUrl();
    $('seed-nav').textContent = report.threshold
      ? 'The poem'
      : selectedCohort === reference.cohort
        ? 'Read the poem ↗'
        : 'Read the prompt ↗';
    const unreviewed = active().status === 'unreviewed';
    $('reading-status').textContent = unreviewed ? 'Unreviewed' : 'A possible reading';
    const caption = $('chart-caption');
    caption.replaceChildren();
    caption.append(
      el(
        'span',
        unreviewed ? 'Selected run unreviewed · missing values are not zero.' : 'Follow a dimension into the reading.',
        'chart-caption-cue'
      )
    );
    if (comparisonNote()) caption.append(el('span', comparisonNote(), 'chart-caption-context'));
    if (spreadCaption(selected)) caption.append(el('span', spreadCaption(selected), 'chart-caption-context'));
    renderJournal();
    document
      .querySelector('.radar-panel')
      .setAttribute('aria-label', $('reading-status').textContent + ' · ' + axes.length + '-axis profile');
  }

  function renderCohortPicker() {
    const picker = $('cohort-picker');
    picker.replaceChildren();
    picker.hidden = report.cohorts.length < 2;
    if (report.cohorts.length < 2) return;
    report.cohorts.forEach(function (item) {
      const button = el('button', item.title, 'cohort-button');
      button.type = 'button';
      button.setAttribute('aria-pressed', String(item.id === selectedCohort));
      button.addEventListener('click', function () {
        selectCohort(item.id);
      });
      picker.append(button);
    });
  }

  // Runs are grouped by the model family that made the artefact (the label before
  // ' · '). An evaluation run joins the group of the run it reads. A family with one
  // run is one chip; otherwise each chip keeps only what distinguishes it.
  function family(label) {
    return label.split(' · ')[0];
  }

  // An artefact's readings: its own selected review first, then every evaluation run of it.
  function artefactOf(r) {
    return r.source_run_id ? runById(r.source_run_id) : r;
  }
  function readingsOf(artefact) {
    return [artefact].concat(
      report.runs.filter(function (r) {
        return r.source_run_id === artefact.id;
      })
    );
  }
  function readingLabel(r) {
    if (!r.source_run_id) return family(report.active_reviews[r.id].reviewer || 'Original reading');
    const artefact = artefactOf(r),
      prefix = family(artefact.label) + ' · ';
    const rest = r.label.indexOf(prefix) === 0 ? r.label.slice(prefix.length) : r.label;
    return rest.replace(/ reading$/, '') || rest;
  }

  // ── The crossing ──────────────────────────────────────────────────────────────
  // Before the workbench, the reader rereads the first world from its own files: each passage
  // its reading cites is seen when it comes into view, and a question takes shape when half of
  // its passages have been seen. When all take shape, the workbench opens. The full research
  // view has no crossing.
  const game = report.game || null;
  const CROSSING_KEY = 'after-the-poem-crossing';
  let crossing = { seen: [], assembled: false, announced: false };
  // The record is sealed with a digest of its contents and this study's identity. An edited
  // record does not verify and simply starts again; whether the reading is assembled is never
  // trusted from storage, it is recomputed from the passages seen. (A static page cannot keep a
  // determined reader out; it can make every shortcut longer than the crossing itself.)
  function digest(text) {
    let h1 = 0xdeadbeef ^ text.length,
      h2 = 0x41c6ce57 ^ text.length;
    for (let i = 0; i < text.length; i++) {
      const c = text.charCodeAt(i);
      h1 = Math.imul(h1 ^ c, 2654435761);
      h2 = Math.imul(h2 ^ c, 1597334677);
    }
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    return (h2 >>> 0).toString(36) + (h1 >>> 0).toString(36);
  }
  function seal(state) {
    return digest(report.rubric_sha256 + '|' + report.baseline + '|' + JSON.stringify(state));
  }
  // The first world's passages: each cited range once, with the questions it carries.
  const passages = {};
  if (game) {
    const review = report.active_reviews[game.first_world];
    axes.forEach(function (axis) {
      review.scores[axis.id].citations.forEach(function (c) {
        const key = c.path + ':' + c.start + '-' + c.end;
        (passages[key] = passages[key] || { key: key, path: c.path, start: c.start, end: c.end, axes: [] }).axes.push(
          axis.id
        );
      });
    });
  }
  function needed(axisId) {
    return Math.ceil(report.active_reviews[game.first_world].scores[axisId].citations.length / 2);
  }
  function seenFor(axisId) {
    return crossing.seen.filter(function (key) {
      return passages[key].axes.indexOf(axisId) >= 0;
    }).length;
  }
  function takenShape(axisId) {
    return seenFor(axisId) >= needed(axisId);
  }
  function restoreCrossing() {
    const stored = JSON.parse(localStorage.getItem(CROSSING_KEY) || 'null');
    if (!stored || typeof stored.state !== 'object' || stored.seal !== seal(stored.state)) return;
    crossing.seen = (Array.isArray(stored.state.seen) ? stored.state.seen : []).filter(function (key, i, all) {
      return passages[key] && all.indexOf(key) === i;
    });
    crossing.assembled = axes.every(function (axis) {
      return takenShape(axis.id);
    });
    crossing.announced = crossing.assembled && stored.state.announced === true;
  }
  if (game) {
    try {
      restoreCrossing();
    } catch (_) {
      /* Each visit begins a new crossing. */
    }
  }
  function saveCrossing() {
    try {
      localStorage.setItem(CROSSING_KEY, JSON.stringify({ state: crossing, seal: seal(crossing) }));
    } catch (_) {
      /* Kept for this visit only. */
    }
  }
  function godmode() {
    return !game || crossing.assembled;
  }
  function pinClass(id) {
    return 'pin-' + (pinned.indexOf(id) + 1);
  }
  function shownPins() {
    return pinned.filter(function (id) {
      return id !== selected;
    });
  }
  function pinName(id) {
    const run = runById(id);
    const twins =
      report.runs.filter(function (item) {
        return item.label === run.label;
      }).length > 1;
    return run.label + (twins ? ' · ' + run.effort : '');
  }
  // Compact names for legend chips and the pinned strip: the model alone where no other run shares it;
  // otherwise what tells them apart, after the family's first word when the family is long.
  function shortPinName(id) {
    const name = pinName(id),
      fam = family(name),
      words = fam.split(' ');
    const shared = report.runs.some(function (r) {
      return r.id !== id && !r.source_run_id && family(r.label) === fam;
    });
    if (!shared) return fam;
    return words.length > 2 ? words[0] + name.slice(fam.length) : name;
  }
  // A line sample matching the pin's colour and dash pattern on the radar.
  function lineSwatch(className) {
    const sample = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    sample.setAttribute('class', 'line-swatch');
    sample.setAttribute('viewBox', '0 0 22 6');
    sample.setAttribute('aria-hidden', 'true');
    sample.innerHTML = '<line class="' + className + '" x1="1" y1="3" x2="21" y2="3"/>';
    return sample;
  }
  // Pointing at a pin brings its outline forward and quiets the others.
  function focusPin(id) {
    if (id) $('radar').dataset.focus = pinClass(id);
    else delete $('radar').dataset.focus;
  }
  function focusable(node, id) {
    node.addEventListener('mouseenter', function () {
      focusPin(id);
    });
    node.addEventListener('mouseleave', function () {
      focusPin(null);
    });
    node.addEventListener('focus', function () {
      focusPin(id);
    });
    node.addEventListener('blur', function () {
      focusPin(null);
    });
  }
  function togglePin(id) {
    const index = pinned.indexOf(id);
    if (index >= 0) pinned.splice(index, 1);
    else {
      if (pinned.length >= MAX_PINS) pinned.shift();
      pinned.push(id);
    }
    render();
    updateLocation();
  }

  // Sideways-scrolling rows fade at their edge only while more content is hidden.
  function markOverflow(row) {
    row.classList.toggle('is-overflowing', row.scrollLeft + row.clientWidth < row.scrollWidth - 1);
  }
  function markLegendOverflow() {
    markOverflow($('pinned-legend'));
  }

  function renderPins() {
    $('baseline-label').parentElement.hidden = pinMode;
    $('baseline-label').closest('.legend').classList.toggle('is-pins', pinMode);
    const toggle = $('pin-toggle'),
      isPinned = pinned.indexOf(selected) >= 0;
    toggle.setAttribute('aria-pressed', String(isPinned));
    toggle.textContent = isPinned ? 'Pinned' : 'Pin';
    toggle.setAttribute(
      'aria-label',
      (isPinned ? 'Unpin ' : 'Pin ') + pinName(selected) + (isPinned ? ' from' : ' to') + ' the comparison'
    );
    toggle.onclick = function () {
      togglePin(selected);
      $('pin-toggle').focus();
    };
    toggle.hidden = !godmode();
    const legend = $('pinned-legend');
    legend.replaceChildren();
    shownPins().forEach(function (id) {
      const row = el('span', undefined, 'pin-chip'),
        name = el('button', undefined, 'pin-name'),
        remove = el('button', '×', 'pin-remove');
      name.type = remove.type = 'button';
      name.title = pinName(id) + ' · ' + runById(id).title;
      name.setAttribute('aria-label', 'Select ' + pinName(id));
      name.append(lineSwatch('pin-line ' + pinClass(id)), el('span', shortPinName(id)));
      name.addEventListener('click', function () {
        focusPin(null);
        selectRun(id);
      });
      remove.setAttribute('aria-label', 'Unpin ' + pinName(id));
      remove.addEventListener('click', function () {
        focusPin(null);
        togglePin(id);
      });
      focusable(name, id);
      focusable(remove, id);
      row.append(name);
      if (godmode()) row.append(remove);
      legend.append(row);
    });
    markLegendOverflow();
  }

  function renderReadings() {
    const legend = $('selected-label'),
      artefact = artefactOf(runById(selected)),
      readings = readingsOf(artefact);
    legend.replaceChildren();
    if (readings.length < 2) {
      legend.textContent = runById(selected).label + ' · ' + runById(selected).effort;
      $('selected-legend').hidden = !pinMode && selected === baselineId();
      return;
    }
    $('selected-legend').hidden = false;
    legend.setAttribute('role', 'group');
    legend.setAttribute('aria-label', 'Readings of ' + artefact.title);
    readings.forEach(function (r) {
      const b = el('button', readingLabel(r), 'reading-button');
      b.type = 'button';
      b.dataset.reading = r.id;
      b.title = r.title + ' · ' + r.effort + ' · ' + r.date;
      b.setAttribute('aria-pressed', String(r.id === selected));
      b.addEventListener('click', function () {
        selectRun(r.id);
      });
      legend.append(b);
    });
  }

  function runGroups() {
    const groups = [];
    report.runs
      .filter(function (r) {
        return r.cohort === selectedCohort && !r.source_run_id;
      })
      .forEach(function (r) {
        const name = family(r.label);
        let group = groups.find(function (item) {
          return item.name === name;
        });
        if (!group) {
          group = { name: name, runs: [] };
          groups.push(group);
        }
        group.runs.push(r);
      });
    return groups;
  }

  function chipLabel(r, group) {
    const prefix = group.name + ' · ';
    const rest = r.label === group.name ? '' : r.label.indexOf(prefix) === 0 ? r.label.slice(prefix.length) : r.label;
    if (rest) return rest;
    const sameEffort = group.runs.filter(function (item) {
      return item.effort === r.effort;
    }).length;
    return group.runs.length > 1 && sameEffort === 1 ? r.effort : '';
  }

  function runButton(r, text) {
    const isBaseline = r.id === baselineId();
    const b = el('button', undefined, 'run-button' + (isBaseline ? ' is-baseline' : ''));
    b.type = 'button';
    b.setAttribute('aria-pressed', String(r.id === artefactOf(runById(selected)).id));
    // Release view: the earliest run is marked only by its date, not as an "original".
    const mark = isBaseline ? (pinMode ? r.date : 'original') : '';
    b.setAttribute('aria-label', r.label + ' · ' + r.effort + (mark ? ' · ' + mark : ''));
    b.dataset.run = r.id;
    b.title = r.title + ' · ' + r.effort + ' · ' + r.date;
    if (pinMode && pinned.indexOf(r.id) >= 0) b.classList.add('is-pinned', pinClass(r.id));
    if (text) b.append(el('span', text, 'run-name'));
    if (mark) b.append(el('span', mark, 'run-effort'));
    b.addEventListener('click', function () {
      selectRun(r.id);
    });
    return b;
  }

  function renderPicker() {
    $('run-picker').replaceChildren();
    runGroups().forEach(function (group) {
      // A family of one shows its name; a run whose label adds to its family (a harness, a sample) is shown,
      // like a larger family, as the family's name with that run beside it.
      if (group.runs.length === 1 && !chipLabel(group.runs[0], group)) {
        $('run-picker').append(runButton(group.runs[0], group.name));
        return;
      }
      const container = el('div', undefined, 'run-group');
      container.setAttribute('role', 'group');
      container.setAttribute('aria-label', group.name);
      container.append(el('span', group.name, 'run-group-name'));
      group.runs.forEach(function (r, index) {
        container.append(runButton(r, chipLabel(r, group) || String(index + 1)));
      });
      $('run-picker').append(container);
    });
  }

  // The side labels differ in width; the radar and all its labels move together towards the narrower side, by
  // as much as the wider label needs, so every label stays on its spoke and clear of the plot.
  let radarShift = 0,
    radarShiftKey = '';
  function radarDimensions() {
    const chart = $('radar'),
      style = getComputedStyle(chart);
    const width = chart.clientWidth,
      compact = innerWidth <= 520 && width < 360;
    const available = Math.max(1, width - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight));
    const height = Math.max(1, chart.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom));
    // Fit the desktop plot to both dimensions; phone layouts keep their numbered key.
    const viewHeight = compact ? 330 : innerWidth > 1100 ? Math.min(520, (height * 520) / available) : 520;
    return { width: width, compact: compact, available: available, viewHeight: viewHeight };
  }

  function sizeRadarText() {
    const svg = $('radar').querySelector('svg');
    if (!svg) return;
    const dimensions = radarDimensions();
    if (
      svg.dataset.compact !== String(dimensions.compact) ||
      Math.abs(Number(svg.dataset.chartWidth) - dimensions.width) > 1 ||
      Math.abs(svg.viewBox.baseVal.height - dimensions.viewHeight) > 1
    ) {
      renderRadar();
      return;
    }
    const scale = svg.getBoundingClientRect().width / svg.viewBox.baseVal.width;
    if (!scale) return;
    svg.style.setProperty('--radar-label-size', Math.max(16, 14 / scale) + 'px');
    svg.style.setProperty('--radar-tick-size', Math.max(12, 11 / scale) + 'px');
    svg.querySelectorAll('.axis-control').forEach(function (group) {
      const label = group.querySelector('text'),
        box = group.querySelector('rect');
      group.removeAttribute('transform');
      const bounds = label.getBBox(),
        padding = 5 / scale;
      box.setAttribute('x', bounds.x - padding);
      box.setAttribute('y', bounds.y - padding);
      box.setAttribute('width', bounds.width + padding * 2);
      box.setAttribute('height', bounds.height + padding * 2);
      box.setAttribute('rx', 7 / scale);
      const left = bounds.x - padding,
        right = left + bounds.width + padding * 2;
      const edge = svg.viewBox.baseVal.width - 4;
      const shift = left < 4 ? 4 - left : right > edge ? edge - right : 0;
      group.setAttribute('transform', 'translate(' + shift + ' 0)');
      group.dataset.left = String(left + shift);
      group.dataset.right = String(right + shift);
    });
    if (dimensions.compact) return;
    // Clearance between the plot and the labels on its left and right spokes, in drawing units.
    const groups = svg.querySelectorAll('.axis-control'),
      cx = Number(svg.dataset.cx),
      radius = Number(svg.dataset.radius);
    const leftIndex = Math.round((axes.length * 3) / 4),
      rightIndex = Math.round(axes.length / 4);
    if (axes.length % 4 || !groups[leftIndex] || !groups[rightIndex]) return;
    const leftGap = cx - radius - Number(groups[leftIndex].dataset.right);
    const rightGap = Number(groups[rightIndex].dataset.left) - (cx + radius);
    const move = Math.round((rightGap - leftGap) / 2);
    if (Math.abs(move) >= 2 && Math.abs(radarShift + move) <= 60) {
      radarShift += move;
      renderRadar();
    }
  }

  function renderRadar() {
    if (axes.length < 3) {
      $('radar').replaceChildren(
        el(
          'p',
          'This ' +
            axes.length +
            '-axis profile uses the table as its primary presentation. A radar outline needs at least three dimensions.',
          'radar-fallback'
        )
      );
      $('export-radar').disabled = true;
      $('export-radar').title = 'A radar export needs at least three dimensions.';
      return;
    }
    $('export-radar').disabled = false;
    $('export-radar').title = '';
    const { width, compact, available, viewHeight } = radarDimensions();
    // One centre for spokes, rings and labels: the middle of the drawing, balanced for the side labels.
    const shiftKey = width + 'x' + viewHeight + (compact ? 'c' : '');
    if (shiftKey !== radarShiftKey) {
      radarShift = 0;
      radarShiftKey = shiftKey;
    }
    const cx = compact ? 180 : 260 + radarShift,
      cy = viewHeight / 2;
    const radius = compact ? 116 : Math.max(24, Math.min(145, ((available - 180) * 260) / available, 200));
    const labelRadiusX = Math.min(250, (210 * 520) / viewHeight);
    const labelRadiusY = Math.min(215, cy - (30 * 520) / available);
    const point = function (i, position) {
      const a = -Math.PI / 2 + (i * 2 * Math.PI) / axes.length;
      return [cx + Math.cos(a) * radius * position, cy + Math.sin(a) * radius * position];
    };
    const xy = function (p) {
      return p
        .map(function (v) {
          return v.toFixed(2);
        })
        .join(',');
    };
    let svg =
      '<svg viewBox="' +
      (compact ? '0 0 360 330' : '0 0 520 ' + viewHeight) +
      '" data-compact="' +
      compact +
      '" data-chart-width="' +
      width +
      '" data-cx="' +
      cx +
      '" data-radius="' +
      radius +
      '" role="group" aria-labelledby="radar-title radar-desc"><title id="radar-title">' +
      axes.length +
      '-axis comparison with ' +
      esc(baselineLabel()) +
      '</title><desc id="radar-desc">Ordinal scores from ' +
      esc(scaleMin) +
      ' to ' +
      esc(scaleMax) +
      '. Select a dimension to read its evidence. All scores gives every value in a table. Missing values are omitted. This profile is exploratory; the ordinal table is the primary comparison. ' +
      esc(comparisonNote()) +
      '</desc>';
    svg +=
      '<text class="ring-number ring-center" x="' +
      cx +
      '" y="' +
      cy +
      '" text-anchor="middle" dominant-baseline="middle">' +
      esc(scaleMin) +
      '</text>';
    radarTicks().forEach(function (tick) {
      const position = scorePosition(tick);
      svg +=
        '<polygon class="grid-ring" points="' +
        axes
          .map(function (_, i) {
            return xy(point(i, position));
          })
          .join(' ') +
        '"/>';
      svg +=
        '<text class="ring-number" x="' +
        (cx + 9) +
        '" y="' +
        (cy - radius * position - 4) +
        '">' +
        esc(tick) +
        '</text>';
    });
    axes.forEach(function (_, i) {
      const p = point(i, 1);
      svg += '<line class="spoke" x1="' + cx + '" y1="' + cy + '" x2="' + p[0] + '" y2="' + p[1] + '"/>';
    });
    function draw(review, className, dots, id) {
      const values = axes.map(function (a) {
        return id ? shownScore(id, a.id, review) : review.scores[a.id].value;
      });
      if (
        values.every(function (v) {
          return v !== null;
        })
      ) {
        svg +=
          '<polygon class="' +
          className +
          '" points="' +
          values
            .map(function (v, i) {
              return xy(point(i, scorePosition(v)));
            })
            .join(' ') +
          '"/>';
      } else {
        values.forEach(function (v, i) {
          const j = (i + 1) % values.length;
          if (v !== null && values[j] !== null)
            svg +=
              '<path class="' +
              className +
              '" style="fill:none" d="M' +
              xy(point(i, scorePosition(v))) +
              ' L' +
              xy(point(j, scorePosition(values[j]))) +
              '"/>';
        });
      }
      if (dots)
        values.forEach(function (v, i) {
          if (v !== null) {
            const p = point(i, scorePosition(v));
            svg +=
              '<circle class="' +
              dots +
              '" data-point="' +
              axes[i].id +
              '" cx="' +
              p[0] +
              '" cy="' +
              p[1] +
              '" r="4.2"/>';
          }
        });
    }
    if (!pinMode) draw(baselineReview(), 'baseline-polygon', 'base-dot', baselineId());
    shownPins().forEach(function (id) {
      if (pinMode || id !== baselineId())
        draw(
          report.active_reviews[id],
          'pinned-polygon ' + pinClass(id),
          pinMode ? null : 'pin-dot ' + pinClass(id),
          id
        );
    });
    if (pinMode || selected !== baselineId()) draw(active(), 'selected-polygon', 'score-dot', selected);
    // Where the sessions split, the share that gave the level named stands beside the point.
    axes.forEach(function (a, i) {
      const record = details(selected, a.id),
        value = shownScore(selected, a.id);
      if (!record || value === null || record.agreeing === record.sessions) return;
      const at = point(i, scorePosition(value)),
        out = point(i, scorePosition(value) + 14 / radius);
      const dx = out[0] - at[0],
        dy = out[1] - at[1];
      // The ring numbers run up the top spoke; a label there sits to the left of its point instead.
      const top = Math.abs(dx) < 3 && dy < 0,
        anchor = top ? 'end' : Math.abs(dx) < 3 ? 'middle' : dx > 0 ? 'start' : 'end';
      const x = top ? at[0] - 10 : out[0] + (anchor === 'start' ? 2 : anchor === 'end' ? -2 : 0);
      const y = top ? at[1] + 4 : out[1] + (dy > 3 ? 8 : dy < -3 ? -2 : 4);
      svg +=
        '<text class="agreement-label" x="' +
        x.toFixed(2) +
        '" y="' +
        y.toFixed(2) +
        '" text-anchor="' +
        anchor +
        '">' +
        record.agreeing +
        '/' +
        record.sessions +
        '<title>' +
        esc(
          a.label +
            ': ' +
            record.agreeing +
            ' of ' +
            record.sessions +
            ' sessions gave level ' +
            active().scores[a.id].value +
            '; the point is their mean, ' +
            scoreLabel(value)
        ) +
        '</title></text>';
    });
    axes.forEach(function (a, i) {
      const angle = -Math.PI / 2 + (i * 2 * Math.PI) / axes.length;
      const p = compact
        ? point(i, 142 / radius)
        : [cx + Math.cos(angle) * labelRadiusX, cy + Math.sin(angle) * labelRadiusY];
      const selectedAxis = a.id === axisId;
      const score = scoreLabel(shownScore(selected, a.id));
      const accessibility = compact
        ? 'aria-hidden="true" aria-pressed="' + selectedAxis + '"'
        : 'role="button" tabindex="0" aria-pressed="' +
          selectedAxis +
          '" aria-label="' +
          esc(a.label + ', ' + score + ', inspect evidence') +
          '"';
      const lines = chartLabelLines(a.label);
      const text = compact
        ? String(i + 1)
        : '<tspan x="' +
          p[0] +
          '">' +
          esc(lines[0]) +
          '</tspan><tspan x="' +
          p[0] +
          '" dy="1.15em">' +
          esc(lines[1]) +
          '</tspan>';
      svg +=
        '<g class="axis-control" ' +
        accessibility +
        ' data-axis="' +
        a.id +
        '"><rect class="axis-hit"/><text text-anchor="middle" class="axis-label" x="' +
        p[0] +
        '" y="' +
        (p[1] + (compact ? 5 : -6)) +
        '">' +
        text +
        '</text></g>';
    });
    svg += '</svg>';
    $('radar').innerHTML = svg;
    $('radar').parentElement.style.setProperty('--radar-width', width + 'px');
    if (compact) {
      const key = el('div', undefined, 'radar-key');
      axes.forEach(function (a, i) {
        const button = el('button');
        button.type = 'button';
        button.dataset.axis = a.id;
        button.setAttribute('aria-pressed', String(a.id === axisId));
        button.append(el('span', String(i + 1)), document.createTextNode(a.label));
        key.append(button);
      });
      $('radar').append(key);
    }
    sizeRadarText();
    $('radar')
      .querySelectorAll('[data-axis]')
      .forEach(function (g) {
        g.addEventListener('click', function () {
          selectAxis(g.dataset.axis);
          if (g.tagName.toLowerCase() === 'button')
            $('radar')
              .querySelector('.radar-key [data-axis="' + g.dataset.axis + '"]')
              .focus({ preventScroll: true });
        });
        if (g.tagName.toLowerCase() !== 'button')
          g.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              const id = g.dataset.axis;
              selectAxis(id);
              $('radar')
                .querySelector('[data-axis="' + id + '"]')
                .focus();
            }
          });
      });
  }

  // The cited passage of the delivered seed, as the reader cited it: a few lines, then the way to the rest.
  const SEED_PREVIEW_CHARS = 360;
  function seedLines(span) {
    const prompt = report.prompts[promptCohort()];
    if (!prompt || !span) return [];
    return prompt.text
      .split('\n')
      .slice(span[0] - 1, span[1])
      .map(function (line) {
        return line
          .replace(/^#+\s*/, '')
          .replace(/`/g, '')
          .trim();
      })
      .filter(Boolean);
  }
  function renderSeedLines(span, seed) {
    const lines = seedLines(span),
      box = $('seed-lines');
    box.replaceChildren();
    // Show whole lines up to a short excerpt; the link carries the rest.
    let shown = 0,
      used = 0;
    while (shown < lines.length && (shown === 0 || used + lines[shown].length <= SEED_PREVIEW_CHARS)) {
      used += lines[shown].length;
      shown += 1;
    }
    lines.slice(0, shown).forEach(function (line) {
      box.append(el('p', line, 'seed-line'));
    });
    if (lines.length > shown)
      box.append(
        el(
          'p',
          '… ' + (lines.length - shown) + (lines.length - shown === 1 ? ' more line' : ' more lines'),
          'seed-more'
        )
      );
    box.append(seed);
  }

  function renderAxis() {
    const a = axes.find(function (v) {
      return v.id === axisId;
    });
    const review = active(),
      s = review.scores[axisId],
      b = baselineReview().scores[axisId];
    const readingHeading = a.question;
    document.querySelector('.reading-heading').style.setProperty('--question-chars', String(readingHeading.length));
    $('axis-position').textContent =
      String(axes.indexOf(a) + 1).padStart(2, '0') + ' / ' + String(axes.length).padStart(2, '0');
    $('axis-tabs').replaceChildren();
    axes.forEach(function (axis, i) {
      const button = el('button', String(i + 1), 'axis-tab');
      button.type = 'button';
      button.setAttribute('aria-label', axis.label);
      button.setAttribute('aria-pressed', String(axis.id === axisId));
      button.addEventListener('click', function () {
        selectAxis(axis.id);
        $('axis-tabs').children[i].focus();
      });
      $('axis-tabs').append(button);
    });
    $('axis-title').textContent = a.label;
    $('axis-question').replaceChildren();
    const compactQuestion = a.question.replace(/\b(sparse|delivered) seed\?$/i, (match) =>
      match.replace(' ', '\u00a0')
    );
    $('axis-question').textContent = compactQuestion;
    document.querySelector('.reading-heading').classList.toggle('has-long-question', a.question.length > 70);
    $('axis-values').replaceChildren();
    (pinMode
      ? [[shortPinName(selected), shownScore(selected, axisId), '']]
      : [
          [baselineLabel(), shownScore(baselineId(), axisId, baselineReview()), 'baseline'],
          [runById(selected).label, shownScore(selected, axisId), '']
        ]
    ).forEach(function (item) {
      const d = el('div');
      const score = el('span', scoreLabel(item[1]), 'axis-value ' + item[2]);
      if (item[1] === null) score.classList.add('is-unassessed');
      if (item[1] !== null) score.append(el('small', ' / ' + scaleMax));
      d.append(score, el('span', item[0], 'axis-value-label'));
      $('axis-values').append(d);
    });
    const strip = $('pinned-strip');
    strip.replaceChildren();
    strip.hidden = !shownPins().length;
    shownPins().forEach(function (id) {
      const score = report.active_reviews[id].scores[axisId],
        item = el('li'),
        button = el('button');
      const verdict = (score.rationale || '').split(/(?<=[.!?])\s/)[0];
      button.type = 'button';
      button.title = 'Make this the primary reading';
      const own = shownScore(selected, axisId),
        theirs = shownScore(id, axisId),
        delta = theirs === null || own === null ? '' : theirs - own;
      const difference = el('span', delta === '' ? '' : Math.abs(delta) < 1e-9 ? '=' : deltaLabel(delta), 'pin-delta');
      difference.title = delta === '' ? '' : 'Compared with ' + pinName(selected);
      button.append(
        lineSwatch('pin-line ' + pinClass(id)),
        el('span', shortPinName(id), 'pin-name'),
        el('span', scoreLabel(theirs), 'pin-level'),
        difference,
        el('span', verdict, 'pin-verdict')
      );
      button.title =
        pinName(id) + ' — ' + withoutAgreement(score.rationale || '') + ' Select to make it the primary reading.';
      button.addEventListener('click', function () {
        focusPin(null);
        selectRun(id);
      });
      focusable(button, id);
      item.append(button);
      strip.append(item);
    });
    $('rationale').textContent = conciseRationale(withoutAgreement(s.rationale), a.anchors[scaleIndex(s.value)]);
    $('rationale').title = 'Full reasoning remains available in Readings and revisions.';
    const other = s.value === null ? null : details(selected, axisId)?.other_levels;
    $('other-levels').hidden = !other;
    $('other-levels').replaceChildren();
    if (other) $('other-levels').append(el('b', 'What would move it'), document.createTextNode(' ' + other));
    $('score-anchor').replaceChildren();
    if (s.value === null) $('score-anchor').textContent = 'Unassessed · no level awarded.';
    else
      $('score-anchor').append(
        el('span', 'Level ' + s.value, 'anchor-level'),
        document.createTextNode(' · ' + a.anchors[scaleIndex(s.value)])
      );
    $('score-byline').textContent =
      readerLabel(review) + ' · ' + review.status + ' · ' + review.date + ' · ' + confidenceText(s);
    $('inspect-original').hidden = pinMode || selected === baselineId();
    $('inspect-original').textContent = 'Inspect ' + baselineLabel() + ' ↗';
    $('evidence').replaceChildren(el('p', 'In the world', 'passage-label'));
    const unreviewed = active().status === 'unreviewed';
    const seed = unreviewed
      ? link('Read the delivered prompt ↗', promptUrl())
      : link('Lines ' + s.seed_span[0] + '–' + s.seed_span[1] + ' of the seed ↗', promptUrl() + '#L' + s.seed_span[0]);
    seed.className = 'seed-link';
    seed.target = '_blank';
    seed.rel = 'noopener';
    renderSeedLines(unreviewed ? null : s.seed_span, seed);
    s.citations.forEach(function (c) {
      const block = el('div', undefined, 'citation');
      const source = link(c.path + ':' + c.start + '–' + c.end + ' ↗', citeUrl(selected, c));
      source.target = '_blank';
      source.rel = 'noopener';
      const key = selected + '/' + c.path + ':' + c.start + '-' + c.end;
      const extracted = report.evidence_text[key];
      if (extracted) block.append(el('div', extracted, 'evidence-text'));
      block.append(source);
      $('evidence').append(block);
    });
    $('evidence').append(
      el(
        'p',
        unreviewed ? 'No interpretive assessment has been recorded.' : confidenceText(s) + ' · artefact interpretation',
        'fine'
      )
    );
    if (!unreviewed && STANDING_AXES.indexOf(axisId) >= 0) renderStanding();
    $('anchors-list').replaceChildren();
    $('anchors-list').start = String(scaleMin);
    $('scale-title').textContent = 'The scale · what earns ' + scaleMin + '–' + scaleMax + '?';
    a.anchors.forEach(function (text, i) {
      const level = scaleValues[i];
      const item = el('li', text, s.value === level ? 'current' : '');
      if (s.value === level) item.append(el('span', 'Awarded', 'level-marker'));
      if (!pinMode && b.value === level && selected !== baselineId())
        item.append(el('span', baselineLabel(), 'level-marker original-marker'));
      if (pinMode)
        shownPins().forEach(function (id) {
          if (report.active_reviews[id].scores[axisId].value === level)
            item.append(el('span', pinName(id), 'level-marker pin-marker'));
        });
      $('anchors-list').append(item);
    });
  }

  function renderHistory() {
    const history = report.reviews.filter(function (r) {
      return r.run_id === selected;
    });
    const selectedLineage = new Set();
    let cursor = active();
    while (cursor) {
      selectedLineage.add(cursor.id);
      cursor = cursor.supersedes
        ? report.reviews.find(function (r) {
            return r.id === cursor.supersedes;
          })
        : null;
    }
    $('review-history').replaceChildren();
    history
      .slice()
      .reverse()
      .forEach(function (review) {
        const entry = el('article', undefined, 'history-entry');
        const label =
          review.status === 'unreviewed'
            ? 'Unreviewed admission'
            : review.id === active().id
              ? 'Current selected reading'
              : selectedLineage.has(review.id)
                ? 'Earlier reading in this lineage'
                : 'Independent reading';
        entry.append(
          el('h4', label),
          el('p', review.reviewer + ' · ' + review.status + ' · ' + review.date + ' · ' + review.id, 'fine'),
          el('p', review.summary)
        );
        if (review.provenance?.context_disclosure) entry.append(el('p', review.provenance.context_disclosure, 'fine'));
        if (review.revision_note) entry.append(el('p', review.revision_note));
        const predecessor = history.find(function (r) {
          return r.id === review.supersedes;
        });
        axes.forEach(function (a) {
          const score = review.scores[a.id],
            detail = el('details');
          let label = a.label + ' · ' + scoreLabel(score.value);
          if (predecessor && predecessor.scores[a.id].value !== score.value)
            label += ' (previously ' + scoreLabel(predecessor.scores[a.id].value) + ')';
          detail.append(el('summary', label), el('p', score.rationale));
          const refs = el('p', undefined, 'history-citations');
          score.citations.forEach(function (c) {
            refs.append(link(c.path + ':' + c.start + '–' + c.end + ' ↗ ', citeUrl(selected, c)));
          });
          detail.append(refs);
          entry.append(detail);
        });
        $('review-history').append(entry);
      });
  }

  function renderReaderNote() {
    $('reader-note').hidden = !report.reader;
    // The note is written in short paragraphs, separated by a blank line.
    if (report.reader)
      $('reader-text').replaceChildren.apply(
        $('reader-text'),
        report.reader.note.split(/\n\s*\n/).map(function (text) {
          return el('p', text.trim());
        })
      );
    const origin = report.earliest_run;
    $('earliest-note').hidden = !origin;
    if (!origin) return;
    $('earliest-heading').textContent = origin.heading;
    $('earliest-text').replaceChildren.apply(
      $('earliest-text'),
      origin.paragraphs.map(function (text) {
        return el('p', text);
      })
    );
  }

  function renderRubric() {
    const table = el('table', undefined, 'rubric-table');
    const body = el('tbody');
    table.append(
      el('caption', 'Each dimension has its own evidence anchors, from ' + scaleMin + ' to ' + scaleMax + '.')
    );
    axes.forEach(function (a) {
      const row = el('tr'),
        heading = el('th'),
        cell = el('td'),
        scale = el('ol');
      heading.scope = 'row';
      heading.append(el('h4', a.label), el('p', a.question));
      scale.start = scaleMin;
      a.anchors.forEach(function (text) {
        scale.append(el('li', text));
      });
      cell.append(scale);
      row.append(heading, cell);
      body.append(row);
    });
    table.append(body);
    $('rubric-guide').replaceChildren(table);
  }

  function renderTable() {
    const head = $('score-table').querySelector('thead tr');
    const column = function (text) {
      const th = el('th', text);
      th.scope = 'col';
      return th;
    };
    const baselineColumn = column(baselineLabel());
    baselineColumn.id = 'score-baseline';
    const reference = pinMode ? shownPins()[0] : baselineId();
    head.replaceChildren.apply(
      head,
      [column('Dimension')].concat(pinMode ? [] : [baselineColumn], [column(pinName(selected))])
    );
    shownPins().forEach(function (id) {
      head.append(column(pinName(id)));
    });
    if (reference) head.append(column(pinMode ? 'Difference from ' + pinName(reference) : 'Difference'));
    $('score-table').querySelector('caption').textContent =
      'Ordinal scores on the declared ' +
      scaleMin +
      '–' +
      scaleMax +
      ' scale · the primary comparison; select a dimension to inspect its evidence. A score in thirds is the mean of the three sessions’ levels.' +
      (comparisonNote() ? ' ' + comparisonNote() : '');
    const body = $('score-table').querySelector('tbody');
    body.replaceChildren();
    axes.forEach(function (a) {
      const row = el('tr'),
        label = el('td'),
        v = shownScore(selected, a.id);
      const b = reference ? shownScore(reference, a.id) : null;
      const button = el('button', a.label);
      button.type = 'button';
      button.addEventListener('click', function () {
        selectAxis(a.id);
        $('axis-title').scrollIntoView({ block: 'center' });
      });
      label.append(button);
      row.append(label);
      if (!pinMode) row.append(el('td', scoreLabel(b)));
      row.append(el('td', scoreLabel(v)));
      shownPins().forEach(function (id) {
        row.append(el('td', scoreLabel(shownScore(id, a.id))));
      });
      const diff = v === null || b === null ? null : Math.abs(v - b) < 1e-9 ? 0 : v - b;
      if (reference)
        row.append(
          el(
            'td',
            diff === null ? '—' : diff === 0 ? 'Same' : (diff > 0 ? '+' : '-') + scoreLabel(Math.abs(diff)),
            diff > 0 ? 'delta-up' : diff < 0 ? 'delta-down' : ''
          )
        );
      body.append(row);
    });
  }

  function render() {
    const run = runById(selected),
      review = active(),
      observation = report.observations[selected];
    renderCohort();
    renderCohortPicker();
    renderPicker();
    renderRadar();
    renderAxis();
    renderTable();
    $('run-condition').textContent =
      run.model +
      ' / ' +
      run.effort +
      ' / ' +
      run.date +
      (review.status === 'unreviewed' ? ' / unreviewed' : '') +
      (run.withdrawn ? ' / withdrawn' : '');
    $('run-title').textContent = run.title;
    $('run-title').title = run.title;
    renderReadings();
    renderPins();
    $('run-facts').replaceChildren();
    const estimated = run.elapsed_seconds === null && run.timing?.estimated_seconds != null;
    const elapsed = run.elapsed_seconds === null ? null : Math.round(run.elapsed_seconds);
    const duration = estimated
      ? '~' + Math.round(run.timing.estimated_seconds / 60) + 'm'
      : elapsed === null
        ? 'Not retained'
        : Math.floor(elapsed / 60) + 'm ' + (elapsed % 60) + 's';
    [
      [run.metrics.text_files + ' sources', 'Retained text'],
      [duration, estimated ? 'Estimated duration' : 'Run duration'],
      ['CLI ' + run.cli_version, 'Historical harness']
    ].forEach(function (v) {
      const d = el('span');
      d.append(el('b', v[0]), document.createTextNode(v[1]));
      $('run-facts').append(d);
    });
    $('run-timing-note').hidden = !run.timing;
    $('run-timing-note').textContent = run.timing
      ? 'Timing: ' +
        run.timing.note +
        ' Source: ' +
        run.timing.estimate_source +
        '. Recorded lifecycle span: ' +
        Math.floor(run.timing.ledger_span.seconds / 60) +
        'm ' +
        Math.round(run.timing.ledger_span.seconds % 60) +
        's.'
      : '';
    $('run-withdrawn-note').hidden = !run.withdrawn;
    $('run-withdrawn-note').textContent = run.withdrawn
      ? 'Withdrawn from the release on ' + run.withdrawn.date + '. ' + run.withdrawn.reason
      : '';
    const presentation = report.presentations && report.presentations[selected];
    document.querySelector('.radar-panel').dataset.presentationId = presentation ? presentation.presentation_id : '';
    document.querySelector('.radar-panel').dataset.presentationStatus = presentation ? presentation.status : '';

    $('observation').textContent = observation.summary;
    $('observation-scope').replaceChildren(
      document.createTextNode(observation.kind + ' · ' + observation.date + ' · '),
      link('Read the retained evidence ↗', observation.url)
    );
    const sourceRecord = report.evidence_sources[observation.evidence.source_sha256];
    $('evidence-source-note').textContent = sourceRecord
      ? sourceRecord.scope +
        ' · ' +
        (sourceRecord.retained_in_repo
          ? 'parent source retained and rehashed'
          : 'parent source not retained; selected projection verified against its recorded hash') +
        '. ' +
        sourceRecord.note
      : 'The parent evidence record is not registered.';
    $('source-summary').textContent =
      run.metrics.files +
      ' archived files · ' +
      run.metrics.text_files +
      ' text projections · ' +
      (run.metrics.bytes / 1024).toFixed(1) +
      ' KiB. File counts and size are descriptive, not quality scores.';
    $('source-list').replaceChildren();
    report.sources[selected].forEach(function (s) {
      const li = el('li');
      if (s.url) li.append(link(s.path + ' ↗', s.url));
      else li.append(document.createTextNode(s.path + ' — ' + s.omission));
      $('source-list').append(li);
    });
    renderHistory();
    document.dispatchEvent(new CustomEvent('poem:run', { detail: selected }));
    themeDocumentLinks();
    blankDocumentLinks();
  }

  document.querySelector('.source-record-heading').addEventListener(
    'wheel',
    function (event) {
      if (event.ctrlKey || event.shiftKey || !event.deltaY) return;
      const list = $('source-list');
      const delta = event.deltaY * (event.deltaMode === 1 ? 20 : event.deltaMode === 2 ? list.clientHeight : 1);
      const canScroll = delta > 0 ? list.scrollTop + list.clientHeight < list.scrollHeight - 1 : list.scrollTop > 0;
      if (canScroll) {
        event.preventDefault();
        list.scrollTop += delta;
      }
    },
    { passive: false }
  );

  $('inspect-original').addEventListener('click', function () {
    selectRun(baselineId());
  });
  $('show-score-table').addEventListener('click', function () {
    showView('scores');
    $('score-table').focus?.();
  });
  $('export-radar').addEventListener('click', function () {
    const sourceSvg = $('radar').querySelector('svg');
    if (!sourceSvg) return;
    const svg = sourceSvg.cloneNode(true);
    const { width, height } = svg.viewBox.baseVal,
      compact = svg.dataset.compact === 'true';
    const captions = [
      runById(selected).label + ' / ' + runById(selected).effort + ' · ' + active().status,
      'Dashed: ' + baselineLabel() + (selected === baselineId() ? '' : ' · Solid: selected')
    ];
    if (comparisonNote()) captions.push(comparisonNote());
    if (spreadCaption(selected)) captions.push(spreadCaption(selected));
    const captionStart = height + (compact ? 138 : 0);
    const exportHeight = captionStart + (captions.length + 1) * 18;
    svg.setAttribute('width', String(width * 2));
    svg.setAttribute('height', String(exportHeight * 2));
    svg.setAttribute('viewBox', '0 0 ' + width + ' ' + exportHeight);
    const live = $('radar').querySelectorAll('svg *');
    svg.querySelectorAll('*').forEach(function (node, i) {
      const computed = getComputedStyle(live[i]);
      [
        'fill',
        'stroke',
        'stroke-width',
        'stroke-dasharray',
        'stroke-linecap',
        'stroke-linejoin',
        'vector-effect',
        'font-family',
        'font-size',
        'font-weight',
        'opacity'
      ].forEach(function (property) {
        node.style.setProperty(property, computed.getPropertyValue(property));
      });
    });
    const palette = getComputedStyle(document.documentElement);
    const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    bg.setAttribute('width', String(width));
    bg.setAttribute('height', String(exportHeight));
    bg.setAttribute('fill', palette.getPropertyValue('--score-bg').trim());
    svg.prepend(bg);
    if (compact)
      axes.forEach(function (a, i) {
        const item = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        item.setAttribute('x', String(16 + (i % 2) * 174));
        item.setAttribute('y', String(height + 24 + Math.floor(i / 2) * 30));
        item.setAttribute('fill', palette.getPropertyValue('--text').trim());
        item.setAttribute('font-size', '12');
        item.setAttribute('font-family', 'sans-serif');
        item.textContent = i + 1 + ' · ' + a.label;
        svg.append(item);
      });
    svg.querySelector('desc').textContent =
      runById(selected).label +
      ' versus ' +
      baselineLabel() +
      '. Rubric ' +
      report.rubric.version +
      '. ' +
      active().reviewer +
      ', ' +
      active().status +
      '. Dashed: ' +
      baselineLabel() +
      '. Solid: selected. Missing values are unassessed, not zero. Scores are ordinal; polygon area is not a rating. ' +
      comparisonNote();
    captions.forEach(function (text, i) {
      const caption = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      caption.setAttribute('x', String(width / 2));
      caption.setAttribute('y', String(captionStart + (i + 1) * 18));
      caption.setAttribute('text-anchor', 'middle');
      caption.setAttribute('fill', palette.getPropertyValue('--muted').trim());
      caption.setAttribute('font-size', '11');
      caption.textContent = text;
      svg.append(caption);
    });
    const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)], { type: 'image/svg+xml' }));
    const a = link('', url);
    a.download = selected + '-radar.svg';
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 1000);
  });
  new ResizeObserver(function (entries) {
    document.documentElement.style.setProperty(
      '--footer-height',
      entries[0].target.getBoundingClientRect().height + 'px'
    );
  }).observe(document.querySelector('footer'));
  new ResizeObserver(function (entries) {
    document.documentElement.style.setProperty(
      '--header-height',
      entries[0].target.getBoundingClientRect().height + 'px'
    );
  }).observe(document.querySelector('.masthead'));
  new ResizeObserver(function (entries) {
    document.documentElement.style.setProperty(
      '--toolbar-height',
      entries[0].target.getBoundingClientRect().height + 'px'
    );
  }).observe(document.querySelector('.lobby-toolbar'));
  new ResizeObserver(function () {
    const pane = document.querySelector('.workspace-view:not([hidden])');
    document
      .querySelector('.comparison-widget')
      .style.setProperty('--pane-scrollbar', (pane.offsetWidth - pane.clientWidth) / 2 + 'px');
  }).observe(document.querySelector('.widget-body'));
  new ResizeObserver(sizeRadarText).observe($('radar'));
  [$('pinned-legend'), $('run-picker'), document.querySelector('.workspace-tabs')].forEach(function (row) {
    new ResizeObserver(function () {
      markOverflow(row);
    }).observe(row);
    row.addEventListener(
      'scroll',
      function () {
        markOverflow(row);
      },
      { passive: true }
    );
  });
  renderRubric();
  // The crossing's sound lives in sound.js, loaded only while the crossing is on; until it arrives it is silent.
  const silence = {
    toggle: function () {},
    sync: function () {},
    weather: function () {},
    island: function () {},
    link: function () {},
    arrive: function () {},
    assemble: function () {},
    solve: function () {}
  };
  let sound = window.PoemSound || silence;
  if (report.threshold && !window.PoemSound) {
    const script = document.createElement('script');
    script.src = 'sound.js';
    script.onload = function () {
      if (window.PoemSound) sound = window.PoemSound;
    };
    document.head.append(script);
  }
  document.querySelectorAll('[data-sound-toggle]').forEach(function (button) {
    button.hidden = !report.threshold;
    button.addEventListener('click', function () {
      sound.toggle();
    });
  });
  sound.sync();

  // ── Act 1 · The poem ──────────────────────────────────────────────────────────────
  // The programmer is typing. The poem is written once, from its first line to its last, as a
  // struggle: hesitations, a wrong key caught, a word begun and doubted, now and then a clause
  // written, held and taken back, then written again more slowly. Meanwhile the rain eases and
  // dawn comes up. Nothing in the poem is clickable; when it is done the caret waits, and a
  // return mark runs it.
  const threshold = report.threshold;
  const TYPED_KEY = 'after-the-poem-typed',
    INSIDE_KEY = 'after-the-poem-inside';
  function reducedMotion() {
    return Boolean(window.matchMedia) && matchMedia('(prefers-reduced-motion: reduce)').matches;
  }
  function keepInSession(key) {
    try {
      sessionStorage.setItem(key, '1');
    } catch (_) {
      /* Remembered only where storage works. */
    }
  }
  function inSession(key) {
    try {
      return sessionStorage.getItem(key) === '1';
    } catch (_) {
      return false;
    }
  }
  function poemUnits() {
    const lines = report.prompts[threshold.cohort].text.split('\n');
    const title = lines.findIndex(function (line) {
      return /^#+\s/.test(line);
    });
    return {
      title: lines[title].replace(/^#+\s*/, ''),
      units: lines
        .map(function (text, i) {
          return { n: i + 1, text: text.trim() };
        })
        .filter(function (unit) {
          return unit.n > title + 1 && unit.text;
        })
    };
  }
  let poemLines = [],
    poemLength = 0,
    typingTimer = 0,
    typingStopped = false,
    lastUserScroll = -1e9;
  function renderThreshold() {
    const poem = poemUnits();
    $('threshold-eyebrow').textContent = threshold.eyebrow;
    $('threshold-title').textContent = poem.title;
    $('threshold-provenance').textContent = threshold.provenance;
    $('run-poem-label').textContent = threshold.run;
    const box = $('threshold-poem');
    box.replaceChildren();
    poemLines = poem.units.map(function (unit) {
      // The words are always present for assistive technology; on screen they are written in.
      const line = el('p', undefined, 'poem-line'),
        shown = el('span', undefined, 'poem-text');
      shown.setAttribute('aria-hidden', 'true');
      shown.append(el('span', '', 'typed'), el('span', undefined, 'caret'), el('span', unit.text, 'rest'));
      line.append(el('span', unit.text, 'visually-hidden'), shown);
      box.append(line);
      return { line: line, text: unit.text };
    });
    poemLength = poemLines.reduce(function (sum, item) {
      return sum + item.text.length;
    }, 0);
    ['threshold-eyebrow', 'threshold-title'].forEach(function (id, n) {
      $(id).classList.add('surface');
      $(id).style.setProperty('--delay', 0.8 + n * 1.2 + 's');
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          $(id).classList.add('is-surfaced');
        });
      });
    });
  }

  // A small seeded random, so the struggle is human but never the same twice.
  let seed = (Date.now() % 2147483646) + 1;
  function chance() {
    seed = (seed * 16807) % 2147483647;
    return (seed - 1) / 2147483646;
  }
  function between(low, high) {
    return low + chance() * (high - low);
  }
  const NEAR = {
    a: 'sqw',
    b: 'vgn',
    c: 'xvd',
    d: 'sfe',
    e: 'wrd',
    f: 'dgr',
    g: 'fht',
    h: 'gjy',
    i: 'uok',
    j: 'hku',
    k: 'jli',
    l: 'ko',
    m: 'nj',
    n: 'bmh',
    o: 'ipl',
    p: 'ol',
    q: 'wa',
    r: 'etf',
    s: 'adw',
    t: 'ryg',
    u: 'yij',
    v: 'cbf',
    w: 'qes',
    x: 'zcs',
    y: 'tuh',
    z: 'xa'
  };
  // How hard the writing is, along the poem: hesitant at the opening, effortless through the
  // enchantment, then a battle from the moment the hands fall still until the resistance is named,
  // easing once the programmer understands, and in flow through the joys to the end.
  // [position in the poem, difficulty].
  const ARC = [
    [0, 0.5],
    [0.05, 0.35],
    [0.09, 0.18],
    [0.18, 0.22],
    [0.195, 1],
    [0.32, 1],
    [0.36, 0.7],
    [0.4, 0.4],
    [0.52, 0.3],
    [0.73, 0.14],
    [0.82, 0.05],
    [1, 0.02]
  ];
  function difficulty(position) {
    for (let i = 1; i < ARC.length; i++) {
      if (position <= ARC[i][0]) {
        const f = (position - ARC[i - 1][0]) / (ARC[i][0] - ARC[i - 1][0]);
        return ARC[i - 1][1] + (ARC[i][1] - ARC[i - 1][1]) * f * f * (3 - 2 * f);
      }
    }
    return ARC[ARC.length - 1][1];
  }
  // The plan for one line: steps of [kind, character, milliseconds], kind 't' (type), 'b' (back),
  // 'p' (pause). Difficulty runs from 0 (flow) to 1 (struggle).
  function struggle(text, hard) {
    const steps = [],
      tokens = text.split(/(\s+)/);
    let written = '',
      clauseStart = 0,
      sinceDoubt = 0;
    const span = function (flow, low, high) {
      return between(flow[0] + (low - flow[0]) * hard, flow[1] + (high - flow[1]) * hard);
    };
    const type = function (ch, ms) {
      steps.push(['t', ch, ms]);
      written += ch;
    };
    const back = function (n, ms) {
      for (let i = 0; i < n; i++) {
        steps.push(['b', '', ms * between(0.8, 1.25)]);
        written = written.slice(0, -1);
      }
    };
    const pause = function (ms) {
      steps.push(['p', '', ms]);
    };
    pause(span([350, 700], 900, 2200));
    tokens.forEach(function (token) {
      if (!token) return;
      if (/^\s+$/.test(token)) {
        type(token, span([34, 80], 60, 140));
        return;
      }
      if (chance() < 0.02 + 0.13 * hard) pause(between(450, 1500)); // a hesitation before a word
      if (token.length > 4 && chance() < 0.07 * hard) {
        // a word begun and doubted
        const begun = token.slice(0, Math.floor(between(1, 4)));
        begun.split('').forEach(function (ch) {
          type(ch, between(70, 150));
        });
        pause(between(450, 1000));
        back(begun.length, 110);
        pause(between(300, 800));
      }
      token.split('').forEach(function (ch) {
        const lower = ch.toLowerCase();
        if (NEAR[lower] && ch === lower && chance() < 0.006 + 0.035 * hard) {
          // a wrong key, noticed
          const near = NEAR[lower],
            wrong = near[Math.floor(chance() * near.length)];
          type(wrong, between(55, 120));
          const drift = chance() < 0.35;
          if (drift) type(lower, between(55, 120));
          pause(between(220, 520));
          back(drift ? 2 : 1, 95);
        }
        type(ch, span([30, 64], 48, 115));
        if (/[,;:—]/.test(ch)) pause(span([150, 380], 320, 850));
        if (/[.?!”"]/.test(ch)) pause(span([300, 650], 650, 1500));
      });
      sinceDoubt += token.length;
      // In the hardest stretch a clause is now and then written, held, taken back, and written again more slowly.
      if (hard > 0.5 && /[,;—.]$/.test(token) && sinceDoubt > 110 && chance() < 0.2 * hard) {
        const clause = written.slice(clauseStart);
        if (clause.trim().length > 12) {
          pause(between(900, 1900));
          back(clause.length, 38);
          pause(between(1300, 2600));
          clause.split('').forEach(function (ch) {
            type(ch, between(75, 165));
            if (/[,;:—]/.test(ch)) pause(between(400, 900));
          });
          sinceDoubt = 0;
        }
      }
      if (/[,;:—.?!]$/.test(token)) clauseStart = written.length + 1;
    });
    pause(span([350, 700], 500, 1200));
    return steps;
  }
  // The pace of the whole act.
  const PACE = 0.8;
  function typeLine(item, done) {
    const shown = item.line.querySelector('.poem-text'),
      typed = shown.querySelector('.typed'),
      rest = shown.querySelector('.rest');
    const steps = struggle(item.text, difficulty((written + item.text.length / 2) / poemLength));
    let buffer = '',
      k = 0;
    shown.classList.add('is-typing');
    item.line.classList.add('is-writing');
    follow(item.line);
    (function next() {
      if (typingStopped) return;
      if (k >= steps.length) {
        typed.textContent = item.text;
        rest.textContent = '';
        shown.classList.remove('is-typing');
        item.line.classList.remove('is-writing');
        item.line.classList.add('is-written');
        done();
        return;
      }
      const step = steps[k++];
      if (step[0] === 't') buffer += step[1];
      else if (step[0] === 'b') buffer = buffer.slice(0, -1);
      // Wrong letters stand in for the unseen remainder, so the page never jumps while it struggles.
      typed.textContent = buffer;
      rest.textContent = item.text.slice(buffer.length);
      written += step[0] === 't' ? 1 : step[0] === 'b' ? -1 : 0;
      if (k % 12 === 0) {
        weather();
        follow(item.line, true);
      }
      typingTimer = setTimeout(next, step[2] * PACE);
    })();
  }
  let written = 0;
  function typePoem() {
    typingStopped = false;
    written = 0;
    let index = 0;
    (function nextLine() {
      if (typingStopped) return;
      if (index >= poemLines.length) {
        poemWritten(false);
        return;
      }
      const item = poemLines[index++];
      typeLine(item, function () {
        written = poemLines.slice(0, index).reduce(function (sum, it) {
          return sum + it.text.length;
        }, 0);
        weather();
        nextLine();
      });
    })();
  }
  // The view follows the line being written, unless the reader has just scrolled.
  function follow(line, onlyIfLow) {
    if (performance.now() - lastUserScroll < 4000) return;
    const box = line.getBoundingClientRect(),
      view = $('threshold').getBoundingClientRect();
    if (onlyIfLow && box.bottom < view.top + view.height * 0.72) return;
    $('threshold').scrollTo({
      top: $('threshold').scrollTop + box.top + box.height / 2 - (view.top + view.height * 0.46),
      behavior: reducedMotion() ? 'auto' : 'smooth'
    });
  }
  ['wheel', 'touchmove', 'keydown'].forEach(function (name) {
    $('threshold').addEventListener(
      name,
      function (event) {
        if (
          name !== 'keydown' ||
          ['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].indexOf(event.key) >= 0
        )
          lastUserScroll = performance.now();
      },
      { passive: true }
    );
  });
  // Lines far from the middle of the view rest dimmer.
  let centreFrame = 0;
  function followCentre() {
    centreFrame = 0;
    const view = $('threshold').getBoundingClientRect(),
      centre = view.top + view.height * 0.46,
      reach = view.height * 0.42;
    poemLines.forEach(function (item) {
      const box = item.line.getBoundingClientRect(),
        distance = Math.max(0, Math.abs(box.top + box.height / 2 - centre) - box.height / 2);
      item.line.style.setProperty('--near', Math.max(0, 1 - distance / reach).toFixed(3));
    });
  }
  function scheduleCentre() {
    if (!centreFrame) centreFrame = requestAnimationFrame(followCentre);
  }

  // The sky and the rain follow the writing: night and rain at first, the rain easing and ceasing
  // about three quarters of the way, the horizon warming; full light when the poem is done.
  const NIGHT = { top: [7, 7, 13], bottom: [16, 16, 28] },
    BEFORE_DAWN = { top: [26, 27, 52], bottom: [150, 98, 92] };
  let rainDensity = 1;
  function mix(a, b, f) {
    return (
      'rgb(' +
      a
        .map(function (v, i) {
          return Math.round(v + (b[i] - v) * f);
        })
        .join(',') +
      ')'
    );
  }
  function weather() {
    const progress = poemLength ? Math.min(1, written / poemLength) : 1,
      eased = progress * progress * (3 - 2 * progress);
    const sky = $('threshold');
    if (!sky.classList.contains('is-dawn')) {
      sky.style.setProperty('--sky-top', mix(NIGHT.top, BEFORE_DAWN.top, eased * 0.9));
      sky.style.setProperty('--sky-bottom', mix(NIGHT.bottom, BEFORE_DAWN.bottom, Math.pow(eased, 1.3)));
    }
    rainDensity = Math.max(0, 1 - progress / 0.75);
    sound.weather(rainDensity);
  }
  // Rain: fine streaks at a slight angle, thinning as the writing goes on.
  let rainFrame = 0;
  function rain() {
    const canvas = $('threshold-rain');
    if (reducedMotion() || $('threshold').hidden) {
      rainFrame = 0;
      return;
    }
    const ratio = window.devicePixelRatio || 1,
      width = canvas.clientWidth,
      height = canvas.clientHeight;
    if (canvas.width !== Math.round(width * ratio)) {
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
    }
    const ctx = canvas.getContext('2d');
    if (!rain.drops)
      rain.drops = Array.from({ length: 240 }, function () {
        return {
          x: Math.random(),
          y: Math.random(),
          speed: 0.6 + Math.random() * 0.8,
          length: 10 + Math.random() * 22,
          alpha: 0.05 + Math.random() * 0.16
        };
      });
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.lineWidth = 1;
    ctx.lineCap = 'round';
    const active = Math.round(rain.drops.length * rainDensity);
    rain.drops.forEach(function (drop, i) {
      drop.y += drop.speed * 0.012;
      drop.x += drop.speed * 0.0016;
      if (drop.y > 1.05) {
        drop.y = -0.05;
        drop.x = Math.random();
      }
      if (drop.x > 1.02) drop.x = -0.02;
      if (i >= active) return;
      ctx.strokeStyle = 'rgba(205, 214, 245, ' + drop.alpha + ')';
      ctx.beginPath();
      ctx.moveTo(drop.x * width, drop.y * height);
      ctx.lineTo(drop.x * width - drop.length * 0.13, drop.y * height - drop.length);
      ctx.stroke();
    });
    rainFrame = rainDensity > 0 ? requestAnimationFrame(rain) : (ctx.clearRect(0, 0, width, height), 0);
  }
  // The poem is written: dawn comes up fully, the caret waits, the return mark appears.
  function poemWritten(instant) {
    keepInSession(TYPED_KEY);
    poemLines.forEach(function (item) {
      const shown = item.line.querySelector('.poem-text');
      shown.querySelector('.typed').textContent = item.text;
      shown.querySelector('.rest').textContent = '';
      shown.classList.remove('is-typing');
      item.line.classList.add('is-written');
    });
    written = poemLength;
    weather();
    const last = poemLines[poemLines.length - 1].line.querySelector('.poem-text');
    last.classList.add('is-typing');
    $('threshold').classList.toggle('is-instant', instant);
    // At dawn the sky is the dawn's own; the colours set while writing are let go.
    setTimeout(
      function () {
        $('threshold').style.removeProperty('--sky-top');
        $('threshold').style.removeProperty('--sky-bottom');
        $('threshold').classList.add('is-dawn');
      },
      instant ? 0 : 2200
    );
    setTimeout(
      function () {
        $('threshold-end').hidden = false;
        requestAnimationFrame(function () {
          requestAnimationFrame(function () {
            $('threshold-end').classList.add('is-surfaced');
          });
        });
        $('run-poem').focus({ preventScroll: true });
        follow($('threshold-end'));
      },
      instant ? 0 : 6500
    );
  }
  function openThreshold() {
    if (!threshold) {
      window.open(cohort().prompt_url, '_blank', 'noopener');
      return;
    }
    if (!poemLines.length) renderThreshold();
    $('threshold').hidden = false;
    document.body.classList.add('at-threshold');
    document.querySelector('.shell').inert = true;
    document.querySelector('.skip').inert = true;
    $('threshold').scrollTop = 0;
    sound.island(true);
    $('threshold-title').setAttribute('tabindex', '-1');
    $('threshold-title').focus({ preventScroll: true });
    if (reducedMotion() || inSession(TYPED_KEY)) {
      poemWritten(true);
      scheduleCentre();
      return;
    }
    written = 0;
    weather();
    if (!rainFrame) rainFrame = requestAnimationFrame(rain);
    setTimeout(typePoem, 5200);
    scheduleCentre();
  }
  function closeThreshold() {
    typingStopped = true;
    clearTimeout(typingTimer);
    $('threshold').hidden = true;
    document.body.classList.remove('at-threshold');
  }

  // The veil between places: fade to dark, hold a name for a breath, then let the next place settle in.
  const VEIL_IN = 1100,
    VEIL_HOLD = 1200,
    VEIL_OUT = 1600,
    SETTLE = 2600;
  let linking = false;
  function passThroughVeil(title, meta, underCover, onArrive) {
    if (linking) return;
    $('link-title').textContent = title;
    $('link-meta').textContent = meta;
    if (reducedMotion()) {
      underCover();
      if (onArrive) onArrive();
      return;
    }
    const veil = $('link-veil');
    linking = true;
    veil.hidden = false;
    veil.className = 'link-veil';
    void veil.offsetWidth;
    veil.classList.add('is-open');
    setTimeout(function () {
      underCover();
      setTimeout(function () {
        document.body.classList.add('is-arriving');
        veil.classList.add('is-leaving');
        if (onArrive) onArrive();
        setTimeout(function () {
          veil.hidden = true;
          veil.className = 'link-veil';
          linking = false;
        }, VEIL_OUT);
        setTimeout(function () {
          document.body.classList.remove('is-arriving');
        }, SETTLE);
      }, VEIL_HOLD);
    }, VEIL_IN);
  }
  // Running the poem: into the first world, or, once its reading is assembled, onto the workbench.
  function runPoem() {
    sound.link();
    keepInSession(INSIDE_KEY);
    if (godmode()) {
      passThroughVeil(
        game.workbench_title,
        game.workbench_meta,
        function () {
          closeThreshold();
          enterWorkbench();
        },
        function () {
          sound.arrive();
        }
      );
      return;
    }
    const first = runById(game.first_world);
    passThroughVeil(
      first.title,
      first.label + ' · ' + first.date,
      function () {
        closeThreshold();
        openField();
      },
      function () {
        sound.arrive();
      }
    );
  }
  if (threshold) {
    $('run-poem').addEventListener('click', runPoem);
    $('threshold').addEventListener('scroll', scheduleCentre, { passive: true });
    window.addEventListener('resize', function () {
      if (!$('threshold').hidden) scheduleCentre();
    });
    $('seed-nav').addEventListener('click', function (event) {
      event.preventDefault();
      openThreshold();
    });
  }

  // ── Act 2 · Dreamy Sol, in pieces ───────────────────────────────────────────────
  // The first world lies as its own artefacts: its memory (the Markdown it left) and its material
  // (its code and pages). Opening one unfolds it as a page of its own text, inert. Where the
  // world's reading cites a passage, a faint mark waits in the margin; when the passage crosses
  // the middle of the view it brightens and names the questions it carries. A question takes
  // shape on the radar when half of its passages have been seen; when all eight have, the
  // reading is assembled and opens onto the workbench.
  let fieldBuilt = false,
    fieldReturn = null,
    sheetWatch = null,
    sentenceTimer = 0,
    sheetFrame = 0;
  // Below this width the shape sits above the pieces and a page rises beneath it.
  const narrowField = window.matchMedia('(max-width: 900px)');
  function artefactKind(path) {
    return /\.md$/i.test(path) ? 'memory' : 'material';
  }
  // Where the pieces lie: [left %, top %, tilt in degrees], composed by hand, memory nearer the top.
  const SCATTER = [
    [4, 2, -3],
    [30, 0, 2],
    [56, 4, -1.5],
    [16, 30, 1.8],
    [44, 28, -2.2],
    [70, 34, 2.6],
    [2, 60, -1.2],
    [28, 62, 1],
    [54, 58, -2.6],
    [74, 70, 1.6],
    [40, 86, -0.8],
    [12, 88, 2]
  ];
  function openField() {
    document.querySelector('.shell').inert = true;
    document.querySelector('.skip').inert = true;
    document.body.classList.add('in-field');
    $('field').hidden = false;
    if (!fieldBuilt) buildField();
    renderFieldRadar(null);
    renderFieldJournal();
    sound.island(true);
    sound.assemble(
      axes.filter(function (a) {
        return takenShape(a.id);
      }).length / axes.length
    );
    $('field-title').setAttribute('tabindex', '-1');
    $('field-title').focus({ preventScroll: true });
  }
  function buildField() {
    fieldBuilt = true;
    const first = runById(game.first_world);
    $('field-title').textContent = first.title;
    const box = $('field-artefacts');
    box.replaceChildren();
    // Memory first, then material; scattered by a fixed pattern so each visit finds them in place.
    const files = game.files.slice().sort(function (x, y) {
      return (artefactKind(x.path) === 'memory' ? 0 : 1) - (artefactKind(y.path) === 'memory' ? 0 : 1);
    });
    files.forEach(function (file, index) {
      const item = el('div', undefined, 'artefact-item'),
        button = el('button', undefined, 'artefact is-' + artefactKind(file.path));
      item.setAttribute('role', 'listitem');
      button.type = 'button';
      button.dataset.path = file.path;
      button.style.setProperty('--i', String(index));
      const place = SCATTER[index % SCATTER.length];
      item.style.setProperty('--x', place[0] + '%');
      item.style.setProperty('--y', place[1] + '%');
      button.style.setProperty('--r', place[2] + 'deg');
      const glimpse = file.text
        .split('\n')
        .map(function (line) {
          return line.replace(/\s+$/, '');
        })
        .filter(Boolean)
        .slice(0, artefactKind(file.path) === 'memory' ? 4 : 7)
        .join('\n');
      button.append(
        el('span', artefactKind(file.path), 'artefact-kind'),
        el('span', file.path, 'artefact-name'),
        el('span', glimpse, 'artefact-glimpse')
      );
      button.setAttribute('aria-label', 'Open ' + file.path + ', ' + artefactKind(file.path));
      button.addEventListener('click', function () {
        openSheet(file.path, button);
      });
      item.append(button);
      box.append(item);
    });
    $('sheet-close').addEventListener('click', closeSheet);
    $('field').addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && !$('sheet').hidden) closeSheet();
    });
  }
  function renderFieldJournal() {
    const shaped = axes.filter(function (a) {
      return takenShape(a.id);
    }).length;
    $('field-journal').textContent = shaped ? '' : game.journal_world;
    $('field-journal').hidden = Boolean(shaped);
  }
  // Unfold one artefact as a page: its own text, line by line, with the passages its reading cites.
  function openSheet(path, opener) {
    const file = game.files.find(function (f) {
      return f.path === path;
    });
    fieldReturn = opener;
    $('sheet-kind').textContent = artefactKind(path);
    $('sheet-title').textContent = path;
    const body = $('sheet-body');
    body.replaceChildren();
    body.className = 'sheet-body is-' + artefactKind(path);
    const lines = file.text.split('\n');
    const here = Object.keys(passages)
      .map(function (key) {
        return passages[key];
      })
      .filter(function (passage) {
        return passage.path === path;
      })
      .sort(function (x, y) {
        return x.start - y.start;
      });
    let n = 1,
      group = null,
      groupEnd = 0;
    const line = function (number) {
      const row = el('div', undefined, 'sheet-line');
      row.append(el('span', String(number), 'sheet-n'), el('span', lines[number - 1] || ' ', 'sheet-text'));
      return row;
    };
    while (n <= lines.length) {
      const opening = here.find(function (passage) {
        return passage.start === n && !group;
      });
      if (opening) {
        // Overlapping citations join one passage on the page (as far as they keep overlapping);
        // each keeps its own questions.
        groupEnd = opening.end;
        let joined = [],
          grown = true;
        while (grown) {
          joined = here.filter(function (passage) {
            return passage.start >= n && passage.start <= groupEnd;
          });
          const end = Math.max.apply(
            null,
            joined.map(function (passage) {
              return passage.end;
            })
          );
          grown = end > groupEnd;
          groupEnd = end;
        }
        group = el('div', undefined, 'trace');
        group.dataset.passages = joined
          .map(function (passage) {
            return passage.key;
          })
          .join(' ');
        const names = [];
        joined.forEach(function (passage) {
          passage.axes.forEach(function (id) {
            const label = axes.find(function (a) {
              return a.id === id;
            }).label;
            if (names.indexOf(label) < 0) names.push(label);
          });
        });
        group.append(el('p', names.join(' · '), 'trace-names'));
        body.append(group);
      }
      (group || body).append(line(n));
      if (group && n >= groupEnd) {
        if (joined_seen(group)) group.classList.add('is-seen');
        group = null;
      }
      n++;
    }
    $('sheet').hidden = false;
    document.body.classList.add('sheet-open');
    // The page eases in two frames later; closing before then cancels it, so a quick Escape never leaves it open.
    sheetFrame = requestAnimationFrame(function () {
      sheetFrame = requestAnimationFrame(function () {
        sheetFrame = 0;
        $('sheet').classList.add('is-open');
      });
    });
    body.scrollTop = 0;
    body.focus({ preventScroll: true });
    if (narrowField.matches)
      $('field').scrollTo({
        top: Math.max(0, document.querySelector('.field-shape').offsetTop - 52),
        behavior: reducedMotion() ? 'auto' : 'smooth'
      });
    if (sheetWatch) sheetWatch.disconnect();
    // A passage is seen when it crosses the middle band of the page.
    sheetWatch = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) see(entry.target);
        });
      },
      { root: body, rootMargin: '-38% 0px -38% 0px' }
    );
    body.querySelectorAll('.trace').forEach(function (trace) {
      sheetWatch.observe(trace);
    });
  }
  function joined_seen(group) {
    return group.dataset.passages.split(' ').every(function (key) {
      return crossing.seen.indexOf(key) >= 0;
    });
  }
  function closeSheet() {
    cancelAnimationFrame(sheetFrame);
    sheetFrame = 0;
    if (sheetWatch) {
      sheetWatch.disconnect();
      sheetWatch = null;
    }
    $('sheet').classList.remove('is-open');
    document.body.classList.remove('sheet-open');
    setTimeout(
      function () {
        if (!$('sheet').classList.contains('is-open')) $('sheet').hidden = true;
      },
      reducedMotion() ? 0 : 900
    );
    if (fieldReturn) fieldReturn.focus({ preventScroll: true });
  }
  function see(group) {
    const before = axes
      .filter(function (a) {
        return takenShape(a.id);
      })
      .map(function (a) {
        return a.id;
      });
    let changed = false;
    group.dataset.passages.split(' ').forEach(function (key) {
      if (crossing.seen.indexOf(key) < 0) {
        crossing.seen.push(key);
        changed = true;
      }
    });
    group.classList.add('is-seen');
    if (!changed) return;
    const shaped = axes
      .filter(function (a) {
        return takenShape(a.id);
      })
      .map(function (a) {
        return a.id;
      });
    const fresh = shaped.filter(function (id) {
      return before.indexOf(id) < 0;
    });
    crossing.assembled = shaped.length === axes.length;
    saveCrossing();
    renderFieldRadar(fresh);
    renderFieldJournal();
    sound.assemble(shaped.length / axes.length);
    if (fresh.length) speak(fresh[fresh.length - 1]);
    if (fresh.length && narrowField.matches) {
      const review = report.active_reviews[game.first_world];
      fresh.forEach(function (id) {
        const axis = axes.find(function (a) {
            return a.id === id;
          }),
          note = el('p', undefined, 'trace-reading');
        note.append(
          el('b', axis.label),
          document.createTextNode(
            conciseRationale(review.scores[id].rationale, axis.anchors[scaleIndex(review.scores[id].value)])
          )
        );
        group.querySelector('.trace-names').after(note);
      });
    }
    if (crossing.assembled) whole();
  }
  // When a question takes shape, the reader's sentence for it surfaces for a moment.
  function speak(axisId) {
    const axis = axes.find(function (a) {
        return a.id === axisId;
      }),
      score = report.active_reviews[game.first_world].scores[axisId];
    const sentence = $('field-sentence');
    sentence.replaceChildren(
      el('span', axis.label, 'field-sentence-name'),
      el('span', conciseRationale(score.rationale, axis.anchors[scaleIndex(score.value)]), 'field-sentence-text')
    );
    sentence.classList.remove('is-shown');
    void sentence.offsetWidth;
    sentence.classList.add('is-shown');
    clearTimeout(sentenceTimer);
    sentenceTimer = setTimeout(function () {
      sentence.classList.remove('is-shown');
    }, 9000);
  }
  // The radar of the field: rings and spokes; each question's spoke brightens as its passages are
  // seen, and when it takes shape its name and point appear. Joined neighbours draw the outline.
  function renderFieldRadar(fresh) {
    const size = 360,
      c = size / 2,
      radius = 128,
      review = report.active_reviews[game.first_world];
    const at = function (i, position) {
      const angle = -Math.PI / 2 + (i * 2 * Math.PI) / axes.length;
      return [c + Math.cos(angle) * radius * position, c + Math.sin(angle) * radius * position];
    };
    const xy = function (point) {
      return point[0].toFixed(1) + ',' + point[1].toFixed(1);
    };
    let svg =
      '<svg viewBox="0 0 ' +
      size +
      ' ' +
      size +
      '" role="img" aria-labelledby="field-radar-title"><title id="field-radar-title">The reading of ' +
      esc(runById(game.first_world).title) +
      ', as it takes shape</title>';
    radarTicks().forEach(function (tick) {
      svg +=
        '<polygon class="field-ring" points="' +
        axes
          .map(function (_, i) {
            return xy(at(i, scorePosition(tick)));
          })
          .join(' ') +
        '"/>';
    });
    const shaped = axes.map(function (a) {
      return takenShape(a.id);
    });
    axes.forEach(function (a, i) {
      const progress = Math.min(1, seenFor(a.id) / needed(a.id));
      svg +=
        '<line class="field-spoke" style="--progress:' +
        progress.toFixed(2) +
        '" x1="' +
        c +
        '" y1="' +
        c +
        '" x2="' +
        at(i, 1)[0].toFixed(1) +
        '" y2="' +
        at(i, 1)[1].toFixed(1) +
        '"/>';
    });
    const points = axes.map(function (a, i) {
      return shaped[i] ? at(i, scorePosition(review.scores[a.id].value)) : null;
    });
    if (shaped.every(Boolean)) svg += '<polygon class="field-shape-whole" points="' + points.map(xy).join(' ') + '"/>';
    else
      points.forEach(function (point, i) {
        const next = points[(i + 1) % points.length];
        if (point && next)
          svg +=
            '<line class="field-edge" x1="' +
            point[0].toFixed(1) +
            '" y1="' +
            point[1].toFixed(1) +
            '" x2="' +
            next[0].toFixed(1) +
            '" y2="' +
            next[1].toFixed(1) +
            '"/>';
      });
    axes.forEach(function (a, i) {
      if (!shaped[i]) return;
      // Names stand clear of their spokes: beside them at the sides, above and below at the poles.
      const point = points[i],
        label = at(i, 1.1),
        isFresh = fresh && fresh.indexOf(a.id) >= 0;
      const side = Math.cos(-Math.PI / 2 + (i * 2 * Math.PI) / axes.length),
        anchor = side > 0.3 ? 'start' : side < -0.3 ? 'end' : 'middle';
      svg +=
        '<circle class="field-point' +
        (isFresh ? ' is-fresh' : '') +
        '" cx="' +
        point[0].toFixed(1) +
        '" cy="' +
        point[1].toFixed(1) +
        '" r="4.6"/>';
      const x = (label[0] + (anchor === 'start' ? 6 : anchor === 'end' ? -6 : 0)).toFixed(1),
        words = anchor === 'middle' ? [a.label] : chartLabelLines(a.label).filter(Boolean);
      svg +=
        '<text class="field-label' +
        (isFresh ? ' is-fresh' : '') +
        '" x="' +
        x +
        '" y="' +
        label[1].toFixed(1) +
        '" text-anchor="' +
        anchor +
        '">' +
        words
          .map(function (word, k) {
            return (
              '<tspan x="' +
              x +
              '" dy="' +
              (k ? 1.2 : 0.35 - (words.length - 1) * 0.6).toFixed(2) +
              'em">' +
              esc(word) +
              '</tspan>'
            );
          })
          .join('') +
        '</text>';
    });
    svg += '</svg>';
    $('field-radar').innerHTML = svg;
    $('field-radar').classList.toggle('is-empty', !shaped.some(Boolean));
    // The same shape as a table: each question that has taken shape, with its level.
    const rows = $('field-table').querySelector('tbody');
    rows.replaceChildren();
    axes.forEach(function (a, i) {
      if (!shaped[i]) return;
      const score = review.scores[a.id],
        row = el('tr'),
        name = el('th', a.label);
      name.scope = 'row';
      row.append(name, el('td', score.value + ' of ' + scaleMax + ' · ' + a.anchors[scaleIndex(score.value)]));
      rows.append(row);
    });
  }
  // The eighth question: the page closes, the shape closes and glows, and after a breath the
  // reading opens onto the workbench.
  function whole() {
    setTimeout(closeSheet, reducedMotion() ? 0 : 1600);
    setTimeout(
      function () {
        $('field-radar').classList.add('is-whole');
        sound.solve();
      },
      reducedMotion() ? 0 : 2200
    );
    setTimeout(
      function () {
        passThroughVeil(game.workbench_title, game.workbench_meta, enterWorkbench, function () {
          sound.arrive();
        });
      },
      reducedMotion() ? 0 : 7000
    );
  }

  // ── The workbench ─────────────────────────────────────────────────────────────────
  function enterWorkbench() {
    $('field').hidden = true;
    document.body.classList.remove('at-threshold', 'in-field');
    document.querySelector('.shell').inert = false;
    document.querySelector('.skip').inert = false;
    if (game) {
      selectedCohort = runById(game.first_world).cohort;
      selected = game.first_world;
    }
    document.querySelectorAll('.workspace-view').forEach(function (pane) {
      pane.scrollTop = 0;
    });
    showView('reading');
    render();
    updateLocation();
    if (game) sound.assemble(1);
    sound.island(false);
    // Arriving (or reloading) puts focus on the world's title: a quiet place to begin, with no ring drawn.
    $('run-title').setAttribute('tabindex', '-1');
    $('run-title').focus({ preventScroll: true });
  }

  // On the workbench, one quiet line under the shape, once.
  function renderJournal() {
    const cue = document.querySelector('#chart-caption .chart-caption-cue');
    if (!game || !cue || active().status === 'unreviewed') return;
    const text = crossing.assembled && !crossing.announced ? game.journal_workbench : '';
    cue.classList.toggle('is-journal', Boolean(text));
    if (text) cue.textContent = text;
  }

  // Every visit begins at the poem. A reader who has already run it in this session resumes where
  // they were: in the first world, or on the workbench once its reading is assembled. A link
  // cannot carry anyone past the poem, and the full research view has no crossing at all.
  renderReaderNote();
  render();
  if (threshold) {
    if (!inSession(INSIDE_KEY)) openThreshold();
    else if (godmode()) enterWorkbench();
    else openField();
  }
  blankDocumentLinks();
  new MutationObserver(function () {
    blankDocumentLinks();
  }).observe(document.querySelector('.comparison-widget'), { childList: true, subtree: true });
})();
