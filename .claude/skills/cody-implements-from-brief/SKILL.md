---
name: cody-implements-from-brief
description: >
  Cody's Phase 3 skill. Picked up by Cody when she dequeues a `cody_demo_task`
  Task Hub item from `cody-task-dispatcher`. Reads the workspace's BRIEF.md,
  ACCEPTANCE.md, business_relevance.md, and SOURCES/, then builds a runnable
  demo of a Claude Code / Claude Agent SDK feature. Invokes `claude` from
  inside the workspace dir so vanilla project-local settings override the
  ZAI mapping. Writes manifest.json (with endpoint_hit verification),
  run_output.txt, and BUILD_NOTES.md (for documented gaps where docs were
  unclear). USE when Cody picks up a `cody_demo_task` task.
---

# cody-implements-from-brief

> **Phase 3 of the ClaudeDevs Intel v2 pipeline.** This is where the demo
> actually gets built. Pairs with `cody-scaffold-builder` (Phase 2) and
> `cody-work-evaluator` (Phase 4).
>
> See [v2 design doc §8](../../docs/proactive_signals/claudedevs_intel_v2_design.md)
> for the full Phase 3 contract and
> [Demo Execution Environments](../../../docs/06_Deployment_And_Environments/09_Demo_Execution_Environments.md)
> for the dual-environment context that makes this skill load-bearing.

## When to use

Cody invokes this skill when:

1. She dequeues a Task Hub item with `source_kind=cody_demo_task` (queued by
   `cody-task-dispatcher` in PR 8).
2. The task's metadata points at a workspace under `/opt/ua_demos/<demo-id>/`.
3. The workspace contains BRIEF.md / ACCEPTANCE.md / business_relevance.md
   that Simone has refined (no `_(Simone: ...)_` placeholders remain).

If the workspace fails the readiness check, Cody returns the task to the
queue with a comment so Simone can fix the missing piece. **Do not invent
content to fill in placeholders.**

## The hard rules (READ EVERY TIME)

These rules exist because Cody's training cutoff predates the features
she's demonstrating. Violating them produces demos that look fine but use
stale or invented APIs.

1. **NO INVENTION.** If the docs in `SOURCES/` don't show how to do
   something, document the gap in `BUILD_NOTES.md` (using
   `cody_implementation.append_build_note(..., kind="gap")`) and stop.
   Do not make up function signatures, class names, env vars, or
   defaults. The docs are ground truth.

2. **CD INTO THE WORKSPACE.** Every `claude` invocation must run from
   inside the workspace dir so the project-local vanilla
   `.claude/settings.json` overrides the polluted `~/.claude/settings.json`.
   Use `cody_implementation.run_in_workspace(...)` — it both `cd`'s and
   scrubs leaky env vars (`ANTHROPIC_AUTH_TOKEN` etc).

3. **VERIFY ENDPOINT.** After every demo run, write `manifest.json` with
   `endpoint_hit` populated. If your demo invokes `claude` and the response
   shows ZAI/GLM hints, the demo accidentally hit ZAI — that's a **failure**
   regardless of whether the code "worked." Use
   `cody_implementation.detect_endpoint_from_text(stdout)` for a heuristic
   check; Simone's evaluator does the rigorous network observation.

4. **READ BEFORE WRITING.** You must read at least the primary doc in
   `SOURCES/` BEFORE writing any code. If `SOURCES/` is empty, the
   workspace is incomplete — return to queue.

## The contract

When you pick up a `cody_demo_task`, do all of:

### Step 1 — Verify the workspace is ready

```python
from pathlib import Path
from universal_agent.services.cody_implementation import (
    verify_workspace_ready,
    workspace_for,
    load_briefing,
    list_sources,
)

workspace = Path(task["metadata"]["workspace_dir"])
readiness = verify_workspace_ready(workspace)
if not readiness.ok:
    # Return to queue with a comment naming the missing pieces.
    raise RuntimeError(f"workspace not ready: {readiness.reasons}")
```

### Step 2 — Read the briefing

```python
briefing = load_briefing(workspace)
sources = list_sources(workspace)
# Read briefing.brief, briefing.acceptance, briefing.business_relevance.
# If briefing.feedback is non-empty, this is iteration > 1 — read it FIRST,
# it tells you what to change from the previous attempt.
```

Use your `Read` tool to actually load the source documents from
`<workspace>/SOURCES/`. Read at least the primary one cover-to-cover
before writing any code.

### Step 3 — Build the demo

You're a Claude Code instance — use your normal coding tools (`Write`,
`Edit`, `Bash`) to build the demo. Place demo code under
`<workspace>/src/`. Add a `pyproject.toml` if the demo needs Python deps:

```bash
# (Cody runs this from inside the workspace)
cd /opt/ua_demos/<demo-id>
uv init --no-readme  # if pyproject doesn't exist yet
# Write src/main.py, etc.
```

