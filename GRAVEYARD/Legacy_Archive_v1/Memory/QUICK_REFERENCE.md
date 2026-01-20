# Letta vs Our Memory System - Quick Reference

## Feature Comparison Matrix

| Feature | Letta | Our System | Gap Level | Phase to Implement |
|---------|-------|------------|-----------|-------------------|
| **CORE MEMORY** | | | | |
| Core memory storage | ✅ | ✅ | None | - |
| Default blocks (persona, human) | ✅ | ✅ | None | - |
| Persistent blocks | ✅ | ✅ | None | - |
| Read-only blocks | ✅ | ⚠️ Partial | Low | Phase 1 |
| Character limits | ✅ | ❌ | High | Phase 1 |
| Character counting/tracking | ✅ | ❌ | High | Phase 1 |
| Block metadata in context | ✅ | ❌ | Medium | Phase 1 |
| **MEMORY EDITING TOOLS** | | | | |
| `memory_replace` (search/replace) | ✅ | ⚠️ Partial | Medium | Phase 1 |
| `memory_insert` (insert at position) | ✅ | ❌ | High | Phase 1 |
| `memory_rethink` (rewrite with reasoning) | ✅ | ❌ | High | Phase 1 |
| `core_memory_append` | ✅ | ✅ | None | - |
| **SHARED MEMORY** | | | | |
| Block IDs (UUID) | ✅ | ❌ | High | Phase 2 |
| Shared blocks across agents | ✅ | ❌ | High | Phase 2 |
| Attach/detach blocks | ✅ | ❌ | High | Phase 2 |
| Agent-scoped memory operations | ✅ | ❌ | High | Phase 2 |
| List agents using a block | ✅ | ❌ | Medium | Phase 6 |
| **ARCHIVAL MEMORY** | | | | |
| Semantic search (vector) | ✅ | ✅ | None | - |
| Tagging system | ✅ | ✅ | None | - |
| Agent-immutable (by design) | ✅ | ✅ | None | - |
| Hybrid search (vector + keyword) | ✅ | ❌ | Medium | Phase 3 |
| Full-text search (FTS) | ✅ | ❌ | Medium | Phase 3 |
| Reciprocal Rank Fusion (RRF) | ✅ | ❌ | Medium | Phase 3 |
| Relevance scores | ✅ | ❌ | Medium | Phase 3 |
| Pagination | ✅ | ❌ | Medium | Phase 3 |
| Time-based filtering | ✅ | ❌ | Medium | Phase 3 |
| **CONVERSATION MANAGEMENT** | | | | |
| Message history tracking | ✅ | ❌ | High | Phase 4 |
| Conversation compaction | ✅ | ❌ | High | Phase 4 |
| Sliding window summarization | ✅ | ❌ | High | Phase 4 |
| Custom summarizer model | ✅ | ❌ | Medium | Phase 4 |
| Summaries in database | ✅ | ❌ | Medium | Phase 4 |
| **BLOCK MANAGEMENT** | | | | |
| Create standalone blocks | ✅ | ⚠️ Partial | Medium | Phase 6 |
| Retrieve by ID | ✅ | ❌ | Medium | Phase 6 |
| List/search blocks | ✅ | ❌ | Medium | Phase 6 |
| Update block properties | ✅ | ⚠️ Partial | Low | Phase 6 |
| Delete blocks | ✅ | ❌ | Low | Phase 6 |
| **EXPORT/IMPORT** | | | | |
| Export core to JSON | ✅ | ❌ | Low | Phase 5 |
| Export archival to JSON | ✅ | ❌ | Low | Phase 5 |
| Import from JSON | ✅ | ❌ | Low | Phase 5 |
| **SYSTEM PROMPT FORMAT** | | | | |
| XML-style formatting | ✅ | ❌ | Low | Phase 1 |
| Markdown formatting | ❌ | ✅ | - | (Our feature!) |
| Metadata injection | ✅ | ❌ | Medium | Phase 1 |

---

## Implementation Priority Matrix

### 🔴 CRITICAL (Must-Have for v1)

| Feature | Impact | Effort | ROI | Phase |
|---------|--------|--------|-----|-------|
| Character limits | High | Low | High | 1 |
| Character tracking | High | Low | High | 1 |
| `memory_insert` tool | High | Low | High | 1 |
| `memory_rethink` tool | High | Low | High | 1 |
| Agent ID system | High | Low | High | 2 |
| Shared memory blocks | High | Medium | High | 2 |
| Agent-scoped operations | High | Medium | High | 2 |

### 🟡 IMPORTANT (Should-Have for v2)

| Feature | Impact | Effort | ROI | Phase |
|---------|--------|--------|-----|-------|
| Hybrid search | Medium | High | Medium | 3 |
| Time-based filtering | Medium | Medium | Medium | 3 |
| Pagination | Medium | Low | Medium | 3 |
| Message tracking | High | Medium | High | 4 |
| Compaction engine | High | High | High | 4 |
| Block CRUD APIs | Medium | Medium | Medium | 6 |

### 🟢 NICE-TO-HAVE (Could defer)

| Feature | Impact | Effort | ROI | Phase |
|---------|--------|--------|-----|-------|
| Relevance scores | Low | Medium | Low | 3 |
| XML formatting | Low | Low | Low | 1 |
| Export/import | Low | Low | Low | 5 |
| Delete blocks | Low | Low | Low | 6 |

---

## Quick Comparison by Category

