"""Unit tests for the VP mission ↔ PR reconciler.

Covers the writer (`record_mission_pr`, `extract_pr_from_text`) and the
reader (`reconcile_vp_missions_with_prs`), with the GitHub HTTP call
mocked. No network access.

Tests use a real in-memory-style task_hub schema so the metadata-merge
behavior is exercised end-to-end, not mocked.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any
from unittest.mock import patch

import pytest

from universal_agent import task_hub
from universal_agent.services.vp_mission_pr_reconciler import (
    extract_pr_from_text,
    reconcile_vp_missions_with_prs,
    record_mission_pr,
)

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def th_conn(tmp_path: Path, monkeypatch) -> sqlite3.Connection:
    """Real SQLite connection with the production task_hub schema applied."""
    monkeypatch.setenv("UA_TASK_HUB_MISSIONS_ENABLED", "1")
    db = tmp_path / "task_hub.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _seed_vp_mission_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    status: str = task_hub.TASK_STATUS_BLOCKED,
    metadata: dict[str, Any] | None = None,
) -> None:
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "vp_mission",
            "title": f"test mission {task_id}",
            "status": status,
            "metadata": metadata or {},
        },
    )
    conn.commit()


# ── extract_pr_from_text ──────────────────────────────────────────────


def test_extract_pr_from_plain_url():
    text = "Done. Shipped at https://github.com/Kjdragan/universal_agent/pull/142"
    result = extract_pr_from_text(text)
    assert result == {
        "url": "https://github.com/Kjdragan/universal_agent/pull/142",
        "owner": "Kjdragan",
        "repo": "universal_agent",
        "number": 142,
    }


def test_extract_pr_from_markdown_link():
    text = "Mission complete. See [PR #247](https://github.com/Kjdragan/universal_agent/pull/247) for details."
    result = extract_pr_from_text(text)
    assert result is not None
    assert result["number"] == 247


def test_extract_pr_from_text_without_url():
    assert extract_pr_from_text("All done, no PR opened.") is None
    assert extract_pr_from_text("") is None


def test_extract_pr_takes_first_match():
    text = "first https://github.com/a/b/pull/1 then https://github.com/c/d/pull/2"
    result = extract_pr_from_text(text)
    assert result["number"] == 1


# ── record_mission_pr ────────────────────────────────────────────────


def test_record_mission_pr_writes_clean_metadata(th_conn):
    _seed_vp_mission_task(th_conn, task_id="vp-mission-abc")
    record_mission_pr(
        th_conn,
        mission_id="vp-mission-abc",
        pr_number=42,
        pr_url="https://github.com/x/y/pull/42",
        head_branch="codie/feature",
    )
    th_conn.commit()
    item = task_hub.get_item(th_conn, "vp-mission-abc")
    pr = item["metadata"]["dispatch"]["pr"]
    assert pr["number"] == 42
    assert pr["url"] == "https://github.com/x/y/pull/42"
    assert pr["head_branch"] == "codie/feature"
    assert "recorded_at" in pr


def test_record_mission_pr_preserves_other_dispatch_keys(th_conn):
    """We must not clobber sibling metadata.dispatch.* fields. This was
    the trap that made the design split out a deep-merge helper instead
    of relying on upsert_item's shallow merge."""
    _seed_vp_mission_task(
        th_conn,
        task_id="vp-mission-xyz",
        metadata={
            "dispatch": {
                "last_disposition": "review",
                "preferred_vp": "vp.coder.primary",
            }
        },
    )
    record_mission_pr(
        th_conn,
        mission_id="vp-mission-xyz",
        pr_number=99,
    )
    th_conn.commit()
    item = task_hub.get_item(th_conn, "vp-mission-xyz")
    dispatch = item["metadata"]["dispatch"]
    assert dispatch["last_disposition"] == "review"
    assert dispatch["preferred_vp"] == "vp.coder.primary"
    assert dispatch["pr"]["number"] == 99


