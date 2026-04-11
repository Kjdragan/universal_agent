---
description: Wipe Universal Agent local dev data dir (destructive, gated)
---

# /devreset — Wipe local dev state

Destructive: deletes `~/lrepos/universal_agent_local_data/` (all local SQLite DBs + artifacts) and `/tmp/ua-local-logs/`. Never touches the VPS or Infisical.

## Preconditions

- Local dev stack must be **stopped**. `dev_reset.sh` refuses to run if `/tmp/ua-local-dev.pids` shows live processes.
- User must confirm by typing `wipe it` (or setting `CONFIRM="wipe it"` for non-interactive use).

## Steps

1. Confirm the user actually wants a destructive wipe. If there is any ambiguity, ask first — this is irreversible.
2. If the local stack is up, tell the user to run `/devdown` first.
3. Run `CONFIRM="wipe it" ./scripts/dev_reset.sh` from the repo root.
4. Report success and remind the user the next `/devup` will start from a blank DB.

Canonical guide: `docs/development/LOCAL_DEV.md`.
