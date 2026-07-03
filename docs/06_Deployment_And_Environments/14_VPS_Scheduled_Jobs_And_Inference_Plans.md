# VPS Operations Reference — Scheduling & Inference Plans

> How `uaonvps` runs recurring jobs and how ALL projects route AI inference across
> our subscription plans. Compiled 2026-07-03 from live inspection; companion to
> [`10_Interactive_Coding_Environment.md`](10_Interactive_Coding_Environment.md)
> (the two-backend inversion) and
> [`13_Claude_Max_OAuth_Credentials.md`](13_Claude_Max_OAuth_Credentials.md).
> Rendered/browsable copy:
> https://uaonvps.taildcc090.ts.net/scratch/vps-ops-reference/vps-ops-reference.html
> A general-use sibling copy lives at `demo_factory/VPS_OPERATIONS_REFERENCE.md`.
> The job inventory below is a dated snapshot — refresh with the commands in §7.

## 0. The three rules

1. **Never pay raw Anthropic API prices.** All inference runs on a subscription:
   **Anthropic Max** for interactive/quality work, **ZAI/GLM** (near-unlimited
   flat coding plan) for high-volume, autonomous, and background work.
2. **Recurring jobs = systemd timers on the always-on VPS.** Not Claude Routines
   (the cloud sandbox clones the repo fresh — no local venv, no private plugins,
   no working email send), and not the in-app `/schedule`/`CronCreate` (session-only,
   dies with the session, 7-day cap).
3. **Background/scheduled inference routes through GLM** so it stays off the Max
   usage limits, which are preserved for interactive coding.

## 1. The two-backend inference model

Both machines (`ua@uaonvps`, `kjdragan@mint-desktop`) run the same
"default-Anthropic inversion": plain `claude` is real Anthropic Max; ZAI/GLM is
an explicit opt-in.

| Path | Backend | Mechanism | Cost basis |
|---|---|---|---|
| plain/aliased `claude` (any terminal) | **Anthropic Max** | alias → `claude_with_mcp_env.sh` → OAuth to api.anthropic.com (no base-URL override) | Max plan usage limits |
| `zai <args>` shell function | **ZAI / GLM** | same `claude` binary wrapped in `infisical run --env=production`, which injects `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` (GLM proxy) | Flat ZAI coding plan |
| UA autonomous services (Simone/Atlas/Cody, all timers) | **ZAI / GLM** | `initialize_runtime_secrets()` injects the ZAI vars into `os.environ` at process start; spawned `claude` inherits | Flat ZAI coding plan |
| Your own Agent-SDK / Anthropic-SDK Python | **ZAI / GLM** | `lab_common.zai.configure_zai_inference()` after `load_secrets()` → base URL + bearer + `opus/sonnet/haiku → glm-5.2 / glm-5-turbo / glm-4.5-air` | Flat ZAI coding plan |

**Constraints & facts:**

- The ZAI coding plan is **single-concurrent-session** — don't run ZAI-routed
  agents on desktop *and* VPS at once (double-bills the one plan). The VPS is the
  runtime; the desktop is dev.
- Routing config lives in Infisical `production`
  (project `9970e5b7-d48a-4ed8-a8af-43e923e67572`); no ZAI endpoints are hardcoded.
- The Agent SDK's printed `cost_usd` is an **equivalent-cost meter** (API list
  price of the tokens), not a bill — under Max or ZAI it is subscription-covered.
- Every `/dragan:new-repo` scaffold ships `lab_common/zai.py`, so new projects
  have `configure_zai_inference()` from birth.

## 2. Scheduling decision table

| Approach | Session-free? | Reaches our venv / plugins / secrets? | Inference cost | Verdict |
|---|---|---|---|---|
| **VPS systemd timer + GLM** | ✅ always-on box | ✅ full local access | ZAI flat plan | **Use this** |
| VPS systemd timer + Max | ✅ | ✅ | Max limits | Only when Claude-grade quality is needed |
| Desktop cron | ✅ (OS timer) | ✅ | Max (desktop default) | OK, but the desktop must be on |
| Claude Routines (cloud preview) | ✅ | ❌ fresh sandbox clone | subscription | Wrong for local-dependency jobs |
| In-app `/schedule` / `CronCreate` | ❌ dies with the session | — | — | Session-only; never for durable jobs |
| Raw Anthropic API | — | — | $ per token | Never — we have plans |

