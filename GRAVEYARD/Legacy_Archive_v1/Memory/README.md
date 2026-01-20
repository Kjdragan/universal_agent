# Universal Agent Memory System

## Overview

The Universal Agent Memory System is a sophisticated, dual-layer memory architecture designed to provide persistent, contextual memory for AI agents. It combines immediate context awareness with long-term semantic recall, enabling agents to maintain continuity across sessions and learn from interactions.

The system is inspired by [Letta](https://docs.getletta.com/) but implemented with a hybrid storage approach optimized for performance and scalability.

## Architecture

### Dual-Layer Memory Model

```
┌─────────────────────────────────────────────────────────────────┐
│                    Agent Memory System                          │
├─────────────────────────────────────────────────────────────────┤
│  CORE MEMORY (SQLite) - Immediate Context                      │
│  - Always in-system prompt                                   │
│  - Fast access, low latency                                   │
│  - Structured data with edit controls                         │
│                                                             │
│  ARCHIVAL MEMORY (ChromaDB) - Long-term Semantic Storage    │
│  - Out-of-context, vector-searchable                         │
│  - Semantic similarity search                                  │
│  - Tagged metadata for filtering                              │
└─────────────────────────────────────────────────────────────────┘
```

### Core Components

1. **MemoryManager** - High-level controller orchestrating memory operations
2. **StorageManager** - Hybrid storage handling SQLite (core) + ChromaDB (archival)
3. **Models** - Pydantic data models for structured memory blocks
4. **Tools** - Memory management tools exposed to the agent

## Memory Types

### Core Memory (SQLite)
- **Purpose**: Critical, frequently accessed information kept in context
- **Storage**: SQLite database with fast read/write operations
- **Structure**: Labeled memory blocks with metadata
- **Default Blocks**:
  - `persona`: Agent identity and personality
  - `human`: User preferences and facts
  - `system_rules`: Technical constraints and project rules

### Archival Memory (ChromaDB)
- **Purpose**: Long-term storage with semantic search capabilities
- **Storage**: ChromaDB vector database with automatic embeddings
- **Structure**: Content + tags + timestamp
- **Features**: Semantic similarity search, metadata filtering

## Storage Architecture

### Hybrid Storage Strategy

```
Memory_System/data/
├── agent_core.db              # SQLite - Core Memory
│   ├── core_blocks            # Memory blocks
│   └── processed_traces       # Agent College trace tracking
└── chroma_db/                 # ChromaDB - Archival Memory
    └── chroma.sqlite3        # Vector embeddings & metadata
```

### Data Flow

1. **Initialization**: System creates default core blocks if empty
2. **Runtime**: Core memory injected into system prompt
3. **Updates**: Changes persisted immediately to SQLite
4. **Long-term**: Facts/docs moved to archival via semantic similarity
5. **Retrieval**: Searches combine both layers as needed

## Integration with Main Agent

### System Prompt Injection
Core memory is automatically injected into the agent's system prompt:

```python
memory_context_str = mem_mgr.get_system_prompt_addition()
```

Resulting format:
```
# 🧠 CORE MEMORY (Always Available)

## [PERSONA]
I are Antigravity, a powerful agentic AI coding assistant...

## [HUMAN]
Name: User
Preferences: None recorded yet.

## [SYSTEM_RULES]
Package Manager: uv (Always use `uv add`)
OS: Linux
```

### Agent College Integration
The memory system tracks processed traces to prevent redundant work in multi-agent workflows.

## Developer Guide

### Basic Usage

```python
from Memory_System.manager import MemoryManager
from Memory_System.models import MemoryBlock

# Initialize memory manager
mem_mgr = MemoryManager(storage_dir="custom/path")

# Access memory programmatically
persona_block = mem_mgr.get_memory_block("persona")
if persona_block:
    print(persona_block.value)

# Update memory
mem_mgr.update_memory_block("human", "Name: Kevin\nRole: Developer")

# System prompt injection
context = mem_mgr.get_system_prompt_addition()
```

### Extending the Memory System

#### Adding New Memory Block Types
1. Define new block type in `models.py`
2. Add initialization logic in `MemoryManager._load_or_initialize_state()`
3. Update tool definitions if needed

#### Custom Storage Backends
1. Extend `StorageManager` class
2. Implement required methods for the new backend
3. Update `MemoryManager` to use the new storage

#### Adding New Tools
1. Create tool method in `MemoryManager`
2. Add definition in `get_tools_definitions()`
3. Optionally create mapping in `tools.py`

### Tool Reference

#### Core Memory Tools
- `core_memory_replace(label, new_value)` - Overwrite a memory block
- `core_memory_append(label, text_to_append)` - Append to a memory block

#### Archival Memory Tools
- `archival_memory_insert(content, tags)` - Save content to long-term storage
- `archival_memory_search(query, limit)` - Semantic search archival memory

#### Trace Management Tools
- `mark_trace_processed(trace_id)` - Mark trace as processed (Agent College)
- `has_trace_been_processed(trace_id)` - Check if trace was processed

## Performance Considerations

### Core Memory
- **Reads**: Fast SQLite queries for context injection
- **Writes**: Immediate persistence with transaction safety
- **Size**: Keep blocks concise (typically <1KB each)

### Archival Memory
- **Inserts**: Vector embedding generation adds latency (~100-500ms)
- **Searches**: Semantic similarity with configurable limits
- **Size**: Optimized for large documents; use tags for filtering

### Best Practices
1. **Core Memory**: Use for critical, frequently accessed information
2. **Archival Memory**: Use for historical facts, documents, and events
3. **Batch Operations**: Group multiple archival inserts when possible
4. **Tag Strategy**: Use consistent, hierarchical tags (e.g., `project/coding/`)

## Testing

Run the test suite:
```bash
python -m pytest tests/test_memory_system.py
```

Test coverage includes:
- Default memory block initialization
- Core memory persistence and editing
- Archival memory vector search
- Integration with agent workflow

## Troubleshooting

### Common Issues

1. **Import Errors**
   - Ensure `Memory_System` directory is in Python path
   - Use absolute imports: `from Memory_System.manager import MemoryManager`

2. **Storage Permission Issues**
   - Verify write permissions for storage directory
   - Check disk space for SQLite/ChromaDB files

3. **ChromaDB Connection Issues**
   - ChromaDB uses SQLite backend; verify `chroma.sqlite3` isn't corrupted
   - Reinitialize by deleting the chroma_db directory

### Debug Mode

Enable debug logging:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Data Recovery

1. **Core Memory**: SQLite database can be directly queried
2. **Archival Memory**: ChromaDB files can be reinitialized
3. **Backup Strategy**: Regular backup of `Memory_System/data/` directory

## Future Enhancements

### Planned Features
- [ ] Memory summarization and compression
- [ ] Context-aware memory retrieval
- [ ] Memory decay and importance scoring
- [ ] Multi-user memory isolation
- [ ] Export/import memory functionality

### Performance Optimizations
- [ ] Redis caching for frequent reads
- [ ] Asynchronous write operations
- [ ] Connection pooling for SQLite
- [ ] Quantized embeddings for archival memory

## Contributing

### Development Setup
1. Install dependencies: `pip install chromadb pydantic`
2. Run tests: `python -m pytest tests/test_memory_system.py`
3. Follow existing patterns and maintain compatibility

### Code Style
- Use type hints consistently
- Follow Pydantic model patterns
- Document public methods with docstrings
- Maintain separation between controller and storage layers

## References

- [Letta Memory Architecture](https://docs.getletta.com/)
- [ChromaDB Documentation](https://www.trychroma.com/)
- [Pydantic Models](https://docs.pydantic.dev/)
- [SQLite Best Practices](https://www.sqlite.org/docs.html)