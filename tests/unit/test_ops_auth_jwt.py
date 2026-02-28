from datetime import timezone

from universal_agent.auth.ops_auth import issue_ops_jwt, validate_ops_token


def test_issue_and_validate_jwt_token_roundtrip():
    token, expires_at = issue_ops_jwt(
        jwt_secret="test-jwt-secret-key-with-32-bytes!!",
        subject="worker_test",
        ttl_seconds=3600,
    )
    assert expires_at.tzinfo == timezone.utc

    verdict = validate_ops_token(
        token,
        jwt_secret="test-jwt-secret-key-with-32-bytes!!",
        legacy_token="",
        allow_legacy=True,
    )
    assert verdict.ok is True
    assert verdict.mode == "jwt"
    assert verdict.subject == "worker_test"


def test_legacy_fallback_and_reject_when_disabled():
    verdict_ok = validate_ops_token(
        "legacy-token",
        jwt_secret="",
        legacy_token="legacy-token",
        allow_legacy=True,
    )
    assert verdict_ok.ok is True
    assert verdict_ok.mode == "legacy"

    verdict_fail = validate_ops_token(
        "legacy-token",
        jwt_secret="",
        legacy_token="legacy-token",
        allow_legacy=False,
    )
    assert verdict_fail.ok is False
