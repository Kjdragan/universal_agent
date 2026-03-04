from __future__ import annotations

from csi_ingester.threads_webhooks import ThreadsWebhookSettings, validate_signed_payload, validate_verification_request


def test_threads_webhook_verification_token_guard():
    settings = ThreadsWebhookSettings(enabled=True, verify_token="verify-me", app_secret="secret")
    challenge = validate_verification_request(
        mode="subscribe",
        verify_token="verify-me",
        challenge="abc123",
        settings=settings,
    )
    assert challenge == "abc123"


def test_threads_webhook_signature_validation():
    settings = ThreadsWebhookSettings(enabled=True, verify_token="", app_secret="topsecret")
    payload = b'{"object":"threads"}'

    import hmac
    import hashlib

    digest = hmac.new(b"topsecret", payload, hashlib.sha256).hexdigest()
    assert validate_signed_payload(raw_body=payload, signature_header=f"sha256={digest}", settings=settings) is True
