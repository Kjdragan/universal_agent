# Agent Operating Notes

## Read first for VPS work
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/31_VPS_Deployment_Decision_Tree_2026-02-13.md`
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/22_VPS_Remote_Dev_Deploy_And_File_Transfer_Runbook_2026-02-11.md`
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/23_Agent_Workspace_Inspector_Skill_2026-02-11.md`

## Deployment environment
- Local repo: `/home/kjdragan/lrepos/universal_agent`
- VPS: `root@187.77.16.29`
- App root on VPS: `/opt/universal_agent`
- Main services:
  - `universal-agent-gateway`
  - `universal-agent-api`
  - `universal-agent-webui`

## Required scheduling behavior
- Heartbeat missed windows are **not** backfilled.
- Heartbeat missed windows are **not** alerted/stasis queued.
- Heartbeats run on normal interval when enabled, and do not run when disabled.

## Standard remote update pattern
Prefer `scripts/vpsctl.sh` over raw scp/ssh. See Doc 31 for the full decision tree.
1. `scripts/vpsctl.sh push <path...>` — copy files to VPS.
2. `scripts/vpsctl.sh restart gateway|api|webui|telegram|all` — restart affected units.
3. `scripts/vpsctl.sh status all` — verify health.
4. Ask for UI hard refresh + validation.
5. For full redeploy: `./scripts/deploy_vps.sh`.

## VPS file inspection (IDE agents)
IDE agents (Cascade, Claude Code, Codex, etc.) inspect VPS files via `scripts/vpsctl.sh`:
- `scripts/vpsctl.sh sessions` — list agent sessions with metadata.
- `scripts/vpsctl.sh browse <path>` — list files at a VPS path (project-relative).
- `scripts/vpsctl.sh read <path> [tail_lines]` — read a file (optional tail mode).
- `scripts/vpsctl.sh inspect <session_id>` — session diagnostics (run.log, work products, transcript).

Security: Uses existing SSH key auth only. No new ports, daemons, or attack surface.
Paths are project-relative under `/opt/universal_agent/`.

## Session workspace introspection (deployed agents)
- Deployed agents use MCP tools: `inspect_session_workspace`, `list_agent_sessions`, `read_vps_file`.
- `transcript.md` is included by default (`include_transcript=true`).
- `read_vps_file` has path restrictions — allowed roots only (no `.env`, no system files).

## Remote workspace sync defaults
- Remote workspace sync now targets local `AGENT_RUN_WORKSPACES` by default.
- One-command manual pull:
  - `scripts/pull_remote_workspaces_now.sh`
  - Optional single session: `scripts/pull_remote_workspaces_now.sh session_...`
- For quick file reads without syncing, use `scripts/vpsctl.sh read <path>` instead.

## FileBrowser (human web access)
- URL: `https://app.clearspringcg.com/files/` (or SSH tunnel: `ssh -L 8080:127.0.0.1:8080 root@187.77.16.29`).
- Credentials in VPS `.env` (`FILEBROWSER_ADMIN_PASSWORD`, `FILEBROWSER_VIEWER_PASSWORD`).
- Doc: `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/32_VPS_FileBrowser_Setup_And_Access_2026-02-13.md`.
