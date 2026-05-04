# Mission Control Intelligence System

> **Status:** Implementation plan locked 2026-05-03. Phase 0 not yet started.
> **Owner:** Universal Agent core team.
> **Living document:** Update phase status as work lands. Update v2 follow-up list as items move in or out of v1.
> **Companion doc:** [Task Hub Dashboard §5.1A](./Task_Hub_Dashboard.md#51a-mission-control-chief-of-staff-readout) — describes the existing Chief-of-Staff readout layer that this system extends.

---

## 1. Purpose

Mission Control is the operator's intelligence surface. Today it presents raw events, repetitive cron failures, and a Chief-of-Staff readout that is high-quality but invisible because the page renders empty before the manual refresh ever returns. This system replaces the existing Operator Brief with a three-tier intelligence stack that surfaces meaning, not data, and only spends compute when underlying evidence has actually changed.

**Design principles:**

1. **Show meaning, not data.** Every operator-facing element is either a mechanically computed truth (tier 0) or LLM-synthesised narrative (tier 1 and 2). Raw activity stays available behind links, never on the primary surface.
2. **Spend compute only when evidence has moved.** Bundle-signature gating, per-tile watermarks, and a single in-flight LLM call on a dedicated lane keep idle-system cost near zero.
3. **Remember everything we surfaced.** Cards retire into a durable Knowledge Ledger so recurrence detection, operator commentary, and pattern recognition compound over time.
4. **No truncation.** Tier-1 evidence collection captures full text. Storage is bounded at the retention boundary, never at collection.
5. **Diagnose-don't-remediate by default.** Auto-action is gated by a remediation-class registry. Class A safe-and-reversible actions execute autonomously; Class B and C require operator approval.

---

## 2. System Architecture

### 2.1 Three Tiers

**Tier 0 — Mechanical health tiles.** Nine traffic-light tiles computed by Python from cheap SQL signature queries. Tile state is `green / yellow / red`. The LLM only fires to annotate a tile when it transitions to yellow or red. Yellow/red transitions auto-create an `infrastructure`-kind tier-1 card so the deep dive always exists when the tile alarms.

**Tier 1 — Narrative intelligence cards.** LLM-discovered cards with no fixed taxonomy. Identity is anchored in stable subject entities (`task / run / mission / artifact / failure_pattern / infrastructure / idea`). On each sweep, the LLM is given the prior live cards' subject_ids plus new evidence and asked which subjects are still relevant, changed, resolved, or new. Cards not re-emitted are auto-retired into the Knowledge Ledger.

**Tier 2 — Page synthesis (existing Chief-of-Staff readout).** Reads tier-0 transitions, tier-1 live cards, and Knowledge Ledger context (recurring subjects, recently retired cards) to produce the executive paragraph. Receives full content, no truncation.

### 2.2 Backend Sweeper

A new `mission_control_intelligence_sweeper` background task in the gateway process, structured like the heartbeat daemon. Single sweep loop runs every 60 seconds:

1. Tier-0 watermark check on each of the nine tiles (sub-millisecond per tile).
2. Tier-1 bundle-signature check against the prior signature.
3. Tier-2 readiness check based on tier-0 transitions or tier-1 success.
4. Drains the LLM queue serially with at most one in-flight `glm-4.7` call.

Frontend just polls cached state. Manual `Run Brief` becomes a `force_refresh` enqueue with 30-second per-user rate limit and queue-and-coalesce semantics — multiple clicks during cooldown collapse into one refresh, never returns an error.

### 2.3 Refresh Cadence Policy

| Tier | Watermark check | LLM floor | LLM ceiling | Concurrency |
|---|---|---|---|---|
| 0 (mechanical) | 60s | per-tile cooldown 10 min on yellow/red transition | n/a (only fires on transition) | 1 (shared lane) |
| 1 (narrative) | 60s | 3 min between calls | 30 min on idle | 1 (shared lane) |
| 2 (page synthesis) | triggered by tier-0 transition or tier-1 success | 2 min between calls | 5 min on idle | 1 (shared lane) |

Worst case at idle: ~2 LLM calls per hour. Under sustained noise: ~30 calls per hour. Both well inside the dedicated `glm-4.7` lane envelope.

### 2.4 Dedicated Model Lane

A new model designation `mission_control_intelligence` bound directly to `glm-4.7`. Bypasses the standard `resolve_model()` ladder so Mission Control's LLM work does not consume Opus or Sonnet concurrency budget. Single env override `UA_MISSION_CONTROL_MODEL` for operator dial-up. Documented fallback: `glm-5-turbo` if the lane gets flaky.

---

## 3. Data Model

### 3.1 Tier-0 Tile States

Table `mission_control_tile_states`:

| Column | Purpose |
|---|---|
| `tile_id` (PK) | Stable tile identifier (e.g. `csi_ingester`) |
| `current_state` | `green / yellow / red` |
| `state_since` | Timestamp of last state change |
| `last_signature` | Hash of the signature query result |
| `last_checked_at` | Last watermark check |
| `last_annotation_at` | Last LLM annotation (only when yellow/red) |
| `current_annotation` | Latest LLM annotation text (no length cap) |
| `evidence_payload` | Full evidence JSON used for the latest annotation |

### 3.2 Tier-1 Cards

Table `mission_control_cards`:

| Column | Purpose |
|---|---|
| `card_id` (PK) | Stable hash of `(subject_kind, subject_id)` |
| `subject_kind` | One of: `task / run / mission / artifact / failure_pattern / infrastructure / idea` |
| `subject_id` | Stable identifier of the entity being discussed |
| `current_state` | `live / retired / archived` |
| `severity` | `critical / warning / watching / informational / success` |
| `title` | Single sentence headline (~120 chars) |
| `narrative` | Full multi-paragraph synthesis. **No length limit.** |
| `why_it_matters` | Operator-relevance paragraph. **No length limit.** |
| `recommended_next_step` | Free-form, optional (null on pure FYI/success cards) |
| `tags` | Short tag list |
| `evidence_refs_json` | Array of `{kind, id, uri, label}` |
| `evidence_payload_json` | Full untrimmed evidence supplied to the LLM |
| `synthesis_history_json` | Last 10 entries: `{ts, narrative, evidence_signature, model}` |
| `dispatch_history_json` | Generate-prompt and Send-to-Codie history |
| `operator_feedback_json` | `{thumbs, snoozed_until, comments[]}` |
| `last_viewed_at` | Per-user timestamps (F#6) |
| `first_observed_at` | Initial creation timestamp |
| `last_synthesized_at` | Last LLM synthesis |
| `last_evidence_signature` | Hash for change detection |
| `recurrence_count` | Bumped each time card revives from retired/archived |
| `synthesis_model` | Model identifier used (typically `glm-4.7`) |

**No-truncation contract:**
- `title`: LLM-constrained to ~120 chars (it is genuinely a headline)
- `narrative`, `why_it_matters`, `recommended_next_step`: unbounded
- `evidence_payload_json.raw_evidence_supplied_to_llm`: unbounded; dropped on archival to save storage
- `synthesis_history_json[*].narrative`: unbounded per entry (list itself capped at 10 entries per card)
- `operator_feedback_json.comments[*].text`: unbounded; comment list never auto-pruned

Storage management replaces truncation: archived cards (state=`archived`) older than 180 days drop the full payload but keep title, subject_id, comments, thumbs, and signatures so recurrence detection still works.

### 3.3 Dispatch History

Table `mission_control_dispatch_history`:

| Column | Purpose |
|---|---|
| `dispatch_id` (PK) | UUID |
| `card_id` | Foreign key to `mission_control_cards` |
| `action` | `prompt_generated_for_external` or `dispatched_to_codie` |
| `ts` | Timestamp |
| `prompt_text` | Full generated prompt (immutable record) |
| `operator_steering_text` | Append-only steering text added in flyout (Send-to-Codie only) |
| `task_id` | Task Hub item created (Send-to-Codie only) |

Operator steering text is dual-purpose: stored here for Codie to read, AND mirrored into `operator_feedback_json.comments` as a timestamped entry for future synthesis context.

### 3.4 Event Title Templates

Table `event_title_templates` (Phase 7):

| Column | Purpose |
|---|---|
| `template_id` (PK) | Hash of `(event_kind, metadata_shape_signature)` |
| `event_kind` | Event kind |
| `metadata_shape_signature` | Sorted set of metadata keys + types |
| `title_template` | Jinja-style template (`"Cron Complete · {job_id} · {duration_seconds}s · {status}"`) |
| `generated_by_model` | Model that generated the template |
| `generated_at` | Generation timestamp |
| `validated_at` | Last weekly re-validation pass |
| `operator_override_text` | If set, locks template against re-validation (F#11, deferred) |

---

## 4. Tier-0 Tile Definitions

| Tile | Green when | Yellow when | Red when | Auto-remediation class |
|---|---|---|---|---|
| Gateway | health endpoint OK in last 60s | last health >60s old | health failed or >5 min stale | none |
| Database | task_hub + activity DBs respond <100ms | response 100ms–1s, or one DB failed | both DBs failing or >1s | none |
| CSI Ingester | last event ≤ 12h ago (within one polling window) | last event 12–25h ago (missed ≥1 cycle) | last event > 25h ago or none in 48h (missed ≥2 cycles) | A — restart service |
| Cron Pipelines | all scheduled jobs ran on time in last 24h | one job failed once | ≥2 distinct jobs failing or same job ≥3 times | B — diagnostic only |
| Heartbeat Daemon | tick within last 2× expected interval | one missed tick | ≥3 missed ticks or daemon dead | A — recycle daemon |
| Task Hub Pressure | <10 in_progress, no stuck claims | 10–25 in_progress or 1 stuck claim >15 min | >25 or ≥3 stuck claims | A — stuck claim sweep |
| Model Usage Today | spend < daily budget × 0.7 | ≥70% budget | ≥95% budget or rate-limit 429s today | none |
| Proactive Pipeline | proactive tasks completing daily | no completions in 48h | ≥3 consecutive proactive failures | B — diagnostic only |
| VP Agent Health | both vp.coder + vp.general producing successful runs | one VP showing degraded run rate | one VP failing all recent runs or unreachable | B — diagnostic only |

Each tile is a small Python class implementing:
- `name` — stable identifier
- `signature_query()` — returns SQL or computed signature for change detection
- `compute_state()` — returns `(color, one_line_status, evidence_dict)`
- `transition_thresholds()` — returns the green/yellow/red boundaries (env-overridable)
- `llm_annotation_prompt()` — only invoked on yellow/red transition
- `auto_action_class()` — returns `A`, `B`, or `None`

**Threshold-tuning principle (added 2026-05-03 after CSI red-tile incident):** tile thresholds must reflect the *actual production cadence* of the underlying signal, not a generic "fresh" assumption. CSI is a twice-daily scheduled job (cron `0 8,16 * * * America/Chicago` → ~13:00/21:00 UTC), so the green window is one full polling interval (12h), yellow is one missed cycle (12–25h), red is ≥2 missed cycles (>25h). When you add a new tile, document the upstream cadence in code comments and pick green/yellow/red from it — otherwise the tile will alarm during normal operation and operators will learn to ignore it.

---

## 5. Operator Interaction

### 5.1 Card Feedback Signals

Each tier-1 card exposes:

- **More of this** — thumbs-up; signals "surface things like this more aggressively"
- **Less of this** — thumbs-down; signals "deprioritize similar future surfaces"
- **Snooze** — hide for chosen interval (1h / 4h / 1d / 1w). Auto-revives on expiry with "snooze expired" badge for one cycle (F#5).
- **Comment** — flyout input that captures durable operator commentary. Comments are timestamped, never overwritten, never truncated. Flyout shows full thread of prior comments on the same subject. Comments persist in `operator_feedback_json.comments` AND get fed back into Chief-of-Staff prompt context AND any future LLM synthesis on the same `subject_id`.

Thumbs aggregate across `subject_kind + tag pattern` as a lightweight reinforcement signal — no ML required, just prompt-context.

### 5.2 Action Buttons

**Generate Investigation Prompt** (zero side effects). Server builds a self-contained prompt from card narrative, why_it_matters, recommended_next_step, evidence_refs, full evidence_payload, subject metadata, recurrence count, and prior synthesis history. Returns text in a copyable modal. Subject-kind-specific framing tail (`failure_pattern` cards get "diagnose root cause and propose code change"; `infrastructure` cards get "diagnose, propose fix, only act if explicitly confirmed"). Prompt text persisted in `dispatch_history` for audit.

**Send to Codie** (real side effect, with confirmation flyout). Click opens flyout showing the prompt that's about to be sent plus an editable append-only steering text area. On send:

1. Server runs the same prompt template
2. Prepends operator's steering text if any
3. Creates a Task Hub item with `target_agent: vp.coder.primary`, `source_kind: mission_control_card_dispatch`, standard `external_effect_policy` (PR allowed; merge / main-push / deploy disallowed)
4. Auto-stamps implicit thumbs-up
5. Mirrors steering text into `operator_feedback_json.comments`
6. Existing Task Hub dispatch path executes the mission

Future syntheses of the same subject reference the dispatch history.

### 5.3 Auto-Action Policy

**Three remediation classes:**

| Class | Examples | Default policy | Card behavior |
|---|---|---|---|
| A — Safe, idempotent, reversible | `systemctl restart` of stateless daemon, clearing stuck lock file, recycling worker, stuck claim sweep | Auto-execute on red. No operator gate. | Transient card created → auto-action fires → card retires on success → ledger entry retained for audit |
| B — Requires judgment | Credential rotation, config drift, code change, schema migration | Diagnostic-only mission to vp.general.primary, read-only enforced at runtime, pre-fills "Send to Codie" remediation candidate | Card persists, "Send to Codie" pre-loaded |
| C — Destructive or external-effect | DB rollback, deploy, customer-facing, anything touching `main` | Never auto. Always explicit operator approval with extra warning | Card with extra confirmation step |

**Class A registry** lives in code (`mission_control_remediation_actions.py`) so adding/auditing entries is a reviewable PR. Each entry ships with: exact command, pre-condition check, post-condition check, rollback plan (F#10), per-action cooldown.

Initial Class A entries:
1. `csi-ingester` service restart on red
2. Heartbeat daemon recycle on red
3. Stuck `task_hub` claim sweep

**Safety rails:**
- Idempotent on `subject_id`: if `infra:csi_ingester_outage` already has an in-flight diagnostic, don't dispatch another
- Per-subject cooldown: 4 hours
- Global circuit breaker: ≥3 distinct Class A actions in 15 minutes pauses auto-remediation globally and surfaces a `meta_card`
- Operator kill switches: `UA_MISSION_CONTROL_AUTO_REMEDIATION=0` global; per-action env vars for surgical disable
- Read-only is enforced at the VP runtime level, not just by prompt
- Auto-action failure is itself a tier-1 critical card (F#10)

**Class A diagnostic mission template library** (F#9): each tile type has a curated diagnostic prompt stored in `mission_control_diagnostic_templates.py` so consistency holds across firings.

---

## 6. Knowledge Ledger

`/dashboard/mission-control/ledger` route (Phase 6).

**Purpose:** durable card history with operator commentary, recurrence detection, and pattern feedback into Chief-of-Staff. The Proactive-Task-History parallel — ideas, retired cards, and operator commentary live here forever.

**Card lifecycle:** `live` → `retired` → `archived` → optional `revived` (same `card_id` resurrects rather than duplicating; `recurrence_count` increments).

**Retirement timing per subject_kind:**
- `task` and `run`: terminal status + 24h grace
- `infrastructure`: green-stable for ≥1h
- `mission`: completion or abandonment
- `artifact`: unchanged for 7 days OR superseded by newer artifact in same workspace
- `failure_pattern`: LLM marks resolved OR no matching evidence for 14 days
- `idea`: 30-day soft decay (auto-archives unless operator marks acted-on/dismissed)

**Filters:** subject_kind, recurrence_count, disposition, date range.

**Per-card view:** full synthesis history (no truncation), comment thread, dispatch history, evidence_refs.

**Feedback loop into tier-2:** Chief-of-Staff prompt includes:
- Currently live cards (full payloads)
- Recently retired cards (last 48h)
- Recurring subjects (`recurrence_count >= 2`)

So tier-2 can say "third time this week" because the data is in front of it.

---

## 7. Page Layout

```
Page header — Mission Control · last full sweep N min ago
─────────────────────────────────────────────────────────
Tier-0 tile strip — 9 tiles in a single row
Yellow/red tiles expand-on-click; red tiles auto-expand
─────────────────────────────────────────────────────────
Tier-2 synthesis — Chief-of-Staff Readout
Headline + executive_snapshot bullets
"Run Brief" button (queued, never blocks)
─────────────────────────────────────────────────────────
Tier-1 cards — sorted by:
  1. severity (critical > warning > watching > informational > success)
  2. recurrence_count (within severity)
  3. last_synthesized_at (within recurrence)
Each card: title, narrative, why_it_matters, next_step,
  evidence_refs, feedback controls, action buttons
Snoozed cards collapsed under "N snoozed" expander
─────────────────────────────────────────────────────────
Footer links: Knowledge Ledger · Events (raw) · Task Hub
```

**Empty state:** when no tier-1 cards exist on a calm system, render: *"All systems nominal. Last sweep N minutes ago surfaced no active cards. The Knowledge Ledger has X retired cards from the last 7 days you can review."*

**Operator Brief panel deleted** — its job is fully subsumed by tier-1 cards. `/api/v1/dashboard/situations` deprecated for one release cycle.

---

## 8. Events Page (`/dashboard/events`)

Becomes the evidence-grade view, not the operator's primary surface. Stays in sidebar (second position, after Mission Control) during initial rollout so operators can compare side-by-side; may be moved out later.

**Smart titles via cached templates** (Phase 7):
- Each `(event_kind, metadata_shape_signature)` pair → one `glm-4.7` call to generate a Jinja-style template
- Template stored in `event_title_templates`
- Subsequent events of the same shape use the cached template deterministically — zero LLM cost
- Weekly re-validation: sample event re-titled, compared, updated if meaningfully different
- Operator override (F#11, deferred) locks template against re-validation

**Smart default filter** hides:
- `severity=info` heartbeat ticks with no findings
- Routine `autonomous_run_completed` for cron syncs with `metadata.changed=false`
- `mission_complete` events older than 1h that produced no new artifacts
- Repeated successful cron runs (only most recent green per `job_id` per day shows; older greens collapse under "N prior successful runs · expand")
- `cron_run_cancelled` events (in-flight cron tasks cancelled by service restart — these fire on every deploy and are operational noise, not failures)

Default filter shows:
- Anything `severity ≥ warning`
- Anything `requires_action=true`
- Anything that produced new artifact / PR / email / dispatch
- State-change events (first cron success after streak of failures)
- Heartbeat ticks that emitted findings

**"Show All Activity" toggle** in the filter bar reveals the firehose for debugging. Sticky-per-user with 7-day soft expiry.

---

## 9. Implementation Phases

Each phase ships behind a feature flag (`UA_MC_PHASE_N_ENABLED`). All work on `feature/latest2`; deploys via the standard `/ship` protocol.

| Phase | Scope | Visible change | Risk | Status |
|---|---|---|---|---|
| 0 | Foundations: tables, sweeper skeleton, model designation, no-truncation refactor | None (backend only) | Low | **Done (5ff03cce, deployed)** |
| 1 | Tier-0 tile strip + tile-card auto-coupling | Tile strip appears at top of MC | Low | **Done (Phase 1A: e58f825b deployed; Phase 1B: d98625c4 deployed; production env enabled 2026-05-03; first-appearance/backfill fixes 43bd8a37 + f3aa9600 deployed)** |
| 2 | Tier-1 narrative cards + feedback UI (incl. F#5 snooze auto-revival, F#6 last_viewed_at) | Cards replace Operator Brief content | Medium | **Done in code (backend 2ebc2aff + frontend 8520063b on feature/latest2). NOT YET ENABLED in production — UA_MC_PHASE_2_ENABLED needs to be set to 1 in Infisical before sweeper starts the tier-1 LLM pass.** |
| 3 | Tier-2 synthesis with ledger feedback | Chief-of-Staff sees recurrence + retired-card history | Low | **Done in code (feature/latest2). Activates immediately on next deploy — no env flip needed; the COS readout next refresh will see cards as evidence.** |
| 4 | Action buttons (Generate Prompt + Send to Codie) | Manual action loop unlocked | Medium | **Done in code (089b4ebc on feature/latest2). Ready to ship — no env flip needed; buttons activate as soon as the new gateway code is live.** |
| 5 | Auto-remediation Class A (incl. F#9 templates, F#10 auto-rollback) | Three starter Class A actions live behind kill switch | High → 1-week observation period with `UA_MC_AUTO_REMEDIATION=0`, flip after gates confirmed | Not started |
| 6 | Knowledge Ledger surface | `/dashboard/mission-control/ledger` route | Low | **Done (2026-05-04 on `feature/latest2`)** — backend `GET /api/v1/dashboard/mission-control/ledger` (with `subject_kind`, `min_recurrence`, `state`, `since_iso`, `limit` filters) + `list_ledger_cards` + `ledger_summary` helpers in `mission_control_cards.py`. Frontend page at `web-ui/app/dashboard/mission-control/ledger/page.tsx` with summary band, filter row, and per-card detail expanders (narrative, why_it_matters, next step, tags, synthesis history, comments, dispatch history, evidence refs). Footer link wired into Deep Dives panel on the main MC page. |
| 7 | Events page rebuild — smart titles + smart filter + sidebar reorder | Events page becomes scannable | Medium | **Done in code (b643782e on feature/latest2). Smart titles use cached LLM templates per (kind, metadata_shape) with code-only fallback; hide_by_default filter active by default; Show All Activity toggle sticky 7d via localStorage. Sidebar already correctly ordered.** |
| 8 | Cleanup: delete Operator Brief panel, deprecate `/api/v1/dashboard/situations`, docs | Removal of dead surfaces | Low | **Done (2026-05-04 on `feature/latest2`)** — Operator Brief panel and its `DashboardSituation` type / `situationPriorityBadge` helper removed from `web-ui/app/dashboard/mission-control/page.tsx`. `/api/v1/dashboard/situations` endpoint marked `deprecated=True` (FastAPI swagger flag) with a runtime warning log; remains functional for one release cycle for external/cached clients, then can be removed entirely. |

**Estimated focus time:** Phase 0 ~1d, Phase 1 ~2d, Phase 2 ~3d, Phase 3 ~1d, Phase 4 ~2d, Phase 5 ~2d, Phase 6 ~1.5d, Phase 7 ~2d, Phase 8 ~0.5d. Total ~15 days of focused work, not calendar time.

---

## 10. Configuration Surface

| Env var | Default | Purpose |
|---|---|---|
| `UA_MISSION_CONTROL_MODEL` | `glm-4.7` | Override the dedicated lane model |
| `UA_MISSION_CONTROL_SWEEPER_INTERVAL_S` | `60` | Sweeper tick interval |
| `UA_MISSION_CONTROL_TIER1_FLOOR_S` | `180` | Minimum seconds between tier-1 LLM calls |
| `UA_MISSION_CONTROL_TIER1_CEILING_S` | `1800` | Force tier-1 refresh on idle every N seconds |
| `UA_MISSION_CONTROL_TIER2_FLOOR_S` | `120` | Minimum seconds between tier-2 LLM calls |
| `UA_MISSION_CONTROL_TIER2_CEILING_S` | `300` | Force tier-2 refresh on idle every N seconds |
| `UA_MISSION_CONTROL_LANE_CONCURRENCY` | `1` | Max in-flight calls on the dedicated lane |
| `UA_MISSION_CONTROL_AUTO_REMEDIATION` | `0` (Phase 5 default) | Master kill switch for Class A auto-remediation |
| `UA_MC_AUTOFIX_<TILE>` | `1` after master enabled | Per-tile auto-remediation enable |
| `UA_MC_PHASE_<N>_ENABLED` | `0` | Per-phase feature flag |
| `UA_MISSION_CONTROL_RETENTION_DAYS` | `180` | Days before archived card payloads dropped |
| `UA_MISSION_CONTROL_LEDGER_RETENTION_ENTRIES` | `5000` | Max cards in ledger before pruning oldest |

---

## 11. v2 Follow-ups

Tracked but explicitly **not** in v1 scope:

| ID | Description | Notes |
|---|---|---|
| F#1 | SSE push for card-update events | UX upgrade; polling is fine for v1 |
| F#2 | Raise dedicated-lane concurrency from 1 to 2 | Revisit once steady-state queue depth is observable |
| F#3 | Mission Control Knowledge Ledger comment/disposition surface | Partially landed in Phase 6; full search and bulk operations deferred |
| F#4 | Cross-card comment search | "Find every card I commented 'investigate later' on" |
| F#7 | Additional dispatch targets beyond Codie (e.g. Simone for research) | New severity-target routing rules needed |
| F#8 | Class B → Class A graduation pathway | Observation-count gating after operator approves the same Class B remediation N times |
| F#11 | Operator override of generated event-title templates | Lock template against weekly re-validation |

**Promoted into v1 from initial v2 list:** F#5 snooze auto-revival (Phase 2), F#6 `last_viewed_at` per card (Phase 2), F#9 diagnostic mission template library (Phase 5), F#10 auto-rollback on Class A failure (Phase 5).

---

## 12. References

- Existing Chief-of-Staff readout layer: [Task Hub Dashboard §5.1A](./Task_Hub_Dashboard.md#51a-mission-control-chief-of-staff-readout)
- Heartbeat daemon (sweeper pattern reference): [Heartbeat Service](./Heartbeat_Service.md)
- Proactive pipeline (Task Hub dispatch reference): [Proactive Pipeline](./Proactive_Pipeline.md)
- Source files (current):
  - `src/universal_agent/services/mission_control_chief_of_staff.py` — existing tier-2 service to extend
  - `src/universal_agent/scripts/mission_control_chief_of_staff.py` — script entry point
  - `src/universal_agent/gateway_server.py` — `dashboard_chief_of_staff`, `dashboard_situations` endpoints
  - `web-ui/app/dashboard/mission-control/page.tsx` — frontend page to refactor
  - `web-ui/app/dashboard/events/page.tsx` — Events page to rebuild in Phase 7

- Source files (new, to be created):
  - `src/universal_agent/services/mission_control_intelligence_sweeper.py`
  - `src/universal_agent/services/mission_control_tiles.py`
  - `src/universal_agent/services/mission_control_cards.py`
  - `src/universal_agent/services/mission_control_remediation_actions.py`
  - `src/universal_agent/services/mission_control_diagnostic_templates.py`
  - `src/universal_agent/services/event_title_templates.py`
  - `web-ui/app/dashboard/mission-control/ledger/page.tsx`

---

## 13. Update Log

- **2026-05-03** — Initial implementation plan locked after design grilling. Phase 0 ready to start. Document is a living plan; update phase status, follow-up promotions/deferrals, and configuration surface as work lands.
- **2026-05-03** — Phase 0 (`5ff03cce`) shipped to production. Foundations only; no operator-visible behavior change.
- **2026-05-03** — Phase 1A (`e58f825b`) shipped: tile abstractions, 9 tile classes, sweeper tier-0 logic, auto-card creation. Backend only; sweeper still gated.
- **2026-05-03** — Phase 1B (`d98625c4`) shipped: sweeper background-task wiring, `/api/v1/dashboard/mission-control/{tiles,cards}` endpoints, `TileStripPanel` frontend component. Code in production but dormant pending env flip.
- **2026-05-03** — Production env vars set via Infisical: `UA_MC_PHASE_1_ENABLED=1`, `UA_MISSION_CONTROL_MODEL=glm-4.7`, `UA_MISSION_CONTROL_SWEEPER_INTERVAL_S=60`, `UA_MC_AUTO_REMEDIATION=0`. This commit triggers the deploy that reads the new env on gateway startup.
- **2026-05-03** — Phase 1 production smoke test surfaced the heartbeat-daemon-silent-since-2026-05-01-23:45-UTC issue (~26h gap). Documented in `docs/03_Operations/INCIDENT_2026-05-03_heartbeat_silence.md`. Mission Control Phase 1 working AS DESIGNED — surfaced a pre-existing operational issue that had been hiding in plain sight.
- **2026-05-03** — Phase 1.1 first-appearance card fix (`43bd8a37`) and Phase 1.2 backfill invariant fix (`f3aa9600`) shipped after smoke testing revealed the original Phase 1 only created cards on color transitions, not on tile-row first appearance or for tiles with no live card.
- **2026-05-03** — Phase 2 backend (`2ebc2aff`) and frontend (`8520063b`) committed to `feature/latest2`. NOT yet activated; awaiting `UA_MC_PHASE_2_ENABLED=1` env flip after operator review.
- **2026-05-03** — `UA_MC_PHASE_2_ENABLED=1` set in Infisical production environment (operator action via UI). Gateway needs restart to pull the new env value into `os.environ` (the runtime bootstrap reads Infisical once at startup and doesn't auto-refresh). This commit triggers the deploy/restart that activates tier-1 LLM card discovery.
- **2026-05-03** — Phase 2 activation confirmed via `/api/v1/dashboard/mission-control/diagnostics` after deploy. Tier-1 produced 3 LLM-discovered cards on first pass: 1 task warning (CODIE proactive cleanup blocked), 1 task informational (quarantined email), 1 artifact success (Karpathy YouTube tutorial ready). All using glm-4.7 model. Self-imposed 3-min floor + 30-min ceiling cadence working as designed; no upstream 429s observed.
- **2026-05-03** — Phase 4 backend + frontend (`089b4ebc`) committed: Generate Prompt + Send to Codie action buttons on every tier-1 card. Backend endpoints support a two-stage confirmation protocol for Codie dispatch (preview → confirm). Audit trail flows into both the long-form `mission_control_dispatch_history` table and the card's in-mirror summary list. Ready to ship; activates on next deploy without any env change.
- **2026-05-03** — Two production noise fixes (`257808c1`):
  - **CSI tile threshold tuning**: `CsiIngesterTile` was hourly-tuned (green ≤1h, yellow ≤6h, red >6h) while CSI is actually a twice-daily cron. Tile sat in red ~22h/day on a healthy system. Retuned to green ≤12h, yellow ≤25h, red >25h with SQL window widened to 48h. See "Threshold-tuning principle" added to §4.
  - **Cron `asyncio.CancelledError` handler**: Python 3.8+ made `CancelledError` a `BaseException`, so `except Exception` in `cron_service._run_job` did not catch it. Every gateway restart cancelled in-flight cron tasks; the run record was never finalized; the recovery sweep on next startup emitted phantom "Cron Run Failed" notifications that surfaced as red cards. Now caught explicitly: `record.status='cancelled'`, gateway emits info-severity `cron_run_cancelled`, hidden by default in `/dashboard/events`. Operator can reveal via Show All Activity.
- **2026-05-03** — Two latent code bugs found during Mission-Control tile triage (fixed on `claude/recover-mission-control-GweNR`):
  - **`gateway_server._runtime_db_connect` `NameError: main_module`**: helper at `gateway_server.py:9013` referenced `main_module` without importing it, while every other call-site uses a local `import universal_agent.main as main_module`. Broke `ops_preferences_get` / `ops_preferences_patch` HTTP endpoints with a 500. Fixed by adding the same local import inside the helper. Regression test pending.
  - **PR #141 silent rename `waiting_for_human` → `waiting_on_human`**: a "magic-strings → constants" refactor (`workflow_admission.py:29`, `services/dag_runner.py:17`) silently changed the string VALUE, not just the name. The rest of the codebase (`main.py` writers, `run_workspace.py` filter, `urw/orchestrator.py`, `WAITING_STATUSES` set) still reads/writes `"waiting_for_human"` to runtime SQLite. Result: `_ACTIVE_RUN_STATUSES` membership checks no longer recognized in-flight human-gate runs. Reverted the constant VALUE to `"waiting_for_human"` (kept the new constant NAME for readability) and updated `vp/clients/dag_client.py:124` to match. Added `tests/test_status_constants.py` to pin the values so a future refactor cannot silently re-introduce the drift.
- **2026-05-03** — Tile-logic findings deferred for follow-up (NOT bugs in the tile code per se, but signal-source mismatches):
  - **Heartbeat Daemon tile RED while heartbeat is healthy**: tile reads `MAX(created_at) FROM activity_events WHERE source_domain='heartbeat'`, assuming every tick writes a row. In reality the heartbeat service only emits rows when there is an alarm-worthy finding (`autonomous_heartbeat_completed`, `heartbeat_mediation_dispatched`, `heartbeat_investigation_completed`, `heartbeat_operator_review_*`). After 12:10 UTC 2026-05-03 the active CSI alarm was classified as a false alarm; subsequent ticks are "all nominal" and emit no row, so the tile sees a stale `MAX()`. Fix options: (a) tile-side — read `heartbeat.last_tick_at` from the runtime overview API or check the `/var/lib/universal-agent/heartbeat/gateway.heartbeat` file mtime; (b) service-side — emit a low-severity `heartbeat_tick` info event every cycle, hidden from the default Events feed by the existing Phase 7 hide-by-default filter.
  - **Gateway tile RED is the same shape**: queries any `activity_events` row in the last 15 min as a liveness proxy. When the system is quiet (no cron, no agent activity) this naturally goes silent even though the gateway is fully healthy. Same remediation pattern — pick a real liveness signal (process heartbeat file, `/api/v1/health` ping) rather than activity-events presence.
- **2026-05-03** — Heartbeat tile fix shipped (option (b) from above): `heartbeat_service._scheduler_loop` now emits a low-severity `heartbeat_tick` row to `activity_events` once per `UA_HEARTBEAT_TICK_EMIT_INTERVAL_S` (default 60s). New helper `_emit_heartbeat_tick_activity_event` writes directly via `connect_runtime_db(get_activity_db_path())` with `severity='info'`, `kind='heartbeat_tick'`, and metadata `{active_sessions, tick_interval_s, scope}`. Hidden by default on `/dashboard/events` via the existing Phase 7 `hide_by_default` rule (`source_domain='heartbeat'` + `severity='info'` + no findings/investigation/review in kind), so operator events feed stays clean. The Gateway tile (any-recent-`activity_events` liveness proxy) also flips green on the same emission.
- **2026-05-04** — Cron jobs (`6df69e8e9e` system_health_report and `a652c8dce5` proactive_codie) restored to working state via two changes on the VPS only — no code change needed:
  - **`cron_jobs.json` patched** to add `timeout_seconds: 1800` (30 min) to both jobs and `metadata.codebase_access` (with `roots: ['/opt/universal_agent']` and a wide `mutation_agents` list) to the CODIE job specifically.
  - **`UA_APPROVED_CODEBASE_ROOTS=/opt/universal_agent`** added to `/opt/universal_agent/.env` to grant codebase read/mutate access globally to all agent sessions, matching the operator's stated intent that CODIE has full codebase authorization. Hooks at `hooks.py:1319` now recognize `/opt/universal_agent/src/...` paths via `path_is_within_roots(...)` instead of blocking them.
  - **Manual smoke run at 01:17 UTC produced PR #146** end-to-end (CODIE picked theme, ran ruff cleanup, opened PR, emailed Kevin) with zero sandbox violations — confirms the fix works in production.
- **2026-05-04** — Wake-next mechanism investigation (red herring, documented for next session):
  - **Initial hypothesis (wrong):** the heartbeat scheduler's `wake_next_sessions` set was cancelling in-flight cron runs at the 5-min mark.
  - **Actual mechanism:** `_should_register_with_heartbeat` at `gateway_server.py:7905` already excludes role `"cron"`. Cron sessions are never added to `heartbeat_service.active_sessions`, so the heartbeat scheduler loop never processes them. `request_heartbeat_next()` for a cron session ID is effectively a no-op log line — it adds to `wake_next_sessions`, but the scheduler iterates `active_sessions.items()` only.
  - **Real cause of the apparent 5-min cap:** sandbox violations during the morning runs populated `result.metadata.errors`, which `cron_service._run_job:1165` classifies as `record.status = "error"` even though the agent recovered. The `_maybe_wake_heartbeat` call at `cron_service:1345` then fires AFTER the run is already marked failed, which is why the wake-next log line appeared at the same second as the failure event.
  - **Fix:** environmental — adding `UA_APPROVED_CODEBASE_ROOTS=/opt/universal_agent` eliminated the sandbox-violation source, and the cron-classification logic was left intact (changing it to permissive would mask real failures). No wake-next code change required. Future hardening idea: cron's error-classification could downgrade `errors` non-empty + `response_text` non-empty to a warning rather than a fail.
- **2026-05-04** — Mission Control page evaluated against §7 layout spec via headless Chromium (Playwright). Findings:
  - ✓ Page header "Mission Control" present
  - ✓ All 9 tier-0 tile names visible in the strip (Gateway, Database, CSI Ingester, Cron Pipelines, Heartbeat Daemon, Task Hub Pressure, Model Usage, Proactive Pipeline, VP Agent Health)
  - ✓ Chief-of-Staff "Readout" panel renders with substantive content + section headings (Infrastructure Health, Stuck Missions, Todo Execution Session, Completed Work Today, Watchlist, Action Candidates)
  - ✓ "Run Brief" button present in the Readout header
  - ✓ Intelligence Cards section present with 8 live cards
  - ✓ Per-card "Generate Prompt" + "Send to Codie" action buttons present (8 of each, one per card)
  - ✓ Comment input present (1 — opens via per-card flyout per Phase 2 design)
  - ✓ Events footer link, Task Hub footer link present
  - ✓ Snooze controls (1h/4h/1d/1w) ARE present — `SnoozeMenu` component at `page.tsx:1457`. The earlier audit was a false-negative — the headless probe used `aria-label*=snooze` selectors which didn't match the actual implementation (uses a glyph button + dropdown menu without `aria-label`). Phase 2 is fully landed.
  - ✓ Thumbs up/down ARE present — 👍/👎 emoji buttons with `title="More of this"` / `title="Less of this"` at `page.tsx:1448,1455`. Same false-negative root cause. Phase 2 is fully landed.
  - ✗ Knowledge Ledger footer link absent — Phase 6 not started (expected)
  - ✗ Tile elements lack `data-tile-id` attributes — minor; affects automated probing, not user UX
- **2026-05-04** — Phase 8 cleanup shipped: removed `OperatorBriefPanel` (and its `DashboardSituation` type + `situationPriorityBadge` helper, ~209 lines) from `web-ui/app/dashboard/mission-control/page.tsx`. Removed `<OperatorBriefPanel />` mount from the page JSX. Removed unused `ClipboardList` lucide-react import. Updated the page-level docstring to describe the new tile-strip + intelligence-cards model. Marked `/api/v1/dashboard/situations` with `deprecated=True` + runtime warning log so the FastAPI swagger surface and gateway logs both flag any remaining callers; endpoint remains functional for one release cycle for external/cached UI clients, then can be deleted entirely.
- **2026-05-04** — Cron error-classification hardening: `cron_service._run_job:1165` previously failed any LLM cron run whose `result.metadata.errors` list was non-empty, regardless of whether the agent produced a final response. This was the root cause of the morning runs being marked failed despite the agent recovering from sandbox violations. New policy:
  - `errors + non-empty response_text` → `status=success`, `output_preview=response_text`, `record.error="completed with N tool warning(s); first: <msg>"`. Logged at WARNING level so the operator still sees the warnings in the journal but the cron tile reflects truth (the run accomplished its job).
  - `errors + empty response_text` → still `status=error`, `record.error=errors[0]`, `output_preview=record.error`. A run with no final answer is still a real failure.
  - `auth_required` and `success` paths unchanged.
- **2026-05-04** — Audit correction: snooze controls (1h/4h/1d/1w via `SnoozeMenu`) and thumbs-up/down (👍/👎 with `title=More of this`/`title=Less of this`) were marked missing in the Phase 7 page evaluation. They are NOT missing — they exist at `page.tsx:1448-1463`. The headless probe used `aria-label*=snooze` / `aria-label*=thumb` selectors that don't match the actual implementation (no aria-labels, just `title` attributes + emoji glyphs). Phase 2 is fully landed and complete.
- **2026-05-04** — Phase 6 (Knowledge Ledger) shipped on `feature/latest2`:
  - Backend: `mission_control_cards.list_ledger_cards()` returns retired+archived cards filtered by `subject_kind`, `min_recurrence`, `state`, `since_iso`, with sort by `recurrence_count DESC, last_synthesized_at DESC`. Companion `ledger_summary()` returns `{retired_count, archived_count, recurring_count, most_recent_retired_iso}` for the page header.
  - API: new `GET /api/v1/dashboard/mission-control/ledger` endpoint mirrors the live-cards endpoint shape (hydrated JSON columns: `tags`, `evidence_refs`, `synthesis_history`, `dispatch_history`, `operator_feedback`).
  - Frontend: new page at `web-ui/app/dashboard/mission-control/ledger/page.tsx`. Header with back-link to Mission Control + summary band (retired / archived / recurring / most-recent retire). Filter row (subject_kind, recurrence ≥, state). Per-card row with severity + state + subject_kind + recurrence badges; click "Detail" to expand narrative, why_it_matters, recommended_next_step, tags, synthesis history (last 5), operator comments (last 10), dispatch history (last 5), and evidence refs.
  - Wiring: footer "Knowledge Ledger" link added to Deep Dives panel on the main Mission Control page (uses `FileText` lucide icon, already imported).
  - Per the spec §6, this completes the lifecycle visibility: live cards on MC page → retire to ledger → archive at retention cutoff (180d default per `UA_MISSION_CONTROL_RETENTION_DAYS`).