## 3. How to add a scheduled job

### 3a. UA app job → SYSTEM timer (deploy pipeline)

Two **static** files committed to `/opt/universal_agent/deployment/systemd/`
(`<name>.timer` + `<name>.service`); `scripts/deploy/remote_deploy.sh` rsyncs
them into `/etc/systemd/system/` and runs `daemon-reload` + `enable --now`.
House style (identical across all ~40 UA timers):

```ini
[Timer]
OnCalendar=<spec> America/Chicago   ; explicit TZ — the box is UTC
Persistent=true                     ; replay a slot missed during deploy/reboot
RandomizedDelaySec=120
Unit=<name>.service

[Service]
Type=oneshot
User=ua
WorkingDirectory=/opt/universal_agent
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=/opt/universal_agent/src
Environment=UA_DEPLOYMENT_PROFILE=vps
Environment=UA_INFISICAL_ENABLED=1
Environment=INFISICAL_ENVIRONMENT=production
EnvironmentFile=-/opt/universal_agent/.env
ExecStart=/opt/universal_agent/.venv/bin/python -m universal_agent.scripts.<module> [args]
```

Secrets are **never inlined** — the Python entrypoint bootstraps Infisical at
runtime from the machine identity in `/opt/universal_agent/.env`. Secret-needing
units are annotated `SECRET-BEARING:` in their `Description=`. Some jobs are
code-gated to an active window unless a 24/7 override env var is set
(`UA_INTEL_DIGEST_24_7`, `UA_CSI_CONVERGENCE_SYNC_24_7`).

### 3b. Personal / standalone-repo job → USER timer

For a job that isn't part of the UA app (a demo repo, a personal digest), use a
**user timer** in `~/.config/systemd/user/` — no root; user `ua` has
`Linger=yes`, so it fires with no session. This is what `ua-deslop-remediation`
and `daily-eight-brief` use.

```ini
# ~/.config/systemd/user/<name>.timer
[Timer]
OnCalendar=*-*-* 10:00:00 America/Chicago
Persistent=true
[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now <name>.timer
```

Bootstrap ZAI/secrets inside the wrapper script the way the `zai` shell function
does — machine identity from `/opt/universal_agent/.env`, then re-exec under
Infisical:

```bash
TOK=$(infisical login --method=universal-auth \
        --client-id="$INFISICAL_CLIENT_ID" --client-secret="$INFISICAL_CLIENT_SECRET" \
        --plain --silent)
exec env INFISICAL_TOKEN="$TOK" infisical run \
  --env=production --projectId=9970e5b7-d48a-4ed8-a8af-43e923e67572 --silent -- <your command>
```

A cloned repo has **no `.env`** (gitignored) — this bootstrap, or
`lab_common.secrets.load_secrets()`, is how it gets creds.

### 3c. Unattended email

**AgentMail**, not the Gmail connector (which has *no send tool*):
`demo_factory/scripts/email_send.py --subject … --text … [--to …]` —
AgentMail key auto-loads from Infisical; GWS CLI is the fallback.

## 4. Job inventory (snapshot 2026-07-03) — 45 custom recurring jobs

32 `universal-agent-*` system timers + 7 `csi-*` + `mirror-health` + user timers
+ crontabs + cron.d. Box TZ is UTC; UA timers pin `America/Chicago` (CT below);
CSI/infra/crontab run UTC. All `universal-agent-*`: `Type=oneshot`, `User=ua`,
`ExecStart=/opt/universal_agent/.venv/bin/python -m <module>`.

**Briefings / proactive intelligence (14):** morning-briefing 06:30 CT ·
evening-briefing 18:00 · proactive-report morning/midday/afternoon 07:05/12:05/16:05
(3 timers → one service) · hourly-intel-digest hourly :00 (window-gated) ·
proactive-signal-card-sync hourly :25 · proactive-artifact-digest 08:35 ·
csi-convergence-sync hourly :00 · intel-auto-promoter 10:35 & 15:35 ·
csi-demo-triage-rank 10:05 & 15:05 (LLM) · backlog-triage 08:30 (email → Kevin) ·
nightly-wiki 03:15 · artifact-reminders-sweep every 30 min 06:00–21:30.

