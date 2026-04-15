---
name: llm-wiki-orchestration
description: >
  Operate LLM knowledge bases via NotebookLM. Use this skill whenever the user
  mentions wiki, knowledge base, knowledge vault, research vault, "build a wiki
  about", "what does our wiki say about", "create a knowledge base for", 
  "learn about X and remember it", or ANY request about persistent organized
  knowledge. Always use this over one-shot research when the user wants durable,
  queryable knowledge.
---

# LLM Wiki Orchestration

Use this skill whenever the user wants a persistent knowledge base or memory vault rather than a one-shot retrieval answer. 

## Routing Contract

Knowledge bases are built as NotebookLM notebooks. Route requests to the `notebooklm-operator` sub-agent with specialized `kb_` missions:

- "create/build/start a knowledge base about X" → `Task(subagent_type='notebooklm-operator', mission='kb_research_and_build')`
- "add X to the Y knowledge base" → `Task(subagent_type='notebooklm-operator', mission='kb_add_sources')`
- "what does the wiki say about X" → `Task(subagent_type='notebooklm-operator', mission='kb_query')`
- "generate a podcast from wiki X" → `Task(subagent_type='notebooklm-operator', mission='kb_generate_artifact')`
- "what knowledge bases do we have" → `Task(subagent_type='notebooklm-operator', mission='kb_list')`
- "lint/check wiki X" → `Task(subagent_type='notebooklm-operator', mission='Check notebook health/description')`

## Internal Memory Vaults

To sync operational memory or project durable session checkpoints into a structured, internal markdown format, use the regular Python toolkit directly or delegate to a local tool operator, since it does NOT use NotebookLM:
1. `wiki_init_vault`
2. `wiki_sync_internal_memory`
3. `wiki_query` (on internal path)
4. `wiki_lint` (on internal path)

Internal memory syncs run completely locally via Python AST analysis and simple LLM calls without NotebookLM external boundaries.

## Natural Language → Mission Mapping

- "Simone, create a Wiki knowledge base for the topic of agentic harnesses in AI coding" → `kb_research_and_build`
- "Add this article URL to the agentic-harnesses knowledge base" → `kb_add_sources`
- "What does our wiki know about Claude Agent SDK?" → `kb_query`
- "Generate a mind map from the agentic-harnesses wiki" → `kb_generate_artifact`

## Performance Hints (Include in Delegation)

When delegating to the operator, include these hints:

1. **Parallel artifact generation**: Fire ALL `studio_create` calls first, then poll `studio_status` once for all. Do NOT wait between individual create calls.
2. **Adaptive polling**: Use `sleep 5` for fast research, `sleep 10` for studio artifacts, `sleep 20` for deep research/audio.
3. **Default to fast research** unless user says "comprehensive/thorough/exhaustive."
4. **NLM-first for all artifacts** — do NOT use `generate_image` or generic LLM markdown for KB artifacts.

