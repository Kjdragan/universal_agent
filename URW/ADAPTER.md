# URW Adapter Contract (Integration Checklist)

**Purpose:** Define the integration contract between the URW orchestrator and the existing Universal Agent system.

## 1) Adapter Responsibilities
The adapter is the **only integration point** between URW and the agent system.

It must:
- Create a **fresh agent instance** per phase/task.
- Inject the provided context into that agent.
- Execute the phase and **extract structured results**.
- Return artifacts, side effects (receipts), learnings, and failures.

## 2) Required Output Schema (AgentExecutionResult)
The adapter returns a structured result object with these fields:

- **success**: bool
- **output**: str (agent summary)
- **error**: Optional[str]
- **artifacts_produced**: List[Dict]
  - Example: `{ "path": "reports/final.pdf", "type": "file", "metadata": {...} }`
- **side_effects**: List[Dict]
  - Example: `{ "type": "email_sent", "key": "gmail:msg_id", "details": {"message_id": "..."} }`
- **learnings**: List[str]
- **failed_approaches**: List[Dict]
  - Example: `{ "approach": "Direct scrape", "why_failed": "Rate limited" }`
- **tools_invoked**: List[str]
- **context_tokens_used**: int
- **execution_time_seconds**: float

## 3) Evidence Mapping Rules
- **Receipt evidence** = `side_effects` entries with provider IDs (e.g., Gmail message ID).
- **Artifact evidence** = `artifacts_produced` file paths.
- **Hybrid evidence** = both receipt + artifact for the same task.

## 4) Idempotency Rules
- Every side effect must include a **stable idempotency key**.
- The orchestrator uses this key to avoid duplicate deliveries on retries.

## 5) Phase Context Injection (Input to Adapter)
The adapter receives a **context block** that should be injected into the agent prompt:

```
## Plan Status
- Complete: 2 | In Progress: 1 | Pending: 3

## Current Phase
Task: Research X
Success Criteria: file_exists:report.md

## Inputs
- tasks/01/research_overview.md

## Failed Approaches (DO NOT REPEAT)
- Direct scraping: rate limited
```

## 6) Checklist (Implementation)
- [ ] Create adapter class implementing the required interface
- [ ] Ensure **fresh agent instance** per phase
- [ ] Inject context block into system prompt
- [ ] Extract artifacts + side effects from agent outputs
- [ ] Return structured AgentExecutionResult
- [ ] Validate evidence stored in DB

## 7) Validation Tests
- **Test A:** single-phase task produces an artifact
- **Test B:** delivery task returns receipt evidence (Gmail ID)
- **Test C:** failed approach is captured and reinjected next phase
