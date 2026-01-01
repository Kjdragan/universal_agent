import os
import sqlite3
from typing import Optional

from .migrations import ensure_schema


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
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    ensure_schema(conn)
    return conn
