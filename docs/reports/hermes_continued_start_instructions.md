# Hermes Continued — Start Instructions for the Operator

**Audience:** Kevin (the operator). Not the coding agent.
**Purpose:** Tell you exactly how to launch the new Claude Code session so it has the capabilities the previous sandbox session lacked, and how to hand it off to the work.
**Companion doc:** [`hermes_continued.md`](./hermes_continued.md) — the technical handoff for the coding agent itself.

---

## 1. What we want the new session to have

* **Anthropic Max plan for coding** — full Opus/Sonnet/Haiku via OAuth. You get this automatically with any `claude` launch from a shell on your desktop or VPS (per the 2026-05 inversion).
* **AgentMail MCP loaded** — `mcp__agentmail__*` tools callable so we can finally triage the cron-error emails the sandbox couldn't reach.
* **All other MCP servers loaded** — GitHub MCP, ZAI MCPs, NotebookLM MCP, Drive, etc.
* **Local dev stack reachable** — `just dev` available so the agent can verify UI changes before pushing.

**Critical:** the magic is in the launcher. `scripts/claude_with_mcp_env.sh` (or whatever wrapper of yours invokes `initialize_runtime_secrets()`) is what injects Infisical secrets like `AGENTMAIL_API_KEY` into the environment **before** `claude` starts, which is what makes the MCP servers spawn with valid credentials. A bare `claude` launch (without the wrapper) won't have those.

---

## 2. Two launch options

### Option A — Desktop (preferred)

Best for: any work that needs `just dev` for live UI verification. Most coding tasks. Today's B.2 finish.

```bash
cd ~/lrepos/universal_agent
git fetch origin --prune
git pull --ff-only origin main           # make sure your tree matches prod main
scripts/claude_with_mcp_env.sh            # or your alias if you have one (e.g. `zai`)
```

Once `claude` starts, you're on Anthropic Max plan, all MCPs loaded, working tree on `main`. Hand the agent the handoff doc:

> Read `docs/reports/hermes_continued.md` and execute § 9 (the concrete first-hour checklist). Start with the cron-error triage in § 4.

### Option B — VPS via Antigravity Remote-SSH (fallback)

Best for: when desktop isn't available, or when you specifically want VPS `journalctl` / production state access alongside the coding session.

1. Open Antigravity Remote-SSH window connecting to `uaonvps`.
2. Inside the remote window:

   ```bash
   cd /home/ua/dev/universal_agent
   git fetch origin --prune
   git pull --ff-only origin main
   scripts/claude_with_mcp_env.sh
   ```

3. Same handoff: tell the agent to read `docs/reports/hermes_continued.md`.

Note: Doc 11 (`docs/06_Deployment_And_Environments/11_Daily_Dev_Workflow.md`) marks VPS-as-dev as the **fallback path** post-2026-05-11. The desktop option is the canonical one.

---

## 3. Verify the new session has the capabilities

Before handing off real work, ask the agent to run this self-check. If any of these fail, the launcher didn't load the env properly and you should restart.

### Check 1 — AgentMail MCP loaded

```
Ask: "List threads from Simone's inbox in the past hour."
Expected: agent calls mcp__agentmail__list_threads and returns a list (or "no threads").
Failure mode: agent reports "I don't have AgentMail tools available" — relaunch via the wrapper.
```

### Check 2 — Anthropic Max plan in effect

```
Ask: "What model are you running on?"
Expected: agent reports an Anthropic Claude model (Opus / Sonnet / Haiku 4.x).
Failure mode: agent reports a GLM-* or ZAI model — the OAuth credential is stale or
              ANTHROPIC_* env vars are leaking through. Fix: from inside the demo
              workspace folder, `claude /login`, then relaunch.
```

### Check 3 — Local dev reachable

```
Ask: "Run `just --list` and confirm the dev recipe exists."
Expected: agent runs the command, sees a `dev` recipe.
Failure mode: `just` not installed — `sudo apt install just` then relaunch.
```

### Check 4 — Git state matches prod

```
Ask: "Run `git log origin/main --oneline -3` and report the latest SHA."
Expected: latest SHA matches what you see on https://github.com/Kjdragan/universal_agent/commits/main.
Failure mode: stale local main — `git fetch origin && git pull --ff-only origin main`.
```

---

## 4. Model mix — what's actually running where

Once the session is launched, the model picture across the system is:

| Surface | Model | Why |
|---|---|---|
| **Your new coding session (the Claude Code you just launched)** | **Anthropic Max plan** (Opus 4.x / Sonnet 4.x / Haiku 4.x via OAuth) | Real Claude for coding. Default for all interactive launches post-2026-05 inversion. |
| **UA autonomous loops on the VPS** (heartbeat, cron, dispatch_sweep, Simone, Atlas, Cody today, ClaudeDevs intel) | **ZAI proxy / GLM models** | Cheap inference. ZAI vars injected at service-start by `initialize_runtime_secrets()`. |
| **Demo workspace execution** (`/opt/ua_demos/<id>/`) | **Anthropic Max plan** | Demos that need real Claude features that ZAI lacks. |

**Cody specifically:** today Cody runs on the ZAI lane like the other UA autonomous agents. **The "Cody on Anthropic" toggle is Hermes Phase E**, which hasn't shipped yet. Until Phase E lands, Cody stays on ZAI. If you want Cody to use real Anthropic models for a specific task right now, the only path is to do that task inside a `/opt/ua_demos/<id>/` workspace (which already runs on Anthropic).

The agent's handoff doc § 5 has the Phase E details. If you want to prioritize Phase E (before C and D), tell the agent — the plan doc says E "can land in parallel with B/C/D."

---

## 5. Where to point the agent

After the self-check passes, give the agent this one-line instruction:

> Read `docs/reports/hermes_continued.md`. Execute the § 9 first-hour checklist. Start with the cron-error triage in § 4 before resuming any code work.

That's it. The handoff doc is self-contained.

---

## 6. If something goes wrong

* **AgentMail MCP not loading:** check `INFISICAL_CLIENT_ID` and `INFISICAL_CLIENT_SECRET` are set in your shell env or in `~/.config/ua/bootstrap.env` (or wherever your machine bootstraps from). Run `infisical secrets get AGENTMAIL_API_KEY --env=production` and confirm it returns a value. If it doesn't, the Infisical secret needs to be created.
* **`scripts/claude_with_mcp_env.sh` doesn't exist:** check the post-2026-05-10 wrapper name. Some sessions referred to it as `claude_with_mcp_env`, some as a `zai()` shell function. Whichever wrapper invokes `initialize_runtime_secrets()` is the right one. Run `grep -r "initialize_runtime_secrets" scripts/` to find it.
* **`just dev` boot fails:** the agent's handoff doc § 6.2 covers troubleshooting. Most common cause: missing `.env` at repo root; run `bash scripts/bootstrap_local_hq_dev.sh` once.
* **Production B.1 regression suspected:** if the cron-error triage finds a B.1-induced regression, the fix path is a hotfix PR to main (NOT a revert of #198 unless the regression is severe). The agent should pause B.2, file a follow-up issue, push the hotfix, then resume.

---

## 7. After the session ends

The agent should:

1. Have either shipped B.2 (PR opened → CI green → auto-merged → deployed) or have a clean handoff for the next session if blocked.
2. Have written any new findings to `docs/reports/hermes_continued.md` (append, don't overwrite) so the next session inherits the state.
3. Have NOT touched the original Hermes plan doc beyond the Implementation Status section.

If the agent pushes a follow-up PR, it'll auto-merge on green CI thanks to `pr-auto-merge.yml`. Watch for the GitHub Actions deploy notification.
