# Memory System Usage Examples

This document provides practical examples of how to use the Universal Agent Memory System in various scenarios. Each example includes code snippets and explanations for common use cases.

## Basic Usage Examples

### 1. Initializing the Memory System

```python
from Memory_System.manager import MemoryManager
from Memory_System.models import MemoryBlock, ArchivalItem
import shutil
import os

# Clean up any existing test data
test_dir = "memory_test_data"
if os.path.exists(test_dir):
    shutil.rmtree(test_dir)

# Initialize memory manager
mem_mgr = MemoryManager(storage_dir=test_dir)

print("Memory system initialized successfully!")
print(f"Core memory blocks: {[b.label for b in mem_mgr.agent_state.core_memory]}")
```

### 2. Accessing Core Memory

```python
# Get all core memory blocks
print("Core Memory Blocks:")
for block in mem_mgr.agent_state.core_memory:
    print(f"- {block.label}: {block.value[:50]}...")

# Get a specific block
persona_block = mem_mgr.get_memory_block("persona")
if persona_block:
    print(f"\nPersona: {persona_block.value}")

# Access programmatic methods
human_block = mem_mgr.get_memory_block("human")
print(f"Human preferences: {human_block.value}")
```

### 3. Modifying Core Memory

```python
# Replace a memory block
result = mem_mgr.core_memory_replace(
    "human",
    "Name: Alice Johnson\nRole: Senior Developer\nLocation: San Francisco\nLikes: Python, Coffee, Hiking"
)
print(result)

# Append to a memory block
append_result = mem_mgr.core_memory_append(
    "system_rules",
    "\nPreferred IDE: VS Code\nCode Style: Black formatter"
)
print(append_result)

# Verify the changes
updated_human = mem_mgr.get_memory_block("human")
print(f"\nUpdated human info: {updated_human.value}")
```

### 4. Working with Archival Memory

```python
# Insert items into archival memory
mem_mgr.archival_memory_insert(
    "User mentioned they're working on a machine learning project",
    tags="project,ml,context"
)

mem_mgr.archival_memory_insert(
    "User prefers async/await patterns in Python",
    tags="preference,programming,async"
)

mem_mgr.archival_memory_insert(
    "Project deadline is March 15, 2025",
    tags="deadline,project"
)

# Search archival memory
print("\nSearch Results:")
results = mem_mgr.archival_memory_search("machine learning project", limit=3)
for i, item in enumerate(results, 1):
    print(f"{i}. {item.content[:60]}... (Tags: {', '.join(item.tags)})")
```

## Advanced Usage Examples

### 1. Custom Memory Block Types

```python
# Create custom memory blocks
custom_blocks = [
    MemoryBlock(
        label="current_project",
        value="Building a memory system for AI agents",
        description="Current main project focus",
        is_editable=True
    ),
    MemoryBlock(
        label="technical_constraints",
        value="Python 3.12+, UV package manager, Async patterns required",
        description="Technical environment constraints",
        is_editable=False  # Read-only
    ),
    MemoryBlock(
        label="user_goals",
        value="Learn about AI agent architecture and build production-ready systems",
        description="User's main objectives",
        is_editable=True
    )
]

# Save custom blocks
for block in custom_blocks:
    mem_mgr.storage.save_block(block)

# Verify custom blocks
print("\nCustom Memory Blocks:")
for block in mem_mgr.agent_state.core_memory:
    if block.label in ["current_project", "technical_constraints", "user_goals"]:
        print(f"- {block.label}: {block.value}")
```

### 2. Batch Operations

```python
import time

# Batch archival inserts
batch_items = [
    ArchivalItem(
        content=f"User completed task {i} on {time.strftime('%Y-%m-%d')}",
        tags=["task", "completed", f"batch_{i}"]
    )
    for i in range(1, 11)
]

# Insert one by one (simple approach)
start_time = time.time()
for item in batch_items:
    mem_mgr.archival_memory_insert(item.content, ",".join(item.tags))
batch_time = time.time() - start_time
print(f"Batch insert completed in {batch_time:.2f} seconds")

# Search for recent completions
recent_tasks = mem_mgr.archival_memory_search("completed task", limit=5)
print(f"\nFound {len(recent_tasks)} recent task completions")
```

### 3. Semantic Search with Tags

