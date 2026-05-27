# Runbook: Flush VP mission backlog

**Last updated:** 2026-05-27

## When to use this

After landing a change that affects mission ordering (the priority-tier
PR shipped 2026-05-27) it's useful to wipe the existing queued backlog
so the new behaviour starts from a clean slate. Otherwise pre-existing
queue items dominate observations of whether the new system is in
equilibrium.

Outside that scenario, run this only when the operator explicitly
wants a clean start — e.g. the proactive pipeline produced a flood of
spurious missions during testing.

**Do NOT flush** if you suspect the system is generating legitimate
work that just hasn't been processed yet. The fix in that case is
upstream (rate-limit producers, scale workers), not flushing.

## What gets cancelled

- Any `vp_missions` row with `status='queued'` and `cancel_requested=0`.

## What does NOT get touched

- Missions currently `status='running'` — they have a worker actively
  executing them. Cancelling mid-run would orphan the work.
- Missions in terminal states (`completed`, `failed`, `cancelled`).
- The `task_hub_items` table — those have their own lifecycle managed
  by the task-hub claim/dispatch logic.

## Audit trail

Before mutating, the script writes a full snapshot of what it's about
to cancel to
`/opt/universal_agent/AGENT_RUN_WORKSPACES/flush_audit/vp_mission_backlog_flush_<ts>.json`.
Each cancelled mission also gets a `vp.mission.cancelled` event row
in `vp_events` with the reason string. The data is preserved, just
removed from the active queue.

## Usage

Dry-run preview (recommended first pass):

```bash
ssh ua@uaonvps "cd /opt/universal_agent && \
    uv run python -m universal_agent.scripts.flush_vp_mission_backlog --dry-run"
```

The dry-run lists tier-by-tier counts of what would be cancelled but
mutates nothing.

Actual flush:

```bash
ssh ua@uaonvps "cd /opt/universal_agent && \
    uv run python -m universal_agent.scripts.flush_vp_mission_backlog \
        --reason 'post-priority-tiers PR clean slate'"
```

Restrict to a single VP if needed:

```bash
ssh ua@uaonvps "cd /opt/universal_agent && \
    uv run python -m universal_agent.scripts.flush_vp_mission_backlog \
        --vp vp.general.primary"
```

## Verification post-flush

```bash
ssh ua@uaonvps "sqlite3 /opt/universal_agent/AGENT_RUN_WORKSPACES/vp_state.db \
    \"SELECT status, COUNT(*) FROM vp_missions GROUP BY status;\""
```

Expected: `queued` is 0 (or close to it — new dispatches arrive
constantly). `running` is whatever's actively being processed.

## Recovery if something is wrong

The script is idempotent — running it twice cancels nothing the second
time. To "un-cancel" a specific mission, manually update the row:

```sql
UPDATE vp_missions
SET status = 'queued', cancel_requested = 0
WHERE mission_id = '<id from audit JSON>';
```

But: re-queueing is usually pointless for proactive pipeline output —
the producers will regenerate the work. Only re-queue if the operator
specifically wants that exact mission back.
