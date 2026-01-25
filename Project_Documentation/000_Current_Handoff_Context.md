# Handoff Context: Web UI Gateway Testing

**Date:** 2026-01-24
**Status:** Web UI validation phase

## 📍 Where We Are
We completed a focused Letta memory pass and organized Letta-related tooling into a dedicated workspace.

- **Letta workspace:** `letta/` now contains scripts, reports, and README.
- **Letta blocks updated:** Added `failure_patterns` + `recovery_patterns`, plus seeded `system_rules` and `project_context`.
- **Test agents cleaned up:** `universal_agent_test*` removed from Letta.
- **Latest memory snapshot:** `letta/reports/letta_memory_report_postseed.md`.

## 🎯 Current Goal
Begin **web-based UI testing** to confirm the refactor works end-to-end with the gateway architecture.

We need to validate:
- Gateway session creation & resume behavior in the Web UI.
- Tool calls and logs render correctly in the Web UI.
- Web UI can run the same workflows as the CLI without divergence.
- Web UI output artifacts are stored in the correct session workspace.

## 🧾 Latest Summary (Web UI Bring-up)
- Started refactored stack: API server (`uv run python -m universal_agent.api.server`, port 8001) + Next.js UI (`npm run dev`, port 3000).
- UI loaded but showed **Disconnected** and query input disabled.
- Root cause suspected: WebSocket URL defaulted to `ws://<ui-host>:3000/ws/agent` instead of API server.
- Patch applied in `web-ui/lib/websocket.ts` to:
  - Honor `NEXT_PUBLIC_WS_URL` if set.
  - Auto-switch dev port from `3000` → `8001` when building the ws URL.
- Next.js dev server restarted after patch. API server remained running.
- **Status:** UI still needs verification of connection + ability to submit queries.

## 🧭 Next Steps
1. **Start the Web UI** and confirm gateway connectivity.
2. **Run a simple end-to-end test** (small prompt) to verify session creation and tool execution.
3. **Verify logs & artifacts** appear in the UI and in `AGENT_RUN_WORKSPACES/{session_id}`.
4. **Record any deviations** between CLI and Web UI execution paths.

## 🔑 Key Files
- `src/universal_agent/api/server.py`: Web entry point & gateway integration.
- `src/universal_agent/agent_setup.py`: Shared config for CLI/Web.
- `src/web/server.py`: Web server glue and session handling.
- `web-ui/`: Next.js frontend (UI for gateway sessions).
- `letta/README.md`: Letta tooling overview.

## 📂 Relevant Context
- `Project_Documentation/010_clawdbot_integration_phasing.md`: Refactor roadmap.
- `letta/reports/letta_memory_report_postseed.md`: Seeded Letta memory snapshot.
