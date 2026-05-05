# ClaudeDevs X Intelligence System

> **Status as of 2026-05-05:** v2 architecture is feature-complete in production
> across 17 PRs. This doc is the canonical reference for how the system is
> ARCHITECTED and how to OPERATE it. For the original v2 design rationale
> (the "why"), see
> [`../proactive_signals/claudedevs_intel_v2_design.md`](../proactive_signals/claudedevs_intel_v2_design.md).
> For PR-by-PR execution history, see
> [`../proactive_signals/claudedevs_intel_v2_remaining_work.md`](../proactive_signals/claudedevs_intel_v2_remaining_work.md).

## Purpose

This subsystem turns multiple Claude Code–related X accounts (currently
`@ClaudeDevs` and `@bcherny`) into a durable intelligence pipeline that:

1. **Discovers** new Claude Code / Anthropic SDK features faster than model
   training cutoffs
2. **Captures** them as durable knowledge in a vault that grows over time
3. **Demonstrates** them as runnable reference implementations clients can lift
4. **Keeps the VPS current** by auto-applying the SDK upgrades the announcements imply

---

## v2 Production Architecture (current state)

The system runs as a **6-phase pipeline**, with Phases 0 + 1 on cron and the
rest operator/Cody/Simone-driven on-demand.

```mermaid
flowchart TD
    Cron["claude_code_intel_sync<br/>cron 0 8,16 * * * America/Chicago"]
    Cron --> Phase1
    Phase0["Phase 0 — Dependency Currency<br/>continuous"]

    subgraph Phase1 ["Phase 1 — Discovery & Research (cron, 2x/day)"]
        Poll["Poll @ClaudeDevs + @bcherny via X API"]
        Enrich["Three-pass URL enrichment<br/>(no 3K cap — full doc absorption)"]
        Classify["LLM classifier<br/>+ release_announcement detection"]
        Research["Research grounding fallback<br/>when sources thin or terms unknown"]
        Memex["Memex CREATE/EXTEND/REVISE<br/>on entity & concept pages"]
        Brief["Rebuild 28-day brief +<br/>full-corpus capability library"]
        Trigger["release_announcement detected?<br/>→ Phase 0 actuator"]

        Poll --> Enrich --> Classify --> Research --> Memex --> Brief
        Classify --> Trigger
    end

    Phase1 --> Phase2

    subgraph Phase0 ["Phase 0 — Dependency Currency"]
        Sweep["Daily sweep:<br/>uv pip list --outdated +<br/>npm outdated + claude --version"]
        Detect["release_announcement classifier"]
        Actuator["Upgrade actuator:<br/>bump pyproject → uv sync →<br/>ZAI smoke + Anthropic-native smoke"]
        Email["Email Kevin: success or rollback"]

        Sweep --> Actuator
        Detect --> Actuator
        Actuator --> Email
    end

    Trigger --> Phase0

    subgraph Phase2 ["Phase 2 — Briefing (Simone, heartbeat)"]
        Pick["Simone reads vault entity pages<br/>flagged briefing_status: pending"]
        Scaffold["cody-scaffold-builder:<br/>provision /opt/ua_demos/&lt;demo-id&gt;/<br/>copy SOURCES, write BRIEF/ACCEPTANCE"]
        Dispatch["cody-task-dispatcher:<br/>queue cody_demo_task<br/>(persistent queue)"]

        Pick --> Scaffold --> Dispatch
    end

    Phase2 --> Phase3

    subgraph Phase3 ["Phase 3 — Implementation (Cody)"]
        Read["Cody reads BRIEF/ACCEPTANCE/SOURCES"]
        Build["Build demo via vanilla Claude Code<br/>(env-scrubbed, project-local settings)"]
        Manifest["Write manifest.json<br/>(endpoint_hit, versions, status)"]

        Read --> Build --> Manifest
    end

    Phase3 --> Phase4

    subgraph Phase4 ["Phase 4 — Review (Simone, multi-loop)"]
        Eval["cody-work-evaluator:<br/>EvaluationReport"]
        Verdict{"Verdict?"}
        Pass["complete + vault-demo-attach"]
        Iter["FEEDBACK.md +<br/>reissue_cody_demo_task"]
        Defer["park with reason"]

        Eval --> Verdict
        Verdict -->|pass| Pass
        Verdict -->|iterate| Iter
        Verdict -->|defer| Defer
        Iter --> Phase3
    end

    Phase4 --> Phase5

    subgraph Phase5 ["Phase 5 — Memorialize (occasional)"]
        Promote["Pattern proven across 3-4 demos<br/>→ promote to UA skill"]
    end
```

### What lives where (architecture-to-code map)

