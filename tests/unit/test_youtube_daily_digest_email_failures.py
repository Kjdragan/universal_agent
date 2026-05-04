"""Email-delivery contract for the Daily YouTube Digest.

The user observed digests producing no email in production.  Beyond the
job-not-registered bug fixed in Phase 1, the digest also has a dangerous
silent-failure path: if `mail.send_email` raises, the original code
swallowed the exception and STILL marked all videos as processed via
`_save_processed_videos`.  Result: videos got burned with no delivery,
no retry possible, no operator alert.

These tests pin three contracts:
  1. Email succeeds → videos saved as processed (happy path unchanged).
  2. Email fails → videos NOT saved + `proactive_delivery_failed`
     notification fires (so the digest can re-run next day with the
     same un-processed videos still eligible).
  3. `email_to=None` (no-email mode) → videos saved (intentional path
     for callers that drive their own delivery; must not regress).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def digest_module(monkeypatch, tmp_path):
    """Import the digest module with all heavy dependencies stubbed.

    Stubs both anthropic.AsyncAnthropic and the AgentMail service so the
    test never touches the real network or the real LLM.  Each test
    further replaces the AgentMailService stub with whatever behaviour
    it needs to assert.
    """
    monkeypatch.setenv("UA_WORKSPACES_DIR", str(tmp_path))
    from universal_agent.scripts import youtube_daily_digest as ydd

    monkeypatch.setattr(ydd, "initialize_runtime_secrets", lambda: None)
    monkeypatch.setenv("MONDAY_YT_PLAYLIST", "PLfake")

    monkeypatch.setattr(
        ydd,
        "get_playlist_items",
        lambda playlist_id: [
            {"video_id": "vid1", "title": "First Video", "playlist_item_id": "pli-1"},
        ],
    )
    monkeypatch.setattr(
        ydd,
        "ingest_youtube_transcript",
        lambda video_url=None, video_id=None, require_proxy=True: {
            "ok": True,
            "transcript_text": "fake transcript content",
        },
    )

    async def _fake_generate(prompt: str) -> str:
        return "# Fake Digest\n\nSummary content.\n\n```json\n{\"decisions\": []}\n```"

    monkeypatch.setattr(ydd, "_generate_digest_content", _fake_generate)
    # No-op the side-effecting telemetry / dispatch helpers — they're
    # safe to keep firing pre-email per the plan, but we don't want
    # them touching the filesystem in ways unrelated to this test.
    monkeypatch.setattr(ydd, "_save_repopulate_pocket", lambda **kw: None)
    monkeypatch.setattr(ydd, "_emit_csi_digest", lambda **kw: True)
    monkeypatch.setattr(
        ydd,
        "_save_tutorial_candidates",
        lambda **kw: tmp_path / "fake_candidates.json",
    )
    monkeypatch.setattr(ydd, "_dispatch_tutorial_candidates", lambda **kw: [])
    return ydd


def _install_agentmail_stub(monkeypatch, digest_module, *, send_raises: bool = False):
    """Install a stand-in `AgentMailService` whose send_email behaviour
    is controlled by `send_raises`."""
    calls: list[dict[str, Any]] = []

    class _StubAgentMailService:
        async def startup(self):
            return None

        async def shutdown(self):
            return None

        async def send_email(self, **kwargs):
            calls.append(kwargs)
            if send_raises:
                raise RuntimeError("simulated SMTP failure")
            return {"message_id": "msg-test-1"}

    monkeypatch.setattr(digest_module, "AgentMailService", _StubAgentMailService)
    return calls


def test_process_daily_digest_saves_videos_on_email_success(monkeypatch, digest_module):
    """Happy path: email succeeds → _save_processed_videos IS called."""
    save_calls: list[tuple[list, str]] = []
    monkeypatch.setattr(
        digest_module,
        "_save_processed_videos",
        lambda items, day: save_calls.append((items, day)),
    )
    _install_agentmail_stub(monkeypatch, digest_module, send_raises=False)

    digest_module.process_daily_digest(
        dry_run=False,
        day_override="MONDAY",
        email_to="kevin@example.com",
        auto_tutorial_top_n=0,
    )

    assert len(save_calls) == 1, "Expected exactly one _save_processed_videos call on email success"
    items, day = save_calls[0]
    assert day == "MONDAY"
    assert len(items) == 1


def test_process_daily_digest_skips_video_save_on_email_failure(monkeypatch, digest_module):
    """Email fails → _save_processed_videos must NOT be called.

    Without this guard, a failed delivery burns the videos and they
    cannot be retried on the next cron tick.
    """
    save_calls: list[tuple[list, str]] = []
    notification_calls: list[dict] = []
    monkeypatch.setattr(
        digest_module,
        "_save_processed_videos",
        lambda items, day: save_calls.append((items, day)),
    )
    monkeypatch.setattr(
        digest_module,
        "_emit_proactive_delivery_failure",
        lambda **kw: notification_calls.append(kw),
    )
    _install_agentmail_stub(monkeypatch, digest_module, send_raises=True)

    digest_module.process_daily_digest(
        dry_run=False,
        day_override="MONDAY",
        email_to="kevin@example.com",
        auto_tutorial_top_n=0,
    )

    assert save_calls == [], (
        "When email fails, _save_processed_videos must NOT run — videos "
        "must remain unprocessed so the next cron tick can retry."
    )
    assert len(notification_calls) == 1, (
        "Phase 2 must emit a `proactive_delivery_failed` notification when "
        "email fails so the operator can see the burn would have happened."
    )
    notif = notification_calls[0]
    assert notif["day_name"] == "MONDAY"
    assert "simulated SMTP failure" in str(notif.get("error") or "")


def test_process_daily_digest_saves_videos_when_email_not_configured(monkeypatch, digest_module):
    """`email_to=None` is the intentional no-email mode (callers do their
    own delivery).  Phase 2 must NOT regress this path: videos still get
    saved as processed."""
    save_calls: list[tuple[list, str]] = []
    monkeypatch.setattr(
        digest_module,
        "_save_processed_videos",
        lambda items, day: save_calls.append((items, day)),
    )
    _install_agentmail_stub(monkeypatch, digest_module, send_raises=False)

    digest_module.process_daily_digest(
        dry_run=False,
        day_override="MONDAY",
        email_to=None,
        auto_tutorial_top_n=0,
    )

    assert len(save_calls) == 1, (
        "no-email mode (email_to=None) must still mark videos as processed"
    )
