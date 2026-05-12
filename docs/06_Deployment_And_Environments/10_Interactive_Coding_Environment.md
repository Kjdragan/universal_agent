# 10. Interactive Coding Environment — Default-Anthropic Inversion

> **Status:** ✅ **EXECUTED, VERIFIED, AND HARDENED.** Phases A–C executed 2026-05-07 on VPS (`ua@uaonvps`) and desktop (`kjdragan@mint-desktop`); end-to-end routing confirmed via `/proc/<claude-pid>/environ` inspection. Phases D (Antigravity Remote-SSH) and Phase G (full 7-test acid suite) remain as optional polish. **2026-05-08 follow-up:** the canonical alias `claude=/opt/universal_agent/scripts/claude_with_mcp_env.sh` (introduced same day for MCP credential bootstrap) was silently re-injecting `ANTHROPIC_*` from Infisical and defeating the inversion; fixed with two-layer exclusion (`exclude_prefixes=("ANTHROPIC_",)` at load time + defense-in-depth strip in the launcher). Live re-verification on `ua@uaonvps` 2026-05-08 confirmed `Opus 4.7 · Claude Max` routing for interactive `claude`. Details in § "Related interactive-claude patterns" below.
>
> This is the canonical source-of-truth reference for how Kevin's interactive coding (Antigravity terminal, Antigravity IDE side panel, plain `claude` from any terminal) routes to **real Anthropic Max** while UA's autonomous agent runs continue to route to **ZAI / GLM**. Companion to [`09_Demo_Execution_Environments.md`](09_Demo_Execution_Environments.md).
>
> **Audience:** Operators, AI coders, future agents touching anything Claude-related on either machine.
> **Last updated:** 2026-05-08 (post-2026-05-08 launcher hardening; lessons-learned section added; § "Related interactive-claude patterns" rewritten).

## Lessons learned during execution (2026-05-07)

Three real issues surfaced during apply that future operators must know about. The committed scripts (`scripts/apply_phase_b_inversion.sh`) handle these correctly; this section documents the *why* so future agents don't re-discover them.

1. **`/proc/<pid>/environ` shows EXEC-time env, not runtime os.environ.** The original Phase B verify check tried to read `ANTHROPIC_BASE_URL` from `/proc/<gateway-pid>/environ` after restarting UA services, expecting to see the Infisical-injected value. It was always empty. Reason: UA Python services receive ZAI vars via `initialize_runtime_secrets()` injecting into `os.environ` *at runtime*; `/proc/environ` only reflects what was passed to `execve()`. **Fix in committed script:** B.2 now invokes `/opt/universal_agent/.venv/bin/python -c '...initialize_runtime_secrets()...; print(os.environ["ANTHROPIC_BASE_URL"])'` as a contract test instead. For the `claude` CLI subprocess (a node binary that doesn't mutate its own env at runtime), `/proc/environ` IS reliable — that's what the acid test uses.

2. **Infisical CLI is not installed by default on UA VPS.** UA's Python services use the SDK directly via `httpx` (see `infisical_loader.py:440`), so the CLI was never needed. The `zai()` shell wrapper shells out to `infisical run`, so the CLI must be present. **Fix in committed script:** B.3.5 detects and installs from the official deb repo (`https://artifacts-cli.infisical.com/setup.deb.sh`).

3. **`infisical run` requires either a CLI session OR explicit universal-auth — neither exists by default.** Kevin's desktop CLI had no `infisical login` session, so `infisical run --env=development -- claude ...` failed with "Failed to automatically trigger login flow." UA VPS has the same problem. **Fix in committed script:** the `zai()` function reads `INFISICAL_CLIENT_ID` / `INFISICAL_CLIENT_SECRET` from the bootstrap `.env` (`/opt/universal_agent/.env` on VPS, `~/lrepos/universal_agent/.env` on desktop), runs `infisical login --method=universal-auth --plain --silent` to get a token, then uses `INFISICAL_TOKEN=<token> infisical run ...`. Subshell isolation prevents creds leaking to the parent shell.

Bonus gotcha that bit us: **stale `~/.claude/.credentials.json`.** Kevin's desktop had OAuth credentials from Apr 30 that returned 401 from api.anthropic.com. Resolution: `cd ~ && claude /login` to refresh. Won't surface unless the inversion was previously incomplete and OAuth was never the active auth path.

---

## Context

### Why this change

UA's `~/.claude/settings.json` (on both `kjdragan@mint-desktop` and `ua@uaonvps`) carries an `env` block that maps Anthropic endpoints to the ZAI proxy and Anthropic model names to GLM models. This is correct for UA's autonomous in-process principals (Simone heartbeats, Atlas, ClaudeDevs cron — high-volume agentic inference where cheap GLM is the right answer), but it incorrectly routes **Kevin's interactive coding** through ZAI as well. *(Historical note: Cody's per-task CLI subprocesses were also in this "ZAI by default" bucket until the 2026-05-11 PM flip. As of `services/cody_mode.py:_HARDCODED_FALLBACK_MODE = "anthropic"`, Cody now defaults to Anthropic Max for every task — see § "The three modes" table further down for the post-flip topology.)*

