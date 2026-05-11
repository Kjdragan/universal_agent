# 11. Daily Dev Workflow — VPS-as-Dev via Antigravity Remote-SSH (Fallback)

> **⚠️ SUPERSEDED 2026-05-11.** The canonical local-dev runbook is now [`12_Local_Dev_Environment.md`](12_Local_Dev_Environment.md). Development happens on **Kevin's desktop** via `just dev` (on-demand, autonomous loops off, no ZAI quota burn, no collision with prod state). The VPS is production-only.
>
> **This doc is retained as a FALLBACK** for cases where desktop dev isn't available (e.g., away from desktop, traveling, desktop hardware issue). It describes the Antigravity Remote-SSH workflow that was the canonical path between 2026-05-07 (Default-Anthropic inversion) and 2026-05-11 (dev/prod separation initiative). The mechanics below still work — they're just no longer the primary path.
>
> If you're a new operator or fresh AI session: **read Doc 12 first.** Use this doc only if Doc 12's `just dev` path isn't an option in your current situation.

---

> **Audience:** Kevin (primary), and any future agent or operator who needs to understand how UA development happened pre-2026-05-11. This is the **idiot-proof beginning-to-end guide for the VPS-as-dev fallback path.** For the canonical post-2026-05-11 path, see [`12_Local_Dev_Environment.md`](12_Local_Dev_Environment.md). For the one-page TL;DR see [`docs/WORKFLOW.md`](../WORKFLOW.md). For the deep-dive on *why* Claude routes the way it does, see [`10_Interactive_Coding_Environment.md`](10_Interactive_Coding_Environment.md).
>
> **Status:** **Fallback workflow** (post-2026-05-11). Was canonical between 2026-05-07 (Default-Anthropic inversion) and 2026-05-11 (dev/prod separation initiative). The previous "develop on desktop, push to VPS" model from before 2026-05-07 is obsolete (see § Why the workflow changed).

---

## 60-second mental model

You sit at `mint-desktop`. You open Antigravity. Antigravity Remote-SSH's you into `ua@uaonvps`. The IDE editor lives on your desktop screen, but the workspace, terminal, Claude Code side-panel extension, file system, and `claude` binary all live on the VPS. You edit `/home/ua/dev/universal_agent/...` (a clean dev-tree, separate from prod). Your local terminal shell on the VPS has `claude` (Anthropic Max) and `zai` (cheap GLM proxy) commands. You commit and run `/ship` from the same Antigravity terminal. CI/CD takes over from there and deploys to `/opt/universal_agent` on the same VPS.

```
┌─────────────────────────┐      Antigravity Remote-SSH      ┌─────────────────────────────────┐
│ mint-desktop (display)  │  ──── over Tailscale (uaonvps) ──→ │ uaonvps                          │
│  • Antigravity IDE      │                                    │  • /home/ua/dev/universal_agent │  ← you edit here
│  • SSH client            │                                    │  • Claude Code extension (remote)│
│  • Anthropic Max OAuth   │                                    │  • claude (Max), zai (GLM)       │
│  • /ship from desktop OK │                                    │  • git push origin feature/lat..│
└─────────────────────────┘                                    │                                  │
                                                               │  CI/CD ↓ (GH Actions on push to  │
                                                               │  main, separate from your edit)  │
                                                               │                                  │
                                                               │  /opt/universal_agent ← prod     │
                                                               │  (clobbered every deploy; never  │
                                                               │  edit here directly)             │
                                                               └──────────────────────────────────┘
```

**Three Claude environments, all on the same VPS:**
| Where | Routing | Use |
|---|---|---|
| Plain `claude` from any VPS terminal (or Antigravity remote terminal/side-panel) | Anthropic Max (your subscription) | Interactive coding |
| `zai` shell function | ZAI / GLM via Infisical | Cheap inference, ad-hoc experiments |
| UA systemd services + demo workspaces under `/opt/ua_demos/` | ZAI for services, Anthropic for demos | Don't touch — they self-route correctly |

---

## Why the workflow changed (skip if you know this)

Pre-2026-05-07 you developed on `mint-desktop` and shipped to the VPS. Two things made that increasingly painful:

1. **The ZAI coding plan is single-concurrent-session.** A fully-equipped local dev environment running heartbeats/agents on your desktop while VPS prod is also running them = double-billing the same plan. So you couldn't have a real dev environment running locally alongside VPS prod.
2. **The ANTHROPIC_* env block in `~/.claude/settings.json` was forcing all interactive `claude` invocations through ZAI proxy** — meaning you were coding through GLM models on your desktop even though you pay for Anthropic Max. Doc 10 documents the inversion fix.

After the inversion: plain `claude` defaults to Anthropic Max everywhere, `zai` is the explicit opt-in for cheap inference, and dev happens on the VPS via Remote-SSH because that's the only machine that doesn't double-bill the agent runs. You get the best of both worlds: real Claude for your interactive work, GLM for high-volume agent runs.

---

## Prerequisites checklist

