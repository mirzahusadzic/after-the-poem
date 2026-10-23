# Troubleshooting

Failures grouped by cause, with the command that diagnoses each one. No step here calls a model unless it says `--accept-run`.

See [Commands](commands.md) for every flag, [Environment](environment.md) for the tested setup and [Pipeline](pipeline.md) for the order of operations.

## Router and Codex setup

The router is local and loopback-only. Anything else is refused before any call.

| Symptom | Fix |
| --- | --- |
| Router URL refused | Use `http://127.0.0.1:18766/v1` (the default) or pass `--router-url` with another `http://127.0.0.1` address. Hostnames, HTTPS and remote hosts are rejected. |
| Catalog missing | The study looks for `models.json` in `CODEX_HOME` (`~/.codex`). Pass `--catalog FILE` or follow the [router install](https://github.com/mirzahusadzic/codex-unified-model-router#install). |
| Router profile missing | The installer writes `unified-openrouter.config.toml` into `~/.codex`. Pass `--router-profile FILE` if yours lives elsewhere. |
| Wrong model name | The requested model must match the catalog exactly, including any `openrouter/` prefix. |
| Setup doubt | Run the matching doctor first: `evaluate.py doctor-profile`, `doctor-dream-profile` or `doctor --dry-run`. Doctors call no model. |

## Model capabilities

Every model must be known in two places: the router's catalog and the study's `data/model-capabilities.json`. A model missing from either fails before any call — add it to both (a docs-adjacent data change, outside these pages) or pick a listed model. Rescore profiles in `examples/rescore-muse/` name the release reader exactly.

## Budgets

Live work refuses without `--accept-run` and without an explicit budget, and stops before the turn that would pass a limit.

- A reading needs `--max-turns`, `--max-input-tokens` and `--max-output-tokens` at minimum, from the profile or the flags. `--max-seconds` and `--max-usd` are optional caps.
- Any budget flag on the command line replaces the profile's whole budget. Pass all of them together, including through `claude_dream.py --read-arg=--flag=value`.
- `--max-usd` needs all three `--price-*` flags (uncached input, cached input, output, per million tokens). The cap is checked against the estimate from reported tokens, not the provider's bill.
- `claude_dream.py run --max-usd` is enforced inside Claude Code and is required with `ANTHROPIC_API_KEY`, which bills per token.
- `load_bearing.py run` takes `--total-usd` for all trials plus each trial's budget after a bare `--`; a trial that does not fit its reservation stays unresolved and is never estimated.
- A run stopped by its budget resumes with the same command and a larger budget; nothing completed is repeated.

## Resuming interrupted work

Rerun the same command with the same arguments: `run-profile`, `run-dream-profile`, `run-evaluation`, `score` and `extract` replay preserved turns and continue from the first missing answer. Campaigns resume with `evaluate.py resume` (or `pipeline.py run` again), which skips jobs that have a receipt.

| Case | Command |
| --- | --- |
| Sessions finished but the record was not written | `evaluate.py recover-score DIRECTORY`, or `recover-extraction DIRECTORY` for an extraction. |
| Unfinished attempt to set aside | `claude_dream.py run --retry` or `load_bearing.py run --retry`; the old attempt is kept as `.attempt-N` (or `TRIAL.attempt-N`). |
| Campaign refuses after a code change | The runner froze the pipeline hash at `plan`/`prepare`; plan a new campaign rather than forcing the old one. |
| Dry run admitted nothing | Expected: `OUTPUT.dry-run` rehearsals use synthetic answers and are never admitted. Note `run-dream-profile --dry-run` rehearses the generation alone. |

## Native sandbox and previews

Native worlds run only on macOS through the isolated local runner, and only from the research or release home target — never on the static site and never with a fallback to unrestricted execution.

- Start `npm run dev:full` (research view) before `npm run test:preview`, with `PREVIEW_URL=http://127.0.0.1:4317`.
- At most six native sessions exist at once; they expire after 30 minutes without a console command and are cleaned up on server exit. A force-killed runner can leave a temporary directory for manual cleanup.
- Each session gets a 15-second command timeout, 20-second CPU limit, 16-MiB file-size limit, 128 open files and 64 KiB of captured output. The candidate writes only inside its disposable `world/` copy; `control/` stays host-only.
- The boundary tests deny host reads and candidate-supplied executables; they skip only when an outer sandbox forbids nested `sandbox-exec`.
- Console worlds need entries in `CONSOLES`/`COMMANDS` in `scripts/previews.py` plus `data/artefacts.json`; server worlds live in `SERVERS`. Recordings come from `python3 scripts/record_consoles.py` on macOS.

## Browser installs and smoke tests

Install Node.js 22 and a Playwright browser as in [Environment](environment.md#playwright-install).

- `test:ui` builds the full view, runs `tests/ui-smoke.mjs`, then rebuilds the release view. `test:page` tests the local site it builds. `test:release` builds the static site and restores the local site after.
- To reuse an existing Playwright copy, set `PLAYWRIGHT_MODULE_PATH` instead of installing.
- `test:engines` repeats the release test under WebKit and Firefox (`PLAYWRIGHT_BROWSER`); `test:preview` needs the macOS runner above. Both are outside `verify`.
- Serve over HTTP. From `file:`, sandboxed frames cannot load assets and the page explains how to start a server rather than relaxing the sandbox.
- If the external-navigation probe fails the build, a preview attempted a non-loopback request: treat it as a world bug, not a test flaw.
