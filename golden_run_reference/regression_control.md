# Regression Control and Recovery

> Invariants, test suite, and recovery procedures for the research pipeline.

---

## Hard Invariants (Never Violate)

### SDK Level (`constants.py`)

1. **`DISALLOWED_TOOLS` must NOT contain any tool that subagents need.**
   The SDK hides these from ALL agents. Hooks cannot override.

2. **`TaskStop` and `task_stop` MUST be in `DISALLOWED_TOOLS`.**
   Simone doesn't use background tasks. Removing this causes death loops.

3. **`PRIMARY_ONLY_BLOCKED_TOOLS` must remain empty.**
   No reliable way to detect subagent context in PreToolUse hooks.

4. **Composio crawl/fetch tools must be in `DISALLOWED_TOOLS`.**
   All crawling goes through Crawl4AI Cloud API via `run_research_phase`.

### Agent Definition Level (`.claude/agents/*.md`)

5. **`research-specialist.md` must list `mcp__internal__run_research_phase` in `tools:` frontmatter.**

6. **`research-specialist.md` must have a `SESSION WORKSPACE` section.**

7. **`research-specialist.md` must have a `GLOBAL CRAWL BAN`** (all modes).

### Hook Level (`hooks.py`)

8. **PreToolUse hooks must NOT check `parent_tool_use_id`** for subagent detection (not in SDK schema).

9. **`run_in_background` must be stripped** from Task calls for pipeline subagents.

### Workspace Level

10. **`CURRENT_SESSION_WORKSPACE` must be injected** into every subagent's `systemMessage`.

11. **`capabilities.md` must be dynamically generated** at session start.

---

## Regression Test Suite

```bash
# Run all drift detection tests
uv run pytest tests/unit/test_research_pipeline_drift.py -v

# Run full validation suite
uv run pytest -q \
  tests/unit/test_hooks_vp_tool_enforcement.py \
  tests/unit/test_prompt_assets_capabilities.py \
  tests/unit/test_agent_definition_tooling.py \
  tests/unit/test_research_pipeline_drift.py
```

### What Each Section Guards (41 Tests)

| Section | Tests | Catches |
|---------|-------|---------|
| Tool Permission Invariants | 14 | Subagent tools in `DISALLOWED_TOOLS`; crawl tools escaping ban |
| Agent Definition Integrity | 9 | Missing tools in frontmatter; missing session workspace section |
| `run_in_background` Guardrail | 3 | Guardrail not stripping background flag for pipeline subagents |
| Golden Run Sequence | 3 | Missing `run_research_phase` or `run_report_generation` |
| Session Workspace Structure | 4 | Missing `refined_corpus.md`, missing HTML report, repo root leaks |
| Prompt Builder Instructions | 3 | Delegation instructions for research-specialist and report-writer |
| Dynamic Capabilities | 2 | `capabilities.md` parses `.claude/agents/*.md` correctly |
| Documentation | 2 | SDK permissions doc exists |

### When to Run

- **Before any commit** touching: `constants.py`, `hooks.py`, `main.py`, `prompt_builder.py`, `agent_setup.py`, `.claude/agents/*.md`
- **After any SDK upgrade**
- **After any gateway restart** (smoke test)
- **In CI** (runs automatically)

---

## Recovery Procedure

### Step 1: Immediate Diagnosis

```bash
# Run drift tests
uv run pytest tests/unit/test_research_pipeline_drift.py -v

# Check DISALLOWED_TOOLS
grep -A 50 'DISALLOWED_TOOLS' src/universal_agent/constants.py

# Check agent tools
head -10 .claude/agents/research-specialist.md

# Check Playwright
ssh hostinger-vps 'ls /home/ua/.cache/ms-playwright/chromium_headless_shell-*'
```

### Step 2: Common Fixes

| Symptom | Root Cause | Fix |
|---------|-----------|-----|
| "Tool is blocked" | Tool in `DISALLOWED_TOOLS` | Remove from list |
| TaskStop death loop | `TaskStop` not in `DISALLOWED_TOOLS` | Add it back |
| Files at repo root | Missing workspace injection | Check hook |
| Primary polls with sleep | `run_in_background: true` | Check guardrail |
| PDF fails | Playwright version drift | `playwright install chromium` |
| Composio fetch instead of Crawl4AI | Crawl tools not banned | Add to `DISALLOWED_TOOLS` |

### Step 3: After Fix

```bash
# Run tests
uv run pytest tests/unit/test_research_pipeline_drift.py -v

# Deploy and restart
git push origin develop
ssh hostinger-vps 'cd /opt/universal_agent && git pull origin develop'
ssh hostinger-vps 'sudo systemctl restart universal-agent-gateway'

# Run golden prompt to verify
```

---

## Cross-References

- **SDK permissions**: `docs/002_SDK_PERMISSIONS_HOOKS_SUBAGENTS.md`
- **Architecture**: `docs/001_AGENT_ARCHITECTURE.md`
- **Tests**: `tests/unit/test_research_pipeline_drift.py`
- **Constants**: `src/universal_agent/constants.py`
- **Agent definitions**: `.claude/agents/research-specialist.md`, `.claude/agents/report-writer.md`
- **Guardrails**: `src/universal_agent/guardrails/tool_schema.py`
- **Hooks**: `src/universal_agent/hooks.py`
- **Prompt builder**: `src/universal_agent/prompt_builder.py`
