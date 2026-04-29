# Managed Agents Memory & Skill Integration

- Bundle ID: `memory-managed-agents`
- Recommended variant: `exploratory_skill`
- UA value: Medium - Reference implementation for persistent memory.
- Agent-system value: Medium - Understanding how memory handles state across sessions is crucial for long-running agents.

## Summary

Anthropic released 'Memory' for Claude Managed Agents (public beta) and added support for this feature to the built-in `claude-api` skill in Claude Code.

## Why Now

New capability launched (Public Beta); accessible via CLI skill.

## For Kevin

New feature to explore: **Memory**. 

1. **Try it:** In Claude Code, run `/claude-api` and ask about "Managed Agents memory".
2. **Source:** Check the [skill on GitHub](https://github.com/anthropics/skills/tree/main/skills/claude-api) to see how they implemented the API calls for memory.
3. **Concept:** This allows agents to learn across sessions. Does UA need cross-session memory?

## For UA

The `claude-api` skill now includes logic to interact with Managed Agents Memory. 

**Implementation Hint:** If building custom agents, inspect the API calls made by this skill to understand the interface for `update_memory` and `query_memory` endpoints.

## Canonical Sources

- [skills/skills/claude-api](https://github.com/anthropics/skills/tree/main/skills/claude-api) — `github_tree` / `github.com`

## Variants

### Skill Discovery: Memory API

- Key: `exploratory_skill`
- Intent: To understand the API surface for the new Memory feature.
- Applicability: `["Agent SDK"]`
- Confidence: `high`

#### Trigger the Skill

- Kind: `code_snippet`
- Rationale: The fastest way to see the implementation is to let the agent show it.

Inside Claude Code, execute:
```
/claude-api
```
Then ask: "Show me the code for Managed Agents memory support."

This will reveal the specific API endpoints (e.g., `POST /v1/memory`) used by the system.
