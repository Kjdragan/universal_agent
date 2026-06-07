---
title: "Agent Runbook: Reaching the Production VPS (read live state without fighting SSH)"
status: active
canonical: true
subsystem: plat-agent-access
code_paths:
  - src/universal_agent/gateway_server.py
  - src/universal_agent/api/routers/
  - infrastructure/tailscale/tailnet-policy.hujson
  - infrastructure/tailscale/device_roles.json
  - scripts/tailscale_vps_preflight.sh
last_verified: 2026-06-07
---

# Agent Runbook: Reaching the Production VPS

> **Why this doc exists.** Agents (Claude Code, Codex, etc.) running from a dev session
> repeatedly waste time trying to `ssh ua@uaonvps`, getting denied by the harness
> permission layer, and concluding "I can't reach prod." **That conclusion is wrong.**
> The tailnet already gives you a low-privilege, read-only HTTP window into live prod —
> the gateway API on `:8002` — that answers *most* "what is the system actually doing
> right now?" questions **with no SSH and no permission grant at all.** Reach for that
> first. This runbook is the canonical "how do I, as an agent, observe/operate prod"
> reference. Infrastructure (Tailscale ACLs, SSHFS, proxy, nginx) lives in its sibling
> [`06_networking_tailscale_proxy_sshfs.md`](06_networking_tailscale_proxy_sshfs.md).

> **STATUS (updated 2026-06-07).** The one-time operator allow-rule for shell access **has
> been added** (`Bash(ssh ua@uaonvps:*)`, 2026-06-06). **SSH from an agent now works** —
> `ssh ua@uaonvps "<cmd>"` (with `dangerouslyDisableSandbox: true` for egress) is a live
> channel for `journalctl`/`systemctl`/`sqlite3`. The "agents cannot self-grant" *principle*
> in §3 still holds (only the operator adds the rule), but it is no longer *pending* — Channel 3
> is available today. Still prefer Channel 1 (the read-API) for anything it can answer.

## TL;DR — pick the channel by what you need

```
Need to … ?                                            → Use …
─────────────────────────────────────────────────────────────────────────────────
READ live prod state (cron jobs/runs, CSI health,      → Gateway HTTP API over tailnet
  Mission Control cards/tiles, heartbeat, version)        http://uaonvps:8002/...   (no SSH, no grant)
SEE a rendered HTML artifact (findings/report)         → WebFetch the tailnet /scratch URL
  someone published                                       https://uaonvps.taildcc090.ts.net/scratch/...
RUN a shell command (sqlite3 on the 2.5 GB DBs,        → ssh ua@uaonvps  OR  tailscale ssh ua@uaonvps
  journalctl, systemctl, grep a unit file)               (needs a one-time operator-added allow-rule — see §3)
MUTATE prod                                             → Never directly. PR → deploy only.
```

**The single most important habit:** before you SSH, ask "can the gateway API answer
this?" For cron state, CSI/HN liveness, Mission Control, heartbeat, deploy SHA — it can.

## The box

| Fact | Value |
|---|---|
| MagicDNS name | `uaonvps` (resolves only on a tailnet member with MagicDNS) |
| FQDN | `uaonvps.taildcc090.ts.net` |
| Tailnet IP | `100.106.113.93` |
| Raw provider hostname (ACL/tag key) | `srv1360701` (Hostinger) — see networking doc §1.1 |
| This agent's host | `mint-desktop` (`tag:operator-workstation`) |
| Quick reachability check | `tailscale status \| grep uaonvps` → expect `active; direct` |

The Tailscale ACL **already** grants `tag:operator-workstation` → `tag:vps` SSH as users
`root`/`ua` (networking doc §1.2). So at the *network* layer you can already reach the
box; the only thing that blocks `ssh ua@uaonvps` from an agent is the **harness
permission layer** (§3), not the tailnet.

## 1. Channel 1 — Gateway read-API over the tailnet (PREFERRED, no SSH)

The gateway process serves a large read-API (≈296 routes) on **plain HTTP, port 8002**,
bound on the tailnet interface. From any tailnet member:

```bash
curl -s http://uaonvps:8002/api/v1/version
# {"commit_sha":"9ace1fe5...","short_sha":"9ace1fe5","branch":"...","repo_root":"/opt/universal_agent",...}
```