Before your first session, confirm each of these (one-time setup):

| Check | How | Expected |
|---|---|---|
| Tailscale up on desktop | `tailscale status` | `mint-desktop` connected, `uaonvps` reachable |
| Tailscale up on VPS | `ssh root@uaonvps tailscale status` | listed peers include `mint-desktop` |
| SSH config | `grep -A 4 'Host uaonvps' ~/.ssh/config` | Host stanza with User=ua and IdentityFile |
| Anthropic Max OAuth on VPS | `ssh ua@uaonvps 'ls /home/ua/.claude/.credentials.json'` | file present, recent |
| Anthropic Max OAuth on desktop | `ls ~/.claude/.credentials.json` | file present, recent |
| Phase B applied (VPS) | `ssh ua@uaonvps 'type zai'` | `zai is a function` |
| Phase C applied (desktop) | `type zai` (from desktop bash) | `zai is a function` |
| Dev-tree provisioned | `ssh ua@uaonvps 'ls /home/ua/dev/universal_agent/.git'` | directory present |
| Antigravity Remote-SSH ext installed | (manual GUI check) | Remote Explorer panel shows SSH targets |
| Claude Code extension installed on **remote host** | (manual GUI check) | side-panel works after Remote-SSH connect |

If any check fails, see § Recovery procedures below for the corresponding fix.

---

## Cold-start session (~2 min from "fresh boot" to "actively coding")

1. **Boot desktop, connect to Tailscale** (your tailnet should auto-connect; if not, `sudo tailscale up`).
2. **Open Antigravity.**
3. **Click Remote Explorer** (left sidebar) → SSH Targets → click `uaonvps` to connect.
   - First connect of the day: Antigravity will reuse your `~/.ssh` keys; no password prompt unless your key is encrypted.
