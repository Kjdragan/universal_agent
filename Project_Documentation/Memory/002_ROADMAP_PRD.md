# Memory System Enhancement PRD
## Bridging the Gap with Letta Memory Features

**Document Version**: 1.0
**Last Updated**: 2025-12-29
**Status**: Draft for Review

---

## Executive Summary

This PRD outlines a comprehensive enhancement plan for the Universal Agent Memory System to achieve feature parity with Letta's memory architecture. Our current system implements the foundational concepts from Letta (inspired by MemGPT), but lacks several critical capabilities that make Letta's memory system production-grade for sophisticated AI agents.

### Key Objective
Transform our memory system from a basic dual-layer storage system into a comprehensive, agent-managed memory architecture that enables sophisticated multi-agent workflows, intelligent context management, and production-grade memory capabilities.

### Target State
By implementing this PRD, our memory system will support:
- Agent-driven memory management with multiple editing tools
- Shared memory patterns for multi-agent coordination
- Intelligent conversation compaction and context engineering
- Advanced archival search with hybrid semantic/keyword search
- Memory block limits and character counting
- Read-only and editable block configurations
- Export/import capabilities for memory portability

---

## Current State Analysis

### What We Have (Implemented)

| Feature | Status | Notes |
|---------|--------|-------|
| **Core Memory (SQLite)** | ✅ Implemented | Basic storage with label, value, description |
| **Archival Memory (ChromaDB)** | ✅ Implemented | Semantic search with tags |
| **Memory Block Structure** | ✅ Implemented | label, value, is_editable, description, last_updated |
| **Basic Tools** | ✅ Implemented | `core_memory_replace`, `core_memory_append`, `archival_memory_insert`, `archival_memory_search` |
| **System Prompt Injection** | ✅ Implemented | `get_system_prompt_addition()` formats blocks for context |
| **Persistence** | ✅ Implemented | All operations persisted immediately |
| **Default Blocks** | ✅ Implemented | persona, human, system_rules initialized if empty |

### What Letta Has (Missing Features)

| Feature Category | Missing Features | Impact |
|------------------|------------------|--------|
| **Memory Editing Tools** | `memory_insert`, `memory_rethink` | Limited editing flexibility |
| **Memory Block Limits** | Character limits, `chars_current`, `chars_limit` tracking | No size management |
| **Block Metadata** | XML-like formatting in context, usage statistics | Poor visibility into block state |
| **Shared Memory** | Block sharing across agents via `block_ids` | No multi-agent coordination |
| **Compaction System** | Conversation summarization, sliding window | No context window management |
| **Advanced Search** | Hybrid vector + keyword search, pagination, RRF scoring | Basic semantic search only |
| **Time-Based Filtering** | `start_datetime`, `end_datetime` in search | No temporal filtering |
| **Memory Export/Import** | Programmatic backup and restore | No portability |
| **Block Management** | Attach/detach blocks, list blocks by label | Limited CRUD operations |
| **Agent-Scoped Operations** | Retrieve blocks by agent ID, list agent blocks | No agent-centric view |
| **Read-Only Enforcement** | `read_only` field blocks agent edits | Manual enforcement only |

---

## Detailed Gap Analysis

### 1. Memory Editing Tools

**Letta Approach:**
- `memory_replace(label, old_text, new_value)` - Search and replace for precise edits
- `memory_insert(label, text)` - Insert a line into a block (safer than replace)
- `memory_rethink(label, new_value)` - Rewrite entire block with reasoning

**Our Current State:**
- `core_memory_replace(label, new_value)` - Full overwrite only
- `core_memory_append(label, text_to_append)` - Append to end

**Gap:** We lack granular editing tools. Agents can only overwrite or append, making precise edits difficult and error-prone.

**Priority:** HIGH - Critical for agent-driven memory management

---

### 2. Memory Block Limits and Character Tracking

**Letta Approach:**
```xml
<metadata>
- chars_current=128
- chars_limit=5000
</metadata>
```

