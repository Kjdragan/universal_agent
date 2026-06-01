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
from pathlib import Path
import sqlite3
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


def test_newest_manifest_wins_over_stale_undated(tmp_path: Path) -> None:
    """Regression: reused cron workspace where a date-stamped manifest is
    written each run but the undated ``manifest.json`` is left frozen at a
    prior run. The disclosure must reflect the newest run, not the stale
    undated file. (Paper-to-podcast false "wrong topic" alarm, 2026-05-31.)
    """
    workspace = tmp_path / "ws"
    sub = workspace / "work_products" / "paper_to_podcast"
    sub.mkdir(parents=True)

    stale = sub / "manifest.json"
    stale.write_text(
        json.dumps({"topic": "Open-source AI democratization (yesterday)"}),
        encoding="utf-8",
    )
    fresh = sub / "manifest_20260531.json"
    fresh.write_text(
        json.dumps({"topic": "Agentic AI architectures (today)"}),
        encoding="utf-8",
    )
    # Make the undated file older than the date-stamped one.
    os.utime(stale, (1_780_000_000, 1_780_000_000))
    os.utime(fresh, (1_780_100_000, 1_780_100_000))

    manifest = _load_manifest(workspace)
    assert manifest is not None
    assert manifest["topic"] == "Agentic AI architectures (today)"


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


def test_manifest_artifacts_as_dict_of_descriptors(tmp_path: Path) -> None:
    """Regression: paper_to_podcast stores ``artifacts`` as a dict keyed by
    artifact name, not a list. The extractor must still surface them instead
    of falling through to the work_products scan."""
    workspace = tmp_path / "ws"
    (workspace / "work_products" / "paper_to_podcast").mkdir(parents=True)
    (workspace / "work_products" / "paper_to_podcast" / "manifest.json").write_text(
        json.dumps(
            {
                "pipeline": "paper_to_podcast",
                "topic": "Open-source AI democratization",
                "artifacts": {
                    "podcast": {"type": "audio", "file": "podcast_deep_dive.m4a"},
                    "quiz": {"type": "quiz", "file": "quiz.html"},
                    "flashcards": {"type": "flashcards", "file": "flashcards.html"},
                },
            }
        ),
        encoding="utf-8",
    )
    manifest = _load_manifest(workspace)
    assert manifest is not None
    listing = _build_artifacts_listing(workspace, manifest)
    titles = sorted(item["title"] for item in listing)
    assert titles == ["flashcards", "podcast", "quiz"]
    paths = {item["path"] for item in listing}
    assert "podcast_deep_dive.m4a" in paths


def test_scan_skips_agent_scaffolding_and_reaches_subdir_deliverables(
    tmp_path: Path,
) -> None:
    """Regression for the May-30 false 'no deliverable artifacts' alarm:
    numbered agent context-dumps (AGENTS_*.md, IDENTITY_*.md, ...) must not
    crowd out the real deliverables that live in a skill output subdir."""
    workspace = tmp_path / "ws"
    work_products = workspace / "work_products"
    work_products.mkdir(parents=True)
    # ~40 numbered context-snapshot files — these sort before "paper_to_podcast/"
    # and previously exhausted the 25-file cap.
    for family in ("AGENTS", "IDENTITY", "USER", "capabilities", "SOUL", "HEARTBEAT"):
        (work_products / f"{family}.md").write_text("scaffold")
        for n in range(1, 8):
            (work_products / f"{family}_{n}.md").write_text("scaffold")
    (work_products / "cron_result_18.md").write_text("scaffold")
    (work_products / "run_manifest_39.json").write_text("scaffold")
    # The real deliverables in the skill subdir.
    skill = work_products / "paper_to_podcast"
    skill.mkdir()
    (skill / "podcast_deep_dive.m4a").write_text("audio")
    (skill / "quiz.html").write_text("quiz")
    (skill / "flashcards.html").write_text("cards")

    listing = _build_artifacts_listing(workspace, None)
    titles = {item["title"] for item in listing}
    assert {"podcast_deep_dive.m4a", "quiz.html", "flashcards.html"} <= titles
    assert not any(t.startswith(("AGENTS", "IDENTITY", "USER", "SOUL")) for t in titles)


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


# ── Task Hub linkage for Proactive Task History tab ───────────────────


