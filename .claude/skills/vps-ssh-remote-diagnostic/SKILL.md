---
name: vps-ssh-remote-diagnostic
description: "Read-only-first diagnostics on the UA production VPS (uaonvps) over SSH: inspect systemd services, journald logs, disk, processes, git state, and the remote venv before changing anything. Use whenever the user wants to ssh ua@uaonvps, ssh into the server/box, log into the prod box, check the prod gateway or prod worker, look at journalctl on the deployed host, or see why prod broke. Use when the user says \"ssh ua@uaonvps\", \"ssh into the server\", \"log into the box\", \"on the remote host\", \"the prod box\", \"the deployed box\", \"check the prod gateway\", \"check the prod worker\", \"is the worker unit active\", \"tail the worker logs on prod\", \"journalctl on the vps\", \"systemctl status on prod\", or \"check the remote venv\". Also fires on failure symptoms in prod: \"prod is down\", \"prod is throwing errors\", \"prod 502s / 500s\", \"the gateway is timing out\", \"service crashed\", \"the unit is dead/inactive/failed\", \"OOM / out of memory on prod\", \"prod is slow / high load\", \"disk full / out of space on prod\". Also on deploy/version checks: \"did my deploy land\", \"did the deploy go through\", \"is prod up to date\", \"what version is running in prod\", \"is prod on the latest commit\", \"check prod git state\", \"is there a dirty working tree on prod\", \"what commit is prod on\". Also on read-only state-DB inspection: \"check the prod database\", \"read csi.db on prod\", \"query activity_state.db read-only\", \"inspect prod sqlite state\", \"count the rows in csi.db on prod\". NOT for desktop/worktree git work (use operations-worktree), NOT for local/dev DB work, and NOT for deploying or mutating prod (this skill is read-only-first — do not restart units or edit files under this skill)."
user-invocable: true
risk: safe
source: "Derived from the UA skill-gap finder backlog (issue #796) -- vps-ssh-remote-diagnostic."
---

# VPS SSH Remote Diagnostic

Inspect the UA production VPS (`uaonvps` — the prod box, the deployed host, the remote server) **read-only first**. The host runs the deployed universal_agent under systemd with its own virtualenv; almost every "is prod healthy / why did prod break / did my deploy land" question is answered by `systemctl`, `journalctl`, `df`, `free`, `ps`, `git rev-parse`, and a `sqlite3` peek — none of which mutate anything. Look before you touch, and never restart a unit or edit a file under this skill.

## Canonical facts

These are the load-bearing constants. Memorize them; do not re-derive them every session.

