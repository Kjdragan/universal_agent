"""The seen-message claim must dedupe correctly AND keep its blocking DB
persist off the event loop.

`_claim_seen_message_id` previously ran a `time.sleep`-based sqlite lock-retry
loop synchronously inside async inbound-email handlers, stalling the shared
gateway event loop for up to ~3s. It was split into a fast in-memory claim
(`_claim_seen_message_id_in_memory`) plus a blocking persist
(`_persist_seen_message_id`) offloaded via `asyncio.to_thread` in
`_claim_seen_message_id_async`. These tests pin the dedup semantics (unchanged)
and prove the async path persists + offloads.
"""

import asyncio

import pytest

from universal_agent.services.agentmail_service import AgentMailService


@pytest.fixture
def svc(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(tmp_path / "activity_state.db"))
    s = AgentMailService()
    s._ensure_queue_schema()
    return s


def test_in_memory_claim_dedupes(svc):
    assert svc._claim_seen_message_id_in_memory("m-1") == "m-1"
    # Second claim of the same id is a no-op (already seen).
    assert svc._claim_seen_message_id_in_memory("m-1") is None
    # Empty ids are never claimed.
    assert svc._claim_seen_message_id_in_memory("") is None
    assert svc._claim_seen_message_id_in_memory("   ") is None


def test_sync_claim_persists_and_dedupes(svc):
    assert svc._claim_seen_message_id("m-2") is True
    assert svc._claim_seen_message_id("m-2") is False
    with svc._queue_connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM agentmail_seen_messages WHERE message_id=?",
            ("m-2",),
        ).fetchone()
    assert row["n"] == 1


def test_async_claim_offloads_and_persists(svc, monkeypatch):
    calls = {"to_thread": 0}
    real_to_thread = asyncio.to_thread

    async def _tracking_to_thread(func, /, *args, **kwargs):
        calls["to_thread"] += 1
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _tracking_to_thread)

    async def _run():
        first = await svc._claim_seen_message_id_async("m-3")
        second = await svc._claim_seen_message_id_async("m-3")
        return first, second

    first, second = asyncio.run(_run())
    assert first is True
    assert second is False
    # First (new) claim offloaded the blocking persist; the deduped second claim
    # short-circuits in memory without touching a thread.
    assert calls["to_thread"] == 1
    with svc._queue_connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM agentmail_seen_messages WHERE message_id=?",
            ("m-3",),
        ).fetchone()
    assert row["n"] == 1
