"""Hermes Phase F follow-up — cron task-hub linkage tests.

Covers ``services/cron_task_hub_link.py``:

* :func:`derive_cron_task_id` returns the canonical ``cron:<name>`` key.
* :func:`ensure_cron_task_link` auto-ensures a stable task per cron
  source on first call.
* Re-running the same cron does NOT create duplicate task rows (the
  ``cron:<name>`` task_id is the dedupe key).
* ``metadata.skip_task_hub_link = True`` opts a cron out of the linkage.
* ``metadata.task_id`` (the email-scheduler path) is honored verbatim
  and short-circuits the auto-link path.
* :func:`close_cron_task_link` resets a successful run's task back to
  ``open`` so the next tick can re-claim it.
* Each ensure call appends a fresh ``task_hub_assignments`` row + a
  ``task_hub_runs`` row so F.1/F.3 wiring sees per-run granularity.
"""

from __future__ import annotations

import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.services.cron_task_hub_link import (
    CRON_TASK_SOURCE_KIND,
    close_cron_task_link,
    derive_cron_task_id,
    ensure_cron_task_link,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    task_hub.ensure_schema(c)
    yield c
    c.close()


# ── derive_cron_task_id ────────────────────────────────────────────────────


def test_derive_prefers_system_job_over_job_id() -> None:
    assert (
        derive_cron_task_id(system_job="morning_briefing", job_id="job-xyz")
        == "cron:morning_briefing"
    )


def test_derive_falls_back_to_job_id_when_system_job_empty() -> None:
    assert (
        derive_cron_task_id(system_job="", job_id="adhoc-123") == "cron:adhoc-123"
    )


def test_derive_returns_none_when_both_empty() -> None:
    assert derive_cron_task_id(system_job="", job_id="") is None
    assert derive_cron_task_id(system_job=None, job_id=None) is None
    assert derive_cron_task_id(system_job="  ", job_id="  ") is None


# ── ensure_cron_task_link: opt-out ─────────────────────────────────────────


def test_ensure_returns_none_when_skip_flag_set(conn: sqlite3.Connection) -> None:
    result = ensure_cron_task_link(
        conn,
        job_id="job-xyz",
        job_metadata={
            "system_job": "csi_demo_triage_rank",
            "skip_task_hub_link": True,
        },
    )
    assert result is None
    # No row should have been created.
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM task_hub_items WHERE task_id = ?",
        ("cron:csi_demo_triage_rank",),
    ).fetchone()
    assert row["c"] == 0


def test_ensure_returns_none_when_no_derivable_task_id(conn: sqlite3.Connection) -> None:
    result = ensure_cron_task_link(
        conn,
        job_id="",
        job_metadata={"system_job": ""},
    )
    assert result is None


# ── ensure_cron_task_link: explicit task_id (email scheduler) ──────────────


def test_ensure_honors_explicit_task_id_verbatim(conn: sqlite3.Connection) -> None:
    """The email scheduler path: ``metadata.task_id`` is already set."""
    result = ensure_cron_task_link(
        conn,
        job_id="job-email-1",
        job_metadata={
            "source": "email_task_scheduler",
            "task_id": "email-task:abc123",
            "system_job": "",
        },
    )
    assert result == {"task_id": "email-task:abc123", "assignment_id": ""}
    # Should NOT create a `cron:<job-id>` row — the email scheduler
    # owns the lifecycle of its own task.
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM task_hub_items WHERE task_id LIKE 'cron:%'",
    ).fetchone()
    assert row["c"] == 0


# ── ensure_cron_task_link: auto-link happy path ────────────────────────────


def test_ensure_creates_stable_task_per_cron_name(conn: sqlite3.Connection) -> None:
    result = ensure_cron_task_link(
        conn,
        job_id="job-mb-1",
        job_metadata={"system_job": "morning_briefing"},
        description="Daily briefing",
    )
    assert result is not None
    assert result["task_id"] == "cron:morning_briefing"
    assert result["assignment_id"].startswith("asg_cron_")

    item = task_hub.get_item(conn, "cron:morning_briefing")
    assert item is not None
    assert item["source_kind"] == CRON_TASK_SOURCE_KIND
    assert item["status"] == task_hub.TASK_STATUS_IN_PROGRESS
    # `agent_ready = False` ensures dispatch sweep won't claim this row.
    assert item["agent_ready"] is False or item["agent_ready"] == 0


