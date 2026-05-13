"""Hermes-F site-wiring timing instrumentation unit tests.

Covers the ``_phase_f_start`` / ``_phase_f_done`` helpers added to
``cron_service.py`` to leave breadcrumbs in the journal so a future
event-loop freeze can be diagnosed without operator intervention.

Background: the 2026-05-12 → 2026-05-13 gateway-freeze incident left
operators with a 20.8h silence and no information about which exact
synchronous SQL call deadlocked the asyncio event loop. The
instrumentation here logs an entry marker (DEBUG) before each sync
call and an exit marker (DEBUG/INFO/WARNING based on elapsed) after.
A future freeze leaves an "entering X" log with no matching "took N ms"
log — pointing directly at X as the freeze site.

See plans/2026-05-13_proactivity_gap_findings.md.
"""

from __future__ import annotations

import logging
import time
from unittest import mock

import pytest

from universal_agent.cron_service import (
    _PHASE_F_INFO_MS,
    _PHASE_F_WARN_MS,
    _phase_f_done,
    _phase_f_start,
)

# ── _phase_f_start ──────────────────────────────────────────────────────────


class TestPhaseFStart:
    def test_returns_perf_counter_float(self) -> None:
        """Returns a monotonic timestamp that ``_phase_f_done`` can use."""
        before = time.perf_counter()
        result = _phase_f_start("job-abc", "open_conn")
        after = time.perf_counter()
        assert isinstance(result, float)
        # The returned value must be from the same clock that
        # _phase_f_done will sample. Sanity-check it falls in the
        # before/after window (allowing a generous margin for jitter).
        assert before - 0.01 <= result <= after + 0.01

    def test_logs_entry_at_debug_level(self, caplog: pytest.LogCaptureFixture) -> None:
        """Entry log is at DEBUG so the journal at INFO+ doesn't get flooded."""
        with caplog.at_level(logging.DEBUG, logger="universal_agent.cron_service"):
            _phase_f_start("job-xyz", "ensure_link")
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert len(debug_records) == 1
        msg = debug_records[0].getMessage()
        assert "job-xyz" in msg
        assert "ensure_link" in msg
        assert "entering" in msg.lower()

    def test_entry_log_not_emitted_at_info_level(self, caplog: pytest.LogCaptureFixture) -> None:
        """When logging level is INFO, entry log is suppressed (DEBUG-only)."""
        with caplog.at_level(logging.INFO, logger="universal_agent.cron_service"):
            _phase_f_start("job-info", "open_conn")
        info_records = [r for r in caplog.records if r.levelno >= logging.INFO]
        # The entry-log is DEBUG, so nothing at INFO+ should fire here.
        assert info_records == []


# ── _phase_f_done — fast path (DEBUG only) ──────────────────────────────────


class TestPhaseFDoneFastPath:
    def test_fast_call_logs_at_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        """A <500ms call only emits a DEBUG log — no INFO/WARNING noise."""
        with caplog.at_level(logging.DEBUG, logger="universal_agent.cron_service"):
            t0 = time.perf_counter()
            _phase_f_done("job-fast", "open_conn", t0)
        # Should have exactly one log record, at DEBUG.
        debug_recs = [r for r in caplog.records if r.levelno == logging.DEBUG]
        higher_recs = [r for r in caplog.records if r.levelno > logging.DEBUG]
        assert len(debug_recs) == 1
        assert higher_recs == []
        msg = debug_recs[0].getMessage()
        assert "job-fast" in msg
        assert "open_conn" in msg
        assert "took" in msg

    def test_fast_call_suppressed_at_info_level(self, caplog: pytest.LogCaptureFixture) -> None:
        """At INFO level, a fast call produces no log noise at all."""
        with caplog.at_level(logging.INFO, logger="universal_agent.cron_service"):
            t0 = time.perf_counter()
            _phase_f_done("job-quiet", "open_conn", t0)
        assert caplog.records == []


# ── _phase_f_done — slow paths (INFO and WARNING) ───────────────────────────


