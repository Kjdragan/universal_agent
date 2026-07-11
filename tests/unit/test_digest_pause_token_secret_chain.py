"""Regression: the digest-pause link emitter and verifier must share ONE HMAC
secret chain.

hourly_intel_digest mints the emailed "pause digest 24h" link; the live
/api/v1/digest/pause endpoint verifies it via cron_artifact_notifier. The two
modules had independent sign/verify copies whose secret resolution differed —
hourly checked UA_FEEDBACK_HMAC_SECRET first, cron_artifact_notifier did not —
so setting UA_FEEDBACK_HMAC_SECRET (which exists only for this feature) silently
made every emitted link fail verification. hourly now re-exports the canonical
functions, so emit and verify agree by construction.
"""

import importlib

import pytest


@pytest.fixture
def modules(monkeypatch):
    # The exact env that used to break it: UA_FEEDBACK_HMAC_SECRET set and
    # distinct from the chain the verifier reads.
    monkeypatch.setenv("UA_FEEDBACK_HMAC_SECRET", "feedback-secret-distinct")
    monkeypatch.setenv("UA_ARTIFACT_ACK_SECRET", "ack-secret-different")
    import universal_agent.services.cron_artifact_notifier as can
    import universal_agent.services.hourly_intel_digest as hid

    importlib.reload(can)
    importlib.reload(hid)
    return hid, can


def test_emit_token_verifies_with_endpoint_verifier(modules):
    hid, can = modules
    # Emitter side (what goes into the email):
    token = hid.sign_digest_pause_token(24)
    assert token, "emitter produced an empty token"
    # Verifier side (what /api/v1/digest/pause actually calls):
    assert can.verify_digest_pause_token(24, token) is True


def test_emitter_reexports_canonical_functions(modules):
    hid, can = modules
    assert hid.sign_digest_pause_token is can.sign_digest_pause_token
    assert hid.verify_digest_pause_token is can.verify_digest_pause_token
