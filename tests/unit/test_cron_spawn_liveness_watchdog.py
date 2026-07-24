"""Regression tests for the LivenessWatchdog-governed cron ``!script`` spawn.

Root cause (recurring nightly 2026-06 -> 2026-07, re-diagnosed 2026-07-24):
``_spawn_script_with_timeout`` wrapped ``proc.communicate()`` in a bare
``asyncio.wait_for`` wall-clock cap. ``communicate()`` blocks until the child
exits and emits NO incremental signal, so a ``!script`` worker that spawned
fine then hung silently (a sqlite lock, a blocked import) -- or whose fork/exec
itself stalled -- left the single-threaded cron dispatch loop with no heartbeat
and no timely kill. The every-minute ``simone_chat_auto_complete`` job wedged
the loop for up to ~60 minutes until the stuck-run reaper fired, and every
other cron stopped dispatching in the same window.

The fix wraps the spawn+drain in the shared ``timeout_policy.LivenessWatchdog``
(idle/no-progress kill, the same policy the in-process ProcessTurnAdapter and
the VP claude CLI lane use): each stdout/stderr chunk is a heartbeat, and a
worker that produces no output for ``idle_kill_seconds`` is reaped in seconds.
``timeout_seconds`` survives only as the absolute backstop.

These tests prove: (1) a silently-hung worker is reaped at the idle boundary,
not the backstop/reaper; (2) a fork/exec stall is reaped at the idle boundary;
(3) a stuck job frees the sequential dispatch loop fast enough that an
unrelated cron dispatches promptly; (4) the kill is progress-aware -- a worker
that keeps emitting output is NOT reaped (it is not a wall-clock cap).
"""

from __future__ import annotations

import asyncio
import sys
import time

import pytest

from universal_agent import cron_service
from universal_agent.timeout_policy import cron_script_idle_kill_seconds


@pytest.mark.asyncio
async def test_hung_worker_reaped_by_idle_kill_not_backstop():
    """A silently-hung worker (spawned fine, then sleeps forever with no
    output) is reaped by the idle kill in ~``idle_kill`` seconds -- NOT the
    ``timeout_seconds`` backstop, NOT the 60-minute reaper."""
    idle = 2.0
    t0 = time.monotonic()
    proc, stdout, stderr, killed = await asyncio.wait_for(
        cron_service._spawn_script_with_timeout(
            argv=[sys.executable, "-c", "import time; time.sleep(3600)"],
            cwd=".",
            env={"PATH": "/usr/bin:/bin"},
            timeout_seconds=3600,  # backstop far away -- must NOT be what fires
            job_id="wedgejob",
            idle_kill_seconds=idle,
        ),
        timeout=20,  # test guard; the helper must return in ~idle, not 3600s
    )
    elapsed = time.monotonic() - t0
    assert killed is True
    assert proc is not None and proc.returncode is not None  # killed + reaped
    # Reaped near the idle window, nowhere near the 3600s backstop.
    assert elapsed < idle + 6, f"reap took {elapsed:.1f}s (expected ~{idle}s)"


@pytest.mark.asyncio
async def test_hung_spawn_reaped_by_idle_kill(monkeypatch):
    """A fork/exec that never resolves (proc stays None) is reaped by the idle
    kill in ~``idle_kill`` seconds, not the backstop. This is the spawn-stall
    shape that previously wedged until the 60-minute reaper."""

    async def _hang_forever(*args, **kwargs):
        await asyncio.sleep(3600)

    monkeypatch.setattr(cron_service.asyncio, "create_subprocess_exec", _hang_forever)

    t0 = time.monotonic()
    proc, stdout, stderr, killed = await asyncio.wait_for(
        cron_service._spawn_script_with_timeout(
            argv=[sys.executable, "-c", "print('hi')"],
            cwd=".",
            env={"PATH": "/usr/bin:/bin"},
            timeout_seconds=3600,
            job_id="spawnstall",
            idle_kill_seconds=1.5,
        ),
        timeout=15,
    )
    elapsed = time.monotonic() - t0
    assert killed is True
    assert proc is None
    assert elapsed < 1.5 + 6, f"spawn reap took {elapsed:.1f}s"


@pytest.mark.asyncio
async def test_stuck_job_does_not_block_unrelated_dispatch():
    """The single-threaded cron dispatch loop processes jobs sequentially. A
    stuck ``!script`` job must be reaped in ~``idle_kill`` seconds so the next
    (unrelated) cron dispatches promptly -- the regression that used to wedge
    the whole loop for ~60 minutes until the stuck-run reaper fired."""
    idle = 2.0
    t0 = time.monotonic()

    # Job 1: hangs silently (the wedge shape).
    p1, _out1, _err1, k1 = await cron_service._spawn_script_with_timeout(
        argv=[sys.executable, "-c", "import time; time.sleep(3600)"],
        cwd=".",
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=3600,
        job_id="stuck",
        idle_kill_seconds=idle,
    )
    # Job 2: unrelated + healthy -- must dispatch promptly after job 1 reaped.
    p2, out2, _err2, k2 = await cron_service._spawn_script_with_timeout(
        argv=[sys.executable, "-c", "print('unrelated-ok')"],
        cwd=".",
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=30,
        job_id="unrelated",
        idle_kill_seconds=idle,
    )
    elapsed = time.monotonic() - t0

    assert k1 is True and p1.returncode is not None  # stuck job reaped fast
    assert k2 is False and p2.returncode == 0  # unrelated job ran fine
    assert b"unrelated-ok" in out2
    # Whole sequence well under the 3600s backstop / 60-min reaper.
    assert elapsed < idle + 10, f"sequence took {elapsed:.1f}s"


@pytest.mark.asyncio
async def test_heartbeats_keep_active_worker_alive():
    """The idle kill is progress-aware, NOT a wall-clock cap: a worker that
    keeps emitting output past the idle window is NOT reaped -- it completes.
    This is the core LivenessWatchdog guarantee that a bare wall-clock cap
    violated when it killed live Simone turns on 2026-06-14."""
    # Print a line every 0.3s for ~1.5s (5 beats), then exit 0. idle_kill=1.0
    # must NOT fire because each line is a heartbeat that resets the window.
    script = (
        "import time, sys\n"
        "for _ in range(5):\n"
        "    print('beat'); sys.stdout.flush(); time.sleep(0.3)\n"
    )
    proc, stdout, stderr, killed = await asyncio.wait_for(
        cron_service._spawn_script_with_timeout(
            argv=[sys.executable, "-c", script],
            cwd=".",
            env={"PATH": "/usr/bin:/bin"},
            timeout_seconds=30,
            job_id="beater",
            idle_kill_seconds=1.0,
        ),
        timeout=20,
    )
    assert killed is False
    assert proc.returncode == 0
    assert stdout.count(b"beat") == 5


def test_cron_script_idle_kill_accessor_reads_env(monkeypatch):
    """The new idle-kill knob is env-driven and defaults to 60s."""
    monkeypatch.setenv("UA_CRON_SCRIPT_IDLE_KILL_SECONDS", "7.5")
    assert cron_script_idle_kill_seconds() == 7.5
    monkeypatch.setenv("UA_CRON_SCRIPT_IDLE_KILL_SECONDS", "0")
    assert cron_script_idle_kill_seconds() == 0.0
    monkeypatch.delenv("UA_CRON_SCRIPT_IDLE_KILL_SECONDS", raising=False)
    assert cron_script_idle_kill_seconds() == 60.0
