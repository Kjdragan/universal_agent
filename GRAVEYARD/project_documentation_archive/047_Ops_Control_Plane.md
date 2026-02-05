# 047 Ops Control Plane

**Date:** 2026-02-03  
**Scope:** UA Ops API and runtime control configuration.

---

## 1) Ops Config File

- **Location:** `AGENT_RUN_WORKSPACES/ops_config.json`
- **Override Path:** `UA_OPS_CONFIG_PATH` (optional)
- **Write Safety:** server requires `base_hash` to prevent stale writes.

### Example Structure
```json
{
  "skills": {
    "entries": {
      "report-writer": {"enabled": false},
      "image-expert": true
    }
  },
  "channels": {
    "entries": {
      "telegram": {"enabled": false},
      "web": true
    }
  }
}
```

---

## 2) Security

- **Optional:** `UA_OPS_TOKEN`
- **Auth Header:**
  - `Authorization: Bearer <token>`
  - or `x-ua-ops-token: <token>`

---

## 3) Ops Endpoints

### Sessions
- `GET /api/v1/ops/sessions`
- `GET /api/v1/ops/sessions/{session_id}`
- `GET /api/v1/ops/sessions/{session_id}/preview`
- `POST /api/v1/ops/sessions/{session_id}/reset`
- `POST /api/v1/ops/sessions/{session_id}/compact`
- `DELETE /api/v1/ops/sessions/{session_id}?confirm=true`

### Logs
- `GET /api/v1/ops/logs/tail?path=run.log&cursor=...&limit=...&max_bytes=...`

### Skills
- `GET /api/v1/ops/skills`
- `PATCH /api/v1/ops/skills/{skill_key}`
  - Payload: `{ "enabled": true/false }`

### Channels
- `GET /api/v1/ops/channels`
- `POST /api/v1/ops/channels/{channel_id}/probe`
- `POST /api/v1/ops/channels/{channel_id}/logout`

### Ops Config
- `GET /api/v1/ops/config`
- `GET /api/v1/ops/config/schema`
- `POST /api/v1/ops/config`
- `PATCH /api/v1/ops/config`
  - Payload includes `base_hash` and patch/document

### Approvals
- `GET /api/v1/ops/approvals`
- `POST /api/v1/ops/approvals`
- `PATCH /api/v1/ops/approvals/{approval_id}`

### Models
- `GET /api/v1/ops/models`

---

## 4) UI Integration

- Ops panel is in the Web UI and consumes the endpoints above.
- Config editor writes **only** `ops_config.json`.

---

## 5) Operational Notes

- **Skill disable** is immediate for UI + gateway discovery; no restart required.
- **Channel logout** is a soft disable in ops config; restart may be required for some integrations.
- **Logs tail** is cursor-based and bounded for safety.

---

## 6) Testing Guidance

Use the ops tests (no Telegram):

```bash
uv run pytest tests/gateway/test_ops_api.py -v
```
