# 025 — Handoff: Harness Verification + Repair Loop + Ops Control Plane

**Date:** 2026-02-04
**Owner:** Codex (handoff)
**Scope:** Universal Agent — Ops/Control Plane parity work + testing/linting + documentation updates.

---

## 0) Executive Summary (What’s done / where we are)
We have implemented a Phase‑3 Ops/Control Plane slice to close parity gaps with Clawdbot and added UI support for it. This includes:

- **Gateway Ops API additions**: channel probes, approvals CRUD, ops config schema.
- **Approvals store**: persisted to `AGENT_RUN_WORKSPACES/approvals.json` (overridable).
- **Ops config JSON Schema**: returned by gateway to ensure UI can render/validate.
- **Web UI Ops Panel** updates**: channels list + probe button, approvals list + approve/reject, schema viewer.
- **Lint stability**: ESLint v9 flat config, cleaned hooks‑order issues; `npm run lint` now clean.
- **Tests**: `tests/gateway/test_ops_api.py` expanded, passing.
- **Docs**: Phase‑3 plan updated, Ops Control Plane doc added, architecture/functionality docs added.

**Current status:** Code and docs are in place. Main follow‑ups are to continue Phase‑3 follow‑ups beyond this slice, and to extend Phase‑2 heartbeat/parity once scoped against Clawdbot. Telegram is explicitly out‑of‑scope for testing right now.

---

## 1) Repo Layout + Key References

### Universal Agent (primary repo)
`/home/kjdragan/lrepos/universal_agent`

**Key directories & files touched:**
- `src/universal_agent/gateway_server.py`
  - Added Ops endpoints: channel probe, approvals, config schema.
- `src/universal_agent/approvals.py`
  - New approvals store and helpers.
- `src/universal_agent/ops_config.py`
  - `ops_config_schema()` returns JSON Schema for UI.
- `web-ui/components/OpsPanel.tsx`
  - Added Channels + Probes, Approvals, Schema viewer.
- `web-ui/app/page.tsx`
  - Hook ordering fixes + minor lint fixes.
- `web-ui/components/CombinedActivityLog.tsx`
  - Lint fixes (no setState in effect for derived state).
- `web-ui/eslint.config.mjs`
  - ESLint v9 flat config.
- `web-ui/package.json`
  - `lint` script updated to `eslint .`.
- `Project_Documentation/047_Ops_Control_Plane.md`
  - New/updated Ops API docs.
- `Project_Documentation/BUILD_OUT_CLAWD_FEATURES/03_Implementation_Plan.md`
  - Phase‑3 plan updated to reflect implemented work.
- `Project_Documentation/045_Technical_Architecture_Overview.md`
  - Architecture overview.
- `Project_Documentation/046_Functionality_Catalog.md`
  - Functionality catalog.
- `.env.sample`
  - Added `UA_APPROVALS_PATH`, `UA_WEB_UI_URL`.

### Clawdbot (reference)
`/home/kjdragan/lrepos/clawdbot`

**Purpose:** feature parity reference only. We integrate functionality into Universal Agent, not copy wholesale.

---

## 2) Implemented Features (Phase‑3 slice)

### 2.1 Ops API additions (Gateway)
**Endpoints:**
- `POST /api/v1/ops/channels/{channel_id}/probe`
  - Probes known channel IDs: `gateway`, `cli`, `web`, `telegram`.
  - Result cached in gateway state and included in channel list.
- `GET /api/v1/ops/config/schema`
  - Returns JSON Schema for `ops_config`.
- `GET /api/v1/ops/approvals`
  - List approvals.
- `POST /api/v1/ops/approvals`
  - Create/update approval.
- `PATCH /api/v1/ops/approvals/{approval_id}`
  - Partial update (approve/reject/comment).

**Implementation notes:**
- Channel probing uses `UA_WEB_UI_URL` for web. Telegram probe is best‑effort (requires token) and currently not tested.
- All endpoints are simple and minimal; no auth gating currently applied.

### 2.2 Approvals Store
**File:** `src/universal_agent/approvals.py`
- Approvals persisted in `AGENT_RUN_WORKSPACES/approvals.json` by default.
- Override via `UA_APPROVALS_PATH`.
- Functions:
  - `list_approvals()`
  - `upsert_approval(approval)`
  - `update_approval(approval_id, patch)`

### 2.3 Ops Config Schema
**File:** `src/universal_agent/ops_config.py`
- `ops_config_schema()` returns JSON schema for UI.
- UI renders it as JSON in Ops Panel.

### 2.4 Web UI Ops Panel
**File:** `web-ui/components/OpsPanel.tsx`
- **Channels**: list from `/api/v1/ops/channels` + probe action.
- **Approvals**: list/approve/reject (`/api/v1/ops/approvals`).
- **Schema viewer**: `/api/v1/ops/config/schema`.

### 2.5 Lint & Hook Fixes
- ESLint v9 flat config in `web-ui/eslint.config.mjs`.
- `web-ui/app/page.tsx` hook ordering fixed.
- `web-ui/components/CombinedActivityLog.tsx` derived state moved to `useMemo`.

