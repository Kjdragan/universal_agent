# Claude Code Cheat Sheet — Local Dev

> **Audience:** Kevin running interactive Claude Code (Anthropic Max plan) from his desktop while `just dev` is running.
>
> **TL;DR:** Open a terminal at `/home/kjdragan/lrepos/universal_agent`, run `bash scripts/claude_with_mcp_env.sh`. That's it. The rest of this doc is flags, alternatives, and troubleshooting.

---

## 1. The one command you need

From the repo root (`/home/kjdragan/lrepos/universal_agent`):

```bash
bash scripts/claude_with_mcp_env.sh
```

That launches Claude Code with:
- **Anthropic Max** OAuth (real Opus 4.7 / Sonnet 4.6 / Haiku — not ZAI/GLM)
- All MCP servers authenticated (Composio, edgartools, video_audio, youtube, zai_vision, etc. — the `${VAR}` placeholders in `.mcp.json` get resolved at startup)
- Project-local skills, slash commands (`/ship`, `/dev`, etc.), agents discovered

The wrapper handles two concerns at once: (a) Infisical secrets for MCPs, (b) preserving your Anthropic Max OAuth (it deliberately strips `ANTHROPIC_*` from Infisical-injected env so api.anthropic.com is the endpoint, not Z.AI).

### Where the wrapper looks for `.env`

The wrapper needs an Infisical-bootstrap `.env` to load secrets from. Auto-detection (2026-05-11):

1. `UA_INSTALL_ROOT` env var if set (explicit override)
2. `/opt/universal_agent/.env` (canonical VPS prod path)
3. **The repo containing the script itself** (your desktop checkout — this is the default case)

You shouldn't need to set `UA_INSTALL_ROOT` manually on your desktop — auto-detect handles it. If you have multiple checkouts and want to point at a specific one:

```bash
UA_INSTALL_ROOT=/home/kjdragan/lrepos/universal_agent bash scripts/claude_with_mcp_env.sh
```

If you see `❌ <path>/.env not found` at launch, the most common cause is you haven't run `bash scripts/bootstrap_local_hq_dev.sh` yet — that's what creates `.env` from your Infisical bootstrap creds.

---

## 2. Common flags

Pass any of these AFTER the wrapper script:

```bash
# One-shot prompt mode (don't open the chat UI; print + exit)
bash scripts/claude_with_mcp_env.sh -p "summarize what's in src/universal_agent/loop_control.py"

# Pick a specific model
bash scripts/claude_with_mcp_env.sh --model sonnet
bash scripts/claude_with_mcp_env.sh --model haiku
bash scripts/claude_with_mcp_env.sh --model opus           # default
bash scripts/claude_with_mcp_env.sh --model claude-opus-4-7

# Resume previous session
bash scripts/claude_with_mcp_env.sh --resume

# Skip permission prompts (auto-allow common tools — be careful with this)
bash scripts/claude_with_mcp_env.sh --dangerously-skip-permissions

# Show full help
bash scripts/claude_with_mcp_env.sh --help
```

For the canonical full flag list: `claude --help` (works after launch too).

---

## 3. Cheap-mode alternative — `zai` (full GLM routing)

When you want cheap inference (high-volume experimental work, bulk token burns where Claude quality isn't worth the Max plan tokens), use the `zai` shell function instead:

```bash
zai -p "summarize this file"
zai --model glm-5.1
```

`zai` is a shell function (installed by `scripts/_claude_launcher.py` setup), not a script. It sets `ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic` + auth token + model mapping in the env, then launches `claude`. Everything else (slash commands, MCPs, agents) works the same — just the LLM endpoint changes.

**When to use which:**

| Scenario | Use |
|---|---|
| Daily interactive coding, debugging, refactoring | `bash scripts/claude_with_mcp_env.sh` (Anthropic Max) |
| Bulk find/replace, mass refactor, batch analysis | `zai -p "..."` (cheap GLM) |
| Anything that needs the strongest reasoning | `bash scripts/claude_with_mcp_env.sh --model opus` |
| Quick one-liners where you don't need MCPs | `claude -p "..."` (plain; MCPs may fail but that's OK for trivial prompts) |

---

## 4. Slash commands you'll use most

Once inside Claude (these are typed at the prompt, not in the shell):

```
/help                # full slash command list for the current session
/clear               # clear context (start fresh)
/model               # show current model + switch
/cost                # show token usage for current session
/login               # re-OAuth Anthropic Max (use when API returns 401)
/ship                # commit + push current branch, open PR, enable auto-merge
                     #   (see docs/operations/2026-05-11_autonomous_pr_and_deploy_flow_briefing.md)
/dev                 # status / start / stop the local dev stack (if you have a /dev skill)
```

Custom slash commands live at `.claude/commands/<name>.md`. The `/ship` command is defined at `.claude/commands/ship.md`.

---

## 5. Verifying you're actually on Anthropic Max (not ZAI)

After launching, sanity-check:

```
/model
```

