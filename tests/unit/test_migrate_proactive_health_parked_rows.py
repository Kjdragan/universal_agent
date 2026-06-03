"""Unit test for scripts/migrate_proactive_health_parked_rows.py.

Verifies the one-time migration that closes legacy parked/needs_review
``proactive_health`` Task Hub rows:

- both proactive_health rows (one still-firing, one recovered) end up
  ``status='completed'``;
- a non-proactive_health ``needs_review`` row is left untouched;
- the close ``reason`` recorded on each row contains NO email-ish word (so the
  delivery-verification gate doesn't re-park them);
- a second run is a no-op (idempotent).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

from universal_agent import task_hub

# Load the migration module by path — scripts/ is not an importable package.
REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = REPO_ROOT / "scripts" / "migrate_proactive_health_parked_rows.py"
_SRC_ROOT = REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

_spec = importlib.util.spec_from_file_location(
    "migrate_proactive_health_parked_rows", _MIGRATION_PATH
)
assert _spec and _spec.loader
migrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate)


# finding_ids look like "invariant:<id>" (see pipeline_invariants /
# HeartbeatFinding.finding_id).
STILL_FIRING_FINDING = "invariant:csi_pipeline_stalled"
RECOVERED_FINDING = "invariant:youtube_transcript_lag"

# Tokens the completion gate treats as email-ish. The recorded close reason
# must contain NONE of these or the row would be re-parked in needs_review.
EMAIL_ISH_TOKENS = (
    "email",
    "emailed",
    "e-mail",
    "mail",
    "agentmail",
    "gmail",
    "sent",
    "deliver",
)


def _seed_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        task_hub.ensure_schema(conn)
        # proactive_health row #1 — still firing → needs_review
        task_hub.upsert_item(
            conn,
            {
                "task_id": "ph-still-firing",
                "source_kind": "proactive_health",
                "title": "CSI pipeline stalled",
                "status": task_hub.TASK_STATUS_REVIEW,
                "metadata": {"finding_id": STILL_FIRING_FINDING},
            },
        )
        # proactive_health row #2 — recovered → parked
        task_hub.upsert_item(
            conn,
            {
                "task_id": "ph-recovered",
                "source_kind": "proactive_health",
                "title": "YouTube transcript lag",
                "status": task_hub.TASK_STATUS_PARKED,
                "metadata": {"finding_id": RECOVERED_FINDING},
            },
        )
        # control row — NOT proactive_health, in needs_review; must be untouched
        task_hub.upsert_item(
            conn,
            {
                "task_id": "other-needs-review",
                "source_kind": "email",
                "title": "Operator follow-up",
                "status": task_hub.TASK_STATUS_REVIEW,
                "metadata": {"finding_id": "should-not-matter"},
            },
        )
        conn.commit()
    finally:
        conn.close()


def _status_of(db_path: str, task_id: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM task_hub_items WHERE task_id = ?", (task_id,)
        ).fetchone()
        assert row is not None, f"task {task_id} missing"
        return str(row[0])
    finally:
        conn.close()


def _evaluation_reasons(db_path: str, task_id: str) -> list[str]:
    """Collect every reason recorded against a task in task_hub_evaluations."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT reason FROM task_hub_evaluations WHERE task_id = ?", (task_id,)
        ).fetchall()
        return [str(r[0] or "") for r in rows]
    finally:
        conn.close()


def _assert_no_email_ish(text: str) -> None:
    lowered = text.lower()
    for tok in EMAIL_ISH_TOKENS:
        assert tok not in lowered, f"email-ish token {tok!r} leaked into reason: {text!r}"


def test_migration_closes_proactive_health_rows(tmp_path, monkeypatch) -> None:
    db_path = str(tmp_path / "activity.db")
    _seed_db(db_path)

    # Report the still-firing finding as currently active; the recovered one is
    # absent → classified as recovered.
    def _fake_payload(*, activity_conn, **_kwargs):  # noqa: ANN001
        return {"invariants": [{"finding_id": STILL_FIRING_FINDING}]}

    monkeypatch.setattr(migrate, "build_proactive_health_payload", _fake_payload)

    summary = migrate.run_migration(db_path=db_path, dry_run=False)

    # Both proactive_health rows scanned + completed; control row excluded.
    assert summary["scanned"] == 2
    assert summary["completed"] == 2
    assert summary["recovered"] == 1
    assert summary["still_firing"] == 1
    assert summary["skipped"] == 0

    # Both proactive_health rows are now terminal-completed.
    assert _status_of(db_path, "ph-still-firing") == task_hub.TASK_STATUS_COMPLETED
    assert _status_of(db_path, "ph-recovered") == task_hub.TASK_STATUS_COMPLETED

    # Control row is untouched.
    assert _status_of(db_path, "other-needs-review") == task_hub.TASK_STATUS_REVIEW

    # The recorded close reason on each contains NO email-ish word — otherwise
    # the delivery gate would have re-parked the row instead of completing it.
    for action in summary["actions"]:
        _assert_no_email_ish(action["reason"])
    for task_id in ("ph-still-firing", "ph-recovered"):
        for reason in _evaluation_reasons(db_path, task_id):
            _assert_no_email_ish(reason)

    # Idempotency: a second run finds nothing left to close.
    summary2 = migrate.run_migration(db_path=db_path, dry_run=False)
    assert summary2["scanned"] == 0
    assert summary2["completed"] == 0
    assert summary2["recovered"] == 0
    assert summary2["still_firing"] == 0

    # Statuses unchanged after the no-op second run.
    assert _status_of(db_path, "ph-still-firing") == task_hub.TASK_STATUS_COMPLETED
    assert _status_of(db_path, "ph-recovered") == task_hub.TASK_STATUS_COMPLETED
    assert _status_of(db_path, "other-needs-review") == task_hub.TASK_STATUS_REVIEW


def test_dry_run_does_not_mutate(tmp_path, monkeypatch) -> None:
    db_path = str(tmp_path / "activity.db")
    _seed_db(db_path)

    monkeypatch.setattr(
        migrate,
        "build_proactive_health_payload",
        lambda *, activity_conn, **_k: {"invariants": []},
    )

    summary = migrate.run_migration(db_path=db_path, dry_run=True)

    assert summary["scanned"] == 2
    assert summary["completed"] == 0
    # Both classified recovered (empty firing set), but nothing mutated.
    assert summary["recovered"] == 2
    assert all(a["applied"] is False for a in summary["actions"])

    # Rows still in their original non-terminal states.
    assert _status_of(db_path, "ph-still-firing") == task_hub.TASK_STATUS_REVIEW
    assert _status_of(db_path, "ph-recovered") == task_hub.TASK_STATUS_PARKED

    # Reasons that WOULD be applied are still email-ish-free.
    for action in summary["actions"]:
        _assert_no_email_ish(action["reason"])


def test_main_prints_json_summary(tmp_path, monkeypatch, capsys) -> None:
    db_path = str(tmp_path / "activity.db")
    _seed_db(db_path)

    monkeypatch.setattr(
        migrate,
        "build_proactive_health_payload",
        lambda *, activity_conn, **_k: {"invariants": [{"finding_id": STILL_FIRING_FINDING}]},
    )

    rc = migrate.main(["--db-path", db_path])
    assert rc == 0

    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is False
    assert out["scanned"] == 2
    assert out["completed"] == 2
