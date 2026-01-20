# Active Memory Management PRD
## Autonomous Memory Formation in the Universal Agent

**Document Version**: 1.1
**Last Updated**: 2026-01-03
**Status**: Draft for Review
**Complexity**: LOW (Prompt Engineering + Minor Code Changes)

---

## Executive Summary

This PRD documents Letta's "active memory management" capability—the ability of agents to **autonomously decide when and what to remember** during conversations. Unlike traditional RAG systems that passively retrieve documents, Letta agents actively maintain their own memory using built-in tools.

**Key Finding:** Letta's "self-learning" is NOT complex machine learning or reinforcement learning. It is **tool-based memory formation** enhanced through:
1. Prescriptive tool descriptions that guide behavior
2. System prompt reinforcement of memory maintenance
3. Detailed memory block descriptions
4. Always-visible memory (feedback loop)
5. **NEW:** `memory_create` tool for autonomous block creation

**Implementation Effort:** ~6 hours (prompt engineering + new memory_create tool)

---

## Background: Letta's Approach

### What "Active Memory Management" Means

From Letta documentation:

> **"Unlike traditional RAG systems that passively retrieve documents, Letta agents actively manage their own memory using built-in tools to read, write, and search their persistent storage."**

> **"Agents use built-in tools to decide what to remember, update, and search for."**

> **"This enables agents to:**
> - **Learn user preferences over time**
> - **Maintain consistent personality across sessions**
> - **Build long-term relationships with users**
> - **Continuously improve from interactions**"

### How Letta Achieves This

Letta's "self-learning" is achieved through **four key mechanisms**:

#### 1. Prescriptive Tool Descriptions

Letta's tool descriptions include **explicit guidance on when to use them**:

```python
# Letta-style (inferred from documentation)
{
    "name": "core_memory_replace",
    "description": """
    Overwrite a Core Memory block. Use this to update facts about the user or yourself.

    IMPORTANT: You should update memory when:
    - The user provides new information about themselves
    - The user changes their preferences or opinions
    - You learn something that should be remembered long-term

    Examples:
    - User says "My name changed from Alex to Alex" → Update 'human' block
    - User says "I now prefer tea over coffee" → Update 'human' block
    """
}
```

#### 2. System Prompt Reinforcement

Letta's system prompt includes **explicit instructions** about memory maintenance:

> "You are a stateful agent with long-term memory. You should actively maintain your memory blocks by updating them when you learn new information about the user or your context."

#### 3. Detailed Memory Block Descriptions

Letta emphasizes the importance of block descriptions:

> **"When making memory blocks, it is crucial to provide a good `description` field that accurately describes what the block should be used for. The `description` is the main information used by the agent to determine how to read and write to that block."**

Letta's default descriptions:
- **persona**: "The persona block: Stores details about your current persona, guiding how you behave and respond."
- **human**: "The human block: Stores key details about the person you are conversing with, allowing for more personalized and friend-like conversation."

#### 4. Always-Visible Memory (Feedback Loop)

Memory blocks are **injected into every system prompt** in XML format, creating a constant feedback loop:

```
Agent sees memory → Agent updates memory → Updated memory is visible → Repeat
```

### What Letta Does NOT Have

Letta's "self-learning" does **NOT** include:
- ❌ Reinforcement learning
- ❌ Neural network weight updates
- ❌ Automated pattern extraction from conversations
- ❌ Machine learning-based memory formation
- ❌ Background processing or async learning

**Letta's "learning" = Agent uses tools to write to a database.**

---

## Current State Analysis

### What Our System Has

#### ✅ Tools Exposed to Agent

**File:** `Memory_System/manager.py:162-216`

```python
def get_tools_definitions(self) -> List[Dict]:
    return [
        {
            "name": "core_memory_replace",
            "description": "Overwrite a Core Memory block (e.g. 'human', 'persona'). Use this to update facts about the user or yourself.",
            "input_schema": {...}
        },
        {
            "name": "core_memory_append",
            "description": "Append text to a Core Memory block. Useful for adding a new preference without deleting old ones.",
            "input_schema": {...}
        },
        {
            "name": "archival_memory_insert",
            "description": "Save a fact, document, or event to long-term archival memory. Use for things that don't need to be in active context.",
            "input_schema": {...}
        },
        {
            "name": "archival_memory_search",
            "description": "Search long-term archival memory using semantic search.",
            "input_schema": {...}
        }
    ]
```

