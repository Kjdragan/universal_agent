# Skills Integration for Claude Agent SDK

**Date:** December 25, 2025
**Status:** ACTIVE
**Related Files:**
- `src/universal_agent/main.py` - Skill discovery and hooks
- `.claude/skills/` - Installed skills directory
- `028_CLAUDE_SKILLS_INTEGRATION.md` - Skills overview

---

## Overview

This document describes how we implemented Claude Code's skills system for the Claude Agent SDK. Claude Code automatically discovers and uses skills; the raw SDK does not. We bridged this gap with a **dual-hook progressive disclosure system**.

---

## The Problem

| Claude Code (CLI) | Claude Agent SDK |
|-------------------|------------------|
| Auto-scans `.claude/skills/` | No skill awareness |
| Progressive disclosure built-in | Raw API only |
| Skills invoked by model decision | Must implement ourselves |

**Solution**: Implement skill discovery + hook-based injection to achieve the same behavior.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ STARTUP                                                         │
├─────────────────────────────────────────────────────────────────┤
│ 1. discover_skills() scans .claude/skills/                      │
│ 2. Parses YAML frontmatter (name + description ONLY)            │
│ 3. Injects <available_skills> XML into system prompt            │
│ 4. Registers hooks: UserPromptSubmit + PreToolUse               │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ RUNTIME: User submits prompt                                    │
├─────────────────────────────────────────────────────────────────┤
│ LAYER 1: UserPromptSubmit Hook (EARLY - guides planning)       │
│   • Checks prompt for document keywords (pdf, excel, etc.)     │
│   • If match → injects skill guidance with description + path  │
│   • Multiple skills can match one prompt                        │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ RUNTIME: Agent executes Bash command                            │
├─────────────────────────────────────────────────────────────────┤
│ LAYER 2: PreToolUse Hook (BACKUP - catches missed skills)      │
│   • Checks Bash command for pdf/pptx/docx/xlsx keywords        │
│   • If match → injects last-minute skill reminder              │
│   • Safety net if agent ignored early guidance                  │
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

## Key Functions

### `discover_skills(skills_dir)`

Scans `.claude/skills/` and parses SKILL.md frontmatter.

```python
def discover_skills(skills_dir: str = None) -> list[dict]:
    """
    Returns: [{"name": "pdf", "description": "...", "path": "..."}]
    
    Progressive disclosure: Only loads name + description.
    Full SKILL.md content loaded by agent when needed.
    """
```

### `generate_skills_xml(skills)`

Creates `<available_skills>` XML for system prompt injection.

```xml
<available_skills>
<skill>
  <name>pdf</name>
  <description>Comprehensive PDF manipulation...</description>
  <path>/path/to/.claude/skills/pdf/SKILL.md</path>
</skill>
</available_skills>
```

---

## Skill Matching Logic

### Trigger Configuration

```python
SKILL_PROMPT_TRIGGERS = {
    "pdf": ["pdf", "create pdf", "generate pdf", "pdf document"],
    "docx": ["word document", "docx", "word file"],
    "pptx": ["presentation", "powerpoint", "slides"],
    "xlsx": ["spreadsheet", "excel", "xlsx", "worksheet"],
}
```

### Multi-Skill Matching

```python
# Example prompt: "Create a PDF and Excel file"
# Result: matched_skills = [("pdf", path, desc), ("xlsx", path, desc)]

matched_skills = []
for skill_name, triggers in SKILL_PROMPT_TRIGGERS.items():
    if any(trigger in prompt for trigger in triggers):
        matched_skills.append((skill_name, path, description))
```

All matching skills are injected together in one message.

---

## Hooks Reference

| Hook | When | Purpose |
|------|------|---------|
| `UserPromptSubmit` | User submits prompt | Early guidance before planning |
| `PreToolUse` (Bash) | Before Bash execution | Backup reminder if skill not read |
| `SubagentStop` | Sub-agent finishes | Verify artifacts created |

### Hook Message Format

**UserPromptSubmit** (early injection):
```
🎯 SKILL GUIDANCE: This task involves document creation.
BEFORE writing code, read the relevant skill(s) for proven patterns:

  - **pdf**: Comprehensive PDF manipulation...
    Path: `/path/to/.claude/skills/pdf/SKILL.md`

Use `read_local_file` on the SKILL.md path to load full instructions.
```

**PreToolUse** (backup):
```
⚠️ SKILL REMINDER: You're about to create PDF content.
The `pdf` skill at `/path/...` has proven patterns.
Consider reading it FIRST to avoid common issues.
```

---

## Why This Matters

| Without Skills | With Skills |
|----------------|-------------|
| Agent writes PDF code from scratch | Agent follows proven patterns |
| Hit-or-miss library choices | Correct libraries/approaches |
| Common errors repeated | Known pitfalls avoided |
| Inconsistent results | Deterministic workflows |

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
3. **That's it!** Triggers are auto-generated from the description keywords.

**Optional**: Override triggers in `SKILL_PROMPT_TRIGGERS_OVERRIDE`:
```python
SKILL_PROMPT_TRIGGERS_OVERRIDE = {
    "my-skill": ["custom-trigger", "another-trigger"],
}
```

Skills are auto-discovered at startup - triggers extracted from description automatically.

---

## Future Enhancements

- [ ] YouTube processing skill (yt-dlp, youtube-transcript-api)
- [ ] External API best practices skills
- [ ] Sub-agent specialized skills
- [ ] Skill versioning and A/B testing

---

*Document Version: 1.0*
*Last Updated: December 25, 2025*
