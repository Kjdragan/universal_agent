---
description: Show Universal Agent local dev status (local PIDs, ports, VPS pause state)
---

# /devstatus — Local dev status snapshot

Run the read-only status check. Never modifies anything. Useful for:

- Confirming whether the user is in State A (NORMAL) or State B (DEV) right now.
- Verifying the local stack is healthy on its three ports.
- Checking whether a VPS pause stamp exists (and when it expires).
- Inspecting the VPS-side `active/inactive` state of the conflict services.

## Steps

1. Run `./scripts/dev_status.sh` from the repo root.
2. Summarize the output: state (A or B), which local services are alive, whether VPS is paused, expiry time if so, and any red flags.
3. If the user is in State B and the expiry is close, remind them to either run `/devdown` or be aware the reconciler will auto-release.

Canonical guide: `docs/development/LOCAL_DEV.md`.