#### ✅ System Prompt Mentions Memory

**File:** `Memory_System/manager.py:64-78`

```python
def get_system_prompt_addition(self) -> str:
    prompt_lines = ["\n# 🧠 CORE MEMORY (Always Available)"]

    for block in self.agent_state.core_memory:
        prompt_lines.append(f"\n## [{block.label.upper()}]")
        prompt_lines.append(f"{block.value}")

    prompt_lines.append("\nNote: You can update these memory blocks using the `core_memory_replace` tool.")
    prompt_lines.append("Use `archival_memory_insert` to save huge facts/docs that don't fit here.\n")

    return "\n".join(prompt_lines)
```

#### ⚠️ Mixed Memory Block Descriptions

**File:** `Memory_System/manager.py:26-56`

```python
# Persona block - Generic description
persona = MemoryBlock(
    label="persona",
    value="I are Antigravity, a powerful agentic AI coding assistant...",
    description="The agent's personality and identity."  # Too generic
)

# Human block - Better description
human = MemoryBlock(
    label="human",
    value="Name: User\nPreferences: None recorded yet.",
    description="Personal facts about the user (name, location, likes)."  # Better
)

# System rules - Generic description
system_rules = MemoryBlock(
    label="system_rules",
    value="Package Manager: uv (Always use `uv add`)\nOS: Linux",
    description="Technical rules and project constraints."  # Too generic
)
```

### What's Missing

| Component | Letta | Ours | Gap |
|-----------|-------|------|-----|
| **Prescriptive tool descriptions** | ✅ Detailed when/how guidance | ⚠️ Generic single sentence | Medium |
| **System prompt reinforcement** | ✅ Strong imperative guidance | ⚠️ Weak note-style | Medium |
| **Block descriptions** | ✅ Detailed purpose guidance | ⚠️ Mixed (some good, some generic) | Low |
| **Few-shot examples** | ✅ Likely yes (in system prompt) | ❌ None | Medium |
| **Memory visibility** | ✅ Always in context | ✅ Always in context | None |

---

## Proposed Solution

### Approach: Enhanced Prompt Engineering

The solution is **primarily prompt engineering** with minimal code changes:

1. **Rewrite tool descriptions** to be prescriptive with examples
2. **Add active memory guidance** to system prompt
3. **Enhance memory block descriptions** with detailed purpose statements
4. **Optionally add few-shot examples** to demonstrate behavior

### Implementation

#### Change 1: Enhanced Tool Descriptions

**File:** `Memory_System/manager.py`

**Current:**
```python
{
    "name": "core_memory_replace",
    "description": "Overwrite a Core Memory block (e.g. 'human', 'persona'). Use this to update facts about the user or yourself.",
    "input_schema": {...}
}
```

**Proposed:**
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
- User: "Turns out I prefer tea over coffee" → Update 'human' block with new preference

**Remember:** Good memory management makes you more helpful and personalized. Update memory
whenever you learn something worth remembering.
    """.strip(),
    "input_schema": {...}
}
```

**Current:**
```python
{
    "name": "archival_memory_insert",
    "description": "Save a fact, document, or event to long-term archival memory. Use for things that don't need to be in active context.",
    "input_schema": {...}
}
```

**Proposed:**
```python
{
    "name": "archival_memory_insert",
    "description": """
Save detailed information to long-term archival memory for later retrieval.

**Use archival memory for:**
- Detailed project information (requirements, specs, decisions)
- Extended conversation context (meeting notes, discussion outcomes)
- Technical documentation (API references, code examples)
- Historical events and milestones
- Information too large for core memory blocks

**Tags are important!** Use descriptive tags to organize memories:
- Personal: `user_info`, `preference`, `background`
- Project: `project`, `requirement`, `decision`, `bug`
- Technical: `documentation`, `api`, `reference`, `code`

**Examples:**
- "User is building a REST API using FastAPI" → Insert with tags: `project`, `fastapi`, `api`
- "Meeting outcome: decided to use PostgreSQL" → Insert with tags: `meeting`, `decision`, `database`
- "User mentioned they have a PhD in Computer Science" → Insert with tags: `user_info`, `background`

