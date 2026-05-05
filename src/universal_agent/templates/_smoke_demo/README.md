# Phase 0 Smoke Demo

A minimal demo that verifies the `/opt/ua_demos/_smoke/` execution path
works end-to-end against real Anthropic endpoints. Used by the dependency
upgrade worker (PR 6b) as the gate that decides whether an Anthropic SDK
bump is safe to ship.

## What it checks

1. The subprocess inherits no `ANTHROPIC_BASE_URL` override (no ZAI mapping
   leakage).
2. The `anthropic` SDK can authenticate via the Max plan OAuth session and
   complete a one-shot message exchange.
3. The response carries the expected model and stop reason.

## How to run

From inside `/opt/ua_demos/_smoke/` on the VPS:

```bash
uv run python smoke.py
```

Exit codes:
- `0` — both checks passed
- `1` — anthropic call failed
- `2` — endpoint mismatch (env var leak)

## Pre-conditions

- `claude /login` was run once on the VPS with Kevin's Max plan account.
- `~/.claude/settings.json` may contain the ZAI mapping (UA's normal
  state). The demo's project-local `.claude/settings.json` overrides it.
- `anthropic` Python SDK is installed in the demo's `uv` environment.
