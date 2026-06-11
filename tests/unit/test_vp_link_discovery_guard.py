"""Guards for the linked-task auto-discovery hijack fix (2026-06-10).

The nightly-wiki incident (vp-mission-214dc4539a9549b54e34329d): a taskless
``proactive_wiki`` dispatch fired while a freshly-approved ``tutorial_build``
card sat seized (P2b ``dispatch_on_approval`` seizes as ``dashboard_operator``
until Simone's todo loop dispatches). The PR #490c auto-discovery in
``_vp_dispatch_mission_impl`` linked that card to the wiki mission, which
inherited ``use_goal_loop``/``cody_mode`` and falsely closed the card at
mission completion.

Two gates close the class — pinned here:

1. ``link_task=False`` — routine dispatchers (wiki/briefings/scout) declare
   "this mission executes no Task Hub task"; discovery is skipped entirely.
2. Lane compatibility — a DISCOVERED (never explicit) coder-lane task
   (``_CODER_LANE_SOURCE_KINDS``) only auto-links to a coder-VP dispatch.

Plus the #490c regression guard: a taskless coder-VP dispatch still
auto-links the seized coder-lane card (the fallback's original purpose).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
from typing import Any

from universal_agent import task_hub
from universal_agent.durable.db import connect_runtime_db, get_vp_db_path
from universal_agent.services.proactive_tutorial_builds import (
    queue_tutorial_build_task,
)
from universal_agent.tools.vp_orchestration import _vp_dispatch_mission_impl


def _unwrap(result: dict) -> dict:
    assert "content" in result
    return json.loads(result["content"][0]["text"])


def _read_mission_payload(mission_id: str) -> dict[str, Any]:
    conn = connect_runtime_db(get_vp_db_path())
    try:
        row = conn.execute(
            "SELECT payload_json FROM vp_missions WHERE mission_id = ?",
            (mission_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return json.loads(row[0])


def _seed_seized_tutorial_card(tmp_path: Path) -> str:
    """Queue a tutorial_build card and seize its assignment (the P2b window)."""
    activity_db = str((tmp_path / "activity_state.db").resolve())
    conn = connect_runtime_db(activity_db)
    try:
        queued = queue_tutorial_build_task(
            conn,
            video_id="hijack1",
            video_title="Build an app from your phone in AI Studio",
            video_url="https://youtube.test/watch?v=hijack1",
            channel_name="Google Cloud Tech",
            extraction_plan={"language": "javascript"},
        )
        task_id = queued["task"]["task_id"]
        # Mirror dispatch_on_approval's seize: assignment held by the
        # dashboard operator while awaiting the real dispatch.
        conn.execute(
            "INSERT INTO task_hub_assignments "
            "(assignment_id, task_id, agent_id, state, started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "asg_hijack1",
                task_id,
                "dashboard_operator",
                "seized",
                "2026-06-10T23:32:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return task_id


def _dispatch(args: dict[str, Any]) -> dict[str, Any]:
    base = {
        "objective": "Test mission objective",
        "idempotency_key": f"link-guard-{args.get('vp_id')}-{args.get('mission_type')}",
    }
    base.update(args)
    return _unwrap(asyncio.run(_vp_dispatch_mission_impl(base)))


def _env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("UA_VP_DB_PATH", str((tmp_path / "vp_state.db").resolve()))
    monkeypatch.setenv(
        "UA_ACTIVITY_DB_PATH", str((tmp_path / "activity_state.db").resolve())
    )


def test_taskless_noncoder_dispatch_does_not_steal_coder_card(monkeypatch, tmp_path):
    """The exact incident shape: taskless proactive_wiki dispatch to the
    general VP while a tutorial_build card sits seized — must NOT link."""
    _env(monkeypatch, tmp_path)
    _seed_seized_tutorial_card(tmp_path)

    dispatched = _dispatch(
        {
            "vp_id": "vp.general.primary",
            "mission_type": "proactive_wiki",
            "idempotency_key": "nightly-wiki-test",
        }
    )
    assert dispatched["ok"] is True

    payload = _read_mission_payload(dispatched["mission_id"])
    assert payload.get("task_id") in ("", None)
    metadata = payload.get("metadata") or {}
    assert not metadata.get("linked_task_id")
    # No linkage → no inheritance of the card's goal/cody stamps.
    assert not metadata.get("use_goal_loop")


def test_link_task_false_suppresses_discovery_entirely(monkeypatch, tmp_path):
    """A routine dispatcher passing link_task=False skips discovery even for
    a coder-VP dispatch that would otherwise be lane-compatible."""
    _env(monkeypatch, tmp_path)
    _seed_seized_tutorial_card(tmp_path)

    dispatched = _dispatch(
        {
            "vp_id": "vp.coder.primary",
            "mission_type": "task",
            "cody_mode": "zai",
            "link_task": False,
            "idempotency_key": "link-false-test",
        }
    )
    assert dispatched["ok"] is True

    payload = _read_mission_payload(dispatched["mission_id"])
    assert payload.get("task_id") in ("", None)
    assert not (payload.get("metadata") or {}).get("linked_task_id")


def test_coder_dispatch_still_auto_links_coder_card(monkeypatch, tmp_path):
    """PR #490c regression guard: the fallback's original purpose — a
    taskless coder-VP dispatch picks up the seized coder-lane card."""
    _env(monkeypatch, tmp_path)
    task_id = _seed_seized_tutorial_card(tmp_path)

    dispatched = _dispatch(
        {
            "vp_id": "vp.coder.primary",
            "mission_type": "task",
            "cody_mode": "zai",
            "idempotency_key": "coder-autolink-test",
        }
    )
    assert dispatched["ok"] is True

    payload = _read_mission_payload(dispatched["mission_id"])
    metadata = payload.get("metadata") or {}
    assert metadata.get("linked_task_id") == task_id


def test_explicit_task_id_is_always_honored(monkeypatch, tmp_path):
    """Explicit task_id bypasses both gates — explicit linkage is the
    caller's responsibility, and link_task=False only suppresses discovery."""
    _env(monkeypatch, tmp_path)
    task_id = _seed_seized_tutorial_card(tmp_path)

    dispatched = _dispatch(
        {
            "vp_id": "vp.general.primary",
            "mission_type": "research_analysis",
            "task_id": task_id,
            "link_task": False,
            "idempotency_key": "explicit-task-test",
        }
    )
    assert dispatched["ok"] is True

    payload = _read_mission_payload(dispatched["mission_id"])
    assert payload.get("task_id") == task_id


def test_routine_scripts_pass_link_task_false():
    """The three scheduled-routine dispatchers carry the opt-out."""
    repo = Path(__file__).resolve().parents[2]
    for script in (
        "src/universal_agent/scripts/nightly_wiki_agent.py",
        "src/universal_agent/scripts/freelance_scout_agent.py",
        "src/universal_agent/scripts/briefings_agent.py",
    ):
        text = (repo / script).read_text(encoding="utf-8")
        assert "link_task=False" in text, f"{script} missing link_task=False"
