# UA Workflow — One-Page Operator Index

> **Purpose:** the single page you read first when you sit down to work on this project. For deep-dives, every section links to the canonical doc.
>
> **Last updated:** 2026-05-13 (retired `feature/latest2`; `claudereal` now auto-lands new sessions on `main` after PR auto-merge — see [doc 04 § Session Baseline Cleanup](06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md#session-baseline-cleanup)).

---

## 1 — Mental model in 60 seconds

Two physical machines, two distinct roles:

| Machine | Role | What runs there |
|---|---|---|
| **`mint-desktop`** (your screen + keyboard) | **Local dev — on-demand.** | `just dev` runs gateway/api/web-ui locally. Autonomous loops (heartbeat, cron, dispatch sweep, AgentMail polling, etc.) are **off**. Zero ZAI quota burn at idle. You spin it up when working, kill it when done. |
| **`uaonvps`** (Hostinger VPS via Tailscale MagicDNS) | **Production — always-on.** | The deployed gateway, full autonomous loop fleet, all the Claude environments below. Every merge to `main` deploys here via GitHub Actions. |

Three Claude environments still live on the VPS (production-side; for the canonical reference see [doc 10](06_Deployment_And_Environments/10_Interactive_Coding_Environment.md)):

| When | Endpoint | Models |
|---|---|---|
| Kevin coding interactively on VPS (when away from desktop) — Antigravity terminal, IDE side panel, `claude` (aliased to `scripts/claude_with_mcp_env.sh`, which auto-injects `--dangerously-skip-permissions` for sessions) | **api.anthropic.com** | Claude Opus 4.7 / Sonnet 4.6 / Haiku — Max plan OAuth |
| `zai` shell function (explicit cheap-mode opt-in) | **api.z.ai** | GLM-5.x via ZAI proxy |
| UA autonomous services (Simone, Atlas, ClaudeDevs cron, dispatch sweep, briefings, …) | **api.z.ai** | GLM-5.x via Infisical-injected env |
| Cody per-task CLI subprocess (default since 2026-05-11 PM, applies to in-env work AND demos) | **api.anthropic.com** | Max plan OAuth (per-task `cody_mode` field can flip to `"zai"`) |
| Demo workspaces under `/opt/ua_demos/<id>/` (vanilla `.claude/settings.json` layer) | **api.anthropic.com** | Real Anthropic for new-feature demos |

---

## 2 — Daily session start (canonical: desktop-local)

```bash
cd /home/kjdragan/lrepos/universal_agent
git fetch origin && git pull --ff-only origin main
uv sync
just dev
```

`just dev` boots gateway (`:8002`) + API (`:8001`) + web-ui Next.js (`:3000`) with prefixed output. Ctrl-C tears down the whole tree. All autonomous loops are off (no heartbeat firing, no cron ticking, no real emails). To opt a specific loop in for testing, set `UA_DEV_<NAME>_FORCE_ON=1` in `.env` and restart.

Full walkthrough + prerequisites + troubleshooting: [`docs/06_Deployment_And_Environments/12_Local_Dev_Environment.md`](06_Deployment_And_Environments/12_Local_Dev_Environment.md) **(canonical)**.

### Inspection helpers (from anywhere)

```bash
PYTHONPATH=src python -m universal_agent.dev_tools env-report      # per-loop dev decisions
PYTHONPATH=src python -m universal_agent.dev_tools loop-status heartbeat
PYTHONPATH=src python -m universal_agent.dev_tools cron-list       # persisted cron jobs
python scripts/snapshot_prod_to_dev.py --dry-run                   # pull prod data preview
```

### Fallback: VPS-as-dev via Antigravity Remote-SSH

If desktop dev isn't available (away from desktop, hardware issue, etc.), the older VPS-as-dev path still works:

```
1. Wake desktop (Tailscale auto-connects)
2. Open Antigravity → Remote Explorer → connect to ua@uaonvps
3. Open folder: /home/ua/dev/universal_agent
4. Ctrl+` → start coding
```

If `/home/ua/dev/universal_agent` doesn't exist: `ssh root@uaonvps 'bash /opt/universal_agent/scripts/provision_vps_dev_tree.sh'`. Fallback walkthrough: [`docs/06_Deployment_And_Environments/11_Daily_Dev_Workflow.md`](06_Deployment_And_Environments/11_Daily_Dev_Workflow.md).

---

## 3 — Coding flow

| You want | Run |
|---|---|
| Real Claude (Anthropic Max) — code/debug/refactor | `claude -p "..."` (or use the Antigravity side-panel) |
| Cheap GLM (ZAI) — bulk / experimental token-burns | `zai -p "..."` |

Plain `claude` defaults to Anthropic. Use `zai` only when you actively want to save Max plan tokens.

---

## 4 — Shipping

```
/ship     # from inside Claude Code (any clone with the right git remote)
```

What it does (post-2026-05-10 redesign — PR-only-to-`main`):
1. Pre-flight syntax/lint checks on changed `.py` files
2. Auto-commits any pending changes on your feature branch
3. Pushes the branch to origin
4. Opens a PR to `main` via `gh pr create` (or prints the PR-create URL if `gh` isn't installed)
5. Watches `pr-validate.yml` CI and reports green/red
6. Operator clicks Merge in GitHub UI; the merge to `main` triggers `.github/workflows/deploy.yml`

Works from desktop, VPS dev-tree, or anywhere with the right git remote. `gh` CLI is optional but recommended (without it, `/ship` prints the PR URL for you to open in browser). Defined at [`.claude/commands/ship.md`](../.claude/commands/ship.md). The `develop` branch was retired 2026-05-10 — see [`docs/06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md`](06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md).

---

## 5 — Common breakages and 1-line fixes

| Symptom | First-try fix |
|---|---|
| Antigravity can't reach uaonvps | `tailscale ping uaonvps` from desktop; if fail, `sudo tailscale up` |
| `claude` returns 401 | `cd ~ && claude /login` (browser flow refreshes OAuth) |
| `zai` says "no infisical login session" | `cat /home/ua/dev/universal_agent/.env \| grep INFISICAL_CLIENT_ID` — should not be empty |
| Side panel uses ZAI / wrong endpoint | Reinstall Claude Code extension on the **remote** host (not local) |
| `/ship` aborts with non-fast-forward | `git pull --rebase origin main`, retry. Never `--force`. |
| UA gateway responding wrong endpoint | `ssh root@uaonvps 'sudo systemctl restart universal-agent-gateway'` |

Fuller diagnostic table: [doc 11 § Recovery procedures](06_Deployment_And_Environments/11_Daily_Dev_Workflow.md#recovery-procedures-when-things-break).

---

## 6 — Pointer table to deeper docs

| Topic | Doc |
|---|---|
| **Canonical desktop-local dev runbook (`just dev`)** | [**doc 12**](06_Deployment_And_Environments/12_Local_Dev_Environment.md) |
| **Running Claude Code in local dev — cheat sheet** | [**development/CLAUDE_CODE_CHEAT_SHEET.md**](development/CLAUDE_CODE_CHEAT_SHEET.md) |
| Infisical dev-env hygiene (optional cleanup) | [doc 13](06_Deployment_And_Environments/13_Infisical_Dev_Env_Hygiene.md) |
| Local runtime lane definitions (HQ Dev / Desktop Worker) | [doc 05](06_Deployment_And_Environments/05_Local_Runtime_Modes.md) |
| VPS-as-dev fallback workflow (Antigravity Remote-SSH) | [doc 11](06_Deployment_And_Environments/11_Daily_Dev_Workflow.md) |
| Why interactive `claude` defaults to Anthropic Max — full mechanism, lessons learned | [doc 10](06_Deployment_And_Environments/10_Interactive_Coding_Environment.md) |
| Demo workspace mechanics, OAuth wrinkles | [doc 09](06_Deployment_And_Environments/09_Demo_Execution_Environments.md) |
| Demo provisioning runbook (one-time setup) | [demo_workspace_provisioning.md](operations/demo_workspace_provisioning.md) |
| Tailscale architecture + SSH ACLs | [doc 87](03_Operations/87_Tailscale_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md) |
| Infisical secret model + 5 ANTHROPIC_* keys | [secrets_and_environments.md](deployment/secrets_and_environments.md) + [doc 85](03_Operations/85_Infisical_Secrets_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md) |
| Infisical CLI usage including universal-auth bootstrap | [doc 97](03_Operations/97_Infisical_CLI_Reference_And_Lessons_Learned_2026-03-14.md) |
| Git branching + AI coder responsibilities | [ai_coder_instructions.md](deployment/ai_coder_instructions.md) |
| CI/CD pipeline (deploy.yml internals) | [ci_cd_pipeline.md](deployment/ci_cd_pipeline.md) |
| `/ship` slash command source | [.claude/commands/ship.md](../.claude/commands/ship.md) |
| Phase B inversion migration script | [scripts/apply_phase_b_inversion.sh](../scripts/apply_phase_b_inversion.sh) |
| Phase D dev-tree provisioning script | [scripts/provision_vps_dev_tree.sh](../scripts/provision_vps_dev_tree.sh) |

---

## Verification checklist (after any major change to env / settings / Infisical)

- [ ] Plain `claude -p "OK"` from VPS terminal → returns text; `/proc/<pid>/environ` shows **no** `ANTHROPIC_BASE_URL`.
- [ ] `zai -p "OK"` from VPS terminal → returns text; `/proc/<pid>/environ` shows `ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic`.
- [ ] Antigravity Claude Code side panel (Remote-SSH'd) responds to a test prompt.
- [ ] Plain `claude` from desktop → returns text; no `ANTHROPIC_BASE_URL` in process env.
- [ ] `zai` from desktop → returns text; ZAI URL in process env.
- [ ] `cd /opt/ua_demos/_smoke && python smoke.py` → exit 0, endpoint = api.anthropic.com.
- [ ] Trivial doc commit + `/ship` → CI/CD green; `/opt/universal_agent` HEAD updated; services restarted.
- [ ] `curl https://api.clearspringcg.com/api/v1/health` returns `{"status":"healthy"}`.
