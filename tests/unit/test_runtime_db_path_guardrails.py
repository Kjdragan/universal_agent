from __future__ import annotations

from pathlib import Path

from universal_agent.durable.db import (
    connect_runtime_db,
    get_coder_vp_db_path,
    get_runtime_db_path,
    get_sqlite_busy_timeout_ms,
    get_vp_db_path,
)


def test_runtime_db_default_stays_under_agent_run_workspaces(monkeypatch):
    monkeypatch.delenv("UA_RUNTIME_DB_PATH", raising=False)

    db_path = Path(get_runtime_db_path()).resolve()
    assert db_path.name == "runtime_state.db"
    assert "AGENT_RUN_WORKSPACES" in db_path.parts


def test_runtime_db_env_override_still_supported(monkeypatch, tmp_path: Path):
    custom_path = (tmp_path / "custom_runtime.db").resolve()
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(custom_path))

    assert Path(get_runtime_db_path()).resolve() == custom_path


def test_coder_vp_db_default_uses_dedicated_file(monkeypatch):
    monkeypatch.delenv("UA_CODER_VP_DB_PATH", raising=False)

    db_path = Path(get_coder_vp_db_path()).resolve()
    assert db_path.name == "coder_vp_state.db"
    assert "AGENT_RUN_WORKSPACES" in db_path.parts


def test_coder_vp_db_env_override_still_supported(monkeypatch, tmp_path: Path):
    custom_path = (tmp_path / "custom_coder_vp.db").resolve()
    monkeypatch.setenv("UA_CODER_VP_DB_PATH", str(custom_path))

    assert Path(get_coder_vp_db_path()).resolve() == custom_path


def test_vp_db_default_uses_dedicated_file(monkeypatch):
    monkeypatch.delenv("UA_VP_DB_PATH", raising=False)

    db_path = Path(get_vp_db_path()).resolve()
    assert db_path.name == "vp_state.db"
    assert "AGENT_RUN_WORKSPACES" in db_path.parts


def test_vp_db_env_override_still_supported(monkeypatch, tmp_path: Path):
    custom_path = (tmp_path / "custom_vp.db").resolve()
    monkeypatch.setenv("UA_VP_DB_PATH", str(custom_path))

    assert Path(get_vp_db_path()).resolve() == custom_path


def test_sqlite_busy_timeout_uses_default_when_no_env(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("UA_SQLITE_BUSY_TIMEOUT_MS", raising=False)
    db_path = (tmp_path / "runtime_state.db").resolve()

    conn = connect_runtime_db(str(db_path))
    try:
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()

    assert get_sqlite_busy_timeout_ms() == 15000
    assert busy_timeout == 15000


def test_sqlite_busy_timeout_env_override_is_honored(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("UA_SQLITE_BUSY_TIMEOUT_MS", "4500")
    db_path = (tmp_path / "runtime_state.db").resolve()

    conn = connect_runtime_db(str(db_path))
    try:
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()

    assert get_sqlite_busy_timeout_ms() == 4500
    assert busy_timeout == 4500
