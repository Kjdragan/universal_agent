# Memory System Developer Guide

This guide provides comprehensive instructions for developers working with the Universal Agent Memory System. Whether you're extending functionality, debugging issues, or integrating with other components, this guide will help you understand and work with the memory system effectively.

## Getting Started

### Prerequisites

- Python 3.12+
- Understanding of Pydantic models
- Basic knowledge of SQLite and vector databases
- Familiarity with the Universal Agent architecture

### Development Setup

1. **Install Dependencies**
```bash
# Core dependencies
pip install chromadb pydantic

# Development dependencies
pip install pytest black isort mypy
```

2. **Directory Structure**
```
Memory_System/
├── __init__.py
├── manager.py          # Memory manager implementation
├── models.py          # Pydantic data models
├── storage.py         # Storage backends
└── tools.py           # Tool definitions
```

3. **Running Tests**
```bash
# Run all tests
python -m pytest tests/test_memory_system.py -v

# Run with coverage
python -m pytest tests/test_memory_system.py --cov=Memory_System

# Run specific test
python -m pytest tests/test_memory_system.py::TestMemorySystem::test_core_memory_edit
```

## Code Structure Deep Dive

### MemoryManager Class

The `MemoryManager` is the central orchestrator that coordinates between the agent and storage backends.

```python
class MemoryManager:
    def __init__(self, storage_dir: str = "Memory_System/data"):
        self.storage = StorageManager(storage_dir)
        self.agent_state = self._load_or_initialize_state()
```

#### Key Methods

##### Initialization Pattern
```python
def _load_or_initialize_state(self) -> AgentState:
    """
    Load agent state from storage, or initialize defaults if empty.
    Mirroring Letta, we allow for multiple Core Memory blocks.
    """
    blocks = self.storage.get_core_memory()

    if not blocks:
        # Create default blocks
        persona = MemoryBlock(...)
        # ... other defaults
        return AgentState(core_memory=blocks)

    return AgentState(core_memory=blocks)
```

##### System Prompt Injection
```python
def get_system_prompt_addition(self) -> str:
    """
    Format the Core Memory blocks for injection into the System Prompt.
    This provides the 'Context Link' that makes the agent stateful.
    """
    prompt_lines = ["\n# 🧠 CORE MEMORY (Always Available)"]

    for block in self.agent_state.core_memory:
        prompt_lines.append(f"\n## [{block.label.upper()}]")
        prompt_lines.append(f"{block.value}")

    return "\n".join(prompt_lines)
```

##### Memory Operations
```python
def core_memory_replace(self, label: str, new_value: str) -> str:
    """Tool: Overwrite a specific memory block."""
    # Find block
    block = next((b for b in self.agent_state.core_memory if b.label == label), None)
    if not block:
        return f"Error: Memory block '{label}' not found..."

    if not block.is_editable:
        return f"Error: Memory block '{label}' is read-only."

    # Update
    old_value = block.value
    block.value = new_value
    block.last_updated = datetime.now()

    # Persist
    self.storage.save_block(block)

    return f"✅ Successfully updated '{label}' block..."
```

### StorageManager Class

The `StorageManager` implements the hybrid storage strategy using SQLite for core memory and ChromaDB for archival memory.

#### SQLite Core Memory Implementation

```python
def get_core_memory(self) -> List[MemoryBlock]:
    """Retrieve all core memory blocks."""
    conn = sqlite3.connect(self.sqlite_path)
    cursor = conn.cursor()

    cursor.execute("SELECT label, value, description, is_editable, last_updated FROM core_blocks")
    rows = cursor.fetchall()
    conn.close()

    blocks = []
    for r in rows:
        blocks.append(MemoryBlock(
            label=r[0],
            value=r[1],
            description=r[2],
            is_editable=bool(r[3]),
            last_updated=datetime.fromisoformat(r[4]) if r[4] else datetime.now()
        ))
    return blocks
```

#### ChromaDB Archival Memory Implementation

```python
def insert_archival(self, item: ArchivalItem) -> str:
    """Insert an item into archival memory."""
    item_id = item.item_id or str(uuid.uuid4())

    # Metadata allows filtering by tags later
    metadata = {
        "timestamp": item.timestamp.isoformat(),
        "tags": ",".join(item.tags)
    }

    self.collection.add(
        documents=[item.content],
        metadatas=[metadata],
        ids=[item_id]
    )

    return item_id

def search_archival(self, query: str, limit: int = 5) -> List[ArchivalItem]:
    """Semantic search for archival items."""
    results = self.collection.query(
        query_texts=[query],
        n_results=limit
    )

    items = []
    if results['ids'] and len(results['ids']) > 0:
        # Process results and convert to ArchivalItems
        # ...

    return items
```

## Data Models

### MemoryBlock Model

```python
class MemoryBlock(BaseModel):
    """Represents a Core Memory block (in-context)."""
    label: str
    value: str
    is_editable: bool = True
    description: Optional[str] = None
    last_updated: datetime = Field(default_factory=datetime.now)
```

