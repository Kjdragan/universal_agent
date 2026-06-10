---
name: cody-implements-from-brief
description: >
  Cody's Phase 3 skill. Picked up by Cody when she dequeues a `cody_demo_task`
  Task Hub item from `cody-task-dispatcher`. Reads the workspace's BRIEF.md,
  ACCEPTANCE.md, business_relevance.md, and SOURCES/, then builds a runnable
  demo of the briefed capability. Carries the canonical Demo build contract
  (framework-per-video selection, functional-completeness acceptance, ZAI
  inference wiring for Claude-Agent-SDK demos) — the same contract embedded
  in `tutorial_build` BRIEFs. Invokes `claude` from inside the workspace dir
  so the vanilla project-local settings apply (inference inherits the
  daemon's ZAI routing env). Writes manifest.json (with endpoint_hit
  verification), run_output.txt, and BUILD_NOTES.md (for documented gaps
  where docs were unclear). USE when Cody picks up a `cody_demo_task` task
  or any tutorial_build demo-build mission.
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

## Framework selection (per video) — decide BEFORE writing code

The Demo is a runnable mini-app of the video's **capability**, not a
reproduction of the video's tutorial. Pick the stack from what the video is
about:

| The video is about… | Build the demo in… |
|---|---|
| a specific SDK/stack (Google ADK, Gemini, LangGraph, …) | **that native stack** — first-class; hands-on learning of that stack is the point, not a fallback |
| a Claude Code / Anthropic feature (e.g. `/goal`) | the **Claude Agent SDK** |
| a stack-agnostic concept (e.g. "memory pipelines") | **the Claude Agent SDK by default** (the north star) |
| cross-framework integration (Claude Agent SDK ↔ ADK, …) | **ONLY on explicit operator direction — never by default** |

If you cannot tell which row applies, PAUSE for operator input: leave the
task in review with your specific question in the note. Ambiguity about
"how to build this one" never blocks demo-worthiness — it only pauses the
build.

## Acceptance bar — functional completeness, not looks

The demo is the operator's personal learning/reference library entry.

- **Simple UI.** Spend zero effort on design polish — a plain CLI, a bare
  terminal transcript, or a minimal unstyled page is correct.
- **Functionally complete.** The demo must be sophisticated enough to
  FULLY exercise the capability it demonstrates — real inputs, real
  outputs, the interesting code path actually executed end-to-end.
- Acceptance = functional completeness, not looks. Never trade capability
  coverage for styling, and never pad the demo with UI work.

## Inference wiring — Claude Agent SDK demos run on ZAI

Claude-Agent-SDK + Claude-Max OAuth inference is currently BROKEN. Any demo
built on the Claude Agent SDK must be wired to ZAI/GLM inference or it will
not run:

- The demo MUST read `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` from
  the environment. The UA daemon injects both (`ANTHROPIC_BASE_URL` points
  at the ZAI proxy, `ANTHROPIC_AUTH_TOKEN` holds the ZAI key) and
  `run_in_workspace` inherits them by default (`scrub_env=False`).
- Do NOT scrub `ANTHROPIC_*` env vars for these demos — that would strip
  the very routing the demo needs.
- NEVER hardcode an endpoint URL or token in demo code; NEVER commit a
  token. Reference the two env var names in the demo's README instead.

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
   Use `cody_implementation.run_in_workspace(...)` — it `cd`'s for you and
   (by default, `scrub_env=False`) inherits the daemon's ZAI routing env.
   Pass `scrub_env=True` only for a demo that must hit real Anthropic.

3. **VERIFY ENDPOINT.** After every demo run, write `manifest.json` with
   `endpoint_hit` populated. `endpoint_hit` must match the task's
   `endpoint_required` (for ZAI-wired demos that is `zai`); a mismatch in
   either direction is a **failure** regardless of whether the code
   "worked." Use
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
    scrub_env=False,  # default — inherit the daemon's ZAI routing env
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

