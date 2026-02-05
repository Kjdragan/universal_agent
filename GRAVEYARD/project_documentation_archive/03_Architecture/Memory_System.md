# Memory System Architecture

The Universal Agent implements a **Letta (formerly MemGPT)** inspired memory architecture. This allows the agent to maintain a consistent persona and long-term facts across different sessions.

## Memory Hierarchy

```mermaid
graph TD
    Prompt[System Prompt] -->|Includes| Core[Core Memory]
    Agent -->|Read/Write| Core
    Agent -->|Search/Insert| Archive[Archival Memory]
    
    subgraph "Core Memory (Hot)"
        P[Persona Block]
        H[Human Block]
        S[System Rules]
    end
    
    subgraph "Archival Memory (Cold)"
        V[Vector DB / SQL]
    end
```

### 1. Core Memory (Hot State)
*   **Location**: Directly inside the System Prompt (Context Window).
*   **Capacity**: Limited (approx 2000 chars).
*   **Purpose**: Immediate consistency. Who am I? Who is the user? What are the active constraints?
*   **Editing**: The agent can use `core_memory_replace` to update these blocks in real-time.
    *   *Example*: "User said they live in Chicago" -> Agent updates `Human` block.

### 2. Archival Memory (Cold Storage)
*   **Location**: `Memory_System/data/` (SQLite/JSON).
*   **Capacity**: Unlimited.
*   **Purpose**: Storing facts, report summaries, and previous task outcomes.
*   **Access**: The agent must explicitly *search* this memory using `archival_memory_search`.

## The Memory Lifecycle

```mermaid
sequenceDiagram
    participant A as Agent
    participant M as MemoryManager
    participant D as Storage
    
    Note over A, M: Startup
    M->>D: Load Core Memory
    D-->>M: Return Human/Persona Blocks
    M->>A: Inject into System Prompt
    
    Note over A, M: Runtime
    A->>M: core_memory_replace(label="human", value="Name: Dave...")
    M->>D: Persist Update
    M-->>A: "Success"
    
    Note over A, M: Recall
    A->>M: archival_memory_search(query="previous quantum research")
    M->>D: Semantic Search / SQL
    D-->>M: Results
    M-->>A: Contextual Snippets
```

## Adding New Memory Types
Developers can extend the schema in `Memory_System/manager.py`. Adding a new "Core Block" (e.g., `TaskState`) involves:
1.  Defining the default block in `_load_or_initialize_state`.
2.  The `MemoryManager` will automatically inject it into the prompt.