**Key Fields:**
- `label`: Unique identifier for the memory block
- `value`: The actual content/information
- `is_editable`: Controls whether the block can be modified
- `description`: Human-readable description
- `last_updated`: Timestamp of last modification

### ArchivalItem Model

```python
class ArchivalItem(BaseModel):
    """Represents a single item in Archival Memory (out-of-context)."""
    content: str
    tags: List[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.now)
    item_id: Optional[str] = None
```

**Key Fields:**
- `content`: The information to store
- `tags`: List of tags for categorization and filtering
- `timestamp`: When the item was created
- `item_id`: UUID assigned by ChromaDB

## Tool Integration

### Tool Registration Pattern

The memory system exposes tools to the agent through a standardized interface:

```python
def get_tools_definitions(self) -> List[Dict]:
    """Return standard tool definitions for Claude/Composio."""
    return [
        {
            "name": "core_memory_replace",
            "description": "Overwrite a Core Memory block...",
            "input_schema": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "..."},
                    "new_value": {"type": "string", "description": "..."}
                },
                "required": ["label", "new_value"]
            }
        },
        # ... other tools
    ]
```

### Tool Execution Mapping

Tools are mapped to executable functions:

```python
def get_memory_tool_map(manager: MemoryManager) -> Dict[str, Any]:
    """Returns a dictionary mapping tool names to executable functions."""
    return {
        "core_memory_replace": manager.core_memory_replace,
        "core_memory_append": manager.core_memory_append,
        "archival_memory_insert": manager.archival_memory_insert,
        "archival_memory_search": manager.archival_memory_search
    }
```

## Debugging and Troubleshooting

### Common Issues and Solutions

#### 1. Import Errors

**Problem**: `ModuleNotFoundError: No module named 'Memory_System'`

**Solution**: Ensure the module is properly in the Python path:

```python
# In main.py or similar
import sys
import os
sys.path.append(os.path.abspath("."))

# Then import
from Memory_System.manager import MemoryManager
```

#### 2. SQLite Permission Issues

**Problem**: `sqlite3.OperationalError: unable to open database file`

**Solution**: Check directory permissions:

```python
import os

# Ensure directory exists and is writable
storage_dir = "Memory_System/data"
os.makedirs(storage_dir, exist_ok=True)
os.chmod(storage_dir, 0o755)
```

#### 3. ChromaDB Connection Issues

**Problem**: ChromaDB fails to initialize or connect

**Solution**: Reset ChromaDB collection:

```python
# Delete the chroma_db directory to reset
import shutil
shutil.rmtree("Memory_System/data/chroma_db", ignore_errors=True)
```

#### 4. Memory Corruption

**Problem**: Memory blocks contain stale or incorrect data

**Solution**: Check database integrity and restore from defaults:

```python
def reset_memory_system(manager: MemoryManager):
    """Reset memory system to default state."""
    # Clear existing data
    manager.storage.get_core_memory().clear()

    # Reinitialize
    manager.agent_state = manager._load_or_initialize_state()
```

### Debugging Techniques

#### Enable Logging

```python
import logging

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Memory system logging
logger = logging.getLogger('MemorySystem')
logger.debug("Loading memory manager...")
```

#### Database Inspection

Inspect SQLite database directly:

```python
import sqlite3

def inspect_memory_db(db_path: str):
    """Inspect the SQLite memory database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Check core blocks
    cursor.execute("SELECT * FROM core_blocks")
    print("Core Blocks:")
    for row in cursor.fetchall():
        print(f"  {row[0]}: {row[1][:50]}...")

    conn.close()
```

#### ChromaDB Inspection

```python
def inspect_chromadb(chroma_path: str):
    """Inspect ChromaDB collection."""
    from chromadb import PersistentClient

    client = PersistentClient(path=chroma_path)
    collection = client.get_collection("archival_memory")

    # Get collection stats
    count = collection.count()
    print(f"Archival items: {count}")

    # Sample query
    results = collection.query(query_texts=["test"], n_results=1)
    print(f"Sample results: {results}")
```

## Testing Strategies

### Unit Testing

#### Testing Memory Operations

```python
def test_core_memory_update():
    """Test core memory update functionality."""
    # Setup
    test_dir = "test_memory_data"
    if os.path.exists(test_dir):
        shutil.rmtree(test_dir)

    manager = MemoryManager(storage_dir=test_dir)

    # Test update
    result = manager.core_memory_replace("human", "Name: Test User")
    assert "Successfully updated" in result

    # Verify persistence
    new_manager = MemoryManager(storage_dir=test_dir)
    human_block = new_manager.get_memory_block("human")
    assert "Test User" in human_block.value

    # Cleanup
    shutil.rmtree(test_dir)
```

#### Testing Archival Search

