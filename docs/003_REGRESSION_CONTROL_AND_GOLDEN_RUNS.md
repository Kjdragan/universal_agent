# 003 - Regression Control and Golden Run Validation

> **Purpose**: Prevent pipeline drift by defining golden run baselines, regression
> test coverage, and recovery procedures.
>
> Written after the Feb 23, 2026 incident where the research pipeline was silently
> broken across multiple layers (SDK permissions, hook logic, workspace context,
> async execution) and required a multi-hour recovery session to restore.

---

## 1. Background: What Went Wrong

A prompt that had been producing reliable end-to-end results ("Search for X, create
a report, save as PDF, email it to me") stopped working. The failure was **silent
and multi-layered** — no single change broke it, but accumulated drift across
several files created a cascade of failures:

| Layer | Drift | Effect |
|-------|-------|--------|
| `constants.py` | `run_research_phase` added to `DISALLOWED_TOOLS` | SDK hid the tool from ALL agents including subagents |
| `hooks.py` | `_primary_transcript_path` never set in `AgentHookSet` | Subagent detection always returned False |
| `hooks.py` | Hook checked `parent_tool_use_id` in PreToolUse input | Field does not exist in SDK hook data |
| `prompt_builder.py` | No instruction against `run_in_background` | Primary wasted turns polling async subagents |
| `research-specialist.md` | No `SESSION WORKSPACE` section | Files scattered to repo root |
| `research-specialist.md` | Crawl ban only in `composio_pipeline` mode | Fallback mode used Composio fetch tools |

**Key lesson**: In an agentic system, drift is not a single-point failure. It
compounds across prompt text, hook logic, SDK configuration, and agent definitions.
Standard unit tests on individual functions would not have caught this because the
failure only materialized when all layers interacted at runtime.

## 2. Golden Run Definition

A **golden run** is a known-good execution of a reference prompt that produces the
expected end-to-end result. It serves as the regression baseline.

### 2.1 Reference Prompt

```
Search for the latest information from the Russia-Ukraine war over the last days.
Create a report. Save the report as a PDF and Gmail that to me.
```

### 2.2 Expected Tool Sequence (Subsequence)

The primary agent should produce tool calls containing this ordered subsequence:

```
1. Task(research-specialist)
2. COMPOSIO_MULTI_EXECUTE_TOOL  (search)
3. run_research_phase            (crawl + refine via Crawl4AI)
4. Task(report-writer)
5. run_report_generation         (outline → draft → synthesize → HTML)
6. html_to_pdf                   (Chrome headless conversion)
7. COMPOSIO_MULTI_EXECUTE_TOOL  (Gmail send with attachment)
```

### 2.3 Expected Session Workspace Structure

```
session_*/
  search_results/
    crawl_*.md              (20+ crawled source files)
    processed_json/         (optional, search result JSONs)
  tasks/{task_name}/
    refined_corpus.md       (REQUIRED — synthesized research output)
    filtered_corpus/        (individual filtered crawl files)
    research_overview.md    (optional summary)
  work_products/
    report.html             (REQUIRED — final HTML report)
    *.pdf                   (REQUIRED — PDF conversion)
    _working/               (intermediate drafts)
      outline.json
      sections/*.md
  run.log
```

### 2.4 Timing Baseline

| Phase | Golden Run | Acceptable Range |
|-------|-----------|-----------------|
| Research (search + crawl + refine) | ~85s | 60-180s |
| Report (outline + draft + synthesize) | ~90s | 60-180s |
| PDF + Email | ~30s | 15-60s |
| **Total** | **~375s** | **200-500s** |

### 2.5 Golden Run Reference Session

```
Session:  session_20260223_215506_140963c1
Tools:    9 tool calls
Time:     376.2s
Iters:    1 (single iteration, no retries)
```

## 3. Regression Test Suite

### 3.1 Test File

`tests/unit/test_research_pipeline_drift.py` — **41 tests** across 7 sections.

Run with:
```bash
uv run pytest tests/unit/test_research_pipeline_drift.py -v
```

### 3.2 What Each Section Guards

| Section | Tests | Catches |
|---------|-------|---------|
| **Tool Permission Invariants** | 14 | Subagent tools accidentally added to `DISALLOWED_TOOLS`; Composio crawl/fetch escaping the ban; `PRIMARY_ONLY_BLOCKED_TOOLS` becoming non-empty |
| **Agent Definition Integrity** | 9 | Missing tools in frontmatter; missing session workspace section; missing crawl ban; missing mode selection |
| **run_in_background Guardrail** | 3 | Guardrail not stripping `run_in_background` for pipeline subagents |
| **Golden Run Sequence** | 3 | Expected tool subsequence validation; detects missing `run_research_phase` or `run_report_generation` |
| **Session Workspace Structure** | 4 | Validates directory layout; detects missing `refined_corpus.md`, missing HTML report, repo root file leaks |
| **Prompt Builder Instructions** | 3 | Delegation instructions present for research-specialist and report-writer |
| **Dynamic Capabilities Generation**| 2 | Verifies `capabilities.md` parses `.claude/agents/*.md` and writes valid output to the session workspace |
| **Documentation** | 2 | SDK permissions reference doc exists and covers required topics |

### 3.3 When to Run

- **Before every commit** that touches: `constants.py`, `hooks.py`, `main.py`,
  `prompt_builder.py`, `agent_setup.py`, `guardrails/tool_schema.py`,
  `.claude/agents/*.md`, `tools/research_bridge.py`, `tools/internal_registry.py`
- **After any SDK upgrade** (`claude-agent-sdk-python`)
- **After any gateway restart** (as a smoke test)
- **In CI** (already runs via `pnpm test` / `uv run pytest tests/unit/`)

## 4. Invariants to Never Violate

These are the hard invariants that, if broken, will silently break the pipeline.
The regression tests encode all of these, but they are stated here for human reference.

### 4.1 SDK-Level (constants.py)

1. **`DISALLOWED_TOOLS` must NOT contain any tool that subagents need.**
   The SDK hides these tools from ALL agents. Hooks cannot override this.

2. **`PRIMARY_ONLY_BLOCKED_TOOLS` must remain empty.**
   There is no reliable way to detect subagent context in PreToolUse hooks.
   `parent_tool_use_id` is not in `PreToolUseHookInput`.
   `transcript_path` may not differ for foreground Task calls.

3. **Composio crawl/fetch tools must be in `DISALLOWED_TOOLS`.**
   All crawling goes through Crawl4AI Cloud API via `run_research_phase`.

### 4.2 Agent Definition Level (.claude/agents/*.md)

4. **`research-specialist.md` must list `mcp__internal__run_research_phase` in
   `tools:` frontmatter.**

5. **`research-specialist.md` must have a `SESSION WORKSPACE` section** instructing
   the subagent to use the injected `CURRENT_SESSION_WORKSPACE` path.

6. **`research-specialist.md` must have a `GLOBAL CRAWL BAN`** that applies across
   ALL modes, not just `composio_pipeline`.

### 4.3 Hook Level (hooks.py)

7. **PreToolUse hooks must NOT check `parent_tool_use_id`** for subagent detection.
   It is not present in `PreToolUseHookInput`.

8. **If `_primary_transcript_path` tracking is used, it must be initialized** on the
   first hook call, not left as `None`.

### 4.4 Guardrail Level (tool_schema.py)

9. **`run_in_background` must be stripped** from Task calls for `research-specialist`
   and `report-writer`. These are sequential pipeline prerequisites.

### 4.5 Workspace Level

10. **`CURRENT_SESSION_WORKSPACE` must be injected** into every subagent's
    `systemMessage` via the `on_pre_task_skill_awareness` hook.

11. **`capabilities.md` must be dynamically generated** at the start of the session
    and persisted to `CURRENT_SESSION_WORKSPACE/capabilities.md`. This file is injected
    into the primary agent's system prompt and serves as the single source of truth for
    routing and delegation. It must contain the dynamically discovered list of agents
    (from `.claude/agents/*.md`) and skills (from `.claude/skills/*/SKILL.md`).

## 5. Recovery Procedure

If the pipeline breaks and you need to recover:

### 5.1 Immediate Diagnosis

```bash
# Run drift detection tests
uv run pytest tests/unit/test_research_pipeline_drift.py -v

# Check what's in DISALLOWED_TOOLS
grep -A 50 'DISALLOWED_TOOLS' src/universal_agent/constants.py

# Check PRIMARY_ONLY_BLOCKED_TOOLS
grep -A 10 'PRIMARY_ONLY_BLOCKED_TOOLS' src/universal_agent/constants.py

# Check agent definition tools
head -10 .claude/agents/research-specialist.md
```

### 5.2 Common Fixes

| Symptom | Root Cause | Fix |
|---------|-----------|-----|
| "Tool is blocked" / "denied" | Tool in `DISALLOWED_TOOLS` | Remove from list |
| Subagent can't find tool | Tool in `DISALLOWED_TOOLS` | Remove from list |
| Files at repo root | Missing workspace injection | Check `on_pre_task_skill_awareness` hook |
| Primary polls with sleep+tail | `run_in_background: true` | Check guardrail in `tool_schema.py` |
| Composio fetch instead of Crawl4AI | Crawl tools not banned | Add to `DISALLOWED_TOOLS` |
| Hook blocks subagent tools | `PRIMARY_ONLY_BLOCKED_TOOLS` non-empty | Empty the list |

### 5.3 After Fix

```bash
# Run tests
uv run pytest tests/unit/test_research_pipeline_drift.py -v

# Restart gateway to pick up changes
# (Python process caches imports at startup)
kill <gateway_pid>; ./start_gateway.sh --clean-start

# Run golden prompt to verify
```

## 6. Adding New Pipeline Stages

When adding new stages to the research pipeline:

1. **Add the tool to `_SUBAGENT_REQUIRED_TOOLS`** in
   `tests/unit/test_research_pipeline_drift.py`
2. **Add the tool to the agent definition's `tools:` frontmatter** if a subagent
   needs it
3. **Add the tool to `GOLDEN_TOOL_SUBSEQUENCE`** in the test file
4. **Verify it is NOT in `DISALLOWED_TOOLS`**
5. **Run the drift tests** before committing
6. **Update this document** (Section 2.2 and 2.3)

## 7. Adding New Subagent Types

When adding new subagent types to the pipeline:

1. **Create `.claude/agents/{name}.md`** with correct `tools:` frontmatter
2. **Add a `TestXxxDefinition` class** in the drift test file
3. **If the subagent is a sequential prerequisite**, add its type to
   `_FOREGROUND_ONLY_SUBAGENTS` in `guardrails/tool_schema.py`
4. **Inject `CURRENT_SESSION_WORKSPACE`** via `on_pre_task_skill_awareness`
5. **Run the full drift test suite**

## 8. Cross-References

- **SDK permissions reference**: `docs/002_SDK_PERMISSIONS_HOOKS_SUBAGENTS.md`
- **Architecture overview**: `docs/001_AGENT_ARCHITECTURE.md`
- **Drift detection tests**: `tests/unit/test_research_pipeline_drift.py`
- **Constants**: `src/universal_agent/constants.py`
- **Agent definitions**: `.claude/agents/research-specialist.md`, `.claude/agents/report-writer.md`
- **Guardrails**: `src/universal_agent/guardrails/tool_schema.py`
- **Hooks**: `src/universal_agent/hooks.py`
- **Prompt builder**: `src/universal_agent/prompt_builder.py`

---

## Appendix: Incident Timeline (Feb 23, 2026)

| Time | Event |
|------|-------|
| ~21:00 | User runs golden prompt. Pipeline fails silently. |
| ~21:07 | Diagnosis begins. `run_research_phase` blocked by SDK `disallowed_tools`. |
| ~21:15 | Root cause 1: Tool in `DISALLOWED_TOOLS` → moved to `PRIMARY_ONLY_BLOCKED_TOOLS`. |
| ~21:30 | Root cause 2: `_primary_transcript_path` never set in `AgentHookSet`. |
| ~21:33 | Run 2 fails. Hook still blocks. `parent_tool_use_id` not in SDK hook data. |
| ~21:39 | SDK investigation via DeepWiki confirms `PreToolUseHookInput` schema. |
| ~21:43 | Root cause 3: Subagent detection unreliable. `PRIMARY_ONLY_BLOCKED_TOOLS` emptied. |
| ~21:46 | Gateway restarted. |
| ~21:48 | Run 3 still fails (pre-restart code cached). Gateway re-restarted. |
| ~21:55 | **Run 4 succeeds.** Golden run restored. 376s, 9 tools, 1 iteration. |
| ~22:00 | Drift detection test suite created (41 tests). |
| ~22:10 | Reference documentation written (docs/002, docs/003). |
