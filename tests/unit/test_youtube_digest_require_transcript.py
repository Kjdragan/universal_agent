"""Transcript-required mode for the Daily YouTube Digest.

When `UA_YOUTUBE_DIGEST_REQUIRE_TRANSCRIPT=1` (default), videos with no usable
transcript are EXCLUDED from the digest instead of getting a metadata-only
retelling. The exclusion branches on WHY:
  * permanent (no captions) -> dropped AND marked processed (won't retry).
  * transient fetch block    -> dropped but NOT marked processed (retries later).
Both are surfaced in a "Skipped — No Transcript" footer.
"""
from __future__ import annotations

import glob
from typing import Any

import pytest

# --- pure-function tests --------------------------------------------------


def test_build_skipped_footer_empty_returns_blank(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_WORKSPACES_DIR", str(tmp_path))
    from universal_agent.scripts import youtube_daily_digest as ydd
    assert ydd._build_skipped_videos_footer([]) == ""


def test_build_skipped_footer_splits_permanent_and_retryable(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_WORKSPACES_DIR", str(tmp_path))
    from universal_agent.scripts import youtube_daily_digest as ydd
    footer = ydd._build_skipped_videos_footer([
        {"video_id": "a1", "title": "No Caps", "failure_class": "transcript_unavailable", "retryable": False},
        {"video_id": "b2", "title": "Blocked", "failure_class": "request_blocked", "retryable": True},
    ])
    assert "Skipped — No Transcript" in footer
    assert "Excluded (1)" in footer and "Deferred (1)" in footer
    assert "No Caps" in footer and "transcript_unavailable" in footer
    assert "Blocked" in footer and "request_blocked" in footer
    assert "youtube.com/watch?v=a1" in footer


def test_require_transcript_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_WORKSPACES_DIR", str(tmp_path))
    from universal_agent.scripts import youtube_daily_digest as ydd
    monkeypatch.setenv("UA_YOUTUBE_DIGEST_REQUIRE_TRANSCRIPT", "1")
    assert ydd._require_transcript() is True
    monkeypatch.setenv("UA_YOUTUBE_DIGEST_REQUIRE_TRANSCRIPT", "0")
    assert ydd._require_transcript() is False


# --- behavioral test ------------------------------------------------------


@pytest.fixture
def digest_module(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_WORKSPACES_DIR", str(tmp_path))
    monkeypatch.setenv("UA_YOUTUBE_DIGEST_REQUIRE_TRANSCRIPT", "1")
    monkeypatch.setenv("UA_YOUTUBE_PLAYLIST_RECREATE_ENABLED", "0")
    monkeypatch.setenv("MONDAY_YT_PLAYLIST", "PLfake")
    from universal_agent.scripts import youtube_daily_digest as ydd

    monkeypatch.setattr(ydd, "initialize_runtime_secrets", lambda: None)
    monkeypatch.setattr(
        ydd, "get_playlist_items",
        lambda playlist_id: [
            {"video_id": "vid_ok", "title": "Has Transcript", "playlist_item_id": "p1"},
            {"video_id": "vid_block", "title": "Proxy Blocked", "playlist_item_id": "p2"},
            {"video_id": "vid_none", "title": "No Captions", "playlist_item_id": "p3"},
        ],
    )

    def _ingest(video_url=None, video_id=None, require_proxy=True, **kwargs):
        if video_id == "vid_ok":
            return {"ok": True, "transcript_text": "real transcript content"}
        if video_id == "vid_block":
            return {"ok": False, "failure_class": "request_blocked", "error": "blocked", "detail": "IP flagged"}
        return {"ok": False, "failure_class": "transcript_unavailable", "error": "no captions", "detail": ""}

    monkeypatch.setattr(ydd, "ingest_youtube_transcript", _ingest)

    async def _fake_generate(**kwargs) -> str:
        return "# Fake Digest\n\nSummary.\n\n```youtube_digest_decisions\n{\"ranked_videos\": []}\n```"

    monkeypatch.setattr(ydd, "_generate_digest_content", _fake_generate)
    monkeypatch.setattr(ydd, "_save_repopulate_pocket", lambda **kw: None)
    monkeypatch.setattr(ydd, "_emit_csi_digest", lambda **kw: True)
    monkeypatch.setattr(ydd, "_save_tutorial_candidates", lambda **kw: tmp_path / "c.json")
    monkeypatch.setattr(ydd, "_dispatch_tutorial_candidates", lambda **kw: [])

    class _Mail:
        async def startup(self): return None
        async def shutdown(self): return None
        async def send_email(self, **kw): return {"message_id": "m1"}

    monkeypatch.setattr(ydd, "AgentMailService", _Mail)
    return ydd, tmp_path


def test_blocked_video_not_persisted_permanent_is(monkeypatch, digest_module):
    ydd, tmp_path = digest_module
    save_calls: list[tuple[list, str]] = []
    monkeypatch.setattr(ydd, "_save_processed_videos", lambda items, day: save_calls.append((items, day)))

    ydd.process_daily_digest(
        dry_run=False, day_override="MONDAY", email_to="kevin@example.com", auto_tutorial_top_n=0,
    )

    assert len(save_calls) == 1, "email succeeded -> exactly one persist call"
    items, _ = save_calls[0]
    saved_ids = {i["video_id"] for i in items}
    # vid_ok (retold) + vid_none (permanent no-transcript) marked processed;
    # vid_block (transient) deliberately left unprocessed to retry.
    assert saved_ids == {"vid_ok", "vid_none"}, f"unexpected persisted set: {saved_ids}"
    assert "vid_block" not in saved_ids

    # The footer must surface both skipped videos with their reasons.
    md_files = glob.glob(str(tmp_path / "daily_digests" / "*_MONDAY_Digest.md"))
    assert md_files, "digest artifact not written"
    content = open(md_files[0], encoding="utf-8").read()
    assert "Skipped — No Transcript" in content
    assert "Proxy Blocked" in content and "request_blocked" in content   # deferred
    assert "No Captions" in content and "transcript_unavailable" in content  # excluded