```python
def test_archival_search():
    """Test archival memory search functionality."""
    # Setup
    test_dir = "test_memory_data"
    if os.path.exists(test_dir):
        shutil.rmtree(test_dir)

    manager = MemoryManager(storage_dir=test_dir)

    # Insert test data
    manager.archival_memory_insert("The user prefers Python over JavaScript", tags="preference")
    manager.archival_memory_insert("Python 3.12 is the current version", tags="technology")

    # Search
    results = manager.archival_memory_search("programming language", limit=1)
    assert len(results) == 1
    assert "Python" in results[0].content

    # Cleanup
    shutil.rmtree(test_dir)
```

### Integration Testing

#### Agent Integration Test

```python
def test_memory_integration():
    """Test memory integration with main agent."""
    # Initialize memory
    mem_mgr = MemoryManager()

    # Get system prompt addition
    context = mem_mgr.get_system_prompt_addition()

    # Verify context contains expected blocks
    assert "PERSONA" in context
    assert "HUMAN" in context
    assert "SYSTEM_RULES" in context

    # Verify memory is persisted
    new_mgr = MemoryManager()
    assert len(new_mgr.agent_state.core_memory) > 0
```

## Performance Optimization

### SQLite Optimization

1. **Enable WAL Mode**
```python
def _init_sqlite(self):
    conn = sqlite3.connect(self.sqlite_path)
    cursor = conn.cursor()

    # Enable WAL for better concurrent access
    cursor.execute("PRAGMA journal_mode=WAL")

    # Set synchronous mode to NORMAL for performance
    cursor.execute("PRAGMA synchronous=NORMAL")

    conn.commit()
    conn.close()
```

2. **Connection Management**
```python
# Use connection pooling if needed
class SQLiteConnectionPool:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._pool = []

    def get_connection(self):
        if not self._pool:
            return sqlite3.connect(self.db_path)
        return self._pool.pop()

    def return_connection(self, conn):
        self._pool.append(conn)
```

### ChromaDB Optimization

1. **Batch Operations**
```python
def batch_insert_archival(self, items: List[ArchivalItem]) -> List[str]:
    """Insert multiple archival items efficiently."""
    ids = []
    documents = []
    metadatas = []

    for item in items:
        item_id = item.item_id or str(uuid.uuid4())
        ids.append(item_id)
        documents.append(item.content)

        metadata = {
            "timestamp": item.timestamp.isoformat(),
            "tags": ",".join(item.tags)
        }
        metadatas.append(metadata)

    self.collection.add(
        documents=documents,
        metadatas=metadatas,
        ids=ids
    )

    return ids
```

2. **Search Optimization**
```python
def optimized_search(self, query: str, tags: List[str] = None, limit: int = 5):
    """Search with optional tag filtering for better performance."""
    where_clause = {}

    if tags:
        where_clause = {"tags": {"$in": tags}}

    results = self.collection.query(
        query_texts=[query],
        n_results=limit,
        where=where_clause
    )

    return results
```

## Best Practices

### Memory Management

1. **Keep Core Memory Concise**
   - Core memory is injected into every system prompt
   - Keep blocks under 1KB each
   - Use archival memory for large content

2. **Use Memory Effectively**
   - Core memory: Critical, frequently accessed information
   - Archival memory: Historical facts, documents, events
   - Update memory as the agent learns new information

3. **Tag Strategy**
   - Use consistent, hierarchical tags
   - Example: `project/coding`, `user/preference`, `context/meeting`
   - Use tags to filter archival searches

### Code Quality

1. **Type Hints**
   - Use type hints for all public methods
   - Import from `typing` module
   - Use Union, Optional, List, Dict appropriately

2. **Error Handling**
   - Handle SQLite and ChromaDB specific errors
   - Provide meaningful error messages
   - Log errors for debugging

3. **Documentation**
   - Document all public methods
   - Include parameter descriptions
   - Provide usage examples

### Security Considerations

1. **Input Validation**
   - Validate memory content before storage
   - Sanitize user inputs
   - Prevent injection attacks

2. **Data Privacy**
   - Consider sensitivity of stored information
   - Implement proper access controls
   - Follow data retention policies

## Contributing

### Development Workflow

1. **Create Feature Branch**
```bash
git checkout -b feature/memory-improvement
```

2. **Implement Changes**
   - Write tests for new functionality
   - Follow coding standards
   - Update documentation

3. **Run Tests**
```bash
python -m pytest tests/test_memory_system.py
python -m black Memory_System/
python -m isort Memory_System/
```

4. **Submit Pull Request**
   - Include test results
   - Update documentation
   - Add performance metrics if applicable

### Code Review Checklist

- [ ] Tests pass and have good coverage
- [ ] Code follows style guidelines
- [ ] Documentation is updated
- [ ] Performance impact considered
- [ ] Security implications reviewed
- [ ] Backward compatibility maintained

This developer guide should provide you with everything needed to work effectively with the memory system. For additional questions or specific use cases, refer to the main README and architecture documentation.