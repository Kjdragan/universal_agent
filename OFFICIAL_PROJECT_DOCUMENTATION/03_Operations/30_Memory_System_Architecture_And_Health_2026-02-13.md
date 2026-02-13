# 30. Memory System Architecture & Health Audit (2026-02-13)

## Purpose

Comprehensive documentation of the Universal Agent memory system: how memory is loaded, stored, injected into the system prompt, and whether it is actually functioning properly on the VPS to build useful context over time.

**Verdict: The memory system is structurally sound but operationally broken.** Memory infrastructure exists at every layer, but almost nothing accumulates. The agent gets near-empty memory context on every run.

---

## Architecture Overview

The memory system has **two parallel backends** and **three injection points** into the system prompt.

```
┌─────────────────────────────────────────────────────────────┐
│                    SYSTEM PROMPT INJECTION                    │
│                                                               │
│  prompt_builder.py → build_system_prompt()                   │
│    ├── soul_context      ← SOUL.md (personality)             │
│    ├── memory_context    ← MemoryManager + File Memory       │
│    ├── capabilities      ← capabilities.md                   │
│    └── skills_xml        ← .claude/skills/                   │
│                                                               │
│  memory_context is assembled from TWO sources:               │
│    1. MemoryManager.get_system_prompt_addition()             │
│       → reads core_blocks from agent_core.db (GLOBAL)        │
│    2. build_file_memory_context()                            │
│       → reads memory/index.json (PER-WORKSPACE)              │
└─────────────────────────────────────────────────────────────┘
```

### Backend 1: Legacy Memory_System (MemoryManager)

**Location:** `Memory_System_Data/` (global, shared across all sessions)

| Component | Path | Purpose | Status |
|-----------|------|---------|--------|
| `agent_core.db` | `Memory_System_Data/agent_core.db` | Core blocks + archival FTS | **4 stale blocks, 0 traces** |
| `chroma_db/` | `Memory_System_Data/chroma_db/` | Vector embeddings | Exists, unclear population |

**Tables in agent_core.db:**
- `core_blocks` — 4 rows: persona, system_rules, AGENT_COLLEGE_NOTES, human
- `processed_traces` — **0 rows** (never indexed a single trace)
- `archival_fts` — 1 row (minimal full-text search data)

**Core Blocks (as of 2026-02-13):**

| Block | Last Updated | Content |
|-------|-------------|---------|
| `persona` | 2026-01-21 | "I are Antigravity..." ← **stale, grammatically broken** |
| `system_rules` | 2026-01-21 | "Package Manager: uv" ← **23 days stale** |
| `AGENT_COLLEGE_NOTES` | 2026-01-30 | Scratchpad, empty |
| `human` | 2026-01-31 | "Name: User, Preferences: None recorded yet" ← **13 days stale, wrong name** |

**How it injects:** `MemoryManager.get_system_prompt_addition()` reads core_blocks and formats them into a text block that goes into the system prompt.

### Backend 2: UA File Memory

**Location:** `{workspace}/memory/` (per-workspace, isolated)

| Component | Path | Purpose | Status |
|-----------|------|---------|--------|
| `MEMORY.md` | `{workspace}/MEMORY.md` | Human-readable memory file | **Scaffold only** |
| `index.json` | `{workspace}/memory/index.json` | Long-term memory index | **Does not exist** |
| `session_index.json` | `{workspace}/memory/session_index.json` | Session transcript index | 1 entry |
| `sessions/` | `{workspace}/memory/sessions/` | Session transcript chunks | 1 file |

**How it injects:** `build_file_memory_context()` reads `index.json`, formats recent entries, trims to token budget (800 tokens default).

---

## Data Flow: How Memory Gets Written

### Write Path 1: Agent MCP Tools (Agent-Initiated)

The agent has MCP tools to explicitly manage memory:

```
mcp__internal__core_memory_replace   → Updates a core_block in agent_core.db
mcp__internal__core_memory_append    → Appends to a core_block
mcp__internal__archival_memory_insert → Adds to archival FTS index
mcp__internal__archival_memory_search → Searches archival memory
mcp__internal__get_core_memory_blocks → Reads all core blocks
```

**Problem:** The agent almost never calls these tools. There's no prompt instruction telling it WHEN to save memories. The SOUL.md says "Use your memory system" but gives no trigger conditions.

