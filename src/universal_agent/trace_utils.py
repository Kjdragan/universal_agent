import json
import os


def write_trace(trace: dict, workspace_dir: str) -> str:
    trace_path = os.path.join(workspace_dir, "trace.json")
    with open(trace_path, "w") as handle:
        json.dump(trace, handle, indent=2, default=str)
    return trace_path
