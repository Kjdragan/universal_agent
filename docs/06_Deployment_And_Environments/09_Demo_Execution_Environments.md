# Demo Execution Environments

> **Audience:** Anyone trying to understand why UA has two parallel Claude Code environments on the VPS, what lives where, and which one runs which workload.
> **Status:** Canonical reference for v2 of the ClaudeDevs intelligence pipeline. Companion to [`docs/proactive_signals/claudedevs_intel_v2_design.md`](../proactive_signals/claudedevs_intel_v2_design.md) §3, §8.

---

## The two environments

UA's normal operation and Phase 3 demo execution intentionally run in **two different environments on the same VPS**, because they have opposite needs:

| Profile | Used for | Models hit | Auth | Purpose |
|---|---|---|---|---|
| **ZAI-mapped (default UA)** | All routine UA agent work — Cody coding tasks, Simone heartbeats, Atlas execution, every dashboard interaction | GLM-5.x via ZAI proxy | `ANTHROPIC_AUTH_TOKEN` env (ZAI key) | **Cheap inference.** GLM emulation gives us Sonnet/Opus-class behavior at a fraction of the per-token cost. |
| **Anthropic-native (demos only)** | ClaudeDevs intel pipeline Phase 3 demo execution under `/opt/ua_demos/<demo-id>/` | Real Anthropic Claude (Haiku/Sonnet/Opus) | Max plan OAuth session via `claude /login` | **Real feature surface.** Brand-new Anthropic features (Skills, Memory Tool, Managed Agents, etc.) may not be implemented in the ZAI proxy yet. Running a new-feature demo against ZAI would silently hit the wrong API. |

**Both environments coexist on the same VPS user account**, distinguished by working directory and by which `.claude/settings.json` Claude Code picks up.

---

## What lives where

### The UA repo (ZAI-mapped environment)

**Location:** the deployed checkout of `Kjdragan/universal_agent`. On the VPS this is typically `/opt/universal_agent/` (or wherever the deploy pipeline lands the code). On Kevin's workstation it's `/home/kjdragan/lrepos/universal_agent/`.

**Settings file:** `~/.claude/settings.json` (the polluted one — see Kevin's actual file in the v2 design doc §8.2). Contains:

- `ANTHROPIC_BASE_URL: https://api.z.ai/api/anthropic` — the ZAI redirect.
- `ANTHROPIC_AUTH_TOKEN` — ZAI API key.
- `ANTHROPIC_DEFAULT_*_MODEL: glm-5-turbo / glm-5.1` — model emulation map.
- A full hook chain pointing at `~/.claude/agent-flow/hook.js`.
- A long `enabledPlugins` list (`feature-dev`, `agent-sdk-dev`, etc.).
- `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS: 1`.
- `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: 1`.
- `extraKnownMarketplaces`, `plansDirectory`, `skipDangerousModePermissionPrompt`, etc.

**What runs here:**

- Universal Agent itself (gateway, Simone, Atlas, etc.).
- Cody when she's doing routine UA coding.
- The ClaudeDevs intel cron (`claude_code_intel_sync` — it polls X, runs URL enrichment, writes packets, replays, builds the rolling brief and capability library — all using the ZAI mapping for the LLM calls).
- The dependency-currency drift sweep (`dependency_currency_sweep.py`) — observation only, doesn't need real Anthropic.
- Phase 0 release-announcement detection in `classify_post`.

**Why ZAI?** Cost. UA does a lot of LLM calls and GLM-via-ZAI is materially cheaper than direct Anthropic API. For everything that doesn't depend on bleeding-edge Anthropic feature parity, ZAI is the right call.

---

### The demo workspaces (Anthropic-native environment)

**Location:** `/opt/ua_demos/<demo-id>/` on the VPS. Override via `UA_DEMOS_ROOT` env var. **Not** in any git repo — these are runtime artifacts, not code-under-version-control.

**Settings file:** project-local `.claude/settings.json` inside each `<demo-id>/` directory. Vanilla, minimal — explicitly free of:

- `env` block (no ZAI redirect)
- `hooks` (no UA agent-flow chain)
- `enabledPlugins` (no UA plugins)
- `extraKnownMarketplaces`
- `plansDirectory`
- `skipDangerousModePermissionPrompt`
- `model` pin
- `statusLine`

The provisioner (`services/demo_workspace.py:verify_vanilla_settings`) refuses to declare a workspace ready if any of these `POLLUTION_INDICATORS` are present. This is a **fail-loud** safety net — a future scaffold edit accidentally re-introducing a polluted key would block provisioning rather than silently producing demos that hit the wrong endpoint.

**Authentication:** Max plan OAuth session, established once on the VPS via `claude /login` from inside `/opt/ua_demos/_smoke/`. The session token persists in the user's home directory; Cody and Simone never re-authenticate.

**What runs here:**

- Cody's Phase 3 demo implementation (PR 9 — `cody-implements-from-brief`).
- Phase 0's smoke-test demo at `/opt/ua_demos/_smoke/` after every Anthropic SDK upgrade (PR 6b's actuator gates on this).

