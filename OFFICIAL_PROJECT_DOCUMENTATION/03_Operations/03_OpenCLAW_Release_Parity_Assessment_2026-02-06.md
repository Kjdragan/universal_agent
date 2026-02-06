# 03 OpenCLAW Release Parity Assessment (January 23 to February 6, 2026)

## Status
- State: **DONE / CLOSED**
- Closed on: **February 6, 2026**
- Decision note: Date-parity differences are explicitly accepted and not a blocker.

## Scope
- Date window reviewed: **January 23, 2026 through February 6, 2026**.
- Source of truth: OpenCLAW GitHub releases page/API.
- Releases in scope:
  - `v2026.1.22` (2026-01-23)
  - `v2026.1.23` (2026-01-24)
  - `v2026.1.24` (2026-01-25)
  - `v2026.1.29` (2026-01-30)
  - `v2026.1.30` (2026-01-31)
  - `v2026.2.1` (2026-02-02)
  - `v2026.2.2` (2026-02-04)
  - `v2026.2.3` (2026-02-05)

## Executive Findings
- OpenCLAW’s highest-value deltas for UA in this period are **security boundary hardening** (auth defaults, path/sandbox enforcement, approvals allowlists, SSRF defenses) and **scheduler/session reliability**.
- UA already has strong building blocks for workspace containment and run leasing (`src/universal_agent/hooks.py:348`, `src/universal_agent/guardrails/workspace_guard.py:126`, `src/universal_agent/worker.py:67`, `src/universal_agent/durable/state.py:234`), but public API surfaces remain comparatively open.
- The most urgent UA gaps are not channel features; they are boundary controls on existing UA HTTP/WebSocket/file APIs.
- OpenCLAW has additional security commits on `main` in the same period that are likely next release material (credential redaction, plugin code scanning, session-history payload caps, approvals parsing hardening).

## Release Timeline (Two-Week Window)
| Release | Published (UTC) | Link |
|---|---:|---|
| `v2026.2.3` | 2026-02-05T01:57:22Z | https://github.com/openclaw/openclaw/releases/tag/v2026.2.3 |
| `v2026.2.2` | 2026-02-04T01:05:25Z | https://github.com/openclaw/openclaw/releases/tag/v2026.2.2 |
| `v2026.2.1` | 2026-02-02T11:54:07Z | https://github.com/openclaw/openclaw/releases/tag/v2026.2.1 |
| `v2026.1.30` | 2026-01-31T15:22:30Z | https://github.com/openclaw/openclaw/releases/tag/v2026.1.30 |
| `v2026.1.29` | 2026-01-30T05:26:50Z | https://github.com/openclaw/openclaw/releases/tag/v2026.1.29 |
| `v2026.1.24` | 2026-01-25T14:29:07Z | https://github.com/openclaw/openclaw/releases/tag/v2026.1.24 |
| `v2026.1.23` | 2026-01-24T13:04:16Z | https://github.com/openclaw/openclaw/releases/tag/v2026.1.23 |
| `v2026.1.22` | 2026-01-23T08:59:57Z | https://github.com/openclaw/openclaw/releases/tag/v2026.1.22 |

## UA Parity Matrix (Security + Reliability Focus)

Feasibility legend:
- `Direct`: concept maps cleanly to UA.
- `Adapted`: concept is useful but must be implemented in UA-native way.
- `Low-fit`: OpenCLAW feature is mostly channel-stack specific.

| ID | OpenCLAW change (release) | UA current state (evidence) | Fit | Feasibility | Priority |
|---|---|---|---|---|---|
| SEC-01 | Fail-closed gateway auth defaults; remove no-auth mode (`v2026.1.29`, reinforced in later security fixes). | UA gateway defaults to public mode when allowlist empty (`src/universal_agent/gateway_server.py:75`), and trust boundary is weak because caller-supplied `user_id` is accepted (`src/universal_agent/identity/resolver.py:7`, `src/universal_agent/gateway_server.py:703`). | Adapted | High | P0 |
| SEC-02 | Enforce auth on exposed web surfaces/assets and harden auth bypasses (`v2026.1.29+`, `v2026.2.x`). | UA has multiple unauthenticated public surfaces: API server CORS+public endpoints (`src/universal_agent/api/server.py:140`), legacy server (`src/universal_agent/server.py:59`), and web server (`src/web/server.py:270`). | Adapted | High | P0 |
| SEC-03 | Path/sandbox/LFI hardening across tools/file handlers (`v2026.1.30`, `v2026.2.1`, `v2026.2.2`). | UA path checks often use string `startswith` and can be prefix-confused (`src/universal_agent/api/server.py:253`, `src/universal_agent/server.py:83`). Additional file routes bypass robust boundary checks (`src/web/server.py:333`, `src/universal_agent/api/agent_bridge.py:254`, `src/universal_agent/api/gateway_bridge.py:277`). | Direct | High | P0 |
| SEC-04 | Credential redaction in config-facing APIs (recent upstream hardening). | UA ops config endpoint returns raw config unredacted (`src/universal_agent/gateway_server.py:1210`, `src/universal_agent/ops_config.py:19`). | Direct | High | P0 |
| SEC-05 | SSRF protections for installer/media/provider fetch paths (`v2026.2.1`, `v2026.2.2`). | UA crawl pipeline accepts arbitrary URLs and posts remote fetch payloads without allow/deny network policy checks (`src/mcp_server.py:1545`). | Adapted | Medium-High | P1 |
| SEC-06 | Exec approvals/allowlist parsing hardening and coercion fixes (late-window upstream commits). | UA approvals store is permissive and schema-light (`src/universal_agent/approvals.py:46`), with a public `/api/approvals` acknowledge endpoint (`src/universal_agent/api/server.py:399`). | Adapted | Medium | P1 |
| SEC-07 | Skill/plugin code safety scanner for dangerous patterns (late-window upstream main). | UA skill discovery only checks dependency binaries/frontmatter, no code-content scan (`src/universal_agent/prompt_assets.py:64`). | Adapted | Medium | P1 |
| REL-01 | Cron reliability fixes (timer re-arm, due-job skip prevention, legacy timestamp parsing) (`v2026.2.3` + late-window commits). | UA scheduler is simple 1s polling and immediate `schedule_next` recompute (`src/universal_agent/cron_service.py:257`, `src/universal_agent/cron_service.py:421`), with fewer protections around drift/recovery edge cases. | Adapted | Medium | P1 |
| REL-02 | Session lock cleanup on termination and stale-lock resilience (`v2026.1.23`, `v2026.1.29`, late-window commits). | UA already has lease acquire/heartbeat/release and finally-release behavior (`src/universal_agent/durable/state.py:234`, `src/universal_agent/worker.py:67`). | Direct | Medium | P2 |
| REL-03 | Cap session-history/tool payload size to prevent context overflow (late-window upstream main). | UA has token-based truncation and tool preview caps (`src/universal_agent/utils/message_history.py:78`, `src/universal_agent/hooks.py:204`), but not a unified explicit cap policy across all exposed history/file/ops payloads. | Adapted | Medium | P1 |
| REL-04 | Tool-call/result fallback robustness in malformed/partial turns (`v2026.2.2`, `v2026.2.3`). | UA already has schema/tool guardrails and corrective hooks (`src/universal_agent/hooks.py:333`, `src/universal_agent/guardrails/workspace_guard.py:126`), plus context pruning (`src/universal_agent/memory/context_manager.py:66`). | Direct | Medium | P2 |
| OPS-01 | Token usage dashboard and richer session analytics (`v2026.2.3`). | UA tracks tokens and trace metadata but has no dedicated ops dashboard view (`src/universal_agent/utils/message_history.py:145`, `src/universal_agent/gateway.py:211`). | Adapted | Medium | P2 |

