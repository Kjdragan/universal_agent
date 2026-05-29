"""Deploy-restart casualty suppression for the todo-execution lifecycle guardrail.

When a deploy (or operator ``systemctl restart``) SIGTERMs a Simone ToDo run
mid-flight, the ``execution_missing_lifecycle_mutation`` guardrail fires even
though the work item was reopened and will be retried. These tests pin the
routing helper so the operator is NOT paged (email/telegram) for that
self-healing non-event, while genuine lifecycle misses outside a deploy window
still alert loudly on every channel.

Regression context: notification ``ntf_1780067280822_501`` (2026-05-29 10:08 AM
Houston) — PR #559's deploy SIGTERM'd (exit 143) a ToDo run holding
``convergence-candidate:aabfb9575c280a89``; the guardrail correctly fired but
the resulting [ERROR] email was pure noise (the task self-healed via retry
4 minutes later).
"""

from __future__ import annotations

import universal_agent.gateway_server as gs


def _route(monkeypatch, *, deploy_active: bool, kind: str = "execution_missing_lifecycle_mutation"):
    monkeypatch.setattr(
        "universal_agent.cron_service._is_deploy_window_active",
        lambda: deploy_active,
    )
    return gs._lifecycle_miss_notification_routing(
        notification_kind=kind,
        goal_message="Mission requirements were not satisfied. ToDo execution "
        "ended without a durable Task Hub lifecycle mutation.",
    )


def test_lifecycle_miss_inside_deploy_window_is_dashboard_only(monkeypatch):
    severity, requires_action, channels, casualty, message = _route(
        monkeypatch, deploy_active=True
    )
    assert casualty is True
    assert severity == "warning"
    assert requires_action is False
    # Dashboard-only => the notification dispatcher skips email + telegram.
    assert channels == ["dashboard"]
    assert "retried automatically" in message


def test_lifecycle_miss_outside_deploy_window_still_pages_operator(monkeypatch):
    severity, requires_action, channels, casualty, message = _route(
        monkeypatch, deploy_active=False
    )
    assert casualty is False
    assert severity == "error"
    assert requires_action is True
    # channels=None => global routing (dashboard + email + telegram) applies.
    assert channels is None
    # Message is left untouched for a genuine miss.
    assert "retried automatically" not in message


def test_other_guardrail_kinds_are_never_downgraded(monkeypatch):
    # Even inside a deploy window, a non-lifecycle guardrail (e.g. the generic
    # assistance_needed kind) keeps its loud error routing.
    severity, requires_action, channels, casualty, _message = _route(
        monkeypatch, deploy_active=True, kind="assistance_needed"
    )
    assert casualty is False
    assert severity == "error"
    assert requires_action is True
    assert channels is None


def test_deploy_window_probe_failure_defaults_to_loud(monkeypatch):
    # If the deploy-window probe raises, never suppress — fail loud.
    def _boom():
        raise RuntimeError("proc read failed")

    monkeypatch.setattr(
        "universal_agent.cron_service._is_deploy_window_active", _boom
    )
    severity, requires_action, channels, casualty, _message = (
        gs._lifecycle_miss_notification_routing(
            notification_kind="execution_missing_lifecycle_mutation",
            goal_message="x",
        )
    )
    assert casualty is False
    assert severity == "error"
    assert requires_action is True
    assert channels is None
