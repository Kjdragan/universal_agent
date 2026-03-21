# Integration Proposal: Multimodal RAG as a Universal Agent Sub-Agent

This document outlines proposals for integrating the Multimodal RAG system into the Universal Agent framework as a sub-agent or skill module.

## Executive Summary

The Multimodal RAG project provides powerful document understanding capabilities through:
- Native multimodal embeddings (text + visual)
- Semantic search over PDF content
- Citation-backed answer generation

Integrating this into Universal Agent would enable agents to query and reason over document knowledge bases as part of their toolset.

---

## Proposal 1: Document Query Skill

### Concept
Expose RAG functionality as a skill that any agent can invoke when it needs to query document knowledge.

### Implementation

```python
# skills/document_query/skill.py

from typing import Any
from universal_agent.skills.base import BaseSkill
from universal_agent.skills.decorators import skill

@skill(
    name="document_query",
    description="Query indexed PDF documents using semantic search",
    parameters={
        "query": {"type": "string", "description": "The question to search for"},
        "collection": {"type": "string", "default": "pdf_pages", "description": "Collection to search"}
    }
)
class DocumentQuerySkill(BaseSkill):
    async def execute(self, query: str, collection: str = "pdf_pages") -> dict[str, Any]:
        """Execute a RAG query against indexed documents."""
        from pathlib import Path
        import sys

        # Import RAG pipeline
        rag_path = Path(__file__).parent.parent.parent / "New_Rag_Project"
        sys.path.insert(0, str(rag_path))

        from rag import RAGPipeline

        pipeline = RAGPipeline()
        result = pipeline.query(query)

        return {
            "answer": result["answer"],
            "sources": result["sources"],
            "context_excerpt": result["context"][:1000]  # Truncate for token efficiency
        }
```

### Benefits
- Simple integration
- Reusable across all agents
- Maintains separation of concerns

### Challenges
- Requires documents to be pre-indexed
- No automatic document discovery

---

## Proposal 2: Document Indexing Skill

### Concept
Allow agents to dynamically index new documents as part of their workflows.

### Implementation

```python
# skills/document_index/skill.py

from typing import Any
from universal_agent.skills.base import BaseSkill
from universal_agent.skills.decorators import skill

@skill(
    name="document_index",
    description="Index a PDF document for semantic search",
    parameters={
        "pdf_path": {"type": "string", "description": "Path or URL to the PDF document"},
        "collection": {"type": "string", "default": "pdf_pages"}
    }
)
class DocumentIndexSkill(BaseSkill):
    async def execute(self, pdf_path: str, collection: str = "pdf_pages") -> dict[str, Any]:
        """Index a PDF document into the vector store."""
        # Handle URLs by downloading first
        if pdf_path.startswith("http"):
            pdf_path = await self._download_pdf(pdf_path)

        # Run ingestion
        from pathlib import Path
        import sys

        rag_path = Path(__file__).parent.parent.parent / "New_Rag_Project"
        sys.path.insert(0, str(rag_path))

        from ingest import ingest_pdf

        stats = ingest_pdf(pdf_path)

        return {
            "success": True,
            "pages_indexed": stats["pages_processed"],
            "errors": stats["errors"]
        }

    async def _download_pdf(self, url: str) -> str:
        """Download a PDF from URL to temp file."""
        import aiohttp
        import tempfile

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                content = await response.read()

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(content)
            return f.name
```

### Benefits
- Dynamic document ingestion
- Agents can expand their own knowledge base
- Supports remote documents

### Challenges
- Need to manage storage and cleanup
- Long-running operations may timeout

---

## Proposal 3: Dedicated RAG Sub-Agent

### Concept
Create a specialized sub-agent that manages document knowledge and provides a higher-level interface.

### Architecture

```
Universal Agent (Coordinator)
        |
        v
    RAG Sub-Agent
    /     |     \
Index  Query  Manage
Skill  Skill  Skill
```

### Implementation

```python
# agents/rag_agent/agent.py

from universal_agent.agents.base import BaseAgent
from universal_agent.agents.decorators import agent

@agent(
    name="rag_agent",
    description="Document knowledge management agent",
    capabilities=["document_indexing", "semantic_search", "knowledge_retrieval"]
)
class RAGAgent(BaseAgent):
    """Specialized agent for document RAG operations."""

    def __init__(self):
        super().__init__()
        self.collections = {}  # Track multiple document collections

    async def index_document(self, source: str, collection: str = "default") -> dict:
        """Index a document into a named collection."""
        # Implementation using ingest.py
        pass

    async def query(self, question: str, collections: list[str] = None) -> dict:
        """Query across collections with intelligent routing."""
        # Implementation using rag.py with multi-collection support
        pass

    async def list_collections(self) -> list[dict]:
        """List all indexed collections with metadata."""
        pass

    async def remove_collection(self, collection: str) -> bool:
        """Remove an indexed collection."""
        pass
```

