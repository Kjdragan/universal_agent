"""Tests for ``log_manual_artifact_delivery`` — the manual-recovery delivery logger.

These pin the contract that a *manually-recovered* scheduled-artifact delivery
(re-delivered by a principal/agent from a non-cron run context) writes a
``[<job_id>]``-tagged row into ``proactive_artifact_emails`` that the
``paper_to_podcast_email_delivery`` proactive-health watchdog counts — clearing
the false "no email in 30h" critical — while staying idempotent on
``message_id`` and per-job isolated, and without changing the existing
notifier / cron-self-send path behavior.

Companion to ``test_cron_self_send_delivery_tracking.py``, which pins the
cron-self-send half of the same shared ``_record_job_delivery`` core.
"""

from datetime import datetime, timedelta, timezone
import sqlite3

from universal_agent.services import proactive_artifacts
from universal_agent.services.cron_artifact_notifier import (
    log_manual_artifact_delivery,
    record_cron_run_delivery_email,
)
from universal_agent.services.invariants.proactive_pipeline_invariants import (
    PAPER_TO_PODCAST_JOB_ID,
    PAPER_TO_PODCAST_MAX_AGE_HOURS,
)

JOB_ID = PAPER_TO_PODCAST_JOB_ID  # "2afe05ab96"
OTHER_JOB_ID = "deadbeef00"  # a different scheduled job, for isolation
RECIPIENT = "kevinjdragan@gmail.com"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    proactive_artifacts.ensure_schema(conn)
    return conn


def _watchdog(
    conn: sqlite3.Connection, job_id: str, recipient: str = RECIPIENT
) -> tuple[int, str]:
    """The EXACT query shape ``paper_to_podcast_email_delivery`` runs."""
    row = conn.execute(
        "SELECT MAX(sent_at) AS last_sent, COUNT(*) AS total "
        "FROM proactive_artifact_emails "
        "WHERE recipient = ? AND subject LIKE ?",
        (recipient, f"[{job_id}]%"),
    ).fetchone()
    return int(row["total"] or 0), str(row["last_sent"] or "")


def _age_hours(sent_at: str) -> float:
    if not sent_at:
        return float("inf")
    return (
        datetime.now(timezone.utc) - datetime.fromisoformat(sent_at)
    ).total_seconds() / 3600.0


def _insert_old_tagged_row(
    conn: sqlite3.Connection, *, job_id: str, hours_ago: float, message_id: str
) -> None:
    """Seed a stale notifier-style delivery row (the row that made the watchdog critical)."""
    sent_at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    artifact = proactive_artifacts.upsert_artifact(
        conn,
        artifact_type="cron_run_output",
        source_kind="cron_artifact",
        source_ref=f"{job_id}:stale",
        title="stale notifier disclosure",
        status=proactive_artifacts.ARTIFACT_STATUS_SURFACED,
        delivery_state=proactive_artifacts.DELIVERY_EMAILED,
        metadata={"job_id": job_id},
    )
    proactive_artifacts.record_email_delivery(
        conn,
        artifact_id=artifact["artifact_id"],
        message_id=message_id,
        subject=f"[{job_id}] stale notifier disclosure",
        recipient=RECIPIENT,
    )
    # Force the seeded row to the stale timestamp (record_email_delivery stamps now).
    conn.execute(
        "UPDATE proactive_artifact_emails SET sent_at = ? WHERE message_id = ?",
        (sent_at, message_id),
    )
    conn.commit()


def test_manual_delivery_clears_critical_within_window():
    """Case (i): a manual recovery lands a fresh tagged row → watchdog goes current.

    Mirrors the verified Jul 9 instance: a stale Jul 8 row had the watchdog
    critical, then the podcast was manually re-delivered at 21:04 UTC.
    """
    conn = _connect()
    _insert_old_tagged_row(
        conn, job_id=JOB_ID, hours_ago=45.0, message_id="ses-jul8-stale"
    )

    total_before, last_before = _watchdog(conn, JOB_ID)
    assert _age_hours(last_before) > PAPER_TO_PODCAST_MAX_AGE_HOURS  # critical: >30h

    result = log_manual_artifact_delivery(
        conn,
        job_id=JOB_ID,
        message_id="ses-jul9-manual",
        subject="Top 5 Papers + Podcast + Quiz (manual recovery)",
        recipient=RECIPIENT,
        artifact_path="/home/kjdragan/.../podcast.m4a",
        delivered_by="simone",
    )
    assert result is not None, "manual delivery must record a row"

    total_after, last_after = _watchdog(conn, JOB_ID)
    assert total_after == 2  # the stale row + the fresh manual row
    assert (
        _age_hours(last_after) < PAPER_TO_PODCAST_MAX_AGE_HOURS
    )  # cleared: within 30h
    # The fresh row is the manual one (newest).
    assert _age_hours(last_after) < 1.0

    # Provenance markers landed in metadata.
    meta = conn.execute(
        "SELECT metadata_json FROM proactive_artifact_emails WHERE message_id = 'ses-jul9-manual'"
    ).fetchone()
    import json

    parsed = json.loads(meta["metadata_json"])
    assert parsed["kind"] == "manual_recovery"
    assert parsed["delivered_by"] == "simone"
    assert parsed["artifact_path"].endswith("podcast.m4a")
    conn.close()


