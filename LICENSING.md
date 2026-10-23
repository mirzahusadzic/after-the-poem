# Licensing

After the Poem · © 2026 Mirza Husadzic

This repository holds two kinds of material: the study, which the author made,
and the records it studies, which models produced. They are licensed
differently.

## The study

The website text, rubric, method, documentation, editorial notes, images made
for the site, and the poem are shared under
[Creative Commons Attribution 4.0 International](LICENSE-CONTENT) (CC BY 4.0).
You may share and adapt this material, including commercially, with attribution
and an indication of changes. The poem's meaning is the author's, worked out in
dialogue with Sol and compiled by Sol into its present form; please keep that
provenance when you reuse it.

The website implementation, build and preview tools, tests and configuration
are available under the [MIT License](LICENSE).

Suggested credit: **After the Poem — Mirza Husadzic**.

## Artifact map

The table below maps repository artifacts to their rights regime as recorded
in this repository. It describes repository evidence only and makes no legal
conclusion about enforceability, originality, or third-party rights. Where an
artifact carries its own notice, that notice controls over this summary. A more
detailed per-path table is kept in [LICENSES-DATA.md](LICENSES-DATA.md).

| Artifact | Regime recorded here |
| --- | --- |
| Study text and method: site copy, `docs/`, rubric (`data/rubric.json`), editorial selections (`data/review-selection.json`, `data/presentation.json` display choices), and the study's own generated report framing in `prompt.txt` / `scores.csv` / `report.json` | CC BY 4.0 (author's study material). Model-authored fields embedded in a report remain preserved records under the regime below |
| Study implementation: `src/`, `scripts/` build and preview tools, `tests/`, configuration including `package.json` | MIT |
| Operator-authored inputs: prompts in `corpus/prompts/` (named by SHA-256) and example profiles and campaigns in `examples/*.json` | CC BY 4.0 as study material, to the extent the author holds rights; they are inputs to model runs, not model output |
| Preserved model records: candidate worlds projected into `corpus/runs/`, retained bundles in `data/bundles/` (`run.json`, `manifest.json`, `observations.json`), model readings in `data/evaluations/` and model-attributed reviews in `data/reviews/` | Included as produced, as research records; see "Model-generated records" below |
| Host admission placeholders: `*-unreviewed-v1.json` reviews in `data/reviews/` with every dimension null | Host records noting that no assessment exists; not scores and not model output |
| Inspection images in `assets/artefacts/` (hashes and provenance in `data/artefacts.json`) and console recordings in `data/recordings/` | Operator captures of, or derived from, model-produced work; see "Screenshots and favicon" below |
| Site artwork including `assets/favicon.svg` | Study image made for the site; CC BY 4.0 |
| Control text `examples/controls/owl-and-the-pussy-cat.txt` | Public-domain control on the basis of its in-file attribution to Edward Lear, 1871 |
| Vendored and test-only dependencies | Their own licenses; see [Third-party notices](THIRD_PARTY_NOTICES.md) |

Candidate code and candidate prose may carry different rights from each other,
and both may differ from the study's licenses. Treat them separately when
reusing a world. Where a preserved file carries, quotes, or reproduces
third-party content under its own notice, that notice is preserved and remains
in force; this repository's licenses do not replace it.

## Screenshots and favicon

Inspection images under `assets/artefacts/` are operator-made captures taken
through the sandboxed preview inspection or the isolated local runner, as
recorded per image in `data/artefacts.json` (`caption` and `provenance`). Some
captions describe a current presentation check rather than a retained original
return snapshot or assessment evidence, and observed state may differ from a
candidate's frozen entry state.

A screenshot therefore has two layers to consider on reuse: the operator's
capture of it, and the underlying model-produced world it shows. The capture
does not convert the underlying candidate work into study material, and the
underlying work does not convert the capture into model output. Console
recordings under `data/recordings/` are likewise presentation material, never
assessment evidence.

The favicon (`assets/favicon.svg`) is different: it is site artwork made for
the study, not a capture of model work, and it is shared under CC BY 4.0 with
the rest of the study's non-code material.

## Model-generated records

The worlds in `corpus/runs/` and `data/bundles/`, their screenshots in
`assets/artefacts/`, and the model readings in `data/evaluations/` and
`data/reviews/` were produced by, or show the work of, the models named in
their run and review records. They are included as produced, as research
records. Their inclusion is not an endorsement of their content.

The author makes no claim that this material is original, that anyone holds
rights in it, or that it is free of third-party rights; a model can reproduce
material from elsewhere. To the extent the author holds any rights in it, they
are offered under the same terms as the rest of the repository: CC BY 4.0 for
text and other non-code material, MIT for code. Check the terms of the model
provider named in each record before reusing it, and attribute the model as
the record does.

No provider grant is given or implied here. This repository cannot grant rights
on behalf of any model provider, and nothing in this file substitutes for the
provider's own terms. Before reusing model-generated material, consult the
terms of the provider identified in the relevant record (for example the
`model` and gateway fields in `data/runs.json`, `data/bundles/*/run.json`,
and `data/evaluations/*/evaluation.json`). No provider policy is quoted or
summarised here; where a provider's terms restrict training, reuse, or
publication, those restrictions are not waived by preservation in this
repository.

## Footer copyright scope

The site footer line "© 2026 Mirza Husadzic" (see `src/index.html`) asserts the
author's copyright in the study and its presentation only. It does not assert
authorship or ownership of the preserved candidate worlds, the model readings,
the separately attributed observations, or any third-party material that
carries its own notice. The footer's license links ("Content: CC BY 4.0",
"Code: MIT") likewise describe the study's material, not the rights status of
model-generated records or third-party content.

## Reuse and endorsement

Using, adapting or extending this work does not imply the author's
endorsement. That includes running the pipeline with a different seed or
different models, and anything produced that way. Whoever generates or
publishes such material is responsible for it.

CC BY 4.0 already says this in its own terms. Reusers may not suggest that the
author endorses them or their use (Section 2(a)(6)). If the author asks, an
adaptation must remove the author's attribution as far as reasonably
practicable (Section 3(a)(3)).

To report material in this repository for review or removal, write to the
author at [mirza.husadzic@proton.me](mailto:mirza.husadzic@proton.me).

## Third-party material

Existing licenses and credits remain in force for material that carries its
own notice. Bundled dependencies are covered by their respective licenses,
as listed in [Third-party notices](THIRD_PARTY_NOTICES.md).
