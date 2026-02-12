# Agent Operating Notes

## Read first for VPS work
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
1. Copy exact changed files with `scp -i ~/.ssh/id_ed25519`.
2. Restart only affected systemd units.
3. Verify service health and logs.
4. Ask for UI hard refresh + validation.

## Session workspace introspection
- Use internal tool `inspect_session_workspace` for read-only run diagnostics.
- `transcript.md` is included by default (`include_transcript=true`).
