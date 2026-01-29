# 03. UA Heartbeat Schema and Event Model

## 1. Goals
- Enable **proactive** agent turns on a schedule.
- Keep the system **auditable** (produce traces/artifacts like normal runs).
- Avoid spam with a clear **suppression contract**.
- Support multiple delivery surfaces in the future (web UI, Telegram, Slack).

## 2. Non-goals (for MVP)
- Exact-time scheduling (“run at 09:00 sharp”). That’s a cron job concept.
- Distributed scheduling across multiple gateway instances.
- Automated multi-step workflows with approvals (that’s closer to URW / workflow runtimes).

## 3. Proposed configuration schema (target)
UA does not currently have a single canonical user-facing config file like Clawdbot. This schema is written as if we will introduce a small UA config layer later (JSON5/YAML/Pydantic settings).

### 3.1 Top-level
```yaml
heartbeat:
  enabled: true
  every: "30m"
  activeHours:
    start: "08:00"
    end: "22:00"
    timezone: "user"  # user | local | IANA tz
  prompt: "Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. If nothing needs attention, reply UA_HEARTBEAT_OK."
  ackToken: "UA_HEARTBEAT_OK"
  ackMaxChars: 300
  includeReasoning: false
  dedupeWindow: "24h"
  deliver:
    mode: "last"      # last | explicit | none
    channel: null      # telegram | slack | webui | ... (when explicit)
    to: null           # recipient id / chat id when explicit
  visibility:
    showOk: false
    showAlerts: true
    useIndicator: true
```

### 3.2 Per-session overrides
Because UA’s “session” is currently strongly tied to a workspace directory, a practical model is **per-session** overrides:
```yaml
sessions:
  session_2026_01_28_main:
    heartbeat:
      enabled: true
      every: "15m"
      deliver:
        mode: "explicit"
        channel: "telegram"
        to: "<chat_id>"
```

## 4. Workspace contract
Each gateway session has a workspace directory (example from your run):
- `AGENT_RUN_WORKSPACES/session_20260128_234441/`

Proposed heartbeat workspace artifacts:
- `HEARTBEAT.md` (optional checklist)
- `heartbeat_state.json` (optional: dedupe + last-run metadata)
- Standard UA artifacts should remain:
  - `trace.json`
  - `transcript.md`
  - `run.log`
  - `work_products/*`

## 5. Prompt + response contract
### 5.1 Prompt
Default prompt should be intentionally short and stable (mirrors Clawdbot):
- “Read `HEARTBEAT.md` if it exists (workspace context). Follow it strictly. If nothing needs attention, reply `UA_HEARTBEAT_OK`.”

### 5.2 Ack token behavior
Rules (recommended):
- If the model returns exactly the ack token (or ack token at the start/end with minimal extra text), the system suppresses delivery.
- If the model returns substantive content (beyond `ackMaxChars`) the system treats it as an “alert” message and delivers it.

## 6. Event model (UA)
UA already has `AgentEvent` + `EventType` in `src/universal_agent/agent_core.py`.

### 6.1 Proposed new event type
Option A (recommended):
- Add a new `EventType.HEARTBEAT = "heartbeat"`.

Option B (lowest friction):
- Emit as `EventType.STATUS` but include a stable discriminator:
  - `{"status": "...", "kind": "heartbeat", ...}`

### 6.2 Heartbeat event payload (stable schema)
```json
{
  "ts": "2026-01-28T23:51:08Z",
  "session_id": "session_...",
  "workspace_dir": ".../AGENT_RUN_WORKSPACES/session_...",
  "reason": "interval", 
  "result": "sent",
  "indicator": "alert",
  "delivered": {
    "mode": "last",
    "channel": "webui",
    "to": null
  },
  "preview": "Short preview of delivered content (<=200 chars)",
  "duration_ms": 1234,
  "tool_calls": 3,
  "trace_id": "019c...",
  "silent": false,
  "error": null
}
```

Where:
- `result` ∈ `sent | ok-token | ok-empty | skipped | failed`
- `indicator` ∈ `ok | alert | error | none`

### 6.3 Mapping to existing UA event stream
During heartbeat execution, the engine will still emit normal events:
- `SESSION_INFO`, `STATUS`, `TOOL_CALL`, `TOOL_RESULT`, `TEXT`, `WORK_PRODUCT`, `ITERATION_END`

The new heartbeat event is a **summary envelope** to make UI + routing decisions consistent.

## 7. Concurrency + gating policy
Mirroring Clawdbot’s approach:
- **Do not** run heartbeat if the session already has an active query/engine run.
- Coalesce repeated wake requests.
- Retry after a short delay if the engine is busy.

## 8. Relationship to URW “heartbeat_interval_seconds”
UA already has a field named `heartbeat_interval_seconds` in `URWConfig`. That is currently best interpreted as an **orchestrator internal tick**, not the proactive user-facing heartbeat described here.

Recommendation:
- Keep names distinct in code when implemented (e.g., `proactive_heartbeat_every` vs `urw_tick_interval`).

