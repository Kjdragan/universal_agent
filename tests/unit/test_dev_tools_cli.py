"""Unit tests for ``python -m universal_agent.dev_tools`` CLI.

Verifies subcommand parsing, exit codes, and basic output. Doesn't
exercise the gateway — these are inspection helpers, not live triggers.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_agent.dev_tools.__main__ import main

# ─── env-report ────────────────────────────────────────────────────────


def test_env_report_in_dev_returns_zero(monkeypatch, capsys, caplog) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    import logging
    with caplog.at_level(logging.INFO):
        rc = main(["env-report"])
    assert rc == 0
    # report_dev_overrides logs per-loop info; we expect "loop_control" in logs.
    assert any("loop_control" in r.message for r in caplog.records)


def test_env_report_outside_dev_returns_nonzero(monkeypatch, capsys) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "production")
    rc = main(["env-report"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "not in dev mode" in captured.err


# ─── loop-status ───────────────────────────────────────────────────────


def test_loop_status_known_loop_dev_default(monkeypatch, capsys) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.delenv("UA_HEARTBEAT_ENABLED", raising=False)
    monkeypatch.delenv("UA_DEV_HEARTBEAT_FORCE_ON", raising=False)
    rc = main(["loop-status", "heartbeat"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "loop[heartbeat]:" in captured.out
    assert "OFF" in captured.out


def test_loop_status_known_loop_with_dev_force_on(monkeypatch, capsys) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.setenv("UA_DEV_HEARTBEAT_FORCE_ON", "1")
    rc = main(["loop-status", "heartbeat"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "ON" in captured.out
    assert "opt-in" in captured.out


def test_loop_status_unknown_loop_warns(monkeypatch, capsys) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    rc = main(["loop-status", "totally_made_up"])
    captured = capsys.readouterr()
    assert rc == 0
    # Stdout still has the answer
    assert "loop[totally_made_up]:" in captured.out
    # Stderr warns it's not in _KNOWN_LOOPS
    assert "not in the canonical _KNOWN_LOOPS list" in captured.err


def test_loop_status_in_production_explicit_on(monkeypatch, capsys) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "production")
    monkeypatch.setenv("UA_HEARTBEAT_ENABLED", "1")
    rc = main(["loop-status", "heartbeat"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "ON" in captured.out


# ─── cron-list ─────────────────────────────────────────────────────────


def test_cron_list_no_file_returns_zero(tmp_path: Path, capsys) -> None:
    rc = main(["cron-list", "--workspace", str(tmp_path)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "no cron jobs file" in captured.out


def test_cron_list_empty_file(tmp_path: Path, capsys) -> None:
    (tmp_path / "cron_jobs.json").write_text("{}", encoding="utf-8")
    rc = main(["cron-list", "--workspace", str(tmp_path)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "is empty" in captured.out


def test_cron_list_with_jobs(tmp_path: Path, capsys) -> None:
    jobs = {
        "abc123": {
            "job_id": "abc123",
            "cron_expr": "0 7 * * *",
            "next_run_at": 1700000000.0,
            "command": "!script some.module",
        },
        "def456": {
            "job_id": "def456",
            "every_seconds": 1800,
            "command": "!script another.module",
        },
    }
    (tmp_path / "cron_jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
    rc = main(["cron-list", "--workspace", str(tmp_path)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "2 persisted cron job(s)" in captured.out
    assert "abc123" in captured.out
    assert "def456" in captured.out
    assert "0 7 * * *" in captured.out
    assert "every 1800s" in captured.out


def test_cron_list_malformed_file(tmp_path: Path, capsys) -> None:
    (tmp_path / "cron_jobs.json").write_text("not json at all", encoding="utf-8")
    rc = main(["cron-list", "--workspace", str(tmp_path)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "failed to parse" in captured.err


def test_cron_list_unexpected_shape(tmp_path: Path, capsys) -> None:
    (tmp_path / "cron_jobs.json").write_text("[1, 2, 3]", encoding="utf-8")
    rc = main(["cron-list", "--workspace", str(tmp_path)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "unexpected top-level shape" in captured.err


# ─── unknown subcommand ────────────────────────────────────────────────


def test_unknown_subcommand_exits_nonzero(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["nonexistent-subcommand"])
    assert exc_info.value.code != 0
