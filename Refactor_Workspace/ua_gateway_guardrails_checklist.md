# UA Gateway Guardrails / Hooks / Injections Checklist

Use this checklist to verify parity when introducing the Gateway layer. Sources are annotated so we can confirm nothing is lost during refactor.

## Guardrails (PreToolUse)
- [ ] **Malformed tool name guardrail** (XML arg concatenation detection + repair hint)
  - Source: @src/universal_agent/agent_core.py#175-217
  - Source: @src/universal_agent/guardrails/tool_schema.py#272-405
- [ ] **Composio SDK via Bash block** (deny direct SDK imports)
  - Source: @src/universal_agent/agent_core.py#219-261
  - Source: @src/universal_agent/main.py#1826-1877
- [ ] **Tool schema validation** (missing required fields blocks + examples)
  - Source: @src/universal_agent/agent_core.py#263-288
  - Source: @src/universal_agent/guardrails/tool_schema.py#484-523
- [ ] **COMPOSIO_MULTI_EXECUTE_TOOL constraints** (tools list validation + MAX_PARALLEL_TOOLS)
  - Source: @src/universal_agent/guardrails/tool_schema.py#349-404
- [ ] **Write zero-byte guard** (missing content/file path handling)
  - Source: @src/universal_agent/agent_core.py#290-340
  - Source: @src/universal_agent/main.py#721-797
- [ ] **DISALLOWED_TOOLS** (TaskOutput/TaskResult/WebSearch blocks)
  - Source: @src/universal_agent/agent_setup.py#42-54
  - Source: @src/universal_agent/main.py#687-719
- [ ] **Durable job tool gate** (restrict remote workbench / file tools)
  - Source: @src/universal_agent/main.py#1120-1141

## Guardrails (PostToolUse)
- [ ] **Write failure validator** (retry guidance + escalation)
  - Source: @src/universal_agent/agent_core.py#350-359
- [ ] **Schema error nudges** (invalid tool name / missing fields)
  - Source: @src/universal_agent/guardrails/tool_schema.py#526-625
- [ ] **PostToolUse ledgers & artifacts** (ledger + email/artifact hooks)
  - Source: @src/universal_agent/main.py#6291-6297

## Hooks Wiring (Claude Agent SDK)
- [ ] **Default hooks via AgentSetup**
  - PreToolUse: malformed tool guardrail
  - PostToolUse: Write validator
  - PreCompact: context capture
  - Source: @src/universal_agent/agent_setup.py#453-473
- [ ] **CLI hooks wiring** (AgentStop/SubagentStop/PreToolUse/PostToolUse/UserPromptSubmit)
  - Source: @src/universal_agent/main.py#6276-6304

## System Prompt Injections
- [ ] **AgentSetup system prompt** (skills, memory context, search hygiene, execution protocols)
  - Source: @src/universal_agent/agent_setup.py#279-379
- [ ] **AgentCore prompt builders** (workspace confinement + tool usage rules + tool knowledge block)
  - Source: @src/universal_agent/agent_core.py#951-1117
- [ ] **CLI prompt augmentation** (workspace context + tool knowledge block)
  - Source: @src/universal_agent/main.py#6338-6359

## Environment / Session Injections
- [ ] **CURRENT_SESSION_WORKSPACE** export for MCP subprocess + tools
  - Source: @src/universal_agent/agent_setup.py#141-143
  - Source: @src/universal_agent/main.py#6375-6378
- [ ] **OBSERVER_WORKSPACE_DIR** for artifact observers
  - Source: @src/universal_agent/main.py#6329-6331

## MCP Servers / Tools Configuration
- [ ] **MCP server config** (composio/local_toolkit/internal/etc.)
  - Source: @src/universal_agent/agent_setup.py#391-452
  - Source: @src/universal_agent/main.py#6240-6270
- [ ] **Disallowed memory tools when memory disabled**
  - Source: @src/universal_agent/agent_setup.py#253-262

## Gateway Entry Path Notes
- [ ] **CLI pre-tool guardrails parity** (gateway preview now injects `build_cli_hooks()` when launched from CLI; still verify parity before making gateway default)
- [ ] **Workspace binding parity** (gateway sessions must set `CURRENT_SESSION_WORKSPACE` + `OBSERVER_WORKSPACE_DIR` for tools/observers before defaulting to gateway)
- [ ] **DISALLOWED_TOOLS parity** (gateway entry path must enforce disallowed tools list consistently with CLI)
- [ ] **Durability gates parity** (gateway path must honor durable job tool gate + schema guardrail usage)
- [ ] **Session listing parity** (gateway `list_sessions()` should include non-default workspace roots used by CLI)