**Retrieval:** You can search archival memory later using `archival_memory_search`.
    """.strip(),
    "input_schema": {...}
}
```

#### Change 2: Enhanced System Prompt

**File:** `Memory_System/manager.py`

**Current:**
```python
def get_system_prompt_addition(self) -> str:
    prompt_lines = ["\n# 🧠 CORE MEMORY (Always Available)"]

    for block in self.agent_state.core_memory:
        prompt_lines.append(f"\n## [{block.label.upper()}]")
        prompt_lines.append(f"{block.value}")

    prompt_lines.append("\nNote: You can update these memory blocks using the `core_memory_replace` tool.")
    prompt_lines.append("Use `archival_memory_insert` to save huge facts/docs that don't fit here.\n")

    return "\n".join(prompt_lines)
```

**Proposed:**
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

#### Change 3: Enhanced Block Descriptions

**File:** `Memory_System/manager.py`

**Current:**
```python
persona = MemoryBlock(
    label="persona",
    value="I are Antigridity, a powerful agentic AI coding assistant...",
    description="The agent's personality and identity."
)
```

**Proposed:**
```python
persona = MemoryBlock(
    label="persona",
    value="I are Antigravity, a powerful agentic AI coding assistant...\n\
I am pair programming with the USER to solve their coding task.\n\
I have access to a persistent memory system.",
    description="Your identity, role, and behavioral guidelines. This defines who you are and how you should respond."
)
```

**Current:**
```python
human = MemoryBlock(
    label="human",
    value="Name: User\nPreferences: None recorded yet.",
    description="Personal facts about the user (name, location, likes)."
)
```

**Proposed:**
```python
human = MemoryBlock(
    label="human",
    value="Name: User\nPreferences: None recorded yet.",
    description="Everything you know about the user. Update this when learning their name, preferences, background, goals, or any personal information. This makes your interactions personalized."
)
```

**Current:**
```python
system_rules = MemoryBlock(
    label="system_rules",
    value="Package Manager: uv (Always use `uv add`)\nOS: Linux",
    description="Technical rules and project constraints."
)
```

**Proposed:**
```python
system_rules = MemoryBlock(
    label="system_rules",
    value="Package Manager: uv (Always use `uv add`)\nOS: Linux",
    description="Technical constraints, project requirements, and rules you must follow. Update this when learning about new technical requirements, environment details, or project decisions."
)
```

#### Change 4: Optional Few-Shot Examples

**Add to system prompt (optional but powerful):**

```python
def get_system_prompt_addition(self) -> str:
    # ... existing code ...

    # Add few-shot examples (optional)
    prompt_lines.append("""
**Memory Formation Examples:**

User: "My name is Alice and I prefer Python over JavaScript."
→ Action: Update `human` block with name and preference

User: "Actually, call me Ally instead of Alice."
→ Action: Update `human` block, change name from "Alice" to "Ally"

User: "I'm working on a machine learning project using TensorFlow with PyTorch as backup."
→ Action: Update `human` block with project info, use `archival_memory_insert` for detailed ML context

