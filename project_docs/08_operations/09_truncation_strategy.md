# Truncation strategy — no blind cuts on LLM-bound content

**Operator directive (2026-08-08):** blind truncation — cutting a string at N
chars with no understanding of what is being cut — degrades assessment quality
("incredible stupidity"): the model reasons over amputated context and the
intelligence is not generated properly. Cutting is only acceptable when we can
say *what* is being cut and *why it doesn't matter*. This document is the
standing program: the taxonomy, the 2026-08-08 baseline inventory of every
truncation site, the ranked remediation list, and the rules for new code.

Measured backdrop: the token cost that motivates caps is real (Mission Control
tier-1 once ran ~150k tokens/call, 64% of all ZAI tokens) — but the fix that
actually worked there was **delta-gating** (skip the call when nothing
changed) plus **per-field bounded caps**, not whole-document truncation. Cost
control and context integrity are not in tension when the cut is designed.

## Taxonomy — classify every site as one of three

- **NOISE-CUT** (acceptable): cuts boilerplate, markup, or content whose loss
  provably doesn't change the judgment (a 500-char cap on tweets that X
  length-caps anyway; a security cap on calendar-invite descriptions).
- **ITEM-BOUND** (usually acceptable): caps *how many* items, not item
  content — top-N by an explicit relevance order. The order must be stated
  and defensible (recency, score), and the drop should be logged.
- **CONTEXT-AMPUTATION** (the defect class): cuts evidence mid-thought — a
  transcript head-sliced before analysis, a fetched doc cut at 4k, a JSON
  blob sliced mid-object. Every site classified this way is a remediation
  candidate below.

## Rules for new code (and for touching old sites)

1. **Prefer not calling over cutting.** Delta-gate (signature-skip when inputs
   are unchanged) before you shrink inputs — it saves 100% of the tokens and
   0% of the context. `mission_control_tier1.evidence_signature` is the
   template.
2. **Prefer structure over bulk.** A schema-forced structured-output call over
   extracted fields beats a free-text call over a raw dump: quality is
   knowable, size is bounded by design. When input is genuinely long, use
   map-reduce (per-chunk structured extraction → synthesis over the
   extracts), as `youtube_daily_digest._reduce_meta_synthesize` already does.
3. **If you must cap content, cap per-FIELD with bounded blast radius** —
   every item survives, only oversized fields shrink (tier-1's
   `_cap_field`) — never whole-document head-slices.
4. **Pick the end that carries the signal.** Head-keep for documents (thesis
   first); TAIL-keep for logs, transcripts of runs, and error output — the
   failure signature lives at the end (`vp_failure_rescue` and
   `notification_dispatcher` get this right; `cody_evaluation` gets it wrong).
5. **Every cut is marked and surfaced.** Append an explicit
   `[truncated: kept X of Y chars]` marker in the content AND a boolean/count
   the caller can see (`youtube_ingest.transcript_truncated` is the
   reference). A silent slice (`text[:2000]` with no marker) is a bug.
6. **Never slice serialized JSON as text.** Select items first, then
   serialize (`backlog_triage.py:135` is the counterexample).
7. **Env-knob every cap** so ops can raise it without a deploy, and remember
   **Infisical injects env invisibly to on-disk grep** — check it before
   concluding a knob is at its default (`UA_ZAI_EVENTS_MAX_LINES=50000` hid
   there).
8. **max_tokens is not truncation** — it's the response budget. But set too
   low it truncates the model's own JSON mid-object (`cleanup_report.py`
   exists to catch exactly that); size it to the schema.

## Baseline inventory (2026-08-08, three-agent sweep of src/universal_agent)

Full per-site evidence lives in the session's agent reports; this table is the
durable index. ~40 sites examined; response-budget knobs and human-facing
display trims excluded.

