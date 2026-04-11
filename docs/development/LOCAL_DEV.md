# Local Development Guide

**Canonical guide for running Universal Agent on your local workstation.**

This document is the single source of truth for the `scripts/dev_*.sh` workflow. If anything here conflicts with older notes, this file wins.

---

## 1. What this workflow gives you

- `./scripts/dev_up.sh` → visit `http://localhost:3000` → the Web UI is running your working-tree code, with the real `api`, `gateway`, and `web-ui` processes backed by real Infisical secrets.
- Secrets are **never written to disk**. They live only in the memory of the running services, injected at launch time by `infisical run --env=local`.
- Your local SQLite DBs live under `~/lrepos/universal_agent_local_data/`, completely separate from the VPS data.
- While you are in local dev mode, the VPS services that would conflict (Telegram long-poll, Discord bot, watchdog, etc.) are **paused**. They are resumed automatically when you run `./scripts/dev_down.sh`, or — if you forget — by a safety-net timer on the VPS itself.

You develop with the **same functionality** you get on the VPS. No "turn everything off" dev mode.

---

## 2. Mental model: two states

The system is in exactly one of two states at any moment:

| State | Name   | Where HQ runs           | VPS conflict services | When you are in it                      |
|-------|--------|-------------------------|-----------------------|------------------------------------------|
| **A** | NORMAL | VPS (`app.clearspringcg.com`) | running             | Default. Production path. CI/CD deploys. |
| **B** | DEV    | your local desktop      | **paused**            | You ran `dev_up.sh` and haven't run `dev_down.sh` yet. |

The **critical rule**: do not push to `develop` or `main` while in State B. A deploy will restart the paused services on the VPS — and those restarted services will collide with your locally running stack (double Telegram polling, double Discord responses, queue double-processing).

The correct workflow is always:

```
dev_up.sh  →  code, test, iterate  →  dev_down.sh  →  git push
```

If you forget `dev_down.sh`, two safety nets catch you:

1. **`dev_status.sh`** clearly shows when you are in State B.
2. A **VPS-side reconciler timer** runs every 15 minutes, reads a pause-stamp file, and if the stamp has expired (default 8 hours), auto-releases the pause and restarts the services. The VPS cannot stay degraded forever because you closed your laptop.

---

## 3. One-time setup

You only need to do each of these once, ever.

### 3.1 Shell profile: bootstrap Infisical credentials

The three bootstrap credentials live in `~/.bashrc`. They are the **only** secrets that touch disk, and they are the minimum needed to fetch every other secret at runtime.

Add to `~/.bashrc`:

```bash
# ---- Universal Agent — Infisical bootstrap (required for local dev) ----
export INFISICAL_CLIENT_ID="<your-machine-identity-client-id>"
export INFISICAL_CLIENT_SECRET="<your-machine-identity-client-secret>"
export INFISICAL_PROJECT_ID="9970e5b7-d48a-4ed8-a8af-43e923e67572"
# Optional: self-hosted Infisical instance URL
# export INFISICAL_API_URL="https://app.infisical.com"

# ---- Universal Agent — discoverability cd hook ----
# When you cd into the repo, remind yourself of the local-dev entry points.
cd() {
  builtin cd "$@" || return
  if [[ "$PWD" == *"/universal_agent"* ]] && [[ -x "./scripts/dev_up.sh" ]]; then
    if [[ -z "${UA_DEV_HINT_SHOWN:-}" ]]; then
      printf '\n\033[36m[universal_agent]\033[0m Local dev: ./scripts/dev_up.sh  |  status: ./scripts/dev_status.sh  |  stop: ./scripts/dev_down.sh\n'
      printf '\033[36m[universal_agent]\033[0m Guide: docs/development/LOCAL_DEV.md\n\n'
      UA_DEV_HINT_SHOWN=1
    fi
  fi
}
```

Reload your shell: `source ~/.bashrc` (or open a new terminal).

### 3.2 Tooling prerequisites

You need on your workstation:

- `uv` (Python package manager) — <https://docs.astral.sh/uv/>
- `python` 3.12+ (managed by `uv`)
- `fnm` + Node LTS — <https://github.com/Schniz/fnm>
- `infisical` CLI — <https://infisical.com/docs/cli/overview>
- `ssh` with your key at `~/.ssh/id_ed25519` configured to reach `root@uaonvps`

Sanity check:

```bash
infisical --version
uv --version
node --version
npm --version
ssh -i ~/.ssh/id_ed25519 -o BatchMode=yes root@uaonvps 'echo ok'
```

### 3.3 Project dependencies

```bash
cd ~/lrepos/universal_agent
uv sync
(cd web-ui && npm install)
```

### 3.4 Install the VPS-side pause reconciler

One-time, runs on the VPS to install the safety-net timer. From your laptop:

```bash
ssh -i ~/.ssh/id_ed25519 root@uaonvps 'bash -s' \
    < scripts/install_vps_dev_pause_reconciler.sh
```

