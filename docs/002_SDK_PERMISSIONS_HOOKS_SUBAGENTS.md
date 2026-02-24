# 002 - Claude Agent SDK: Permissions, Hooks, and Subagent Architecture

> **Source of truth** for Claude Agent SDK (Python) and Claude Code CLI behavior
> regarding tool permissions, hook lifecycle, and subagent context detection.
>
> Last verified against: `claude-agent-sdk-python` + `claude-code` (Feb 2026)

---

## 1. Tool Permission Evaluation Order

The SDK evaluates tool permissions in this order:

1. **`disallowed_tools`** (SDK-level) — hard block, tool is hidden from ALL agents
2. **PreToolUse hooks** — can `block` or `allow` tool calls dynamically
3. **Permission rules** — static allow/deny rules
4. **Permission mode** — `acceptEdits`, etc.
5. **`can_use_tool` callback** — dynamic per-call permission decisions

**Critical**: `disallowed_tools` is a **hard SDK-level block**. If a tool is in this list,
it is invisible to ALL agents (primary AND subagents). Hooks never fire for it.
The agent cannot even attempt to call it.

## 2. ClaudeAgentOptions Key Fields

```python
ClaudeAgentOptions(
    allowed_tools=["Read", "Write", ...],      # Allowlist (optional)
    disallowed_tools=["tool_a", "tool_b"],      # Hard block for ALL agents
    permission_mode="acceptEdits",              # Auto-accept file edits
    can_use_tool=my_callback,                   # Dynamic per-call permission
    agents={                                     # Programmatic subagent definitions
        "my-agent": AgentDefinition(...)
    },
    hooks={                                      # Lifecycle hooks
        "PreToolUse": [HookMatcher(...)],
    },
    add_dirs=[".claude"],                        # Load filesystem agent definitions
    setting_sources=["project"],                 # Enable .claude/agents/*.md
    env={"KEY": "value"},                        # Environment variables
)
```

## 3. disallowed_tools vs Hook-Level Blocking

### Problem Pattern (Bug We Hit)

Putting tools in `disallowed_tools` that subagents need:

```python
# BAD: This blocks run_research_phase for ALL agents including subagents
disallowed_tools = ["mcp__internal__run_research_phase"]
```

The subagent's `.claude/agents/*.md` `tools:` frontmatter cannot override `disallowed_tools`.
If a tool is in both the agent's `tools:` list AND `disallowed_tools`, the SDK blocks it.

### Solution: Prompt-Level Delegation Only

**Hook-level blocking of shared tools is NOT viable.** Subagent detection in
PreToolUse hooks is unreliable for foreground Task calls (see Section 5).

```python
# constants.py

# SDK-level: truly banned for everyone (hallucinated, deprecated, dangerous)
DISALLOWED_TOOLS = [
    "TaskOutput", "TaskResult",           # Hallucinated tools
    "mcp__local_toolkit__*",              # Deprecated aliases
    "mcp__composio__COMPOSIO_CRAWL_*",    # Banned crawl tools
]

# Hook-level: INTENTIONALLY EMPTY.
# Subagent detection is unreliable. Rely on prompt-level delegation.
PRIMARY_ONLY_BLOCKED_TOOLS: list[str] = []
```

For tools the primary should delegate (not call directly), use **prompt instructions**
in `prompt_builder.py` instead of hook-level blocking. The prompt tells the primary
to delegate research to the specialist. This is a soft guardrail but the only one
that doesn't break the subagent pipeline.

## 4. PreToolUse Hook Input Schema

The `PreToolUseHookInput` TypedDict contains these fields:

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | `str` | Current session ID |
| `transcript_path` | `str` | Path to the transcript file |
| `cwd` | `str` | Current working directory |
| `permission_mode` | `str \| None` | Permission mode |
| `hook_event_name` | `Literal["PreToolUse"]` | Always "PreToolUse" |
| `tool_name` | `str` | Name of the tool being called |
| `tool_input` | `dict` | Tool input parameters |
| `tool_use_id` | `str` | Unique ID for this tool use |

### What is NOT in PreToolUseHookInput

- **`parent_tool_use_id`** — NOT present. Only exists on streamed `UserMessage` objects.
- **`agent_id`** — NOT present.
- **`agent_type`** — NOT present.
- **`is_subagent`** — NOT present.

## 5. Detecting Subagent Context in Hooks

### The Only Reliable Signal: `transcript_path`

