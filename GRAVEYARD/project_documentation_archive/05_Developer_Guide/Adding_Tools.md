# Adding Tools

## Overview
There are two ways to give the Universal Agent new capabilities:
1.  **Native Tools**: Python functions running locally in the agent's process. Best for file I/O, data processing, or internal logic.
2.  **Composio Tools**: External SaaS integrations (GitHub, Slack, Calendar). Best for authorized API interactions.

## 1. Adding a Native Tool

### Step 1: Define the Function
Create your tool in `src/universal_agent/tools/`.
```python
# src/universal_agent/tools/my_tool.py

def calculate_pi(precision: int) -> str:
    """
    Calculate Pi to N decimal places.
    
    Args:
        precision: Number of decimal places (max 1000).
    """
    import math
    return str(math.pi)[:precision+2]
```

### Step 2: Register in `agent_core.py` (or `mcp_server.py`)
Currently, local tools are often exposed via the internal MCP server in `src/mcp_server.py`.
Add your tool to the `tools` list:

```python
# src/mcp_server.py

@mcp.tool()
def calculate_pi(precision: int) -> str:
    # ... implementation ...
```

The `UniversalAgent` automatically discovers tools exposed by the MCP server.

## 2. Adding a Composio Tool

### Step 1: Authorize the App
Run this in your terminal:
```bash
composio add github
# Follow the browser authentication flow
```

### Step 2: Update Configuration
In `main.py` (or your entry point), the `toolset` is often filtered to prevent context pollution.
Ensure your new app is allowed.

```python
# main.py
toolset = composio.get_tools(apps=["GITHUB", "SLACK"])
```

### Step 3: Use it
The agent will now see tools like `github_create_issue` in its system prompt. No extra code is needed.