def test_ensure_dedupes_by_task_id_across_runs(conn: sqlite3.Connection) -> None:
    """Re-running the same cron name produces ONE task row, multiple assignments."""
    r1 = ensure_cron_task_link(
        conn, job_id="job-1", job_metadata={"system_job": "nightly_wiki"},
    )
    r2 = ensure_cron_task_link(
        conn, job_id="job-1", job_metadata={"system_job": "nightly_wiki"},
    )
    r3 = ensure_cron_task_link(
        conn, job_id="job-1", job_metadata={"system_job": "nightly_wiki"},
    )

    assert r1 is not None and r2 is not None and r3 is not None
    assert r1["task_id"] == r2["task_id"] == r3["task_id"] == "cron:nightly_wiki"
    # Distinct assignment_ids per run.
    assert r1["assignment_id"] != r2["assignment_id"] != r3["assignment_id"]

    # Exactly ONE task row.
    task_count = conn.execute(
        "SELECT COUNT(*) AS c FROM task_hub_items WHERE task_id = 'cron:nightly_wiki'",
    ).fetchone()["c"]
    assert task_count == 1

    # THREE assignment rows (one per ensure call).
    assignment_count = conn.execute(
        "SELECT COUNT(*) AS c FROM task_hub_assignments WHERE task_id = 'cron:nightly_wiki'",
    ).fetchone()["c"]
    assert assignment_count == 3

    # THREE run rows (one per ensure call, via _open_run).
    run_count = conn.execute(
        "SELECT COUNT(*) AS c FROM task_hub_runs WHERE task_id = 'cron:nightly_wiki'",
    ).fetchone()["c"]
    assert run_count == 3


def test_ensure_uses_job_id_when_system_job_missing(conn: sqlite3.Connection) -> None:
    """Ad-hoc crons without a system_job fall back to the job_id."""
    result = ensure_cron_task_link(
        conn,
        job_id="adhoc-xyz",
        job_metadata={},  # no system_job
    )
    assert result is not None
    assert result["task_id"] == "cron:adhoc-xyz"


def test_ensure_records_assignment_with_running_state_and_agent(
    conn: sqlite3.Connection,
) -> None:
    result = ensure_cron_task_link(
        conn,
        job_id="job-1",
        job_metadata={"system_job": "hackernews_snapshot"},
    )
    assert result is not None
    row = conn.execute(
        "SELECT state, agent_id FROM task_hub_assignments WHERE assignment_id = ?",
        (result["assignment_id"],),
    ).fetchone()
    assert row is not None
    assert row["state"] == "running"
    assert row["agent_id"] == "cron_scheduler"


# ── close_cron_task_link ───────────────────────────────────────────────────


def test_close_resets_in_progress_to_open(conn: sqlite3.Connection) -> None:
    ensure_cron_task_link(
        conn, job_id="job-1", job_metadata={"system_job": "morning_briefing"},
    )
    # Sanity: task is in_progress after ensure.
    assert (
        task_hub.get_item(conn, "cron:morning_briefing")["status"]
        == task_hub.TASK_STATUS_IN_PROGRESS
    )

    close_cron_task_link(conn, task_id="cron:morning_briefing", success=True)

    item = task_hub.get_item(conn, "cron:morning_briefing")
    assert item["status"] == task_hub.TASK_STATUS_OPEN


def test_close_resets_completed_to_open(conn: sqlite3.Connection) -> None:
    """The cron site wiring flips the task to ``completed`` before F.3;
    ``close_cron_task_link`` should then flip it back to ``open``."""
    ensure_cron_task_link(
        conn, job_id="job-1", job_metadata={"system_job": "morning_briefing"},
    )
    # Simulate the pre-F.3 flip to completed.
    conn.execute(
        "UPDATE task_hub_items SET status = ? WHERE task_id = ?",
        (task_hub.TASK_STATUS_COMPLETED, "cron:morning_briefing"),
    )
    conn.commit()

    close_cron_task_link(conn, task_id="cron:morning_briefing", success=True)

    assert (
        task_hub.get_item(conn, "cron:morning_briefing")["status"]
        == task_hub.TASK_STATUS_OPEN
    )