**Why Anthropic-native?** Two reasons. First, brand-new features may not exist in the ZAI proxy. Second, even when they do, we can't trust the proxy's behavior to match the real API exactly — and the whole point of demos is to validate real Claude Code / Claude Agent SDK functionality so we can lift those patterns into client engagements.

---

## How Claude Code picks which environment

Claude Code reads settings.json with a clear precedence:

1. **Project-local** `.claude/settings.json` in the current working directory (and ancestors).
2. **User-global** `~/.claude/settings.json`.
3. **Built-in defaults**.

A demo subprocess that does `cd /opt/ua_demos/<demo-id>/` before invoking the CLI inherits the project-local vanilla settings, which override the user-global polluted settings. As long as no `ANTHROPIC_*` env vars leak from the parent shell, the demo hits real Anthropic.

The smoke demo (`/opt/ua_demos/_smoke/smoke.py`) explicitly verifies `endpoint == api.anthropic.com` and exits with code `2` on mismatch. This catches env-leak regressions before they corrupt a real demo run.

---

## Common pitfalls and their failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Demo runs to completion but produces output that uses an old API surface | Settings precedence broke — demo hit ZAI, which doesn't yet have the new feature | Verify `cd /opt/ua_demos/<demo-id>/` happened before invoking `claude`. Run `smoke.py`. |
| `smoke.py` exits with code `2` (`endpoint_mismatch`) | An `ANTHROPIC_BASE_URL` env var is set in the parent shell | `unset ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_DEFAULT_*_MODEL` in the demo wrapper. Long-term: run Cody as a systemd service with explicit `Environment=` clause that doesn't inherit. |
| `smoke.py` exits with code `1` (auth) | Max plan session expired or `claude /login` was never run | Re-run the runbook step 3: `cd /opt/ua_demos/_smoke && claude /login`. Critical: do this from inside the demo workspace, NOT from a directory that inherits ZAI-mapped settings. |
| `provision_smoke_workspace()` raises `ValueError: settings.json carries pollution markers` | Someone edited `templates/_smoke_demo/.claude/settings.json` and re-introduced a forbidden key | Remove the offending key from the template; the safety net is doing its job. |
| New ClaudeDevs intel cron runs are using GLM models even for the ingest's research grounding LLM calls | This is **correct** by design — Phase 1 (discovery + research grounding) and Phase 4 (Simone review) run on ZAI for cost. Only Phase 3 demo execution needs Anthropic-native. | No fix needed unless you specifically want Phase 1 LLM calls on Anthropic — in which case override at the script level, not by polluting the demo workspace. |

---

## Dependency currency across both environments

Both environments must run the same versions of Anthropic-adjacent packages so a demo built on one machine behaves the same on another. The Phase 0 dependency-currency layer keeps drift visible:

