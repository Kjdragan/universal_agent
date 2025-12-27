# 034 Letta Memory System Manual

**Date:** December 26, 2025
**Status:** Implemented & Active
**Module:** `Memory_System`

## 1. Executive Summary

We have upgraded the Universal Agent with a **Letta-inspired Persistent Memory System**. Ideally, the agent is no longer "amnesic" between sessions. It now possesses a persistent "state" that loads every time it wakes up.

This system gives the agent:
1.  **Identity & Rules that stick** (Core Memory).
2.  **Long-term recall of user facts** (Core Memory).
3.  **Searchable knowledge base** (Archival Memory).

---

## 2. The Two Types of Memory

Everything is stored locally in `Memory_System_Data/`. No external cloud services are required.

### A. Core Memory (The "Hacker's RAM")
**Storage:** SQLite
**Behavior:** Always visible in the System Prompt.
**Constraint:** Limited size (should fit in context window).

Core Memory is divided into "Blocks". By default, we have three:
1.  `[PERSONA]`: Who the agent is.
2.  `[HUMAN]`: Who the user is (names, preferences, location).
3.  `[SYSTEM_RULES]`: Technical constraints (e.g., "Use `uv`", "Use `Logfire`").

### B. Archival Memory (The "Library")
**Storage:** ChromaDB (Vector Database)
**Behavior:** Hidden by default. Accessed via Search Tools.
**Capacity:** Infinite.

Used for storing documentation, meeting notes, large code snippets, or logs that the agent might need to *look up* later, but doesn't need to know every second.

---

## 3. How to Populate & Use the Memory

You do **not** need to write SQL or Python to populate the memory. You interact with it naturally through the agent.

### Method A: Natural Language Teaching (Preferred)
Just tell the agent facts. It has tools (`core_memory_replace`, `core_memory_append`) that it *decides* to use when it hears something important.

**Examples:**
*   **User:** "My name is Kevin and I live in Chicago."
    *   **Agent Action:** Calls `core_memory_replace('human', ...)`
    *   **Result:** Next time you open a session, the agent sees: `User: Kevin, Location: Chicago`.
*   **User:** "Always use `pytest` for testing, never `unittest`."
    *   **Agent Action:** Calls `core_memory_append('system_rules', 'Testing: Always use pytest')`.
    *   **Result:** This rule becomes a permanent law for the agent.

### Method B: Explicit Commands
You can order the agent to save specific data to Archival Memory.

**Examples:**
*   **User:** "Save this entire conversation explanation to your archival memory so you remember how the system works."
    *   **Agent Action:** Calls `archival_memory_insert(content="...", tags=["system_docs"])`.
*   **User:** "Read `project_roadmap.md` and memorize the key dates."
    *   **Agent Action:** Reads file -> Calls `archival_memory_insert`.

### Method C: Recall & Search
You can ask the agent to remember things from the past.

**Examples:**
*   **User:** "What is my tech stack preference?"
    *   **Agent Logic:** Checks Core Memory `[SYSTEM_RULES]`. If not there, calls `archival_memory_search("tech stack preference")`.
*   **User:** "What did we do regarding the PDF feature last week?"
    *   **Agent Action:** Calls `archival_memory_search("PDF feature last week")`.

---

## 4. Technical Maintenance

### File Locations
*   **Database**: `universal_agent/Memory_System_Data/`
    *   `agent_core.db`: SQLite file (viewable with any DB viewer).
    *   `chroma_db/`: Folder containing vector indices.

### Resetting Memory
To wipe the agent's brain and start fresh:
1.  Stop the agent.
2.  Delete the `Memory_System_Data/` directory.
3.  Restart the agent. It will regenerate the default "Blank Slate" blocks.

### Debugging
You can ask the agent: *"Dump your core memory blocks"* to see exactly what it currently believes to be true. It will use the `get_core_memory_blocks` tool to show you the raw state.