**Demo factory (2):** proactive-demo-build-sweep 08:30/13:30/18:30 CT ·
proactive-demo-nuggets 23:50 CT (EOD judge + land).

**Health / watchdogs (4, root):** service-watchdog every 30 s ·
proactive-health every 10 min · oom-alert minutely · youtube-oauth-watchdog 07:00 CT.

**YouTube (2):** gold-channel-poller 05:30 CT · daily-digest 06:00 CT.

**Maintenance / GC (7):** session-reaper 03:00 · scratch-pruning 07:00 ·
cron-workspace-pruning 17:12 · vp-coder-regenerable-reap 06:25 ·
vp-coder-workspace-pruning Sun 17:05 · uv-cache-prune 09:30 UTC ·
codie-proactive-cleanup 01:30.

**Quality / knowledge (3):** architecture-canvas-drift Mon 06:30 ·
skill-gap-finder every 5 days 09:00 · vault-lint-contradictions monthly 1st 07:00.

**CSI Ingester (7, root, own venv, UTC):** daily-summary 00:10 · db-backup 03:40 ·
replay-dlq every 4h · rss-semantic-enrich every 4h · threads-semantic-enrich
every 4h · threads-token-refresh-sync 03:15 · youtube-transcript-canary hourly :17.
Units under `/opt/universal_agent/CSI_Ingester/development/deployment/systemd/`;
DB `/var/lib/universal-agent/csi/csi.db`.

**Other:** mirror-health every 30 min (Tome libgen self-healer) ·
**daily-eight-brief** user timer 10:00 CT (**the daily AI brief — GLM, AgentMail**) ·
ua-deslop-remediation user timer every 6h :15 CT · crontab: umami backup 02:00 UTC,
clearspring-studio pull 09:17 UTC · cron.d: docker-image-prune 00:41 UTC,
monarx-update Tue 11:10 UTC.

## 5. Directories & docs map

- `/opt/universal_agent` — production UA runtime: `.venv`, `.env` (Infisical
  machine identity), `deployment/systemd/` (unit source), ExecStart targets.
- `/opt/universal_agent/CSI_Ingester/development` — CSI subsystem (own venv/env/timers).
- `/home/ua/lrepos/*` — dev/demo repos (`demo_factory`, `claude_science`,
  `demo-autonomous-research-loop`, `clearspring-studio`, …).
- `/home/ua/dev/universal_agent` — dev checkout (not the runtime).
- Canonical docs: `universal_agent/docs/06_Deployment_And_Environments/` —
  esp. `10_Interactive_Coding_Environment.md` (two-backend model) and
  `13_Claude_Max_OAuth_Credentials.md`.

## 6. Worked example — the daily-eight brief

The template for "recurring GLM job that emails you"
(repo: `demo-autonomous-research-loop`; skill: `/dragan:daily-eight-research-loop`):

- Repo cloned + `uv sync` at `/home/ua/lrepos/demo-autonomous-research-loop`.
- Wrapper `scripts/daily_brief_cron.sh`: self-bootstraps Infisical (§3b), sets
  `DEMO_ROUTE_ZAI=1 DEMO_MODEL=sonnet` (→ glm-5-turbo), runs the research loop,
  emails via `email_send.py`.
- User timer `daily-eight-brief.timer`: `OnCalendar=*-*-* 10:00:00 America/Chicago`,
  `Persistent=true`, enabled.
- Validated 2026-07-03: full 8-entry run hit `api.z.ai`, `DEMO_SELFCHECK: PASS`,
  `EMAIL_SEND: OK http200`.

## 7. Reaching the box / refreshing this inventory

- `ssh uaonvps` (user `ua`), and **always** wrap remote commands in
  `bash -lc "…"` — the login shell puts `~/.local/bin` (`claude` v2.1.x, `uv`)
  on PATH; `infisical` is `/usr/bin`.
- Refresh the inventory:
  `systemctl list-timers --all` · `systemctl --user list-timers --all` ·
  `crontab -l` · `ls /etc/cron.d/` — then `systemctl cat <unit>` for details.
