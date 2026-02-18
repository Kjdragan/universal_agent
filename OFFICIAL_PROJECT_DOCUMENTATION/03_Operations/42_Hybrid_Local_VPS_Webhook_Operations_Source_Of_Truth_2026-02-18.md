# 42. Hybrid Local+VPS YouTube Webhook Operations Source of Truth (2026-02-18)

## Document Metadata
- **Doc ID**: `OPS-42`
- **Status**: Active source of truth
- **Last Updated**: 2026-02-18
- **Scope**: YouTube webhook ingestion/execution across VPS + local worker, artifacts, observability, and failure recovery

## Why This Exists
This document is the canonical operations reference for the current hybrid architecture:
1. **Webhook control plane runs on VPS**
2. **Transcript ingestion runs on local worker (residential IP path)**
3. **Learning/exhibit generation executes on VPS**
4. **Artifacts are durable on VPS and mirrored locally for browsing**

Use this doc first when debugging YouTube trigger flows.

## Canonical Topology
1. Composio sends trigger to VPS: `POST https://api.clearspringcg.com/api/v1/hooks/composio`
2. VPS `hooks_service` resolves action/session and calls local ingest worker via tunnel:
   - `UA_HOOKS_YOUTUBE_INGEST_URL=http://127.0.0.1:18002/api/v1/youtube/ingest`
3. Local worker extracts transcript and returns JSON.
4. VPS writes ingestion evidence into hook session:
   - `AGENT_RUN_WORKSPACES/session_hook_yt_.../ingestion/youtube_transcript.local.txt`
   - `AGENT_RUN_WORKSPACES/session_hook_yt_.../ingestion/youtube_local_ingest_result.json`
5. VPS executes YouTube skill workflow and writes artifacts under:
   - `/opt/universal_agent/artifacts/youtube-tutorial-learning/YYYY-MM-DD/<run_id>/...`
6. File browser can read:
   - Local session files
   - Local artifacts
   - VPS mirrored workspaces/artifacts

## Canonical Paths
### VPS
1. Sessions: `/opt/universal_agent/AGENT_RUN_WORKSPACES`
2. Artifacts: `/opt/universal_agent/artifacts`
3. Runtime DB: `/opt/universal_agent/AGENT_RUN_WORKSPACES/runtime_state.db`
4. Gateway service: `universal-agent-gateway.service`

### Local
1. Sessions mirror: `AGENT_RUN_WORKSPACES`
2. VPS artifacts mirror: `tmp/remote_vps_artifacts`
3. VPS workspaces mirror: `tmp/remote_vps_workspaces`

## Required Health Endpoints
1. `GET /api/v1/health`
2. `GET /api/v1/hooks/readyz` (no auth; readiness payload)

Important:
1. `401` on `/api/v1/hooks/...` without token is expected and not a pipeline failure.
2. Use `readyz` for unauthenticated probe checks.

## Verified Working Signals
A run is healthy when all are true:
1. Hook ingress accepted in journal:
   - `Hook ingress accepted path=composio ... action=agent`
2. Hook session created:
   - `session_hook_yt_<channel>_<video>`
3. Ingestion lines present in session `run.log`:
   - `local_youtube_ingest_status: succeeded`
4. Artifacts directory created under VPS artifacts root.
5. `Hook action dispatched session_id=...` appears in gateway journal.

## Incident Signatures and Fixes
### A) `404 Hooks disabled`
Cause:
1. Hooks service disabled by config/env.

Fix:
1. Ensure hook bootstrap/config is present.
2. Confirm with `GET /api/v1/hooks/readyz` returning `hooks_enabled: true`.

### B) `401 Unauthorized` while probing hooks
Cause:
1. Probing authenticated endpoints without token.

Fix:
1. Use `/api/v1/hooks/readyz` for no-auth readiness.
2. Use bearer token for manual hook calls.

