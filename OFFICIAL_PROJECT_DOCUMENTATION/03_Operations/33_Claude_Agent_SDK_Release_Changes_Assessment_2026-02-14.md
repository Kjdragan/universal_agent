# 33 Claude Agent SDK Release Changes Assessment (January 24 to February 14, 2026)

## Status
- State: **DONE / CLOSED**
- Closed on: **February 14, 2026**

## Scope
- Date window reviewed: **January 24, 2026 through February 14, 2026**.
- SDKs reviewed:
  - **Python**: `claude-agent-sdk` (PyPI) + `anthropics/claude-agent-sdk-python` (Git tags / changelog).
  - **TypeScript**: `@anthropic-ai/claude-agent-sdk` (npm) + `anthropics/claude-agent-sdk-typescript` (changelog).
- Source of truth:
  - **Published release timestamps**: PyPI JSON API and npm registry metadata.
  - **Change descriptions**: upstream `CHANGELOG.md` files (pinned to release tags when possible).
- This report focuses on **meaningful functional deltas**; many releases in this window are “parity bumps” to match Claude Code CLI versions.

## Work Plan (Executed)
1. Identify all published SDK versions in the window (Python + TypeScript) and their publish timestamps.
2. Extract changelog entries for all versions in scope and separate “parity-only” releases from functional releases.
3. Map meaningful deltas onto Universal Agent integration points (hooks, tool annotations, permissions, MCP reliability, UI streaming).
4. Verify local environment version alignment in this repo’s `.venv` (no product-code changes).
5. Produce this numbered document under `OFFICIAL_PROJECT_DOCUMENTATION/`.

## Local Repo State (Universal Agent)
- Dependency declaration:
  - `pyproject.toml` currently allows `claude-agent-sdk>=0.1.18`.
  - `uv.lock` currently resolves `claude-agent-sdk==0.1.35` (lock is **not** updated by this report).
- `.venv` verification (as of **2026-02-14**):
  - Installed `claude-agent-sdk==0.1.36`.
  - Bundled Claude Code CLI version inside the SDK: `2.1.42`.
  - `ClaudeAgentOptions` includes `thinking` and `effort` fields (and `max_thinking_tokens` is marked deprecated upstream).

## Executive Findings
- The highest-leverage Python SDK changes for UA in this window are:
  1. **Hook surface expansion** (new hook events + richer hook payloads), which can directly improve UA’s **UI event streaming**, **guardrail accuracy**, and **subagent observability**.
  2. **MCP tool annotations** in `@tool(..., annotations=...)`, which can replace UA’s current heuristic read/write classification and strengthen “safe by default” behavior.
  3. **Extended thinking controls** (`thinking`, `effort`) which can become a first-class UA capability knob per interface/phase (CLI vs Web vs URW harness), if we wire it into `ClaudeAgentOptions` construction.
  4. `ClaudeSDKClient.get_mcp_status()` which enables real-time MCP health checks and can reduce “tool loop” failure modes caused by MCP connectivity drift.
- TypeScript SDK deltas are mostly parity bumps, but add a few concepts worth copying into UA:
  - Programmatic debug logging options (`debug`, `debugFile`).
  - MCP server introspection and management (`reconnectMcpServer`, `toggleMcpServer`) which Python has only partially (status querying).
  - More hook events (e.g. `TeammateIdle`, `TaskCompleted`) that suggest where the platform is headed for richer lifecycle reporting.

## Python SDK Release Timeline (PyPI `claude-agent-sdk`)