def test_record_mission_pr_idempotent(th_conn):
    """Calling twice with the same PR number must not corrupt state."""
    _seed_vp_mission_task(th_conn, task_id="vp-mission-i")
    record_mission_pr(th_conn, mission_id="vp-mission-i", pr_number=7)
    th_conn.commit()
    first = task_hub.get_item(th_conn, "vp-mission-i")["metadata"]["dispatch"]["pr"]
    record_mission_pr(th_conn, mission_id="vp-mission-i", pr_number=7, pr_url="https://github.com/a/b/pull/7")
    th_conn.commit()
    second = task_hub.get_item(th_conn, "vp-mission-i")["metadata"]["dispatch"]["pr"]
    assert second["number"] == 7
    assert second["url"] == "https://github.com/a/b/pull/7"
    # `recorded_at` from first call should be preserved (we only setdefault it).
    assert second["recorded_at"] == first["recorded_at"]


def test_record_mission_pr_silent_when_task_missing(th_conn):
    """No task_hub row → log and return; do not raise."""
    # Note we do NOT seed a row.
    record_mission_pr(th_conn, mission_id="vp-mission-ghost", pr_number=1)
    th_conn.commit()
    assert task_hub.get_item(th_conn, "vp-mission-ghost") is None


# ── reconcile_vp_missions_with_prs ───────────────────────────────────


def _gh_response(*, merged_at: str | None, merge_commit_sha: str | None = "abc123") -> dict[str, Any]:
    return {
        "number": 42,
        "state": "closed" if merged_at else "open",
        "merged_at": merged_at,
        "merge_commit_sha": merge_commit_sha,
        "head": {"ref": "codie/some-branch"},
    }


