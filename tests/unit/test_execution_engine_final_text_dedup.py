"""Regression guard for the T5 "alert bodies emailed twice" fix.

``ExecutionEngineAdapter.execute`` always re-emits the complete final
response as a ``TEXT`` event shaped ``data={"text": ..., "final": True}`` —
a fallback for consumers that don't process incremental streaming text (see
the tail of :meth:`ExecutionEngineAdapter.execute`). When streaming text was
already delivered for the turn (via ``hooks.emit_text_event()``, which
stamps a ``time_offset`` key on each TEXT event), that final re-emission is
a verbatim repeat and must be dropped.

``gateway_server.py`` implemented this dedup inline; ``hooks_service.py``'s
``_consume_gateway_execute`` did not, so the last-8-text-events tail it
builds (persisted as ``execution_summary["response_preview"]`` and read
downstream by ``services/agentmail_service.py`` when composing an
email/alert body) contained the full response text twice.

``execution_engine.is_redundant_final_text`` is the extracted, single
source of truth for the predicate both consumers must apply. These tests
exercise it directly, independent of either consumer.
"""

from universal_agent.agent_core import AgentEvent, EventType
from universal_agent.execution_engine import is_redundant_final_text


def _streaming_text_event(text: str, time_offset: float = 1.0) -> AgentEvent:
    """A TEXT event as emitted incrementally via hooks.emit_text_event()."""
    return AgentEvent(type=EventType.TEXT, data={"text": text, "time_offset": time_offset})


def _final_text_event(text: str) -> AgentEvent:
    """The non-streaming fallback re-emission from execution_engine.execute()."""
    return AgentEvent(type=EventType.TEXT, data={"text": text, "final": True})


def test_final_event_is_redundant_when_streaming_was_seen():
    event = _final_text_event("full response")
    assert is_redundant_final_text(event, saw_streaming_text=True) is True


def test_final_event_is_not_redundant_when_no_streaming_was_seen():
    """No streaming text seen (e.g. a non-SDK/non-streaming turn) → the final
    event is the ONLY copy of the response and must be kept."""
    event = _final_text_event("full response")
    assert is_redundant_final_text(event, saw_streaming_text=False) is False


def test_streaming_event_itself_is_never_redundant():
    event = _streaming_text_event("partial chunk")
    assert is_redundant_final_text(event, saw_streaming_text=True) is False
    assert is_redundant_final_text(event, saw_streaming_text=False) is False


def test_final_flag_alone_without_final_true_value_is_not_redundant():
    """Only an exact ``final is True`` marks the fallback re-emission —
    a falsy/missing/truthy-but-not-True value must not match."""
    event = AgentEvent(type=EventType.TEXT, data={"text": "x", "final": False})
    assert is_redundant_final_text(event, saw_streaming_text=True) is False

    event_missing = AgentEvent(type=EventType.TEXT, data={"text": "x"})
    assert is_redundant_final_text(event_missing, saw_streaming_text=True) is False


def test_non_text_event_is_never_redundant():
    event = AgentEvent(type=EventType.STATUS, data={"final": True})
    assert is_redundant_final_text(event, saw_streaming_text=True) is False


def test_non_dict_data_is_never_redundant():
    event = AgentEvent(type=EventType.TEXT, data={})
    # data is a dict but empty — final is falsy, so not redundant.
    assert is_redundant_final_text(event, saw_streaming_text=True) is False