```python
# Insert diverse tagged content
docs_to_store = [
    ("The user is allergic to peanuts", ["health", "allergy", "peanuts"]),
    ("Project deadline is Q1 2025", ["project", "deadline", "2025"]),
    ("User prefers React over Vue", ["preference", "frontend", "react"]),
    ("Database uses PostgreSQL", ["technology", "database", "postgresql"]),
    ("User has PhD in Computer Science", ["background", "education", "phd"])
]

for content, tags in docs_to_store:
    mem_mgr.archival_memory_insert(content, ",".join(tags))

# Perform filtered searches
print("\nSemantic Search Results:")

# Search with tag filter
tech_results = mem_mgr.archival_memory_search("database technology", limit=3)
print("Technology-related:")
for item in tech_results:
    print(f"  - {item.content}")

# Search without filter
all_results = mem_mgr.archival_memory_search("user", limit=5)
print(f"\nAll 'user' results ({len(all_results)} found):")
for item in all_results:
    print(f"  - {item.content[:50]}... (Tags: {', '.join(item.tags)})")
```

### 4. Memory System with Agent Context

```python
# Simulate agent workflow
def simulate_agent_interaction():
    """Simulate how an agent would use memory."""
    print("\n=== Agent Interaction Simulation ===")

    # Agent gets system prompt with memory context
    system_prompt = f"You are an AI assistant.\n{mem_mgr.get_system_prompt_addition()}"
    print("System prompt with memory:")
    print("-" * 50)
    print(system_prompt[:200] + "..." if len(system_prompt) > 200 else system_prompt)

    # Agent processes user input and learns new information
    user_input = "I'm allergic to penicillin and love working with Go"

    # Extract and store preferences
    if "allergic to penicillin" in user_input:
        mem_mgr.core_memory_replace(
            "human",
            mem_mgr.get_memory_block("human").value + "\nAllergy: Penicillin"
        )

    if "love working with Go" in user_input:
        mem_mgr.archival_memory_insert(
            "User expressed interest in Go programming language",
            tags="preference", "programming", "go"
        )

    print("\nAgent updated memory based on user input")

# Run the simulation
simulate_agent_interaction()
```

## Integration Examples

### 1. Memory System with Composio Tools

```python
from typing import Dict, Any

def integrate_with_composio(memory_manager: MemoryManager) -> Dict[str, Any]:
    """Integrate memory system with Composio tool execution."""

    # Get memory tools
    memory_tools = memory_manager.get_tools_definitions()

    # Create tool map for execution
    tool_map = {
        "core_memory_replace": memory_manager.core_memory_replace,
        "core_memory_append": memory_manager.core_memory_append,
        "archival_memory_insert": memory_manager.archival_memory_insert,
        "archival_memory_search": memory_manager.archival_memory_search
    }

    return {
        "tool_definitions": memory_tools,
        "tool_map": tool_map,
        "system_prompt_addition": memory_manager.get_system_prompt_addition()
    }

# Integration example
integration = integrate_with_composio(mem_mgr)
print(f"\nIntegration ready with {len(integration['tool_definitions'])} memory tools")
```

### 2. Memory System with Multi-Agent workflows

```python
class AgentWithMemory:
    """Example agent with memory integration."""

    def __init__(self, memory_manager: MemoryManager, agent_name: str):
        self.memory = memory_manager
        self.name = agent_name
        self.trace_processed = set()

    def process_task(self, task: str, trace_id: str) -> str:
        """Process a task with memory integration."""

        # Check if task already processed
        if self.memory.has_trace_been_processed(trace_id):
            return "Task already completed - retrieving from memory"

        # Store task context
        self.memory.archival_memory_insert(
            f"Processing task: {task}",
            tags=["task", self.name.lower(), "active"]
        )

        # Process task (simplified)
        result = f"Agent {self.name} processed: {task}"

        # Mark as processed
        self.memory.mark_trace_processed(trace_id)

        return result

# Test multi-agent memory usage
agent1 = AgentWithMemory(mem_mgr, "Coder")
agent2 = AgentWithMemory(mem_mgr, "Reviewer")

# Process tasks
trace1 = "task_001"
trace2 = "task_002"

print(f"\nMulti-agent memory usage:")
result1 = agent1.process_task("Write user authentication module", trace1)
print(f"Agent 1: {result1}")

result2 = agent2.process_task("Review code implementation", trace2)
print(f"Agent 2: {result2}")

# Check trace processing
print(f"\nTrace {trace1} processed: {agent1.memory.has_trace_been_processed(trace1)}")
print(f"Trace {trace2} processed: {agent2.memory.has_trace_been_processed(trace2)}")
```

### 3. Memory System with File Operations