| Phase | Concern | Code |
|---|---|---|
| 0 | Daily drift sweep | [`services/dependency_currency.py`](../../src/universal_agent/services/dependency_currency.py), [`scripts/dependency_currency_sweep.py`](../../src/universal_agent/scripts/dependency_currency_sweep.py) |
| 0 | Upgrade actuator | [`services/dependency_upgrade.py`](../../src/universal_agent/services/dependency_upgrade.py), [`scripts/dependency_upgrade.py`](../../src/universal_agent/scripts/dependency_upgrade.py) |
| 0 | Auto-trigger from release tweets | [`services/release_auto_trigger.py`](../../src/universal_agent/services/release_auto_trigger.py) |
| 1 | Polling + checkpointing | [`services/claude_code_intel.py`](../../src/universal_agent/services/claude_code_intel.py) |
| 1 | URL enrichment (no 3K cap) | [`services/csi_url_judge.py`](../../src/universal_agent/services/csi_url_judge.py) |
| 1 | Release-announcement detection | [`services/dependency_currency.detect_release_announcement`](../../src/universal_agent/services/dependency_currency.py) wired into `classify_post` |
| 1 | Research grounding fallback | [`services/research_grounding.py`](../../src/universal_agent/services/research_grounding.py) |
| 1 | Replay + Memex pass | [`services/claude_code_intel_replay.py`](../../src/universal_agent/services/claude_code_intel_replay.py) (calls into [`wiki/core.py`](../../src/universal_agent/wiki/core.py) memex primitives) |
| 1 | Brief + capability library | [`services/claude_code_intel_rollup.py`](../../src/universal_agent/services/claude_code_intel_rollup.py) |
| 1 | Cron orchestration + email | [`scripts/claude_code_intel_run_report.py`](../../src/universal_agent/scripts/claude_code_intel_run_report.py) |
| 2 | Vault entity → demo workspace | [`services/cody_scaffold.py`](../../src/universal_agent/services/cody_scaffold.py), skill: `.claude/skills/cody-scaffold-builder/` |
| 2 | Demo workspace provisioning | [`services/demo_workspace.py`](../../src/universal_agent/services/demo_workspace.py), templates: `src/universal_agent/templates/ua_demos_scaffold/` |
| 2 | Task Hub dispatch | [`services/cody_dispatch.py`](../../src/universal_agent/services/cody_dispatch.py), skill: `.claude/skills/cody-task-dispatcher/` |
| 3 | Cody implementation contract | [`services/cody_implementation.py`](../../src/universal_agent/services/cody_implementation.py), skill: `.claude/skills/cody-implements-from-brief/` |
| 3 | Smoke demo (gates Phase 0 upgrades) | `src/universal_agent/templates/_smoke_demo/` |
| 4 | Monitor / Evaluate / Attach | [`services/cody_evaluation.py`](../../src/universal_agent/services/cody_evaluation.py), skills: `.claude/skills/cody-progress-monitor/`, `cody-work-evaluator/`, `vault-demo-attach/` |
| Ops | Vault contradictions sweep | [`services/vault_lint_contradictions.py`](../../src/universal_agent/services/vault_lint_contradictions.py), [`scripts/vault_contradiction_lint.py`](../../src/universal_agent/scripts/vault_contradiction_lint.py) |
| Ops | Backfill (parallel-vault staging) | [`services/backfill_v2.py`](../../src/universal_agent/services/backfill_v2.py), [`scripts/claude_code_intel_backfill_v2.py`](../../src/universal_agent/scripts/claude_code_intel_backfill_v2.py) |
| Multi-lane | Lane config + accessor | [`config/intel_lanes.yaml`](../../src/universal_agent/config/intel_lanes.yaml), [`services/intel_lanes.py`](../../src/universal_agent/services/intel_lanes.py) |

### Critical architectural invariants

1. **The vault is the canonical product.** Brief, capability library, demos
   are derivative views.
2. **Append-dominant Memex.** ~80% CREATE, ~15% EXTEND, ~5% REVISE. `raw/`
   and `sources/` are immutable; only `entities/`, `concepts/`, `analyses/`
   are mutable. REVISE snapshots to `_history/` before rewriting.
3. **Dual-environment safety.** UA's normal ZAI mapping handles all routine
   work; demos under `/opt/ua_demos/` use vanilla Claude Code + Max plan
   OAuth. Never mix. See
   [`../06_Deployment_And_Environments/09_Demo_Execution_Environments.md`](../06_Deployment_And_Environments/09_Demo_Execution_Environments.md).
4. **Persistent demo queue.** Cody picks up `cody_demo_task` items
   indefinitely; never times out, never gives up.
5. **No invention.** Cody documents gaps in `BUILD_NOTES.md` instead of
   guessing at API surface. Hard rule.
6. **Auto-rollback on smoke failure.** Phase 0 upgrades fail safe — both
   ZAI and Anthropic-native smokes must pass; either failure rolls back
   `pyproject.toml` and emails Kevin with the failure detail.

---

## What to expect after ship — operational guide

### The emails are the primary signal

Three subjects to know:

#### 1. Routine cron summary (twice daily, ~8 AM and 4 PM CT)

```
[ClaudeDevs X Intel] @ClaudeDevs sync (X new / Y actions)
```

Existing email from v1, still arrives. **What changed in v2:** the vault now
actually accretes entity pages (PR 15) and the brief/capability library
auto-regenerate every tick (PR 4 + 5). Email shape is the same.

- **Healthy signal:** email arrives at every tick, even when "0 new posts" —
  per-handle checkpoint working correctly, NOT a failure.
- **First worry:** if these stop arriving for >24 hours, cron broke or X API broke.

#### 2. Phase 0 upgrade email (NEW — fires on release detection)

When `@ClaudeDevs` announces a versioned release of an Anthropic-adjacent package:

```
[Phase 0 upgrade] OK — claude-agent-sdk 0.1.66 → 0.1.73
```

or on failure:

```
[Phase 0 upgrade] FAIL — anthropic 0.99.0
```

- **OK email = SUCCESS:** both smokes passed, bumped `pyproject.toml` is in
  the working tree on `feature/latest2` waiting for `/ship`. Email body has
  the diff for review.
- **FAIL email = SYSTEM DID THE RIGHT THING:** smoke caught a regression,
  actuator rolled back, working tree unchanged. "What broke" section in the
  email shows which smoke failed and why.
- **NO email after a release tweet you saw:** classifier missed it (rare)
  OR cron hasn't run yet — wait for the next tick.

#### 3. Cody demo notifications (LATER — Phase 3 is operator-initiated)

Doesn't fire automatically. When you eventually trigger the first demo via
the Simone scaffold-builder skill, you'll see Task Hub activity and the
demo workspace will populate under `/opt/ua_demos/`.

### Where to look on disk (when you want to dig deeper)

On the VPS:

