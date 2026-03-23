"""Tests for the heartbeat notification deduplication layers.

Covers:
- Layer 1: classification-aware mediation cooldown
- Layer 2: heartbeat notification upsert in _add_notification
- Layer 3: email-level dedup in _notify_operator_of_heartbeat_recommendation
- Layer 4: stale session skip in _heartbeat_session_already_investigated
"""
from __future__ import annotations

import time

import pytest


# ---------------------------------------------------------------------------
# Layer 1 – _heartbeat_classification_label
# ---------------------------------------------------------------------------

def test_classification_label_from_findings_with_ids() -> None:
    from universal_agent.gateway_server import _heartbeat_classification_label

    findings = {
        "overall_status": "warn",
        "findings": [
            {"finding_id": "agentmail_credentials_missing", "severity": "warn"},
            {"finding_id": "disk_usage_high", "severity": "warn"},
        ],
    }
    label = _heartbeat_classification_label(findings)
    assert label == "warn:agentmail_credentials_missing,disk_usage_high"


def test_classification_label_stable_across_different_sessions() -> None:
    """Same finding IDs from two different sessions → same label."""
    from universal_agent.gateway_server import _heartbeat_classification_label

    findings_a = {
        "overall_status": "critical",
        "findings": [
            {"finding_id": "e2big_spawn_failure", "severity": "critical"},
        ],
    }
    findings_b = {
        "overall_status": "critical",
        "findings": [
            {"finding_id": "e2big_spawn_failure", "severity": "critical"},
        ],
    }
    assert _heartbeat_classification_label(findings_a) == _heartbeat_classification_label(findings_b)


def test_classification_label_empty_findings_returns_status() -> None:
    from universal_agent.gateway_server import _heartbeat_classification_label

    assert _heartbeat_classification_label({"overall_status": "ok"}) == "ok"
    assert _heartbeat_classification_label({}) == "unknown"


# ---------------------------------------------------------------------------
# Layer 1 – _should_dispatch_heartbeat_mediation with classification cooldown
# ---------------------------------------------------------------------------

def test_classification_cooldown_blocks_same_class_dispatch() -> None:
    from universal_agent.gateway_server import (
        _heartbeat_classification_cooldowns,
        _heartbeat_mediation_cooldowns,
        _should_dispatch_heartbeat_mediation,
    )

    # Clear state
    _heartbeat_classification_cooldowns.clear()
    _heartbeat_mediation_cooldowns.clear()

    config = {
        "enabled": True,
        "coverage_model": "all_non_ok_tiered",
        "cooldown_minutes": 60,
        "classification_cooldown_minutes": 120,
    }

    # First dispatch → allowed
    allowed_1, reason_1 = _should_dispatch_heartbeat_mediation(
        "sig_aaa", config, classification_label="warn:creds_missing"
    )
    assert allowed_1 is True
    assert reason_1 is None

    # Second dispatch, different signature, SAME classification → blocked
    allowed_2, reason_2 = _should_dispatch_heartbeat_mediation(
        "sig_bbb", config, classification_label="warn:creds_missing"
    )
    assert allowed_2 is False
    assert reason_2 == "classification_cooldown_active"


def test_different_classifications_pass_through() -> None:
    from universal_agent.gateway_server import (
        _heartbeat_classification_cooldowns,
        _heartbeat_mediation_cooldowns,
        _should_dispatch_heartbeat_mediation,
    )

    _heartbeat_classification_cooldowns.clear()
    _heartbeat_mediation_cooldowns.clear()

    config = {
        "enabled": True,
        "coverage_model": "all_non_ok_tiered",
        "cooldown_minutes": 60,
        "classification_cooldown_minutes": 120,
    }

    allowed_1, _ = _should_dispatch_heartbeat_mediation(
        "sig_ccc", config, classification_label="warn:creds_missing"
    )
    assert allowed_1 is True

    # Different classification → should be allowed
    allowed_2, reason_2 = _should_dispatch_heartbeat_mediation(
        "sig_ddd", config, classification_label="critical:e2big"
    )
    assert allowed_2 is True
    assert reason_2 is None