def test_per_job_isolation():
    """Case (ii): a manual delivery for job X clears X's critical, not job Y's."""
    conn = _connect()
    log_manual_artifact_delivery(
        conn,
        job_id=JOB_ID,
        message_id="ses-isolate-x",
        subject="podcast for job X",
        recipient=RECIPIENT,
        delivered_by="simone",
    )

    total_x, last_x = _watchdog(conn, JOB_ID)
    total_y, last_y = _watchdog(conn, OTHER_JOB_ID)

    assert total_x == 1 and _age_hours(last_x) < PAPER_TO_PODCAST_MAX_AGE_HOURS
    # Job Y has no delivery of any kind — the row for X must not bleed into Y.
    assert total_y == 0 and last_y == ""
    conn.close()


def test_idempotent_on_message_id():
    """Case (iii): retrying a manual delivery with the same message_id does not double-count."""
    conn = _connect()
    log_manual_artifact_delivery(
        conn,
        job_id=JOB_ID,
        message_id="ses-dedup-manual",
        subject="podcast (attempt 1)",
        recipient=RECIPIENT,
        delivered_by="simone",
    )
    again = log_manual_artifact_delivery(
        conn,
        job_id=JOB_ID,
        message_id="ses-dedup-manual",  # same message_id — retry
        subject="podcast (attempt 2)",
        recipient=RECIPIENT,
        delivered_by="simone",
    )
    assert again is None, "a duplicate message_id must short-circuit (dedup)"

    total, _ = _watchdog(conn, JOB_ID)
    assert total == 1, "duplicate manual delivery must not double-count"
    n_for_id = conn.execute(
        "SELECT COUNT(*) AS c FROM proactive_artifact_emails WHERE message_id = 'ses-dedup-manual'"
    ).fetchone()["c"]
    assert n_for_id == 1
    conn.close()


def test_pipeline_and_cron_paths_still_clear_critical():
    """Case (iv) regression: the refactor did not change notifier / cron-self-send behavior.

    Both the notifier-style tagged row and the cron self-send layer must still
    land a ``[<job_id>]``-tagged row the watchdog counts (shared core, kind
    agnostic).
    """
    conn = _connect()

    # Notifier-style row (the original pipeline write path): tagged subject,
    # written via record_email_delivery.
    art = proactive_artifacts.upsert_artifact(
        conn,
        artifact_type="cron_run_output",
        source_kind="cron_artifact",
        source_ref=f"{JOB_ID}:notifier",
        title="notifier disclosure",
        status=proactive_artifacts.ARTIFACT_STATUS_SURFACED,
        delivery_state=proactive_artifacts.DELIVERY_EMAILED,
        metadata={"job_id": JOB_ID},
    )
    proactive_artifacts.record_email_delivery(
        conn,
        artifact_id=art["artifact_id"],
        message_id="ses-notifier-001",
        subject=f"[{JOB_ID}] notifier disclosure",
        recipient=RECIPIENT,
    )
    total_n, last_n = _watchdog(conn, JOB_ID)
    assert total_n == 1 and _age_hours(last_n) < PAPER_TO_PODCAST_MAX_AGE_HOURS

    # Cron self-send layer (the cron run_kind path) — now a thin wrapper over
    # the same shared core; must still tag + record identically.
    record_cron_run_delivery_email(
        conn,
        job_id=JOB_ID,
        message_id="ses-cron-selfsend-001",
        subject="podcast (cron self-send)",
        recipient=RECIPIENT,
        source="agentmail_send_with_local_attachments",
    )
    total_c, last_c = _watchdog(conn, JOB_ID)
    assert total_c == 2 and _age_hours(last_c) < PAPER_TO_PODCAST_MAX_AGE_HOURS

    # The cron self-send row keeps its kind marker (regression of the refactor).
    cron_meta = conn.execute(
        "SELECT metadata_json FROM proactive_artifact_emails WHERE message_id = 'ses-cron-selfsend-001'"
    ).fetchone()
    import json

    assert json.loads(cron_meta["metadata_json"])["kind"] == "cron_self_send"
    conn.close()
