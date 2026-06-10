---
name: self-brief-and-attest
description: >
  Mandatory first-turn skill for every autonomous VP mission. The VP
  interrogates its task against codebase/docs/prior artifacts (via
  grill-with-docs style document interrogation — NOT against the
  operator), produces a BRIEF.md (universal) and, when /goal-eligible,
  also an ACCEPTANCE.md + goal_condition.txt. At end of mission, before
  calling finalize(completed), the VP writes COMPLETION.md and
  self-attests against its own BRIEF. USE this skill at the start of
  every autonomous Cody or Atlas mission.
---

# self-brief-and-attest

> **Mandatory contract for every autonomous VP mission.**
> Front-loads interpretation (BRIEF), success criteria (ACCEPTANCE, /goal-eligible only),
> and final-state attestation (COMPLETION). Universal across Cody + Atlas;
> /goal-specific outputs only when the mission is in the /goal-eligible set.

## When to use

- **Start of every autonomous VP mission** (Cody or Atlas, any source_kind).
- Specifically: the FIRST turn after the VP claims a mission and opens its workspace.

## When NOT to use

- Operator-initiated /goal sessions where Kevin is at the keyboard — those use
  `idea-refine` / `grill-me` / `spec-driven-development` instead (Kevin is the
  source of truth there, not the documents).
- Trivial single-tool-call missions where briefing overhead is greater than the
  work itself — but even then, write at minimum a one-line BRIEF.md for the
  failure-rescue audit trail.

## The contract (5 phases per mission)

### Phase 1 — Read the task, interrogate the context (FIRST TURN, ALWAYS)

Before starting any work:

1. **Read** the task hub item's objective, metadata, and any linked artifacts
   (vault entity files, prior PRs, source code referenced in the brief).
2. **Interrogate the codebase/docs** for missing context the task assumes but
   doesn't state. Examples:
   - "Fix CSI gateway PR validation drift" → grep CSI service for the
     gateway, read its current PR validation code, find recent PRs that
     touched it, identify the drift the brief is talking about.
   - "Build a demo of ToolX" → read ToolX's official docs (Context7 or
     WebFetch), check `cody-implements-from-brief` skill, look for prior
     demos of similar tools in `/opt/ua_demos/`.
3. **Resolve ambiguities by reading**, NOT by guessing. If the documents don't
   resolve it, capture the unresolved ambiguity in your BRIEF.md — don't paper
   over it.

### Phase 2 — Write BRIEF.md (UNIVERSAL — every VP, every mission)

`BRIEF.md` lives at the workspace root. Free-prose markdown. Captures your
interpretation BEFORE you act, so that if the mission fails Simone (or Kevin
post-delivery) can see what you thought you were doing.

```markdown
# BRIEF — <one-line title>

## What I think the task is asking for

<one paragraph: your interpretation of the objective. Be specific about
scope boundaries.>

## What I checked

- <doc 1 you read> — <what it told you>
- <doc 2 you read> — <what it told you>
- <code 3 you grep'd> — <what it told you>

## Unresolved ambiguities (if any)

- <ambiguity 1> — <how I'm going to handle it absent clarification>

## Constraints I'm operating under

- <constraint 1: workspace path, branch, must-not-touch areas, etc.>
```

### Phase 3 — Write ACCEPTANCE.md + goal_condition.txt (/goal-eligible Cody ONLY)

Required only when the mission's `source_kind` is in the /goal-eligible set:
`cody_demo_task`, `cody_scaffold_request`, `tutorial_build`, or operator-dispatched
Cody (mission carries `metadata.use_goal_loop=True`). `tutorial_build` missions
always carry `metadata.use_goal_loop=True` (stamped by
`services/proactive_tutorial_builds.queue_tutorial_build_task`) — use Card mode below.

**`ACCEPTANCE.md`** — structured success criteria with rationale. Each criterion:
- Specific (not "code is good" — "ruff check on changed files exits 0")
- Verifiable (something that surfaces in the transcript or as a file in the workspace)
- Includes a rationale ("why this matters")
- Includes must-not-change constraints

