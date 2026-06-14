---
title: Intelligence Model Tiering by Process
status: active
canonical: true
subsystem: intelligence-model-tiering
related:
  - project_docs/01_architecture/04_model_choice_and_resolution.md
code_paths:
  - src/universal_agent/utils/model_resolution.py
  - src/universal_agent/services/llm_classifier.py
  - src/universal_agent/services/csi_url_judge.py
  - src/universal_agent/services/csi_intelligence_pass.py
  - src/universal_agent/services/csi_demo_triage_ranker.py
  - src/universal_agent/services/proactive_convergence.py
  - src/universal_agent/proactive_signals.py
  - src/universal_agent/services/proactive_work_recap.py
  - src/universal_agent/backlog_triage.py
  - src/universal_agent/skill_gap_finder.py
  - src/universal_agent/services/health_evaluator.py
  - src/universal_agent/services/decomposition_agent.py
  - src/universal_agent/services/refinement_agent.py
  - src/universal_agent/services/session_dossier.py
  - src/universal_agent/services/claude_code_intel.py
  - src/universal_agent/wiki/llm.py
  - src/universal_agent/urw/evaluator.py
  - src/universal_agent/urw/decomposer.py
  - src/universal_agent/urw/phase_planner.py
  - src/universal_agent/services/mission_control_tier1.py
  - src/universal_agent/services/mission_control_event_titles.py
  - src/universal_agent/services/zai_observability.py
  - src/universal_agent/rate_limiter.py
  - src/universal_agent/services/invariants/zai_inference_health.py
  - src/universal_agent/gateway_server.py
  - src/universal_agent/services/transcript_corpus.py
last_verified: 2026-06-14
---

# Intelligence Model Tiering by Process

This is the **per-process registry** of which model intelligence each LLM call in
the system uses, and *why*. It complements
[`04_model_choice_and_resolution.md`](../01_architecture/04_model_choice_and_resolution.md),
which documents the *mechanism* (tier resolvers, the ZAI tier→model map, execution
profiles, endpoint/credential routing). This document answers a different question:
**for each concrete inference call site, what intelligence level does the task
actually need, and is the cheap model good enough?**

> **Why this document exists.** On **2026-06-10** the `zai_inference_health`
> watchdog fired a critical "ZAI 429 burst" — 50 rate-limit rejections in a
> 43-second window, **100% attributed to `services/llm_classifier.py`** (and ~80%
> of all historical 429s trace to that one module). Root cause was not a broken
> rate-limit control; it was that nearly every classification/extraction call in
> the system silently ran on the **flagship** model (`glm-5.1`) because the shared
> helpers default to `resolve_opus()` and no caller ever specified a cheaper tier.
> High-volume, low-reasoning work (binary judgments, short extractions) was
> contending for flagship concurrency it never needed. This registry records the
> deliberate tier assignment for each call site so the default-to-flagship trap
> does not silently reappear.

---

## 1. The three GLM tiers

Resolvers live in `utils/model_resolution.py`; the canonical map is `ZAI_MODEL_MAP`.

| Resolver | ZAI model | Character | Use for |
|---|---|---|---|
| `resolve_haiku()` | **`glm-4.5-air`** | Lightweight, fast, cheap. **Operator-locked** to `glm-4.5-air` (2026-06-05) — do not remap. | Pure classification, binary judgment, short structured extraction |
| `resolve_sonnet()` | **`glm-5-turbo`** | Standard daily-driver | Nuanced judgment, light synthesis, gating decisions with consequences |
| `resolve_opus()` | **`glm-5.1`** | Flagship, most capable, most concurrency-constrained | Multi-step planning, long-form synthesis, knowledge-graph canonicalization |

> **`glm-4.5-air` is the operator-locked haiku model and is verified working in
> production.** Do not remap the haiku tier away from it. `resolve_haiku()` returns
> `glm-4.5-air` (operator decision, 2026-06-05); the sibling architecture doc's
> `ZAI_MODEL_MAP` reflects the same.

Dedicated, off-tier lanes exist for subsystems that must not contend with the main
agent's budget at all:

- **Mission Control** — `resolve_mission_control_model()` → `glm-4.7` (bypasses the
  tier map; override `UA_MISSION_CONTROL_MODEL`). As of **2026-06-12** this covers **all**
  Mission Control LLM work including the tier-2 Chief-of-Staff readout (`mission_control_chief_of_staff.py::synthesize_readout`),
  which was previously on the opus tier (`resolve_model("opus")` → glm-5.1/glm-5-turbo) but was
  consolidated onto the glm-4.7 lane to keep the readout off the shared sonnet/opus lane it was
  contending on. Override still honored via `UA_MISSION_CONTROL_COS_MODEL` (should be unset in
  Infisical so the code default applies).

---

## 2. The decision rubric

Each call site is assigned a tier by the nature of its task, weighted by how often
it fires:

- **`glm-4.5-air` (haiku) is sufficient when** the call is a *bounded* decision —
  pick one of N categories, a true/false gate, or a short fixed-shape JSON
  extraction — **and** a deterministic fallback already guards a bad/empty answer.
  A small model rarely degrades these, and the cost/concurrency win is large. **The
  more frequently a call fires, the stronger the case for air**, because volume is
  what turns "slightly cheaper per call" into "stops tripping the rate limit."
- **`glm-5-turbo` (sonnet) is the floor when** the decision is nuanced or
  multi-criteria, or a wrong answer has real downstream consequences (mis-routes a
  mission, wrongly gates a retry, clobbers a knowledge surface). Cheaper than
  flagship, but more headroom than air.
- **`glm-5.1` (flagship) is warranted when** the call does genuine multi-step
  reasoning, free-form planning, long-form synthesis, or canonicalization where the
  *meaning* itself is being decided.

> **Call volume is a first-class input, not an afterthought.** Two of the three
> highest-frequency callers in the system (the YouTube tutorial-buildability judge,
> which can fire up to ~200×/5-min sweep, and the convergence topic-signature
> extractor, which fires once per ingested video) were on flagship purely by
> default. They are the clearest air wins precisely because they are both trivial
> *and* frequent.

---

## 3. Rollout status (2026-06-10)

The tier in the **Target** column is the agreed assignment from the 429-burst
remediation. The swap is implemented by threading an optional `model=` parameter
through each helper, resolved behind a per-site `UA_*_MODEL` env knob that defaults
to the target tier — mirroring the existing, proven pattern at
`proactive_convergence.py::triage_candidate` (`UA_INTEL_TRIAGE_MODEL` →
`resolve_haiku()`). Until that change merges, sites marked **was: flagship** still
resolve to `glm-5.1`. Every assignment stays overridable by env var with no code
change, so a tier can be tuned up or down per process if quality or cost dictates.

---

> **2026-06-11 implementation note:** the `llm_classifier.py` wrapper rows below were
> registry intent that the code did not yet implement — the wrappers passed no
> `model=`, so `_call_llm`'s `resolve_opus()` fallback kept them on `glm-5.1`. As of
> 2026-06-11 all five wrappers (`classify_priority`, `classify_agent_route`,
> `generate_calendar_task_description`, `extract_due_at`, `extract_disjointed_tasks`)
> explicitly pass `llm_classifier.py::_classifier_default_model` — **sonnet
> (`glm-5-turbo`) by default**, override via `UA_LLM_CLASSIFIER_DEFAULT_MODEL`. Sonnet
> (not haiku) for the Tier-A-registered rows because live per-model 429 data showed
> ZAI throttling `glm-4.5-air` at ~61% and `glm-5.1` at ~85%+ while `glm-5-turbo`
> flowed clean — move them down to haiku via the env knob once air pressure clears.

## 4. Registry — at a glance


### Tier A — `glm-4.5-air` (haiku)

