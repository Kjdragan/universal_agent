# 31. VPS Deployment Decision Tree (2026-02-14)

## Purpose

Single reference for deploying local changes to the production VPS. Replaces the need to cross-reference Docs 22 and 27.

## Environment

- Local repo: `/home/kjdragan/lrepos/universal_agent`
- VPS host: `root@100.106.113.93` (Tailscale VPN)
- VPS app root: `/opt/universal_agent`
- SSH key: `~/.ssh/id_ed25519`
- Services: `universal-agent-gateway`, `universal-agent-api`, `universal-agent-webui`, `universal-agent-telegram`

## Critical Rule

The VPS app directory is **not a git clone**. Never use `git pull` on the VPS. All deployments are file-sync from local via VPN.

---

## Decision Tree: "I changed something locally — what do I do?"

### 1) I changed 1-5 Python source files (no new dependencies)

**Use: `vpsctl push` + targeted restart**

```bash
# Push the changed files
scripts/vpsctl.sh push src/universal_agent/agent_setup.py src/universal_agent/execution_engine.py

# Restart only the affected service
scripts/vpsctl.sh restart gateway
```

**Which service to restart?**

| What you changed | Restart |
| --- | --- |
| `src/universal_agent/gateway_server.py`, `src/universal_agent/execution_engine.py`, `src/universal_agent/agent_setup.py`, `src/universal_agent/main.py`, anything under `src/universal_agent/memory/`, `src/universal_agent/hooks*`, `src/universal_agent/cron*`, `src/universal_agent/urw/` | `gateway` |
| `src/universal_agent/api/` | `api` |
| `web-ui/` source files | `webui` (after rebuild, see below) |
| `src/universal_agent/telegram*` | `telegram` |
| Shared library code used by multiple services | `all` |

**Time:** ~10 seconds.

---

### 2) I changed prompt assets, skills, agent definitions, or config (no code)

**Use: `vpsctl push` + restart gateway**

```bash
scripts/vpsctl.sh push src/universal_agent/prompt_assets/SOUL.md
scripts/vpsctl.sh push .claude/agents/task-decomposer.md
scripts/vpsctl.sh restart gateway
```

Prompt assets, skill files, and agent definitions are loaded at session start, so restarting the gateway picks them up for the next session. No dependency install needed.

**Time:** ~10 seconds.

---

### 3) I changed web-ui files (Next.js frontend)

**Use: `vpsctl push` + remote rebuild + restart webui**

```bash
# Push the changed files (or the whole web-ui dir for many changes)
scripts/vpsctl.sh push web-ui/

# Rebuild and restart on VPS
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 '
  set -e
  cd /opt/universal_agent
  runuser -u ua -- bash -lc "cd /opt/universal_agent/web-ui && npm install && npm run build"
  systemctl restart universal-agent-webui
  systemctl is-active universal-agent-webui
'
```

**Time:** ~30-60 seconds (npm build).

---

### 4) I changed Python dependencies (pyproject.toml, uv.lock)

**Use: `vpsctl push` + remote `uv sync` + restart affected services**

```bash
scripts/vpsctl.sh push pyproject.toml uv.lock

ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 '
  set -e
  cd /opt/universal_agent
  runuser -u ua -- bash -lc "cd /opt/universal_agent && /home/ua/.local/bin/uv sync"
  systemctl restart universal-agent-gateway universal-agent-api
  for s in universal-agent-gateway universal-agent-api; do
    printf "%s=" "$s"; systemctl is-active "$s"
  done
'
```

**Time:** ~20-40 seconds.

---

### 5) I changed many files across multiple areas (big feature, refactor)

### Use: Full deploy script

```bash
./scripts/deploy_vps.sh
```

This does:

1. `rsync` entire workspace to VPS (excludes `.git`, `.venv`, `.env`, `AGENT_RUN_WORKSPACES`, `artifacts`, `tmp`)
2. Preserves VPS `.env` and runtime data
3. Runs `uv sync` for Python deps
4. Runs `npm install && npm run build` for web-ui
5. Restarts all 4 services
6. Verifies health and public endpoints
7. Checks ops auth gate

**Time:** ~60-120 seconds.

---

### 6) I changed `.env` variables

**Never overwrite VPS `.env` from local.** The VPS `.env` contains production secrets that may differ from local.

```bash
# Edit directly on VPS
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 'nano /opt/universal_agent/.env'

# Then restart affected services
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 '
  systemctl restart universal-agent-gateway universal-agent-api universal-agent-webui universal-agent-telegram
'
```

---

### 7) I changed nginx config or systemd units

These are VPS-only files not in the repo. Edit directly:

```bash
# Edit nginx
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 'nano /etc/nginx/sites-enabled/universal-agent-app'
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 'nginx -t && systemctl reload nginx'

# Edit systemd unit
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 'nano /etc/systemd/system/universal-agent-gateway.service'
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 'systemctl daemon-reload && systemctl restart universal-agent-gateway'
```

---

## Post-Deploy Verification (always do this)

### Quick check

```bash
scripts/vpsctl.sh status all
```

### Full check (after big deploys)

```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 '
  for s in universal-agent-gateway universal-agent-api universal-agent-webui universal-agent-telegram; do
    printf "%s=" "$s"; systemctl is-active "$s"
  done
  echo
  curl -s https://api.clearspringcg.com/api/v1/health | head -c 220; echo
  curl -s -o /dev/null -w "APP=%{http_code}\n" https://app.clearspringcg.com/
'
```

### If something is broken

```bash
# Check logs for the failing service
scripts/vpsctl.sh logs gateway   # or api, webui, telegram

# Roll back: re-deploy from last known-good commit
git stash   # or git checkout <good-commit>
./scripts/deploy_vps.sh
git stash pop   # restore your WIP
```

---

## Agent/Cascade Deploy Guidelines

When Cascade or any agent makes changes during a session:

1. **If working on code that affects the deployed gateway/API** — push changes after each logical unit of work, not after every single edit.
2. **If working on prompt/skill/config changes only** — batch and push at the end of the conversation or when the user wants to test.
3. **If the user says "deploy this" or "push this"** — use `vpsctl push` for targeted files + restart, or `deploy_vps.sh` for full redeploy.
4. **Always verify** service health after any deploy.
5. **Never auto-deploy** without the user's explicit request.

---

## Tool Reference

| Tool | When | Command |
| --- | --- | --- |
| **vpsctl push** | 1-5 files, targeted | `scripts/vpsctl.sh push <path...>` |
| **vpsctl restart** | After push | `scripts/vpsctl.sh restart gateway\|api\|webui\|telegram\|all` |
| **vpsctl status** | Quick health check | `scripts/vpsctl.sh status all` |
| **vpsctl logs** | Debugging | `scripts/vpsctl.sh logs gateway\|api\|webui\|telegram` |
| **deploy_vps.sh** | Full redeploy | `./scripts/deploy_vps.sh` |
| **pull_remote_workspaces_now.sh** | Pull workspace files locally | `scripts/pull_remote_workspaces_now.sh [session_id]` |
