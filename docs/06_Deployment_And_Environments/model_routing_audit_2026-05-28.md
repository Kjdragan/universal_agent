# Model-Routing Audit — ZAI Emulation vs Real Anthropic

**Date:** 2026-05-28
**Author:** Claude Code (parallel-agent codebase audit)
**Question:** Confirm that UA's runtime inference is done via the ZAI proxy (GLM models emulating Claude), and that the **only** path using the real Anthropic endpoint + real Anthropic models is the **Cody** agent. Flag any other place the real Anthropic endpoint/models are used.

---

## TL;DR

- **The core mental model is correct.** In the running UA daemon, default inference routes to the **ZAI proxy** (`https://api.z.ai/api/anthropic`), where **GLM-5.1 emulates `opus`** and **GLM-5-turbo emulates `sonnet`/`haiku`**. Every direct Anthropic SDK client in the codebase inherits this routing.
- **Cody's anthropic mode does use real Anthropic** (`api.anthropic.com`, Max-plan OAuth, model `claude-opus-4-8`). Still true.
- **But "Cody is the only exception" is not accurate.** Real Anthropic is also reached by: (2) Kevin's interactive `claude` launcher, (3) Cody **demo workspaces** (a second Cody path), (4) the dependency-upgrade / `/opt/ua_demos/_smoke` Anthropic-native smoke tests, and — formerly — (5) **one autonomous, non-Cody endpoint that hardcoded `api.anthropic.com`** (the vision endpoint, now fixed — see below). A handful of bare `Anthropic()` call sites in skills/test scripts are **environment-dependent** (ZAI in the daemon, real Anthropic if run from a scrubbed shell).
- **One landmine — now fixed (this PR):** `gateway_server.py` `/api/v1/vision/describe` hardcoded `https://api.anthropic.com` and was triple-misconfigured. It is **not dead** — it backs the dashboard's image-paste-to-vision feature (`web-ui/components/dashboard/SimoneChatBar.tsx:125-176`). Rewired to route through `ANTHROPIC_BASE_URL` (ZAI) like the rest of the daemon.

---

## How the routing actually works (the mechanism)

Routing is **not** decided by the model string — it's decided by the `ANTHROPIC_BASE_URL` environment variable, which the official Anthropic SDK reads at client-construction time.

1. **Daemon startup injects ZAI env from Infisical.** `initialize_runtime_secrets()` (`src/universal_agent/infisical_loader.py:394`) fetches secrets and writes them onto `os.environ` via `_inject_environment_values()` (`infisical_loader.py:124-153`, `os.environ[key]=value` at `:144`, `overwrite=True`). The injected keys include `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY` (the ZAI key), and `ZAI_API_KEY`.
2. **Production base URL ≈ `https://api.z.ai/api/anthropic`.** Not stored in the repo (lives in Infisical), but every in-repo default literal and doc agrees: e.g. `tools/corpus_refiner.py:51`, `services/csi_demo_triage_ranker.py:156`, `docs/ZAI_OPENAI_COMPATIBLE_SETUP.md:83`. `deploy.yml`'s `.env` bootstrap contains **no** ANTHROPIC/ZAI values — they only ever come from Infisical.
3. **Model strings resolve to GLM by default.** `utils/model_resolution.py:30-34` — `ZAI_MODEL_MAP = {haiku: glm-5-turbo, sonnet: glm-5-turbo, opus: glm-5.1}`. `resolve_opus()` → `glm-5.1`, `resolve_sonnet()`/`resolve_haiku()` → `glm-5-turbo`.
4. **The standard SDK idiom (~25 sites) passively follows the daemon env:**
   ```python
   api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ZAI_API_KEY")
   base_url = os.getenv("ANTHROPIC_BASE_URL")
   client = Anthropic(api_key=api_key, **({"base_url": base_url} if base_url else {}))
   ```
   No site scrubs `ANTHROPIC_BASE_URL`; they only pass it when present. In the daemon it's always present → **ZAI**.
5. **The Claude Agent SDK path is also ZAI.** `agent_setup.py:_build_options` forces `model="glm-5.1"` (`:411`) and sets `ANTHROPIC_DEFAULT_*_MODEL` to GLM strings (`:429-431`); the spawned Claude Code CLI subprocess inherits `ANTHROPIC_BASE_URL` from `os.environ` (the subprocess sanitizer `execution_engine.py:217-229` is a blocklist that deliberately keeps `ANTHROPIC_*`).

**→ Conclusion: a bare `Anthropic()` in any daemon process hits ZAI, because the SDK reads `ANTHROPIC_BASE_URL` from the env that Infisical populated.**

---

