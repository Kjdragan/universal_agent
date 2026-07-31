"""VP-coder finalize-step crash diagnosability — ``work_done_finalize_failed``.

Guards the diagnosability fix for the recurring class where a ``vp.coder.primary``
mission crashes in finalize/finish AFTER the real work (apply-edits) ran, and the
mission record used to collapse to an opaque ``message="Unknown error"`` with
``final_text=""`` and ``trace_id=null``. The fix captures a recoverable
``work_done_finalize_failed`` disposition with structured context instead.

Covers:
* ``vp.finalize_failure_context`` — snapshot capture + work-done detection
  (positive + negative) + payload assembly.
* ``consume_adapter_events_with_idle_timeout`` — trace_id captured from the
  ERROR event, final_text salvaged from streamed text on the failure path, and
  the ``error_context`` sink populated.
* ``worker_loop._classify_outcome_failure_mode`` — recognizes the disposition
  before the generic ``vp_self_reported`` fallback.
* ``ClaudeCodeClient.run_mission`` — end-to-end: a finalize crash after work
  yields the recoverable disposition (positive); a crash with NO work does not
  (negative/contrast).
"""
from __future__ import annotations

import asyncio
import types

import pytest

from universal_agent.agent_core import EventType
from universal_agent.vp.clients.base import consume_adapter_events_with_idle_timeout
from universal_agent.vp.clients.claude_code_client import ClaudeCodeClient
from universal_agent.vp.finalize_failure_context import (
    DISPOSITION_WORK_DONE_FINALIZE_FAILED,
    WorkSnapshot,
    build_work_done_finalize_failed_payload,
    capture_work_snapshot,
    maybe_work_done_finalize_failed_payload,
    work_was_done,
)

# ───────────────────────── helpers ──────────────────────────


class _Ev:
    def __init__(self, type_, data):
        self.type = type_
        self.data = data


class _FakeAdapter:
    """Minimal ProcessTurnAdapter stand-in: yields canned events, then closes."""

    def __init__(self, events):
        self._events = events
        self.config = types.SimpleNamespace()

    async def initialize(self):  # noqa: ARG002
        return None

    async def execute(self, prompt):  # noqa: ARG002
        for ev in self._events:
            yield ev

    async def close(self):  # noqa: ARG002
        return None


def _seed_work_done_workspace(workspace) -> None:
    """Populate ``workspace`` with evidence the apply ran + the agent self-marked."""
    (workspace / "apply_typehints.py").write_text("# apply script ran", encoding="utf-8")
    (workspace / "fail_with_edits.txt").write_text("", encoding="utf-8")


# ───────────────── finalize_failure_context unit tests ─────────────


def test_work_was_done_apply_scripts_plus_fail_marker(tmp_path):
    _seed_work_done_workspace(tmp_path)
    snap = capture_work_snapshot(tmp_path)
    assert snap.apply_scripts == ["apply_typehints.py"]
    assert snap.fail_with_edits_marker is True
    assert work_was_done(snap) is True


def test_work_was_done_work_products(tmp_path):
    (tmp_path / "work_products").mkdir()
    (tmp_path / "work_products" / "report.md").write_text("x", encoding="utf-8")
    snap = capture_work_snapshot(tmp_path)
    assert work_was_done(snap) is True


def test_work_was_done_negative_empty_workspace(tmp_path):
    # A startup-crash workspace with no evidence of work must NOT be recoverable.
    snap = capture_work_snapshot(tmp_path)
    assert snap.apply_scripts == []
    assert snap.work_product_files == []
    assert snap.fail_with_edits_marker is False
    assert work_was_done(snap) is False


def test_work_was_done_negative_apply_script_alone_is_not_enough(tmp_path):
    # An apply-script present but no fail-marker / git change / work-products
    # is ambiguous — do not over-claim recoverable work.
    (tmp_path / "apply_edits.py").write_text("# present but no evidence it ran", encoding="utf-8")
    snap = capture_work_snapshot(tmp_path)
    assert work_was_done(snap) is False


def test_build_payload_carries_structured_context(tmp_path):
    _seed_work_done_workspace(tmp_path)
    payload = build_work_done_finalize_failed_payload(
        workspace_dir=tmp_path,
        final_text="partial streamed summary",
        trace_id="abc-trace",
        error_message="VP run ended without a result",
        error_detail="Traceback (most recent call last): ...",
        log_tail="tail line",
    )
    assert payload["disposition"] == DISPOSITION_WORK_DONE_FINALIZE_FAILED
    assert payload["recoverable"] is True
    assert payload["final_text"] == "partial streamed summary"
    assert payload["trace_id"] == "abc-trace"
    assert payload["error_context"]["message"] == "VP run ended without a result"
    assert payload["error_context"]["detail"].startswith("Traceback")
    assert payload["error_context"]["log_tail"] == "tail line"
    snap = payload["work_snapshot"]
    assert snap["apply_scripts"] == ["apply_typehints.py"]
    assert snap["fail_with_edits_marker"] is True


