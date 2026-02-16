# 07. Phase A Sustained Default-On Operating Model and Meta Prompt

This document explains how to run CODIE in **sustained default-on mode** after guarded rollout gates have passed.

It is written for operators and AI coding agents.

---

## 1) What this mode is

This is the operating model for the period **after promotion**.

The goal shifts from proving readiness to maintaining reliable day-to-day operation with low monitoring overhead.

In this mode:

1. CODIE is the default lane for eligible coding intents.
2. Monitoring is lightweight but continuous.
3. Rollback triggers remain explicit and ready.
4. Evidence capture continues on a fixed cadence.

---

## 2) Canonical docs to keep synchronized

1. `00_DraganCorp_Program_Control_Center.md`
   - live status, decisions, lessons.
2. `03_Phase_A_CODER_VP_Observability_Playbook.md`
   - query workflow and sustained cadence.
3. `04_Phase_A_Controlled_Rollout_Evidence_Log.md`
   - sustained cycle evidence rows.
4. `05_Phase_A_Execution_Operating_Model_and_Meta_Prompt.md`
   - packetized implementation discipline.

---

## 3) Core sustained-monitoring principles

### A) Signal over volume
Use enough data to detect degradation, not maximum data every cycle.

### B) Keep rollback hot
Default-on does not remove the fallback path.

### C) Track trend quality, not single spikes
Look at rolling fallback/failure behavior first, latency second.

### D) Keep docs live
Every operationally meaningful cycle should leave evidence.

---

## 4) Monitoring cadence model (low cost)

### Active implementation windows
- Capture every 30-60 minutes.

### Normal steady state
- Capture 2-4 times per day.

### After incidents/config changes
- Capture immediately,
- then capture once again within 30 minutes.

---

## 5) Lightweight command profile

Use this as the default sustained command:

```bash
PYTHONPATH=src uv run python scripts/coder_vp_rollout_capture.py \
  --mode http \
  --gateway-url "http://127.0.0.1:8002" \
  --assessment-profile sustained \
  --mission-limit 60 \
  --event-limit 180 \
  --window-label "Sustained default-on monitor cycle" \
  --scope "vp.coder.primary sustained default-on"
```

Why this profile:

- lower query volume than rollout windows,
- still enough to detect fallback/failure trend changes,
- reusable for future VP lanes.

---

## 6) Decision model for sustained mode

### `SUSTAINED_DEFAULT_ON_HEALTHY`
- Keep default-on.
- Continue standard cadence.

### `SUSTAINED_WATCH`
- Increase cadence temporarily.
- Inspect recent fallback payloads.
- Check intent-routing share trend.

### `SUSTAINED_FORCE_FALLBACK`
- Set `UA_CODER_VP_FORCE_FALLBACK=1`.
- Validate primary path health.
- Capture incident evidence + decision note before re-enabling.

---

## 7) Sustained-mode enforcement gates

A sustained monitoring cycle is complete only when:

1. **S1 Signal gate**: fallback + latency snapshot captured.
2. **S2 Trend gate**: no sustained `vp.mission.failed` pattern.
3. **S3 Recovery gate**: rollback switch path remains available.
4. **S4 Documentation gate**: evidence + status/decision docs updated.

---

## 8) Meta prompt (copy/paste for AI coder)

```text
You are the Phase A sustained-operations operator for CODIE (CODER VP lane).

Objective:
- [INSERT CURRENT OBJECTIVE]

Constraints:
- Keep CODIE default-on unless sustained guardrails are breached.
- Use low-cost monitoring profile by default.
- Preserve explicit fallback readiness.

Required process:
1) Read canonical docs:
   - 00_DraganCorp_Program_Control_Center.md
   - 03_Phase_A_CODER_VP_Observability_Playbook.md
   - 04_Phase_A_Controlled_Rollout_Evidence_Log.md
   - 07_Phase_A_Sustained_Default_On_Operating_Model_and_Meta_Prompt.md
2) Run sustained snapshot capture with lightweight limits unless incident conditions require deeper pulls.
3) Classify cycle outcome:
   - SUSTAINED_DEFAULT_ON_HEALTHY
   - SUSTAINED_WATCH
   - SUSTAINED_FORCE_FALLBACK
4) If watch/critical thresholds are hit:
   - increase cadence,
   - inspect fallback/failure payloads,
   - trigger fallback path when needed.
5) Update docs after each meaningful cycle:
   - evidence row,
   - control-center status,
   - decision/lesson entries when policy or interpretation changes.

Must-pass gates:
- S1 signal captured
- S2 trend quality confirmed
- S3 rollback readiness preserved
- S4 documentation synchronized

Output format each cycle:
- Snapshot outcome
- Decision taken
- Evidence row text
- Docs updated
- Risk + next scheduled check
```

---

## 9) Short meta prompt (fast mode)

```text
Run CODIE in sustained default-on mode with low-cost monitoring, explicit rollback readiness, and mandatory evidence/status doc synchronization after each meaningful cycle.
```

---

## 10) Session close checklist

- [ ] Sustained snapshot captured.
- [ ] Decision state recorded (`HEALTHY` / `WATCH` / `FORCE_FALLBACK`).
- [ ] Rollback path validated or reaffirmed.
- [ ] Evidence log updated.
- [ ] Program control center updated.
- [ ] Risks and next check time documented.

---

## 11) Why this model is reusable

This design can be reused for future VP lanes because it separates:

1. **Common mechanics** (snapshot -> classify -> document -> decide),
2. **Lane-specific parameters** (`vp_id`, thresholds, cadence),
3. **Operator actions** (watch vs fallback).

That gives a modular monitoring template for broader DraganCorp rollout phases.
