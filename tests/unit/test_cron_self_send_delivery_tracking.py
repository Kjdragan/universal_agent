"""Regression: cron self-send email delivery must be tracked.

Reproduces the ``paper_to_podcast_daily`` resume/self-send defect: the agent
delivers the podcast directly via an AgentMail tool (the local attachment
tool for the mp3, or ``mcp__AgentMail__send_message``), bypassing
``cron_artifact_notifier``. Without the delivery-tracking layer no row landed
in ``proactive_artifact_emails`` and the subject lacked the ``[<job_id>]``
tag the ``paper_to_podcast_email_delivery`` proactive-health watchdog keys on
(``recipient='kevinjdragan@gmail.com' AND subject LIKE '[<job_id>]%'``),
producing a recurring false 'no email in 30h' *critical* even though Kevin
actually received the podcast (verified Amazon SES ``message_id``).

These tests pin the shared layer
``cron_artifact_notifier.record_cron_run_delivery_email`` that the
self-send observation sites (``local_toolkit_bridge`` + ``hooks``) call.
"""

from __future__ import annotations

import json
import sqlite3

from universal_agent.services import proactive_artifacts
from universal_agent.services.cron_artifact_notifier import (
    record_cron_run_delivery_email,
)

# The real paper_to_podcast cron job id (proactive_pipeline_invariants.PAPER_TO_PODCAST_JOB_ID)
# and the recipient the watchdog probes.
JOB_ID = "2afe05ab96"
RECIPIENT = "kevinjdragan@gmail.com"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    proactive_artifacts.ensure_schema(conn)
    return conn


def _watchdog_last_sent(conn: sqlite3.Connection) -> tuple[int, str]:
    """The exact query the proactive-health watchdog runs."""
    row = conn.execute(
        "SELECT MAX(sent_at) AS last_sent, COUNT(*) AS total "
        "FROM proactive_artifact_emails "
        "WHERE recipient = ? AND subject LIKE ?",
        (RECIPIENT, f"[{JOB_ID}]%"),
    ).fetchone()
    return int(row["total"] or 0), str(row["last_sent"] or "")


def test_self_send_lands_tagged_row_for_watchdog():
    """Core regression: an untagged self-send records a [<job_id>]-tagged row."""
    conn = _connect()
    untagged = "Causal inference & causal ML: Top 5 Papers + Podcast + Quiz (resumed run)"

    record_cron_run_delivery_email(
        conn,
        job_id=JOB_ID,
        message_id="ses-abc-001",
        subject=untagged,
        recipient=RECIPIENT,
        source="agentmail_send_with_local_attachments",
    )

    total, last_sent = _watchdog_last_sent(conn)
    assert total >= 1, "watchdog query found no tagged delivery row"
    assert last_sent, "sent_at must be populated"

    row = conn.execute(
        "SELECT subject, recipient, message_id, metadata_json FROM proactive_artifact_emails "
        "WHERE message_id = 'ses-abc-001'"
    ).fetchone()
    assert row["subject"].startswith(f"[{JOB_ID}]"), row["subject"]
    assert row["recipient"] == RECIPIENT
    # The original (untagged) subject is preserved in metadata, nothing lost.
    assert json.loads(row["metadata_json"])["original_subject"] == untagged
    conn.close()


def test_idempotent_on_message_id():
    """Repeated calls with the same message_id never duplicate the row."""
    conn = _connect()
    for _ in range(3):
        record_cron_run_delivery_email(
            conn,
            job_id=JOB_ID,
            message_id="ses-dup-001",
            subject="today's podcast",
            recipient=RECIPIENT,
        )
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM proactive_artifact_emails WHERE message_id = 'ses-dup-001'"
    ).fetchone()["c"]
    assert count == 1
    conn.close()


def test_dedup_against_notifier_message_id():
    """If the notifier already recorded the same message_id, the self-send layer skips."""
    conn = _connect()
    notifier_artifact = proactive_artifacts.upsert_artifact(
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
        artifact_id=notifier_artifact["artifact_id"],
        message_id="ses-shared-001",
        subject=f"[{JOB_ID}] notifier disclosure",
        recipient=RECIPIENT,
    )
    before = conn.execute("SELECT COUNT(*) AS c FROM proactive_artifact_emails").fetchone()["c"]

    record_cron_run_delivery_email(
        conn,
        job_id=JOB_ID,
        message_id="ses-shared-001",  # same message_id the notifier recorded
        subject="self send body",
        recipient=RECIPIENT,
    )
    after = conn.execute("SELECT COUNT(*) AS c FROM proactive_artifact_emails").fetchone()["c"]
    assert before == after, "self-send layer must not duplicate a notifier-recorded delivery"
    conn.close()


def test_coalesces_onto_existing_cron_artifact():
    """A self-send attaches to an existing same-cron open artifact, minting no new row."""
    conn = _connect()
    existing = proactive_artifacts.upsert_artifact(
        conn,
        artifact_type="cron_run_output",
        source_kind="cron_artifact",
        source_ref=f"{JOB_ID}:prior",
        title="prior run artifact",
        status=proactive_artifacts.ARTIFACT_STATUS_PRODUCED,
        delivery_state=proactive_artifacts.DELIVERY_NOT_SURFACED,
        metadata={"job_id": JOB_ID, "task_id": f"cron:{JOB_ID}"},
    )
    existing_id = existing["artifact_id"]

    record_cron_run_delivery_email(
        conn,
        job_id=JOB_ID,
        message_id="ses-coalesce-001",
        subject="podcast",
        recipient=RECIPIENT,
    )

    n_artifacts = conn.execute(
        "SELECT COUNT(*) AS c FROM proactive_artifacts WHERE source_kind = 'cron_artifact'"
    ).fetchone()["c"]
    assert n_artifacts == 1, "self-send should coalesce, not mint a second artifact"
    email = conn.execute(
        "SELECT artifact_id FROM proactive_artifact_emails WHERE message_id = 'ses-coalesce-001'"
    ).fetchone()
    assert email["artifact_id"] == existing_id
    conn.close()


def test_requires_job_id_and_message_id():
    """No job_id or no message_id -> no-op (guards against spurious rows)."""
    conn = _connect()
    record_cron_run_delivery_email(conn, job_id="", message_id="x", subject="s", recipient=RECIPIENT)
    record_cron_run_delivery_email(conn, job_id=JOB_ID, message_id="", subject="s", recipient=RECIPIENT)
    assert conn.execute("SELECT COUNT(*) AS c FROM proactive_artifact_emails").fetchone()["c"] == 0
    conn.close()


def test_never_raises_on_bad_db():
    """The layer is best-effort: a broken conn must not propagate into the send path."""
    # A closed connection makes every query raise (ProgrammingError); the
    # helper's ``ensure_schema`` can't paper over this, so the try/except must
    # swallow it and return None rather than disrupting the email send path.
    conn = sqlite3.connect(":memory:")
    conn.close()
    result = record_cron_run_delivery_email(
        conn,
        job_id=JOB_ID,
        message_id="ses-bad-001",
        subject="podcast",
        recipient=RECIPIENT,
    )
    assert result is None