def test_close_leaves_needs_review_untouched(conn: sqlite3.Connection) -> None:
    """F.3 may have moved the task to ``needs_review`` (real protocol
    violation).  ``close_cron_task_link`` must NOT stomp that signal."""
    ensure_cron_task_link(
        conn, job_id="job-1", job_metadata={"system_job": "morning_briefing"},
    )
    # Simulate F.3 routing to needs_review.
    conn.execute(
        "UPDATE task_hub_items SET status = ? WHERE task_id = ?",
        (task_hub.TASK_STATUS_REVIEW, "cron:morning_briefing"),
    )
    conn.commit()

    close_cron_task_link(conn, task_id="cron:morning_briefing", success=True)

    assert (
        task_hub.get_item(conn, "cron:morning_briefing")["status"]
        == task_hub.TASK_STATUS_REVIEW
    )


def test_close_leaves_task_untouched_on_failure(conn: sqlite3.Connection) -> None:
    """``success=False`` => leave the task at whatever state it's in."""
    ensure_cron_task_link(
        conn, job_id="job-1", job_metadata={"system_job": "morning_briefing"},
    )
    close_cron_task_link(conn, task_id="cron:morning_briefing", success=False)
    # Still in_progress (ensure set it that way).
    assert (
        task_hub.get_item(conn, "cron:morning_briefing")["status"]
        == task_hub.TASK_STATUS_IN_PROGRESS
    )


def test_close_skips_non_cron_owned_tasks(conn: sqlite3.Connection) -> None:
    """Guard: only reset rows with source_kind='cron_run'.  An accidentally
    matching task_id from another source must NOT be reset."""
    task_hub.upsert_item(
        conn,
        {
            "task_id": "not-a-cron",
            "source_kind": "internal",
            "title": "manual task",
            "status": task_hub.TASK_STATUS_IN_PROGRESS,
        },
    )
    conn.commit()
    close_cron_task_link(conn, task_id="not-a-cron", success=True)
    # Unchanged.
    assert (
        task_hub.get_item(conn, "not-a-cron")["status"]
        == task_hub.TASK_STATUS_IN_PROGRESS
    )


def test_close_swallows_unknown_task(conn: sqlite3.Connection) -> None:
    """Best-effort: calling close on a non-existent task is a no-op."""
    close_cron_task_link(conn, task_id="cron:does-not-exist", success=True)
    # No exception, no rows.


def test_close_empty_task_id_is_noop(conn: sqlite3.Connection) -> None:
    close_cron_task_link(conn, task_id="", success=True)
    close_cron_task_link(conn, task_id="   ", success=True)


# ── Per-run granularity: each ensure creates a fresh assignment + run ──────


def test_each_run_gets_distinct_assignment_and_run_id(
    conn: sqlite3.Connection,
) -> None:
    """The whole point of the auto-link is per-run observability —
    every cron tick MUST land in its own assignment row + run row."""
    r1 = ensure_cron_task_link(
        conn, job_id="job-1", job_metadata={"system_job": "morning_briefing"},
    )
    r2 = ensure_cron_task_link(
        conn, job_id="job-1", job_metadata={"system_job": "morning_briefing"},
    )
    assert r1["assignment_id"] != r2["assignment_id"]

    # Both runs should exist and reference distinct assignments.
    runs = conn.execute(
        "SELECT assignment_id FROM task_hub_runs WHERE task_id = 'cron:morning_briefing' "
        "ORDER BY started_at",
    ).fetchall()
    assert len(runs) == 2
    assert runs[0]["assignment_id"] != runs[1]["assignment_id"]


# ── F-wiring contract: auto-linked task is queryable by find_active_assignment_for_task ──


def test_ensure_creates_assignment_findable_by_phase_f_helper(
    conn: sqlite3.Connection,
) -> None:
    """The F.1 PID-stamping path uses ``find_active_assignment_for_task``
    as a fallback.  Even though the auto-link returns the assignment_id
    directly, this verifies the row is queryable through the canonical
    classifier helper too (defense in depth)."""
    from universal_agent.services.worker_exit_classifier import (
        find_active_assignment_for_task,
    )

    result = ensure_cron_task_link(
        conn, job_id="job-1", job_metadata={"system_job": "morning_briefing"},
    )
    found = find_active_assignment_for_task(conn, task_id="cron:morning_briefing")
    assert found == result["assignment_id"]


