# Active Memory Management Implementation - New Conversation Context

**Purpose:** This file contains all necessary context to implement the Active Memory Management PRD in a fresh conversation.

---

## Project Overview

**Repository:** `/home/kjdragan/lrepos/universal_agent`
**Package Manager:** `uv` (NOT pip)
**Python Version:** 3.12+

### Key File Locations

```
Memory_System/
├── __init__.py
├── manager.py          # Main MemoryManager class
├── models.py          # Pydantic models (MemoryBlock, ArchivalItem, AgentState)
├── storage.py         # StorageManager (SQLite + ChromaDB)
└── tools.py           # Tool mappings

src/universal_agent/
├── main.py            # Main agent entry point
└── agent_core.py      # Core agent logic

Project_Documentation/Memory/
├── ACTIVE_MEMORY_MANAGEMENT_PRD.md    # The PRD to implement
├── README.md                          # System overview
└── ...
```

---

## Current Implementation

### MemoryManager Class (`Memory_System/manager.py`)

**Key Methods:**

```python
class MemoryManager:
    def __init__(self, storage_dir: str = "Memory_System/data"):
        self.storage = StorageManager(storage_dir)
        self.agent_state = self._load_or_initialize_state()

    def get_system_prompt_addition(self) -> str:
        """Injects core memory into system prompt."""
        prompt_lines = ["\n# 🧠 CORE MEMORY (Always Available)"]
        for block in self.agent_state.core_memory:
            prompt_lines.append(f"\n## [{block.label.upper()}]")
            prompt_lines.append(f"{block.value}")
        prompt_lines.append("\nNote: You can update these memory blocks using the `core_memory_replace` tool.")
        prompt_lines.append("Use `archival_memory_insert` to save huge facts/docs that don't fit here.\n")
        return "\n".join(prompt_lines)

    def get_tools_definitions(self) -> List[Dict]:
        """Returns tool definitions for agent."""
        return [
            {
                "name": "core_memory_replace",
                "description": "Overwrite a Core Memory block (e.g. 'human', 'persona'). Use this to update facts about the user or yourself.",
                ...
            },
            {
                "name": "core_memory_append",
                "description": "Append text to a Core Memory block. Useful for adding a new preference without deleting old ones.",
                ...
            },
            {
                "name": "archival_memory_insert",
                "description": "Save a fact, document, or event to long-term archival memory. Use for things that don't need to be in active context.",
                ...
            },
            {
                "name": "archival_memory_search",
                "description": "Search long-term archival memory using semantic search.",
                ...
            }
        ]

    def core_memory_replace(self, label: str, new_value: str) -> str:
        """Tool: Overwrite a specific memory block."""
        ...

    def core_memory_append(self, label: str, text_to_append: str) -> str:
        """Tool: Append to a memory block."""
        ...

    def archival_memory_insert(self, content: str, tags: str = "") -> str:
        """Tool: Save to long-term storage."""
        ...

    def archival_memory_search(self, query: str, limit: int = 5) -> str:
        """Tool: Search archival memory."""
        ...
```

### Default Memory Blocks (`Memory_System/manager.py:26-56`)

```python
def _load_or_initialize_state(self) -> AgentState:
    blocks = self.storage.get_core_memory()

    if not blocks:
        # 1. PERSONA: Who the agent is
        persona = MemoryBlock(
            label="persona",
            value=(
                "I are Antigravity, a powerful agentic AI coding assistant.\n"
                "I am pair programming with the USER to solve their coding task.\n"
                "I have access to a persistent memory system."
            ),
            description="The agent's personality and identity."
        )

        # 2. HUMAN: Facts about the user
        human = MemoryBlock(
            label="human",
            value=(
                "Name: User\n"
                "Preferences: None recorded yet."
            ),
            description="Personal facts about the user (name, location, likes)."
        )

        # 3. SYSTEM_RULES: Technical constraints
        system_rules = MemoryBlock(
            label="system_rules",
            value=(
                "Package Manager: uv (Always use `uv add`)\n"
                "OS: Linux"
            ),
            description="Technical rules and project constraints."
        )

        self.storage.save_block(persona)
        self.storage.save_block(human)
        self.storage.save_block(system_rules)
        blocks = [persona, human, system_rules]

    return AgentState(core_memory=blocks)
```