The demo MUST satisfy every numbered requirement in `ACCEPTANCE.md`.
If a requirement uses an API or pattern not shown in `SOURCES/`, write
a build note with `append_build_note(workspace, "...", kind="gap")` and
return — don't invent.

### Step 4 — Run the demo (with proper env)

```python
from universal_agent.services.cody_implementation import (
    run_in_workspace,
    write_run_output,
    detect_endpoint_from_text,
)

result = run_in_workspace(
    workspace,
    ["uv", "run", "python", "src/main.py"],
    timeout=600,
    scrub_env=True,  # default — strips leaky ANTHROPIC_* env vars
)
write_run_output(workspace, result.stdout)
endpoint_hit = detect_endpoint_from_text(result.stdout + "\n" + result.stderr)
```

For demos that exercise Claude Code itself, you might need to invoke
`claude` directly inside the demo's runtime (not your own Cody session).
That's fine as long as it happens via `run_in_workspace` — that wrapper
guarantees the project-local settings take effect.

### Step 5 — Write the manifest

```python
from datetime import datetime, timezone
from universal_agent.services.cody_implementation import (
    DemoManifest,
    write_manifest,
    probe_versions,
)

versions = probe_versions()
manifest = DemoManifest(
    demo_id=task["metadata"]["demo_id"],
    feature=task["metadata"]["entity_slug"],
    endpoint_required=task["metadata"].get("endpoint_required", "anthropic_native"),
    endpoint_hit=endpoint_hit,
    model_used="<from response if available>",
    claude_code_version=versions.get("claude_code", ""),
    claude_agent_sdk_version=versions.get("claude_agent_sdk", ""),
    wall_time_seconds=result.wall_time_seconds,
    acceptance_passed=False,  # Cody self-check, Simone re-verifies in Phase 4
    iteration=task["metadata"].get("iteration", 1),
    started_at=<iso of step 4 start>,
    finished_at=<iso of step 4 end>,
    notes="<short summary>",
)
write_manifest(workspace, manifest)
```

Set `acceptance_passed=True` only if your demo actually produced the
output ACCEPTANCE.md required AND `endpoint_hit` matches `endpoint_required`.

### Step 6 — Mark the task

If `acceptance_passed=True` and `endpoint_hit` matches `endpoint_required`:
mark the Task Hub item complete. Simone (Phase 4) will re-run and verify.

If anything failed — failed acceptance, endpoint mismatch, build gap:
write `BUILD_NOTES.md` describing exactly what happened, leave the task
in OPEN status, and surface the situation. Simone will iterate via
`cody-task-dispatcher.reissue_cody_demo_task_with_feedback`.

## Failure modes that need build notes (not invention)

| Symptom | Action |
|---|---|
| Required API not in SOURCES/ | `append_build_note(... kind="gap")`. STOP. |
| Required env var/config not in SOURCES/ | Same. |
| Two competing patterns in SOURCES/ | `append_build_note(... kind="decision")` documenting which you picked and why. |
| Demo runs but hits ZAI (endpoint_hit != endpoint_required) | `append_build_note(... kind="blocker")`. Possible env leak. |
| `claude /login` session expired | `append_build_note(... kind="blocker")`. Operator intervention needed. |
| Wall-time exceeded | Set `acceptance_passed=False`, write what you got, note the timeout in `BUILD_NOTES.md`. |

## What this skill does NOT do

- It does NOT decide whether the demo is good. That's Simone's Phase 4
  judgment via `cody-work-evaluator` (PR 10).
- It does NOT auto-iterate. If acceptance fails, Cody documents and
  stops. Simone reads BUILD_NOTES.md and decides whether to write
  FEEDBACK.md and re-queue.
- It does NOT modify the vault or capability library. `vault-demo-attach`
  (PR 10) does that AFTER Simone judges the demo passing.
- It does NOT run `claude /login`. The OAuth session was set up once on
  the VPS by an operator (see [provisioning runbook](../../../docs/operations/demo_workspace_provisioning.md)).

## Related skills

- `cody-task-dispatcher` (PR 8) — what queues this skill's input.
- `cody-progress-monitor` (PR 10) — what Simone uses to check Cody's status.
- `cody-work-evaluator` (PR 10) — what judges Cody's output.
- `vault-demo-attach` (PR 10) — what links a passing demo back into the vault.

## Operator notes

This skill's first end-to-end exercise on the VPS is the moment we find
out whether the dual-environment dance holds up under a real demo build.
Watch for:

1. The first `claude` invocation under `run_in_workspace` should succeed
   and produce output that `detect_endpoint_from_text` returns
   `anthropic_native` for.
2. If it returns `zai`, an env var is leaking — either UA's normal env
   has `ANTHROPIC_AUTH_TOKEN` set in a way `_scrubbed_env()` doesn't catch,
   or there's a settings.json precedence issue.
3. `manifest.json.endpoint_hit` is the durable record. Simone's
   evaluator (PR 10) will reject any demo whose `endpoint_hit` doesn't
   match `endpoint_required`.
