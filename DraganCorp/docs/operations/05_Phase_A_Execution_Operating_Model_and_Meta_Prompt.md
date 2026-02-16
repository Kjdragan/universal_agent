# 05. Phase A Execution Operating Model and Meta Prompt

This document explains **how to run development the way we just did**: structured, evidence-driven, and continuously documented.

It is written for non-specialists and AI coding agents.

---

## 1) What this is

This is a practical operating system for Phase A work.

It gives you:

1. A repeatable development approach.
2. A detailed step-by-step execution loop.
3. Verification and rollback discipline.
4. Documentation sync rules (so docs stay true as code changes).
5. A copy/paste **meta prompt** you can use with future AI coders.

---

## 2) Canonical document stack (what to keep aligned)

Use these as the required source of truth:

1. `00_DraganCorp_Program_Control_Center.md`
   - Program status, decisions, lessons, and governance.
2. `02_Phase_A_Persistent_CODER_VP_Implementation_Plan.md`
   - Detailed implementation blueprint.
3. `03_Phase_A_CODER_VP_Observability_Playbook.md`
   - Ops queries, interpretation, and recovery steps.
4. `04_Phase_A_Controlled_Rollout_Evidence_Log.md`
   - Every rollout window + objective evidence + recommendation.

This file (`05_...`) defines **how to execute and keep all four in sync**.

---

## 3) Core approach (plain-English)

### A) Build in small slices
Do one logical change at a time (not giant mixed edits).

### B) Verify immediately
After each slice, run targeted checks/tests before moving on.

### C) Capture evidence, not opinions
When making rollout decisions, use measured data (`fallback`, `p95`, event counts), not intuition.

### D) Keep docs live
Each meaningful change updates:
- status log,
- decision log (if governance/strategy changed),
- lessons learned,
- rollout evidence rows.

### E) Use guardrails for promotion
Never broaden rollout without explicit rollback triggers.

---

## 4) Detailed execution loop

Use this loop for every development session.

### Step 1: Intake + scope lock
- Restate the concrete objective.
- Define what is in scope and out of scope.
- Identify which canonical docs must change if successful.

### Step 2: Plan in work packets
Create 2-6 small packets, each with:
- change goal,
- files touched,
- verification command,
- expected evidence output.

### Step 3: Implement packet
- Make minimal code/doc edits for that packet only.
- Avoid unrelated refactors.

### Step 4: Run verification ladder
Run in this order:
1. **Unit/local check** for changed behavior.
2. **Integration/probe** if runtime behavior changed.
3. **Operational snapshot** if rollout/metrics changed.

If any step fails:
- stop,
- diagnose root cause,
- fix,
- re-run ladder.

### Step 5: Evidence capture
For rollout-impacting work, record:
- timestamp,
- fallback metrics,
- latency metrics,
- event profile,
- decision taken,
- notes on anomalies + recovery actions.

### Step 6: Documentation sync
Update docs in this order:
1. `04` evidence row(s),
2. `00` live status log,
3. `00` decision log (if policy/strategy changed),
4. `00` lessons learned.

### Step 7: Commit discipline
Use clean, scoped commits:
- one commit per logical change bundle,
- avoid mixing unrelated files.

---

## 5) Verification protocol (enforcement gates)

A packet is not “done” until all gates pass.

### Gate G1: Functional gate
The intended behavior is observed.

### Gate G2: Regression gate
Relevant tests pass (or a justified exception is recorded).

### Gate G3: Observability gate
Metrics/logs provide enough signal to evaluate safety.

### Gate G4: Documentation gate
Canonical docs updated to match reality.

### Gate G5: Rollout safety gate (when applicable)
Explicit rollback conditions are documented and monitored.

---

## 6) Dynamic documentation adherence rules

Use this trigger matrix:

| If you change... | You must update... |
|---|---|
| Runtime behavior, routing, cancellation, recovery | `00` status log + `00` decision log (if governance changed) |
| Rollout windows or traffic policy | `04` evidence log + `00` status log |
| New operational query/process | `03` observability playbook |
| Strategic acceptance or phase direction | `00` decision log + checklist status |

### Rule of thumb
If a future operator could be misled without a doc update, the update is mandatory.

---

## 7) Rollout decision model (Phase A)

### Promote carefully
Use objective criteria over rolling windows:
- low fallback rate,
- no sustained failure pattern,
- stable latency,
- no continuity regressions.

### Broaden with guardrails
When broadening traffic, always include:
1. rollback thresholds,
2. observation cadence,
3. owner + response action.

### Example rollback trigger
- Fallback rate > 10% over rolling 20 missions, or
- sustained `vp.mission.failed` pattern.

---

## 8) Meta prompt (copy/paste for AI coder)

Use this as your default instruction block.

```text
You are the implementation operator for DraganCorp Phase A.

Objective:
- [INSERT CURRENT OBJECTIVE]

Constraints:
- Work in small, logically scoped packets.
- Do not mix unrelated refactors.
- Prefer root-cause fixes over band-aids.

Required process:
1) Read and honor canonical docs:
   - 00_DraganCorp_Program_Control_Center.md
   - 02_Phase_A_Persistent_CODER_VP_Implementation_Plan.md
   - 03_Phase_A_CODER_VP_Observability_Playbook.md
   - 04_Phase_A_Controlled_Rollout_Evidence_Log.md
2) Propose packetized plan (2-6 packets).
3) For each packet:
   - implement,
   - run targeted verification,
   - report pass/fail with concrete evidence.
4) If rollout-impacting, capture objective metrics and append evidence row(s).
5) Keep documentation synchronized:
   - update 04 evidence,
   - update 00 status,
   - update 00 decisions if governance changed,
   - update 00 lessons.
6) Use clean, scoped commits only.

Enforcement gates (must pass before marking complete):
- G1 Functional behavior confirmed
- G2 Regression checks pass
- G3 Observability signal captured
- G4 Documentation synchronized
- G5 Rollout safety/rollback conditions documented

Output format each cycle:
- What changed
- Verification commands + outcomes
- Evidence captured
- Docs updated
- Risks/next guardrail checks
```

---

## 9) Short meta prompt (fast mode)

```text
Follow the DraganCorp Phase A operating model: packetized changes, immediate verification, evidence-first rollout decisions, and mandatory doc sync (00/03/04). No change is complete without tests/probes + updated evidence/status/decision logs when applicable.
```

---

## 10) Session close checklist (operator)

Before ending a session, verify:

- [ ] Code behavior validated.
- [ ] Relevant tests/probes run.
- [ ] Rollout evidence captured (if applicable).
- [ ] `04` updated with new window(s) and decision notes.
- [ ] `00` updated (status/decision/lessons as needed).
- [ ] Commits are scoped and understandable.
- [ ] Open risks and next actions are explicit.

---

## 11) Why this works

This approach prevents three common failures:

1. **Drift** (code and docs say different things).
2. **Silent regressions** (changes made without verification).
3. **Unsafe rollout acceleration** (promotion without evidence/guardrails).

It creates a stable loop: **Plan -> Implement -> Verify -> Document -> Decide**.
