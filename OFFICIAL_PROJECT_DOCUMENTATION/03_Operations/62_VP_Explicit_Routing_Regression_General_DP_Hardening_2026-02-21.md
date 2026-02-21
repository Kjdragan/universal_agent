# 62) VP Explicit Routing Regression ("General DP") Hardening — 2026-02-21

## Purpose
Document the regression observed in the "use the general DP/VP" run, identify root causes, and record the hardening changes applied to restore deterministic VP tool-first behavior.

## Incident Snapshot
- User request: "Simone, I need you to use the general DP to create ... a thousand word story ... and gmail that to me."
- Observed behavior (run starting ~23:18 local):
  - Primary agent explored codebase/tooling first (Grep/Glob/Read).
  - Agent attempted `vp_dispatch_mission` inside `mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL`.
  - Composio returned: `Tool VP_DISPATCH_MISSION not found`.
  - Agent then bypassed VP delegation and completed task directly.

## Root Causes
1. Intent alias gap:
- Explicit VP intent detector covered "General VP" but not "General DP" phrasing.

2. Guardrail gap in wrong namespace path:
- Existing enforcement blocked `Task(...)` fallback for VP-intent turns.
- It did **not** block `vp_*` attempted as inner `tool_slug` within Composio multi-execute.

3. Dispatch-first contract not strict enough:
- VP-intent turns were not requiring first tool call to be `vp_dispatch_mission`, allowing non-essential discovery/exploration before delegation.

## Hardening Implemented
### A) Expanded explicit VP intent detection
- Added DP alias coverage for user phrasing variants:
  - `general dp`, `coder dp`
  - `use/delegate ... (vp|dp)` variants
- File: `src/universal_agent/hooks.py`

### B) Dispatch-first enforcement for VP-intent turns
- New rule: when explicit VP intent is detected and dispatch has not yet occurred, first allowed tool call must be VP mission tooling (`vp_dispatch_mission` path).
- Blocks unrelated pre-dispatch tool calls to prevent drift/off-happy-path exploration.
- File: `src/universal_agent/hooks.py`

### C) Block invalid Composio wrapping of VP tools
- New schema guardrail: deny any `COMPOSIO_MULTI_EXECUTE_TOOL` payload whose inner `tool_slug` starts with `vp_`.
- Corrective guidance returned to model: call `vp_dispatch_mission`/`vp_wait_mission` directly.
- File: `src/universal_agent/guardrails/tool_schema.py`

### D) Prompt policy reinforcement
- Added explicit instruction that VP tools must not be wrapped in Composio multi-execute and should be called directly.
- File: `src/universal_agent/prompt_builder.py`

## Tests Added/Updated
- `tests/unit/test_hooks_vp_tool_enforcement.py`
  - Added DP-alias regression test.
  - Added pre-dispatch strict enforcement test.

- `tests/unit/test_tool_schema_guardrail.py`
  - Added block test for `COMPOSIO_MULTI_EXECUTE_TOOL` containing `vp_dispatch_mission` inner slug.

## Verification Results
- Command:
  - `uv run pytest -q tests/unit/test_hooks_vp_tool_enforcement.py tests/unit/test_tool_schema_guardrail.py`
- Result:
  - `26 passed`

## Expected Post-Fix Behavior (Happy Path)
For explicit General/Coder VP requests:
1. Primary must dispatch via `vp_dispatch_mission` first.
2. Primary may then wait/read mission state via `vp_wait_mission` (and related VP tools).
3. Follow-up actions (e.g., Gmail send) occur after VP output, not instead of VP delegation.
4. Wrong path (`vp_*` via Composio wrapper) is denied immediately.

## Operational Notes
- Preferred user phrasing remains: "use the General VP" / "use the Coder VP".
- DP alias is now supported as resilience, but VP naming remains canonical.
- This hardening is policy-level and deterministic; it does not depend on model self-discipline.

## Remaining Considerations
- If future runs show edge-cases where pre-dispatch strictness is too aggressive, refine allowlist carefully without re-opening Task/Composio bypass paths.
