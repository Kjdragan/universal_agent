"""Tests for universal_agent.approvals — file-based approval CRUD."""

import json
import time

import pytest

from universal_agent.approvals import (
    clear_approvals,
    list_approvals,
    resolve_approvals_path,
    update_approval,
    upsert_approval,
)


@pytest.fixture
def approvals_file(tmp_path, monkeypatch):
    """Point approvals at a temp file and return its path."""
    path = tmp_path / "approvals.json"
    path.write_text(json.dumps({"approvals": []}))
    monkeypatch.setenv("UA_APPROVALS_PATH", str(path))
    return path


# ---------------------------------------------------------------------------
# resolve_approvals_path
# ---------------------------------------------------------------------------


class TestResolvePath:
    def test_env_override(self, tmp_path, monkeypatch):
        p = tmp_path / "custom.json"
        monkeypatch.setenv("UA_APPROVALS_PATH", str(p))
        assert resolve_approvals_path() == p.resolve()

    def test_default_when_no_env(self, monkeypatch):
        monkeypatch.delenv("UA_APPROVALS_PATH", raising=False)
        path = resolve_approvals_path()
        assert path.name == "approvals.json"
        assert "AGENT_RUN_WORKSPACES" in str(path)


# ---------------------------------------------------------------------------
# list_approvals
# ---------------------------------------------------------------------------


class TestListApprovals:
    def test_empty_file(self, approvals_file):
        assert list_approvals() == []

    def test_returns_all_without_filter(self, approvals_file):
        upsert_approval({"approval_id": "a1", "status": "pending"})
        upsert_approval({"approval_id": "a2", "status": "approved"})
        result = list_approvals()
        assert len(result) == 2

    def test_filters_by_status(self, approvals_file):
        upsert_approval({"approval_id": "a1", "status": "pending"})
        upsert_approval({"approval_id": "a2", "status": "approved"})
        result = list_approvals(status="pending")
        assert len(result) == 1
        assert result[0]["approval_id"] == "a1"

    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("UA_APPROVALS_PATH", str(tmp_path / "nonexistent.json"))
        assert list_approvals() == []

    def test_corrupt_file_returns_empty(self, approvals_file):
        approvals_file.write_text("NOT JSON{{{")
        assert list_approvals() == []


# ---------------------------------------------------------------------------
# upsert_approval
# ---------------------------------------------------------------------------


class TestUpsertApproval:
    def test_create_with_explicit_id(self, approvals_file):
        rec = upsert_approval({"approval_id": "a1", "status": "pending"})
        assert rec["approval_id"] == "a1"
        assert rec["status"] == "pending"

    def test_auto_generates_id_when_missing(self, approvals_file):
        rec = upsert_approval({"status": "pending"})
        assert rec["approval_id"].startswith("approval_")

    def test_falls_back_to_phase_id(self, approvals_file):
        rec = upsert_approval({"phase_id": "phase_x"})
        assert rec["approval_id"] == "phase_x"

    def test_defaults_status_to_pending(self, approvals_file):
        rec = upsert_approval({"approval_id": "a1"})
        assert rec["status"] == "pending"

    def test_updates_existing_record(self, approvals_file):
        upsert_approval({"approval_id": "a1", "status": "pending"})
        rec = upsert_approval({"approval_id": "a1", "status": "approved"})
        assert rec["status"] == "approved"
        assert len(list_approvals()) == 1

    def test_updated_at_advances_on_update(self, approvals_file):
        r1 = upsert_approval({"approval_id": "a1", "status": "pending"})
        time.sleep(0.01)
        r2 = upsert_approval({"approval_id": "a1", "status": "approved"})
        assert r2["updated_at"] > r1["updated_at"]

    def test_preserves_created_at_when_passed(self, approvals_file):
        fixed_ts = 1000000.0
        upsert_approval({"approval_id": "a1", "status": "pending", "created_at": fixed_ts})
        rec = upsert_approval({"approval_id": "a1", "status": "approved", "created_at": fixed_ts})
        assert rec["created_at"] == fixed_ts

    def test_persists_to_disk(self, approvals_file):
        upsert_approval({"approval_id": "a1"})
        data = json.loads(approvals_file.read_text())
        assert len(data["approvals"]) == 1


# ---------------------------------------------------------------------------
# update_approval
# ---------------------------------------------------------------------------


class TestUpdateApproval:
    def test_updates_existing(self, approvals_file):
        upsert_approval({"approval_id": "a1", "status": "pending"})
        rec = update_approval("a1", {"status": "approved"})
        assert rec["status"] == "approved"

    def test_returns_none_for_missing(self, approvals_file):
        assert update_approval("nonexistent", {"status": "ok"}) is None

    def test_preserves_approval_id(self, approvals_file):
        upsert_approval({"approval_id": "a1", "status": "pending"})
        rec = update_approval("a1", {"status": "approved", "approval_id": "wrong"})
        assert rec["approval_id"] == "a1"


# ---------------------------------------------------------------------------
# clear_approvals
# ---------------------------------------------------------------------------


class TestClearApprovals:
    def test_clear_all(self, approvals_file):
        upsert_approval({"approval_id": "a1", "status": "pending"})
        upsert_approval({"approval_id": "a2", "status": "approved"})
        removed = clear_approvals()
        assert removed == 2
        assert list_approvals() == []

    def test_clear_by_status(self, approvals_file):
        upsert_approval({"approval_id": "a1", "status": "pending"})
        upsert_approval({"approval_id": "a2", "status": "approved"})
        removed = clear_approvals(statuses=["pending"])
        assert removed == 1
        remaining = list_approvals()
        assert len(remaining) == 1
        assert remaining[0]["approval_id"] == "a2"

    def test_clear_case_insensitive(self, approvals_file):
        upsert_approval({"approval_id": "a1", "status": "Pending"})
        removed = clear_approvals(statuses=["PENDING"])
        assert removed == 1

    def test_empty_file_returns_zero(self, approvals_file):
        assert clear_approvals() == 0

    def test_no_match_returns_zero(self, approvals_file):
        upsert_approval({"approval_id": "a1", "status": "approved"})
        assert clear_approvals(statuses=["denied"]) == 0
