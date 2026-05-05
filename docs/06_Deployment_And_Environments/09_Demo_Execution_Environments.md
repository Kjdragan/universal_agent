# Demo Execution Environments

> ⚠️ **READ THIS BEFORE TOUCHING ANYTHING CLAUDE-RELATED ON THE VPS** ⚠️
>
> Universal Agent runs **TWO separate Claude environments** on the same VPS at the same time.
> They look almost identical from the outside but have completely different behavior.
> Mistaking one for the other is the #1 source of confusion in this system.
>
> This document is the canonical reference. Read the [TL;DR](#tldr-the-mental-model)
> first, then the [decision tree](#decision-tree-which-environment-do-i-need),
> then the rest as needed.

> **Audience:** Anyone deploying, debugging, or building demos on the VPS — operators, AI coders, and Cody/Simone themselves.
> **Status:** Canonical reference for v2 of the ClaudeDevs intelligence pipeline.
> **Companion docs:** [`docs/proactive_signals/claudedevs_intel_v2_design.md`](../proactive_signals/claudedevs_intel_v2_design.md) §3, §8 · [Demo Workspace Provisioning Runbook](../operations/demo_workspace_provisioning.md)

---

## TL;DR — the mental model

UA is **always** running two Claude environments side by side on the VPS:

| | **ZAI environment** (default) | **Anthropic-native environment** (demos only) |
|---|---|---|
| **Where** | Anywhere except `/opt/ua_demos/` | Inside `/opt/ua_demos/<demo-id>/` |
| **Who's there** | UA itself, Simone, Atlas, normal Cody work, ClaudeDevs intel cron, dependency drift sweep, dashboards | Cody when she's building a Phase 3 demo. Phase 0 smoke tests. |
| **Models** | GLM via ZAI proxy (cheap, GLM-5.x emulating Claude) | Real Anthropic Claude (Opus 4.7, Sonnet, Haiku) |
| **Auth** | API key (`ANTHROPIC_AUTH_TOKEN` env) | Max plan **OAuth session** from `claude /login` |
| **Why** | Cost — 95% of UA work doesn't need bleeding-edge Claude | Demo correctness — new Anthropic features may not exist in the proxy |
| **Settings file** | `~/.claude/settings.json` (polluted, with env block + hooks + plugins) | Project-local `.claude/settings.json` inside each demo dir (vanilla) |

**Both environments use the same `claude` binary on the VPS.** What environment Claude Code lands in is determined entirely by **which directory you `cd` into before invoking it**, plus whether any `ANTHROPIC_*` env vars leaked from the parent shell.

---

## Decision tree — which environment do I need?

```
Am I building or testing a NEW Anthropic feature
that requires the real Claude API surface?
                │
        ┌───────┴───────┐
        │ YES           │ NO
        ▼               ▼
Use Anthropic-native    Use ZAI (default)
                                │
                    Just `cd` somewhere outside
                    /opt/ua_demos/ and run normally.
                    Don't change anything.

Anthropic-native path:
        │
        ▼
Am I writing Python that calls
`from anthropic import Anthropic`?
        │
┌───────┴───────┐
│ YES           │ NO  (I'm using the claude CLI directly,
│               │     OR I'm Cody operating Claude Code)
▼               ▼
Need ANTHROPIC_API_KEY      Just `cd /opt/ua_demos/<dir>/`
in the workspace env.       and invoke `claude`. The Max
The OAuth session is        plan OAuth session set up by
NOT enough — the SDK        `claude /login` does the work.
ignores OAuth.              No extra credential needed.
        │
        ▼
Get the API key from console.anthropic.com
under the same Max plan account. Add it to
the workspace's env (NOT to a settings file
or any committed file). See "Category-2
demos" below.
```

---

## Why two environments at all?

Because they have **opposite needs**:

**ZAI is correct for routine UA work** — UA does enormous numbers of LLM calls (Simone heartbeats, Atlas execution, ClaudeDevs intel cron processing 100+ posts a week, dashboards, etc). Direct Anthropic billing on every one of those calls would burn money for no benefit, because routine UA work doesn't depend on Claude-specific features that ZAI's GLM proxy can't handle. ZAI emulates Claude's API shape well enough for normal coding/orchestration tasks.

**Anthropic-native is correct for demos** — the entire point of the ClaudeDevs intel pipeline is to build reference implementations of *brand-new* Anthropic features so we can lift those patterns into client engagements. New features may not exist in the ZAI proxy yet (Skills, Memory Tool, Managed Agents, latest tool-use shapes, etc). Running a demo of a new feature against ZAI would silently produce wrong results — either the feature doesn't fire, or the proxy substitutes a stale implementation. We'd think the demo was broken when really the environment was wrong.

**You can't pick one and use it for everything.** Pure ZAI breaks demos of new features. Pure Anthropic-native breaks UA's cost model. So we run both, segregated by working directory.

---

## What lives where (filesystem layout)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ VPS srv1360701 (production)                                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  /opt/universal_agent/  ←─── UA repo (git-tracked, deploys here)        │
│      ├── src/universal_agent/        Code (READ-ONLY for runtime)       │
│      ├── docs/                       Documentation (this very file)     │
│      ├── .venv/                      uv-managed Python venv             │
│      └── .claude/                    NOTHING here — UA settings live    │
│                                      in user-global ~/.claude/, not     │
│                                      project-local                      │
│                                                                         │
│  ~/.claude/  (or /root/.claude/ if running as root)                     │
│      └── settings.json               ★ THE POLLUTED ONE ★               │
│          • ANTHROPIC_BASE_URL → api.z.ai  (ZAI proxy)                   │
│          • ANTHROPIC_AUTH_TOKEN → ZAI key                               │
│          • ANTHROPIC_DEFAULT_*_MODEL → glm-5-turbo / glm-5.1            │
│          • Hooks → ~/.claude/agent-flow/hook.js                         │
│          • Plugins, marketplaces, experimental flags                    │
│      └── (Max plan OAuth session token — set up by `claude /login`      │
│           from inside /opt/ua_demos/_smoke/. Persists across reboots.)  │
│                                                                         │
│  /opt/ua_demos/  ←─── Demo execution root (NOT in git, runtime only)    │
│      │                                                                  │
│      ├── _smoke/                     Phase 0 smoke demo                 │
│      │   ├── .claude/settings.json   ★ THE VANILLA ONE ★                │
│      │   │     • No env block                                           │
│      │   │     • No hooks                                               │
│      │   │     • No plugins                                             │
│      │   │     • effortLevel + autoUpdatesChannel only                  │
│      │   ├── pyproject.toml          Empty deps so `uv run` works       │
│      │   ├── smoke.py                Shells out to `claude -p "..."`    │
│      │   └── README.md                                                  │
│      │                                                                  │
│      ├── <demo-id-1>/                One real demo                      │
│      │   ├── .claude/settings.json   Vanilla (provisioner asserts this) │
│      │   ├── BRIEF.md                Authored by Simone (Phase 2)       │
│      │   ├── ACCEPTANCE.md           Authored by Simone                 │
│      │   ├── business_relevance.md   Authored by Simone                 │
│      │   ├── SOURCES/                Curated raw docs from vault        │
│      │   ├── pyproject.toml          Demo-local Python deps             │
│      │   ├── src/                    Cody's implementation              │
│      │   ├── BUILD_NOTES.md          Cody documents gaps (no invention) │
│      │   ├── manifest.json           endpoint hit + versions used       │
│      │   └── run_output.txt          Captured stdout from successful run│
│      │                                                                  │
│      └── <demo-id-2>/  ...                                              │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

**Key visual takeaways:**
- The polluted settings.json is at `~/.claude/`. It applies whenever `claude` is invoked from a directory that doesn't have its own project-local settings.
- The vanilla settings.json is project-local inside each `/opt/ua_demos/<dir>/`. It applies whenever `claude` is invoked from inside that directory.
- The Max plan OAuth session is also at `~/.claude/` (different file from settings.json). It's auth-only and is reused across both environments — `claude` will use it whenever it's not overridden by a token env var.

---

## How Claude Code picks which environment

Claude Code reads its configuration with a clear precedence:

1. **Environment variables** (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_DEFAULT_*_MODEL`) — **highest priority, beats everything else**.
2. **Project-local** `.claude/settings.json` in the current working directory (and ancestors).
3. **User-global** `~/.claude/settings.json`.
4. **Built-in defaults** (`api.anthropic.com`, etc.).

A demo subprocess that does `cd /opt/ua_demos/<demo-id>/` before invoking `claude` inherits the project-local **vanilla** settings, which override the user-global **polluted** settings. As long as **no `ANTHROPIC_*` env vars leak from the parent shell**, the demo hits real Anthropic.

The smoke demo (`/opt/ua_demos/_smoke/smoke.py`) explicitly verifies `endpoint == api.anthropic.com` and exits with code `2` on mismatch. This catches env-leak regressions.

### The env-leak gotcha

This is the most common confusion. UA's normal operation **sets** the polluted env vars at the shell level. So if you `ssh` into the VPS, those vars are in your shell. Then if you `cd /opt/ua_demos/_smoke/ && claude -p "test"`, Claude Code obeys the env vars (priority #1 above) and routes to ZAI **even though** the project-local settings say otherwise.

**Fix when running demos manually:**

```bash
cd /opt/ua_demos/_smoke
unset ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_DEFAULT_HAIKU_MODEL ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_OPUS_MODEL
claude -p "Reply with the word OK"
```

**Fix for production (Cody's autonomous demo execution):** Cody invokes the demo subprocess with an explicit `env={}` (or with only the env vars she actually wants) so no parent-shell pollution leaks in. PR 9 (`cody-implements-from-brief`) handles this.

---

## The CLI vs SDK auth wrinkle (READ THIS, IT BIT US)

This is non-obvious and we discovered it the hard way. There are TWO completely separate auth systems within the Anthropic-native environment:

| Tool | Auth source | Used by |
|---|---|---|
| `claude` CLI | Max plan OAuth session (set up by `claude /login`) | All demos that exercise Claude Code features. Cody invokes this. |
| `from anthropic import Anthropic` (Python SDK) | `ANTHROPIC_API_KEY` env var ONLY (or constructor kwarg) | Demos that exercise raw Anthropic API features (rare) |

**The CLI's OAuth session is invisible to the Python SDK.** A successful `claude /login` does NOT make `Anthropic()` work in Python. The SDK doesn't read OAuth credentials.

This means:

- **Category-1 demos (most demos): Claude Code feature demos.**
  Examples: "demonstrate the Skills feature", "show how Hooks work", "build something with Subagents".
  Implementation: Cody (a Claude Code instance herself) builds these by **writing Claude Code agents** that exercise the feature. She invokes `claude` directly. No API key needed; the OAuth session does it.

- **Category-2 demos (rare): Raw Anthropic API demos.**
  Examples: "demonstrate prompt caching efficiency from Python", "exercise the Memory Tool API surface programmatically".
  Implementation: Demo author writes Python that does `client = Anthropic(); client.messages.create(...)`. **This requires a real `ANTHROPIC_API_KEY`** — get it from `console.anthropic.com` under the same Max plan account, then add it to the workspace's env (in a `.env` file or via systemd `Environment=`, never committed). Sources are billed against the Max plan's API key allowance, distinct from the Max plan chat allowance.

If a category-2 demo errors with `Could not resolve authentication method. Expected one of api_key, auth_token, or credentials to be set`, the API key isn't in the env. Fix: set `ANTHROPIC_API_KEY` in the workspace env.

---

## Common pitfalls and their failure modes

| Symptom | Root cause | Fix |
|---|---|---|
| Demo runs but the output uses an old API surface | Settings precedence broke — demo accidentally hit ZAI | Verify `cd /opt/ua_demos/<demo-id>/` happened before invoking `claude`. Run `smoke.py` to verify endpoint. |
| `smoke.py` exits with code 2 (`endpoint_mismatch`) | `ANTHROPIC_BASE_URL` env var inherited from parent shell | `unset ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_DEFAULT_*_MODEL`. Long-term: run Cody as systemd service with explicit `Environment=` clause. |
| `smoke.py` exits with code 1 (`live_call: failed`), stderr mentions auth | Max plan OAuth session expired or never set up | Re-run runbook step 3: `cd /opt/ua_demos/_smoke && claude /login`. Critical: do this from inside the demo workspace, NOT from a directory that inherits ZAI mapping. |
| `smoke.py` exits with code 1 (`live_call: skipped_claude_cli_not_installed`) | `claude` binary not on PATH | `npm install -g @anthropic-ai/claude-code` (or wait for PR 6b's upgrade actuator) |
| `Anthropic()` constructor in some demo file raises `Could not resolve authentication method` | Trying to use the Python SDK with the OAuth session, which the SDK ignores | Either rewrite the demo to shell out to `claude` (preferred for category-1), OR add an `ANTHROPIC_API_KEY` from `console.anthropic.com` to the workspace env (category-2). |
| `provision_smoke_workspace` raises `ValueError: settings.json carries pollution markers` | Someone edited `templates/_smoke_demo/.claude/settings.json` and re-introduced a forbidden key | Remove the offending key from the template; the safety net is doing its job. Don't bypass it. |
| New ClaudeDevs intel cron runs are using GLM models even for the ingest's research grounding LLM calls | This is **correct by design** — Phase 1 (discovery + research grounding) and Phase 4 (Simone review) run on ZAI for cost. Only Phase 3 demo execution needs Anthropic-native. | No fix needed unless you specifically want Phase 1 LLM calls on Anthropic — in which case override at the script level, not by polluting the demo workspace. |
| Demo accidentally hits ZAI even after `cd` and `unset` | Parent process started before the unset was added; descendants inherit the original env | Restart the parent process, or use `env -i` to launch a fully clean subprocess: `env -i HOME=$HOME PATH=$PATH bash -c "cd /opt/ua_demos/_smoke && claude -p test"` |

---

## Dependency currency across both environments

Both environments must run the **same versions** of Anthropic-adjacent packages so a demo built against the smoke environment behaves identically when promoted into a real demo workspace. The Phase 0 dependency-currency layer keeps drift visible:

- **Daily sweep** ([`dependency_currency_sweep.py`](../../src/universal_agent/scripts/dependency_currency_sweep.py)) reports outdated `claude-code` CLI, `claude-agent-sdk-python`, `claude-agent-sdk-typescript`, `anthropic`, `@anthropic-ai/sdk`. Writes to `vault/infrastructure/version_drift.md`.
- **Release detection** (in `classify_post`) flags `@ClaudeDevs` tweets that announce new versions, attaching a structured `release_info` to the action.
- **Upgrade actuator (PR 6b, pending)** will: bump the manifest, run smoke tests against **both environments** (ZAI smoke verifies UA's normal operation still works; Anthropic-native smoke verifies demo path still works), deploy via the existing `develop → main` GitHub Actions pipeline, email Kevin on every change. Rollback on either smoke fail.

The dual-environment smoke matrix is the single most important guardrail in Phase 0. An upgrade that breaks ZAI breaks all of UA. An upgrade that breaks Anthropic-native breaks demos. **Both have to pass before anything ships.**

---

## Generalization to other lanes

The dual-environment pattern generalizes. When we add the `openai-codex-intelligence` lane (currently a disabled template in [`config/intel_lanes.yaml`](../../src/universal_agent/config/intel_lanes.yaml)), demo execution will need a **third** environment: `openai_native` with its own auth path. Same shape, same provisioner, different `endpoint_required` value on the entity page frontmatter and a different config home for the OpenAI Agents SDK.

The lane config already has `demo_endpoint_profile` as a field for this reason. PR 7's provisioner reads it implicitly today (only `anthropic_native` is supported), but the surface is in place for future expansion.

---

## What about Cody on her main UA work vs Cody building a demo?

Same Cody, different working directory. A worked example:

**Scenario A:** Kevin asks Cody to refactor `csi_url_judge.py`.
- Cody is invoked in `/opt/universal_agent/` (the UA repo).
- Project-local `.claude/settings.json` doesn't exist there.
- User-global `~/.claude/settings.json` (the polluted one) takes effect.
- Claude Code routes through ZAI, models map to GLM-5.x.
- Cheap. Fast. Right for routine coding.

**Scenario B:** Simone hands Cody a demo task for the new Skills feature.
- Cody is invoked in `/opt/ua_demos/<skills-demo-id>/`.
- Project-local `.claude/settings.json` exists there (vanilla).
- It overrides the user-global polluted settings.
- The dispatcher unsets any `ANTHROPIC_*` env vars before launching.
- Claude Code routes to `api.anthropic.com` using the Max plan OAuth.
- Real Claude Opus 4.7 / Sonnet 4.6 / Haiku 4.5. Real Skills feature. Real demo.

**The Cody process is the same binary in both cases. Only the working directory changes.**

---

## Quick verification commands

When you're not sure which environment you're in, run these from your current directory:

```bash
# Show which settings.json claude would use right now.
claude config list

# Show what models claude thinks are available.
claude --version
```

Or, definitive proof of what endpoint a request actually hits:

Terminal 1:

```bash
claude -p "Write a 200-word story about a cat."
```

Terminal 2 (while terminal 1 is mid-request):

```bash
ss -t state established | grep -E 'anthropic|z\.ai'
```

You should see `api.anthropic.com` for Anthropic-native, `api.z.ai` for ZAI, **never both**.

---

## Related docs

- [ClaudeDevs X Intelligence System](../02_Subsystems/ClaudeDevs_X_Intelligence_System.md) — full subsystem reference
- [ClaudeDevs X Intel v2 Design](../proactive_signals/claudedevs_intel_v2_design.md) §3, §8 — design rationale
- [Demo Workspace Provisioning Runbook](../operations/demo_workspace_provisioning.md) — one-time setup steps
- [Model Choice and Resolution](../01_Architecture/10_Model_Choice_And_Resolution.md) — Anthropic-to-ZAI mapping internals
- [ZAI / OpenAI-Compatible Setup](../ZAI_OPENAI_COMPATIBLE_SETUP.md) — ZAI proxy configuration reference
