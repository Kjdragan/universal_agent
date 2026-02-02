# Gateway Parity Findings Report (CLI vs Gateway)

**Date:** 2026-02-02

## Executive Summary
The Gateway path is **functionally close** to CLI for research and report generation, but diverges on the **email attachment happy path** and **PDF conversion**. The Gateway session `session_20260201_221312_2d19b1de` failed to access `mcp__local_toolkit__upload_to_composio`, which triggered a cascade of off‑path actions (Playwright install, SDK attempts, base64 attachment, tool discovery errors). This is the primary parity break. Additionally, the Gateway mode lacks explicit logging of available MCP tools, making parity verification harder. UI polling generates heavy `/api/files` traffic (not a correctness issue but adds load/latency).

## Baselines

### CLI Golden Run
- Workspace: `AGENT_RUN_WORKSPACES/session_20260201_230120`
- Outcome: Full happy path → report + PDF + Gmail attachment delivered.
- Evidence: `AGENT_RUN_WORKSPACES/session_20260201_230120/transcript.md` shows successful upload + Gmail send using `mcp__local_toolkit__upload_to_composio` and `mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL`.

### Gateway Run (Problem Case)
- Workspace: `AGENT_RUN_WORKSPACES/session_20260201_221312_2d19b1de`
- Outcome: Research + report succeeded; email attachment failed, followed by workaround summary email.
- Evidence: `AGENT_RUN_WORKSPACES/session_20260201_221312_2d19b1de/activity_journal.lSo I've reviewed the differences according to your according to this report. Why are there actual differences? I thought that the execution engine was supposed to be exactly the same, as in the same file, everything. How are there differences in the gateway with Web UI version? in the tool availability, etc. Our goal was to make it use of the same execution engine, not a copy of it, I thought. Because then you end up with these kind of slight differences, whwe were trying to avoid. Can you address this and discuss what's going on with regards to this? Don't change anything yet, but just let me know your explanation.og`.

## Findings (Non‑Parity Issues)

### 1) Missing `mcp__local_toolkit__upload_to_composio` in Gateway
**Impact:** Hard failure on attachment upload; agent went off the happy path.

Evidence:
- `AGENT_RUN_WORKSPACES/session_20260201_221312_2d19b1de/activity_journal.log:181-182`
  - Tool call → `mcp__local_toolkit__upload_to_composio`
  - Result → `No such tool available`

This tool **exists and works in CLI** (successful upload in CLI transcript). The missing tool is the primary parity break.

### 2) PDF Conversion Deviated to Playwright (System Binary)
**Impact:** Added latency, browser install, and reliance on system binaries; violates environment guidance (favor Python‑native tools).

Evidence:
- `AGENT_RUN_WORKSPACES/session_20260201_221312_2d19b1de/activity_journal.log:118-175`
  - Uses Playwright for HTML→PDF
  - Fails due to missing browser; installs Chromium/ffmpeg

CLI run used WeasyPrint (Python‑native) instead. This is an efficiency and reliability parity gap.

### 3) Tool Discovery Attempted with Disallowed Tool
**Impact:** Extra errors and time; indicates guardrail doesn’t steer to the correct fallback.

Evidence:
- `AGENT_RUN_WORKSPACES/session_20260201_221312_2d19b1de/activity_journal.log:190-191`
  - `mcp__composio__COMPOSIO_SEARCH_TOOLS` → `No such tool available`

The tool is disallowed for primary agent by design. When upload fails, the agent should be routed to a deterministic fallback instead of searching.

### 4) Invalid Tool Attempt: `FILE_STORE_UPLOAD_FILE`
**Impact:** More detours; indicates confusion about correct upload workflow.

Evidence:
- `AGENT_RUN_WORKSPACES/session_20260201_221312_2d19b1de/activity_journal.log:187-188`
  - `FILE_STORE_UPLOAD_FILE` not found

### 5) Missing Tool Registry Visibility in Gateway Logs
**Impact:** Harder to confirm tool parity at startup; slows debugging.

Evidence:
- `gateway.log` shows active apps + skills but **no explicit “Active Local MCP Tools” list** (present in CLI startup output).

### 6) Heavy UI Polling Overhead
**Impact:** Potential latency and load in Gateway mode; not directly a correctness issue.

Evidence:
- `api.log` contains repeated `GET /api/files` calls for the same session (hundreds of lines).

### 7) WebSocket Disconnects During Gateway Run
**Impact:** Potential risk to session continuity / duplicated inputs.

Evidence:
- `gateway.log` shows two disconnect events for the same session.

## What Stayed in Parity
- Research phase succeeded via `mcp__internal__run_research_phase` in Gateway.
- Report generation succeeded via `mcp__internal__run_report_generation` in Gateway.
- Search results were captured to `search_results/` as expected.

## Gaps in Evidence
- Sessions `session_20260201_215150_064eeac6` and `session_20260201_212852_9f4d8c25` are not present on disk. Prior observations about context loss and duplicated queries could not be validated from local files.
- Gateway does not log its full MCP tool registry; parity confirmation must currently be inferred from failures.

---

## Overall Assessment
**Gateway mode is close to parity, but brittle around email attachment and PDF conversion.** Fixing upload tool availability and enforcing a deterministic PDF conversion path should restore the happy path consistency with CLI.

