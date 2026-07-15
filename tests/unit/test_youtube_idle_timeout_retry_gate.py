"""Root-cause gate: a youtube_tutorial_hook idle-timeout must NOT be retryable.

A manual-webhook idle-timeout (``hook_idle_timeout_{N}s``) means the agent
session went silent — there is no live session to resume. Previously,
``_queue_or_finalize_youtube_attempt`` treated ``hook_idle_timeout`` as
retryable, enqueuing a fire-and-forget retry
(``_schedule_youtube_retry_attempt`` -> ``asyncio.create_task``) whose dispatch
coroutine could fail to lease the new attempt, leaving run=queued /
attempt=queued forever — the orphan class reaped by
``finalize_stale_youtube_hook_runs``.

The gate makes ``_is_retryable_youtube_dispatch_failure`` return False for
``hook_idle_timeout`` / ``hook_idle_timeout_*`` so the lost tutorial is routed
to ``needs_review`` (surfaced) instead of silently orphaned. Hard total timeout
(``hook_timeout_*``), dispatch failures, and proxy-connect failures stay
retryable.

The end-to-end "no retry is enqueued" assertion against the real
``_queue_or_finalize_youtube_attempt`` path lives in
``tests/test_hooks_service.py::test_idle_timeout_does_not_enqueue_retry_routes_to_needs_review``
(reuses the ``hooks_service`` fixture).
"""

from __future__ import annotations

from universal_agent.hooks_service import HooksService


def test_idle_timeout_seconds_form_is_not_retryable() -> None:
    """The exact reason string emitted by the HookIdleTimeout handler
    (``hook_idle_timeout_{N}s``) must not be retryable for youtube hooks — this
    is the literal boolean that gates ``queue_retry``."""
    assert HooksService._is_retryable_youtube_dispatch_failure(
        "hook_idle_timeout_900s"
    ) is False


def test_idle_timeout_bare_form_is_not_retryable() -> None:
    assert HooksService._is_retryable_youtube_dispatch_failure(
        "hook_idle_timeout"
    ) is False


def test_hard_total_timeout_stays_retryable() -> None:
    """Hard wall-clock timeout (``hook_timeout_*``) is unchanged by the gate —
    only idle-timeout is de-retryabled."""
    assert HooksService._is_retryable_youtube_dispatch_failure(
        "hook_timeout_300s"
    ) is True
    assert HooksService._is_retryable_youtube_dispatch_failure("hook_timeout") is True


def test_dispatch_and_proxy_failures_stay_retryable() -> None:
    assert HooksService._is_retryable_youtube_dispatch_failure(
        "hook_dispatch_failed"
    ) is True
    assert HooksService._is_retryable_youtube_dispatch_failure(
        "hook_dispatch_interrupted"
    ) is True
    assert HooksService._is_retryable_youtube_dispatch_failure(
        "proxy_connect_failed"
    ) is True


def test_empty_or_unknown_reason_is_not_retryable() -> None:
    assert HooksService._is_retryable_youtube_dispatch_failure("") is False
    assert HooksService._is_retryable_youtube_dispatch_failure(
        "something_unrelated"
    ) is False
