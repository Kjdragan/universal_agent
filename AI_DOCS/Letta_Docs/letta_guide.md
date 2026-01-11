# Letta (MemGPT) Usage Guide

## Quickstart

### Installation & Server Start
```bash
pip install letta_client letta
letta quickstart
```

### Create an Agent with Memory (Python)

```python
from letta_client import Letta, CreateBlock

client = Letta(token="YOUR_TOKEN") # Local server usually doesn't need token or uses default

# Check and delete existing agent
agent_name = "memory_agent"
existing = client.agents.list(name=agent_name)
if existing:
    client.agents.delete(agent_id=existing[0].id)

# Create agent with custom memory blocks
agent = client.agents.create(
    name=agent_name,
    model="letta/letta-free", # or gpt-4
    memory_blocks=[
        CreateBlock(label="human", value="Name: User. Context: Developer building agents."),
        CreateBlock(label="persona", value="You are a helpful AI with long-term memory."),
        CreateBlock(label="project_context", value="Current project: Universal Agent integration."),
    ]
)
print(f"Created agent: {agent.id}")
```

## Memory Management

### CRUD Operations on Memory Blocks

```python
# List memory blocks
blocks = client.agents.list_core_memory_blocks(agent_id=agent.id)
for block in blocks:
    print(f"{block.label}: {block.value}")

# Update a block (e.g. updating facts about human)
client.agents.modify_core_memory_block(
    agent_id=agent.id,
    block_label="human",
    update={"value": "Name: User. Context: Developer. Likes Python and AI."}
)

# Create and attach a new block
new_block = client.blocks.create(
    label="scratchpad",
    value="Temporary notes: check deployment status.",
    read_only=False
)
client.agents.core_memory_blocks.attach(agent_id=agent.id, block_id=new_block.id)

# Detach a block
client.agents.core_memory_blocks.detach(agent_id=agent.id, block_id=new_block.id)
```

### Concept: Core Memory vs. Archival Memory
- **Core Memory**: The "active" context window (Human, Persona, Scratchpad). Editable via tools.
- **Archival Memory**: Long-term storage (embeddings). Agents can search this but it doesn't occupy context window space until retrieved.
