"""Regression guard for T5: alert/email bodies built from
``HooksService._consume_gateway_execute``'s text tail were doubled verbatim.

Root cause (proven against a stored ``full_message`` that contained
``EMAIL TRIAGE BRIEF`` twice, 3,693 chars): ``execution_engine.py`` always
re-emits the complete final response as a ``TEXT`` event shaped
``data={"final": True}`` for non-streaming consumers. ``gateway_server.py``
already filtered that re-emission out when streaming text had been seen for
the turn; ``hooks_service.py::_consume_gateway_execute`` did not — it
appended every ``text`` event's text into ``text_tail`` unconditionally, so
the same content landed twice in ``execution_summary["response_preview"]``,
which ``services/agentmail_service.py`` reads verbatim when composing an
alert/triage email body.

The fix threads the same ``execution_engine.is_redundant_final_text``
predicate (already covered directly in
``test_execution_engine_final_text_dedup.py``) through
``_consume_gateway_execute``, tracking a local ``saw_streaming_text`` flag
exactly as ``gateway_server.py`` does.
"""

from unittest.mock import MagicMock

import pytest

from universal_agent.agent_core import AgentEvent, EventType
from universal_agent.gateway import InProcessGateway
from universal_agent.hooks_service import HooksService


def _streaming_text_event(text: str, time_offset: float) -> AgentEvent:
    return AgentEvent(type=EventType.TEXT, data={"text": text, "time_offset": time_offset})


def _final_text_event(text: str) -> AgentEvent:
    return AgentEvent(type=EventType.TEXT, data={"text": text, "final": True})


def _iteration_end_event(duration_seconds: float = 1.0) -> AgentEvent:
    return AgentEvent(
        type=EventType.ITERATION_END,
        data={"status": "complete", "duration_seconds": duration_seconds, "tool_calls": 0},
    )


def _build_service(events: list[AgentEvent]) -> HooksService:
    """A HooksService wired to a fake gateway that replays ``events``."""
    gateway = MagicMock(spec=InProcessGateway)

    async def async_gen(*_args, **_kwargs):
        for event in events:
            yield event

    gateway.execute.side_effect = async_gen
    return HooksService(gateway)


@pytest.mark.parametrize("brief_text", ["EMAIL TRIAGE BRIEF\nSubject: test\nBody: hello"])
async def test_streaming_plus_final_reemission_yields_text_exactly_once(brief_text):
    """Streamed text + the fallback final=True re-emission of the SAME text
    must collapse to a single copy in the returned summary."""
    events = [
        _streaming_text_event(brief_text, time_offset=0.5),
        _iteration_end_event(),
        _final_text_event(brief_text),
    ]
    service = _build_service(events)

    summary = await service._consume_gateway_execute(session=None, request=None, workspace_root=None)

    preview = summary.get("response_preview", "")
    assert preview.count("EMAIL TRIAGE BRIEF") == 1, (
        f"expected the brief to appear exactly once, got: {preview!r}"
    )
    assert preview == brief_text


async def test_no_streaming_text_keeps_the_final_event():
    """When no streaming text was ever seen (e.g. a non-SDK / fully
    non-streaming turn), the final=True event is the ONLY copy of the
    response and must NOT be dropped."""
    brief_text = "EMAIL TRIAGE BRIEF\nSubject: test\nBody: hello"
    events = [
        _iteration_end_event(),
        _final_text_event(brief_text),
    ]
    service = _build_service(events)

    summary = await service._consume_gateway_execute(session=None, request=None, workspace_root=None)

    preview = summary.get("response_preview", "")
    assert preview.count("EMAIL TRIAGE BRIEF") == 1
    assert preview == brief_text


async def test_multiple_streaming_chunks_plus_final_reemission_dedup():
    """A realistic multi-chunk stream: several incremental TEXT events whose
    concatenation equals the final re-emission's text — only the streamed
    chunks should survive."""
    chunks = ["EMAIL TRIAGE BRIEF\n", "Subject: test\n", "Body: hello"]
    full_text = "".join(chunks)
    events = [_streaming_text_event(c, time_offset=i * 0.1) for i, c in enumerate(chunks, start=1)]
    events.append(_iteration_end_event())
    events.append(_final_text_event(full_text))
    service = _build_service(events)

    summary = await service._consume_gateway_execute(session=None, request=None, workspace_root=None)

    preview = summary.get("response_preview", "")
    assert preview.count("EMAIL TRIAGE BRIEF") == 1
    # Each streamed chunk is stripped before being appended, then the tail
    # is re-joined with "\n" — the 3 streamed chunks survive (stripped),
    # the redundant final event is dropped.
    assert preview == "\n".join(c.strip() for c in chunks)
