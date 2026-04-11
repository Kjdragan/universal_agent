---
description: Start Universal Agent local dev mode (hot-swap VPS services, launch local stack)
---

# /devup — Start local development mode

Run the local dev launcher. This puts the system into **State B (DEV)**: VPS conflict services are paused, a local stack (api, gateway, web-ui) is started wrapped in `infisical run --env=local`, and the Web UI becomes available at <http://localhost:3000>.

## Critical rules this command reinforces

1. **Do NOT push to `develop` or `main` while in State B.** A deploy will restart the paused VPS services and collide with the local stack (Telegram long-poll, Discord bot, queue workers). Always run `/devdown` before pushing.
2. The `local` Infisical env is a copy of `production`. Local dev hits **real** shared infra (Slack, Discord, Telegram, AgentMail, Redis, Postgres). Treat it accordingly.
3. If the VPS is unreachable and you genuinely want to run offline, re-run with `UA_VPS_SKIP_PAUSE=1`.

## Steps

1. Run `./scripts/dev_up.sh` from the repo root.
2. Read the banner output. Confirm the three services (gateway, api, webui) show PIDs and the web-ui health check passes.
3. Report the URLs (`http://localhost:3000`, API on :8001, Gateway on :8002) back to the user.
4. Remind the user to run `/devdown` before their next `git push`.

## Troubleshooting

- If the script fails at the "Missing bootstrap env vars" check, the user's `~/.bashrc` export block for `INFISICAL_CLIENT_ID`/`INFISICAL_CLIENT_SECRET`/`INFISICAL_PROJECT_ID` is missing or their terminal didn't source it.
- If the SSH to `root@uaonvps` fails, show the user `ssh -i ~/.ssh/id_ed25519 root@uaonvps 'echo ok'` to debug.
- Canonical guide: `docs/development/LOCAL_DEV.md`.