You should see `ua-dev-pause-reconciler.timer` in the output. This is the timer that auto-releases the pause if you forget to run `dev_down.sh`.

---

## 4. Daily use

### 4.1 Start local dev

```bash
cd ~/lrepos/universal_agent
./scripts/dev_up.sh
```

What happens:

1. Bootstrap env vars and tooling are verified.
2. `ssh` to `root@uaonvps` stops the conflict services and writes a pause stamp at `/etc/universal-agent/dev_pause.stamp` with an 8-hour expiry.
3. Local env vars are set: runtime identity (`HEADQUARTERS`, `development`, `local_workstation`), ports (8001/8002/3000), DB paths under `~/lrepos/universal_agent_local_data/`.
4. Three services start in the background wrapped in `infisical run --env=local`:
    - `gateway` → port 8002
    - `api` → port 8001
    - `web-ui` → port 3000 (Next.js `dev` mode, hot reload)
5. PIDs written to `/tmp/ua-local-dev.pids`, logs to `/tmp/ua-local-logs/*.log`.
6. A health check pings the Web UI, then a big banner prints.

Open <http://localhost:3000>. That's the working-tree code running.

### 4.2 Tail logs

```bash
tail -f /tmp/ua-local-logs/webui.log
tail -f /tmp/ua-local-logs/api.log
tail -f /tmp/ua-local-logs/gateway.log
```

### 4.3 Check state

```bash
./scripts/dev_status.sh
```

Shows: local PIDs + port health + `/tmp/ua-local-logs` summary + VPS pause-stamp state + VPS unit states. Read-only.

### 4.4 Stop local dev

```bash
./scripts/dev_down.sh
```

What happens:

1. Local processes are sent `SIGTERM`, then `SIGKILL` after 2s if still alive.
2. `ssh` to `root@uaonvps` deletes the pause stamp and starts all conflict services back up.
3. Final banner confirms you are back in State A (NORMAL) and safe to push.

### 4.5 Reset local DBs

```bash
./scripts/dev_reset.sh
```

Refuses to run if local services are up. Asks you to type `wipe it` to confirm. Wipes `~/lrepos/universal_agent_local_data/` and `/tmp/ua-local-logs/`. Never touches the VPS or Infisical.

### 4.6 Commit & push

Always:

```bash
./scripts/dev_down.sh   # resume VPS
git add -p
git commit -m "..."
git push -u origin <branch>
```

---

## 5. VPS coordination in detail

### 5.1 Services that get paused

See the top of `scripts/dev_up.sh` for the authoritative list. Currently:

Services:

- `universal-agent-api.service`
- `universal-agent-gateway.service`
- `universal-agent-webui.service`
- `universal-agent-telegram.service` — hard conflict: Telegram long-poll cannot run in two places at once.
- `ua-discord-cc-bot.service` — hard conflict: two bots would respond to every message.
- `universal-agent-service-watchdog.service` — would restart the other paused services otherwise.

Timers:

- `universal-agent-service-watchdog.timer`
- `universal-agent-youtube-playlist-poller.timer`

If the unit names on the VPS drift, edit the arrays at the top of `dev_up.sh`, `dev_down.sh`, `dev_status.sh`, and `install_vps_dev_pause_reconciler.sh` — the list is the only thing to change.

### 5.2 Services that are **not** paused

- `universal-agent-oom-alert.service/.timer` — harmless monitoring, leave running.
- `universal-agent-vp-worker@*.service` — VP workers continue processing queue work from the VPS-side HQ. If your local dev publishes delegations and you want them processed locally, run a VP worker locally too (not part of `dev_up.sh` by default; see §7).

### 5.3 The pause stamp

`/etc/universal-agent/dev_pause.stamp` on the VPS, created by `dev_up.sh`:

```
paused_by_host=kevins-desktop
paused_at_epoch=1744...
paused_at_iso=2026-04-11T...
expires_at_epoch=1744...
expires_at_iso=2026-04-11T...
reason=local_dev_hot_swap
```

The VPS-side reconciler reads this file every 15 minutes. If `expires_at_epoch` is in the past, it deletes the stamp and restarts the paused services.

Override the pause window when starting dev:

```bash
UA_VPS_PAUSE_HOURS=2 ./scripts/dev_up.sh
```

### 5.4 Emergency: bypass the VPS SSH entirely

If the VPS is already down or unreachable (e.g. network is flaky, you only want to test offline features):

```bash
UA_VPS_SKIP_PAUSE=1 ./scripts/dev_up.sh
UA_VPS_SKIP_RESUME=1 ./scripts/dev_down.sh
```

You accept any conflict that results.

---

## 6. Security notes

