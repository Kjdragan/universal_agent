"""Tests for the spawn-inclusive cron script timeout (60-minute wedge fix).

Root cause (VP diagnosis 2026-07-23, task_32c02c29e190): the per-job timeout
wrapped only ``proc.communicate()``, so a hung
``asyncio.create_subprocess_exec`` never armed the timer and the run sat
silent until the 60-minute stuck-run reaper — 29 wedged runs across Jun–Jul,
all the every-minute ``simone_chat_auto_complete`` job.
``_spawn_script_with_timeout`` moves the ``wait_for`` boundary outward so the
job timeout covers the WHOLE subprocess lifecycle.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from universal_agent import cron_service


@pytest.mark.asyncio
async def test_hung_spawn_trips_the_job_timeout(monkeypatch):
    """A spawn that never resolves must be killed at timeout_seconds — not
    left for the 60-minute reaper. proc is None (never assigned)."""

    async def _hang_forever(*args, **kwargs):
        await asyncio.sleep(3600)

    monkeypatch.setattr(cron_service.asyncio, "create_subprocess_exec", _hang_forever)

    proc, stdout, stderr, killed = await asyncio.wait_for(
        cron_service._spawn_script_with_timeout(
            argv=[sys.executable, "-c", "print('hi')"],
            cwd=".",
            env={"PATH": "/usr/bin:/bin"},
            timeout_seconds=0.2,
            job_id="testjob",
        ),
        timeout=10,  # the test's own guard: the helper must return promptly
    )
    assert killed is True
    assert proc is None
    assert (stdout, stderr) == (b"", b"")


@pytest.mark.asyncio
async def test_hung_process_after_spawn_still_times_out():
    """The pre-existing behaviour (communicate hangs) keeps working."""
    proc, stdout, stderr, killed = await cron_service._spawn_script_with_timeout(
        argv=[sys.executable, "-c", "import time; time.sleep(3600)"],
        cwd=".",
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=0.5,
        job_id="testjob",
    )
    assert killed is True
    assert proc is not None
    assert proc.returncode is not None  # killed and reaped


@pytest.mark.asyncio
async def test_normal_run_completes_with_output():
    proc, stdout, stderr, killed = await cron_service._spawn_script_with_timeout(
        argv=[sys.executable, "-c", "print('ok-marker')"],
        cwd=".",
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=30,
        job_id="testjob",
    )
    assert killed is False
    assert proc.returncode == 0
    assert b"ok-marker" in stdout


@pytest.mark.asyncio
async def test_on_spawned_hook_runs_and_failures_are_swallowed():
    seen: list[int] = []

    def _hook(proc):
        seen.append(proc.pid)
        raise RuntimeError("bookkeeping hiccup must not kill the run")

    proc, stdout, stderr, killed = await cron_service._spawn_script_with_timeout(
        argv=[sys.executable, "-c", "print('with-hook')"],
        cwd=".",
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=30,
        job_id="testjob",
        on_spawned=_hook,
    )
    assert killed is False
    assert seen and seen[0] == proc.pid
    assert b"with-hook" in stdout