```bash
# Did the latest cron tick produce a packet?
ls -lt /opt/universal_agent/artifacts/proactive/claude_code_intel/packets/$(date +%Y-%m-%d)/

# Are entity pages accreting in the vault? (NEW from PR 15 — was empty in v1)
ls /opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence/entities/

# What did the latest tick write to the change log?
tail -50 /opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence/log.md

# Phase 0 infrastructure pages (NEW from PR 6a)
cat /opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence/infrastructure/installed_versions.md
cat /opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence/infrastructure/version_drift.md

# Did an auto-upgrade fail? (rollback records)
ls /opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence/infrastructure/upgrade_failures/

# Latest brief
cat /opt/universal_agent/artifacts/proactive/claude_code_intel/rolling/current/rolling_14_day_report.md
# (filename misleading — actually 28-day brief now per PR 4)
```

### Mission Control dashboard

The existing dashboard at `/dashboard/claude-code-intel/` shows v1-shape
surfaces (packets, brief, capability bundles, vault page search). All of
that still works. **The new v2-specific signals are NOT yet surfaced in
the dashboard** — they live in:

- The operator emails (Phase 0 upgrade emails are entirely email-only)
- The vault `log.md` change log
- The vault `infrastructure/` pages
- The packet `manifest.json` and `replay_summary.json`

Wiring v2 signals (Memex action counts, grounded source counts, auto-upgrade
history) into the dashboard is a follow-up PR — non-blocking for the core
pipeline. If you want it, that's a clean ~150-line addition.

### A practical "first week" watching schedule

**Day 1 after ship:**
- After the next cron tick (~8 AM or 4 PM CT): check email for the standard
  `[ClaudeDevs X Intel]` summary. Should arrive even with 0 actions.
- SSH to the VPS:
  ```bash
  ls /opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence/entities/
  ```
  If empty, PR 15 Memex wiring isn't firing. If has any `.md` files, v2
  ingest is producing entity pages.

**Day 2-3:**
- Watch the brief regeneration. Each cron tick should rebuild
  `current/rolling_14_day_report.md` (filename misleading per PR 4 — actually
  28-day brief). Mission Control's claude-code-intel route should show the
  latest brief. If timestamp stops moving, rebuild trigger broke.

**Day 4-7:**
- Watch your inbox for any `[Phase 0 upgrade]` email. Fires the moment
  `@ClaudeDevs` tweets a versioned release of an Anthropic-adjacent package.
  Expected cadence: every couple weeks. **OK = good. FAIL = also good
  (caught a regression). NO email after a release tweet = real signal
  something didn't fire.**

### What "good" looks like in one sentence

**Within 7 days of ship, you should have at least one entity page in
`vault/entities/`, a brief that's been regenerated multiple times, and
either an `installed_versions.md` matching what's actually deployed OR a
Phase 0 upgrade email (success or failure) in your inbox.**

If all three are happening, v2 is working as designed. If any are missing
after a week, something needs investigation.

### 48-hour audit checkpoint (run this 2 days after ship)

This is a concrete checklist to walk through after the system has had ~4
cron ticks (twice-daily × 2 days). Use it to verify v2 is doing what this
doc says it should.

#### Email inbox — has the cron been firing?

- [ ] At least 4 `[ClaudeDevs X Intel] @ClaudeDevs sync (...)` emails
      (one per cron tick). If fewer, cron may have skipped.
- [ ] At least one tick shows `new posts: > 0` (otherwise the X API
      query is degenerate or @ClaudeDevs went silent — the latter is
      possible but unusual for 48 hours).
- [ ] If `@ClaudeDevs` tweeted a release in this window: a corresponding
      `[Phase 0 upgrade]` email arrived. If yes, **the v2 auto-trigger
      worked end-to-end for the first time** — that's the headline
      success signal.

#### Vault on disk — is it accreting?

```bash
ssh ua@vps
cd /opt/universal_agent
ls artifacts/knowledge-vaults/claude-code-intelligence/entities/ | wc -l
# Should be > 0 if any post in the last 48 hours mentioned a new feature.
# An empty entities/ directory after 4 ticks is a smell — check log.md.

tail -30 artifacts/knowledge-vaults/claude-code-intelligence/log.md
# Should show CREATE entries from PR 15 Memex pass.
# Format: ## [<iso>] entities/<slug>.md CREATE / EXTEND / REVISE
```

#### Phase 0 infrastructure pages — are they populating?

```bash
cat artifacts/knowledge-vaults/claude-code-intelligence/infrastructure/installed_versions.md
# Should list installed versions of claude-code, claude-agent-sdk, anthropic, etc.

cat artifacts/knowledge-vaults/claude-code-intelligence/infrastructure/version_drift.md
# May show drift; that's informational, not a problem unless it's wrong.
```

If both files exist with current dates, Phase 0 sweep is running.

#### Brief — is it regenerating?

```bash
ls -lh artifacts/proactive/claude_code_intel/rolling/current/rolling_14_day_report.md
# mtime should be the latest cron tick timestamp.
# If mtime is older than 24 hours, brief regeneration is broken.

head -20 artifacts/proactive/claude_code_intel/rolling/current/rolling_14_day_report.md
# Should mention recent (last 28 days) feature names.
```

#### Capability library — is it full-corpus?

```bash
cat agent_capability_library/claude_code_intel/current/index.json | python3 -m json.tool
# Should show "source_mode": "full_corpus" and "source_action_count" > 0.
# If source_mode says "windowed", PR 5 is disabled or broken.
```

#### Decision points after the audit

- **All checkpoints pass:** the v2 system is working as designed. Move on
  to Phase 3 demo orchestration when ready (operator-supervised first run).
- **Email arriving but vault empty:** Memex wiring (PR 15) failed to land
  or has a bug. Tell me; I'll diagnose.
- **No emails arriving at all:** check Logfire and the `cron_claude_code_intel_sync`
  workspace under `AGENT_RUN_WORKSPACES/`. Cron service or X API is the issue.