User: "I forgot, I use UV package manager, not pip."
→ Action: Update `system_rules` block to reflect UV requirement
""")

    # ... rest of code ...
```

---

## Enhanced Feature: Autonomous Block Creation

### The `memory_create` Tool

Based on deeper analysis of Letta's implementation, a key capability we should add is **autonomous memory block creation**. This allows the agent to create entirely new memory blocks when existing blocks don't adequately represent a category of information.

### Why This Matters

Our current system has three fixed blocks:
- `persona` - Agent identity
- `human` - User information
- `system_rules` - Technical constraints

But conversations often involve information that doesn't fit neatly into these categories:

| Scenario | Problem | Solution |
|----------|---------|----------|
| User discusses a specific project frequently | Clutters `human` or `system_rules` | Create `project_acme` block |
| Agent learns about user's team dynamics | Doesn't belong in `persona` | Create `team` block |
| Recurring workflow patterns emerge | Lost in generic blocks | Create `workflows` block |
| User has multiple roles/contexts | `human` becomes messy | Create `roles` block |

### Letta's Approach

Letta agents have access to a `memory_create` tool with this pattern:

```python
def memory_create(label: str, value: str = "", description: str = "") -> str:
    """
    Create a new memory block when existing blocks don't cover a category of information.

    **When to create a new block:**
    - Information doesn't logically fit in 'human', 'persona', or 'system_rules'
    - Information represents a distinct category that will be referenced frequently
    - Existing blocks are becoming cluttered with unrelated information
    - A dedicated block would improve organization and retrieval

    **Examples of when to create:**
    - User discusses a specific project extensively → Create 'project_{name}' block
    - User mentions their team members frequently → Create 'team' block
    - Agent learns about recurring workflows → Create 'workflows' block
    - User has distinct roles (work vs personal) → Create separate context blocks

    **Block naming conventions:**
    - Use snake_case: `project_acme`, `user_team`, `daily_workflow`
    - Be descriptive but concise
    - Consider prefixing for organization: `project_*`, `team_*`

    **Return value:** Confirmation message with block label and description.
    """
```

### Decision Guidance for the Agent

The LLM needs clear guidance on when to create blocks vs. using existing ones:

```
DECISION TREE: Should I create a new memory block?

1. Is this information about the USER specifically?
   YES → Use 'human' block (update or append)
   NO → Continue

2. Is this about the AGENT'S identity or behavior?
   YES → Use 'persona' block
   NO → Continue

3. Is this a technical rule or constraint?
   YES → Use 'system_rules' block
   NO → Continue

4. Does this information represent a NEW, DISTINCT category?
   AND will it be referenced frequently?
   AND does it clutter existing blocks?
   ALL YES → Create new block with memory_create
   ANY NO → Use archival_memory_insert instead
```

---

## Implementation Plan

### Phase 1: Tool Description Updates (1 hour)

**Tasks:**
1. Rewrite `core_memory_replace` description with prescriptive guidance
2. Rewrite `core_memory_append` description with usage examples
3. Rewrite `archival_memory_insert` description with tagging guidance
4. Rewrite `archival_memory_search` description with query examples

**Files:**
- `Memory_System/manager.py` (lines 162-216)

**Acceptance Criteria:**
- [ ] Tool descriptions include "IMPORTANT" sections
- [ ] Tool descriptions include examples of when to use
- [ ] Tool descriptions emphasize proactive memory management

### Phase 2: Block Description Updates (30 minutes)

**Tasks:**
1. Enhance `persona` block description
2. Enhance `human` block description
3. Enhance `system_rules` block description

**Files:**
- `Memory_System/manager.py` (lines 26-56)

**Acceptance Criteria:**
- [ ] Block descriptions explain purpose clearly
- [ ] Block descriptions guide what to store
- [ ] Block descriptions mention when to update

### Phase 3: System Prompt Enhancement (1 hour)

**Tasks:**
1. Add active memory guidance section
2. Rephrase tool availability notes
3. (Optional) Add few-shot examples

**Files:**
- `Memory_System/manager.py` (lines 64-78)

**Acceptance Criteria:**
- [ ] System prompt explicitly states agent's responsibility
- [ ] System prompt explains what to remember
- [ ] System prompt explains tool purposes

### Phase 4: Testing and Iteration (2 hours)

**Tasks:**
1. Run test conversations
2. Observe memory formation behavior
3. Iterate on descriptions/prompts based on results
4. Document patterns that work/don't work

**Acceptance Criteria:**
- [ ] Agent proactively saves user information
- [ ] Agent updates memory when preferences change
- [ ] Agent uses archival memory for detailed information
- [ ] No excessive memory saving (selective behavior)

### Phase 5: Autonomous Block Creation (2 hours)

**Tasks:**
1. Implement `memory_create` tool in MemoryManager
2. Add tool to agent's tool definitions
3. Update system prompt to include block creation guidance
4. Test with scenarios that warrant new blocks

**Files:**
- `Memory_System/manager.py` - Add `memory_create` method and tool definition
- `Memory_System/storage.py` - May need schema update for dynamic blocks

**Implementation:**
```python
def memory_create(self, label: str, value: str = "", description: str = "") -> str:
    """
    Create a new memory block when existing blocks don't cover a category of information.

    **When to create a new block:**
    - Information doesn't logically fit in 'human', 'persona', or 'system_rules'
    - Information represents a distinct category that will be referenced frequently
    - Existing blocks are becoming cluttered with unrelated information
    - A dedicated block would improve organization and retrieval

    **Examples of when to create:**
    - User discusses a specific project extensively → Create 'project_{name}' block
    - User mentions their team members frequently → Create 'team' block
    - Agent learns about recurring workflows → Create 'workflows' block
    """
    # Check if block already exists
    existing = self.storage.get_block(label)
    if existing:
        return f"Block '{label}' already exists. Use core_memory_replace to update it."

    # Create new block
    new_block = MemoryBlock(
        label=label,
        value=value,
        description=description or f"Memory block for {label}"
    )
    self.storage.save_block(new_block)
    self.agent_state.core_memory.append(new_block)
    return f"Created new memory block '{label}': {description}"
```

**Tool Definition:**
```python
{
    "name": "memory_create",
    "description": """