- **Plain `http://`, not `https://`.** `https://...:8002` fails TLS — `:8002` is not a TLS port.
- **Deploy truth = `commit_sha`**, *not* the `branch` field (branch is a stale/misleading
  artifact of how the deploy checks out — it routinely shows an unrelated branch name).
- **From a sandboxed Bash tool you need `dangerouslyDisableSandbox: true`** on the call.
  The harness *classifier* allows the GET (it is a benign read); the *sandbox* blocks
  network egress. These are two different gates — only the sandbox one applies here, and
  the flag clears it. (No permission allow-rule is needed for these curls.)

### 1.1 The endpoints that answer real questions (all 200, no auth)

| Question | Endpoint |
|---|---|
| What commit is live? | `GET /api/v1/version` → `commit_sha` |
| Cron jobs: enabled? last/next run? (system jobs come back **hashed**; real name is in `metadata.system_job`) | `GET /api/v1/cron/jobs` |
| A specific job's run history (status, error, output_preview) | `GET /api/v1/cron/jobs/{job_id}/runs` |
| Recent cron runs across jobs | `GET /api/v1/cron/runs` |
| Mission Control evidence cards / tiles / durable ledger | `GET /api/v1/dashboard/mission-control/{cards,tiles,ledger}` |
| CSI per-source liveness (last_seen, events_last_6h/48h, lag, failures) | `GET /api/v1/dashboard/csi/health` |
| CSI delivery health / SLO / digests | `GET /api/v1/dashboard/csi/{delivery-health,reliability-slo,digests}` |
| HackerNews snapshot freshness | `GET /api/v1/hackernews/health` |
| Overall health / last heartbeat tick | `GET /api/v1/health`, `GET /api/v1/heartbeat/last` |

Enumerate everything with `curl -s http://uaonvps:8002/openapi.json | jq '.paths|keys'`.

### 1.2 What this channel canNOT give you

- **`/api/v1/ops/*` returns `401`** (e.g. `ops/proactive_health`, `ops/system-health`).
  These need `UA_OPS_TOKEN` (Infisical, `production`). Skip unless you have the token.
