"""Tests for cron_artifact_notifier.

Covers:
  - Opt-in gate (default OFF; only fires when notify_on_artifact=True)
  - Workspace discovery (manifest.json preferred; falls back to scan)
  - Initial email is sent + delivery recorded
  - Reminder state seeded into metadata_json
  - HMAC ack token sign/verify
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from universal_agent.services import proactive_artifacts
from universal_agent.services.cron_artifact_notifier import (
    _build_artifacts_listing,
    _load_manifest,
    notify_cron_artifact,
    sign_ack_token,
    verify_ack_token,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    proactive_artifacts.ensure_schema(c)
    return c


@pytest.fixture
def mail_service() -> AsyncMock:
    svc = AsyncMock()
    svc.send_email = AsyncMock(
        return_value={
            "message_id": "msg_1",
            "thread_id": "thread_1",
            "status": "sent",
        }
    )
    return svc


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UA_CRON_ARTIFACT_LLM_SUMMARY", "0")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "cron_paper_to_podcast"
    (ws / "work_products" / "paper_to_podcast").mkdir(parents=True)
    return ws


def _write_manifest(workspace: Path, items: list[dict]) -> None:
    (workspace / "manifest.json").write_text(
        json.dumps({"artifacts": items}), encoding="utf-8"
    )


# ── Opt-in gate ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_default_off_does_not_fire(conn, mail_service, workspace) -> None:
    """Default behavior: notify_on_artifact unset → no email."""
    _write_manifest(
        workspace,
        [{"title": "podcast.mp3", "path": "work_products/podcast.mp3"}],
    )
    result = await notify_cron_artifact(
        conn=conn,
        mail_service=mail_service,
        job_id="example_cron",
        job_metadata={},  # NOT opted in
        job_command="run something",
        workspace_dir=workspace,
        started_at=1779600000.0,
        finished_at=1779600300.0,
        recipient="kevinjdragan@gmail.com",
    )
    assert result is None
    mail_service.send_email.assert_not_called()


@pytest.mark.asyncio
async def test_opt_in_fires(conn, mail_service, workspace) -> None:
    _write_manifest(
        workspace,
        [{"title": "podcast.mp3", "path": "work_products/podcast.mp3"}],
    )
    result = await notify_cron_artifact(
        conn=conn,
        mail_service=mail_service,
        job_id="paper_to_podcast",
        job_metadata={"notify_on_artifact": True},
        job_command="generate a podcast",
        workspace_dir=workspace,
        started_at=1779600000.0,
        finished_at=1779600300.0,
        recipient="kevinjdragan@gmail.com",
    )
    assert result is not None
    mail_service.send_email.assert_called_once()
    # Recipient + opt-in landed correctly.
    call = mail_service.send_email.call_args
    assert call.kwargs["to"] == "kevinjdragan@gmail.com"
    assert "paper_to_podcast" in call.kwargs["subject"]


# ── Workspace discovery ────────────────────────────────────────────────


def test_manifest_preferred_over_scan(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "work_products").mkdir()
    # Junk file that would normally appear in a scan
    (workspace / "work_products" / "garbage.tmp").write_text("noise")
    # Manifest declares the real artifact
    _write_manifest(
        workspace,
        [{"title": "podcast.mp3", "path": "work_products/podcast.mp3"}],
    )
    manifest = _load_manifest(workspace)
    listing = _build_artifacts_listing(workspace, manifest)
    assert len(listing) == 1
    assert listing[0]["title"] == "podcast.mp3"


def test_scan_fallback_when_no_manifest(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    work_products = workspace / "work_products"
    work_products.mkdir()
    (work_products / "podcast.mp3").write_text("audio")
    (work_products / "quiz.md").write_text("quiz")
    # Cron-scaffolding noise that should be filtered out
    (work_products / "sync_ready_1.json").write_text("noise")
    (work_products / "BOOTSTRAP_1.md").write_text("noise")
    (work_products / "HEARTBEAT_1.md").write_text("noise")

    manifest = _load_manifest(workspace)
    assert manifest is None
    listing = _build_artifacts_listing(workspace, manifest)
    titles = sorted(item["title"] for item in listing)
    assert titles == ["podcast.mp3", "quiz.md"]


def test_empty_workspace_yields_no_listing(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    listing = _build_artifacts_listing(workspace, None)
    assert listing == []


@pytest.mark.asyncio
async def test_no_artifacts_does_not_send(conn, mail_service, tmp_path: Path) -> None:
    workspace = tmp_path / "empty_ws"
    workspace.mkdir()
    result = await notify_cron_artifact(
        conn=conn,
        mail_service=mail_service,
        job_id="empty_cron",
        job_metadata={"notify_on_artifact": True},
        job_command="produce nothing",
        workspace_dir=workspace,
        started_at=1779600000.0,
        finished_at=1779600100.0,
        recipient="kevinjdragan@gmail.com",
    )
    assert result is None
    mail_service.send_email.assert_not_called()


# ── Reminder state seed ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reminder_state_seeded(conn, mail_service, workspace) -> None:
    _write_manifest(
        workspace,
        [{"title": "podcast.mp3", "path": "work_products/podcast.mp3"}],
    )
    finished_at = 1779600300.0
    artifact = await notify_cron_artifact(
        conn=conn,
        mail_service=mail_service,
        job_id="paper_to_podcast",
        job_metadata={"notify_on_artifact": True},
        job_command="generate a podcast",
        workspace_dir=workspace,
        started_at=1779600000.0,
        finished_at=finished_at,
        recipient="kevinjdragan@gmail.com",
    )
    assert artifact is not None
    meta = artifact.get("metadata") or {}
    reminder = meta.get("reminder") or {}
    assert reminder["count"] == 1
    assert reminder["schedule_state"] == "sent_initial"
    assert reminder["stopped"] is False
    # Same-day nudge scheduled at finished_at + 4h.
    assert reminder["next_reminder_at_epoch"] == finished_at + 4 * 3600


# ── Email delivery recording ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_email_delivery_recorded(conn, mail_service, workspace) -> None:
    _write_manifest(
        workspace, [{"title": "x.md", "path": "work_products/x.md"}]
    )
    await notify_cron_artifact(
        conn=conn,
        mail_service=mail_service,
        job_id="t",
        job_metadata={"notify_on_artifact": True},
        job_command="x",
        workspace_dir=workspace,
        started_at=1779600000.0,
        finished_at=1779600300.0,
        recipient="kevinjdragan@gmail.com",
    )
    rows = conn.execute(
        "SELECT message_id, thread_id, recipient FROM proactive_artifact_emails"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "msg_1"
    assert rows[0][1] == "thread_1"
    assert rows[0][2] == "kevinjdragan@gmail.com"


# ── HMAC ack token sign/verify ─────────────────────────────────────────


def test_ack_token_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UA_ARTIFACT_ACK_SECRET", "test-secret-1234")
    token = sign_ack_token("pa_abc123")
    assert token  # non-empty
    assert verify_ack_token("pa_abc123", token) is True


def test_ack_token_wrong_artifact_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UA_ARTIFACT_ACK_SECRET", "test-secret-1234")
    token = sign_ack_token("pa_abc123")
    assert verify_ack_token("pa_different", token) is False


def test_ack_token_empty_token_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UA_ARTIFACT_ACK_SECRET", "test-secret-1234")
    assert verify_ack_token("pa_abc", "") is False


def test_ack_token_no_secret_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UA_ARTIFACT_ACK_SECRET", raising=False)
    monkeypatch.delenv("UA_OPS_TOKEN", raising=False)
    monkeypatch.delenv("UA_INTERNAL_API_TOKEN", raising=False)
    assert sign_ack_token("pa_abc") == ""
