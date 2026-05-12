"""Hermes Ship 4 — LLM cron path observability tests.

Covers the LLM (non-`!script`) cron execution branch in
``cron_service.py``.  The branch runs an in-process LLM coroutine via
``self.gateway.run_query(...)`` wrapped in ``asyncio.wait_for(...)`` —
it is the second of the two execution paths in
``CronService._run_job``.

The wiring follows the Task Hub Observability Protocol:

1. **Identity** — ``ensure_cron_task_link`` produces a stable
   ``cron:<system_job>`` ``task_hub_items`` row (unless
   ``metadata.skip_task_hub_link`` is True).
2. **Claim ledger** — same call appends a fresh
   ``task_hub_assignments`` row per tick.
3. **Run history** — same call appends a fresh ``task_hub_runs`` row
   (per-run granularity for observability).
4. **Subprocess identity** — N/A for in-process LLM crons;
   ``worker_pid`` stays NULL.
5. **Protocol violation routing** — ``classify_worker_exit`` decides
   the outcome bucket from ``(rc_equiv, was_timeout_killed,
   task_closed_normally)``; F.3 routes via
   ``park_task_for_protocol_violation`` with ``site="cron"``.
6. **Standard recovery verbs** — out of scope here; covered by the
   Simone-callable verb tests.

The cron service's _run_job orchestration is hard to mock end-to-end
(GatewayRequest, session lifecycle, _persist_run_output, etc.).
Following the established pattern in ``test_hermes_phase_f_site_wiring.py``
these tests exercise the classifier behavior at the boundary the LLM
branch feeds it, plus the surrounding helper composition: identity,
claim, history, violation routing.
"""

from __future__ import annotations

