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
| 1.1 `claude_cli_client.py` | ⬜ In Progress | Core subprocess bridge |
| 1.2 JSON stream parsing | ⬜ In Progress | Part of client |
| 1.3 Input request handling | ⬜ Pending | VP responds to CLI stdin |
| 1.4 VpWorkerLoop integration | ⬜ Pending | execution_mode selection |
| 1.5 Mission dispatch API update | ⬜ Pending | Accept execution_mode param |
| 1.6 Verification test | ⬜ Pending | End-to-end dispatch → CLI → result |

## Phase 2: CODIE Upgrade

| Task | Status | Notes |
|------|--------|-------|
| 2.1 Prompt engineering | ⬜ Pending | Structured mission briefs for CLI |
| 2.2 Result evaluation | ⬜ Pending | Inspect artifacts, decide success |
| 2.3 Retry logic | ⬜ Pending | Max 2 retries with adjusted prompts |

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
| 2026-03-08 | — | Phase 1 implementation started |
