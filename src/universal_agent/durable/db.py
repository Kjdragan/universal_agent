import os
import sqlite3
from typing import Optional

DEFAULT_DB_FILENAME = "runtime_state.db"


def get_runtime_db_path() -> str:
    env_path = os.getenv("UA_RUNTIME_DB_PATH")
    if env_path:
        return env_path

    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    )
    runtime_dir = os.path.join(repo_root, "AGENT_RUN_WORKSPACES")
    os.makedirs(runtime_dir, exist_ok=True)
    return os.path.join(runtime_dir, DEFAULT_DB_FILENAME)


def connect_runtime_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or get_runtime_db_path()
    # NOTE: Multiple UA processes can be running (gateway, CLI, worker).
    # - Use a generous busy timeout for lock contention.
    # - Disable same-thread checks because the gateway can dispatch work
    #   across async tasks and libraries that may use background threads.
    conn = sqlite3.connect(path, timeout=60.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=60000;")
    return conn
