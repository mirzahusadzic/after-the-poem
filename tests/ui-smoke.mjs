import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve } from 'node:path';
import { mkdir, readFile, access, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import assert from 'node:assert/strict';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const artifacts = resolve(root, '.local/browser');
await mkdir(artifacts, { recursive: true });
const channel = process.env.PLAYWRIGHT_CHANNEL || 'chrome';
const browser = await chromium.launch({ headless: true, channel });
const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, reducedMotion: 'reduce' });
const page = await context.newPage();
const errors = [],
  external = [];
page.on('pageerror', (error) => errors.push(String(error)));
const fileUrl = pathToFileURL(resolve(root, 'dist/index.html')).href;
const firstSentence = (text) =>
  String(text)
    .replace(/\s+/g, ' ')
    .trim()
    .match(/^.*?[.!?](?=\s|$)/)?.[0] || String(text).trim();
const substantiveRationale = (text, anchor) => {
  const normalized = String(text).replace(/\s+/g, ' ').trim();
  const sentences = normalized.match(/.*?[.!?](?=\s|$)|.+$/g) || [normalized];
  return (
    sentences
      .map((sentence) => sentence.trim())
      .filter((sentence) => !/^Level\s*[0-4]\b/i.test(sentence) && sentence !== anchor)[0] || sentences[0].trim()
  );
};
const displayRationale = (text, anchor) => {
  let summary = substantiveRationale(text, anchor);
  if (summary.length > 240) {
    summary = summary.slice(0, 237);
    const boundary = summary.lastIndexOf(' ');
    if (boundary > 160) summary = summary.slice(0, boundary);
    summary = summary.replace(/[,;:\-–—\s]+$/, '') + '…';
  }
  return summary;
};
async function assertScoreCardsFit(label) {
  const overflow = await page.locator('#axis-values').evaluate((group) => {
    const bounds = group.getBoundingClientRect(),
      failures = [];
    for (const card of group.children) {
      const box = card.getBoundingClientRect(),
        style = getComputedStyle(card);
      if (box.left < bounds.left - 1 || box.right > bounds.right + 1 || card.scrollWidth > card.clientWidth + 1)
        failures.push('card: ' + card.textContent);
      for (const field of card.children) {
        const range = document.createRange();
        range.selectNodeContents(field);
        for (const text of range.getClientRects()) {
          if (
            text.left < box.left + parseFloat(style.paddingLeft) - 1 ||
            text.right > box.right - parseFloat(style.paddingRight) + 1 ||
            text.top < box.top - 1 ||
            text.bottom > box.bottom + 1
          )
            failures.push(field.textContent);
        }
      }
    }
    return failures;
  });
  assert.deepEqual(overflow, [], 'Score content leaves its card: ' + label);
}
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
    server.once('exit', (code) => {
      clearTimeout(timeout);
      reject(new Error('Local report server exited: ' + code));
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
function colorParts(value) {
  const match = value.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  assert.ok(match, 'Unable to parse computed colour: ' + value);
  return match.slice(1, 4).map(Number);
}
function relativeLuminance(value) {
  const channels = colorParts(value).map((channel) => {
    const normalized = channel / 255;
    return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}
function contrastRatio(foreground, background) {
  const values = [relativeLuminance(foreground), relativeLuminance(background)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}
function hslLightness(value) {
  const channels = colorParts(value).map((channel) => channel / 255);
  const maximum = Math.max(...channels),
    minimum = Math.min(...channels);
  return (maximum + minimum) / 2;
}
let server;
try {
  const serving = await serveReport();
  server = serving.server;
  const url = serving.url;
  await context.route(/^https?:/, (route) => {
    if (new URL(route.request().url()).origin === new URL(url).origin) return route.continue();
    external.push(route.request().url());
    return route.abort();
  });
  await page.goto(url);
  await page.locator('[data-run="sol56-max"]').waitFor();
  await page.locator('#palette-study summary').click();
  for (const palette of ['dusk', 'daylight', 'play']) {
    await page.locator('.palette-option[data-palette="' + palette + '"]').click();
    const themeSurfaces = await page.evaluate(() => {
      const color = (selector) => getComputedStyle(document.querySelector(selector)).backgroundColor;
      const foreground = (selector) => getComputedStyle(document.querySelector(selector)).color;
      const token = (name) => {
        const probe = document.createElement('div');
        probe.style.backgroundColor = 'var(' + name + ')';
        document.body.append(probe);
        const value = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return value;
      };
      return {
        radar: color('.radar-panel'),
        header: color('.widget-heading'),
        footer: color('.cockpit-method'),
        headingToken: token('--heading-bg'),
        headingText: foreground('.widget-heading h1'),
        footerText: foreground('.cockpit-method h3'),
        page: color('body')
      };
    });
    assert.equal(themeSurfaces.header, themeSurfaces.footer, 'Header and footer surfaces differ under ' + palette);
    assert.equal(
      themeSurfaces.header,
      themeSurfaces.headingToken,
      'Header does not use its heading shade under ' + palette
    );
    assert.ok(
      relativeLuminance(themeSurfaces.header) < relativeLuminance(themeSurfaces.radar),
      'Header/footer must be darker than the radar under ' + palette
    );
    const surfaceRatio = hslLightness(themeSurfaces.header) / hslLightness(themeSurfaces.page);
    assert.ok(
      surfaceRatio >= 0.9 && surfaceRatio <= 0.92,
      'Header/footer must preserve the Paper & Iris relative surface shade under ' +
        palette +
        ': ' +
        surfaceRatio.toFixed(3)
    );
    assert.ok(
      contrastRatio(themeSurfaces.headingText, themeSurfaces.header) >= 4.5,
      'Header contrast is below 4.5 under ' + palette
    );
    assert.ok(
      contrastRatio(themeSurfaces.footerText, themeSurfaces.footer) >= 4.5,
      'Footer contrast is below 4.5 under ' + palette
    );
  }
  await page.locator('.palette-option[data-palette="dusk"]').click();
  await page.locator('#palette-study summary').click();
  await page.locator('.cohort-button').nth(1).click();
  await page.locator('#tab-sources').click();
  await page.locator('#brand-home').click();
  assert.equal(await page.locator('.cohort-button').first().getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator('[data-run="sol56-max"]').getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator('#tab-reading').getAttribute('aria-selected'), 'true');
  assert.equal(await page.locator('#radar [data-axis="recognition"]').getAttribute('aria-pressed'), 'true');
  assert.equal(await page.evaluate(() => location.hash), '');
  assert.equal(
    await page.evaluate(() => location.pathname.endsWith('/index.html') || location.pathname.endsWith('/')),
    true
  );
  assert.equal(
    await page
      .locator('.comparison-widget')
      .evaluate((element) => getComputedStyle(element).getPropertyValue('--card-breathing').trim()),
    '10vw'
  );
  assert.ok(
    await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight),
    'Desktop card growth introduces page scroll'
  );
  assert.deepEqual(
    await page.evaluate(() => Object.keys(window.POEM_REPORT.presentations).sort()),
    await page.evaluate(() => window.POEM_REPORT.runs.map((run) => run.id).sort())
  );
  assert.equal(
    await page.locator('.radar-panel').getAttribute('data-presentation-id'),
    'sol56-max-historical-presentation-v1'
  );
  assert.deepEqual(
    await page.evaluate(() =>
      Object.entries(window.POEM_REPORT.artefacts)
        .filter(
          ([, artefact]) => ['browser', 'server', 'console'].includes(artefact.kind) && artefact.images.length !== 1
        )
        .map(([run]) => run)
    ),
    []
  );
  const unsafeLinks = await page.locator('a[href]').evaluateAll((links) =>
    links
      .filter((a) => !a.getAttribute('href').startsWith('#'))
      .filter((a) => a.target !== '_blank' || !a.rel.split(/\s+/).includes('noopener'))
      .map((a) => ({ href: a.getAttribute('href'), target: a.target, rel: a.rel }))
  );
  assert.deepEqual(unsafeLinks, []);
  const mainCohortRuns = await page.evaluate(
    () =>
      window.POEM_REPORT.runs.filter((run) => run.cohort === window.POEM_REPORT.cohorts[0].id && !run.source_run_id)
        .length
  );
  assert.equal(await page.locator('.run-button').count(), mainCohortRuns);
  assert.equal(await page.locator('polygon.selected-polygon').count(), 1);
  const expected = await page.evaluate(() => {
    const report = window.POEM_REPORT;
    const selected = report.active_reviews['sol56-max'];
    const baseline = report.active_reviews[report.baseline];
    return {
      touch: selected.scores.touch.value,
      touchQuote: selected.scores.touch.citations[0].quote,
      touchEvidence: (() => {
        const citation = selected.scores.touch.citations[0];
        return report.evidence_text['sol56-max/' + citation.path + ':' + citation.start + '-' + citation.end] || null;
      })(),
      relationsRationale: report.active_reviews['sol-original'].scores.relations.rationale,
      differences: report.rubric.axes.map((axis) => selected.scores[axis.id].value - baseline.scores[axis.id].value)
    };
  });
  for (const id of ['sol-original', 'astra-garden', 'sol56-max', 'sol61-max', 'astra-max']) {
    await page.locator('[data-run="' + id + '"]').click();
    assert.equal(await page.locator('[data-run="' + id + '"]').getAttribute('aria-pressed'), 'true');
    assert.ok((await page.locator('#rationale').textContent()).length > 30);
  }
  await page.locator('[data-run="sol56-max"]').click();
  assert.equal(
    await page.locator('#chart-caption .chart-caption-cue').textContent(),
    'Follow a dimension into the reading.'
  );
  assert.equal(await page.locator('#chart-caption .chart-caption-context').count(), 0);
  assert.deepEqual(await page.locator('#radar .ring-number').allTextContents(), ['0', '1', '2', '3', '4']);
  await page.locator('#radar [data-axis="touch"]').focus();
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('#axis-title').textContent(), 'Consequential touch');
  assert.match(await page.locator('#axis-values').textContent(), new RegExp(expected.touch + ' / 4'));
  await page.locator('#tab-scores').click();
  assert.equal(await page.locator('#score-table tbody tr').count(), 8);
  const rowValues = await page.locator('#score-table tbody tr').evaluateAll((rows) =>
    rows.map((row) =>
      Array.from(row.cells)
        .slice(1, 4)
        .map((cell) => cell.textContent.trim())
    )
  );
  assert.deepEqual(
    rowValues.map((row) => row[2]),
    expected.differences.map((value) => (value === 0 ? 'Same' : value > 0 ? '+' + value : String(value)))
  );
  const evidenceHref = await page.locator('#evidence .citation a').first().getAttribute('href');
  await access(resolve(root, 'dist', decodeURI(evidenceHref.split('#')[0])));
  const sourcePage = await context.newPage();
  await sourcePage.goto(new URL(evidenceHref, url).href);
  assert.equal(await sourcePage.locator('script').count(), 0);
  assert.ok(await sourcePage.locator('#L19').count());
  const unsafeSourceLinks = await sourcePage.locator('a[href]').evaluateAll((links) =>
    links
      .filter((a) => !a.getAttribute('href').startsWith('#'))
      .filter((a) => a.target !== '_blank' || !a.rel.split(/\s+/).includes('noopener'))
      .map((a) => a.getAttribute('href'))
  );
  assert.deepEqual(unsafeSourceLinks, []);
  await sourcePage.close();
  await page.locator('#tab-artefact').click();
  await page.locator('[data-run="sol-original"]').click();
  assert.equal(await page.locator('#artefact-images img').getAttribute('src'), 'assets/artefacts/sol-original.png');
  assert.match(await page.locator('#artefact-images figcaption').textContent(), /Sandboxed preview inspection/);
  await page.locator('#open-preview').click();
  await page.locator('#preview-workspace').waitFor({ state: 'visible' });
  assert.ok(await page.locator('#preview-workspace.is-full-window').count());
  const windowFill = await page.locator('#preview-workspace').evaluate((element) => {
    const box = element.getBoundingClientRect();
    return { left: box.left, top: box.top, width: box.width, height: box.height, viewport: [innerWidth, innerHeight] };
  });
  assert.deepEqual([windowFill.left, windowFill.top], [0, 0]);
  assert.deepEqual([windowFill.width, windowFill.height], windowFill.viewport);
  assert.equal(await page.evaluate(() => document.fullscreenElement), null);
  assert.equal(await page.locator('#preview-status').textContent(), 'Press Esc to exit');
  assert.equal(await page.locator('.preview-toolbar').count(), 0);
  await page.waitForTimeout(3200);
  await page.locator('#preview-status').waitFor({ state: 'hidden' });
  await page.keyboard.press('Escape');
  await page.locator('#preview-workspace').waitFor({ state: 'hidden' });
  assert.equal(await page.locator('body').evaluate((body) => body.classList.contains('preview-full-window')), false);
  await page.locator('[data-run="sol56-max"]').click();
  await page.locator('#tab-reading').click();
  await page.locator('#show-score-table').click();
  assert.equal(await page.locator('#tab-scores').getAttribute('aria-selected'), 'true');
  await page.locator('#tab-reading').click();
  assert.equal(await page.locator('#review-studio').count(), 0);
  assert.equal(await page.locator('#anchors-list li').count(), 5);
  assert.match(
    await page.locator('#score-anchor').textContent(),
    new RegExp('Level ' + expected.touch + '|Unassessed')
  );
  if (expected.touchEvidence)
    assert.equal(await page.locator('#evidence .evidence-text').first().textContent(), expected.touchEvidence);
  assert.equal(await page.locator('#evidence .evidence-text').count(), Number(Boolean(expected.touchEvidence)));
  assert.equal(await page.locator('#evidence details').count(), 0);
  assert.equal(await page.locator('#evidence .markdown').count(), 0);
  assert.equal(await page.locator('#rubric-guide tbody tr').count(), 8);
  assert.equal(await page.locator('#view-method details').count(), 0);
  await page.locator('[data-run="sol-original"]').click();
  await page.locator('[data-axis="relations"]').click();
  assert.equal(
    await page.locator('#rationale').textContent(),
    displayRationale(expected.relationsRationale, 'Some distinct contributions or relational depth remain visible.')
  );
  assert.ok((await page.locator('#review-history .history-entry').count()) >= 1);
  await page.evaluate(() => {
    const report = window.POEM_REPORT;
    const other = structuredClone(report.active_reviews['sol-original']);
    other.id = 'independent-fixture';
    other.reviewer = 'Independent fixture reader';
    other.supersedes = null;
    report.reviews.push(other);
  });
  await page.locator('[data-run="sol56-max"]').click();
  await page.locator('[data-run="sol-original"]').click();
  assert.match(await page.locator('#review-history').textContent(), /Independent reading/);
  await page.locator('[data-run="sol56-max"]').click();
  await page.evaluate(() => {
    window.POEM_REPORT.active_reviews['sol56-max'].scores.consent.value = null;
  });
  await page.locator('[data-run="sol56-max"]').click();
  assert.equal(await page.locator('polygon.selected-polygon').count(), 0);
  await page.reload();
  assert.equal(await page.locator('#cohort-counts').count(), 0);
  assert.equal(await page.locator('.cohort-button').count(), 2);
  await page.getByRole('button', { name: 'October pilot · seed + handoff', exact: true }).click();
  await page.locator('#radar [data-axis="recognition"]').click();
  const pilotReview = await page.evaluate(() => window.POEM_REPORT.active_reviews['deepseek-flash-max-pilot']);
  const originalReview = await page.evaluate(() => window.POEM_REPORT.active_reviews[window.POEM_REPORT.baseline]);
  // The retained pilot leads its cohort; each admitted evaluation package is its own selectable run.
  const pilotCohortRuns = await page.evaluate(() => {
    const report = window.POEM_REPORT;
    const pilot = report.runs.find((run) => run.id === 'deepseek-flash-max-pilot');
    return report.runs
      .filter((run) => run.cohort === pilot.cohort)
      .map((run) => ({ id: run.id, title: run.title, evaluation: run.condition === 'evaluation' }));
  });
  // The toolbar lists artefacts; an artefact's readings share its legend line.
  assert.equal(await page.locator('.run-button').count(), pilotCohortRuns.filter((run) => !run.evaluation).length);
  assert.equal(await page.locator('.reading-button').count(), pilotCohortRuns.length);
  for (const run of pilotCohortRuns.filter((item) => item.evaluation)) {
    await page.locator('.reading-button[data-reading="' + run.id + '"]').click();
    assert.equal(await page.locator('#run-title').textContent(), run.title);
    assert.equal(
      await page.locator('.reading-button[data-reading="' + run.id + '"]').getAttribute('aria-pressed'),
      'true'
    );
    assert.equal(await page.locator('[data-run="deepseek-flash-max-pilot"]').getAttribute('aria-pressed'), 'true');
    const review = await page.evaluate((id) => window.POEM_REPORT.active_reviews[id], run.id);
    assert.match(
      await page.locator('#score-byline').textContent(),
      new RegExp('^' + review.reviewer.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    );
    const scored = Object.values(review.scores).filter((score) => score.value !== null).length;
    assert.equal(await page.locator('.score-dot').count(), scored, 'Score dots differ on ' + run.id);
    assert.equal(await page.locator('polygon.selected-polygon').count(), scored === 8 ? 1 : 0);
    assert.equal(await page.locator('.base-dot').count(), 8);
  }
  await page.locator('[data-run="deepseek-flash-max-pilot"]').click();
  await page.locator('#radar [data-axis="recognition"]').click();
  assert.equal(await page.locator('#run-title').textContent(), 'The Quiet Orb');
  assert.doesNotMatch(await page.locator('#run-condition').textContent(), /upstream unverified/);
  assert.doesNotMatch(await page.locator('#run-condition').textContent(), /unreviewed/);
  assert.equal(await page.locator('#reading-status').textContent(), 'A possible reading');
  assert.equal(
    await page.locator('#score-byline').textContent(),
    pilotReview.reviewer +
      ' · provisional · ' +
      pilotReview.date +
      ' · Confidence: ' +
      pilotReview.scores.recognition.confidence
  );
  assert.equal(
    await page.locator('#rationale').textContent(),
    displayRationale(
      pilotReview.scores.recognition.rationale,
      'Carries the central tension into several connected choices.'
    )
  );
  assert.equal(await page.locator('#baseline-label').textContent(), 'Dreamy Sol · original · xhigh');
  assert.match(await page.locator('#baseline-name').textContent(), /Dreamy Sol.*across cohorts/);
  assert.match(await page.locator('#chart-caption').textContent(), /Different prompts and conditions/);
  assert.equal(
    await page.locator('#chart-caption .chart-caption-context').textContent(),
    'Different prompts and conditions · interpretive comparison.'
  );
  assert.equal(
    await page.locator('#axis-values .baseline').textContent(),
    originalReview.scores.recognition.value + ' / 4'
  );
  assert.equal(await page.locator('#axis-values .axis-value-label').first().textContent(), 'Dreamy Sol · original');
  assert.equal(await page.locator('polygon.baseline-polygon').count(), 1);
  const pilotScoredCount = Object.values(pilotReview.scores).filter((score) => score.value !== null).length;
  assert.equal(await page.locator('polygon.selected-polygon').count(), pilotScoredCount === 8 ? 1 : 0);
  assert.equal(await page.locator('.base-dot').count(), 8);
  assert.equal(await page.locator('.score-dot').count(), pilotScoredCount);
  assert.equal(await page.locator('#selected-legend').isVisible(), true);
  await assertScoreCardsFit('pilot desktop');
  assert.equal(await page.locator('#evidence .citation').count(), pilotReview.scores.recognition.citations.length);
  const displayedPilotRationale = await page.locator('#rationale').textContent();
  assert.ok(displayedPilotRationale.length <= 241, 'Reading pane repeats the complete rationale essay');
  assert.equal(await page.locator('#evidence details, #evidence summary').count(), 0);
  assert.equal(
    await page.locator('#seed-side .seed-link').getAttribute('href'),
    'sources/prompts/dream-workspace-result-v1.html#L' + pilotReview.scores.recognition.seed_span[0]
  );
  await page.locator('#radar [data-axis="touch"]').click();
  const pilotTouchAnchor = await page.evaluate(() => {
    const report = window.POEM_REPORT,
      review = report.active_reviews['deepseek-flash-max-pilot'];
    const axis = report.rubric.axes.find((item) => item.id === 'touch');
    return axis.anchors[report.rubric.scale.indexOf(review.scores.touch.value)];
  });
  assert.equal(
    await page.locator('#rationale').textContent(),
    displayRationale(pilotReview.scores.touch.rationale, pilotTouchAnchor)
  );
  assert.doesNotMatch(await page.locator('#rationale').textContent(), /^Level\s*2/i);
  assert.ok((await page.locator('#review-history .history-entry').count()) >= 2);
  assert.match(await page.locator('#review-history').textContent(), /Current selected reading/);
  assert.match(await page.locator('#review-history').textContent(), /Unreviewed admission/);
  if (pilotReview.provenance?.context_disclosure) {
    assert.ok(
      (await page.locator('#review-history').textContent()).includes(pilotReview.provenance.context_disclosure)
    );
  }
  const pilotAxes = await page.evaluate(() => window.POEM_REPORT.rubric.axes.map((axis) => axis.id));
  for (const axis of pilotAxes) {
    await page.locator('#radar [data-axis="' + axis + '"]').click();
    const expectedCitations = await page.evaluate((id) => {
      const report = window.POEM_REPORT;
      return report.active_reviews['deepseek-flash-max-pilot'].scores[id].citations.map((citation) => {
        const key = 'deepseek-flash-max-pilot/' + citation.path + ':' + citation.start + '-' + citation.end;
        return { path: citation.path, quote: citation.quote, extracted: report.evidence_text[key] || null };
      });
    }, axis);
    const extracted = await page.locator('#evidence .evidence-text').allTextContents();
    assert.deepEqual(
      extracted,
      expectedCitations.map((citation) => citation.extracted).filter(Boolean),
      'DS extracted text differs on ' + axis
    );
    assert.equal(
      await page.locator('#evidence details, #evidence summary').count(),
      0,
      'DS evidence still has expanders on ' + axis
    );
    assert.equal(
      await page
        .locator(
          '#evidence .markdown, #evidence blockquote, #evidence pre, #evidence code, #evidence script, #evidence img'
        )
        .count(),
      0,
      'DS evidence rendered markup on ' + axis
    );
    const clipping = await page
      .locator('#evidence')
      .evaluate((evidence) =>
        [evidence, ...evidence.querySelectorAll('.citation, .evidence-text')]
          .filter(
            (node) =>
              getComputedStyle(node).overflowY !== 'visible' ||
              (getComputedStyle(node).maxHeight !== 'none' && node.scrollHeight > node.clientHeight)
          )
          .map((node) => node.className || node.id)
      );
    assert.deepEqual(clipping, [], 'DS evidence has a nested clipped scroller on ' + axis);
    const sourceLinks = await page.locator('#evidence .citation > a').evaluateAll((links) =>
      links.map((link) => ({
        text: link.textContent,
        href: link.getAttribute('href'),
        target: link.target,
        rel: link.rel
      }))
    );
    assert.equal(sourceLinks.length, expectedCitations.length, 'DS source links differ on ' + axis);
    for (const source of sourceLinks) {
      assert.ok(source.href && !source.href.startsWith('#'), 'DS source link is not addressable');
      assert.equal(source.target, '_blank');
      assert.ok(source.rel.split(/\s+/).includes('noopener'));
    }
    const unsafe = await page
      .locator('#evidence a[href]')
      .evaluateAll((links) =>
        links
          .filter(
            (link) =>
              !link.getAttribute('href').startsWith('#') &&
              (link.target !== '_blank' || !link.rel.split(/\s+/).includes('noopener'))
          )
          .map((link) => link.textContent)
      );
    assert.deepEqual(unsafe, [], 'DS evidence link did not open safely on ' + axis);
  }
  const cohorts = await page.evaluate(() =>
    window.POEM_REPORT.cohorts.map((cohort) => ({ id: cohort.id, runs: cohort.runs.slice() }))
  );
  for (const [cohortIndex, cohort] of cohorts.entries()) {
    await page.locator('.cohort-button').nth(cohortIndex).click();
    for (const runId of cohort.runs) {
      // Evaluation runs are readings of an artefact: choose the artefact, then the reading.
      const source = await page.evaluate(
        (id) => window.POEM_REPORT.runs.find((run) => run.id === id).source_run_id || null,
        runId
      );
      if (source) {
        await page.locator('[data-run="' + source + '"]').click();
        await page.locator('.reading-button[data-reading="' + runId + '"]').click();
      } else {
        await page.locator('[data-run="' + runId + '"]').click();
      }
      for (const axis of pilotAxes) {
        await page.locator('#radar svg [data-axis="' + axis + '"]').click();
        const expectedReading = await page.evaluate(
          ([id, axisId]) => {
            const report = window.POEM_REPORT;
            const citations = report.active_reviews[id].scores[axisId].citations;
            return {
              citations: citations.length,
              texts: citations
                .map(
                  (citation) =>
                    report.evidence_text[id + '/' + citation.path + ':' + citation.start + '-' + citation.end] || null
                )
                .filter(Boolean)
            };
          },
          [runId, axis]
        );
        const ui = await page.evaluate(() => ({
          citations: document.querySelectorAll('#evidence .citation').length,
          excerpts: [...document.querySelectorAll('#evidence .evidence-text')].map((node) => node.textContent),
          sourceLinks: document.querySelectorAll('#evidence .citation > a').length,
          expanders: document.querySelectorAll('#evidence details, #evidence summary').length
        }));
        assert.deepEqual(
          ui,
          {
            citations: expectedReading.citations,
            excerpts: expectedReading.texts,
            sourceLinks: expectedReading.citations,
            expanders: 0
          },
          'Shared Reading component differs for ' + runId + '/' + axis
        );
      }
    }
  }
  await page.locator('.cohort-button').nth(1).click();
  await page.locator('[data-run="deepseek-flash-max-pilot"]').click();
  await page.locator('#radar svg [data-axis="recognition"]').click();
  await page.locator('#view-reading').evaluate((pane) => {
    pane.scrollTop = pane.scrollHeight;
  });
  await page.locator('#radar [data-axis="recognition"]').click();
  assert.equal(
    await page.locator('#view-reading').evaluate((pane) => pane.scrollTop),
    0,
    'Axis change retained the Reading scroll position'
  );
  assert.equal(
    await page.locator('#evidence').evaluate((pane) => pane.scrollTop),
    0,
    'Axis change retained the evidence scroll position'
  );
  assert.equal(
    await page.locator('#evidence').evaluate((pane) => getComputedStyle(pane).overflowY),
    'visible',
    'Retained material uses a nested scrollbar'
  );
  assert.equal(await page.locator('#seed-nav').getAttribute('href'), 'sources/prompts/dream-workspace-result-v1.html');
  assert.equal(await page.locator('#exact-prompt').getAttribute('href'), 'prompts/dream-workspace-result-v1.txt');
  assert.equal(await page.locator('#source-list li').count(), 13);
  assert.match(await page.locator('#observation').textContent(), /Retrospective capture/);
  assert.match(await page.locator('#observation').textContent(), /completion handoff was missing/);
  assert.match(await page.locator('#observation').textContent(), /No original return snapshot/);
  await page.locator('#inspect-original').click();
  assert.equal(await page.locator('[data-run="sol-original"]').getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator('#run-title').textContent(), 'The Hand Remains');
  assert.equal(await page.locator('#seed-nav').getAttribute('href'), 'sources/prompts/dream-4521-v1.html');
  assert.doesNotMatch(await page.locator('#chart-caption').textContent(), /Different prompts/);
  await page.getByRole('button', { name: 'October pilot · seed + handoff', exact: true }).click();
  assert.equal(await page.locator('#exact-prompt').getAttribute('href'), 'prompts/dream-workspace-result-v1.txt');
  await page.locator('#tab-scores').click();
  assert.equal(await page.locator('#score-baseline').textContent(), 'Dreamy Sol · original');
  assert.match(await page.locator('#score-table caption').textContent(), /Different prompts and conditions/);
  const pilotRows = await page.locator('#score-table tbody tr').evaluateAll((rows) =>
    rows.map((row) =>
      Array.from(row.cells)
        .slice(1)
        .map((cell) => cell.textContent.trim())
    )
  );
  const axisIds = await page.evaluate(() => window.POEM_REPORT.rubric.axes.map((axis) => axis.id));
  assert.deepEqual(
    pilotRows,
    axisIds.map((id) => {
      const value = pilotReview.scores[id].value,
        baseline = originalReview.scores[id].value;
      if (value === null) return [String(baseline), 'Unassessed', '—'];
      const difference = value - baseline;
      return [
        String(baseline),
        String(value),
        difference === 0 ? 'Same' : difference > 0 ? '+' + difference : String(difference)
      ];
    })
  );
  const promptDownload = page.waitForEvent('download');
  await page.locator('#exact-prompt').click();
  const pilotPromptPath = resolve(artifacts, 'pilot-delivered-prompt.txt');
  await (await promptDownload).saveAs(pilotPromptPath);
  const pilotPrompt = await readFile(pilotPromptPath);
  const promptHash = await page.evaluate(
    () => window.POEM_REPORT.cohorts.find((c) => c.id === 'dream-workspace-result-v1').prompt_sha256
  );
  assert.equal(createHash('sha256').update(pilotPrompt).digest('hex'), promptHash);
  const pilotRadarDownload = page.waitForEvent('download');
  await page.locator('#export-radar').click();
  await (await pilotRadarDownload).saveAs(resolve(artifacts, 'pilot-radar.svg'));
  const pilotSvg = await readFile(resolve(artifacts, 'pilot-radar.svg'), 'utf8');
  assert.match(pilotSvg, /Dashed: Dreamy Sol · original/);
  assert.match(pilotSvg, /Different prompts and conditions/);
  assert.match(pilotSvg, new RegExp(pilotReview.reviewer.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ', provisional'));
  assert.match(pilotSvg, /class="baseline-polygon"/);
  if (pilotScoredCount === 8) assert.match(pilotSvg, /class="selected-polygon"/);
  else assert.match(pilotSvg, /<path class="selected-polygon"/);
  const widePage = await context.newPage();
  widePage.on('pageerror', (error) => errors.push(String(error)));
  await widePage.setViewportSize({ width: 2560, height: 1440 });
  await widePage.goto(url + '#run=deepseek-flash-max-pilot');
  await widePage.locator('#tab-artefact').click();
  await widePage.locator('#open-preview').click();
  const wideFrame = widePage.frameLocator('#artefact-frame');
  await wideFrame.locator('main.app-shell').waitFor();
  const wideWorld = await wideFrame.locator('main.app-shell').evaluate((element) => {
    const box = element.getBoundingClientRect();
    return { viewport: innerWidth, width: box.width, left: box.left, right: innerWidth - box.right };
  });
  assert.equal(wideWorld.viewport, 2560);
  assert.ok(
    Math.abs(wideWorld.width - 1520) <= 0.5,
    'Preview changed the retained desktop shell: ' + JSON.stringify(wideWorld)
  );
  const wideGrid = await wideFrame.locator('.app-grid').evaluate((element) => {
    const box = element.getBoundingClientRect();
    return { width: box.width, height: box.height };
  });
  assert.ok(Math.abs(wideGrid.width - 1518) <= 0.5, 'Preview grid did not preserve the retained desktop width');
  assert.ok(wideGrid.height > 900, 'Preview grid lost the retained page proportions');
  const fitted = await widePage.locator('#artefact-frame').boundingBox();
  assert.ok(
    Math.abs(fitted.width - 2560) <= 1 && Math.abs(fitted.height - 1440) <= 1,
    'Preview did not fill the browser window: ' + JSON.stringify(fitted)
  );
  await widePage.locator('#preview-workspace').screenshot({ path: resolve(artifacts, 'pilot-preview-1920.png') });
  await widePage.keyboard.press('Escape');
  await widePage.locator('#preview-workspace').waitFor({ state: 'hidden' });
  await widePage.close();
  await page.evaluate(() => {
    const report = window.POEM_REPORT,
      rid = 'deepseek-flash-max-pilot';
    const predecessor = report.reviews.find((review) => review.run_id === rid && review.status === 'unreviewed');
    report.active_reviews[rid] = predecessor;
    report.review_selections[rid] = predecessor.id;
  });
  await page.locator('[data-run="deepseek-flash-max-pilot"]').click();
  assert.equal(await page.locator('#reading-status').textContent(), 'Unreviewed');
  assert.match(await page.locator('#chart-caption').textContent(), /not zero/);
  assert.equal(await page.locator('polygon.baseline-polygon').count(), 1);
  assert.equal(await page.locator('.base-dot').count(), 8);
  assert.equal(await page.locator('.selected-polygon, .score-dot').count(), 0);
  assert.equal(await page.locator('#evidence .citation').count(), 0);
  assert.equal(
    await page.locator('#seed-side .seed-link').getAttribute('href'),
    'sources/prompts/dream-workspace-result-v1.html'
  );
  const unassessedRows = await page.locator('#score-table tbody tr').evaluateAll((rows) =>
    rows.map((row) =>
      Array.from(row.cells)
        .slice(1)
        .map((cell) => cell.textContent.trim())
    )
  );
  assert.deepEqual(
    unassessedRows,
    axisIds.map((id) => [String(originalReview.scores[id].value), 'Unassessed', '—'])
  );
  await page.locator('#tab-reading').click();
  await assertScoreCardsFit('unassessed pilot');
  const unassessedDownload = page.waitForEvent('download');
  await page.locator('#export-radar').click();
  await (await unassessedDownload).saveAs(resolve(artifacts, 'pilot-unassessed-fixture.svg'));
  const unassessedSvg = await readFile(resolve(artifacts, 'pilot-unassessed-fixture.svg'), 'utf8');
  assert.match(unassessedSvg, /unreviewed/);
  assert.match(unassessedSvg, /class="baseline-polygon"/);
  assert.doesNotMatch(unassessedSvg, /class="(?:selected-polygon|score-dot)"/);
  await page.reload();
  assert.equal(await page.locator('[data-run="deepseek-flash-max-pilot"]').getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator('#reading-status').textContent(), 'A possible reading');
  await page.locator('#tab-artefact').click();
  await page.screenshot({ path: resolve(artifacts, 'pilot-lobby.png'), fullPage: true });
  assert.equal(
    await page.locator('#artefact-images img').getAttribute('src'),
    'assets/artefacts/deepseek-flash-max-pilot.png'
  );
  assert.match(await page.locator('#artefact-images figcaption').textContent(), /Sandboxed preview inspection/);
  await page.locator('#open-preview').click();
  const pilotFrame = page.frameLocator('#artefact-frame');
  await pilotFrame.locator('#thoughtNodes .thought-node').first().waitFor();
  assert.ok(await page.locator('#preview-workspace.is-full-window').count());
  assert.equal(
    await page.locator('#artefact-frame').getAttribute('sandbox'),
    'allow-scripts allow-forms allow-downloads'
  );
  assert.equal(await pilotFrame.locator('#thoughtCount').textContent(), '4 gestures held');
  await pilotFrame.locator('#thoughtInput').fill('A temporary preview gesture, not assessment evidence.');
  await pilotFrame.locator('#addThought').click();
  assert.equal(await pilotFrame.locator('#thoughtCount').textContent(), '5 gestures held');
  const opaqueFrame = page.frames().find((frame) => frame.url().includes('/previews/deepseek-flash-max-pilot/'));
  assert.ok(opaqueFrame);
  const beforeNavigation = opaqueFrame.url();
  await opaqueFrame.evaluate(() => {
    const link = document.createElement('a');
    link.id = 'external-navigation-probe';
    link.href = 'https://example.com/network-probe';
    link.textContent = 'external';
    document.body.append(link);
  });
  await pilotFrame.locator('#external-navigation-probe').click();
  assert.equal(opaqueFrame.url(), beforeNavigation);
  await page.locator('#preview-workspace').screenshot({ path: resolve(artifacts, 'pilot-preview.png') });
  await pilotFrame.locator('#thoughtInput').press('Escape');
  await page.locator('#preview-workspace').waitFor({ state: 'hidden' });
  const offlinePage = await context.newPage();
  offlinePage.on('pageerror', (error) => errors.push(String(error)));
  await offlinePage.goto(fileUrl + '#run=deepseek-flash-max-pilot');
  assert.equal(await offlinePage.locator('#run-title').textContent(), 'The Quiet Orb');
  assert.equal(await offlinePage.locator('#baseline-label').textContent(), 'Dreamy Sol · original · xhigh');
  assert.equal(await offlinePage.locator('polygon.selected-polygon').count(), pilotScoredCount === 8 ? 1 : 0);
  assert.equal(await offlinePage.locator('#source-list li').count(), 13);
  await offlinePage.locator('#tab-artefact').click();
  await offlinePage.locator('#open-preview').click();
  assert.match(await offlinePage.locator('#preview-status').textContent(), /need a web server/);
  assert.equal(await offlinePage.locator('#artefact-frame').getAttribute('src'), null);
  await offlinePage.keyboard.press('Escape');
  await offlinePage.locator('#preview-workspace').waitFor({ state: 'hidden' });
  const promptPopup = offlinePage.waitForEvent('popup');
  await offlinePage.locator('#exact-prompt').click();
  const promptPage = await promptPopup;
  await promptPage.waitForLoadState();
  assert.equal(promptPage.url(), new URL('prompts/dream-workspace-result-v1.txt', fileUrl).href);
  assert.equal(await promptPage.locator('pre').textContent(), pilotPrompt.toString('utf8'));
  await promptPage.close();
  await offlinePage.close();
  await page.locator('#open-preview').click();
  await pilotFrame.locator('#thoughtNodes .thought-node').first().waitFor();
  assert.equal(await pilotFrame.locator('#thoughtCount').textContent(), '4 gestures held');
  await pilotFrame.locator('#thoughtInput').press('Escape');
  await page.locator('#preview-workspace').waitFor({ state: 'hidden' });
  for (const width of [1440, 1280, 1100, 1050, 840, 800, 768, 600, 521, 520, 390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await page.locator('#tab-reading').click();
    assert.ok(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      'Pilot lobby overflows at ' + width
    );
    await assertScoreCardsFit('pilot at ' + width);
    if (width === 1440 || width === 390 || width === 320)
      await page
        .locator('.reading-heading')
        .screenshot({ path: resolve(artifacts, 'pilot-score-cards-' + width + '.png') });
  }
  for (const width of [1440, 1152, 1100, 390, 320]) {
    await page.setViewportSize({ width, height: 1050 });
    const gap = await page.evaluate(
      () =>
        document.querySelector('#radar').getBoundingClientRect().top -
        document.querySelector('.cockpit .legend').getBoundingClientRect().bottom
    );
    assert.ok(gap >= 0, 'Legend runs under the radar at ' + width + ' by ' + -gap + 'px');
  }
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.locator('.cohort-button').first().click();
  assert.equal(await page.locator('#baseline-label').textContent(), 'Dreamy Sol · original · xhigh');
  assert.equal(await page.locator('#exact-prompt').getAttribute('href'), 'prompts/dream-4521-v1.txt');
  for (const width of [1440, 1280, 1100, 1050, 800, 390, 320]) {
    await page.setViewportSize({ width, height: 1050 });
    await page.locator('.cohort-button').first().click();
    await page.waitForTimeout(50);
    const radarBox = () => page.locator('#radar svg').boundingBox();
    const chartFrame = () =>
      page.evaluate(() =>
        Object.fromEntries(
          ['.legend', '#chart-caption'].map((selector) => {
            const box = document.querySelector(selector).getBoundingClientRect();
            return [selector, { width: box.width, height: box.height }];
          })
        )
      );
    const originalRadar = await radarBox();
    const originalPanel = await page.locator('.radar-panel').boundingBox();
    const originalFrame = await chartFrame();
    await page.locator('.cohort-button').nth(1).click();
    await page.waitForTimeout(50);
    const pilotRadar = await radarBox();
    const pilotPanel = await page.locator('.radar-panel').boundingBox();
    const pilotFrame = await chartFrame();
    assert.ok(Math.abs(originalRadar.width - pilotRadar.width) <= 0.5, 'Radar width changed in ' + width);
    assert.ok(Math.abs(originalRadar.height - pilotRadar.height) <= 0.5, 'Radar height changed in ' + width);
    assert.ok(Math.abs(originalPanel.width - pilotPanel.width) <= 0.5, 'Radar panel width changed in ' + width);
    assert.ok(Math.abs(originalPanel.height - pilotPanel.height) <= 0.5, 'Radar panel height changed in ' + width);
    assert.ok(
      Math.abs(pilotFrame['.legend'].width - originalFrame['.legend'].width) <= 0.5 &&
        Math.abs(pilotFrame['.legend'].height - originalFrame['.legend'].height) <= 0.5 &&
        Math.abs(pilotFrame['#chart-caption'].width - originalFrame['#chart-caption'].width) <= 0.5 &&
        Math.abs(pilotFrame['#chart-caption'].height - originalFrame['#chart-caption'].height) <= 1,
      'Radar frame changed in ' + width + ': ' + JSON.stringify({ pilotFrame, originalFrame })
    );
  }
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.locator('.cohort-button').first().click();
  await page.evaluate(() => {
    const report = window.POEM_REPORT;
    const source = report.runs[0],
      review = structuredClone(report.active_reviews[source.id]);
    const run = {
      ...source,
      id: 'future-run-fixture',
      cohort: 'future-cohort-fixture',
      label: 'Synthetic · deepseek/deepseek-v4.1-flash-1017',
      title: 'Future fixture',
      model_identity_basis: 'requested; upstream identity unverified'
    };
    report.runs.push(run);
    report.cohorts.push({
      ...report.cohorts[0],
      id: 'future-cohort-fixture',
      title: 'Future fixture cohort',
      baseline: run.id,
      default_run: run.id,
      baseline_label: run.label,
      runs: [run.id]
    });
    report.prompts['future-cohort-fixture'] = {
      ...report.prompts[report.cohorts[0].id],
      bytes: report.prompts[report.cohorts[0].id].bytes
    };
    report.active_reviews[run.id] = review;
    report.review_selections[run.id] = review.id;
    report.observations[run.id] = structuredClone(report.observations[source.id]);
    report.sources[run.id] = [];
    report.artefacts[run.id] = {
      kind: 'none',
      description: 'Fixture',
      documents: [],
      preview: null,
      preview_note: 'No preview',
      images: []
    };
    report.rubric.version = 'next-fixture';
  });
  await page.locator('[data-run="sol-original"]').click();
  assert.equal(await page.locator('.run-button').count(), mainCohortRuns);
  assert.equal(await page.locator('.cohort-button').count(), 3);
  assert.equal(await page.locator('#footer-rubric').textContent(), 'Rubric next-fixture');
  await page.locator('.cohort-button').last().click();
  assert.equal(await page.locator('.run-button').count(), 1);
  assert.equal(await page.locator('[data-run="future-run-fixture"]').getAttribute('aria-pressed'), 'true');
  assert.doesNotMatch(await page.locator('#run-condition').textContent(), /upstream unverified/);
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    assert.ok(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      'Long model label overflows at ' + width
    );
    await assertScoreCardsFit('long model label at ' + width);
  }
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.locator('.cohort-button').nth(0).click();
  await page.evaluate(() => {
    window.POEM_REPORT.runs = window.POEM_REPORT.runs.slice(0, 1);
    window.POEM_REPORT.cohorts = window.POEM_REPORT.cohorts.slice(0, 1);
    window.POEM_REPORT.cohorts[0].runs = ['sol-original'];
  });
  await page.locator('[data-run="sol-original"]').click();
  assert.equal(await page.locator('.run-button').count(), 1);
  assert.equal(await page.locator('#cohort-scope').textContent(), '1 run uses this prompt.');
  await page.reload();
  const radarDownload = page.waitForEvent('download');
  await page.locator('#export-radar').click();
  await (await radarDownload).saveAs(resolve(artifacts, 'radar.svg'));
  const svg = await readFile(resolve(artifacts, 'radar.svg'), 'utf8');
  assert.ok(svg.includes('xmlns="http://www.w3.org/2000/svg"'));
  assert.ok(svg.includes('Dashed: Dreamy Sol · original'));
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({ path: resolve(artifacts, 'desktop.png'), fullPage: true });
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await page.locator('[data-run="sol-original"]').click();
    const sizes = await page.evaluate(() => ({ view: innerWidth, width: document.documentElement.scrollWidth }));
    assert.ok(sizes.width <= sizes.view, 'Horizontal page overflow at ' + width + ': ' + JSON.stringify(sizes));
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: resolve(artifacts, 'mobile-' + width + '.png'), fullPage: true });
  }
  const baseReport = await page.evaluate(() => structuredClone(window.POEM_REPORT));
  const makeVariant = (source, requestedAxes, nextScale) => {
    const data = structuredClone(source);
    data.rubric.axes = requestedAxes.map((id) => data.rubric.axes.find((axis) => axis.id === id));
    data.rubric.scale = nextScale.slice();
    for (const axis of data.rubric.axes) {
      axis.anchors = nextScale.map((_, index) => axis.anchors[Math.min(index, axis.anchors.length - 1)]);
    }
    const normalize = (review) => {
      const prior = review.scores;
      review.scores = Object.fromEntries(
        data.rubric.axes.map((axis) => {
          const score = structuredClone(prior[axis.id]);
          if (score.value !== null)
            score.value = Math.round(((score.value - 0) * (nextScale[nextScale.length - 1] - nextScale[0])) / 4);
          return [axis.id, score];
        })
      );
    };
    data.reviews.forEach(normalize);
    Object.values(data.active_reviews).forEach(normalize);
    return data;
  };
  const variantData = makeVariant(
    baseReport,
    ['recognition', 'relations', 'consent', 'touch', 'judgment'],
    [0, 1, 2, 3, 4, 5, 6]
  );
  const variantContext = await browser.newContext({ viewport: { width: 1440, height: 1050 }, reducedMotion: 'reduce' });
  await variantContext.route(/^https?:/, (route) => {
    const target = new URL(route.request().url());
    if (target.origin === new URL(url).origin) return route.continue();
    external.push(target.href);
    return route.abort();
  });
  await variantContext.route(/\/report-data\.js(\?v=[a-f0-9]+)?$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/javascript',
      body: 'window.POEM_REPORT = ' + JSON.stringify(variantData) + ';'
    })
  );
  const variantPage = await variantContext.newPage();
  variantPage.on('pageerror', (error) => errors.push(String(error)));
  await variantPage.goto(url);
  await variantPage.locator('#radar .axis-control').first().waitFor();
  assert.equal(await variantPage.locator('#radar .axis-control').count(), 5);
  assert.equal(await variantPage.locator('#radar .ring-number').count(), 7);
  assert.equal(await variantPage.locator('#score-table tbody tr').count(), 5);
  assert.equal(await variantPage.locator('#anchors-list li').count(), 7);
  assert.match(await variantPage.locator('#scale-title').textContent(), /0–6/);
  assert.match(await variantPage.locator('#axis-values small').first().textContent(), /\/ 6/);
  await variantPage.screenshot({ path: resolve(artifacts, 'variable-profile-five-axis-1440.png'), fullPage: true });
  await variantPage.locator('#radar [data-axis="relations"]').click();
  assert.equal(await variantPage.locator('#axis-title').textContent(), 'Shared agency');
  await variantPage.setViewportSize({ width: 320, height: 844 });
  await variantPage.waitForFunction(() => document.querySelectorAll('.radar-key [data-axis]').length === 5);
  assert.equal(await variantPage.locator('.radar-key [data-axis]').count(), 5);
  assert.ok(await variantPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await variantPage.screenshot({ path: resolve(artifacts, 'variable-profile-five-axis-320.png'), fullPage: true });
  await variantContext.close();

  const twoAxisData = makeVariant(baseReport, ['recognition', 'relations'], [0, 1, 2, 3, 4]);
  const twoAxisContext = await browser.newContext({ viewport: { width: 800, height: 900 }, reducedMotion: 'reduce' });
  await twoAxisContext.route(/^https?:/, (route) =>
    new URL(route.request().url()).origin === new URL(url).origin ? route.continue() : route.abort()
  );
  await twoAxisContext.route(/\/report-data\.js(\?v=[a-f0-9]+)?$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/javascript',
      body: 'window.POEM_REPORT = ' + JSON.stringify(twoAxisData) + ';'
    })
  );
  const twoAxisPage = await twoAxisContext.newPage();
  twoAxisPage.on('pageerror', (error) => errors.push(String(error)));
  await twoAxisPage.goto(url);
  await twoAxisPage.locator('.radar-fallback').waitFor();
  assert.match(await twoAxisPage.locator('.radar-fallback').textContent(), /at least three dimensions/);
  assert.equal(await twoAxisPage.locator('#export-radar').isDisabled(), true);
  assert.equal(await twoAxisPage.locator('#score-table tbody tr').count(), 2);
  await twoAxisPage.screenshot({ path: resolve(artifacts, 'variable-profile-two-axis-800.png'), fullPage: true });
  await twoAxisContext.close();
  assert.deepEqual(errors, []);
  assert.deepEqual(external, []);
  await writeFile(
    resolve(artifacts, 'verification.json'),
    JSON.stringify(
      {
        passed: true,
        widths: [1440, 1280, 1100, 1050, 840, 800, 768, 600, 521, 520, 390, 320],
        runSelection: 6,
        cohortSelection: true,
        originalReferenceAcrossCohorts: true,
        scoreLabelsContained: true,
        sourceNavigation: true,
        keyboardAxisSelection: true,
        unassessedBreaksPolygon: true,
        variableRubricProfiles: true,
        variableScale: true,
        radarFallback: true,
        pilotEditorialReading: true,
        pilotUnreviewedHistory: true,
        pilotPreviewReset: true,
        pilotPromptNavigation: true,
        svgExport: true,
        externalRequests: external,
        pageErrors: errors
      },
      null,
      2
    ) + '\n'
  );
  console.log(
    'Browser checks passed: original Dreamy Sol reference across cohorts, score cards contained at 12 viewport widths, provisional pilot reading and unreviewed history, preview interaction/reset/Escape, exact prompt, keyboard/spokes, evidence, exports and offline mode.'
  );
} finally {
  server?.kill();
  await browser.close();
}