### C) Transcript extraction blocked on VPS (`Sign in to confirm you're not a bot`)
Cause:
1. Cloud IP restrictions from YouTube.

Fix:
1. Keep hybrid mode with local ingest worker as primary.
2. Keep run degradable only by explicit policy when ingest fails.

### D) Wrong `video_id` contamination from prior runs
Cause:
1. Agent reused stale values.

Fix:
1. Enforce authoritative payload lines in hook prompt.
2. Validate `manifest.json` uses authoritative video values.

### E) Artifact write path failures with literal `UA_ARTIFACTS_DIR` segment
Cause:
1. Agent wrote `/opt/universal_agent/UA_ARTIFACTS_DIR/...` literal path.

Fix:
1. Always resolve absolute artifacts root first.
2. Reject literal folder token usage in prompts and checks.

### F) Hook run not visible in its own session (`run.log` empty, activity in `vp_coder_primary`)
Cause:
1. CODER-VP route delegation moved webhook execution to shared lane.

Fix:
1. Pin `source=webhook` requests to their explicit hook session workspace.

### G) `sqlite3.OperationalError: database is locked` and readonly memory warnings
Cause:
1. Permission/ownership drift on memory/db files and concurrent writes.

Fix:
1. Set ownership to service user `ua:ua` for runtime/memory DB paths.
2. Restart gateway after ownership correction.

## Operational Commands
### Quick VPS Status
```bash
ssh root@100.106.113.93
cd /opt/universal_agent
systemctl is-active universal-agent-gateway universal-agent-api universal-agent-webui
journalctl -u universal-agent-gateway -n 120 --no-pager
```

### Hook Readiness (No Auth)
```bash
curl -sS https://api.clearspringcg.com/api/v1/hooks/readyz
```

### Manual Hook Trigger (Auth)
```bash
TOKEN=$(grep -E '^UA_HOOKS_TOKEN=' .env | cut -d= -f2-)
curl -sS -X POST http://127.0.0.1:8002/api/v1/hooks/youtube/manual \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary '{"video_url":"https://www.youtube.com/watch?v=SpReZZk_13w","video_id":"SpReZZk_13w","mode":"explainer_plus_code","allow_degraded_transcript_only":true}'
```

### Tail Latest Hook Session
```bash
cd /opt/universal_agent
latest=$(ls -1dt AGENT_RUN_WORKSPACES/session_hook_yt_* | head -n1)
echo "$latest"
tail -n 200 "$latest/run.log"
```

### Verify Artifacts for Latest Run
```bash
cd /opt/universal_agent
find artifacts/youtube-tutorial-learning -maxdepth 3 -type f | tail -n 40
```

## Recommended Execution Policy
1. **Control plane**: VPS only.
2. **YouTube transcript ingest**: Local worker over tunnel.
3. **Artifact generation**: VPS only.
4. **UI browsing**: Use built-in file browser with VPS mirror scopes.
5. **Keep local machine online** if local ingest is required.

## Indexing and Reference Keys
Use these IDs in future tickets, chats, and incident notes.

1. `OPS-42` → This source-of-truth architecture/runbook
2. `OPS-29` → `29_Hybrid_Youtube_Ingestion_LocalWorker_Runbook_2026-02-18.md`
3. `OPS-20` → `20_VPS_Daily_Ops_Quickstart_2026-02-11.md`
4. `OPS-22` → `22_VPS_Remote_Dev_Deploy_And_File_Transfer_Runbook_2026-02-11.md`
5. `OPS-32` → `32_VPS_FileBrowser_Setup_And_Access_2026-02-13.md`

## Change Log
### 2026-02-18
1. Added no-auth hooks readiness endpoint (`/api/v1/hooks/readyz`).
2. Added webhook timeout controls and pinned webhook execution to hook session workspace.
3. Stabilized VPS ownership for memory/runtime DB files.
4. Verified end-to-end completion for `SpReZZk_13w` with durable artifacts.
