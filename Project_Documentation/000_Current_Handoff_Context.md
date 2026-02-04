# 000 — Current Handoff Context (Comprehensive)

**Date:** 2026-02-04
**Project:** Universal Agent
**Primary Repo:** `/home/kjdragan/lrepos/universal_agent`
**Reference Repo:** `/home/kjdragan/lrepos/clawdbot`

---

## 1) Purpose of this document
This is the authoritative handoff for a new agent to take over the work. It captures:
- What has been implemented and why.
- What remains to be done.
- Where the reference materials live.
- How to verify current work (including Web UI checks).
- How we are using Clawdbot for parity without copying blindly.

If you only read one file, read this one.

---

## 2) Executive Summary (where we are)
We implemented a Phase‑3 Ops/Control Plane slice to close parity gaps with Clawdbot. That work is complete and tested. The web UI has been updated to surface Ops features. Lint issues were addressed.

We explicitly **paused Telegram** work (do not test Telegram). The immediate next big effort is **Phase‑2 Heartbeat/Proactive Loop**, which is still a detailed‑plan gap. We need to expand the plan by reviewing Clawdbot’s actual implementation and then integrate into Universal Agent.

**Current status:**
- Ops/Control Plane API endpoints added + UI wiring.
- Ops tests passing.
- UI lint clean.
- Documentation updated (plan + ops doc + architecture + functionality).
- Telegram excluded from testing for now.

---

## 3) Primary Repos & References

### 3.1 Universal Agent repo
`/home/kjdragan/lrepos/universal_agent`

#### Key files (code)
- `src/universal_agent/gateway_server.py`
  - Ops endpoints added.
- `src/universal_agent/approvals.py`
  - Approvals store (new).
- `src/universal_agent/ops_config.py`
  - `ops_config_schema()` for UI.

#### Key files (web UI)
- `web-ui/components/OpsPanel.tsx`
  - Channels list + probe, approvals list, schema viewer.
- `web-ui/app/page.tsx`
  - Hook order fixes.
- `web-ui/components/CombinedActivityLog.tsx`
  - Derived state changes to satisfy lint.
- `web-ui/eslint.config.mjs`
  - ESLint v9 flat config.
- `web-ui/package.json`
  - `lint` script set to `eslint .`.

#### Key docs
- `Project_Documentation/047_Ops_Control_Plane.md` (Ops API + UI details)
- `Project_Documentation/045_Technical_Architecture_Overview.md`
- `Project_Documentation/046_Functionality_Catalog.md`
- `Project_Documentation/BUILD_OUT_CLAWD_FEATURES/03_Implementation_Plan.md`

#### Env samples
- `.env.sample` updated with:
  - `UA_APPROVALS_PATH`
  - `UA_WEB_UI_URL`

### 3.2 Clawdbot repo (parity reference)
`/home/kjdragan/lrepos/clawdbot`

**Use‑case:** feature parity reference. We are not opposed to verbatim functionality, but it must integrate cleanly into Universal Agent’s architecture and code style.

---

## 4) Implemented Ops/Control Plane Slice (Phase‑3)

### 4.1 Gateway Ops API endpoints (new)
- `POST /api/v1/ops/channels/{channel_id}/probe`
  - Probes channel health. Implemented for `gateway`, `cli`, `web` (uses `UA_WEB_UI_URL`), and `telegram` (best‑effort).
- `GET /api/v1/ops/config/schema`
  - Returns JSON Schema for ops config.
- `GET /api/v1/ops/approvals`
  - Lists approvals.
- `POST /api/v1/ops/approvals`
  - Creates/updates approvals.
- `PATCH /api/v1/ops/approvals/{approval_id}`
  - Partial approval updates (approve/reject/comment).

### 4.2 Approvals store
**File:** `src/universal_agent/approvals.py`
- Stored in `AGENT_RUN_WORKSPACES/approvals.json`.
- Override via `UA_APPROVALS_PATH`.