- **`[Phase 0 upgrade] FAIL` appeared:** READ THE EMAIL. The "What broke"
  section tells you which smoke caught what regression. The actuator
  rolled back automatically — your `pyproject.toml` is unchanged. Decide
  whether the failure is real (Anthropic shipped a breaking change) or
  spurious (env-leak in the smoke setup).

### Three escape hatches (verified by unit tests)

### Failure-symptom diagnosis

| Symptom | Likely cause | Where to look |
|---|---|---|
| No `[ClaudeDevs X Intel]` emails for 24+ hours | Cron broke OR X API broke | Logfire traces, gateway_server logs |
| Emails arrive but always show 0 actions | URL enrichment OR classifier broken | Latest packet's `actions.json` |
| `vault/entities/` stays empty | PR 15 Memex wiring broken | `vault/log.md` for CREATE entries; if log.md not growing, Memex pass isn't running |
| `[Phase 0 upgrade]` shows endpoint_mismatch in smoke output | env-leak in actuator's smoke subprocess | `/opt/ua_demos/_smoke/` env; check that ANTHROPIC_AUTH_TOKEN isn't being inherited |
| `[Phase 0 upgrade]` shows uv sync failed | New package version has a real conflict | Email's stderr excerpt = the actual `uv sync` error |
| No vault entity page after a major release tweet | Memex extraction missed the feature term | Check `actions.json` for the post; check `release_info` was populated |

### Three escape hatches (verified by unit tests)

If anything goes sideways:

1. **`UA_CSI_AUTO_UPGRADE_ON_RELEASE=0`** — disables PR 6c auto-trigger,
   reverts to manual upgrade only.
2. **`manifest.endpoint_hit` verification** in Cody's evaluator (PR 10) —
   automatically catches env-leak when a demo accidentally hits ZAI.
3. **`backfill --revert-swap`** — rolls back a bad backfill swap; archive
   becomes canonical, current canonical parks at `<name>-rolledback/`.

---

## Operational quick-reference

### Common commands (run on VPS)

```bash
# Manual cron tick (don't normally need to do this — cron handles it)
cd /opt/universal_agent
PYTHONPATH=src uv run python -m universal_agent.scripts.claude_code_intel_run_report

# Force the brief to rebuild even if no new posts
PYTHONPATH=src uv run python -m universal_agent.scripts.claude_code_intel_run_report --rebuild-brief

# Phase 0 drift sweep (read-only — no side effects)
PYTHONPATH=src uv run python -m universal_agent.scripts.dependency_currency_sweep

# Manual Phase 0 upgrade (operator-supervised)
PYTHONPATH=src uv run python -m universal_agent.scripts.dependency_upgrade \
    --package claude-agent-sdk --target-version 0.5.1 --dry-run
# Drop --dry-run to actually apply

# Backfill historical packets (operator-supervised first run)
PYTHONPATH=src uv run python -m universal_agent.scripts.claude_code_intel_backfill_v2 --dry-run
# Then without --dry-run for the actual replay
# Then --diff-only to inspect
# Then --swap-only to atomic-rename, OR --revert-swap to roll back

# Vault contradictions lint (monthly, when ready to wire to cron)
PYTHONPATH=src uv run python -m universal_agent.scripts.vault_contradiction_lint
```

### When you want to add another lane (Codex, Gemini, etc.)

See [`../proactive_signals/intel_lane_templates.md`](../proactive_signals/intel_lane_templates.md)
for the step-by-step guide. The short version: edit
[`config/intel_lanes.yaml`](../../src/universal_agent/config/intel_lanes.yaml)
to flip `enabled: true`, provision the vault dir, add the lane's API
secret to Infisical, run a manual sync to validate, then wire cron.

---

## Legacy v1 capability detail

The numbered list below catalogs the system's accumulated capabilities
chronologically. Useful as a reference for "when did X land," but the
architecture overview above is the authoritative read for current state.

### v1 capabilities (1-27, shipped before 2026-05)

The system can:

1. Poll multiple configured X handles (`@ClaudeDevs`, `@bcherny`) via the official X API, with per-handle state tracking.
2. Write durable packets under `artifacts/proactive/claude_code_intel/packets/`.
3. Deduplicate by stable X post ID.
4. Classify posts into `digest`, `kb_update`, `demo_task`, or `strategic_follow_up`.
5. Use an LLM-assisted classifier with deterministic fallback.
6. Replay packets idempotently.
7. Fetch direct linked sources in a bounded way and preserve snapshots.
8. Classify linked sources by source type (GitHub repo/file/tree, docs page, vendor docs, event page, X page, non-HTML, generic web).
9. Populate an external Claude Code knowledge vault.
10. Write per-post candidate ledgers with Task Hub, assignment, email, and wiki linkage.
11. Record attachment-based AgentMail delivery evidence so real deliveries can satisfy Task Hub completion verification.
12. Keep new ClaudeDevs cron sessions heartbeat-exempt to avoid reusing the packet workspace as a heartbeat workspace.
13. Clean up historically polluted ClaudeDevs cron workspaces with a dedicated archive utility.
14. Use an LLM-assisted classifier with deterministic fallback and fetched-source summary context.
15. Expose an operator skill surface (`$claudedevs-x-intel`) backed by a deterministic report script that writes `operator_report.md` / `operator_report.json` into each packet and can email artifact links.
16. Route the built-in production ClaudeDevs cron through the report entry point so actionable polling runs automatically send a professional artifact email.
17. Reject browser-gated social shells during linked-source expansion so `t.co -> x.com/twitter.com` redirects do not poison the Claude Code knowledge vault with JavaScript error pages.
18. Expose a dedicated dashboard review surface at `/dashboard/claude-code-intel` backed by a read-only dashboard endpoint for packet history, report links, and vault-page search.
19. Regenerate a rolling 14-day builder brief plus first-class capability bundles after successful runs, then materialize reusable derivatives into a versioned repo library.
20. Track `synthesis_method` (`llm` | `fallback`) in rolling JSON output and display it on the dashboard so operators can see bundle quality at a glance.
21. Log warnings when LLM synthesis fails or is unavailable, instead of silently falling back.
22. Enrich fallback bundles with linked source titles, URLs, and excerpts instead of generic placeholder text.
23. Provide operator trigger controls (`Run Pipeline`, `Rollup Only`) on the dashboard via `POST /api/v1/dashboard/claude-code-intel/trigger`.
24. Support multiple intelligence handles via `UA_CLAUDE_CODE_INTEL_X_HANDLES` env var (default: `ClaudeDevs,bcherny`), with per-handle state files (`state__{handle}.json`) and automatic migration from legacy single `state.json`.
25. Filter Tier 1 digest posts from rolling synthesis (`MIN_SYNTHESIS_TIER = 2`) so personal/community chatter never becomes capability bundles while remaining in packet history.
26. Enrich linked URLs during live sync via a three-pass pipeline (regex pre-filter → Anthropic LLM judge → selective fetch) before post classification, providing actual linked content to the tier classifier for informed tier decisions.
27. Feature-gate URL enrichment via `UA_CSI_URL_ENRICHMENT_ENABLED` env var (default: `1`/on) for safe production rollout.

