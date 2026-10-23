# Commands

Eight programs in `scripts/` run the study: `evaluate.py`, `claude_dream.py`, `pipeline.py`, `load_bearing.py`, `reading_controls.py`, `record_consoles.py`, `atlas.py` and `bundles.py`. Run each from the repository root as `python3 scripts/PROGRAM.py COMMAND [FLAGS]`. `--help` on a program or on any of its commands prints the text the tables below repeat.

Live work calls models, which cost money, and refuses without `--accept-run`; every other command calls no model. See [Safety](#safety) before a first live run, and [Pipeline](pipeline.md) for the order in which the commands are used.

## npm scripts

`npm run NAME` is a shorter way to run some of these. Flags after `--` pass through, as in `npm run evaluate -- status CAMPAIGN`.

| Script | What it does | Calls a model? |
| --- | --- | --- |
| `evaluate` | `python3 scripts/evaluate.py` | Only a live command with `--accept-run` |
| `dream:claude` | `python3 scripts/claude_dream.py` | Only `run --accept-run` |
| `doctor:deepseek` | `evaluate.py doctor-profile` on `examples/evaluation-profile-deepseek.json` | No |
| `evaluation:deepseek` | `evaluate.py run-profile` on the same profile | Only with `-- --accept-run` |
| `doctor:deepseek-dream` | `evaluate.py doctor-dream-profile` on `examples/dream-profile-deepseek.json` | No |
| `evaluation:deepseek-dream` | `evaluate.py run-dream-profile` on the same profile | Only with `-- --accept-run` |
| `pipeline` | `python3 scripts/pipeline.py`: the campaign steps `prepare`, `run` and `admit`, as in `evaluate.py plan`, `run` and `admit` | Only `run --accept-run` on a live campaign |
| `build` | `atlas.py build`: the local site in `dist/` | No |
| `build:pages` | `atlas.py build --target pages`: the static site in `dist/` | No |
| `serve:pages` | `atlas.py serve --target pages --port 4318` | No |
| `check` | `atlas.py validate` | No |
| `dev` | `atlas.py serve --previews` on port 4317 | No |
| `dev:full` | `atlas.py serve --view full --previews` | No |
| `preview` | The same as `dev` | No |
| `test` | The Python unit tests | No |
| `test:ui` | Builds the full view, runs `tests/ui-smoke.mjs`, then rebuilds the release view | No |
| `test:page` | Builds the site and runs `tests/page-smoke.mjs` | No |
| `test:release` | Builds the static site, runs `tests/release-smoke.mjs`, then rebuilds the local site | No |
| `test:engines` | The release smoke test in WebKit and Firefox | No |
| `test:preview` | `tests/preview-smoke.mjs` against a running `npm run dev:full` on macOS (`PREVIEW_URL=http://127.0.0.1:4317`) | No |
| `format` | Formats the JavaScript with Prettier | No |
| `format:check` | Checks the JavaScript formatting | No |
| `verify` | `format:check`, `test`, `check`, `build`, a contract validation, `test:ui`, `test:page` and `test:release` | No |

The browser tests need Node.js 22 and Playwright; see [Environment](environment.md#playwright-install). `test:engines` and `test:preview` are opt-in and are not part of `verify`.

## evaluate.py

Reads worlds against the rubric, dreams new ones and admits both, through Codex and the local model router. Each command prints its result as JSON on stdout.

```sh
python3 scripts/evaluate.py COMMAND [FLAGS]
```

```sh
python3 scripts/evaluate.py run-profile examples/evaluation-profile-deepseek.json --accept-run   # read a world (calls a model)
python3 scripts/evaluate.py run-profile examples/evaluation-profile-deepseek.json -n             # rehearse with synthetic answers
python3 scripts/evaluate.py doctor-profile examples/evaluation-profile-deepseek.json             # check first; no model call
```

The commands are listed from most to least common; campaign commands come last, from `doctor` on.

### Shared flags

Most commands take `--project`. Commands that reach Codex take the Codex and router flags. Commands that call a model also take `-n`/`--dry-run`, `--accept-run` and the [budget flags](#budget-flags).

| Flag | Values | Effect |
| --- | --- | --- |
| `--project` | `DIR` | Study directory (default: this repository) |
| `-n`, `--dry-run` | — | Rehearse with synthetic answers; calls no model |
| `--accept-run` | — | Allow model calls, which cost money; live work refuses without it |

Codex and router flags:

| Flag | Values | Effect |
| --- | --- | --- |
| `--router-url` | `URL` | Local model router; only http://127.0.0.1 is accepted (default: http://127.0.0.1:18766/v1) |
| `--catalog` | `FILE` | Codex model catalog (default: models.json in CODEX_HOME, ~/.codex) |
| `--router-profile` | `FILE` | Codex config for the router (default: unified-openrouter.config.toml in CODEX_HOME) |
| `--capabilities` | `FILE` | Model capability registry (default: data/model-capabilities.json) |

`--reasoning` and its variants take `none`, `low`, `medium`, `high`, `xhigh` or `max`.

### run-profile

Read a world as a JSON profile describes, then admit it; rerunning resumes.

```sh
python3 scripts/evaluate.py run-profile profile [FLAGS]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `profile` | path | Evaluation profile JSON |
| `-n`, `--dry-run` | — | Rehearse with synthetic answers in a separate OUTPUT.dry-run; calls no model |
| `--accept-run` | — | Allow model calls, which cost money; live work refuses without it |

Also: `--project`, the Codex and router flags, and the budget flags. Any budget flag here replaces the profile's whole budget.

### run-profiles

Run several evaluation profiles at once, each as its own session and package.

```sh
python3 scripts/evaluate.py run-profiles profile [profile ...] [FLAGS]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `profile` | paths | Evaluation profile JSON; each may appear once |
| `--parallel` | `N` | Profiles to run at once, 1-16 (default: 4) |
| `-n`, `--dry-run` | — | Rehearse with synthetic answers; calls no model |
| `--accept-run` | — | Allow model calls, which cost money; live work refuses without it |

Also: `--project`. Each profile runs under its own budget.

### doctor-profile

Check a profile, Codex, the router, the catalog and reasoning support; calls no model.

```sh
python3 scripts/evaluate.py doctor-profile profile [FLAGS]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `profile` | path | Evaluation profile JSON |

Also: `--project` and the Codex and router flags.

### run-dream-profile

Generate a new world from a dream profile, read it and admit both; rerunning resumes.

```sh
python3 scripts/evaluate.py run-dream-profile profile [FLAGS]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `profile` | path | Dream profile JSON |
| `--runner-model` | `MODEL` | Model that creates the world (default: the profile's) |
| `--runner-reasoning` | effort | Runner's reasoning effort (default: the profile's) |
| `--scorer-model` | `MODEL` | Model that reads the world (default: the profile's) |
| `--scorer-reasoning` | effort | Scorer's reasoning effort (default: the profile's) |
| `--generation-attempts` | `N` | Generation attempts, 1-5; after a retryable failure the next is a fresh, full generation (default: 1) |
| `--repairs` | `N` | Repair turns per reading task after an invalid answer (default: the profile's, else 2) |
| `-n`, `--dry-run` | — | Rehearse the generation alone in OUTPUT.dry-run; calls no model |
| `--accept-run` | — | Allow model calls, which cost money; live work refuses without it |

Also: `--project`, the Codex and router flags, and the budget flags for the reading. Any budget flag here replaces the profile's `evaluation_budget`.

### doctor-dream-profile

Check a dream profile's runner and scorer, Codex and the router; calls no model.

```sh
python3 scripts/evaluate.py doctor-dream-profile profile [FLAGS]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `profile` | path | Dream profile JSON |
| `--runner-model` | `MODEL` | Check this runner model instead of the profile's |
| `--runner-reasoning` | effort | Check this runner reasoning effort instead |
| `--scorer-model` | `MODEL` | Check this scorer model instead of the profile's |
| `--scorer-reasoning` | effort | Check this scorer reasoning effort instead |

Also: `--project` and the Codex and router flags.

### generate-policy

Write the full-rubric scoring policy from data/rubric.json; calls no model.

```sh
python3 scripts/evaluate.py generate-policy --output FILE [FLAGS]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `--output` | `FILE`, required | Policy JSON to write |
| `--repetitions` | `N` | Independent sessions per judgment (default: 1) |
| `--minimum-agreement` | `SHARE` | Share of sessions that must agree on a level (default: 0.67) |

Also: `--project`.

### score

Read the evidence once per session, answer every judgment and aggregate them; rerunning resumes.

```sh
python3 scripts/evaluate.py score (--run-id RUN_ID | --extraction FILE) --policy FILE --output DIR --model MODEL [FLAGS]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `--run-id` | `RUN_ID` | World to read; the session extracts first, then judges |
| `--extraction` | `FILE` | Judge with this validated extraction.json instead |
| `--policy` | `FILE`, required | Scoring policy JSON |
| `--output` | `DIR`, required | Directory for the scoreflow; an existing one resumes |
| `--evaluation-id` | `ID` | Stable evaluation ID (default: evaluation-RUN_ID) |
| `--model` | `MODEL`, required | Model to read with, as the catalog names it |
| `--reasoning` | effort | Reasoning effort (default: high) |
| `--layout` | `compact`, `per-axis` | compact asks every axis in one turn; per-axis asks one turn each (default: compact) |
| `--repairs` | `N` | Repair turns per task after an invalid answer; invalid answers are kept (default: 2) |
| `--synthetic-outcomes` | `LIST` | Dry run only: comma-separated outcomes, one per repetition |
| `-n`, `--dry-run` | — | Rehearse with synthetic answers; calls no model |
| `--accept-run` | — | Allow model calls, which cost money; live work refuses without it |

Give exactly one of `--run-id` and `--extraction`. Also: `--project`, the Codex and router flags, and the budget flags.

Controls and trials, never admitted:

| Flag | Values | Effect |
| --- | --- | --- |
| `--control-seed` | `FILE` | Show the world with this seed text instead of its own |
| `--mask-terms` | `FILE` | JSON list of the seed's words to mask in the evidence |
| `--ablate` | `FILE` | JSON list of {path, start, end} line ranges to blank in the evidence |

### validate-evaluation

Validate a scoreflow output against its world; calls no model.

```sh
python3 scripts/evaluate.py validate-evaluation directory [--project DIR]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `directory` | path | Scoreflow output directory |

### recover-score

Finish a scoreflow whose sessions completed but whose record was not written; calls no model.

```sh
python3 scripts/evaluate.py recover-score directory [FLAGS]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `directory` | path | Scoreflow output directory without evaluation.json |
| `--evaluation-id` | `ID` | Stable evaluation ID (default: recovered-evaluation-RUN_ID) |

Also: `--project`.

### integrate-evaluation

Admit a validated evaluation as its own run package and rebuild the site.

```sh
python3 scripts/evaluate.py integrate-evaluation --evaluation DIR --review-id ID [FLAGS]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `--evaluation` | `DIR`, required | Scoreflow output directory |
| `--review-id` | `ID`, required | Stable ID for the review record |
| `--evaluation-run-id` | `ID` | ID of the run package (default: the evaluation ID) |
| `--run-label` | `TEXT` | Run label (default: the evaluation's) |
| `--run-title` | `TEXT` | Run title (default: the world's title · rubric reading) |
| `--reviewer` | `TEXT` | Reviewer name (default: MODEL · rubric session) |

Also: `--project`.

### run-evaluation

Extract, score, admit and rebuild for one world, every option on the command line; rerunning resumes.

```sh
python3 scripts/evaluate.py run-evaluation run_id --output DIR --model MODEL --evaluation-id ID --review-id ID [FLAGS]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `run_id` | ID | World to read |
| `--policy` | `FILE` | Custom full-rubric policy (default: generated from data/rubric.json) |
| `--output` | `DIR`, required | Directory for the evaluation; an existing one resumes |
| `--model` | `MODEL`, required | Model to read with, as the catalog names it |
| `--reasoning` | effort | Reasoning effort (default: high) |
| `--evaluation-id` | `ID`, required | Stable evaluation ID |
| `--evaluation-run-id` | `ID` | ID of the run package (default: the evaluation ID) |
| `--review-id` | `ID`, required | Stable ID for the review record |
| `--run-label` | `TEXT` | Run label (default: the evaluation's) |
| `--run-title` | `TEXT` | Run title (default: the world's title · rubric reading) |
| `--reviewer` | `TEXT` | Reviewer name (default: MODEL · rubric session) |
| `--repetitions` | `N` | Independent evidence sessions; more than one adds a per-axis quorum (default: 1) |
| `--minimum-agreement` | `SHARE` | Share of sessions that must agree on a level (default: 0.67) |
| `--repairs` | `N` | Repair turns per task after an invalid answer; invalid answers are kept (default: 2) |
| `--layout` | `compact`, `per-axis` | compact asks every axis in one turn; per-axis asks one turn each (default: compact) |
| `-n`, `--dry-run` | — | Rehearse with synthetic answers; calls no model |
| `--accept-run` | — | Allow model calls, which cost money; live work refuses without it |

Also: `--project`, the Codex and router flags, and the budget flags.

### prepare-page

Prepare presentation JSON and static HTML for an evaluation; calls no model.

```sh
python3 scripts/evaluate.py prepare-page --evaluation DIR --output DIR [FLAGS]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `--evaluation` | `DIR`, required | Scoreflow output directory |
| `--output` | `DIR`, required | New directory for the presentation |
| `--presentation-id` | `ID` | Stable presentation ID (default: EVALUATION_ID-presentation-v1) |

Also: `--project`.

### validate-presentation

Validate a prepared presentation and its manifest; calls no model.

```sh
python3 scripts/evaluate.py validate-presentation directory [--project DIR]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `directory` | path | Presentation directory |

### capture-artefact

Register a browser world and capture its screenshot in a sandboxed browser.

```sh
python3 scripts/evaluate.py capture-artefact run_id [FLAGS]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `run_id` | ID | World with a top-level index.html |
| `--description` | `TEXT` | Screenshot description (default: taken from the world's README) |

Also: `--project`. The capture needs Playwright.

### doctor

Check a campaign config, the study, Codex and the router; calls no model.

```sh
python3 scripts/evaluate.py doctor config [FLAGS]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `config` | path | Campaign config JSON |
| `-n`, `--dry-run` | — | Check the config and study only, not Codex or the router |

Also: `--project` and the Codex and router flags.

### plan

Freeze a campaign config into a new campaign directory; calls no model.

```sh
python3 scripts/evaluate.py plan config --output DIR [FLAGS]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `config` | path | Campaign config JSON |
| `--output` | `DIR`, required | New campaign directory |
| `-n`, `--dry-run` | — | Freeze a synthetic campaign; running it calls no model |

Also: `--project` and the Codex and router flags.

### run

Start a prepared campaign: generate and review every queued job.

```sh
python3 scripts/evaluate.py run campaign [--accept-run]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `campaign` | path | Campaign directory made by plan |
| `--accept-run` | — | Allow model calls, which cost money; a live campaign refuses without it |

### resume

Continue an interrupted campaign; jobs that have a receipt are not repeated.

```sh
python3 scripts/evaluate.py resume campaign [--accept-run]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `campaign` | path | Campaign directory made by plan |
| `--accept-run` | — | Allow model calls, which cost money; a live campaign refuses without it |

### status

Show a campaign's queue and job states; calls no model.

```sh
python3 scripts/evaluate.py status campaign
```

| Flag | Values | Effect |
| --- | --- | --- |
| `campaign` | path | Campaign directory |

### validate

Validate a campaign's report and bundles and count its reviews; calls no model.

```sh
python3 scripts/evaluate.py validate campaign
```

| Flag | Values | Effect |
| --- | --- | --- |
| `campaign` | path | Campaign directory |

### validate-contract

Validate a normalized evaluation contract directory; calls no model.

```sh
python3 scripts/evaluate.py validate-contract directory
```

| Flag | Values | Effect |
| --- | --- | --- |
| `directory` | path | Contract directory |

### admit

Admit a completed live campaign's bundles and reviews into the study.

```sh
python3 scripts/evaluate.py admit campaign [--project DIR]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `campaign` | path | Campaign directory |

### reconcile-generation

Admit a finished generation that failed only the after-process transport check, as a separate reconciled bundle.

```sh
python3 scripts/evaluate.py reconcile-generation job --cohort ID --label TEXT --title TEXT [FLAGS]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `job` | path | Campaign job directory (jobs/generation-NNNN) |
| `--cohort` | `ID`, required | Cohort whose prompt the generation received |
| `--label` | `TEXT`, required | Label for the reconciled run |
| `--title` | `TEXT`, required | Title for the reconciled run |
| `--verify-only` | — | Check the job and write nothing |

Also: `--project`.

### extract

Extract source-only evidence for one world in its own session (score extracts by itself).

```sh
python3 scripts/evaluate.py extract run_id --output DIR --model MODEL [FLAGS]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `run_id` | ID | World to read |
| `--output` | `DIR`, required | Directory for the extraction; an existing one resumes |
| `--extraction-id` | `ID` | Stable extraction ID (default: extraction-RUN_ID) |
| `--model` | `MODEL`, required | Model to read with, as the catalog names it |
| `--reasoning` | effort | Reasoning effort (default: high) |
| `--repairs` | `N` | Repair turns after an invalid answer; invalid answers are kept (default: 2) |
| `-n`, `--dry-run` | — | Rehearse with synthetic answers; calls no model |
| `--accept-run` | — | Allow model calls, which cost money; live work refuses without it |

Also: `--project`, the Codex and router flags, and the budget flags.

### validate-extraction

Validate an extraction record against its world; calls no model.

```sh
python3 scripts/evaluate.py validate-extraction extraction [--project DIR]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `extraction` | path | extraction.json to validate |

### recover-extraction

Finish an extraction whose session completed but whose record was not written; calls no model.

```sh
python3 scripts/evaluate.py recover-extraction directory [--project DIR]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `directory` | path | Extraction output directory |

## claude_dream.py

Dreams a new world with Claude Code, then packs, admits and, with `--read`, reads it. The session runs in an isolated home and world with a sandboxed shell and no network for its tools; canaries check that isolation before the dream begins.

```sh
python3 scripts/claude_dream.py COMMAND profile [FLAGS]
```

```sh
python3 scripts/claude_dream.py check examples/dream-profile-claude-v2.json                    # no model call
python3 scripts/claude_dream.py run examples/dream-profile-claude-v2.json --accept-run         # dream, pack and admit
python3 scripts/claude_dream.py run examples/dream-profile-claude-v2.json --accept-run --read  # and read the world
```

| Command | Effect |
| --- | --- |
| `check` | Check the profile, the cohort prompt, Claude Code and credentials; calls no model |
| `run` | Audit the sandbox, dream, pack and admit; with --read, also read the world |
| `pack` | Rebuild the bundle of a finished dream that is not yet admitted; calls no model |

| Flag | Values | Effect |
| --- | --- | --- |
| `profile` | path | Claude dream profile JSON |
| `--project` | `DIR` | Study directory (default: this repository) |
| `--accept-run` | — | Allow model calls, which cost money; run refuses without it |
| `--max-usd` | `USD` | Spending cap passed to Claude Code; required with ANTHROPIC_API_KEY, which bills per token |
| `--title` | `TEXT` | World's name when its own files give none |
| `--retry` | — | Set an unfinished previous attempt aside and dream again |
| `--no-admit` | — | Stop after packing the bundle |
| `--read` | — | Read the admitted world with the profile's reader (evaluate.py run-profile) |
| `--read-arg` | `ARG` | Pass one argument to the reading (evaluate.py run-profile); repeat for more, each in the = form: --read-arg=--flag=value |

Every command takes `profile` and `--project`; `pack` also takes `--title`, and the other flags belong to `run`.

`--read-arg` hands one argument to `evaluate.py run-profile`; the `=` form keeps a value that starts with `--` attached to its flag. Budget flags passed this way replace the reader's whole budget, so give all of them:

```sh
python3 scripts/claude_dream.py run examples/dream-profile-claude-v2.json --accept-run --read \
  --read-arg=--max-turns=9 --read-arg=--max-input-tokens=900000 --read-arg=--max-output-tokens=300000 \
  --read-arg=--max-seconds=2400 --read-arg=--max-usd=USD --read-arg=--price-input=USD \
  --read-arg=--price-cached-input=USD --read-arg=--price-output=USD
```

Credentials come from the environment and are never recorded:

| Variable | Effect |
| --- | --- |
| `CLAUDE_CODE_OAUTH_TOKEN` | Subscription token from `claude setup-token` |
| `CLAUDE_DREAM_TOKEN_FILE` | Private file (`chmod 600`) that holds that token |
| `ANTHROPIC_API_KEY` | Used when no token is set; it bills per token, so `run` then needs `--max-usd` |

## load_bearing.py

Declares, runs and summarizes load-bearing trials: does a reading's level rest on the evidence it cites? Trials run against a scratch study and are never admitted.

```sh
python3 scripts/load_bearing.py prepare draft --project DIR --output FILE
python3 scripts/load_bearing.py run plan --project DIR --output DIR --total-usd USD [--parallel N] [--accept-run] [--retry] -- TRIAL_BUDGET...
python3 scripts/load_bearing.py findings plan --output DIR
```

```sh
python3 scripts/load_bearing.py prepare DRAFT.json --project .local/scratch-study --output PLAN.json
python3 scripts/load_bearing.py run PLAN.json --project .local/scratch-study --output .local/load-bearing/OUT \
  --total-usd 1.00 --accept-run -- \
  --max-turns 9 --max-input-tokens 900000 --max-output-tokens 300000 --max-seconds 2400 \
  --max-usd 0.07 --price-input 0.10 --price-cached-input 0.002 --price-output 0.20
python3 scripts/load_bearing.py findings PLAN.json --output .local/load-bearing/OUT
```

| Command | Effect |
| --- | --- |
| `prepare` | Declare a plan: pin identities, derive ablations and draw shams; calls no model |
| `run` | Run every trial of a declared plan against a scratch study; each trial's budget follows a bare -- |
| `findings` | Recompute findings from retained trials; calls no model |

`prepare`:

| Flag | Values | Effect |
| --- | --- | --- |
| `draft` | path | Draft plan JSON |
| `--project` | `DIR`, required | Scratch study holding the reference readings |
| `--output` | `FILE`, required | New file for the declared plan; an existing one is never overwritten |

`run`:

| Flag | Values | Effect |
| --- | --- | --- |
| `plan` | path | Declared plan JSON |
| `--project` | `DIR`, required | Scratch study the trials read |
| `--output` | `DIR`, required | Directory for the trials and findings.json; rerunning resumes |
| `--parallel` | `N` | Trials to run at once (default: 6) |
| `--total-usd` | `USD`, required | Estimated US dollars to allow across every trial in the output, earlier attempts included |
| `--accept-run` | — | Allow model calls, which cost money; run refuses without it |
| `--retry` | — | Set unfinished trials aside as .attempt-N and run them again in fresh sessions |

`findings`:

| Flag | Values | Effect |
| --- | --- | --- |
| `plan` | path | Declared plan JSON |
| `--output` | `DIR`, required | Directory of the trials; findings.json is written there |

Everything after a bare `--` is each trial's budget, passed to `evaluate.py score`. Write it as `FLAG VALUE` pairs, not `FLAG=VALUE`: `--max-turns`, `--max-input-tokens`, `--max-output-tokens`, and `--max-usd` with `--price-input`, `--price-cached-input` and `--price-output`; `--max-seconds` is optional. `--max-usd` caps each trial and `--total-usd` caps them all: before a trial starts, the runner reserves its cap plus one turn's margin against the total.

## reading_controls.py

Prepares a scratch copy of the study, with the authored decoy world imported as the control run `control-decoy-gestures`. Readings against the copy never reach the study.

```sh
python3 scripts/reading_controls.py prepare OUT
```

```sh
python3 scripts/reading_controls.py prepare .local/scratch-study
python3 scripts/evaluate.py score --project .local/scratch-study --run-id control-decoy-gestures \
  --policy POLICY.json --model MODEL --output .local/evaluation-runs/control-decoy --accept-run \
  --max-turns 3 --max-input-tokens 300000 --max-output-tokens 120000
```

| Command | Effect |
| --- | --- |
| `prepare` | Copy the study into a new directory and import the decoy world; calls no model |

| Flag | Values | Effect |
| --- | --- | --- |
| `OUT` | path | New directory for the scratch study; it must not exist |

## record_consoles.py

Records the console worlds' answers for the static site, where nothing can run. Recording starts each world in the isolated local runner on macOS, plays `help`, its opening command and its shortcuts, and writes `data/recordings/RUN.json`. See [Previews](previews.md).

```sh
python3 scripts/record_consoles.py [--project DIR] [--list] [RUN_ID ...]
```

```sh
python3 scripts/record_consoles.py --list   # which worlds would be recorded; starts nothing
python3 scripts/record_consoles.py          # record every console world shown on the release page
python3 scripts/record_consoles.py RUN_ID   # record one world again
```

| Flag | Values | Effect |
| --- | --- | --- |
| `RUN_ID` | IDs | Console world to record (default: every one shown on the release page) |
| `--project` | `DIR` | Study directory (default: this repository) |
| `--list` | — | Print the console worlds that would be recorded and exit; starts nothing |

## atlas.py

Validates the study, builds its site into `dist/` and serves it locally. Validation and the build read the preserved worlds and never run them.

```sh
python3 scripts/atlas.py COMMAND [FLAGS]
```

```sh
python3 scripts/atlas.py validate
python3 scripts/atlas.py build
python3 scripts/atlas.py serve --previews
```

| Command | Effect |
| --- | --- |
| `validate` | Check source hashes, the rubric, citations and review lineage; writes nothing |
| `analyze` | Print descriptive metrics for every world as JSON |
| `build` | Build the site into dist/ |
| `serve` | Build the site and serve it on 127.0.0.1 until Ctrl-C |
| `add-review` | Add an exported review to the study; rebuild to show it |
| `select-review` | Select a review for display; rebuild to show it |

| Flag | Values | Effect |
| --- | --- | --- |
| `--view` | `release`, `full` | release applies data/presentation.json; full shows every run and reading (default: release) |
| `--target` | `home`, `pages` | home is the local site with native previews; pages is the static site (default: home) |
| `--port` | `PORT` | Port to listen on (default: 4317) |
| `--previews` | — | Run native worlds on request in the isolated local runner (macOS, home target) |
| `REVIEW_JSON` | path | Exported review file |
| `--select` | — | Select the review for display (a review that supersedes the selected one always is) |
| `REVIEW_ID` | ID | ID of a review already in the study |

`build` and `serve` take `--view` and `--target`; `serve` also takes `--port` and `--previews`. `add-review` takes `REVIEW_JSON` and `--select`, and `select-review` takes `REVIEW_ID`.

## bundles.py

Validates a Run Bundle v1 directory, or imports it into the study as unreviewed evidence, without executing it.

```sh
python3 scripts/bundles.py {validate,import} bundle [--project PROJECT]
```

| Flag | Values | Effect |
| --- | --- | --- |
| `validate` | — | Check the bundle's files, hashes and records |
| `import` | — | Validate the bundle and add it to the study |
| `bundle` | path | Run bundle directory |
| `--project` | `PROJECT` | Study directory (default: this repository) |

## pipeline.py

Prepares and runs local Codex generation/review campaigns, then admits their records. This is the older local-campaign path: `evaluate.py plan`, `run`, `resume`, `status`, `validate` and `admit` are the newer campaign commands, while `pipeline.py prepare`, `run` and `admit` freeze, execute and import a `pipeline.py` campaign. The `pipeline` npm script is this program. Nothing here executes an archived candidate.

```sh
python3 scripts/pipeline.py prepare config --output DIR [--project DIR] [--catalog FILE] [--router-profile FILE] [--capabilities FILE] [--router-url URL] [--dry-run]
python3 scripts/pipeline.py run CAMPAIGN [--accept-run]
python3 scripts/pipeline.py admit CAMPAIGN [--project DIR]
```

| Command | Effect |
| --- | --- |
| `prepare` | Freeze inputs and queue jobs into a new campaign directory; never calls a model |
| `run` | Execute or resume the queued jobs without repeating finished ones; calls a model only with `--accept-run` on a live campaign |
| `admit` | Import the campaign's retained bundles and reviews into the study; calls no model |

`prepare`:

| Flag | Values | Effect |
| --- | --- | --- |
| `config` | path | Campaign config JSON (positional, required) |
| `--output` | `DIR`, required | New campaign directory; it must not exist yet |
| `--project` | `DIR` | Study directory (default: this repository) |
| `--catalog` | `FILE` | Codex model catalog (default: models.json in CODEX_HOME, ~/.codex) |
| `--router-profile` | `FILE` | Codex config for the router (default: unified-openrouter.config.toml in CODEX_HOME) |
| `--capabilities` | `FILE` | Model capability registry (default: `data/model-capabilities.json`) |
| `--router-url` | `URL` | Local model router; only http://127.0.0.1 is accepted (default: http://127.0.0.1:18766/v1) |
| `--dry-run` | — | Freeze a synthetic campaign; running it calls no model |

`run`:

| Flag | Values | Effect |
| --- | --- | --- |
| `campaign` | path | Campaign directory made by `prepare` (positional, required) |
| `--accept-run` | — | Allow model calls, which cost money; a live campaign refuses without it |

`admit`:

| Flag | Values | Effect |
| --- | --- | --- |
| `campaign` | path | Finished campaign directory (positional, required) |
| `--project` | `DIR` | Study directory (default: this repository) |

`prepare --dry-run` and `run` without `--accept-run` call no model. Rerunning `run` on the same campaign resumes without repeating jobs that have a receipt. A retained receipt is reused only when its requested model, reasoning, time allowance, prompt and instruction identities still match the frozen plan; a stale receipt stops the resume with an actionable error. See [Pipeline](pipeline.md) for where this path fits and [Troubleshooting](troubleshooting.md) for resuming and budget failures.

## Safety

Live work is a model call: reading, scoring, extracting, generating a world or reviewing one. It costs money on the operator's own accounts, so it refuses without `--accept-run`. The commands that can call a model are `evaluate.py` `run-profile`, `run-profiles`, `run-dream-profile`, `score`, `run-evaluation`, `extract`, and `run` or `resume` on a live campaign; `pipeline.py run` on a live campaign; `claude_dream.py run`; and `load_bearing.py run`.

`-n`/`--dry-run` rehearses with synthetic answers and calls no model. A profile's rehearsal writes to `OUTPUT.dry-run` beside the live output, and nothing synthetic is admitted. One exception: `run-dream-profile --dry-run` rehearses the generation alone in `OUTPUT.dry-run`, not the whole dream-plus-reading path. `doctor-profile`, `doctor-dream-profile`, `doctor` and `claude_dream.py check` check a setup before a live run, also without a model call.

### Budget flags

A live reading needs a budget: `--max-turns`, `--max-input-tokens` and `--max-output-tokens` at least, from the flags or from the profile. Any budget flag on the command line replaces the profile's whole budget rather than adding to it. A budget below the planned minimum is refused before the first call.

| Flag | Values | Effect |
| --- | --- | --- |
| `--max-turns` | `N` | Turns to allow |
| `--max-input-tokens` | `N` | Input tokens to allow, cached ones included |
| `--max-output-tokens` | `N` | Output tokens to allow |
| `--max-seconds` | `N` | Seconds of turn time to allow |
| `--max-usd` | `USD` | Estimated US dollars to allow; needs all three --price-* flags |
| `--price-input` | `USD` | US dollars per million uncached input tokens, for the estimate |
| `--price-cached-input` | `USD` | US dollars per million cached input tokens, for the estimate |
| `--price-output` | `USD` | US dollars per million output tokens, for the estimate |

Work stops before the next turn once any limit is reached, so the turn in progress can pass it. The cost is estimated from the reported tokens and the three prices: uncached input × `--price-input`, plus cached input × `--price-cached-input`, plus output × `--price-output`, each per million tokens. It is the estimate the cap checks, not the provider's bill.

`claude_dream.py run --max-usd` is a different cap: Claude Code enforces it inside the dream session. `load_bearing.py run` takes `--total-usd` for all trials and the budget flags above, after `--`, for each one.

## Output

`evaluate.py` prints each command's result as one JSON document on stdout and its progress, messages and errors on stderr, so `> result.json` keeps only the result. The other programs print short progress lines and summaries on stdout; `atlas.py analyze` prints JSON and `record_consoles.py --list` prints one run ID per line. Errors always go to stderr.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success. |
| `1` | An error, explained on stderr. For `evaluate.py`, also a run that did not complete; its JSON on stdout says how far it got. |
| `2` | Invalid usage, such as an unknown flag or a missing argument; the usage line says which. |
| `130` | Interrupted with Ctrl-C. Preserved work stays where it is. |

`atlas.py serve` treats Ctrl-C as its normal stop and exits with `0`. `evaluate.py` treats a termination signal like Ctrl-C.

## Resuming

Rerunning a command with the same arguments continues it from what is preserved. In `run-profile`, `run-dream-profile`, `run-evaluation`, `score` and `extract`, a reading replays its preserved turns and continues from the first missing answer, and a run stopped by its budget continues with the same command and a larger budget. `resume` continues a campaign without repeating any job that has a receipt. `claude_dream.py run` reuses a finished dream, and `load_bearing.py run` keeps every finished trial.

An unfinished attempt is set aside only on request: `claude_dream.py run --retry` and `load_bearing.py run --retry` keep it as `.attempt-N` and start fresh sessions.

When every session of a reading has completed but its record was not written, `recover-score` finishes it without a model call; `recover-extraction` does the same for an extraction. A campaign refuses to continue once the pipeline code it froze has changed; plan a new one.