| Site | What's cut | Class |
|---|---|---|
| `memory/context_manager.py:198-205` — tool_result content in pruned history: 200-char blind keep, re-injected as `initial_history_prompt` on SDK reconnect | live conversation evidence amputated mid-session; the code's own comment admits replies "might look like hallucination" | **AMPUTATION (core loop)** |
| `hooks_service.py:6003` — `response_preview[:4000]` head-keep feeding `parse_email_triage_brief` | the JSON safety/routing envelope is emitted at the END of the message — a head cut can silently drop the safety decision | **AMPUTATION (safety-adjacent)** |
| `session_checkpoint.py:163` / `urw/context_summarizer.py:147` — final blind tail-cut of the checkpoint after per-section caps | "Remaining Tasks" is appended last, so the most actionable section vanishes first | **AMPUTATION** |
| `prompt_builder.py:148,627` — 4,000-char blind cut on RECOVERY_HANDOFF.md / MISSION_BRIEFING.md ("follow these instructions FIRST"); `recovery_handoff.py:205` slices tool-call-evidence JSON as text | mandatory instructions and the don't-repeat-failures evidence trail cut blind (double-truncation chain) | **AMPUTATION** |
| `wiki/llm.py:150,168,186,235,361` — 4,000-char head-cut on ALL entity/concept/summary extraction | every wiki artifact derives from the first ~4k chars of any source | **AMPUTATION (worst per-artifact)** |
| `claude_code_intel.py:962,964` — 4,000-char re-slice of post + linked context | undoes the documented v2 fix that removed this exact cut one hop upstream | **AMPUTATION (regression)** |
| `cody_evaluation.py:202-203,220-221,336-337` — 4000-then-2000 double head-cut; `cody_implementation.py:369` silent 2000 on rerun stdout/stderr | demo-review judge sees only the HEAD of run output; failure signatures live at the tail | **AMPUTATION** |
| `backlog_triage.py:135` — `json.dumps(...)[:18000]` | serialized backlog JSON cut mid-object, unvalidated | **AMPUTATION** |
| `tools/corpus_refiner.py:156,331` — 50k/article (self-flagged TODO), 30k head for outline themes | later articles' themes never seen | **AMPUTATION** |
| `scripts/generate_outline.py:50` — corpus `[:100000]` | outline built from corpus head only | **AMPUTATION** |
| `scripts/youtube_daily_digest.py:750,2825` — transcript `[:50000]` (env-knobbed at the map site) | long-form videos lose mid/late content before retell | **AMPUTATION** |
| `csi_intelligence_pass.py:336-402` 8k/source; `claude_code_intel_replay.py:656` 8k | fetched linked sources cut, no env knob | **AMPUTATION** |
| `proactive_auto_investigator.py:222` — task description `[:500]` | what-was-being-investigated thinned | AMPUTATION (small) |
| `email_task_bridge.py:1331` — reply `[:2000]` into task description | operator intent amputated in the stored task | AMPUTATION (small) |
| `skill_gap_finder.py:398-401` — 60k backstop cuts the DEDUP list first (rendered last) | re-proposal risk on heavy weeks | ordering bug |
| `mission_control_tier1.py` per-field caps (env-knobbed); `proactive_convergence` corpus/index caps; `session_dossier` 480k backstop; `proactive_work_recap` tail-keep 10k; `vp_failure_rescue` tail 2k; `csi_url_judge` v2 200k storage; `csi_watchlist`, triage-ranker, calendar bridge, rollup/item caps, nuggets judge chunking | designed, documented, or generous | NOISE-CUT / ITEM-BOUND — leave alone |
| `transcript_corpus.load_full_sources_for_candidate` — deliberately UNCAPPED full transcripts × cluster size for brief authoring | by design (anti-truncation); unbounded on two axes | watchlist: monitor via token telemetry |

## Remediation program — ranked, gated on ZAI being back online

Each fix is LLM-behavioral, so it ships one-at-a-time with a before/after
check (output quality on a fixed case + token delta from the usage lanes /
demo_factory token ledger). The weekly ZAI usage report
(`universal-agent-zai-usage-report.timer`, Wednesdays) carries this program as
a standing agenda item.

1. **context_manager tool-result slice (core loop)** → summarize-or-keep per
   result (structured "what this tool returned" extraction), or at minimum a
   much larger tail-aware keep with markers. Touches every long session's
   reasoning quality — highest leverage in the estate.
2. **hooks_service email-triage head-cut** → tail-keep (the envelope is at
   the end) or extract the JSON envelope explicitly before any cut.
   Safety-adjacent; small fix.
3. **checkpoint/summarizer last-section drop** → reserve budget per section
   (Remaining Tasks first-class), never a final blind cut over the whole
   string.
4. **prompt_builder instruction-file cuts** → fail loud (warn + surface
   `truncated=true`) and raise the cap; instructions should be short by
   contract, but cutting them silently is never acceptable.
5. **wiki/llm.py 4k cap** → map-reduce: per-chunk structured extraction,
   merge entities/concepts; summary from extract-of-chunks. Highest quality
   payoff per token among content flows.
6. **claude_code_intel.py:962 regression** → delete the re-slice; rely on the
   upstream v2 contract + a per-field bound if cost demands one.
7. **Cody evaluation/rerun cuts** → tail-keep with markers, single cap
   (kill the double-cut), env knob. Directly improves demo-review verdicts.
8. **backlog_triage JSON slice** → item-bound selection before serialize.
   Same fix for `recovery_handoff.py:205`.
9. **corpus_refiner + generate_outline** → per-article structured extraction
   feeding the outline (map-reduce), replacing head-slices.
10. **youtube transcript 50k** → chunked retell or raised env default; the
    map-reduce skeleton already exists.
11. **csi_intelligence_pass / replay 8k** → env knobs + boundary-aware cut
    with markers.
12. **skill_gap_finder ordering** → reserve budget for the dedup section.
13. **email_task_bridge** → store full body retrievably; cap only the display.

## Verification loop

Truncation fixes change LLM behavior — never batch them. Per fix: PR with the
site's before/after classification in the body → merged on green → next
weekly report + `/dragan:zai-usage-audit` run confirms the token delta is
acceptable and the quality gain is real (spot-check one artifact produced by
the changed flow). A fix that balloons tokens gets a delta-gate or map-reduce
pass, not a reverted cap.
