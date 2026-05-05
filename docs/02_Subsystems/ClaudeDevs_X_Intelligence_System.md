# ClaudeDevs X Intelligence System

## Purpose

This subsystem turns multiple Claude Code–related X accounts (currently `@ClaudeDevs` and `@bcherny`) into a durable intelligence lane for Universal Agent.

It exists to keep the project current on Claude Code changes that are newer than model training cutoffs, then convert those changes into:

- durable packet artifacts
- a Claude Code external knowledge vault
- candidate ledgers tying posts to work and outcomes
- Task Hub follow-up for higher-value updates
- reviewable analyses, migration notes, and implementation plans

## Current Capability

The system can now:

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