Primary agent and subagents get **different `transcript_path` values**.
Track the first transcript path seen (primary), then compare:

```python
class MyHooks:
    def __init__(self):
        self._primary_transcript_path = None
        self._seen_transcript_paths = set()

    async def on_pre_tool_use(self, input_data, tool_use_id, context):
        # Track transcript paths
        transcript_path = str(input_data.get("transcript_path", "") or "")
        if transcript_path:
            if self._primary_transcript_path is None:
                self._primary_transcript_path = transcript_path
            self._seen_transcript_paths.add(transcript_path)

        # Detect subagent context
        is_subagent = (
            bool(self._primary_transcript_path)
            and bool(transcript_path)
            and transcript_path != self._primary_transcript_path
        )

        if tool_name in PRIMARY_ONLY_BLOCKED_TOOLS:
            if is_subagent:
                pass  # Allow for subagents
            else:
                return {"decision": "block", ...}  # Block for primary
```

### What Does NOT Work

- **`parent_tool_use_id`**: Not in `PreToolUseHookInput`. Always `None`.
- **`can_use_tool` callback**: `ToolPermissionContext` has no subagent info.
  Only contains `signal` (always None) and `suggestions` (list of PermissionUpdate).

## 6. AgentDefinition (Programmatic)

```python
@dataclass
class AgentDefinition:
    description: str                                          # When to use this agent
    prompt: str                                               # System prompt
    tools: list[str] | None = None                           # Allowlist (inherits all if None)
    model: Literal["sonnet", "opus", "haiku", "inherit"] | None = None
```

- `tools` acts as an **allowlist**. If omitted, the subagent inherits all parent tools.
- `tools` cannot override `disallowed_tools` — SDK blocks take precedence.
- No `disallowed_tools` field on `AgentDefinition` in the Python SDK dataclass.

## 7. Filesystem Agent Definitions (.claude/agents/*.md)

### Supported Frontmatter Fields

```yaml
---
name: research-specialist
description: |
  Sub-agent for multi-mode research.
tools: Read, Bash, mcp__internal__run_research_phase
model: opus
disallowedTools: tool_a, tool_b    # Added in Claude Code v2.0.30
---
```

| Field | Description |
|-------|-------------|
| `name` | Agent identifier (matches subagent_type in Task tool) |
| `description` | Natural language description of when to use this agent |
| `tools` | Comma-separated allowlist of tools |
| `model` | Model override (sonnet, opus, haiku, inherit) |
| `disallowedTools` | Per-agent tool blocking (v2.0.30+) |

### Loading

Filesystem agents are loaded when:
- `add_dirs` includes the `.claude` directory
- `setting_sources` includes `"project"`

```python
ClaudeAgentOptions(
    add_dirs=[os.path.join(src_dir, ".claude")],
    setting_sources=["project"],
)
```

### Precedence

Filesystem definitions (`.claude/agents/*.md`) and programmatic definitions
(`ClaudeAgentOptions.agents`) can coexist. Programmatic definitions take precedence
if both define the same agent name.

## 8. Task Tool and run_in_background

The `Task` tool spawns subagents. Key parameters:

| Parameter | Type | Description |
|-----------|------|-------------|
| `subagent_type` | `str` | Matches agent name from definitions |
| `description` | `str` | What the subagent should do |
| `prompt` | `str` | The user message sent to the subagent |
| `run_in_background` | `bool` | Run as async background task |

### run_in_background Behavior

When `run_in_background: true`:
- The Task tool returns immediately with an `agentId` and `output_file` path
- The subagent runs as a sidechain process
- Output goes to `/tmp/claude-*/tasks/{agentId}.output`
- The primary agent must poll for completion (wastes turns)
- The subagent's `cwd` is the repo root, NOT the session workspace

**Lesson learned**: For sequential pipeline tasks (research → report → PDF),
NEVER use `run_in_background`. The primary agent wastes turns polling with
`sleep && tail` instead of waiting for the result synchronously.

### Guardrail: Strip run_in_background for Pipeline Tasks

```python
# In _normalize_tool_input (guardrails/tool_schema.py)
_FOREGROUND_ONLY_SUBAGENTS = {"research-specialist", "report-writer"}
if subagent_type in _FOREGROUND_ONLY_SUBAGENTS and tool_input.get("run_in_background"):
    updated = dict(tool_input)
    updated.pop("run_in_background", None)
    return updated
```

## 9. Subagent Workspace Context

### Problem

