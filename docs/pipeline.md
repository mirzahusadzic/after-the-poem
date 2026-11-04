# Pipeline

This page shows how a world travels from the seed to the workbench, and how to make that journey yourself. Every step is a command, so you can repeat the whole study with Codex and the [Unified Model Router](https://github.com/mirzahusadzic/unified-model-router), or with Claude Code.

The seed, the reader, the rubric and the pipeline stay the same from run to run. So when a newer model dreams a world, that world lands next to the earlier ones, and you can read them side by side.

---

### Overview

**Key:** 🔵 record · 🟡 a model at work · ⚪ host code · 🔴 the page

```mermaid
---
config:
    layout: elk
    look: neo
    themeVariables:
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif'
    flowchart:
        wrappingWidth: 220
        padding: 20
    themeCSS: >-
        foreignObject { overflow: visible; }
        [data-look="neo"] * { filter: none !important; }
        .node foreignObject, .node foreignObject *, .edgeLabel, .edgeLabel * {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif !important; line-height: 1.5 !important; }
        .node foreignObject, .node foreignObject * { white-space: nowrap !important; }
        code { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo,
        Consolas, monospace; font-size: 0.9em; line-height: inherit;
        color: inherit !important; text-shadow: none !important;
        padding: 0 4px; border-radius: 4px;
        background: color-mix(in srgb, currentColor 14%, transparent)
        !important; }
        .edgeLabel, .edgeLabel p, .edgeLabel span {
        background: transparent !important; }
        .edgeLabel rect { fill: transparent !important; }
        .labelBkg { --knock: var(--vscode-editor-background,
        var(--bgColor-default, rgb(from currentColor
        clamp(30, (128 - r) * 100, 255) clamp(30, (128 - r) * 100, 255)
        clamp(30, (128 - r) * 100, 255)))); --ring: var(--knock);
        --fill: color-mix(in srgb, var(--ring) 30%, var(--knock));
        background: var(--fill) !important; border-radius: 4px;
        box-shadow: 0 0 0 5px var(--fill), 0 0 0 7px var(--ring); }
        .edgeLabel p, .edgeLabel span, .edgeLabel text {
        --halo: var(--fill, var(--vscode-editor-background,
        var(--bgColor-default,
        rgb(from currentColor clamp(0, (128 - r) * 100, 255)
        clamp(0, (128 - r) * 100, 255) clamp(0, (128 - r) * 100, 255)))));
        text-shadow: 1px 0 var(--halo), -1px 0 var(--halo),
        0 1px var(--halo), 0 -1px var(--halo), 1px 1px var(--halo),
        -1px -1px var(--halo), 1px -1px var(--halo), -1px 1px var(--halo),
        0 0 3px var(--halo), 0 0 4px var(--halo); }

---
flowchart LR
    SD["<b>Seed</b><br/>the poem,<br/>an empty world"]
    DR["<b>Dream</b><br/>Codex or<br/>Claude Code"]
    AD["<b>Admission</b><br/>bundle,<br/>corpus,<br/>preview"]
    RD["<b>Reading</b><br/>three sessions,<br/>a quorum"]
    WB["<b>Workbench</b><br/>listed<br/>by hand"]
    SD --> DR --> AD --> RD --> WB

    classDef default fill:#8a96a82e,stroke:#8a96a8,stroke-width:1px
    classDef record fill:#4d6bfe4d,stroke:#4d6bfe,stroke-width:2px
    classDef model fill:#e3a0084d,stroke:#e3a008,stroke-width:2px
    classDef host fill:#b5bcc633,stroke:#b5bcc6,stroke-width:2px
    classDef page fill:#c94a4a4d,stroke:#c94a4a,stroke-width:2px
    class SD record
    class DR,RD model
    class AD host
    class WB page
```

<p align="center"><em>Each step has its own section below.</em></p>

1. **Seed.** The poem and an empty world. Every dreamer receives exactly the same prompt.
2. **Dream.** A model, the *dreamer*, works in the empty world for a fixed time, in Codex or in Claude Code.
3. **Admission.** Host code, the study's own Python rather than a model, packs what the dreamer left, checks it and freezes it as a *world*.
4. **Reading.** Another model, the *reader*, reads the world in three separate sessions. Host code keeps a level only where at least two of them agree.
5. **Workbench.** You add the world to the page by hand.

| Step | Command | Calls a model? | What it leaves |
| --- | --- | --- | --- |
| [Dream](#dream) | `evaluate.py run-dream-profile` or `claude_dream.py run` | Yes, the dreamer | A capture in `.local/dream-runs/` |
| [Admission](#admission) | Runs inside both dream commands | No | `corpus/runs/RUN.json`, `data/bundles/RUN/` and an entry in `data/runs.json` |
| [Reading](#reading) | Runs inside both dream commands (Claude Code's with `--read`), or `evaluate.py run-profile` for any admitted world | Yes, the reader | `data/evaluations/EVALUATION_ID/` |
| [Workbench](#workbench) | Edit `data/presentation.json`, then `npm run build` | No | The page, in `dist/` |

Model calls cost money on your own accounts. A command that calls a model refuses to start unless you pass `--accept-run` and it has a budget. Each one also has a check that calls no model, so you can test your setup first.

Every flag is listed in [Commands](commands.md). For a complete example, see [One run, start to finish](#one-run-start-to-finish).

---

### Setup

You need:

- **Python 3.13 or later, and [uv](https://docs.astral.sh/uv/).** On this page `python3` means that Python, and `uv run python` gives you one. `uv run` also installs the one Python package the study pins, Gadfly, from `uv.lock`: `gadfly_bridge.py` needs it, and the npm scripts already use it.
- **Node.js 22**, for screenshots, formatting checks and the browser tests only; the build, serve and validation steps need only Python. See [Environment](environment.md#playwright-install) for the Playwright install.

For readings and Codex dreams:

- **The Codex CLI**, tested with 0.163.0 (the published readings) and 0.166.0 (Gadfly round 1).
- **The Unified Model Router**, installed as [its README](https://github.com/mirzahusadzic/unified-model-router#install) describes. The installer puts the router's Codex profile (`unified-openrouter`) and its model catalog in `~/.codex`. This study looks there by default, or in `CODEX_HOME` if you set it.
- **The router's address**, `http://127.0.0.1:18766/v1`. If your setup differs, pass `--router-url`, `--catalog` and `--router-profile`.
- **Its provider report.** After a live Codex dream or reading, the study asks the router which providers served its calls, and waits up to a minute for OpenRouter to record the last one. The names go into the record ([Records](records.md)); a router that cannot report is refused before any call.

For Claude Code dreams:

- **Claude Code**, with `claude` on your `PATH` (tested with 2.1.308).
- **A credential**, described under [In Claude Code](#in-claude-code).

For native previews and console recordings:

- **macOS.** Zsh, Python and Ruby worlds run inside the macOS sandbox.

Two more things to know:

- **Model descriptors.** Every model must be known in two places: the router's catalog and the study's [model descriptors](../data/model-capabilities.json). A model missing from either fails before any call.
- **The reader's tier.** The reader, Muse Spark 1.3 Contributor, runs on Meta's contributor tier, which trains on what it receives. That is fine for the public worlds here; don't send it anything private.

---

### Dream

**Key:** 🔵 record · 🟡 the dreamer at work · ⚪ host code

```mermaid
---
config:
    layout: elk
    look: neo
    themeVariables:
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif'
    flowchart:
        wrappingWidth: 220
        padding: 20
    themeCSS: >-
        foreignObject { overflow: visible; }
        [data-look="neo"] * { filter: none !important; }
        .node foreignObject, .node foreignObject *, .edgeLabel, .edgeLabel * {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif !important; line-height: 1.5 !important; }
        .node foreignObject, .node foreignObject * { white-space: nowrap !important; }
        code { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo,
        Consolas, monospace; font-size: 0.9em; line-height: inherit;
        color: inherit !important; text-shadow: none !important;
        padding: 0 4px; border-radius: 4px;
        background: color-mix(in srgb, currentColor 14%, transparent)
        !important; }
        .edgeLabel, .edgeLabel p, .edgeLabel span {
        background: transparent !important; }
        .edgeLabel rect { fill: transparent !important; }
        .labelBkg { --knock: var(--vscode-editor-background,
        var(--bgColor-default, rgb(from currentColor
        clamp(30, (128 - r) * 100, 255) clamp(30, (128 - r) * 100, 255)
        clamp(30, (128 - r) * 100, 255)))); --ring: var(--knock);
        --fill: color-mix(in srgb, var(--ring) 30%, var(--knock));
        background: var(--fill) !important; border-radius: 4px;
        box-shadow: 0 0 0 5px var(--fill), 0 0 0 7px var(--ring); }
        .edgeLabel p, .edgeLabel span, .edgeLabel text {
        --halo: var(--fill, var(--vscode-editor-background,
        var(--bgColor-default,
        rgb(from currentColor clamp(0, (128 - r) * 100, 255)
        clamp(0, (128 - r) * 100, 255) clamp(0, (128 - r) * 100, 255)))));
        text-shadow: 1px 0 var(--halo), -1px 0 var(--halo),
        0 1px var(--halo), 0 -1px var(--halo), 1px 1px var(--halo),
        -1px -1px var(--halo), 1px -1px var(--halo), -1px 1px var(--halo),
        0 0 3px var(--halo), 0 0 4px var(--halo); }

---
flowchart LR
    SD["<b>Seed</b><br/>the cohort's exact prompt"]
    subgraph CXP["Through#nbsp;Codex#nbsp;via#nbsp;Unified#nbsp;Model#nbsp;Router"]
        CF["<b>Campaign</b><br/>seed, catalog and<br/>router frozen"]
        GT["<b>Generation thread</b><br/>a fresh Codex session<br/>via the router"]
    end
    subgraph CCP["In#nbsp;Claude#nbsp;Code"]
        FH["<b>Fresh home</b><br/>nothing of yours<br/>reaches it"]
        CN["<b>Canaries</b><br/>host read, tools,<br/>network"]
        DS["<b>Dream session</b><br/>sealed world,<br/>wall time"]
    end
    CAP["<b>Host capture</b><br/>all the dreamer left,<br/>failures too"]
    SD -->|"run-dream-profile"| CF
    CF --> GT --> CAP
    SD -->|"claude_dream.py run"| FH
    FH --> CN --> DS --> CAP

    classDef default fill:#8a96a82e,stroke:#8a96a8,stroke-width:1px
    classDef record fill:#4d6bfe4d,stroke:#4d6bfe,stroke-width:2px
    classDef model fill:#e3a0084d,stroke:#e3a008,stroke-width:2px
    classDef host fill:#b5bcc633,stroke:#b5bcc6,stroke-width:2px
    classDef modelBox fill:#e3a00814,stroke:#e3a008,stroke-width:2px,stroke-dasharray:6 4
    class SD record
    class GT,DS model
    class CF,FH,CN,CAP host
    class CXP,CCP modelBox
```

<p align="center"><em>Each dashed box is one harness, and the label on its arrow is the command that starts it.</em></p>

Both paths start from the same seed: the cohort's exact prompt and an empty world. A *cohort* is the group of worlds dreamed from one exact prompt. Nothing in the prompt says what to build.

Host code captures whatever the dreamer leaves behind, including failed and unfinished attempts.

#### Through Codex via Unified Model Router

A dream is described by a small JSON file, the *dream profile*. Start from the [DeepSeek dream profile](../examples/dream-profile-deepseek.json), which names:

- the exact prompt;
- the dreamer, in `generator_model` and `generator_reasoning`;
- the wall time, in `generation_seconds`;
- the reader, in `evaluator_model` and `evaluator_reasoning`;
- the reading: three sessions (`repetitions`) and an `evaluation_budget`;
- the IDs: `campaign_id`, `generator_id` and the reading's `evaluation_id` and `review_id`.

To dream with another model, change the dreamer and the IDs. The new world's ID will be `CAMPAIGN_ID-GENERATOR_ID-s1`.

Check your setup first. `doctor-dream-profile` checks both models, the router and the budget, and calls no model:

```sh
python3 scripts/evaluate.py doctor-dream-profile PROFILE.json
```

Rehearse the generation alone, with synthetic answers in `OUTPUT.dry-run`; a rehearsal is never admitted:

```sh
python3 scripts/evaluate.py run-dream-profile PROFILE.json \
  --dry-run
```

Then dream, admit and read:

```sh
python3 scripts/evaluate.py run-dream-profile PROFILE.json \
  --accept-run
```

What happens:

1. **A campaign is frozen.** The seed, the rubric, the model catalog and the router's revision are recorded before the first call.
2. **The dream.** The dreamer works in a fresh Codex session, through the router.
3. **Admission and reading**, as described below.

Good to know:

- **Another model, just once.** `--runner-model` and `--runner-reasoning` override the dreamer, and `--scorer-model` and `--scorer-reasoning` override the reader.
- **One attempt.** Each attempt is a full, paid dream, so by default only one is made. `--generation-attempts N` (up to 5) allows a new attempt after an ordinary failure, such as a dropped connection. Timeouts, integrity failures, cancellations and invalid output never retry.
- **Resuming.** If a run stops, run the same command again. It picks up the preserved dream and reading.

#### In Claude Code

Claude Code is a different harness from Codex, with its own system prompt and tools. So a Claude Code dream is recorded as a separate condition, `claude-code-dream-v1`.

Its [prompt](../examples/prompts/dream-4521-claude-code.txt) is the cohort prompt with one change: the first line names Claude Code instead of Codex. Its worlds form their own cohort, `dream-4521-claude-code-v1`.

In `data/runs.json` that cohort is declared as sharing the Codex seed (`"seed_of": "dream-4521-v1"`). The build checks that the first line is the only difference, then shows both cohorts on the same workbench.

Start from [Fable's dream profile](../examples/dream-profile-claude-fable.json). It names the run, the prompt, the model and its effort, the wall time, the model for the canaries (`audit_model`) and the reader.

You need one credential. It stays outside the repository and is never recorded:

- **A Claude subscription.** Run `claude setup-token` in your own terminal. Keep the token in a private file (`chmod 600`) and point `CLAUDE_DREAM_TOKEN_FILE` at it, or export it as `CLAUDE_CODE_OAUTH_TOKEN`. Any `ANTHROPIC_API_KEY` is then kept away from the session.
- **Or an API key**, in `ANTHROPIC_API_KEY`. It is billed per token, so `run` then also requires `--max-usd`.

Check your setup first. `check` looks at the profile, the prompt, Claude Code and the credentials, and calls no model:

```sh
python3 scripts/claude_dream.py check PROFILE.json
```

Then dream, admit and read:

```sh
python3 scripts/claude_dream.py run PROFILE.json \
  --accept-run --read
```

What happens:

1. **A fresh home.** The session gets its own Claude Code configuration, home folder and world. None of your memory, `CLAUDE.md`, plugins, skills, hooks or MCP servers reach it.
2. **Canaries.** A short test session, on the same settings, tries what the dream must not do: read your files and reach the network. It also checks that the world itself can be read and written. If anything is off, the dream does not start.
3. **The dream.** The dreamer receives the exact prompt, plus the generation instructions the Codex harness gives. It stops at the wall time.
4. **Admission and, with `--read`, the reading**, as described below.

During the dream:

- **Files and shell.** The dreamer can read and edit files only inside its world, and use a shell that has no network. Web tools and helper agents are refused.
- **Permissions.** A fixed policy, `scripts/dream_permissions.py`, answers every permission request, the same way every time.

Afterwards:

- **The transcript.** It stays private, in the profile's `.local` output folder, and never enters the repository.
- **Resuming.** If a run stops, run the same command again. It resumes from whatever finished. `--retry` sets an unfinished dream aside as `OUTPUT.attempt-N` and starts a new one.
- **The reading's budget.** The reading uses the reader's budget from the profile. To add a cost cap, pass the whole budget through `--read-arg`; [Commands](commands.md#claude_dreampy) shows the full line.

---

### Admission

Admission turns what the dreamer left into a *world*: a frozen, hashed record that nothing runs. Host code does it right after the dream, in three steps:

1. **Run Bundle.** Everything captured is packed into a Run Bundle: the world's files with their hashes, the exact prompt, the harness and its version, the token usage and the outcome. If the dream failed or timed out, or the bundle does not validate, it stays in `.local` and is never admitted.
2. **Corpus.** The world's files are stored as inert JSON in `corpus/runs/RUN.json`. Its bundle goes to `data/bundles/RUN/`, and it gets an entry in `data/runs.json`. Its cohort is found from the hash of its prompt.
3. **Preview.** A world with a top-level `index.html` gets a sandboxed browser copy and a 1440 × 1050 screenshot. If the screenshot fails, `python3 scripts/evaluate.py capture-artefact RUN_ID` takes it later; it never replaces one already taken. Zsh, Python and Ruby worlds get their previews by hand, as the Workbench section below describes.

From here on, a world only ever runs as a disposable copy, in a preview. [Records](records.md) describes the bundle and the corpus in full, and [Previews](previews.md) the sandboxes.

---

### Reading

**Key:** 🔵 evidence · 🟡 the reader at work · ⚪ host code · 🔴 the reading

```mermaid
---
config:
    layout: elk
    look: neo
    elk:
        mergeEdges: true
        nodePlacementStrategy: NETWORK_SIMPLEX
        forceNodeModelOrder: true
    themeVariables:
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif'
    flowchart:
        wrappingWidth: 220
        padding: 20
    themeCSS: >-
        foreignObject { overflow: visible; }
        [data-look="neo"] * { filter: none !important; }
        .node foreignObject, .node foreignObject *, .edgeLabel, .edgeLabel * {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif !important; line-height: 1.5 !important; }
        .node foreignObject, .node foreignObject * { white-space: nowrap !important; }
        code { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo,
        Consolas, monospace; font-size: 0.9em; line-height: inherit;
        color: inherit !important; text-shadow: none !important;
        padding: 0 4px; border-radius: 4px;
        background: color-mix(in srgb, currentColor 14%, transparent)
        !important; }
        .edgeLabel, .edgeLabel p, .edgeLabel span {
        background: transparent !important; }
        .edgeLabel rect { fill: transparent !important; }
        .labelBkg { --knock: var(--vscode-editor-background,
        var(--bgColor-default, rgb(from currentColor
        clamp(30, (128 - r) * 100, 255) clamp(30, (128 - r) * 100, 255)
        clamp(30, (128 - r) * 100, 255)))); --ring: var(--knock);
        --fill: color-mix(in srgb, var(--ring) 30%, var(--knock));
        background: var(--fill) !important; border-radius: 4px;
        box-shadow: 0 0 0 5px var(--fill), 0 0 0 7px var(--ring); }
        .edgeLabel p, .edgeLabel span, .edgeLabel text {
        --halo: var(--fill, var(--vscode-editor-background,
        var(--bgColor-default,
        rgb(from currentColor clamp(0, (128 - r) * 100, 255)
        clamp(0, (128 - r) * 100, 255) clamp(0, (128 - r) * 100, 255)))));
        text-shadow: 1px 0 var(--halo), -1px 0 var(--halo),
        0 1px var(--halo), 0 -1px var(--halo), 1px 1px var(--halo),
        -1px -1px var(--halo), 1px -1px var(--halo), -1px 1px var(--halo),
        0 0 3px var(--halo), 0 0 4px var(--halo); }

---
flowchart LR
    E["<b>Evidence</b><br/>the world's hashed<br/>sources, numbered"]
    subgraph MS["Three sessions · the reader"]
        S1["<b>Session 1</b><br/>maps, then eight levels"]
        S2["<b>Session 2</b><br/>maps, then eight levels"]
        S3["<b>Session 3</b><br/>maps, then eight levels"]
    end
    C["<b>Citations</b><br/>checked against the<br/>sources · host code"]
    subgraph HC["Quorum · host code"]
        QR["<b>Core recognition</b><br/>two of three agree"]
        QA["<b>Shared agency</b><br/>two of three agree"]
        QC["<b>Mutual consent</b><br/>two of three agree"]
        QT["<b>Consequential touch</b><br/>two of three agree"]
        QJ["<b>Independent judgment</b><br/>two of three agree"]
        QF["<b>Fidelity through change</b><br/>two of three agree"]
        QO["<b>Open continuation</b><br/>two of three agree"]
        QX["<b>Expressive coherence</b><br/>two of three agree"]
    end
    P["<b>Reading</b><br/>0–4 or unresolved,<br/>every session kept"]
    E --> S1
    E --> S2
    E --> S3
    S1 --> C
    S2 --> C
    S3 --> C
    C --> QR & QA & QC & QT & QJ & QF & QO & QX
    QR & QA & QC & QT & QJ & QF & QO & QX --> P

    classDef default fill:#8a96a82e,stroke:#8a96a8,stroke-width:1px
    classDef evidence fill:#4d6bfe4d,stroke:#4d6bfe,stroke-width:2px
    classDef judgment fill:#e3a0084d,stroke:#e3a008,stroke-width:2px
    classDef quorum fill:#b5bcc633,stroke:#b5bcc6,stroke-width:2px
    classDef terminal fill:#c94a4a4d,stroke:#c94a4a,stroke-width:2px
    classDef model fill:#e3a00814,stroke:#e3a008,stroke-width:2px,stroke-dasharray:6 4
    classDef host fill:#b5bcc614,stroke:#b5bcc6,stroke-width:2px,stroke-dasharray:6 4
    class E evidence
    class S1,S2,S3 judgment
    class C,QR,QA,QC,QT,QJ,QF,QO,QX quorum
    class P terminal
    class MS model
    class HC host
```

<p align="center"><em>The dashed box on the left holds the reader's three sessions, each answering all eight questions. The one on the right is host code, giving each question its own quorum.</em></p>

The three sessions run independently of one another. Each session:

1. reads the world's evidence once, with every line numbered;
2. writes its notes on the world: what is there, how it reads the poem, what was carried over and who can do what;
3. answers all eight questions, each with a level from 0 to 4 and exact line citations.

Then host code takes over:

- **Citations.** It checks every citation against the world's hashed sources.
- **Quorum.** For each question, it keeps the level that at least two of the three sessions give (`minimum_agreement` 0.67). If no two agree, the question stays unresolved.
- **Admission.** It admits the reading as its own package in `data/evaluations/EVALUATION_ID/`, with all three sessions kept.

What the reader is asked, and why, is in [Reading a continuation](study.md).

#### Read a world again

Both dream commands read the new world for you, Claude Code's with `--read`. To read a world that is already admitted, write a *reading profile*.

Each reading on the page has its [reading profile](../examples/rescore-muse/) among the examples. Copy one, then give it new IDs and a new output folder:

```json
{
  "schema_version": 2,
  "run_id": "sol-original",
  "model": "openrouter/meta/muse-spark-1.3-contributor",
  "reasoning": "xhigh",
  "repetitions": 3,
  "evaluation_id": "sol-original-reading-2",
  "review_id": "sol-original-reading-2-review",
  "reviewer": "Muse Spark 1.3 Contributor · rubric session",
  "output": ".local/evaluation-runs/sol-original-reading-2",
  "layout": "compact",
  "budget": {
    "max_turns": 9,
    "max_input_tokens": 900000,
    "max_output_tokens": 300000,
    "max_seconds": 2400
  }
}
```

Check your setup first. `doctor-profile` checks the profile, the budget, Codex, the router and the model, and calls no model:

```sh
python3 scripts/evaluate.py doctor-profile PROFILE.json
```

Rehearse the whole path with synthetic answers; a rehearsal is never admitted:

```sh
python3 scripts/evaluate.py run-profile PROFILE.json \
  --dry-run
```

Then run the three sessions, which are validated and admitted:

```sh
python3 scripts/evaluate.py run-profile PROFILE.json \
  --accept-run
```

The budget caps the reading. Work stops before the next turn once any limit is reached, so the turn in progress can pass it.

| Field | What it caps | Required |
| --- | --- | --- |
| `max_turns` | Turns | Yes |
| `max_input_tokens` | Input tokens, cached ones included | Yes |
| `max_output_tokens` | Output tokens | Yes |
| `max_seconds` | Seconds of turn time | No |
| `max_usd` | Estimated US dollars. It needs three prices per million tokens (uncached input, cached input, output), under `budget.pricing` or as flags. | No |

The host refuses a budget that is below what the reading needs at minimum, and checks usage before every turn.

Good to know:

- **Repairs.** If an answer fails validation, it is kept exactly as written. The reader gets a repair turn in the same session, told what was wrong. Nothing is edited to make it pass.
- **Resuming.** If a run stops, run the same command again. It replays the preserved turns and continues from the first missing answer. A reading stopped by its budget continues with a larger one.
- **Several worlds.** To read several worlds at once, pass their profiles to `run-profiles` with `--parallel N` (1 to 16, default 4).

---

### Workbench

The *research view* (`npm run dev:full`) shows every admitted world and every reading, straight away. The *release page*, the public one, shows only the worlds you list, each with its latest `-muse-v5-1` reading. See [Release](release.md) for what each view holds, the reader generations and the flat-reader caveat.

To put a world on the release page:

1. **Read it with the page's reader.** The page shows one reader, set under `reader` in the [presentation settings](../data/presentation.json): Muse Spark 1.3 Contributor, three sessions, and an evaluation ID ending in `-muse-v5-1`. That suffix marks the current v5 prompt; earlier `-muse-v4-1` readings and the `static-*` reviews stay visible only in the research view. Both dream commands read new worlds this way, and so do the profiles in `examples/rescore-muse/`.
2. **Look at it** in the research view, and run `npm run check`.
3. **Name it.** A Claude Code world takes its label from the profile and its title from its own files. A Codex world starts out labelled with the model ID and titled with its run ID; give it a proper `label` and `title` in its entry in `data/runs.json`.
4. **Give it a preview.** A browser world already has one from admission. A Zsh, Python or Ruby world needs:
   - its console or server entry in the [preview runner](../scripts/previews.py) and the [preview registry](../data/artefacts.json) (see [Previews](previews.md#native-worlds));
   - a screenshot of its console in `assets/artefacts/RUN.png`, registered in `data/artefacts.json` with its hash;
   - for a Zsh or Python console world, a [recording](#record-a-console-for-the-static-site) for the static site; a Ruby server world shows its screenshot there instead.
5. **List it.** Add its run ID to `shown_runs` in `data/presentation.json`. `npm run build` prints every world that is not listed yet; [Release](release.md#nine-shown-ten-retained) explains the one retained run kept off the page.
6. **Check and build.** `npm run verify` checks everything without calling a model; [Environment](environment.md#fast-checks-vs-the-full-gate) lists the faster checks and what the full gate runs. `npm run build:pages` builds the static site, and the Pages workflow publishes it when you start it by hand.

#### Record a console for the static site

The static site cannot run anything native, so a console world there replays a recording instead. Record it on macOS:

| To | Run |
| --- | --- |
| See which console worlds would be recorded, starting nothing | `python3 scripts/record_consoles.py --list` |
| Record every console world shown on the release page | `python3 scripts/record_consoles.py` |
| Record one world, such as `astra-max` | `python3 scripts/record_consoles.py astra-max` |

Recording starts the world in the isolated local runner, just as `npm run dev` does. It then plays `help`, the world's opening command and its shortcuts, once each. The answers are saved exactly, in `data/recordings/RUN.json`, with the date and the world's corpus hash.

- **Host paths.** If the world prints the path of its temporary copy, the recording writes "[the disposable copy]" instead. Any other path from your machine stops the recording.
- **The build.** It checks each recording against its world and uses it only for the static site.
- **Not evidence.** Recordings are presentation, not evidence for a reading. Record again whenever a world's console or shortcuts change.

---

### One run, start to finish

Here is a real run: Claude Fable 5.1's world, made with its dream profile, `examples/dream-profile-claude-fable.json`. It was checked, then dreamed, admitted and read:

```sh
python3 scripts/claude_dream.py check \
  examples/dream-profile-claude-fable.json
python3 scripts/claude_dream.py run \
  examples/dream-profile-claude-fable.json \
  --accept-run --read
```

**Dream.** The canaries passed, on Claude Haiku 5.5. Then Fable 5.1, at xhigh effort, dreamed in Claude Code 2.1.308.

| Measure | Value |
| --- | --- |
| Wall time allowed | 30 minutes |
| Time used | 9½ minutes |
| Input tokens | 947,070, of which 874,251 cached |
| Output tokens | 46,669 |

The transcript stayed private, in `.local/dream-runs/dream-claude-fable51-20261022/`.

**Admission.** The world, *Tendora dream world*, is a Python apparatus. It was frozen as a [corpus record](../corpus/runs/dream-claude-fable51-20261022.json), with its [bundle](../data/bundles/dream-claude-fable51-20261022/), in the cohort `dream-4521-claude-code-v1`.

**Reading.** Muse read it in three sessions, in under eight minutes, using 73,313 input and 59,953 output tokens. The reading is its own [evaluation package](../data/evaluations/dream-claude-fable51-20261022-muse-v5-1/).

Every question reached a quorum. On shared agency, two sessions gave 1 and one gave 2, so the page shows 1⅓, with "2 of 3 sessions agree".

**Workbench.** Its console was added to `scripts/previews.py` and `data/artefacts.json`, with a screenshot. It was recorded with `python3 scripts/record_consoles.py dream-claude-fable51-20261022`. Then its ID went into `shown_runs`.

Through Codex via the Unified Model Router, the same run starts with `run-dream-profile` and a profile that names the dreamer. Every step after the dream is the same.

---

### Checks on the reading

These checks ask whether a reading's levels follow the poem, or only a world's structure; [Checks](study.md#checks) explains why that matters. Controls and load-bearing trials run on a scratch copy of the study, and Gadfly checks need none. Nothing any of them produces is admitted.

#### Controls

A control reads a world against deliberately changed evidence. First, copy the study's records into a scratch study, which also imports the decoy world:

```sh
python3 scripts/reading_controls.py prepare \
  .local/scratch-study
```

Then write a scoring policy for three sessions, two of which must agree:

```sh
python3 scripts/evaluate.py generate-policy \
  --repetitions 3 --output .local/rubric-policy.json
```

Each control is then one `score` run. The first is the *decoy*, `control-decoy-gestures`: a world written to name every gesture of the poem while carrying none of its relations. Read it with:

```sh
python3 scripts/evaluate.py score \
  --project .local/scratch-study \
  --run-id control-decoy-gestures \
  --policy .local/rubric-policy.json \
  --model openrouter/meta/muse-spark-1.3-contributor \
  --reasoning xhigh \
  --output .local/controls/decoy --accept-run \
  --max-turns 9 --max-input-tokens 900000 \
  --max-output-tokens 300000 --max-seconds 2400
```

For the other two controls, use a preserved world's run ID (`--run-id RUN_ID`) and add one flag:

| Control | Change | What it shows |
| --- | --- | --- |
| Decoy | `--run-id control-decoy-gestures` | A reader that is not fooled by echo reads it low on recognition, fidelity, shared agency and consent. |
| Masked evidence | `--mask-terms examples/controls/seed-vocabulary.json` | The poem's distinctive words become neutral tokens in the evidence; a level that falls only here is carried by echo. |
| Unrelated seed | `--control-seed examples/controls/owl-and-the-pussy-cat.txt` | The world is read against an unrelated poem; recognition and fidelity should fall, and the dimensions that hold read the world itself. |

#### Load-bearing trials

A load-bearing trial asks whether a reading's level actually rests on the evidence it cites. It looks at one question in one world at a time ([The load-bearing test](study.md#the-load-bearing-test)).

It takes four steps:

1. **Draft** a plan, as JSON (fields below).
2. **Declare** it with `prepare`. This pins the rubric, the corpus and the masked vocabulary by hash and the reading prompt by its version, works out which lines to blank, and draws the sham. A declared plan is never overwritten.
3. **Run** it. Every trial is read in its own sessions, within a cap per trial and a total cap.
4. **Read the findings.** `findings` works them out again from the saved trials, without calling a model.

A draft has these fields:

| Field | What it holds |
| --- | --- |
| `schema_version` | `1`. |
| `id`, `question` | The plan's name and the question it asks. |
| `frame` | The reader: `model`, `reasoning`, `sessions` (at least 3) and `minimum_agreement`. |
| `masked_terms` | The file of the poem's vocabulary to mask. |
| `sham_seed` | A number that fixes where the sham's blank lines fall. |
| `rules` | Optional. `2` also requires every baseline and repeat session to agree. |
| `readings` | Worlds to read as they are, by run ID. |
| `cases` | One per world and question: an `id`, the `run_id`, the `axis`, and either a `reference` reading (an `evaluation.json` in the scratch study) or `anchors` (saved trials whose citations decide which lines to blank). |
| `reuse_root` | Optional. An earlier output whose trials can be reused, pinned by hash. A reading, or a case's `repeat` and `presentation` trials, names them under `reuse`; `anchors` need a `reuse_root` too. |
| `note` | Optional. A note kept with the plan. |

Declare the draft once, before any trial:

```sh
python3 scripts/load_bearing.py prepare DRAFT.json \
  --project .local/scratch-study --output PLAN.json
```

Then run every trial. `OUT` is a new folder, such as `.local/load-bearing/plan-1`, and everything after `--` is the budget for each trial:

```sh
python3 scripts/load_bearing.py run PLAN.json \
  --project .local/scratch-study --output OUT \
  --total-usd TOTAL_USD --accept-run -- \
  --max-turns 9 --max-input-tokens 900000 \
  --max-output-tokens 300000 --max-seconds 2400 \
  --max-usd TRIAL_USD --price-input USD_PER_M_INPUT \
  --price-cached-input USD_PER_M_CACHED_INPUT \
  --price-output USD_PER_M_OUTPUT
```

Read the findings, without calling a model:

```sh
python3 scripts/load_bearing.py findings PLAN.json \
  --output OUT
```

Good to know:

- **The total cap.** Before each trial, the runner sets aside that trial's cap, plus one turn's margin, from the total; what earlier attempts in `OUT` spent counts too. A trial that does not fit waits for running trials to settle. If none is running, it is skipped and left unresolved; it is never estimated.
- **Resuming.** If a run stops, run it again. Finished trials are kept. `--retry` sets an unfinished trial aside as `TRIAL.attempt-N` and reads it again in fresh sessions; what it spent still counts.

What the output holds:

- `OUT/readings/RUN_ID`: the plain readings.
- `OUT/cases/CASE/TRIAL`: each case's trials, with the blanked and sham line ranges beside them.
- `OUT/findings.json`: each case's category and reason; every trial's level, agreement, per-session results and evaluation hash; and the plan's hash.

#### Gadfly checks

[Gadfly](https://github.com/mirzahusadzic/gadfly) runs the load-bearing test as a published package, `midwife`, pinned at 0.7.1 in `uv.lock`. Its verdict rules are the ones above, with `rules: 2`. Here it tests the levels the release page shows, as they stand: each frame is built from one published reading, and no scratch study is needed.

A frame names one world and one question. Every trial comes from that reading and the frozen world alone:

| Trial | What the reader sees |
| --- | --- |
| Baseline, repeat | The world as preserved. |
| Ablation | The lines the three sessions cite for the question, blanked. |
| Cumulative | Those lines and the fallback passages the round names, blanked. Optional. |
| Sham | As many uncited lines, in blocks of the same sizes, blanked. |
| Presentation | The poem's distinctive words renamed to neutral tokens in the world. |
| Control | Every line the reading cites on any question, blanked. It shows whether the reader can move at all. |

The reader is the study's own. Each call is one v5 session, through the reading prompt the published reading used; the frame pins that prompt's hash. A blanked line keeps its number.

The frame also names the code that runs the reader (`pipeline_sha256`). That code has changed since the published readings; their reading prompt has not.

A round is a spec in `examples/gadfly/`. It lists the frames and, for each one, the predictions, written before the first call.

A spec can also choose its trials: `trials` lists any of `repeat`, `sham`, `presentation` and `control`, and the ablation always runs. A spec that names none runs round 1's set, all four. `control` says how far the control reaches:

| `control` | What it blanks |
| --- | --- |
| `cited` | Every line the reading cites on any question. Round 1's control. |
| `remnant` | Every line but one uncited block for each cited block, of the same size, drawn with the published evaluation's hash as the seed. No line any reading cites is left. |
| `world` | Every line. Only for a second reader: the study's reader cites a non-blank line for every level, so `build` refuses it there. |

A stable standing reads the baseline and repeat sessions together, so it needs `repeat`.

It takes four steps:

1. **Build** the frames. `build` derives them from the published readings and pins the reader, the code and the midwife release.
2. **Plan.** `plan` prints each frame's trials and its number of calls, without calling a model.
3. **Run.** A dry run reads every trial with synthetic sessions and calls no model. A live run needs frames built against the live router (`--router-revision live`), `--accept-run`, a total cap, a cap per call and the reader's prices.
4. **Report.** `report` reads the verdicts from the kept records, without calling a model, and shows the predictions beside them.

Round 1's spec names midwife 0.1.2, the release it ran under. `build --dry-run` builds it under the pinned release for dry runs only.

To build, plan, run and report round 1 dry, with no model call:

```sh
python3 scripts/gadfly_bridge.py build \
  examples/gadfly/round-1.json \
  --output .local/gadfly/round-1-dry --dry-run
python3 scripts/gadfly_bridge.py plan .local/gadfly/round-1-dry
python3 scripts/gadfly_bridge.py run .local/gadfly/round-1-dry \
  --dry-run
python3 scripts/gadfly_bridge.py report \
  .local/gadfly/round-1-dry \
  --spec examples/gadfly/round-1.json --dry-run
```

Good to know:

- **Round 1.** It has three frames and 57 calls: Dreamy Sol · open continuation, Sol 5.6 · mutual consent and Claude Fable 5.1 · core recognition. It has run once, for about $0.53, and its verdicts are on the Study page ([Gadfly round 1](study.md#gadfly-round-1)).
- **Round 2.** It asks the same three decisions whether the reader moves at all. Beside the ablation it runs only a remnant control, with no repeat, sham or presentation: 27 calls in all. It has run once, for about $0.21, and the $0.25 cap stopped its last call; its verdicts are on the Study page ([Gadfly round 2](study.md#gadfly-round-2)).
- **Frames never change.** A new reading, new code, another router revision or another midwife release gives a new frame with a new hash, so build into a new folder.
- **Releases.** A spec keeps the release it ran under: rounds 1 and 2 name midwife 0.1.2, the second-reader rounds 0.1.1. Under another release `build` takes it only with `--dry-run`, and `run` refuses such a round live. A new live run is a new round, with a new spec and its own predictions.
- **Re-reading.** `report` reads kept records under the pinned release. Every kept round reads the same under 0.7.1 as under the release it ran with: frame hashes, calls, each trial's input and level, and the verdicts. Since 0.7.0 the findings beside a verdict are worded differently; they say the same.
- **Resuming.** If a run stops, run it again: a kept record is never read twice. `--only TRIAL` runs one trial; repeat it for more.
- **Settling.** `run --settle` makes no more calls for a trial whose level can no longer change, such as the third session after two agree. The verdict reads the same. The report then shows the two sessions that settled the trial, not how a third would have read. Under a stable standing, the baseline and repeat always run whole.
- **Carriage.** The round reads with `carriage: listed`. A category is named at once, and the checks it leaves open, such as the missing bypass trial, are listed beside it.

What the output holds:

- `OUT/round.json` and `OUT/frames/NAME.json`: the frames and their hashes.
- `OUT/records/NAME.jsonl`: one Gadfly record per call.
- `OUT/readings/HASH/call-N`: each call's session, an evaluation package marked as never admitted.
- `OUT/report-midwife-VERSION.json`: each frame's verdict, levels, evidence and open checks, beside its predictions, as that midwife release's rules read them.

A dry run keeps its own `dry-records`, `dry-readings` and `dry-report-midwife-VERSION.json` instead, apart from any live run's.

##### Second readers

A round can also be read by Gemma 4 on your own machine, through [eGemma](https://github.com/mirzahusadzic/egemma) and Gadfly's own eGemma battery. It costs no money, and its level never reaches the page (`materialized: false`). It asks a different question: does the evidence the published reading cites carry the level for another reader too?

- **New frames.** The same decisions get the same trials. Only the reader changes, so each frame is new, with predictions of its own.
- **Instructions.** The reader's instructions hold the seed, the author's declared reading, the v5 rules that apply to one answer, and the question with its anchors. The seed stays whole in every trial; renaming reaches the world only.
- **Removed lines.** A removed line is left out, its number with it, because Gadfly's battery sends the trial as Gadfly writes it.
- **Context.** eGemma reads about 32,000 tokens and refuses a reading that does not fit. `build` refuses such a world before any call, so round 1's Dreamy Sol and Sol 5.6 have no eGemma frame.
- **Key and caps.** The key comes from `EGEMMA_API_KEY` or `EGEMMA_API_KEY_FILE`. A live run needs `--accept-run`, and no dollar caps.

The eGemma round ran with these commands, under midwife 0.1.1:

```sh
python3 scripts/gadfly_bridge.py build \
  examples/gadfly/round-1-egemma.json \
  --output .local/gadfly/round-1-egemma
python3 scripts/gadfly_bridge.py run \
  .local/gadfly/round-1-egemma --accept-run
python3 scripts/gadfly_bridge.py report \
  .local/gadfly/round-1-egemma \
  --spec examples/gadfly/round-1-egemma.json
```

The study's critic can be the second reader too. Muse Spark 1.3 Contributor then reads through the router and Gadfly's unified battery: one call per reading, with no tools, under the same instructions as the eGemma round.

- **Never on the page.** It is a different path from the published reading, which is a Codex session with the full v5 reading prompt, so its levels never reach the page either.
- **Context.** Muse holds about a million tokens, so every world fits.
- **Training.** Its contributor tier trains on what it reads, so the frame names it under `trainsOnInputs`. OpenRouter routes it only when the account allows endpoints that may train on inputs.
- **Router revision.** The frame pins the router's revision: build with `--router-revision live`, and a run refuses a router at another revision.
- **Live run.** A live run needs `--accept-run`, a total cap and the prices; the per-call cap is the study's reader's only. Before each call the runner sets aside the most that call can cost (its whole input and a full answer), and it counts what each call reports.

The Muse round ran with these commands, under midwife 0.1.1:

```sh
python3 scripts/gadfly_bridge.py build \
  examples/gadfly/round-1-muse.json \
  --output .local/gadfly/round-1-muse \
  --router-revision live
python3 scripts/gadfly_bridge.py run \
  .local/gadfly/round-1-muse --accept-run \
  --total-usd 1.00 --price-input 0.10 \
  --price-cached-input 0.002 --price-output 0.20
```
