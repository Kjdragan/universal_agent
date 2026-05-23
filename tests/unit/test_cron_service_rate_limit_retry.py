"""Tests for `_is_rate_limit_exception` — Workstream G of the
Vercel-checkpoint incident (2026-05-23).

When a cron tick's heavyweight bootstrap (or its in-process work) raises
because an upstream returned HTTP 429, retrying twice more inside the
same minute triples the request volume and lengthens the rate-limit
window. The cron's own schedule (next tick in ≤ 60s) is the right
backoff — so we set `retryable=False` for known rate-limit shapes and
let the natural schedule reissue.

Coverage:
  - Vercel-checkpoint HTML body → non-retryable (Composio-edge 429 shape).
  - Raw "429 Too Many Requests" httpx string → non-retryable.
  - "Rate limit exceeded" / "Too many requests" tokens → non-retryable.
  - Plain Python exceptions / generic 500s → still retryable.
  - Empty / None error_text → still retryable (default behaviour).
"""

from __future__ import annotations

import pytest


def test_vercel_checkpoint_body_is_non_retryable() -> None:
    from universal_agent.cron_service import _is_rate_limit_exception

    body = (
        '<!DOCTYPE html><html><head>'
        '<title>Vercel Security Checkpoint</title></head><body>'
        'iad1::1779519002</body></html>'
    )
    assert _is_rate_limit_exception(body) is True


def test_vercel_link_alone_is_non_retryable() -> None:
    from universal_agent.cron_service import _is_rate_limit_exception

    body = '<html><a href="https://vercel.link/security-checkpoint">fix</a></html>'
    assert _is_rate_limit_exception(body) is True


def test_raw_httpx_429_string_is_non_retryable() -> None:
    """Some SDKs surface the raw httpx error: `Client error '429 Too
    Many Requests' for url '...'`. Must classify."""
    from universal_agent.cron_service import _is_rate_limit_exception

    err = "Client error '429 Too Many Requests' for url 'https://api.example.com/foo'"
    assert _is_rate_limit_exception(err) is True


def test_rate_limit_phrase_is_non_retryable() -> None:
    from universal_agent.cron_service import _is_rate_limit_exception

    assert _is_rate_limit_exception("API: Rate limit exceeded, try again later") is True


@pytest.mark.parametrize(
    "err",
    [
        "ValueError: invalid task_id 'abc'",
        "HTTP 503 Service Unavailable",
        "OSError: [Errno 111] Connection refused",
        "TimeoutError: deadline exceeded",
    ],
)
def test_unrelated_errors_remain_retryable(err: str) -> None:
    """Non-rate-limit failures keep the existing retry behaviour —
    otherwise we'd accidentally turn off retries for transient bugs."""
    from universal_agent.cron_service import _is_rate_limit_exception

    assert _is_rate_limit_exception(err) is False


def test_empty_and_none_remain_retryable() -> None:
    """An empty error_text shouldn't trip the classifier — default
    retryable behaviour preserved."""
    from universal_agent.cron_service import _is_rate_limit_exception

    assert _is_rate_limit_exception("") is False
    assert _is_rate_limit_exception(None) is False  # type: ignore[arg-type]
