# Skill Development Guide

Skills are the "hands and eyes" of the Universal Agent. This guide explains how to add new capabilities to the system.

## 1. What is a Skill?

A skill is a self-contained directory containing:

1. **`SKILL.md`**: Metadata and usage examples (required).
2. **Implementation**: Python scripts, MCP server definitions, or configuration files.
3. **Dependencies**: Managed via `uv` or system binaries.

## 2. Skill Directory Structure

Skills live in `/home/kjdragan/lrepos/universal_agent/.claude/skills/`.

```text
my-new-skill/
├── SKILL.md          # Metadata, install instructions, tool descriptions
├── scripts/          # Helper Python scripts (run via 'uv run')
└── mcp_server.py     # (Optional) FastMCP server implementation
```

## 3. Creating `SKILL.md`

The `SKILL.md` file uses a YAML frontmatter to tell the agent how to install and use the skill.

```markdown
---
name: my-skill
description: Describe what the skill does for the LLM.
metadata:
  {
    "openclaw": {
      "emoji": "🛠️",
      "requires": { "bins": ["uv"] },
      "install": [
        { "id": "python-pkg", "kind": "uv", "package": "requests" }
      ]
    }
  }
---

# My Skill Documentation
Usage examples go here...
```

## 4. Tool Integration Patterns

The Universal Agent discovers tools in three ways:

### A. Local Python Script (The "Plumbing" Pattern)

Use `os.run_command` or a bridge to run a Python script in the background. This is best for deterministic processing.

### B. FastMCP Server

Implement a `fastmcp` server. The agent automatically detects and routes calls to these servers if they are registered in the `mcp_server.py` of the skill.

### C. Composio Tool Router

For external APIs (Gmail, Slack, GitHub), we use the **Composio Router**. New skills can be added by configuring the app on Composio and enabling it in the agent's `agent_setup.py`.

## 5. Best Practices

1. **Plumbing vs. Reasoning**: Use Python/Shell for deterministic "plumbing" (e.g., parsing a CSV). Use LLM tools for reasoning (e.g., deciding which rows are relevant).
2. **Unique IDs**: Ensure all tools have unique, descriptive names.
3. **Idempotency**: Tools should be safe to run multiple times.
4. **Error Handling**: Return descriptive error messages to the agent so it can recover.
