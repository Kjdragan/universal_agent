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
