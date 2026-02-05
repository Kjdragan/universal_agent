---
name: zread-dependency-docs
description: Read documentation and code from open source GitHub repositories using the ZRead MCP server
---

# ZRead Dependency Documentation Skill

Use this skill to explore open source repositories, read their documentation, understand project structure, and analyze source code.

## Prerequisites

The ZRead MCP server must be configured. It uses the `ZAI_API_KEY` from your environment.

## Available Tools

### 1. `search_doc`

Search documentation, issues, PRs, and contributor info for a repository.

```
Use: "Search the documentation for {owner/repo} about {topic}"
Example: "Search the documentation for langchain-ai/langchain about memory systems"
```

### 2. `get_repo_structure`

Get the directory tree and file list of a repository.

```
Use: "Show me the structure of {owner/repo}"
Example: "Show me the structure of anthropics/anthropic-sdk-python"
```

### 3. `read_file`

Read the complete content of a specific file.

```
Use: "Read the file {path} from {owner/repo}"
Example: "Read the file src/anthropic/client.py from anthropics/anthropic-sdk-python"
```

## Common Workflows

### Learning a New Library

1. Search docs for overview/getting started
2. Get repo structure to understand layout
3. Read key files (README, main entry points)

### Debugging Dependency Issues

1. Search docs for the error or behavior
2. Search for related issues/PRs
3. Read the relevant source code

### Evaluating a Dependency

1. Get structure to assess code organization
2. Search for recent issues and activity
3. Read tests to understand quality

## Documentation Index

For complete Z.AI documentation, fetch: `https://docs.z.ai/llms.txt`

## Requirements

- Repository must be public (open source)
- Format: `owner/repo` (e.g., `facebook/react`)
- Quota: Shared with web search (see Z.AI plan limits)
