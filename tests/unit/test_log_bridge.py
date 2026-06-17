"""Focused regression tests for universal_agent.utils.log_bridge.

These exercise the public surface of ``LogBridgeHandler`` and
``StdoutInterceptor`` (incl. the async ``create_task`` emit path) so the
type-hint annotations on ``emit``/``write``/``flush``/``__getattr__`` are
backed by observable behaviour. ``asyncio_mode = "auto"`` runs the async
cases without an explicit marker.

Note on buffering semantics: ``StdoutInterceptor.write`` *always* writes to
the original stream first and unconditionally; the internal ``_buffer`` only
gates when partial lines are forwarded to the event callback (it batches
fragments until a newline, then flush drains the remainder). These tests
pin that contract.
"""

from __future__ import annotations

import asyncio
import io
import logging

from universal_agent.agent_core import EventType
from universal_agent.utils.log_bridge import LogBridgeHandler, StdoutInterceptor


class _SentinelStream(io.StringIO):
    """StringIO exposing an extra attribute to exercise ``__getattr__`` proxying."""

    marker = 123


def _make_interceptor(captured: list, prefix: str = "stdout") -> StdoutInterceptor:
    async def _cb(event_type: EventType, payload: dict) -> None:
        captured.append((event_type, payload))

    return StdoutInterceptor(_cb, _SentinelStream(), prefix=prefix)


def test_write_proxies_to_original_stream() -> None:
    interceptor = _make_interceptor([])
    interceptor.write("hello world\n")
    assert "hello world" in interceptor.original_stream.getvalue()


def test_write_always_writes_to_original_stream_and_buffers_partial_lines() -> None:
    interceptor = _make_interceptor([])
    # No newline: the original stream is still written immediately and
    # unconditionally; only the event-emission path buffers the fragment.
    interceptor.write("buffered")
    assert interceptor.original_stream.getvalue() == "buffered"
    assert interceptor._buffer == "buffered"


def test_flush_drains_event_buffer() -> None:
    interceptor = _make_interceptor([])
    interceptor.write("buffered")
    assert interceptor._buffer == "buffered"
    interceptor.flush()
    assert interceptor._buffer == ""


def test_getattr_proxies_to_original_stream() -> None:
    interceptor = _make_interceptor([])
    # ``marker`` lives on the underlying stream, not on StdoutInterceptor,
    # so the lookup must fall through ``__getattr__``.
    assert interceptor.marker == 123
    assert interceptor.__getattr__("marker") == 123


def test_write_and_flush_return_none() -> None:
    """``write``/``flush`` are documented as returning ``None`` (no byte-count contract)."""
    interceptor = _make_interceptor([])
    assert interceptor.write("x\n") is None
    assert interceptor.flush() is None


async def test_write_invokes_callback_under_running_loop() -> None:
    captured: list = []
    interceptor = _make_interceptor(captured, prefix="Console")
    interceptor.write("line one\nline two\n")

    # The interceptor schedules the callback via loop.create_task and does
    # not await it; yield control until the scheduled tasks have run.
    for _ in range(50):
        if captured:
            break
        await asyncio.sleep(0.01)

    assert len(captured) == 2
    for event_type, payload in captured:
        assert event_type is EventType.STATUS
        assert payload["is_log"] is True
        assert payload["prefix"] == "Console"
        assert payload["level"] == "INFO"
    assert {captured[0][1]["status"], captured[1][1]["status"]} == {"line one", "line two"}


async def test_flush_emits_buffered_content_under_running_loop() -> None:
    captured: list = []
    interceptor = _make_interceptor(captured, prefix="Console")
    interceptor.write("partial")  # buffered (no newline) -> no callback yet
    interceptor.flush()  # drains the buffer -> one callback

    for _ in range(50):
        if captured:
            break
        await asyncio.sleep(0.01)

    assert len(captured) == 1
    event_type, payload = captured[0]
    assert event_type is EventType.STATUS
    assert payload["status"] == "partial"
    assert payload["prefix"] == "Console"


async def test_log_bridge_handler_emit_invokes_callback() -> None:
    captured: list = []

    async def _cb(event_type: EventType, payload: dict) -> None:
        captured.append((event_type, payload))

    handler = LogBridgeHandler(_cb)
    record = logging.LogRecord(
        name="test.logger",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="something happened",
        args=None,
        exc_info=None,
    )
    handler.emit(record)

    for _ in range(50):
        if captured:
            break
        await asyncio.sleep(0.01)

    assert len(captured) == 1
    event_type, payload = captured[0]
    assert event_type is EventType.STATUS
    assert payload["status"] == "something happened"
    assert payload["level"] == "WARNING"
    assert payload["prefix"] == "test.logger"
    assert payload["is_log"] is True


def test_emit_without_running_loop_does_not_raise() -> None:
    """Sync context: no running loop -> emit must swallow RuntimeError gracefully."""

    async def _cb(event_type: EventType, payload: dict) -> None:
        return None

    handler = LogBridgeHandler(_cb)
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="no loop here",
        args=None,
        exc_info=None,
    )
    # Should not raise even though there is no running event loop.
    handler.emit(record)