### Write Path 2: Memory Flush on Exit (Automatic)

When a session ends, if `UA_MEMORY_FLUSH_ON_EXIT=1`:

```
main.py (line ~7559) → flush_pre_compact_memory()
  → Reads last 4000 chars of transcript.md
  → Creates a MemoryEntry with source="pre_compact"
  → Appends to workspace memory/index.json + daily .md file
```

**Problem:** This only fires in the **legacy CLI path** (`main.py`). The **ProcessTurnAdapter** (VPS/gateway path) has NO memory flush on close. The `close()` method at line 743 just calls `reset()` — no memory operations.

### Write Path 3: Session Sync (Automatic)

The `UAFileMemoryAdapter.sync_session()` indexes transcript deltas when:
- Delta bytes ≥ 100,000 OR delta messages ≥ 50

**Problem:** For typical cron jobs (short runs), transcripts are well under these thresholds, so session sync rarely triggers. The one session that did sync was likely forced.

### Write Path 4: Memory Orchestrator (Unified Mode)

When `UA_MEMORY_ORCHESTRATOR_MODE=unified`, writes go through `MemoryOrchestrator` which fans out to all registered adapters.

**Problem:** The VPS is NOT in unified mode. `UA_MEMORY_ORCHESTRATOR_MODE` is not set, defaulting to `"legacy"`. This means the orchestrator's `_allow_write()` gating never fires and multi-adapter fan-out doesn't happen.

---

## Data Flow: How Memory Gets Read (Injected)

```python
# agent_setup.py → _load_memory_context()
def _load_memory_context(self) -> str:
    # SOURCE 1: Legacy MemoryManager
    mem_mgr = MemoryManager(storage_dir=storage_path, workspace_dir=self.workspace_dir)
    context = mem_mgr.get_system_prompt_addition()
    # → Returns core_blocks formatted as text (stale persona + human blocks)

    # SOURCE 2: File-based memory
    file_context = build_file_memory_context(
        self.workspace_dir,
        max_tokens=800,    # ← UA_MEMORY_MAX_TOKENS
        index_mode="json", # ← UA_MEMORY_INDEX
        recent_limit=8,    # ← UA_MEMORY_RECENT_ENTRIES
    )
    # → Reads workspace/memory/index.json → returns empty (file doesn't exist)

    return f"{context}\n{file_context}\n"
```

**What the agent actually sees in its system prompt:**
1. The stale core_blocks from `agent_core.db` ("I are Antigravity", "Name: User", "Preferences: None recorded yet")
2. Nothing from file memory (empty)

---

## Critical Findings

### 🔴 FINDING 1: Core blocks are stale and wrong

The `human` block says "Name: User" and "Preferences: None recorded yet" — 13 days after the user identified as Kev. The `persona` block still says "Antigravity" — we renamed to Simone today. These blocks inject every time and give the agent incorrect identity context.

**Root cause:** Nothing updates core_blocks automatically. The agent must explicitly call `mcp__internal__core_memory_replace` — which it almost never does because the prompt doesn't tell it when to.

### 🔴 FINDING 2: Zero long-term memory accumulation on VPS

`memory/index.json` doesn't exist on the VPS. Zero long-term memories have been written via the file-based system.

**Root cause:** The automatic flush (`flush_pre_compact_memory`) only fires in `main.py` (legacy CLI). The `ProcessTurnAdapter.close()` does NOT flush memory. Cron jobs end without saving anything.

### 🔴 FINDING 3: Workspace isolation prevents memory sharing

Each session/cron job gets its own workspace directory. File-based memory is per-workspace. A cron job in `cron_c028f679d4/` cannot see memories from `session_20260213/`.

The Legacy `Memory_System_Data/` IS global, but `processed_traces` has 0 rows — it never indexed any session transcripts.

**Result:** Memory doesn't accumulate across sessions. Each run starts fresh.

### 🟡 FINDING 4: Session sync works but thresholds are too high

Session sync triggers at 100KB or 50 messages. Most cron runs produce < 5KB transcripts. The thresholds were designed for long interactive sessions, not short automated runs.

### 🟡 FINDING 5: Orchestrator mode is "legacy" — unified features disabled

The VPS `.env` doesn't set `UA_MEMORY_ORCHESTRATOR_MODE`. It defaults to `"legacy"`, which means the `MemoryOrchestrator` with its multi-adapter fan-out, importance gating, and tag decoration is never used.