4. **File → Open Folder → `/home/ua/dev/universal_agent`** (or pick from Recent).
5. **Wait for the workspace to load.** Antigravity sends extensions to the remote host on first connect; you may see a brief "Installing extensions on uaonvps…" banner. Subsequent connects are instant.
6. **Open the integrated terminal** (Ctrl+`). Verify:
   - `whoami` → `ua`
   - `pwd` → `/home/ua/dev/universal_agent`
   - `git status` → `On branch feature/latest2; nothing to commit, working tree clean` (or current state)
7. You're ready. Edit code in the IDE; the Claude Code side panel uses Anthropic Max automatically.

---

## Edit → commit → ship cycle

**Edit:** open files in Antigravity normally. The Claude Code side panel (right side) is connected to Anthropic Max via OAuth — ask it questions, request edits, paste errors. Plain `claude` invocations from the integrated terminal also use Anthropic Max.

**For cheap experiments** (long token-burning explorations where Max-plan throughput would be a waste): use `zai` instead of `claude`:
```
zai -p "compose 5 different ways to refactor this 200-line module"
```
The `zai` function wraps `infisical run --env=production -- claude` so you get GLM models via the ZAI proxy.

**Commit and push** from the integrated terminal:
```
git add <files>
git commit -m "your message"
```

**Ship** (deploy to production):
```
/ship
```
Run it inside the Claude Code side panel OR from the terminal as `claude` (in either case Claude Code reads `.claude/commands/ship.md`). The `/ship` command is checkout-agnostic — it works the same whether run from your desktop or from the VPS dev-tree. It will:

1. Auto-commit any uncommitted changes
2. Push your current branch (typically `feature/latest2`)
3. Merge into `develop`
4. Fast-forward `main` (this triggers GitHub Actions)
5. Wait for the deploy run to complete and report success/failure

After `/ship` shows green, `/opt/universal_agent` on the VPS has your changes and services have been restarted.

**End of session:** just close Antigravity. No special teardown. The dev-tree persists.

---

## Recovery procedures (when things break)

| Symptom | Diagnosis | Fix |
|---|---|---|
| Antigravity Remote-SSH connect hangs forever | Tailscale not up, or VPS unreachable | `tailscale status` on desktop; `tailscale ping uaonvps`; `ssh ua@uaonvps echo ok` |
| Connect succeeds but workspace empty / file tree spinning | First-time extension install on remote; wait 30-60s | Check Antigravity status bar for "Installing…" |
| `claude -p "OK"` returns 401 / auth error | OAuth credentials expired (default ~30 days) | `cd ~ && claude /login` from VPS user `ua` (browser flow on desktop) |
| `zai -p "..."` says "infisical login session" | Universal-auth flow failed; bootstrap `.env` may be missing creds | Check `cat /home/ua/dev/universal_agent/.env` has INFISICAL_CLIENT_ID/SECRET |
| Side panel claude reports "wrong endpoint" or weird responses | Extension was installed on desktop instead of remote host | Ctrl+Shift+P → "Remote: Reinstall Extensions on Host" |
| `/ship` aborts with "non-fast-forward / 403" | Remote feature/latest2 advanced (parallel commits from another machine) | Per ship.md guidance: `git pull --rebase origin feature/latest2`, retry. **Never** `git push --force`. |
| `git status` shows phantom changes you didn't make | SSHFS mount stale OR you're in `/home/kjdragan/...` (desktop's filesystem mounted on VPS) | `pwd` to confirm location; for SSHFS issues `fusermount -u <mount>` and remount |
| Antigravity shows "Connection lost: uaonvps" | Tailscale flapped or VPS rebooted | Reconnect; if persistent see `docs/03_Operations/87_Tailscale_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md` § ACL/SSH Troubleshooting |
| UA service on VPS routing wrong endpoint | New deploy may have changed Infisical secrets; service needs restart to re-load | `ssh root@uaonvps 'sudo systemctl restart universal-agent-gateway'` (and others as needed) |
| `uv sync` fails in dev-tree | Network blip or pyproject.toml change conflicts | Re-run; if persistent `cd ~/dev/universal_agent && rm -rf .venv && uv sync` |

For anything not on this list, check [`10_Interactive_Coding_Environment.md`](10_Interactive_Coding_Environment.md) lessons-learned section first.

---

## When to use desktop-local Claude vs VPS Remote-SSH

**Default: VPS Remote-SSH for everything.** That's the supported flow.

**Rare cases for desktop-local:**

- VPS / Tailscale is down and you need to keep working: open `~/lrepos/universal_agent` on desktop, your local `claude` (Anthropic Max via OAuth) and `zai` (development env via Infisical) still work. Commit locally; `/ship` when VPS is back.
- Demo work that explicitly needs the desktop side (rare).
- You want to validate something works on your local terminal (smoke test for Phase C config).

**Don't use desktop-local for normal work:** running heartbeats/agents locally while VPS prod is running them double-bills your ZAI plan, and your edits + VPS prod state can drift in confusing ways.

---

## When to use `claude` vs `zai`

| Situation | Use |
|---|---|
| Coding assistance, debugging, refactoring | `claude` (Anthropic Max — your best models) |
| Architectural discussions, design help | `claude` |
| Pasting a stack trace, asking "what's wrong" | `claude` |
| Writing test cases | `claude` |
| Bulk operations: "summarize these 50 files", "generate variations" | `zai` (cheap GLM, your tokens last longer) |
| Long exploration sessions where Max plan throughput is the bottleneck | `zai` |
| Anything where you'd burn through your Max plan rate-limit | `zai` |

You can also pass any flag to either:
```
claude --resume <session-id>
zai --resume <session-id>
```

---

## Verification checklist (run after major changes)

If you change anything related to env, settings.json, Infisical, OAuth, or the dev-tree, run this checklist to confirm everything still works:

- [ ] Plain `claude` from VPS terminal → response received, `/proc/<pid>/environ` shows no `ANTHROPIC_BASE_URL`.
- [ ] `zai -p "..."` from VPS terminal → response received, `/proc/<pid>/environ` shows `ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic`.
- [ ] Antigravity Claude Code side panel responds to a test prompt.
- [ ] Plain `claude` from desktop terminal → response received, no `ANTHROPIC_BASE_URL` in process env.
- [ ] `zai` from desktop terminal → response received, `ANTHROPIC_BASE_URL=...z.ai...` in process env.
- [ ] `cd /opt/ua_demos/_smoke && python smoke.py` → `endpoint == api.anthropic.com`, exit 0 (regression check for demo path).
- [ ] `/ship` from VPS dev-tree on a trivial doc commit → CI/CD deploys successfully.
- [ ] UA gateway service on VPS responds to `curl https://api.clearspringcg.com/api/v1/health`.

For the sniffer-based bullet-proof routing checks see the test scripts referenced in [`10_Interactive_Coding_Environment.md`](10_Interactive_Coding_Environment.md) Phase G.

---

## Pointer table to deeper docs

| Topic | Doc |
|---|---|
| Why the inversion happened, the full mechanism, lessons learned | [`10_Interactive_Coding_Environment.md`](10_Interactive_Coding_Environment.md) |
| Demo workspace path mechanics | [`09_Demo_Execution_Environments.md`](09_Demo_Execution_Environments.md) |
| Demo provisioning runbook | [`../operations/demo_workspace_provisioning.md`](../operations/demo_workspace_provisioning.md) |
| Tailscale + SSH | [`../03_Operations/87_Tailscale_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`](../03_Operations/87_Tailscale_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md) |
| Infisical secret model | [`../deployment/secrets_and_environments.md`](../deployment/secrets_and_environments.md) |
| Git/CI/CD branching | [`../deployment/ai_coder_instructions.md`](../deployment/ai_coder_instructions.md) |
| `/ship` command (slash command) | [`../../.claude/commands/ship.md`](../../.claude/commands/ship.md) |
| Phase B migration script | [`../../scripts/apply_phase_b_inversion.sh`](../../scripts/apply_phase_b_inversion.sh) |
| Dev-tree provisioning script | [`../../scripts/provision_vps_dev_tree.sh`](../../scripts/provision_vps_dev_tree.sh) |
