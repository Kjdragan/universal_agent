"""Tests for the upstream-outage alert dedup added to `_emit_cron_event`.

A brief Composio / network outage can fail multiple in-flight cron runs
(and their retries) in seconds. Without dedup the operator gets an alert
email per failed attempt — observed 2026-05-12, three emails for a 30s
Composio blip. The dedup keeps the first alert in a window and suppresses
the rest so the operator's inbox isn't a megaphone for one upstream blip.

Coverage:
  - `_classify_upstream_outage_signature` recognizes the known patterns
    and returns None for unrelated errors (no false dedup).
  - `_should_suppress_upstream_outage_alert` lets the first alert
    through, suppresses subsequent ones within the window, lets a
    different signature through, and respects a 0-second window
    (effectively disabling dedup).
  - The window can be overridden via env.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest


def _reset_module_state(module) -> None:
    """Clear the per-process dedup table between tests."""
    module._last_outage_alert_at.clear()


# ── classification ────────────────────────────────────────────────────────


def test_classify_recognizes_toolrouterv2_internalservererror() -> None:
    from universal_agent import gateway_server as gs

    sig = gs._classify_upstream_outage_signature(
        "Error code: 500 - {'error': {'message': 'Failed to search for "
        "existing tool router session', 'code': 4340, 'slug': "
        "'ToolRouterV2_InternalServerError', 'status': 500}}"
    )
    assert sig == "composio_tool_router_500"


def test_classify_recognizes_tool_router_session_phrase_only() -> None:
    """Match on the human-readable phrase even when the slug differs."""
    from universal_agent import gateway_server as gs

    sig = gs._classify_upstream_outage_signature(
        "RuntimeError: failed to search for existing tool router session"
    )
    assert sig == "composio_tool_router_500"


def test_classify_recognizes_generic_503() -> None:
    from universal_agent import gateway_server as gs

    assert gs._classify_upstream_outage_signature(
        "HTTP 503 Service Unavailable from backend"
    ) == "upstream_503"


def test_classify_recognizes_502_bad_gateway() -> None:
    from universal_agent import gateway_server as gs

    assert gs._classify_upstream_outage_signature(
        "Gateway error: 502 Bad Gateway"
    ) == "upstream_502"


def test_classify_recognizes_connection_refused() -> None:
    from universal_agent import gateway_server as gs

    assert gs._classify_upstream_outage_signature(
        "OSError: [Errno 111] Connection refused"
    ) == "upstream_connection_refused"


def test_classify_returns_none_for_unrelated_error() -> None:
    """A normal script error must NOT be classified as an outage —
    otherwise we'd dedup real bugs."""
    from universal_agent import gateway_server as gs

    assert gs._classify_upstream_outage_signature(
        "ValueError: invalid task_id 'abc'"
    ) is None


def test_classify_returns_none_for_empty_string() -> None:
    from universal_agent import gateway_server as gs

    assert gs._classify_upstream_outage_signature("") is None
    assert gs._classify_upstream_outage_signature(None) is None  # type: ignore[arg-type]


def test_classify_recognizes_composio_429_vercel_rate_limit() -> None:
    """Observed 2026-05-23: Composio's Vercel-fronted edge returned a 429
    with a Vercel "Security Checkpoint" HTML body carrying an iad1::
    edge fingerprint. The Composio-specific signature must win over the
    generic Vercel branch — it gives Mission Control vendor grouping
    AND keys the cron retry policy on a known rate-limit shape."""
    from universal_agent import gateway_server as gs

    body = (
        '<!DOCTYPE html><html lang="en" data-astro-cid-nbv56vs3><head>'
        '<title>Vercel Security Checkpoint</title></head><body>'
        '<a href="https://vercel.link/security-checkpoint">fix</a>'
        'iad1::1779519002-U6zRHDmphyrJ4xP4Be4SzCBgM1J9LNPZ</body></html>'
    )
    assert gs._classify_upstream_outage_signature(body) == "composio_tool_router_429_vercel_rl"


def test_classify_recognizes_vercel_security_checkpoint_title() -> None:
    """A Vercel checkpoint body without the iad1:: Composio fingerprint
    falls through to the generic Vercel signature. Defense in depth for
    any future caller that talks to a different Vercel-fronted upstream."""
    from universal_agent import gateway_server as gs

    body = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<title>Vercel Security Checkpoint</title></head><body>...</body></html>'
    )
    assert gs._classify_upstream_outage_signature(body) == "upstream_vercel_challenge"


def test_classify_recognizes_vercel_link_even_without_title() -> None:
    """Match the canonical fix-text link too, in case Vercel changes
    the page title down the road."""
    from universal_agent import gateway_server as gs

    body = (
        "<html><body>Something different "
        '<a href="https://vercel.link/security-checkpoint">fix</a></body></html>'
    )
    assert gs._classify_upstream_outage_signature(body) == "upstream_vercel_challenge"


def test_classify_does_not_match_random_html_page() -> None:
    """A random HTML page (no Vercel signatures) must NOT classify —
    we deliberately avoid a generic HTML catch-all so legitimate HTML
    errors from a real Vercel-hosted dependency don't get muted."""
    from universal_agent import gateway_server as gs

    body = "<!doctype html><html><body>Some random page content</body></html>"
    assert gs._classify_upstream_outage_signature(body) is None


def test_classify_does_not_match_html_word_inside_normal_error() -> None:
    """Don't classify a plain-text error that merely mentions 'html'."""
    from universal_agent import gateway_server as gs

    assert gs._classify_upstream_outage_signature(
        "ValueError: expected HTML but got JSON"
    ) is None


