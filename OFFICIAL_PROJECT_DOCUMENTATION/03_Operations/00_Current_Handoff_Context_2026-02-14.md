# Current Handoff Context

**Date:** 2026-02-16
**Status:** Todoist migration implementation complete; pending final commit/push decision

## 1. Executive Snapshot

This session completed the planned Todoist migration packets end-to-end (service + CLI + internal tools + heartbeat integration + brainstorming pipeline + Taskwarrior cleanup), verified with targeted tests, and prepared the repo for handoff.

Implementation followed the previously agreed v1 scope:

- Native in-process integration (no new external MCP server)
- Manual-only brainstorming promotion in v1
- Manual work-thread creation from approved ideas in v1

---

## 2. What Was Implemented

### A. Todoist foundation (Packet 1)

- Added `todoist-api-python` dependency in `pyproject.toml`
- Added `TODOIST_API_TOKEN` to `.env.sample`
- Added core service:
  - `src/universal_agent/services/todoist_service.py`
  - `src/universal_agent/services/__init__.py`

Service capabilities include:

- Taxonomy bootstrap (`Agent Tasks`, `UA Brainstorm Pipeline`, sections, labels)
- Task CRUD-ish lifecycle methods (query/create/update/complete/delete/comment)
- Label/state transitions (blocked/unblocked/needs-review)
- Deterministic heartbeat summary payload generation
- Brainstorm lifecycle (`record_idea`, dedupe+confidence bump, `promote_idea`, `park_idea`, pipeline counts)

### B. JSON CLI surface (Packet 2)

- Added CLI module:
  - `src/universal_agent/cli/todoist_cli.py`
  - `src/universal_agent/cli/__init__.py`

Commands include:

- `setup`, `heartbeat`, `tasks`, `task`
- `create`, `complete`, `comment`, `block`, `unblock`, `review`
- `idea`, `promote`, `park`, `pipeline`

### C. Heartbeat deterministic integration (Packet 3)

- Updated `src/universal_agent/heartbeat_service.py`:
  - Added Todoist deterministic pre-step (`TodoService().heartbeat_summary()`)
  - Injects `todoist_summary` system event/metadata when actionable tasks exist
  - Added fast-path skip: if Todoist pre-step succeeds and there are zero actionable tasks (and no competing system-event conditions), skip expensive gateway LLM execution
  - Added `UA_HEARTBEAT_MIN_INTERVAL_SECONDS` override (default remains 30m), primarily to make heartbeat tests deterministic/fast

### D. Brainstorming pipeline behavior (Packet 4)

Implemented in `todoist_service.py` + CLI/tool surfaces:

- Dedupe-aware idea recording by `dedupe_key`
- Confidence bump + audit comment when resurfaced
- Section transitions for approve/promote/park
- Pipeline counters returned for heartbeat/UI use

### E. Cleanup / deprecation (Packet 5)

- Removed Taskwarrior MCP registration from `src/universal_agent/agent_setup.py`
- Deleted legacy server file: `src/universal_agent/mcp_server_taskwarrior.py`
- Removed stale Taskwarrior capability block from `src/universal_agent/prompt_assets/capabilities.md`
- Confirmed `tasklib` removed from deps/lockfile

### F. Native internal tool registration

- Added tool bridge:
  - `src/universal_agent/tools/todoist_bridge.py`
- Registered wrappers in:
  - `src/universal_agent/tools/internal_registry.py`

Tool names now available in internal registry:

- `todoist_setup`
- `todoist_query`
- `todoist_get_task`
- `todoist_task_action`
- `todoist_idea_action`

### G. Documentation update

- Updated `README.md` with Todoist section and quick CLI commands

---

## 3. Verification Evidence

Targeted verification suite run and passing:

```bash
uv run pytest \
  tests/unit/test_internal_registry_todoist.py \
  tests/unit/test_todoist_service.py \
  tests/unit/test_todoist_cli.py \
  tests/unit/test_todoist_bridge.py \
  tests/unit/test_heartbeat_todoist_injection.py \
  tests/gateway/test_heartbeat_mvp.py \
  tests/gateway/test_heartbeat_wake.py \
  tests/gateway/test_heartbeat_last.py -q
```

Result: **24 passed**.

---

