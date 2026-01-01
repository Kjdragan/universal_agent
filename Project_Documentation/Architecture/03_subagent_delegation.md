# Sub-Agent Architecture & Specialist Agents

**Document Version**: 1.1
**Last Updated**: 2025-12-29
**Component**: Universal Agent
**Primary Files**: `src/universal_agent/main.py`, `.claude/agents/*.md`

---

## Table of Contents

1. [Overview](#overview)
2. [AgentDefinition Architecture](#agentdefinition-architecture)
3. [Specialist Agents Registry](#specialist-agents-registry)
    - [Report Creation Expert](#1-report-creation-expert)
    - [Video Creation Expert](#2-video-creation-expert)
    - [Image Generation Expert](#3-image-generation-expert)
    - [Slack Expert](#4-slack-expert)
4. [Delegation Mechanisms](#delegation-mechanisms)
5. [Compliance Verification](#compliance-verification)

---

## Overview

The Universal Agent uses a **specialist delegation model**. The main agent acts as an orchestrator and router, while complex, domain-specific tasks are delegated to sub-agents. Each sub-agent is a "persona" with a restricted toolset and a specialized system prompt, defined using the Claude Agent SDK's `AgentDefinition` class.

### Benefits
1.  **Tool Scoping**: Sub-agents only see relevant tools, reducing hallucination.
2.  **Context Hygiene**: Sub-agents run with clean context, avoiding token bloat from long orchestration histories.
3.  **Prompt Specialization**: Prompts can be highly detailed for specific tasks (e.g., FFmpeg syntax) without confusing the main agent.

---

## AgentDefinition Architecture

Sub-agents are configured in `ClaudeAgentOptions` using the `AgentDefinition` dataclass.

```python
@dataclass
class AgentDefinition:
    description: str           # Instructions for MAIN agent on WHEN to delegate
    prompt: str                # System prompt for the SUB-AGENT
    tools: list[str] | None    # Whitelist of tools available to sub-agent
    model: str | None          # "inherit", "claude-3-5-sonnet", etc.
```

---

## Specialist Agents Registry

### 1. Report Creation Expert
**Delegate ID**: `report-creation-expert`
**Primary Role**: Deep research, web crawling, and comprehensive report generation.
**Key Tools**:
*   `mcp__local_toolkit__crawl_parallel` (Batch web scraping)
*   `mcp__local_toolkit__save_corpus` (Research persistence)
*   `mcp__local_toolkit__write_local_file` (HTML report generation)

**Hard Constraints**:
*   Mandatory use of `crawl_parallel` (no snippets).
*   Must save `expanded_corpus.json` before writing reports.
*   Produces HTML (which main agent converts to PDF).

### 2. Video Creation Expert
**Delegate ID**: `video-creation-expert`
**Primary Role**: Video/Audio downloading, editing, and processing.
**Key Tools**:
*   `mcp__youtube__*` (Download video/audio/metadata)
*   `mcp__video_audio__*` (FFmpeg wrappers: trim, concatenate, transitions, overlays)

**Guidance**:
*   "MANDATORY DELEGATION TARGET" for all media processing.
*   Handles intermediate files (temp names) and produces final output.

### 3. Image Generation Expert
**Delegate ID**: `image-expert`
**Primary Role**: Generating and editing images using AI models.
**Key Tools**:
*   `mcp__local_toolkit__generate_image` (Gemini/ZAI generation)
*   `mcp__local_toolkit__describe_image` (Vision analysis)
*   Inherits Composio tools for finding reference materials.

**Workflow**:
*   Plan → Generate → Review (Describe) → Iterate.

### 4. Slack Expert
**Delegate ID**: `slack-expert`
**Primary Role**: Interacting with Slack workspaces (reading history, posting messages).
**Key Tools**:
*   `SLACK_LIST_CHANNELS`
*   `SLACK_FETCH_CONVERSATION_HISTORY`
*   `SLACK_SEND_MESSAGE`

**Purpose**:
*   Summarizing channel discussions.
*   Posting announcements or reports to teams.
*   Abstracts API complexity (channel ID lookups) from main agent.

---

## Delegation Mechanisms

The system supports two primary ways delegation is triggered:

### 1. Agent-Driven (Contextual)
The main agent decides to delegate based on the `description` field in `AgentDefinition`.
*   *Example*: User asks "Make a video about cats."
*   Main Agent sees `video-creation-expert` description: "WHEN TO DELEGATE: User asks to... process video."
*   Main Agent calls `Task(agent="video-creation-expert")`.

### 2. JIT Guided (Architecture Enforced)
For critical workflows like Reports, we use **Just-In-Time (JIT) Guide Rails** (see `10_jit_delegation_guide_rail.md`) to force delegation even if the agent is unsure.
*   *Mechanism*: A Knowledge Base file (`report_workflow.md`) explicitly instructs: "When you have search results, YOU MUST DELEGATE."
*   This overrides the Main Agent's tendency to summarize snippets itself.

---

## Compliance Verification

After a sub-agent completes a task, the Main Agent (via the Observer Pattern) verifies compliance with architectural rules.

**Verifier**: `verify_subagent_compliance()` in `main.py`.

**Checks**:
*   **Report Tasks**: Did the sub-agent save `expanded_corpus.json`?
*   **Video Tasks**: (Potential future check) Did it produce a file in `work_products/media/`?

If verification fails, an error message is injected into the Main Agent's context, prompting it to retry or fix the issue.
