---
description: Stop Universal Agent local dev mode (stop local stack, resume VPS services)
---

# /devdown — Stop local development mode

Run the local dev teardown. This puts the system back into **State A (NORMAL)**: local processes are stopped, the VPS pause stamp is cleared, and the paused VPS services are restarted.

## When to run this

- Before every `git push` to `develop` or `main`.
- At the end of a work session.
- Any time `./scripts/dev_status.sh` shows a pause stamp is present but you are no longer actively developing.

## Steps

1. Run `./scripts/dev_down.sh` from the repo root.
2. Verify the banner shows "LOCAL DEV STOPPED" and "VPS services resumed".
3. If the VPS resume step reports failures, the user can wait for the VPS-side reconciler timer (runs every 15 minutes, auto-releases expired pauses) or manually `ssh -i ~/.ssh/id_ed25519 root@uaonvps` and follow the emitted hints.

## After /devdown

Safe to commit and push. The user is back in the same state as if they had never run `/devup`.

Canonical guide: `docs/development/LOCAL_DEV.md`.
