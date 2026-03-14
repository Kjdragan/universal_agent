# 41. Todoist Heartbeat and Triage Operational Runbook (2026-02-16)

## Purpose

This runbook defines the day-to-day operating rhythm for the Todoist-backed task system in UA v1.

It focuses on:

1. deterministic heartbeat usage (`TodoService().heartbeat_summary()` pre-step),
2. manual triage and promotion workflow,
3. practical verification checks that keep task flow healthy without adding new automation scope.

This runbook aligns with current v1 boundaries:

- brainstorming promotion remains manual,
- work-thread creation remains manual.

---

## Preconditions

1. `TODOIST_API_TOKEN` is set in `.env`.
2. Todoist dependency is installed via `uv` lock (`todoist-api-python`).
3. Gateway heartbeat flow is running in an environment that can read `.env`.

Quick bootstrap:

```bash
uv run python -m universal_agent.cli.todoist_cli setup
```

Expected: JSON with non-empty `agent_project_id`, `brainstorm_project_id`, and section maps.

---

## Daily operating cadence (recommended)

### Morning boot (5-10 min)

1. Refresh taxonomy (safe/idempotent):

```bash
uv run python -m universal_agent.cli.todoist_cli setup
```

2. Check actionable queue:

```bash
uv run python -m universal_agent.cli.todoist_cli tasks
```

3. Check deterministic heartbeat payload (what heartbeat can see):

```bash
uv run python -m universal_agent.cli.todoist_cli heartbeat
```

Interpretation:

- `actionable_count > 0`: heartbeat will inject `todoist_summary` system event.
- `actionable_count == 0`: heartbeat may take skip fast-path when no competing system-event conditions exist.

### Midday triage (10-15 min)

1. Resolve stale blockers:

```bash
uv run python -m universal_agent.cli.todoist_cli tasks --filter "@blocked"
```

2. Move completed work out of active queue (`complete`) and annotate if needed (`comment`).

3. Re-prioritize urgent items by updating task state from the UI or Todoist app as needed.

### End-of-day close (5-10 min)

1. Capture out-of-scope ideas into brainstorm inbox:

```bash
uv run python -m universal_agent.cli.todoist_cli idea "<idea>" --dedupe-key "<stable-key>"
```

2. Review brainstorm section distribution:

```bash
uv run python -m universal_agent.cli.todoist_cli pipeline
```

3. Manually decide each candidate:

- `promote` for approved work,
- `park` for defer/reject,
- leave in triage if uncertain.

---

## Manual promotion workflow (v1 boundary)

### Brainstorm capture

```bash
uv run python -m universal_agent.cli.todoist_cli idea "Investigate resilient retry/backoff policy" \
  --description "Triggered during delivery packet" \
  --dedupe-key "retry-backoff-policy"
```

### Promote to approved

```bash
uv run python -m universal_agent.cli.todoist_cli promote <task_id> --to approved
```

### Park with rationale

```bash
uv run python -m universal_agent.cli.todoist_cli park <task_id> --rationale "Out of scope for current packet"
```

### Create implementation thread manually after promote

Use the existing work-thread ops flow after an idea is approved:

- `POST /api/v1/ops/work-threads`
- `POST /api/v1/ops/work-threads/decide`
- `PATCH /api/v1/ops/work-threads/{thread_id}`

---

## Heartbeat behavior checks

Use these checks if heartbeat appears too noisy or too quiet.

1. Validate Todoist summary is available:

```bash
uv run python -m universal_agent.cli.todoist_cli heartbeat
```

2. Confirm skip-path expectation:

- If `actionable_count == 0` and no other system events are pending, heartbeat should avoid costly agent execution.

3. Validate actionable path expectation:

- If `actionable_count > 0`, heartbeat should include Todoist context as a `todoist_summary` signal.

---

## Safe troubleshooting checklist

### Symptom: heartbeat is not reflecting Todoist tasks

1. Confirm token in environment:

```bash
env | grep TODOIST_API_TOKEN
```

2. Confirm CLI can read tasks:

```bash
uv run python -m universal_agent.cli.todoist_cli tasks
```

3. If CLI fails, resolve token/dependency issues before debugging heartbeat logic.

### Symptom: brainstorm duplicates are proliferating

1. Ensure `--dedupe-key` is used consistently.
2. Keep dedupe key stable by idea identity (not timestamp).
3. Re-capture with same key to raise confidence on an existing item instead of creating a new one.

---

## Verification commands (operator quick set)

```bash
uv run pytest \
  tests/unit/test_todoist_service.py \
  tests/unit/test_todoist_cli.py \
  tests/unit/test_todoist_bridge.py \
  tests/unit/test_heartbeat_todoist_injection.py -q
```

Optional guarded live integration suite:

```bash
RUN_TODOIST_LIVE_TESTS=1 \
TODOIST_API_TOKEN=<token> \
uv run pytest tests/integration/test_todoist_live_guarded.py -q
```

---

## Scope reminders

Not included in this runbook's implementation scope:

1. automatic brainstorm promotion,
2. automatic work-thread creation from approved ideas,
3. heartbeat/cron self-maintenance automation for CODIE.
