# Records

The study keeps three kinds of record apart: the preserved worlds, the readings of them, and the editorial choices about what the page shows. Records pin what they depend on by hash, and none of them holds a credential, a private conversation, a raw inference event or a host path.

## Where records live

| Path | Holds |
| --- | --- |
| `corpus/runs/RUN.json` | Each preserved world as inert JSON: every file's path, size and SHA-256, with its text, or only the hash for binary material |
| `corpus/prompts/` | Every delivered prompt, named by its SHA-256 |
| `data/runs.json` | Cohorts and runs, with prompt, corpus and condition identities |
| `data/bundles/RUN/` | The `run.json`, `manifest.json` and `observations.json` retained from each imported run bundle |
| `data/evaluations/EVALUATION/` | One reading per directory, as an evaluation package |
| `data/reviews/` | Other attributed review records, including the unreviewed placeholder each import creates |
| `data/observations.json`, `data/evidence-sources.json` | Separately attributed observations and where their sources are kept |
| `data/artefacts.json` | Preview registration and inspection images, with their hashes |
| `data/recordings/` | Console recordings for the static site; presentation, never evidence |
| `data/review-selection.json`, `data/presentation.json` | Editorial choices: which reading each view displays and which worlds the release page shows |
| `data/rubric.json`, `data/model-capabilities.json` | The rubric and the model capability descriptors |
| `assets/artefacts/` | Inspection images, hashed in `data/artefacts.json` |

Local work (sessions, transcripts, campaign directories and scratch studies) stays under `.local/`, which git ignores.

## Run bundles

A run bundle is how a world enters the study. It has exactly five entries:

```text
run.json            identity, condition, budgets and outcome
prompt.txt          the exact delivered prompt
candidate/          the returned files, frozen before inspection or repair
manifest.json       relative path, byte count and SHA-256 per file
observations.json   separately attributed checks and outcomes
```

`run.json` keeps the run and seed identities, the exact prompt hash, the cohort and whether the run is its baseline, and an outcome (completed, failed, timed out, cancelled or partial). It also keeps start and end times, stop reason, errors and truncation, the requested and reported model and provider, router revision and routing policy, the harness, reasoning, tools and runtime, the isolation mode and network policy, explicit limits, and usage counters with their provenance.

A requested model must be explicit. A reported model stays `null` when the transport does not expose upstream identity, and a requested identity is never copied into the reported fields to make a record look verified.

Every candidate path is relative, canonical, NFC-normalised, not a symlink and unique after case-folding, and the manifest's file set must equal the candidate's. An empty candidate is valid for a failed, timed-out, cancelled or partial run.

```sh
python3 scripts/bundles.py validate BUNDLE              # every check, without changing the study
python3 scripts/bundles.py import BUNDLE --project .    # admit it
```

The validator never imports modules, invokes candidate files or runs a build from the bundle. Import verifies the complete bundle before changing anything, then:

- projects the candidate's text into `corpus/runs/`, with binary material kept as a hash only;
- copies the prompt to its content-addressed path;
- retains `run.json`, `manifest.json` and `observations.json` under `data/bundles/RUN/`;
- registers separate observation provenance;
- creates an explicitly unreviewed review with every dimension `null`;
- registers a new cohort's first admitted run as its baseline.

The placeholder review is not a score and cannot cite interpreted evidence. A world opts into a browser preview only through a separate entry in `data/artefacts.json`.

## Evaluation packages

Each reading is admitted as its own package under `data/evaluations/EVALUATION/`, with nine files:

```text
run.json            the reading's run record, linked to the world it reads by source_run_id
evaluation.json     every session's judgments, the per-question quorum and the outcomes
extraction.json     the evidence inventory, with exact line ranges
carriage.json       each session's seed reading, carriage map and standing map
scoring-policy.json the policy the host followed, generated from the rubric
status.json         outcome, levels, counts and usage
review.json         the displayed review, with concise verdicts and citations
selection.json      the package's own display selection
manifest.json       the SHA-256 of every other file
```