- **SSH target:** `ssh ua@uaonvps` (host alias resolved from the operator's `~/.ssh/config`; the deploy user is `ua`). "ssh into the server", "log into the box", "the prod box", "the deployed box" all mean this host.
- **Deploy root:** `/opt/universal_agent` — the checked-out repo on prod.
- **Remote venv python:** `/opt/universal_agent/.venv/bin/python` — always invoke this absolute interpreter, never a bare `python`/`python3` (the system python is the wrong env).
- **Source layout:** code lives under `src/`; run app modules with `PYTHONPATH=src` from the deploy root.
- **systemd units — the prefix is `universal-agent-`, NOT `ua-`.** Discover the real unit names rather than hardcoding them. The two you most often want:
  - the **gateway** → `universal-agent-local-gateway.service`
  - the **worker** → `universal-agent-vp-worker@.service` (a templated unit; instances appear as `universal-agent-vp-worker@<id>.service`).
  Reserve `ua-*` for the Discord/youtube daemons (e.g. `ua-youtube-forward-tunnel.service`) — it does **not** match the core gateway/worker services. There is no `ua-gateway` or `ua-worker` on prod.
- **State DBs (read-only inspection only):** the SQLite databases under the deploy root (e.g. `csi.db`, `activity_state.db`). Open them `file:...?mode=ro` so a stray write can never corrupt prod state.

## Read-only-first posture

The default verb is **inspect**, never **change**. Sequence every investigation as:

0. **Discover units** — learn the real unit names before feeding them into any command (the prefix is `universal-agent-`, so an `ua-*` glob finds nothing useful).
1. **Status** — is the unit active? `systemctl status <unit>`.
2. **Logs** — what did it say before/while failing? `journalctl -u <unit>`.
3. **Resources** — is the box starved (OOM, high load, disk full)? `df -h`, `free -h`, `ps`/`top`.
4. **Version** — what commit is prod actually on / did the deploy land? `git -C /opt/universal_agent rev-parse HEAD`.
5. **State** — read the SQLite DBs in `mode=ro` only.

Only after the read-only pass has localized the problem do you consider a mutation — and a mutation is a separate, operator-approved step outside this skill.

## The catalog (copy-pasteable, all read-only)

Each command below is a single SSH round-trip. Quote the remote command so the local shell doesn't expand it. Run the discovery command **first** and substitute a real `<unit>` (e.g. `universal-agent-local-gateway.service`) into the rest.

**Discover the real unit names (do this before anything else):**
```bash
ssh ua@uaonvps 'systemctl list-units --type=service | grep universal-agent'
# Discord/youtube daemons use the ua- prefix; widen the net if you need them too:
ssh ua@uaonvps "systemctl list-units --type=service --no-legend --plain | grep -E '(universal-agent|ua-)'"
```

**Service status (substitute the `<unit>` you discovered, e.g. `universal-agent-local-gateway.service`):**
```bash
ssh ua@uaonvps 'systemctl status universal-agent-local-gateway.service --no-pager'
ssh ua@uaonvps 'systemctl is-active universal-agent-local-gateway.service'
# Templated worker instance (substitute the real @<id> from the discovery step):
ssh ua@uaonvps "systemctl is-active 'universal-agent-vp-worker@*.service'"
```

**Journald logs (bounded — always cap with `-n` or `--since` so you don't stream forever):**
```bash
ssh ua@uaonvps 'journalctl -u universal-agent-local-gateway.service -n 200 --no-pager'
ssh ua@uaonvps 'journalctl -u universal-agent-local-gateway.service --since "1 hour ago" --no-pager'
ssh ua@uaonvps 'journalctl -u universal-agent-local-gateway.service -p err -n 100 --no-pager'
# Worker logs (substitute the real @<id> instance):
ssh ua@uaonvps "journalctl -u 'universal-agent-vp-worker@*.service' -n 200 --no-pager"
```

**Disk / memory / processes (OOM, high load, out of space):**
```bash
ssh ua@uaonvps 'df -h /opt /'
ssh ua@uaonvps 'free -h'
ssh ua@uaonvps 'ps aux --sort=-%mem | head -15'
ssh ua@uaonvps 'uptime'                                   # load average
ssh ua@uaonvps 'journalctl -k --since "1 hour ago" --no-pager | grep -i oom'   # kernel OOM-killer
```

**Git state on prod (which commit is deployed / did the deploy land / dirty tree):**
```bash
ssh ua@uaonvps 'git -C /opt/universal_agent rev-parse HEAD'
ssh ua@uaonvps 'git -C /opt/universal_agent status -s'
ssh ua@uaonvps 'git -C /opt/universal_agent log --oneline -5'
```

**Remote venv sanity (confirm the interpreter and a package version):**
```bash
ssh ua@uaonvps '/opt/universal_agent/.venv/bin/python --version'
ssh ua@uaonvps '/opt/universal_agent/.venv/bin/python -c "import sys; print(sys.executable)"'
```

**Run an app module read-only (note `PYTHONPATH=src` from the deploy root):**
```bash
ssh ua@uaonvps 'cd /opt/universal_agent && PYTHONPATH=src .venv/bin/python -m <module> --help'
```

**Peek at a state DB read-only (never open it writable):**
```bash
ssh ua@uaonvps 'sqlite3 "file:/opt/universal_agent/activity_state.db?mode=ro" ".tables"'
ssh ua@uaonvps 'sqlite3 "file:/opt/universal_agent/csi.db?mode=ro" "select count(*) from sqlite_master;"'
```

## Sudo conventions

Most diagnostics need **no** sudo: a normal user can read `systemctl status`, `journalctl` for its own units, `df`, `free`, and `ps`. Reach for sudo only when a read is genuinely privileged.

- Prefer **`sudo -n`** (non-interactive) so the command fails fast instead of hanging on a password prompt over a non-interactive SSH session:
  ```bash
  ssh ua@uaonvps 'sudo -n journalctl -u universal-agent-local-gateway.service -n 200 --no-pager'
  ```
- If `sudo -n` returns `a password is required`, **stop** and hand it to the operator — do not try to type a password through the agent.
- Keep sudo scoped to reads (`journalctl`, `systemctl status`, `cat` of a root-owned log). A `sudo systemctl restart` is a mutation and is out of scope for this skill.

## Operator escape hatch (classifier blocks agent SSH)

The UA auto-mode classifier may **block the agent from running `ssh ua@uaonvps` directly**, even though the operator's settings allow it. When that happens, do not fight the classifier — hand the operator a self-contained probe to run with the `!` prefix (which executes as the operator, bypassing the classifier):

1. Write a small read-only probe script locally, e.g. `probe.sh` (note: it discovers the unit name first, then feeds it in):
   ```bash
   #!/usr/bin/env bash
   set -euo pipefail
   systemctl list-units --type=service | grep universal-agent
   systemctl is-active universal-agent-local-gateway.service || true
   journalctl -u universal-agent-local-gateway.service -n 50 --no-pager -p err
   df -h /opt
   free -h
   git -C /opt/universal_agent rev-parse HEAD
   ```
2. Ask the operator to pipe it over SSH from their shell:
   ```bash
   ! cat probe.sh | ssh ua@uaonvps bash
   ```
   The `!` prefix runs the line as the operator, so it isn't subject to the agent SSH block. `cat … | ssh … bash` streams the script to the remote shell with no file copied to prod.
3. Read the captured output the operator pastes back, then iterate on the probe — keep each probe read-only.

Batch your checks into one probe rather than asking the operator to run many separate SSH lines.

## When to use

- "Is prod up?" / "prod is down" / "check the prod gateway" / "is the vps healthy" → discover units, then status + logs pass.
- "Check the prod worker" / "is the worker unit active" / "tail the worker logs on prod" / "the worker unit is dead" → discover the templated `universal-agent-vp-worker@<id>.service` instance, then status + bounded logs.
- "Prod is throwing errors" / "prod 502s / 500s" / "the gateway is timing out" / "service crashed" / "the unit is dead/inactive/failed" → bounded `journalctl -p err` for the failing unit, then resources, then deployed commit.
- "Is the box out of disk/memory?" / "OOM / out of memory" / "prod is slow / high load" / "disk full / out of space" → `df -h`, `free -h`, `uptime`, `ps aux --sort=-%mem`, kernel OOM grep.
- "Did my deploy land?" / "is prod up to date" / "what version is running in prod" / "is prod on the latest commit" / "is there a dirty working tree on prod" / "what commit is prod on" → `git -C /opt/universal_agent rev-parse HEAD` + `git status -s`.
- "Check the remote venv / which python is prod using" → the absolute `/opt/universal_agent/.venv/bin/python`.
- "Check the prod database" / "count the rows in csi.db on prod" / "query activity_state.db read-only" / "inspect prod sqlite state" → open it `mode=ro`.

## When NOT to use

- Desktop git, branches, or worktrees → use `operations-worktree`. This skill is the **remote prod box**, not the local checkout.
- Local/dev DB work that never touches prod → no SSH needed; the `mode=ro` recipes here are for the prod state DBs only.
- Deploying, restarting units, editing remote files, or migrating a DB — those are mutations. This skill stops at the read-only diagnosis; the change is a separate operator-approved step.
- Local/dev troubleshooting that never touches `uaonvps` — no SSH needed.

## NEVER

- NEVER hardcode `ua-gateway`/`ua-worker` — those units do not exist. The prefix is `universal-agent-`; discover the real names with `systemctl list-units --type=service | grep universal-agent` before issuing any per-unit command.
- NEVER mutate prod under this skill: no `systemctl restart/stop`, no editing files under `/opt/universal_agent`, no `git pull`/`reset`, no DB writes. Read-only first, and the write is always a separate approved step.
- NEVER open a state DB writable. Always `file:...?mode=ro`; a stray write corrupts live prod state.
- NEVER invoke a bare `python`/`python3` on prod — always the absolute `/opt/universal_agent/.venv/bin/python`, or the wrong interpreter runs.
- NEVER stream unbounded `journalctl` (no `-f`, always `-n`/`--since`) — it can hang the SSH session and flood the transcript.
- NEVER try to type a sudo password through the agent. Use `sudo -n`; if it needs a password, hand it to the operator.
- NEVER fight the auto-mode classifier when agent SSH is blocked — give the operator a `! cat probe.sh | ssh ua@uaonvps bash` probe instead.
- NEVER actually SSH while *authoring or editing this skill* — the recipes here are documentation, validated by shape, not by live execution.
