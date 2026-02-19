from __future__ import annotations

from pathlib import Path

from universal_agent.durable.db import get_runtime_db_path


def test_runtime_db_default_stays_under_agent_run_workspaces(monkeypatch):
    monkeypatch.delenv("UA_RUNTIME_DB_PATH", raising=False)

    db_path = Path(get_runtime_db_path()).resolve()
    assert db_path.name == "runtime_state.db"
    assert "AGENT_RUN_WORKSPACES" in db_path.parts


def test_runtime_db_env_override_still_supported(monkeypatch, tmp_path: Path):
    custom_path = (tmp_path / "custom_runtime.db").resolve()
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(custom_path))

    assert Path(get_runtime_db_path()).resolve() == custom_path
