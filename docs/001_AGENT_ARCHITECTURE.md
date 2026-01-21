# 001 - Agent Architecture: agent_core.py vs main.py

## Overview

The Universal Agent system has two primary entry points for agent execution, each serving different use cases. Understanding when and why each is used is critical for maintaining consistency.

## Entry Points

### 1. `main.py` - Terminal CLI

**Purpose**: Interactive command-line interface for direct user interaction.

**Used By**: 
- `./local_dev.sh` (local development)
- Direct terminal sessions

**Key Functions**:
- `setup_session()` - Full agent initialization with rich console output
- REPL loop with `prompt_toolkit` integration
- Checkpoint/resume for long-running jobs
- Rich terminal formatting and progress output

**Characteristics**:
- Runs as standalone process
- Console I/O with rich formatting
- Session state in `AGENT_RUN_WORKSPACES/session_*`

---

### 2. `agent_core.py` - Programmatic API

**Purpose**: Reusable `UniversalAgent` class for non-CLI contexts.

**Used By**:
- **API/WebSocket** (`api/agent_bridge.py`) - Web UI backend
- **URW Harness** (`urw/integration.py`) - Long-running orchestrated tasks

**Key Class**: `UniversalAgent`
- `initialize()` - Calls `AgentSetup` for unified configuration
- `run_query()` - Async generator yielding `AgentEvent` objects
- `bind_workspace()` - Switch workspace without recreating session (for URW phases)
- `from_setup()` - Create agent from existing `AgentSetup` (for session reuse)

**Characteristics**:
- No console I/O - emits events programmatically
- Designed for embedding in other systems
- Supports session reuse across URW phases

---

## The Synchronization Problem

Historically, `main.py` and `agent_core.py` evolved independently, leading to:

| Component | `main.py` | `agent_core.py` (old) |
|-----------|-----------|----------------------|
| Agent definitions | `.claude/agents/` via `add_dirs` | Hardcoded in Python |
| Skills discovery | ✅ Yes | ❌ No |
| Knowledge base | ✅ Yes | ❌ No |
| Hooks | Full set | Partial |

This caused the UI and URW to behave differently from the terminal.

---

## Solution: `agent_setup.py`

A unified `AgentSetup` class now provides shared initialization:

```
┌────────────────────────────────────────────────────┐
│                 AgentSetup                         │
│  (Composio session, skills, agents, MCP servers)   │
└────────────────────────────────────────────────────┘
         ▲                ▲                ▲
         │                │                │
    main.py         agent_core.py    urw/integration.py
   (terminal)         (API/UI)          (harness)
```

### Key Features

1. **Session Initialization** (`initialize()`)
   - Creates Composio session
   - Loads agents from `.claude/agents/` via `add_dirs`
   - Discovers skills from `.claude/skills/`
   - Configures all MCP servers

2. **Workspace Binding** (`bind_workspace()`)
   - Updates workspace path without recreating session
   - Used by URW for phase transitions

3. **Agent Factory** (`from_setup()`)
   - Creates `UniversalAgent` from existing setup
   - Enables session reuse across iterations

---

## URW Session Management

The URW harness manages long-running tasks across multiple phases:

```
Phase 1               Phase 2               Phase 3
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│ session_    │      │ session_    │      │ session_    │
│ phase_1/    │      │ phase_2/    │      │ phase_3/    │
└─────────────┘      └─────────────┘      └─────────────┘
      ↑                    ↑                    ↑
      └────────────────────┴────────────────────┘
                 Same Composio Session
                 (via bind_workspace)
```

- **Same session**: Reused for faster startup, shared auth
- **Different workspace**: Each phase outputs to its own directory
- **Context compaction**: Auto-triggers at ~80% capacity

---

## Keeping In Sync

When modifying agent configuration:

1. **Update `.claude/agents/`** - Agent definitions (autoloaded via `add_dirs`)
2. **Update `agent_setup.py`** - Shared initialization logic
3. **Test both paths**:
   - Terminal: `./local_dev.sh`
   - URW: `PYTHONPATH=src uv run python -m universal_agent.urw.smoke_test`

> **Rule**: Never add agent configuration directly to `main.py` or `agent_core.py`. Use the `.claude/agents/` directory or `agent_setup.py`.
