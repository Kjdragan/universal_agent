"""Unit tests for ``universal_agent.utils.log_bridge``.

``LogBridgeHandler`` and ``StdoutInterceptor`` bridge Python stdlib logging
and raw stdout/stderr writes into the Agent event stream so subprocess output
(HTTPX requests, third-party prints) is visible in the frontend UI. They had
no dedicated behavioral coverage. These tests pin the load-bearing contracts:

* the handler forwards the ``%(message)s``-formatted record as a ``STATUS``
  event carrying ``level``/``prefix``/``is_log=True``;
* both emitters are no-ops (never raise) when there is no running event loop,
  and route unexpected failures through ``handleError`` / swallow-and-continue
  rather than crashing the writer;
* ``StdoutInterceptor`` always tees to the original stream, line-buffers
  fragmented writes (accumulating until a newline, emitting each complete
  non-blank line, and holding the trailing partial), flushes the remainder on
  ``flush()``, and proxies non-intercepted attributes back to the original
  stream.

All scheduling runs through ``loop.create_task`` (``asyncio_mode = "auto"``),
so the callback is only observed inside a running loop; each async test yields
once with ``asyncio.sleep(0)`` to let the scheduled coroutine run.
"""

from __future__ import annotations

import asyncio
import io
import logging

from universal_agent.agent_core import EventType
from universal_agent.utils.log_bridge import LogBridgeHandler, StdoutInterceptor


def _recording_callback(sink):
    async def _cb(event_type, payload):
        sink.append((event_type, payload))

    return _cb


def _record(name, level, msg, args=None):
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


class TestLogBridgeHandler:
    async def test_emit_schedules_status_event_with_expected_shape(self):
        sink = []
        handler = LogBridgeHandler(_recording_callback(sink))

        handler.emit(_record("test.log_bridge.shape", logging.WARNING, "hello world"))
        await asyncio.sleep(0)

        assert len(sink) == 1
        event_type, payload = sink[0]
        assert event_type is EventType.STATUS
        assert payload["status"] == "hello world"
        assert payload["level"] == "WARNING"
        assert payload["prefix"] == "test.log_bridge.shape"
        assert payload["is_log"] is True

    async def test_emit_forwards_only_the_formatted_message(self):
        # Handler installs a '%(message)s' formatter, so payload.status must
        # be the bare interpolated message -- not e.g. "LEVELNAME:message".
        sink = []
        handler = LogBridgeHandler(_recording_callback(sink))

        handler.emit(_record("test.log_bridge.fmt", logging.INFO, "count=%d", (3,)))
        await asyncio.sleep(0)

        assert sink[0][1]["status"] == "count=3"

    def test_emit_without_running_loop_does_not_raise(self):
        # Sync test -> no running loop -> asyncio.get_running_loop() raises
        # RuntimeError, which emit() must swallow rather than propagate to the
        # caller's logging/print path.
        handler = LogBridgeHandler(_recording_callback([]))
        # No assert needed: the call must simply not raise.
        handler.emit(_record("test.log_bridge.noloop", logging.INFO, "noop"))

    def test_emit_unexpected_exception_routes_to_handle_error(self, monkeypatch):
        handled = []
        handler = LogBridgeHandler(_recording_callback([]))

        def boom(_record):
            raise RuntimeError("format exploded")

        monkeypatch.setattr(handler, "format", boom)
        monkeypatch.setattr(handler, "handleError", lambda rec: handled.append(rec))

        record = _record("test.log_bridge.handle_error", logging.ERROR, "boom")
        handler.emit(record)

        assert handled == [record]


class TestStdoutInterceptor:
    @staticmethod
    def _make(prefix="stdout"):
        original = io.StringIO()
        sink = []
        interceptor = StdoutInterceptor(
            _recording_callback(sink), original, prefix=prefix
        )
        return interceptor, original, sink

    def test_write_tees_to_original_stream(self):
        interceptor, original, _ = self._make()
        interceptor.write("visible\n")
        # The original stream must always see the bytes, regardless of buffering.
        assert original.getvalue() == "visible\n"

    async def test_newline_write_emits_complete_lines_and_buffers_tail(self):
        interceptor, _, sink = self._make()
        interceptor.write("line1\nline2\npartial")
        await asyncio.sleep(0)

        assert [p["status"] for _, p in sink] == ["line1", "line2"]
        # Trailing partial segment is held back until a newline or flush.
        assert interceptor._buffer == "partial"

    async def test_fragmented_writes_accumulate_into_one_line(self):
        interceptor, _, sink = self._make()
        interceptor.write("par")
        interceptor.write("t-1")
        interceptor.write("\n")
        await asyncio.sleep(0)

        assert [p["status"] for _, p in sink] == ["part-1"]

    async def test_blank_lines_are_not_emitted(self):
        interceptor, _, sink = self._make()
        # The writer skips parts that are empty/whitespace-only. Blank lines
        # for formatting still reach the original stream (teed above) but do
        # not generate noisy STATUS events.
        interceptor.write("\n\nreal\n")
        await asyncio.sleep(0)

        assert [p["status"] for _, p in sink] == ["real"]

    async def test_flush_emits_and_clears_remaining_buffer(self):
        interceptor, _, sink = self._make()
        interceptor.write("dangling")
        await asyncio.sleep(0)
        assert [p["status"] for _, p in sink] == []  # nothing emitted yet

        interceptor.flush()
        await asyncio.sleep(0)

        assert [p["status"] for _, p in sink] == ["dangling"]
        assert interceptor._buffer == ""

    async def test_flush_is_a_noop_when_buffer_is_empty(self):
        interceptor, _, sink = self._make()
        interceptor.write("done\n")
        await asyncio.sleep(0)
        assert [p["status"] for _, p in sink] == ["done"]

        interceptor.flush()
        await asyncio.sleep(0)
        # No spurious empty-string payload from flushing an empty buffer.
        assert [p["status"] for _, p in sink] == ["done"]

    async def test_emit_payload_carries_prefix_and_is_log_flag(self):
        interceptor, _, sink = self._make(prefix="stderr")
        interceptor.write("boom\n")
        await asyncio.sleep(0)

        _, payload = sink[0]
        assert payload["prefix"] == "stderr"
        assert payload["level"] == "INFO"
        assert payload["is_log"] is True
        assert payload["status"] == "boom"

    def test_getattr_proxies_non_intercepted_attrs_to_original(self):
        # write/flush/_buffer are real interceptor attrs; everything else
        # (isatty, closed, ...) must resolve via __getattr__ to the underlying
        # stream so the interceptor is a transparent stand-in for stdout.
        interceptor, original, _ = self._make()
        assert interceptor.isatty() == original.isatty()
