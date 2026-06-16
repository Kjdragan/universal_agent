"""_enrich_with_routing must skip the legacy Simone-first stamp when the priority
dispatcher is enabled, and keep stamping it only when explicitly disabled.

As of 2026-06-16 the dispatcher is DEFAULT-ON, so:
  - unset / default            -> dispatcher ON  -> no legacy stamp
  - explicit 1/true/on         -> dispatcher ON  -> no legacy stamp
  - explicit 0/false/no/off    -> dispatcher OFF -> legacy Simone-first stamp (kill switch)
"""

import pytest

from universal_agent.services import dispatch_service, priority_dispatcher as pd


@pytest.fixture(autouse=True)
def _clear_flag(monkeypatch):
    monkeypatch.delenv("UA_PRIORITY_DISPATCHER_ENABLED", raising=False)


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, True),       # unset -> default ON
        ("1", True), ("true", True), ("on", True), ("yes", True),
        ("0", False), ("false", False), ("no", False), ("off", False),  # kill switch
        ("garbage", True),  # unrecognized -> default ON
    ],
)
def test_priority_dispatcher_enabled_tristate(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("UA_PRIORITY_DISPATCHER_ENABLED", raising=False)
    else:
        monkeypatch.setenv("UA_PRIORITY_DISPATCHER_ENABLED", value)
    assert pd.priority_dispatcher_enabled() is expected


def test_legacy_stamp_applied_when_dispatcher_explicitly_off(monkeypatch):
    monkeypatch.setenv("UA_PRIORITY_DISPATCHER_ENABLED", "0")  # kill switch
    claimed = [{"task_id": "t1"}]
    out = dispatch_service._enrich_with_routing(claimed)
    assert out is claimed
    assert claimed[0]["_routing"]["agent_id"] == "simone"
    assert claimed[0]["_routing"]["should_delegate"] is False


def test_legacy_stamp_skipped_when_dispatcher_default_on():
    # Flag unset -> dispatcher is ON by default -> no advisory stamp.
    claimed = [{"task_id": "t1"}]
    out = dispatch_service._enrich_with_routing(claimed)
    assert out is claimed
    assert "_routing" not in claimed[0]


def test_legacy_stamp_skipped_when_dispatcher_explicitly_on(monkeypatch):
    monkeypatch.setenv("UA_PRIORITY_DISPATCHER_ENABLED", "1")
    claimed = [{"task_id": "t1"}]
    out = dispatch_service._enrich_with_routing(claimed)
    assert out is claimed
    assert "_routing" not in claimed[0]


def test_empty_claimed_is_noop():
    assert dispatch_service._enrich_with_routing([]) == []
