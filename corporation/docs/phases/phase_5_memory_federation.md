# Phase 5: Organizational Memory & Database Federation

**Status:** Not Started
**Priority:** Low — future phase after fleet is operational
**Depends on:** Phase 3 (fleet operational), Phase 4 (observability)

---

## Sub-Phases

### Phase 5a: Memo Promotion Pipeline

**Objective:** When a local factory completes a mission, it generates an "Executive Summary Memo" of what it learned. High-value lessons are promoted to HQ's global knowledge base, preventing log pollution while preserving corporate learning.

#### Design

```
Local Factory completes mission
  → Local Simone/agent writes structured memo
  → Memo tagged with promotion_candidate=true/false
  → If candidate: POST memo to HQ promotion endpoint
  → HQ evaluates and ingests into global knowledge base
```

#### Memo Schema

```python
@dataclass
class ExecutiveMemo:
    memo_id: str                    # uuid
    factory_id: str
    mission_job_id: str             # link to delegation history
    mission_kind: str
    summary: str                    # 1-3 sentence summary
    key_findings: list[str]         # bullet points
    lessons_learned: list[str]      # universally applicable insights
    artifacts: list[str]            # paths or URLs to produced artifacts
    promotion_candidate: bool       # whether this memo should be promoted to HQ
    promotion_tags: list[str]       # categories: "dependency_management", "api_pattern", etc.
    created_at: str                 # ISO8601
```

#### Files to Create

**`src/universal_agent/delegation/memo.py`**
- `ExecutiveMemo` Pydantic model
- `generate_memo_from_mission_result()` — takes mission result + context, produces memo
- `should_promote()` — heuristic: promote if `lessons_learned` is non-empty and mission was SUCCESS

**`src/universal_agent/delegation/handlers/memo_promotion.py`**
- Integration into consumer: after any handler completes, optionally generate and submit memo

#### HQ Endpoints

```python
@app.post("/api/v1/ops/memos/promote")
async def promote_memo(request: Request, memo: ExecutiveMemo):
    """Receive a promoted memo from a factory and ingest into global knowledge base."""
    _require_ops_auth(request)
    # Store in memos table
    # Index into knowledge base (future: vector store)
    ...

@app.get("/api/v1/ops/memos")
async def list_memos(request: Request, limit: int = 50, factory_id: str | None = None):
    """List promoted memos."""
    _require_ops_auth(request)
    ...
```

#### Storage

```sql
CREATE TABLE IF NOT EXISTS promoted_memos (
    memo_id TEXT PRIMARY KEY,
    factory_id TEXT,
    mission_job_id TEXT,
    mission_kind TEXT,
    summary TEXT,
    key_findings TEXT,      -- JSON array
    lessons_learned TEXT,   -- JSON array
    artifacts TEXT,         -- JSON array
    promotion_tags TEXT,    -- JSON array
    created_at TEXT,
    ingested_at TEXT,
    applied_globally BOOLEAN DEFAULT FALSE
);
```

#### Acceptance Criteria
- [ ] `ExecutiveMemo` schema defined
- [ ] Consumer generates memos after mission completion
- [ ] Promotion-worthy memos are POSTed to HQ
- [ ] HQ stores and indexes promoted memos
- [ ] Memos visible in a new Corporation View section
- [ ] Unit tests for memo generation and promotion heuristic

---

### Phase 5b: Database Federation

**Objective:** Formalize the boundary between Global State (HQ-owned, authoritative) and Local State (factory-owned, ephemeral). Ensure factories query HQ for global state and never assume their local copy is authoritative.

#### State Classification

| State Type | Owner | Storage | Examples |
|---|---|---|---|
| **Global** | HQ | PostgreSQL (HQ) | User preferences, master to-do list, promoted memos, delegation history, factory registrations |
| **Local** | Each Factory | SQLite | `vp_state.db`, session scratchpads, execution logs, local agent memory |
| **Shared-Read** | HQ publishes, factories read | HQ API | Capabilities registry, mission templates, global config |

#### Design Principles

1. **Factories never write to global state directly.** They submit via API (memo promotion, registration, mission results).
2. **Factories query HQ API for shared-read state on boot** and cache locally with TTL.
3. **Local state is disposable.** If a factory is wiped, no global state is lost.
4. **Conflict resolution:** HQ is always authoritative. If local and global state conflict, global wins.

#### Files to Create

**`src/universal_agent/federation/state_boundary.py`**
```python
class StateBoundary:
    """Enforces global vs local state access patterns."""
    
    def __init__(self, factory_role: str, hq_base_url: str | None = None) -> None: ...
    
    def is_global_state(self, table_name: str) -> bool:
        """Returns True if the table belongs to global state (HQ-only write)."""
        ...
    
    def require_hq_for_write(self, table_name: str) -> None:
        """Raises if a LOCAL_WORKER tries to write to global state directly."""
        ...
    
    async def fetch_shared_read(self, key: str) -> Any:
        """Fetch shared-read state from HQ API with local cache."""
        ...
```

**`src/universal_agent/federation/config_sync.py`**
```python
class ConfigSync:
    """Syncs shared configuration from HQ to local factory on boot."""
    
    async def sync_on_boot(self) -> None:
        """Pull latest shared config from HQ API."""
        ...
    
    async def refresh(self) -> None:
        """Periodic refresh of cached shared config."""
        ...
```

#### Acceptance Criteria
- [ ] State boundary classification documented and enforced
- [ ] Factories fetch shared-read state from HQ on boot
- [ ] LOCAL_WORKER cannot directly write to global state tables
- [ ] Config sync runs on factory startup
- [ ] Unit tests for state boundary enforcement

---

## Long-Term Considerations

These are noted for future phases beyond Phase 5:

- **Vector store for memos:** Index promoted memos into a vector store for semantic retrieval by agents across the fleet.
- **Cross-factory collaboration:** Factories that discover they need capabilities they lack could request delegation to a peer factory (peer-to-peer via HQ routing).
- **Factory auto-scaling:** Spin up cloud factories on-demand for burst workloads (e.g., a Fly.io or Railway factory template).
- **Audit trail:** Complete audit log of all cross-factory interactions for security review.