### v2 capability additions (PRs 1, 2, 3, 4, 5, 6a, 7, 11 — 2026-05)

The v2 work documented in [`docs/proactive_signals/claudedevs_intel_v2_design.md`](../proactive_signals/claudedevs_intel_v2_design.md) lifts the v1 truncation caps, decouples brief regeneration from new-post arrivals, adds Memex update primitives, ships a research grounding subagent, scaffolds the demo execution environment, and stands up a Phase 0 dependency-currency observation layer.

28. Read full official documentation (no 3K excerpt cap) into the classifier via `csi_url_judge`. Three env-driven knobs replaced the hard-coded caps: `UA_CSI_DOC_STORAGE_MAX_CHARS` (default 200K, was 20K), `UA_CSI_MAX_FETCH_PER_POST` (default 10, was 3), and `build_linked_context(max_content_chars=None)` no longer truncates per source.
29. Synthesize the rolling builder brief over a configurable window (`UA_CLAUDE_CODE_INTEL_BRIEF_WINDOW_DAYS`, default **28**, was 14) with no 18-item cap (`UA_CLAUDE_CODE_INTEL_BRIEF_MAX_CONTEXTS`, default 500). The brief now rebuilds on every successful tick (`UA_CLAUDE_CODE_INTEL_BRIEF_ALWAYS_REBUILD=1`) so backfills surface immediately. New `--rebuild-brief` CLI flag on `claude_code_intel_run_report.py` for explicit operator override.
30. Synthesize the capability library over the **full corpus**, not a windowed slice. `UA_CSI_LIBRARY_FULL_CORPUS=1` (default) runs a separate library synthesis using `_load_action_contexts(window_days=None)`. Library `index.json` records `source_mode: full_corpus` and `source_action_count`. Brief stays windowed.
31. Memex update primitives in `wiki/core.py`: `memex_create_page`, `memex_extend_page`, `memex_revise_page`, plus `memex_apply_action` dispatcher. CREATE refuses to overwrite. EXTEND appends a dated section. REVISE snapshots to `_history/<kind>/<slug>/<iso>.md` and requires an explicit `reason` for the audit log. `memex_append_change_log` writes structured `## [<iso>] <page> <ACTION>` entries to `log.md`. Source pages remain immutable; only entity/concept/analyses pages are mutable.
32. Research grounding subagent (`services/research_grounding.py`). Tier ≥ 2 gate (`UA_CSI_RESEARCH_TIER_GATE`). Four trigger reasons: NO_LINKS, THIN_LINKED_SOURCES, UNKNOWN_TERM, OPERATOR_FORCE. Allowlist priority ranking via `allowlist_rank()` reads from `intel_lanes.yaml`. No-invention contract: empty allowlist or zero candidates returns `ResearchResult` with `skipped_reason`, never a fabricated source. Reuses `csi_url_judge.fetch_url_content` so storage caps apply uniformly.
33. Multi-lane configuration scaffolding (`config/intel_lanes.yaml` + `services/intel_lanes.py`). Pydantic-validated `LaneConfig` with strict unknown-key rejection. Default lane `claude-code-intelligence` (enabled). Templates `openai-codex-intelligence` and `gemini-intelligence` ship disabled. `get_lane(slug)`, `enabled_lanes()`, `all_lanes()` accessors with cache.
34. Phase 0 dependency-currency observation (`services/dependency_currency.py` + `scripts/dependency_currency_sweep.py`). Parses `uv pip list --outdated`, `npm outdated --json`, and `claude --version`. Writes `infrastructure/installed_versions.md`, `infrastructure/version_drift.md`, `infrastructure/release_timeline.md`, and `infrastructure/upgrade_failures/<ts>_<pkg>.md`. Anthropic-adjacent allowlist: `claude-code`, `claude-agent-sdk`, `anthropic`, `@anthropic-ai/sdk`, `@anthropic-ai/claude-agent-sdk`. Deterministic release-announcement detection wired into `classify_post` as a new `action_type: release_announcement` with structured `release_info` payload (package, version, is_anthropic_adjacent). Tier floor of 2 enforced for release announcements.
35. Demo execution environment scaffolding (`services/demo_workspace.py` + `templates/ua_demos_scaffold/` + `templates/_smoke_demo/`). Provisions `/opt/ua_demos/<demo-id>/` with vanilla project-local `.claude/settings.json` (no env, no hooks, no plugins, no extraKnownMarketplaces, no plansDirectory). `verify_vanilla_settings()` is the safety net: refuses to declare a workspace ready if any of `POLLUTION_INDICATORS` leaked back in. Slug-safe; refuses `..`. Smoke demo at `/opt/ua_demos/_smoke/` exits with code 2 on endpoint mismatch (catches `ANTHROPIC_BASE_URL` env leakage). Requires one-time operator setup: `mkdir -p /opt/ua_demos` + `claude /login` with the Max plan account. See [Demo Workspace Provisioning Runbook](../operations/demo_workspace_provisioning.md).

