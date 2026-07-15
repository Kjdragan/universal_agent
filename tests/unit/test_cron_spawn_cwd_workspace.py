"""Regression tests for the cron spawn CWD / workspace-dir fix.

Background (2026-07-15, Atlas investigation vp-mission-76f903fbe4181501ae67e2f5,
confirmed by Simone): the paper_to_podcast_daily cron SUCCEEDED — a real 40 MB
podcast landed at ``/opt/universal_agent/work_products/paper_to_podcast/`` — but
the post-run guard fired fail-loud on an empty dir because it inspects
``job.workspace_dir/work_products/paper_to_podcast/`` (which
``prepare_run_workspace`` had just wiped). Root cause: cron ``!script`` spawn
sites resolved the subprocess CWD as ``job.workspace_dir_resolved if hasattr(...)
else Path(__file__).resolve().parents[2]``, and ``workspace_dir_resolved`` is
never a declared ``CronJob`` field and is never populated — so the ``hasattr``
check is effectively always False and the spawn CWD fell back to the DAEMON
ROOT (``/opt/universal_agent``), decoupling the agent's relative
``work_products/`` writes from the directory the guard inspects. The in-process
LLM-session path has the same daemon-CWD behaviour (the SDK subprocess inherits
the daemon CWD), so the paper_to_podcast prompt is additionally pinned to
absolute output paths regardless of CWD.

These tests pin both layers:

1. ``_cron_spawn_cwd`` falls back to ``job.workspace_dir`` (never the daemon
   root) when ``workspace_dir_resolved`` is unset — reproducing the race.
2. ``_paper_to_podcast_command`` embeds an ABSOLUTE output path so the agent
   writes to the exact dir the guard inspects regardless of CWD.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from universal_agent import cron_service, gateway_server
from universal_agent.cron_service import CronJob, _cron_spawn_cwd


def _daemon_root() -> str:
    """The CWD the old buggy fallback returned."""
    return str(Path(cron_service.__file__).resolve().parents[2])


# ---------------------------------------------------------------------------
# Fix #1: _cron_spawn_cwd
# ---------------------------------------------------------------------------


def test_spawn_cwd_falls_back_to_workspace_dir_when_resolved_unset():
    """When workspace_dir_resolved is unset (the normal case — it is not a
    declared CronJob field and is never populated), the spawn CWD MUST be
    job.workspace_dir, NOT the daemon root. This is the race that produced the
    2026-07-15 paper_to_podcast false-positive."""
    job = CronJob(
        job_id="cron_paper_to_podcast",
        user_id="cron:paper_to_podcast",
        workspace_dir="/agent/run/workspaces/cron_paper_to_podcast",
        command="!script universal_agent.scripts.paper_to_podcast",
    )
    assert not hasattr(job, "workspace_dir_resolved")  # the race precondition

    cwd = _cron_spawn_cwd(job)

    assert cwd == job.workspace_dir
    # Regression: the old fallback returned the daemon root.
    assert cwd != _daemon_root()
    assert cwd != str(Path("/opt/universal_agent"))


def test_spawn_cwd_honours_workspace_dir_resolved_when_set():
    """If a re-dispatch path populates workspace_dir_resolved, it wins."""
    job = CronJob(
        job_id="some_redispatch",
        user_id="cron:redispatch",
        workspace_dir="/default/workspace",
        command="!script some.module",
    )
    job.workspace_dir_resolved = "/resolved/by/redispatch"  # type: ignore[attr-defined]

    assert _cron_spawn_cwd(job) == "/resolved/by/redispatch"
    assert _cron_spawn_cwd(job) != job.workspace_dir


def test_spawn_cwd_ignores_empty_workspace_dir_resolved():
    """An empty/falsy workspace_dir_resolved must not shadow workspace_dir."""
    job = CronJob(
        job_id="x",
        user_id="cron:x",
        workspace_dir="/the/workspace",
        command="!script m",
    )
    job.workspace_dir_resolved = ""  # type: ignore[attr-defined]

    assert _cron_spawn_cwd(job) == "/the/workspace"


def test_spawn_cwd_never_returns_daemon_root_for_distinct_workspace():
    """Even for an arbitrary !script cron, the CWD must track job.workspace_dir,
    not decay to the daemon root."""
    job = CronJob(
        job_id="cron_other",
        user_id="cron:other",
        workspace_dir="/some/per/job/workspace",
        command="!script some.module --flag",
    )
    cwd = _cron_spawn_cwd(job)
    assert cwd == "/some/per/job/workspace"
    assert cwd != _daemon_root()


def test_spawn_cwd_accepts_duck_typed_job():
    """The helper uses getattr/str, so a SimpleNamespace job works too (the
    cron dispatcher passes CronJob instances, but duck-typing keeps the helper
    robust and mirrors how other cron tests build fake jobs)."""
    job = SimpleNamespace(workspace_dir="/duck/ws")
    assert _cron_spawn_cwd(job) == "/duck/ws"


# ---------------------------------------------------------------------------
# Fix #2: _paper_to_podcast_command embeds an ABSOLUTE output path
# ---------------------------------------------------------------------------


def test_paper_to_podcast_command_embeds_absolute_output_path():
    """The prompt must name the absolute output dir so the in-process LLM
    session (which runs with the daemon CWD, not job.workspace_dir) writes to
    the exact dir the post-run guard inspects, regardless of CWD."""
    command = gateway_server._paper_to_podcast_command()

    workspaces_dir = Path(gateway_server.WORKSPACES_DIR)
    expected_abs = str(
        (workspaces_dir / "cron_paper_to_podcast" / "work_products" / "paper_to_podcast")
        .expanduser()
        .resolve()
    )

    # The absolute dir is named up front in the directive.
    assert command.startswith("OUTPUT DIRECTORY")
    assert expected_abs in command

    # The directive + every path reference must be absolute.
    assert Path(expected_abs).is_absolute()
    assert "cron_paper_to_podcast/work_products/paper_to_podcast" in expected_abs


def test_paper_to_podcast_command_has_no_bare_relative_output_paths():
    """After absolutization, the relative 'work_products/paper_to_podcast' must
    survive ONLY as the directive's descriptive mention (quoted), never as an
    actual write target. Every real reference must be the absolute dir."""
    command = gateway_server._paper_to_podcast_command()

    workspaces_dir = Path(gateway_server.WORKSPACES_DIR)
    abs_dir = str(
        (workspaces_dir / "cron_paper_to_podcast" / "work_products" / "paper_to_podcast")
        .expanduser()
        .resolve()
    )

    # The absolute dir is used in many places (download, save-flat, report,
    # publish, FAILURE.txt, manifest) — at least several.
    assert command.count(abs_dir) >= 4, (
        f"expected the absolute output dir to appear >=4 times, "
        f"got {command.count(abs_dir)}"
    )

    # The relative form 'work_products/paper_to_podcast' legitimately survives
    # as the TAIL of every injected absolute path (.../cron_paper_to_podcast/
    # work_products/paper_to_podcast) AND once as the directive's descriptive
    # mention. What must NOT survive is a BARE relative write target. Count
    # occurrences that are NOT the absolute path's tail — that bare count must
    # be exactly 1 (the directive).
    abs_tail = "cron_paper_to_podcast/work_products/paper_to_podcast"
    total = command.count("work_products/paper_to_podcast")
    in_absolute = command.count(abs_tail)
    bare = total - in_absolute
    assert bare == 1, (
        f"expected exactly 1 bare relative mention (the directive), got {bare}; "
        f"total={total}, inside-absolute-path={in_absolute}"
    )


def test_paper_to_podcast_command_output_path_matches_guard_inspected_dir():
    """The absolute path injected into the prompt MUST be the same directory
    the post-run guard inspects (paper_to_podcast_guard keys off
    <workspace_dir>/work_products/paper_to_podcast). This is the invariant the
    whole fix exists to preserve."""
    from universal_agent.services.paper_to_podcast_guard import (
        _WORK_PRODUCTS_SUBPATH,
    )

    command = gateway_server._paper_to_podcast_command()
    workspaces_dir = Path(gateway_server.WORKSPACES_DIR)
    injected_abs = str(
        (workspaces_dir / "cron_paper_to_podcast" / "work_products" / "paper_to_podcast")
        .expanduser()
        .resolve()
    )

    # The guard's subpath is the tail of the injected absolute dir.
    assert injected_abs.endswith(_WORK_PRODUCTS_SUBPATH)
    assert injected_abs in command
