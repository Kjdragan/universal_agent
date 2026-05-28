---
name: hourly-intel-digest
description: >
  Simone skill for packaging and delivering the hourly intel digest email.
  Self-throttling via hour-bucket comparison: exits immediately if a digest
  was already sent this clock hour. Pure packaging — no editorial LLM pass,
  no scoring. Invoke on every heartbeat; the skill handles its own gate.
---

# Hourly Intel Digest

## Purpose

You are the **delivery channel** for ATLAS's authored intel briefs. ATLAS
ships `verdict='ship'` rows into `proactive_artifacts` with pre-signed
feedback URLs already attached. This skill bundles every qualifying ship
brief from the current clock hour into one HTML email, sends it from
`oddcity216@agentmail.to`, and stamps the artifacts as delivered.

**Pure packaging. Do not re-evaluate, re-score, summarize, or edit the
briefs.** The whole point of consolidation is that LLM judgment lives in
ATLAS's authoring step, not yours.

## When to invoke

Every Simone heartbeat. The skill handles its own throttle so calling it
every tick is correct and free — if nothing qualifies the skill exits in
under 5 ms with no email.

## Workflow

### Phase 0 — Gate check (pause + throttle)

Open `activity_state.db` and call the gate helpers. Both are SQL-only:

```python
from universal_agent.services import hourly_intel_digest as digest
from universal_agent.durable.db import get_activity_db_path
import sqlite3

conn = sqlite3.connect(get_activity_db_path())
conn.row_factory = sqlite3.Row
digest.ensure_schema_addons(conn)
payload = digest.compose_send_payload(conn)
```

`compose_send_payload` does the full pause + throttle + candidate-pull +
render flow and returns a dict whose `status` field tells you how to
proceed:

| `payload["status"]`   | Action                                          |
|-----------------------|-------------------------------------------------|
| `paused`              | Operator paused digest. Exit silent. No log noise. |
| `throttled`           | Digest already sent this clock hour. Exit silent.  |
| `no_candidates`       | Zero `verdict='ship'` rows in this hour. Exit silent. |
| `ready`               | Proceed to send.                                |

For all three exit-silent statuses, do not surface anything in the
heartbeat response. The skill is designed to be invoked every heartbeat
and most invocations will exit silently — that's correct.

### Phase 1 — Send via AgentMail

For `status == "ready"`:

```python
result = mcp__agentmail__send_message(
    inboxId=payload["inbox_id"],          # oddcity216@agentmail.to
    to=[payload["recipient"]],
    cc=payload["cc"],                     # list — may be empty
    subject=payload["subject"],
    text=payload["text"],
    html=payload["html"],
)
```

`payload["cc"]` is non-empty only for needs-attention digests (the
`compose_send_payload` helper handles that decision based on
`metadata.needs_attention` on the candidate briefs).

If your AgentMail MCP wrapper doesn't accept a `cc` kwarg, fall back to
sending without CC. The needs-attention briefs will still go to the
primary recipient via `to=[payload["recipient"]]`.

### Phase 2 — Stamp delivered

On successful send:

```python
digest.mark_all_delivered(conn, payload["artifact_ids"])
```

This writes `delivered_at`, `delivery_state='emailed'`, and
`delivery_channel='hourly_digest'` on every artifact in the digest. The
channel column is what `is_throttled` reads on the next heartbeat to
prevent a duplicate.

### Phase 3 — Failure handling

If `mcp__agentmail__send_message` raises:

1. **Do NOT call `mark_all_delivered`.** Artifacts stay eligible for
   next heartbeat's attempt — natural recovery.
2. Park a Task Hub `needs_review` item per the Task Hub Observability
   Protocol (`docs/03_Operations/129_Task_Hub_Observability_Protocol.md`):

   ```python
   task_hub.park_task_for_protocol_violation(
       conn,
       task_id=f"hourly_intel_digest:send_failure:{int(time.time())}",
       reason=f"AgentMail send failed: {exc}",
       source_kind="hourly_intel_digest",
       metadata={
           "artifact_ids": payload["artifact_ids"],
           "brief_count": payload["brief_count"],
           "recipient": payload["recipient"],
       },
   )
   ```

   Surface a one-line note in the heartbeat response so Kevin sees the
   miss.

## Anti-patterns

- **Don't re-score briefs.** Composite score, key entities, thesis, and
  feedback URLs are already on `metadata_json`. Read; don't author.
- **Don't summarize across briefs.** No editorial "top theme this hour"
  intro. Each card stands on its own.
- **Don't email when `status != "ready"`.** Sending an empty digest is
  worse than sending none.
- **Don't bypass the helper.** `compose_send_payload` is the contract.
  Don't reach into the DB directly or you'll get the throttle behavior
  wrong.
- **Don't sign feedback URLs in the skill.** ATLAS pre-signed them at
  artifact-write time and stored them in `metadata_json.feedback_url_up`
  / `feedback_url_down`. The skill is read-only on those.

## Related modules

- `src/universal_agent/services/hourly_intel_digest.py` — every helper
  called above lives here.
- `src/universal_agent/services/proactive_artifacts.py` — artifact
  storage + delivery lifecycle.
- `docs/proactive_signals/insight_pipeline_consolidation_spec.md` — full
  pipeline design.
- `docs/03_Operations/129_Task_Hub_Observability_Protocol.md` — failure
  parking protocol.
