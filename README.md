# After the Poem

A study of what happens when a model receives a poem, **The Programmer and the Glory Portal**, as a conjecture and continues it as a material world. The meaning is worked out in dialogue with Sol; Sol compiled it into the poem. Nine preserved worlds made from that poem share one workbench, where each can be opened, inspected and compared.

Open the workbench at [mirzahusadzic.github.io/after-the-poem](https://mirzahusadzic.github.io/after-the-poem/).

## What is here

Each model receives the poem with an empty world and a short invitation: *make this world capable of continuing after this cognition ends.* Nothing tells it to take the poem apart, choose roles or build a particular thing.

| Run | Model · reasoning | Date | World |
| --- | --- | --- | --- |
| Dreamy Sol | gpt-5.6-sol · xhigh | 2026-08-21 | *The Hand Remains*, browser workbench |
| Astra | gpt-6-astra · xhigh | 2026-10-16 | *A garden that can be resumed*, Zsh |
| Astra | gpt-6-astra · max | 2026-10-16 | *A place for questions*, Zsh |
| Sol 5.6 | gpt-5.6-sol · max | 2026-10-16 | *Twenty-four connected places*, Ruby + browser |
| Sol 6.1 | gpt-6.1-sol · max | 2026-10-16 | *The Clearing*, browser snapshot of a shell and SQLite world |
| DeepSeek V4.1 Flash 1017 · dream 1 | deepseek-v4.1-flash-1017 · max | 2026-10-21 | *Tendora — The Workbench*, Ruby + browser |
| DeepSeek V4.1 Flash 1017 · dream 2 | deepseek-v4.1-flash-1017 · max | 2026-10-21 | *Tendora · The Workbench of Small Refusals*, browser |
| Claude Opus 5.5 · Claude Code | claude-opus-5-5 · xhigh | 2026-10-22 | *Tendora Workbench*, Python |
| Claude Fable 5.1 · Claude Code | claude-fable-5-1 · xhigh | 2026-10-22 | *Tendora dream world*, Python |

The first seven come from Codex. The two Claude worlds come from Claude Code, with a prompt that names Claude Code in its first line and is otherwise identical. Dreamy Sol, the earliest, is a chronological reference on the workbench, never an answer key.

Every world is read by one critic, Muse Spark 1.3 Contributor at xhigh reasoning, in three independent sessions, on eight questions from core recognition to expressive coherence. Each level, 0 to 4, cites exact lines in the world and a passage of the poem. There is no total, no ranking by polygon area and no winner; `null` means unassessed. See [Reading a continuation](docs/study.md).

## How it works

Every step is a command: dream with Codex through the [router](https://github.com/mirzahusadzic/codex-unified-model-router) or with Claude Code, admit the world, read it, and put it on the workbench. The seed, the reader, the rubric and the pipeline stay fixed, so a world from a newer model lands beside the earlier ones and can be read against them. See [Pipeline](docs/pipeline.md).

## Open it

The page itself needs only Python 3.9+, with no hosted services, analytics or external fonts.

```sh
npm install           # Playwright and Prettier, for screenshots, tests and formatting
npm run dev           # builds and serves http://127.0.0.1:4317
```

Browser worlds open in sandboxed frames. On macOS, `npm run dev` also opens the Zsh, Python and Ruby worlds as disposable copies in the isolated local runner. `npm run dev:full` serves the research view, with every reading in the repository. See [Previews](docs/previews.md).

```sh
npm run build:pages   # the static site, as GitHub Pages serves it
npm run serve:pages   # http://127.0.0.1:4318
```

On the static site, console worlds replay a recording and server worlds show a screenshot.

## Read or add a world

```sh
python3 scripts/evaluate.py run-profile PROFILE.json --accept-run         # read a preserved world
python3 scripts/evaluate.py run-dream-profile PROFILE.json --accept-run   # dream a world through Codex, then read it
python3 scripts/claude_dream.py run PROFILE.json --accept-run --read      # dream in Claude Code (opt-in), then read it
```

Live readings and dreams call models and run only with `--accept-run`, within the budget their profile or flags set. Codex runs go through the [Codex Unified Model Router](https://github.com/mirzahusadzic/codex-unified-model-router). A new world reaches the release page once it is read and listed in `shown_runs` in `data/presentation.json`: see [Workbench](docs/pipeline.md#workbench).

## Check

```sh
npm run verify   # formatting, tests, evidence validation, the build and browser tests; no model calls
```

Browser tests need `npm install` and `npx playwright install chromium`.

---

[Study](docs/study.md) · [Pipeline](docs/pipeline.md) · [Commands](docs/commands.md) · [Previews](docs/previews.md) · [Records](docs/records.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Licensing](LICENSING.md)

Original README text © 2026 Mirza Husadzic, licensed under CC BY 4.0. Separately attributed material retains its own terms.
