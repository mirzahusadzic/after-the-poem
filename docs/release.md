# Release

What the public page shows, what the research view keeps, and how a world gets published.

See [Pipeline](pipeline.md#workbench) for the step-by-step order and [Concepts](concepts.md) for the vocabulary.

## Two views

| View | Command | Shows |
| --- | --- | --- |
| Release page | `npm run dev`, `npm run build`, `npm run build:pages` | Nine run IDs from `shown_runs`, each with its latest `-muse-v5-1` reading. |
| Research view | `npm run dev:full`, `atlas.py build --view full` | All ten retained runs and every reading: `-muse-v4-1`, `-muse-v5-1`, `static-*`, `unreviewed` and the hidden pilot. |

`atlas.py serve` defaults to the release view on port 4317; `serve:pages` serves the static build on port 4318.

## Reader generations

The release reader is pinned under `reader` in `data/presentation.json`: Muse Spark 1.3 Contributor, three sessions, `evaluation_suffix: -muse-v5-1`.

| Generation | IDs | What it is |
| --- | --- | --- |
| v5 (current) | `*-muse-v5-1` | The fixed pipeline and prompt the release page shows. Sessions record a seed reading, carriage map and standing map; each judgment carries a lens, a reading and the case for neighbouring levels. Profiles live in `examples/rescore-muse/`. |
| v4 (earlier) | `*-muse-v4-1` | The previous prompt generation, kept for comparison. Visible only in the research view. |
| Static | `*-static-*-v1` | Codex static readings on retained artefacts (`basis: static-artifact`), including the revised `sol-original-static-20261019-v2` which supersedes v1. |
| Placeholders | `*-unreviewed-v1` | What bundle import creates: every dimension `null`, explicitly unreviewed. Never a score. |
| Editorial | `deepseek-flash-max-pilot-editorial-20261021-v1` | The pilot's in-session editorial reading. |

A world can carry several readings at once; `data/review-selection.json` and `data/presentation.json` choose what each view displays without deleting the rest. The page also shows supported seed alternatives when a reading's carriage retains them. `data/review-manifest.json` pins each admitted authored review's bytes so a valid-looking edit cannot silently replace earlier history.

## The flat-reader caveat

The release page is deliberately flat: one world, one reader, one reading. That flattening hides real structure:

- Where sessions split, the radar point and the table show the sessions' mean in thirds (3⅓: two at 3, one at 4), and the level named beside it is the one at least two sessions give. The "2 of 3 sessions agree" note states the quorum; every raw level stays in the evaluation package.
- An unresolved question (no two sessions agree) has no release level; the research view keeps every session.
- `null` stays unassessed on both views and is never rendered as zero. Static checks in the repository cannot establish comprehension or consent.
- The critic was chosen after comparing readers, the two DeepSeek worlds are the largest artefacts here, and every v5 finding holds for one reader at one reasoning level through one prompt. See [Study](study.md#limits).

Compare readings in the research view before citing a level.

## Nine shown, ten retained

`data/runs.json` retains ten runs; `shown_runs` lists nine. The tenth is `deepseek-flash-max-pilot`, the sole member of the hidden cohort `dream-workspace-result-v1`:

- The October pilot used an earlier router and a seed with a handoff suffix; its prompt hash differs from the study cohort's.
- It stays in the data, with its bundle, corpus record, v4 evaluation and editorial review — but `hidden_cohorts` keeps it off the release page, and the build refuses a `shown_runs` entry from a hidden cohort.
- The research view shows it, and the browser smoke test asserts that it leads its own cohort while each evaluation package remains separately selectable.

`npm run build` prints every retained world that is not listed yet, so nothing goes public by accident.

## Checklist: publish a world

1. Read it with the page's reader (a `-muse-v5-1` package, three sessions). Both dream commands do this; admitted worlds use the `examples/rescore-muse/` profiles.
2. Open it in the research view and run `npm run check`.
3. Name it: a proper `label` and `title` in `data/runs.json` (Codex runs start with the model ID and run ID; Claude Code worlds take the profile's label and the world's own title).
4. Register its preview: browser worlds need an entry in `data/artefacts.json`; Zsh, Python and Ruby worlds need entries in `scripts/previews.py` and `data/artefacts.json` and a hashed screenshot in `assets/artefacts/RUN.png`. Zsh and Python console worlds also need a recording (`python3 scripts/record_consoles.py RUN` on macOS); a Ruby server world shows its screenshot on the static site.
5. Add its run ID to `shown_runs` in `data/presentation.json`.
6. Run the fast checks, then the full gate: `npm run verify` (no model calls). Build the static site with `npm run build:pages`; the Pages workflow publishes only when started by hand.