Should show something like `claude-opus-4-7` or `claude-sonnet-4-6` — NOT `glm-5.1` or `glm-4.6`.

Or from inside chat: ask `What model are you?` — Claude will say "Claude Opus" / "Claude Sonnet" / etc. If it says "GLM" you accidentally launched in ZAI mode.

Or from your shell BEFORE launching:

```bash
env | grep ANTHROPIC_  # should be empty for Anthropic-Max mode
                       # If you see ANTHROPIC_BASE_URL=z.ai... you're in ZAI mode
```

---

## 6. Anti-patterns (per CLAUDE.md)

| Don't | Why |
|---|---|
| `claude` plain (no wrapper) | MCP servers will fail with "no token" / placeholder unresolved errors because `${VAR}` in `.mcp.json` doesn't resolve without the wrapper's Infisical bootstrap |
| `infisical run --env=development -- claude` | The Infisical CLI's auth context is unreliable; the Python SDK path (what the wrapper uses) is the canonical one |
| Letting Claude Code Doctor / IDE plugin auto-resolve `${VAR}` in `.mcp.json` | If `git status` shows `.mcp.json` modified with `${VAR}` → literal substitutions, run `git checkout -- .mcp.json` immediately. The 2026-05-08 Hostinger token leak (see `docs/operations/2026-05-08_hostinger_token_remediation.md`) is the cautionary tale. |
| `claude` with `ANTHROPIC_API_KEY` set in your environment | Claude Code treats that as an "external API key" and overrides your Max plan OAuth, giving you billed-API behavior instead of Max plan. The wrapper strips `ANTHROPIC_*` from Infisical injection to prevent this. |

---

## 7. Troubleshooting

### `claude` says "Invalid API key · Fix external API key"

Your environment leaked an `ANTHROPIC_*` var that's overriding OAuth. Fix:

```bash
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL ANTHROPIC_MODEL ANTHROPIC_DEFAULT_HAIKU_MODEL ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_OPUS_MODEL
bash scripts/claude_with_mcp_env.sh
```

Or refresh OAuth: `cd ~ && claude /login` (run from outside the repo to dodge any project-level `.env` injection).

### MCP server fails to start with "missing env var" or "401"

`${VAR}` placeholder in `.mcp.json` didn't resolve. Confirm the wrapper actually ran the bootstrap:

```bash
ls -l .env                          # should exist with INFISICAL_CLIENT_ID etc.
bash scripts/bootstrap_local_hq_dev.sh   # re-bootstrap if needed
bash scripts/claude_with_mcp_env.sh
```

### Side panel uses ZAI / wrong endpoint (in Antigravity)

Reinstall the Claude Code extension on the **active** Antigravity window — i.e., on your local profile if you're in local dev, NOT on a Remote-SSH'd session.

### "develop" branch error from `/ship`

You're on the retired branch. `git checkout <feature>` first, then `/ship`. `develop` was retired 2026-05-10 (see [Doc 04](../06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md)).

### Long-running session feels slow

Run `/clear` to drop context. Or `/cost` to see token usage — if you're past 60K context, response latency creeps up.

---

## 8. Workflow examples

### Start a new feature

```bash
# In Antigravity terminal at repo root
git pull --ff-only origin main
git checkout -b claude/my-feature-name
just dev                            # starts gateway + api + web-ui in another terminal

# In a SECOND terminal in Antigravity
bash scripts/claude_with_mcp_env.sh

# Inside Claude:
"Implement X. Test it. Show me the diff before committing."

# When happy:
/ship                               # commits, pushes, opens PR, enables auto-merge — walk away
```

### Quick one-shot analysis

```bash
bash scripts/claude_with_mcp_env.sh -p "What does src/universal_agent/loop_control.py do? Summarize in 5 bullets."
```

### Cheap bulk task

```bash
zai -p "Read every .py file under src/universal_agent/services/ and list the ones that import 'requests'. Output as a markdown table."
```

### Resume yesterday's session

```bash
bash scripts/claude_with_mcp_env.sh --resume
```

---

## 9. Related docs

- **[Local Dev Environment (canonical)](../06_Deployment_And_Environments/12_Local_Dev_Environment.md)** — how `just dev` works, what's gated off in dev, how to opt loops in
- **[Interactive Coding Environment (Claude routing internals)](../06_Deployment_And_Environments/10_Interactive_Coding_Environment.md)** — the full inversion mechanism, why `zai` is a function vs script, etc.
- **[Secrets and Environments (Infisical canonical)](../deployment/secrets_and_environments.md)** — what the wrapper's bootstrap actually loads, `${VAR}` placeholder pattern in `.mcp.json`
- **[Autonomous PR + Deploy Flow Briefing](../operations/2026-05-11_autonomous_pr_and_deploy_flow_briefing.md)** — what `/ship` does, how auto-merge fires, deploy mechanics
- **[`scripts/claude_with_mcp_env.sh`](../../scripts/claude_with_mcp_env.sh)** — the wrapper script source itself; ~60 lines of bash, worth reading once