**Our Current State:**
- No character limit enforcement
- No character counting/tracking
- No metadata in system prompt injection

**Gap:** Agents have no visibility into block sizes, leading to potential context window overflow. No enforcement of size limits.

**Priority:** HIGH - Essential for context window management

---

### 3. Shared Memory Patterns

**Letta Approach:**
```python
# Create shared block
shared_block = client.blocks.create(label="organization", ...)

# Attach to multiple agents
agent1 = client.agents.create(block_ids=[shared_block.id])
agent2 = client.agents.create(block_ids=[shared_block.id])

# Both agents see same block data in context
```

**Our Current State:**
- Single global memory manager
- No concept of block IDs or agent-specific blocks
- All agents share the same memory blocks

**Gap:** Cannot implement sophisticated multi-agent patterns (supervisor/worker, read-only policies, etc.)

**Priority:** HIGH - Required for multi-agent coordination

---

### 4. Conversation Compaction

**Letta Approach:**
- Automatic summarization of conversation history when context window fills
- Configurable compaction settings (sliding window, all mode)
- Custom summarization prompts
- Separate summarizer model for cost optimization

**Our Current State:**
- No conversation history tracking
- No summarization
- No context window management

**Gap:** Long conversations will exceed context limits with no recovery mechanism. Cannot maintain continuity in long-running sessions.

**Priority:** MEDIUM - Important for long-running agents, but can be phased

---

### 5. Advanced Archival Search

**Letta Approach:**
- Hybrid search: Vector (semantic) + Keyword (full-text search)
- Reciprocal Rank Fusion (RRF) for combined scoring
- Pagination support (page parameter)
- Relevance scores: `rrf_score`, `vector_rank`, `fts_rank`
- Time-based filtering: `start_datetime`, `end_datetime`

**Our Current State:**
- Basic ChromaDB semantic search only
- No pagination
- No relevance scores
- No time-based filtering
- No keyword/full-text search

**Gap:** Search quality and scalability are limited. Cannot filter by time or paginate large result sets.

**Priority:** MEDIUM - Enhances search quality but basic search works

---

### 6. Block Management APIs

**Letta Approach:**
- `blocks.create()` - Create standalone blocks
- `blocks.retrieve(block_id)` - Get block by ID
- `blocks.list(label=, label_search=)` - List/search blocks
- `blocks.update(block_id, ...)` - Update properties
- `blocks.delete(block_id)` - Delete block
- `blocks.agents.list(block_id)` - See which agents use block
- `agents.blocks.attach(agent_id, block_id)` - Attach block to agent
- `agents.blocks.detach(agent_id, block_id)` - Detach block
- `agents.blocks.retrieve(agent_id, block_label)` - Get agent's block by label

**Our Current State:**
- `save_block()` - Save or update (no separation)
- `get_core_memory()` - Get all blocks
- `get_block(label)` - Get by label only
- No delete operation
- No attach/detach semantics
- No agent-scoped operations

**Gap:** Limited block management. Cannot build complex agent architectures or manage block lifecycle properly.

**Priority:** MEDIUM - Nice-to-have for advanced use cases

---

### 7. Export/Import Capabilities

**Letta Approach:**
- Export archival memories to JSON
- Import archival memories from JSON
- Export core memory blocks
- Migrate memories between agents

**Our Current State:**
- No export functionality
- No import functionality
- Direct database access only

**Gap:** No memory portability. Cannot backup, migrate, or transfer memories between environments.

**Priority:** LOW - Can use database backup, but programmatic export would be convenient

---

## Implementation Roadmap

### Phase 1: Core Memory Enhancements (Weeks 1-2)

**Goal:** Add critical memory management features

#### 1.1 Character Limits and Tracking
- Add `limit` field to `MemoryBlock` model
- Track `chars_current` in block metadata
- Enforce limits in `core_memory_replace` and `core_memory_append`
- Update system prompt injection to include metadata

