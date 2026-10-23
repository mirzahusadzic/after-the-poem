# Licenses by data artifact

This note maps the study's data, report, profile, review, and corpus prompt
artifacts to the rights regime recorded in this repository. It summarises
repository evidence only: file locations, in-file attribution, and the
provenance fields the validators check. It makes no legal conclusion about
originality, ownership, or third-party rights. Where an artifact carries its
own notice, that notice controls.

## How to read this table

- **Study (CC BY 4.0, non-code)** means the author's study material, shared
  under [CC BY 4.0](LICENSE-CONTENT) to the extent the author holds rights.
- **Study (MIT, code/config)** means implementation shared under the
  [MIT License](LICENSE).
- **Preserved record** means model-produced or model-about material included
  as produced, as a research record. Reuse requires checking the provider
  named in the record; this repository grants nothing on any provider's
  behalf.
- **Host record** means a file the study's own pipeline wrote (admission
  placeholders, selections, presentation choices). Host records describe the
  study's handling, not a model's claim.

## Artifact table

| Path | What it holds (per repository records) | Regime recorded here |
| --- | --- | --- |
| `corpus/prompts/*.txt` | Exact delivered prompts, stored and named by SHA-256; pinned by `prompt_sha256` in `data/runs.json` and bundle `run.json` files | Study input (CC BY 4.0). Operator-authored text supplied to models, not model output |
| `corpus/runs/*.json` | Preserved worlds as inert JSON (per-file path, size, SHA-256, and text; hash only for binary material) | Preserved record. Model-produced candidate work, included as produced |
| `data/runs.json` | Cohort and run roster with prompt, corpus, condition, and model identities | Study compilation (CC BY 4.0) describing preserved records; the `model` fields identify, but do not license, the producers |
| `data/bundles/*/run.json`, `manifest.json` | Retained identity, condition, budgets, outcome, and per-file manifest from each imported bundle | Preserved record, retained from the import bundle |
| `data/bundles/*/observations.json` | Separately attributed checks and outcomes, with provenance in `data/evidence-sources.json` | Separately attributed observation record (host capture or candidate-authored handoff per its `scope`); read its `scope` and `note` fields before reuse |
| `data/evaluations/*/` (`run.json`, `evaluation.json`, `extraction.json`, `carriage.json`, `scoring-policy.json`, `status.json`, `review.json`, `selection.json`, `manifest.json`) | One admitted reading per directory, hash-pinned to prompt, corpus, rubric, extraction, scoring policy, and reading prompt | Preserved reading record. Sessions were produced through the named reader model and pipeline; reuse requires the reader provider's terms as well as the underlying world's terms |
| `data/reviews/*.json` (attributed readings, e.g. `*-static-*.json`, `*-editorial-*.json`) | Displayed reviews with verdicts and exact citations, each naming its reviewer, rubric version, and any `supersedes` predecessor | Attributed review record: regime follows its `reviewer`. Human/pipeline editorial readings are study material (CC BY 4.0) to the extent the author holds rights; model-attributed readings are preserved records |
| `data/reviews/*-unreviewed-v1.json` | Bundle-admission placeholders with every scored dimension null | Host record. Explicitly not a score and cites no interpreted evidence |
| `data/review-manifest.json` | SHA-256 pins for every committed authored review under `data/reviews/` | Host integrity record. It describes the bytes of the review, not ownership of the model- or human-authored content inside it |
| `data/observations.json`, `data/evidence-sources.json` | Separately attributed observations and where their sources are kept (in-repo path or marked external trust boundary) | Observation compilation (CC BY 4.0 as a compilation); individual observation rights follow the source's `scope` and any carried notice |
| `data/artefacts.json`, `assets/artefacts/*` | Preview registration, per-image SHA-256, captions, and provenance for inspection images | Operator captures (see LICENSING.md "Screenshots and favicon"). The capture is a host presentation check; the depicted world remains a preserved record |
| `data/recordings/` | Console recordings for the static site | Host presentation material, never assessment evidence |
| `data/rubric.json`, `data/model-capabilities.json` | The assessment rubric and the model capability descriptors | Study material (CC BY 4.0) |
| `data/review-selection.json`, `data/presentation.json` (including the `reader` block) | Editorial choices about which reading each view displays and which worlds the release page shows; the `reader` block describes the critic setup | Study editorial material (CC BY 4.0). The `reader.model` field identifies the critic; it does not license the critic's output |
| `data/presentation.json` generated prose, generated `report.json` / `scores.csv` / `prompt.txt` downloads | The study's presentation of its findings | Study compilation under CC BY 4.0. Model-authored fields embedded from preserved readings remain preserved records under the regimes above |
| `examples/*.json` (campaigns, dream profiles, evaluation profiles, scoring policies) | Example operator-authored run configurations and policies | Study material (CC BY 4.0) |
| `examples/controls/seed-vocabulary.json`, `examples/controls/decoy-world/` | Control inputs for calibration runs | Study material (CC BY 4.0), except where a file carries its own notice |
| `examples/controls/owl-and-the-pussy-cat.txt` | Control text attributed in-file to Edward Lear, 1871 | Public-domain control on the basis of that attribution |
| `src/vendor/*`, `src/vendor/*-package.json`, `src/vendor/*-LICENSE` | Vendored terminal builds with pinned version, tarball URL, and integrity | Their own licenses (see THIRD_PARTY_NOTICES.md) |

## Cautions

- No provider grant is given or implied. Fields such as `model`,
  `generator_model`, `evaluator_model`, and `gateway` identify a producer or
  transport; they do not convey that producer's permission. Consult the terms
  of the provider named in each record before reuse. This file quotes and
  summarises no provider policy.
- Candidate code and candidate prose within one world may carry different
  rights. Preserved third-party notices stay in force and are not replaced by
  the study's licenses.
- Static checks, hashes, file sizes, and test or assertion counts establish
  integrity and provenance only. They do not establish originality, semantic
  comprehension, or consent.
