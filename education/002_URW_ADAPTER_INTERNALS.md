# 002 URW Adapter Internals

Purpose: Explain how the URW adapter bridges the Universal Agent runtime with the orchestrator, and how artifacts/receipts are extracted.

## 1) Adapter Role
The adapter is the single integration point between URW and your multi-agent runtime. It must:
- Instantiate a **fresh agent** per phase
- Inject the URW context block
- Collect structured outputs
- Return `AgentExecutionResult` to URW

Reference: `src/universal_agent/urw/integration.py`

## 2) Execution Flow
1. URW builds **context** (task + dependencies + learnings + guardrails).
2. Adapter builds a **prompt** using the task + context.
3. Adapter creates a **fresh UniversalAgent**.
4. Adapter runs `agent.run_query(prompt)` and collects events.
5. Adapter extracts outputs into a structured result.
6. URW stores artifacts + receipts and verifies completion.

## 3) Event Capture (UniversalAgentAdapter)
The adapter listens for agent events:
- `TEXT` → concatenated as `output`
- `TOOL_CALL` → tracked for tools + file writes
- `TOOL_RESULT` → used to extract delivery receipts
- `WORK_PRODUCT` → treated as artifacts
- `AUTH_REQUIRED` → causes failure to allow retry after auth

## 4) Artifact Extraction Rules
Artifacts are collected from two sources:
1. **Work products** (`EventType.WORK_PRODUCT`)
   - HTML reports or content saved to `work_products/`
2. **File writes** (tool calls)
   - `Write`, `append_to_file`, etc.
   - Uses `file_path` or `path` from tool input

## 5) Receipt Extraction Rules
Receipts are extracted from tool results, based on tool names:
- Gmail send → `email_sent` receipt
- Slack send → `slack_message_sent` receipt

Receipts include:
- `type`
- `key` (idempotency key)
- `details` (e.g., message_id)

## 6) Idempotency
Each receipt uses a stable idempotency key, preventing re-sends on retries.

## 7) Failure Modes
- **Auth required** → adapter returns failure, URW can retry later.
- **Empty output** → returns placeholder text so URW can still evaluate artifacts.

## 8) Extension Points
You can add more receipt extractors (calendar invites, drive uploads, etc.) by extending `_capture_side_effects()`.

## 9) Pointers
- Adapter implementation: `src/universal_agent/urw/integration.py`
- Orchestrator: `src/universal_agent/urw/orchestrator.py`
- Evidence storage: `src/universal_agent/urw/state.py`
