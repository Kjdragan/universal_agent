---
name: evaluate-and-author-intel-brief
description: >
  Atlas batch skill for evaluating one or more convergence candidates and
  authoring intel briefs for those that clear the bar. Reads the recent
  briefs index for prior-verdict context, processes candidates serially
  within the same mission, and produces ship/skip/defer verdicts plus
  full HTML briefs for ships. When a candidate was already shipped by the
  pre-Task-Hub triage (``metadata.triage.kind=='ship'``) the in-mission
  ship/skip/defer rubric is SKIPPED — the verdict is already 'ship' and the
  skill only authors. When no triage verdict is present (triage disabled /
  legacy task) the skill decides as before. Pre-signs feedback URLs at
  artifact-write time and stores them in artifact metadata.
---

# Evaluate and Author Intel Brief

## Overview

This is Atlas's batch evaluation skill. It runs once per mission against
one or more `convergence_candidate` Task Hub items that were claimed in the
same dispatch batch.

Each candidate represents a cluster of recent (≤72h) videos from at least
two independent channels covering the same primary topic. The skill must:

1. Read the **recent briefs index** (last 48h of ship/skip/defer verdicts)
   so Atlas's evaluation is grounded in prior decisions.
2. Walk the batch **serially**: each verdict is appended to the index file
   immediately so the next candidate in the same batch sees it.
3. For each candidate, choose `ship` / `skip` / `defer` based on novelty,
   source strength, operator-rating history, and overlap with very recent
   prior briefs.
4. For ships only: author the full HTML brief, copy it to the durable
   `artifacts/intel/<artifact_id>.html` location, pre-sign feedback URLs,
   register the artifact via `proactive_artifacts.upsert_artifact`.
5. Update the `convergence_candidates` row (`verdict`, `verdict_reasoning`,
   `artifact_id`, `evaluated_at`) and close the Task Hub item.

**This skill never sends email.** Delivery is handled by Simone's
`/hourly-intel-digest` skill (PR D).

## When to use it

Invoke this skill when a `convergence_candidate` Task Hub item is claimed.
The task description carries the `candidate_id` and the path to the recent
briefs index. If multiple `convergence_candidate` items are dispatched in
the same batch, this skill processes them all in one mission — that's the
intended pattern.

## Inputs

From the Task Hub task metadata (one item per candidate in the batch):

