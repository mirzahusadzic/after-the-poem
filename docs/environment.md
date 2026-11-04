# Environment

What is tested, what each step needs, and which steps stay Python-only.

See [Commands](commands.md) for every flag and [Troubleshooting](troubleshooting.md) when a check fails.

---

### Tested matrix

| Piece | Tested | Notes |
| --- | --- | --- |
| Python | 3.13+ | Standard library, plus Gadfly (`midwife` 0.7.1) for load-bearing checks. Build, serve, validation, readings and dreams all run on it. |
| uv | 0.11.6 | Installs the Python and the Gadfly release that `uv.lock` pins. The npm scripts run Python through `uv run`; CI sets `UV_LOCKED=1`, so a stale lock fails. |
| Node.js | 22 | Needed only for browser smoke tests, formatting checks and screenshots. CI pins `node-version: 22`. |
| Codex CLI | 0.163.0, 0.166.0 | For Codex dreams and readings through the router. The published readings used 0.163.0; Gadfly rounds 1 and 2 read through 0.166.0. |
| Claude Code | 2.1.308 | For Claude Code dreams (`claude` on `PATH`). |
| Model router | 127.0.0.1:18766/v1 | The [Unified Model Router](https://github.com/mirzahusadzic/unified-model-router); only loopback URLs are accepted. It must report which provider served a call (`reports: provider`, `generationLookup` in `/health`). |
| Reader | Muse Spark 1.3 Contributor, xhigh | Fixed for the release page; other models need a catalog and capability entry. |
| Browsers | Chromium (CI), WebKit/Firefox opt-in | CI installs Chromium and sets `PLAYWRIGHT_CHANNEL=chromium`. |
| Native previews | macOS | Zsh, Python and Ruby worlds run only in the macOS sandbox; no fallback elsewhere. |

Python's version is a floor, and it is Gadfly's: on an older Python every script stops with a reason. Node's version is a pin and follows CI.

In these docs, `python3` means Python 3.13 or later. If yours is older, write `uv run python` instead.

---

### Prerequisites

| Goal | Needs |
| --- | --- |
| Build, serve, validate | Python 3.13+ (or uv): `python3 scripts/atlas.py build`, `serve` and `validate`. Their npm shortcuts (`npm run build`, `npm run dev`, `npm run check`) need Node only to start them. |
| Browser tests, screenshots, formatting | Node.js 22 plus `npm install`. Then a Playwright browser (below). |
| Readings, Codex dreams | Codex CLI, the router, a catalog entry and a capability entry per model, plus `--accept-run` and an explicit budget. |
| Claude Code dreams | `claude` on `PATH` plus `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_DREAM_TOKEN_FILE` (mode 600) or `ANTHROPIC_API_KEY` with `--max-usd`. |
| Native previews, console recordings | macOS with `sandbox-exec`. Previews open from the local site with its runner (`npm run dev` or `npm run dev:full`), and the preview smoke test runs against the research view. Recordings start the runner themselves. |

---

### Playwright install

On macOS, install the Node packages and Playwright's Chromium:

```sh
npm install
npx playwright install chromium
```

On Linux, as in CI, install Chromium with the system packages it needs:

```sh
npm install
npx playwright install --with-deps chromium
```

- The smoke tests launch the installed Google Chrome (Playwright's `chrome` channel) unless `PLAYWRIGHT_CHANNEL` names another. CI sets `PLAYWRIGHT_CHANNEL=chromium` to use the Chromium it installs.
- To reuse an existing Playwright copy instead of installing one, set `PLAYWRIGHT_MODULE_PATH` to its `playwright` package directory. Every smoke test and the screenshot script read it.
- Serve over HTTP (`npm run dev`, `npm run dev:full`, `npm run serve:pages`). Opened from `file:`, sandboxed frames cannot load their assets and the page shows how to start a server instead.
- `npx playwright install --with-deps` needs OS package installation; on a locked-down Linux host, prefer `PLAYWRIGHT_MODULE_PATH` or run only the fast checks.

---

### Fast checks vs the full gate

Each fast check calls no model, and each npm script is a shortcut for a Python command:

| Command | Python command | What it does |
| --- | --- | --- |
| `npm test` | `uv run python -m unittest discover -s tests -v` | Python unit tests |
| `npm run check` | `uv run python scripts/atlas.py validate` | Records, hashes, citations; writes nothing |
| `npm run build` | `uv run python scripts/atlas.py build` | Local site into `dist/` |

The full gate needs Node and browsers but still calls no model:

```sh
npm run verify
```

It runs `format:check`, `test`, `check`, `build`, `validate-contract` on `tests/fixtures/evaluation/attribution`, `test:ui`, `test:page` and `test:release`, in that order.

`npm run test:engines` (WebKit/Firefox repeat of the release test) and `npm run test:preview` (native previews against `npm run dev:full` on macOS) are opt-in and outside `verify`.