Today, anytime Kevin opens the Antigravity terminal or the Claude Code IDE side panel, those `claude` invocations inherit the user-global `env` block and code through ZAI. That's the wrong default for interactive use — Kevin pays for an Anthropic Max plan precisely so his interactive coding gets real Claude (Opus 4.7).

### The constraint that drives the architecture

Kevin's ZAI coding plan is **single-concurrent-session**. A development environment running heartbeats/agents on his desktop while VPS production is also running them = double-billing the same plan. So Kevin can't have a fully-equipped local dev environment alongside the VPS prod environment. He has to develop *directly against the VPS*. That makes "code on VPS as if it's local" the actual workflow we need to enable, via Antigravity Remote-SSH.

### Desired end state

| Mode | Endpoint | Mechanism |
|---|---|---|
| Plain `claude` from any directory (desktop or VPS) | **Anthropic Max** | User-global settings.json no longer carries the ZAI env block; OAuth session resolves to api.anthropic.com |
| Antigravity IDE side panel + integrated terminal (Remote-SSH'd into VPS) | **Anthropic Max** | Same — extension runs on VPS, picks up VPS user-global settings (now vanilla for env) |
| Explicit `zai` shell function | **ZAI / GLM proxy** | Wraps with `infisical run --env=… -- claude "$@"`; values come from Infisical |
| UA systemd-supervised services (Simone/Atlas/Cody-normal/cron) | **ZAI / GLM proxy** | Mechanism changes: ZAI vars now injected by `initialize_runtime_secrets()` from Infisical at process startup; behavior unchanged |
| Demo workspaces under `/opt/ua_demos/<id>/` | **Anthropic Max** | Unchanged — existing project-local vanilla settings + launcher unset already work |

### Why Infisical, not `.env`

`/opt/universal_agent/.env` is bootstrap-only (Infisical creds + machine identity + ports) and is regenerated on every push to `main` by `.github/workflows/deploy.yml`. Per `docs/deployment/secrets_and_environments.md`, runtime secrets MUST flow through Infisical. The 5 ZAI keys are runtime configuration that the UA Python services consume, so they belong in Infisical `production` (and `development` for desktop use).

### Key facts established by exploration

1. **Infisical injection is automatic on service restart.** UA Python services start by reading bootstrap `.env`, then call `initialize_runtime_secrets()` at `src/universal_agent/infisical_loader.py:440`, which fetches every Infisical secret and injects them into `os.environ` (with `overwrite=False`). Subprocesses, including `claude` CLI invocations spawned from those services, inherit that env. **Adding the 5 ZAI keys to Infisical = they appear in every UA Python service's env on next restart, with zero code changes.**
2. **VPS user is `ua`**, not root. UA services run as `User=ua`. So the user-global settings to invert is `/home/ua/.claude/settings.json`, and the Max plan OAuth session must be set up under `/home/ua/.claude/`.
3. **Web UI is irrelevant** — it does deploy-time render of `web-ui/.env.local` and doesn't make LLM calls from Node.
4. **No hardcoded ZAI endpoints in runtime code.** `src/universal_agent/utils/model_resolution.py` reads from env vars; `ZAI_MODEL_MAP` at lines 30-34 is a fallback default, not a hardcoded route.
5. **Vanilla template canonical at** `src/universal_agent/templates/_smoke_demo/.claude/settings.json` (8 lines). Demo provisioner enforces structural rules: no `env`, no `hooks`, no `enabledPlugins`, no `extraKnownMarketplaces`. We will NOT use this template for the user-global swap (per surgical-strip choice below); it's referenced only for the demo path which stays unchanged.
6. **Antigravity Remote-SSH** inherits VS Code's model: editor local, workspace + terminal + side-panel extension run on the remote (VPS) side. Bidirectional SSHFS mount (`kjdragan@<tailscale-ip>:/home/kjdragan` → `/home/ua/`-side path) is already documented.

### Design choices (confirmed)

- **Surgical strip** — remove ONLY the 5 `ANTHROPIC_*` keys from the user-global settings.json env block. Preserve hooks (`agent-flow`), plugins, statusline (`omc-hud`), marketplaces, `model: "opus[1m]"`, and the `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` / `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` feature flags. Lowest blast radius.
- **Path 2 (sandbox-Claude SSH bootstrap) deferred** — Phase E creates the `bootstrap_vps_access.sh` script but execution waits until after Phases A–D are verified. Single source of truth in this plan; no execution dependency.

---

## Phase A — Stage ZAI keys in Infisical

**Goal.** Put the 5 ZAI keys in Infisical so `initialize_runtime_secrets()` injects them into UA service env on next restart.

**Stage in both environments:**
- `production` — UA services on VPS read here.
- `development` — Kevin's desktop `zai()` wrapper reads here.

**Reuse:** `scripts/infisical_upsert_secret.py` (idempotent helper).

**Commands** (run from any machine with the Infisical CLI authenticated):

```bash
PROJECT_ID=9970e5b7-d48a-4ed8-a8af-43e923e67572
for ENV in production development; do
  python scripts/infisical_upsert_secret.py --env "$ENV" --project "$PROJECT_ID" \
    --key ANTHROPIC_BASE_URL --value 'https://api.z.ai/api/anthropic'
  python scripts/infisical_upsert_secret.py --env "$ENV" --project "$PROJECT_ID" \
    --key ANTHROPIC_AUTH_TOKEN --value 'd747f216d4434754af9ff1672ecf261f.ISELcv9prVdsewyj'
  python scripts/infisical_upsert_secret.py --env "$ENV" --project "$PROJECT_ID" \
    --key ANTHROPIC_DEFAULT_HAIKU_MODEL  --value 'glm-5-turbo'
  python scripts/infisical_upsert_secret.py --env "$ENV" --project "$PROJECT_ID" \
    --key ANTHROPIC_DEFAULT_SONNET_MODEL --value 'glm-5-turbo'
  python scripts/infisical_upsert_secret.py --env "$ENV" --project "$PROJECT_ID" \
    --key ANTHROPIC_DEFAULT_OPUS_MODEL   --value 'glm-5.1'
done
```

**Verify (read-only):**

```bash
infisical secrets --env=production --projectId=$PROJECT_ID --plain | \
  grep -E '^ANTHROPIC_(BASE_URL|AUTH_TOKEN|DEFAULT_)'
```

Expect 5 hits per env.

**Failure modes:** auth scope error → run from VPS as `ua`; helper missing → fall back to `infisical secrets set KEY=VAL --env=$ENV --projectId=$PROJECT_ID`.

---

## Phase B — VPS-side inversion (`/home/ua/`)

**Order matters:** restart services *before* stripping the env block, so they pick up the Infisical-injected ZAI vars first.

### B.1 — Verify Max plan OAuth session for `ua`

```bash
ssh ua@uaonvps 'ls -la /home/ua/.claude/ 2>/dev/null | grep -iE "credential|auth|session"'
```

If output is empty / no credential file present, run interactively from the demo workspace (which has vanilla project-local settings, so `claude /login` lands creds in the right place):

```bash
ssh -t ua@uaonvps 'cd /opt/ua_demos/_smoke && claude /login'
```

Manual / browser-OAuth — cannot be automated.

### B.2 — Restart UA services so they pick up Infisical-injected ZAI

```bash
ssh ua@uaonvps 'sudo systemctl list-units --type=service --state=running | \
  grep -E "universal-agent-|csi-|ua-"'
ssh ua@uaonvps 'sudo systemctl restart universal-agent-gateway universal-agent-api \
  universal-agent-telegram universal-agent-webui'
# Plus any vp-worker@N, csi-* worker units that are active.
```

**Verify env injection per process** — pick one running service:

```bash
ssh ua@uaonvps 'PID=$(pgrep -u ua -f universal_agent.gateway_server | head -1); \
  sudo tr "\\0" "\\n" < /proc/$PID/environ | grep -E "^ANTHROPIC_(BASE_URL|AUTH_TOKEN|DEFAULT_)"'
```

Expect 5 vars present, BASE_URL = `https://api.z.ai/api/anthropic`. **If empty: stop. Diagnose before proceeding** — that service didn't go through `initialize_runtime_secrets()` and stripping settings.json would route it to Anthropic.

### B.3 — Surgical strip of `/home/ua/.claude/settings.json`

**Backup first:**

```bash
ssh ua@uaonvps 'cp /home/ua/.claude/settings.json \
  /home/ua/.claude/settings.json.preinversion.$(date +%Y%m%d-%H%M%S).bak'
```

**Edit:** remove ONLY these 5 keys from the `env` block, keep everything else (hooks, plugins, statusline, model, marketplaces, CLAUDE_CODE_* feature flags, TELEGRAM_*, GITHUB_*):

- `ANTHROPIC_BASE_URL`
- `ANTHROPIC_AUTH_TOKEN`
- `ANTHROPIC_DEFAULT_HAIKU_MODEL`
- `ANTHROPIC_DEFAULT_SONNET_MODEL`
- `ANTHROPIC_DEFAULT_OPUS_MODEL`

If after removal the `env` block is empty or only contains keys like `API_TIMEOUT_MS`, leave the block intact with whatever non-Anthropic keys remain. Do NOT delete the entire `env` key just because it's smaller.

### B.4 — Add `zai()` shell function for `ua`

Append to `/home/ua/.bashrc`:

```bash
# --- ZAI explicit-opt-in wrapper (added per Interactive Coding Environment plan) ---
# Default `claude` now hits Anthropic Max. Use `zai` when you want cheap GLM inference.
export INFISICAL_PROJECT_ID="${INFISICAL_PROJECT_ID:-9970e5b7-d48a-4ed8-a8af-43e923e67572}"
zai() {
  infisical run --env=production --projectId="$INFISICAL_PROJECT_ID" --silent -- \
    claude "$@"
}
```

Use the `--` passthrough form (verified in `docs/deployment/secrets_and_environments.md` line 28). Avoids `--command=...` quoting hazards.

### B.5 — Failure modes

| Symptom | Likely cause | Detection |
|---|---|---|
| UA service still hits Anthropic after strip | Service didn't ingest Infisical | `tr '\0' '\n' < /proc/$pid/environ \| grep BASE_URL` |
| Plain `claude` still hits ZAI | Strip incomplete, or shell has leaked vars | `env \| grep ANTHROPIC_` should be empty in fresh login shell |
| `claude` reports not logged in | OAuth missing | Re-run B.1 |
| `zai` works but plain `claude` also goes ZAI | Env keys still in settings.json | `cat /home/ua/.claude/settings.json` |

---

## Phase C — Desktop-side inversion (`kjdragan@mint-desktop`)

Mirror of B.3 + B.4. UA services don't run on desktop, so B.2 doesn't apply. The `zai()` function on desktop pins to `--env=development` instead of `production` (desktop's Infisical machine identity is dev-scoped).

**Files modified:**
- `~/.claude/settings.json` — surgical strip of the same 5 keys.
- `~/.bashrc` (or `~/.zshrc` if `echo $SHELL` shows zsh) — append `zai()` block, `--env=development`.

**Pre-checks:**
- `claude /login` already done on desktop (likely yes — Kevin uses Antigravity terminal there).
- `which infisical` returns a real binary.

**Side benefit:** Antigravity *desktop-local* terminal also flips to Anthropic Max. Antigravity Remote-SSH terminals into VPS are governed by Phase B.

---

## Phase D — Antigravity Remote-SSH bring-up

### Pre-conditions

1. Tailscale up on both ends. Reuse `scripts/tailscale_vps_preflight.sh` for VPS-side validation.
2. `ssh ua@uaonvps 'echo ok'` returns `ok`.
3. `~/.ssh/config` on desktop has:
   ```
   Host uaonvps
     HostName uaonvps
     User ua
     IdentityFile ~/.ssh/id_ed25519
     ServerAliveInterval 30
     ServerAliveCountMax 3
   ```
4. Antigravity Remote-SSH extension installed (manual, GUI).
5. Phase B complete on VPS — otherwise remote terminals open `claude` via ZAI again.

### First-time connect (manual, GUI)

1. Antigravity → Remote Explorer → SSH Targets → connect to `uaonvps`.
2. Open a workspace folder on the VPS (e.g., `/opt/universal_agent/` for ops work, or any project under `/home/ua/`).
3. Antigravity will prompt to install the Claude Code extension on the remote host — accept. The extension now runs *on the VPS*.
4. Open integrated terminal — confirm `whoami` returns `ua`, `pwd` is on VPS.

### Acid test (run while Phase B is in effect)

Two-terminal verification:

- **T1** (Antigravity remote integrated terminal): `claude -p "say hi"`
- **T2** (separate `ssh ua@uaonvps` from desktop): `ss -t state established | grep -E 'anthropic|z\.ai'` while T1 is mid-call.
- Expect: connection to `api.anthropic.com:443`, **none** to `api.z.ai`.

Inverse test:

- **T1**: `zai -p "say hi"`
- **T2**: same sniffer.
- Expect: `api.z.ai:443`, none to anthropic.

Side-panel test: send a small prompt via the Claude Code side panel inside Antigravity. Same sniffer in T2. Expect: anthropic.

### Failure modes

- Side-panel uses ZAI → extension was installed locally on desktop instead of remote host. Reinstall in Remote-SSH host scope.
- Tailscale ACL blocks SSH → see ACL/SSH troubleshooting in `docs/03_Operations/87_Tailscale_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`.
- SSHFS mount stale → `fusermount -u <mountpoint>; remount`.

---

## Phase E — Sandbox-Claude bootstrap (deferred)

Write the script during this work. **Do not execute end-to-end until Phases A–D are verified.**

**File created:** `scripts/bootstrap_vps_access.sh` (executable, bash).

**Inputs (pasted into sandbox-Claude prompt at session start, NEVER committed to repo):**
1. `TS_AUTHKEY` — Tailscale ephemeral auth key (one-time, rotates per session).
2. `INFISICAL_TOKEN` — Infisical service token, scoped to `production` read.
3. `INFISICAL_PROJECT_ID` — `9970e5b7-d48a-4ed8-a8af-43e923e67572`.

**Script behavior (high-level):**
1. Refuse to run outside an obvious sandbox (heuristic: hostname `vm`, `$HOME=/root`, no persistent dotfiles).
2. `apt-get install -y openssh-client curl jq`.
3. Install Tailscale via `curl -fsSL https://tailscale.com/install.sh | sh`. Start `tailscaled --tun=userspace-networking --socks5-server=localhost:1055 &`. Run `tailscale up --authkey="$TS_AUTHKEY" --hostname="sandbox-claude-$(date +%s)" --ephemeral --ssh`.
4. Install Infisical CLI (reuse pattern from `scripts/install_vps_infisical_sdk.sh`).
5. Authenticate Infisical via `INFISICAL_TOKEN` env (no `infisical login` required for service tokens).
6. Connect to VPS via `tailscale ssh ua@uaonvps` (preferred — leverages tailnet ACL) or fall back to key-based SSH if Kevin supplies a key.
7. Print success banner with VPS hostname + summary of running UA services.

**.gitignore addition:** ensure no credential files committed (e.g., `scripts/.bootstrap_vps_access.env*`).

---

## Phase F — Documentation

**File created:**
- `docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md` — canonical reference. Mirrors this plan's structure (context, per-machine matrix, phase-by-phase mechanism, acid tests, rollback). Cross-links to:
  - `09_Demo_Execution_Environments.md` for the demo path.
  - `docs/deployment/secrets_and_environments.md` for Infisical commands.
  - `docs/03_Operations/87_Tailscale_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md` for tailnet/SSH.

**Files modified:**
- `docs/06_Deployment_And_Environments/09_Demo_Execution_Environments.md` — add a new section "Interactive coding vs demo execution: how they coexist after the inversion." Update the diagram around line 106 to clarify the env block is now service-side only via Infisical, not user-global. Update the `unset` workaround note (line 171) to mark obsolete for interactive use.
- `docs/deployment/secrets_and_environments.md` — add the 5 `ANTHROPIC_*` keys to the secret inventory with source = "ZAI proxy", consumers = "UA Python services + interactive `zai()` wrapper".
- `CLAUDE.md` — add a one-paragraph pointer at the top: "Before touching anything Claude-related on either machine, read `docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md` first."
- `docs/06_Deployment_And_Environments/README.md` if it has an index — add doc 10.

---

## Phase G — Acid tests + rollback

### End-to-end acid tests (run after B + C complete; before declaring done)

1. **VPS interactive Anthropic.** T1: `ssh ua@uaonvps 'cd ~ && claude -p "1+1"'`. T2: `ss -t state established | grep -E 'anthropic|z\.ai'`. Expect: anthropic only.
2. **VPS UA service ZAI.** Trigger any UA agent task (Simone heartbeat / cron). T2 watches. Expect: z.ai only. Cross-check: `tr '\0' '\n' < /proc/<pid>/environ | grep BASE_URL` returns z.ai URL.
3. **VPS demo workspace Anthropic.** `cd /opt/ua_demos/_smoke && python smoke.py`. Expect: `endpoint == api.anthropic.com`, exit 0. Regression-checks the demo path wasn't broken.
4. **Desktop interactive Anthropic.** Same as test 1 but on `mint-desktop`.
5. **Desktop `zai()` ZAI.** T1: `zai -p "1+1"`. T2 sniff. Expect: z.ai.
6. **Antigravity Remote-SSH integrated terminal Anthropic.** Same as test 1 but inside Antigravity's remote terminal.
7. **Antigravity side panel Anthropic.** Send a prompt via side panel; T2 sniff. Expect: anthropic.

### Rollback

If any "should-be-Anthropic" test routes to ZAI and a fast revert is needed:

1. Restore the env block: `cp /home/ua/.claude/settings.json.preinversion.<ts>.bak /home/ua/.claude/settings.json` (and same on desktop). System returns to pre-inversion all-ZAI default for interactive use; demos still work; services now consume Infisical-injected vars (which are identical to what settings.json would have set), so behavior is safe.
2. **Do NOT delete Infisical keys yet** — services depend on them. They're additive at this point.
3. Full unwind (only if abandoning the architecture): delete the 5 keys from both Infisical envs and restart services.

### Failure-mode → diagnosis cheat sheet

| Test fails | Most likely cause | First check |
|---|---|---|
| 1 / 4 / 6 / 7 hits ZAI | env keys still in user-global settings.json, or shell exports leaking | `grep -E 'ANTHROPIC_(BASE_URL\|AUTH_TOKEN)' ~/.claude/settings.json`; `env \| grep ANTHROPIC_` |
| 2 hits Anthropic | UA service didn't get Infisical injection | Check service unit invokes the Python entrypoint that calls `initialize_runtime_secrets()` |
| 3 fails with `endpoint_mismatch` | Demo provisioner regression | Re-validate vanilla project-local settings per `provision_smoke_workspace()` |
| 5 hits Anthropic | `zai()` function definition wrong, or Infisical CLI not authenticated | `type zai`; `infisical secrets --env=development --projectId=$INFISICAL_PROJECT_ID --plain` |

---

## Critical files

**Existing files referenced (read-only or carefully edited):**
- `/home/user/universal_agent/scripts/infisical_upsert_secret.py` — Phase A.
- `/home/user/universal_agent/src/universal_agent/templates/_smoke_demo/.claude/settings.json` — referenced for demo path; not used for user-global swap (surgical strip).
- `/home/user/universal_agent/src/universal_agent/infisical_loader.py:440` — the `initialize_runtime_secrets()` contract that makes Phase B.2 work.
- `/home/user/universal_agent/scripts/tailscale_vps_preflight.sh` — Phase D pre-condition validation.
- `/home/user/universal_agent/scripts/install_vps_infisical_sdk.sh` — install pattern reference for Phase E.
- `/home/user/universal_agent/docs/deployment/secrets_and_environments.md` — Phase A commands canonical; Phase F update target.
- `/home/user/universal_agent/docs/06_Deployment_And_Environments/09_Demo_Execution_Environments.md` — Phase F update target.
- `/home/user/universal_agent/CLAUDE.md` — Phase F pointer addition.

**New files to be created:**
- `/home/user/universal_agent/docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md` — canonical doc.
- `/home/user/universal_agent/scripts/bootstrap_vps_access.sh` — Phase E (deferred execution).

**Files modified outside the repo (on machines, not git-tracked):**
- `/home/ua/.claude/settings.json` (VPS) — surgical strip.
- `/home/ua/.bashrc` (VPS) — append `zai()` block.
- `/home/kjdragan/.claude/settings.json` (desktop) — surgical strip.
- `/home/kjdragan/.bashrc` or `.zshrc` (desktop) — append `zai()` block.

---

## Verification (the single most important section)

After all phases complete, run all 7 acid tests in Phase G. **Do not declare done until every one passes.** The two-terminal `ss` sniffer pattern is the only bulletproof verification — `claude config list` and similar self-reports can lie if env vars override settings.

---

## Related interactive-claude patterns (different concerns, same machine)

The phases above govern **model routing** for interactive `claude` sessions
(Anthropic-Max default, `zai()` opt-in). A separate but adjacent concern is
**MCP server credentials** — the tokens that `.mcp.json` references via
`${VAR}` placeholders (AgentMail, Discord, Hostinger, etc.). Those are
populated by a different launcher (`scripts/claude_with_mcp_env.sh`) which
runs UA's `initialize_runtime_secrets()` before exec'ing `claude`.

The two launchers serve different purposes and (after the 2026-05-08 hardening) coexist cleanly:

| Launcher | Purpose | When to use | Interaction with the other |
|---|---|---|---|
| `zai()` shell function (this doc) | Force `claude` to route LLM calls through the ZAI proxy instead of Anthropic Max for explicit cheap inference | Operator wants GLM models for one specific session | Independent. `zai` uses `infisical run`, doesn't go through `claude_with_mcp_env.sh`, and only sets `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` for the duration of the `claude` invocation. |
| `scripts/claude_with_mcp_env.sh` | Populate MCP server credentials (`AGENTMAIL_API_KEY`, `DISCORD_BOT_TOKEN`, `HOSTINGER_API_TOKEN`) so `${VAR}` placeholders in `.mcp.json` resolve | Default everywhere — alias `claude` to it in shell rc | Excludes `ANTHROPIC_*` at the Infisical inject step (`exclude_prefixes=("ANTHROPIC_",)`) plus a defense-in-depth strip post-bootstrap, so OAuth → Anthropic Max is the resolved auth path. MCP creds flow unchanged. See note below. |

> **⚠️ 2026-05-08 correction.** The original "No conflict" claim for
> `claude_with_mcp_env.sh` was wrong. The launcher calls
> `initialize_runtime_secrets()`, which fetches **every** Infisical secret onto
> `os.environ`. Phase A staged the 5 `ANTHROPIC_*` ZAI routing vars in
> Infisical so UA Python services would pick them up — but UA also keeps
> `ANTHROPIC_API_KEY` there for direct-SDK code paths
> (`refinement_agent`, `gateway_server` vision endpoint, `proactive_signals`,
> etc.). The first-pass fix only stripped the 5 routing vars; it left
> `ANTHROPIC_API_KEY` intact, which Claude Code interpreted as an
> "external API key" and rejected with `Invalid API key · Fix external API key`
> when the staged key was for a different account / had no Max billing.
>
> **Fix shipped 2026-05-08 (revised):** the strip now covers the entire
> `ANTHROPIC_*` namespace, in two layers:
>
> 1. **Load-time filter.** `initialize_runtime_secrets()` accepts an
>    `exclude_prefixes` parameter; the launcher passes
>    `exclude_prefixes=("ANTHROPIC_",)` so those keys never enter
>    `os.environ` from Infisical in the first place. UA Python services that
>    need them call the same function without the parameter and get all
>    secrets normally.
> 2. **Defense-in-depth strip.** After the bootstrap, the launcher also
>    strips any `ANTHROPIC_*` key that landed on `os.environ` from a
>    non-Infisical source (bootstrap `.env`, parent shell leak).
>
> Net effect: interactive `claude` falls through to Anthropic Max OAuth via
> `~/.claude/.credentials.json`. MCP credentials flow unchanged. UA Python
> services unaffected. The `zai` shell function uses `infisical run` (not
> this launcher) and remains the explicit ZAI opt-in.
>
> **Operator action required after deploy:** if `~/.claude/.credentials.json`
> for `ua` is stale or absent, the first `claude` call will fail with a
> not-logged-in error. Refresh OAuth from a vanilla project-local context:
> `cd /opt/ua_demos/_smoke && claude /login`.
>
> Regression guards: `tests/unit/test_claude_launcher_strip.py` covers both
> layers (load-time exclude + defense-in-depth strip). Functional smoke:
> after the OAuth refresh, run `claude -p "say PASS"` and confirm the model
> replies; for protocol-level certainty use the `ss -t state established`
> sniffer pattern from § "Phase G acid tests" above.

For the canonical reference on MCP credentials and the launcher's design (the
"`infisical run` CLI was the wrong primitive" lesson, the auto-resolution
anti-pattern, the operator alias setup), see
[`docs/deployment/secrets_and_environments.md` § MCP Server Credentials](../deployment/secrets_and_environments.md#mcp-server-credentials-mcpjson-placeholders).

If you ever need *both* — interactive ZAI routing AND populated MCP creds in
the same session — compose them: alias `claude` to the MCP launcher (the
default), and define `zai()` to call the MCP launcher with the ZAI env vars
overlaid. (Not a current need; flagged here so a future operator doesn't
re-derive the relationship from scratch.)

---

## Out of scope (explicit non-goals; track as separate work)

1. Migrating the remaining non-Anthropic secrets currently in user-global settings.json env blocks (`TELEGRAM_BOT_TOKEN`, `GITHUB_PERSONAL_ACCESS_TOKEN`) to Infisical. Those don't affect ZAI routing; they're a separate hygiene improvement.
2. Replacing user-global settings.json with the full vanilla template (drops hooks/plugins/statusline). Decided against — surgical strip preserves Kevin's IDE setup.
3. Phase E *execution* (sandbox-Claude bootstrap end-to-end). Script is written in this work; first real bootstrap happens in a later session when Kevin pastes credentials.
4. Auditing every CSI / discord / vp-worker systemd unit for whether it goes through `initialize_runtime_secrets()`. Phase 1 audit confirmed the Python services that matter do; non-Python or template-only units (telegram, docs, youtube tunnel) don't make LLM calls so are exempt.

---

## Tool Surface by Execution Mode

> **Why this exists.** Universal Agent runs three distinct Claude execution profiles (autonomous / interactive / demo). The model behind each profile is different (GLM-5.1 via Z.ai vs Claude Opus 4.7 via Anthropic Max), and the *appropriate* tool surface is different too. This section is the canonical reference for "which tool should this agent be using?" Future agents and human operators consult it before assuming a tool is available — both to avoid wasted attempts (an Anthropic-side session calling a ZAI MCP burns ZAI quota for an Anthropic-side use; an autonomous-mode call to `WebSearch` hits the disallow list) and to keep tool-use reliability high (GLM-5.1 is trained on ZAI MCPs; Anthropic models are trained on their built-ins).
>
> Added 2026-05-09 as part of the [Hacker News Phase 2 plan](../integrations/hackernews_phase2_plan.md) when registering `web-reader` and `web-search-prime` ZAI MCPs in `.mcp.json`.

### The three modes

| Mode | What runs in it | cwd | Endpoint | Model |
|---|---|---|---|---|
| **Autonomous** | Simone heartbeats, Atlas (`vp.general.primary`), ClaudeDevs cron, briefings, dispatch sweeps, scheduled VP missions | `/opt/universal_agent/` | `https://api.z.ai/api/anthropic` | GLM-5.1 (mapped to `opus`) / GLM-5-turbo (mapped to `sonnet`/`haiku`) |
| **Interactive** | Kevin's `claude` from any terminal (Antigravity, Remote-SSH, plain shell). Alias `claude` → `scripts/claude_with_mcp_env.sh`, which auto-injects `--dangerously-skip-permissions` for sessions (and skips it for management subcommands like `claude agents` / `auth` / `doctor`). The new `claude agents` (v2.1.139+, 2026-05-11) is the **Agent View** session-roster manager — not a different coding interface; the wrapper passes it through cleanly because its flag surface is just `--setting-sources` + `-h`. | typically `/opt/universal_agent/` (VPS) or `~/lrepos/universal_agent/` (desktop) | `https://api.anthropic.com` | Claude Opus 4.7 / Sonnet 4.6 / Haiku 4.5 (Max plan OAuth) |
| **Cody (per-task CLI subprocess)** | Every Cody task — BOTH in-environment work AND demo workspaces. Spawned by `vp/clients/claude_cli_client.py` per mission. Mode resolved from `cody_mode` field (per-task → DB setting → `UA_CODY_DEFAULT_MODE` env → hardcoded `"anthropic"`, see `services/cody_mode.py:34-37`). Flipped from `"zai"` default to `"anthropic"` 2026-05-11 PM per operator decision. | varies (`/opt/ua_demos/<id>/` for demos; mission workspace for in-env work) | `https://api.anthropic.com` (when `cody_mode="anthropic"`); ZAI proxy (when `cody_mode="zai"`) | Claude Max plan OAuth by default; GLM via ZAI only when explicitly overridden |
| **Demo workspace settings** | Vanilla `.claude/settings.json` in `templates/ua_demos_scaffold/` (deployed to `/opt/ua_demos/<id>/.claude/settings.json`). Second layer of defense on top of Cody's per-mission `ANTHROPIC_*` env scrub. | `/opt/ua_demos/<id>/` | `https://api.anthropic.com` | Claude Max plan OAuth (vanilla, no ZAI mapping) |

### MCP visibility per mode

`/opt/universal_agent/.mcp.json` is project-scope — visible to any `claude` invoked with cwd inside the project. Demo workspaces under `/opt/ua_demos/<id>/` have their own settings and **do not see** the project `.mcp.json`. So:

| Mode | Sees `/opt/universal_agent/.mcp.json`? |
|---|---|
| Autonomous | YES (intended — Atlas/Simone/etc. need MCPs registered there) |
| Interactive (cwd inside project) | YES (file is in scope; documented convention is to not over-use ZAI MCPs from Anthropic-side sessions) |
| Interactive (cwd outside project, e.g. `~`, `/tmp`) | NO (only user-scope `~/.claude.json` MCPs apply) |
| Demo | NO (cwd is `/opt/ua_demos/<id>/`, outside project) |

### Tool surface — what's appropriate per mode

| Need | Autonomous (ZAI / GLM) | Interactive (Anthropic) | Demo (Anthropic) |
|---|---|---|---|
| **Search the web** | `webSearchPrime` MCP (ZAI) | `WebSearch` (Claude built-in)¹ | `WebSearch` |
| **Fetch & extract a URL** | `webReader` MCP (ZAI) | `WebFetch` (Claude built-in) | `WebFetch` |
| **Vision / OCR / image analysis** | `zai-mcp-server` Vision MCP (when registered) | Direct image input to Claude | Direct image input |
| **Bash, Read, Write, Edit, Grep, Glob** | All available (Claude Agent SDK runtime) | All available | All available |
| **File-system I/O within project tree** | Always allowed | Always allowed | Always allowed |
| **MCPs in project `.mcp.json`** (agentation, AgentMail, arxiv, hostinger, discord, discord-intelligence-bridge) | All available | All available — same MCPs, same credentials | NOT visible (cwd outside project) |

¹ `WebSearch` is on UA's `DISALLOWED_TOOLS` list at [`agent_core.py:78`](../../src/universal_agent/agent_core.py#L78) — that disallow applies to the autonomous Claude Agent SDK runtime specifically. Kevin's interactive `claude` sessions are not gated by `DISALLOWED_TOOLS` (which is a UA-internal config, not a Claude Code config), so `WebSearch` works there normally.

### Decision rule

Before reaching for a tool inside an autonomous prompt or sub-agent definition:

1. **Is this code path running under autonomous mode on ZAI?** (heartbeats, scheduled cron, VP missions, Simone/Atlas, ClaudeDevs intel cron) → **Use ZAI MCPs.** `webReader` for URL extraction, `webSearchPrime` for general search, `zai-mcp-server` for vision. The model behind these (GLM-5.1) was trained on these tool schemas.
2. **Is this Kevin's interactive coding session?** → **Use Anthropic-native tools.** `WebFetch` for URL extraction, `WebSearch` for search. Even though the project `.mcp.json` makes ZAI MCPs callable from this session, doing so burns ZAI quota for an Anthropic-side use. Avoid unless you specifically need the ZAI surface (e.g., testing a ZAI MCP's behavior).
3. **Is this a Cody per-task CLI subprocess?** → **Check `cody_mode` first.** If `cody_mode="anthropic"` (the default since 2026-05-11 PM), use Anthropic-native tools — the spawned `claude` subprocess is on Max plan OAuth. If `cody_mode="zai"` (explicit override), use ZAI MCPs.
4. **Is this a demo workspace prompt?** → **Use Anthropic-native tools.** Demos run on Anthropic Max plan OAuth and don't see project MCPs by design — the demo environment is intentionally vanilla.

### Quota accounting

ZAI MCPs draw from your GLM Coding Plan quota:

| Plan tier | Combined web-search + web-reader + vision per month |
|---|---|
| Lite | 100 |
| Pro | 1,000 |
| **Max** | **4,000** |

Lane A's HN briefing typically uses ~5-10 `webReader` calls per day = ~150-300/month. Comfortable on Max; tight on Lite. If you migrate to a smaller plan, add a quota guardrail in the helper before each `webReader` call.

### What this section does NOT enforce

This is a documented convention, not a runtime gate. We can't prevent a script in `/opt/universal_agent/` from invoking `webReader` while running under Anthropic auth — the file-scope of `.mcp.json` makes the tool callable, and we have no per-mode tool-allowlist mechanism in Claude Code today. The `_comment_zai_scope` block in `.mcp.json` flags the convention; this doc is the authoritative reference; CLAUDE.md's Pre-Implementation Reading matrix points future agents here.

If a future agent needs to be more restrictive (e.g., enforce that demo missions can't call ZAI MCPs even if the file were visible), the right hook is a Claude Code permissions config in the demo's `.claude/settings.json` — not a change to `.mcp.json` or this doc.