def test_maybe_returns_none_when_no_work(tmp_path):
    # Negative: empty workspace → None, so the caller keeps its original payload.
    out = maybe_work_done_finalize_failed_payload(
        workspace_dir=tmp_path,
        final_text="",
        trace_id=None,
        error_message="boom",
    )
    assert out is None


def test_maybe_preserves_prior_sdk_bookkeeping(tmp_path):
    _seed_work_done_workspace(tmp_path)
    out = maybe_work_done_finalize_failed_payload(
        workspace_dir=tmp_path,
        final_text="x",
        trace_id="t",
        error_message="boom",
        prior_payload={"sdk_consecutive_timeouts": 3, "sdk_parked_for_review": True},
    )
    assert out is not None
    assert out["disposition"] == DISPOSITION_WORK_DONE_FINALIZE_FAILED
    assert out["sdk_consecutive_timeouts"] == 3
    assert out["sdk_parked_for_review"] is True


def test_snapshot_never_raises_on_bad_workspace(tmp_path):
    # A snapshot failure must never mask the original finalize crash.
    missing = tmp_path / "does-not-exist"
    snap = capture_work_snapshot(missing)
    assert isinstance(snap, WorkSnapshot)
    assert work_was_done(snap) is False


# ─────────── consume_adapter_events_with_idle_timeout ───────────


def test_trace_id_captured_from_error_event():
    # ITERATION_END (the usual trace_id carrier) only fires on success; the
    # engine now puts trace_id on the ERROR event too — capture it.
    events = [_Ev(EventType.ERROR, {"message": "finalize crash", "trace_id": "err-trace"})]
    adapter = _FakeAdapter(events)
    _final, error_text, trace_id = asyncio.run(
        consume_adapter_events_with_idle_timeout(adapter, "go", idle_timeout_seconds=5)
    )
    assert error_text == "finalize crash"
    assert trace_id == "err-trace"


def test_final_text_salvaged_from_streamed_text_on_failure():
    # The agent streamed its response, then finalize crashed before the
    # final=True marker. The failure path must salvage the streamed text
    # instead of recording an empty final_text.
    events = [
        _Ev(EventType.TEXT, {"text": "I applied the type hints and ran ruff."}),
        _Ev(EventType.ERROR, {"message": "finalize crash", "trace_id": "t1"}),
    ]
    adapter = _FakeAdapter(events)
    final_text, error_text, _trace_id = asyncio.run(
        consume_adapter_events_with_idle_timeout(adapter, "go", idle_timeout_seconds=5)
    )
    assert error_text == "finalize crash"
    assert final_text == "I applied the type hints and ran ruff."


def test_streamed_text_not_used_on_success_path():
    # Behavior-preserving on success: no final marker + no error → final_text
    # stays "" (the zero-output success path is unchanged).
    events = [_Ev(EventType.TEXT, {"text": "streamed but not final"})]
    adapter = _FakeAdapter(events)
    final_text, error_text, _trace_id = asyncio.run(
        consume_adapter_events_with_idle_timeout(adapter, "go", idle_timeout_seconds=5)
    )
    assert final_text == ""
    assert error_text is None


def test_error_context_sink_populated():
    events = [_Ev(EventType.ERROR, {"message": "boom", "detail": "det", "log_tail": "lt", "trace_id": "tr"})]
    adapter = _FakeAdapter(events)
    sink: dict = {}
    asyncio.run(
        consume_adapter_events_with_idle_timeout(
            adapter, "go", idle_timeout_seconds=5, error_context=sink
        )
    )
    assert sink["message"] == "boom"
    assert sink["detail"] == "det"
    assert sink["log_tail"] == "lt"
    assert sink["trace_id"] == "tr"


def test_final_marker_still_wins():
    # Regression guard: an explicit final=True marker is still authoritative.
    events = [
        _Ev(EventType.TEXT, {"text": "streamed partial"}),
        _Ev(EventType.TEXT, {"final": True, "text": "final answer"}),
    ]
    adapter = _FakeAdapter(events)
    final_text, error_text, _trace_id = asyncio.run(
        consume_adapter_events_with_idle_timeout(adapter, "go", idle_timeout_seconds=5)
    )
    assert final_text == "final answer"
    assert error_text is None


# ───────────────── classifier recognition ─────────────────


def test_classifier_recognizes_work_done_disposition():
    from universal_agent.vp.worker_loop import _classify_outcome_failure_mode

    outcome = types.SimpleNamespace(
        status="failed",
        message="VP run ended without a result",
        payload={
            "disposition": DISPOSITION_WORK_DONE_FINALIZE_FAILED,
            "final_text": "",
            "work_snapshot": {"apply_scripts": ["apply_x.py"]},
        },
    )
    assert _classify_outcome_failure_mode(outcome) == "work_done_finalize_failed"