## 4. Current Working State (Important)

### A. Not yet committed/pushed

All implementation changes are currently local in working tree (modified + new files).

### B. Blocker before final commit

An unrelated local change exists in:

- `universal_agent.code-workspace`

It adds an extra folder (`../clawdbot`). This change was not part of Todoist packets.

Before commit/push, the maintainer should explicitly choose one:

1. Include this workspace change in commit, or
2. Exclude/revert it from the Todoist commit

---

## 5. Next Session: Exact Continuation Steps

1. **Resolve unrelated workspace file decision**
   - Decide include/exclude for `universal_agent.code-workspace`

2. **Finalize commit**
   - Stage only intended Todoist packet files
   - Commit with a message covering Todoist migration + heartbeat + Taskwarrior cleanup

3. **Push branch and proceed to review/PR flow**

4. **Optional immediate follow-up packet (if desired)**
   - Add end-to-end docs for operational runbook (daily heartbeat + Todoist triage cadence)
   - Expand integration tests to cover more live-like Todoist flows behind feature/env guard

---

## 6. Scope Explicitly Deferred (Still Deferred)

- Automatic brainstorm promotion (kept manual in v1)
- Automatic work-thread creation from approved brainstorms (kept manual in v1)

These were intentional scope boundaries, not missing work.

---

## 7. Post-push Live Validation Evidence (2026-02-16)

### A. Guarded live Todoist integration tests

Executed:

```bash
set -a; source .env; set +a
export TODOIST_API_TOKEN="${TODOIST_API_TOKEN:-$TODOIST_API_KEY}"
RUN_TODOIST_LIVE_TESTS=1 uv run pytest tests/integration/test_todoist_live_guarded.py -q
```

Observed result:

- `2 failed` (after env alias correction from `TODOIST_API_KEY` -> `TODOIST_API_TOKEN`)
- Failure mode: taxonomy bootstrap unavailable during `ensure_taxonomy()`

Root-cause evidence collected during diagnostics:

- `todoist_api_python` installed in this environment exposes paginated iterators (`Iterator[list[...]]`) and `get_tasks()` does **not** accept a `filter=` kwarg.
- Current `TodoService` implementation still assumes legacy flat-list/filter-kwarg behavior.
- This mismatch causes deterministic heartbeat actionable query path to return empty results.

### B. Real heartbeat actionable/non-actionable probe

Executed live probe with direct Todoist API task lifecycle (create `agent-ready` task -> update to `blocked` -> query `TodoService().heartbeat_summary()` both times):

- `actionable_case.task_present=false`
- `actionable_case.actionable_count=0`
- `non_actionable_case.task_present=false`
- `non_actionable_case.actionable_count=0`

Interpretation:

- Heartbeat summary did not surface a known live actionable task.
- This confirms a functional compatibility bug in current Todoist service query logic under the installed SDK/API behavior.

Cleanup confirmation:

- Temporary validation tasks were deleted after probe.
- Temporary diagnostic project (`UA Diagnostic Tmp`) was deleted.

### C. Remediation + re-validation (same session)

Applied compatibility patch in `src/universal_agent/services/todoist_service.py`:

- Added paginator flattening (`Iterator[list[...]]` support) for projects/sections/labels/tasks.
- Added API token fallback support for `TODOIST_API_KEY` when `TODOIST_API_TOKEN` is unset.
- Reworked actionable query path to use `label="agent-ready"` + local deterministic filtering when SDK does not support `filter=`.

Added regression coverage in `tests/unit/test_todoist_service.py`:

- paged iterator taxonomy bootstrap compatibility test,
- no-`filter`-kwarg actionable filtering compatibility test.

Re-run evidence:

1. Guarded live Todoist tests:

```bash
set -a; source .env; set +a
RUN_TODOIST_LIVE_TESTS=1 uv run pytest tests/integration/test_todoist_live_guarded.py -q
```

Result: `2 passed`.

2. Real heartbeat actionable/non-actionable probe:

- `actionable_case.task_present=true`
- `actionable_case.actionable_count=1`
- `non_actionable_case.task_present=false`
- `non_actionable_case.actionable_count=0`

Interpretation:

- Deterministic heartbeat summary now correctly includes a live actionable task and excludes the same task once blocked.
