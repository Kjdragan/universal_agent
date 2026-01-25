# Letta Workspace

Organized home for Letta-related scripts, reports, and notes.

## Directory Layout

```
letta/
  README.md
  scripts/
    fetch_letta_memories.py
    list_agents.py
    cleanup_memories.py
    fetch_local_memory_context.py
  reports/
    letta_memory_report*.md
    letta_memory_report*.json
```

## Requirements

- `.env` in repo root with `LETTA_API_KEY` and `LETTA_PROJECT_ID`
- Python environment: `source .venv/bin/activate`

## Scripts

### 1) Fetch Letta Memories

Captures memory blocks for all `universal_agent*` agents and produces a report.

```
python letta/scripts/fetch_letta_memories.py
python letta/scripts/fetch_letta_memories.py --json
python letta/scripts/fetch_letta_memories.py --output letta/reports/letta_memory_report.md
```

### 2) List Letta Agents

Lists agents, memory blocks, and recent messages.

```
python letta/scripts/list_agents.py
```

### 3) Cleanup + Seed Memories

Seeds missing blocks, populates empty `system_rules` and `project_context`, and adds
`failure_patterns` + `recovery_patterns` templates. Defaults to **dry-run**.

```
python letta/scripts/cleanup_memories.py --apply
python letta/scripts/cleanup_memories.py --apply --only universal_agent
python letta/scripts/cleanup_memories.py --delete-test-agents --apply
```

### 4) Local Memory (non-Letta)

This is for the legacy/local `Memory_System` context (not Letta).

```
python letta/scripts/fetch_local_memory_context.py
```

## Reports

Store Letta memory snapshots in `letta/reports/` for quick diffing over time.