## How Cody reaches real Anthropic (confirmed still true)

- **Mode resolution** — `services/cody_mode.py`: `_HARDCODED_FALLBACK_MODE = "anthropic"` (`:37`, flipped from `"zai"` 2026-05-11). Precedence: per-task `cody_mode` → DB `cody_default_mode` → `UA_CODY_DEFAULT_MODE` env → hardcoded `"anthropic"` (`:67-99`).
- **Forced to the CLI path** — `tools/vp_orchestration.py:442-454`: when resolved mode is `"anthropic"`, execution is forced to `execution_mode="cli"` (the SDK path is never used for anthropic mode).
- **Env scrub + OAuth** — `vp/clients/claude_cli_client.py:_build_cli_env` (`:948-977`): builds the subprocess env as `{k:v for k,v in os.environ.items() if not k.startswith("ANTHROPIC_")}` (removes the ZAI base URL **and** key), forces `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, injects `CLAUDE_CODE_OAUTH_TOKEN` (Max OAuth), and `pop`s any residual `ANTHROPIC_API_KEY`. With no base URL, `claude` defaults to `api.anthropic.com`.
- **Model** — `claude_cli_client.py:319-322`: `--model` is set only in anthropic mode, default `os.getenv("UA_CODY_CLI_MODEL", "claude-opus-4-8")`.
- **Demo workspaces** (a *second* Cody real-Anthropic path) — `services/cody_implementation.py:_scrubbed_env` (`:283-294`) strips all `ANTHROPIC_*` before spawning `claude`; the scaffold `templates/ua_demos_scaffold/.claude/settings.json` is intentionally empty of ZAI overrides.

---

## Every place the REAL Anthropic endpoint is (or could be) reached — NOT just Cody

| # | Site | path:line | Real Anthropic? | Status / Notes |
|---|---|---|---|---|
| 1 | **Cody — CLI anthropic mode** | `vp/clients/claude_cli_client.py:948-977,319` | ✅ Yes | Intended. Scrubs `ANTHROPIC_*`, OAuth, `--model claude-opus-4-8`. |
| 2 | **Cody — demo workspaces** | `services/cody_implementation.py:283-294,446` | ✅ Yes | Intended (also Cody). `_scrubbed_env` + vanilla `.claude/settings.json`. |
| 3 | **Kevin's interactive `claude`** | `scripts/_claude_launcher.py:166-167,238` | ✅ Yes | Intended. `initialize_runtime_secrets(exclude_prefixes=("ANTHROPIC_",))` → Max OAuth. This is the *interactive coding* profile, not autonomous inference. |
| 4 | **Anthropic-native smokes** | `services/dependency_upgrade.py` (`run_anthropic_native_smoke`), `/opt/ua_demos/_smoke/smoke.py` | ✅ Yes (by design) | Verification scaffolding for the demo path. Note: the *ZAI* smoke in the same file (`run_zai_smoke`, snippet at `dependency_upgrade.py:307-324`, model `claude-haiku-4-5`) routes through ZAI because it runs in the daemon env. |
| 5 | **Vision endpoint** `/api/v1/vision/describe` | `gateway_server.py:29263-29333` | ✅ Now ZAI (fixed) | Formerly hardcoded `api.anthropic.com` + triple-misconfigured. **Live caller:** dashboard image-paste-to-vision (`SimoneChatBar.tsx:125-176`). Rewired this PR to route via `ANTHROPIC_BASE_URL` (ZAI/`glm-5.1`). See below. |
| 6 | **CSI_Ingester dedicated lane (mode 1)** | `CSI_Ingester/.../llm_auth.py:38` | ⚠️ Ambiguous | `CSI_ANTHROPIC_*` keys *could* point at real Anthropic, but defaults to mode 0 (shared UA env = ZAI). Currently config-gated/unused. |
| 7 | **Bare `Anthropic()` in skills/test scripts** | `.claude/skills/mcp-builder/scripts/evaluation.py:227`, `.claude/skills/skill-creator/scripts/{improve_description.py:219,run_loop.py:78}`, `scripts/analyze_channels.py:44`, `tests/letta/*` (5) | ⚠️ Env-dependent | No `base_url`/`api_key` kwargs. Run inside the daemon → ZAI; run from a scrubbed/interactive shell → real Anthropic. Not production autonomous inference; routing is not explicitly controlled. |

Everything else — ~25 direct SDK clients across `services/`, `URW/`, `RLM/`, `discord_intelligence/`, `scripts/`, and the Agent-SDK / CLI-zai paths — is **ZAI-routed**. (Full per-site table available from the audit; the dominant idiom is item-4 in the mechanism section.)

---

## The vision endpoint — FIXED this PR (`gateway_server.py:29263-29333`)

`/api/v1/vision/describe` was the sole autonomous, non-Cody code path that bypassed ZAI and pointed at the real Anthropic endpoint. **It is not dead** — it backs the dashboard's image-paste-to-vision feature: pasting an image into Simone's chat bar (`web-ui/components/dashboard/SimoneChatBar.tsx:125-176`) base64-encodes it, POSTs here with "Describe this image in detail.", and inlines the returned description as `[Attached Image Description: …]`. It is also documented in `docs/04_API_Reference/Ops_API.md`.

It was misconfigured three ways and could not succeed (the paste feature errored out):

1. **`NameError` before the call.** `model = resolve_opus()` had a documented `# noqa: F821` / `FIXME` — `resolve_opus` was **not imported** in `gateway_server.py` (tracked as issue #177). Any call raised `NameError` before the HTTP request fired.
2. **Wrong credential.** It sent `x-api-key: os.getenv("ANTHROPIC_API_KEY")` to `api.anthropic.com` — but in the daemon `ANTHROPIC_API_KEY` is the **ZAI** key → would 401 even if (1) were fixed.
3. **Wrong model.** Even with valid auth, `model` would be `glm-5.1` (a GLM string) sent to real Anthropic → 400.

**Fix applied (ZAI rewire — consistent with the rest of the daemon):**
- Added `resolve_opus` to the existing `from universal_agent.utils.model_resolution import …` import and removed the `# noqa: F821` (closes the NameError / issue #177).
- Replaced the hardcoded `https://api.anthropic.com/v1/messages` with `f"{base_url}/v1/messages"` where `base_url = os.getenv("ANTHROPIC_BASE_URL") or "https://api.z.ai/api/anthropic"` → the ZAI key now goes to the ZAI endpoint and `glm-5.1` is understood there (vision via the GLM-5.x lane).
- Broadened the key resolution to the standard fallback chain (`ANTHROPIC_API_KEY` → `ANTHROPIC_AUTH_TOKEN` → `ZAI_API_KEY`).

**Future option (not taken):** to use real Anthropic high-res Opus 4.8 vision instead, point `ANTHROPIC_BASE_URL` at `api.anthropic.com` with a dedicated console key and set an explicit `claude-opus-4-8` model at this call site. That spends real Anthropic credits, so it's an explicit operator decision rather than the default.

---

## Documentation drift noted during the audit (informational)

- **Stale model IDs.** The canonical docs (`09_Demo_Execution_Environments.md`, `10_Interactive_Coding_Environment.md`, root `CLAUDE.md`) pin "Opus 4.7 / Sonnet 4.6 / Haiku 4.5" throughout. Newest Opus is **4.8** (and Cody's CLI default is now `claude-opus-4-8`). Treat the doc version pins as illustrative.
- **"Two environments" vs "three profiles" conflict.** `09_Demo_Execution_Environments.md` (lines 5, 21) frames Anthropic as "demos only" (TWO environments); `CLAUDE.md` + `10_Interactive_Coding_Environment.md` describe THREE profiles (autonomous ZAI / interactive Anthropic / Cody Anthropic). `09_…md` predates the 2026-05-11 Cody flip and the interactive inversion and its §"What about Cody on her main UA work" (lines 244-263) still describes the pre-flip ZAI-by-cwd behavior.
- **Haiku map disagreement.** `docs/01_Architecture/10_Model_Choice_And_Resolution.md:49` says `haiku→GLM-4.5-Air`, but `model_resolution.py:31` and `secrets_and_environments.md:144` use `glm-5-turbo`. Code (env-var override) wins; the architecture doc is stale.

These don't affect routing behavior but should be reconciled in a follow-up doc pass.

---

## Verification method

- Read the routing mechanism directly: `utils/model_resolution.py`, `infisical_loader.py`, `agent_setup.py`, `execution_engine.py`.
- Read the Cody paths directly: `cody_mode.py`, `claude_cli_client.py`, `cody_implementation.py`, `vp_orchestration.py`.
- Enumerated every `Anthropic(`/`AsyncAnthropic(` instantiation and `claude` CLI/SDK spawn across `src/`, `URW/`, `RLM/`, `discord_intelligence/`, `scripts/`, `security_evals/`, `.claude/skills/`, `tests/`, `CSI_Ingester/`.
- Manually confirmed the vision-endpoint finding by reading `gateway_server.py:29263-29314`.
- **Not verified:** the live Infisical value of `ANTHROPIC_BASE_URL` in production (requires a secret read). All in-repo evidence is consistent that it is `https://api.z.ai/api/anthropic`.
