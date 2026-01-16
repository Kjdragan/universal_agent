# 003 URW Evidence Taxonomy

Purpose: Define the evidence types used for verification and how they should be applied consistently across tasks.

## 1) Evidence Types (Standard)
1. **Receipt**
   - Provider-issued ID or acknowledgment
   - Examples: Gmail message ID, Slack timestamp
   - Source: `side_effects` in the state DB

2. **Artifact**
   - File output produced by the agent
   - Examples: PDF, JSON, report HTML/MD
   - Source: `.urw/artifacts/` + `artifacts` table

3. **Hybrid**
   - Requires **both** receipt + artifact
   - Example: “Send report via email” (report file + Gmail ID)

4. **Programmatic**
   - Deterministic checks (tests/lint)
   - Example: “tests pass” or “lint clean”

## 2) How Evidence Maps to Tasks
Each task declares its evidence type, either explicitly or by default:
- **Email delivery** → Receipt (or Hybrid if a file must be sent)
- **Report generation** → Artifact
- **Pipeline steps** → Hybrid if they deliver outputs externally

## 3) Verification Findings
For each iteration, a verification findings artifact is written:
```
.urw/verification/verify_<task_id>_<iteration>.json
```
This records:
- evidence_type
- evidence_refs
- evaluation summary
- pass/fail

## 4) Evidence Source of Truth
- **Receipts** are canonical in the DB (`side_effects` table)
- **Artifacts** are canonical on disk + `artifacts` table
- **Verification findings** are stored in DB + JSON artifacts

## 5) Avoiding Ambiguity
- Prefer explicit evidence type per task.
- Avoid LLM-only verification without a binary check.
- For external side effects, always use a receipt-based check.

## 6) Quick Reference Table
| Task Type | Evidence Type | Example Evidence |
|---|---|---|
| Email send | Receipt | Gmail message ID |
| Report generation | Artifact | report.md / report.pdf |
| Report + delivery | Hybrid | report.pdf + Gmail ID |
| Testing | Programmatic | tests pass |
