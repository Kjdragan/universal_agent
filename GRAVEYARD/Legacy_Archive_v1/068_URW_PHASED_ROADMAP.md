# 068 URW Phased Roadmap (Harness Wrapper)

**Status:** Draft (Phase Plan)
**Date:** 2026-01-16
**Owner:** Universal Agent Team

## 1) Goal
Build a long-running harness wrapper that:
- Decomposes massive requests into phased tasks
- Executes phases with fresh context windows
- Persists evidence and progress across hours/days
- Verifies completion with task-appropriate evidence
- Keeps the existing multi-agent system as the primary executor

## 2) Design Principle: Harness is Opt-In
The harness (URW wrapper) should be a **parameterized mode**, not the default. The fast path remains the existing multi-agent system.

**Activation policy:**
- Default: non-harness execution (fast path).
- Explicit activation: `/harness` or CLI flags (`--max-iterations`, `--completion-promise`).
- Optional auto-detect: suggest harness when task is predicted to exceed a single context window (require confirmation).
- Manual override: user can always force harness on/off.

## 3) Target Architecture (End State)

**Outer Loop (URW-style Orchestrator)**
- Determines current phase/task
- Injects context + constraints into fresh agent instance
- Runs evaluation + checkpoints results

**Planner / Decomposer**
- Templates for known task classes
- LLM fallback for novel requests
- Output: mission.json + URW task graph (aligned schemas)

**State & Evidence Store**
- DB-backed evidence (artifacts + receipts)
- Verification findings artifact written per phase
- Failed-approach guardrails persisted

**Evaluator**
- Evidence taxonomy: receipt / artifact / hybrid / programmatic
- Optional semantic checks for qualitative tasks

**Agent Adapter (Bridge)**
- Runs your existing multi-agent system per phase
- Extracts artifacts, side effects, learnings, failed approaches
- Returns structured results to orchestrator

## 4) Phased Rollout (Gated)

### Phase 0 — Adapter Baseline
**Goal:** Prove URW can run one phase using your agent system.
- Implement adapter with minimal extraction (artifacts + outputs).
- Run a single, small phase.

**Gate:**
- One task completes end-to-end with artifacts recorded.

---

### Phase 1 — Evidence & Verification Pipeline
**Goal:** Ensure verification is reliable across long runs.
- Implement evidence taxonomy (receipt/artifact/hybrid/programmatic).
- Persist evidence in DB.
- Generate verification findings artifact on completion.
- Accept Gmail IDs as receipt evidence.

**Gate:**
- Receipt-based task verified without manual artifacts.

---

### Phase 2 — Decomposition into Phases
**Goal:** Generate a structured plan for massive requests.
- Templates for common requests.
- LLM fallback for novel tasks.
- Map into mission.json + URW task graph.

**Gate:**
- A large request becomes a multi-phase plan with dependencies.

---

### Phase 3 — Context Injection + Guardrails
**Goal:** Robust continuity across resets.
- Explicit context injection (current task, prior artifacts, failures).
- Guardrails logging and reinjection.

**Gate:**
- Restart continues without repeated failures or duplicated side effects.

---

### Phase 4 — Controlled Production Usage
**Goal:** Validate multi-hour tasks.
- Run 1–2 real workloads.
- Track retries, evidence pass rate, completion time.

**Gate:**
- Multi-hour tasks complete with verified evidence and stable loop behavior.

## 5) Evidence Standards (Draft)
- **Receipt:** provider ID or success receipt (e.g., Gmail message ID).
- **Artifact:** file output (PDF, JSON, report).
- **Hybrid:** requires receipt + artifact.
- **Programmatic:** deterministic check (tests/lint). 

Each task declares its evidence type with a default mapping by task class.

## 6) Verification Findings Artifact (Template)
Fields:
- verification_id
- task_id
- task_type
- evidence_type
- evidence_refs
- verifier_version
- verification_timestamp
- status (pass | fail | warn)
- notes

## 7) Risks & Mitigations
- **Dual state systems (URW + current DB):** Start with adapter-only phase and unify evidence schema early.
- **Evaluation ambiguity:** Use explicit evidence types per task; avoid LLM-only verification.
- **Overhead regression:** Keep harness opt-in with explicit activation.

## 8) Success Metrics
- % phases completed without manual intervention
- % verification pass rate by evidence type
- Average retries per task
- Time-to-completion for multi-hour tasks

## 9) Next Actions
- Implement Phase 0 adapter baseline.
- Define evidence DB schema + verification artifact format.
- Draft decomposition templates for top 3 request types.
- Run URW smoke check: `PYTHONPATH=src uv run python scripts/urw_smoke.py`
