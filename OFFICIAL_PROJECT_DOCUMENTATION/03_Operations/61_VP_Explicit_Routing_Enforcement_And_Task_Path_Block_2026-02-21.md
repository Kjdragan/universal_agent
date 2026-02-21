# 61_VP_Explicit_Routing_Enforcement_And_Task_Path_Block_2026-02-21

## Summary
Implemented strict VP-intent routing enforcement to prevent silent fallback to `Task` delegation when the user explicitly asks for a VP lane (for example, "use the General VP").

Date: 2026-02-21

## Problem
1. A request with explicit VP intent could still be satisfied through `Task(subagent_type='general-purpose', ...)`.
2. That produced correct output in some runs, but bypassed VP mission ledger visibility and external VP mission lifecycle.

## Implementation
1. Added explicit VP intent detection in hook layer:
- `src/universal_agent/hooks.py`
- Pattern matching for explicit VP phrases and VP lane identifiers.

2. Added turn-level VP routing state:
- `AgentHookSet` now tracks:
  - current turn prompt
  - whether VP-only path is required
  - whether `vp_dispatch_mission` has already been called this turn

3. Added pre-tool enforcement:
- In `on_pre_tool_use_ledger`, when explicit VP intent is present:
  - block `Task(...)` use in primary context until VP mission dispatch path is used
  - return corrective instruction requiring:
    1. `vp_dispatch_mission`
    2. `vp_wait_mission`

4. Added payload-level catch:
- Even if user-prompt state is unavailable, `Task` payloads that explicitly attempt "General VP"/"Coder VP" delegation are blocked.

## Validation
1. New unit tests:
- `tests/unit/test_hooks_vp_tool_enforcement.py`
  - blocks `Task` on explicit General VP user intent
  - blocks `Task` when payload tries VP delegation
  - allows normal `Task` when VP intent absent
  - allows `Task` after VP dispatch marker in same turn

2. Regression tests:
- `tests/unit/test_mission_guardrails.py`
- `tests/unit/test_storage_api_helpers.py`

3. Test run result:
- `11 passed`

## Operational Impact
1. Explicit "use General VP/Coder VP" requests now reliably route through VP mission tools.
2. This aligns user-visible behavior with VP mission dashboard/session expectations and eliminates silent Task-path drift for explicit VP instructions.
