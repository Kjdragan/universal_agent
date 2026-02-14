# 34. Code Writer + Analytics Routing Policy (Local-First, CodeInterpreter Fallback)

Date: 2026-02-14

## Why This Doc Exists

We saw a failure mode where the system attempted to find a "Code Interpreter" capability via tool discovery and got misrouted (e.g., discovery suggested Databricks), while the actual work was successfully completed using local sandbox execution.

This doc defines a **stable, repeatable routing policy** for:
- "Write/modify code in the repo" work
- "Run analytics / charts" work
- When and how to use Composio CodeInterpreter

Goals:
- Avoid expensive/slow tool discovery loops.
- Prefer fast local execution when possible.
- Still support isolated, persistent execution environments when needed.

## Architecture Overview

### Primary Orchestrator (Simone)
Role:
- Orchestration, decomposition, recovery, synthesis.
- Delegates real work to specialists.

### `code-writer` Sub-Agent (Focused Repo Coding)
File:
- `.claude/agents/code-writer.md`

Use when:
- Implementing features, refactors, scripts, tests inside this repo.

Key properties:
- Minimal prompt surface area.
- Runs local commands (`uv run ...`, `pytest`).
- Produces reviewable diffs + tests.

### `data-analyst` Sub-Agent (Local-First Analytics)
File:
- `.claude/agents/data-analyst.md`

Policy:
- Prefer **local** execution first (Bash + `uv run python`).
- Use **Composio CodeInterpreter** only when isolation/persistence is explicitly beneficial.

## Routing Policy (Decision Tree)

### A) Does the task require changing this repository?
If YES:
- Delegate to `code-writer`.

Examples:
- "Add a new guardrail"
- "Implement a new internal tool"
- "Fix failing tests"

### B) Is the task analytics-only (charts, stats, transforms)?
If YES:
1. Prefer local:
   - `Bash` + `uv run python ...`
   - Write outputs to `work_products/analysis/`
2. If local fails or isolation is needed:
   - Use CodeInterpreter toolkit (`CODEINTERPRETER_*`).

### C) Avoid using tool discovery for compute
Do NOT use `mcp__composio__COMPOSIO_SEARCH_TOOLS` to "find code interpreter".
We already know the toolkit and slugs.

## Composio CodeInterpreter Toolkit (Reference)

Toolkit slug: `CODEINTERPRETER` (version `20260211_00`, NO_AUTH)

Expected tool slugs:
- `CODEINTERPRETER_CREATE_SANDBOX`
- `CODEINTERPRETER_EXECUTE_CODE`
- `CODEINTERPRETER_RUN_TERMINAL_CMD`
- `CODEINTERPRETER_UPLOAD_FILE_CMD`
- `CODEINTERPRETER_GET_FILE_CMD`

Remote file conventions:
- Use `/home/user/...` for all read/write.
- Avoid `plt.show()`; write images to files and pull them back.
- Reuse `sandbox_id` for persistent multi-step work.

## Validation / Smoke Tests

### Unit Tests (No Network)
- `uv run python -m pytest -q tests/unit/test_generate_image_with_review_tool.py`

### Live CodeInterpreter Smoke Test (Network)
Script:
- `scripts/experiments/codeinterpreter_smoke_test.py`

Run:
- `RUN_CODEINTERPRETER_SMOKE=1 uv run python -m pytest -q tests/integration/test_codeinterpreter_smoke.py`

Or directly:
- `uv run python scripts/experiments/codeinterpreter_smoke_test.py`

Outputs:
- `work_products/analysis/codeinterpreter_smoke.txt`

## Related Changes (2026-02-14)

- Added `code-writer` agent for repo code changes.
- Updated `data-analyst` agent to be local-first and to reference the correct CodeInterpreter slugs.
- Updated orchestration prompts to avoid implying CodeInterpreter is the default compute lane.
- Added Pro-model image generation + self-review loop tool:
  - `mcp__internal__generate_image_with_review`

## Known Risks / Next Steps

- CodeInterpreter response shapes may vary; the smoke test script is intentionally tolerant, but we may still need to adapt parsing for different Composio SDK versions.
- Consider adding a small internal helper/wrapper for CodeInterpreter to reduce boilerplate around file upload/download and sandbox_id reuse.
