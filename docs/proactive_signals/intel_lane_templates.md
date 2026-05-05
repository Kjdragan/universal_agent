# Adding a New Intel Lane (Codex, Gemini, or beyond)

> **Audience:** Operator or AI coder adding a new intelligence lane to UA.
> **Status:** Step-by-step guide. Companion to the v2 design and remaining-work plan.
> **Linked:** [`claudedevs_intel_v2_design.md`](claudedevs_intel_v2_design.md) §13, [`claudedevs_intel_v2_remaining_work.md`](claudedevs_intel_v2_remaining_work.md), [`../06_Deployment_And_Environments/09_Demo_Execution_Environments.md`](../06_Deployment_And_Environments/09_Demo_Execution_Environments.md).

---

## What is a "lane"?

A **lane** is one full intelligence pipeline pointed at a specific topic. The v2 architecture is built so adding `openai-codex-intelligence` or `gemini-intelligence` (or any other vendor / project / community) is mostly **configuration**, not new code.

Each lane has:

- A list of X handles to poll
- A research-grounding allowlist (which official sources to fetch when tweets are thin)
- A vault (where extracted entities, sources, and demos live)
- A capability library (where bundles for agent reuse live)
- A cron schedule
- A demo execution profile (`anthropic_native` / `openai_native` / `gemini_native` / `none`)

The shared infrastructure handles polling, classification, URL enrichment, Memex updates, briefing, demo orchestration, and dependency currency. None of that needs to be reimplemented per lane.

---

## What's already shipped (works for any lane today)

These pieces are fully lane-aware:

| Component | Lane awareness |
|---|---|
| `intel_lanes.yaml` | Multi-lane config with Pydantic schema (PR 11) |
| `services/intel_lanes.py` | `get_lane()`, `enabled_lanes()`, `all_lanes()` accessors |
| `ClaudeCodeIntelConfig.from_env()` | Reads handles from `intel_lanes.yaml` when env unset (PR 17) |
| `services/research_grounding.py` | Pulls allowlist from `LaneConfig.research_allowlist` (PR 3) |
| `services/demo_workspace.py` | `endpoint_profile` parameter (PR 14): `anthropic_native`, `openai_native`, `gemini_native`, `none` |

These pieces still hard-code the `claude-code-intelligence` lane and need per-lane work to fully generalize:

| Component | Status |
|---|---|
| `LANE_SLUG = "claude_code_intel"` | Hardcoded; would need a per-lane equivalent |
| `KB_SLUG = "claude-code-intelligence"` | Hardcoded vault slug |
| Cron job registration in `gateway_server.py` | Single `claude_code_intel_sync` registration |
| Dashboard surfaces in `web-ui` | Single `/dashboard/claude-code-intel/` route |
| Operator email subjects + templates | Reference "ClaudeDevs" by name |

For a **first lane addition**, you can mostly ignore the second list — the shared infrastructure still works because each lane's code paths are independent in practice. The hardcoded references just mean dashboard/cron polish would come as follow-up work after the new lane proves itself.

---

## Step-by-step: adding the OpenAI Codex lane

This is the canonical "add a new lane" walkthrough. It works as written today. Replace `openai-codex-intelligence` and Codex-specific bits to add Gemini or any other lane.

### Step 1 — Flip the YAML config

The Codex lane is already declared in [`src/universal_agent/config/intel_lanes.yaml`](../../src/universal_agent/config/intel_lanes.yaml) as a disabled template. Edit:

```yaml
openai-codex-intelligence:
  enabled: false        # ← change to true
  ...
```

Verify the configured handles, allowlist, cron schedule are what you want. Customize as needed:

```yaml
openai-codex-intelligence:
  enabled: true
  title: "OpenAI Codex Intelligence"
  description: |
    Tracks OpenAI Codex, the OpenAI Agents SDK, and adjacent releases
    via X handles.
  handles:
    - OpenAIDevs
    - OpenAI
    - sama
  research_allowlist:
    - "platform.openai.com/docs"
    - "github.com/openai"
    - "openai.com/blog"
    - "openai.com/index"
  vault_slug: "openai-codex-intelligence"
  capability_library_slug: "openai_codex_intel"
  cron_expr: "0 9,17 * * *"
  cron_timezone: "America/Chicago"
  demo_endpoint_profile: "openai_native"
  tracked_packages:
    - "openai"
    - "openai-agents"
    - "@openai/agents"
```

### Step 2 — Provision the vault directory