### 🟡 FINDING 6: `processed_traces` table is empty

The Legacy MemoryManager has a `processed_traces` table designed to track which traces/sessions have been indexed. It has 0 rows. Whatever was supposed to populate this never ran.

---

## Feature Flag Configuration (VPS)

| Flag | Value | Effect |
|------|-------|--------|
| `UA_MEMORY_ENABLED` | `1` | Memory system ON |
| `UA_MEMORY_INDEX` | `json` | JSON index mode (not vector) |
| `UA_MEMORY_MAX_TOKENS` | `800` | Max tokens for memory injection |
| `UA_MEMORY_RECENT_ENTRIES` | `8` | Show 8 most recent entries |
| `UA_MEMORY_FLUSH_MAX_CHARS` | `4000` | Max chars for flush |
| `UA_MEMORY_FLUSH_ON_EXIT` | `1` | Flush enabled (but only fires in legacy path!) |
| `UA_DISABLE_LOCAL_MEMORY` | `0` | Memory NOT disabled |
| `UA_LETTA_SUBAGENT_MEMORY` | `0` | Letta integration OFF |
| `UA_MEMORY_ORCHESTRATOR_MODE` | *(unset)* | Defaults to "legacy" |

---

## File Inventory

```
src/universal_agent/memory/
├── __init__.py
├── adapters/
│   ├── base.py              # MemoryAdapter ABC
│   ├── letta.py             # Letta adapter (off by default)
│   ├── memory_system.py     # Legacy MemoryManager adapter (shadow)
│   └── ua_file.py           # File-based adapter (active) — 307 lines
├── chromadb_backend.py       # ChromaDB vector backend
├── context_manager.py        # Context pruning (conversation history) — 259 lines
├── embeddings.py             # Embedding utilities
├── lancedb_backend.py        # LanceDB vector backend
├── memory_context.py         # build_file_memory_context() — 68 lines
├── memory_flush.py           # flush_pre_compact_memory() — 66 lines
├── memory_index.py           # JSON index CRUD — 97 lines
├── memory_models.py          # MemoryEntry dataclass — 45 lines
├── memory_store.py           # append_memory_entry() + scaffold — 215 lines
├── memory_vector_index.py    # SQLite vector index utilities
└── orchestrator.py           # MemoryOrchestrator broker — 318 lines
```

---

## Recommended Fixes (Priority Order)

### P0: Add memory flush to ProcessTurnAdapter.close()

The gateway/cron path never flushes memory. This is the single biggest reason memory doesn't accumulate.

**Fix:** In `execution_engine.py`, add a memory flush call in `ProcessTurnAdapter.close()` or at the end of `execute()`, mirroring what `main.py` does at line ~7559.

### P0: Update stale core_blocks

The `persona` block says "Antigravity" and the `human` block says "Name: User". These need to be updated to reflect Simone and Kev.

**Fix:** Run SQL update on VPS `agent_core.db`, or have the agent call `core_memory_replace` for persona + human blocks.

### P1: Add memory-save triggers to system prompt

The agent doesn't know WHEN to save memories. Add explicit trigger conditions:
- "After learning a new user preference, save it via `mcp__internal__core_memory_replace` on the `human` block"
- "After completing a significant task, save a summary via `mcp__internal__archival_memory_insert`"
- "At the start of each session, read your core memory with `mcp__internal__get_core_memory_blocks`"

### P1: Lower session sync thresholds for cron jobs

Change `UA_MEMORY_SESSION_DELTA_BYTES` from 100,000 to ~5,000 and `UA_MEMORY_SESSION_DELTA_MESSAGES` from 50 to ~10 for cron workspaces.

### P2: Enable unified orchestrator mode

Set `UA_MEMORY_ORCHESTRATOR_MODE=unified` to enable multi-adapter fan-out, importance gating, and proper tag decoration.

### P2: Implement cross-workspace memory sharing

Currently file memory is per-workspace. Either:
- Point all workspaces to a shared memory directory, OR
- Use the global `Memory_System_Data/` as the canonical long-term store and ensure it gets populated

### P3: Auto-populate processed_traces

The `processed_traces` table was designed to track indexed sessions but has 0 rows. Wire up the post-session hook to populate this.

---

*Generated 2026-02-13. See Doc 29 for system prompt comparison context.*