| Process | Location | What it decides | Volume | Was | Haiku sufficient? |
|---|---|---|---|---|---|
| Task priority classify | `llm_classifier.py::classify_priority` | one of P0–P3 + 1-sentence reason | medium | flagship | Yes — 4-way classify, heuristic fallback |
| Calendar → task description | `llm_classifier.py::generate_calendar_task_description` | 2–4 sentence prep blurb + labels | medium (batched) | flagship | Yes — short bounded generation |
| Email due-date extraction | `llm_classifier.py::extract_due_at` | one ISO datetime or none | low (regex-gated) | flagship | Yes — single-field extraction, validated |
| Tutorial-buildability judge | `llm_classifier.py::classify_tutorial_buildability` | buildable true/false | **high** (≤200 / 5-min) | flagship | Yes — binary gate, fail-closed to false |
| CSI URL judge | `csi_url_judge.py` | per-URL worth_fetching + enum category | high | flagship | Yes — structured per-URL classification |
| Convergence signature extract | `proactive_convergence.py::extract_topic_signature_from_text` | small fixed JSON (topics/claims/type) | **high** (per video) | flagship | Yes — short extraction, deterministic fallback |
| Claude-Code intel tier classify | `claude_code_intel.py` (`_call_sync_llm`) | tier 1–4 / action_type enum | medium | flagship | Yes — enum classify, heuristic fallback |

### Tier B — `glm-5-turbo` (sonnet)

| Process | Location | What it decides | Volume | Was | Why not haiku |
|---|---|---|---|---|---|
| Agent routing | `llm_classifier.py::classify_agent_route` | Simone / CODIE / ATLAS | low (≤5 / sweep) | flagship | Wrong route mis-delegates a whole mission |
| Email task-splitting | `llm_classifier.py::extract_disjointed_tasks` | split into N tasks, keep context | low (default-off) | flagship | Segmentation+context risk; air may over/under-split |
| Convergence cluster-refine | `proactive_convergence.py::_refine_cluster_with_llm` | is this bucket a real convergence? | **high** (≤2 concurrent; ~89% cache-hit) | flagship→**sonnet** (A/B 2026-06-10) | Precision gate; air/4.7 over-confirm — sonnet is the floor, matches opus |
| Heartbeat health evaluator | `health_evaluator.py` | ignore / directive / escalate | low | flagship | Misroute drops a real human escalation |
| Brainstorm refinement | `refinement_agent.py` | advance / question / hold + rewrite | low | flagship | Mixed judgment + rewrite, light synthesis |
| URW completion judge | `urw/evaluator.py` (`LLMJudgeEvaluator`) | 0–1 rubric score, gates retries | low | flagship | Too-lenient/harsh score wrongly passes or burns retries |
| URW phase planner | `urw/phase_planner.py` (`PhasePlanner`) | group tasks into phases | low | flagship | Constrained combinatorial reasoning |
| Proactive work-recap eval | `proactive_work_recap.py` | implemented / issues / assessment | low | flagship | Evidence-grounded judgment; "don't infer success from silence" |
| Wiki semantic extraction | `wiki/llm.py` (`extract_entities` / `extract_concepts` → `_extract_model()`) | entities / concepts | low–medium | **turbo** | Bounded structured extraction; moved opus→turbo 2026-06-13 (`UA_WIKI_EXTRACT_MODEL`) |
| Wiki summary generation | `wiki/llm.py` (`generate_summary`) | 1–3 sentence summary | low–medium | flagship | Generative knowledge-store surface; kept on opus (quality-sensitive) |

### Tier C — `glm-5.1` (flagship, keep)

| Process | Location | Why flagship |
|---|---|---|
| CSI knowledge-vault extraction | `csi_intelligence_pass.py` | Explicit GLM-5.1 module contract; CREATE/EXTEND/REVISE + canonicalization + relation typing |
| Track-B ideation synthesis | `proactive_convergence.py::track_b_ideation_synthesis` | Non-obvious cross-cutting macro-trend synthesis — the high-value engine. **Kept on flagship by default but now low-volume:** one whole-corpus call (was 3×20 recency batches) + a new-content gate (`_ideation_should_run`) cut it from ~16 sweeps/day to only sweeps with fresh material, so flagship quality is affordable without throttling. Escape hatch: `UA_IDEATION_MODEL` (e.g. `glm-5-turbo`) moves it off the contended opus tier with no deploy |
| Feedback → rules distiller | `proactive_signals.py::distill_feedback_to_rules` | Rewrites an entire rules markdown doc in place; weak model risks clobbering it |
| Task decomposition (gateway) | `decomposition_agent.py` | Free-form multi-step planning; split/ordering cascades into every subagent |
| URW task decomposer | `urw/decomposer.py` (`LLMDecomposer`) | Free-form atomic-task-graph planning that drives the whole URW run |
| Convergence intel-brief authoring | `evaluate-and-author-intel-brief` SKILL.md | Long-form synthesis from full transcripts; low volume (accepted survivors only) |

