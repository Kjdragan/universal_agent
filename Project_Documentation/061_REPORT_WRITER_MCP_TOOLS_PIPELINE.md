# 061: Report Writer MCP Tools Pipeline

> **Session Date:** 2026-01-15
> **Focus:** Debugging and completing the report-writer tool pipeline

---

## 🎯 Session Objective

Ensure the entire report generation pipeline using `draft_report_parallel` and `compile_report` MCP tools functions correctly and robustly.

---

## ✅ Issues Resolved

### 1. Environment Variable Propagation (Critical)

**Problem:** `CURRENT_SESSION_WORKSPACE` was not being passed to the MCP server subprocess, causing tools to operate from the wrong directory.

**Symptoms:**
```
Error: CURRENT_SESSION_WORKSPACE not set. Cannot determine session workspace.
```

**Root Cause:** Environment variables set in `agent_core.py` were being set *after* the MCP server subprocess was already started.

**Fix:** Added `os.environ["CURRENT_SESSION_WORKSPACE"] = workspace_dir` in `main.py:setup_session()` (line 5709) **before** the MCP server subprocess starts.

**Files Modified:**
- `src/universal_agent/main.py` - Set env var early in `setup_session()`
- `src/universal_agent/agent_core.py` - Added backup env setting in MCP config

---

### 2. PROJECT_ROOT NameError in Tools

**Problem:** Both `draft_report_parallel` and `compile_report` tools referenced undefined `PROJECT_ROOT`.

**Fix:** Defined `PROJECT_ROOT` within each tool's function scope in `mcp_server.py`.

---

### 3. Report Section Ordering

**Problem:** Compiled reports had sections in alphabetical order by filename, not logical order (e.g., "Challenges" before "Executive Summary").

**Root Cause:** `compile_report.py` used `sorted(glob(...))` which sorted alphabetically:
- `challenges_solutions.md` (first alphabetically)
- `executive_summary.md` (should be first!)

**Fix:** Updated `parallel_draft.py` to prefix filenames with order numbers:
```python
# Before: sections/executive_summary.md
# After:  sections/01_executive_summary.md
```

**Files Modified:**
- `src/universal_agent/scripts/parallel_draft.py` - Added order prefix to filenames

---

### 4. Workspace Path Handling

**Problem:** Scripts were using incorrect paths for `sections_dir` and `output_path`.

**Fix:** Updated `compile_report.py` to correctly include `work_products/` in paths.

---

### 5. Dangerous `getcwd()` Fallback

**Problem:** `mcp_server.py` used `os.getcwd()` as fallback when env var not set, silently operating from wrong directory.

**Fix:** Removed fallback - tools now fail explicitly with clear error messages.

---

### 6. Report Writer System Prompt

**Problem:** Prompt was not explicit enough about the 3-phase workflow.

**Fix:** Rewrote prompt with:
- Clear phase breakdown (Planning → Drafting → Assembly)
- Exact JSON format for `outline.json`
- Exact tool call syntax
- Critical rules section

---

## 📁 Files Modified This Session

| File | Change |
|------|--------|
| `main.py:5709` | Set `CURRENT_SESSION_WORKSPACE` early in `setup_session()` |
| `agent_core.py:1004-1070` | Rewritten report-writer prompt |
| `agent_core.py:816-818` | Added env to MCP server config |
| `mcp_server.py:485-497` | Removed `getcwd()` fallback from `draft_report_parallel` |
| `mcp_server.py:532-553` | Defined `PROJECT_ROOT` + removed fallback from `compile_report` |
| `scripts/parallel_draft.py:13-18, 88-90` | Added order number prefix to section filenames |
| `scripts/compile_report.py:43-44` | Fixed path construction |

---

## 🧪 Tests Created

**File:** `tests/test_workspace_environment.py` (9 tests, all passing)

| Test | Purpose |
|------|---------|
| `test_draft_report_parallel_without_env` | Verify explicit failure when env not set |
| `test_draft_report_parallel_with_nonexistent_workspace` | Verify path validation |
| `test_compile_report_without_env` | Verify explicit failure when env not set |
| `test_compile_report_with_nonexistent_workspace` | Verify path validation |
| `test_mcp_server_subprocess_environment` | Verify subprocess inherits env |
| `test_mcp_server_subprocess_no_environment` | Verify no silent defaults |
| `test_parallel_draft_script_workspace_isolation` | Verify script uses correct workspace |
| `test_workspace_not_confused_with_repo_root` | Prevent repository-root mistakes |
| `test_multiple_workspaces_dont_interfere` | Verify session isolation |

---

## 🔄 Report Writer Workflow (Final)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    REPORT WRITER PIPELINE                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Phase 1: PLANNING                                                  │
│  ├── Read refined_corpus.md                                         │
│  └── Write outline.json with section structure                      │
│                   ↓                                                 │
│  Phase 2: DRAFTING                                                  │
│  ├── Call mcp__local_toolkit__draft_report_parallel()               │
│  ├── Tool reads outline.json                                        │
│  ├── Generates sections in parallel (via z.ai API)                  │
│  └── Saves: 01_section.md, 02_section.md, ...                       │
│                   ↓                                                 │
│  Phase 3: ASSEMBLY                                                  │
│  ├── Call mcp__local_toolkit__compile_report(theme="modern")        │
│  ├── Tool reads sections in ORDER                                   │
│  └── Creates: work_products/report.html                             │
│                   ↓                                                 │
│  Phase 4: COMPLETION                                                │
│  └── Return success with report location                            │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 📍 Next Steps

1. **Verify in Harness:** Run the pipeline through the full harness system to confirm end-to-end success.
2. **Add Table of Contents:** Consider adding TOC generation to `compile_report.py`.
3. **Additional Themes:** Expand the theme library for different report styles.

---

## 🔗 Related Documentation

- `030_CONTEXT_EXHAUSTION_FIX_SUMMARY.md` - Two-phase sub-agent architecture
- `060_ATOMIC_RESEARCH_TASKS.md` - Research pipeline design
- `057_RESEARCH_PIPELINE_EVALUATION.md` - Pipeline evaluation