**Acceptance Criteria:**
- [ ] Blocks have `limit` field (default: 5000)
- [ ] Character count displayed in system prompt
- [ ] Rejections occur when limit exceeded
- [ ] Tests for limit enforcement

**Integration Notes:**
- No breaking changes to existing code
- Default limit ensures backward compatibility
- Metadata injection into system prompt

#### 1.2 Enhanced Memory Editing Tools

Add new tools matching Letta's approach:

**`memory_insert(label, text, insert_after=None)`**
- Insert text at specific location or at end
- Search for `insert_after` pattern and insert after match
- Safer than replace for adding information

**`memory_rethink(label, new_value, reasoning=None)`**
- Rewrite entire block with new content
- Optional `reasoning` parameter explains the change
- Logs reasoning for audit trail

**Acceptance Criteria:**
- [ ] Three editing tools: replace, insert, rethink
- [ ] `memory_insert` supports pattern matching
- [ ] `memory_rethink` logs reasoning
- [ ] Tools exposed to agent via tool definitions
- [ ] Tests for all editing operations

**Integration Notes:**
- Existing `core_memory_append` remains for backward compatibility
- New tools provide finer-grained control
- No changes to storage schema

---

### Phase 2: Shared Memory Architecture (Weeks 3-4)

**Goal:** Enable multi-agent memory coordination

#### 2.1 Block Identity System

**Schema Changes:**
```sql
ALTER TABLE core_blocks ADD COLUMN block_id TEXT UNIQUE;
ALTER TABLE core_blocks ADD COLUMN is_shared BOOLEAN DEFAULT FALSE;
CREATE TABLE agent_block_attachments (
    agent_id TEXT,
    block_id TEXT,
    attached_at TIMESTAMP,
    PRIMARY KEY (agent_id, block_id)
);
```

**API Changes:**
- `create_block(label, value, description=None, limit=5000, is_shared=False)` → Returns `block_id`
- `attach_block(agent_id, block_id)` - Link block to agent
- `detach_block(agent_id, block_id)` - Unlink block from agent
- `list_agent_blocks(agent_id)` - Get agent's blocks
- `list_block_agents(block_id)` - Get agents using block

**Acceptance Criteria:**
- [ ] Block has UUID `block_id` field
- [ ] Blocks can be marked as shared
- [ ] Attach/detach operations work
- [ ] Multiple agents can share same block
- [ ] Tests for shared memory scenarios

**Integration Notes:**
- Breaking change: Block creation now returns ID
- Update `MemoryManager` to support agent-scoped operations
- Maintain backward compatibility with label-based access
- Add migration script for existing blocks

#### 2.2 Agent-Scoped Memory Operations

New methods in `MemoryManager`:
- `get_agent_blocks(agent_id: str)` - List all agent's blocks
- `get_agent_block(agent_id: str, block_label: str)` - Get specific block
- `update_agent_block(agent_id: str, block_label: str, **kwargs)` - Update agent's block
- `create_agent_block(agent_id: str, **kwargs)` - Create agent-specific block

**Acceptance Criteria:**
- [ ] Agent ID parameter in memory operations
- [ ] Can list all blocks for an agent
- [ ] Can retrieve agent's block by label
- [ ] Shared blocks visible to all attached agents
- [ ] Private blocks visible only to owner

**Integration Notes:**
- Requires agent identification system
- Update system prompt injection to include agent's blocks only
- Shared blocks appear once in context (deduplication)

---

### Phase 3: Advanced Archival Search (Weeks 5-6)

**Goal:** Enhance search quality and capabilities

#### 3.1 Hybrid Search Implementation

**Approach:** Combine vector and keyword search
- Use ChromaDB for vector search (already have)
- Add SQLite FTS5 for keyword search
- Implement Reciprocal Rank Fusion (RRF) for combined scoring

**Schema Changes:**
```sql
CREATE VIRTUAL TABLE archival_fts USING fts5(
    content, tags, timestamp,
    content='archival_items',
    content_rowid='rowid'
);
```