# ── Ship 4 — housekeeping crons flipped opt-IN to the protocol ─────────────
#
# These four cron sources were opt-OUT (``skip_task_hub_link=True``)
# in PR #240 because they're dispatcher/GC crons whose work-products
# already carry their own task_hub linkage.  Ship 4 flips them opt-IN:
# meta-observability ("is the dispatcher itself running cleanly?")
# is worth more than the cost of one row per cron source.  These tests
# pin that the registration source no longer carries the opt-out flag.


def _gateway_source_excerpt(symbol: str, *, lines_after: int = 40) -> str:
    """Read the gateway_server.py source around ``def {symbol}(...)``.

    We grep the registration function body rather than calling it,
    because the function depends on a live ``_cron_service`` global
    and a SQLite-backed cron store that aren't available in unit-test
    fixtures.  The protocol contract this test enforces lives at the
    call-site of ``_register_system_cron_job`` (or in the inline
    metadata dict for the cleanup cron) — both are textually
    inspectable.

    Slices from the ``def {symbol}(`` anchor to the next top-level
    ``def`` (or ``class``) so a downstream function inserted between
    this one and the next can't pollute the excerpt — caught when
    `_ensure_vp_mission_pr_reconciler_cron_job` was added below
    `_ensure_codie_proactive_cleanup_cron_job` and the 80-line
    window slurped the new function's docstring.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "universal_agent" / "gateway_server.py"
    text = src.read_text(encoding="utf-8")
    anchor = f"def {symbol}("
    idx = text.find(anchor)
    if idx == -1:
        raise AssertionError(f"could not locate def {symbol}() in gateway_server.py")
    tail_lines = text[idx:].splitlines()
    out_lines: list[str] = []
    for i, line in enumerate(tail_lines):
        if i > 0 and (line.startswith("def ") or line.startswith("class ")):
            break
        out_lines.append(line)
        if i + 1 >= max(lines_after, 80):
            break
    return "\n".join(out_lines)


def test_codie_proactive_cleanup_is_observed() -> None:
    """codie_proactive_cleanup uses an inline metadata dict (not
    ``_register_system_cron_job``).  Ship 4 removed the
    ``skip_task_hub_link`` key from that dict."""
    excerpt = _gateway_source_excerpt(
        "_ensure_codie_proactive_cleanup_cron_job", lines_after=80,
    )
    # The key should NOT appear at all in the body — neither as
    # ``"skip_task_hub_link": True`` nor as a kwarg.
    assert '"skip_task_hub_link"' not in excerpt, (
        "codie_proactive_cleanup must no longer set "
        "metadata['skip_task_hub_link'] post-Ship-4"
    )
    assert "skip_task_hub_link=True" not in excerpt


def test_vp_coder_workspace_pruning_is_observed() -> None:
    """vp_coder_workspace_pruning uses ``_register_system_cron_job``;
    Ship 4 removed the ``skip_task_hub_link=True`` kwarg."""
    excerpt = _gateway_source_excerpt(
        "_ensure_vp_coder_workspace_pruning_cron_job", lines_after=40,
    )
    assert "skip_task_hub_link=True" not in excerpt, (
        "vp_coder_workspace_pruning must no longer pass "
        "skip_task_hub_link=True post-Ship-4"
    )


def test_atlas_direct_dispatch_is_observed() -> None:
    """atlas_direct_dispatch uses ``_register_system_cron_job``;
    Ship 4 removed the ``skip_task_hub_link=True`` kwarg."""
    excerpt = _gateway_source_excerpt(
        "_ensure_atlas_direct_dispatch_cron_job", lines_after=40,
    )
    assert "skip_task_hub_link=True" not in excerpt, (
        "atlas_direct_dispatch must no longer pass "
        "skip_task_hub_link=True post-Ship-4"
    )


def test_csi_demo_triage_rank_is_observed() -> None:
    """csi_demo_triage_rank uses ``_register_system_cron_job``;
    Ship 4 removed the ``skip_task_hub_link=True`` kwarg."""
    excerpt = _gateway_source_excerpt(
        "_ensure_csi_demo_triage_rank_cron_job", lines_after=40,
    )
    assert "skip_task_hub_link=True" not in excerpt, (
        "csi_demo_triage_rank must no longer pass "
        "skip_task_hub_link=True post-Ship-4"
    )
