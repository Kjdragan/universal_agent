# Gateway Parity Success Report

**Date:** 2026‑02‑02

## Purpose
Consolidate the work performed to achieve parity between **CLI direct mode** and **Gateway/Web UI mode**, including analysis, root‑cause findings, in‑process tooling migration, guardrails, and final validation.

---

## Executive Summary
We achieved functional parity between CLI and Gateway by:
- Moving critical local subprocess MCP tools **in‑process** under the `internal` MCP server.
- Standardizing PDF conversion and Gmail upload paths.
- Fixing tool availability drift and path typo issues via guardrails.
- Re‑running gateway validation to confirm end‑to‑end success (research → report → PDF → Gmail).

Result: Gateway runs are now **fast, reliable, and consistent** with CLI outcomes.

---

## Key Phases & Outcomes

### 1) Parity Analysis
- Compared a **golden CLI run** vs a **gateway run**.
- Identified divergence in tool availability (notably `upload_to_composio`) and PDF conversion workflow.
- Confirmed gateway used the same execution engine (`ProcessTurnAdapter`) but suffered from **tool registry mismatch** and **missing local subprocess MCP tools**.

### 2) In‑Process Tooling Migration
- Consolidated critical local tools into the **in‑process `internal` MCP** to eliminate subprocess drift.
- Added internal wrappers for:
  - Research/report pipeline
  - File ops (list/append)
  - Upload to Composio
  - Image generation and preview
  - Memory tools
  - Batch tool execution
  - HTML→PDF conversion

**Benefits:**
- Higher observability (in‑process logging)
- Lower latency (no stdio overhead)
- Consistent tool availability across CLI + Gateway

### 3) PDF Conversion Strategy
- Standardized preference:
  - **HTML → PDF:** Chrome headless
  - **Markdown/other → PDF:** WeasyPrint
- Added `mcp__internal__html_to_pdf` tool (Chrome headless with WeasyPrint fallback).
- Added guardrail to block Playwright for non‑HTML conversions.

### 4) Path & Workspace Guardrails
- Observed a typo‑created workspace directory (`AGENT_RUNWORKSPACES`).
- Implemented normalization to auto‑correct common workspace root typos.
- Expanded workspace guardrails to validate `html_path`/`pdf_path`.
- Added system prompt reminder to always use `CURRENT_SESSION_WORKSPACE`.

### 5) Validation Runs
- Gateway test rerun with canonical Composio user ID.
- Confirmed:
  - Research → report → PDF → Gmail
  - No tool errors
  - No rogue workspaces created
  - Gmail send success

---

## Architecture Summary (Current)

### Execution Engine
- **Single shared engine** (`process_turn` via `ProcessTurnAdapter`) used by CLI and Gateway.

### MCP Tooling
- **Internal MCP (in‑process):** all core local tools (research/report pipeline, upload, PDF, memory).
- **Composio MCP (remote):** Gmail, search, etc.
- **External MCPs:** kept out‑of‑process only for heavy/remote systems.

### Tool Registry Visibility
- CLI and Gateway now log **in‑process tool list** for parity verification.

---

## Benefits Achieved

- **Parity:** Gateway and CLI now behave the same for critical workflows.
- **Speed:** Reduced latency from removing subprocess hops.
- **Reliability:** Tool availability stabilized; fewer “No such tool” failures.
- **Observability:** In‑process tools emit structured logs to the UI.
- **Determinism:** Guardrails prevent path drift and rogue outputs.

---

## Remaining Recommendations

1. Keep external MCPs minimal and only for tools requiring isolation or heavy dependencies.
2. Continue enforcing workspace path guardrails in new tools.
3. Consider a periodic **gateway parity regression test** using the same canonical prompt.

---

## Canonical Test Prompt
"Search for the latest news from the Russia‑Ukraine war over the last three days. Create a report about it. Save that report as a PDF and gmail it to me."

---

## Conclusion
Gateway parity has been achieved and validated. The system now runs consistently across CLI and Gateway, with improved speed, observability, and reliability due to the in‑process MCP consolidation and guardrails.

