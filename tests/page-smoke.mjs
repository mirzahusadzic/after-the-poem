import { createRequire } from 'node:module';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import assert from 'node:assert/strict';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const artifacts = resolve(root, '.local/browser');
await mkdir(artifacts, { recursive: true });
const temporary = await mkdtemp(resolve(tmpdir(), 'after-poem-page-smoke-'));
const extraction = resolve(temporary, 'extraction');
const evaluation = resolve(temporary, 'evaluation');
const pageDir = resolve(temporary, 'page');

function runEvaluate(...args) {
  const result = spawnSync('python3', [resolve(root, 'scripts/evaluate.py'), ...args], { cwd: root, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  return JSON.parse(result.stdout);
}

const model = 'deepseek/deepseek-v4.1-flash-1017';
runEvaluate(
  'extract',
  'sol-original',
  '--output',
  extraction,
  '--extraction-id',
  'sol-original-browser-extraction',
  '--model',
  model,
  '--dry-run'
);
runEvaluate(
  'score',
  '--extraction',
  resolve(extraction, 'extraction.json'),
  '--policy',
  resolve(root, 'tests/fixtures/evaluation/attribution/scoring-policy.json'),
  '--output',
  evaluation,
  '--evaluation-id',
  'sol-original-browser-evaluation',
  '--model',
  model,
  '--synthetic-outcomes',
  'contradicted,mixed,supported',
  '--dry-run'
);
runEvaluate('prepare-page', '--evaluation', evaluation, '--output', pageDir);
runEvaluate('validate-presentation', pageDir);

const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' });
const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, reducedMotion: 'reduce' });
const page = await context.newPage();
const errors = [],
  external = [];
page.on('pageerror', (error) => errors.push(String(error)));
await context.route(/^https?:/, (route) => {
  external.push(route.request().url());
  return route.abort();
});
try {
  await page.goto(pathToFileURL(resolve(pageDir, 'index.html')).href);
  assert.equal(await page.title(), 'From conjecture to continuation · After the Poem evaluation');
  assert.equal(await page.locator('table tbody tr').count(), 3);
  assert.match(await page.locator('.status').textContent(), /unresolved.*No score awarded/);
  assert.equal(await page.locator('script').count(), 0);
  assert.deepEqual(errors, []);
  assert.deepEqual(external, []);
  for (const width of [1440, 800, 390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    const size = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, viewport: innerWidth }));
    assert.ok(size.scroll <= size.viewport, `Generic presentation overflows at ${width}: ${JSON.stringify(size)}`);
    const layout = await page.evaluate(() => {
      const table = document.querySelector('.table-scroll').getBoundingClientRect();
      const evidence = [...document.querySelectorAll('section')]
        .find((section) => section.textContent.startsWith('Evidence'))
        .getBoundingClientRect();
      return { tableBottom: table.bottom, evidenceTop: evidence.top, tableHeight: table.height };
    });
    assert.ok(layout.tableHeight > 0, `Judgment table collapsed at ${width}`);
    assert.ok(
      layout.tableBottom <= layout.evidenceTop + 1,
      `Judgment table overlaps evidence at ${width}: ${JSON.stringify(layout)}`
    );
    if (width === 1440 || width === 320) {
      await page.screenshot({ path: resolve(artifacts, `evaluation-page-${width}.png`), fullPage: true });
    }
  }
  console.log(
    'Generic evaluation page passed: unresolved split, three samples, escaped evidence, no scripts or network, desktop and mobile layout.'
  );
} finally {
  await context.close();
  await browser.close();
  await rm(temporary, { recursive: true, force: true });
}
