# Concepts

The words the study uses, the path a world takes, the files behind the page, and the rule that every chart ships with a table.

See [Pipeline](pipeline.md) for the commands in order, [Records](records.md) for what is kept, and [Release](release.md) for what the public page shows.

## Terminology

| Term | Meaning |
| --- | --- |
| Seed | The poem plus the short awakening instruction, delivered as one prompt. Nothing in it names an architecture or task. |
| Cohort | The group of worlds dreamed from one exact prompt, pinned by its SHA-256. `dream-4521-v1` holds the seven Codex worlds plus Dreamy Sol; `dream-4521-claude-code-v1` holds the two Claude Code worlds; `dream-workspace-result-v1` holds only the hidden October pilot. |
| Dream, dreamer | The generation session and the model that works in the empty world. A Codex dream runs through the router; a Claude Code dream runs in Claude Code. |
| World, run | What the dreamer left, frozen as a record. `corpus/runs/RUN.json` holds its files as inert text; `data/runs.json` holds its identity, cohort, prompt hash and corpus hash. |
| Bundle | How a world enters: `run.json`, `prompt.txt`, `candidate/`, `manifest.json` and `observations.json`. Validated by `scripts/bundles.py` without executing anything. |
| Admission | Host code packing, checking and freezing a finished dream. Browser worlds opt into a preview separately in `data/artefacts.json`. |
| Reader, reading | The model that reads worlds (one critic: Muse Spark 1.3 Contributor at xhigh) and its three independent sessions. Host code keeps a level only where at least two sessions agree. |
| Evaluation package | One reading as its own directory under `data/evaluations/EVALUATION_ID/`, with the sessions, quorum, citations and review. |
| Review | The displayed judgment. A `-muse-v5-1` package carries the current v5 reading; a `-muse-v4-1` package the earlier prompt; a `static-*` review a Codex static reading on a retained artefact; an `unreviewed` review the placeholder import creates. Authored reviews are byte-pinned in `data/review-manifest.json`. |
| Baseline | The first admitted run of a cohort, recorded in `data/runs.json`. It is a chronological marker, never a score to beat. Dreamy Sol is the study's earliest reference, not an answer key. |
| Retained vs shown | Ten runs are retained in `data/runs.json` and `corpus/runs/`; nine run IDs are listed in `shown_runs` in `data/presentation.json` and shown on the release page. The tenth is the hidden pilot; see [Release](release.md). |
| Research view vs release page | `npm run dev:full` (or `atlas.py build --view full`) shows every retained run and every reading. The default `npm run dev` and `npm run build` apply `data/presentation.json`: nine worlds, one v5 reading each. |
| Preview | An opt-in, disposable copy of a world: browser copies in sandboxed frames, native copies in the isolated local runner. Never evidence; see [Previews](previews.md). |
| Recording | Saved console answers for the static site in `data/recordings/RUN.json`. Presentation, never evidence. |
| Presentation | The editorial choices in `data/presentation.json` and `data/review-selection.json`: which reader, which runs, which review per run. |

## Lifecycle

1. **Seed.** The cohort's exact prompt and an empty world.
2. **Dream.** A dreamer works for a fixed wall time. The capture keeps whatever it left, including failures.
3. **Admission.** Host code validates the bundle, freezes the corpus record and registers the run. Nothing runs in its preserved directory.
4. **Reading.** The reader maps the world, answers eight questions in three sessions, and host code applies the per-question quorum.
5. **Workbench.** By hand: read with the page's reader, preview, record consoles, list in `shown_runs`, check and build. See [Pipeline](pipeline.md#workbench) and [Release](release.md).

Revisions are new records with explicit supersession; readings are never silently overwritten. `null` is unassessed and never becomes zero.

## The `src/` file map

The page is buildless browser JavaScript; `scripts/atlas.py` copies `src/` into `dist/` with the preserved sources.

| File | Holds |
| --- | --- |
| `src/index.html` | The single page: workbench, reading pane, preview workspace and score table shells. |
| `src/app.js` | Workbench logic: run selection, pins, the radar (`#radar`), the score table (`#score-table`), chart caption, keyboard handling and exports. |
| `src/previews.js` | Preview controls: opening, closing, Reset/Escape behaviour and the static-site replay notice. |
| `src/style.css` | Layout, panels and responsive rules; mobile and keyboard paths stay usable. |
| `src/palette.css` | Named palettes the smoke test checks (header darker than radar, contrast kept). |
| `src/source.css` | Inert source views: escaped text and Markdown with per-line anchors. |
| `src/vendor/xterm.js`, `src/vendor/addon-fit.js` | Vendored terminal and fit addon with their licenses (`*-LICENSE`) and pinned package metadata (`*-package.json`); never edited by hand. |

`src/vendor/` keeps its licenses and pinned metadata. Formatting covers `src/*.js` (`npm run format`); Python tooling never rewrites `src/`.

## Chart/table invariant

Every chart has a table equivalent, and the table is authoritative.

- The radar (`#radar`) and the score table (`#score-table`) render the same eight dimensions from the same selected reading.
- The point and every score cell show the same value: the recorded level or, where sessions split, their mean in thirds (3⅓: two at 3, one at 4). The level named beside it is the one at least two sessions give; the radar caption and the table caption both say so.
- A radar outline needs at least three dimensions. With fewer, the panel states that the table is the primary presentation and disables the radar export.
- The full browser smoke test (`tests/ui-smoke.mjs`) checks both: ring labels `0`–`4`, eight table rows, matching captions and the fallback text.
