# UA Demo Workspace

A demo workspace under `/opt/ua_demos/<demo-id>/` is the unit of execution for
Phase 3 (Cody implementation) of the v2 ClaudeDevs intelligence pipeline.

## Contract

| File | Owner | Purpose |
|---|---|---|
| `.claude/settings.json` | scaffold | Vanilla Claude Code settings — no UA pollution, no ZAI mapping, no hooks. |
| `BRIEF.md` | Simone | Feature briefing in plain language. Cody reads this first. |
| `ACCEPTANCE.md` | Simone | Explicit success contract Cody must satisfy. |
| `business_relevance.md` | Simone | Kevin-facing rationale for client engagements. |
| `SOURCES/` | Simone | Curated subset of vault `raw/` docs. Cody can `grep` them. Full text, no truncation. |
| `pyproject.toml` | scaffold or Cody | Demo-local dependencies. Cody may modify. |
| `src/` | Cody | Demo implementation. |
| `BUILD_NOTES.md` | Cody | Documents any gaps where the official docs were unclear. **No invention.** |
| `run_output.txt` | Cody | Captured stdout from a successful run. |
| `manifest.json` | Cody | Metadata: versions used, endpoint hit, success status. Simone verifies this. |
| `FEEDBACK.md` | Simone | Iteration directive on a failed pass; Cody reads this on the next attempt. |

## Execution invariants

1. **Cody runs Claude Code from inside the workspace dir** so project-local
   `.claude/settings.json` takes precedence over `~/.claude/`.
2. **No `ANTHROPIC_AUTH_TOKEN` env var leaks into the demo subprocess.** The
   workspace inherits the Max plan OAuth session from a one-time `claude /login`
   on the VPS.
3. **Cody never invents API surface.** If the docs don't show how to do
   something, Cody documents the gap in `BUILD_NOTES.md` and stops; Simone
   resolves the gap on the next iteration.
4. **`manifest.json.endpoint_hit` records which endpoint actually served the
   demo.** Simone verifies this matches the entity page's
   `endpoint_required` field. A demo that accidentally hit the ZAI mapping
   is rejected.

See `docs/proactive_signals/claudedevs_intel_v2_design.md` §3, §8, §9.
