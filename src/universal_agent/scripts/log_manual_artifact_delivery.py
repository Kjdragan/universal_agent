#!/usr/bin/env python3
"""Manual scheduled-artifact recovery delivery logger (recovery call site).

Invoke SELECTIVELY — only on the manual-recovery code path, AFTER a missed
scheduled artifact (e.g. ``paper_to_podcast_daily``) has been re-delivered to
Kevin via an AgentMail send from a non-cron run context. Records the delivery
into ``proactive_artifact_emails`` with the ``[<job_id>]`` subject tag the
proactive-health watchdog matches, so the false "no email in 30h" critical
clears. Must NOT be auto-fired on every AgentMail send.

Canonical form (run from the repo root, or anywhere with the venv active):

    uv run python -m universal_agent.scripts.log_manual_artifact_delivery \\
        --job-id 2afe05ab96 \\
        --message-id <agentmail-message-id> \\
        --subject "Top 5 Papers + Podcast (manual recovery)" \\
        --recipient kevinjdragan@gmail.com \\
        --artifact-path /home/kjdragan/.../podcast.mp3 \\
        --delivered-by simone

Exit 0 on a recorded (or already-present) row; non-zero only on argument or DB
errors. Best-effort: a missing optional field never fails the run.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
from universal_agent.services.cron_artifact_notifier import log_manual_artifact_delivery


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Log a manually-recovered scheduled-artifact delivery so the "
            "proactive-health watchdog clears. Invoke only on the manual "
            "recovery path, after the AgentMail re-delivery send succeeds."
        )
    )
    parser.add_argument(
        "--job-id",
        required=True,
        help="The missed scheduled cron job id (e.g. 2afe05ab96 for paper_to_podcast).",
    )
    parser.add_argument(
        "--message-id",
        required=True,
        help="The AgentMail message_id returned by the re-delivery send.",
    )
    parser.add_argument(
        "--subject",
        default="",
        help="The email subject of the re-delivery (tagged with [<job_id>] if missing).",
    )
    parser.add_argument(
        "--recipient",
        default="",
        help="Recipient address (the watchdog probes kevinjdragan@gmail.com).",
    )
    parser.add_argument(
        "--artifact-path",
        default="",
        help="Path to the delivered artifact (stored as provenance, optional).",
    )
    parser.add_argument(
        "--delivered-by",
        default="",
        help="Who/what performed the recovery (e.g. simone, a VP id). Stored as provenance.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    db_path = get_activity_db_path()
    conn = connect_runtime_db(db_path)
    try:
        result = log_manual_artifact_delivery(
            conn,
            job_id=args.job_id,
            message_id=args.message_id,
            subject=args.subject,
            recipient=args.recipient,
            artifact_path=args.artifact_path,
            delivered_by=args.delivered_by,
        )
    finally:
        conn.close()

    if result is None:
        # Either missing job_id/message_id (argparse makes these required, so
        # unlikely) or a dedup hit / best-effort skip — treat as success: the
        # row the watchdog needs is present either way.
        print(
            f"no-op for job={args.job_id} message_id={args.message_id} "
            "(missing key args, or a row for this message_id already exists)"
        )
        return 0

    # Echo the watchdog query so the operator can confirm the critical cleared.
    conn = connect_runtime_db(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(sent_at) AS last_sent, COUNT(*) AS total "
            "FROM proactive_artifact_emails "
            "WHERE recipient = ? AND subject LIKE ?",
            (args.recipient, f"[{args.job_id}]%"),
        ).fetchone()
    finally:
        conn.close()
    total = int(row["total"] or 0) if row else 0
    last_sent = str(row["last_sent"] or "") if row else ""
    print(
        f"recorded manual recovery for job={args.job_id} "
        f"message_id={args.message_id} -> artifact_id={result.get('artifact_id')}"
    )
    print(
        f"watchdog[{args.job_id}] now sees total={total} delivery rows, "
        f"last_sent={last_sent or '(none)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
