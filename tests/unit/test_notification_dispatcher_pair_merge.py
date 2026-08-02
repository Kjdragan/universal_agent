"""Unit tests for the T9 cross-kind pair-merge dedup in NotificationDispatcher.

An untrusted inbound email fires TWO different notification kinds for the
SAME thread: ``agentmail_external_arrived`` immediately on ingress, then
~30-60s later (once the triage LLM finishes) either
``agentmail_review_required`` or ``agentmail_quarantined``. The per-kind
cooldown in ``_within_cooldown``/``_record_send`` structurally cannot merge
these — they are different kinds — so both fired as separate operator
emails.

``_scope_key_for_record`` now also resolves ``metadata.thread_id``, and
``_cooldown_kind_for_record`` maps the three agentmail kinds onto one shared
cooldown bucket (``_CROSS_KIND_MERGE_GROUPS``). This drives
``dispatch_pending_once`` with a controllable clock to prove: same
thread_id + a grouped kind pair -> ONE email; unrelated kinds (or
different threads) still alert independently.
"""
from __future__ import annotations

import asyncio

from universal_agent.services.notification_dispatcher import (
    NotificationDispatcher,
    _cooldown_kind_for_record,
    _scope_key_for_record,
)


class _Clock:
    """Mutable monotonic clock for deterministic window math."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _agentmail_row(kind: str, thread_id: str, *, severity: str = "warning", rid: str = "") -> dict:
    """Build an email-eligible activity-notification row shaped like the
    real ``_add_notification`` records ``agentmail_service.py`` emits for
    an untrusted inbound email (no task_id/job_id/run_id — only thread_id)."""
    return {
        "id": rid or f"{kind}:{thread_id}",
        "kind": kind,
        "title": f"{kind} for {thread_id}",
        "full_message": f"details {thread_id}",
        "severity": severity,
        "channels": ["dashboard", "email", "telegram"],
        "metadata": {"thread_id": thread_id, "sender_email": "vendor@example.com"},
        "updated_at": "2026-01-01T00:00:00Z",
    }


def _build(clock, *, rollup_enabled=False, window=180.0, cooldown=300.0):
    # Rollup disabled by default in these tests: the per-kind rollup window
    # is orthogonal to the cross-kind merge under test and would otherwise
    # buffer the SECOND same-kind row rather than exercising the cooldown
    # path directly.
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


class TestScopeKeyThreadIdResolution:
    def test_resolves_thread_id_when_no_task_job_run_id(self):
        record = {"metadata": {"thread_id": "thread-abc"}}
        assert _scope_key_for_record(record) == "thread-abc"

    def test_task_id_still_takes_priority_over_thread_id(self):
        record = {"metadata": {"task_id": "task-1", "thread_id": "thread-abc"}}
        assert _scope_key_for_record(record) == "task-1"

    def test_falls_back_to_session_id_when_no_thread_id(self):
        record = {"metadata": {}, "session_id": "sess-1"}
        assert _scope_key_for_record(record) == "sess-1"


class TestCooldownKindForRecord:
    def test_agentmail_trio_maps_to_shared_bucket(self):
        assert (
            _cooldown_kind_for_record("agentmail_external_arrived")
            == _cooldown_kind_for_record("agentmail_review_required")
            == _cooldown_kind_for_record("agentmail_quarantined")
        )

    def test_unrelated_kind_maps_to_itself(self):
        assert _cooldown_kind_for_record("cron_run_failed") == "cron_run_failed"
        assert _cooldown_kind_for_record("calendar_missed") == "calendar_missed"


class TestPairMergeSameThread:
    def test_arrival_then_review_required_same_thread_sends_one_email(self):
        clock = _Clock()
        disp, sends, delivered, pending = _build(clock)

        # Tick 1: arrival notice fires immediately on ingress.
        summary1 = _tick(
            disp, pending, [_agentmail_row("agentmail_external_arrived", "thread-1")]
        )
        assert summary1["email_sent"] == 1
        assert len(sends) == 1

        # Tick 2 (~54s later, well inside the cooldown window): the
        # post-triage review-required notice fires for the SAME thread.
        clock.advance(54.0)
        summary2 = _tick(
            disp, pending, [_agentmail_row("agentmail_review_required", "thread-1")]
        )
        assert summary2["email_sent"] == 0
        assert summary2["email_cooldown_skipped"] == 1
        # Still exactly one email total for the pair.
        assert len(sends) == 1

    def test_arrival_then_quarantined_same_thread_sends_one_email(self):
        clock = _Clock()
        disp, sends, delivered, pending = _build(clock)

        _tick(disp, pending, [_agentmail_row("agentmail_external_arrived", "thread-2")])
        assert len(sends) == 1

        clock.advance(40.0)
        summary2 = _tick(
            disp, pending, [_agentmail_row("agentmail_quarantined", "thread-2")]
        )
        assert summary2["email_cooldown_skipped"] == 1
        assert len(sends) == 1

    def test_both_records_in_same_tick_still_sends_one_email(self):
        clock = _Clock()
        disp, sends, delivered, pending = _build(clock)

        rows = [
            _agentmail_row("agentmail_external_arrived", "thread-3"),
            _agentmail_row("agentmail_review_required", "thread-3"),
        ]
        summary = _tick(disp, pending, rows)
        assert summary["email_sent"] == 1
        assert summary["email_cooldown_skipped"] == 1
        assert len(sends) == 1

    def test_second_row_still_marked_undelivered_when_suppressed(self):
        # A cooldown-skipped row must NOT be marked delivered — it stays
        # eligible so a persistent condition can still alert once the
        # cooldown window lapses (mirrors existing per-kind cooldown
        # semantics; see test_cooldown_still_applies_same_scope_when_rollup_disabled
        # in test_notification_dispatcher_rollup.py).
        clock = _Clock()
        disp, sends, delivered, pending = _build(clock)

        _tick(disp, pending, [_agentmail_row("agentmail_external_arrived", "thread-4", rid="r1")])
        clock.advance(10.0)
        _tick(disp, pending, [_agentmail_row("agentmail_review_required", "thread-4", rid="r2")])

        assert ("r1", "email") in delivered
        assert ("r2", "email") not in delivered


class TestPairMergeDoesNotOverMerge:
    def test_different_threads_alert_independently(self):
        clock = _Clock()
        disp, sends, delivered, pending = _build(clock)

        rows = [
            _agentmail_row("agentmail_external_arrived", "thread-a"),
            _agentmail_row("agentmail_review_required", "thread-b"),
        ]
        summary = _tick(disp, pending, rows)
        # Distinct threads -> distinct scopes -> both send.
        assert summary["email_sent"] == 2
        assert summary["email_cooldown_skipped"] == 0
        assert len(sends) == 2

    def test_unrelated_kinds_same_scope_still_independent(self):
        # A same-scope pair of kinds NOT in the merge group must not be
        # coalesced -- only the specifically-grouped agentmail kinds share
        # a cooldown bucket. Two genuinely different alert kinds about the
        # same task_id should both still fire.
        clock = _Clock()
        disp, sends, delivered, pending = _build(clock)

        def _row(kind: str, scope: str) -> dict:
            return {
                "id": f"{kind}:{scope}",
                "kind": kind,
                "title": f"{kind} for {scope}",
                "full_message": "details",
                "severity": "warning",
                "channels": ["dashboard", "email", "telegram"],
                "metadata": {"task_id": scope},
                "updated_at": "2026-01-01T00:00:00Z",
            }

        rows = [_row("cron_run_failed", "job-1"), _row("autonomous_run_failed", "job-1")]
        summary = _tick(disp, pending, rows)
        assert summary["email_sent"] == 2
        assert summary["email_cooldown_skipped"] == 0
        assert len(sends) == 2

    def test_two_agentmail_arrivals_for_different_threads_both_send(self):
        clock = _Clock()
        disp, sends, delivered, pending = _build(clock)

        rows = [
            _agentmail_row("agentmail_external_arrived", "thread-x"),
            _agentmail_row("agentmail_external_arrived", "thread-y"),
        ]
        summary = _tick(disp, pending, rows)
        assert summary["email_sent"] == 2
        assert len(sends) == 2
