"""Tests for CronStore.append_run size-based rotation of cron_runs.jsonl.

The run-history log is append-only; a high-frequency cron drove it to
122 MB / ~120k lines in prod with no bound. append_run now rolls the active
log to cron_runs.jsonl.1 once it exceeds UA_CRON_RUNS_MAX_BYTES (default
50 MB), keeping exactly one rollover, and read_runs stitches the rollover +
active log so recent lines survive the boundary.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent.cron_service import CronRunRecord, CronStore


def _record(n: int) -> CronRunRecord:
    return CronRunRecord(
        run_id=f"run-{n}",
        job_id="freq_job",
        status="success",
        scheduled_at=float(n),
        started_at=float(n),
    )


def _make_store(tmp_path: Path) -> CronStore:
    return CronStore(
        jobs_path=tmp_path / "cron_jobs.json",
        runs_path=tmp_path / "cron_runs.jsonl",
    )


def test_rotation_triggers_at_cap_and_preserves_recent_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the active log exceeds the cap it rolls to .1; the next append
    starts a fresh active log; recent lines are still readable via read_runs."""
    # Tiny cap so a handful of records trips it.
    monkeypatch.setenv("UA_CRON_RUNS_MAX_BYTES", "400")
    store = _make_store(tmp_path)
    rollover = store.runs_path.with_name(store.runs_path.name + ".1")

    # Append enough records to blow past the 400-byte cap. Each JSON line is
    # ~150 bytes, so ~5+ records exceed it and the NEXT append rotates.
    total = 12
    for n in range(total):
        store.append_run(_record(n))

    # A rollover file exists (rotation actually happened).
    assert rollover.exists(), "expected cron_runs.jsonl.1 after exceeding cap"
    # The active log was reset mid-run: it holds fewer than all `total` lines
    # (the rest live in the rollover), proving the growth bound engaged.
    active_lines = store.runs_path.read_text().count("\n")
    assert 0 < active_lines < total

    # read_runs stitches rollover + active log: every appended run is still
    # visible and in newest-last order (recent lines preserved).
    rows = store.read_runs(limit=1000)
    assert [r["run_id"] for r in rows] == [f"run-{n}" for n in range(total)]


def test_only_one_rollover_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rotating twice replaces .1 rather than accumulating .2/.3 — the growth
    bound holds at one rollover, and no .2 is ever created."""
    monkeypatch.setenv("UA_CRON_RUNS_MAX_BYTES", "300")
    store = _make_store(tmp_path)
    for n in range(40):
        store.append_run(_record(n))

    rollover2 = store.runs_path.with_name(store.runs_path.name + ".2")
    assert not rollover2.exists(), "only one rollover (.1) must be kept"
    # The very newest run is always readable.
    rows = store.read_runs(limit=1)
    assert rows[-1]["run_id"] == "run-39"


def test_rotation_disabled_when_cap_non_positive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UA_CRON_RUNS_MAX_BYTES <= 0 disables rotation — the log grows in place
    and no rollover is created."""
    monkeypatch.setenv("UA_CRON_RUNS_MAX_BYTES", "0")
    store = _make_store(tmp_path)
    rollover = store.runs_path.with_name(store.runs_path.name + ".1")
    for n in range(50):
        store.append_run(_record(n))
    assert not rollover.exists()
    rows = store.read_runs(limit=1000)
    assert len(rows) == 50
