# Self-Improving Agent Notes (Living Doc)

Date started: 2026-02-13

Purpose: capture recurring failure modes, recovery patterns, and system-level guardrails so we can progressively build a "self-improving" (eventually self-learning) agent and skill ecosystem. This is intended to be updated after notable runs.

## Incident: Runaway Image "Copy" Loop

### What Happened
- A session entered a runaway loop repeatedly calling image generation to "copy/duplicate" an already-generated infographic using prompts like "no changes" / "output exactly as shown".
- Each call created a new timestamped artifact under `artifacts/media/`, producing dozens of near-duplicates and burning tool/API budget.

### Root Cause Pattern
- A "copy/preserve" intent was incorrectly implemented as "re-generate via model".
- A downstream requirement (image manifest needing `width_px`/`height_px`) drove repeated attempts to use a vision model to infer metadata that is better read locally from the file.
- Missing convergence logic: the agent kept retrying the same strategy instead of switching methods.

### Fixes Implemented
- Tool-level guardrail: `mcp__internal__generate_image` now treats "copy/no changes/duplicate" as a filesystem copy when `input_image_path` is provided (no model call).
- Prompt-level guidance: "degraded output" is explicitly **last resort**, and an anti-runaway rule prohibits spamming near-identical tool calls.
- System guardrails:
  - Session policy defaults are now finite (tool/runtime) to reduce unbounded runs.
  - A circuit breaker detects non-converging repeated tool calls and aborts.
  - Execution engine resets the underlying Claude subprocess when a guardrail triggers to avoid orphans.

### Generalizable Lessons
- Prefer deterministic local operations for deterministic tasks (copying files, reading dimensions, hashing).
- Encode "method switching" rules: retry once with a real change, then switch approaches.
- Add circuit breakers based on non-convergence, not just budgets.

## Self-Improvement Patterns To Implement Next

### 1. Post-Run Retrospective Capture (Automatable)
- Inputs:
  - `run.log` tail
  - `trace.json` / `trace_catalog.md`
  - tool call counts + per-tool repetition
- Outputs:
  - A short "run review" entry appended here (what happened, why, how prevented next time).
  - Optional: a "suggested guardrail" ticket (future: GitHub issue).

### 2. Non-Convergence Heuristics (More Robust Than Budgets)
- Repeated tool calls with stable semantic signature (tool-specific normalization).
- Repeated "copy/no changes" prompts for generative tools.
- Repeated identical errors without input changes.
- Time-based stalling: no new work products for N minutes while tool calls continue.

### 3. Heartbeat-Driven Proactive Maintenance (Future)
- Periodic scan of recent sessions for runaway patterns.
- If detected:
  - create a note in this doc
  - propose a small patch (guardrail, prompt tweak, tool improvement)
  - optionally disable a problematic integration via ops config until fixed

## Operating Principle
Default behavior is to push for full success with creative recovery. "Partial output" is a deliberate last resort after recovery attempts, not an excuse to stop early.