> **Session-link fields (P5 — do not author).** After the mission terminates,
> the VP worker stamps `build_mission_id` / `build_session_id`
> (the `vp-mission-<id>`) / `build_vp_id` / `build_cli_session_id` into
> `manifest.json` (`vp/worker_loop.py::_stamp_demo_manifest_build_session`)
> so the dashboard can open the build session in the 3-panel viewer. Never
> invent, copy, or delete these fields — and never strip fields you don't
> recognize when rewriting `manifest.json` on a later iteration.

### Step 5.5 — Deslop the demo diff (best-effort, NON-blocking)

Before opening any PR for the demo, take a quick behavior-preserving cleanup
pass over the diff via the `technical-deslop` skill — apply only safe removals
(redundant comments, narration logging, dead defensive checks) and re-run the
relevant checks. This is best-effort only: if it errors or finds nothing,
proceed. It must NEVER block or delay the autonomous ship.

### Step 6 — Mark the task

If `acceptance_passed=True` and `endpoint_hit` matches `endpoint_required`:
mark the Task Hub item complete. Simone (Phase 4) will re-run and verify.

If anything failed — failed acceptance, endpoint mismatch, build gap:
write `BUILD_NOTES.md` describing exactly what happened, leave the task
in OPEN status, and surface the situation. Simone will iterate via
`cody-task-dispatcher.reissue_cody_demo_task_with_feedback`.

## Ephemeral Postgres via Ghost (when the demo needs a real DB)

Demo workspaces ship with `.mcp.json` that exposes the **Ghost** MCP server —
on-demand Postgres databases with pgvector, TimescaleDB hypertables, PostGIS,
and JSONB. Use this when the demo legitimately needs a database (e.g.
demonstrating a memory pattern, a vector retrieval, a time-series feature)
instead of inventing fixtures or scaffolding SQLite.

Tools available: `ghost_create`, `ghost_sql`, `ghost_schema`, `ghost_fork`,
`ghost_logs`, `ghost_delete`. The `GHOST_API_KEY` env var is injected by the
UA daemon — you don't need to source it yourself.

**Cleanup obligation.** Ghost's free tier is 100 hours/month for the whole UA
account. Abandoned demo DBs burn that cap. Therefore:

1. Record every DB you create in `manifest.json.ghost_databases: ["<name>"]`.
2. On successful run, call `ghost_delete` on each name BEFORE writing the
   final `manifest.json`.
3. On failure, leave the DB intact AND keep its name in `manifest.json` so
   the next iteration or operator audit can reclaim it via
   `ghost list` / `ghost_delete`.

If `${GHOST_API_KEY}` resolves empty (the MCP server fails to start), that's
an Infisical bootstrap problem — write a `kind="blocker"` build note and
stop. Do not invent a workaround.

## Failure modes that need build notes (not invention)

| Symptom | Action |
|---|---|
| Required API not in SOURCES/ | `append_build_note(... kind="gap")`. STOP. |
| Required env var/config not in SOURCES/ | Same. |
| Two competing patterns in SOURCES/ | `append_build_note(... kind="decision")` documenting which you picked and why. |
| Demo hits the wrong endpoint (`endpoint_hit` != `endpoint_required`) | `append_build_note(... kind="blocker")`. Possible env leak or settings precedence issue. |
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
   and produce output for which `detect_endpoint_from_text` returns the
   endpoint matching the task's `endpoint_required` (for ZAI-wired demos,
   `zai`).
2. If it returns the wrong endpoint, an env var is leaking or being
   scrubbed unexpectedly — check `run_in_workspace`'s `scrub_env` argument
   and the workspace `.claude/settings.json` precedence.
3. `manifest.json.endpoint_hit` is the durable record. Simone's
   evaluator (PR 10) will reject any demo whose `endpoint_hit` doesn't
   match `endpoint_required`.