## What UA Already Does Well (Keep and Reuse)
- Workspace-scoped write guardrail with explicit block behavior and tests.
  - `src/universal_agent/hooks.py:348`
  - `src/universal_agent/guardrails/workspace_guard.py:126`
  - `tests/test_hooks_workspace_guard.py:7`
- Durable run lease model with heartbeat and explicit release paths.
  - `src/universal_agent/durable/state.py:234`
  - `src/universal_agent/worker.py:67`
- Context-size management primitives already exist.
  - `src/universal_agent/utils/message_history.py:78`
  - `src/universal_agent/memory/context_manager.py:66`

## Not Priority for UA (Now)
These were reviewed but are mostly channel/platform specific for OpenCLAW and low immediate leverage for UA:
- Feishu/Telegram/Slack/Discord channel-specific routing and allowlist semantics.
- Voice-call webhook channel hardening details.
- Mobile/desktop app channel UX changes.

## Priority Investigation Queue (No Implementation Plan Yet)

### P0 (Investigate First)
1. `SEC-01 + SEC-02`: Consolidate UA auth boundary model for all public HTTP/WS surfaces.
2. `SEC-03`: Perform full file-route/path-boundary audit and prefix-confusion remediation.
3. `SEC-04`: Add config/diagnostic secret redaction policy and test coverage.

### P1 (Investigate Next)
1. `SEC-05`: Define outbound URL/network policy for crawl/fetch operations (SSRF control set).
2. `SEC-06`: Harden approval payload validation and approval endpoint access model.
3. `SEC-07`: Add skill code safety scanning during discovery/install/update.
4. `REL-01 + REL-03`: Cron resilience and explicit payload cap policy across ops/history surfaces.

### P2 (Backlog)
1. `REL-02`: Expand lease/lock crash tests and stale-recovery observability.
2. `REL-04`: Tighten malformed-tool-call repair behavior and regression tests.
3. `OPS-01`: Optional token-usage dashboard parity if operations UX becomes priority.

## Additional Watchlist: Late-Window Upstream Main (Likely Next Release)
These are important and should be tracked even though they were not yet on the latest release page snapshot when this report was generated:
- Credential redaction in gateway config responses: `0c7fa2b0d`
- Skill/plugin code safety scanner: `bc88e58fc`
- Require auth for canvas/assets: `a459e237e`
- Cap `sessions_history` payloads: `bccdc95a9`
- Exec approvals allowlist coercion hardening: `141f551a4`, `6ff209e93`
- Session lock release robustness: `ec0728b35`
- Cron timer/due-run correctness fixes: `313e2f2e8`, `40e23b05f`, `b0befb5f`

## Sources
- OpenCLAW Releases API: https://api.github.com/repos/openclaw/openclaw/releases?per_page=30
- OpenCLAW releases pages:
  - https://github.com/openclaw/openclaw/releases/tag/v2026.2.3
  - https://github.com/openclaw/openclaw/releases/tag/v2026.2.2
  - https://github.com/openclaw/openclaw/releases/tag/v2026.2.1
  - https://github.com/openclaw/openclaw/releases/tag/v2026.1.30
  - https://github.com/openclaw/openclaw/releases/tag/v2026.1.29
  - https://github.com/openclaw/openclaw/releases/tag/v2026.1.24
  - https://github.com/openclaw/openclaw/releases/tag/v2026.1.23
  - https://github.com/openclaw/openclaw/releases/tag/v2026.1.22
- Upstream commit log reference base: https://github.com/openclaw/openclaw/commits/main
