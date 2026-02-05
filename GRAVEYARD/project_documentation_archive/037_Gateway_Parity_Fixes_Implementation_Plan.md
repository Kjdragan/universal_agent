# Gateway Parity Fixes & Implementation Plan

**Scope:** Proposed fixes for CLI ↔ Gateway parity gaps identified in `036_Gateway_Parity_Findings_Report.md`. **No code changes in this document.**

## Fixes Overview (Proposed)

### A0) In‑Process Tool Consolidation (Local Subprocess MCPs → In‑Process)
**Goal:** Reduce tool ambiguity, improve observability, and remove stdio overhead for stable local tools.

**Scope (local, non‑remote only):**
- Move stable local tools from the `local_toolkit` subprocess into the in‑process `internal` MCP server.
- Keep **remote** MCPs (Composio HTTP, external MCPs, browser/video) as out‑of‑process.

**Candidates to move in‑process (shortlist):**
- `mcp__local_toolkit__upload_to_composio` (already bridged in‑process)
- `finalize_research`, `crawl_parallel` (already mirrored via wrappers)
- `list_directory`, `append_to_file` (if needed for UI/workflow)

**De‑duplication policy:**
- If a tool is available in `internal`, remove or disable the subprocess version to avoid ambiguity.
- Maintain a single authoritative tool registry list used by both CLI and Gateway.

**Why:** Keeps the happy path deterministic and improves logging/traceability.

---

### A) Ensure `upload_to_composio` Always Available in Gateway
**Problem:** `mcp__local_toolkit__upload_to_composio` missing in Gateway run.

**Fix Options:**
1. **Hard requirement check** at session init: verify `mcp__local_toolkit__upload_to_composio` is registered; if not, fail fast with a clear error in the UI.
2. **In‑process fallback** tool `mcp__internal__upload_to_composio` wired into the internal MCP server; use it when local_toolkit is missing.
3. **Startup tool registry logging** to ensure visibility.

**Why:** Prevents Gmail attachment workflow from falling off the happy path.

---

### B) Deterministic HTML→PDF Conversion Path
**Problem:** Gateway run used Playwright + browser install (slow, brittle).

**Fix Options:**
1. Add a single internal tool: `mcp__internal__html_to_pdf` using WeasyPrint (Python‑native).
2. Ensure the agent **reads the PDF skill** (`.claude/skills/pdf/SKILL.md`) before PDF generation to get universal guidance, then apply the stated preferences:
   - **HTML → PDF:** Chrome headless
   - **Markdown/other → PDF:** WeasyPrint
3. Update PDF skill / environment rules to reflect the above stated preferences.
4. Guardrail: block Playwright/Chrome for non‑HTML conversion unless explicitly requested.

**Why:** Reduces latency and removes dependency on external system binaries.

---

### C) Guardrails for Gmail Upload Workflow
**Problem:** When upload tool missing, agent tried SDK, `FILE_STORE_UPLOAD_FILE`, and `COMPOSIO_SEARCH_TOOLS`.

**Fix Options:**
1. Hook: if `mcp__local_toolkit__upload_to_composio` fails, **auto‑suggest** fallback tool and block unrelated tool discovery paths.
2. Update composio knowledge block: explicit fallback flow with `mcp__internal__upload_to_composio`.
3. Add a short “email attachment checklist” in the system prompt or hooks.

**Why:** Keeps the agent on a deterministic happy path in Gateway.

---

### D) Tool Registry Parity Logging
**Problem:** Gateway startup doesn’t show local MCP tool list like CLI does.

**Fix Options:**
1. Log all MCP tool names when the Gateway adapter initializes.
2. Emit a `SESSION_INFO` event with tool registry snapshot for the UI.

**Why:** Improves debugging and parity validation.

---

### E) UI File Polling Throttle
**Problem:** UI repeatedly fetches `/api/files`, creating load and noise.

**Fix Options:**
1. Introduce a debounce (e.g., 2–5s) for file tree polls.
2. Cache + delta update (only fetch when run emits `WORK_PRODUCT` or `FILE` events).

**Why:** Reduces latency and backend load.

---

### F) WebSocket Disconnect Handling
**Problem:** Gateway shows double disconnects; may risk duplicate queries or session resets.

**Fix Options:**
1. Add explicit “query in flight” guard in API server.
2. Require client‑side acknowledgement before resending input after reconnect.

**Why:** Prevents duplicate execution and improves session continuity.

---

## Implementation Plan (Phased)

### Phase 1 — Parity Critical (Happy Path)
1. Consolidate stable local tools into the in‑process `internal` MCP.
2. Add gateway‑side tool registry logging at session init.
3. Ensure `upload_to_composio` availability (fallback or hard requirement).
4. Introduce deterministic HTML→PDF conversion tool (WeasyPrint path).

**Exit Criteria:** Gateway can complete the “golden run” end‑to‑end with the same workflow as CLI.

### Phase 2 — Guardrails & UX Reliability
1. Hook‑level fallback guidance for upload failures.
2. Updated composio + gmail knowledge blocks to reflect fallback.
3. Block Playwright for HTML→PDF unless explicitly requested.

**Exit Criteria:** Gateway run no longer detours to SDK or browser installs.

### Phase 3 — Performance & UI
1. UI file polling debounce/delta strategy.
2. WebSocket reconnect / duplicate query controls.

**Exit Criteria:** Reduced backend noise and consistent session behavior.

---

## Verification Plan
- Re‑run the same “golden run” query via Gateway and CLI.
- Confirm:
  - `upload_to_composio` succeeds
  - PDF generation uses Python‑native conversion (no Playwright install)
  - Gmail send uses `attachment.s3key`
  - No `COMPOSIO_SEARCH_TOOLS` invocation by primary agent
  - No duplicate query execution

## Deliverables
- Gateway parity fix PR (after approval).
- Updated documentation of stable “happy path” workflow.
