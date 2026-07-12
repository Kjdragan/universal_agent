---
name: activity-state-db-inspector
description: >-
  Inspect Universal Agent state SQLite databases (activity_state.db,
  coder_vp_state.db) without hand-writing fragile sqlite3 one-liners. Use
  whenever you need to query task_hub_items, activity_events, vp_missions,
  dispatch queues, proactive_* tables, or any UA runtime state — or when a
  sqlite query against UA state just failed with "no such table" or wrong
  column names. Provides a read-only CLI with schema introspection and canned
  queries; never guess table or column names again.
---

# Activity State DB Inspector

One read-only CLI, `scripts/asdb.py`, replaces ad-hoc
`DB=...; sqlite3 -header "$DB" "SELECT ..."` constructions. Every connection
opens `mode=ro` — it cannot write, so it is always safe to run.

## The trap this skill exists for

UA state is split across TWO databases under
`/opt/universal_agent/AGENT_RUN_WORKSPACES/` (override root with
`UA_STATE_ROOT`):

| alias | file | what lives there |
|-------|------|------------------|
| `activity` | `activity_state.db` | `task_hub_items` and all `task_hub_*` (assignments, runs, dispatch_queue, evaluations…), `activity_events` (+stream/audit), all `proactive_*` tables, `agentmail_*`, `csi_specialist_loops`, `token_usage_events` |
| `vp` | `coder_vp_state.db` | `vp_missions`, `vp_sessions`, `vp_events`, `runs`, `run_attempts`, `run_steps`, `tool_calls`, `checkpoints` |

`vp_missions` is NOT in `activity_state.db` — querying it there is the classic
"no such table: vp_missions" failure. The CLI's canned `missions` command
already points at the right file.

## Usage

```bash
S=<this skill dir>/scripts/asdb.py     # runs on system python3, stdlib only

python3 $S dbs                         # which DBs exist on this box
python3 $S tables --db activity       # tables + row counts
python3 $S schema task_hub_items      # exact columns — check BEFORE ad-hoc SQL
python3 $S tasks -n 10                # recent task_hub_items
python3 $S events -n 10               # recent activity_events
python3 $S missions -n 10             # recent vp_missions (vp db, automatically)
python3 $S q "SELECT status, COUNT(*) FROM task_hub_items GROUP BY status"
python3 $S q "SELECT ..." --db vp --json   # --json = full untruncated values
```

Table output truncates cells to 60 chars for terminal sanity; use `--json`
when you need full text (e.g. `objective`, `metadata_json`).

## Rules

1. Never hand-write `sqlite3` against UA state DBs; call `asdb.py`.
2. Before any ad-hoc `q`, run `schema <table>` once — columns drift
   (`task_hub_items` has gained 9+ columns via ALTER TABLE).
3. Writes are out of scope by design. State mutations go through the owning
   service (`task_hub.py`, dispatch, VP bridge) — never raw SQL.

`python3 $S selfcheck` proves the plumbing (canned query + read-only
enforcement) without touching real DBs.

> Mirror note: canonical home is dragan-plugins `skills/activity-state-db-inspector/` — edit there first, then re-sync this copy.
