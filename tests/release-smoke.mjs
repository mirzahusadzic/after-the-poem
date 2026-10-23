// Browser checks for the release view: one reader, pinned comparisons and the editorial notes.
// Run after `python3 scripts/atlas.py build --target pages` (the public static site); nothing here executes a candidate.
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { readFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import assert from 'node:assert/strict';

const require = createRequire(import.meta.url);
const playwright = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
// PLAYWRIGHT_BROWSER selects the engine (chromium, webkit or firefox); Chromium may use an installed channel.
const engine = process.env.PLAYWRIGHT_BROWSER || 'chromium';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const channel = process.env.PLAYWRIGHT_CHANNEL || 'chrome';
const report = JSON.parse(await readFile(resolve(root, 'dist/report.json'), 'utf8'));
const presentation = JSON.parse(await readFile(resolve(root, 'data/presentation.json'), 'utf8'));

function serveReport() {
  return new Promise((resolveServer, reject) => {
    const server = spawn(
      'python3',
      ['-u', '-m', 'http.server', '0', '--bind', '127.0.0.1', '--directory', resolve(root, 'dist')],
      { stdio: ['ignore', 'pipe', 'ignore'] }
    );
    let output = '';
    const timeout = setTimeout(() => {
      server.kill();
      reject(new Error('Local report server did not start'));
    }, 10000);
    server.once('error', (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    server.stdout.on('data', (chunk) => {
      output += chunk;
      const match = output.match(/Serving HTTP on 127\.0\.0\.1 port (\d+)/);
      if (match) {
        clearTimeout(timeout);
        resolveServer({ server, url: 'http://127.0.0.1:' + match[1] + '/index.html' });
      }
    });
  });
}

// The report itself: one reader, its readings under artefact IDs, no hidden cohorts.
assert.ok(report.reader, 'Release view needs a configured reader');
assert.deepEqual(report.site, { target: 'pages' }, 'Release checks run against the static Pages build');
assert.equal(report.reader.model, presentation.reader.model);
const artefacts = report.runs.map((run) => run.id);
assert.deepEqual(
  [...artefacts].sort(),
  [...presentation.shown_runs].sort(),
  'The release page shows exactly the listed worlds'
);
assert.ok(
  artefacts.every((id) => !report.runs.find((run) => run.id === id).source_run_id),
  'Reading runs leak into the release view'
);
for (const id of artefacts) {
  const review = report.active_reviews[id];
  assert.equal(review.provenance.method, presentation.reader.method, 'Another reader is shown for ' + id);
}
for (const hidden of presentation.hidden_cohorts || [])
  assert.ok(!report.runs.some((run) => run.cohort === hidden), 'Hidden cohort shown: ' + hidden);
for (const id of artefacts) {
  const entry = report.artefacts[id];
  if (entry.kind !== 'none') assert.equal(entry.images.length, 1, 'Previewable artefact without a screenshot: ' + id);
}
const defaults = presentation.comparison.default_pins;
const other = artefacts.find((id) => !defaults.includes(id));

const browser = await playwright[engine].launch(
  engine === 'chromium' ? { headless: true, channel } : { headless: true }
);
const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, reducedMotion: 'reduce' });
const page = await context.newPage();
const errors = [],
  external = [];
page.on('pageerror', (error) => errors.push(String(error)));
let server;
try {
  const serving = await serveReport();
  server = serving.server;
  const base = serving.url;
  await context.route(/^https?:/, (route) => {
    if (new URL(route.request().url()).origin === new URL(base).origin) return route.continue();
    external.push(route.request().url());
    return route.abort();
  });
  const pins = () => page.evaluate(() => new URLSearchParams(location.hash.slice(1)).get('pin'));
  const state = () =>
    page.evaluate(() => ({
      polygons: document.querySelectorAll('#radar .pinned-polygon').length,
      chips: [...document.querySelectorAll('#pinned-legend .pin-name')].map((node) => node.textContent),
      strip: document.querySelectorAll('#pinned-strip li').length,
      markers: document.querySelectorAll('#radar .pin-marker').length,
      head: [...document.querySelectorAll('#score-table thead th')].map((node) => node.textContent)
    }));

  await page.goto(base);
  // Every tab panel lives inside the comparison card's workspace (a stray closing tag once pushed them out).
  assert.deepEqual(
    await page.evaluate(() =>
      [...document.querySelectorAll('[role=tabpanel]')]
        .filter((panel) => !panel.closest('.workspace'))
        .map((panel) => panel.id)
    ),
    [],
    'A tab panel sits outside the workspace'
  );
  // Act 1: a fresh visit begins at the poem (written whole under reduced motion). Nothing in it
  // is a link, and nothing leads past it.
  if (report.threshold) {
    await page.locator('#threshold').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#threshold-eyebrow').textContent(), report.threshold.eyebrow);
    assert.ok((await page.locator('#threshold-poem .poem-line').count()) > 10, 'The poem is not on the threshold');
    assert.equal(
      await page.locator('#threshold-poem a, #threshold-poem button').count(),
      0,
      'A line of the poem is a control'
    );
    assert.equal(
      await page.locator('.shell').evaluate((node) => node.inert),
      true,
      'The workbench is reachable behind the poem'
    );
    await page.keyboard.press('Escape');
    assert.ok(await page.locator('#threshold').isVisible(), 'Escape left the poem');
    assert.equal(await page.locator('#threshold button', { hasText: /skip/i }).count(), 0, 'A skip control is offered');
    // A link into the workbench carries no progress: it lands at the poem.
    const stranger = await context.newPage();
    await stranger.goto(base + '#run=' + other + '&axis=' + report.rubric.axes[0].id + '&pin=');
    await stranger.locator('#threshold').waitFor({ state: 'visible' });
    await stranger.close();
    await page.locator('#run-poem').waitFor({ state: 'visible' });
  }
  // Act 2: running the poem lays out the first world in pieces. Reading the passages its reading
  // cites gives each question its shape; when all have taken shape, the workbench opens.
  if (report.game) {
    const first = report.runs.find((run) => run.id === report.game.first_world);
    await page.locator('#run-poem').focus();
    await page.keyboard.press('Enter');
    await page.locator('#field').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#field-title').textContent(), first.title);
    assert.equal(await page.locator('#field-journal').textContent(), report.game.journal_world);
    assert.deepEqual(
      (await page.locator('.artefact').evaluateAll((list) => list.map((node) => node.dataset.path))).sort(),
      report.game.files.map((file) => file.path).sort(),
      "The field does not hold the first world's files"
    );
    assert.equal(
      await page.locator('.shell').evaluate((node) => node.inert),
      true,
      'The workbench is reachable from the field'
    );
    const shaped = () => page.locator('#field-table tbody tr').count();
    assert.equal(await shaped(), 0, 'A question has shape before anything was read');
    assert.doesNotMatch(await page.locator('#field').textContent(), /\b\d+\s*(\/|of)\s*\d+\b/, 'The field counts');
    // An edited record does not verify: it is discarded rather than trusted.
    const every = [
      ...new Set(
        Object.values(report.active_reviews[first.id].scores).flatMap((score) =>
          score.citations.map((c) => c.path + ':' + c.start + '-' + c.end)
        )
      )
    ];
    await page.evaluate(
      (seen) =>
        localStorage.setItem(
          'after-the-poem-crossing',
          JSON.stringify({ state: { seen, assembled: true }, seal: 'forged' })
        ),
      every
    );
    await page.reload();
    await page.locator('#field').waitFor({ state: 'visible' });
    assert.equal(await shaped(), 0, 'A forged record shaped the reading');
    assert.equal(
      await page.locator('.shell').evaluate((node) => node.inert),
      true,
      'A forged record opened the workbench'
    );
    // Keyboard: an artefact opens as an inert page of its own text; Escape folds it and returns.
    const opener = page.locator('.artefact').first();
    await opener.focus();
    await page.keyboard.press('Enter');
    await page.locator('#sheet').waitFor({ state: 'visible' });
    assert.equal(await page.evaluate(() => document.activeElement.id), 'sheet-body', 'The page did not take focus');
    assert.equal(
      await page.locator('#sheet-body script, #sheet-body style, #sheet-body iframe').count(),
      0,
      'A page renders its source'
    );
    await page.keyboard.press('Escape');
    await page.locator('#sheet').waitFor({ state: 'hidden' });
    assert.equal(
      await page.evaluate(() => document.activeElement.dataset.path),
      await opener.getAttribute('data-path'),
      'Focus did not return to the artefact'
    );
    assert.ok(await page.locator('#field').isVisible(), 'Escape left the field');
    // Escape in the same moment as opening: no later frame may open the page again.
    const reopened = await page.evaluate(async () => {
      document.querySelector('.artefact').click();
      document.getElementById('field').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      for (let frame = 0; frame < 4; frame++) await new Promise((done) => requestAnimationFrame(done));
      await new Promise((done) => setTimeout(done, 50));
      const sheet = document.getElementById('sheet');
      return sheet.classList.contains('is-open') || !sheet.hidden;
    });
    assert.equal(reopened, false, 'A page closed as it opened came back');
    // Read every page from top to bottom.
    for (const path of report.game.files.map((file) => file.path)) {
      if (await page.locator('#field').isHidden()) break;
      await page.locator('.artefact[data-path="' + path + '"]').click({ force: true });
      const body = page.locator('#sheet-body');
      const height = await body.evaluate((node) => node.scrollHeight);
      for (let y = 0; y <= height; y += 240) {
        await body.evaluate((node, y) => {
          node.scrollTop = y;
        }, y);
        await page.waitForTimeout(40);
        if (await page.locator('#field').isHidden()) break;
      }
      if (await page.locator('#sheet').isVisible()) await page.keyboard.press('Escape');
    }
    await page.locator('#field').waitFor({ state: 'hidden', timeout: 10000 });
    assert.equal(
      await page.locator('.shell').evaluate((node) => node.inert),
      false,
      'The assembled reading did not open the workbench'
    );
    assert.equal(await page.locator('.chart-caption-cue').textContent(), report.game.journal_workbench);
    assert.ok(await page.locator('#pin-toggle').isVisible(), 'The workbench opened without its instruments');
    assert.equal(
      await page.locator('[data-run="' + report.game.first_world + '"]').getAttribute('aria-pressed'),
      'true',
      'The workbench did not open on the first world'
    );
    // Within the session a reload returns to the workbench; a new visit begins at the poem and,
    // once run, goes straight to the workbench.
    await page.reload();
    await page.locator('#run-picker [data-run]').first().waitFor();
    assert.ok(
      (await page.locator('#threshold').isHidden()) && (await page.locator('#field').isHidden()),
      'A reload left the workbench'
    );
    const returning = await context.newPage();
    await returning.goto(base);
    await returning.locator('#threshold').waitFor({ state: 'visible' });
    await returning.locator('#run-poem').click();
    await returning.locator('#threshold').waitFor({ state: 'hidden' });
    assert.ok(await returning.locator('#field').isHidden(), 'An assembled reader was sent back into the field');
    await returning.close();
  }
  await page.locator('#run-picker [data-run]').first().waitFor();
  // One reader: no reading switcher and no "original" label; the earliest run shows its date.
  assert.equal(await page.locator('.reading-button').count(), 0);
  const picker = await page.locator('#run-picker').textContent();
  assert.ok(!/original/i.test(picker), 'The release picker still says original');
  const earliest = report.runs.find((run) => run.id === report.baseline);
  assert.ok(picker.includes(earliest.date), 'The earliest run is not marked by its date');

  // The default pin is data, not code: select another run and the default appears as an outline.
  await page.locator('[data-run="' + other + '"]').click();
  let now = await state();
  assert.equal(now.polygons, defaults.length);
  assert.equal(now.strip, defaults.length);
  assert.equal(now.chips.length, defaults.length);
  assert.equal((await page.locator('#radar .score-dot').count()) > 0, true, 'Selected reading lost its dots');
  assert.equal(await page.locator('#radar .pin-dot').count(), 0, 'Pins should be outlines without vertex dots');
  assert.ok(
    now.head.some((text) => text.startsWith('Difference from')),
    'Table lacks the pinned difference column'
  );

  // Legend rows sit above the radar card and pinning never moves the radar.
  const radarTop = async () => (await page.locator('#radar').boundingBox()).y;
  const before = await radarTop();
  const legend = await page.locator('.cockpit .legend').boundingBox();
  assert.ok(legend.y + legend.height <= before + 1, 'Legend overlaps the radar');

  // Keyboard pinning, a cap of three, and focus from the legend.
  const pinnable = artefacts.filter((id) => !defaults.includes(id));
  for (const id of pinnable.slice(0, 3)) {
    await page.locator('[data-run="' + id + '"]').click();
    await page.locator('#pin-toggle').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#pin-toggle').getAttribute('aria-pressed'), 'true');
  }
  assert.equal((await pins()).split(',').length, 3, 'More than three pins were kept');
  await page.locator('[data-run="' + pinnable[3] + '"]').click();
  now = await state();
  assert.equal(now.polygons, 3);
  assert.ok(Math.abs((await radarTop()) - before) <= 1, 'Pinning moved the radar');
  assert.ok(now.markers <= 3);
  await page.locator('#pinned-legend .pin-name').first().hover();
  assert.match(await page.locator('#radar').getAttribute('data-focus'), /^pin-\d$/);
  await page.mouse.move(5, 5);
  assert.equal(await page.locator('#radar').getAttribute('data-focus'), null);
  assert.equal(await page.locator('#run-picker .run-button.is-pinned').count(), 3);

  // Unpinning everything persists across a reload instead of restoring the default.
  while (await page.locator('#pinned-legend .pin-remove').count())
    await page.locator('#pinned-legend .pin-remove').first().click();
  assert.equal(await pins(), '');
  await page.reload();
  await page.locator('#run-picker [data-run]').first().waitFor();
  assert.equal((await state()).polygons, 0, 'An unpinned default came back after reload');

  // The brand mark returns the workbench to its opening, even after every pin was removed: the first world and the default pins.
  await page.locator('#brand-home').click();
  const opening = report.game ? report.game.first_world : report.cohorts[0].default_run;
  assert.equal(
    await page.locator('[data-run="' + opening + '"]').getAttribute('aria-pressed'),
    'true',
    'The brand mark left another world selected'
  );
  assert.deepEqual(
    await page
      .locator('#run-picker .run-button.is-pinned')
      .evaluateAll((nodes) => nodes.map((node) => node.dataset.run).sort()),
    [...defaults].sort(),
    "The brand mark kept the reader's pins"
  );

  // The static site runs nothing native: a server world shows its screenshot with a run-locally note; a console
  // world with a recording replays its recorded answers, says so, and answers anything else with how to run it.
  for (const id of artefacts) {
    const kind = report.artefacts[id].kind,
      recorded = report.recordings && report.recordings[id];
    await page.locator('[data-run="' + id + '"]').click();
    await page.locator('#tab-artefact').click();
    assert.equal(
      await page.locator('#open-preview').isVisible(),
      kind === 'browser' || Boolean(recorded),
      'Preview button for ' + id
    );
    if (kind === 'server' || (kind === 'console' && !recorded))
      assert.match(await page.locator('#preview-availability').textContent(), /local copy/);
    if (recorded) {
      assert.match(await page.locator('#preview-availability').textContent(), /runs nothing/);
      await page.locator('#open-preview').click();
      await page.locator('.xterm-screen').waitFor({ timeout: 10000 });
      const opening = recorded.entries.find((entry) => entry.command === report.artefacts[id].initial_command);
      const tail = opening.output.trim().split('\n').pop().trim().slice(0, 30);
      await page.waitForFunction(
        (tail) => document.querySelector('.xterm-accessibility')?.textContent.includes(tail),
        tail
      );
      await page.locator('#console-command').fill('rm -rf /');
      await page.locator('#console-form button').click();
      await page.waitForFunction(() =>
        document.querySelector('.xterm-accessibility')?.textContent.includes('runs nothing')
      );
      await page.keyboard.press('Escape');
      await page.locator('#preview-workspace').waitFor({ state: 'hidden' });
    }
  }

  // A phone has no Escape key, so every full-window preview carries a close button.
  const framed = artefacts.find((id) => report.artefacts[id].kind === 'browser');
  await page.locator('[data-run="' + framed + '"]').click();
  await page.locator('#tab-artefact').click();
  await page.locator('#open-preview').click();
  assert.match(await page.locator('#preview-status').textContent(), /Press Esc to exit/);
  await page.locator('#preview-close').click();
  await page.locator('#preview-workspace').waitFor({ state: 'hidden' });
  if (engine === 'chromium') {
    const phone = await browser.newContext({
      viewport: { width: 390, height: 844 },
      isMobile: true,
      hasTouch: true,
      reducedMotion: 'reduce'
    });
    const handset = await phone.newPage();
    await handset.goto(base + '#run=' + framed);
    await handset.locator('#tab-artefact').click();
    await handset.locator('#open-preview').click();
    assert.match(await handset.locator('#preview-status').textContent(), /Tap × to exit/);
    await handset.locator('#preview-close').tap();
    await handset.locator('#preview-workspace').waitFor({ state: 'hidden' });
    await phone.close();
  }
  await page.locator('#tab-reading').click();

  // Editorial notes.
  await page.locator('#tab-method').click();
  assert.ok(await page.locator('#reader-note').isVisible());
  assert.deepEqual(
    await page.locator('#reader-text p').allTextContents(),
    presentation.reader.note.split(/\n\s*\n/).map((text) => text.trim())
  );
  if (presentation.earliest_run) {
    assert.ok(await page.locator('#earliest-note').isVisible());
    assert.equal(await page.locator('#earliest-text p').count(), presentation.earliest_run.paragraphs.length);
  }

  // No horizontal page scroll at phone and desktop widths, with three pins.
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: width < 600 ? 844 : 1050 });
    await page.goto(base + '#run=' + other + '&pin=' + pinnable.slice(0, 3).join(','));
    await page.locator('#run-picker [data-run]').first().waitFor();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
    assert.ok(overflow <= 0, 'Horizontal overflow at ' + width + 'px: ' + overflow);
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(external, []);
  console.log(
    'Release checks passed under ' +
      engine +
      ': ' +
      (report.threshold ? 'the poem, the first world in pieces, the workbench' : 'the workbench (crossing paused)') +
      '; ' +
      artefacts.length +
      ' artefacts read by ' +
      report.reader.label +
      '; default pin, keyboard pinning, three-pin cap, focus, unpin persistence, notes and widths.'
  );
} finally {
  if (server) server.kill();
  await browser.close();
}