class TestPhaseFDoneSlowPath:
    def test_slow_call_logs_at_info(self, caplog: pytest.LogCaptureFixture) -> None:
        """A 500ms-5s call escalates to INFO so it's visible in production logs."""
        # Synthesize a "took 600ms" outcome by faking perf_counter on exit.
        # ``_phase_f_done`` reads ``time.perf_counter()`` once at entry; we
        # patch it to return a value 0.6s after t0 so the elapsed math
        # is deterministic regardless of test runtime.
        t0 = 100.0
        with mock.patch("universal_agent.cron_service.time.perf_counter", return_value=t0 + 0.600):
            with caplog.at_level(logging.INFO, logger="universal_agent.cron_service"):
                _phase_f_done("job-slow", "ensure_link", t0)
        info_recs = [r for r in caplog.records if r.levelno == logging.INFO]
        warn_recs = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(info_recs) == 1
        assert warn_recs == []
        msg = info_recs[0].getMessage()
        assert "job-slow" in msg
        assert "ensure_link" in msg
        # The elapsed is rendered as an integer-ms in INFO path; just
        # verify the rough number is in the message.
        assert "600" in msg

    def test_very_slow_call_logs_at_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A >5s call escalates to WARNING — the "possible deadlock" signal."""
        t0 = 200.0
        with mock.patch("universal_agent.cron_service.time.perf_counter", return_value=t0 + 7.0):
            with caplog.at_level(logging.WARNING, logger="universal_agent.cron_service"):
                _phase_f_done("job-deadlock", "ensure_link", t0)
        warn_recs = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warn_recs) == 1
        msg = warn_recs[0].getMessage()
        assert "job-deadlock" in msg
        assert "ensure_link" in msg
        assert "7000" in msg  # 7.0s = 7000ms
        # The warning message includes the "possible deadlock" phrase so
        # log-scraping tools can grep for it specifically.
        assert "deadlock" in msg.lower()

    def test_threshold_boundary_exactly_500ms_is_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        """500.0ms is NOT > 500ms (strict greater-than), so it stays DEBUG."""
        t0 = 0.0
        with mock.patch("universal_agent.cron_service.time.perf_counter", return_value=0.500):
            with caplog.at_level(logging.DEBUG, logger="universal_agent.cron_service"):
                _phase_f_done("job-edge", "open_conn", t0)
        info_recs = [r for r in caplog.records if r.levelno >= logging.INFO]
        assert info_recs == []

    def test_threshold_boundary_just_over_500ms_is_info(self, caplog: pytest.LogCaptureFixture) -> None:
        """500.001ms IS > 500ms, so it escalates to INFO."""
        t0 = 0.0
        with mock.patch("universal_agent.cron_service.time.perf_counter", return_value=0.5001):
            with caplog.at_level(logging.INFO, logger="universal_agent.cron_service"):
                _phase_f_done("job-edge2", "open_conn", t0)
        info_recs = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(info_recs) == 1

    def test_threshold_boundary_exactly_5s_is_info_not_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """5000.0ms is NOT > 5000ms, so it stays at INFO."""
        t0 = 0.0
        with mock.patch("universal_agent.cron_service.time.perf_counter", return_value=5.0):
            with caplog.at_level(logging.INFO, logger="universal_agent.cron_service"):
                _phase_f_done("job-edge3", "open_conn", t0)
        warn_recs = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warn_recs == []
        # And there IS an INFO record (since 5000.0 > 500.0).
        info_recs = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(info_recs) == 1


# ── Module-level constants ──────────────────────────────────────────────────


def test_threshold_constants_are_floats_in_ms() -> None:
    """Constants are floats so the comparison in _phase_f_done is unambiguous.

    Guards against a future refactor that introduces ints (which can
    surprise the boundary tests above).
    """
    assert isinstance(_PHASE_F_INFO_MS, float)
    assert isinstance(_PHASE_F_WARN_MS, float)
    assert _PHASE_F_INFO_MS < _PHASE_F_WARN_MS


def test_warn_threshold_at_least_5s() -> None:
    """The 5-second WARNING threshold is the operationally meaningful one.

    Any sync SQL call taking >5s inside an async coroutine is almost
    certainly a deadlock candidate. Tightening below 5s would risk
    operator alert fatigue from cold-cache spikes.
    """
    assert _PHASE_F_WARN_MS >= 5000.0
