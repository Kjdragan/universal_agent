# Golden Run Reference

This directory is the **single source of truth** for understanding what a successful pipeline execution looks like. Use these documents to diagnose failures, validate regressions, and onboard new developers.

## Contents

| Document | Purpose |
|----------|---------|
| [README.md](README.md) | This index |
| [golden_run_anatomy.md](golden_run_anatomy.md) | What a golden run looks like — tool sequence, timing, workspace structure, validation checklist |
| [lessons_learned.md](lessons_learned.md) | Historical failures, root causes, and permanent fixes |
| [regression_control.md](regression_control.md) | Invariants, test suite, and recovery procedures |
| [reference_sessions/](reference_sessions/) | Captured run logs from known-good sessions |

## Quick Reference

**Golden prompt:**
```
Search for the latest information from the Russia-Ukraine war over the past five days.
Create a report, save the report as a PDF, and email it to me.
```

**Expected first tool call:** `Task(subagent_type='research-specialist', ...)`

**Expected tool count:** 9-11 calls

**Expected total time:** 200-600s

**If the pipeline breaks, start here:** [lessons_learned.md](lessons_learned.md)
