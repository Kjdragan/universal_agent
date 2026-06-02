"""Unit tests for the hourly intel digest helper module (PR D).

These tests target ``services/hourly_intel_digest.py`` — the Pythonic
substrate the ``hourly-intel-digest`` skill calls into. The skill
itself is an agent directive so it can't be exercised in pytest; we
test every non-agent decision (gate checks, candidate selection, render,
stamp, pause-token signing) directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3
from typing import Any
import unittest
from unittest.mock import patch

from universal_agent.services import (
    hourly_intel_digest as digest,
    proactive_artifacts as _pa,
)


def _mk_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    digest.ensure_schema_addons(conn)
    return conn


def _insert_ship_artifact(
    conn: sqlite3.Connection,
    *,
    artifact_id: str,
    title: str = "ATLAS insight brief: Test convergence",
    summary: str = "",
    thesis: str = "A convergence about X is emerging across 4 channels.",
    composite_score: float = 0.82,
    needs_attention: bool = False,
    key_actions: list[str] | None = None,
    key_entities: list[str] | None = None,
    feedback_up: str = "https://app.example.com/api/v1/briefs/x/feedback?v=up&t=abc",
    feedback_down: str = "https://app.example.com/api/v1/briefs/x/feedback?v=down&t=def",
    created_at: str | None = None,
) -> None:
    """Insert a `verdict='ship'` artifact suitable for digest pickup."""
    metadata: dict[str, Any] = {
        "thesis": thesis,
        "composite_score": composite_score,
        "needs_attention": needs_attention,
        "key_actions": key_actions or ["Watch channel A's next drop", "Compare against Z paper"],
        "key_entities": key_entities or ["Channel A", "Channel B", "Topic X"],
        "feedback_url_up": feedback_up,
        "feedback_url_down": feedback_down,
    }
    _pa.upsert_artifact(
        conn,
        artifact_id=artifact_id,
        artifact_type="intel_brief",
        source_kind="convergence_candidate",
        source_ref=artifact_id,
        title=title,
        summary=summary,
        status=_pa.ARTIFACT_STATUS_PRODUCED,
        delivery_state=_pa.DELIVERY_NOT_SURFACED,
        metadata=metadata,
    )
    # Mark the artifact as verdict='ship' (this column was added by
    # ensure_schema_addons). We also align created_at to current hour
    # by default so the SELECT picks it up.
    when = created_at or datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE proactive_artifacts SET verdict = 'ship', created_at = ? WHERE artifact_id = ?",
        (when, artifact_id),
    )
    conn.commit()


# ── Pause checks ───────────────────────────────────────────────────────


class PauseTests(unittest.TestCase):
    def test_no_pause_by_default(self) -> None:
        conn = _mk_conn()
        self.assertFalse(digest.is_paused(conn))

    def test_pause_future_blocks(self) -> None:
        conn = _mk_conn()
        future = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
        digest.set_pause(conn, future)
        self.assertTrue(digest.is_paused(conn))

    def test_pause_past_does_not_block(self) -> None:
        conn = _mk_conn()
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        digest.set_pause(conn, past)
        self.assertFalse(digest.is_paused(conn))

    def test_compose_send_payload_short_circuits_on_pause(self) -> None:
        conn = _mk_conn()
        future = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
        digest.set_pause(conn, future)
        _insert_ship_artifact(conn, artifact_id="pa_paused_001")
        payload = digest.compose_send_payload(conn)
        self.assertEqual(payload, {"status": "paused"})


# ── Throttle checks ────────────────────────────────────────────────────


class ThrottleTests(unittest.TestCase):
    def test_no_throttle_when_no_prior_delivery(self) -> None:
        conn = _mk_conn()
        self.assertFalse(digest.is_throttled(conn))

    def test_throttle_when_delivery_in_current_hour(self) -> None:
        conn = _mk_conn()
        _insert_ship_artifact(conn, artifact_id="pa_throttled_001")
        # Stamp delivered_at = now (within current hour bucket).
        digest.mark_all_delivered(conn, ["pa_throttled_001"])
        self.assertTrue(digest.is_throttled(conn))

    def test_no_throttle_when_delivery_was_in_prior_hour(self) -> None:
        conn = _mk_conn()
        _insert_ship_artifact(conn, artifact_id="pa_throttled_002")
        prior_hour = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        conn.execute(
            """
            UPDATE proactive_artifacts
            SET delivered_at = ?, delivery_state = 'emailed', delivery_channel = 'hourly_digest'
            WHERE artifact_id = ?
            """,
            (prior_hour, "pa_throttled_002"),
        )
        conn.commit()
        self.assertFalse(digest.is_throttled(conn))

    def test_throttle_ignores_other_channels(self) -> None:
        conn = _mk_conn()
        _insert_ship_artifact(conn, artifact_id="pa_other_channel")
        # Delivered via a *different* channel — must not throttle.
        conn.execute(
            """
            UPDATE proactive_artifacts
            SET delivered_at = ?, delivery_state = 'emailed', delivery_channel = 'cron_artifact'
            WHERE artifact_id = ?
            """,
            (datetime.now(timezone.utc).isoformat(), "pa_other_channel"),
        )
        conn.commit()
        self.assertFalse(digest.is_throttled(conn))

    def test_compose_send_payload_short_circuits_on_throttle(self) -> None:
        conn = _mk_conn()
        _insert_ship_artifact(conn, artifact_id="pa_throttled_003")
        digest.mark_all_delivered(conn, ["pa_throttled_003"])
        # Now insert another ship brief and try again — should throttle.
        _insert_ship_artifact(conn, artifact_id="pa_throttled_004")
        payload = digest.compose_send_payload(conn)
        self.assertEqual(payload, {"status": "throttled"})


# ── Candidate selection ────────────────────────────────────────────────


class CandidateSelectionTests(unittest.TestCase):
    def test_no_candidates_returns_empty(self) -> None:
        conn = _mk_conn()
        self.assertEqual(digest.select_candidates_for_current_hour(conn), [])

    def test_compose_no_candidates(self) -> None:
        conn = _mk_conn()
        payload = digest.compose_send_payload(conn)
        self.assertEqual(payload, {"status": "no_candidates"})

    def test_skips_already_delivered(self) -> None:
        conn = _mk_conn()
        _insert_ship_artifact(conn, artifact_id="pa_delivered_001")
        # Pre-stamp delivered_at — should fall out of the selection.
        conn.execute(
            "UPDATE proactive_artifacts SET delivered_at = ? WHERE artifact_id = ?",
            (datetime.now(timezone.utc).isoformat(), "pa_delivered_001"),
        )
        conn.commit()
        self.assertEqual(digest.select_candidates_for_current_hour(conn), [])

    def test_skips_non_ship_verdict(self) -> None:
        conn = _mk_conn()
        _insert_ship_artifact(conn, artifact_id="pa_skip_001")
        conn.execute(
            "UPDATE proactive_artifacts SET verdict = 'skip' WHERE artifact_id = ?",
            ("pa_skip_001",),
        )
        conn.commit()
        self.assertEqual(digest.select_candidates_for_current_hour(conn), [])

    def test_picks_up_recent_undelivered_ship_briefs(self) -> None:
        # Orphan recovery (2026-06-02): a ship brief authored in a prior hour
        # that was never delivered must still be surfaced — the old
        # current-clock-hour gate orphaned ~40% of ship briefs whenever no
        # digest run caught them in their authoring hour.
        conn = _mk_conn()
        prior = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _insert_ship_artifact(conn, artifact_id="pa_prior_001", created_at=prior)
        out = digest.select_candidates_for_current_hour(conn)
        self.assertEqual([b["artifact_id"] for b in out], ["pa_prior_001"])

    def test_skips_briefs_older_than_lookback(self) -> None:
        conn = _mk_conn()
        old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        _insert_ship_artifact(conn, artifact_id="pa_old_001", created_at=old)
        self.assertEqual(digest.select_candidates_for_current_hour(conn), [])

    def test_needs_attention_pinned_to_top(self) -> None:
        conn = _mk_conn()
        _insert_ship_artifact(
            conn,
            artifact_id="pa_high_score_routine",
            composite_score=0.95,
            needs_attention=False,
        )
        _insert_ship_artifact(
            conn,
            artifact_id="pa_low_score_attention",
            composite_score=0.40,
            needs_attention=True,
        )
        out = digest.select_candidates_for_current_hour(conn)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["artifact_id"], "pa_low_score_attention")
        self.assertEqual(out[1]["artifact_id"], "pa_high_score_routine")


# ── Render ─────────────────────────────────────────────────────────────


class RenderTests(unittest.TestCase):
    def test_subject_single_brief(self) -> None:
        briefs = [{
            "artifact_id": "pa_x",
            "title": "ATLAS insight brief: Hot topic emerging",
            "metadata": {"needs_attention": False},
        }]
        # Fix the time so we can assert the prefix shape.
        when = datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc)
        subj = digest.render_subject(briefs, now_ct=when)
        self.assertIn("Hot topic emerging", subj)
        self.assertNotIn("(+", subj)
        self.assertNotIn("NEEDS ATTENTION", subj)

    def test_subject_multiple_briefs(self) -> None:
        briefs = [
            {"artifact_id": "pa_1", "title": "Headline A", "metadata": {}},
            {"artifact_id": "pa_2", "title": "Headline B", "metadata": {}},
            {"artifact_id": "pa_3", "title": "Headline C", "metadata": {}},
        ]
        when = datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc)
        subj = digest.render_subject(briefs, now_ct=when)
        self.assertIn("Headline A", subj)
        self.assertIn("(+2 more)", subj)

    def test_subject_needs_attention_prefix(self) -> None:
        briefs = [
            {"artifact_id": "pa_1", "title": "Urgent thing", "metadata": {"needs_attention": True}},
        ]
        when = datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc)
        subj = digest.render_subject(briefs, now_ct=when)
        self.assertIn("NEEDS ATTENTION", subj)
        self.assertIn("Urgent thing", subj)

    def test_render_html_contains_required_elements(self) -> None:
        briefs = [{
            "artifact_id": "pa_render_1",
            "title": "Test convergence: AI agents trend",
            "summary": "",
            "metadata": {
                "thesis": "Multiple channels agree X.",
                "composite_score": 0.82,
                "needs_attention": False,
                "key_actions": ["Track upstream channel"],
                "key_entities": ["LangGraph", "CrewAI"],
                "feedback_url_up": "https://example.com/up",
                "feedback_url_down": "https://example.com/down",
            },
        }]
        text, html = digest.render_digest_html(
            briefs,
            base_url="https://app.example.com",
            pause_token="deadbeef" * 2,
        )
        # Plain text
        self.assertIn("Test convergence", text)
        self.assertIn("Multiple channels agree X.", text)
        self.assertIn("https://app.example.com/briefs/pa_render_1", text)
        # HTML
        self.assertIn("Test convergence", html)
        self.assertIn("Multiple channels agree X.", html)
        self.assertIn("Why it matters", html)
        self.assertIn("LangGraph", html)
        self.assertIn("Read full brief", html)
        self.assertIn("https://example.com/up", html)
        self.assertIn("https://example.com/down", html)
        self.assertIn("Pause digest 24h", html)
        self.assertIn("oddcity216@agentmail.to", html)


# ── Stamping ───────────────────────────────────────────────────────────


class StampingTests(unittest.TestCase):
    def test_mark_all_delivered_sets_channel_and_state(self) -> None:
        conn = _mk_conn()
        _insert_ship_artifact(conn, artifact_id="pa_stamp_001")
        digest.mark_all_delivered(conn, ["pa_stamp_001"])
        row = conn.execute(
            "SELECT delivered_at, delivery_state, delivery_channel FROM proactive_artifacts WHERE artifact_id = ?",
            ("pa_stamp_001",),
        ).fetchone()
        self.assertTrue(row["delivered_at"])
        self.assertEqual(row["delivery_state"], _pa.DELIVERY_EMAILED)
        self.assertEqual(row["delivery_channel"], digest.DELIVERY_CHANNEL_HOURLY_DIGEST)

    def test_mark_all_delivered_tolerates_missing_artifact(self) -> None:
        conn = _mk_conn()
        _insert_ship_artifact(conn, artifact_id="pa_stamp_002")
        # One real id + one bogus id. Should not raise.
        digest.mark_all_delivered(conn, ["pa_stamp_002", "pa_does_not_exist"])
        row = conn.execute(
            "SELECT delivery_channel FROM proactive_artifacts WHERE artifact_id = ?",
            ("pa_stamp_002",),
        ).fetchone()
        self.assertEqual(row["delivery_channel"], digest.DELIVERY_CHANNEL_HOURLY_DIGEST)


# ── Happy-path compose_send_payload ───────────────────────────────────


class ComposeReadyTests(unittest.TestCase):
    def test_compose_ready_payload_for_two_ship_briefs(self) -> None:
        conn = _mk_conn()
        _insert_ship_artifact(
            conn,
            artifact_id="pa_ready_a",
            title="ATLAS insight brief: Topic A",
            thesis="Topic A is emerging.",
            composite_score=0.90,
        )
        _insert_ship_artifact(
            conn,
            artifact_id="pa_ready_b",
            title="ATLAS insight brief: Topic B",
            thesis="Topic B is emerging.",
            composite_score=0.60,
        )
        payload = digest.compose_send_payload(conn)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["brief_count"], 2)
        self.assertEqual(payload["inbox_id"], "oddcity216@agentmail.to")
        self.assertEqual(payload["cc"], [])
        self.assertIn("Topic A", payload["subject"])
        self.assertIn("(+1 more)", payload["subject"])
        self.assertEqual(
            set(payload["artifact_ids"]),
            {"pa_ready_a", "pa_ready_b"},
        )
        # Higher composite_score wins primary slot.
        self.assertEqual(payload["artifact_ids"][0], "pa_ready_a")

    def test_compose_ready_payload_includes_cc_when_needs_attention(self) -> None:
        conn = _mk_conn()
        # Use a non-default recipient so the CC isn't deduped.
        with patch.dict(os.environ, {"UA_INTEL_DIGEST_RECIPIENT": "other@example.com"}):
            _insert_ship_artifact(
                conn,
                artifact_id="pa_urgent_1",
                title="ATLAS insight brief: Urgent thing",
                composite_score=0.30,
                needs_attention=True,
            )
            payload = digest.compose_send_payload(conn)
        self.assertEqual(payload["status"], "ready")
        self.assertIn("NEEDS ATTENTION", payload["subject"])
        self.assertEqual(payload["cc"], [digest.ESCALATION_CC])
        self.assertTrue(payload["needs_attention"])


# ── Pause-token signing ────────────────────────────────────────────────


class PauseTokenTests(unittest.TestCase):
    def test_round_trip_with_secret(self) -> None:
        with patch.dict(os.environ, {"UA_FEEDBACK_HMAC_SECRET": "x" * 32}):
            tok = digest.sign_digest_pause_token(24)
            self.assertTrue(tok)
            self.assertTrue(digest.verify_digest_pause_token(24, tok))
            self.assertFalse(digest.verify_digest_pause_token(48, tok))

    def test_empty_when_no_secret(self) -> None:
        # Strip every secret env var the helper would consider.
        secrets = [
            "UA_FEEDBACK_HMAC_SECRET",
            "UA_ARTIFACT_ACK_SECRET",
            "UA_OPS_TOKEN",
            "UA_INTERNAL_API_TOKEN",
        ]
        with patch.dict(os.environ, {k: "" for k in secrets}, clear=False):
            for k in secrets:
                os.environ.pop(k, None)
            self.assertEqual(digest.sign_digest_pause_token(24), "")
            self.assertFalse(digest.verify_digest_pause_token(24, "anything"))


# ── Send-failure recovery semantics ────────────────────────────────────


class SendFailureRecoveryTests(unittest.TestCase):
    def test_artifact_remains_eligible_when_send_step_skipped(self) -> None:
        """If the skill fails to call mark_all_delivered (e.g. send raised),
        the artifact stays eligible for the next heartbeat's attempt."""
        conn = _mk_conn()
        _insert_ship_artifact(conn, artifact_id="pa_retry_1")
        payload = digest.compose_send_payload(conn)
        self.assertEqual(payload["status"], "ready")
        # Skill experiences a send failure → does NOT call mark_all_delivered.
        # Verify the artifact is still selectable next tick.
        next_tick = digest.select_candidates_for_current_hour(conn)
        self.assertEqual(len(next_tick), 1)
        self.assertEqual(next_tick[0]["artifact_id"], "pa_retry_1")


if __name__ == "__main__":
    unittest.main()