```markdown
# ACCEPTANCE — <one-line title>

## Success criteria

- [ ] `pytest tests/<scope> -x` exits 0
  - Rationale: regression test suite covering the changed area passes
- [ ] `ruff check src/<scope>` exits 0
  - Rationale: lint clean on changed files
- [ ] PR created via `gh pr create` and URL printed in transcript
  - Rationale: work product is reviewable

## Must-not-change

- No changes to `<list of paths>`
- No new dependencies in `pyproject.toml`
- No deploy or merge actions

## Self-bounding

- Stop after 20 turns if criteria not met
- Stop after 60 minutes wall clock if criteria not met
```

**`goal_condition.txt`** — single ≤4000-char prose string phrased for
transcript-reading evaluator (Haiku). NOT identical to ACCEPTANCE.md — this is
adapted to /goal's specific evaluator semantics.

Per Anthropic's `/goal` docs:
- "The evaluator does not run commands or read files independently, so write
  the condition as something Claude's own output can demonstrate."
- "One measurable end state, a stated check, constraints that matter."

```text
All of the following hold AND have been demonstrated in this conversation:
1. The transcript shows `pytest tests/<scope>` was run and exited 0.
2. The transcript shows `ruff check src/<scope>` was run and exited 0.
3. The transcript shows a `gh pr create` command was run and the
   resulting PR URL (a string matching https://github.com/[^/]+/[^/]+/pull/[0-9]+)
   appears in this conversation.
4. No file under `<must-not-change paths>` has been modified.
5. No new line was added to pyproject.toml's `[project.dependencies]` section.

OR stop after 20 turns regardless. Stop after 60 minutes regardless.
```

The `goal_condition.txt` is what gets passed to `claude -p "/goal <contents>"`.

### Card mode — `tutorial_build` missions

`tutorial_build` cards carry no Simone-authored BRIEF — only the card data
(video title / URL / channel / extraction_plan JSON) plus the binding
"Demo build contract" embedded in the objective. In card mode you TRANSFORM
the card into the briefing artifacts yourself:

**BRIEF.md** — derive from the card: what capability the video demonstrates,
which stack the contract's framework-selection rule picks, the demo's scope
(standalone mini-app, NOT a line-by-line reproduction), and the source
attribution (video title + URL verbatim from the card).

**ACCEPTANCE.md** — MUST include all of these criteria (plus any
demo-specific ones):

- [ ] Runnable end-to-end with a uv-managed environment: `pyproject.toml`
  present and `uv sync` (or a committed `uv.lock`) prepares the venv;
  the documented run command (e.g. `uv run python main.py`) executes
  successfully in the transcript.
- [ ] README.md contains a "Run" section with the exact setup + run commands.
- [ ] `manifest.json` authored at the workspace root, schema-compatible with
  `services/cody_implementation.py::DemoManifest` (demo_id, feature,
  endpoint_required, endpoint_hit, model_used, acceptance_passed,
  iteration, started_at, finished_at, notes); endpoint_hit recorded
  truthfully (zai vs anthropic_native).
- [ ] Simple UI, functionally complete — fully exercises the capability per
  the Demo build contract's acceptance bar (no design polish).

**goal_condition.txt** — phrase the above as transcript-demonstrable checks
(the evaluator reads only the conversation), and keep the standard
self-bounding clause (turn + wall-clock stop).

### Phase 4 — Do the work (the mission itself)

Standard execution. Use available skills, sub-agents, tools. Self-evaluate
along the way against your own BRIEF/ACCEPTANCE.

### Phase 5 — Write COMPLETION.md and self-attest (UNIVERSAL)

Before calling `finalize_vp_mission(completed)` (or letting the parent worker
do so), write `COMPLETION.md` at the workspace root. This file is the
audit trail for the work and the gate for the worker-level completion guard.