**API Changes:**
- `archival_memory_search(query, tags=None, start_datetime=None, end_datetime=None, page=0, limit=5)`
- Returns results with `rrf_score`, `vector_rank`, `fts_rank`

**Acceptance Criteria:**
- [ ] FTS5 table created and synced
- [ ] Hybrid search combines vector + keyword
- [ ] RRF scoring implemented
- [ ] Time-based filtering works
- [ ] Pagination works correctly
- [ ] Tests for search quality

**Integration Notes:**
- Requires keeping FTS5 index in sync with ChromaDB
- Trigger or callback to update FTS5 on insert
- Performance impact: dual searches + fusion
- Migration script to index existing archival data

#### 3.2 Enhanced Search Metadata

**New Return Format:**
```python
{
    "content": "...",
    "tags": [...],
    "timestamp": "...",
    "relevance": {
        "rrf_score": 0.95,
        "vector_rank": 2,
        "fts_rank": 1
    }
}
```

**Acceptance Criteria:**
- [ ] Search results include relevance scores
- [ ] Scores help agents assess quality
- [ ] Pagination allows browsing large result sets

---

### Phase 4: Conversation Compaction (Weeks 7-8)

**Goal:** Add context window management

#### 4.1 Conversation History Tracking

**Schema Changes:**
```sql
CREATE TABLE conversation_messages (
    message_id TEXT PRIMARY KEY,
    agent_id TEXT,
    role TEXT,  -- 'user' or 'assistant'
    content TEXT,
    timestamp TIMESTAMP,
    is_compacted BOOLEAN DEFAULT FALSE
);

CREATE TABLE conversation_summaries (
    summary_id TEXT PRIMARY KEY,
    agent_id TEXT,
    message_range_start TEXT,
    message_range_end TEXT,
    summary TEXT,
    created_at TIMESTAMP
);
```

**Acceptance Criteria:**
- [ ] Messages stored in database
- [ ] Message tracking per agent
- [ ] Summaries linked to message ranges
- [ ] Tests for message persistence

#### 4.2 Compaction Engine

**Configuration:**
```python
@dataclass
class CompactionSettings:
    model: str  # Summarizer model
    mode: str  # 'sliding_window' or 'all'
    sliding_window_percentage: float = 0.3
    clip_chars: int = 2000
    prompt: Optional[str] = None
    prompt_acknowledgement: bool = False
```

**Implementation:**
- Monitor context window usage (count tokens in messages + blocks)
- Trigger compaction when approaching limit
- Summarize messages based on mode (sliding window or all)
- Replace summarized messages with summary

**Acceptance Criteria:**
- [ ] Context window monitoring
- [ ] Automatic summarization when needed
- [ ] Sliding window mode works
- [ ] All mode works
- [ ] Summaries preserve critical information
- [ ] Tests for compaction logic

**Integration Notes:**
- Requires token counting library (tiktoken)
- Separate summarizer model API call
- Integration with Claude Agent SDK message handling
- Must preserve order and causality in conversation

---

### Phase 5: Export/Import (Week 9)

**Goal:** Enable memory portability

#### 5.1 Export Functionality

**API:**
```python
def export_core_memory(agent_id: str) -> dict:
    """Export agent's core memory blocks to JSON."""

def export_archival_memory(
    agent_id: str,
    start_datetime: Optional[str] = None,
    end_datetime: Optional[str] = None,
    tags: Optional[List[str]] = None
) -> dict:
    """Export archival memories with optional filters."""
```

**Acceptance Criteria:**
- [ ] Export core memory to JSON
- [ ] Export archival memory with filters
- [ ] Export includes metadata (timestamps, tags)
- [ ] Validate JSON schema

#### 5.2 Import Functionality

**API:**
```python
def import_core_memory(agent_id: str, data: dict, merge: bool = False) -> int:
    """Import core memory from JSON."""

def import_archival_memory(agent_id: str, data: dict) -> int:
    """Import archival memories from JSON."""
```

