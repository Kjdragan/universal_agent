from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ExecutionSession:
    workspace_dir: str
    run_id: Optional[str] = None
    trace: Optional[dict] = None
    runtime_db_conn: Optional[Any] = None
    current_run_attempt_id: Optional[str] = None
