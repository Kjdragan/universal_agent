---
name: cody-work-evaluator
description: >
  Simone's Phase 4 judgment skill. After Cody returns a built demo,
  this skill bundles every artifact Simone needs to score it
  (manifest, BRIEF, ACCEPTANCE, BUILD_NOTES, run_output, optional
  rerun) into a structured EvaluationReport. Simone reads the report
  + the artifacts and produces a pass / iterate / defer verdict. The
  helper then writes FEEDBACK.md (iterate), marks Task Hub completed
  (pass), or parks the task (defer). USE after `cody-progress-monitor`
  shows a demo task is ready for review.
---

# cody-work-evaluator

> **Phase 4 of the ClaudeDevs Intel v2 pipeline (judgment piece).**
> The multi-loop director Simone uses to push Cody past first-shot
> output. Pairs with `cody-progress-monitor` (read) and
> `vault-demo-attach` (post-pass).

## When to use

- A `cody_demo_task` has been returned by Cody (manifest.json exists,
  Cody marked the task with status that signals review).
- Simone is doing a Phase 4 review pass and wants to make a verdict.

## The procedural contract

### Step 1 — Pull together the report

```python
from pathlib import Path
from universal_agent.services.cody_evaluation import evaluate_demo

workspace = Path(task["metadata"]["workspace_dir"])
report = evaluate_demo(
    workspace,
    demo_id=task["metadata"]["demo_id"],
    entity_slug=task["metadata"]["entity_slug"],
    # Optional: re-run the demo to verify reproducibility. Skip the
    # first time you run this skill in production — let Cody's run be
    # the source of truth until you're confident the rerun path
    # behaves correctly.
    rerun_command=None,
)
print(report.to_dict())
```

`evaluate_demo` performs mechanical checks:

- `workspace_complete`: BRIEF / ACCEPTANCE / business_relevance present and non-empty
- `cody_self_reported_pass`: manifest.acceptance_passed == True
- `endpoint_match`: manifest.endpoint_hit == manifest.endpoint_required (or "any")
- Optional `rerun`: re-runs the demo via run_in_workspace; reports
  detected_endpoint from output text

### Step 2 — Read the artifacts

You're an LLM. You can read.

- `BRIEF.md` — what the demo was supposed to demonstrate
- `ACCEPTANCE.md` — explicit success criteria
- `business_relevance.md` — Kevin-facing rationale (does the
  implementation match the client-relevance shape we wanted?)
- `BUILD_NOTES.md` — gaps Cody documented during the build (NO
  INVENTION rule means this might be substantial)
- `run_output.txt` — captured stdout from Cody's successful run
- `manifest.json` — versions used, endpoint, wall time, iteration

### Step 3 — Make a verdict

Three options:

#### Verdict: pass

The demo satisfies every numbered acceptance criterion AND mechanical
checks all green AND Cody documented no blockers.

```python
from universal_agent.services.cody_evaluation import (
    complete_demo_task,
)

complete_demo_task(
    conn,
    task_id=task["task_id"],
    completion_summary="One-line summary for the artifact ledger.",
)
```

Then run `vault-demo-attach` to link the demo into the vault entity
page.

#### Verdict: iterate

Some acceptance criteria not met OR a blocker is real but resolvable
OR endpoint mismatch suggests env-leak that next run could fix.

```python
from universal_agent.services.cody_evaluation import write_feedback_file
from universal_agent.services.cody_dispatch import (
    reissue_cody_demo_task_with_feedback,
)

feedback_path = write_feedback_file(
    workspace_dir=workspace,
    feedback_markdown="""
- Cody: criterion 2 wasn't satisfied. Use `SkillRegistry.register()` per
  SOURCES/skills_quickstart.md#L42 instead of the `register_skill()`
  pattern you tried (that name doesn't exist in the public API).
- The endpoint_hit field still shows `unknown` — the rerun should
  produce text containing 'api.anthropic.com' or a 'claude-' model
  identifier so detection works.
""".strip(),
    iteration=report.iteration + 1,
)

reissue_cody_demo_task_with_feedback(
    conn,
    workspace_dir=workspace,
    entity_slug=task["metadata"]["entity_slug"],
    entity_path=Path(task["metadata"]["entity_path"]),
    demo_id=task["metadata"]["demo_id"],
    feedback_path=feedback_path,
    iteration=report.iteration + 1,
)
```

#### Verdict: defer

The demo can't be salvaged: docs are too thin for a faithful
implementation, the feature requires beta access we don't have,
Cody hit a fundamental blocker, or iteration count exceeds reasonable
bound (~3–5 attempts).

```python
from universal_agent.services.cody_evaluation import defer_demo_task

defer_demo_task(
    conn,
    task_id=task["task_id"],
    reason="Official docs don't yet show the SkillRegistry constructor surface; defer until release notes catch up.",
)
```

The task moves to `parked` status (Task Hub doesn't have a
`deferred` status, so we reuse `parked` semantically). Reason
persists in metadata.deferred_reason.

## How Simone makes the verdict call

The mechanical checks tell you "did the plumbing work." YOU tell us
"did the demo actually demonstrate the feature correctly."

A demo with all-green mechanical checks could still fail substantively
if:

- It demonstrated only a subset of the feature
- The implementation looks plausible but uses an API that Cody
  invented (BUILD_NOTES is the audit trail — read it)
- The output looks right but doesn't match what `business_relevance.md`
  said clients would actually care about
- The `must_use_examples` from ACCEPTANCE weren't followed

Conversely, a demo with one red mechanical check might still pass if:

- `cody_self_reported_pass=False` because Cody was being conservative
  and you can verify the output yourself
- `endpoint_match=False` because the manifest detection heuristic
  missed something but the rerun shows the right endpoint

Use judgment. The mechanical report is the start, not the answer.

## What this skill does NOT do

- It does NOT auto-make the verdict. Simone reads + decides.
- It does NOT modify the vault on pass — that's `vault-demo-attach`.
- It does NOT requeue automatically — Simone calls
  `reissue_cody_demo_task_with_feedback` explicitly after writing
  FEEDBACK.md.
- It does NOT touch the brief / acceptance — those are immutable from
  Phase 2 onward (Simone refines them BEFORE first dispatch).

## Operator notes

The first few times this skill runs in production, the rerun path
(`rerun_command=...`) is the highest-risk piece. Keep `rerun_command=None`
on the first end-to-end production run and trust Cody's manifest.
Once you've seen a few clean rerun_command paths in a non-production
workspace, enable them for live evaluations.

## Related skills

- `cody-progress-monitor` — what shows you which demos are ready.
- `cody-task-dispatcher` — used by the iterate path (reissue helper).
- `vault-demo-attach` — used by the pass path.