```markdown
# COMPLETION — <one-line title>

## Summary

<one paragraph: what was produced, where artifacts live>

## Mapping vs BRIEF / ACCEPTANCE

| Item from BRIEF/ACCEPTANCE | Status | Evidence |
|---|---|---|
| <criterion 1> | satisfied / not satisfied / partial | <pointer: file path, log line, test output> |
| <criterion 2> | … | … |

## Artifacts produced

- `<absolute path to artifact 1>` — <what it is>
- `<absolute path to artifact 2>` — <what it is>

## Outbound delivery

- Email sent to Kevin at <time>, CC'd Simone — Yes / No (reason)
- See `services/vp_email_directive.build_vp_outbound_email_directive` for the
  canonical email contract.

## Self-attestation

I have read BRIEF.md and (if applicable) ACCEPTANCE.md. I attest that:
- [ ] All criteria above are satisfied OR explicitly marked partial/not satisfied with evidence
- [ ] All artifacts in the "Artifacts produced" list exist at the paths listed
- [ ] No must-not-change boundary was violated
- [ ] The outbound email was sent successfully (or, if not, this was recorded
      and finalize(failed) will be called instead of finalize(completed))

If ANY of the above is NOT true, do NOT call finalize(completed). Call
finalize(failed) with a clear reason — the failure-rescue system will route
to Simone for evaluation.
```

The worker process will check for the presence of `COMPLETION.md` before
allowing `finalize(completed)`. Missing the file is treated as a protocol
violation and routes the mission into the failure-rescue lane with
`failure_mode="missing_completion_attestation"`.

## Why this exists

Three concrete failure modes this skill prevents:

1. **"VP drifted from the brief"** — without explicit BRIEF.md capture, the
   VP's interpretation lives only in its context window and is forgotten on
   failure. With BRIEF.md, Simone can see what the VP thought it was doing
   and tailor rescue guidance to the misinterpretation.
2. **"`/goal` condition was ambiguous"** — separating ACCEPTANCE.md (for
   humans reading the audit trail) from `goal_condition.txt` (phrased for the
   Haiku evaluator) prevents bad conditions that either loop forever or
   terminate prematurely.
3. **"`finalize(completed)` was a lie"** — without the COMPLETION attestation
   step, VPs would mark complete based on subprocess exit code alone, which
   is too thin a signal. Forcing a final read-the-brief-and-attest pass
   catches the common "I drifted" failure mode in the VP's own context.

## Integration with /goal

When `UA_VP_GOAL_ENABLED=1` and the mission is /goal-eligible:

1. Phase 1-3 run as a SEPARATE subprocess (briefing turn) — Cody is invoked
   with a briefing prompt that produces BRIEF.md + ACCEPTANCE.md +
   `goal_condition.txt`.
2. Phase 4 runs as the `/goal` loop — Cody is invoked with
   `claude -p "/goal <contents of goal_condition.txt>"`. The Haiku
   evaluator drives the loop until the condition is met (or the
   self-bounding clause fires).
3. Phase 5 runs as the FINAL turn inside the `/goal` loop or as a follow-up
   subprocess — Cody writes COMPLETION.md and the parent worker checks for
   it before finalize.

When `UA_VP_GOAL_ENABLED=0` (default) OR mission is not /goal-eligible:

- All five phases run in a single subprocess (no two-phase split).
- Skip Phase 3 (no ACCEPTANCE.md, no goal_condition.txt) for non-/goal
  missions; BRIEF.md and COMPLETION.md are still required.

## What this skill does NOT do

- Does NOT guess at requirements. Capture ambiguity in BRIEF.md instead.
- Does NOT call any tools itself — it's instructional, the VP does the work.
- Does NOT replace `idea-refine` / `grill-me` — those are operator-interactive.
  This skill grills *documents* via `grill-with-docs` style, not humans.
- Does NOT replace `cody-work-evaluator` — that's Simone's evaluator for
  `cody_demo_task` workspaces; it remains the source of truth for that
  source_kind even with self-briefing in place.

## Related

- `grill-with-docs` — the document-interrogation style this skill borrows from
- `cody-implements-from-brief` — Cody's existing implementation skill, often
  invoked AFTER self-briefing as the work-phase skill
- `vp-orchestration` — the Simone-side delegation skill
- `services/vp_failure_rescue` — where COMPLETION.md absence routes the
  mission into the failure-rescue lane
