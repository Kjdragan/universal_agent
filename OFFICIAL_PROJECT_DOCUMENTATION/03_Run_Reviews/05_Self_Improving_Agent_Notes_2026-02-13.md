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

## Incident: Grok X Trends Returned "No Posts" (Parser Bug)

### What Happened
- A run invoked the `grok-x-trends` skill and the script printed:
  - `Themes: 0`
  - `Posts: 0`
- This led the agent to conclude “X is returning empty results” and pivot away from X, even though the upstream API call succeeded and contained usable post URLs.

### Root Cause Pattern
- Silent parse failure: the script extracted the model output text but failed to parse the JSON due to incorrectly escaped regex patterns (`[\\s\\S]` instead of `[\s\S]`, `\\d` instead of `\d`).
- The raw model output actually contained valid JSON with themes and post URLs, but the script discarded it and surfaced an empty result.

### Fix Implemented
- Corrected the regex patterns in `.claude/skills/grok-x-trends/scripts/lib/xai_x_search.py` so valid JSON is parsed into structured `themes`/`posts`.
- Removed a noisy `datetime.utcnow()` deprecation warning in `.claude/skills/grok-x-trends/scripts/grok_x_trends.py` by using timezone-aware UTC time.

### Generalizable Lessons
- Any “wrapper script” around an LLM/tool should treat parse failures as first-class errors, not as “empty results”.
- When a parser returns empty, preserve and surface a bounded `raw_text` preview so operators can tell the difference between:
  - “tool returned nothing”
  - “tool returned something but our parser dropped it”
- Add a minimal regression test for parsers using a recorded fixture response (no live API dependency) when feasible.

## Incident: Code Interpreter Discovery Misrouted to Databricks

### What Happened
- A run tried to locate “Code Interpreter” capabilities via `mcp__composio__COMPOSIO_SEARCH_TOOLS`.
- The discovery results emphasized Databricks job submission, which is unrelated to our “run Python for charts” intent.

### Root Cause Pattern
- Tool discovery is inherently fuzzy: the query matched “execute python” and the router suggested an enterprise compute platform.
- Our agent prompts/docs also mixed “local Python”, “CodeInterpreter”, and “tool discovery” as interchangeable, which increased the chance of misrouting.

### Fix Implemented
- Updated `data-analyst` to be **local-first** and treat CodeInterpreter as fallback/isolation.
- Updated orchestration prompts to stop implying CodeInterpreter is the default compute lane.
- Updated references to the correct CodeInterpreter slugs (e.g. `CODEINTERPRETER_EXECUTE_CODE`).
- Added a live smoke test harness: `scripts/experiments/codeinterpreter_smoke_test.py`.

### Generalizable Lessons
- Avoid using tool discovery for capabilities we already know exist (especially for compute).
- Treat analytics as a “local default” unless isolation/persistence is explicitly needed.
