"""Unit tests for the per-kind email rollup window in NotificationDispatcher.

The rollup caps incident fan-out: the first alert of a kind sends
immediately and opens a window; same-kind alerts arriving while the
window is open are buffered and emitted as ONE rollup email when the
window expires. This file drives ``dispatch_pending_once`` with a
controllable clock and fake send callables.
"""
from __future__ import annotations

import asyncio

from universal_agent.services.notification_dispatcher import (
    NotificationDispatcher,
    _format_rollup_email,
)


class _Clock:
    """Mutable monotonic clock for deterministic window math."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _row(kind: str, scope: str, *, severity: str = "warning", rid: str = "") -> dict:
    """Build an email-eligible activity-notification row."""
    return {
        "id": rid or f"{kind}:{scope}",
        "kind": kind,
        "title": f"{kind} for {scope}",
        "full_message": f"details {scope}",
        "severity": severity,
        "channels": ["dashboard", "email", "telegram"],
        "metadata": {"task_id": scope},
        "updated_at": "2026-01-01T00:00:00Z",
    }


def _build(clock, *, rollup_enabled=True, window=180.0, cooldown=300.0):
    sends: list[dict] = []
    delivered: list[tuple[str, str]] = []

    async def _send_email(*, to, subject, text, html):
        sends.append({"to": to, "subject": subject, "text": text, "html": html})

    async def _send_telegram(*, chat_id, text):  # pragma: no cover - unused
        raise AssertionError("telegram should not be called in these tests")

    pending: dict[str, list[dict]] = {"rows": []}

    disp = NotificationDispatcher(
        get_pending_rows=lambda: pending["rows"],
        mark_delivered=lambda rid, ch: delivered.append((rid, ch)),
        send_email=_send_email,
        send_telegram=_send_telegram,
        email_targets=["kevin@example.com"],
        telegram_chat_id=None,
        cooldown_seconds=cooldown,
        rollup_enabled=rollup_enabled,
        rollup_window_seconds=window,
        now_fn=clock,
    )
    return disp, sends, delivered, pending


def _tick(disp, pending, rows):
    pending["rows"] = rows
    return asyncio.run(disp.dispatch_pending_once())


class TestRollupWindow:
    def test_first_alert_sends_immediately_and_opens_window(self):
        clock = _Clock()
        disp, sends, delivered, pending = _build(clock)
        summary = _tick(disp, pending, [_row("calendar_missed", "evt1")])
        assert summary["email_sent"] == 1
        assert summary["email_rolled_up"] == 0
        assert len(sends) == 1
        # Window is now open for the kind.
        assert disp._rollup_window_open("calendar_missed") is True

    def test_subsequent_same_kind_buffered_then_one_rollup(self):
        clock = _Clock()
        disp, sends, delivered, pending = _build(clock, window=180.0)
        # One tick with 4 same-kind, distinct-scope rows. Row 1 sends and
        # opens the window; rows 2-4 are buffered (rolled up), not sent.
        rows = [_row("calendar_missed", f"evt{i}") for i in range(4)]
        summary = _tick(disp, pending, rows)
        assert summary["email_sent"] == 1
        assert summary["email_rolled_up"] == 3
        assert len(sends) == 1  # only the first individual send so far
        # Buffered rows were marked delivered so they cannot re-surface.
        assert ("calendar_missed:evt1", "email") in delivered
        assert ("calendar_missed:evt2", "email") in delivered
        assert ("calendar_missed:evt3", "email") in delivered
        # Window not yet expired → no rollup email emitted yet.
        assert summary["rollup_emails_sent"] == 0

        # Advance past the window and tick with no new rows → flush.
        clock.advance(181)
        summary2 = _tick(disp, pending, [])
        assert summary2["rollup_emails_sent"] == 1
        assert len(sends) == 2
        rollup = sends[1]
        assert "calendar_missed" in rollup["subject"]
        assert "3" in rollup["subject"]
        assert "evt1" in rollup["text"]  # sample list preserved
        assert disp._rollup_window_open("calendar_missed") is False

    def test_isolated_alert_no_rollup_at_close(self):
        clock = _Clock()
        disp, sends, delivered, pending = _build(clock, window=180.0)
        _tick(disp, pending, [_row("continuity_alert", "s1")])
        assert len(sends) == 1
        clock.advance(181)
        summary = _tick(disp, pending, [])
        # Nothing buffered → window cleared with no rollup email.
        assert summary["rollup_emails_sent"] == 0
        assert len(sends) == 1
        assert disp._rollup_window_open("continuity_alert") is False

    def test_two_kinds_have_independent_windows(self):
        clock = _Clock()
        disp, sends, delivered, pending = _build(clock, window=180.0)
        rows = [
            _row("calendar_missed", "a1"),
            _row("calendar_missed", "a2"),
            _row("execution_missing_lifecycle_mutation", "b1"),
            _row("execution_missing_lifecycle_mutation", "b2"),
        ]
        summary = _tick(disp, pending, rows)
        # Two first-sends (one per kind), two buffered.
        assert summary["email_sent"] == 2
        assert summary["email_rolled_up"] == 2
        clock.advance(181)
        summary2 = _tick(disp, pending, [])
        assert summary2["rollup_emails_sent"] == 2
        subjects = " ".join(s["subject"] for s in sends[2:])
        assert "calendar_missed" in subjects
        assert "execution_missing_lifecycle_mutation" in subjects

    def test_rollup_disabled_sends_each_individually(self):
        clock = _Clock()
        disp, sends, delivered, pending = _build(clock, rollup_enabled=False)
        # 4 distinct-scope same-kind rows: no rollup, distinct scopes dodge
        # the per-(kind,scope) cooldown, so all 4 send individually.
        rows = [_row("calendar_missed", f"evt{i}") for i in range(4)]
        summary = _tick(disp, pending, rows)
        assert summary["email_sent"] == 4
        assert summary["email_rolled_up"] == 0
        assert summary["rollup_emails_sent"] == 0
        assert len(sends) == 4

    def test_cooldown_still_applies_same_scope_when_rollup_disabled(self):
        clock = _Clock()
        disp, sends, delivered, pending = _build(clock, rollup_enabled=False, cooldown=300.0)
        # Same (kind, scope) twice in one tick → first sends, second is
        # cooldown-skipped (not sent).
        rows = [_row("autonomous_run_failed", "job1", rid="r1"),
                _row("autonomous_run_failed", "job1", rid="r2")]
        summary = _tick(disp, pending, rows)
        assert summary["email_sent"] == 1
        assert summary["email_cooldown_skipped"] == 1
        assert len(sends) == 1


class TestFormatRollupEmail:
    def test_subject_and_body_include_kind_and_count(self):
        subject, text, html = _format_rollup_email("calendar_missed", 5, ["A (scope: x)", "B (scope: y)"])
        assert "calendar_missed" in subject
        assert "5" in subject
        assert "Collapsed events" in text
        assert "A (scope: x)" in text
        assert "calendar_missed" in html

    def test_overflow_noted_when_count_exceeds_samples(self):
        samples = [f"evt{i} (scope: s{i})" for i in range(3)]
        _subject, text, html = _format_rollup_email("k", 10, samples)
        assert "7 more" in text  # 10 - 3 samples
        assert "7 more" in html

    def test_html_escapes_sample_content(self):
        _subject, _text, html = _format_rollup_email("k", 1, ["<script>alert(1)</script>"])
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