Empty parent directory only — the vault structure auto-creates on first ingest:

```bash
mkdir -p artifacts/knowledge-vaults/openai-codex-intelligence
```

On the VPS:

```bash
ssh ua@vps
sudo mkdir -p /opt/universal_agent/artifacts/knowledge-vaults/openai-codex-intelligence
sudo chown ua:ua /opt/universal_agent/artifacts/knowledge-vaults/openai-codex-intelligence
```

### Step 3 — Add the secret(s) to Infisical

The Codex lane uses the X API for polling (same as ClaudeDevs — that secret already exists). For demo execution, you'll need an OpenAI API key. Add via the Infisical CLI or web UI:

```
OPENAI_API_KEY = sk-...        # for Phase 3 demo execution
```

Don't put this in `.env` files. Don't commit it. Infisical injects it into the runtime environment via the existing UA secret loader.

### Step 4 — Run a manual poll

Verify the lane works end-to-end before wiring cron:

```bash
cd /opt/universal_agent
PYTHONPATH=src \
  UA_CLAUDE_CODE_INTEL_LANE_SLUG=openai-codex-intelligence \
  uv run python -m universal_agent.scripts.claude_code_intel_run_report \
    --no-email
```

Check stdout JSON for `handles_synced` matching what's in your YAML. Inspect `artifacts/knowledge-vaults/openai-codex-intelligence/` for fresh ingest content.

If it errors:

| Error | Fix |
|---|---|
| `KeyError: lane not configured` | Lane slug typo in YAML or env var |
| `missing X_BEARER_TOKEN` | X API auth not loaded — check Infisical / env |
| Empty `handles_synced` | Lane has no handles, or `enabled: false` |
| `vault_slug` permissions | Wrong owner; re-run step 2 chown |

### Step 5 — Wire the cron job (operator gate)

This is where the architectural gap surfaces — `gateway_server.py` registers exactly one cron job (`claude_code_intel_sync`). To add a second lane's cron, currently the cleanest path is:

**Option A (recommended for v1 of a new lane):** Run the new lane manually a few times to validate, then add a parallel cron registration. Edit `gateway_server.py`:

```python
def _register_codex_intel_cron_jobs(...) -> None:
    if not _cron_service or not _codex_intel_cron_enabled():
        return
    job_id = "openai_codex_intel_sync"
    command = (
        "!script universal_agent.scripts.claude_code_intel_run_report "
        "--no-email"  # add lane env via systemd, or extend the script to take --lane
    )
    cron_expr = os.getenv("UA_OPENAI_CODEX_INTEL_CRON_EXPR", "0 9,17 * * *")
    timezone_name = os.getenv("UA_OPENAI_CODEX_INTEL_CRON_TIMEZONE", "America/Chicago")
    workspace_dir = str(WORKSPACES_DIR / "cron_openai_codex_intel_sync")
    metadata = {
        "source": "system",
        "system_job": job_id,
        "autonomous": True,
        "proactive_producer": "openai_codex_intel",
        "session_id": "cron_openai_codex_intel_sync",
        "lane_slug": "openai-codex-intelligence",  # NEW — script reads this
    }
    ...
```

**Option B (cleaner long-term, larger change):** Generalize `_register_claude_code_intel_cron_jobs` to iterate over `enabled_lanes()` and register one cron per lane. This is the right shape but requires touching cron registration logic and is a bigger PR.

Pick Option A for the first new lane, then refactor to Option B once two lanes are running and the pattern is clear.

### Step 6 — Add `--lane` arg to the run-report script

The script currently uses one default lane. Add a CLI flag:

```python
parser.add_argument(
    "--lane",
    default="claude-code-intelligence",
    help="intel_lanes.yaml slug to operate on. Default: claude-code-intelligence.",
)
```

Plumb it into `ClaudeCodeIntelConfig.from_lane(args.lane)` instead of `from_env()`. Then the cron command becomes:

```
!script universal_agent.scripts.claude_code_intel_run_report --lane openai-codex-intelligence --no-email
```

### Step 7 — Demo execution (optional, only if you want demos for this lane)

If you want autonomous demos for Codex tutorials/features, the demo workspace pattern from PR 7/PR 14 already supports it:

```python
from universal_agent.services.demo_workspace import (
    ENDPOINT_PROFILE_OPENAI,
    provision_demo_workspace,
)

result = provision_demo_workspace(
    "codex_agents_quickstart__demo-1",
    endpoint_profile=ENDPOINT_PROFILE_OPENAI,
)
```

