# 064: Mission Manifest & Output Artifact Verification Trace

**Date:** January 15, 2026  
**Status:** Draft (Discovery Notes)

---

## 1. Mission Manifest Creation
`mission.json` is created by the agent during harness planning or via helper scripts such as `scripts/create_mission.py`. In harness planning mode, the agent is explicitly instructed to include `output_artifacts` in each task definition.

---

## 2. Mission Updates (Interview Flow)
If the interview tool is used, answers are merged into `mission.json` while status remains `PLANNING`. After approval, status moves to `IN_PROGRESS`.

---

## 3. Verification Pipeline (Harness)
When the agent outputs a completion promise, the harness verifies **all COMPLETED tasks with output_artifacts**.

**Verifier tiers:**
- Tier 1: Binary existence (file must exist)
- Tier 2: Format validation (non-empty, valid JSON/HTML/PDF)
- Tier 3: Optional semantic LLM check (async)

If verification fails, tasks are marked `RETRY` in `mission.json`.

---

## 4. Output Artifacts Expectations
`output_artifacts` are **file-based**. This means success is currently proven by files, not ledger receipts. If a task declares `email_sent_confirmation`, that file must exist—even if Gmail already returned a message ID.

This explains the harness restart behavior observed in prior runs when an email confirmation artifact was missing.

---

## 5. Related Scripts & Files
- `scripts/create_mission.py` — simple manifest generator
- `harness/verifier.py` — TaskVerifier implementation
- `main.py` — harness verification and retry logic

---

## 6. Open Notes
- Tool receipts capture external IDs (e.g., Gmail message id), but the verifier does not yet consume them.
- A future enhancement could map `email_sent_confirmation` to ledger evidence, but this is not implemented yet.