- **No secret ever touches disk.** `dev_up.sh` wraps every service launch in `infisical run --env=local --projectId=$INFISICAL_PROJECT_ID --`. The child process receives the secrets as env vars in its own memory space, exactly like on the VPS.
- **The `local` Infisical env is a copy of `production`.** Anything you do that writes to shared infra (Redis, Postgres, Slack, Discord, Telegram, AgentMail, third-party APIs) is hitting **real** endpoints. Treat local dev with the same caution as working directly on production.
- **`~/.bashrc` holds only bootstrap credentials** (`INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`, `INFISICAL_PROJECT_ID`). These are the minimum needed to fetch all other secrets at runtime. If your laptop is compromised, rotate the machine identity in Infisical.
- **`web-ui/.env.local` is written to disk** because Next.js `dev` mode requires it. This file contains only non-secret runtime config (identity, ports, DB paths) and is `.gitignore`d. It is rewritten by `install_local_webui_env.sh` on every `dev_up.sh`.

---

## 7. Known limitations

- **Infisical `local` ≡ `production` drift.** The `local` env was seeded as a one-time copy of `production`. If you want true isolation (e.g. a separate local Redis, local Postgres), explicitly override in `dev_up.sh` the env vars for those services **before** the `infisical run` call. Because the Python loader uses `overwrite=False`, anything you set in the shell wins.
- **VP workers are not started locally by default.** If you are developing VP worker code, start a worker in a separate terminal after `dev_up.sh` completes. This is deliberate — VP workers are the most resource-intensive part of the stack and you rarely need them on your laptop.
- **Telegram bot, Discord bot, AgentMail receive live events.** They are running under your `local` Infisical env, which points at the real tokens. Use a separate dev bot/channel if you want isolation.
- **`next.config.js` has hardcoded `localhost:8001` and `localhost:8002`.** If you ever need different ports, you must edit `web-ui/next.config.js` as well.
- **The shell `cd` hook is bash-only.** Zsh/fish users will need to adapt it.

---

## 8. Troubleshooting

### `dev_up.sh` says "Missing bootstrap env vars"

Your `~/.bashrc` export block is missing or your terminal didn't source it. Run:

```bash
source ~/.bashrc
echo "${INFISICAL_CLIENT_ID:0:8}..."
```

### `dev_up.sh` says "Missing binaries on PATH: node npm"

`fnm` isn't set up. Either install `fnm` or make sure its init is in `~/.bashrc`:

```bash
eval "$(fnm env --use-on-cd)"
```

### `dev_up.sh` fails at the VPS SSH step

```bash
ssh -i ~/.ssh/id_ed25519 root@uaonvps 'echo ok'
```

If that prints `ok`, the hot-swap should work. If it prints a Tailscale ACL error like `tailnet policy does not permit you to SSH as user 'kjdragan'`, you tried to SSH without the `root@` prefix — the ACL only permits `root`. Use `root@uaonvps`, not `uaonvps`.

### Web UI shows but API calls 404

Your local API or gateway didn't start. Check:

```bash
./scripts/dev_status.sh
tail -100 /tmp/ua-local-logs/api.log
tail -100 /tmp/ua-local-logs/gateway.log
```

The most common cause is a stale `web-ui/.env.local` pointing at the wrong ports. Delete it and re-run `dev_up.sh`.

### "Pause stamp present" in `dev_status.sh` but I don't remember running `dev_up.sh`

Your last session exited uncleanly, or the reconciler hasn't run yet. Run `dev_down.sh` to clean up, or wait up to 15 minutes for the reconciler.

### I need to SSH to the VPS but the shell keeps kicking me out

Tailscale ACL only permits the `root` user. Always: `ssh -i ~/.ssh/id_ed25519 root@uaonvps`.

---

## 9. Related files

- `scripts/dev_up.sh` — start local stack
- `scripts/dev_down.sh` — stop local stack & resume VPS
- `scripts/dev_reset.sh` — wipe local data dir
- `scripts/dev_status.sh` — read-only health snapshot
- `scripts/install_vps_dev_pause_reconciler.sh` — one-time VPS safety-net installer
- `scripts/install_local_webui_env.sh` — writes `web-ui/.env.local`
- `scripts/render_service_env_from_infisical.py` — env renderer used by the above
- `src/universal_agent/infisical_loader.py` — runtime secret fetch (SDK + REST fallback)
- `src/universal_agent/runtime_role.py` — factory role resolver and capability overrides
- `web-ui/next.config.js` — port rewrites (must match `UA_API_PORT`/`UA_GATEWAY_PORT`)
- `docs/development/LOCAL_DEV_SLASH_COMMANDS.md` — Claude Code slash-command reference
- `AGENTS.md` — the "Local Development Mode" section for non-Claude-Code agents (Antigravity, etc.)

---

## 10. Deprecated

- `scripts/bootstrap_local_hq_dev.sh` — **deprecated**. Writes Infisical credentials to a plaintext `.env` file. Do not use. Use `scripts/dev_up.sh` instead.
