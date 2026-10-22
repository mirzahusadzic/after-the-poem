import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { dirname, extname, resolve, sep } from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const root = resolve(process.argv[2]);
const entry = process.argv[3] || 'index.html';
const output = resolve(process.argv[4]);
const mime = {
  '.css': 'text/css; charset=utf-8',
  '.gif': 'image/gif',
  '.html': 'text/html; charset=utf-8',
  '.jpeg': 'image/jpeg',
  '.jpg': 'image/jpeg',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.webmanifest': 'application/manifest+json; charset=utf-8',
  '.webp': 'image/webp'
};

const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url, 'http://127.0.0.1');
    const relative = decodeURIComponent(url.pathname).replace(/^\/+/, '') || entry;
    const path = resolve(root, relative);
    if (path !== root && !path.startsWith(root + sep)) throw new Error('Outside preview root');
    const body = await readFile(path);
    response.writeHead(200, {
      'Content-Type': mime[extname(path).toLowerCase()] || 'application/octet-stream',
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
      // Same-origin resources only: no outbound fetch, WebSocket, frame or form target.
      'Content-Security-Policy':
        "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; " +
        "connect-src 'self'; frame-src 'self'; form-action 'none'; worker-src 'self' blob:"
    });
    response.end(body);
  } catch (_) {
    response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    response.end('Not found');
  }
});

await new Promise((accept, reject) => {
  server.once('error', reject);
  server.listen(0, '127.0.0.1', accept);
});
const address = server.address();
const origin = `http://127.0.0.1:${address.port}`;
const browser = await chromium.launch({
  headless: true,
  // Resolve no host except loopback and keep WebRTC off the network.
  args: [
    '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
    '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'
  ],
  ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {})
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 1050 },
  reducedMotion: 'reduce',
  serviceWorkers: 'block'
});
const page = await context.newPage();
const errors = [];
const external = [];
page.on('pageerror', (error) => errors.push(String(error)));
await context.route(/^https?:/, (route) => {
  if (
    route
      .request()
      .url()
      .startsWith(origin + '/')
  )
    return route.continue();
  external.push(route.request().url());
  return route.abort();
});
if (typeof context.routeWebSocket === 'function') {
  await context.routeWebSocket(/.*/, (socket) => {
    external.push(socket.url());
    socket.close();
  });
}

try {
  await page.goto(origin + '/' + entry, { waitUntil: 'load', timeout: 30000 });
  await page.waitForTimeout(1000);
  if (errors.length) throw new Error('Preview page errors: ' + errors.join(' | '));
  if (external.length) throw new Error('Preview requested external network: ' + external.join(' | '));
  await mkdir(dirname(output), { recursive: true });
  await page.screenshot({ path: output, fullPage: false, animations: 'disabled' });
  console.log(
    JSON.stringify({ status: 'captured', output, viewport: { width: 1440, height: 1050 }, errors, external })
  );
} finally {
  await context.close();
  await browser.close();
  await new Promise((accept) => server.close(accept));
}
