# UA Workflow — One-Page Operator Index

> **Purpose:** the single page you read first when you sit down to work on this project. For deep-dives, every section links to the canonical doc.
>
> **Last updated:** 2026-05-07 (post-inversion + Phase D dev-tree workflow).

---

## 1 — Mental model in 60 seconds

UA runs on **`uaonvps`** (Hostinger VPS, accessed via Tailscale MagicDNS). Three Claude environments live there, all on the same machine:

| When | Endpoint | Models |
|---|---|---|
| Kevin coding interactively (Antigravity terminal, IDE side panel, plain `claude`) | **api.anthropic.com** | Claude Opus 4.7 / Sonnet 4.6 / Haiku — Max plan OAuth |
| `zai` shell function (explicit cheap-mode opt-in) | **api.z.ai** | GLM-5.x via ZAI proxy |
| UA autonomous services (Simone, Atlas, Cody normal work, ClaudeDevs cron, …) | **api.z.ai** | GLM-5.x via Infisical-injected env |
| Demo workspaces under `/opt/ua_demos/<id>/` | **api.anthropic.com** | Real Anthropic for new-feature demos |

Two physical machines:
- **`mint-desktop`** — your screen, keyboard, Antigravity IDE, Tailscale client.
- **`uaonvps`** — Linux VPS where everything actually runs (services, dev-tree, prod checkout, demo workspaces).

You connect mint-desktop → uaonvps via Antigravity Remote-SSH over Tailscale. Editor displays on desktop; everything else (workspace, terminal, Claude Code, git) runs on the VPS.

---

## 2 — Daily session start

```
1. Wake desktop (Tailscale auto-connects)
2. Open Antigravity
3. Remote Explorer → connect to ua@uaonvps
4. Open folder: /home/ua/dev/universal_agent
5. Ctrl+`  →  start coding
```

If `/home/ua/dev/universal_agent` doesn't exist yet, run the provisioning script once (this only needs to happen once, ever):

```
ssh root@uaonvps 'bash /opt/universal_agent/scripts/provision_vps_dev_tree.sh'
```

Full walkthrough + prerequisite checklist: [`docs/06_Deployment_And_Environments/11_Daily_Dev_Workflow.md`](06_Deployment_And_Environments/11_Daily_Dev_Workflow.md).

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
| `/ship` aborts with non-fast-forward | `git pull --rebase origin feature/latest2`, retry. Never `--force`. |
| UA gateway responding wrong endpoint | `ssh root@uaonvps 'sudo systemctl restart universal-agent-gateway'` |

Fuller diagnostic table: [doc 11 § Recovery procedures](06_Deployment_And_Environments/11_Daily_Dev_Workflow.md#recovery-procedures-when-things-break).

---

## 6 — Pointer table to deeper docs

| Topic | Doc |
|---|---|
| Daily workflow walkthrough (this doc, expanded) | [doc 11](06_Deployment_And_Environments/11_Daily_Dev_Workflow.md) |
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