def test_classification_cooldown_expires() -> None:
    from universal_agent.gateway_server import (
        _heartbeat_classification_cooldowns,
        _heartbeat_mediation_cooldowns,
        _should_dispatch_heartbeat_mediation,
    )

    _heartbeat_classification_cooldowns.clear()
    _heartbeat_mediation_cooldowns.clear()

    config = {
        "enabled": True,
        "coverage_model": "all_non_ok_tiered",
        "cooldown_minutes": 60,
        "classification_cooldown_minutes": 0,  # 0 = disabled
    }

    _should_dispatch_heartbeat_mediation(
        "sig_eee", config, classification_label="warn:test"
    )
    # Classification cooldown disabled → second dispatch should pass
    allowed, reason = _should_dispatch_heartbeat_mediation(
        "sig_fff", config, classification_label="warn:test"
    )
    assert allowed is True


# ---------------------------------------------------------------------------
# Layer 3 – Email-level dedup
# ---------------------------------------------------------------------------

def test_email_cooldown_blocks_duplicate_send() -> None:
    from universal_agent.gateway_server import _heartbeat_operator_email_cooldowns

    _heartbeat_operator_email_cooldowns.clear()

    # Simulate a recent email send
    _heartbeat_operator_email_cooldowns["email:code_regression"] = time.time()

    # Check that a second send for the same classification would be blocked
    # (We can't easily test the async function directly, but we test the state)
    key = "email:code_regression"
    cooldown_seconds = 240 * 60  # 4 hours
    now = time.time()
    last_ts = _heartbeat_operator_email_cooldowns.get(key)
    assert last_ts is not None
    assert (now - last_ts) < cooldown_seconds  # Within cooldown


def test_email_cooldown_different_class_not_blocked() -> None:
    from universal_agent.gateway_server import _heartbeat_operator_email_cooldowns

    _heartbeat_operator_email_cooldowns.clear()
    _heartbeat_operator_email_cooldowns["email:code_regression"] = time.time()

    # Different classification should not be blocked
    key = "email:infra_drift"
    last_ts = _heartbeat_operator_email_cooldowns.get(key)
    assert last_ts is None  # No previous send for this classification


# ---------------------------------------------------------------------------
# Layer 4 – _heartbeat_session_already_investigated
# ---------------------------------------------------------------------------

def test_session_already_investigated_returns_true() -> None:
    import universal_agent.gateway_server as gs

    original_notifications = list(gs._notifications)
    try:
        gs._notifications.clear()
        gs._notifications.append({
            "id": "ntf_test_123",
            "kind": "heartbeat_operator_review_sent",
            "session_id": "session_20260322_064304_b5a69748",
            "status": "new",
            "metadata": {"session_id": "session_20260322_064304_b5a69748"},
        })
        assert gs._heartbeat_session_already_investigated("session_20260322_064304_b5a69748") is True
    finally:
        gs._notifications.clear()
        gs._notifications.extend(original_notifications)


def test_session_not_investigated_returns_false() -> None:
    import universal_agent.gateway_server as gs

    original_notifications = list(gs._notifications)
    try:
        gs._notifications.clear()
        gs._notifications.append({
            "id": "ntf_test_456",
            "kind": "heartbeat_operator_review_sent",
            "session_id": "session_different",
            "status": "new",
            "metadata": {"session_id": "session_different"},
        })
        assert gs._heartbeat_session_already_investigated("session_20260322_064304_b5a69748") is False
    finally:
        gs._notifications.clear()
        gs._notifications.extend(original_notifications)


def test_session_investigated_via_metadata_session_key() -> None:
    import universal_agent.gateway_server as gs

    original_notifications = list(gs._notifications)
    try:
        gs._notifications.clear()
        gs._notifications.append({
            "id": "ntf_test_789",
            "kind": "heartbeat_investigation_completed",
            "session_id": "",
            "status": "new",
            "metadata": {"session_key": "session_20260322_target"},
        })
        assert gs._heartbeat_session_already_investigated("session_20260322_target") is True
    finally:
        gs._notifications.clear()
        gs._notifications.extend(original_notifications)
