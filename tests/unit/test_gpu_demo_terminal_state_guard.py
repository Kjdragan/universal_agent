"""Terminal-state guard for ``classify_and_gate_gpu_demo`` (re-notify suppression).

The demo-build sweep (3x/day) calls ``classify_and_gate_gpu_demo`` per candidate.
A human's terminal decision on a GPU demo — ``rejected`` / ``approved`` (stamped
by the emailed one-click link -> ``gateway_server.gpu_demo_approve_get``) or
``built`` (``finalize_desktop_gpu_demo``) — must survive across sweeps: the sweep
must NOT reset ``gpu_approval.state`` back to ``"pending"`` and must NOT re-send
the approval email. A brand-new / ``pending`` candidate keeps the original
behavior exactly. No live network / no real email send here.
"""

from __future__ import annotations

import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.services import proactive_tutorial_builds as ptb


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _candidate(video_id: str) -> dict:
    # "Ollama" in the title makes gpu_bound_from_candidate() return gpu_bound=True.
    return {
        "video_id": video_id,
        "video_title": f"Run {video_id} locally with Ollama",
        "video_url": f"https://youtu.be/{video_id}",
        "channel_name": "AI Builder",
        "extraction_plan": {},
        "priority": 3,
    }


def _gpu_state(conn: sqlite3.Connection, task_id: str) -> str:
    item = task_hub.get_item(conn, task_id)
    gpu_approval = (item.get("metadata") or {}).get("gpu_approval") or {}
    return str(gpu_approval.get("state") or "")


def _stamp_gpu_state(conn: sqlite3.Connection, task_id: str, state: str) -> None:
    item = task_hub.get_item(conn, task_id)
    meta = dict(item.get("metadata") or {})
    meta["gpu_approval"] = {"state": state}
    task_hub.upsert_item(conn, {**item, "metadata": meta})
    conn.commit()


@pytest.fixture()
def emails(monkeypatch):
    """Feature flag ON + record (never send) approval emails; yields the recorder."""
    monkeypatch.setattr(
        "universal_agent.feature_flags.gpu_demo_desktop_approval_enabled",
        lambda: True,
    )
    sent: list[str] = []

    async def _fake_send(*, task_id, candidate, verdict):
        sent.append(task_id)
        return {"sent": True, "task_id": task_id}

    monkeypatch.setattr(ptb, "_send_gpu_demo_approval_email", _fake_send)
    return sent


def test_new_candidate_queues_pending_and_emails(emails):
    """(3) Baseline unchanged: a never-seen candidate is queued pending + emailed."""
    conn = _make_conn()
    result = ptb.classify_and_gate_gpu_demo(
        conn, candidate=_candidate("vid_new"), source="csi_auto_route"
    )
    assert result is not None
    task_id = result["task"]["task_id"]
    assert _gpu_state(conn, task_id) == "pending"
    assert emails == [task_id]  # emailed exactly once
    conn.close()


@pytest.mark.parametrize("terminal_state", ["rejected", "approved", "built"])
def test_terminal_state_not_reset_or_reemailed(emails, terminal_state):
    """(1)/(2) A terminal decision is preserved: no reset to pending, no re-email."""
    conn = _make_conn()
    cand = _candidate("vid_term")

    # First sweep: queues pending + emails once.
    first = ptb.classify_and_gate_gpu_demo(conn, candidate=cand, source="csi_auto_route")
    task_id = first["task"]["task_id"]
    assert emails == [task_id]

    # Operator decision (or build completion) stamps a terminal state.
    _stamp_gpu_state(conn, task_id, terminal_state)
    emails.clear()

    # Next sweep re-encounters the same gpu-bound candidate.
    again = ptb.classify_and_gate_gpu_demo(conn, candidate=cand, source="csi_auto_route")

    # Non-None keeps the caller on its "handled — skip normal ceiling path" branch,
    # so a rejected build is never resurrected as an auto-dispatch.
    assert again is not None
    assert again.get("skipped_terminal_state") == terminal_state
    # Guard held: state unchanged, no re-email.
    assert _gpu_state(conn, task_id) == terminal_state
    assert emails == []
    # The row is untouched: still pending-approval (agent_ready False), not queued.
    row = task_hub.get_item(conn, task_id)
    assert row["agent_ready"] is False
    conn.close()
