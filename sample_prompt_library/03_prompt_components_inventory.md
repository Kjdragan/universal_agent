# Simone Prompt Components Inventory

## Prompt Assembly Order

| # | Component | Source | Est. Size | Notes |
|---|-----------|--------|-----------|-------|
| 0 | Claude Code Preset | Built-in SDK | ~20k tokens | Base tools (Bash/Read/Write/Edit/Agent/Task/TaskStop) |
| 1 | SOUL.md (Identity) | `prompt_assets/SOUL.md` | 152 lines | Simone persona, voice, execution model |
| 2 | Workspace Key Files | Session workspace | Variable | AGENTS.md, IDENTITY.md, USER.md, TOOLS.md, HEARTBEAT.md |
| 3 | Temporal Context | Computed | ~10 lines | Current date, time window rules |
| 4 | Role Definition | `prompt_builder.py` | ~5 lines | 'Universal Coordinator Agent' |
| 5 | Recovery Handoff | Session workspace | Variable | Only if RECOVERY_HANDOFF.md exists |
| 6 | Capabilities Registry | `capabilities.md` | **1157 lines** | **Largest component** — agents, skills, toolkits |
| 7 | Memory Context | Memory files | Variable | Core memory blocks, preferences, prior sessions |
| 8 | User Profile | `config/USER.md` | ~50 lines | Private user data |
| 9 | Architecture & Tools | `prompt_builder.py` | ~20 lines | MCP namespaces, VP rules, sibling tool warning |
| 10 | Capability Domains | `prompt_builder.py` | ~15 lines | Intelligence, Computation, Media, etc. |
| 11 | Execution Strategy | `prompt_builder.py` | ~15 lines | **⚠️ Contains TaskStop reference at line 324** |
| 12 | Browser Lane Selection | `prompt_builder.py` | ~5 lines | Bowser-first policy |
| 13 | Showcase Guidance | `prompt_builder.py` | ~15 lines | Open-ended request handling |
| 14 | Search Hygiene | `prompt_builder.py` | ~10 lines | Research-specialist routing |
| 15 | Data Flow Policy | `prompt_builder.py` | ~10 lines | Local-first, sync_response rules |
| 16 | Workbench Restrictions | `prompt_builder.py` | ~8 lines | Remote workbench rules |
| 17 | Artifact Output Policy | `prompt_builder.py` | ~8 lines | Durable deliverables |
| 18 | Email & Communication | `prompt_builder.py` | ~5 lines | One attachment per email |
| 19 | Autonomous Behavior | `prompt_builder.py` | ~8 lines | No confirmation, full authority |
| 20 | Report Delegation | `prompt_builder.py` | ~10 lines | Research → Report pipeline |
| 21 | System Config Delegation | `prompt_builder.py` | ~6 lines | system-configuration-agent routing |
| 22 | Memory Management | `prompt_builder.py` | ~25 lines | Read/write/proactive memory |
| 23 | Skills | Skills XML | Variable | Available skills listing |
| 24 | Workspace Context | Computed | ~2 lines | CURRENT_SESSION_WORKSPACE path |

## Runtime Hook Injections

| Hook | When | Content | Notes |
|------|------|---------|-------|
| `UserPromptSubmit` | First user msg >10 words | Decomposition + Golden Path example | **⚠️ Mentions TaskStop at end** |
| `PreToolUse` (no matcher) | Every tool call | Guardrails, ledger, circuit-breaker | Reactive block |
| `PreToolUse` (Bash) | Bash calls | Artifact path rewrite, composio block | |
| `PreToolUse` (Task) | Task delegation | Skill awareness injection | |
| `PostToolUse` | After tool calls | Validation, guidance, caching | |

## ⚠️ Key Finding: TaskStop is a Built-in SDK Tool

`TaskStop` is part of the **Claude Code preset** (component #0). It is a built-in lifecycle
tool that Claude Code uses to stop running tasks/subagents. It is NOT defined by our code.

The problem: The Claude Code preset makes `TaskStop` available to the model as a callable tool.
When our system prompt mentions it (even negatively), the model sees it in its tool list,
associates it with 'task management', and tries to use it as part of its 'task decomposition'
workflow — fabricating task IDs to stop tasks that were never started.

## 🔑 Root Cause Analysis

The model's first action being TaskStop is driven by a combination of:

1. **Built-in tool visibility**: `TaskStop` exists in the Claude Code tool palette
2. **Prompt priming**: Two places explicitly mention TaskStop/lifecycle tools
   - `prompt_builder.py:324-326` (Execution Strategy section)
   - `hooks.py:1814` (UserPromptSubmit decomposition injection)
3. **Decomposition confusion**: The model interprets 'decompose this request' as
   'manage tasks' rather than 'plan execution steps', leading it to try lifecycle operations
4. **No strong positive first-action directive**: Nothing says 'YOUR FIRST TOOL CALL MUST BE Task()'
   with sufficient emphasis to override the tool palette's pull