**Acceptance Criteria:**
- [ ] Import core memory from JSON
- [ ] Import archival memory
- [ ] Merge vs replace mode
- [ ] Validation of input data
- [ ] Returns count of imported items
- [ ] Tests for round-trip export/import

**Integration Notes:**
- No schema changes
- Pure application-level functionality
- Useful for backups and migrations

---

### Phase 6: Block Management APIs (Week 10)

**Goal:** Complete CRUD operations for blocks

#### 6.1 Block Lifecycle Management

**API:**
```python
def create_block(**kwargs) -> str:
    """Create a standalone block, returns block_id."""

def retrieve_block(block_id: str) -> MemoryBlock:
    """Get block by ID."""

def list_blocks(label: Optional[str] = None, label_search: Optional[str] = None) -> List[MemoryBlock]:
    """List blocks with optional filters."""

def update_block(block_id: str, **kwargs) -> None:
    """Update block properties."""

def delete_block(block_id: str) -> None:
    """Delete block (detaches from all agents)."""

def list_block_agents(block_id: str) -> List[str]:
    """List agent IDs that use this block."""
```

**Acceptance Criteria:**
- [ ] All CRUD operations work
- [ ] Block ID-based retrieval
- [ ] Search by label
- [ ] Delete removes from all agents
- [ ] Tests for lifecycle management

**Integration Notes:**
- Existing `save_block` becomes internal method
- Public API uses ID-based operations
- Label-based access still works for backward compatibility

---

## Technical Architecture Updates

### Updated Data Models

```python
@dataclass
class MemoryBlock:
    # Existing fields
    label: str
    value: str
    is_editable: bool = True
    description: Optional[str] = None
    last_updated: datetime = Field(default_factory=datetime.now)

    # New fields
    block_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    limit: int = 5000
    is_shared: bool = False
    chars_current: int = Field(init=False)

    def __post_init__(self):
        self.chars_current = len(self.value)

@dataclass
class ArchivalItem:
    # Existing fields
    content: str
    tags: List[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.now)
    item_id: Optional[str] = None

    # New fields
    relevance_scores: Optional[Dict[str, float]] = None

@dataclass
class ConversationMessage:
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str
    role: str  # 'user' or 'assistant'
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)
    is_compacted: bool = False

@dataclass
class CompactionSettings:
    model: str
    mode: str = 'sliding_window'
    sliding_window_percentage: float = 0.3
    clip_chars: int = 2000
    prompt: Optional[str] = None
    prompt_acknowledgement: bool = False
```

### Updated System Prompt Format

```python
def get_system_prompt_addition(self, agent_id: Optional[str] = None) -> str:
    """
    Format the Core Memory blocks for injection into the System Prompt.
    Now includes character counts and limits.
    """
    blocks = self.get_agent_blocks(agent_id) if agent_id else self.agent_state.core_memory

    prompt_lines = ["\n<memory_blocks>"]

    for block in blocks:
        prompt_lines.append("")
        prompt_lines.append(f"<{block.label}>")
        prompt_lines.append(f"<description>{block.description}</description>")
        prompt_lines.append("<metadata>")
        prompt_lines.append(f"- chars_current={block.chars_current}")
        prompt_lines.append(f"- chars_limit={block.limit}")
        prompt_lines.append("</metadata>")
        prompt_lines.append(f"<value>{block.value}</value>")
        prompt_lines.append(f"</{block.label}>")

    prompt_lines.append("")
    prompt_lines.append("</memory_blocks>")

    prompt_lines.append("\nNote: You can update these memory blocks using memory editing tools.")
    prompt_lines.append("Use archival_memory_insert to save facts/docs that don't fit here.")

    return "\n".join(prompt_lines)
```

---

## Integration with Universal Agent

### Changes to main.py

#### 1. Agent Identification