## Canonical Paths

| Surface | Path |
| --- | --- |
| Packet root | `UA_ARTIFACTS_DIR/proactive/claude_code_intel/packets/` |
| State files (per-handle) | `UA_ARTIFACTS_DIR/proactive/claude_code_intel/state__{handle}.json` |
| Lane ledger root | `UA_ARTIFACTS_DIR/proactive/claude_code_intel/ledger/` |
| OAuth state | `UA_ARTIFACTS_DIR/proactive/claude_code_intel/oauth2/` |
| Lightweight source index | `UA_ARTIFACTS_DIR/knowledge-bases/claude-code-intelligence/source_index.md` |
| External vault | `UA_ARTIFACTS_DIR/knowledge-vaults/claude-code-intelligence/` |
| Rolling current artifacts | `UA_ARTIFACTS_DIR/proactive/claude_code_intel/rolling/current/` |
| Rolling history | `UA_ARTIFACTS_DIR/proactive/claude_code_intel/rolling/history/` |
| Repo capability library | `agent_capability_library/claude_code_intel/current/` |
| Operator skill | `.claude/skills/claudedevs-x-intel/` |
| Operator report script | `src/universal_agent/scripts/claude_code_intel_run_report.py` |
| Dashboard route | `web-ui/app/dashboard/claude-code-intel/page.tsx` |
| Dashboard API (read) | `GET /api/v1/dashboard/claude-code-intel` |
| Dashboard API (trigger) | `POST /api/v1/dashboard/claude-code-intel/trigger` |
| Lane config (v2) | `src/universal_agent/config/intel_lanes.yaml` |
| Vault `_history/` snapshots (v2) | `UA_ARTIFACTS_DIR/knowledge-vaults/<vault_slug>/_history/<kind>/<slug>/<iso>.md` |
| Phase 0 infrastructure pages (v2) | `UA_ARTIFACTS_DIR/knowledge-vaults/<vault_slug>/infrastructure/` |
| Demo workspace root (v2) | `/opt/ua_demos/<demo-id>/` (env override `UA_DEMOS_ROOT`) |
| Smoke demo workspace (v2) | `/opt/ua_demos/_smoke/` |
| Dependency-currency CLI (v2) | `src/universal_agent/scripts/dependency_currency_sweep.py` |

## Runtime Flow

```mermaid
flowchart TD
    Cron["claude_code_intel_sync"] --> Fetch["Fetch @ClaudeDevs via X API"]
    Fetch --> Packet["Write packet files"]
    Packet --> Enrich["URL Enrichment (3-pass)"]
    Enrich --> Filter["Pass 1: Regex pre-filter"]
    Filter --> Judge["Pass 2: LLM judge"]
    Judge --> Scrape["Pass 3: Selective fetch"]
    Scrape --> Classify["Tier posts (with linked context)"]
    Classify --> Queue["Queue Tier 3/4 Task Hub work"]
    Packet --> Replay["Post-process / replay packet"]
    Replay --> Sources["Fetch linked sources"]
    Sources --> Ledger["Write candidate ledger"]
    Sources --> Vault["Populate external knowledge vault"]
    Queue --> Todo["Simone / VP execution"]
    Todo --> Verify["Record outbound delivery evidence"]
    Verify --> Audit["Auditable linkage: post -> task -> assignment -> email -> wiki"]
```

## Packet Contract

Each run writes:

- `manifest.json`
- `raw_user.json`
- `raw_posts.json`
- `new_posts.json`
- `actions.json`
- `triage.md`
- `digest.md`
- `source_links.md`

Replay/post-processing adds:

- `linked_sources.json`
- `linked_sources/<hash>/metadata.json`
- `linked_sources/<hash>/source.md`
- `linked_sources/<hash>/analysis.md`
- `implementation_opportunities.md`
- `candidate_ledger.json`
- `replay_summary.json`

## Classification Contract

The system uses four outcome types:

| Tier | Action type | Meaning |
| --- | --- | --- |
| 1 | `digest` | Informational update, low direct implementation value |
| 2 | `kb_update` | Reference/docs/usage/release note update |
| 3 | `demo_task` | Code-worthy or implementation opportunity |
| 4 | `strategic_follow_up` | Migration risk, bug, breakage, or strategic operational issue |

### LLM-Assisted Classification

The classifier now runs in two layers:

1. deterministic fallback
2. optional LLM override

The fallback explicitly downshifts generic community/event posts such as hackathons and application announcements when they lack stronger engineering implications. The LLM layer can further refine this judgment.

## URL Enrichment Pipeline (Live Sync)

As of 2026-04-27, the live sync path (`run_sync`) enriches URLs **before** post classification using a three-pass architecture implemented in `csi_url_judge.py`:

```mermaid
flowchart LR
    A[Post URLs] --> B[Pass 1: Regex Pre-Filter]
    B --> C{Social/product?}
    C -->|Yes| D[Filtered out]
    C -->|No| E[Pass 2: LLM Judge]
    E --> F{Worth fetching?}
    F -->|No| G[Skipped]
    F -->|Yes| H[Pass 3: Selective Fetch]
    H --> I[defuddle / GitHub API / httpx]
    I --> J[linked_context for classifier]
```

### Pass 1: Fast Regex Pre-Filter

Deterministic exclusion of social domains (`twitter.com`, `x.com`, `discord.gg`, etc.), product app domains (`claude.ai`, `chatgpt.com`), and social path keywords (`/discord`, `/newsletter`, `/subscribe`).