| Version | Published (UTC) | Highlights |
|---|---:|---|
| `0.1.23` | 2026-01-27T01:46:16Z | `ClaudeSDKClient.get_mcp_status()` (MCP connection introspection); CLI `2.1.20` |
| `0.1.24` | 2026-01-28T07:09:48Z | CLI `2.1.22` |
| `0.1.25` | 2026-01-29T01:20:17Z | CLI `2.1.23` |
| `0.1.26` | 2026-01-30T20:48:54Z | `PostToolUseFailure` hook event type; CLI `2.1.27` |
| `0.1.27` | 2026-01-31T23:48:29Z | CLI `2.1.29` |
| `0.1.28` | 2026-02-03T18:16:05Z | Fix: `AssistantMessage.error` population; CLI `2.1.30` |
| `0.1.29` | 2026-02-04T00:53:54Z | New hook events: `Notification`, `SubagentStart`, `PermissionRequest`; hook payload upgrades; CLI `2.1.31` |
| `0.1.30` | 2026-02-05T17:58:37Z | CLI `2.1.32` |
| `0.1.31` | 2026-02-06T02:01:51Z | `@tool(..., annotations=...)`; fix: large agent definitions (ARG_MAX) registration; CLI `2.1.33` |
| `0.1.32` | 2026-02-07T18:12:16Z | CLI `2.1.36` |
| `0.1.33` | 2026-02-07T19:19:53Z | CLI `2.1.37` |
| `0.1.34` | 2026-02-10T01:04:00Z | CLI `2.1.38` |
| `0.1.35` | 2026-02-10T23:21:04Z | CLI `2.1.39` |
| `0.1.36` | 2026-02-13T20:08:48Z | `ClaudeAgentOptions.thinking` + `effort`; CLI `2.1.42` |

## TypeScript SDK Release Timeline (npm `@anthropic-ai/claude-agent-sdk`)

| Version | Published (UTC) | Highlights |
|---|---:|---|
| `0.2.20` | 2026-01-27T00:40:02Z | Support `additionalDirectories` for loading `CLAUDE.md`; `CLAUDE_CODE_ENABLE_TASKS` env var |
| `0.2.21` | 2026-01-28T01:38:12Z | Richer `McpServerStatus`; `reconnectMcpServer()` / `toggleMcpServer()`; fix PermissionRequest hooks |
| `0.2.22` | 2026-01-28T06:34:59Z | Structured output fix (empty assistant messages) |
| `0.2.23` | 2026-01-29T00:19:32Z | Structured output validation error reporting fix |
| `0.2.25` | 2026-01-29T20:33:50Z | Parity bump |
| `0.2.26` | 2026-01-30T02:13:59Z | (no changelog entry) |
| `0.2.27` | 2026-01-30T20:14:04Z | `tool(..., annotations=...)` hints; `mcpServerStatus()` includes dynamic servers |
| `0.2.29` | 2026-01-31T20:27:45Z | Parity bump |
| `0.2.30` | 2026-02-03T16:53:01Z | `debug` / `debugFile`; PDF page-range reads (`pages`) and richer outputs (`parts`) |
| `0.2.31` | 2026-02-04T00:22:12Z | `stop_reason` in SDK result objects |
| `0.2.32` | 2026-02-05T17:21:47Z | Parity bump |
| `0.2.33` | 2026-02-06T00:33:00Z | New hook events (`TeammateIdle`, `TaskCompleted`); `sessionId` override |
| `0.2.34` | 2026-02-06T06:54:04Z | Parity bump |
| `0.2.36` | 2026-02-07T17:45:31Z | Parity bump |
| `0.2.37` | 2026-02-07T18:55:56Z | Parity bump |
| `0.2.38` | 2026-02-10T00:24:36Z | Parity bump |
| `0.2.39` | 2026-02-10T21:36:14Z | Parity bump |
| `0.2.40` | 2026-02-12T01:20:05Z | Parity bump |
| `0.2.41` | 2026-02-13T01:40:28Z | Parity bump |
| `0.2.42` | 2026-02-13T19:26:52Z | Parity bump |

## Meaningful Changes (Python) And How UA Can Use Them

### 1) `ClaudeAgentOptions.thinking` + `effort` (Python `0.1.36`)
**What changed**
- Adds explicit `thinking` configuration objects (adaptive/enabled/disabled) and an `effort` knob (`low|medium|high|max`).
- `thinking` takes precedence over `max_thinking_tokens` (which becomes deprecated).

**Concrete API shape (as shipped upstream)**
- `ThinkingConfigAdaptive`: `{ "type": "adaptive", "budget_tokens": int }`
- `ThinkingConfigEnabled`: `{ "type": "enabled", "budget_tokens": int }`
- `ThinkingConfigDisabled`: `{ "type": "disabled" }`
- `ClaudeAgentOptions` additions:
  - `thinking: ThinkingConfig | None`
  - `effort: Literal["low","medium","high","max"] | None`
  - `max_thinking_tokens` marked deprecated in favor of `thinking`