- `candidate_id` — primary key into `convergence_candidates`
- `preferred_vp` — should be `vp.general.primary` (Atlas)
- `index_path` — absolute path to `recent_briefs_index.md` (empty string
  means: use the helper's default)
- `invoke_skill` — should be `evaluate-and-author-intel-brief`
- `triage` — present when the upstream pre-Task-Hub triage decided this
  candidate. Shape: `{"kind": "ship", "reasoning": str, "demo_amenable": bool,
  "model": str}`. When `kind=='ship'`, **the verdict is already decided** —
  see Phase 0.5; do not re-run the rubric. Absent ⇒ triage was off / legacy
  task ⇒ decide in Phase 1 (kill-switch path).

From environment (resolved at runtime, NOT from task metadata):

- `UA_GATEWAY_BASE_URL` — used to build the pre-signed feedback URLs
- `UA_RECENT_BRIEFS_INDEX_PATH` — overrides `index_path` from task metadata
  when set; otherwise the default resolver in `recent_briefs_index.py` wins

## Phase 0 — Orient

Open a Python helper task and:

1. Load every claimed batch task's `candidate_id` from the Task Hub items
   that were dispatched in this mission. Fetch each row from
   `convergence_candidates` (DDL in `services/proactive_convergence.py`):

   ```python
   import json
   import sqlite3

   conn = sqlite3.connect("/path/to/activity_state.db")
   conn.row_factory = sqlite3.Row
   row = conn.execute(
       "SELECT * FROM convergence_candidates WHERE candidate_id = ?",
       (candidate_id,),
   ).fetchone()
   signatures = json.loads(row["signatures_json"])  # cluster sources
   video_ids = json.loads(row["video_ids_json"])
   channels = json.loads(row["channel_names_json"])
   primary_topics = json.loads(row["primary_topics_json"])
   ```

2. Read the recent briefs index. Use `read_index_or_fallback` so a missing
   or corrupted file does NOT crash the mission — it rebuilds from DB:

   ```python
   from universal_agent.services.recent_briefs_index import read_index_or_fallback
   index_text = read_index_or_fallback(conn, lookback_hours=48, limit=200)
   ```

3. Parse the index into a quick mental model: list of prior `[SHIP]`,
   `[SKIP]`, `[DEFER]` blocks with their `thesis`, `decided_at`,
   `key_entities`, `operator_rating`. The structured markdown is grep-friendly
   on purpose — Atlas should read it as context, not as data.

## Phase 0.5 — Triage short-circuit (validation already done upstream)

The ship/skip/defer decision now happens **before** the Task Hub, in the
cheap pre-Task-Hub triage (`services/proactive_convergence.py::triage_candidate`).
A `convergence_candidate` Task Hub item is **only created when triage already
returned `ship`** — skip/defer/retry candidates never become tasks (and never
reach this skill). So for any task carrying a triage verdict, re-deciding here
is redundant double-work.

For each candidate, read its task metadata (and the `convergence_candidates`
row's `metadata_json.triage`). Then:

- **`metadata.triage.kind == "ship"`** (the normal path under triage):
  the verdict is **already `ship`**. **Do NOT run the Phase 1 decision rubric.**
  Set `verdict = "ship"`, `verdict_reasoning =` the triage reasoning
  (`metadata.triage.reasoning`), capture `demo_amenable =
  bool(metadata.triage.demo_amenable)`, append the verdict to the index
  (Phase 1.3), then go straight to **Phase 2 (author)**. This is the common
  case and the whole point of the upstream triage — author, don't re-judge.

- **No `metadata.triage` present** (triage disabled via
  `UA_INTEL_TRIAGE_ENABLED=0`, or a legacy task queued before triage existed):
  fall through to **Phase 1** and decide ship/skip/defer here exactly as
  described below. This preserves the kill-switch: with triage off, this skill
  is still the gate. `demo_amenable` is unknown in this path — treat as `false`.

> Why this is safe: `write_convergence_candidate` only queues a task on a
> triage `ship`, so the only triage verdict this skill can observe is `ship`.
> The branch above is therefore "author-only when triage decided, decide-then-
> author when it didn't" — no candidate is ever published without *some* gate.

## Phase 1 — Evaluate each candidate serially

For each candidate in the batch (in the order they were claimed):

### 1.1 Build the evaluation context

For the current candidate, compile:

- Distinct channels covering the cluster (from `channel_names_json`)
- Primary topics (from `primary_topics_json`)
- Per-channel key claims (from `signatures_json[*].key_claims`)
- Source URLs (from `signatures_json[*].video_url`)

### 1.2 Apply the decision rubric

**FIRST check `metadata.candidate_kind`** (from the candidate row / task metadata):

- **`ideation`** — this is NOT a same-story convergence. It is a non-obvious
  abstract pattern, trend, or cross-cutting relationship synthesized by the
  Track B ideation sweep (the narrative + "so what" are in `metadata.thesis` /
  `metadata.value` and the task description). Judge it on **novelty, insight,
  and actionability for an operator who builds/runs an AI agent platform** —
  NOT on multi-channel same-topic overlap. Do **not** skip merely because the
  supporting videos cover different topics; cross-topic synthesis is the entire
  point. Ideation rubric, in order:
  1. **Generic / obvious?** If the narrative is a truism, a restatement of the
     headlines, or something the operator obviously already knows → **skip**.
  2. **Unsupported?** If the narrative is not actually borne out by the
     supporting videos' claims (the LLM over-reached) → **skip**.
  3. **Duplicate of a recent ship?** Same insight as a `[SHIP]` in the last 48h
     → **skip**, cite the prior `artifact_id`.
  4. **Genuinely non-obvious + useful?** → **ship**, authoring from the
     synthesized narrative + value (Phase 2), with the supporting videos as
     evidence. Then SKIP the convergence rubric below.

- **`convergence`** (default / absent) — apply the convergence rubric below.

Walk these in order (convergence candidates):

1. **Same thesis as a ship within last 48h?** Compare against prior `[SHIP]`
   blocks in the index. If a recent brief covers essentially the same
   thesis (same primary topic, overlapping entities, very similar
   convergence framing) → **skip**. Cite the prior `artifact_id` in
   `verdict_reasoning`.

2. **Sources thin?** If the cluster has fewer than 2 independent channels
   with fewer than 3 supporting claims total → **skip**. (The producer
   already enforces ≥2 channels, but reasoning depth matters too.)

3. **Borderline strength, sources may grow?** If only 2 sources, both very
   recent, plausibly a leading edge → **defer** with an explicit stated
   condition: e.g. `"defer until at least one more independent channel
   covers this within 24h"`. The defer entry persists in the index and the
   candidate row but does NOT re-evaluate later. New evidence → new
   `candidate_id` (different hash) → fresh evaluation.

4. **Operator rated similar themes thumbs-down recently?** Scan prior
   `[SHIP]` blocks for matching `key_entities` with `operator_rating: 1`.
   If two or more recent thumbs-down hits on the same theme → apply a
   stricter bar; tilt toward `skip` unless the new convergence brings
   genuinely new entities.

5. **Otherwise** → **ship**.

### 1.3 Append the verdict to the index IMMEDIATELY

This is critical for batch consistency. Before moving to the next
candidate, write your decision to the index file so the next candidate's
evaluation sees it:

```python
from universal_agent.services.recent_briefs_index import append_verdict_to_index

append_verdict_to_index(
    artifact_id=artifact_id,           # "" for skip/defer
    candidate_id=candidate_id,
    verdict=verdict,                    # "ship" | "skip" | "defer"
    title=headline,                     # 1-line cluster headline
    thesis=thesis,                      # 1-2 sentences
    key_entities=key_entities,          # 3-6 strings
    ship_reasoning=verdict_reasoning,   # ≤2 sentences; cite prior IDs for skip
    operator_rating=None,               # always None at write time
    decided_at=now_iso,
)
```

## Phase 2 — Author the brief (ship verdicts only)

> `demo_amenable` was captured in Phase 0.5 (from `metadata.triage.demo_amenable`, or `false` on the legacy/triage-off path). Carry it into the artifact metadata in Phase 3.3 — it is the only field this skill propagates for the (future) code-demo bridge; this skill does NOT itself dispatch a demo build.

For each `ship` verdict, write the full HTML brief with these required
sections, in order:

1. **Convergence signal** — what the cluster is about, and why now (the
   "what + why now"). 2-4 sentences.
2. **Evidence summary** — one short paragraph per source channel,
   summarizing the channel's claims and linking to the source URL.
3. **Divergence** — where the sources disagree, OR explicit "no meaningful
   divergence — all sources align on X" if they don't.
4. **So what** — three specific actionable items for Kevin, each a single
   imperative sentence. These become `metadata.key_actions`.

Save the HTML to the mission workspace as `intel_artifact.html` while
authoring. At end of the mission, copy it to the durable location:

```python
import shutil
from pathlib import Path
from universal_agent.artifacts import resolve_artifacts_dir

intel_root = resolve_artifacts_dir() / "intel"
intel_root.mkdir(parents=True, exist_ok=True)
durable_path = intel_root / f"{artifact_id}.html"
shutil.copy2(workspace_html_path, durable_path)
```

The durable path is what gets stored in `proactive_artifacts.artifact_path`
so `GET /briefs/<artifact_id>` can serve it even after workspace cleanup.

## Phase 3 — Register the artifact (ship verdicts only)

### 3.1 Pre-sign the feedback URLs

Use the helpers shipped in PR B:

```python
import os
from universal_agent.services.cron_artifact_notifier import sign_feedback_token

base = os.environ.get("UA_GATEWAY_BASE_URL", "").rstrip("/")
up_token = sign_feedback_token(artifact_id, "up")
down_token = sign_feedback_token(artifact_id, "down")
feedback_url_up = f"{base}/api/v1/briefs/{artifact_id}/feedback?v=up&t={up_token}"
feedback_url_down = f"{base}/api/v1/briefs/{artifact_id}/feedback?v=down&t={down_token}"
```

Signing happens HERE, at artifact-write time, NOT in Simone's digest
skill. Simone's skill is pure packaging — it reads the pre-signed URLs
straight from `metadata_json`.

### 3.2 Compute composite_score

A simple weighted sum is fine — it's only used for sort ordering in the
digest, NOT for gating. Suggested:

```python
composite_score = (
    0.40 * min(channel_count / 4.0, 1.0)         # channel breadth
    + 0.30 * min(total_claim_count / 12.0, 1.0)  # evidence depth
    + 0.20 * novelty_vs_prior_briefs              # 0..1 — lower if very similar to recent
    + 0.10 * actionability                        # 0..1 — Atlas's read of "so what" specificity
)
```

### 3.3 Upsert the artifact

```python
from universal_agent.services.proactive_artifacts import upsert_artifact

upsert_artifact(
    conn,
    artifact_id=artifact_id,
    artifact_type="intel_brief",
    source_kind="convergence_candidate",
    source_ref=candidate_id,
    title=headline,
    summary=thesis,
    status="produced",
    artifact_path=str(durable_path),
    topic_tags=primary_topics + ["intel_brief"],
    metadata={
        "candidate_id": candidate_id,
        "thesis": thesis,
        "key_entities": key_entities,
        "key_actions": key_actions,
        "needs_attention": bool(needs_attention),
        "composite_score": float(composite_score),
        "delivery_channel": "hourly_digest",
        "feedback_url_up": feedback_url_up,
        "feedback_url_down": feedback_url_down,
        "channel_count": channel_count,
        "video_ids": video_ids,
        # Preserve the upstream triage signal so downstream consumers (e.g. a
        # future intel-ship -> Cody demo bridge) can find code-amenable briefs.
        # `demo_amenable` is True only when triage flagged a concrete buildable
        # software/coding demo; False/absent otherwise (incl. the legacy path).
        "demo_amenable": bool(demo_amenable),
    },
)
```

Set `verdict='ship'` and `verdict_reasoning=<your reasoning>` on the row
via the canonical migration columns added in PR B:

```python
conn.execute(
    """
    UPDATE proactive_artifacts
    SET verdict = 'ship', verdict_reasoning = ?
    WHERE artifact_id = ?
    """,
    (verdict_reasoning, artifact_id),
)
conn.commit()
```

## Phase 4 — Close the candidate + Task Hub item

For every candidate in the batch (ship, skip, defer, or error):

### 4.1 Update the candidate row

```python
now_iso = datetime.now(timezone.utc).isoformat()
conn.execute(
    """
    UPDATE convergence_candidates
    SET verdict = ?,
        verdict_reasoning = ?,
        artifact_id = ?,
        evaluated_at = ?,
        updated_at = ?
    WHERE candidate_id = ?
    """,
    (verdict, verdict_reasoning, artifact_id or "", now_iso, now_iso, candidate_id),
)
conn.commit()
```

`artifact_id` is non-empty only for `ship`.

### 4.2 Close the Task Hub item

Use the standard task-hub completion path:

```python
from universal_agent import task_hub

task_hub.perform_task_action(
    conn,
    task_id=task_id,             # from the candidate row
    action="complete",
    agent_id="atlas",
    note=f"Verdict: {verdict}. {verdict_reasoning[:160]}",
)
```

## Failure handling

- **Single candidate fails** (malformed signatures, unparseable JSON,
  hashing collision): mark that candidate's `verdict='error'` with the
  exception string as `verdict_reasoning`, close the Task Hub item as
  failed, and continue with the next candidate in the batch.
- **Whole mission crashes**: unprocessed candidates remain `verdict=''` in
  the table. Atlas will re-claim them on the next dispatch — the
  `write_convergence_candidate` upsert is idempotent for non-final
  verdicts. No special recovery logic needed.
- **`UA_GATEWAY_BASE_URL` unset**: log a warning and skip the feedback URL
  block in `metadata_json`. Simone's digest skill will render the
  thumbs-up/down buttons as no-ops in that case — better than crashing
  the whole authoring step.
- **Durable copy fails** (permissions, disk full): keep the workspace
  HTML, set `artifact_path` to the workspace path, log a warning. The
  dashboard viewer (`GET /briefs/<id>`) falls back to title+summary if the
  file is missing.

## Output schema (per candidate)

The structured verdict block — persisted to BOTH `convergence_candidates`
AND the recent briefs index file:

```yaml
candidate_id: cand_<16hex>
verdict: "ship" | "skip" | "defer" | "error"
decided_at: <ISO-8601>
thesis: <1-2 sentences>
key_entities: [<3-6 named entities>]
ship_reasoning: <why ship/skip/defer; cite prior IDs for skip>
artifact_id: pa_<id>     # non-empty only for ship
needs_attention: false    # ship only — set true for urgent signals
```

## Quick checklist

Before finishing the mission, confirm:

- [ ] Every claimed candidate has a final verdict in
      `convergence_candidates.verdict`
- [ ] Every ship verdict has a row in `proactive_artifacts` with
      `verdict='ship'`, `artifact_path` set, and `metadata_json` containing
      `feedback_url_up`, `feedback_url_down`, `thesis`, `key_entities`,
      `key_actions`, `composite_score`, `delivery_channel='hourly_digest'`
- [ ] The recent briefs index file has a new block per verdict (in batch
      order)
- [ ] Every claimed Task Hub item is closed (`complete` for done,
      `fail` for `error`)
- [ ] **No emails sent** — delivery is Simone's job
