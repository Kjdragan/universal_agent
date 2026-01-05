# Sub-Agent Architecture & Specialist Agents

**Document Version**: 2.0  
**Last Updated**: 2026-01-05  
**Component**: Universal Agent  
**Primary Files**: `src/universal_agent/main.py`, `.claude/agents/*.md`

---

## Table of Contents

1. [Overview](#overview)
2. [AgentDefinition Architecture](#agentdefinition-architecture)
3. [Specialist Agents Registry](#specialist-agents-registry)
4. [Sub-Agent Lifecycle & Hooks](#sub-agent-lifecycle--hooks)
5. [Skill Awareness System](#skill-awareness-system)
6. [Letta Memory Integration](#letta-memory-integration)
7. [Delegation Mechanisms](#delegation-mechanisms)
8. [Tool Inheritance Model](#tool-inheritance-model)

---

## Overview

The Universal Agent uses a **specialist delegation model** with persistent memory. The main agent acts as an orchestrator and router, while complex, domain-specific tasks are delegated to sub-agents. Each sub-agent is a "persona" with specialized system prompts, access to inherited tools, skill awareness, and persistent memory via Letta Learning SDK integration.

### Key Innovations

1. **Persistent Memory**: Sub-agents retain context across sessions via Letta Learning SDK
2. **Tool Inheritance**: Sub-agents inherit ALL parent tools when `tools` field is omitted
3. **Skill Awareness**: Sub-agents receive progressive skill loading hints before starting
4. **Hook-Based Lifecycle**: Event-driven hooks inject guidance before/after sub-agent execution
5. **Agent College Integration**: Sidecar service for memory sharing and learning

---

## AgentDefinition Architecture

Sub-agents are configured in `ClaudeAgentOptions` using the `AgentDefinition` dataclass.

```python
@dataclass
class AgentDefinition:
    description: str           # Instructions for MAIN agent on WHEN to delegate
    prompt: str                # System prompt for the SUB-AGENT
    tools: list[str] | None    # If omitted, inherits ALL tools from parent
    model: str | None          # "inherit", "claude-3-5-sonnet", etc.
```

**Critical Design Decision**: **Omit `tools` field** to inherit ALL tools including MCP tools.

**Location**: `src/universal_agent/main.py` lines 4477-4683

---

## Specialist Agents Registry

### 1. Report Creation Expert

**Delegate ID**: `report-creation-expert`  
**Location**: `.claude/agents/report-creation-expert.md`  
**Code Location**: `main.py` lines 4477-4551

**Primary Role**: Deep research, web crawling, and comprehensive HTML report generation.

**Tool Inheritance**: Omits `tools` field → inherits ALL tools from parent

**Hard Constraints**:
- **MANDATORY**: Call `finalize_research` BEFORE reading any content
- **DO NOT**: Read raw `search_results/crawl_*.md` files manually
- **DO NOT**: Use COMPOSIO_SEARCH_TOOLS (you already have the tools)
- **MUST**: Use filtered corpus from `finalize_research` output

### 2. Video Creation Expert

**Delegate ID**: `video-creation-expert`  
**Location**: `.claude/agents/video-creation-expert.md`  
**Code Location**: `main.py` lines 4634-4683

**Primary Role**: Video/Audio downloading, editing, and processing using FFmpeg and yt-dlp.

**Tool Inheritance**: Omits `tools` field → inherits ALL tools

### 3. Image Generation Expert

**Delegate ID**: `image-expert`  
**Location**: `.claude/agents/image-expert.md`  
**Code Location**: `main.py` lines 4580-4633

**Primary Role**: AI image generation and editing using Gemini 2.5 Flash.

**Tool Inheritance**: Omits `tools` field → inherits ALL tools

### 4. Slack Expert

**Delegate ID**: `slack-expert`  
**Code Location**: `main.py` lines 4552-4579

**Primary Role**: Interacting with Slack workspaces.

**Tool Inheritance**: Omits `tools` field → inherits ALL tools

---

## Sub-Agent Lifecycle & Hooks

Sub-agents are managed through an **event-driven hook system**.

### Hook System Architecture

**Location**: `src/universal_agent/main.py` lines 4685-4705

```python
hooks={
    "SubagentStop": [HookMatcher(matcher=None, hooks=[on_subagent_stop])],
    "PreToolUse": [
        HookMatcher(matcher=None, hooks=[on_pre_tool_use_ledger]),
        HookMatcher(matcher="Bash", hooks=[on_pre_bash_skill_hint]),
        HookMatcher(matcher="Task", hooks=[on_pre_task_skill_awareness]),
    ],
    "PostToolUse": [
        HookMatcher(matcher=None, hooks=[on_post_tool_use_ledger]),
        HookMatcher(matcher="Task", hooks=[on_post_task_guidance]),
    ],
}
```

### SubagentStop Hook

**Location**: `main.py` lines 1689-1757  
**Event**: Fires when a sub-agent completes

**Purpose**: Verifies artifacts were created and injects next-step guidance

### PreToolUse Hook

**Location**: `main.py` lines 1533-1585  
**Event**: Fires before Task tool executes  
**Purpose**: Injects skill awareness and Letta memory

---

## Skill Awareness System

**Location**: `main.py` lines 1449-1531

Sub-agents receive **progressive skill loading** hints when spawned.

### SkillAwarenessRegistry Class

**Purpose**: Singleton registry that discovers skills and provides awareness context.

### Expected Skills Mapping

```python
SUBAGENT_EXPECTED_SKILLS = {
    "report-creation-expert": ["pdf", "image-generation"],
    "image-expert": ["image-generation"],
    "video-creation-expert": [],
}
```

---

## Letta Memory Integration

**Location**: `main.py` lines 333-525

Sub-agents have **persistent memory** across sessions via the Letta Learning SDK.

### Configuration

**Environment Variables**:
- `UA_LETTA_SUBAGENT_MEMORY=1` (default) - Enable Letta for sub-agents
- `LOGFIRE_TOKEN` - Required for Letta tracing

### Memory Blocks

```python
LETTA_MEMORY_BLOCKS = [
    "human",              # User preferences and interactions
    "system_rules",       # Architectural guidelines
    "project_context",    # Project-specific knowledge
    "recent_queries",     # Recent user requests with timestamps
    "recent_reports",     # Latest reports generated
]
```

---

## Delegation Mechanisms

### 1. Agent-Driven (Contextual)

The main agent decides to delegate based on the `description` field in `AgentDefinition`.

### 2. JIT Guided (Architecture Enforced)

For critical workflows like Reports, we use **Just-In-Time (JIT) Guide Rails** to force delegation.

**File Location**: `.claude/kb/report_workflow.md`

---

## Tool Inheritance Model

**Critical Architectural Pattern**: Sub-agents inherit ALL parent tools when `tools` field is **omitted**.

### Inheritance Rules

| Scenario | Tools Available |
|----------|-----------------|
| **Omit `tools` field** | **ALL** parent tools (MCP + Composio + local) |
| **Specify `tools=[]`** | **NO** tools (empty whitelist) |
| **Specify `tools=["Read", "Bash"]`** | **ONLY** Read and Bash |

**Current Implementation**: All sub-agents omit `tools` field to inherit ALL tools.

---

## Agent College Integration

**Note**: Agent College is a **sidecar service** that runs alongside the main agent for advanced memory sharing and learning.

**Purpose**:
- Shared memory across all agent instances
- Professor agent for reviewing and improving outputs
- Critic agent for evaluating quality

**Files**:
- `src/universal_agent/agent_college/runner.py` - Main College worker
- `src/universal_agent/agent_college/professor.py` - Professor agent
- `src/universal_agent/agent_college/critic.py` - Critic agent

---

**Document Status**: ✅ Active & Updated
**Last System Sync**: 2026-01-05