**Intel-brief authoring — transcript corpus and deterministic provenance (2026-06-11):**
Convergence briefs are now authored from the **full persisted transcript corpus** instead
of the ~300-char distilled `key_claims`. Each YouTube transcript fetched during CSI
enrichment (`CSI_Ingester/development/scripts/csi_rss_semantic_enrich.py::_persist_transcript`)
is written to the `youtube_transcripts` table in `csi.db` (migration
`0014_youtube_transcripts`). At brief-authoring time,
`services/transcript_corpus.py::load_full_sources_for_candidate` enriches each signature
with `full_transcript` from the corpus (falling back to `key_claims` only when no
persisted text exists). The authoring model is captured deterministically:

```python
from universal_agent.utils.model_resolution import resolve_opus
authoring_model = (os.environ.get("ANTHROPIC_DEFAULT_OPUS_MODEL") or "").strip() or resolve_opus()
# Always glm-5.1 on the ZAI execution profile.
```

This replaces the previous "read from runtime env var, fall back to flagship model"
heuristic with a code-derived value that is always the opus tier (glm-5.1). Gating
stages (`triage_candidate`, `_refine_cluster_with_llm`) are unchanged.

### Already off-flagship / out of scope (no change)

| Process | Location | Current | Note |
|---|---|---|---|
| Mission Control card discovery | `mission_control_tier1.py` | `glm-4.7` | Dedicated lane via `resolve_mission_control_model()` |
| Mission Control event titles | `mission_control_event_titles.py` | `glm-4.7` | Dedicated lane |
| Mission Control Chief-of-Staff readout (tier-2) | `mission_control_chief_of_staff.py::synthesize_readout` | `glm-4.7` | **Moved off opus → glm-4.7 on 2026-06-12** to keep it off the shared sonnet/opus lane; unset Infisical `UA_MISSION_CONTROL_COS_MODEL` |
| CSI demo-triage ranker | `csi_demo_triage_ranker.py` | `glm-4.6` | Already below flagship; `UA_CSI_DEMO_TRIAGE_MODEL` knob exists |
| Backlog triage synthesizer | `backlog_triage.py` | `glm-5-turbo` | Already sonnet (`resolve_sonnet`) |
| Skill-gap finder synthesizer | `skill_gap_finder.py` | `glm-5-turbo` | Already sonnet |
| Discord relevance filter | `discord_intelligence/relevance_filter.py` | `glm-4.5-air` | Already air (config `models.relevance`) |
| Pre-Task-Hub intel triage | `proactive_convergence.py::triage_candidate` | `glm-4.5-air` | Already air via `UA_INTEL_TRIAGE_MODEL` — the template pattern |
| Session dossier generator | `session_dossier.py` | `glm-4.5-air` | Already air via `resolve_haiku()` |
| Vision describe endpoint | `gateway_server.py` `/api/v1/vision/describe` | `glm-5.1` | **Out of scope** — needs a vision-capable model; do not route to air |

---

## 5. Detailed rationale

The four explicit questions for each candidate: *what does it do*, *how often does
it fire*, *why this tier*, and *is haiku sufficient*.

### Tier A — moved to `glm-4.5-air`

**Task priority classification** — `llm_classifier.py::classify_priority`.
Assigns an incoming task one of four priority buckets (P0 immediate → P3 background)
with a one-sentence justification, used to order the proactive queue. Fires once per
materialized task (calendar/email intake) — medium volume. A four-way bucketing with
a deterministic heuristic fallback is the canonical low-reasoning, short-output task;
**air is sufficient** and the prompt's rubric does the heavy lifting.