Create a new memory block when existing blocks don't cover a category of information.

**When to create a new block:**
- Information doesn't logically fit in 'human', 'persona', or 'system_rules'
- Information represents a distinct category that will be referenced frequently
- Existing blocks are becoming cluttered with unrelated information
- A dedicated block would improve organization and retrieval

**Examples of when to create:**
- User discusses a specific project extensively → Create 'project_{name}' block
- User mentions their team members frequently → Create 'team' block
- Agent learns about recurring workflows → Create 'workflows' block
- User has distinct roles (work vs personal) → Create separate context blocks

**Block naming conventions:**
- Use snake_case: 'project_acme', 'user_team', 'daily_workflow'
- Be descriptive but concise
- Consider prefixing for organization: 'project_*', 'team_*'

**Remember:** Only create new blocks when truly necessary. Most information belongs
in existing blocks or in archival memory.
    """.strip(),
    "input_schema": {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "description": "The unique label for the new block (use snake_case, e.g., 'project_acme')"
            },
            "value": {
                "type": "string",
                "description": "Initial content for the block (optional, can be updated later)",
                "default": ""
            },
            "description": {
                "type": "string",
                "description": "What this block is for - helps guide how it's used",
                "default": ""
            }
        },
        "required": ["label"]
    }
}
```

**Acceptance Criteria:**
- [ ] Agent can create new blocks when appropriate
- [ ] Agent is selective about creating blocks (doesn't over-create)
- [ ] Created blocks appear in system prompt
- [ ] Blocks can be updated with core_memory_replace after creation

---

## Success Metrics

### Quantitative

- [ ] Agent saves memory in ≥70% of conversations where user provides personal info
- [ ] Agent updates memory (not just appends) in ≥50% of preference changes
- [ ] Agent uses archival memory for detailed info in ≥30% of relevant cases
- [ ] False positive rate <20% (saving unimportant information)

### Qualitative

- [ ] Memory captured is accurate and complete
- [ ] Memory captured is properly categorized (core vs archival)
- [ ] Memory updates are timely (happen during conversation, not delayed)
- [ ] User experience feels more personalized over time

---

## Risks and Mitigations

### Risk 1: Over-Saving (Memory Pollution)

**Problem:** Agent saves too much information, cluttering memory with trivial details.

**Mitigation:**
- Emphasize selectivity in tool descriptions
- Add "be selective" guidance to system prompt
- Monitor and iterate on descriptions

### Risk 2: Under-Saving (Missed Information)

**Problem:** Agent doesn't save important information often enough.

**Mitigation:**
- Strengthen prescriptive language in descriptions
- Add more examples of when to save
- Test with diverse conversation types

### Risk 3: Incorrect Updates

**Problem:** Agent overwrites memory incorrectly or loses information.

**Mitigation:**
- Provide examples of proper update patterns
- Encourage `core_memory_append` for additive changes
- Test edge cases (name changes, preference reversals)

### Risk 4: Model-Dependent Behavior

**Problem:** Different Claude models may respond differently to prompts.

**Mitigation:**
- Test with primary model (Sonnet 4.5)
- Validate behavior across model versions
- Adjust descriptions based on model behavior

---

## Alternatives Considered

### Alternative 1: Post-Processing Memory Extraction

**Approach:** After each conversation, use a separate LLM call to extract and save memories.

**Pros:**
- Guaranteed memory formation
- More control over what gets saved

**Cons:**
- Additional API cost (extra LLM call per conversation)
- Delayed memory formation (not during conversation)
- Not "active" (agent isn't deciding)

**Decision:** Not preferred. Active memory formation is more elegant and aligned with Letta.

### Alternative 2: Rule-Based Triggers

**Approach:** Use regex/rules to detect memory-worthy statements and auto-save.

**Pros:**
- Consistent behavior
- No LLM dependency

**Cons:**
- Brittle (hard to cover all cases)
- Not semantic understanding
- Maintenance burden

**Decision:** Not preferred. LLM-based decision making is more flexible.

### Alternative 3: Reinforcement Learning

**Approach:** Train a model to decide when to save memories.

**Pros:**
- Could optimize over time
- Data-driven

**Cons:**
- Extremely complex
- Requires training data
- Overkill for this use case

**Decision:** Not preferred. Prompt engineering is sufficient.

---

## Open Questions

1. **Should we add a memory review tool?**
   - Letta doesn't have this, but it could help agents reflect on what they've learned
   - Could be implemented as `archival_memory_search(query="summary about user")`

2. **Should we add memory pruning?**
   - Letta doesn't mention this, but memory could grow stale
   - Could implement as a tool or automatic process

3. **How do we measure success?**
   - Need to define "good" memory formation
   - May need user studies or A/B testing

4. **Should this be configurable?**
   - Some users may want more/less active memory management
   - Could add a setting for memory "aggressiveness"

---

## Dependencies

### Required

- None (standalone enhancement)

### Optional (Enhances Value)

- **Character Limits (from main PRD Phase 1):** Helps agent know how much space is available
- **Memory Insert Tool (from main PRD Phase 1):** Safer than replace for additive changes
- **Archival Search Enhancements (from main PRD Phase 3):** Better retrieval of saved memories

---

## Timeline

| Task | Duration | Dependencies |
|------|----------|--------------|
| Phase 1: Tool Descriptions | 1 hour | None |
| Phase 2: Block Descriptions | 30 min | None |
| Phase 3: System Prompt | 1 hour | Phases 1-2 |
| Phase 4: Testing | 2 hours | Phases 1-3 |
| Phase 5: Autonomous Block Creation | 2 hours | Phases 1-4 |
| **Total** | **~6 hours** | - |

---

## Conclusion

This PRD documents Letta's "active memory management" capability—a relatively simple but powerful feature achieved primarily through **prompt engineering** rather than complex architecture.

**Key Takeaway:** Letta's "self-learning" is not ML or RL—it's just **tool-based memory formation** enhanced through prescriptive descriptions and strong prompting.

**Implementation Effort:** LOW (~6 hours, including new `memory_create` tool)
**Impact:** HIGH (agent becomes more personalized and helpful over time)
**Risk:** LOW (reversible, no breaking changes)

**Recommendation:** Implement as a "quick win" before tackling more complex features like shared memory or compaction. The enhanced prompts will also benefit those features when they're implemented.

**What's New (v1.1):**
- Added **Phase 5: Autonomous Block Creation** based on deeper Letta research
- The `memory_create` tool enables agents to create entirely new memory blocks on-the-fly
- Decision tree guidance helps agents decide when to create blocks vs. use existing ones
- Total implementation time updated from ~4 hours to ~6 hours

---

## Appendix: Example Tool Definitions (Complete)

```python
def get_tools_definitions(self) -> List[Dict]:
    return [
        {
            "name": "memory_create",
            "description": """