Citations are exact ranges (`source_path`, `start`, `end`) without quoted text; quotes are resolved from the hash-pinned sources at admission and build. The `gateway` in `evaluation.json` records the router, model, reasoning, Codex version, router revision, pipeline hash and the reading prompt's version and SHA-256. Each judgment pins the SHA-256 of its raw answer and receipt.

A package is the whole removable unit: delete its directory and rebuild, and only that reading goes. On every build the page re-derives what a package displays from its evaluation, policy, rubric and pinned sources. A malformed or partly removed package stops the build rather than showing stale evidence, and a dry-run reading can never be admitted.

The turns, prompts, answers and receipts behind a package stay under `.local/evaluation-runs/`. `python3 scripts/evaluate.py validate-evaluation DIRECTORY` re-derives an evaluation from its preserved turns without inference.

## Revisions

A package review has `supersedes: null`; a new reading is a new package, and independent readings of one world coexist. A correction to any other review is a separate record that names its predecessor in `supersedes`. Nothing is silently overwritten, merged or averaged.

`data/review-selection.json` and `data/presentation.json` choose what is displayed; they never delete, merge or average other readings. A new rubric needs new, explicitly comparable readings, and old scores keep their original meaning.

## Hashes

- Each preserved file has a SHA-256 in the bundle manifest and the corpus record.
- A bundle's digest combines the exact bytes of `run.json`, `manifest.json`, `observations.json` and `prompt.txt`.
- Prompts are stored and named by SHA-256, and every run and cohort pins its prompt's hash.
- A reading pins the hashes of the prompt, corpus, rubric, extraction, scoring policy and reading prompt; a trial plan pins its own.
- Inspection images are hashed in `data/artefacts.json`, and console recordings carry their world's corpus hash.
- Observation sources kept in the repository are rehashed on validation. A source kept only outside the repository is marked as such: it is a trust boundary and cannot be reproduced from here alone.

`npm run check` (`python3 scripts/atlas.py validate`) validates the study's records, hashes and citations without inference.

## What never goes into records

- **Raw inference events.** Event streams, stderr and final messages of a model session stay private under `.local/`; records keep their hashes and the validated answers.
- **Private conversations.** Transcripts of a dream or reading are never copied into the study.
- **Credentials.** The validator rejects credential-like metadata keys. The router token lives only in a local run's private transport configuration, and only its hash enters a record; a Claude token stays in its private file or environment.
- **Host paths.** Bundle validation rejects host-specific absolute paths, and public metadata carries none. A console recording replaces its copy's temporary folder with "[the disposable copy]" and stops on any other host path.

## Schemas

The JSON shapes are in the [schemas](schemas/). The validators are authoritative for the privacy, path and cross-file checks a schema cannot express.

| Schema | Describes |
| --- | --- |
| [Run Bundle v1](schemas/run-bundle-v1.schema.json) | A run bundle's JSON records |
| [Reading profile v2](schemas/evaluation-profile-v2.schema.json) | A reading profile |
| [Dream profile v2](schemas/dream-profile-v2.schema.json) | A Codex dream profile, with separate dreamer and reader |
| [Extraction record v2](schemas/extraction-record-v2.schema.json), [v1](schemas/extraction-record-v1.schema.json) | An evidence extraction with exact line ranges |
| [Evaluation record v2](schemas/evaluation-record-v2.schema.json), [v1](schemas/evaluation-record-v1.schema.json) | An evaluation: judgments, quorum, outcomes and usage |
| [Scoring policy v1](schemas/scoring-policy-v1.schema.json) | A scoring policy: one judgment, quorum and terminal per question |
| [Presentation record v1](schemas/presentation-record-v1.schema.json) | A prepared, self-contained presentation of one evaluation |
| [Model capabilities v1](schemas/model-capabilities-v1.schema.json) | The model capability registry |
