# Skills Integration for Claude Agent SDK

**Date:** December 31, 2025
**Status:** ACTIVE
**Related Files:**
- `src/universal_agent/main.py` - Skill discovery, registries, and hooks
- `.claude/skills/` - Installed skills directory
- `028_CLAUDE_SKILLS_INTEGRATION.md` - Skills overview

---

## Overview

This document describes how we implemented Claude Code's skills system for the Claude Agent SDK. While Claude Code (CLI) automatically discovers skills, the raw SDK requires a custom implementation. We bridged this gap with a **multi-hook progressive disclosure system** that now includes sub-agent awareness.

---

## The Problem

| Claude Code (CLI) | Claude Agent SDK |
|-------------------|------------------|
| Auto-scans `.claude/skills/` | No native skill awareness |
| Progressive disclosure built-in | Raw API only |
| Skills invoked by model decision | Must implement ourselves |

**Solution**: Implement skill discovery + hook-based injection for both the main agent and delegated sub-agents.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ STARTUP                                                         │
├─────────────────────────────────────────────────────────────────┤
│ 1. discover_skills() scans .claude/skills/                      │
│ 2. Parses YAML frontmatter (name + description + triggers)      │
│ 3. Injects <available_skills> XML into system prompt (Main)     │
│ 4. Registers hooks: UserPromptSubmit, PreToolUse (Bash & Task)  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ RUNTIME: User submits prompt                                    │
├─────────────────────────────────────────────────────────────────┤
│ LAYER 1: UserPromptSubmit Hook (EARLY - guides planning)        │
│   • Checks prompt for skill triggers (e.g., "create pdf")       │
│   • If match → injects skill guidance with description + path   │
│   • Multiple skills can match one prompt                        │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ RUNTIME: Agent executes Tool                                    │
├─────────────────────────────────────────────────────────────────┤
│ LAYER 2a: PreToolUse (Bash) - Just-in-Time Reminders            │
│   • If executing bash commands matching keywords                │
│   • Injects skill reminder (e.g., "Read the PDF skill first")   │
│                                                                 │
│ LAYER 2b: PreToolUse (Task) - Sub-Agent Inheritance             │
│   • If delegating to a Sub-Agent (e.g., "report-creation")      │
│   • Injects "Inherited Skill Awareness" into sub-agent context  │
│   • Ensures sub-agents know about relevant skills (e.g. image)  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ RUNTIME: Agent reads skill (via read_local_file)                │
├─────────────────────────────────────────────────────────────────┤
│ LAYER 3: Full SKILL.md loaded into context                      │
│   • Only when agent decides to read it                          │
│   • Progressive disclosure: full content only when needed       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Components

### 1. Skill Discovery (`discover_skills`)
Scans `.claude/skills/` and parses `SKILL.md` frontmatter. Includes support for:
- `docx`, `pdf`, `pptx`, `xlsx` (Office docs)
- `frontend-design`, `webapp-testing` (Web dev)
- `image-generation` (Media)
- `mcp-builder`, `skill-creator` (Meta-skills)

### 2. Skill Awareness Registry
A singleton registry (`SkillAwarenessRegistry`) that holds the discovered skills and provides formatted context strings for sub-agents.

### 3. XML Injection (`generate_skills_xml`)
Creates `<available_skills>` XML for the main agent's system prompt:
```xml
<available_skills>
<skill>
  <name>pdf</name>
  <description>Comprehensive PDF manipulation...</description>
  <path>/path/to/.claude/skills/pdf/SKILL.md</path>
</skill>
...
</available_skills>
```

---

## Hook Implementation Details

### UserPromptSubmit (Early Injection)
**Function:** `on_user_prompt_skill_awareness`
- **When:** User submits a fresh prompt.
- **Logic:** Scans user input against `SKILL_PROMPT_TRIGGERS`.
- **Output:** Returns `hookSpecificOutput` with `additionalContext`.
- **Note:** This hook was previously marked as "DISABLED" due to a CLI bug, but is currently active in the codebase configurations.

### PreToolUse: Bash (Safety Net)
**Function:** `on_pre_bash_skill_hint` (implied from registry)
- **When:** Agent calls `Bash` tool.
- **Logic:** Checks command arguments for keywords (e.g., `ffmpeg`, `.pdf`).
- **Output:** Injects a system message reminding the agent to check the relevant skill doc if it hasn't already.

### PreToolUse: Task (Sub-Agent Awareness)
**Function:** `on_pre_task_skill_awareness`
- **When:** Main agent calls `Task` to spawn a sub-agent.
- **Logic:** 
  1. Identifies sub-agent type (e.g., `report-creation-expert`).
  2. Lookups expected skills for that type in `SUBAGENT_EXPECTED_SKILLS`.
  3. Injects a summarized "Inherited Skill Awareness" block into the `systemMessage`.
- **Benefit:** Allows specialized sub-agents (who don't see the main system prompt) to discover and use skills like `image-generation` without needing full context duplication.

---

## Current Skill Set

Based on the `.claude/skills` directory, the following skills are available:

| Skill | Purpose |
|-------|---------|
| **docx** | Microsoft Word document generation and editing |
| **frontend-design** | Design patterns for web interfaces |
| **image-generation** | AI image creation workflows |
| **mcp-builder** | Creating new MCP servers |
| **pdf** | PDF manipulation and creation (ReportLab/WeasyPrint) |
| **pptx** | PowerPoint presentation generation |
| **skill-creator** | Scaffolding new skills |
| **webapp-testing** | Testing web applications |
| **xlsx** | Excel spreadsheet manipulation |

---

## Adding New Skills

1. **Create skill directory**: `.claude/skills/my-skill/`
2. **Add SKILL.md** with frontmatter:
   ```yaml
   ---
   name: "my-skill"
   description: "What this skill does and when to use it"
   ---
   # Instructions...
   ```
3. **That's it!** Triggers are auto-generated from the description keywords during `discover_skills()` startup.

---

## Future Enhancements

- [ ] **Dynamic Sub-Agent Triggers**: Instead of hardcoded `SUBAGENT_EXPECTED_SKILLS`, allow sub-agents to request skills dynamically.
- [ ] **Skill Versioning**: Support multiple versions of a skill.
- [ ] **Remote Skill Fetching**: Download skills from a central repository.

---

*Document Version: 1.1*
*Last Updated: December 31, 2025*
