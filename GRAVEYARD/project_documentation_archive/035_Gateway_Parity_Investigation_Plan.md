# Gateway Parity Investigation Plan (CLI vs Gateway)

## Goal
Achieve near‑parity between **CLI direct mode** and **Gateway full‑stack mode** for:
- Tool availability and routing (search → crawl → refine → report → PDF → email)
- Reliability (stay on the happy path)
- Latency (minimal extra overhead beyond gateway transport)
- Consistent session/context behavior

This plan focuses on **investigating** gaps before implementing changes.

## Baseline Reference
- **Golden CLI run**: `AGENT_RUN_WORKSPACES/session_20260201_230120`
- Successful flow: search → refine → report → PDF → upload → Gmail

We will re‑run the exact same query via **Gateway + Web UI** and compare outcomes.

## Investigation Tracks

### 1) Configuration Parity (Agent Setup / Prompts / Hooks)
**Purpose:** Ensure Gateway uses the same agent configuration and knowledge injection as CLI.

Checks:
- Compare `AgentSetup` vs Gateway initialization paths (hooks, prompt assets, skills).
- Confirm `DISALLOWED_TOOLS` parity between CLI and Gateway.
- Verify environment variables used in both modes (USER_TIMEZONE, UA flags, COMPOSIO keys).
- Ensure the same knowledge files are injected (composio.md, local_toolkit.md, gmail skill, etc.).

Artifacts:
- Configuration diff table (CLI vs Gateway) with any deviations noted.

### 2) Tool Registry Parity (MCP + Internal Tools)
**Purpose:** Ensure Gateway exposes the same tools and the same in‑process fallbacks.

Checks:
- Compare tool list at startup (CLI log vs gateway log):
  - `mcp__local_toolkit__*`
  - `mcp__internal__*` wrappers
  - Composio tool router exposure
- Verify that research pipeline tools are available and functioning in Gateway.
- Confirm upload tools: `mcp__local_toolkit__upload_to_composio` and `mcp__internal__upload_to_composio`.

Artifacts:
- Tool registry snapshot for CLI and Gateway (from logs).

### 3) Session + Workspace Lifecycle
**Purpose:** Ensure Gateway reuses the same session/workspace for follow‑ups, just like CLI.

Checks:
- Validate session creation/resume logic in Gateway and API bridge.
- Ensure Web UI follow‑ups route to the correct session without new workspace creation.
- Verify that “keep session history” behavior matches CLI.

Artifacts:
- Session lifecycle sequence diagram for Gateway path.
- Confirmed mapping: Web UI session → Gateway session → workspace dir.

### 4) Event Streaming and Latency
**Purpose:** Identify extra latency or blocking introduced by the Gateway/Web UI path.

Checks:
- Measure end‑to‑end latency per phase (search, refine, report, PDF, upload, email).
- Compare tool‑call durations CLI vs Gateway.
- Ensure event streaming does not block tool execution or deadlock on UI.

Artifacts:
- Latency table (CLI vs Gateway) by phase.
- List of events that accumulate or stall streaming.

### 5) Error Handling & Fallback Behavior
**Purpose:** Validate that Gateway handles tool errors with the same recoverability as CLI.

Checks:
- Confirm that tool_use_error events are surfaced to the agent the same way.
- Verify fallback logic activates when tools are missing (e.g., upload fallback).
- Ensure guardrails prevent SDK misuse in Gateway mode.

Artifacts:
- Error case summary (if any) with recommended guardrail improvements.

### 6) UI/Observer Side Effects
**Purpose:** Ensure UI/observer features don’t perturb runtime behavior.

Checks:
- Confirm observer writes don’t trigger extra tooling or work.
- Verify that UI‑side actions (expansion toggles, file watchers) don’t trigger extra processing.
- Confirm no duplicate query dispatch from UI (client‑side resends).

Artifacts:
- UI interaction audit for duplicate triggers.

## Execution Steps

1. **Baseline Replay (Gateway)**
   - Run the same query via Gateway/Web UI.
   - Capture logs: gateway, api, run logs, activity journal, trace.json.

2. **Config + Tool Registry Comparison**
   - Extract “Active Local MCP Tools / Internal MCP Tools” from logs.
   - Compare to CLI baseline log.

3. **Session Mapping Verification**
   - Trace session_id mapping from Web UI → Gateway → workspace.
   - Confirm follow‑up query lands in same workspace.

4. **Latency Profiling**
   - Extract tool timing for key phases.
   - Compute overhead added by gateway transport.

5. **Error/Fallback Audit**
   - Scan for tool_use_error, retries, or unexpected tool usage.
   - Verify that the happy path stays intact with no extra tool detours.

6. **Summarize Findings & Recommendations**
   - Identify gaps.
   - Propose targeted fixes only where parity breaks.

## Success Criteria
- Gateway mode completes the same query with **no extra detours** or missing tools.
- Follow‑up questions reference the same workspace/context.
- Extra latency is measurable but bounded and acceptable.
- Tool registry and hooks are functionally equivalent.

## Deliverables
- This plan (current doc).
- A follow‑up **Gateway Parity Findings Report** with:
  - Config/tool parity diff
  - Latency comparison
  - Session mapping validation
  - Recommended fixes (if any)

---

**Owner:** Universal Agent team
**Next Review:** After Gateway baseline replay + log collection