# ── Vercel dedup end-to-end ──────────────────────────────────────────────


def test_vercel_alert_storm_dedups_after_first() -> None:
    """48 Vercel-tagged retries in 22 minutes (2026-05-23 incident shape)
    must collapse to one alert via the dedup window."""
    from universal_agent import gateway_server as gs

    _reset_module_state(gs)
    body = (
        '<!DOCTYPE html><html><head><title>Vercel Security Checkpoint</title>'
        "</head><body>iad1::checkpoint</body></html>"
    )
    results = [gs._should_suppress_upstream_outage_alert(body) for _ in range(5)]
    assert results == [False, True, True, True, True]


# ── suppression ──────────────────────────────────────────────────────────


def test_first_alert_passes_through() -> None:
    """The first alert with a known signature is always sent."""
    from universal_agent import gateway_server as gs

    _reset_module_state(gs)
    composio_err = "ToolRouterV2_InternalServerError code 4340"
    assert gs._should_suppress_upstream_outage_alert(composio_err) is False


def test_second_alert_in_window_is_suppressed() -> None:
    """Same signature, same window → suppress."""
    from universal_agent import gateway_server as gs

    _reset_module_state(gs)
    err = "ToolRouterV2_InternalServerError"
    gs._should_suppress_upstream_outage_alert(err)  # First — stamps timestamp.
    assert gs._should_suppress_upstream_outage_alert(err) is True


def test_three_alerts_in_window_all_share_dedup() -> None:
    """The 2026-05-12 incident shape: 3 alerts in 5 minutes for the
    same Composio outage → first goes through, second + third
    suppressed."""
    from universal_agent import gateway_server as gs

    _reset_module_state(gs)
    err = "Failed to search for existing tool router session"
    results = [gs._should_suppress_upstream_outage_alert(err) for _ in range(3)]
    assert results == [False, True, True]


def test_unrelated_error_never_suppressed() -> None:
    """Even repeated, an error we don't classify must keep alerting."""
    from universal_agent import gateway_server as gs

    _reset_module_state(gs)
    err = "ValueError: bad task_id"
    assert gs._should_suppress_upstream_outage_alert(err) is False
    assert gs._should_suppress_upstream_outage_alert(err) is False


def test_different_signatures_dedup_independently() -> None:
    """A Composio outage shouldn't suppress an unrelated 503 elsewhere."""
    from universal_agent import gateway_server as gs

    _reset_module_state(gs)
    composio_err = "ToolRouterV2_InternalServerError"
    upstream_503 = "HTTP 503 Service Unavailable"
    assert gs._should_suppress_upstream_outage_alert(composio_err) is False
    assert gs._should_suppress_upstream_outage_alert(upstream_503) is False
    # Both stamps now set; next attempt suppresses each independently.
    assert gs._should_suppress_upstream_outage_alert(composio_err) is True
    assert gs._should_suppress_upstream_outage_alert(upstream_503) is True


def test_alert_passes_after_window_expires() -> None:
    """If `time.time()` advances past the window, the next alert goes
    through."""
    from universal_agent import gateway_server as gs

    _reset_module_state(gs)
    err = "ToolRouterV2_InternalServerError"
    with patch.object(gs.time, "time", return_value=1000.0):
        assert gs._should_suppress_upstream_outage_alert(err) is False
        assert gs._should_suppress_upstream_outage_alert(err) is True
    # Jump forward past default 10-min window.
    with patch.object(gs.time, "time", return_value=1000.0 + 700):
        assert gs._should_suppress_upstream_outage_alert(err) is False


def test_zero_window_disables_dedup(monkeypatch: pytest.MonkeyPatch) -> None:
    """`UA_CRON_ALERT_OUTAGE_DEDUP_SECONDS=0` is an escape hatch —
    every alert goes through."""
    from universal_agent import gateway_server as gs

    _reset_module_state(gs)
    monkeypatch.setenv("UA_CRON_ALERT_OUTAGE_DEDUP_SECONDS", "0")
    err = "ToolRouterV2_InternalServerError"
    assert gs._should_suppress_upstream_outage_alert(err) is False
    assert gs._should_suppress_upstream_outage_alert(err) is False
    assert gs._should_suppress_upstream_outage_alert(err) is False


def test_custom_window_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env var overrides the 600s default."""
    from universal_agent import gateway_server as gs

    _reset_module_state(gs)
    monkeypatch.setenv("UA_CRON_ALERT_OUTAGE_DEDUP_SECONDS", "60")
    err = "ToolRouterV2_InternalServerError"
    with patch.object(gs.time, "time", return_value=1000.0):
        assert gs._should_suppress_upstream_outage_alert(err) is False
        assert gs._should_suppress_upstream_outage_alert(err) is True
    # 61s later, beyond the 60s custom window.
    with patch.object(gs.time, "time", return_value=1061.0):
        assert gs._should_suppress_upstream_outage_alert(err) is False


def test_invalid_window_env_falls_back_to_default() -> None:
    """Garbage env value doesn't crash — default takes over."""
    from universal_agent import gateway_server as gs

    with patch.dict("os.environ", {"UA_CRON_ALERT_OUTAGE_DEDUP_SECONDS": "not_a_number"}, clear=False):
        assert gs._outage_dedup_window_seconds() == gs._UPSTREAM_OUTAGE_DEDUP_DEFAULT_SECONDS
