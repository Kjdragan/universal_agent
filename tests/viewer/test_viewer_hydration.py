"""Hydration tests — Track B Commit 1.

Covers:
  - readiness=pending when workspace empty (no markers).
  - readiness=ready when any of the four marker files exists.
  - readiness=failed when run_manifest.json has terminal_reason=non-success.
  - history parsed from trace.json (canonical).
  - history fallback to run.log when trace.json is missing.
  - logs interleaved from run.log + activity_journal.log.
  - workspace listing returns directory entries with type/size/mtime.
  - PAN-shaped strings are masked in history + logs.
  - hydration of a missing workspace returns pending readiness without raising.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from universal_agent.viewer import hydration
from universal_agent.viewer.resolver import SessionViewTarget


def _fake_target(workspace: Path) -> SessionViewTarget:
    return SessionViewTarget(
        target_kind="run",
        target_id="run_test",
        run_id="run_test",
        session_id=None,
        workspace_dir=str(workspace),
        is_live_session=False,
        source="test",
        viewer_href="/dashboard/viewer/run/run_test",
    )


# ── Readiness ────────────────────────────────────────────────────────────────


def test_readiness_pending_when_workspace_empty(tmp_path):
    res = hydration.hydrate(_fake_target(tmp_path))
    assert res.readiness.state == "pending"
    assert res.readiness.reason == "no_marker"


def test_readiness_pending_when_workspace_missing(tmp_path):
    missing = tmp_path / "does_not_exist"
    res = hydration.hydrate(_fake_target(missing))
    assert res.readiness.state == "pending"
    assert res.readiness.reason == "workspace_dir_missing"


@pytest.mark.parametrize(
    "marker", ["run_manifest.json", "run_checkpoint.json",
               "session_checkpoint.json", "sync_ready.json"],
)
def test_readiness_ready_for_each_marker(tmp_path, marker):
    (tmp_path / marker).write_text("{}")
    res = hydration.hydrate(_fake_target(tmp_path))
    assert res.readiness.state == "ready"
    assert marker in (res.readiness.reason or "")


def test_readiness_failed_via_terminal_reason(tmp_path):
    (tmp_path / "run_manifest.json").write_text(
        json.dumps({"terminal_reason": "crashed"})
    )
    res = hydration.hydrate(_fake_target(tmp_path))
    assert res.readiness.state == "failed"
    assert "crashed" in (res.readiness.reason or "")


def test_readiness_ready_when_terminal_reason_is_success(tmp_path):
    (tmp_path / "run_manifest.json").write_text(
        json.dumps({"terminal_reason": "completed"})
    )
    res = hydration.hydrate(_fake_target(tmp_path))
    assert res.readiness.state == "ready"


# ── History ──────────────────────────────────────────────────────────────────


def test_history_from_trace_json_messages_array(tmp_path):
    (tmp_path / "trace.json").write_text(json.dumps({
        "messages": [
            {"role": "user", "content": "hello", "ts": 1.0},
            {"role": "assistant", "content": "hi back", "ts": 2.0},
        ]
    }))
    res = hydration.hydrate(_fake_target(tmp_path))
    assert len(res.history) == 2
    assert res.history[0].role == "user"
    assert res.history[0].content == "hello"
    assert res.history[1].role == "assistant"


def test_history_from_trace_json_jsonl(tmp_path):
    """Some traces are JSONL, not a wrapped object."""
    lines = [
        json.dumps({"role": "user", "content": "first"}),
        json.dumps({"role": "assistant", "content": "second"}),
    ]
    (tmp_path / "trace.json").write_text("\n".join(lines))
    res = hydration.hydrate(_fake_target(tmp_path))
    assert len(res.history) == 2


def test_history_falls_back_to_run_log(tmp_path):
    (tmp_path / "run.log").write_text(
        "[USER]: what's the time?\n[ASSISTANT]: noon\n[SYSTEM] startup\n"
    )
    res = hydration.hydrate(_fake_target(tmp_path))
    assert len(res.history) == 3
    assert res.history[0].role == "user"
    assert res.history[0].content == "what's the time?"
    assert res.history[1].role == "assistant"


def test_history_empty_when_no_sources(tmp_path):
    res = hydration.hydrate(_fake_target(tmp_path))
    assert res.history == []


def test_history_respects_limit(tmp_path):
    msgs = [{"role": "user", "content": f"msg {i}"} for i in range(50)]
    (tmp_path / "trace.json").write_text(json.dumps({"messages": msgs}))
    res = hydration.hydrate(_fake_target(tmp_path), history_limit=10)
    assert len(res.history) == 10
    # Truncated returns the LAST 10 (most recent), not the first
    assert res.history[-1].content == "msg 49"


# ── Logs ─────────────────────────────────────────────────────────────────────


def test_logs_from_both_sources(tmp_path):
    (tmp_path / "run.log").write_text("INFO: startup\nERROR: oops\n")
    (tmp_path / "activity_journal.log").write_text("INFO: tool call\n")
    res = hydration.hydrate(_fake_target(tmp_path))
    assert len(res.logs) == 3
    channels = {entry.channel for entry in res.logs}
    assert "run" in channels
    assert "activity" in channels


def test_logs_level_detection(tmp_path):
    (tmp_path / "run.log").write_text(
        "ERROR: bad thing\nWARN: maybe bad\nDEBUG: detail\nplain line\n"
    )
    res = hydration.hydrate(_fake_target(tmp_path))
    levels = [e.level for e in res.logs]
    assert "error" in levels
    assert "warn" in levels
    assert "debug" in levels


def test_logs_empty_when_no_files(tmp_path):
    res = hydration.hydrate(_fake_target(tmp_path))
    assert res.logs == []


# ── Workspace listing ────────────────────────────────────────────────────────


def test_workspace_listing(tmp_path):
    (tmp_path / "file_a.txt").write_text("hello")
    (tmp_path / "file_b.json").write_text("{}")
    (tmp_path / "subdir").mkdir()
    res = hydration.hydrate(_fake_target(tmp_path))
    names = {e.name for e in res.workspace_entries}
    assert names == {"file_a.txt", "file_b.json", "subdir"}
    types = {e.name: e.type for e in res.workspace_entries}
    assert types["subdir"] == "dir"
    assert types["file_a.txt"] == "file"


def test_workspace_listing_dirs_first(tmp_path):
    (tmp_path / "z_file.txt").write_text("")
    (tmp_path / "a_dir").mkdir()
    res = hydration.hydrate(_fake_target(tmp_path))
    # Dirs first by sort key
    assert res.workspace_entries[0].name == "a_dir"
    assert res.workspace_entries[0].type == "dir"


def test_workspace_listing_missing_dir(tmp_path):
    missing = tmp_path / "missing"
    res = hydration.hydrate(_fake_target(missing))
    assert res.workspace_entries == []


# ── PAN masking ──────────────────────────────────────────────────────────────


def test_pan_masking_in_history(tmp_path):
    """Card-shaped digit strings must be masked."""
    msg = {"role": "user", "content": "my card is 4242 4242 4242 4242 thanks"}
    (tmp_path / "trace.json").write_text(json.dumps({"messages": [msg]}))
    res = hydration.hydrate(_fake_target(tmp_path))
    assert "4242 4242 4242 4242" not in res.history[0].content
    assert "4242424242424242" not in res.history[0].content
    assert "••••4242" in res.history[0].content


def test_pan_masking_in_logs(tmp_path):
    (tmp_path / "run.log").write_text("INFO: card 4111111111111234 was used\n")
    res = hydration.hydrate(_fake_target(tmp_path))
    assert "4111111111111234" not in res.logs[0].message
    assert "••••1234" in res.logs[0].message


def test_pan_masking_does_not_touch_short_numbers(tmp_path):
    """Don't mask normal numbers like timestamps, IDs, etc."""
    (tmp_path / "run.log").write_text(
        "INFO: ts=1234567890 user_id=42 attempt=3\n"
    )
    res = hydration.hydrate(_fake_target(tmp_path))
    assert "1234567890" in res.logs[0].message
    assert "42" in res.logs[0].message