### 1. Memory Block Structure

**Letta:**
```python
{
    "label": "persona",
    "value": "I am Sam",
    "description": "Agent persona",
    "limit": 5000,
    "block_id": "abc-123",
    "is_shared": False,
    "read_only": False
}
```

**Ours (Current):**
```python
{
    "label": "persona",
    "value": "I am Sam",
    "description": "Agent persona",
    "is_editable": True
}
```

**Gap:** Missing `limit`, `block_id`, `is_shared`, `read_only`

---

### 2. Memory Editing Tools

**Letta:**
- `memory_replace(label, old_text, new_value)` - Search and replace
- `memory_insert(label, text, insert_after=None)` - Insert at position
- `memory_rethink(label, new_value)` - Rewrite entire block

**Ours (Current):**
- `core_memory_replace(label, new_value)` - Full overwrite
- `core_memory_append(label, text_to_append)` - Append to end

**Gap:** Missing granular insert and rewrite with reasoning

---

### 3. System Prompt Format

**Letta (XML):**
```xml
<memory_blocks>

<persona>
<description>The persona block: ...</description>
<metadata>
- chars_current=128
- chars_limit=5000
</metadata>
<value>I am Sam</value>
</persona>

</memory_blocks>
```

**Ours (Markdown - Current):**
```markdown
## [PERSONA]
I am Sam
```

**Gap:** Missing metadata injection, using simpler format

**Note:** Our format is actually cleaner for some use cases!

---

### 4. Shared Memory Pattern

**Letta:**
```python
# Create shared block
shared = client.blocks.create(label="shared", is_shared=True)

# Attach to multiple agents
agent1 = client.agents.create(block_ids=[shared.id])
agent2 = client.agents.create(block_ids=[shared.id])

# Both see same data
```

**Ours (Current):**
- No shared memory concept
- All agents share same global memory

**Gap:** Cannot isolate or selectively share blocks

---

### 5. Archival Search

**Letta:**
```python
results = client.agents.passages.search(
    agent_id,
    query="machine learning",
    tags=["technical"],
    start_datetime="2025-01-01",
    page=0
)
# Returns: content, tags, timestamp, relevance_scores
```

**Ours (Current):**
```python
results = manager.archival_memory_search(
    query="machine learning",
    limit=5
)
# Returns: ArchivalItem objects
```

**Gap:** Missing time filtering, pagination, relevance scores

---

### 6. Conversation Compaction

**Letta:**
```python
agent = client.agents.create(
    compaction_settings={
        "model": "gpt-4o-mini",
        "mode": "sliding_window",
        "sliding_window_percentage": 0.3
    }
)
# Automatically compacts when context fills
```

**Ours (Current):**
- No conversation tracking
- No compaction
- No context window management

**Gap:** Cannot handle long conversations

---

## What We Do Better

### ✅ Our Advantages

1. **Simpler System Prompt Format**
   - Letta: Verbose XML with metadata
   - Ours: Clean markdown
   - **Impact:** More tokens for actual content

2. **Local-First Design**
   - Letta: Cloud-first with self-hosted option
   - Ours: Local-first by design
   - **Impact:** Better privacy, lower latency

3. **Hybrid Storage from Day 1**
   - Letta: Started with Postgres, added options later
   - Ours: SQLite + ChromaDB from start
   - **Impact:** Simple deployment, no external dependencies

4. **Agent College Integration**
   - Letta: Generic multi-agent support
   - Ours: Purpose-built Agent College with critic/professor/scribe
   - **Impact:** Better code review workflows

---

## What We Need to Catch Up

### ❌ Critical Gaps

1. **Character Limits** - Essential for context management
2. **Shared Memory** - Critical for Agent College coordination
3. **Granular Editing Tools** - Need insert and rethink operations

### ⚠️ Important Gaps

4. **Hybrid Search** - Better search quality
5. **Compaction** - Long-running agent support
6. **Block IDs** - Required for shared memory

### 💡 Nice-to-Have Gaps

7. **Export/Import** - Convenience feature
8. **Relevance Scores** - Search quality insights
9. **Complete CRUD APIs** - Lifecycle management

---

## Implementation Reality Check

### What's Easy (1-2 days each)
- ✅ Character limits and tracking
- ✅ `memory_insert` and `memory_rethink` tools
- ✅ Agent ID system (UUID generation)
- ✅ Read-only flag enforcement

### What's Medium (1 week each)
- ⚠️ Shared memory architecture
- ⚠️ Agent-scoped operations
- ⚠️ FTS5 integration for hybrid search
- ⚠️ Export/import functionality

### What's Hard (2+ weeks each)
- ❌ Compaction engine (requires SDK wrapper)
- ❌ Complete block management APIs
- ❌ RRF scoring and hybrid search fusion

---

## Bottom Line

**Current Status:** We have ~60% feature parity with Letta

**After Phase 1-2:** ~80% feature parity (includes shared memory!)

**After All Phases:** ~95% feature parity (full Letta compatibility)

**Time to v1 (Sprints 1-2):** 4 weeks
**Time to v2 (All Sprints):** 11 weeks

**Recommendation:** Start with Phase 1-2 (Character limits + Shared Memory)
- Highest impact
- Lowest effort
- Enables Agent College coordination
- Foundation for everything else

---

**Last Updated:** 2025-12-29
**Based on Letta Docs:** `/home/kjdragan/lrepos/universal_agent/AI_DOCS/Letta_Docs/`