- **Daily sweep** (`dependency_currency_sweep.py`) reports outdated `claude-code` CLI, `claude-agent-sdk-python`, `claude-agent-sdk-typescript`, `anthropic`, `@anthropic-ai/sdk`. Writes to `vault/infrastructure/version_drift.md`.
- **Release detection** (in `classify_post`) flags `@ClaudeDevs` tweets that announce new versions, attaching structured `release_info` to the action.
- **Upgrade actuator (PR 6b, pending)** will: bump the manifest, run smoke tests against **both environments** (ZAI smoke verifies UA's normal operation still works; Anthropic-native smoke verifies demo path still works), deploy via the existing `develop → main` GitHub Actions pipeline, email Kevin on every change. Rollback on either smoke fail.

The dual-environment smoke matrix is the single most important guardrail in Phase 0. An upgrade that breaks ZAI breaks all of UA. An upgrade that breaks Anthropic-native breaks demos. Both have to pass before anything ships.

---

## Generalization to other lanes

The dual-environment pattern generalizes. When we add the `openai-codex-intelligence` lane (currently a disabled template in [`config/intel_lanes.yaml`](../../src/universal_agent/config/intel_lanes.yaml)), demo execution will need a third environment — `openai_native` — with its own auth path. Same shape, same provisioner, different `endpoint_required` value on the entity page frontmatter and a different config home for the OpenAI Agents SDK.

The lane config already has `demo_endpoint_profile` as a field for this reason. PR 7's provisioner reads it implicitly today (only `anthropic_native` is supported), but the surface is in place for future expansion.

---

## Quick reference

```
┌─────────────────────────────────────────────────────────────────────────┐
│ VPS at / (or /opt/, or wherever the deploy lands)                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  /opt/universal_agent/  ← UA repo (git-tracked)                         │
│      ├── src/universal_agent/        Code                               │
│      ├── docs/                       This documentation                 │
│      └── .claude/                    NOTHING here — UA settings are     │
│                                      user-global, not project-local     │
│                                                                         │
│  ~/.claude/settings.json  ← UA's polluted settings (ZAI-mapped)         │
│      • ANTHROPIC_BASE_URL → ZAI proxy                                   │
│      • Hooks → agent-flow                                               │
│      • Plugins, marketplaces, etc.                                      │
│                                                                         │
│  /opt/ua_demos/  ← Demo execution root (NOT git-tracked, runtime only)  │
│      ├── _smoke/                     Phase 0 smoke demo                 │
│      │   ├── .claude/settings.json   Vanilla — no env, no hooks         │
│      │   ├── smoke.py                Verifies api.anthropic.com hit     │
│      │   └── README.md                                                  │
│      ├── <demo-id-1>/                One real demo                      │
│      │   ├── .claude/settings.json   Vanilla                            │
│      │   ├── BRIEF.md                Authored by Simone (Phase 2)       │
│      │   ├── ACCEPTANCE.md           Authored by Simone                 │
│      │   ├── business_relevance.md   Authored by Simone                 │
│      │   ├── SOURCES/                Curated raw docs                   │
│      │   ├── src/                    Cody's implementation              │
│      │   ├── BUILD_NOTES.md          Cody documents gaps (no invention) │
│      │   ├── manifest.json           Endpoint hit, versions used        │
│      │   └── run_output.txt          Captured stdout                    │
│      └── <demo-id-2>/  ...                                              │
│                                                                         │
│  ~/.claude/                                                              │
│      └── (Max plan OAuth session token, set up via `claude /login`      │
│           from inside /opt/ua_demos/_smoke/)                            │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘

         ZAI environment ←──────┬──────→ Anthropic-native environment
        (cheap, default)        │           (real, demos only)
                                │
                  Distinction = working directory at invocation time +
                  no leaked ANTHROPIC_* env vars in the demo subprocess
```

## Related docs

- [ClaudeDevs X Intelligence System](../02_Subsystems/ClaudeDevs_X_Intelligence_System.md) — full subsystem reference
- [ClaudeDevs X Intel v2 Design](../proactive_signals/claudedevs_intel_v2_design.md) §3, §8 — design rationale
- [Demo Workspace Provisioning Runbook](../operations/demo_workspace_provisioning.md) — one-time setup
- [Model Choice and Resolution](../01_Architecture/10_Model_Choice_And_Resolution.md) — Anthropic-to-ZAI mapping details
- [ZAI / OpenAI-Compatible Setup](../ZAI_OPENAI_COMPATIBLE_SETUP.md) — ZAI proxy configuration reference