**Calendar event → task description** — `llm_classifier.py::generate_calendar_task_description`.
Turns a calendar event into a 2–4 sentence "what to prepare" blurb plus a couple of
labels. Fires per calendar event (paired with priority, so two calls/event), batched
when a window of events materializes at once. Short, bounded generation from
structured fields; **air is sufficient**.

**Email due-date extraction** — `llm_classifier.py::extract_due_at`.
Extracts a single ISO datetime (a deadline/execution time) from email text, or none.
A regex pre-check already short-circuits most emails before any LLM call, so live
volume is low. The output is one validated field; **air is sufficient**.

**YouTube tutorial-buildability judge** — `llm_classifier.py::classify_tutorial_buildability`.
A binary true/false: could a coding agent build a working demo from this video? It is
driven by the proactive-signal sync, which loops over uncached build-oriented CSI
videos (up to ~200 per row-limit) on a ~5-minute cooldown — **the single
highest-volume caller and the prime suspect in the 2026-06-10 burst**. It is a binary
gate with an explicit "when uncertain, return false" rule and a fail-closed fallback.
**Air is sufficient**, and the volume makes it the highest-value downgrade. (The
graded-judge redesign adds an opt-in 0–100 score + `UA_TUTORIAL_BUILD_THRESHOLD`
cutoff and a determinism knob `UA_TUTORIAL_BUILD_TEMPERATURE` /
`UA_LLM_JUDGE_TEMPERATURE` — both default unset = today's binary behavior; the tier is
unchanged. See [`06_platform/10_zai_rate_limiter.md`](../06_platform/10_zai_rate_limiter.md) §7.1.1.)

**CSI URL judge** — `csi_url_judge.py`.
Per-URL classification (worth_fetching boolean + fixed-enum category) over a candidate
list, gating which links the research pass actually fetches. High volume during CSI
passes. Pure structured per-URL classification; **air is sufficient**.

**Convergence topic-signature extraction** — `proactive_convergence.py::extract_topic_signature_from_text`.
Distills a transcript into a small fixed JSON shape (primary/secondary topics, key
claims, content type) used downstream for clustering. Fires **once per ingested
video** — the highest sustained volume in the convergence group — and has a
deterministic `_fallback_signature`. Short fixed-shape extraction; **air is
sufficient**, and the per-video frequency makes it a top cost win.

**Claude-Code intel tier classifier** — `claude_code_intel.py` (`_call_sync_llm`).
Classifies a ClaudeDevs/X post into a tier (1–4) and action_type enum, with a
deterministic heuristic fallback. Medium volume. Enum classification; **air is
sufficient**.

### Tier B — moved to `glm-5-turbo`

**Agent routing** — `llm_classifier.py::classify_agent_route`.
Chooses which agent handles a task (Simone / CODIE / ATLAS); the prompt explicitly
warns against keyword-counting and demands intent judgment. Low volume (the dispatch
sweep caps at 1–5 routings per cycle). **Not air**: a wrong route mis-delegates an
entire mission, and the low volume means the cost of turbo is negligible — buy the
headroom.

**Email task-splitting** — `llm_classifier.py::extract_disjointed_tasks`.
Splits one email into multiple independent tasks while preserving each task's context.
Gated behind a default-off flag, so ~0 live volume. **Not air**: segmentation that
must carry context is where a small model over/under-splits or drops detail.

