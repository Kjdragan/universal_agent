"""Unit tests for VP failure-rescue surfacing.

Covers Step 2 of the VP /goal + Failure-Rescue PRD:
- ``surface_failure_to_simone`` creates a vp_mission_failure task_hub_item
- ``failure_count`` increments across the same rescue_chain_id
- ``operator_cancel`` failures are NOT surfaced
- Workspace path and brief_path fields are derived from result_ref
- Transcript tail is truncated to 2 KB
- Idempotent re-call upserts the same task_id
- finalize_vp_mission's hook fires on failed and cancelled (non-operator)
- finalize_vp_mission's hook is silent on completed
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from universal_agent import task_hub

# Import the module itself (not just the symbols) so monkeypatch can
# resolve the dotted attribute path "universal_agent.services.vp_failure_rescue.X"
# in the tests below — without this explicit import the
# `universal_agent.services` subpackage isn't loaded at monkeypatch time
# on a fresh interpreter, and pytest raises AttributeError on CI.
from universal_agent.services import vp_failure_rescue  # noqa: F401
from universal_agent.services.vp_failure_rescue import (
    SOURCE_KIND_VP_FAILURE,
    surface_failure_to_simone,
)


def _make_activity_db(tmp_path) -> str:
    """Create an in-place activity DB with task_hub schema initialised."""
    db_path = tmp_path / "activity_state.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    conn.close()
    return str(db_path)


def _make_vp_db_with_mission(
    tmp_path,
    *,
    mission_id: str,
    vp_id: str = "vp.coder.primary",
    objective: str = "do the work",
    rescue_chain_id: str | None = None,
    original_task_id: str | None = None,
    cody_mode: str | None = "anthropic",
    result_ref: str | None = None,
) -> str:
    """Create a vp_state DB with a single mission row, returns path."""
    from universal_agent.durable.migrations import ensure_schema as ensure_vp_schema
    from universal_agent.durable.state import upsert_vp_mission

    db_path = tmp_path / "vp_state.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_vp_schema(conn)

    payload: dict[str, Any] = {}
    metadata: dict[str, Any] = {}
    if rescue_chain_id:
        metadata["rescue_chain_id"] = rescue_chain_id
    if original_task_id:
        metadata["original_task_id"] = original_task_id
    if cody_mode:
        metadata["cody_mode"] = cody_mode
    if metadata:
        payload["metadata"] = metadata

    upsert_vp_mission(
        conn,
        mission_id=mission_id,
        vp_id=vp_id,
        status="running",
        objective=objective,
        mission_type="task",
        payload=payload or None,
        result_ref=result_ref,
    )
    conn.close()
    return str(db_path)


def _read_failure_item(activity_db_path: str, mission_id: str) -> dict[str, Any] | None:
    conn = sqlite3.connect(activity_db_path)
    conn.row_factory = sqlite3.Row
    try:
        return task_hub.get_item(conn, f"vp_failure:{mission_id}")
    finally:
        conn.close()


def test_surface_failure_creates_task_hub_item(tmp_path):
    """First failure of a mission creates a vp_mission_failure row with failure_count=1."""
    activity_db = _make_activity_db(tmp_path)
    vp_db = _make_vp_db_with_mission(
        tmp_path,
        mission_id="m-001",
        vp_id="vp.general.primary",
        objective="research X",
        original_task_id="task-abc",
    )

    task_id = surface_failure_to_simone(
        mission_id="m-001",
        failure_mode="vp_self_reported",
        transcript_tail="last 100 bytes of subprocess output here",
        result_ref="workspace:///tmp/m-001-workspace",
        activity_db_path=activity_db,
        vp_db_path=vp_db,
    )
    assert task_id == "vp_failure:m-001"

    row = _read_failure_item(activity_db, "m-001")
    assert row is not None, "task_hub_item should have been created"
    assert row["source_kind"] == SOURCE_KIND_VP_FAILURE
    assert row["status"] == task_hub.TASK_STATUS_OPEN
    assert int(row.get("agent_ready") or 0) == 1

    meta = row.get("metadata") or {}
    assert meta["mission_id"] == "m-001"
    assert meta["vp_id"] == "vp.general.primary"
    assert meta["failure_mode"] == "vp_self_reported"
    assert meta["failure_count"] == 1
    assert meta["rescue_chain_id"] == "m-001"  # first failure → chain anchor is mission_id
    assert meta["original_task_id"] == "task-abc"
    assert meta["original_objective"].startswith("research X")
    assert meta["transcript_tail"] == "last 100 bytes of subprocess output here"
    assert meta["workspace_path"] == "/tmp/m-001-workspace"
    assert meta["brief_path"].endswith("/m-001-workspace/BRIEF.md")


def test_operator_cancel_does_not_surface(tmp_path):
    """operator_cancel failures should NOT create a vp_mission_failure row."""
    activity_db = _make_activity_db(tmp_path)
    vp_db = _make_vp_db_with_mission(tmp_path, mission_id="m-cancel")

    task_id = surface_failure_to_simone(
        mission_id="m-cancel",
        failure_mode="operator_cancel",
        activity_db_path=activity_db,
        vp_db_path=vp_db,
    )
    assert task_id is None
    assert _read_failure_item(activity_db, "m-cancel") is None


def test_failure_count_increments_across_chain(tmp_path):
    """Second failure in the same rescue chain bumps failure_count to 2."""
    activity_db = _make_activity_db(tmp_path)
    # First mission and its failure.
    _make_vp_db_with_mission(
        tmp_path,
        mission_id="m-100",
        original_task_id="task-xyz",
    )
    surface_failure_to_simone(
        mission_id="m-100",
        failure_mode="goal_cap_hit",
        activity_db_path=activity_db,
        vp_db_path=str(tmp_path / "vp_state.db"),
    )

    # Second mission in same chain — rescue_chain_id points back at m-100.
    _make_vp_db_with_mission(
        tmp_path,
        mission_id="m-101",
        rescue_chain_id="m-100",  # this is what vp_dispatch_mission_retry would set
        original_task_id="task-xyz",
    )
    task_id = surface_failure_to_simone(
        mission_id="m-101",
        failure_mode="vp_self_reported",
        activity_db_path=activity_db,
        vp_db_path=str(tmp_path / "vp_state.db"),
    )
    assert task_id == "vp_failure:m-101"

    row = _read_failure_item(activity_db, "m-101")
    assert row is not None
    meta = row.get("metadata") or {}
    assert meta["failure_count"] == 2, f"expected count=2, got {meta.get('failure_count')}"
    assert meta["rescue_chain_id"] == "m-100"


def test_transcript_tail_is_truncated(tmp_path):
    """transcript_tail longer than 2 KB is truncated."""
    activity_db = _make_activity_db(tmp_path)
    _make_vp_db_with_mission(tmp_path, mission_id="m-big")

    long_tail = "x" * 5000  # 5 KB
    surface_failure_to_simone(
        mission_id="m-big",
        failure_mode="subprocess_crash",
        transcript_tail=long_tail,
        activity_db_path=activity_db,
        vp_db_path=str(tmp_path / "vp_state.db"),
    )
    row = _read_failure_item(activity_db, "m-big")
    assert row is not None
    meta = row.get("metadata") or {}
    assert len(meta["transcript_tail"]) <= 2000


def test_surface_is_idempotent_same_task_id(tmp_path):
    """Re-surfacing the same mission upserts (does not create a duplicate)."""
    activity_db = _make_activity_db(tmp_path)
    _make_vp_db_with_mission(tmp_path, mission_id="m-dup")

    surface_failure_to_simone(
        mission_id="m-dup",
        failure_mode="vp_self_reported",
        activity_db_path=activity_db,
        vp_db_path=str(tmp_path / "vp_state.db"),
    )
    surface_failure_to_simone(
        mission_id="m-dup",
        failure_mode="goal_cap_hit",
        activity_db_path=activity_db,
        vp_db_path=str(tmp_path / "vp_state.db"),
    )

    # Only one row.
    conn = sqlite3.connect(activity_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM task_hub_items WHERE source_kind=?",
        (SOURCE_KIND_VP_FAILURE,),
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    # The latest failure_mode wins.
    meta = task_hub.hydrate_item(dict(rows[0])).get("metadata") or {}
    assert meta["failure_mode"] == "goal_cap_hit"


def test_finalize_vp_mission_invokes_surface_on_failed(tmp_path, monkeypatch):
    """finalize_vp_mission(failed) calls surface_failure_to_simone."""
    from universal_agent.durable.migrations import ensure_schema as ensure_vp_schema
    from universal_agent.durable.state import (
        finalize_vp_mission,
        upsert_vp_mission,
    )

    captured: list[dict[str, Any]] = []

    def _capture(*, mission_id, failure_mode, transcript_tail=None, result_ref=None, **_):
        captured.append({
            "mission_id": mission_id,
            "failure_mode": failure_mode,
            "transcript_tail": transcript_tail,
            "result_ref": result_ref,
        })
        return "vp_failure:mocked"

    # Use module-object form (not string form) — pytest's string-based
    # resolve() walks attributes from universal_agent down, and on CI's
    # interpreter init path `services` isn't yet bound as an attribute
    # of universal_agent even after the explicit import above.
    monkeypatch.setattr(vp_failure_rescue, "surface_failure_to_simone", _capture)

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    ensure_vp_schema(db)
    upsert_vp_mission(
        db,
        mission_id="m-fail",
        vp_id="vp.coder.primary",
        status="running",
        objective="fail me",
    )

    ok = finalize_vp_mission(
        db, "m-fail", "failed",
        result_ref="workspace:///tmp/m-fail",
        failure_mode="subprocess_crash",
        transcript_tail="boom",
    )
    assert ok is True
    assert len(captured) == 1
    assert captured[0]["mission_id"] == "m-fail"
    assert captured[0]["failure_mode"] == "subprocess_crash"
    assert captured[0]["transcript_tail"] == "boom"


def test_finalize_vp_mission_silent_on_completed(tmp_path, monkeypatch):
    """finalize_vp_mission(completed) does NOT surface anything."""
    from universal_agent.durable.migrations import ensure_schema as ensure_vp_schema
    from universal_agent.durable.state import (
        finalize_vp_mission,
        upsert_vp_mission,
    )

    captured: list[Any] = []
    monkeypatch.setattr(
        vp_failure_rescue,
        "surface_failure_to_simone",
        lambda **kw: captured.append(kw),
    )

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    ensure_vp_schema(db)
    upsert_vp_mission(
        db, mission_id="m-ok", vp_id="vp.coder.primary",
        status="running", objective="happy path",
    )
    finalize_vp_mission(db, "m-ok", "completed", result_ref="workspace:///tmp/m-ok")
    assert captured == []


def test_finalize_vp_mission_surface_failure_does_not_propagate(tmp_path, monkeypatch):
    """If the rescue hook raises, finalize_vp_mission still succeeds."""
    from universal_agent.durable.migrations import ensure_schema as ensure_vp_schema
    from universal_agent.durable.state import (
        finalize_vp_mission,
        upsert_vp_mission,
    )

    def _boom(**_):
        raise RuntimeError("rescue surfacing failed for test")

    monkeypatch.setattr(vp_failure_rescue, "surface_failure_to_simone", _boom)

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    ensure_vp_schema(db)
    upsert_vp_mission(
        db, mission_id="m-explodes", vp_id="vp.coder.primary",
        status="running", objective="exploding rescue",
    )
    # Should NOT raise.
    ok = finalize_vp_mission(db, "m-explodes", "failed", failure_mode="vp_self_reported")
    assert ok is True


def test_cli_env_injects_oauth_token_in_anthropic_mode(tmp_path, monkeypatch):
    """_build_cli_env(anthropic) forwards CLAUDE_CODE_OAUTH_TOKEN into subprocess env.

    Empirically verified 2026-05-26: ``claude setup-token`` produces a
    long-lived OAuth token (``sk-ant-oat01-...``) and tells the operator
    "Use this token by setting: export CLAUDE_CODE_OAUTH_TOKEN=<token>".
    Forwarding it as ANTHROPIC_API_KEY (earlier behavior) was rejected by
    Claude Code as "Invalid API key · Fix external API key".
    """
    from universal_agent.vp.clients.claude_cli_client import _build_cli_env

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-test-token-12345")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://zai.example/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "zai-aux-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stale-api-key-should-not-survive")

    env = _build_cli_env(
        enable_agent_teams=False,
        workspace_dir=tmp_path,
        cody_mode="anthropic",
    )
    # ZAI routing vars stripped (per cody_mode=anthropic contract).
    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    # OAuth token forwarded under its canonical env var name.
    assert env.get("CLAUDE_CODE_OAUTH_TOKEN") == "sk-ant-oat01-test-token-12345"
    # Stale ANTHROPIC_API_KEY MUST NOT survive (Claude Code would prefer
    # it over OAuth and fail with "Invalid API key").
    assert "ANTHROPIC_API_KEY" not in env
    assert env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] == "1"


def test_cli_env_legacy_max_oauth_token_name_still_works(tmp_path, monkeypatch):
    """Legacy ANTHROPIC_MAX_OAUTH_TOKEN env var still routes correctly during transition.

    During rollout we may have both names in Infisical. CLAUDE_CODE_OAUTH_TOKEN
    is preferred; ANTHROPIC_MAX_OAUTH_TOKEN is the fallback.
    """
    from universal_agent.vp.clients.claude_cli_client import _build_cli_env

    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_MAX_OAUTH_TOKEN", "sk-ant-oat01-legacy-name-token")

    env = _build_cli_env(
        enable_agent_teams=False,
        workspace_dir=tmp_path,
        cody_mode="anthropic",
    )
    assert env.get("CLAUDE_CODE_OAUTH_TOKEN") == "sk-ant-oat01-legacy-name-token"


def test_cli_env_zai_mode_unchanged_by_oauth_token(tmp_path, monkeypatch):
    """ZAI mode preserves ANTHROPIC_* vars; OAuth token presence doesn't disturb routing."""
    from universal_agent.vp.clients.claude_cli_client import _build_cli_env

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-test-token")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://zai.example/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "zai-aux-token")

    env = _build_cli_env(
        enable_agent_teams=True,
        workspace_dir=tmp_path,
        cody_mode="zai",
    )
    # ZAI routing vars preserved (no anthropic-mode scrubbing).
    assert env.get("ANTHROPIC_BASE_URL") == "https://zai.example/v1"
    assert env.get("ANTHROPIC_AUTH_TOKEN") == "zai-aux-token"
    # OAuth token flows through naturally (doesn't start with ANTHROPIC_).
    assert env.get("CLAUDE_CODE_OAUTH_TOKEN") == "sk-ant-oat01-test-token"