### Benefits
- Full lifecycle management
- Multi-collection support
- Can maintain document metadata
- Higher-level reasoning about knowledge

### Challenges
- More complex implementation
- Requires agent orchestration support

---

## Proposal 4: Memory Integration

### Concept
Use the RAG system as a long-term memory backend for agents, storing and retrieving knowledge persistently.

### Implementation Approach

1. **Memory Store Interface**: Implement ChromaDB as a memory backend
2. **Automatic Indexing**: Convert agent memories/learnings to embeddings
3. **Contextual Recall**: Retrieve relevant memories during task execution

```python
# memory/rag_memory_store.py

from universal_agent.memory.base import MemoryStore

class RAGMemoryStore(MemoryStore):
    """Use RAG system as agent memory backend."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.collection_name = f"memory_{agent_id}"
        # Initialize ChromaDB collection

    async def store(self, content: str, metadata: dict = None) -> str:
        """Store a memory with embedding."""
        # Generate embedding and store in ChromaDB
        pass

    async def recall(self, query: str, limit: int = 5) -> list[dict]:
        """Recall relevant memories."""
        # Query ChromaDB for similar memories
        pass

    async def forget(self, memory_id: str) -> bool:
        """Remove a specific memory."""
        pass
```

### Benefits
- Persistent agent memory
- Semantic retrieval of past learnings
- Scales to large knowledge bases

### Challenges
- Memory coherence and updates
- Potential for conflicting information

---

## Proposal 5: Tool-Calling Integration with MCP

### Concept
Expose RAG capabilities as MCP (Model Context Protocol) tools that can be discovered and invoked.

### Implementation

```json
// mcp_tools/rag_tools.json
{
  "tools": [
    {
      "name": "rag_query",
      "description": "Search indexed documents and get AI-generated answers",
      "inputSchema": {
        "type": "object",
        "properties": {
          "query": {"type": "string", "description": "Question to answer from documents"},
          "top_k": {"type": "integer", "default": 5, "description": "Number of sources to retrieve"}
        },
        "required": ["query"]
      }
    },
    {
      "name": "rag_index",
      "description": "Index a PDF document for future queries",
      "inputSchema": {
        "type": "object",
        "properties": {
          "source": {"type": "string", "description": "Path or URL to PDF"}
        },
        "required": ["source"]
      }
    }
  ]
}
```

### Benefits
- Standard tool interface
- Works with any MCP-compatible agent
- Tool discovery enabled

### Challenges
- Requires MCP infrastructure
- Tool state management

---

## Recommended Implementation Path

### Phase 1: Basic Skills (Week 1-2)
1. Implement `document_query` skill
2. Test with existing indexed documents
3. Integrate into agent tool registry

### Phase 2: Dynamic Indexing (Week 3-4)
1. Add `document_index` skill
2. Handle URL downloads
3. Add progress tracking for long operations

### Phase 3: Sub-Agent (Month 2)
1. Create dedicated RAG agent
2. Multi-collection support
3. Management interface

### Phase 4: Memory Integration (Month 3)
1. Memory store interface
2. Automatic memory indexing
3. Contextual recall integration

---

## Technical Considerations

### Environment Variables
Ensure these are available to the agent runtime:
- `GOOGLE_API_KEY`
- `OPENAI_API_KEY`
- `CHROMA_DB_PATH` (use agent-specific path)

### Resource Management
- Monitor ChromaDB storage growth
- Implement collection cleanup policies
- Consider embedding API rate limits

### Error Handling
- Graceful degradation when APIs unavailable
- Fallback to text-only search if embedding fails
- Clear error messages for missing documents

### Security
- API keys must be securely stored
- Document access control if multi-tenant
- Sanitize user queries before embedding

---

## Conclusion

The Multimodal RAG system provides powerful document understanding that would significantly enhance Universal Agent's capabilities. Starting with simple skills and progressively building toward a full sub-agent provides a practical integration path with incremental value delivery.

The recommended first step is implementing the `document_query` skill, which immediately enables any agent to query indexed documents while keeping the implementation simple and maintainable.