### Pass 2: LLM Judge

Remaining URLs are evaluated by an Anthropic LLM (via `resolve_sonnet()` and ZAI proxy) using `tool_use` structured output with Pydantic validation. Each URL receives a `UrlVerdict` with:

- `category`: `github_repo`, `documentation`, `blog_post`, `api_reference`, `dataset`, `tool_page`, `changelog`, `promotional`, `media_only`, `social_noise`, `other`
- `worth_fetching`: boolean
- `reasoning`: brief explanation

Falls back to domain-based heuristics if no LLM key is available.

### Pass 3: Selective Fetch

Only URLs marked `worth_fetching: true` are fetched, using:
- **GitHub repos**: README + metadata via GitHub API (fast, no clone)
- **Web pages**: `defuddle-cli` for clean markdown extraction, `httpx` as fallback
- Content is truncated at 20,000 characters

The enriched content is assembled into a `linked_context` string and passed to `classify_post()`, allowing the tier classifier to make informed decisions based on actual linked content rather than just URL strings.

### Feature Flag

| Variable | Default | Description |
|---|---|---|
| `UA_CSI_URL_ENRICHMENT_ENABLED` | `1` | Enable/disable URL enrichment in live sync |

### Impact on Classification

With URL enrichment, the classifier receives actual page content alongside the tweet text. Testing shows this materially improves tier accuracy — for example, a post linking to documentation pages is correctly classified as Tier 2 (kb_update) rather than over-promoted to Tier 3 (demo_task).

## Linked Source Expansion (Replay Path)

The replay/post-processing path provides a **second layer** of source expansion beyond the live enrichment.

For each direct linked source, the replay path:

- classifies the source type
- fetches the source in a bounded way
- stores fetch metadata
- stores a normalized source snapshot
- writes a first-pass analysis
- ingests the fetched content into the external vault

Guardrail:

- direct `x.com` / `twitter.com` links are skipped up front
- redirects that land on browser-gated `x.com` / `twitter.com` shells are also skipped after fetch classification
- JavaScript-blocked social shells are preserved only as fetch metadata / analysis, not ingested as knowledge pages

Recognized source types include:

- `github_repo`
- `github_file`
- `github_tree`
- `docs_page`
- `vendor_docs`
- `event_page`
- `x_page`
- `non_html`
- `generic_web`

The classifier also uses fetched-source summary/context to refine post classification after fetch/replay, so event/community links can be down-weighted while stronger GitHub/docs evidence can support implementation-oriented routing.

## Candidate Ledger

The per-post ledger is the main audit surface for the subsystem.

Each ledger row can now include:

- post ID and post URL
- tier and action type
- intended source kind and intended task ID
- current Task Hub row and status
- assignment IDs, states, result summaries, and workspaces
- outbound-delivery markers
- email evidence ids from assignment workspaces
- packet artifact id and candidate artifact id
- post source pages
- linked source pages
- work product pages
- combined wiki pages

This is the durable answer to "what happened to this ClaudeDevs post?"

## Delivery Verification

Task Hub completion for email/report-style work is still guarded by outbound-delivery verification.

The key improvement here is that `agentmail_send_with_local_attachments` now records task-scoped delivery evidence during `todo_execution`, so attachment-heavy Claude Code work can complete normally when a real AgentMail send occurred.

## Cleanup Utility

New ClaudeDevs cron sessions are now heartbeat-exempt, so future packet workspaces should stay clean.

Older polluted workspaces can be cleaned with:

```bash
PYTHONPATH=src uv run python -m universal_agent.scripts.claude_code_intel_cleanup_workspace \
  --workspace-dir <AGENT_RUN_WORKSPACES>/cron_claude_code_intel_sync
```

Apply mode:

```bash
PYTHONPATH=src uv run python -m universal_agent.scripts.claude_code_intel_cleanup_workspace \
  --workspace-dir <AGENT_RUN_WORKSPACES>/cron_claude_code_intel_sync \
  --apply
```

The cleanup is conservative. It archives only clearly heartbeat-specific artifacts and leaves mixed transcript/trace/log files in place for forensic integrity.

Status update (2026-04-22): the production `cron_claude_code_intel_sync` workspace has already been cleaned once with this utility. Archived cleanup manifests live under the workspace `archive/claude_code_intel_cleanup_*` directories on the VPS.

## Operator Skill And Report Surface

The operator-facing entry point is:

```bash
PYTHONPATH=src uv run python -m universal_agent.scripts.claude_code_intel_run_report \
  --profile vps \
  --email-to kevinjdragan@gmail.com
```

That command still uses the same underlying sync + replay pipeline, but it additionally writes:

```text
operator_report.md
operator_report.json
```

into the newest packet and can send an email containing HTTPS links to the key packet artifacts.

The built-in production cron now uses the same report entry point instead of the bare sync script. Default behavior:

- if `action_count == 0`, no automatic email is sent
- if `action_count > 0`, the report email is sent automatically
- recipient resolution:
  - `UA_CLAUDE_CODE_INTEL_REPORT_EMAIL_TO` if set
  - otherwise `kevinjdragan@gmail.com` on `UA_DEPLOYMENT_PROFILE=vps`
  - otherwise no automatic email target

## Manual Operator Surface

The new skill is intentionally a manual/on-demand operator surface, not a second autonomous scheduler.

Recommended manual invocation:

```text
$claudedevs-x-intel Run the production ClaudeDevs X intelligence sync, write the operator report summary, and email the results to kevinjdragan@gmail.com.
```

The canonical autonomous scheduler remains the built-in system job `claude_code_intel_sync`.

## Dashboard Review Surface

The dashboard now includes a dedicated Claude Code intelligence page instead of forcing operators to browse raw artifact folders manually.

Current page goals:

