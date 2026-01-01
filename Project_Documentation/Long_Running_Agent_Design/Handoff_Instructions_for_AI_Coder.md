# Handoff Instructions for AI Coder (Durable Jobs v1)

## Read order
1) **Durable Jobs v1 — Technical Design Spec**
2) **Phase 0–2 Ticket Pack**

## How to execute
- Implement **Phase 0** in one PR (IDs + budgets).
- Implement **Phase 1** in one PR (runtime DB + tool ledger + idempotency + wrapping tool execution).
- Implement **Phase 2** in one PR (run/step state machine + checkpoints + CLI resume UX).

## Required demo (must pass)
1) Start CLI demo job that crawls sources, generates report, uploads PDF, and emails it.
2) Force-kill the process mid-run.
3) Resume.
4) Verify via runtime DB ledger:
   - upload_to_composio executed once
   - GMAIL_SEND_EMAIL executed once
   - final artifacts produced once

## Tool side effects to treat as idempotent immediately
From `src/mcp_server.py`:
- External-ish: workbench_upload, upload_to_composio
- Memory: core_memory_replace, core_memory_append, archival_memory_insert
- Local: write_local_file, compress_files, finalize_research, generate_image, preview_image

From Composio/tool router examples:
- GMAIL_SEND_EMAIL (and any SEND/CREATE/UPDATE/DELETE/UPLOAD actions)

## Where to integrate
- The wrapping point is where `ToolUseBlock` is handled and actual tool execution happens (CLI path: `run_conversation()` / `_run_conversation()` per your repo mapping).
- Keep `trace.json` as telemetry; the new runtime DB ledger is for correctness and resumption.

## Tests
- Add unit tests for:
  - idempotency key stability
  - dedupe behavior (second call returns stored receipt)
- Add an integration-style test or demo script for kill/resume.

