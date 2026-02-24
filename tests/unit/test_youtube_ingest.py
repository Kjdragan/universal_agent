from __future__ import annotations

import sys
import types

from universal_agent import youtube_ingest


def test_extract_video_id_from_watch_url() -> None:
    assert youtube_ingest.extract_video_id("https://www.youtube.com/watch?v=dxlyCPGCvy8") == "dxlyCPGCvy8"


def test_normalize_video_target_from_video_id() -> None:
    url, video_id = youtube_ingest.normalize_video_target(None, "dxlyCPGCvy8")
    assert video_id == "dxlyCPGCvy8"
    assert url == "https://www.youtube.com/watch?v=dxlyCPGCvy8"


def test_ingest_youtube_transcript_uses_youtube_transcript_api(monkeypatch) -> None:
    monkeypatch.setattr(
        youtube_ingest,
        "_run_youtube_transcript_api_extract",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source": "youtube_transcript_api",
            "transcript_text": "line 1\nline 2\nline 3\nline 4\nline 5\nline 6",
        },
    )

    out = youtube_ingest.ingest_youtube_transcript(
        video_url="https://www.youtube.com/watch?v=dxlyCPGCvy8",
        video_id=None,
        max_chars=1000,
        min_chars=20,
    )
    assert out["ok"] is True
    assert out["status"] == "succeeded"
    assert out["source"] == "youtube_transcript_api"
    assert out["transcript_quality_pass"] is True


def test_ingest_youtube_transcript_fails_for_low_quality(monkeypatch) -> None:
    monkeypatch.setattr(
        youtube_ingest,
        "_run_youtube_transcript_api_extract",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source": "youtube_transcript_api",
            "transcript_text": "thanks for watching",
        },
    )

    out = youtube_ingest.ingest_youtube_transcript(
        video_url="https://www.youtube.com/watch?v=dxlyCPGCvy8",
        video_id=None,
        max_chars=1000,
        min_chars=20,
    )
    assert out["ok"] is False
    assert out["error"] == "transcript_quality_failed"
    assert out["failure_class"] == "empty_or_low_quality_transcript"
    assert isinstance(out.get("transcript_quality_reason"), str)


def test_ingest_youtube_transcript_failure_class_passthrough(monkeypatch) -> None:
    monkeypatch.setattr(
        youtube_ingest,
        "_run_youtube_transcript_api_extract",
        lambda *_args, **_kwargs: {
            "ok": False,
            "error": "youtube_transcript_api_failed",
            "failure_class": "request_blocked",
            "detail": "Sign in to confirm you're not a bot",
        },
    )

    out = youtube_ingest.ingest_youtube_transcript(
        video_url="https://www.youtube.com/watch?v=dxlyCPGCvy8",
        video_id=None,
    )
    assert out["ok"] is False
    assert out["failure_class"] == "request_blocked"


def test_run_extract_enforces_english_without_proxy(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeFetched:
        snippets = [types.SimpleNamespace(text="hello"), types.SimpleNamespace(text="world")]

    class FakeApi:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = kwargs

        def fetch(self, video_id: str, languages=None):
            captured["video_id"] = video_id
            captured["languages"] = languages
            return FakeFetched()

    monkeypatch.delenv("PROXY_USERNAME", raising=False)
    monkeypatch.delenv("PROXY_PASSWORD", raising=False)
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", types.SimpleNamespace(YouTubeTranscriptApi=FakeApi))
    monkeypatch.delitem(sys.modules, "youtube_transcript_api.proxies", raising=False)

    out = youtube_ingest._run_youtube_transcript_api_extract("dxlyCPGCvy8")

    assert out["ok"] is True
    assert out["language"] == "en"
    assert out["proxy_mode"] == "disabled"
    assert captured["video_id"] == "dxlyCPGCvy8"
    assert captured["languages"] == ["en"]
    assert captured["init_kwargs"] == {}


def test_run_extract_uses_webshare_proxy_when_env_present(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeFetched:
        snippets = [types.SimpleNamespace(text="line1"), types.SimpleNamespace(text="line2")]

    class FakeProxyConfig:
        def __init__(self, **kwargs):
            captured["proxy_kwargs"] = kwargs

    class FakeApi:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = kwargs

        def fetch(self, video_id: str, languages=None):
            captured["video_id"] = video_id
            captured["languages"] = languages
            return FakeFetched()

    monkeypatch.setenv("PROXY_USERNAME", "proxy-user")
    monkeypatch.setenv("PROXY_PASSWORD", "proxy-pass")
    monkeypatch.setenv("PROXY_FILTER_IP_LOCATIONS", "us,de,us")
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", types.SimpleNamespace(YouTubeTranscriptApi=FakeApi))
    monkeypatch.setitem(
        sys.modules,
        "youtube_transcript_api.proxies",
        types.SimpleNamespace(WebshareProxyConfig=FakeProxyConfig),
    )

    out = youtube_ingest._run_youtube_transcript_api_extract("dxlyCPGCvy8")

    assert out["ok"] is True
    assert out["proxy_mode"] == "webshare"
    assert captured["video_id"] == "dxlyCPGCvy8"
    assert captured["languages"] == ["en"]
    assert captured["proxy_kwargs"] == {
        "proxy_username": "proxy-user",
        "proxy_password": "proxy-pass",
        "filter_ip_locations": ["us", "de"],
    }
    assert "proxy_config" in captured["init_kwargs"]
