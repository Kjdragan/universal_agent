# Context Management Architecture

**Status**: IMPLEMENTED  
**Date**: 2026-01-06

---

## Principle

> Keep context trim but operable. Past memory becomes files, not carried context.

---

## Two-Part Context System

| Part | Contents | Size | Purpose |
|------|----------|------|---------|
| **Core** | Mission + current task | ~2K chars | Self-sufficient operation |
| **Index** | File list (escape hatch) | ~500 chars | Pull context if needed |

### Core Context (Always Loaded)

```
MISSION: Create 100-page AI regulation report, EU+US focus, email when done.
PHASE: SYNTHESIS
TASK: Generate the report using research files.
WORKSPACE: /session_20260106_162934/
```

Agent should be able to complete its task with ONLY this.

### Index (Escape Hatch)

```
FILES AVAILABLE:
- tasks/global_ai_reg/research_overview.md (summary of all research)
- tasks/global_ai_reg/filtered_corpus/*.md (7 source files)
- work_products/chapter_1.html (if continuing)

Read these ONLY if you need more context.
```

Agent pulls from files only when it determines information is missing.

---

## Phase Transitions

When a phase completes:

1. **Outputs become files** (research → `filtered_corpus/`, writing → `chapter_N.html`)
2. **Context resets** (new agent with fresh context)
3. **Core reloaded** (mission + new phase task)
4. **Prior work accessible via files** (not in memory)

| Phase | What Agent Carries | What's in Files |
|-------|-------------------|-----------------|
| Research | "Find sources on AI regulation" | - |
| Synthesis | "Write report using research files" | `filtered_corpus/*.md` |
| Delivery | "Email the completed report" | `work_products/report.html` |

---

## Error Recovery

On context exhaustion (3 empty Write failures):

1. Find last phase boundary
2. Reset to fresh agent with:
   - Core context (mission + simplified phase summary)
   - Index (list of available files)
3. Agent reads files on-demand instead of all-at-once

**Same mechanism as normal phase transition** - just triggered by error instead of completion.

---

## Implementation

| File | Purpose |
|------|---------|
| `mission.json` | Mission + status + current phase |
| `mission_progress.txt` | Compact summary of work done (~500 chars) |
| Workspace files | Outputs from completed phases |

No new complex systems needed. Just disciplined use of existing files.
