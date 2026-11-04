# Commands

Nine programs in `scripts/` run the study: `evaluate.py`, `claude_dream.py`, `pipeline.py`, `load_bearing.py`, `gadfly_bridge.py`, `reading_controls.py`, `record_consoles.py`, `atlas.py` and `bundles.py`. Run each from the repository root as `python3 scripts/PROGRAM.py` with its arguments, where `python3` is Python 3.13 or later (`uv run python` gives you one). `--help` on a program or on any of its commands prints its usage and, for most programs, the text the tables below repeat.

Live work calls models, which cost money, and refuses without `--accept-run`; every other command calls no model. See [Safety](#safety) before a first live run, and [Pipeline](pipeline.md) for the order in which the commands are used.

---

### npm scripts

`npm run NAME` is a shorter way to run some of these. Flags after `--` pass through, as in `npm run evaluate -- status CAMPAIGN`.

| To | Run | Calls a model? |
| --- | --- | --- |
| Run `evaluate.py` with uv's Python | `npm run evaluate` | Only a live command with `--accept-run` |
| Run `claude_dream.py` with uv's Python | `npm run dream:claude` | Only `run --accept-run` |
| Check the DeepSeek evaluation profile: `evaluate.py doctor-profile` on `examples/evaluation-profile-deepseek.json` | `npm run doctor:deepseek` | No |
| Read with that profile: `evaluate.py run-profile` | `npm run evaluation:deepseek` | Only with `-- --accept-run` |
| Check the DeepSeek dream profile: `evaluate.py doctor-dream-profile` on `examples/dream-profile-deepseek.json` | `npm run doctor:deepseek-dream` | No |
| Dream and read with that profile: `evaluate.py run-dream-profile` | `npm run evaluation:deepseek-dream` | Only with `-- --accept-run` |
| Run `pipeline.py`'s campaign steps `prepare`, `run` and `admit`, as in `evaluate.py plan`, `run` and `admit` | `npm run pipeline` | Only `run --accept-run` on a live campaign |
| Build the local site into `dist/` (`atlas.py build`) | `npm run build` | No |
| Build the static site into `dist/` (`atlas.py build --target pages`) | `npm run build:pages` | No |
| Serve the static site on port 4318 (`atlas.py serve --target pages --port 4318`) | `npm run serve:pages` | No |
| Validate the study (`atlas.py validate`) | `npm run check` | No |
| Serve the local site with native previews on port 4317 (`atlas.py serve --previews`) | `npm run dev` or `npm run preview` | No |
| Serve it with the full view and native previews (`atlas.py serve --view full --previews`) | `npm run dev:full` | No |
| Run the Python unit tests | `npm test` | No |
| Build the full view, run `tests/ui-smoke.mjs`, then rebuild the release view | `npm run test:ui` | No |
| Build the site and run `tests/page-smoke.mjs` | `npm run test:page` | No |
| Build the static site, run `tests/release-smoke.mjs`, then rebuild the local site | `npm run test:release` | No |
| Run the release smoke test in WebKit and Firefox | `npm run test:engines` | No |
| Run `tests/preview-smoke.mjs` on macOS against a running `npm run dev:full` (`PREVIEW_URL`, default `http://127.0.0.1:4317`) | `npm run test:preview` | No |
| Format the JavaScript with Prettier | `npm run format` | No |
| Check the JavaScript formatting | `npm run format:check` | No |
| Run `format:check`, `test`, `check`, `build`, a contract validation, `test:ui`, `test:page` and `test:release` | `npm run verify` | No |

The browser tests need Node.js 22 and Playwright; see [Environment](environment.md#playwright-install). `test:engines` and `test:preview` are opt-in and are not part of `verify`.

---

### evaluate.py

Reads worlds against the rubric, dreams new ones and admits both, through Codex and the local model router. Each command prints its result as JSON on stdout. Every command has this shape:

```sh
python3 scripts/evaluate.py COMMAND [FLAGS]
```

Check a profile first without a model call, rehearse it with synthetic answers, then read the world, which calls a model:

```sh
python3 scripts/evaluate.py doctor-profile \
  examples/evaluation-profile-deepseek.json
python3 scripts/evaluate.py run-profile \
  examples/evaluation-profile-deepseek.json -n
python3 scripts/evaluate.py run-profile \
  examples/evaluation-profile-deepseek.json --accept-run
```

The commands are listed from most to least common; campaign commands come last, from `doctor` on.

#### Shared flags

These flags mean the same wherever they appear; each command's section says which it takes. Most commands take `--project`, and most that call a model also take `-n`/`--dry-run`, `--accept-run` and the [budget flags](#budget-flags).

| Argument | Effect |
| --- | --- |
| `--project DIR` | Study directory (default: this repository) |
| `-n`, `--dry-run` | Rehearse with synthetic answers; calls no model |
| `--accept-run` | Allow model calls, which cost money; live work refuses without it |

Commands that reach Codex take the Codex and router flags:

| Argument | Effect |
| --- | --- |
| `--router-url URL` | Local model router; only http://127.0.0.1 is accepted (default: http://127.0.0.1:18766/v1) |
| `--catalog FILE` | Codex model catalog (default: models.json in CODEX_HOME, ~/.codex) |
| `--router-profile FILE` | Codex config for the router (default: unified-openrouter.config.toml in CODEX_HOME) |
| `--capabilities FILE` | Model capability registry (default: data/model-capabilities.json) |

`--reasoning EFFORT` and its variants take `none`, `low`, `medium`, `high`, `xhigh` or `max`.

#### run-profile

Read a world as a JSON profile describes, then admit it:

```sh
python3 scripts/evaluate.py run-profile profile [FLAGS]
```

| Argument | Effect |
| --- | --- |
| `profile` | Evaluation profile JSON |
| `--repairs N` | Repair turns per reading task after an invalid answer, 0-4 (default: the profile's, else 2) |
| `--layout {compact,per-axis}` | compact asks every axis in one turn; per-axis asks one turn each (default: the profile's, else compact); a resume with another layout is refused |
| `-n`, `--dry-run` | Rehearse with synthetic answers in a separate `OUTPUT.dry-run`; calls no model |
| `--accept-run` | Allow model calls, which cost money; live work refuses without it |

Rerunning resumes. Also: `--project`, the Codex and router flags, and the budget flags; any budget flag here replaces the profile's whole budget.

#### run-profiles

Run several evaluation profiles at once, each as its own session and package:

```sh
python3 scripts/evaluate.py run-profiles \
  profile [profile ...] [FLAGS]
```

| Argument | Effect |
| --- | --- |
| `profile` | Evaluation profile JSON; each may appear once |
| `--parallel N` | Profiles to run at once, 1-16 (default: 4) |
| `-n`, `--dry-run` | Rehearse with synthetic answers; calls no model |
| `--accept-run` | Allow model calls, which cost money; live work refuses without it |

Also: `--project`. Each profile runs under its own budget.

#### doctor-profile

Check a profile, Codex, the router, the catalog and reasoning support, without calling a model:

```sh
python3 scripts/evaluate.py doctor-profile profile [FLAGS]
```

| Argument | Effect |
| --- | --- |
| `profile` | Evaluation profile JSON |

Also: `--project` and the Codex and router flags.

#### run-dream-profile

Generate a new world from a dream profile, read it and admit both:

```sh
python3 scripts/evaluate.py run-dream-profile profile [FLAGS]
```

| Argument | Effect |
| --- | --- |
| `profile` | Dream profile JSON |
| `--runner-model MODEL` | Model that creates the world (default: the profile's) |
| `--runner-reasoning EFFORT` | Runner's reasoning effort (default: the profile's) |
| `--scorer-model MODEL` | Model that reads the world (default: the profile's) |
| `--scorer-reasoning EFFORT` | Scorer's reasoning effort (default: the profile's) |
| `--generation-attempts N` | Generation attempts, 1-5; after a retryable failure the next is a fresh, full generation (default: 1) |
| `--repairs N` | Repair turns per reading task after an invalid answer, 0-4 (default: the profile's, else 2) |
| `-n`, `--dry-run` | Rehearse the generation alone in `OUTPUT.dry-run`; calls no model |
| `--accept-run` | Allow model calls, which cost money; live work refuses without it |

Rerunning resumes. Also: `--project`, the Codex and router flags, and the budget flags for the reading; any budget flag here replaces the profile's `evaluation_budget`.

#### doctor-dream-profile

Check a dream profile's runner and scorer, Codex and the router, without calling a model:

```sh
python3 scripts/evaluate.py doctor-dream-profile \
  profile [FLAGS]
```

| Argument | Effect |
| --- | --- |
| `profile` | Dream profile JSON |
| `--runner-model MODEL` | Check this runner model instead of the profile's |
| `--runner-reasoning EFFORT` | Check this runner reasoning effort instead |
| `--scorer-model MODEL` | Check this scorer model instead of the profile's |
| `--scorer-reasoning EFFORT` | Check this scorer reasoning effort instead |

Also: `--project` and the Codex and router flags.

#### generate-policy

Write the full-rubric scoring policy from data/rubric.json, without calling a model:

```sh
python3 scripts/evaluate.py generate-policy \
  --output FILE [FLAGS]
```

| Argument | Effect |
| --- | --- |
| `--output FILE` | Required. Policy JSON to write |
| `--repetitions N` | Independent sessions per judgment (default: 1) |
| `--minimum-agreement SHARE` | Share of sessions that must agree on a level (default: 0.67) |

Also: `--project`.

#### score

Read the evidence once per session, answer every judgment and aggregate them:

```sh
python3 scripts/evaluate.py score \
  (--run-id RUN_ID | --extraction FILE) \
  --policy FILE --output DIR --model MODEL [FLAGS]
```

| Argument | Effect |
| --- | --- |
| `--run-id RUN_ID` | World to read; the session extracts first, then judges |
| `--extraction FILE` | Judge with this validated extraction.json instead |
| `--policy FILE` | Required. Scoring policy JSON |
| `--output DIR` | Required. Directory for the scoreflow; an existing one resumes |
| `--evaluation-id ID` | Stable evaluation ID (default: evaluation-RUN_ID) |
| `--model MODEL` | Required. Model to read with, as the catalog names it |
| `--reasoning EFFORT` | Reasoning effort (default: high) |
| `--layout {compact,per-axis}` | compact asks every axis in one turn; per-axis asks one turn each (default: compact) |
| `--repairs N` | Repair turns per task after an invalid answer, 0-4; invalid answers are kept (default: 2) |
| `--synthetic-outcomes LIST` | Dry run only: comma-separated outcomes, one per repetition |
| `-n`, `--dry-run` | Rehearse with synthetic answers; calls no model |
| `--accept-run` | Allow model calls, which cost money; live work refuses without it |

Give exactly one of `--run-id` and `--extraction`. Rerunning resumes. Also: `--project`, the Codex and router flags, and the budget flags.

Controls and trials take these flags, and are never admitted:

| Argument | Effect |
| --- | --- |
| `--control-seed FILE` | Show the world with this seed text instead of its own |
| `--mask-terms FILE` | JSON list of the seed's words to mask in the evidence |
| `--ablate FILE` | JSON list of {path, start, end} line ranges to blank in the evidence |

#### validate-evaluation

Validate a scoreflow output against its world, without calling a model:

```sh
python3 scripts/evaluate.py validate-evaluation \
  directory [--project DIR]
```

| Argument | Effect |
| --- | --- |
| `directory` | Scoreflow output directory |

#### recover-score

Finish a scoreflow whose sessions completed but whose record was not written, without calling a model:

```sh
python3 scripts/evaluate.py recover-score directory [FLAGS]
```

| Argument | Effect |
| --- | --- |
| `directory` | Scoreflow output directory without evaluation.json |
| `--evaluation-id ID` | Stable evaluation ID (default: recovered-evaluation-RUN_ID) |

Also: `--project`.

#### integrate-evaluation

Admit a validated evaluation as its own run package and rebuild the site:

```sh
python3 scripts/evaluate.py integrate-evaluation \
  --evaluation DIR --review-id ID [FLAGS]
```

| Argument | Effect |
| --- | --- |
| `--evaluation DIR` | Required. Scoreflow output directory |
| `--review-id ID` | Required. Stable ID for the review record |
| `--evaluation-run-id ID` | ID of the run package (default: the evaluation ID) |
| `--run-label TEXT` | Run label (default: the evaluation's) |
| `--run-title TEXT` | Run title (default: the world's title · rubric reading) |
| `--reviewer TEXT` | Reviewer name (default: MODEL · rubric session) |

Also: `--project`.

#### run-evaluation

Extract, score, admit and rebuild for one world, with every option on the command line:

```sh
python3 scripts/evaluate.py run-evaluation run_id \
  --output DIR --model MODEL \
  --evaluation-id ID --review-id ID [FLAGS]
```

| Argument | Effect |
| --- | --- |
| `run_id` | World to read |
| `--policy FILE` | Custom full-rubric policy (default: generated from data/rubric.json) |
| `--output DIR` | Required. Directory for the evaluation; an existing one resumes |
| `--model MODEL` | Required. Model to read with, as the catalog names it |
| `--reasoning EFFORT` | Reasoning effort (default: high) |
| `--evaluation-id ID` | Required. Stable evaluation ID |
| `--evaluation-run-id ID` | ID of the run package (default: the evaluation ID) |
| `--review-id ID` | Required. Stable ID for the review record |
| `--run-label TEXT` | Run label (default: the evaluation's) |
| `--run-title TEXT` | Run title (default: the world's title · rubric reading) |
| `--reviewer TEXT` | Reviewer name (default: MODEL · rubric session) |
| `--repetitions N` | Independent evidence sessions; more than one adds a per-axis quorum (default: 1) |
| `--minimum-agreement SHARE` | Share of sessions that must agree on a level (default: 0.67) |
| `--repairs N` | Repair turns per task after an invalid answer, 0-4; invalid answers are kept (default: 2) |
| `--layout {compact,per-axis}` | compact asks every axis in one turn; per-axis asks one turn each (default: compact) |
| `-n`, `--dry-run` | Rehearse with synthetic answers; calls no model |
| `--accept-run` | Allow model calls, which cost money; live work refuses without it |

Rerunning resumes. Also: `--project`, the Codex and router flags, and the budget flags.

#### prepare-page

Prepare presentation JSON and static HTML for an evaluation, without calling a model:

```sh
python3 scripts/evaluate.py prepare-page \
  --evaluation DIR --output DIR [FLAGS]
```

| Argument | Effect |
| --- | --- |
| `--evaluation DIR` | Required. Scoreflow output directory |
| `--output DIR` | Required. New directory for the presentation |
| `--presentation-id ID` | Stable presentation ID (default: EVALUATION_ID-presentation-v1) |

Also: `--project`.

#### validate-presentation

Validate a prepared presentation and its manifest, without calling a model:

```sh
python3 scripts/evaluate.py validate-presentation \
  directory [--project DIR]
```

| Argument | Effect |
| --- | --- |
| `directory` | Presentation directory |

#### capture-artefact

Register a browser world and capture its screenshot in a sandboxed browser:

```sh
python3 scripts/evaluate.py capture-artefact run_id [FLAGS]
```

| Argument | Effect |
| --- | --- |
| `run_id` | World with a top-level index.html |
| `--description TEXT` | Screenshot description (default: taken from the world's README) |

Also: `--project`. The capture needs Playwright.

#### doctor

Check a campaign config, the study, Codex and the router, without calling a model:

```sh
python3 scripts/evaluate.py doctor config [FLAGS]
```

| Argument | Effect |
| --- | --- |
| `config` | Campaign config JSON |
| `-n`, `--dry-run` | Check the config and study only, not Codex or the router |

Also: `--project` and the Codex and router flags.

#### plan

Freeze a campaign config into a new campaign directory, without calling a model:

```sh
python3 scripts/evaluate.py plan config --output DIR [FLAGS]
```

| Argument | Effect |
| --- | --- |
| `config` | Campaign config JSON |
| `--output DIR` | Required. New campaign directory |
| `-n`, `--dry-run` | Freeze a synthetic campaign; running it calls no model |

Also: `--project` and the Codex and router flags.

#### run

Start a prepared campaign, generating and reviewing every queued job:

```sh
python3 scripts/evaluate.py run campaign [--accept-run]
```

| Argument | Effect |
| --- | --- |
| `campaign` | Campaign directory made by plan |
| `--accept-run` | Allow model calls, which cost money; a live campaign refuses without it |

#### resume

Continue an interrupted campaign without repeating any job that has a receipt:

```sh
python3 scripts/evaluate.py resume campaign [--accept-run]
```

| Argument | Effect |
| --- | --- |
| `campaign` | Campaign directory made by plan |
| `--accept-run` | Allow model calls, which cost money; a live campaign refuses without it |

#### status

Show a campaign's queue and job states, without calling a model:

```sh
python3 scripts/evaluate.py status campaign
```

| Argument | Effect |
| --- | --- |
| `campaign` | Campaign directory |

#### validate

Validate a campaign's report and bundles and count its reviews, without calling a model:

```sh
python3 scripts/evaluate.py validate campaign
```

| Argument | Effect |
| --- | --- |
| `campaign` | Campaign directory |

#### validate-contract

Validate a normalized evaluation contract directory, without calling a model:

```sh
python3 scripts/evaluate.py validate-contract directory
```

| Argument | Effect |
| --- | --- |
| `directory` | Contract directory |

#### admit

Admit a completed live campaign's bundles and reviews into the study:

```sh
python3 scripts/evaluate.py admit campaign [--project DIR]
```

| Argument | Effect |
| --- | --- |
| `campaign` | Campaign directory |

#### reconcile-generation

Admit a finished generation that failed only the after-process transport check, as a separate reconciled bundle:

```sh
python3 scripts/evaluate.py reconcile-generation job \
  --cohort ID --label TEXT --title TEXT [FLAGS]
```

| Argument | Effect |
| --- | --- |
| `job` | Campaign job directory (jobs/generation-NNNN) |
| `--cohort ID` | Required. Cohort whose prompt the generation received |
| `--label TEXT` | Required. Label for the reconciled run |
| `--title TEXT` | Required. Title for the reconciled run |
| `--verify-only` | Check the job and write nothing |

Also: `--project`.

#### extract

Extract source-only evidence for one world in its own session (`score` extracts by itself):

```sh
python3 scripts/evaluate.py extract run_id \
  --output DIR --model MODEL [FLAGS]
```

| Argument | Effect |
| --- | --- |
| `run_id` | World to read |
| `--output DIR` | Required. Directory for the extraction; an existing one resumes |
| `--extraction-id ID` | Stable extraction ID (default: extraction-RUN_ID) |
| `--model MODEL` | Required. Model to read with, as the catalog names it |
| `--reasoning EFFORT` | Reasoning effort (default: high) |
| `--repairs N` | Repair turns after an invalid answer, 0-4; invalid answers are kept (default: 2) |
| `-n`, `--dry-run` | Rehearse with synthetic answers; calls no model |
| `--accept-run` | Allow model calls, which cost money; live work refuses without it |

Also: `--project`, the Codex and router flags, and the budget flags.

#### validate-extraction

Validate an extraction record against its world, without calling a model:

```sh
python3 scripts/evaluate.py validate-extraction \
  extraction [--project DIR]
```

| Argument | Effect |
| --- | --- |
| `extraction` | extraction.json to validate |

#### recover-extraction

Finish an extraction whose session completed but whose record was not written, without calling a model:

```sh
python3 scripts/evaluate.py recover-extraction \
  directory [--project DIR]
```

| Argument | Effect |
| --- | --- |
| `directory` | Extraction output directory |

---

### claude_dream.py

Dreams a new world with Claude Code, then packs, admits and, with `--read`, reads it. The session runs in an isolated home and world with a sandboxed shell and no network for its tools; canaries check that isolation before the dream begins. Every command takes a profile:

```sh
python3 scripts/claude_dream.py COMMAND profile [FLAGS]
```

Check the profile first; this calls no model:

```sh
python3 scripts/claude_dream.py check \
  examples/dream-profile-claude-v2.json
```

Then dream, pack and admit, which calls models; the second form also reads the world:

```sh
python3 scripts/claude_dream.py run \
  examples/dream-profile-claude-v2.json --accept-run
python3 scripts/claude_dream.py run \
  examples/dream-profile-claude-v2.json --accept-run --read
```

| To | Run |
| --- | --- |
| Check the profile, the cohort prompt, Claude Code and credentials; calls no model | `check` |
| Audit the sandbox, dream, pack and admit; with `--read`, also read the world | `run` |
| Rebuild the bundle of a finished dream that is not yet admitted; calls no model | `pack` |

Every command takes `profile` and `--project`; `pack` also takes `--title`, and the other flags belong to `run`.

| Argument | Effect |
| --- | --- |
| `profile` | Claude dream profile JSON |
| `--project DIR` | Study directory (default: this repository) |
| `--accept-run` | Allow model calls, which cost money; `run` refuses without it |
| `--max-usd USD` | Spending cap passed to Claude Code; required with ANTHROPIC_API_KEY, which bills per token |
| `--title TEXT` | World's name when its own files give none |
| `--retry` | Set an unfinished previous attempt aside and dream again |
| `--no-admit` | Stop after packing the bundle |
| `--read` | Read the admitted world with the profile's reader (`evaluate.py run-profile`) |
| `--read-arg ARG` | Pass one argument to the reading (`evaluate.py run-profile`); repeat for more, each in the = form: `--read-arg=--flag=value` |

`--read-arg` hands one argument to `evaluate.py run-profile`; the `=` form keeps a value that starts with `--` attached to its flag. Budget flags passed this way replace the reader's whole budget, so give all of them:

```sh
python3 scripts/claude_dream.py run \
  examples/dream-profile-claude-v2.json --accept-run --read \
  --read-arg=--max-turns=9 \
  --read-arg=--max-input-tokens=900000 \
  --read-arg=--max-output-tokens=300000 \
  --read-arg=--max-seconds=2400 \
  --read-arg=--max-usd=USD \
  --read-arg=--price-input=USD \
  --read-arg=--price-cached-input=USD \
  --read-arg=--price-output=USD
```

Credentials come from the environment and are never recorded:

- **`CLAUDE_CODE_OAUTH_TOKEN`:** a subscription token from `claude setup-token`.
- **`CLAUDE_DREAM_TOKEN_FILE`:** a private file (`chmod 600`) that holds that token.
- **`ANTHROPIC_API_KEY`:** used when no token is set. It bills per token, so `run` then needs `--max-usd`.

---

### load_bearing.py

Declares, runs and summarizes load-bearing trials: does a reading's level rest on the evidence it cites? Trials run against a scratch study and are never admitted. The three commands take these forms:

```sh
python3 scripts/load_bearing.py prepare draft \
  --project DIR --output FILE
python3 scripts/load_bearing.py run plan \
  --project DIR --output DIR --total-usd USD \
  [--parallel N] [--accept-run] [--retry] -- TRIAL_BUDGET...
python3 scripts/load_bearing.py findings plan --output DIR
```

For example, declare a plan, run its trials under a one-dollar total cap, which calls a model, and recompute the findings:

```sh
python3 scripts/load_bearing.py prepare DRAFT.json \
  --project .local/scratch-study --output PLAN.json
python3 scripts/load_bearing.py run PLAN.json \
  --project .local/scratch-study \
  --output .local/load-bearing/OUT \
  --total-usd 1.00 --accept-run -- \
  --max-turns 9 --max-input-tokens 900000 \
  --max-output-tokens 300000 --max-seconds 2400 \
  --max-usd 0.07 --price-input 0.10 \
  --price-cached-input 0.002 --price-output 0.20
python3 scripts/load_bearing.py findings PLAN.json \
  --output .local/load-bearing/OUT
```

| To | Run |
| --- | --- |
| Declare a plan: pin identities, derive ablations and draw shams; calls no model | `prepare` |
| Run every trial of a declared plan against a scratch study; each trial's budget follows a bare `--` | `run` |
| Recompute findings from retained trials; calls no model | `findings` |

`prepare`:

| Argument | Effect |
| --- | --- |
| `draft` | Draft plan JSON |
| `--project DIR` | Required. Scratch study holding the reference readings |
| `--output FILE` | Required. New file for the declared plan; an existing one is never overwritten |

`run`:

| Argument | Effect |
| --- | --- |
| `plan` | Declared plan JSON |
| `--project DIR` | Required. Scratch study the trials read |
| `--output DIR` | Required. Directory for the trials and findings.json; rerunning resumes |
| `--parallel N` | Trials to run at once (default: 6) |
| `--total-usd USD` | Required. Estimated US dollars to allow across every trial in the output, earlier attempts included |
| `--accept-run` | Allow model calls, which cost money; run refuses without it |
| `--retry` | Set unfinished trials aside as .attempt-N and run them again in fresh sessions |

`findings`:

| Argument | Effect |
| --- | --- |
| `plan` | Declared plan JSON |
| `--output DIR` | Required. Directory of the trials; findings.json is written there |

Everything after a bare `--` is each trial's budget, passed to `evaluate.py score`:

- **Form:** `FLAG VALUE` pairs, not `FLAG=VALUE`.
- **Flags:** `--max-turns`, `--max-input-tokens`, `--max-output-tokens`, and `--max-usd` with `--price-input`, `--price-cached-input` and `--price-output`; `--max-seconds` is optional.
- **Caps:** `--max-usd` caps each trial and `--total-usd` caps them all. Before a trial starts, the runner reserves its cap plus one turn's margin against the total; a trial that does not fit waits for running trials to settle, and is skipped only when none is running.

---

### gadfly_bridge.py

Builds, plans, runs and reports Gadfly checks on the levels the release page shows: does a published level rest on the lines its sessions cite? Each call is one session, by the study's v5 reader or by a second reader the round names, kept under the round's folder and never admitted. [Gadfly checks](pipeline.md#gadfly-checks) describes the method.

The five commands take these forms:

```sh
python3 scripts/gadfly_bridge.py build spec --output DIR \
  [--router-revision HEX|live] [--router-url URL] [--dry-run]
python3 scripts/gadfly_bridge.py plan DIR [--frame NAME]
python3 scripts/gadfly_bridge.py run DIR \
  (--dry-run | --accept-run [DOLLAR_FLAGS]) \
  [--settle] [--frame NAME] [--only TRIAL]
python3 scripts/gadfly_bridge.py report DIR \
  [--spec FILE] [--dry-run] [--frame NAME]
python3 scripts/gadfly_bridge.py stats DIR \
  [--pool DIR] [--resamples N] [--dry-run] [--frame NAME]
```

A live round starts from a new spec that names the pinned midwife release and holds its predictions. For example, build such a spec against the live router, run it under a one-dollar total cap, which calls a model, and report it:

```sh
python3 scripts/gadfly_bridge.py build \
  examples/gadfly/SPEC.json \
  --output .local/gadfly/ROUND --router-revision live
python3 scripts/gadfly_bridge.py run \
  .local/gadfly/ROUND --accept-run --total-usd 1.00 \
  --max-usd-per-call 0.03 --price-input 0.10 \
  --price-cached-input 0.002 --price-output 0.20
python3 scripts/gadfly_bridge.py report \
  .local/gadfly/ROUND \
  --spec examples/gadfly/SPEC.json
```

| To | Run |
| --- | --- |
| Build a round's frames from its spec into a new folder; calls no model | `build` |
| Print each frame's trials and calls; calls no model | `plan` |
| Read every planned trial, one session per call; resumes from kept records | `run` |
| Read the verdicts from kept records, with `--spec` the predictions beside them; calls no model | `report` |
| Say how firm each trial and verdict is over kept records, pooling another round's identical calls; calls no model | `stats` |

`build`:

| Argument | Effect |
| --- | --- |
| `spec` | Round spec, such as `examples/gadfly/round-1.json` |
| `--output DIR` | Required. New folder for the frames and `round.json` |
| `--router-revision HEX` | Pin this router revision in every frame, or `live` to read it from the router; a live run by the study's reader or through the router needs it |
| `--router-url URL` | Router to ask for `live` (default: `http://127.0.0.1:18766/v1`) |
| `--dry-run` | Build for dry runs only; `run` then refuses the round live. Also takes a spec that names another midwife release, such as one that has run |

`plan` and `report`:

| Argument | Effect |
| --- | --- |
| `DIR` | A built round |
| `--frame NAME` | Only this frame; may repeat |
| `--spec FILE` | `report` only: the round spec, to show its predictions beside the levels |
| `--dry-run` | `report` only: read the dry run's records |

`run`:

| Argument | Effect |
| --- | --- |
| `DIR` | A built round |
| `--dry-run` | Synthetic sessions; no model call |
| `--accept-run` | Allow model calls, which cost money; a live run refuses without it |
| `--settle` | Make no more calls for a trial whose level can no longer change; the verdict reads the same |
| `--total-usd USD` | Estimated dollars allowed across the round's calls, earlier calls included |
| `--max-usd-per-call USD` | Cap for one session by the study's reader, needed only when the round has such frames; such a call starts only when the cap and one turn's margin fit the total |
| `--price-input USD`, `--price-cached-input USD`, `--price-output USD` | The reader's prices in US dollars per million tokens, for the caps |
| `--max-output-tokens N` | Output cap per call, reasoning included (default: 100000 for the study's reader, 32000 through the router, 512 for eGemma) |
| `--max-input-tokens N` | Input cap per call by the study's reader (default: 400000) |
| `--max-seconds N` | Optional time cap per call by the study's reader |
| `--router-url URL` | Router for the study's reader and for frames read through the router (default: `http://127.0.0.1:18766/v1`) |
| `--egemma-url URL` | eGemma for frames read by Gemma 4 (default: `http://127.0.0.1:8000`) |
| `--egemma-timeout SECONDS` | Time for one eGemma reading (default: 600) |
| `--unified-timeout SECONDS` | Time for one reading through the router (default: 900) |
| `--frame NAME` | Only this frame; may repeat |
| `--only TRIAL` | Only this trial, such as `baseline`; may repeat |

What a live run needs depends on the frame's reader:

- **Dollar flags:** `--total-usd` and the three prices are needed when a frame is read by the study's own reader or through the router, and `--max-usd-per-call` only for the study's reader. Through the router, a call starts only when the most it can cost, its whole input and a full answer, fits the total.
- **Router revision:** a frame read by the study's reader or through the router pins the router's revision (`build --router-revision live`), and a live run refuses it at another one.
- **eGemma:** a frame read by eGemma needs `--accept-run` and the eGemma key in `EGEMMA_API_KEY` or `EGEMMA_API_KEY_FILE`.

`report` writes `report-midwife-VERSION.json` (or `dry-report-midwife-VERSION.json`) in the round's folder, named by the midwife release whose rules read the records. A later release reads the same records into a file of its own, without a call.

`stats` needs a midwife with `midwife.stats`, which 0.8.0 on PyPI lacks. Nothing it reports changes a verdict:

| Argument | Effect |
| --- | --- |
| `DIR` | A built round with kept records |
| `--pool DIR` | Another round whose calls on this frame's exact prompts count too, by the same reader; may repeat |
| `--resamples N` | Bootstrap draws of each verdict (default: 200) |
| `--dry-run` | Read the dry run's records |
| `--frame NAME` | Only this frame; may repeat |

It prints, per frame, the reader's agreement, the chance a trial reaches a level at the round's quorum, each trial's answers with a 95% interval, Fisher's p against the baseline with Holm's adjustment, and how often the verdict comes out the same when the calls are drawn again. It writes `stats-midwife-VERSION.json` beside the report.

---

### reading_controls.py

Prepares a scratch copy of the study, with the authored decoy world imported as the control run `control-decoy-gestures`. Nothing a reading against the copy produces enters the study unless it is later admitted on its own.

`prepare` copies the study into a new directory and imports the decoy world, without calling a model:

```sh
python3 scripts/reading_controls.py prepare OUT
```

| Argument | Effect |
| --- | --- |
| `OUT` | New directory for the scratch study; it must not exist |

For example, make a scratch study, then read the decoy there, which calls a model:

```sh
python3 scripts/reading_controls.py prepare \
  .local/scratch-study
python3 scripts/evaluate.py score \
  --project .local/scratch-study \
  --run-id control-decoy-gestures \
  --policy POLICY.json --model MODEL \
  --output .local/evaluation-runs/control-decoy \
  --accept-run --max-turns 3 --max-input-tokens 300000 \
  --max-output-tokens 120000
```

---

### record_consoles.py

Records the console worlds' answers for the static site, where nothing can run. Recording starts each world in the isolated local runner on macOS, plays `help`, its opening command and its shortcuts, and writes `data/recordings/RUN.json`. See [Previews](previews.md).

It takes no command, only optional run IDs and flags:

```sh
python3 scripts/record_consoles.py \
  [--project DIR] [--list] [RUN_ID ...]
```

| Argument | Effect |
| --- | --- |
| `RUN_ID` | Console world to record (default: every one shown on the release page) |
| `--project DIR` | Study directory (default: this repository) |
| `--list` | Print the console worlds that would be recorded and exit; starts nothing |

The three common uses:

| To | Run |
| --- | --- |
| See which worlds would be recorded, starting nothing | `python3 scripts/record_consoles.py --list` |
| Record every console world shown on the release page | `python3 scripts/record_consoles.py` |
| Record one world again | `python3 scripts/record_consoles.py RUN_ID` |

---

### atlas.py

Validates the study, builds its site into `dist/` and serves it locally. Validation and the build read the preserved worlds and never run them. Every command has this shape:

```sh
python3 scripts/atlas.py COMMAND [FLAGS]
```

For example, validate the study, build the site, then serve it with native previews:

```sh
python3 scripts/atlas.py validate
python3 scripts/atlas.py build
python3 scripts/atlas.py serve --previews
```

| To | Run |
| --- | --- |
| Check source hashes, the rubric, citations and review lineage; writes nothing | `validate` |
| Print descriptive metrics for every world as JSON | `analyze` |
| Build the site into dist/ | `build` |
| Build the site and serve it on 127.0.0.1 until Ctrl-C | `serve` |
| Add an exported review to the study; rebuild to show it | `add-review` |
| Select a review for display; rebuild to show it | `select-review` |

The commands that take arguments:

| Argument | Taken by | Effect |
| --- | --- | --- |
| `--view {release,full}` | `build`, `serve` | release applies data/presentation.json; full shows every run and reading (default: release) |
| `--target {home,pages}` | `build`, `serve` | home is the local site with native previews; pages is the static site (default: home) |
| `--port PORT` | `serve` | Port to listen on (default: 4317) |
| `--previews` | `serve` | Run native worlds on request in the isolated local runner (macOS, home target) |
| `REVIEW_JSON` | `add-review` | Exported review file |
| `--select` | `add-review` | Select the review for display (a review that supersedes the selected one always is) |
| `REVIEW_ID` | `select-review` | ID of a review already in the study |

---

### bundles.py

Validates a Run Bundle v1 directory, or imports it into the study as unreviewed evidence, without executing it. Its command is the first argument:

```sh
python3 scripts/bundles.py {validate,import} bundle \
  [--project PROJECT]
```

| To | Run |
| --- | --- |
| Check the bundle's files, hashes and records | `validate` |
| Validate the bundle and add it to the study | `import` |

Both take the same arguments:

| Argument | Effect |
| --- | --- |
| `bundle` | Run bundle directory |
| `--project PROJECT` | Study directory that `import` adds to (default: this repository) |

---

### pipeline.py

Prepares and runs local Codex generation/review campaigns, then admits their records. The `pipeline` npm script is this program, and nothing here executes an archived candidate.

This is the older local-campaign path: `evaluate.py plan`, `run`, `resume`, `status`, `validate` and `admit` are the newer campaign commands, while `pipeline.py prepare`, `run` and `admit` freeze, execute and import a `pipeline.py` campaign. The three commands take these forms:

```sh
python3 scripts/pipeline.py prepare config --output DIR \
  [--project DIR] [--catalog FILE] [--router-profile FILE] \
  [--capabilities FILE] [--router-url URL] [--dry-run]
python3 scripts/pipeline.py run CAMPAIGN [--accept-run]
python3 scripts/pipeline.py admit CAMPAIGN [--project DIR]
```

| To | Run |
| --- | --- |
| Freeze inputs and queue jobs into a new campaign directory; never calls a model | `prepare` |
| Execute or resume the queued jobs without repeating finished ones; calls a model only with `--accept-run` on a live campaign | `run` |
| Import the campaign's retained bundles and reviews into the study; calls no model | `admit` |

`prepare`:

| Argument | Effect |
| --- | --- |
| `config` | Required. Campaign config JSON |
| `--output DIR` | Required. New campaign directory; it must not exist yet |
| `--project DIR` | Study directory (default: this repository) |
| `--catalog FILE` | Codex model catalog (default: models.json in CODEX_HOME, ~/.codex) |
| `--router-profile FILE` | Codex config for the router (default: unified-openrouter.config.toml in CODEX_HOME) |
| `--capabilities FILE` | Model capability registry (default: `data/model-capabilities.json`) |
| `--router-url URL` | Local model router; only http://127.0.0.1 is accepted (default: http://127.0.0.1:18766/v1) |
| `--dry-run` | Freeze a synthetic campaign; running it calls no model |

`run`:

| Argument | Effect |
| --- | --- |
| `campaign` | Required. Campaign directory made by `prepare` |
| `--accept-run` | Allow model calls, which cost money; a live campaign refuses without it |

`admit`:

| Argument | Effect |
| --- | --- |
| `campaign` | Required. Finished live campaign directory; a synthetic one is refused |
| `--project DIR` | Study directory (default: this repository) |

How a campaign behaves when you run it again:

- **No model call:** `prepare --dry-run`, and `run` without `--accept-run`.
- **Resuming:** rerunning `run` on the same campaign does not repeat jobs that have a receipt.
- **Stale receipts:** a retained receipt is reused only when its requested job, model, reasoning, time allowance, prompt, mode and instruction identities still match the frozen plan. A stale receipt stops the resume with an actionable error.

The Pipeline page shows where this path fits, and [Troubleshooting](troubleshooting.md) covers resuming and budget failures.

---

### Safety

Live work is a model call: reading, scoring, extracting, generating a world or reviewing one. These commands can call a model:

- `evaluate.py` `run-profile`, `run-profiles`, `run-dream-profile`, `score`, `run-evaluation` and `extract`, and `run` or `resume` on a live campaign
- `pipeline.py run` on a live campaign
- `claude_dream.py run`
- `load_bearing.py run`
- `gadfly_bridge.py run` with `--accept-run`

> [!IMPORTANT]
> Live work costs money on the operator's own accounts, so it refuses without `--accept-run`.

Two kinds of command prepare a live run without calling a model:

- **Rehearsals:** `-n`/`--dry-run` rehearses with synthetic answers. A profile's rehearsal writes to `OUTPUT.dry-run` beside the live output, and nothing synthetic is admitted. One exception: `run-dream-profile --dry-run` rehearses the generation alone in `OUTPUT.dry-run`, not the whole dream-plus-reading path.
- **Checks:** `doctor-profile`, `doctor-dream-profile`, `doctor` and `claude_dream.py check` check a setup before a live run.

#### Budget flags

A live reading needs a budget: `--max-turns`, `--max-input-tokens` and `--max-output-tokens` at least, from the flags or from the profile. Any budget flag on the command line replaces the profile's whole budget rather than adding to it, so give those three together, and all three prices or none. A budget below the planned minimum is refused before the first call.

| Argument | Effect |
| --- | --- |
| `--max-turns N` | Turns to allow |
| `--max-input-tokens N` | Input tokens to allow, cached ones included |
| `--max-output-tokens N` | Output tokens to allow |
| `--max-seconds N` | Seconds of turn time to allow |
| `--max-usd USD` | Estimated US dollars to allow; needs all three --price-* flags |
| `--price-input USD` | US dollars per million uncached input tokens, for the estimate |
| `--price-cached-input USD` | US dollars per million cached input tokens, for the estimate |
| `--price-output USD` | US dollars per million output tokens, for the estimate |

Work stops before the next turn once any limit is reached, so the turn in progress can pass it. The cost is estimated from the reported tokens and the three prices: uncached input × `--price-input`, plus cached input × `--price-cached-input`, plus output × `--price-output`, each per million tokens. It is the estimate the cap checks, not the provider's bill.

Three programs cap spending their own way:

- **`claude_dream.py run --max-usd`:** a different cap, which Claude Code enforces inside the dream session.
- **`load_bearing.py run`:** `--total-usd` for all trials, and the budget flags above, after `--`, for each one.
- **`gadfly_bridge.py run`:** `--total-usd` for the round and the reader's prices, with `--max-usd-per-call` for each session of the study's reader.

---

### Output

`evaluate.py` prints each command's result as one JSON document on stdout and its progress, messages and errors on stderr, so `> result.json` keeps only the result. The other programs print short progress lines and summaries on stdout, and errors always go to stderr. Some of those summaries are machine-readable:

- `atlas.py analyze` prints JSON.
- `pipeline.py prepare` ends with the queue as JSON, and `pipeline.py run` with its result as JSON.
- `record_consoles.py --list` prints one run ID per line.

---

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success. |
| `1` | An error, explained on stderr. For `evaluate.py` and `pipeline.py run`, also a run that did not complete; its JSON on stdout says how far it got. For `gadfly_bridge.py run`, also a run that stopped before its last planned call, as when the total cap leaves no room. |
| `2` | Invalid usage, such as an unknown flag or a missing argument; the usage line says which. |
| `130` | Interrupted with Ctrl-C. Preserved work stays where it is. |

Two programs differ from the table:

- **`atlas.py serve`:** treats Ctrl-C as its normal stop and exits with `0`.
- **`evaluate.py`:** treats a termination signal like Ctrl-C.

---

### Resuming

Rerunning a command with the same arguments continues it from what is preserved:

- **Readings:** in `run-profile`, `run-dream-profile`, `run-evaluation`, `score` and `extract`, a reading replays its preserved turns and continues from the first missing answer. A run stopped by its budget continues with the same command and a larger budget.
- **Campaigns:** `resume` continues a campaign without repeating any job that has a receipt.
- **Dreams, trials and rounds:** `claude_dream.py run` reuses a finished dream, `load_bearing.py run` keeps every finished trial, and `gadfly_bridge.py run` never reads a kept record twice.

An unfinished attempt is set aside only on request: `claude_dream.py run --retry` and `load_bearing.py run --retry` keep it as `.attempt-N` and start fresh sessions.

When every session of a reading has completed but its record was not written, `recover-score` finishes it without a model call; `recover-extraction` does the same for an extraction. A campaign refuses to continue once the pipeline code it froze has changed; plan a new one.
