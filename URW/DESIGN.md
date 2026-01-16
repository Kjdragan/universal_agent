# URW Design (Architecture + Behavior)

**Purpose:** Define the target architecture for the URW wrapper that orchestrates long-running tasks while delegating execution to the existing Universal Agent system.

## 1) Core Principles
- **Harness is opt-in:** default to fast path; use harness only when explicitly requested or confirmed.
- **Fresh context per phase:** new agent instance per phase/task.
- **Evidence-driven verification:** receipt/artifact/hybrid/programmatic evidence.
- **Durable state:** evidence + progress stored in DB with human-readable artifacts.
- **Guardrails:** failed approaches recorded and reinjected.

## 2) Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                 URW ORCHESTRATOR (Outer Loop)                        │
│  - Selects next phase/task                                           │
│  - Injects context                                                   │
│  - Enforces retries + verification                                   │
└──────────────────────────────────────────────────────────────────────┘
                │
                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                 AGENT ADAPTER (Bridge)                               │
│  - Spawns fresh agent instance                                       │
│  - Runs your multi-agent system                                      │
│  - Extracts artifacts, receipts, learnings, failures                 │
└──────────────────────────────────────────────────────────────────────┘
                │
                ▼
┌──────────────────────────────────────────────────────────────────────┐
│            EXISTING UNIVERSAL AGENT SYSTEM                           │
│  - Composio tool router                                               │
│  - Subagents                                                         │
│  - Regular multi-step execution                                      │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│                  STATE + EVIDENCE STORE                              │
│  - DB records tasks, evidence, receipts                              │
│  - Verification findings artifact                                    │
│  - Guardrails (failed approaches)                                    │
└──────────────────────────────────────────────────────────────────────┘
```

## 3) Phase Model
A “phase” is the smallest unit of work that should complete in a single fresh context window.
- Large requests decompose into phases with dependencies.
- Each phase has explicit evidence requirements.

## 4) Evidence Types
- **Receipt:** provider ID (e.g., Gmail message ID)
- **Artifact:** file output (PDF/JSON/report)
- **Hybrid:** requires both receipt + artifact
- **Programmatic:** deterministic check (tests/lint)

## 5) Verification Findings Artifact
Generated after verification for audit and restart guidance.

**Template fields:**
- verification_id
- task_id
- task_type
- evidence_type
- evidence_refs
- verifier_version
- verification_timestamp
- status (pass | fail | warn)
- notes

## 6) Harness Activation Policy
- Default: **no harness** (fast path)
- Explicit activation: `/harness` or CLI flags
- Optional auto-detect: prompt user to confirm harness usage
- Manual override: user can force harness on/off

## 7) Failure & Retry Policy
- **Schema errors:** synthesize corrected payload when possible
- **Retry budget:** 3 retries before escalation
- **Guardrails:** store failed approaches; reinject on restart

## 8) Interfaces (High-Level)

**Planner/Decomposer:**
- Input: user request
- Output: phase plan + mission.json

**Adapter Contract:**
- Input: context + current phase
- Output: artifacts, receipts, learnings, failed approaches, tools used

**Evaluator:**
- Input: phase + evidence
- Output: pass/fail + verification findings artifact

## 9) Integration with Existing System
- No changes to core agent logic; integration is via adapter.
- Evidence/receipts must map to the harness DB schema.
- Composio receipts are first-class evidence for delivery tasks.