def test_reconcile_closes_task_when_pr_merged(th_conn, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake_token_for_test")
    _seed_vp_mission_task(
        th_conn,
        task_id="vp-mission-merged",
        metadata={"dispatch": {"pr": {"number": 42, "url": "https://github.com/x/y/pull/42"}}},
    )
    with patch(
        "universal_agent.services.vp_mission_pr_reconciler._query_github_pr",
        return_value=_gh_response(merged_at="2026-05-12T10:00:00Z"),
    ):
        result = reconcile_vp_missions_with_prs(th_conn)

    assert result["scanned"] == 1
    assert result["closed"] == 1
    item = task_hub.get_item(th_conn, "vp-mission-merged")
    assert item["status"] == task_hub.TASK_STATUS_COMPLETED
    pr = item["metadata"]["dispatch"]["pr"]
    assert pr["merged_at"] == "2026-05-12T10:00:00Z"
    assert pr["merge_commit_sha"] == "abc123"
    # Disposition trail captured for operator audit.
    assert item["metadata"]["dispatch"]["last_disposition"] == "completed"
    assert "pr_reconciler" in item["metadata"]["dispatch"]["last_disposition_reason"]


def test_reconcile_leaves_task_open_when_pr_not_merged(th_conn, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake_token_for_test")
    _seed_vp_mission_task(
        th_conn,
        task_id="vp-mission-open",
        metadata={"dispatch": {"pr": {"number": 42}}},
    )
    with patch(
        "universal_agent.services.vp_mission_pr_reconciler._query_github_pr",
        return_value=_gh_response(merged_at=None),
    ):
        result = reconcile_vp_missions_with_prs(th_conn)

    assert result["still_open"] == 1
    assert result["closed"] == 0
    item = task_hub.get_item(th_conn, "vp-mission-open")
    assert item["status"] == task_hub.TASK_STATUS_BLOCKED  # unchanged


def test_reconcile_skips_tasks_without_recorded_pr(th_conn, monkeypatch):
    """Tasks whose metadata never got `dispatch.pr.number` are silently
    skipped — they're handled by the operator Mark Complete button."""
    monkeypatch.setenv("GITHUB_TOKEN", "fake_token_for_test")
    _seed_vp_mission_task(th_conn, task_id="vp-mission-no-pr")
    with patch(
        "universal_agent.services.vp_mission_pr_reconciler._query_github_pr"
    ) as mock_query:
        result = reconcile_vp_missions_with_prs(th_conn)

    assert result["scanned"] == 0
    mock_query.assert_not_called()


def test_reconcile_marks_pr_deleted_on_404(th_conn, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake_token_for_test")
    _seed_vp_mission_task(
        th_conn,
        task_id="vp-mission-deleted-pr",
        metadata={"dispatch": {"pr": {"number": 999}}},
    )
    with patch(
        "universal_agent.services.vp_mission_pr_reconciler._query_github_pr",
        return_value={"_status": 404},
    ):
        result = reconcile_vp_missions_with_prs(th_conn)

    assert result["pr_deleted"] == 1
    item = task_hub.get_item(th_conn, "vp-mission-deleted-pr")
    # Task stays open — operator decides.
    assert item["status"] == task_hub.TASK_STATUS_BLOCKED
    assert item["metadata"]["dispatch"]["pr"]["deleted"] is True


def test_reconcile_dry_run_does_not_mutate(th_conn, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake_token_for_test")
    _seed_vp_mission_task(
        th_conn,
        task_id="vp-mission-dry",
        metadata={"dispatch": {"pr": {"number": 42}}},
    )
    with patch(
        "universal_agent.services.vp_mission_pr_reconciler._query_github_pr",
        return_value=_gh_response(merged_at="2026-05-12T10:00:00Z"),
    ):
        result = reconcile_vp_missions_with_prs(th_conn, dry_run=True)

    assert result["closed"] == 1  # counter reflects intent
    item = task_hub.get_item(th_conn, "vp-mission-dry")
    assert item["status"] == task_hub.TASK_STATUS_BLOCKED  # not mutated


def test_reconcile_handles_missing_token_gracefully(th_conn, monkeypatch):
    """Production has GITHUB_TOKEN via Infisical; dev may not. Reconciler
    must short-circuit gracefully, not raise."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    _seed_vp_mission_task(
        th_conn,
        task_id="vp-mission-no-token",
        metadata={"dispatch": {"pr": {"number": 42}}},
    )
    # No patching of _query_github_pr — let it actually run and short-circuit.
    result = reconcile_vp_missions_with_prs(th_conn)

    assert result["scanned"] == 1
    assert result["skipped_no_token"] == 1
    assert result["closed"] == 0
    item = task_hub.get_item(th_conn, "vp-mission-no-token")
    assert item["status"] == task_hub.TASK_STATUS_BLOCKED  # unchanged


def test_reconcile_treats_cloudflare_challenge_as_skip(th_conn, monkeypatch):
    """A Cloudflare JS-challenge on the GitHub call (incident 2026-05-21)
    must not be counted as an error or emit a failure alert with the
    HTML body. The next 15-min tick will retry."""
    monkeypatch.setenv("GITHUB_TOKEN", "fake_token_for_test")
    _seed_vp_mission_task(
        th_conn,
        task_id="vp-mission-cf-blocked",
        metadata={"dispatch": {"pr": {"number": 42}}},
    )
    with patch(
        "universal_agent.services.vp_mission_pr_reconciler._query_github_pr",
        return_value={"_status": "cloudflare_challenge"},
    ):
        result = reconcile_vp_missions_with_prs(th_conn)

    assert result["cloudflare_skipped"] == 1
    assert result["errors"] == 0
    assert result["closed"] == 0
    item = task_hub.get_item(th_conn, "vp-mission-cf-blocked")
    assert item["status"] == task_hub.TASK_STATUS_BLOCKED  # unchanged


def test_looks_like_cloudflare_challenge_detects_known_signature():
    from universal_agent.services.vp_mission_pr_reconciler import (
        _looks_like_cloudflare_challenge,
    )
    challenge_html = (
        b'<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...'
        b'</title><meta http-equiv="Content-Type" content="text/html; charset=UTF-8">'
        b'<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>'
    )
    assert _looks_like_cloudflare_challenge(challenge_html) is True
    assert _looks_like_cloudflare_challenge(b'{"number": 42, "merged_at": null}') is False
    assert _looks_like_cloudflare_challenge("") is False


def test_reconcile_ignores_terminal_status_tasks(th_conn, monkeypatch):
    """A task already in `completed` status must not be re-scanned, even
    if it still has dispatch.pr metadata."""
    monkeypatch.setenv("GITHUB_TOKEN", "fake_token_for_test")
    _seed_vp_mission_task(
        th_conn,
        task_id="vp-mission-already-done",
        status=task_hub.TASK_STATUS_COMPLETED,
        metadata={"dispatch": {"pr": {"number": 42}}},
    )
    with patch(
        "universal_agent.services.vp_mission_pr_reconciler._query_github_pr"
    ) as mock_query:
        result = reconcile_vp_missions_with_prs(th_conn)

    assert result["scanned"] == 0
    mock_query.assert_not_called()