**Convergence cluster-refine** — `proactive_convergence.py::_refine_cluster_with_llm`
(model knob: `proactive_convergence.py::_cluster_judge_overrides`).
The precision layer: judges whether a coarse SQL bucket genuinely converges on one
thesis across independent channels, dropping false buckets. As of 2026-06-10 it
defaults to the **sonnet tier (`glm-5-turbo`)**, down from the former opus default
(`glm-5.1`), and runs **sequential, 1-wide** (`UA_CONVERGENCE_LLM_CONCURRENCY`,
default lowered 6 → 2 on 2026-06-10, then **2 → 1 on 2026-06-13**) — both to stop
this fan-out being the dominant ZAI Fair-Usage 429/1313 burst contributor. The
final 2 → 1 is a storm-avoidance step: ZAI 429s are driven by request *concurrency*
(a call overlapping another rejects ~77% vs ~10% sequential), so even 2-wide
self-overlaps. As of 2026-06-13 the judge also runs **BATCHED** — one structured-
output call per chunk of `UA_CONVERGENCE_JUDGE_BATCH_SIZE` buckets (default 20,
`_refine_clusters_batched`); a batch-size sweep (61 live buckets vs adjudicated
truth) showed ~20/call beats both per-bucket (F1 0.78, 61 calls) and one-giant-call
(F1 0.67) at **F1 0.84, 4 calls, ~half the tokens**. **A/B-confirmed** (`scripts/convergence_model_ab.py`, 30 live buckets,
run twice): glm-5-turbo matches the opus default's precision (both 2/30) at ~35%
lower latency, while `glm-4.5-air` (15/30) and `glm-4.7` (11/30) over-confirm
broad-topic buckets and fail this precision gate. **Turbo is the precision floor; air
is not viable here.**

**TTL-bounded result cache (2026-06-11):**
`proactive_convergence.py::_refine_cache_get` / `_refine_cache_put` add a
SQLite-backed result cache keyed on the bucket's sorted-video-id set
(`proactive_convergence.py::_refine_cluster_key`), stored in the
`convergence_refine_cache` table (`proactive_convergence.py::ensure_schema`). A
cache HIT within the TTL reuses the stored verdict and skips the ZAI LLM call
entirely — zero quality change because a bucket that gains or loses a video produces
a new key and is always re-judged. ~89% of buckets re-judge identically across
consecutive hourly runs, so this reduces the effective sonnet-call volume by ~89%.
Call counts are surfaced per-run via `"sonnet_calls_made"` / `"cache_hits"` keys in
the `sync_topic_signatures_from_csi` return dict (flows into `latest_sync.json`).

Knobs (both env-overridable, no deploy needed):
- `UA_CONVERGENCE_REFINE_CACHE_ENABLED` — `1`/`true` (default on) / `0` to always
  re-judge every bucket.
- `UA_CONVERGENCE_REFINE_CACHE_TTL_HOURS` — float, default `24`; a row older than
  this is a miss and the LLM is re-invoked.

**Heartbeat health evaluator** — `health_evaluator.py`.
Classifies morning-report items into ignore / directive / escalate against a
lessons-learned doc. Low volume (heartbeat-driven). **Not air**: a wrong call routes
noise to a human or, worse, misses a real escalation.

**Brainstorm refinement** — `refinement_agent.py`.
Decides advance / clarifying-question / hold and rewrites an enriched description. Low
volume. **Not air**: mixed judgment + rewriting is light synthesis, above air's
comfortable range.

**URW completion judge** — `urw/evaluator.py` (`LLMJudgeEvaluator`).
Scores an artifact 0–1 against a rubric and gates task completion/retry loops. Low
volume. **Not air**: a miscalibrated score wrongly passes work or burns retries.

**URW phase planner** — `urw/phase_planner.py` (`PhasePlanner`).
Groups atomic tasks into execution phases under token/dependency constraints; simple
cases use a pure-Python heuristic and only moderate/complex sets invoke the LLM. Low
volume. **Not air**: constrained combinatorial reasoning, with a heuristic fallback
making turbo (not flagship) the right floor.

**Proactive work-recap evaluator** — `proactive_work_recap.py`.
Reads a workspace/transcript evidence bundle and produces a grounded multi-field
verdict (implemented / known_issues / success_assessment / confidence) that must not
infer success from silence. Low volume; `UA_PROACTIVE_RECAP_LLM_MODEL` knob already
exists. **Not air**: nuanced evidence-grounded reasoning.