def _create_cron_task_hub_row(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    project_key: str = "immediate",
) -> None:
    """Seed a task_hub_items row that mirrors what the F.1 close-out
    creates for a cron — so the notifier's promotion path has something
    to update."""
    # Schema is defined in task_hub.ensure_schema; create a minimal one
    # for the test rather than importing the full module side effects.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_hub_items (
            task_id TEXT PRIMARY KEY,
            source_kind TEXT NOT NULL,
            source_ref TEXT,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            project_key TEXT NOT NULL DEFAULT 'immediate',
            priority INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'open',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO task_hub_items "
        "(task_id, source_kind, title, project_key, created_at, updated_at) "
        "VALUES (?, 'cron_run', ?, ?, '2026-05-24T00:00:00Z', '2026-05-24T00:00:00Z')",
        (task_id, "Cron: paper_to_podcast_daily", project_key),
    )
    conn.commit()


@pytest.mark.asyncio
async def test_notifier_links_artifact_to_cron_task(
    conn, mail_service, workspace
) -> None:
    _create_cron_task_hub_row(conn, task_id="cron:paper_to_podcast")
    _write_manifest(workspace, [{"title": "x", "path": "work_products/x"}])
    artifact = await notify_cron_artifact(
        conn=conn,
        mail_service=mail_service,
        job_id="paper_to_podcast",
        job_metadata={"notify_on_artifact": True},
        job_command="run",
        workspace_dir=workspace,
        started_at=1779600000.0,
        finished_at=1779600300.0,
        recipient="kevinjdragan@gmail.com",
    )
    assert artifact is not None
    meta = artifact.get("metadata") or {}
    assert meta["task_id"] == "cron:paper_to_podcast"


@pytest.mark.asyncio
async def test_notifier_promotes_cron_task_to_proactive(
    conn, mail_service, workspace
) -> None:
    _create_cron_task_hub_row(
        conn,
        task_id="cron:paper_to_podcast",
        project_key="immediate",
    )
    _write_manifest(workspace, [{"title": "x", "path": "work_products/x"}])
    await notify_cron_artifact(
        conn=conn,
        mail_service=mail_service,
        job_id="paper_to_podcast",
        job_metadata={"notify_on_artifact": True},
        job_command="run",
        workspace_dir=workspace,
        started_at=1779600000.0,
        finished_at=1779600300.0,
        recipient="kevinjdragan@gmail.com",
    )
    row = conn.execute(
        "SELECT project_key FROM task_hub_items WHERE task_id=?",
        ("cron:paper_to_podcast",),
    ).fetchone()
    assert row is not None
    assert row[0] == "proactive"


@pytest.mark.asyncio
async def test_notifier_uses_system_job_for_task_id_when_present(
    conn, mail_service, workspace
) -> None:
    """Regression for 2026-05-24: the prod cron has job_id='2afe05ab96'
    (a hash) but metadata.system_job='paper_to_podcast_daily'. The
    canonical Task Hub row is cron:paper_to_podcast_daily, NOT
    cron:2afe05ab96. The notifier must use the system_job-derived
    task_id (matching derive_cron_task_id in cron_task_hub_link.py)
    or the project_key promotion silently matches zero rows."""
    _create_cron_task_hub_row(conn, task_id="cron:paper_to_podcast_daily")
    _write_manifest(workspace, [{"title": "x", "path": "work_products/x"}])
    artifact = await notify_cron_artifact(
        conn=conn,
        mail_service=mail_service,
        job_id="2afe05ab96",  # hash, NOT the canonical name
        job_metadata={
            "notify_on_artifact": True,
            "system_job": "paper_to_podcast_daily",
        },
        job_command="run",
        workspace_dir=workspace,
        started_at=1779600000.0,
        finished_at=1779600300.0,
        recipient="kevinjdragan@gmail.com",
    )
    assert artifact is not None
    meta = artifact.get("metadata") or {}
    # CRITICAL: task_id derived from system_job, not job_id
    assert meta["task_id"] == "cron:paper_to_podcast_daily"
    # And the promotion landed on the right row:
    row = conn.execute(
        "SELECT project_key FROM task_hub_items WHERE task_id=?",
        ("cron:paper_to_podcast_daily",),
    ).fetchone()
    assert row is not None
    assert row[0] == "proactive"


@pytest.mark.asyncio
async def test_notifier_missing_task_hub_row_no_crash(
    conn, mail_service, workspace
) -> None:
    """If the cron's task_hub_items row doesn't exist yet (race or
    skip_task_hub_link cron), the promotion UPDATE is a no-op and the
    artifact still gets emailed."""
    # NOTE: no _create_cron_task_hub_row call here
    _write_manifest(workspace, [{"title": "x", "path": "work_products/x"}])
    artifact = await notify_cron_artifact(
        conn=conn,
        mail_service=mail_service,
        job_id="orphan_cron",
        job_metadata={"notify_on_artifact": True},
        job_command="run",
        workspace_dir=workspace,
        started_at=1779600000.0,
        finished_at=1779600300.0,
        recipient="kevinjdragan@gmail.com",
    )
    assert artifact is not None
    mail_service.send_email.assert_called_once()


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
