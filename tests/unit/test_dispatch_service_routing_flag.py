"""_enrich_with_routing must skip the legacy Simone-first stamp when the
priority dispatcher is enabled, and keep stamping it when disabled (M2 / D3)."""

import pytest

from universal_agent.services import dispatch_service


@pytest.fixture(autouse=True)
def _clear_flag(monkeypatch):
    monkeypatch.delenv("UA_PRIORITY_DISPATCHER_ENABLED", raising=False)


def test_legacy_stamp_applied_when_dispatcher_off():
    claimed = [{"task_id": "t1"}]
    out = dispatch_service._enrich_with_routing(claimed)
    assert out is claimed
    assert claimed[0]["_routing"]["agent_id"] == "simone"
    assert claimed[0]["_routing"]["should_delegate"] is False


def test_legacy_stamp_skipped_when_dispatcher_on(monkeypatch):
    monkeypatch.setenv("UA_PRIORITY_DISPATCHER_ENABLED", "1")
    claimed = [{"task_id": "t1"}]
    out = dispatch_service._enrich_with_routing(claimed)
    assert out is claimed
    # Routing is owned by the priority dispatcher; no advisory stamp here.
    assert "_routing" not in claimed[0]


def test_empty_claimed_is_noop():
    assert dispatch_service._enrich_with_routing([]) == []
