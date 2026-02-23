from __future__ import annotations

import json

from csi_ingester.net.egress_adapter import detect_anti_bot_block, parse_endpoint_list, post_json_with_failover


def test_parse_endpoint_list_dedupes_and_fallback():
    endpoints = parse_endpoint_list(
        "http://a:1, https://b:2, http://a:1, ftp://bad",
        fallback="http://fallback:3",
    )
    assert endpoints == ["http://a:1", "https://b:2"]

    fallback_only = parse_endpoint_list("", fallback="http://fallback:3")
    assert fallback_only == ["http://fallback:3"]


def test_detect_anti_bot_block_matches_known_markers():
    payload = {
        "error": "youtube_transcript_api_failed",
        "failure_class": "request_blocked",
        "detail": "YouTube is blocking requests from your IP",
    }
    assert detect_anti_bot_block(payload) is True
    assert detect_anti_bot_block({"error": "timeout"}) is False


def test_post_json_with_failover_uses_next_endpoint(monkeypatch):
    calls: list[str] = []

    class _FakeResp:
        def __init__(self, status: int, payload: dict):
            self.status = status
            self._payload = payload

        def read(self) -> bytes:
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def _fake_urlopen(req, timeout=0):
        url = req.full_url
        calls.append(url)
        if "endpoint-a" in url:
            raise OSError("network error")
        return _FakeResp(200, {"ok": True, "value": "from-b"})

    monkeypatch.setattr("csi_ingester.net.egress_adapter.urllib.request.urlopen", _fake_urlopen)

    out = post_json_with_failover(
        endpoints=["http://endpoint-a/api", "http://endpoint-b/api"],
        payload={"hello": "world"},
        timeout_seconds=5,
    )
    assert out.get("ok") is True
    assert out.get("value") == "from-b"
    attempts = out.get("endpoint_attempts")
    assert isinstance(attempts, list) and len(attempts) == 2
    assert calls == ["http://endpoint-a/api", "http://endpoint-b/api"]
