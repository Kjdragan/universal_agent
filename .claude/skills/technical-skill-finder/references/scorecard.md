# Candidate Scorecard

Score each candidate 0–5 in each dimension:

- `frequency` — repeated occurrences across **distinct sessions** (count transcripts, not lines).
- `impact` — time-to-resolution / incident severity when the pattern hits.
- `actionability` — can the pattern be handled by deterministic checks/workflows.
- `toolability` — enough local tooling commands (`jq`, `rg`, shell, MCP) to automate it.
- `novelty` — not already covered by an existing skill (the dedupe result from step 5).

`confidence` = average of the five scores / 5.

## Prioritization thresholds

Surface a candidate as high-priority only when **all** hold:
- `frequency >= 3`
- `confidence >= 0.72`
- `impact >= 3`

Return the top `TOP_N` (default 5) by `confidence`, breaking ties by `frequency` then `impact`.

## Ranking line in the report

Each emitted candidate carries: `frequency`, `impact`, `confidence`, and `skill-fit`.

- `skill-fit` is **not** a scored 0–5 dimension. It is the dedupe verdict from step 5:
  `new` (no overlap with any existing skill) or `update <existing-skill-name>` (high overlap, extend it).
- `novelty` is the 0–5 score that *feeds* the `skill-fit` verdict: low novelty → likely `update`,
  high novelty → likely `new`.

`actionability` and `toolability` are scored (they affect `confidence`) but are not surfaced on the
one-line ranking; include them in the candidate's detail block.