Subagents don't automatically know `CURRENT_SESSION_WORKSPACE`.
Their `cwd` is the repo root. When they fall back to Bash/Write
(e.g., if a pipeline tool is blocked), files scatter to the repo root.

### Solution: Inject Workspace via PreToolUse Hook on Task

The `on_pre_task_skill_awareness` hook fires before the Task tool executes.
Inject workspace path into the subagent's `systemMessage`:

```python
async def on_pre_task_skill_awareness(input_data, tool_use_id, context):
    workspace_path = OBSERVER_WORKSPACE_DIR or os.getenv("CURRENT_SESSION_WORKSPACE", "")
    if workspace_path:
        combined_context = (
            f"# SESSION WORKSPACE (MANDATORY)\n"
            f"CURRENT_SESSION_WORKSPACE: {workspace_path}\n"
            f"ALL file outputs MUST go under this directory.\n"
        )
    # ... inject via return {"systemMessage": combined_context}
```

Also declare workspace expectations in `.claude/agents/*.md`:

```markdown
## SESSION WORKSPACE (CRITICAL)
- The system injects `CURRENT_SESSION_WORKSPACE` in your context.
- ALL file outputs MUST use absolute paths under this directory.
- NEVER write files relative to cwd or the repo root.
```

## 10. Hook Events Reference

| Event | Fires When | Key Use Cases |
|-------|------------|---------------|
| `PreToolUse` | Before any tool call | Block/allow tools, rewrite inputs, inject context |
| `PostToolUse` | After tool completion | Observe results, save artifacts, compliance checks |
| `SubagentStart` | Subagent spawned | Has `agent_id`, `agent_type` |
| `SubagentStop` | Subagent finished | Capture results |
| `PreCompact` | Before context compaction | Preserve critical context |
| `UserPromptSubmit` | User sends a message | Reset counters, setup logging |

### Hook Return Values (PreToolUse)

```python
# Allow (default - return empty dict)
return {}

# Block
return {
    "decision": "block",
    "systemMessage": "Explanation for the agent",
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "Why it was blocked",
    },
}

# Rewrite tool input
return {"tool_input": modified_input_dict}

# Inject system message (without blocking)
return {"systemMessage": "Additional context for the agent"}
```

## 11. Composio Tool Naming Convention

Composio tools follow the pattern: `mcp__composio__COMPOSIO_{ACTION}_{SUBJECT}`

Examples:
- `mcp__composio__COMPOSIO_SEARCH_WEB`
- `mcp__composio__COMPOSIO_SEARCH_NEWS`
- `mcp__composio__COMPOSIO_CRAWL_WEBPAGE`
- `mcp__composio__COMPOSIO_FETCH_URL`
- `mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL`

Internal MCP tools: `mcp__internal__{tool_name}`

## 12. Common Pitfalls

### 1. Putting subagent-needed tools in disallowed_tools
**Symptom**: Subagent says "tool is blocked" or silently can't find the tool.
**Fix**: Move to hook-level blocking (PRIMARY_ONLY_BLOCKED_TOOLS).

### 2. Not tracking transcript_path in hook classes
**Symptom**: Subagent detection always returns False; hooks block subagent tool calls.
**Fix**: Capture `_primary_transcript_path` on first hook call.

### 3. Checking parent_tool_use_id in hooks
**Symptom**: Subagent detection always returns False.
**Fix**: Don't check it — it's not in PreToolUseHookInput. Use transcript_path.

### 4. Using run_in_background for sequential pipeline tasks
**Symptom**: Primary agent wastes 5+ turns polling with `sleep && tail`.
**Fix**: Strip `run_in_background` via guardrail for pipeline subagents.

### 5. Not injecting workspace path into subagent context
**Symptom**: Files created at repo root instead of session workspace.
**Fix**: Inject CURRENT_SESSION_WORKSPACE via PreToolUse hook on Task tool.

### 6. Composio crawl/fetch tools bypassing Crawl4AI pipeline
**Symptom**: Subagent uses Composio URL fetchers instead of Crawl4AI Cloud API.
**Fix**: Add all COMPOSIO_CRAWL_* and COMPOSIO_FETCH_* to SDK-level DISALLOWED_TOOLS.

---

## Appendix: Version Notes

- **Claude Code v2.0.30**: Added `disallowedTools` to custom agent definitions (filesystem)
- **Claude Code v2.0.49**: Fixed subagent permissions issues
- **Claude Agent SDK Python**: `AgentDefinition` dataclass has `description`, `prompt`, `tools`, `model`
