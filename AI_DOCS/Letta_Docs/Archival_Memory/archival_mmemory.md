---
title: Archival memory | Letta Docs
description: Overview of archival memory for long-term information storage beyond context window limits.
---

## What is archival memory?

Archival memory is a semantically searchable database where agents store facts, knowledge, and information for long-term retrieval. Unlike memory blocks that are always visible, archival memory is queried on-demand when relevant.

**Key characteristics:**

- **Agent-immutable** - Agents cannot easily modify or delete archival memories (though developers can via SDK)
- **Unlimited storage** - No practical size limits
- **Semantic search** - Find information by meaning, not exact keywords
- **Tagged organization** - Agents can categorize memories with tags

## How agents interact with archival memory

**Two ways to interact with archival memory:**

**Agent tools** - What agents do autonomously during conversations:

- `archival_memory_insert` - Store new information
- `archival_memory_search` - Query for relevant memories

**SDK endpoints** - What developers do via `client.agents.passages.*`:

- Insert, search, list, update, and delete memories programmatically
- Manage archival content outside of agent conversations

### Inserting information

**Agents** can insert memories during conversations using the `archival_memory_insert` tool:

```
# What the agent does (agent tool call)
archival_memory_insert(
    content="Deckard retired six replicants in the off-world colonies before returning to Los Angeles",
    tags=["replicant", "history", "retirement"]
)
```

**Developers** can also insert programmatically via the SDK:

- [TypeScript](#tab-panel-377)
- [Python](#tab-panel-378)

```
await client.agents.passages.insert(agent.id, {
  content: "The Tyrell Corporation's motto: 'More human than human'",
  tags: ["company", "motto", "tyrell"],
});
```

```
client.agents.passages.insert(
    agent_id=agent.id,
    content="The Tyrell Corporation's motto: 'More human than human'",
    tags=["company", "motto", "tyrell"]
)
```

### Searching for information

**Agents** can search semantically using the `archival_memory_search` tool:

```
# What the agent does (agent tool call)
results = archival_memory_search(
    query="replicant lifespan",
    tags=["technical"],  # Optional: filter by tags
    page=0
)
```

**Developers** can also search programmatically via the SDK:

- [TypeScript](#tab-panel-379)
- [Python](#tab-panel-380)

```
const results = await client.agents.passages.search(agent.id, {
  query: "replicant lifespan",
  tags: ["technical"],
  page: 0,
});
```

```
results = client.agents.passages.search(
    agent_id=agent.id,
    query="replicant lifespan",
    tags=["technical"],
    page=0
)
```

Results return **semantically relevant** information - meaning the search understands concepts and meaning, not just exact keywords. For example, searching for “artificial memories” will find “implanted memories” even though the exact words don’t match.

[Learn more about search and querying →](/guides/agents/archival-search/index.md)

## When to use archival memory

**Use archival memory for:**

- Document repositories (API docs, technical guides, research papers)
- Conversation logs beyond the context window
- Customer interaction history and support tickets
- Reports, articles, and written content
- Code examples and technical references
- Training materials and educational content
- User research data and feedback
- Historical records and event logs

**Don’t use archival memory for:**

- Information that should always be visible → Use memory blocks
- Frequently changing state → Use memory blocks
- Current working memory → Use scratchpad blocks
- Information that needs frequent modification → Use memory blocks

## Real-world examples

### Example 1: Personal knowledge manager

An agent with 30k+ archival memories tracking:

- Personal preferences and history
- Technical learnings and insights
- Article summaries and research notes
- Conversation highlights

### Example 2: Social media agent

An agent with 32k+ memories tracking interactions:

- User preferences and conversation history
- Common topics and interests
- Interaction patterns and communication styles
- Tags by user, topic, and interaction type

### Example 3: Customer support agent

An agent that builds institutional knowledge from support interactions:

- Stores ticket resolutions and common issues
- Tags by product, issue type, priority
- Searches archival for similar past issues
- Learns from successful resolutions over time

### Example 4: Research assistant

An agent that maintains a growing knowledge base of academic literature:

- Stores paper summaries with key findings
- Tags by topic, methodology, author
- Cross-references related research
- Builds a semantic knowledge graph

## Archival memory vs conversation search

**Archival memory** is for **intentional** storage:

- Agents decide what’s worth remembering long-term
- Used for facts, knowledge, and reference material
- Curated by the agent through active insertion

**Conversation search** is for **historical** retrieval:

- Searches through actual past messages
- Used to recall what was said in previous conversations
- Automatic - no agent curation needed

**Example:** User says “I prefer Python for data science projects”

- **Archival:** Agent inserts “User prefers Python for data science” as a fact
- **Conversation search:** Agent can search for the original message later

Use archival for structured knowledge, conversation search for historical context.

## Next steps

[Searching & Querying ](/guides/agents/archival-search/index.md)Learn how to write effective queries and filter results

[Best Practices ](/guides/agents/archival-best-practices/index.md)Patterns, pitfalls, and advanced usage

[Memory Blocks ](/guides/agents/memory-blocks/index.md)Learn about always-visible memory

[Agent Memory Overview ](/guides/agents/memory/index.md)Understand Letta's memory system