### 4.3 Ops config schema
**File:** `src/universal_agent/ops_config.py`
- `ops_config_schema()` returns JSON Schema for UI.

### 4.4 Web UI Ops Panel
**File:** `web-ui/components/OpsPanel.tsx`
- Channel list + probe button.
- Approvals list + approve/reject.
- Schema viewer.

### 4.5 Lint fixes
- `web-ui/app/page.tsx` and `CombinedActivityLog.tsx` updated to avoid conditional hooks and setState in derived state.
- ESLint v9 config added.

---

## 5) Testing & Verification

### 5.1 Gateway Ops tests
```bash
uv run pytest tests/gateway/test_ops_api.py -q
```

### 5.2 Web UI lint
```bash
cd web-ui
npm run lint
```

### 5.3 Web UI manual check (use browser)
Open the Web UI and verify:
1) **Ops Panel** loads.
2) **Channels list** appears; click **Probe** on `gateway` → status updates.
3) **Approvals** list shows; approve/reject buttons update state.
4) **Ops config schema** renders JSON.
5) No hook/lint errors in console.

> Important: Do **not** run Telegram tests. Telegram is explicitly out‑of‑scope.

---

## 6) Known Issues / Non‑Goals

- **Telegram integration**: off‑limits for testing right now. Known instability.
- **Logfire 401s**: tokens may be invalid. Does not block core ops.
- **Heartbeat/Proactive Loop**: plan is outline‑only; must be expanded against Clawdbot.
- **Approvals**: no auth gating yet.

---

## 7) Phase‑2 (Heartbeat) — Required next work

We only have outline notes. The next agent should:

1) Review Clawdbot’s heartbeat/proactive loop implementation.
2) Extract:
   - scheduling mechanism
   - policy gating (time windows, limits, allowlists)
   - configuration source and format
   - logging/telemetry patterns
3) Expand detailed tasks in:
   - `Project_Documentation/BUILD_OUT_CLAWD_FEATURES/03_Implementation_Plan.md`
4) Implement in Universal Agent (likely in gateway), add tests, update docs.

---

## 8) Phase‑3 Follow‑Ups (beyond current slice)

Potential follow‑ups:
- Expanded channel probes (latency, last‑seen, error counters).
- Session health endpoints.
- Log tail endpoint.
- Ops UI improvements (filters, search, per‑channel indicators).

---

## 9) Clawdbot Parity Guidance

We **can** copy functionality verbatim if required, but must integrate cleanly:
- Align with existing gateway architecture and event stream.
- Keep Universal Agent’s conventions (logging, env vars, folder structure).
- Document divergences or intentional changes.

---

## 10) Quick Start Commands

From `/home/kjdragan/lrepos/universal_agent`:

**Gateway (full stack):**
```bash
./start_gateway.sh
```

**CLI dev:**
```bash
./start_cli_dev.sh
```

**Ops tests:**
```bash
uv run pytest tests/gateway/test_ops_api.py -q
```

**UI lint:**
```bash
cd web-ui && npm run lint
```

---

## 11) Where to look in Clawdbot

Reference repo: `/home/kjdragan/lrepos/clawdbot`

Focus on:
- Heartbeat/proactive scheduling logic.
- Approvals or policy gating.
- Ops/Control features and UI.

The parity plan should adapt those ideas into Universal Agent (not a direct fork).

---

## 12) Immediate Next Steps (for new agent)

1) Confirm ops tests still pass.
2) Run UI lint.
3) Manual check Ops Panel in Web UI.
4) Expand Phase‑2 heartbeat plan using Clawdbot’s implementation.
5) Implement heartbeat scheduler + policy gating in gateway.
6) Update docs and tests accordingly.

---

## 13) Additional Notes

- **Do not run Telegram tests** right now.
- If Logfire tokens are invalid, either update or set `UA_DISABLE_LOGFIRE=1` to reduce noise.
- Keep updates reflected in `Project_Documentation/BUILD_OUT_CLAWD_FEATURES/03_Implementation_Plan.md`.

---

End of handoff.
