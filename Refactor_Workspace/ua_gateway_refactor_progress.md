# Universal Agent Gateway Refactor - Progress Tracker

**Owner:** Cascade
**Started:** 2026-01-23
**Status:** Discovery & Planning

## Purpose
Track refactor progress, stage status, decisions, dependencies, and open questions while preserving the current CLI workflow.

## Stages (High-Level)
1. **Dependency Mapping & Baseline**
   - Confirm entry points, session wiring, URW integration points, and durability usage.
2. **Event Stream Normalization**
   - Ensure agent emits structured events consumed by CLI/Web.
3. **Gateway API (In-Process)**
   - Introduce gateway interface behind CLI/UI without network hop.
4. **Gateway Externalization**
   - Optional HTTP/WebSocket gateway with CLI defaulting to in-process path.
5. **URW as Meta-Client**
   - URW calls Gateway API instead of direct process_turn.
6. **Worker Pool + Lease Durability**
   - Integrate durable leases for distributed execution.

## Documentation Cadence
- **Update this file after each milestone or architectural decision.**
- Add entries to the **Progress Log** and **Decisions Log**.

## Progress Log
- 2026-01-23: Initial dependency map and Clawdbot feature inventory captured. Staged refactor plan drafted.
- 2026-01-23: CLI-only behaviors in main.py enumerated. Stage 1 minimal change set + gateway contract draft captured.
- 2026-01-24: Stage 1 wiring started: extracted run.log helpers + DualWriter to `cli_io.py`, centralized workspace binding via `execution_context.bind_workspace_env`, and added `trace_utils.write_trace` for trace persistence. Updated CLI + AgentSetup + URW harness entrypoints to use the shared workspace binding.
- 2026-01-24: Added `ExecutionSession` dataclass scaffold in `execution_session.py` (not yet integrated into call sites).
- 2026-01-24: Moved run.log parsing helpers and response summarization utilities into `cli_io.py`; main now uses shared helpers for local trace ID extraction and summary formatting.
- 2026-01-24: Extracted prompt input/session setup and job completion summary printing into `cli_io.py` (main now delegates prompt reads + completion summaries via shared helpers).
- 2026-01-24: Centralized observer workspace binding in `main.py` via `execution_context.bind_workspace` to set both env + `OBSERVER_WORKSPACE_DIR`.
- 2026-01-24: Added initial Gateway contract scaffolding in `src/universal_agent/gateway.py` (dataclasses + interface, no routing changes yet).
- 2026-01-24: Introduced `ExecutionSession` usage in CLI path (created in `setup_session`, passed into `process_turn`/`continue_job_run`), with `process_turn` now accepting an optional execution session for explicit context binding.
- 2026-01-24: Implemented in-process Gateway facade (`InProcessGateway`) backed by `AgentBridge` to create/resume sessions, stream `AgentEvent`s, and list sessions (no CLI routing changes yet).
- 2026-01-24: Created `ua_gateway_refactor_plan.md` to track staged execution plan, exit criteria, and validation gates.
- 2026-01-24: Added CLI `--use-gateway` flag (and `UA_USE_GATEWAY` env) to optionally route interactive CLI input through `InProcessGateway`; added minimal `AgentEvent` renderer for gateway preview path.
- 2026-01-24: Expanded gateway event renderer to collect TOOL_RESULT/STATUS/WORK_PRODUCT/ERROR metadata and updated guardrails checklist for gateway entry parity requirements.
- 2026-01-24: Added gateway event renderer parity output (tool call/result previews + execution summary) and enabled optional CLI workspace reuse for gateway sessions via `--gateway-use-cli-workspace`/`UA_GATEWAY_USE_CLI_WORKSPACE`. Gateway preview now injects CLI hook set for PreToolUse parity.
- 2026-01-24: Added gateway job-completion summary parity (via event-driven execution summary) plus improved session listing for non-default workspace roots; created `ua_gateway_smoke_tests.md` with a 3-case CLI/Gateway matrix.
- 2026-01-24: Added gateway job-completion summary writer for job runs (event-derived, saved as `job_completion_gateway_<session_id>.md`).
- 2026-01-24: Gateway job-completion summary now includes tool call breakdown + local toolkit trace IDs for parity with CLI trace reporting.
- 2026-01-24: Gateway preview now binds observer workspace to the gateway session (sets `CURRENT_SESSION_WORKSPACE` + `OBSERVER_WORKSPACE_DIR`).
- 2026-01-24: Smoke tests attempted (CLI default / gateway preview / gateway + CLI workspace) but blocked by missing `python-dotenv` dependency in the local environment.
- 2026-01-24: Installed `python-dotenv` in the project venv and reran smoke tests; CLI default + gateway preview (separate + CLI workspace) all reached interactive prompt and exited cleanly via `quit`.