- **Ports other than :8002.** `:8001` (`universal-agent-api`) is a *different* service
  (its `/api/v1/version` 404s — don't use it for deploy checks). `:3000` (Next.js webui)
  is **not** bound on the tailnet (`curl http://uaonvps:3000` → connection refused);
  reach the dashboard via the public nginx vhost or `tailscale serve`, not direct.
- **Raw DB rows / journald / unit files.** Anything below the API surface needs a shell (§3).

## 2. Channel 2 — Published HTML artifacts (`/scratch`)

Reports/findings rendered as HTML are published to a tailnet-served dir and reachable by
URL — `https://uaonvps.taildcc090.ts.net/scratch/<token>/<name>.html`. The **`WebFetch`
tool reaches these directly** (auto-HTTPS via the `ts.net` cert; tailnet membership is the
auth). This is how you read a prior session's findings report. Publishing pattern + serve
config: networking doc §1.6.

## 3. Channel 3 — Shell via SSH / Tailscale SSH (operator grant IS IN PLACE — see §3.1)

When you genuinely need a shell (sqlite3 on `activity_state.db`/`csi.db`, `journalctl -u
universal-agent-*`, `systemctl status`, reading a unit file), use SSH over the tailnet.
**As of 2026-06-06 the allow-rule exists, so this channel is live — just use it.**

### 3.1 The grant (in place since 2026-06-06)

- By default the harness **auto-mode classifier denies** `ssh ua@uaonvps`:
  *"no visible user authorization naming this prod/shared target."*
- **The operator has since added the allow-rule** (`Bash(ssh ua@uaonvps:*)` +
  `Bash(tailscale ssh ua@uaonvps:*)`, 2026-06-06), so an agent can SSH today. Invoke it with
  **no leading flags** so the prefix matcher hits (see §3.2), plus `dangerouslyDisableSandbox`.
- **An agent still cannot grant *itself* the exception** — editing `~/.claude/settings.json`
  to add an allow-rule is blocked as *self-modification* ("the user asked for X, not
  permission grants"). That principle is unchanged; it is simply no longer *pending* here,
  because the operator already added this specific rule. Do not try to add new ones yourself.

### 3.2 The one-time operator action

Add to `~/.claude/settings.json` → `permissions.allow` (operator runs this, not the agent):

```jsonc
"Bash(ssh ua@uaonvps:*)",
"Bash(tailscale ssh ua@uaonvps:*)"
```

Then, from an agent, invoke **without leading flags** so the prefix matcher hits — i.e.
`ssh ua@uaonvps "<remote cmd>"`, *not* `ssh -o ConnectTimeout=10 ua@uaonvps ...` (the
leading `-o` makes the command no longer start with `ssh ua@uaonvps` and the rule misses).
Network egress still needs `dangerouslyDisableSandbox: true`. Prefer `tailscale ssh`
(ACL-gated at the tailnet layer, operator-controlled) over raw key SSH.

### 3.3 Shell discipline (non-negotiable)

- **READ/probe freely.** `sqlite3 'file:/path?mode=ro' "..."`, `journalctl`, `systemctl status`.
- **Open the 2.5 GB canonical DB read-only:** `sqlite3 'file:/opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db?mode=ro'`.
- **MUTATE prod only via PR → deploy.** Snapshot before any delete. The deploy pipeline
  `git reset`s `/opt/universal_agent`; a hand-edit on the box is drift that will be lost
  (and has caused outages — see networking doc §4).

### 3.4 Key live paths (for shell probes)

| Path | What |
|---|---|
| `/opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db` | 2.5 GB canonical Task Hub (read `?mode=ro`) |
| `…/AGENT_RUN_WORKSPACES/{runtime_state.db,cron_runs.jsonl,cron_jobs.json}` | runtime DB + cron ledgers |
| `/var/lib/universal-agent/csi/csi.db` | CSI events DB (`events` table; `source`, `occurred_at`) |
| `mission_control_intelligence.db` | Mission Control card store |
| `/etc/systemd/system/universal-agent-*` | the migrated job timers/services |

## 4. Worked example — triaging Mission Control with zero SSH (2026-06-06)

A session was asked to triage 3 "chronic infrastructure" Mission Control items. SSH was
denied by the classifier. Instead of stalling, it used **Channel 1 only** and fully
resolved all three:

- `GET /api/v1/version` → confirmed live HEAD `commit_sha` (matched the audited worktree).
- `GET /api/v1/cron/jobs` (+ `metadata.system_job`) → confirmed `hourly_intel_digest`
  in-process row `enabled=false` (migrated to a timer) and `paper_to_podcast_daily`
  `enabled=true`; `GET …/{id}/runs` → paper_to_podcast's last 8 runs all `success`
  (so the "failed/parked" readout was stale, not a live failure).
- `GET /api/v1/dashboard/csi/health` → HackerNews **alive** (133 events/48h, 45/6h,
  status `ok`), disproving the "HN source dead / 26h stale" card.
- `GET /api/v1/dashboard/mission-control/{cards,tiles}` → only 4 live cards, none matching
  the 3 flagged items; gateway + heartbeat tiles **green**.

Lesson: **the API channel is usually sufficient.** SSH would only have added raw
`csi.db`/`journalctl` confirmation of details the API already evidenced.

## 5. Gotchas (all verified 2026-06-06)

- **Don't conclude "can't reach prod" from an SSH denial.** Try `curl http://uaonvps:8002/api/v1/version` first.
- **`commit_sha`, not `branch`,** is the deploy source of truth from `/api/v1/version`.
- **`:8002` is plain HTTP.** `https://…:8002` fails TLS.
- **Sandboxed Bash needs `dangerouslyDisableSandbox: true`** for any tailnet egress (curl/ssh) — this is the *sandbox* gate, separate from the *classifier* gate.
- **System cron `job_id`s come back hashed** (e.g. `013f433539`); the real name is `metadata.system_job`. Map by that, not by id.
- **`/api/v1/ops/*` = 401** without `UA_OPS_TOKEN`.
- **Agents cannot self-grant SSH** (the principle) — but the operator **already added** the `permissions.allow` rule (2026-06-06), so SSH is live now (§3.1/§3.2). Invoke SSH with no leading flags so the rule matches.
- **`uaonvps` requires MagicDNS;** off-tailnet it won't resolve (raw name `srv1360701`).