- show the latest operator report in a readable panel
- keep recent packet history visible in one place
- expose direct links to packet sub-artifacts
- make the external Claude Code vault searchable by title, summary, and tags

Current route:

```text
/dashboard/claude-code-intel
```

Current read endpoint:

```text
GET /api/v1/dashboard/claude-code-intel
```

That endpoint returns:

- current ClaudeDevs lane checkpoint state
- latest packet summary
- recent packet history
- rolling 14-day narrative brief
- synthesized capability bundles and variants
- vault index/overview links
- knowledge-page records for the external Claude Code vault

## Rolling Builder Brief And Capability Bundles

Successful report runs now also synthesize a rolling 14-day builder brief and capability bundles.

The rolling brief serves two audiences in one artifact:

- `For Kevin` — explanatory teaching layer
- `For UA` — dense adoption package for agent reuse

Capability bundles are generated from recent packet history and linked canonical sources, then materialized into:

- current artifact snapshots under `artifacts/proactive/claude_code_intel/rolling/current/`
- historical snapshots under `artifacts/proactive/claude_code_intel/rolling/history/`
- a versioned repo library under `agent_capability_library/claude_code_intel/current/`

Each bundle preserves:

- bundle-level summary and “why now”
- canonical linked sources
- Kevin-facing explanation
- UA-facing adoption package
- multiple variants when the implementation path is uncertain
- machine-usable primitives such as workflow recipes, prompt patterns, adaptation patterns, and other low-risk derivative assets

## Key Files

| File | Role |
| --- | --- |
| [`claude_code_intel.py`](../../src/universal_agent/services/claude_code_intel.py) | Live X polling, URL enrichment orchestration, post classification, packet creation, Task Hub queueing |
| [`csi_url_judge.py`](../../src/universal_agent/services/csi_url_judge.py) | Three-pass URL enrichment pipeline (regex filter → LLM judge → selective fetch) with Pydantic validation |
| [`claude_code_intel_replay.py`](../../src/universal_agent/services/claude_code_intel_replay.py) | Replay, source expansion, ledger writing, vault population |
| [`claude_code_intel_sync.py`](../../src/universal_agent/scripts/claude_code_intel_sync.py) | Cron/script entry point |
| [`claude_code_intel_replay_packet.py`](../../src/universal_agent/scripts/claude_code_intel_replay_packet.py) | Replay/backfill entry point |
| [`claude_code_intel_run_report.py`](../../src/universal_agent/scripts/claude_code_intel_run_report.py) | Operator run + summary + email entry point |
| [`x_oauth2_bootstrap.py`](../../src/universal_agent/scripts/x_oauth2_bootstrap.py) | OAuth2 bootstrap and token refresh |
| [`claude_code_intel_cleanup_workspace.py`](../../src/universal_agent/scripts/claude_code_intel_cleanup_workspace.py) | Historical workspace cleanup utility |
| [`claude_code_intel_rollup.py`](../../src/universal_agent/services/claude_code_intel_rollup.py) | v2: 28-day windowed brief + full-corpus capability library synthesis |
| [`research_grounding.py`](../../src/universal_agent/services/research_grounding.py) | v2: official-docs-first research subagent with allowlist enforcement |
| [`intel_lanes.py`](../../src/universal_agent/services/intel_lanes.py) | v2: multi-lane config loader (Pydantic-validated) |
| [`config/intel_lanes.yaml`](../../src/universal_agent/config/intel_lanes.yaml) | v2: lane definitions (Claude Code enabled; Codex/Gemini disabled templates) |
| [`dependency_currency.py`](../../src/universal_agent/services/dependency_currency.py) | v2: drift parsers + vault infrastructure writers + release-announcement detection |
| [`dependency_currency_sweep.py`](../../src/universal_agent/scripts/dependency_currency_sweep.py) | v2: Phase 0 daily drift CLI |
| [`demo_workspace.py`](../../src/universal_agent/services/demo_workspace.py) | v2: Phase 3 demo workspace provisioner with vanilla-settings safety net |
| [`wiki/core.py`](../../src/universal_agent/wiki/core.py) | v2: Memex update primitives (`memex_create_page`, `memex_extend_page`, `memex_revise_page`, `memex_apply_action`, `memex_append_change_log`) |

## What Is Still Incomplete

The subsystem is now functionally real, but a few refinements remain:

- broader end-to-end production validation of the URL enrichment pipeline over more real packets
- optional future promotion of Claude Code knowledge into NotebookLM-backed KB flows if desired
- potential Jina Reader fallback for complex, content-heavy pages that resist defuddle extraction

## Related Docs

- [X API And Claude Code Intel Source Of Truth](../03_Operations/118_X_API_And_Claude_Code_Intel_Source_Of_Truth_2026-04-19.md)
- [ClaudeDevs X Intel VPS Runtime Audit](../03_Operations/120_ClaudeDevs_X_Intel_VPS_Runtime_Audit_2026-04-20.md)
- [ClaudeDevs X Intel Implementation Plan](../03_Operations/122_ClaudeDevs_X_Intel_Implementation_Plan_2026-04-21.md)
- [LLM Wiki System](LLM_Wiki_System.md)
- [Proactive Pipeline](Proactive_Pipeline.md)
- **v2 Design Doc:** [ClaudeDevs X Intel v2 Design](../proactive_signals/claudedevs_intel_v2_design.md) — full v2 architecture (Phase 0–5, Memex update model, demo execution contract, backfill plan)
- **v2 Operational Runbook:** [Demo Workspace Provisioning](../operations/demo_workspace_provisioning.md) — one-time `/opt/ua_demos/` setup + `claude /login` with Max plan
- **v2 Environments Map:** [Demo Execution Environments](../06_Deployment_And_Environments/09_Demo_Execution_Environments.md) — how the UA repo (ZAI-mapped) and `/opt/ua_demos/` (Anthropic-native) coexist on the VPS
