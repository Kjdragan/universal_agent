# URW (Universal Ralph Wrapper) — Integration Workspace

This directory tracks the phased rollout of the URW wrapper around the Universal Agent system.

## Purpose
- Provide a structured long-running harness for multi-hour tasks.
- Keep the existing multi-agent system as the execution engine.
- Preserve fast-path execution by keeping harness activation opt-in.

## Key Documents
- Project Documentation Roadmap: `Project_Documentation/068_URW_PHASED_ROADMAP.md`
- Design Issues Tracker: `Project_Documentation/067_DESIGN_ISSUES_FOR_HARNESS.md`

## Status
- Phase 0: Adapter baseline ✅ (single-phase run verified)
- Phase 1: Evidence & verification pipeline ✅ (receipt evidence verified)
- Phase 2: Decomposition into phases (pending)
- Phase 3: Context injection + guardrails (pending)
- Phase 4: Controlled production usage (pending)

## Phase 0 Runbook (Adapter Baseline)
**Goal:** Confirm the UniversalAgentAdapter can execute a single phase and emit an artifact + verification record.

**Runner:** `URW/phase0_runner.py`

**Command (example):**
```bash
PYTHONPATH=src uv run python URW/phase0_runner.py \
  --workspace ./urw_phase0_workspace_run1 \
  --request "Write a short harness status summary and save it to phase0_report.md. Do not call external APIs or send messages."
```

**Expected Outputs:**
- `phase0_report.md` in the workspace root
- Verification artifact: `.urw/verification/verify_phase0_<id>_1.json`
- State DB: `.urw/state.db`

**Notes:**
- Requires the main multi-agent system runtime (Composio MCP server + auth) to be active.
- Use file-only requests for Phase 0 to avoid tool side effects.

## Phase 1 Runbook (Receipt Evidence)
**Goal:** Confirm receipt-based verification for side-effect tasks (Gmail/Slack).

**Runner:** `URW/phase1_receipt_runner.py`

**Command (example):**
```bash
PYTHONPATH=src uv run python URW/phase1_receipt_runner.py \
  --workspace ./urw_phase1_workspace_run2 \
  --to kevin.dragan@outlook.com \
  --connection "clearspring-cg / all-clearspring-cg"
```

**Expected Outputs:**
- Verification artifact: `.urw/verification/verify_phase1_<id>_1.json`
- Receipt confirmation: `.urw/verification/receipt_effect_*.json`
- State DB: `.urw/state.db`

**Notes:**
- Requires Composio MCP server + authenticated Gmail connection.
- Receipt verification is satisfied by the message ID/provider ID captured in the side-effect record.

## Notes
This folder is intended for implementation artifacts, integration notes, and future adapter code.
