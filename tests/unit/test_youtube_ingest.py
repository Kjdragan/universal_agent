from __future__ import annotations

from universal_agent import youtube_ingest


def test_extract_video_id_from_watch_url() -> None:
    assert youtube_ingest.extract_video_id("https://www.youtube.com/watch?v=dxlyCPGCvy8") == "dxlyCPGCvy8"


def test_normalize_video_target_from_video_id() -> None:
    url, video_id = youtube_ingest.normalize_video_target(None, "dxlyCPGCvy8")
    assert video_id == "dxlyCPGCvy8"
    assert url == "https://www.youtube.com/watch?v=dxlyCPGCvy8"


def test_ingest_youtube_transcript_uses_fallback_when_yt_dlp_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        youtube_ingest,
        "_run_yt_dlp_extract",
        lambda *_args, **_kwargs: {"ok": False, "error": "yt_dlp_failed"},
    )
    monkeypatch.setattr(
        youtube_ingest,
        "_run_youtube_transcript_api_extract",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source": "youtube_transcript_api",
            "transcript_text": "line 1\nline 2",
        },
    )

    out = youtube_ingest.ingest_youtube_transcript(
        video_url="https://www.youtube.com/watch?v=dxlyCPGCvy8",
        video_id=None,
        max_chars=1000,
    )
    assert out["ok"] is True
    assert out["status"] == "succeeded"
    assert out["source"] == "youtube_transcript_api"
    assert out["transcript_text"] == "line 1\nline 2"