> **2026-06-12 limiter fix:** `proactive_work_recap.py::_call_llm_recap_evaluator` previously
> used a raw `Anthropic()` SDK client, bypassing the AIMD per-tier rate limiter entirely.
> Live data showed ~64% hard-fail when the ZAI sonnet tier was momentarily saturated (the
> raw client has no backoff). The function now routes through
> `llm_classifier.py::_call_llm` via the same sync→async bridge pattern used by
> `proactive_convergence.py::_detect_clusters_llm` — so `with_rate_limit_retry` handles
> backoff when `UA_LLM_CLASSIFIER_LIMITER_ENABLED=1`. The heuristic session-evidence
> fallback (`_evaluate_recap` → `evaluation_status="llm_failed_fallback"`) is unchanged;
> the model tier (`glm-5-turbo` via `UA_PROACTIVE_RECAP_LLM_MODEL`, defaulting to
> `resolve_opus()` when unset) is unchanged.

**Wiki semantic extraction** — `wiki/llm.py`. The bounded extraction stages
`extract_entities` / `extract_concepts` now pass `model=_extract_model()` → **turbo**
(`glm-5-turbo`, env `UA_WIKI_EXTRACT_MODEL`), moved off the `_call_llm` `resolve_opus()`
default 2026-06-13 (they were observed burning real flagship tokens on the ZAI token
panel). By task-shape they are air-eligible, **but held at turbo deliberately**: they
feed a *knowledge store*, so extraction noise compounds into later retrieval and
linking, and at this volume the air-vs-turbo cost delta is marginal — when output
compounds and savings are small, take the safer tier. `generate_summary` is separate:
it is generative (1–3 sentence prose) and **kept on flagship** (`resolve_opus()` default).

### Tier C — kept on `glm-5.1`

**CSI knowledge-vault extraction** (`csi_intelligence_pass.py`) carries an explicit
GLM-5.1 contract in its module docstring ("Code never decides what is meaningful; the
LLM does") — it makes CREATE/EXTEND/REVISE judgments, canonicalizes entities, and
types relations; downgrading reintroduces the exact noise the prompt fights.
**Track-B ideation synthesis** (`proactive_convergence.py::track_b_ideation_synthesis`)
is the deliberately high-value engine for non-obvious cross-cutting trends — it was
restored *because* GLM quota is abundant, so cheapening it defeats its purpose.
**Feedback → rules distiller** (`proactive_signals.py::distill_feedback_to_rules`)
rewrites an entire `generation_rules.md` in place (`max_tokens=5000`); a weak model
risks clobbering existing rules. **Task decomposers** (`decomposition_agent.py` and
`urw/decomposer.py`) do free-form multi-step planning whose split granularity and
dependency ordering cascade into every downstream subagent.

---

## 6. Implementation convention

Every reassignment follows one pattern, so the registry stays enforceable and
reversible:

```python
# at the call site (mirrors proactive_convergence.py::triage_candidate)
from universal_agent.utils.model_resolution import resolve_haiku  # or resolve_sonnet
model = (os.getenv("UA_<SITE>_MODEL") or "").strip() or resolve_haiku()
... = _call_llm(system=..., user=..., model=model)
```

- Shared helpers that default to `resolve_opus()` (`llm_classifier.py::_call_llm`,
  `wiki/llm.py::_call_llm`) accept an optional `model=` param threaded from the public
  function, so the per-tier choice lives at the *caller*, not buried in the default.
- The env knob lets the operator pin any site to a different model without a code
  change (e.g. promote a site back to `glm-5.1`, or push a turbo site down to air
  after a quality check).
- New inference call sites **must** specify a tier explicitly. Relying on the
  `resolve_opus()` default is what produced the 2026-06-10 burst.

## 7. Related

- [`01_architecture/04_model_choice_and_resolution.md`](../01_architecture/04_model_choice_and_resolution.md)
  — tier resolvers, ZAI tier→model map, execution profiles, endpoint/credential
  routing.
- `rate_limiter.py` (`ZAIRateLimiter`, `with_rate_limit_retry`) — the global
  concurrency + adaptive-backoff control. Most direct-SDK callers above **bypass** it;
  model tiering reduces the *pressure*, throttling/wrapping addresses the *concurrency*.
- `services/zai_observability.py` + `services/invariants/zai_inference_health.py` —
  the httpx-hook events log and the watchdog that attributed the 2026-06-10 burst to
  `llm_classifier.py`.
