---
description: Build a GPU-bound demo approved for desktop GPU execution via the approval email link.
argument-hint: [task_id]
---

# GPU Demo Build

**Human-driven only. Do NOT run this from a daemon, timer, or autonomous agent. Desktop only.**

This command finalizes a tutorial-build task that was held for desktop GPU approval:
1. Verifies the approval state in the activity database (SSHFS path).
2. Invokes the existing `provision-local-gpu-ollama` skill to bring up Ollama + qwen2.5-coder:7b.
3. Scaffolds and builds the demo under `~/lrepos/Cody_Code_Generations/<demo_id>/`.
4. Writes `manifest.json` and calls `finalize_desktop_gpu_demo` to close the Task Hub row.

## Variables

TASK_ID: $ARGUMENTS

## Workflow

### 0. Guard — require TASK_ID

If no `TASK_ID` is provided, STOP immediately and say:
> "Please provide a task_id. Usage: /gpu-demo-build <task_id>"

### 1. Read and validate the task from the VPS activity store

The canonical activity DB lives at the path resolved by `get_activity_db_path()` in
`src/universal_agent/durable/db.py`. In practice this is:
- VPS production: `/opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db`
- Via SSHFS from the desktop: `/home/kjdragan/mnt/vps/opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db`
- Overridden by `UA_ACTIVITY_DB_PATH` env var if set.

Read the task row:

```bash
python3 - <<'PYEOF'
import sqlite3, json, os, sys

db_path = os.getenv("UA_ACTIVITY_DB_PATH") or "/home/kjdragan/mnt/vps/opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db"
task_id = "TASK_ID"

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
row = conn.execute("SELECT * FROM task_hub_items WHERE task_id = ?", (task_id,)).fetchone()
conn.close()

if row is None:
    print(f"ERROR: task {task_id!r} not found in {db_path}", file=sys.stderr)
    sys.exit(1)

item = dict(row)
meta_raw = item.get("metadata") or "{}"
meta = json.loads(meta_raw) if isinstance(meta_raw, str) else (meta_raw or {})
gpu_approval = meta.get("gpu_approval", {})
state = gpu_approval.get("state", "")

print(f"task_id:        {item['task_id']}")
print(f"title:          {item.get('title')}")
print(f"approval state: {state}")
print(f"model:          {gpu_approval.get('model', 'qwen2.5-coder:7b')}")
print(f"metadata:       {json.dumps(meta, indent=2)}")

if state != "approved":
    print(f"\nERROR: gpu_approval.state={state!r}, expected 'approved'. Stop.", file=sys.stderr)
    sys.exit(2)

print("\nOK: task is approved for desktop GPU build.")
PYEOF
```

If the script exits non-zero or `approval state` is not `approved`, STOP and report the error.

### 2. Provision Ollama + qwen2.5-coder:7b

Invoke the existing provision skill (do NOT modify it or create a second one):

```bash
bash /home/kjdragan/lrepos/universal_agent/.claude/skills/provision-local-gpu-ollama/provision_gpu_ollama.sh
```

Capture the last two output lines which are:
```
OLLAMA_URL=http://127.0.0.1:11434/api/generate
OLLAMA_MODEL=qwen2.5-coder:7b
```

Export these into the shell environment before continuing.

If the provision script fails (non-zero exit, REFUSED, or missing OLLAMA_URL/OLLAMA_MODEL),
STOP and report the failure. Do not attempt the build without GPU inference confirmed working.

### 3. Determine the demo directory

The demo directory follows the Cody_Code_Generations convention:
`~/lrepos/Cody_Code_Generations/<demo_id>/`

Derive `<demo_id>` from the task title or TASK_ID (snake_case, lowercase). For example,
`tutorial-build:abc123` → `gpu_demo_abc123`.

Create the directory:
```bash
mkdir -p ~/lrepos/Cody_Code_Generations/<demo_id>
```

### 4. Scaffold and build the demo

Using the task description (from the `description` field in the task row) as the spec:
- Scaffold a minimal working demo in `~/lrepos/Cody_Code_Generations/<demo_id>/`.
- Wire the demo to use `OLLAMA_URL` and `OLLAMA_MODEL` from the environment.
- The demo should run a real inference call against Ollama (not mocked).
- Run the demo / acceptance test to confirm it works end-to-end on the GPU.

### 5. Write manifest.json

Write `~/lrepos/Cody_Code_Generations/<demo_id>/manifest.json`:

```json
{
  "task_id": "TASK_ID",
  "endpoint_required": "ollama_local",
  "endpoint_hit": "ollama_local",
  "model_used": "qwen2.5-coder:7b",
  "demo_dir": "~/lrepos/Cody_Code_Generations/<demo_id>",
  "built_at": "<ISO timestamp>",
  "ollama_url": "<OLLAMA_URL value>"
}
```

### 6. Finalize the Task Hub row

Call `finalize_desktop_gpu_demo` over SSHFS to close the task:

```bash
python3 - <<'PYEOF'
import sqlite3, json, os, sys
sys.path.insert(0, "/home/kjdragan/lrepos/universal_agent/src")

os.environ.setdefault(
    "UA_ACTIVITY_DB_PATH",
    "/home/kjdragan/mnt/vps/opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db"
)

from universal_agent.services.proactive_tutorial_builds import finalize_desktop_gpu_demo
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path

manifest_path = os.path.expanduser("~/lrepos/Cody_Code_Generations/<demo_id>/manifest.json")

with connect_runtime_db(get_activity_db_path()) as conn:
    result = finalize_desktop_gpu_demo(
        conn,
        task_id="TASK_ID",
        manifest_path=manifest_path,
        agent_id="dashboard_operator",
    )

print("Finalized:", json.dumps(result, default=str, indent=2))
PYEOF
```

If `finalize_desktop_gpu_demo` raises `ValueError` (e.g. already built, state mismatch),
report the error but do NOT re-run the build. The demo artifacts are already written.

### 7. Report

- Demo path: `~/lrepos/Cody_Code_Generations/<demo_id>/`
- Model used: the OLLAMA_MODEL value
- Manifest endpoint_hit: `ollama_local`
- Task Hub status: the action result from step 6
- Any acceptance test output

---

**Reminder**: This command is for operator use only on the desktop. It provisions a live
Ollama process (`pkill -f 'ollama serve'` to stop it when done). Never wire this into
an autonomous loop or a VPS timer.