## Decisions Log
- 2026-01-24: Gateway will wrap existing `AgentBridge` session tracking for Stages 1-3 to minimize behavior changes; revisit ownership after Gateway externalization.
- 2026-01-24: Keep both `server.py` and `api/server.py` through Stage 3; consolidate after Gateway is stable and externalized.

## Dependency Map (Summary)
- **CLI Entry:** `main.py` owns `process_turn` and CLI loop; `/harness` dispatches into URW harness.
- **Web/API Entry:** `api/server.py` + `api/agent_bridge.py` wrap `UniversalAgent.run_query`.
- **URW Harness:** `urw/harness_orchestrator.py` calls `process_turn` directly; `urw/integration.py` uses `UniversalAgentAdapter` (AgentSetup reuse per phase).
- **Agent Setup Assumptions:** `agent_setup.py` sets workspace env, MCP config, skill discovery, memory injection, default hooks; reused by `agent_core.UniversalAgent`.
- **Durability:** `durable/state.py` provides run/lease + step tracking used by CLI run loop in `main.py`.

## CLI-Only Behaviors To Preserve
- **run.log dual logging:** `DualWriter` mirrors stdout/stderr to `run.log` (used by prompt_toolkit + fallback recovery).
- **Workspace injection:** `CURRENT_SESSION_WORKSPACE` injected into system prompt for sub-agents and env for tools.
- **Observer workspace global:** `OBSERVER_WORKSPACE_DIR` drives artifact observers + tool hooks.
- **Trace persistence:** incremental `trace.json` writes during turns + final save at completion.
- **Auth pause:** CLI prompts user to press Enter after auth link.
- **Interactive input:** prompt_toolkit REPL with fallback to stdin for non-interactive mode.
- **Harness commands:** `/harness`, `/harness-test`, `/harness-template` command handling in CLI loop.
- **Execution summaries:** per-request summary + job completion summary with tool receipts and trace IDs.
- **run.log fallback:** harness output fallback reads recent `run.log` lines if response text missing.

## Claude Agent SDK Guardrails / Hooks / Injections (Critical)
- **Hooks wired via AgentSetup**: default hooks from `agent_core` (PreToolUse, PostToolUse, PreCompact) must be preserved.
- **Guardrails in agent_core**: malformed tool name guardrail, schema validation, zero-byte write protection, post-tool validation.
- **System prompt injections**: workspace + tool knowledge blocks added in `main.py` and `agent_setup.py`.
- **Disallowed tools list**: enforced in both `agent_setup.py` and `agent_core.py`.
- **Checklist**: See `Refactor_Workspace/ua_gateway_guardrails_checklist.md` for parity tracking.

### Guardrail / Hook Inventory (Claude Agent SDK)
- **agent_setup.py**
  - `DISALLOWED_TOOLS` list + memory tool blocking when memory disabled; passed into `ClaudeAgentOptions.disallowed_tools` @src/universal_agent/agent_setup.py#42-277.
  - System prompt assembly with skills, memory context, tool knowledge block (injected via `get_tool_knowledge_block`) @src/universal_agent/agent_setup.py#279-379.
  - Default hooks: `malformed_tool_guardrail_hook` (PreToolUse), `tool_output_validator_hook` (PostToolUse Write), `pre_compact_context_capture_hook` (PreCompact) @src/universal_agent/agent_setup.py#453-473.
- **agent_core.py**
  - PreToolUse guardrail: malformed tool names, Composio SDK via Bash, schema validation, zero-byte Write protections @src/universal_agent/agent_core.py#175-340.
  - PostToolUse validator: empty/failed Write retries + escalation @src/universal_agent/agent_core.py#350-359.
  - Main + subagent prompt builders inject tool usage rules, workspace confinement, and tool knowledge block @src/universal_agent/agent_core.py#951-1117.
- **guardrails/tool_schema.py**
  - `pre_tool_use_schema_guardrail`: XML arg concatenation block, schema validation, MAX_PARALLEL_TOOLS enforcement @src/universal_agent/guardrails/tool_schema.py#272-405.
  - `post_tool_use_schema_nudge`: schema error nudges for invalid tool calls @src/universal_agent/guardrails/tool_schema.py#526-625.
- **main.py (CLI path)**
  - PreToolUse guardrails: TaskOutput/TaskResult block, DISALLOWED_TOOLS block, empty Write param block with subagent detection, malformed tool name block @src/universal_agent/main.py#660-799.
  - Durable job tool gate + schema guardrail invocation @src/universal_agent/main.py#1120-1209.
  - CLI hooks wiring: AgentStop/SubagentStop/PreToolUse/PostToolUse/UserPromptSubmit in `ClaudeAgentOptions` @src/universal_agent/main.py#6240-6305.
  - System prompt augmentation with workspace + tool knowledge block @src/universal_agent/main.py#6338-6359.

