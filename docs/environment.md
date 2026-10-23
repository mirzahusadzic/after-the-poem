# Environment

What is tested, what each step needs, and which steps stay Python-only.

See [Commands](commands.md) for every flag and [Troubleshooting](troubleshooting.md) when a check fails.

## Tested matrix

| Piece | Tested | Notes |
| --- | --- | --- |
| Python | 3.9+ | Standard library only. Build, serve, validation, readings and dreams all run on it. |
| Node.js | 22 | Needed only for browser smoke tests, formatting checks and screenshots. CI pins `node-version: 22`. |
| Codex CLI | 0.163.0 | For Codex dreams and readings through the router. |
| Claude Code | 2.1.308 | For Claude Code dreams (`claude` on `PATH`). |
| Model router | 127.0.0.1:18766/v1 | The [Unified Model Router](https://github.com/mirzahusadzic/codex-unified-model-router); only loopback URLs are accepted. |
| Reader | Muse Spark 1.3 Contributor, xhigh | Fixed for the release page; other models need a catalog and capability entry. |
| Browsers | Chromium (CI), WebKit/Firefox opt-in | CI installs Chromium and sets `PLAYWRIGHT_CHANNEL=chromium`. |
| Native previews | macOS | Zsh, Python and Ruby worlds run only in the macOS sandbox; no fallback elsewhere. |

Python and Node versions are floors and pins respectively: newer Python 3 releases work through the standard library; Node follows CI.

## Prerequisites

| Goal | Needs |
| --- | --- |
| Build, serve, validate | Python 3.9+ only: `python3 scripts/atlas.py build`, `serve` and `validate`. Their npm shortcuts (`npm run build`, `npm run dev`, `npm run check`) need Node only to start them. |
| Browser tests, screenshots, formatting | Node.js 22 plus `npm install`. Then a Playwright browser (below). |
| Readings, Codex dreams | Codex CLI, the router, a catalog entry and a capability entry per model, plus `--accept-run` and an explicit budget. |
| Claude Code dreams | `claude` on `PATH` plus `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_DREAM_TOKEN_FILE` (mode 600) or `ANTHROPIC_API_KEY` with `--max-usd`. |
| Native previews, console recordings | macOS with `sandbox-exec`; the research view (`npm run dev:full`). |

## Playwright install

```sh
npm install
npx playwright install chromium                        # macOS
npx playwright install --with-deps chromium            # Linux (CI)
```

- CI sets `PLAYWRIGHT_CHANNEL=chromium`.
- To reuse an existing Playwright copy instead of installing one, set `PLAYWRIGHT_MODULE_PATH` to the directory that holds it, as the UI smoke test already supports.
- Serve over HTTP (`npm run dev`, `npm run dev:full`, `npm run serve:pages`). Opened from `file:`, sandboxed frames cannot load their assets and the page shows how to start a server instead.
- `npx playwright install --with-deps` needs OS package installation; on a locked-down Linux host, prefer `PLAYWRIGHT_MODULE_PATH` or run only the fast checks.

## Fast checks vs the full gate

Each fast check calls no model, and each npm script is a shortcut for a Python command:

| Command | Python command | What it does |
| --- | --- | --- |
| `npm test` | `python3 -m unittest discover -s tests -v` | Python unit tests |
| `npm run check` | `python3 scripts/atlas.py validate` | Records, hashes, citations; writes nothing |
| `npm run build` | `python3 scripts/atlas.py build` | Local site into `dist/` |

The full gate needs Node and browsers but still calls no model:

```sh
npm run verify   # format:check, test, check, build, validate-contract on tests/fixtures/evaluation/attribution, test:ui, test:page, test:release
```

`npm run test:engines` (WebKit/Firefox repeat of the release test) and `npm run test:preview` (native previews against `npm run dev:full` on macOS) are opt-in and outside `verify`.