### Integration in main.py (`src/universal_agent/main.py:4159-4175`)

```python
# --- MEMORY SYSTEM CONTEXT INJECTION ---
memory_context_str = ""
try:
    from Memory_System.manager import MemoryManager
    from universal_agent.agent_college.integration import setup_agent_college

    # Initialize strictly for reading context (shared storage) - Use src_dir (Repo Root)
    storage_path = os.getenv("PERSIST_DIRECTORY", os.path.join(src_dir, "Memory_System_Data"))
    mem_mgr = MemoryManager(storage_dir=storage_path)

    # Initialize Agent College (Sandbox)
    setup_agent_college(mem_mgr)

    memory_context_str = mem_mgr.get_system_prompt_addition()
    print(f"🧠 Injected Core Memory Context ({len(memory_context_str)} chars)")
except Exception as e:
    print(f"⚠️ Failed to load Memory Context/Agent College: {e}")
```

---

## What Letta Does (The Target State)

> **Source:** Research conducted January 2026 on [letta-ai/letta](https://github.com/letta-ai/letta) repository using DeepWiki MCP

Letta's memory system uses a **multi-layered approach** combining prompt engineering, tool-based memory manipulation, and background memory learning agents.

### Memory Block Creation: BOTH User Request AND Automatic Learning

Letta supports **two mechanisms** for adding new memory blocks:

#### 1. User-Triggered Memory Block Creation

Users can explicitly create memory blocks through:

- **Agent Creation Time**: Provide `CreateBlock` objects in the `memory_blocks` field when creating an agent
- **Attaching Existing Blocks**: Use `attach_block_async()` method of `AgentManager` to attach standalone blocks to agents

#### 2. Automatic Memory Learning System ✨

The agent itself can **autonomously invoke memory tools** during reasoning:

| Tool | Purpose |
|------|---------|
| `memory_create` | Create entirely new memory blocks |
| `memory_rethink` | Completely rewrite/reorganize a block (for large-scale changes) |
| `memory_replace` | Replace specific strings within a block |
| `memory_insert` | Insert text at a specific line |
| `memory_apply_patch` | Apply unified-diff style patches |
| `memory_rename` | Rename existing blocks |

**How the Agent Decides When to Create/Modify Memory:**
1. **LLM reasoning** based on conversation context
2. **Tool descriptions in the prompt** that guide appropriate usage
3. **The objective**: `memory_create` for new information categories, `memory_rethink` for consolidating/reorganizing

### Letta Memory Block Structure

Each `Block` in Letta has these attributes:

```python
Block(
    label: str,           # Identifier: "human", "persona", "project", etc.
    value: str,           # The actual memory content
    description: str,     # What this block is for (guides agent usage)
    limit: int,           # Character limit
    is_template: bool,    # If block is a template
    read_only: bool,      # Agent can only read, not modify
    hidden: bool,         # Visibility control
    version: int,         # For optimistic locking
    metadata_: dict,      # Arbitrary additional data
)
```

### Sleep-Time Agents: Background Memory Learning 🌙

This is Letta's most advanced automatic memory feature:

- **Dedicated background agents** that asynchronously process conversations
- Run in the background to **review past conversations** and update memory blocks
- At defined frequencies, triggers a background task to review conversation history
- Uses tools like `store_memories`, `rethink_user_memory`, and `finish_rethinking_memory`

**Key behavior**: When a new block is attached to a main agent in a Sleep-Time group, it's **automatically shared** with the corresponding Sleep-Time Agent.

**Versions:** The codebase contains `SleeptimeMultiAgentV2`, `V3`, and `V4` (most recent), plus specialized `VoiceSleeptimeAgent` for voice interactions.

### Key Insight: LLM-Driven Memory Decisions

The Letta approach combines:
1. **Tool-based memory manipulation** — The agent has explicit tools to create/modify memory
2. **LLM reasoning** — The agent's LLM decides *when* to use these tools based on context
3. **Prescriptive prompt engineering** — Tool descriptions explicitly guide usage scenarios
4. **Background agents (Sleep-Time)** — Dedicated agents that handle memory consolidation asynchronously

> 💡 **This differs from rule-based systems**: The **LLM itself decides** when new memories are worth creating based on the conversation flow and the tool descriptions in its prompt.

---

### Prompt Engineering Implementation (Current Focus)

Letta's "self-learning" is achieved primarily through **prompt engineering**:

#### 1. Prescriptive Tool Descriptions

```python
{
    "name": "core_memory_replace",
    "description": """
Overwrite a Core Memory block with new information.

**IMPORTANT: You should proactively update memory when:**
- The user shares new personal information (name, preferences, background)
- The user changes their mind or preferences
- You learn important context about the project or task
- Information in memory becomes outdated or incorrect

**Memory is your long-term storage.** Information here persists across all conversations.
Be selective: only store information that is genuinely important to remember.

**Examples of when to update:**
- User: "My name is actually Sarah, not Alice" → Update 'human' block with new name
- User: "I've decided to use React instead of Vue" → Update 'system_rules' block
- User: "I work at Acme Corp now" → Update 'human' block with new employer
    """.strip(),
    ...
}
```

#### 2. Strong System Prompt Guidance

```python
def get_system_prompt_addition(self) -> str:
    prompt_lines = ["\n# 🧠 CORE MEMORY (Always Available)"]

    # NEW: Active memory guidance
    prompt_lines.append("""
**YOU ARE RESPONSIBLE FOR MAINTAINING YOUR OWN MEMORY**

Your memory persists across ALL conversations. Information stored here will be available
to you in every future conversation, making you more helpful and personalized.

**When the user provides information worth remembering:**
1. Update the `human` block with personal information, preferences, background
2. Update the `system_rules` block with technical constraints or project decisions
3. Use `archival_memory_insert` for detailed information that doesn't fit in core memory

**Be proactive but selective:** Store information that is genuinely important to remember
long-term. Small details (transient preferences, temporary states) may not need storage.

**Your ability to remember and recall information makes you a better assistant.**
""")

    for block in self.agent_state.core_memory:
        prompt_lines.append(f"\n## [{block.label.upper()}]")
        if block.description:
            prompt_lines.append(f"*{block.description}*")
        prompt_lines.append(f"{block.value}")

    prompt_lines.append("\n**Available Memory Tools:**")
    prompt_lines.append("- `core_memory_replace` - Update a memory block")
    prompt_lines.append("- `core_memory_append` - Add to a memory block")
    prompt_lines.append("- `archival_memory_insert` - Store detailed information")
    prompt_lines.append("- `archival_memory_search` - Search stored information\n")

    return "\n".join(prompt_lines)
```

#### 3. Enhanced Memory Block Descriptions

```python
# Persona block
persona = MemoryBlock(
    label="persona",
    value="I are Antigravity, a powerful agentic AI coding assistant...\n\
I am pair programming with the USER to solve their coding task.\n\
I have access to a persistent memory system.",
    description="Your identity, role, and behavioral guidelines. This defines who you are and how you should respond."
)

# Human block
human = MemoryBlock(
    label="human",
    value="Name: User\nPreferences: None recorded yet.",
    description="Everything you know about the user. Update this when learning their name, preferences, background, goals, or any personal information. This makes your interactions personalized."
)

# System rules block
system_rules = MemoryBlock(
    label="system_rules",
    value="Package Manager: uv (Always use `uv add`)\nOS: Linux",
    description="Technical constraints, project requirements, and rules you must follow. Update this when learning about new technical requirements, environment details, or project decisions."
)
```

---

## Future Enhancement: Dynamic Memory Block Creation

Based on Letta's `memory_create` tool, a future enhancement could allow the agent to **create new memory blocks on-the-fly**:

```python
# Example: New memory_create tool (Future Implementation)
def memory_create(self, label: str, description: str, initial_value: str = "") -> str:
    """
    Create a new memory block when existing blocks don't cover a category of information.
    
    Use cases:
    - User discusses a specific project frequently → Create "project_acme" block
    - User shares information about their team → Create "team" block
    - Agent learns about recurring workflows → Create "workflows" block
    
    The agent decides autonomously when a new block is needed based on:
    1. Information doesn't fit existing blocks (human, persona, system_rules)
    2. Information is important enough to warrant dedicated storage
    3. Information will be referenced frequently in future conversations
    """
    new_block = MemoryBlock(
        label=label,
        value=initial_value,
        description=description
    )
    self.storage.save_block(new_block)
    self.agent_state.core_memory.append(new_block)
    return f"Created new memory block '{label}': {description}"
```

---

## Deep Dive: Letta Sleep-Time Agents Architecture

> **Source:** Deep research conducted January 2026 on [letta-ai/letta](https://github.com/letta-ai/letta) repository using DeepWiki MCP

Sleep-Time Agents are Letta's most sophisticated automatic memory learning feature. This section documents the complete architecture for potential implementation in our Universal Agent memory system.

### Overview: What Are Sleep-Time Agents?

Sleep-Time Agents provide **background memory optimization** by asynchronously processing conversation history and updating shared memory blocks. The architecture involves:

| Component | Role |
|-----------|------|
| **Main Agent** | Handles direct user interactions, triggers sleeptime agent based on frequency |
| **Sleeptime Agent** | Background agent that reviews conversations and optimizes memory |
| **Group Manager** | Orchestrates multi-agent workflows, manages the `turns_counter` |
| **Shared Memory Blocks** | Memory blocks accessible by all agents in the group |

When an agent is created with `enable_sleeptime=True`, a `SleeptimeManager` group is automatically created, linking the main agent with a dedicated sleeptime agent.

---

### SleeptimeMultiAgentV4: The Core Architecture

The `SleeptimeMultiAgentV4` class extends `LettaAgentV3` and coordinates memory optimization:

```
┌─────────────────────────────────────────────────────────────────┐
│                    SleeptimeMultiAgentV4.step()                 │
├─────────────────────────────────────────────────────────────────┤
│  1. Main agent processes input messages                         │
│  2. After main agent completes turn → call run_sleeptime_agents │
│  3. run_sleeptime_agents checks:                                │
│     - sleeptime_agent_frequency                                 │
│     - turns_counter                                             │
│  4. If triggered → _issue_background_task()                     │
│     - Creates Run object (status: created)                      │
│     - Schedules _participant_agent_step coroutine               │
│  5. Sleeptime agent processes conversation transcript           │
│  6. Updates shared memory blocks                                │
└─────────────────────────────────────────────────────────────────┘
```

#### The `turns_counter` Mechanism

```python
# How sleeptime triggering works:
1. Message processed by agent in sleeptime group → turns_counter incremented
2. turns_counter % sleeptime_agent_frequency calculated
3. If result == 0 (or frequency is None) → run_sleeptime_agents() called
4. Background tasks issued for each sleeptime agent in group
```

---

### Manager Types

#### SleeptimeManager (Standard)
- `manager_agent_id`: ID of the sleeptime agent
- `sleeptime_agent_frequency`: How often to trigger (e.g., every N turns)
- `turns_counter`: Initialized to -1, tracks conversation turns

#### VoiceSleeptimeManager (For Voice/Real-time)
- `max_message_buffer_length`: Upper limit for messages in context (default: 30)
- `min_message_buffer_length`: Minimum messages to retain after eviction (default: 15)
- Uses buffer-based triggering instead of frequency-based
- Designed for low-latency voice chat with async memory management

---

### Sleeptime Agent Tools

#### Standard Sleeptime Agent Tools (`sleeptime_agent`)

| Tool | Purpose |
|------|---------|
| `memory_rethink` | Completely rewrite/reorganize a block (large-scale changes) |
| `memory_replace` | Replace specific strings within a block (deduplication) |
| `memory_insert` | Insert text at a specific line |
| `memory_finish_edits` | Signal completion of memory editing |

#### Voice Sleeptime Agent Tools (`voice_sleeptime_agent`)

| Tool | Purpose |
|------|---------|
| `store_memories` | Persist dialogue falling out of context window to archival |
| `rethink_user_memory` | Rewrite a memory block integrating new information |
| `finish_rethinking_memory` | Signal completion of memory rethinking |

---

### The `store_memories` Tool Deep Dive

This is a key innovation for handling context window overflow:

```python
class MemoryChunk(BaseModel):
    """Represents a contiguous block of evicted conversation lines"""
    start_index: int        # Zero-based index of first evicted line
    end_index: int          # Zero-based index of last evicted line (inclusive)
    context: str            # 1-3 sentence paraphrase capturing key facts,
                            # user preferences, or goals for future retrieval

# How store_memories works:
1. VoiceSleeptimeAgent receives chunks: List[MemoryChunk]
2. For each chunk:
   a. Retrieve message segment using start_index/end_index
   b. Serialize message history with serialize_message_history()
   c. Insert as passage into archival memory of main agent
   d. Rebuild system prompt for conversational agent
3. Passage stored in SQL database + vector DB (Turbopuffer) for retrieval
```

---

### Conversation Transcript Processing

When a sleeptime agent is triggered, the conversation transcript is formatted and passed to it:

```python
# _participant_agent_step method flow:
1. Gather messages:
   - prior_messages: from message_manager based on last_processed_message_id
   - response_messages: from current turn

2. Format with stringify_message():
   - Converts messages to human-readable strings
   - Combined into messages_text

3. Construct system reminder prompt:
   """
   You are a sleeptime agent - a background agent that asynchronously 
   processes conversations after they occur.
   
   You are NOT the primary agent. You are reviewing a conversation that 
   already happened between a primary agent and its user.
   
   Your primary role is memory management. Review the conversation and 
   use your memory tools to update any relevant memory blocks with 
   information worth preserving.
   
   Check your memory_persona block for any additional instructions or policies.
   """

4. Create MessageCreate object with formatted transcript
5. Pass to sleeptime_agent.step() as input_messages
```

---

### Shared Memory Block Synchronization

When a new block is attached to the main agent, it's automatically shared:

```python
# attach_block_async() synchronization logic:
1. Check if agent is part of a "sleeptime" multi-agent group
2. If yes, iterate through other agents in group
3. For each sleeptime_agent in group:
   a. Check if block already in agent's core_memory
   b. If not, append block to sleeptime_agent's core_memory
4. Result: All agents in group share the same memory blocks
```

---

### Context Window Summarization (The Summarizer Class)

Letta aggressively manages context window size:

#### Summarization Modes

| Mode | Behavior |
|------|----------|
| `STATIC_MESSAGE_BUFFER` | Fixed-size buffer; older messages evicted when exceeding `message_buffer_limit` |
| `PARTIAL_EVICT_MESSAGE_BUFFER` | Sliding window; evicts partial messages and replaces with recursive summary |
| `all` | Summarizes entire conversation history into single string (emergency mode) |

#### Summarization Flow

```
1. Token count exceeds context_window limit
2. Identify eviction point (boundary between kept/evicted messages)
3. Format evicted messages
4. Generate summary (via summarization agent or LLM call)
5. Create synthetic message with role "user" containing summary
6. Insert summary message, delete evicted messages
7. Re-calculate context_token_estimate
8. Hard eviction fallback if summarization fails (keep only system + 1 summary)
```

---

### Background Task System

Sleeptime agents run asynchronously to avoid blocking the main agent:

```python
# _issue_background_task() flow:
1. Create Run object:
   - status: RunStatus.created
   - metadata: {run_type: "sleeptime_agent_send_message_async", agent_id: ...}
   - Persisted via run_manager.create_run()

2. Schedule background task:
   - Uses safe_create_task() to create asyncio.Task
   - Runs concurrent with other operations (same process, asyncio event loop)
   - NOT a dedicated OS thread per agent (in V4)

3. Track status via Run object:
   - "created" → "pending" → "completed" or "error"
   - Updated via run_manager.update_run_by_id_async()
```

---

### Implications for Universal Agent Memory System

Key patterns we can adopt:

1. **Background Memory Agent**
   - Create a dedicated "memory consolidation" agent that runs after N turns
   - Reviews recent conversation history
   - Uses memory tools to update/create blocks

2. **Shared Block Architecture**
   - When attaching blocks to agents, auto-share with memory agents
   - Enables coordinated memory management across agent instances

3. **Context Window Management**
   - Implement summarization when context grows too large
   - Store evicted content in archival memory with semantic indexing
   - Use `MemoryChunk` pattern for preserving context about evicted content

4. **Tool-Guided Memory Decisions**
   - Expose memory tools to a background agent
   - Let LLM reasoning decide what to save/consolidate
   - Prescriptive tool descriptions guide appropriate usage

5. **Turn-Based Triggering**
   - Simple counter mechanism to trigger memory processing
   - Configurable frequency (`sleeptime_agent_frequency`)
   - Avoids processing overhead on every turn

---

## Implementation Tasks

### Task 1: Update Tool Descriptions (1 hour)

**File:** `Memory_System/manager.py`
**Method:** `get_tools_definitions()` (lines 162-216)

**Changes:**
1. Rewrite `core_memory_replace` description with prescriptive guidance
2. Rewrite `core_memory_append` description with usage examples
3. Rewrite `archival_memory_insert` description with tagging guidance
4. Rewrite `archival_memory_search` description with query examples

**Full Replacement Content:** See `ACTIVE_MEMORY_MANAGEMENT_PRD.md` section "Appendix: Example Tool Definitions (Complete)"

### Task 2: Update Block Descriptions (30 minutes)

**File:** `Memory_System/manager.py`
**Method:** `_load_or_initialize_state()` (lines 17-62)

**Changes:**
1. Enhance `persona` block description
2. Enhance `human` block description
3. Enhance `system_rules` block description

**New Descriptions:**
```python
# Persona
description="Your identity, role, and behavioral guidelines. This defines who you are and how you should respond."

# Human
description="Everything you know about the user. Update this when learning their name, preferences, background, goals, or any personal information. This makes your interactions personalized."

# System Rules
description="Technical constraints, project requirements, and rules you must follow. Update this when learning about new technical requirements, environment details, or project decisions."
```

### Task 3: Enhance System Prompt (1 hour)

**File:** `Memory_System/manager.py`
**Method:** `get_system_prompt_addition()` (lines 64-78)

**Changes:**
1. Add active memory guidance section
2. Include block descriptions in output
3. Rephrase tool availability notes

**New Format:**
```python
def get_system_prompt_addition(self) -> str:
    prompt_lines = ["\n# 🧠 CORE MEMORY (Always Available)"]

    # Active memory guidance
    prompt_lines.append("""
**YOU ARE RESPONSIBLE FOR MAINTAINING YOUR OWN MEMORY**

Your memory persists across ALL conversations. Information stored here will be available
to you in every future conversation, making you more helpful and personalized.

**When the user provides information worth remembering:**
1. Update the `human` block with personal information, preferences, background
2. Update the `system_rules` block with technical constraints or project decisions
3. Use `archival_memory_insert` for detailed information that doesn't fit in core memory

**Be proactive but selective:** Store information that is genuinely important to remember
long-term. Small details (transient preferences, temporary states) may not need storage.

**Your ability to remember and recall information makes you a better assistant.**
""")

    for block in self.agent_state.core_memory:
        prompt_lines.append(f"\n## [{block.label.upper()}]")
        if block.description:
            prompt_lines.append(f"*{block.description}*")
        prompt_lines.append(f"{block.value}")

    prompt_lines.append("\n**Available Memory Tools:**")
    prompt_lines.append("- `core_memory_replace` - Update a memory block")
    prompt_lines.append("- `core_memory_append` - Add to a memory block")
    prompt_lines.append("- `archival_memory_insert` - Store detailed information")
    prompt_lines.append("- `archival_memory_search` - Search stored information\n")

    return "\n".join(prompt_lines)
```

### Task 4: Testing (2 hours)

**Test Scenarios:**
1. Agent saves user's name when provided
2. Agent updates memory when preferences change
3. Agent uses archival memory for detailed project info
4. Agent is selective (doesn't save trivial information)
5. Multiple memory operations in one conversation

**Testing Commands:**
```bash
# Run existing tests
uv run python -m pytest tests/test_memory_system.py -v

# Run the agent locally
./local_dev.sh

# Test conversations to try:
# - "My name is Alice and I prefer Python over JavaScript"
# - "Actually, call me Ally instead of Alice"
# - "I'm working on a machine learning project using TensorFlow"
```

---

## Acceptance Criteria

- [ ] Tool descriptions include "IMPORTANT" sections with prescriptive guidance
- [ ] Tool descriptions include examples of when to use them
- [ ] Tool descriptions emphasize proactive memory management
- [ ] Block descriptions explain purpose clearly
- [ ] Block descriptions guide what to store and when to update
- [ ] System prompt explicitly states agent's responsibility for memory
- [ ] System prompt explains what to remember
- [ ] System prompt explains tool purposes
- [ ] Existing tests pass
- [ ] Manual testing shows agent saves memory proactively

---

## Success Metrics

- Agent saves memory in ≥70% of conversations where user provides personal info
- Agent updates memory (not just appends) in ≥50% of preference changes
- Agent uses archival memory for detailed info in ≥30% of relevant cases
- False positive rate <20% (saving unimportant information)

---

## Commands to Run After Implementation

```bash
# Format code
uv run black Memory_System/
uv run isort Memory_System/

# Run tests
uv run python -m pytest tests/test_memory_system.py -v

# Run agent locally to test
./local_dev.sh
```

---

## Notes for the Developer

1. **Use UV, not pip:** All package operations use `uv`
2. **No breaking changes:** This is prompt engineering only, no database schema changes
3. **Test thoroughly:** The changes affect how the agent behaves - test with real conversations
4. **Iterate if needed:** If agent doesn't save memory enough, strengthen the language more
5. **Monitor for over-saving:** If agent saves too much trivial info, add more "be selective" guidance

---

## Related Documentation

- **Full PRD:** `Project_Documentation/Memory/ACTIVE_MEMORY_MANAGEMENT_PRD.md`
- **System Overview:** `Project_Documentation/Memory/README.md`
- **Architecture:** `Project_Documentation/Memory/ARCHITECTURE.md`
- **Developer Guide:** `Project_Documentation/Memory/DEVELOPER_GUIDE.md`
- **Letta Analysis:** `Project_Documentation/Memory/LETTA_PROXY_ANALYSIS.md`

---

**Estimated Time:** ~4 hours
**Risk:** LOW (prompt engineering only, reversible)
**Impact:** HIGH (agent becomes more personalized and helpful over time)