---

## 3) Tests & Verification

### 3.1 Unit/Integration Tests
**Gateway Ops API tests:**
- `tests/gateway/test_ops_api.py`
  - Config schema returns.
  - Channel probe works for `gateway`.
  - Approvals CRUD works.

**Run:**
```bash
uv run pytest tests/gateway/test_ops_api.py -q
```

### 3.2 Web UI Lint
```bash
cd web-ui
npm run lint
```

### 3.3 Manual UI Verification (no Telegram)
Open Web UI and verify:
1) **Ops Panel**
   - Channels list present.
   - Click “Probe” for `gateway` → status updates.
   - Approvals list appears; Approve/Reject buttons update state.
   - Schema viewer renders JSON.
2) **No regressions**
   - Basic UI loads.
   - Combined Activity Log renders (no hook errors).

---

## 4) Known Issues / Non‑Goals (current phase)

- **Telegram**: Explicitly out‑of‑scope for testing right now. Do not run Telegram tests.
- **Logfire 401**: if tokens invalid, warnings appear. This does not block core flow.
- **Heartbeat Phase‑2**: still outline‑only; needs deeper parity analysis vs Clawdbot.
- **Approvals security**: endpoints are open. If needed, add auth gate later.

---

## 5) Phase‑2 (Heartbeat) — What’s still needed

We only have an outline. Next agent should:
1) Review Clawdbot’s heartbeat/proactive loop implementation.
2) Identify:
   - scheduling logic
   - policy gating
   - configuration source
   - event/logging patterns
3) Expand `Project_Documentation/BUILD_OUT_CLAWD_FEATURES/03_Implementation_Plan.md`
   - Add detailed tasks + acceptance criteria.
4) Implement in Universal Agent (likely in gateway).

**Reference file:**
`Project_Documentation/BUILD_OUT_CLAWD_FEATURES/03_Implementation_Plan.md`

---

## 6) Phase‑3 Follow‑Ups (after current slice)

Planned follow‑ups from Phase‑3 plan:
- Channel probes (done; can be expanded to more signals).
- Config schema (done, may evolve).
- Approvals control (done; add auth + UI enhancements).
- Add Ops UI wiring to new endpoints in more places if needed.

Future extensions:
- Session health endpoints.
- Log tail endpoints.
- Per‑channel status + counters.

---

## 7) Environment Variables (new/updated)

In `.env.sample`:
- `UA_APPROVALS_PATH`: optional override for approvals store path.
- `UA_WEB_UI_URL`: used by channel probe for `web`.

Others used but not changed:
- `AGENT_RUN_WORKSPACES` (implicit, runtime default).

---

## 8) What the next agent should do first

1) **Confirm current tree is clean** (if needed):
   ```bash
   git status -sb
   ```
2) **Run ops tests**:
   ```bash
   uv run pytest tests/gateway/test_ops_api.py -q
   ```
3) **Run UI lint**:
   ```bash
   cd web-ui && npm run lint
   ```
4) **Manual UI check** (Ops panel).
5) **Expand Phase‑2 heartbeat plan** using Clawdbot as reference.

---

## 9) Clawdbot Parity Guidance

We are not opposed to copying verbatim functionality if needed, **but it must integrate cleanly into the Universal Agent architecture**. Keep parity goals clear:

- **Use Clawdbot for reference** on how it structures scheduling, approvals, channel control.
- **Integrate into our gateway + web‑ui** rather than replicating patterns blindly.
- **Document divergence** if we intentionally choose a different approach.

---

## 10) Files to know (quick index)

**Ops / Gateway:**
- `src/universal_agent/gateway_server.py`
- `src/universal_agent/approvals.py`
- `src/universal_agent/ops_config.py`

**Docs:**
- `Project_Documentation/047_Ops_Control_Plane.md`
- `Project_Documentation/045_Technical_Architecture_Overview.md`
- `Project_Documentation/046_Functionality_Catalog.md`
- `Project_Documentation/BUILD_OUT_CLAWD_FEATURES/03_Implementation_Plan.md`

**UI:**
- `web-ui/components/OpsPanel.tsx`
- `web-ui/app/page.tsx`
- `web-ui/components/CombinedActivityLog.tsx`
- `web-ui/eslint.config.mjs`

**Tests:**
- `tests/gateway/test_ops_api.py`

---

## 11) Verification Checklist (short)

- [ ] Ops endpoints respond (approvals list, schema, probe).
- [ ] UI ops panel renders channels + approvals + schema.
- [ ] `npm run lint` clean in `web-ui`.
- [ ] `pytest tests/gateway/test_ops_api.py -q` passes.
- [ ] No Telegram tests run.

---

## 12) Notes

- If you need to revisit Telegram work: isolate in its own branch. Do not mix with heartbeat/ops work.
- If Logfire tokens are invalid, set `UA_DISABLE_LOGFIRE=1` or update tokens, but do not block heartbeat work.

