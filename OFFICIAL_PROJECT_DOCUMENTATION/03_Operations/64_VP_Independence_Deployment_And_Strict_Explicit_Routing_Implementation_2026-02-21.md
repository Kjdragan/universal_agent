# 64. VP Independence Deployment And Strict Explicit Routing Implementation (2026-02-21)

## 1. Objective
- Finish hardening for true VP independence:
  - Always-on external VP workers as first-class systemd services.
  - Strict explicit VP routing so requests like "use General VP/DP" cannot silently fall back to Task/sub-agent/direct primary execution.

## 2. Root Cause Confirmed
- Regression source was stale override wiring in `src/universal_agent/main.py`:
  - `hooks_manager.on_user_prompt_skill_awareness` was overridden with a legacy function that did not maintain VP-intent state in `AgentHookSet`.
  - Result: explicit VP intent guardrails became prompt-fragile in real runs.

## 3. Implemented Changes

### 3.1 Gateway strict explicit-VP routing
- File: `src/universal_agent/gateway.py`
- Added deterministic explicit VP intent inference from user text for:
  - General VP/DP aliases.
  - Coder VP/DP aliases.
- Added strict policy handling:
  - Auto-sets `delegate_vp_id` + `mission_type` when explicit VP intent is detected.
  - Enforces `require_external_vp` for explicit VP turns (configurable by feature flag).
  - If external dispatch is unavailable/fails under strict mode, emits hard error and exits turn.
  - No silent fallback to Simone primary path for explicit VP turns.

### 3.2 New feature flag
- File: `src/universal_agent/feature_flags.py`
- Added:
  - `vp_explicit_intent_require_external(default=True)`
  - Env controls:
    - `UA_VP_EXPLICIT_INTENT_REQUIRE_EXTERNAL`
    - `UA_DISABLE_VP_EXPLICIT_INTENT_REQUIRE_EXTERNAL`

### 3.3 Removed stale override path
- File: `src/universal_agent/main.py`
- Removed stale method override:
  - `hooks_manager.on_user_prompt_skill_awareness = on_user_prompt_skill_awareness`
- This restores canonical `AgentHookSet` behavior and preserves VP-intent turn state.

### 3.4 VP worker systemd service hardening
- File: `deployment/systemd/universal-agent-vp-worker@.service`
- Updated to production-safe defaults:
  - `User=ua`, `Group=ua`
  - `EnvironmentFile=-/opt/universal_agent/.env`
  - Stable `PATH`/`PYTHONPATH`
  - `NoNewPrivileges=true`
  - Network-online dependency

### 3.5 VP service installer script (new)
- File: `scripts/install_vp_worker_services.sh`
- Adds root installer for template + instances:
  - Installs `universal-agent-vp-worker@.service`
  - Enables/starts:
    - `universal-agent-vp-worker@vp.general.primary.service`
    - `universal-agent-vp-worker@vp.coder.primary.service`
  - Creates/chowns runtime roots (`logs`, `AGENT_RUN_WORKSPACES`)

### 3.6 Deploy pipeline enforcement
- File: `scripts/deploy_vps.sh`
- Added strict deploy-time gates:
  - `.env` must exist.
  - Required VP env keys must exist and be valid:
    - `UA_VP_EXTERNAL_DISPATCH_ENABLED` (must be enabled)
    - `UA_VP_DISPATCH_MODE` (must be `db_pull`)
    - `UA_VP_ENABLED_IDS` (must include both `vp.general.primary` and `vp.coder.primary`)
  - Installs/refreshes VP worker services on each deploy.
  - Restarts VP services with core services.
  - Fails deploy if VP sessions are not ready in VP DB.

## 4. Tests Added
- File: `tests/api/test_gateway_coder_vp_routing.py`
- Added:
  - `test_gateway_explicit_general_dp_intent_auto_dispatches_external_vp`
  - `test_gateway_strict_explicit_general_vp_blocks_primary_fallback_when_external_disabled`

## 5. Operational Outcome
- Explicit VP delegation language now routes deterministically to external VP mission queue.
- If infrastructure is not available, the system now fails loudly for explicit VP turns instead of silently continuing on non-VP paths.
- VP workers are now deployed/managed as first-class always-on services in the default VPS deploy flow.

## 6. Remaining Validation
- Run targeted tests and full deploy verification on VPS:
  - Ensure both VP workers show active session rows in VP DB after deploy.
  - Re-run poem/story delegation prompts and confirm no Task-path fallback.
