from __future__ import annotations

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
