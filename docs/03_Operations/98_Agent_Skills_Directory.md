# Agent Skills Directory (`.agents/skills/`)

Last updated: 2026-03-17

## Purpose

The `.agents/skills/` directory contains reusable agent skills that are separate from the primary `.claude/skills/` directory. These skills are designed for portability and can be loaded by different agent runtimes.

## Directory Structure

```
.agents/
└── skills/
    ├── clean-code/
    │   └── SKILL.md          # Clean Code principles from Robert C. Martin
    ├── agentmail/
    │   ├── SKILL.md          # AgentMail integration for Simone's email
    │   └── references/
    │       ├── websockets.md # WebSocket-based real-time notifications
    │       └── webhooks.md   # HTTP webhook delivery
    ├── skill-judge/
    │   └── SKILL.md          # Evaluate skill quality against specifications
    ├── systematic-debugging/
    │   ├── SKILL.md          # Systematic debugging methodology
    │   ├── root-cause-tracing.md
    │   ├── defense-in-depth.md
    │   └── condition-based-waiting.md
    └── vp-orchestration/
        ├── SKILL.md          # External VP agent mission control
        └── references/
            └── tool_reference.md
```

## Available Skills

| Skill | Purpose |
|-------|---------|
| `clean-code` | Applies principles from Robert C. Martin's "Clean Code" - naming, functions, comments, error handling, class design |
| `agentmail` | Simone's native email inbox via AgentMail for sending/receiving emails independently from Kevin's Gmail |
| `skill-judge` | Evaluate agent skill quality against official specifications and best practices |
| `systematic-debugging` | Systematic debugging methodology - always find root cause before proposing fixes |
| `vp-orchestration` | Operate external primary VP agents through tool-first mission control |

## Usage

These skills follow the standard SKILL.md format with YAML frontmatter:

```yaml
---
name: skill-name
description: "When and how to use this skill"
---
```

## Related Documentation

- Primary skills directory: `.claude/skills/`
- SDK Permissions, Hooks & Subagents: `docs/002_SDK_PERMISSIONS_HOOKS_SUBAGENTS.md`
- Email Architecture: `docs/03_Operations/82_Email_Architecture_And_AgentMail_Source_Of_Truth_2026-03-06.md`