**Current:** No agent ID system
**Proposed:** Assign unique agent ID at startup

```python
# In agent initialization
AGENT_ID = os.getenv("AGENT_ID", str(uuid.uuid4()))
```

#### 2. Memory Manager Initialization

**Current:**
```python
mem_mgr = MemoryManager(storage_dir=storage_path)
```

**Proposed:**
```python
mem_mgr = MemoryManager(
    storage_dir=storage_path,
    agent_id=AGENT_ID,
    compaction_settings=CompactionSettings(
        model="claude-3-5-haiku-20241022",
        mode="sliding_window",
        sliding_window_percentage=0.3
    )
)
```

#### 3. Message Tracking

**Current:** No message persistence
**Proposed:** Track all messages for compaction

```python
# Wrap Claude Agent SDK calls
def process_with_memory_tracking(user_message: str):
    # Store user message
    mem_mgr.store_message(
        agent_id=AGENT_ID,
        role="user",
        content=user_message
    )

    # Process with agent
    response = agent.process_turn(user_message)

    # Store assistant message
    mem_mgr.store_message(
        agent_id=AGENT_ID,
        role="assistant",
        content=response.response_text
    )

    # Check compaction
    if mem_mgr.should_compact(AGENT_ID):
        mem_mgr.compact_history(AGENT_ID)

    return response
```

### Agent College Integration

**Current:** Trace tracking for replay prevention
**Proposed:** Enhanced with shared memory

```python
# Shared memory block for agent college
college_block = mem_mgr.create_block(
    label="agent_college_state",
    description="Shared state across all Agent College agents",
    value="",
    is_shared=True
)

# Attach to all college agents
for agent in [critic, professor, scribe]:
    mem_mgr.attach_block(agent.agent_id, college_block.block_id)
```

---

## Testing Strategy

### Unit Tests

**Phase 1: Core Enhancements**
- Test character limit enforcement
- Test new editing tools (insert, rethink)
- Test character counting

**Phase 2: Shared Memory**
- Test block creation with IDs
- Test attach/detach operations
- Test multi-agent scenarios
- Test shared vs private blocks

**Phase 3: Advanced Search**
- Test hybrid search scoring
- Test time-based filtering
- Test pagination
- Test FTS5 sync

**Phase 4: Compaction**
- Test message tracking
- Test sliding window compaction
- Test all-mode compaction
- Test summary quality

**Phase 5: Export/Import**
- Test export to JSON
- Test import from JSON
- Test round-trip preservation
- Test merge vs replace

**Phase 6: Block Management**
- Test CRUD operations
- Test search by label
- Test delete cascades
- Test agent listing

### Integration Tests

**Multi-Agent Coordination**
```python
def test_supervisor_worker_pattern():
    # Create shared block
    shared = mem_mgr.create_block(label="shared_state", is_shared=True)

    # Create supervisor and workers
    supervisor = create_agent("supervisor")
    worker1 = create_agent("worker1")
    worker2 = create_agent("worker2")

    # Attach shared block
    for agent in [supervisor, worker1, worker2]:
        mem_mgr.attach_block(agent.id, shared.block_id)

    # Worker updates shared state
    mem_mgr.update_agent_block(
        agent_id=worker1.id,
        block_label="shared_state",
        value="Task completed: Module A"
    )

    # Supervisor sees update
    supervisor_block = mem_mgr.get_agent_block(supervisor.id, "shared_state")
    assert "Task completed: Module A" in supervisor_block.value
```

**Compaction Integration**
```python
def test_long_conversation_compaction():
    agent = create_agent("test_agent")

    # Generate long conversation (exceeds context)
    for i in range(100):
        agent.process_turn(f"Message {i}")

    # Verify compaction occurred
    history = mem_mgr.get_conversation_history(agent.id)
    summaries = [m for m in history if m.is_compacted]

    assert len(summaries) > 0
    assert len(history) < 100  # Compacted
```

---

## Performance Considerations

