# UA Gateway Smoke Test Matrix

**Owner:** Cascade
**Created:** 2026-01-24
**Purpose:** Quick sanity checks for CLI default vs Gateway preview paths.

## Matrix

| Scenario | Command | Expected Notes |
| --- | --- | --- |
| CLI default (interactive) | `PYTHONPATH=src python3 -m universal_agent.main` | Standard CLI behavior, run.log + trace.json, tool call summaries intact. |
| Gateway preview (separate workspace) | `PYTHONPATH=src python3 -m universal_agent.main --use-gateway` | AgentEvent rendering with tool call/result previews; gateway session workspace may differ from CLI workspace. |
| Gateway preview (CLI workspace) | `PYTHONPATH=src python3 -m universal_agent.main --use-gateway --gateway-use-cli-workspace` | Gateway runs in CLI workspace; trace/run.log parity expected; guardrails hooks active. |

## Quick Checks
- Verify auth prompts still pause and resume correctly.
- Confirm tool call preview + tool result preview appear in Gateway path.
- Confirm `run.log` and `trace.json` appear in expected workspace.
- Confirm `ua_gateway_guardrails_checklist.md` parity items remain valid.

## Results (2026-01-24)
- CLI default: failed to start (missing dependency `python-dotenv`; `ModuleNotFoundError: No module named 'dotenv'`).
- Gateway preview: failed to start (same missing `python-dotenv` import).
- Gateway preview + CLI workspace: failed to start (same missing `python-dotenv` import).

## Results (2026-01-24, venv)
Used `.venv/bin/python` after installing `python-dotenv` into the local venv.
- CLI default: startup completed; interactive prompt reached and accepted `quit` from stdin.
- Gateway preview: startup completed; gateway session created (separate workspace) and accepted `quit`.
- Gateway preview + CLI workspace: startup completed; gateway session created (CLI workspace) and accepted `quit`.