import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.services.cron_task_hub_link import (
    close_cron_task_link,
    ensure_cron_task_link,
)
from universal_agent.services.worker_exit_classifier import (
    PROTOCOL_VIOLATION_REASONS,
    classify_worker_exit,
    find_active_assignment_for_task,
    park_task_for_protocol_violation,
    task_was_closed_normally,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    task_hub.ensure_schema(c)
    yield c
    c.close()


# ── Pure classifier behavior for the LLM rc_equiv contract ─────────────────


def test_llm_cron_success_classifies_as_clean_exit_zero(
    conn: sqlite3.Connection,
) -> None:
    """Successful LLM coroutine + auto-linked task pre-closed -> clean_exit_zero.

    The LLM branch's ``finally`` pre-closes the auto-linked
    ``cron:<system_job>`` task to ``completed`` on the happy path
    BEFORE calling the classifier (see ``cron_service.py`` LLM-branch
    F-close).  So ``task_was_closed_normally`` returns True, and the
    classifier picks ``clean_exit_zero`` (not the no_disposition
    variant).  That is the "everything worked" terminal state.
    """
    linkage = ensure_cron_task_link(
        conn,
        job_id="job-llm-success",
        job_metadata={"system_job": "autonomous_daily_briefing"},
    )
    assert linkage is not None
    task_id = linkage["task_id"]

    # Simulate the LLM-branch pre-close (auto_linked + rc_equiv==0).
    conn.execute(
        "UPDATE task_hub_items SET status = ? WHERE task_id = ?",
        (task_hub.TASK_STATUS_COMPLETED, task_id),
    )
    conn.commit()

    closed = task_was_closed_normally(conn, task_id=task_id)
    classification = classify_worker_exit(
        return_code=0,
        was_signaled=False,
        was_timeout_killed=False,
        task_closed_normally=closed,
    )
    assert classification.outcome == "clean_exit_zero"
    assert classification.is_failure is False
    assert classification.is_protocol_violation is False


def test_llm_cron_timeout_records_timeout_killed(
    conn: sqlite3.Connection,
) -> None:
    """`asyncio.TimeoutError` in the LLM branch -> outcome 'timeout_killed'."""
    ensure_cron_task_link(
        conn,
        job_id="job-llm-timeout",
        job_metadata={"system_job": "paper_to_podcast_daily"},
    )
    classification = classify_worker_exit(
        return_code=1,  # rc_equiv synthesized by LLM branch on timeout
        was_signaled=False,
        was_timeout_killed=True,
        task_closed_normally=False,
    )
    assert classification.outcome == "timeout_killed"
    assert classification.is_failure is True
    # Timeout takes priority over protocol-violation classification.
    assert classification.is_protocol_violation is False


def test_llm_cron_exception_records_nonzero_exit(
    conn: sqlite3.Connection,
) -> None:
    """An exception other than TimeoutError -> outcome 'nonzero_exit'."""
    ensure_cron_task_link(
        conn,
        job_id="job-llm-raised",
        job_metadata={"system_job": "autonomous_daily_briefing"},
    )
    classification = classify_worker_exit(
        return_code=1,  # rc_equiv synthesized by LLM branch on exception
        was_signaled=False,
        was_timeout_killed=False,
        task_closed_normally=False,
    )
    assert classification.outcome == "nonzero_exit"
    assert classification.is_failure is True
    assert classification.is_protocol_violation is False


def test_llm_cron_clean_exit_no_disposition_triggers_protocol_violation(
    conn: sqlite3.Connection,
) -> None:
    """rc=0 but linked task still in_progress -> protocol violation -> park."""
    linkage = ensure_cron_task_link(
        conn,
        job_id="job-llm-violation",
        job_metadata={"system_job": "autonomous_daily_briefing"},
    )
    assert linkage is not None
    task_id = linkage["task_id"]

    # Hypothetical: the LLM cron returned without raising, but did NOT
    # transition the task off in_progress (this is the "agent forgot
    # to mark the work done" case).  Note: in the real LLM branch the
    # pre-close DOES fire for auto_linked + rc=0, so this path is
    # mostly defensive — but it must still classify correctly when an
    # operator-driven cron leaves the task open.
    assert (
        task_hub.get_item(conn, task_id)["status"]
        == task_hub.TASK_STATUS_IN_PROGRESS
    )

    closed = task_was_closed_normally(conn, task_id=task_id)
    classification = classify_worker_exit(
        return_code=0,
        was_signaled=False,
        was_timeout_killed=False,
        task_closed_normally=closed,
    )
    assert classification.outcome == "clean_exit_zero_no_disposition"
    assert classification.is_protocol_violation is True

    parked = park_task_for_protocol_violation(
        conn,
        task_id=task_id,
        site="cron",
        summary="cron LLM job-llm-violation clean exit no disposition",
        agent_id="cron_scheduler",
    )
    assert parked is True
    row = conn.execute(
        "SELECT status FROM task_hub_items WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    assert row["status"] == task_hub.TASK_STATUS_REVIEW


def test_llm_cron_opt_out_skips_wiring(conn: sqlite3.Connection) -> None:
    """metadata.skip_task_hub_link=True -> no row created, no F wiring."""
    result = ensure_cron_task_link(
        conn,
        job_id="job-llm-opt-out",
        job_metadata={
            "system_job": "autonomous_daily_briefing",
            "skip_task_hub_link": True,
        },
    )
    assert result is None
    rows = conn.execute(
        "SELECT COUNT(*) AS c FROM task_hub_items WHERE task_id = ?",
        ("cron:autonomous_daily_briefing",),
    ).fetchone()
    assert rows["c"] == 0


def test_llm_cron_uses_canonical_cron_site_reason(
    conn: sqlite3.Connection,
) -> None:
    """LLM-branch protocol violations must use PROTOCOL_VIOLATION_REASONS['cron'].

    Both !script and LLM crons feed ``site="cron"`` to the parking
    helper — the reason string is shared.  This pins the contract so a
    future refactor that introduces a site-specific reason (e.g.
    'protocol_violation_cron_llm_...') deliberately requires updating
    both sides.
    """
    assert "cron" in PROTOCOL_VIOLATION_REASONS
    reason = PROTOCOL_VIOLATION_REASONS["cron"]
    assert reason.startswith("protocol_violation_cron")


def test_llm_cron_close_link_resets_task_for_next_tick(
    conn: sqlite3.Connection,
) -> None:
    """After a clean LLM cron run, close_cron_task_link must flip the
    perpetual task back to ``open`` so the next tick can re-claim it.
    Mirrors the !script branch's lifecycle contract."""
    linkage = ensure_cron_task_link(
        conn,
        job_id="job-llm-cycle",
        job_metadata={"system_job": "autonomous_daily_briefing"},
    )
    assert linkage is not None
    task_id = linkage["task_id"]
    # LLM-branch finally simulates pre-close to completed:
    conn.execute(
        "UPDATE task_hub_items SET status = ? WHERE task_id = ?",
        (task_hub.TASK_STATUS_COMPLETED, task_id),
    )
    conn.commit()
    # Then close_cron_task_link flips back to open.
    close_cron_task_link(conn, task_id=task_id, success=True)
    item = task_hub.get_item(conn, task_id)
    assert item["status"] == task_hub.TASK_STATUS_OPEN


def test_llm_cron_assignment_findable_for_pid_lookup_path(
    conn: sqlite3.Connection,
) -> None:
    """LLM crons never call ``record_worker_pid`` (in-process; no PID),
    but the assignment row that ``ensure_cron_task_link`` opens is
    still discoverable via ``find_active_assignment_for_task``.  This
    pins the assignment row's discoverability so a future PR that
    extends the LLM path to record an interpreter PID can rely on the
    helper's fallback path.  Defense in depth."""
    linkage = ensure_cron_task_link(
        conn,
        job_id="job-llm-find",
        job_metadata={"system_job": "autonomous_daily_briefing"},
    )
    assert linkage is not None
    found = find_active_assignment_for_task(
        conn, task_id=linkage["task_id"],
    )
    assert found == linkage["assignment_id"]
