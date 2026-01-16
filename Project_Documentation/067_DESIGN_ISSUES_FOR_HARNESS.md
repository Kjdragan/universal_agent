# 067 Design Issues for Harness (Living Document)

Purpose: Capture and iterate on open design questions, decisions, and implementation targets for improving harness verification, evidence handling, and reliability loops.

Last updated: 2026-01-15

## 1) Current Decisions (Agreed)
- **Email verification evidence**: A Gmail message ID from a successful send is **sufficient evidence** for delivery tasks. This should be accepted as verification without requiring a separate manual file artifact.
- **Real-time task status update**: When a task is known to be completed (e.g., Gmail ID received), the task should be marked completed immediately in the tracking system.
- **Evidence categories**: Delivery tasks should allow receipt-based evidence (ledger receipts / provider IDs) as first-class verification.
- **Malformed tool calls**: If schema errors are known, the harness should synthesize a corrected payload and retry up to **3 times** before escalation.
- **Evidence storage**: Evidence should be tracked in the database alongside other harness durability records.
- **Verification findings artifact**: When verification completes, generate an artifact summarizing the evidence used and results.

## 2) Proposed Verification Evidence Standards (Draft)
Define a small, predictable set of evidence types that tasks can declare:
1) **Receipt** – A provider-issued ID or success receipt (e.g., Gmail message ID, Slack message timestamp).
2) **Artifact** – A file or object saved in the workspace or storage (e.g., PDF, JSON output).
3) **Hybrid** – Requires both Receipt and Artifact (e.g., artifact generated and delivered externally).
4) **Programmatic Check** – A deterministic check (tests, lint) producing a pass/fail signal.

Notes:
- Evidence types should be declared per task in mission metadata.
- Defaults should exist by task class (e.g., “email” defaults to Receipt).

## 3) Auto-Generated Confirmation Records (Draft)
If a task is confirmed by receipts, auto-generate a lightweight confirmation record (if desired for auditability). This should be automatic and immediate after receipt is captured. Example fields:
- task_id
- tool_name
- provider_id (e.g., Gmail message ID)
- timestamp
- evidence_type: "receipt"

This keeps file-based audits possible without forcing human intervention.

## 4) Verification Findings Artifact (Template)
Purpose: Provide a single, predictable artifact summarizing the evidence used and the verification outcome.

Suggested fields:
- verification_id
- task_id
- task_type
- evidence_type (receipt | artifact | hybrid | programmatic)
- evidence_refs (IDs, filenames, or URIs)
- verifier_version
- verification_timestamp
- status (pass | fail | warn)
- notes

## 5) Harness Evidence Storage / Tracking (Draft)
Open questions to resolve:
- Where should receipt-based evidence be stored for verification? (ledger, mission manifest, or both)
- Should evidence be attached directly to `mission.json` task entries or referenced by ID?
- Do we require a canonical confirmation artifact name or allow a flexible evidence map?

## 6) Malformed Tool Call Remediation (Suggestions)
Goal: If a malformed tool call occurs, the agent should recover quickly with clear guidance and a retry loop.

Recommended behavior:
1) **Capture & classify** the failure as a schema/guardrail error (not task failure).
2) **Inject corrective guidance** into the retry prompt (e.g., inline schema snippet or example payload).
3) **Retry loop discipline**:
   - retry with corrected schema
   - avoid repeating the malformed call
   - track retries to avoid infinite loops
4) **Evidence logging**: record the malformed call (with redaction) and the correction guidance used.

Open questions:
- Should the harness auto-generate a canonical evidence artifact name or allow a flexible evidence map?

## 7) Open Questions / Next Decisions
- Confirm evidence taxonomy and defaults per task type.
- Decide canonical storage for receipt-based evidence.
- Decide whether the harness should require “receipt + artifact” for certain delivery tasks.
- Specify a retry policy and budget for malformed tool call errors.

## 8) Change Log
- 2026-01-16: Added verification findings artifact template and renumbered sections.
- 2026-01-15: Initial creation. Added decisions on Gmail ID as sufficient evidence, receipt-based verification, auto-update of task status, and draft evidence standards.
