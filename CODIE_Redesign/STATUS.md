# CODIE Redesign — Implementation Status

**Last Updated:** 2026-03-08

## Prerequisites

| Item | Status | Notes |
|------|--------|-------|
| `claude` CLI on VPS | ✅ Done | v2.1.71 installed at `/usr/bin/claude` |
| `ANTHROPIC_API_KEY` available | ✅ Done | Available in gateway process env |
| Node.js on VPS | ✅ Done | v20.20.0 |
| `claude` CLI on local desktop | ⬜ Not checked | Needed for local factory |

## Phase 1: ClaudeCodeCLIClient (The Bridge)

| Task | Status | Notes |
|------|--------|-------|
| 1.1 `claude_cli_client.py` | ✅ Done | 500-line subprocess bridge with stream-json parsing |
| 1.2 JSON stream parsing | ✅ Done | Handles result, assistant, tool_use, error events |
| 1.3 Input request handling | ✅ Done | Stall detection + basic stdin response |
| 1.4 VpWorkerLoop integration | ✅ Done | `_select_client_for_mission()` routes on execution_mode |
| 1.5 Mission dispatch API update | ✅ Done | `execution_mode` wired through dispatcher → tool → gateway |
| 1.6 Verification test | ✅ Done | 22/22 tests pass (12 new CLI + 10 regression) |

## Phase 2: CODIE Hardening & Tests

| Task | Status | Notes |
|------|--------|-------|
| 2A `execution_mode` dispatch pipeline | ✅ Done | `dispatcher.py`, `vp_orchestration.py`, `gateway_server.py` |
| 2B Workspace guardrails | ✅ Done | `_enforce_cli_target_guardrails()` mirrors SDK client pattern |
| 2B Graceful signal handling | ✅ Done | SIGTERM → wait 5s → SIGKILL in `_kill_process()` |
| 2B Cost tracking | ✅ Done | `cost_usd` extracted from result events into outcome payload |
| 2C Unit tests | ✅ Done | `tests/unit/test_claude_cli_client.py`, 12 test cases |
| 2D Documentation update | ✅ Done | STATUS.md + Implementation Roadmap updated |

## Phase 3: VP General CLI Support

| Task | Status | Notes |
|------|--------|-------|
| 3.1 VP General profile update | ⬜ Pending | cli_capable: true |
| 3.2 Skill invocation via CLI | ⬜ Pending | modular-research-report-expert |
| 3.3 Routing logic | ⬜ Pending | Simone dispatch decision tree |

## Phase 4: Concurrency Management

| Task | Status | Notes |
|------|--------|-------|
| 4.1 Session slot tracker | ⬜ Pending | Track across all consumers |
| 4.2 Heavy mission mode | ⬜ Pending | Pause CSI during CLI runs |
| 4.3 Dynamic MAX_CONCURRENT_AGENTS | ⬜ Pending | Set per-subprocess |
| 4.4 Dashboard integration | ⬜ Pending | Show CLI session status |

## Deployment History

| Date | Commit | What |
|------|--------|------|
| 2026-03-08 | — | Phase 1 complete, Phase 2 hardening complete |

## Key Files Modified

| File | Change |
|------|--------|
| `vp/clients/claude_cli_client.py` | Core CLI bridge + guardrails + graceful shutdown + cost tracking |
| `vp/dispatcher.py` | `execution_mode` field in `MissionDispatchRequest` + `_build_payload()` |
| `tools/vp_orchestration.py` | `execution_mode` in `vp_dispatch_mission` tool schema |
| `gateway_server.py` | `execution_mode` passthrough in `ops_vp_dispatch_mission()` |
| `tests/unit/test_claude_cli_client.py` | 12 new test cases |
