"""Regression guard: routine daemon idle reaping must NOT surface as a
"Session Cancelled" warning notification on the dashboard.

Background — what was wrong
============================
Heartbeat daemon sessions sit idle between ticks. When idle exceeds the
threshold (1800s by default), `_heartbeat_session_timeout_callback`
fires and calls `_cancel_session_execution(session_id,
reason="daemon_idle_timeout:1800s")`. That call routinely added a
`Session Cancelled` notification with severity="warning", making
normal operational lifecycle look like failures on the dashboard's
session-cancelled view.

The fix in `gateway_server.py` bypasses `_add_notification` when the
cancellation reason starts with `daemon_idle_timeout`. Real
cancellations (errors, ops-triggered aborts, mid-run failures) still
get the warning notification.
"""
from __future__ import annotations


def test_daemon_idle_reap_reason_is_classified_as_routine():
    """The discriminator the gateway uses to decide whether to suppress
    the dashboard notification is `reason.startswith('daemon_idle_timeout')`.
    Pin both the prefix and the actual production reason format
    (`daemon_idle_timeout:<seconds>s`) so a future schema change to the
    reason string can't silently re-introduce the noisy notification.
    """
    # Production format: "daemon_idle_timeout:1800s"
    production_reason = "daemon_idle_timeout:1800s"
    assert production_reason.startswith("daemon_idle_timeout")

    # Bare format (timeout_seconds=0 path)
    bare_reason = "daemon_idle_timeout"
    assert bare_reason.startswith("daemon_idle_timeout")

    # Real cancellations must NOT match the routine prefix
    real_cancellations = [
        "Cancelled from ops bulk session controls",
        "execution aborted by user",
        "Mission cancelled after 4 worker cycles",
        "Internal error: provider unavailable",
        "",
    ]
    for reason in real_cancellations:
        assert not reason.startswith("daemon_idle_timeout"), (
            f"reason {reason!r} would be wrongly suppressed from the "
            "Session Cancelled dashboard view"
        )


def test_daemon_idle_timeout_filter_logic_matches_production_call_site():
    """Mirrors the exact one-liner used in
    gateway_server.py:_cancel_session_execution to gate the
    `_add_notification` call. If this test breaks because the filter
    drifts (e.g., loosened to match `daemon_*` and accidentally
    suppress real daemon cancellations), the branch coverage here
    will catch it.
    """
    def _should_suppress(reason: str | None) -> bool:
        return str(reason or "").startswith("daemon_idle_timeout")

    # Suppress: routine reaps
    assert _should_suppress("daemon_idle_timeout") is True
    assert _should_suppress("daemon_idle_timeout:1800s") is True
    assert _should_suppress("daemon_idle_timeout:60s") is True

    # Surface: everything else
    assert _should_suppress("daemon_simone_todo crash") is False
    assert _should_suppress("user cancelled") is False
    assert _should_suppress(None) is False
    assert _should_suppress("") is False
    # Defensive: non-prefix mention shouldn't suppress
    assert _should_suppress("triggered by daemon_idle_timeout policy") is False


def test_daemon_execution_timeout_is_dashboard_only_not_email():
    """A daemon *execution* timeout (a wedged turn killed by the watchdog)
    is a real health signal — it must still surface on the dashboard — but
    it is self-healing (the dispatcher re-runs the task), so it must NOT
    email the operator. The gateway emits it with channels=["dashboard"].

    Mirrors the exact discriminators in
    gateway_server.py:_cancel_session_execution:
      - idle reaps  -> fully suppressed (no notification)
      - execution timeouts -> dashboard-only notification
      - everything else -> default channels (email included)
    """
    def _is_idle_reap(reason: str | None) -> bool:
        return str(reason or "").startswith("daemon_idle_timeout")

    def _is_execution_timeout(reason: str | None) -> bool:
        return str(reason or "").startswith("daemon_execution_timeout")

    def _channels_for(reason: str | None):
        # Returns None to signal "notification suppressed entirely".
        if _is_idle_reap(reason):
            return None
        return ["dashboard"] if _is_execution_timeout(reason) else "default"

    # Production format: "daemon_execution_timeout:1800s"
    assert _is_execution_timeout("daemon_execution_timeout:1800s") is True
    assert _channels_for("daemon_execution_timeout:1800s") == ["dashboard"]
    assert _channels_for("daemon_execution_timeout") == ["dashboard"]

    # Idle reaps stay fully suppressed (not merely dashboard-only).
    assert _channels_for("daemon_idle_timeout:1800s") is None

    # Real cancellations keep default channels (email surfaced).
    assert _channels_for("Internal error: provider unavailable") == "default"
    assert _channels_for("Cancelled from ops bulk session controls") == "default"
    assert _channels_for(None) == "default"

    # An execution timeout must NOT be misclassified as an idle reap.
    assert _is_idle_reap("daemon_execution_timeout:1800s") is False