def test_classifier_falls_back_when_no_work_done():
    from universal_agent.vp.worker_loop import _classify_outcome_failure_mode

    # Same opaque message, but NO disposition/work_snapshot → generic fallback
    # (the old, pre-fix behavior) so an empty-workspace crash is NOT mislabeled
    # recoverable.
    outcome = types.SimpleNamespace(
        status="failed",
        message="Unknown error",
        payload={"trace_id": None, "final_text": ""},
    )
    assert _classify_outcome_failure_mode(outcome) == "vp_self_reported"


# ──────────── ClaudeCodeClient.run_mission end-to-end ────────────


def _mission(workspace_dir, mission_id="vp-mission-test"):
    import json

    return {
        "mission_id": mission_id,
        "objective": "add type hints",
        "payload_json": json.dumps({"constraints": {"workspace_dir": str(workspace_dir)}}),
    }


def test_run_mission_finalize_crash_after_work_records_disposition(tmp_path, monkeypatch):
    """POSITIVE: finalize crashes AFTER the apply ran → the outcome carries the
    recoverable work_done_finalize_failed disposition WITH salvaged final_text,
    propagated trace_id, and a work_snapshot — NOT a bare empty/null record."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_work_done_workspace(workspace)

    # Simulate the finalize-step crash: streamed text, then an ERROR event
    # carrying the gateway trace_id (no ITERATION_END, no final marker).
    crash_events = [
        _Ev(EventType.TEXT, {"text": "Applied type hints; ruff + pytest green."}),
        _Ev(EventType.ERROR, {"message": "VP run ended without a result", "trace_id": "gw-trace-7"}),
    ]

    def _fake_adapter_factory(config):  # noqa: ARG001
        return _FakeAdapter(crash_events)

    monkeypatch.setattr(
        "universal_agent.vp.clients.claude_code_client.ProcessTurnAdapter",
        _fake_adapter_factory,
    )

    outcome = asyncio.run(ClaudeCodeClient().run_mission(mission=_mission(workspace), workspace_root=tmp_path))

    assert outcome.status == "failed"
    payload = outcome.payload
    assert payload["disposition"] == DISPOSITION_WORK_DONE_FINALIZE_FAILED
    assert payload["recoverable"] is True
    # final_text salvaged from the streamed assistant text (was "" before the fix).
    assert payload["final_text"] == "Applied type hints; ruff + pytest green."
    # trace_id propagated from the ERROR event (was None before the fix).
    assert payload["trace_id"] == "gw-trace-7"
    # structured error context + work-snapshot captured
    assert payload["error_context"]["message"] == "VP run ended without a result"
    snap = payload["work_snapshot"]
    assert snap["apply_scripts"] == ["apply_typehints.py"]
    assert snap["fail_with_edits_marker"] is True


def test_run_mission_finalize_crash_no_work_is_not_recoverable(tmp_path, monkeypatch):
    """NEGATIVE/CONTRAST: a crash where NO work ran (empty workspace) must NOT
    be marked work_done_finalize_failed — it stays a normal failure."""
    workspace = tmp_path / "ws-empty"
    workspace.mkdir()  # no apply scripts, no work products, no marker

    crash_events = [
        _Ev(EventType.ERROR, {"message": "VP run ended without a result", "trace_id": "t"}),
    ]

    def _fake_adapter_factory(config):  # noqa: ARG001
        return _FakeAdapter(crash_events)

    monkeypatch.setattr(
        "universal_agent.vp.clients.claude_code_client.ProcessTurnAdapter",
        _fake_adapter_factory,
    )

    outcome = asyncio.run(ClaudeCodeClient().run_mission(mission=_mission(workspace), workspace_root=tmp_path))

    assert outcome.status == "failed"
    payload = outcome.payload
    assert payload.get("disposition") != DISPOSITION_WORK_DONE_FINALIZE_FAILED
    assert "work_snapshot" not in payload
    # The normal failure payload shape is preserved.
    assert "trace_id" in payload and "final_text" in payload


def test_run_mission_zero_output_with_prior_work_is_recoverable(tmp_path, monkeypatch):
    """Zero-output path also recognizes prior work: the apply ran but the SDK
    produced no final text before the finalize crash."""
    workspace = tmp_path / "ws-zero"
    workspace.mkdir()
    _seed_work_done_workspace(workspace)

    # No error event AND no text at all → would normally be "zero output".
    events = []

    def _fake_adapter_factory(config):  # noqa: ARG001
        return _FakeAdapter(events)

    monkeypatch.setattr(
        "universal_agent.vp.clients.claude_code_client.ProcessTurnAdapter",
        _fake_adapter_factory,
    )

    outcome = asyncio.run(ClaudeCodeClient().run_mission(mission=_mission(workspace), workspace_root=tmp_path))

    assert outcome.status == "failed"
    assert outcome.payload["disposition"] == DISPOSITION_WORK_DONE_FINALIZE_FAILED
    assert outcome.payload["work_snapshot"]["apply_scripts"] == ["apply_typehints.py"]
