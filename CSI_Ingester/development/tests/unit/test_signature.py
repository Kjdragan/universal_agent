import hashlib
import hmac
import json

from csi_ingester.signature import build_signing_string, generate_signature, verify_signature


def test_signature_roundtrip(monkeypatch):
    payload = {"b": 2, "a": 1}
    request_id = "req_abc"
    signature, timestamp = generate_signature("secret", request_id, payload)
    assert verify_signature(
        shared_secret="secret",
        request_id=request_id,
        timestamp=timestamp,
        payload=payload,
        signature_hex=signature,
    )


def test_signing_string_uses_canonical_json():
    payload = {"z": 1, "a": 2}
    signing = build_signing_string("100", "r1", payload)
    expected_body = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    expected_sig = hmac.new(
        b"secret",
        f"100.r1.{expected_body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    actual = hmac.new(b"secret", signing.encode("utf-8"), hashlib.sha256).hexdigest()
    assert expected_sig == actual

