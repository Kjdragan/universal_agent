"""Unit tests for ``scripts/snapshot_prod_to_dev.py``.

Tests argument parsing, production-mode refusal, and dry-run output.
SSH/scp execution is not exercised — those need a real prod host.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

# Load the script as a module since it's not in src/. Register in
# sys.modules BEFORE exec so dataclass internals can resolve the
# module reference (otherwise dataclasses raises AttributeError on
# sys.modules.get(cls.__module__).__dict__).
_SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "snapshot_prod_to_dev.py"
_spec = importlib.util.spec_from_file_location("snapshot_prod_to_dev", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
snapshot_module = importlib.util.module_from_spec(_spec)
sys.modules["snapshot_prod_to_dev"] = snapshot_module
_spec.loader.exec_module(snapshot_module)


def test_known_dbs_list_matches_durable_db_filenames():
    """KNOWN_DBS should match the DEFAULT_*_DB_FILENAME constants in durable/db.py."""
    assert "runtime_state.db" in snapshot_module.KNOWN_DBS
    assert "activity_state.db" in snapshot_module.KNOWN_DBS
    assert "vp_state.db" in snapshot_module.KNOWN_DBS
    assert "coder_vp_state.db" in snapshot_module.KNOWN_DBS


def test_default_ssh_host_is_ua_at_uaonvps():
    """Sanity: default SSH host points at the production VPS."""
    assert snapshot_module.DEFAULT_SSH_HOST == "ua@uaonvps"


def test_refuses_to_run_in_production(monkeypatch):
    monkeypatch.setenv("UA_RUNTIME_STAGE", "production")
    with pytest.raises(SystemExit) as exc_info:
        snapshot_module._refuse_in_production()
    assert exc_info.value.code == 2


def test_does_not_refuse_in_dev(monkeypatch):
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    # Should not raise
    snapshot_module._refuse_in_production()


def test_does_not_refuse_when_stage_unset(monkeypatch):
    monkeypatch.delenv("UA_RUNTIME_STAGE", raising=False)
    snapshot_module._refuse_in_production()


def test_parse_args_defaults():
    cfg = snapshot_module.parse_args([])
    assert cfg.ssh_host == "ua@uaonvps"
    assert cfg.prod_workspaces_dir == "/opt/universal_agent/AGENT_RUN_WORKSPACES"
    assert cfg.dev_workspaces_dir == Path("AGENT_RUN_WORKSPACES")
    assert cfg.dbs == snapshot_module.KNOWN_DBS
    assert cfg.dry_run is False
    assert cfg.force is False


def test_parse_args_custom():
    cfg = snapshot_module.parse_args([
        "--ssh-host", "alice@host.example.com",
        "--prod-workspaces-dir", "/opt/foo",
        "--dev-workspaces-dir", "/tmp/dev",
        "--db", "custom.db",
        "--dry-run",
        "--force",
    ])
    assert cfg.ssh_host == "alice@host.example.com"
    assert cfg.prod_workspaces_dir == "/opt/foo"
    assert cfg.dev_workspaces_dir == Path("/tmp/dev")
    assert cfg.dbs == ("custom.db",)
    assert cfg.dry_run is True
    assert cfg.force is True


def test_parse_args_multiple_db_flags():
    cfg = snapshot_module.parse_args(["--db", "a.db", "--db", "b.db"])
    assert cfg.dbs == ("a.db", "b.db")


def test_dry_run_main_in_dev(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    rc = snapshot_module.main([
        "--dry-run",
        "--ssh-host", "test@host",
        "--prod-workspaces-dir", "/opt/test",
        "--dev-workspaces-dir", str(tmp_path),
        "--db", "test.db",
    ])
    captured = capsys.readouterr()
    assert rc == 0
    # Dry-run should have logged "would run" for the backup + scp commands
    assert "DRY-RUN: would run:" in captured.out
    assert "test.db" in captured.out
    # Should NOT have actually executed anything
    assert not (tmp_path / "test.db").exists()


def test_main_refuses_production_via_env(monkeypatch, capsys):
    monkeypatch.setenv("UA_RUNTIME_STAGE", "production")
    with pytest.raises(SystemExit) as exc_info:
        snapshot_module.main(["--dry-run"])
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "refusing to run in production" in captured.err
