# Quality Gate: run-profiler
Date: 2026-04-21

## Structural Checklist
- [x] Structure: SKILL.md has frontmatter (name, description), Goal, Success Criteria, Constraints, Context, Approach, Anti-Patterns — all core sections present.
- [x] Not a script wrapper: SKILL.md describes *what* to profile and *why* (the analytical goals), not just "run scripts/extract_profiler_data.py". Scripts assist the methodology defined in the skill.
- [x] Composable: References existing data sources (run_checkpoint.json, trace.json) without reinventing parsing. Could compose with `logfire-eval` skill for deeper trace analysis.
- [x] Generalizable: A different agent in a different session could follow this skill by reading the SKILL.md and running the scripts against any AGENT_RUN_WORKSPACES directory. No hardcoded session paths in the skill itself.
- [x] Progressive disclosure: SKILL.md is ~70 lines (lean). Heavy extraction logic lives in scripts/. Domain knowledge externalized to references/data_schema.md during Phase 5c.

## Improvements Made
- None needed; all 5 checks passed on first evaluation.

## Development Context

### What Was Discovered
- `run_checkpoint.json` contains rich session-level data: tool_call_count, execution_time_seconds, goal_satisfaction with pass/fail and tool_names array
- `trace.json` contains per-tool-call timing data with time_offset_seconds for computing individual call durations
- Individual tool call duration must be *derived* from consecutive offset gaps, not read directly
- `python` is not available on this VPS; must use `python3` explicitly
- Inline Python in bash heredocs with f-strings and backticks causes bash escaping issues; always use script files
- `_daemon_archives/` contains archived daemon run checkpoints — must be included for full coverage
- The auto-completion hook (`auto_completed_after_delivery`) fires when a session ends without explicit Task Hub disposition — this is a recurring pattern, not a bug
- Context pressure heuristic (tool_count * exec_time / 1000) is a reasonable proxy but not perfect

### Environment & Dependencies
- VPS: srv1360701 (Hostinger), Ubuntu, Python 3 available as `python3`
- Workspace root: `/opt/universal_agent/AGENT_RUN_WORKSPACES/`
- No `uv run` needed for these scripts — they use only stdlib (json, os, glob, collections, argparse, datetime)

### What Worked / What Didn't
- Reading checkpoint files directly with Python json.load worked reliably
- Globbing both `*/run_checkpoint.json` and `_daemon_archives/*/run_checkpoint.json` gave full coverage
- Computing tool durations from offset gaps produced reasonable estimates (though noisy for short calls)
- Inline Python with f-strings in bash failed — always write to .py files first

## Process Patterns for Future Skill-Building
- **Always check Python executable**: `python` may not exist; prefer `python3` or `uv run python`
- **Never inline complex Python in bash**: Write to a .py file, then execute. Saves debugging time.
- **Check for archived data**: Daemon/hook/cron runs often live in subdirectories like `_daemon_archives/`
- **Derived metrics need explicit methodology**: "Individual call duration from offset gaps" should be documented in the skill so different agents produce consistent results
- **Separate extraction from presentation**: The `extract_profiler_data.py` → `build_dashboard.py` split worked well — data extraction is reusable, presentation is format-specific

## Meta-Improvements

### Pipeline-Level Observations
- Task Forge Phase 4 should include a note about avoiding inline Python with f-strings in bash commands — this is a recurring failure mode that costs debugging time on every affected run.
- The quality gate template's "Development Context" section is extremely valuable for skills that interact with the filesystem. Future skills inherit this knowledge without re-discovery.

### Proposed Changes
- **To Task Forge SKILL.md, Phase 4**: Add a "Never inline complex Python in Bash" anti-pattern alongside the existing "never use pip" constraint. This is the #1 failure mode for Task Forge runs on this VPS.
- **Which Phase**: Phase 4 (Execute)
- **Status**: proposed

## Phase 5c: Improvement Pass (v0 -> v1)

### Changes Applied

| Pattern Applied | What Changed | Why |
|-----------------|-------------|-----|
| **Pushy description** | Expanded description with trigger phrases: "profile runs", "agent performance", "slow tools", "execution stats", "dashboard" | Skill-creator guidance: undertriggering is the default failure mode. More trigger phrases = more reliable activation. |
| **Externalize domain knowledge** | Created `references/data_schema.md` with JSON schemas for checkpoint and trace files, pressure score thresholds, and session type taxonomy | Domain knowledge was embedded in SKILL.md Context section. Externalizing keeps SKILL.md lean and allows schema updates independently. |
| **Preserve ephemeral code** | `scripts/extract_profiler_data.py` and `scripts/build_dashboard.py` already saved; added explicit reference in SKILL.md Approach section | Scripts survive sessions. SKILL.md now tells the agent exactly what to run. |
| **Specify reproducible methodology** | Added "What the scripts do" section and "Manual fallback" section to Approach | Different agents can produce consistent results even without the scripts. |
| **Tighten scope** | Added "raw extraction data also saved as JSON" to success criteria | Ensures the intermediate data artifact is always produced, enabling downstream consumption by other skills/agents. |

### Version Label
- v0 -> v1
- v0: Intent-only (5 min scaffolding)
- v1: +Externalized references +Pushy description +Reproducible methodology +Fixed deprecation warning

### Promotion Readiness
- Skill is ready for promotion to `task-skills/` (recurring use) but not yet `.claude/skills/` (permanent).
- Would benefit from one more live run to validate against different data patterns before permanent promotion.
