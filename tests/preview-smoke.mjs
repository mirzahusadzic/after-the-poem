import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const channel = process.env.PLAYWRIGHT_CHANNEL || 'chrome';
const browser = await chromium.launch({ headless: true, channel });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, reducedMotion: 'reduce' });
const page = await context.newPage(),
  errors = [],
  external = [];
page.on('pageerror', (e) => errors.push(String(e)));
await context.route(/^https?:\/\/(?!127\.0\.0\.1[:/]|localhost[:/])/, (route) => {
  external.push(route.request().url());
  return route.abort();
});
await mkdir('.local/browser', { recursive: true });
const base = process.env.PREVIEW_URL || 'http://127.0.0.1:4317';
async function runCommand(command, submit) {
  const completed = page.waitForResponse(
    (response) =>
      response.url().endsWith('/_preview/command') &&
      response.request().method() === 'POST' &&
      response.request().postDataJSON().command === command
  );
  await submit();
  const response = await completed,
    result = await response.json();
  assert.equal(response.status(), 200, result.error);
  assert.equal(result.exit_code, 0, result.output);
  // The terminal's accessible rows hold only what is visible; a long answer scrolls its command away,
  // so wait for the answer's last line.
  const tail = result.output.trim().split('\n').pop().trim().slice(0, 40);
  await page.waitForFunction(
    (tail) => document.querySelector('.xterm-accessibility')?.textContent.includes(tail),
    tail
  );
  return result.output;
}
async function assertMaxAndEscape() {
  await page.locator('#preview-workspace').waitFor({ state: 'visible' });
  assert.ok(await page.locator('#preview-workspace.is-full-window').count());
  assert.equal(await page.locator('#preview-status').textContent(), 'Press Esc to exit');
  assert.equal(await page.locator('.preview-toolbar').count(), 0);
  await page.keyboard.press('Escape');
  await page.locator('#preview-workspace').waitFor({ state: 'hidden' });
}
try {
  await page.goto(base);
  await page.locator('#tab-artefact').click();
  for (const [id, selector] of [
    ['sol-original', '#materialList .material-card'],
    ['sol61-max', 'h1']
  ]) {
    await page.locator(`[data-run="${id}"]`).click();
    await page.locator('#open-preview').click();
    const frame = page.frameLocator('#artefact-frame');
    await frame.locator(selector).first().waitFor({ timeout: 15000 });
    assert.ok(await page.locator('#preview-workspace.is-full-window').count());
    const previewFrame = page
      .frames()
      .find((candidate) => candidate !== page.mainFrame() && candidate.url().includes('/previews/' + id + '/'));
    assert.ok(previewFrame, 'Expected an opaque preview frame for ' + id);
    if (id === 'sol-original') {
      await frame.locator('#composerText').fill('A browser preview leaves a consequential question.');
      await frame.locator('#composer button[type="submit"]').click();
      assert.match(await frame.locator('#materialList').textContent(), /A browser preview leaves/);
      const beforeNavigation = previewFrame.url();
      await previewFrame.evaluate(() => {
        const link = document.createElement('a');
        link.id = 'external-navigation-probe';
        link.href = 'https://example.com/network-probe';
        link.textContent = 'external';
        document.body.append(link);
      });
      await previewFrame.locator('#external-navigation-probe').click();
      await page.waitForTimeout(150);
      assert.equal(previewFrame.url(), beforeNavigation);
      await frame.locator('[data-view="constellation"]').click();
      assert.ok(await frame.locator('#constellationNodes').textContent());
    } else {
      console.log('Clearing snapshot opened.');
    }
    await page.locator('#preview-workspace').screenshot({ path: resolve('.local/browser', id + '-preview.png') });
    await assertMaxAndEscape();
  }
  const consoles = {
    'astra-garden': { change: 'step', look: 'inspect', shows: /water=2.*beds=1/ },
    'astra-max': {
      change: 'ask browser-test "A question from the in-browser console"',
      look: 'status',
      shows: /browser-test/
    },
    'dream-claude-fable51-20261022': {
      change: 'probe "Does this world remember a visitor?"',
      look: 'log -n 1',
      shows: /remember a visitor/,
      verify: 'status'
    },
    'dream-claude-opus55-v2-20261022': {
      change: 'probe worlds/jugs.world "big == 4" --why "a visitor asks"',
      look: 'receipts --last 1',
      shows: /a visitor asks/,
      verify: 'receipts --verify'
    }
  };
  for (const [id, steps] of Object.entries(consoles)) {
    await page.locator(`[data-run="${id}"]`).click();
    await page.locator('#open-preview').click();
    await page.locator('.xterm-screen').waitFor({ timeout: 20000 });
    assert.ok(await page.locator('#preview-workspace.is-full-window').count());
    await page.waitForFunction(() => !document.querySelector('#open-preview').disabled);
    await page.waitForFunction(() => document.querySelector('.xterm-accessibility')?.textContent.includes('world ›'));
    await runCommand(steps.change, async () => {
      await page.locator('#console-command').fill(steps.change);
      await page.locator('#console-form button').click();
    });
    const content = await runCommand(steps.look, async () => {
      await page.locator('#console-command').fill(steps.look);
      await page.locator('#console-form button').click();
    });
    assert.match(content, steps.shows);
    assert.doesNotMatch(content, /sandbox_init|Operation not permitted/);
    await runCommand(steps.verify || 'verify', async () => {
      await page.locator('.xterm-helper-textarea').focus();
      await page.keyboard.type(steps.verify || 'verify');
      await page.keyboard.press('Enter');
    });

    await page.locator('#preview-workspace').screenshot({ path: resolve('.local/browser', id + '-console.png') });
    await assertMaxAndEscape();
  }
  await page.locator('[data-run="sol56-max"]').click();
  await page.locator('#open-preview').click();
  await page.frameLocator('#artefact-frame').locator('#worldCanvas').waitFor({ timeout: 20000 });
  assert.ok(await page.locator('#preview-workspace.is-full-window').count());
  const liveFrame = page
    .frames()
    .find((frame) => frame.url().startsWith('http://127.0.0.1:') && frame.url() !== page.url());
  const before = await liveFrame.evaluate(async () => (await (await fetch('/api/world')).json()).cycle);
  await page.frameLocator('#artefact-frame').getByRole('button', { name: 'Turn once' }).click();
  await liveFrame.waitForFunction(
    async (previous) => (await (await fetch('/api/world')).json()).cycle > previous,
    before
  );

  await page.locator('#preview-workspace').screenshot({ path: resolve('.local/browser', 'sol56-live.png') });
  await assertMaxAndEscape();
  // A world that forbids framing runs unmodified in its own tab; Escape here stops its session.
  await page.locator('[data-run="dream-deepseek-20261021-deepseek-s1"]').click();
  await page.locator('#tab-artefact').click();
  await page.locator('#open-preview').click();
  await page.locator('#preview-handoff-link').waitFor({ timeout: 20000 });
  assert.ok(await page.locator('#artefact-frame').isHidden());
  const [world] = await Promise.all([context.waitForEvent('page'), page.locator('#preview-handoff-link').click()]);
  await world.locator('body').waitFor();
  const worldUrl = world.url();
  assert.match(worldUrl, /^http:\/\/127\.0\.0\.1:\d+\/$/);
  assert.ok(await (await world.request.get(worldUrl + 'api/world')).json());
  await world.close();
  await page.bringToFront();
  await page.keyboard.press('Escape');
  await page.locator('#preview-workspace').waitFor({ state: 'hidden' });
  await page.waitForFunction(
    async (url) => {
      try {
        await fetch(url, { mode: 'no-cors' });
        return false;
      } catch {
        return true;
      }
    },
    worldUrl,
    { timeout: 10000 }
  );

  assert.deepEqual(errors, []);
  assert.deepEqual(external, []);
  console.log('The framing-refusing Ruby world opened in its own tab and stopped with Escape.');
  console.log(
    'All six artefact previews opened full-window and closed with Escape; browser, Ruby, Zsh and Python interactions succeeded, including direct terminal typing.'
  );
} catch (e) {
  console.log('Preview status:', await page.locator('#preview-status').textContent());
  console.log('Page errors:', errors);
  await page.screenshot({ path: resolve('.local/browser', 'preview-failure.png'), fullPage: true });
  throw e;
} finally {
  await context.close();
  await browser.close();
}
