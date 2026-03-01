# Phase 4: Corporation Observability & Integration

**Status:** Not Started
**Priority:** Medium — enhances operational visibility and wires CSI upstream
**Depends on:** Phase 3 (all sub-phases complete, fleet operational)

---

## Sub-Phases

### Phase 4a: Enhanced Corporation View

**Objective:** Extend the Corporation View dashboard beyond fleet status to show active workload, delegation history, and mission detail drill-down.

#### Files to Modify

**`web-ui/app/dashboard/corporation/page.tsx`**
Add three new sections below the existing fleet table:

1. **Active Workload Panel** — per-factory cards showing:
   - Current mission (if any): `job_id`, `mission_kind`, elapsed time
   - Queue depth: pending missions in the consumer's backlog
   - Last completed mission: `job_id`, `mission_kind`, duration, status

2. **Delegation History Table** — paginated table of recent missions:
   - Columns: `job_id`, `mission_kind`, `target_factory`, `status`, `published_at`, `completed_at`, `duration`
   - Filter by: status (SUCCESS/FAILED/PENDING), factory, mission_kind
   - Default: last 50 missions, newest first

3. **Mission Detail Drill-Down** — click a row to expand:
   - Full `MissionEnvelope` JSON
   - `MissionResultEnvelope` JSON (if completed)
   - Timeline: published → consumed → started → completed/failed
   - Error details (if failed)

**`src/universal_agent/gateway_server.py`**
Add new API endpoints:

```python
@app.get("/api/v1/ops/delegation/history")
async def delegation_history(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    factory_id: str | None = None,
    mission_kind: str | None = None,
):
    """Return recent delegation missions with optional filtering."""
    _require_ops_auth(request)
    # Query delegation_history table (new persistent store)
    ...

@app.get("/api/v1/ops/delegation/history/{job_id}")
async def delegation_detail(request: Request, job_id: str):
    """Return full mission detail including envelope and result."""
    _require_ops_auth(request)
    ...

@app.get("/api/v1/ops/delegation/workload")
async def delegation_workload(request: Request):
    """Return per-factory active workload summary."""
    _require_ops_auth(request)
    ...
```

**Storage:** Add `delegation_history` table to the runtime DB:
```sql
CREATE TABLE IF NOT EXISTS delegation_history (
    job_id TEXT PRIMARY KEY,
    idempotency_key TEXT,
    mission_kind TEXT,
    target_factory_id TEXT,
    status TEXT DEFAULT 'published',  -- published|consumed|running|completed|failed|dlq
    priority INTEGER DEFAULT 1,
    envelope_json TEXT,
    result_json TEXT,
    published_at TEXT,
    consumed_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    duration_seconds REAL,
    error TEXT
);
```

**Integration points:**
- `RedisMissionBus.publish_mission()` → insert row with status=published
- Consumer picks up → update status=consumed
- Handler starts → update status=running
- Handler completes → update status=completed/failed with result

#### Tests

```python
# tests/gateway/test_delegation_history_api.py
# 1. GET /api/v1/ops/delegation/history returns paginated results
# 2. Filtering by status, factory_id, mission_kind works
# 3. GET /api/v1/ops/delegation/history/{job_id} returns full detail
# 4. GET /api/v1/ops/delegation/workload returns per-factory summary
# 5. All endpoints require ops auth
```

#### Acceptance Criteria
- [ ] Delegation history API endpoints implemented and tested
- [ ] Delegation history persisted in SQLite (survives restart)
- [ ] Corporation View shows workload panel, history table, and drill-down
- [ ] `npm --prefix web-ui run build` succeeds

---

### Phase 4b: Cost Analytics

**Objective:** Aggregate ZAI/LLM API usage and cost metrics across all factories into a single dashboard view.

#### Design

Each factory tracks its own inference usage (tokens in/out, model, cost estimate). Factories report metrics to HQ via:
- **Option A:** Include in heartbeat payload (simple, low-overhead)
- **Option B:** Separate telemetry POST endpoint (richer data, higher overhead)

**Recommendation:** Start with Option A (heartbeat-embedded). Add dedicated telemetry endpoint later if needed.

#### Files to Create/Modify

**`src/universal_agent/telemetry/cost_tracker.py`** (new)
```python
@dataclass
class InferenceCostSnapshot:
    factory_id: str
    period_start: str  # ISO8601
    period_end: str
    total_input_tokens: int
    total_output_tokens: int
    total_requests: int
    estimated_cost_usd: float
    breakdown_by_model: dict[str, ModelUsage]
```

**`src/universal_agent/delegation/heartbeat.py`**
- Include `cost_snapshot` in heartbeat payload (optional field)

**`src/universal_agent/gateway_server.py`**
- Store cost snapshots from heartbeats
- Add `GET /api/v1/ops/fleet/cost-summary` endpoint

**`web-ui/app/dashboard/corporation/page.tsx`**
- Add cost summary card: total fleet spend, per-factory breakdown, trend chart

#### Acceptance Criteria
- [ ] Cost tracking module exists and accumulates per-session usage
- [ ] Heartbeat includes cost snapshot
- [ ] HQ aggregates fleet-wide cost
- [ ] Corporation View shows cost summary

---

### Phase 4c: CSI-to-HQ Bridge

**Objective:** Wire CSI's `opportunity_bundle_ready` events to flow through the delegation bus so HQ Simone can act on trend opportunities.

#### Current CSI State
- CSI emits `opportunity_bundle_ready` events with ranked opportunities
- Events are delivered to UA via `POST /api/v1/signals/ingest`
- CSI runs on the VPS alongside HQ (same machine, different process)

#### Design

CSI already pushes events to HQ's signals ingest endpoint. The bridge work is about making these events actionable in the delegation framework:

1. **Signal-to-Mission adapter:** When HQ receives an `opportunity_bundle_ready` signal, evaluate if any opportunity warrants a delegated mission (e.g., "research this trend deeper" → delegate to local factory's research VP).

2. **New signal handler in gateway:** Register a handler for `opportunity_bundle_ready` that:
   - Evaluates opportunity confidence and priority
   - If above threshold, creates a `MissionEnvelope` with `mission_kind=research_task`
   - Publishes to Redis delegation bus
   - Logs the delegation in delegation_history

#### Files to Modify

**`src/universal_agent/gateway_server.py`**
- Add signal handler for `opportunity_bundle_ready` in the signals ingest pipeline
- Create mission envelope from opportunity bundle
- Publish to delegation bus if factory with research capability is available

#### Acceptance Criteria
- [ ] `opportunity_bundle_ready` signals can trigger delegation missions
- [ ] Missions are published to Redis bus with correct `mission_kind`
- [ ] Delegation appears in Corporation View history
- [ ] End-to-end: CSI emits opportunity → HQ creates mission → factory receives