Cody picks up the workspace, sees `.endpoint_profile = openai_native`, knows to authenticate with `OPENAI_API_KEY` (from `PROFILE_REQUIRED_ENV`) instead of the Anthropic Max plan OAuth.

**Important:** the dual-environment guarantees from the Anthropic case don't transplant directly. OpenAI has its own auth model. Specifically:

- The OpenAI Codex CLI (`codex`) uses an API key, not OAuth. So there's no equivalent of the `claude /login` step.
- The "no env leak" rule still matters: if `ANTHROPIC_AUTH_TOKEN` from the parent shell leaks into a Codex demo subprocess, Codex won't care, but if the demo also touches Anthropic for some reason it'll be confused. Use `cody_implementation.run_in_workspace`'s env scrubbing.

For the first Codex demo, scaffold a workspace manually, run it, see what surprises surface. Only then is it worth generalizing.

### Step 8 — Dashboard (last — optional)

The web UI at `/dashboard/claude-code-intel/` is single-lane. Adding a `/dashboard/openai-codex-intel/` page is mostly copy-paste of `web-ui/app/dashboard/claude-code-intel/page.tsx` with the lane slug changed. The corresponding API route in `gateway_server.py` (`dashboard_claude_code_intel`) needs a parallel `dashboard_openai_codex_intel` (or, again, a generalization to take a lane parameter).

This is purely cosmetic — the underlying data is in `artifacts/knowledge-vaults/openai-codex-intelligence/` and can be inspected by hand or via the operator skill until the dashboard catches up.

---

## Step-by-step: adding the Gemini lane

Same shape as Codex. Replace:

- Lane slug: `gemini-intelligence`
- Handles: `GoogleDeepMind`, `GoogleAI`, plus whoever leads the Gemini API team
- Allowlist: `ai.google.dev`, `developers.googleblog.com`, `github.com/google-gemini`, `cloud.google.com/vertex-ai`
- Vault slug: `gemini-intelligence`
- `demo_endpoint_profile: gemini_native`
- Tracked packages: `google-genai`, `@google/genai`
- Secret: `GEMINI_API_KEY` in Infisical

The remaining-work plan templates this lane as `gemini-intelligence` already.

### Gemini-specific differences

- **Vertex AI vs Gemini API.** They're related but use different auth: Vertex AI uses a service account JSON, Gemini API uses an API key. If you want the lane to cover both, the demo execution layer needs both credentials. Start with the Gemini API path (simpler) and add Vertex later if needed.
- **No Anthropic-style OAuth wrinkle.** Both Vertex and Gemini API use API keys. The "OAuth invisible to SDK" trap from PR 7b doesn't apply here.
- **Quota model.** Gemini's free tier has reasonable rate limits but they're per-minute, not per-month. Build in retry-with-backoff in any demo that does bulk calls.

---

## Step-by-step: adding ANY new lane (general recipe)

For something that isn't Codex / Gemini — e.g., Cohere, Mistral, an internal company project, an open-source community:

1. **Pick a lane slug.** Lowercase, hyphenated, descriptive. Examples: `cohere-intelligence`, `langchain-intelligence`, `acme-internal-intelligence`. The slug is the directory name, the YAML key, and the env var value — keep it stable.

2. **Decide what to track.** Concretely:
   - Which X handles?
   - Which official sources should research grounding fetch from?
   - What package manager(s) does this ecosystem use (PyPI, npm, both, none)?
   - Does it have a CLI like `claude` or `codex` that needs an OAuth flow, OR is it API-key-only?

3. **Add the lane to `intel_lanes.yaml`.** Use the existing entries as a template:

   ```yaml
   <lane-slug>:
     enabled: false                  # disabled while you set things up
     title: "<Human Title>"
     description: |
       <One sentence on what this lane tracks.>
     handles:
       - <Handle1>
       - <Handle2>
     research_allowlist:
       - "<official-docs-domain>"
       - "github.com/<owner>"
       - "<official-blog-domain>"
     vault_slug: "<lane-slug>"
     capability_library_slug: "<lane_slug_underscored>"
     cron_expr: "<UNIQUE schedule, don't overlap with other lanes>"
     cron_timezone: "America/Chicago"
     demo_endpoint_profile: "<anthropic_native | openai_native | gemini_native | none>"
     tracked_packages:
       - "<package-1>"
       - "<package-2>"
   ```

