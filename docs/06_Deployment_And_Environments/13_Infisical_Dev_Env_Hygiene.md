# 13. Infisical `development` Environment Hygiene

> **Status:** Optional cleanup. Phase D (2026-05-11) made dev runtime safe against Infisical prod-parity pollution, so this hygiene step is **not blocking** local-dev correctness. Doing it just removes the "ignored" warnings from `just dev` startup logs and clarifies operator intent.

## Background

Infisical's `production` and `development` environments often mirror each other for "parity" — same keys, sometimes different values. Many `UA_*_ENABLED=1` flags are operationally meaningful in production (turn the heartbeat on, register crons, etc.) but **must not** be honored in local dev, where the operator wants every autonomous loop OFF by default.

Phase D's `loop_control.should_run_loop` defends against this: in `UA_RUNTIME_STAGE=development`, truthy `UA_<NAME>_ENABLED` values are **ignored**. So dev stays clean even if Infisical's `development` env carries every prod flag.

But the gateway logs a warning for every ignored flag, like:

```
WARNING: UA_HEARTBEAT_ENABLED=truthy detected but IGNORED in dev. Likely
Infisical prod-parity injection. Remove from Infisical development env, or
override via UA_DEV_HEARTBEAT_FORCE_ON=1 if you actually want this loop on.
```

If you're tired of those warnings, clean up Infisical.

## What to remove from Infisical `development` env

Open Infisical UI → select your project → switch to **`development`** environment → delete these keys if present:

### Loop-control flags (dev should not run these)

```
UA_HEARTBEAT_ENABLED
UA_ENABLE_HEARTBEAT
UA_HEARTBEAT_AUTONOMOUS_ENABLED
UA_CRON_ENABLED
UA_ENABLE_CRON
UA_CRON_REGISTRATION_ENABLED
UA_IDLE_POLL_ENABLED
UA_DISPATCH_STALE_SWEEP_ENABLED
UA_DAEMON_SESSIONS_ENABLED
UA_VP_EVENT_BRIDGE_ENABLED
UA_VP_STALE_RECONCILE_ENABLED
UA_AGENTMAIL_ENABLED
UA_AGENTMAIL_SERVICE_ENABLED
UA_AGENTMAIL_WS_ENABLED
UA_NOTIFICATION_DISPATCHER_ENABLED
UA_YOUTUBE_PLAYLIST_WATCHER_ENABLED
UA_HQ_SELF_HEARTBEAT_ENABLED
UA_AUTONOMOUS_DAILY_BRIEFING_ENABLED
UA_CODIE_PROACTIVE_CLEANUP_ENABLED
UA_VP_CODER_WORKSPACE_PRUNING_ENABLED
```

### Kill-switches (also unsafe to mirror from prod)

```
UA_DISABLE_HEARTBEAT
UA_DISABLE_CRON
```

These are operator-facing prod incident response tools; they have no business in dev.

## What to KEEP in Infisical `development` env

Anything dev actually needs:

- Infisical bootstrap creds (the ones the bootstrap script writes to `.env`) — should be in BOTH envs but distinct values
- API keys for external services that dev legitimately uses (Gemini, ZAI, etc.)
- Connection strings for dev resources (dev Postgres URL if you have one, otherwise leave unset to fall back to SQLite — see Phase F)
- `INFISICAL_ENVIRONMENT`, `UA_RUNTIME_STAGE`, `FACTORY_ROLE`, `UA_DEPLOYMENT_PROFILE`, `UA_MACHINE_SLUG` — these are lane-identity values; dev's should be `development`/`development`/`HEADQUARTERS`/`local_workstation`/`kevins-desktop`

## What about production?

**Don't touch the `production` environment.** The flags listed above are operationally correct in prod. They're only wrong in `development`.

## Verifying the cleanup worked

After removing the flags from Infisical and restarting `just dev`, you should see:

```
INFO: 🔧 loop_control: UA_RUNTIME_STAGE=development; reporting per-loop decisions...
INFO:    loop_control[heartbeat]: dev default (UA_RUNTIME_STAGE=development) → OFF
INFO:    loop_control[cron]: dev default (UA_RUNTIME_STAGE=development) → OFF
INFO:    loop_control[agentmail_service]: dev default (UA_RUNTIME_STAGE=development) → OFF
...
INFO: 🔧 loop_control: no dev opt-ins; all loops dev-default-OFF.
```

— no `WARNING: ... detected but IGNORED in dev` lines. That's the clean state.

## When you DO want a specific loop on for dev testing

Use `UA_DEV_<NAME>_FORCE_ON=1` in your local `.env`. See `12_Local_Dev_Environment.md` § Triggering autonomous loops manually for the full table.

## Cross-references

- [`12_Local_Dev_Environment.md`](12_Local_Dev_Environment.md) — the contract
- [`../deployment/secrets_and_environments.md`](../deployment/secrets_and_environments.md) — canonical Infisical guide
- `src/universal_agent/loop_control.py` — the gate logic
