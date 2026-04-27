from __future__ import annotations

import json
from pathlib import Path

from csi_ingester.adapters.threads_api import ThreadsAPIError
from csi_ingester.adapters.threads_publishing import (
    ThreadsPublishingDisabledError,
    ThreadsPublishingGovernanceError,
    ThreadsPublishingInterface,
)
import pytest


class _FakeClient:
    def __init__(self):
        self.create_calls = []
        self.publish_calls = []
        self.status_calls = []

    async def create_media_container(self, **kwargs):
        self.create_calls.append(kwargs)
        return {"id": "90000000000000001"}

    async def publish_media_container(self, *, creation_id: str):
        self.publish_calls.append({"creation_id": creation_id})
        return {"id": "17890000000000000"}

    async def container_status(self, *, container_id: str):
        self.status_calls.append({"container_id": container_id})
        return {"id": container_id, "status": "FINISHED"}


class _PollingClient(_FakeClient):
    def __init__(self):
        super().__init__()
        self._statuses = ["IN_PROGRESS", "IN_PROGRESS", "FINISHED"]

    async def container_status(self, *, container_id: str):
        self.status_calls.append({"container_id": container_id})
        status = self._statuses.pop(0) if self._statuses else "FINISHED"
        return {"id": container_id, "status": status}


class _FailingClient(_FakeClient):
    async def create_media_container(self, **kwargs):
        self.create_calls.append(kwargs)
        raise ThreadsAPIError("http_500")


@pytest.mark.asyncio
async def test_threads_publishing_disabled_guard(tmp_path: Path):
    iface = ThreadsPublishingInterface(
        enabled=False,
        state_path=str(tmp_path / "state.json"),
        client=_FakeClient(),
    )
    with pytest.raises(ThreadsPublishingDisabledError):
        await iface.create_container({"media_type": "TEXT", "text": "hello", "approval_id": "appr-1"})


@pytest.mark.asyncio
async def test_threads_publishing_requires_manual_approval(tmp_path: Path):
    iface = ThreadsPublishingInterface(
        enabled=True,
        dry_run=True,
        approval_mode="manual_confirm",
        state_path=str(tmp_path / "state.json"),
        client=_FakeClient(),
    )
    with pytest.raises(ThreadsPublishingGovernanceError):
        await iface.create_container({"media_type": "TEXT", "text": "hello"})


@pytest.mark.asyncio
async def test_threads_publishing_autonomous_mode_does_not_require_approval(tmp_path: Path):
    audit_path = tmp_path / "audit.jsonl"
    iface = ThreadsPublishingInterface(
        enabled=True,
        dry_run=True,
        approval_mode="autonomous",
        max_daily_posts=2,
        state_path=str(tmp_path / "state.json"),
        client=_FakeClient(),
    )
    iface.governance.audit_path = audit_path
    out = await iface.create_container({"media_type": "TEXT", "text": "hello"})
    assert out["status"] == "dry_run"


@pytest.mark.asyncio
async def test_threads_publishing_dry_run_and_quota_state(tmp_path: Path):
    state_path = tmp_path / "state.json"
    audit_path = tmp_path / "audit.jsonl"
    iface = ThreadsPublishingInterface(
        enabled=True,
        dry_run=True,
        approval_mode="manual_confirm",
        max_daily_posts=2,
        max_daily_replies=1,
        state_path=str(state_path),
        source_config={},
        client=_FakeClient(),
    )
    iface.governance.audit_path = audit_path

    out = await iface.create_container({"media_type": "TEXT", "text": "hello", "approval_id": "appr-1"})
    assert out["status"] == "dry_run"
    assert out["daily_quota_committed"] is False
    assert not state_path.exists()
    assert audit_path.exists()

    await iface.create_container({"media_type": "TEXT", "text": "second", "approval_id": "appr-2"})
    await iface.create_container({"media_type": "TEXT", "text": "third", "approval_id": "appr-3"})


@pytest.mark.asyncio
async def test_threads_publishing_audit_includes_actor_and_reason(tmp_path: Path):
    state_path = tmp_path / "state.json"
    audit_path = tmp_path / "audit.jsonl"
    iface = ThreadsPublishingInterface(
        enabled=True,
        dry_run=True,
        approval_mode="manual_confirm",
        max_daily_posts=2,
        state_path=str(state_path),
        client=_FakeClient(),
    )
    iface.governance.audit_path = audit_path
    await iface.create_container(
        {
            "media_type": "TEXT",
            "text": "hello",
            "approval_id": "appr-audit-1",
            "audit_actor": "threads-rollout-bot",
            "audit_reason": "phase2 controlled canary",
        }
    )
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert lines
    row = json.loads(lines[-1])
    assert row.get("approval_ref") == "appr-audit-1"
    assert row.get("actor") == "threads-rollout-bot"
    assert row.get("reason") == "phase2 controlled canary"


@pytest.mark.asyncio
async def test_threads_publishing_live_reply_calls_client(tmp_path: Path):
    fake = _FakeClient()
    audit_path = tmp_path / "audit.jsonl"
    iface = ThreadsPublishingInterface(
        enabled=True,
        dry_run=False,
        approval_mode="manual_confirm",
        max_daily_replies=3,
        state_path=str(tmp_path / "state.json"),
        client=fake,
    )
    iface.governance.audit_path = audit_path
    out = await iface.reply_to_post(
        "18001234000000000",
        {"text": "thanks for the update", "approval_id": "appr-11"},
    )
    assert out["status"] == "ok"
    assert fake.create_calls
    assert fake.publish_calls
    create_payload = fake.create_calls[-1]
    assert create_payload["media_type"] == "TEXT"
    assert create_payload["reply_to_id"] == "18001234000000000"
    assert fake.status_calls
    assert audit_path.exists()


@pytest.mark.asyncio
async def test_threads_publishing_live_reply_waits_until_container_finished(tmp_path: Path):
    fake = _PollingClient()
    iface = ThreadsPublishingInterface(
        enabled=True,
        dry_run=False,
        approval_mode="manual_confirm",
        max_daily_replies=3,
        state_path=str(tmp_path / "state.json"),
        client=fake,
    )
    out = await iface.reply_to_post(
        "18001234000000000",
        {"text": "poll until ready", "approval_id": "appr-22"},
    )
    assert out["status"] == "ok"
    assert len(fake.status_calls) >= 3
    assert fake.publish_calls


@pytest.mark.asyncio
async def test_threads_publishing_live_error_does_not_consume_quota(tmp_path: Path):
    state_path = tmp_path / "state.json"
    iface = ThreadsPublishingInterface(
        enabled=True,
        dry_run=False,
        approval_mode="manual_confirm",
        max_daily_posts=1,
        state_path=str(state_path),
        client=_FailingClient(),
    )
    with pytest.raises(ThreadsPublishingGovernanceError):
        await iface.create_container({"media_type": "TEXT", "text": "hello", "approval_id": "appr-1"})
    # Failed live attempts should not burn the daily cap.
    assert not state_path.exists()