4. **Provision Infisical secret(s).** Whatever auth this ecosystem needs.

5. **Manual run, inspect, iterate.** Don't wire cron until you've seen one full poll succeed and produce a sensible vault entry.

6. **Cron + dashboard come last.** Both have copy-paste shape; do them after the lane has proven itself.

---

## What about endpoint profiles for new providers?

PR 14 ships four profiles: `anthropic_native`, `openai_native`, `gemini_native`, `none`. Adding a new profile (e.g., `cohere_native`, `mistral_native`) is a small follow-up:

```python
# src/universal_agent/services/demo_workspace.py
ENDPOINT_PROFILE_COHERE = "cohere_native"
VALID_ENDPOINT_PROFILES = (
    ENDPOINT_PROFILE_ANTHROPIC,
    ENDPOINT_PROFILE_GEMINI,
    ENDPOINT_PROFILE_OPENAI,
    ENDPOINT_PROFILE_COHERE,           # ← add
    ENDPOINT_PROFILE_NONE,
)
PROFILE_REQUIRED_ENV[ENDPOINT_PROFILE_COHERE] = "COHERE_API_KEY"

# add a keyword group for topic detection
_PROFILE_KEYWORDS[ENDPOINT_PROFILE_COHERE] = (
    "cohere",
    "command-r",
    "command-r-plus",
)
```

Plus the test parametrization at `tests/unit/test_demo_workspace_endpoint_profile.py` to keep the regression guards in place.

---

## What's deferred until later

These are intentional gaps in v1 multi-lane support:

1. **Per-lane email subjects/templates.** All lanes currently use ClaudeDevs-themed email copy. When the second lane goes live, the operator email path needs lane-aware subject prefixes ("[ClaudeDevs Intel]" vs "[Codex Intel]").

2. **Per-lane dashboards.** Single `/dashboard/claude-code-intel/` today. Generalizing to `/dashboard/intel/<lane-slug>/` is mechanical refactoring, not architecture.

3. **Per-lane Phase 0 dependency-currency triggers.** PR 6c auto-fires the actuator on `release_announcement` actions. The current actuator only knows about Anthropic-adjacent packages. Adding `is_codex_adjacent()` / `is_gemini_adjacent()` companion checks is a straightforward extension when the second lane goes live.

4. **Cross-lane analytics.** No "compare which ecosystem is moving faster" surfaces today. If you want that, the rolling brief generation can be extended to do cross-lane synthesis on a separate cadence.

None of these are blockers for adding a second lane — they're polish that surfaces naturally as the lane runs.

---

## Verification checklist before declaring a new lane "live"

After completing the steps above, run through this:

- [ ] `intel_lanes.yaml` declares the lane with `enabled: true`
- [ ] `python -c "from universal_agent.services.intel_lanes import get_lane; print(get_lane('<lane-slug>'))"` returns a `LaneConfig` (no `KeyError`)
- [ ] `artifacts/knowledge-vaults/<vault-slug>/` exists with correct ownership on the VPS
- [ ] Required Infisical secret is set
- [ ] Manual run of `claude_code_intel_run_report` (or equivalent) with the lane env override produces a packet under `artifacts/proactive/<lane-slug>/packets/`
- [ ] The packet's `actions.json` shows posts classified into tiers (not all `digest`)
- [ ] At least one source under `vault/sources/` from the manual run
- [ ] Memex CREATE entries appear in `vault/log.md`
- [ ] Cron registration deployed (or operator decision to defer cron)
- [ ] First demo workspace successfully provisioned with the lane's `demo_endpoint_profile`
- [ ] First demo runs without env-leak surprises (check `manifest.json.endpoint_hit`)

Once all of these check out, flip cron on, watch the next two cron ticks land cleanly, and the lane is live.

---

## Related docs

- [`claudedevs_intel_v2_design.md`](claudedevs_intel_v2_design.md) — full v2 architecture
- [`claudedevs_intel_v2_remaining_work.md`](claudedevs_intel_v2_remaining_work.md) — execution catalog
- [`../06_Deployment_And_Environments/09_Demo_Execution_Environments.md`](../06_Deployment_And_Environments/09_Demo_Execution_Environments.md) — dual-environment architecture (read first if adding a lane that needs OAuth)
- [`../operations/demo_workspace_provisioning.md`](../operations/demo_workspace_provisioning.md) — original Anthropic-native runbook (template for OpenAI / Gemini equivalents)