### Expected Performance Impact

| Operation | Current | After Phase 3 | After Phase 4 |
|-----------|---------|----------------|---------------|
| Core memory read | <1ms | <2ms (metadata) | <2ms |
| Core memory write | <5ms | <6ms (validation) | <6ms |
| Archival insert | ~100-500ms | ~150-550ms (FTS sync) | ~150-550ms |
| Archival search | ~200-1000ms | ~300-1200ms (hybrid) | ~300-1200ms |
| Context injection | <1ms | <2ms | <10ms (compaction check) |

### Optimization Strategies

1. **Async FTS Updates**: Queue FTS index updates instead of synchronous
2. **Caching**: Cache character counts, block lookups
3. **Batch Compaction**: Trigger compaction only when needed, not every message
4. **Connection Pooling**: Reuse SQLite connections
5. **Lazy Loading**: Load blocks only when needed

---

## Migration Path

### Database Migration Script

```python
def migrate_to_v2():
    """Migrate existing memory system to enhanced version."""

    # 1. Add new columns to core_blocks
    conn.execute("ALTER TABLE core_blocks ADD COLUMN block_id TEXT UNIQUE")
    conn.execute("ALTER TABLE core_blocks ADD COLUMN is_shared BOOLEAN DEFAULT FALSE")
    conn.execute("ALTER TABLE core_blocks ADD COLUMN limit INTEGER DEFAULT 5000")

    # 2. Generate block IDs for existing blocks
    blocks = conn.execute("SELECT label FROM core_blocks").fetchall()
    for (label,) in blocks:
        block_id = str(uuid.uuid4())
        conn.execute(
            "UPDATE core_blocks SET block_id = ? WHERE label = ?",
            (block_id, label)
        )

    # 3. Create agent_block_attachments table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_block_attachments (
            agent_id TEXT,
            block_id TEXT,
            attached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (agent_id, block_id)
        )
    """)

    # 4. Create FTS5 table
    conn.execute("""
        CREATE VIRTUAL TABLE archival_fts USING fts5(
            content, tags, timestamp
        )
    """)

    # 5. Create conversation tables
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversation_messages (
            message_id TEXT PRIMARY KEY,
            agent_id TEXT,
            role TEXT,
            content TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_compacted BOOLEAN DEFAULT FALSE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversation_summaries (
            summary_id TEXT PRIMARY KEY,
            agent_id TEXT,
            message_range_start TEXT,
            message_range_end TEXT,
            summary TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
```

---

## Risk Assessment

### High Risks

1. **Breaking Changes to Existing Code**
   - **Risk**: Block creation now returns ID instead of void
   - **Mitigation**: Provide backward-compatible wrapper functions
   - **Migration Path**: Gradual rollout with feature flags

2. **Performance Degradation from Hybrid Search**
   - **Risk**: Dual searches + fusion = slower queries
   - **Mitigation**: Async searches, caching, result pagination
   - **Fallback**: Allow disabling keyword search if needed

3. **Compaction Quality Issues**
   - **Risk**: Summaries lose important information
   - **Mitigation**: Extensive testing, configurable prompts, manual override
   - **Fallback**: Disable automatic compaction, manual trigger only

### Medium Risks

1. **Shared Memory Race Conditions**
   - **Risk**: Concurrent writes to shared blocks
   - **Mitigation**: SQLite transactions, last-write-wins semantics
   - **Fallback**: Read-only locks for critical blocks

2. **Database Schema Complexity**
   - **Risk**: More tables = more migration issues
   - **Mitigation**: Comprehensive migration scripts, automated testing
   - **Fallback**: Rollback mechanism for failed migrations

### Low Risks

1. **Export/Import Data Loss**
   - **Risk**: Round-trip export/import loses data
   - **Mitigation**: Schema validation, comprehensive tests
   - **Fallback**: Direct database backup always available

---

## Success Metrics

### Technical Metrics

