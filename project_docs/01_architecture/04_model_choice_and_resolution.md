---
title: Model Choice & Resolution
status: active
canonical: true
subsystem: arch-model-resolution
code_paths:
  - src/universal_agent/utils/model_resolution.py
  - src/universal_agent/services/cody_mode.py
  - src/universal_agent/vp/clients/claude_cli_client.py
  - src/universal_agent/tools/vp_orchestration.py
  - src/universal_agent/agent_setup.py
  - scripts/_claude_launcher.py
  - scripts/claude_with_mcp_env.sh
  - src/universal_agent/services/invariants/zai_inference_health.py
last_verified: 2026-06-10
---

# Model Choice & Resolution

This subsystem answers two distinct questions:

1. **Which model string do we hand to the Anthropic SDK / Claude Code?** — the
   tier resolvers in `utils/model_resolution.py` (`resolve_opus` / `resolve_sonnet`
   / `resolve_haiku`) plus the ZAI tier→model map.
2. **Which *endpoint and credential* does a given execution use?** — ZAI/GLM proxy
   (cheap, via `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` on `os.environ`) vs. real
   Anthropic Max (OAuth, via env-scrub). This is governed by the **three execution
   profiles** and, for Cody specifically, by `cody_mode`.

Inference health governance (`zai_inference_health` invariant) watches the ZAI side
for throttling and ban risk.

These two axes are orthogonal. The tier resolvers always return a model *name*; what
that name means (a GLM model on ZAI, or an Anthropic model on Max) depends entirely
on which `ANTHROPIC_*` env vars are present in the process — which is what the
execution profile and env-scrub logic decide.

> **The single most important fact: routing is decided by `ANTHROPIC_BASE_URL`, not
> by the model string.** The official Anthropic SDK reads `ANTHROPIC_BASE_URL` at
> client-construction time. In the daemon, `initialize_runtime_secrets()` injects
> `ANTHROPIC_BASE_URL` (≈ `https://api.z.ai/api/anthropic`), `ANTHROPIC_AUTH_TOKEN`,
> `ANTHROPIC_API_KEY` (the **ZAI** key), and `ZAI_API_KEY` onto `os.environ`. So a
> bare `Anthropic()` anywhere in a daemon process hits ZAI — the model string
> `glm-5.1` is simply understood by the ZAI endpoint. To reach *real* Anthropic you
> must remove `ANTHROPIC_BASE_URL` (and the ZAI key) so the SDK/CLI falls back to
> `api.anthropic.com` + OAuth. That removal is exactly what the interactive launcher
> and the Cody anthropic-mode env-scrub do. The base URL value lives only in Infisical
> — `deploy.yml`'s `.env` bootstrap contains **no** ANTHROPIC/ZAI values.

---

## 1. Tier resolution — `utils/model_resolution.py`

UA code never hardcodes a model string. It calls a tier resolver. The canonical map
lives in `model_resolution.py::ZAI_MODEL_MAP`:

```python
ZAI_MODEL_MAP = {
    "haiku": "glm-4.5-air",     # Operator-locked.
    "sonnet": "glm-5-turbo",    # Z.AI standard model.
    "opus": "glm-5.1",          # Z.AI flagship model (NOT glm-5-1 — dash breaks it).
}
```

`resolve_model(tier)` is the core:

- For `tier="haiku"` it reads `ANTHROPIC_DEFAULT_HAIKU_MODEL`; `"sonnet"` reads
  `ANTHROPIC_DEFAULT_SONNET_MODEL`; anything else reads `ANTHROPIC_DEFAULT_OPUS_MODEL`.
- If that env var is set and non-empty, the env value wins. **Otherwise** it falls
  back to `ZAI_MODEL_MAP[tier]` (defaulting to the sonnet entry for unknown tiers).

The thin wrappers express intent at call sites:

