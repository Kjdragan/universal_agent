# 004: Claude Agent SDK Subagent Durability Research (anthropics/claude-agent-sdk-python)

Date: 2026-01-02
Scope: Subagent execution (Task tool / TaskOutput flow) and durability across restarts
Repo: https://github.com/anthropics/claude-agent-sdk-python

## Executive Summary
The Python SDK exposes subagents and tool use in its message model and hooks, but it does not provide an explicit persistence or recovery mechanism for in-flight subagent tasks across process restarts. The only built-in durability feature documented in this repo is file checkpointing/rewind, which is scoped to filesystem changes. Session resumption and forking are supported at the conversation level, not as a task-level replay mechanism.

## Findings by Question

1) Is a Task/subagent invocation resumable after the orchestrator process dies and restarts?
Answer: No explicit resumability is documented in this repo. The SDK models tool calls as ToolUseBlock/ToolResultBlock pairs and subagent lifecycle as a hook event (SubagentStop), but there is no persistence of an in-flight Task or TaskOutput state across restarts.

Evidence:
- Tool use is modeled as a ToolUseBlock with an id, and ToolResultBlock references it by tool_use_id, which implies in-session correlation rather than durable storage.
  - Source: src/claude_agent_sdk/types.py
  - Quote: "class ToolUseBlock: ... id: str ... name: str ... input: dict[str, Any]" and "class ToolResultBlock: ... tool_use_id: str" (src/claude_agent_sdk/types.py)
- Subagent lifecycle exposure is via hook input, not a recoverable task state.
  - Source: src/claude_agent_sdk/types.py
  - Quote: "class SubagentStopHookInput(BaseHookInput): ... hook_event_name: Literal['SubagentStop']" (src/claude_agent_sdk/types.py)

2) Does the SDK provide any official mechanism to persist/recover Task state?
Answer: The only explicit durability mechanism in this repo is file checkpointing and rewind, which reverts filesystem changes to a prior UserMessage UUID. No SDK API in this repo persists or replays subagent/task execution state.

Evidence:
- File checkpointing is a dedicated feature with an explicit toggle and rewind API.
  - Source: src/claude_agent_sdk/types.py
  - Quote: "enable_file_checkpointing: bool = False" and the comment "Enable file checkpointing to track file changes during the session. When enabled, files can be rewound to their state at any user message using `ClaudeSDKClient.rewind_files()`." (src/claude_agent_sdk/types.py)
- Rewind files API shows scope is filesystem, not task state.
  - Source: src/claude_agent_sdk/client.py
  - Quote: "Rewind tracked files to their state at a specific user message." and "Requires: enable_file_checkpointing=True ... extra_args={'replay-user-messages': None} to receive UserMessage objects with uuid" (src/claude_agent_sdk/client.py)
- Changelog confirms file checkpointing is the added durability feature.
  - Source: CHANGELOG.md
  - Quote: "File checkpointing and rewind: Added enable_file_checkpointing option ... and rewind_files(user_message_id) method ... This enables reverting file changes made during a session back to a specific checkpoint" (CHANGELOG.md)

3) Are there documented best practices for long-running workflows (hours+) re: subagents, tool receipts, checkpointing?
Answer: Not in this repo. There is no documentation here describing long-running workflows, tool receipt persistence, or explicit subagent durability best practices. The repo focuses on SDK usage, hooks, permissions, and file checkpointing.

Evidence:
- The README links to external docs for SDK usage and hooks but does not include long-running durability guidance.
  - Source: README.md
  - Quote: "See the [Claude Agent SDK documentation] ... for more information." and "Hooks ... Read more in [Claude Code Hooks Reference]" (README.md)

4) If Task is not resumable, is there an SDK-supported alternative pattern?
Answer: The repo suggests subagents can be defined programmatically and sessions can be resumed or forked, but it does not provide a built-in durable subagent workflow. The practical alternative is to persist subagent inputs/outputs outside the SDK (files/DB), and re-run subagent work using saved inputs after a restart.

Evidence:
- Programmatic subagents are an SDK feature, but the repo does not mention durability.
  - Source: CHANGELOG.md
  - Quote: "Programmatic subagents: Subagents can now be defined inline in code using the agents option, enabling dynamic agent creation without filesystem dependencies." (CHANGELOG.md)
- Session control exists at conversation level (resume/fork), not task state.
  - Source: src/claude_agent_sdk/types.py
  - Quote: "continue_conversation: bool = False" and "resume: str | None = None" and "fork_session: bool = False" (src/claude_agent_sdk/types.py)

## Keyword Scan Summary (Explicit Support vs Developer Responsibility)

### Explicitly Supported (by SDK)
1) Conversation resume/fork flags (not task-level resume).
   - Source: src/claude_agent_sdk/types.py
   - Quote: "continue_conversation: bool = False", "resume: str | None = None", "fork_session: bool = False" (src/claude_agent_sdk/types.py)
2) File checkpointing/rewind (filesystem-only durability).
   - Source: src/claude_agent_sdk/types.py
   - Quote: "enable_file_checkpointing: bool = False" and "Enable file checkpointing to track file changes during the session. When enabled, files can be rewound to their state at any user message using `ClaudeSDKClient.rewind_files()`." (src/claude_agent_sdk/types.py)
   - Source: src/claude_agent_sdk/client.py
   - Quote: "Rewind tracked files to their state at a specific user message." and "Requires: enable_file_checkpointing=True ... extra_args={'replay-user-messages': None}" (src/claude_agent_sdk/client.py)
3) Subagent lifecycle signal (stop hook), but no persistence.
   - Source: src/claude_agent_sdk/types.py
   - Quote: "class SubagentStopHookInput(BaseHookInput): ... hook_event_name: Literal['SubagentStop']" (src/claude_agent_sdk/types.py)

### Not Explicitly Supported / Not Present in Repo
1) TaskOutput / Task tool / durable / restart / event history: no repo mentions found in SDK code/docs searched.
2) Replay or resume of in-flight subagent tasks: no SDK API or documented mechanism for task replay or task state recovery.

## Implications for Universal Agent
1) Treat in-flight Task/subagent IDs as ephemeral. On restart, expect TaskOutput to fail and re-run subagent work using persisted inputs.
2) To make subagents durable, persist their inputs/outputs to workspace or DB at boundaries and implement a replay/restart mechanism in the orchestrator (as you are doing with the durable ledger/checkpointing).
3) File checkpointing can be used to rewind filesystem changes but does not preserve tool execution or subagent state.

## Source References
- README.md
- CHANGELOG.md
- src/claude_agent_sdk/types.py
- src/claude_agent_sdk/client.py