```python
def process_document_with_memory(memory_manager: MemoryManager, file_path: str):
    """Process a document and extract information for memory."""

    # Read document (simulated)
    document_content = f"""
    User Meeting Notes - {time.strftime('%Y-%m-%d')}

    Discussed new project requirements:
    - Build a recommendation engine
    - Use collaborative filtering
    - Target completion: June 2025

    User preferences:
    - Prefers Python backend
    - Interested in machine learning
    - Available for weekly meetings
    """

    # Store document in archival memory
    doc_id = memory_manager.archival_memory_insert(
        document_content,
        tags="meeting", "notes", "project", "recommendation_engine"
    )
    print(f"Document stored with ID: {doc_id}")

    # Extract key information and update core memory
    if "prefers Python backend" in document_content:
        current_prefs = memory_manager.get_memory_block("human").value
        memory_manager.core_memory_replace(
            "human",
            current_prefs + "\nBackend preference: Python"
        )

    return doc_id

# Process a document
doc_id = process_document_with_memory(mem_mgr, "meeting_notes.txt")
print(f"\nUpdated human preferences: {mem_mgr.get_memory_block('human').value}")
```

## Error Handling Examples

### 1. Graceful Error Handling

```python
def safe_memory_operations(memory_manager: MemoryManager):
    """Demonstrate safe memory operations with error handling."""

    try:
        # Attempt to update a non-existent block
        result = memory_manager.core_memory_replace(
            "nonexistent_block",
            "This should fail"
        )
        print(f"Update result: {result}")
    except Exception as e:
        print(f"Error updating memory: {e}")

    try:
        # Attempt to search archival memory
        results = memory_manager.archival_memory_search("test query")
        print(f"Search found {len(results)} results")
    except Exception as e:
        print(f"Error searching archival memory: {e}")

    # Safe way to check if block exists
    block = memory_manager.get_memory_block("persona")
    if block:
        print(f"Persona block exists and is editable: {block.is_editable}")
    else:
        print("Persona block not found")

# Test safe operations
safe_memory_operations(mem_mgr)
```

### 2. Memory System Recovery

```python
def recover_memory_system(memory_manager: MemoryManager):
    """Demonstrate memory system recovery procedures."""

    print("\n=== Memory Recovery Example ===")

    # Check current state
    print(f"Current core memory blocks: {[b.label for b in memory_manager.agent_state.core_memory]}")

    # Simulate corruption by removing a block
    if memory_manager.get_memory_block("human"):
        memory_manager.storage.get_core_memory().clear()
        print("Simulated memory corruption - cleared core memory")

    # Recovery: Reinitialize from storage
    try:
        # Try to reload from storage
        new_manager = MemoryManager(storage_dir=test_dir)
        print(f"Recovered memory blocks: {[b.label for b in new_manager.agent_state.core_memory]}")

        # If recovery fails, reset to defaults
        if not new_manager.agent_state.core_memory:
            print("Recovery failed - resetting to defaults")
            memory_manager.agent_state = memory_manager._load_or_initialize_state()
            print(f"Default memory blocks restored: {[b.label for b in memory_manager.agent_state.core_memory]}")

    except Exception as e:
        print(f"Recovery failed with error: {e}")
        # Last resort: manual reset
        memory_manager.agent_state = memory_manager._load_or_initialize_state()

# Test recovery
recover_memory_system(mem_mgr)
```

## Performance Examples

### 1. Memory Performance Testing

```python
import time

def test_memory_performance(memory_manager: MemoryManager):
    """Test memory system performance."""

    print("\n=== Performance Testing ===")

    # Test core memory performance
    start_time = time.time()
    for i in range(100):
        memory_manager.core_memory_append(
            "test_performance",
            f"Test data {i}"
        )
    core_time = time.time() - start_time

    # Test archival memory performance
    start_time = time.time()
    for i in range(50):
        memory_manager.archival_memory_insert(
            f"Archival test data {i}",
            tags="performance", f"test_{i}"
        )
    archival_time = time.time() - start_time

    # Test search performance
    start_time = time.time()
    for i in range(10):
        memory_manager.archival_memory_search("test", limit=5)
    search_time = time.time() - start_time

    print(f"Core memory operations (100): {core_time:.3f}s")
    print(f"Archival inserts (50): {archival_time:.3f}s")
    print(f"Search operations (10): {search_time:.3f}s")
    print(f"Average per operation: {(core_time + archival_time + search_time)/160:.4f}s")

# Run performance test
test_memory_performance(mem_mgr)
```

## Cleanup

```python
# Clean up test data
if os.path.exists(test_dir):
    shutil.rmtree(test_dir)
    print(f"\nCleaned up test directory: {test_dir}")
```

These examples demonstrate the versatility and practical usage of the Universal Agent Memory System. You can adapt these patterns to your specific needs and integrate memory functionality into your AI agent workflows.