Create a new memory block when existing blocks don't cover a category of information.

**When to create a new block:**
- Information doesn't logically fit in 'human', 'persona', or 'system_rules'
- Information represents a distinct category that will be referenced frequently
- Existing blocks are becoming cluttered with unrelated information
- A dedicated block would improve organization and retrieval

**Examples of when to create:**
- User discusses a specific project extensively → Create 'project_{name}' block
- User mentions their team members frequently → Create 'team' block
- Agent learns about recurring workflows → Create 'workflows' block
- User has distinct roles (work vs personal) → Create separate context blocks

**Block naming conventions:**
- Use snake_case: 'project_acme', 'user_team', 'daily_workflow'
- Be descriptive but concise
- Consider prefixing for organization: 'project_*', 'team_*'

**Remember:** Only create new blocks when truly necessary. Most information belongs
in existing blocks or in archival memory.
            """.strip(),
            "input_schema": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "The unique label for the new block (use snake_case, e.g., 'project_acme')"
                    },
                    "value": {
                        "type": "string",
                        "description": "Initial content for the block (optional, can be updated later)",
                        "default": ""
                    },
                    "description": {
                        "type": "string",
                        "description": "What this block is for - helps guide how it's used",
                        "default": ""
                    }
                },
                "required": ["label"]
            }
        },
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
- User: "Turns out I prefer tea over coffee" → Update 'human' block with new preference

**Remember:** Good memory management makes you more helpful and personalized. Update memory
whenever you learn something worth remembering.
            """.strip(),
            "input_schema": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "The label of the block to update (e.g. 'human', 'persona', 'system_rules')"
                    },
                    "new_value": {
                        "type": "string",
                        "description": "The new content for the block. This completely replaces existing content."
                    }
                },
                "required": ["label", "new_value"]
            }
        },
        {
            "name": "core_memory_append",
            "description": """
Add information to the end of a Core Memory block without overwriting existing content.

**Use append when:**
- Adding new information that doesn't replace existing information
- Building up a list or collection of related items
- You want to preserve what's already there and add to it

**Examples:**
- User: "I also like hiking and photography" → Append to 'human' block preferences
- User: "One more thing: I use Linux for development" → Append to 'system_rules' block
- User: "I have a cat named Mittens" → Append to 'human' block

**When to use append vs replace:**
- Use REPLACE when updating or changing existing information
- Use APPEND when adding new, unrelated information
            """.strip(),
            "input_schema": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "The label of the block (e.g. 'human', 'persona')"
                    },
                    "text_to_append": {
                        "type": "string",
                        "description": "Text to add to the end of the block."
                    }
                },
                "required": ["label", "text_to_append"]
            }
        },
        {
            "name": "archival_memory_insert",
            "description": """
Save detailed information to long-term archival memory for later retrieval.

**Use archival memory for:**
- Detailed project information (requirements, specs, decisions)
- Extended conversation context (meeting notes, discussion outcomes)
- Technical documentation (API references, code examples)
- Historical events and milestones
- Information too large for core memory blocks

**Tags are important!** Use descriptive tags to organize memories:
- Personal: `user_info`, `preference`, `background`
- Project: `project`, `requirement`, `decision`, `bug`
- Technical: `documentation`, `api`, `reference`, `code`

**Examples:**
- "User is building a REST API using FastAPI" → Insert with tags: `project`, `fastapi`, `api`
- "Meeting outcome: decided to use PostgreSQL" → Insert with tags: `meeting`, `decision`, `database`
- "User mentioned they have a PhD in Computer Science" → Insert with tags: `user_info`, `background`

**Retrieval:** You can search archival memory later using `archival_memory_search`.

**NOTE:** Core memory is for important, frequently-accessed information. Archival memory is
for detailed information that you might need later but don't need always visible.
            """.strip(),
            "input_schema": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The detailed content to store."
                    },
                    "tags": {
                        "type": "string",
                        "description": "Comma-separated tags for categorization (e.g., 'user_info,preference')"
                    }
                },
                "required": ["content"]
            }
        },
        {
            "name": "archival_memory_search",
            "description": """
Search long-term archival memory using semantic search.

**Use this when:**
- You need to recall detailed information from past conversations
- You're looking for specific details that aren't in core memory
- You want to review what you know about a topic

**Search tips:**
- Use natural language queries (e.g., "what did we decide about the database")
- You can search for concepts, not just exact words
- Results are ranked by semantic relevance

**Example queries:**
- "What are the user's technical skills?"
- "What did we decide about the API framework?"
- "What are the project requirements?"
            """.strip(),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The semantic query string."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 5)."
                    }
                },
                "required": ["query"]
            }
        }
    ]
```

---

**Document Status:** Ready for Review
**Next Steps:** Await approval before implementation