## Open Questions
- Does URW need dedicated event subscription support (e.g., phase completion events) beyond current polling?

## Stage 1 Minimal Change Set (Dependency Hardening)
1. **Isolate CLI I/O/logging:** extract `DualWriter`, prompt loop, run.log fallback, summary printing into `cli_io.py` (no behavior change).
2. **Centralize workspace binding:** single helper to set `CURRENT_SESSION_WORKSPACE` + `OBSERVER_WORKSPACE_DIR` for all entrypoints.
3. **Session context object:** introduce `ExecutionSession` dataclass (workspace_dir, run_id, trace, runtime_db_conn) to pass explicitly.
4. **Trace persistence helper:** extract trace.json incremental + final write into a small utility.
5. **Gateway facade (no routing change yet):** add a `Gateway` class that wraps `process_turn` without changing callers.

### Stage 1 Extraction Validation (Call Sites)
- **Workspace binding**
  - CLI: env + observer set in `process_turn` @src/universal_agent/main.py#6363-6379.
  - AgentSetup: `initialize()` + `bind_workspace()` set `CURRENT_SESSION_WORKSPACE` @src/universal_agent/agent_setup.py#141-236.
  - Harness: phase toggles update `CURRENT_SESSION_WORKSPACE` @src/universal_agent/urw/harness_helpers.py#15-43.
- **CLI I/O + run.log**
  - `DualWriter` in CLI setup; run.log fallback in harness stop hook @src/universal_agent/main.py#119-2466.
  - Separate `ExecutionLogger` (bot adapter) also implements DualWriter @src/universal_agent/bot/execution_logger.py#1-45.
  - Operator CLI tails run.log for durable runs @src/universal_agent/agent_operator/operator_cli.py#189-204.
- **Trace persistence**
  - CLI incremental + final trace.json writes @src/universal_agent/main.py#6578-7822.
  - Core agent saves trace on completion (`_save_trace`) @src/universal_agent/agent_core.py#1607-1614.
  - Transcript builder consumes trace.json @src/universal_agent/transcript_builder.py#1-224.

## Gateway Contract Draft (In-Process)
- `Gateway.create_session(user_id, workspace_dir=None) -> GatewaySession`
- `Gateway.resume_session(session_id) -> GatewaySession`
- `Gateway.execute(session: GatewaySession, request: GatewayRequest) -> AsyncIterator[GatewayEvent]`
- `Gateway.run_query(session, request) -> GatewayResult` (optional non-streaming convenience)
- `Gateway.list_sessions() -> list[GatewaySessionSummary]`
- Event model aligns with `agent_core.AgentEvent` (TEXT/TOOL_CALL/TOOL_RESULT/STATUS/AUTH_REQUIRED/WORK_PRODUCT)
- CLI owns rendering + I/O; gateway only emits events and updates trace/session metadata.

### Gateway Contract Validation (API + URW)
- **API bridge expectations**
  - `AgentBridge.create_session()` creates workspace + initializes `UniversalAgent` @src/universal_agent/api/agent_bridge.py#40-64.
  - `resume_session()` reuses workspace, fresh agent instance @src/universal_agent/api/agent_bridge.py#66-87.
  - `execute_query()` streams `AgentEvent` → `WebSocketEvent` mapping and emits `QUERY_COMPLETE` @src/universal_agent/api/agent_bridge.py#89-107.
  - `list_sessions()` infers completion via `trace.json`; consumers expect workspace file taxonomy (work_products/search_results/workbench_activity) @src/universal_agent/api/agent_bridge.py#134-197.
- **API event schema**
  - WebSocket event types mirror `AgentEvent` + control events (CONNECTED, QUERY_COMPLETE, PONG) @src/universal_agent/api/events.py#16-35.
  - Approval event schema exists for URW phase approvals @src/universal_agent/api/events.py#112-225.
- **URW adapter expectations**
  - `UniversalAgentAdapter` uses `AgentSetup` + `UniversalAgent.run_query()` event stream; aggregates TEXT/TOOL_CALL/TOOL_RESULT/WORK_PRODUCT/AUTH_REQUIRED @src/universal_agent/urw/integration.py#107-203.
  - Workspace rebinding across phases relies on `AgentSetup.bind_workspace()` (session reuse) @src/universal_agent/urw/integration.py#118-134.
  - Harness orchestrator still calls `process_turn` directly (Stage 1 keeps as-is) @src/universal_agent/urw/harness_orchestrator.py#122-200.

## Next Steps
- Validate Stage 1 extraction points (CLI I/O helpers + session context) against actual call sites.
- Review gateway contract with stakeholders (CLI + API + URW owners).
- Catalog all Claude Agent SDK guardrails/hooks/injections to ensure parity in gateway flow.
- Confirm missing Clawdbot features (lanes/sandboxing) by checking the Clawdbot repo directly.