**UA leverage**
- UA already treats “thinking” as a surfaced concept in its event pipeline (`EventType.THINKING` in `src/universal_agent/agent_core.py`), but UA does not currently expose a clean “thinking policy” knob when building `ClaudeAgentOptions`.
- We can wire a UA-native configuration surface (env vars or ops config) into:
  - `src/universal_agent/agent_setup.py` at `ClaudeAgentOptions(...)` construction (starts at ~line 388).
  - `src/universal_agent/main.py` legacy options builder (starts at ~line 7098).
- Potential wins:
  - Lower latency/cost in “ops mode” runs (heartbeat, housekeeping) by disabling or lowering effort.
  - Higher quality in URW report generation phases by forcing effort higher only for drafting/editing steps.
  - Consistent policy across CLI, Web, and webhook-driven sessions.

**Engineering caveat**
- This should be implemented behind a UA feature flag or config defaults, because it affects cost, latency, and response structure (thinking blocks).

### 2) MCP tool annotations via `@tool(..., annotations=...)` (Python `0.1.31`)
**What changed**
- The SDK tool decorator can now attach MCP metadata hints (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`).

**UA leverage**
- UA currently infers read-only behavior using tool-name heuristics inside the workspace guard (`src/universal_agent/hooks.py` around the `READ_ONLY_TOOLS` and substring checks).
- If we annotate UA internal tools (e.g. `mcp__internal__list_directory`, `ua_memory_get`, etc.), we can:
  - Replace fragile name/substring logic with authoritative metadata.
  - Improve permission and safety UX (e.g., show “destructive” actions more clearly; block destructive tools by policy).
  - Enable a more principled retry/caching strategy for idempotent tools (durable execution can safely replay idempotent reads).

**Concrete candidates**
- Annotate internal file ops tools (`src/universal_agent/tools/local_toolkit_bridge.py`, `src/universal_agent/tools/memory.py`) as read-only vs destructive.
- Annotate “web fetch / crawl” as open-world.

### 3) Hook system expansion and payload upgrades (Python `0.1.26` + `0.1.29`)
**What changed**
- New hook events: `PostToolUseFailure`, `Notification`, `SubagentStart`, `PermissionRequest`.
- Hook payloads now consistently include `tool_use_id` for pre/post tool hooks, plus richer `SubagentStop` fields (agent id/type/transcript).

**Concrete payload deltas (useful for UA)**
- New hook event types (UA can register handlers for all of these):
  - `PostToolUseFailure`
  - `Notification`
  - `SubagentStart`
  - `PermissionRequest`
- Added/standardized fields in hook inputs:
  - `PreToolUseHookInput.tool_use_id`
  - `PostToolUseHookInput.tool_use_id`
  - `SubagentStopHookInput.agent_id`
  - `SubagentStopHookInput.agent_transcript_path`
  - `SubagentStopHookInput.agent_type`
- Added fields in hook outputs (enables stronger interventions):
  - `PreToolUse` output: `additionalContext`
  - `PostToolUse` output: `updatedMCPToolOutput`

**UA leverage**
- UA’s hook registration (`src/universal_agent/hooks.py` `AgentHookSet.build_hooks`) currently handles:
  - `PreToolUse`, `PostToolUse`, `PreCompact`, `UserPromptSubmit`, `AgentStop`, `SubagentStop`.
- We can:
  - Add `PostToolUseFailure` hook handling to reliably emit failure events (today we infer failures via `is_error` flags and string parsing in `emit_tool_result_event`).
  - Add `SubagentStart` handling to emit “subagent started” UI status events and correlate with `SubagentStop` using `agent_id`.
  - Add `Notification` handling to surface CLI/SDK notifications as UA `EventType.STATUS`.
  - Add `PermissionRequest` handling if/when UA moves away from `permission_mode=\"bypassPermissions\"` (or even to log and measure permission friction).

### 4) MCP health introspection (`ClaudeSDKClient.get_mcp_status`) (Python `0.1.23`)
**What changed**
- Adds a public, supported way to query MCP server connection status without reading private internals.

**UA leverage**
- UA currently does not query MCP health before tool calls; if an MCP server is disconnected, we tend to find out only after tool failures.
- We can use `get_mcp_status()` to:
  - Show MCP server health in UI (especially Composio MCP).
  - Gate certain workflows (e.g., “don’t start a research run if composio MCP is `needs-auth`”).
  - Improve ops diagnostics endpoints and reduce repeated tool-loop retries.

### 5) Large agent definitions no longer silently fail (Python `0.1.31`)
**What changed**
- Fixes registration failure for large agent payloads due to platform CLI arg limits (ARG_MAX) by sending definitions via stdin initialize control request.

**UA leverage**
- UA’s system prompt, skills XML, and capabilities registry can become quite large; this fix reduces the chance of “it looks like the agent started, but custom definitions never loaded.”
- This directly de-risks adding more structured content to prompts (skills, capabilities, policy) without hitting platform-specific cliffs.

### 6) `AssistantMessage.error` is correctly populated (Python `0.1.28`)
**What changed**
- The SDK now populates `AssistantMessage.error` reliably (previously it was reading the wrong data path).

**UA leverage**
- UA can stop relying on string heuristics for certain failure modes and instead:
  - detect error states directly on message objects
  - propagate richer error metadata to the UI and run logs

## Meaningful Changes (TypeScript) And How UA Can Use Them (Conceptually)
UA is Python-first today, but TS SDK changes reveal where upstream is investing. Two actions for UA:
1. Copy valuable concepts into UA’s Python integration where possible.
2. Track divergences where TS has capabilities Python lacks (e.g., MCP server toggle/reconnect).

Key ideas worth copying:
- Programmatic debug outputs (`debug`/`debugFile`) as an ops knob for “turn on deep CLI logging for a session id”.
- MCP management APIs (toggle/reconnect) suggest UA could provide “reconnect composio MCP” flows at the gateway layer.
- Richer hook lifecycle events indicate we should treat hook events as a first-class stream in UA, not just tool call/result.

## UA Adoption Plan (No Code Changes In This Report)

### Phase 1: Dependency and Smoke Validation (0.5 day)
1. Update `uv.lock` to `claude-agent-sdk==0.1.36` in a dedicated change (so `uv sync` reproduces the `.venv`).
2. Run UA minimal smoke:
   - CLI single turn.
   - Web UI session connect and one tool call.
   - One URW harness run with `Task` delegation.

### Phase 2: Tool Annotation Rollout (1-2 days)
1. Add `annotations=` to UA internal tools and classify as read-only/destructive/idempotent/open-world.
2. Update workspace guard to trust tool annotations instead of name heuristics.
3. Add a small regression test suite around annotation-driven enforcement.

### Phase 3: Hook Event Enrichment (1-2 days)
1. Add handlers + event emissions for:
   - `PostToolUseFailure`
   - `Notification`
   - `SubagentStart`
2. Extend UI event stream schema to render these cleanly.
3. Correlate subagent start/stop using `agent_id` and transcript paths.

### Phase 4: Thinking Policy Control (1 day + tuning)
1. Add UA config surface for `thinking` and `effort`.
2. Default to conservative settings and allow per-session override.
3. Add instrumentation (tokens, latency, cost) to compare policy choices.

### Phase 5: MCP Health UX (0.5-1 day)
1. Call `get_mcp_status()` and expose results in ops endpoint and UI.
2. Add “blocked by MCP needs-auth” user-facing messages instead of repeated failures.

## Sources
- Python SDK changelog (tagged):
  - https://raw.githubusercontent.com/anthropics/claude-agent-sdk-python/v0.1.36/CHANGELOG.md
- Python SDK release timestamps:
  - https://pypi.org/pypi/claude-agent-sdk/json
- TypeScript SDK changelog:
  - https://raw.githubusercontent.com/anthropics/claude-agent-sdk-typescript/main/CHANGELOG.md
- TypeScript SDK release timestamps:
  - https://registry.npmjs.org/@anthropic-ai/claude-agent-sdk
