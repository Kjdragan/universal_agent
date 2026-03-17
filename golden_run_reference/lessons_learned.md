# Lessons Learned — Pipeline Failures and Permanent Fixes

> This document records every significant pipeline failure, its root cause, and the
> permanent fix applied. Use this to diagnose new failures — the pattern may match
> a known issue.

---

## Issue #3: TaskStop Death Loop (March 17, 2026)

### Symptom
On a **brand new session** with zero running tasks, Simone's first action was calling `TaskStop` with fabricated IDs like `bg_research_russia_ukraine`. This persisted for 100+ consecutive calls, never doing productive work.

### Root Cause
`TaskStop` is a **built-in Claude Code SDK tool** that the Claude Code preset exposes to the model. The model confused its planned `Task()` delegations with "background tasks" needing lifecycle management. It fabricated IDs for tasks it planned to create and tried to "clean the slate" before starting work.

**None of our code, knowledge files, or system prompt mentioned TaskStop.** The hook injection explicitly said "start with productive work." The model was primed by the SDK's own tool definition.

### What Didn't Work
| Attempted Fix | Why It Failed |
|---------------|---------------|
| Blacklist specific task ID prefixes (task_, session_, run_) | Model switched to `bg_*` prefix |
| Circuit-breaker after 2 failures with redirect message | Model ignored redirect and kept calling |
| Removed "TaskStop" from error messages | Model still called it — term came from SDK preset |
| Added "first-action rule" to system prompt | Model ignored the rule |
| Allowlist approach (only accept opaque token IDs) | Belt-and-suspenders, but doesn't address root cause |

### Permanent Fix
**Added `TaskStop` and `task_stop` to `DISALLOWED_TOOLS` in `constants.py`.**

This blocks the tool at the SDK level — the model never sees it in its available tools. Since Simone uses foreground `Task()` delegations that run to completion, there is no legitimate use case for stopping tasks mid-execution.

### Key Insight
> When a model hallucination stems from the tool's mere existence in the toolset,
> no amount of prompting or error handling will fix it. The only reliable fix is
> removing the tool entirely.

### Files Changed
- `src/universal_agent/constants.py` — Added `TaskStop`, `task_stop` to `DISALLOWED_TOOLS`

---

## Issue #2: Playwright Version Mismatch for PDF (March 17, 2026)

### Symptom
`mcp__internal__html_to_pdf` failed with: `Executable doesn't exist at chromium_headless_shell-1208`. WeasyPrint fallback also failed because the model passed the wrong HTML path.

### Root Cause (Two Issues)

**A) Playwright browser version drift**
The `_ensure_playwright_chromium()` function in `pdf_bridge.py` checked for `chromium-*` directories. It found stale `chromium-1212` from an older install and skipped reinstall. But Playwright 1.58 actually needed `chromium_headless_shell-1208` at runtime.

**B) Path mismatch (model error)**
`run_report_generation` output the report to `work_products/report.html`, but the model passed `tasks/russia_ukraine_war_news/report.html` to `html_to_pdf`.

### Permanent Fix
**A)** Changed the glob check from `chromium-*` to `chromium_headless_shell-*` so the auto-install triggers correctly when the right version is missing.

**B)** This was a model error, not a code bug. The path mismatch self-corrected once Playwright worked properly (follow-up golden run used the correct path).

### Key Insight
> Auto-install checks must verify the **specific binary** the runtime needs, not
> just any binary in the cache. Version-pinned dependencies can drift silently.

### Files Changed
- `src/universal_agent/tools/pdf_bridge.py` — Fixed `_ensure_playwright_chromium()` version check

---

## Issue #1: Silent Multi-Layer Pipeline Drift (February 23, 2026)

### Symptom
The golden prompt stopped producing results. No single error — the pipeline silently degraded across multiple layers.

### Root Cause (Six Interacting Issues)

| Layer | Drift | Effect |
|-------|-------|--------|
| `constants.py` | `run_research_phase` in `DISALLOWED_TOOLS` | SDK hid the tool from ALL agents including subagents |
| `hooks.py` | `_primary_transcript_path` never initialized | Subagent detection always returned False |
| `hooks.py` | Checked `parent_tool_use_id` in PreToolUse | Field doesn't exist in SDK hook data |
| `prompt_builder.py` | No instruction against `run_in_background` | Primary wasted turns polling async subagents |
| `research-specialist.md` | No `SESSION WORKSPACE` section | Files scattered to repo root |
| `research-specialist.md` | Crawl ban only in `composio_pipeline` mode | Fallback used Composio fetch tools |

### Permanent Fix
- Removed `run_research_phase` from `DISALLOWED_TOOLS`
- Emptied `PRIMARY_ONLY_BLOCKED_TOOLS` (subagent detection unreliable)
- Added `SESSION WORKSPACE` section to agent definitions
- Added global crawl ban to research-specialist
- Created 41-test drift detection suite (`test_research_pipeline_drift.py`)

### Key Insight
> In an agentic system, drift is not a single-point failure. It compounds across
> prompt text, hook logic, SDK configuration, and agent definitions. Standard unit
> tests on individual functions won't catch it — you need integration tests that
> validate the complete tool sequence.

### Files Changed
- `src/universal_agent/constants.py`
- `src/universal_agent/hooks.py`
- `src/universal_agent/prompt_builder.py`
- `.claude/agents/research-specialist.md`
- `tests/unit/test_research_pipeline_drift.py` (NEW — 41 tests)

---

## Common Failure Patterns Quick Reference

| What You See | Likely Cause | Check First |
|-------------|--------------|-------------|
| `TaskStop` as first action | Tool exposed to model | Is `TaskStop` in `DISALLOWED_TOOLS`? |
| "Tool is blocked" / "denied" | Tool in `DISALLOWED_TOOLS` | Remove from list |
| PDF conversion fails | Playwright version drift | Run `playwright install chromium` |
| Files at repo root | Missing workspace injection | Check `on_pre_task_skill_awareness` hook |
| Primary polls with sleep+tail | `run_in_background: true` | Check guardrail in `tool_schema.py` |
| No `refined_corpus.md` | Research phase skipped | Check `run_research_phase` not banned |
| No `report.html` | Report phase skipped | Check `run_report_generation` not banned |
| 50+ tool calls | Hallucination loop | Check for banned tools causing retry loops |
| Wrong PDF path | Model used task dir instead of work_products | Playwright working? (fixes the fallback issue) |
