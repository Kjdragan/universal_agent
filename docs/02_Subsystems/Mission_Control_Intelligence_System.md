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
| CSI Ingester | events ingested in last hour | no events in last 6h, service active | no events in last 24h or service inactive | A — restart service |
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
| 0 | Foundations: tables, sweeper skeleton, model designation, no-truncation refactor | None (backend only) | Low | Not started |
| 1 | Tier-0 tile strip + tile-card auto-coupling | Tile strip appears at top of MC | Low | Not started |
| 2 | Tier-1 narrative cards + feedback UI (incl. F#5 snooze auto-revival, F#6 last_viewed_at) | Cards replace Operator Brief content | Medium | Not started |
| 3 | Tier-2 synthesis with ledger feedback | Chief-of-Staff sees recurrence + retired-card history | Low | Not started |
| 4 | Action buttons (Generate Prompt + Send to Codie) | Manual action loop unlocked | Medium | Not started |
| 5 | Auto-remediation Class A (incl. F#9 templates, F#10 auto-rollback) | Three starter Class A actions live behind kill switch | High → 1-week observation period with `UA_MC_AUTO_REMEDIATION=0`, flip after gates confirmed | Not started |
| 6 | Knowledge Ledger surface | `/dashboard/mission-control/ledger` route | Low | Not started |
| 7 | Events page rebuild — smart titles + smart filter + sidebar reorder | Events page becomes scannable | Medium | Not started |
| 8 | Cleanup: delete Operator Brief panel, deprecate `/api/v1/dashboard/situations`, docs | Removal of dead surfaces | Low | Not started |

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