| Resolver | Returns (default) | Notes |
|---|---|---|
| `resolve_haiku()` | `glm-4.5-air` | `model_resolution.py::resolve_haiku` |
| `resolve_sonnet()` | `glm-5-turbo` | `model_resolution.py::resolve_sonnet` |
| `resolve_opus()` | `glm-5.1` | `model_resolution.py::resolve_opus` |
| `resolve_claude_code_model(default="sonnet")` | tier passthrough | the string passed to claude-agent-sdk |

### Haiku and sonnet resolve to different models

`resolve_haiku()` returns `glm-4.5-air` (operator-locked; verified working) and
`resolve_sonnet()` returns `glm-5-turbo`. The Claude Agent SDK makes small **internal
preflight calls** (system-prompt cache management, compaction routing, tool-selection
classifier) on the haiku tier, which is why the haiku tier exists as its own lane.
`resolve_haiku()` is kept as a separate function so the haiku tier can be tuned without
touching every caller. The haiku tier is operator-locked to `glm-4.5-air`; do not
remap it.

### Gotcha: `resolve_sonnet()` no longer secretly returns opus

The docstring on `resolve_sonnet` notes it was historically overridden to return opus,
silently promoting every direct caller to the expensive flagship. That override was
removed — **sonnet now means sonnet** (`glm-5-turbo`). If you see old docs claiming
"sonnet maps to opus," the code contradicts them; the code wins.

### The default daemon tier is opus

`agent_setup.py` builds the daemon's `ClaudeAgentOptions` with
`model=resolve_claude_code_model(default="opus")` — i.e. the in-process daemon
(Simone, Atlas, dispatch sweep, etc.) runs on **opus / glm-5.1** by default. It also
forces the SDK's internal preflight model env vars into the subprocess env so the SDK
picks up the central mappings regardless of external env:

```python
"ANTHROPIC_DEFAULT_HAIKU_MODEL": resolve_haiku(),       # glm-4.5-air
"ANTHROPIC_DEFAULT_SONNET_MODEL": resolve_model("sonnet"),
"ANTHROPIC_DEFAULT_OPUS_MODEL": resolve_claude_code_model(default="opus"),
```

> Note on the resolver default vs. the daemon default: `resolve_model()` /
> `resolve_claude_code_model()` *default to "sonnet"* when called with no argument
> (operator decision), but `agent_setup.py` explicitly passes
> `default="opus"`, so the actual daemon main-agent model is opus. Subagents that
> prefer sonnet get it via their own `.claude/agents/*.md` YAML.

### Per-tier wall-clock timeouts

`model_call_timeout_seconds(tier)` returns the per-turn cap, overridable via
`UA_MODEL_TIMEOUT_<TIER>_SECONDS` (set to `0` to disable). Defaults
(`_TIER_DEFAULT_TIMEOUTS`):

| Tier | Default cap | Rationale |
|---|---|---|
| haiku | 120 s | SDK preflight + tiny tasks; a failed cheap-tier call should fail fast |
| sonnet | 180 s | Daily-driver multi-tool turns |
| opus | 1800 s | Heavy research / multi-doc synthesis / long crons — generous on purpose |

Per-request override is `GatewayRequest.metadata["turn_timeout_seconds"]` (consumed in
`execution_engine.py`) — the recommended knob for a single slow workflow, rather than
dragging the global default up.

### Mission Control dedicated lane

Mission Control intelligence (tier-0 annotations, card discovery, page synthesis,
event-title templates) runs on its **own** model lane via
`resolve_mission_control_model()` so it doesn't consume opus/sonnet concurrency budget.
Default `MISSION_CONTROL_DEFAULT_MODEL = "glm-4.7"` (override `UA_MISSION_CONTROL_MODEL`;
documented fallback `glm-5-turbo`). This **bypasses `ZAI_MODEL_MAP` entirely** — the
value is passed straight to `AsyncAnthropic(model=...)`. Its per-call timeout is
`mission_control_call_timeout_seconds()` (default 180 s, override
`UA_MISSION_CONTROL_CALL_TIMEOUT_SECONDS`, `0` disables).

### Agent Teams flag

