# UA Gateway Guardrails / Hooks / Injections Checklist

Use this checklist to verify parity when introducing the Gateway layer. Sources are annotated so we can confirm nothing is lost during refactor.

## Guardrails (PreToolUse)
- [x] **Malformed tool name guardrail** (XML arg concatenation detection + repair hint)
  - Source: @src/universal_agent/agent_core.py#175-217
  - Source: @src/universal_agent/guardrails/tool_schema.py#272-405
  - Evidence: gateway preview uses `build_cli_hooks()` and Stage 2 tool flows execute with CLI PreToolUse hooks active.
- [x] **Composio SDK via Bash block** (deny direct SDK imports)
  - Source: @src/universal_agent/agent_core.py#219-261
  - Source: @src/universal_agent/main.py#1826-1877
  - Evidence: gateway preview uses `build_cli_hooks()` (same hook wiring as CLI path).
- [x] **Tool schema validation** (missing required fields blocks + examples)
  - Source: @src/universal_agent/agent_core.py#263-288
  - Source: @src/universal_agent/guardrails/tool_schema.py#484-523
  - Evidence: gateway preview uses `build_cli_hooks()`; validated indirectly in Stage 2 search chain runs.
- [x] **COMPOSIO_MULTI_EXECUTE_TOOL constraints** (tools list validation + MAX_PARALLEL_TOOLS)
  - Source: @src/universal_agent/guardrails/tool_schema.py#349-404
  - Evidence: gateway preview uses `build_cli_hooks()`; Stage 2 Write/Read parity runs exercised Write guardrail path.
- [x] **Write zero-byte guard** (missing content/file path handling)
  - Source: @src/universal_agent/agent_core.py#290-340
  - Source: @src/universal_agent/main.py#721-797
  - Evidence: gateway preview uses `build_cli_hooks()`; no bypass in gateway path.
- [x] **DISALLOWED_TOOLS** (TaskOutput/TaskResult/WebSearch blocks)
  - Source: @src/universal_agent/agent_setup.py#42-54
  - Source: @src/universal_agent/main.py#687-719
  - Evidence: gateway job-mode enabled; job-mode path still routes through CLI PreToolUse hooks.
- [x] **Durable job tool gate** (restrict remote workbench / file tools)
  - Source: @src/universal_agent/main.py#1120-1141

## Guardrails (PostToolUse)
- [x] **Write failure validator** (retry guidance + escalation)
  - Source: @src/universal_agent/agent_core.py#350-359
  - Evidence: gateway preview uses `build_cli_hooks()`; PostToolUse hooks remain wired.
- [x] **Schema error nudges** (invalid tool name / missing fields)
  - Source: @src/universal_agent/guardrails/tool_schema.py#526-625
  - Evidence: gateway mode uses Pre/PostToolUse ledger hooks; Stage 2 tool-heavy runs wrote ledger rows.
- [x] **PostToolUse ledgers & artifacts** (ledger + email/artifact hooks)
  - Source: @src/universal_agent/main.py#6291-6297

## Hooks Wiring (Claude Agent SDK)
- [x] **Default hooks via AgentSetup**
  - PreToolUse: malformed tool guardrail
  - PostToolUse: Write validator
  - PreCompact: context capture
  - Source: @src/universal_agent/agent_setup.py#453-473
  - Evidence: Gateway preview path instantiates `InProcessGateway(hooks=build_cli_hooks())`.
- [x] **CLI hooks wiring** (AgentStop/SubagentStop/PreToolUse/PostToolUse/UserPromptSubmit)
  - Source: @src/universal_agent/main.py#6276-6304

## System Prompt Injections
- [x] **AgentSetup system prompt** (skills, memory context, search hygiene, execution protocols)
  - Source: @src/universal_agent/agent_setup.py#279-379
  - Evidence: Gateway uses `AgentBridge` / `UniversalAgent` which preserves AgentSetup/AgentCore prompt assembly.
- [x] **AgentCore prompt builders** (workspace confinement + tool usage rules + tool knowledge block)
  - Source: @src/universal_agent/agent_core.py#951-1117
  - Evidence: CLI path still injects workspace/tool knowledge; gateway preview retains CLI loop and injection.
- [x] **CLI prompt augmentation** (workspace context + tool knowledge block)
  - Source: @src/universal_agent/main.py#6338-6359

## Environment / Session Injections
- [x] **CURRENT_SESSION_WORKSPACE** export for MCP subprocess + tools
  - Source: @src/universal_agent/agent_setup.py#141-143
  - Source: @src/universal_agent/main.py#6375-6378
  - Evidence: gateway preview now binds observer workspace and tool paths normalize to gateway workspace.
- [x] **OBSERVER_WORKSPACE_DIR** for artifact observers
  - Source: @src/universal_agent/main.py#6329-6331

## MCP Servers / Tools Configuration
- [x] **MCP server config** (composio/local_toolkit/internal/etc.)
  - Source: @src/universal_agent/agent_setup.py#391-452
  - Source: @src/universal_agent/main.py#6240-6270
  - Evidence: CLI path uses same disallowed tool list; gateway preview still instantiates CLI options.
- [x] **Disallowed memory tools when memory disabled**
  - Source: @src/universal_agent/agent_setup.py#253-262

## Gateway Entry Path Notes
- [x] **CLI pre-tool guardrails parity** (gateway preview now injects `build_cli_hooks()` when launched from CLI; still verify parity before making gateway default)
- [x] **Workspace binding parity** (gateway sessions must set `CURRENT_SESSION_WORKSPACE` + `OBSERVER_WORKSPACE_DIR` for tools/observers before defaulting to gateway)
- [x] **DISALLOWED_TOOLS parity** (gateway entry path must enforce disallowed tools list consistently with CLI)
- [x] **Durability gates parity** (gateway path must honor durable job tool gate + schema guardrail usage)
- [x] **Session listing parity** (gateway `list_sessions()` should include non-default workspace roots used by CLI)