- [ ] All Phase 1-6 features implemented and tested
- [ ] 95%+ test coverage for new code
- [ ] No breaking changes to existing API (backward compatible)
- [ ] Performance: Core memory ops <10ms, Archival search <1.5s
- [ ] Migration script completes without data loss

### Functional Metrics

- [ ] Agents can manage memory with 3 editing tools (replace, insert, rethink)
- [ ] Character limits enforced and tracked
- [ ] Shared memory enables multi-agent coordination
- [ ] Compaction handles conversations 10x context window
- [ ] Hybrid search improves relevance by 20%+ (measured by user feedback)

### Adoption Metrics

- [ ] Documentation updated with new features
- [ ] Migration guide provided for existing users
- [ ] Examples demonstrate multi-agent patterns
- [ ] Agent College uses shared memory for coordination

---

## Timeline Summary

| Phase | Duration | Dependencies | Deliverable |
|-------|----------|--------------|-------------|
| **Phase 1: Core Enhancements** | 2 weeks | None | Character limits, editing tools |
| **Phase 2: Shared Memory** | 2 weeks | Phase 1 | Block IDs, attach/detach, agent-scoped ops |
| **Phase 3: Advanced Search** | 2 weeks | None | Hybrid search, FTS5, pagination |
| **Phase 4: Compaction** | 2 weeks | Phase 1 | Conversation tracking, summarization |
| **Phase 5: Export/Import** | 1 week | None | Memory portability |
| **Phase 6: Block Management** | 1 week | Phase 2 | Complete CRUD APIs |
| **Testing & Docs** | 2 weeks | All phases | Test suite, migration guide |
| **Total** | **12 weeks** | - | Production-ready system |

---

## Open Questions

1. **Agent ID System**: How do we generate and manage agent IDs? Should they be persistent across restarts?
2. **Compaction Trigger**: What percentage of context window usage should trigger compaction? (Suggest: 80%)
3. **Embedding Model Migration**: Can we migrate archival embeddings if we change models? (Requires re-embedding all data)
4. **Multi-Agent Concurrency**: How do we handle concurrent writes to shared blocks? (Last-write-wins acceptable?)
5. **Backward Compatibility**: How long do we maintain the old API? (Suggest: 2 major versions)

---

## Conclusion

This PRD provides a comprehensive roadmap to transform our memory system from a basic dual-layer storage system into a sophisticated, Letta-compatible memory architecture. The phased approach allows incremental implementation while maintaining backward compatibility and minimizing risk.

**Key Benefits:**
- Agents can manage their own memory with granular tools
- Multi-agent coordination via shared memory
- Intelligent context window management
- Enhanced search quality
- Memory portability and lifecycle management

**Next Steps:**
1. Review and approve this PRD
2. Prioritize phases (consider starting with Phase 1 or 2)
3. Assign development resources
4. Set up feature flags for gradual rollout
5. Begin Phase 1 implementation

---

**Appendix: Letta Feature Parity Matrix**

| Feature | Letta | Current | Phase | Priority |
|---------|-------|---------|-------|----------|
| memory_replace | ✅ | ✅ (partial) | 1 | HIGH |
| memory_insert | ✅ | ❌ | 1 | HIGH |
| memory_rethink | ✅ | ❌ | 1 | HIGH |
| Character limits | ✅ | ❌ | 1 | HIGH |
| Shared blocks | ✅ | ❌ | 2 | HIGH |
| Block IDs | ✅ | ❌ | 2 | HIGH |
| Agent-scoped ops | ✅ | ❌ | 2 | HIGH |
| Hybrid search | ✅ | ❌ | 3 | MEDIUM |
| Pagination | ✅ | ❌ | 3 | MEDIUM |
| Time filtering | ✅ | ❌ | 3 | MEDIUM |
| Compaction | ✅ | ❌ | 4 | MEDIUM |
| Export/import | ✅ | ❌ | 5 | LOW |
| Block CRUD | ✅ | ❌ | 6 | MEDIUM |