`resolve_agent_teams_enabled(default=True)` resolves whether Claude Code Agent Teams is
on. Precedence: `UA_AGENT_TEAMS_ENABLED` → `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` →
default `True`.

---

## 2. The three execution profiles

UA runs Claude under three distinct profiles. Confusing them is the single most common
source of model-routing mistakes. The profile decides **endpoint + credential**, which
in turn decides what a tier name resolves to in practice.

| # | Profile | Endpoint / models | How `ANTHROPIC_*` is handled | Entry point |
|---|---|---|---|---|
| 1 | **Interactive coding** (Kevin's `claude` / Antigravity) | Anthropic Max via OAuth (real Opus/Sonnet/Haiku) | `ANTHROPIC_*` **excluded/stripped** so OAuth wins | `scripts/claude_with_mcp_env.sh` → `scripts/_claude_launcher.py` |
| 2 | **UA autonomous in-process** (Simone heartbeats, Atlas, dispatch sweep, intel crons) | ZAI proxy / GLM (glm-5.1 opus, glm-5-turbo sonnet) | `ANTHROPIC_*` ZAI-routing vars **kept** on `os.environ` (loaded at service start) | `agent_setup.py` `ClaudeAgentOptions` |
| 3 | **Cody per-task CLI subprocess** | Anthropic Max by default (or ZAI when `cody_mode="zai"`) | `ANTHROPIC_*` scrubbed when `cody_mode=="anthropic"` | `vp/clients/claude_cli_client.py::_build_cli_env` |

The governing principle: **any `ANTHROPIC_*` key on `os.environ` overrides OAuth.**
`ANTHROPIC_API_KEY` makes Claude Code treat it as an external API key (and reject it
with "Invalid API key · Fix external API key" if it's for the wrong account/billing);
`ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` route to ZAI/GLM. So:

- Profiles that want **ZAI** keep those vars present (profile 2 — loaded at startup by
  `initialize_runtime_secrets()`).
- Profiles that want **Anthropic Max** strip them so the CLI falls through to
  workspace-local OAuth (`~/.claude/.credentials.json`) or a forwarded
  `CLAUDE_CODE_OAUTH_TOKEN` (profiles 1 and 3-anthropic).

```mermaid
flowchart TD
    A[Claude invocation] --> B{Execution profile}
    B -->|Interactive: claude_with_mcp_env.sh| C[_claude_launcher.py]
    C --> C1["initialize_runtime_secrets(exclude_prefixes=('ANTHROPIC_',))"]
    C1 --> C2["strip leaked ANTHROPIC_* + GH_TOKEN/GITHUB_TOKEN"]
    C2 --> C3["execvp claude → resolves Anthropic Max OAuth"]

    B -->|In-process daemon| D["agent_setup.py ClaudeAgentOptions"]
    D --> D1["ANTHROPIC_* ZAI-routing vars present on os.environ"]
    D1 --> D2["SDK calls route to ZAI / GLM (opus=glm-5.1)"]

    B -->|Cody CLI mission| E["vp_orchestration.vp_dispatch_mission"]
    E --> E1{resolve_cody_mode}
    E1 -->|anthropic| E2["force execution_mode=cli"]
    E2 --> E3["_build_cli_env scrubs ANTHROPIC_*, sets OAuth + Agent Teams"]
    E3 --> E4["claude --model claude-opus-4-8 → Anthropic Max"]
    E1 -->|zai| E5["execution_mode=sdk, inherit ZAI routing"]
```

### Profile 1 — interactive coding launcher

`scripts/claude_with_mcp_env.sh` exists because an interactive `claude` invocation does
not run UA's secret bootstrap, so `${VAR}` placeholders in `.mcp.json` would substitute
to empty and MCP children would fail. The wrapper:

- Auto-detects `UA_INSTALL_ROOT` (`/opt/universal_agent`, else the repo containing the
  script).
- Auto-injects `--dangerously-skip-permissions` for interactive sessions, but **skips
  that flag for management subcommands** (`agents`, `auth`, `auto-mode`, `doctor`,
  `install`, `mcp`, `plugin(s)`, `project`, `setup-token`, `ultrareview`,
  `update`/`upgrade`) which would reject it.
- Preserves the caller's CWD via `UA_ORIGINAL_CWD` (it must `cd` into UA for `uv run`,
  but the launcher `os.chdir`'s back before `execvp`).

`scripts/_claude_launcher.py` then:

1. Sources `$UA_INSTALL_ROOT/.env` bootstrap creds (without overwriting existing env).
2. Calls `initialize_runtime_secrets(exclude_prefixes=("ANTHROPIC_",))` — the entire
   `ANTHROPIC_*` namespace is filtered out **at the Infisical-injection step** so those
   vars never enter `os.environ`.
3. **Defense-in-depth strip** (`_strip_interactive_routing_vars`): removes any
   `ANTHROPIC_*` that leaked from a non-Infisical source (bootstrap `.env`, parent
   shell).
4. **Also strips `GH_TOKEN` / `GITHUB_TOKEN`** (`_strip_named_interactive_vars`): a
   stale/expired Infisical `GH_TOKEN` was overriding the file-stored `gh` OAuth
   (`~/.config/gh/hosts.yml`) and breaking every interactive `gh` call (and therefore
   `/ship`'s in-script deploy watching). Crons/services still get these vars — they
   don't go through this launcher.
5. Runs a git baseline check, then `execvp`'s `claude` with the bootstrapped env. With
   `ANTHROPIC_*` gone, the CLI resolves to Anthropic Max OAuth.

> [VERIFY: explicit ZAI opt-in for interactive sessions is described as a `zai` shell
> function in the launcher docstring; that function lives in shell config, not in this
> repo path set, so it is not code-verified here.]

### Profile 2 — UA autonomous in-process (ZAI)

The daemon principals run inside the UA process. `initialize_runtime_secrets()` is
called **without** `exclude_prefixes`, so the ZAI routing vars (`ANTHROPIC_BASE_URL`,
`ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_DEFAULT_*_MODEL`) and `ANTHROPIC_API_KEY` (for
direct-SDK code paths like the vision endpoint and refinement agent) stay on
`os.environ`. `agent_setup.py` builds `ClaudeAgentOptions` on top of that, with the
opus default and the forced preflight-model env vars described above. There is no
per-task model switch — this profile is heartbeat-driven and ZAI-routed by design.

### Profile 3 — Cody per-task CLI subprocess

See §3.

### "Cody is the only real-Anthropic path" is NOT accurate

The common shorthand "ZAI everywhere except Cody" is wrong. Real Anthropic
(`api.anthropic.com`, Max OAuth) is reached by several paths (verified in the
2026-05-28 routing audit and re-confirmed in code):

1. **Cody CLI anthropic mode** — `claude_cli_client.py::_build_cli_env` (§3). Intended.
2. **Cody demo workspaces** — a *second* Cody real-Anthropic path.
   `services/cody_implementation.py::_scrubbed_env` strips all `ANTHROPIC_*` before
   spawning `claude`; the scaffold `templates/ua_demos_scaffold/.claude/settings.json`
   is intentionally empty of ZAI overrides. `/build_cli_env` explicitly mirrors this
   pattern.
3. **Kevin's interactive `claude`** — profile 1, the interactive *coding* profile (not
   autonomous inference).
4. **Demo-path smoke** — `/opt/ua_demos/_smoke/smoke.py` deliberately verifies the demo
   path against real Anthropic. The former dependency-upgrade Anthropic-native smoke
   (`run_anthropic_native_smoke`) was retired 2026-06-07 (#805); only
   `services/dependency_upgrade.py::run_zai_smoke` now gates upgrades, routing through ZAI
   because it runs in the daemon env.

Everything else (~25 direct SDK clients across `services/`, `urw/`, `discord_intelligence/`,
`scripts/`, and the Agent-SDK / CLI-zai paths) is ZAI-routed.

> **Gotcha — the vision endpoint was a former landmine, now ZAI.** `gateway_server.py`
> `/api/v1/vision/describe` (backs the dashboard image-paste-to-vision feature) once
> hardcoded `https://api.anthropic.com` and was triple-misconfigured (missing
> `resolve_opus` import → `NameError`; sent the ZAI key to real Anthropic; sent a GLM
> model string to real Anthropic). It is now rewired to route through
> `base_url = os.getenv("ANTHROPIC_BASE_URL") or "https://api.z.ai/api/anthropic"`
> with `model = resolve_opus()` (glm-5.1) — i.e. ZAI like the rest of the daemon. The
> code comment notes that pointing `ANTHROPIC_BASE_URL` at `api.anthropic.com` + a
> console key + an explicit `claude-*` model is the (cost-bearing) opt-in for real
> high-res Opus vision.

> **Operational fact — `CLAUDE_CODE_OAUTH_TOKEN` in Infisical is the canonical
> Cody-on-Anthropic credential.** The `/home/ua/.claude/.credentials.json` file on the
> VPS is orphan state from an old interactive session; nothing in production reads it.

> **Operational fact — ZAI peak-hours throttling.** The ZAI proxy's customer base is
> concentrated in Greater China; peak demand (Beijing business hours ~16:00–22:00 CST)
> overlaps US Central *night*. Heavy autonomous crons scheduled "overnight" US time hit
> ZAI capacity limits — the inverse of the usual "run batch overnight" intuition. The
> `/opt/ua_demos/` Anthropic-native path is immune to this throttling and is the
> documented emergency override (with a real-credit cost tradeoff).

---

## 3. Cody mode resolution — `services/cody_mode.py`

Every Cody task carries a `cody_mode` ∈ {`"zai"`, `"anthropic"`} that decides whether it
runs cheap (ZAI/GLM) or on real Anthropic Max.

`resolve_cody_mode(task, *, conn=None)` resolution order (highest priority first):

1. `task["cody_mode"]` — per-task override on `task_hub_items`.
2. DB setting `cody_default_mode` — operator-configurable via the dashboard tile,
   persisted in `task_hub_settings` (read through `_resolve_db_setting` →
   `task_hub._get_setting`). Requires a `conn` to the activity DB; skipped if `conn`
   is `None`.
3. `UA_CODY_DEFAULT_MODE` env var — deploy-time override, usually unset.
4. `_HARDCODED_FALLBACK_MODE = "anthropic"`.

> **Gotcha — the hardcoded default is `anthropic`, flipped from `zai` on 2026-05-11 PM**
> per an operator decision. Cody now runs on real Anthropic Max for *every* task unless
> explicitly overridden. Reverting requires a per-task `cody_mode="zai"`, the dashboard
> tile, or `UA_CODY_DEFAULT_MODE=zai`. (Older docs/memory may say "Cody normally runs on
> ZAI" — that's stale.)

`resolve_from_payload(payload)` is the downstream variant (VP worker, CLI client) used
after dispatch when the task row is out of scope. It reads `payload["cody_mode"]`, then
`payload["metadata"]["cody_mode"]` (vp_orchestration plumbs it under `metadata`), then
env, then the hardcoded default. It does **not** consult the DB setting — the dispatch
decision is already baked into the payload by then.

Dashboard plumbing: `set_default_mode(conn, mode, updated_by=...)` validates and writes
`{mode, updated_at, updated_by}` to `task_hub_settings` (raises `ValueError` on invalid
mode so the settings endpoint can 400). `get_default_mode_state(conn)` returns the
current mode + audit fields + a `source` of `db_setting` / `env_var` /
`hardcoded_default`. Both are wired into a gateway settings endpoint
(`gateway_server.py` near the `set_default_mode` / `get_default_mode_state` imports).

### cody_mode → execution_mode coupling (the source-of-truth rule)

`tools/vp_orchestration.py::vp_dispatch_mission` resolves `cody_mode` (explicit arg →
linked task row → `resolve_cody_mode`) and then enforces:

```python
explicit_exec_mode = str(args.get("execution_mode") or "").strip().lower()
if resolved_cody_mode == "anthropic":
    resolved_execution_mode = "cli"          # forced — Anthropic Max only via CLI
elif explicit_exec_mode:
    resolved_execution_mode = explicit_exec_mode
else:
    resolved_execution_mode = "sdk"
```

**`cody_mode="anthropic"` FORCES `execution_mode="cli"`** and *ignores* any explicit
`execution_mode` argument (with a warning). The rationale (in the code comment): the
Anthropic Max plan is only reachable through the workspace-local OAuth in the CLI
subprocess; running anthropic mode under the SDK/autonomous path would silently route
to ZAI anyway. To explicitly run autonomously, callers must pass `cody_mode="zai"`,
which conveys the intent properly. The resolved mode is plumbed into
`mission_metadata["cody_mode"]`.

### `_build_cli_env` — the env-scrub that makes Anthropic Max actually engage

`vp/clients/claude_cli_client.py::_build_cli_env(enable_agent_teams, workspace_dir, *, cody_mode="zai")`:

- **`cody_mode == "anthropic"`**: builds env as
  `{k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}` — every
  `ANTHROPIC_*` var scrubbed so the spawned `claude` falls through to OAuth. Then:
  - `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS = "1"` (forced — "Agent Teams is the whole
    point of Anthropic mode").
  - Forwards the long-lived Max OAuth token from `CLAUDE_CODE_OAUTH_TOKEN` (Infisical;
    legacy fallback `ANTHROPIC_MAX_OAUTH_TOKEN`) into the subprocess env, and
    explicitly `env.pop("ANTHROPIC_API_KEY", None)` because Claude Code prefers an API
    key over OAuth when both are present.
- **otherwise (`zai`)**: `env = dict(os.environ)` (inherits ZAI routing); Agent Teams
  set only if `enable_agent_teams`.
- Both paths set `CURRENT_RUN_WORKSPACE` / `CURRENT_SESSION_WORKSPACE`, pop
  `UA_INFISICAL_STRICT` (don't enforce Infisical in the subprocess), and forward
  `REPORT_MAX_CONCURRENT_AGENTS` if set.

> **Gotcha — OAuth token vs API key.** An earlier version translated the
> `claude setup-token` output into `ANTHROPIC_API_KEY`, which Claude Code rejected as
> "Invalid API key · Fix external API key" because an `sk-ant-oat01-...` OAuth token is
> not valid in the API-key auth slot. The token must be set as `CLAUDE_CODE_OAUTH_TOKEN`
> (it doesn't start with `ANTHROPIC_`, so it survives the scrub naturally; it's read
> explicitly to make the contract obvious).

### CLI model selection

In `_execute_cli_session`, model selection is applied **only when
`cody_mode == "anthropic"`** (ZAI/SDK paths have their own routing and would ignore
`--model`):

```python
if cody_mode == "anthropic":
    model_override = os.getenv("UA_CODY_CLI_MODEL", "claude-opus-4-8").strip()
    if model_override and model_override.lower() != "default":
        cmd.extend(["--model", model_override])
```

So Cody's Anthropic CLI work defaults to **`claude-opus-4-8`** (Opus 4.8). Override per
process with `UA_CODY_CLI_MODEL`; set it to `default` (or empty) to use the CLI's own
default (currently Sonnet, no `--model` flag).

> Demo workspaces (`/opt/ua_demos/<id>/`) add a second defense layer: a vanilla
> `.claude/settings.json` and the `services/cody_implementation._scrubbed_env` pattern
> that `_build_cli_env` mirrors.

---

## 4. Inference health governance — `zai_inference_health`

The `services/invariants/zai_inference_health.py` invariant (P4 of the watchdog
restoration) protects the ZAI side of the system: throttling that kills throughput and,
worse, Fair-Use-Policy (FUP) signals that risk a subscription ban. It runs each
heartbeat with no AI inference, no DB write, no HTTP — just a ~1 KB JSON read, a tail of
a JSONL events file, and one `pgrep`.

It reads two sources:

1. **ZAIRateLimiter snapshot** (`zai_inference_state.json`, written by `record_*` in
   `rate_limiter.py`) — covers in-band callers wrapped by `with_rate_limit_retry`.
2. **Universal P7 events JSONL** (`zai_inference_events.jsonl`, written by
   `zai_observability.py`'s httpx hook) — catches **direct-httpx callers that bypass
   `with_rate_limit_retry`**. This is the gap that hid the 2026-05-21 `session_dossier`
   49-request 429 burst from the watchdog.

One invariant emits at most one finding listing every triggered condition (in
`observed_value.triggered_conditions`) so a correlated bad day doesn't spam multiple
alerts. Conditions:

| Condition | Source | Severity | Default threshold (env override) |
|---|---|---|---|
| FUP signal in window | snapshot `last_fup_at` OR events `fup_signal` | **critical** (immediate, no grace) | 30 min — `UA_ZAI_FUP_DETECT_WINDOW_SECONDS` / `UA_ZAI_EVENTS_FUP_WINDOW_SECONDS` |
| Sustained consecutive 429s | snapshot `consecutive_429s` | **critical** | ≥3 — `UA_ZAI_CONSECUTIVE_429_CRITICAL` |
| 429 burst in rolling window | events `rate_limited_429` | **critical** | ≥3 in 10 min — `UA_ZAI_EVENTS_429_CRITICAL_COUNT` / `UA_ZAI_EVENTS_429_WINDOW_SECONDS` |
| Adaptive backoff floor saturated | snapshot `backoff_floor` | **critical** | ≥8.0 s — `UA_ZAI_BACKOFF_FLOOR_MAX` |
| UA Python process count high | `pgrep -cf 'universal_agent\|csi_ingester'` | **warn** | >30 — `UA_PYTHON_PROC_SOFT_LIMIT` |

Severity logic: FUP wins (critical); otherwise any 429-tier condition is critical;
process-count alone is only warn. If nothing has data and process count is within the
soft limit, the invariant stays silent (returns `None`). The events file path is
`AGENT_RUN_WORKSPACES/zai_inference_events.jsonl` (override `UA_ZAI_EVENTS_PATH`), read
defensively (never raises on bad upstream data), capped at `UA_ZAI_EVENTS_MAX_READ`
(default 5000) tail lines.

The headline message names the worst cause first and includes caller attribution from
the events file (top callers by count) so the operator can immediately see which
direct-httpx caller is causing the burst.

---

## Quick reference: env vars

| Var | Effect |
|---|---|
| `ANTHROPIC_DEFAULT_{HAIKU,SONNET,OPUS}_MODEL` | Override the tier→model mapping (else `ZAI_MODEL_MAP`) |
| `UA_CODY_DEFAULT_MODE` | Deploy-time Cody mode default (`zai`/`anthropic`), priority below DB setting |
| `UA_CODY_CLI_MODEL` | Cody Anthropic-CLI model (default `claude-opus-4-8`; `default` = CLI default) |
| `UA_MISSION_CONTROL_MODEL` | Mission Control lane model (default `glm-4.7`, bypasses tier map) |
| `UA_MODEL_TIMEOUT_{HAIKU,SONNET,OPUS}_SECONDS` | Per-tier turn cap (`0` disables) |
| `UA_MISSION_CONTROL_CALL_TIMEOUT_SECONDS` | Mission Control per-call cap (default 180) |
| `UA_AGENT_TEAMS_ENABLED` / `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` | Agent Teams toggle |
| `CLAUDE_CODE_OAUTH_TOKEN` (legacy `ANTHROPIC_MAX_OAUTH_TOKEN`) | Anthropic Max OAuth token forwarded into Cody anthropic CLI subprocess |
| `UA_ZAI_*` (see table above) | ZAI inference-health invariant thresholds/windows |
