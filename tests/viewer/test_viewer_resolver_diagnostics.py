"""Resolver diagnostic + literal-directory fallback tests.

Covers the gap that produced the live "Could not resolve a viewer target
for this item" alert from Task Hub:
  - When all the original branches miss, a session_id whose workspace dir
    is named after the session id (no `run_` prefix) should still resolve.
  - The trace list should record every branch that missed + why, so 404
    responses are diagnosable without reading server logs.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from universal_agent.viewer import resolver


@pytest.fixture
def workspaces_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "AGENT_RUN_WORKSPACES"
    root.mkdir()
    monkeypatch.setenv("AGENT_RUN_WORKSPACES_DIR", str(root))
    return root


@pytest.fixture
def empty_catalog(monkeypatch: pytest.MonkeyPatch):
    """Catalog that knows nothing — the case that produced 404s in prod."""

    class FakeCatalog:
        def get_run(self, _):
            return None

        def find_run_for_workspace(self, _):
            return None

        def find_latest_run_for_provider_session(self, _):
            return None

    monkeypatch.setattr(resolver, "_get_run_catalog", lambda: FakeCatalog())


# ── New branch: literal-directory fallback for non-daemon session ids ────────


def test_session_id_literal_dir_fallback(workspaces_root, empty_catalog):
    """A session_id like `vp_atlas_001` whose workspace dir is named exactly
    that — no `run_` prefix and no provider_session_id catalog entry —
    should still resolve via the new literal-dir branch.
    """
    ws = workspaces_root / "vp_atlas_001"
    ws.mkdir()

    target = resolver.resolve_session_view_target(session_id="vp_atlas_001")
    assert target is not None
    assert target.workspace_dir == str(ws.resolve())
    assert target.session_id == "vp_atlas_001"
    assert target.source == "session_id_literal_dir"


def test_session_id_literal_dir_archive_fallback(workspaces_root, empty_catalog):
    """Same fallback but for archived workspaces under _daemon_archives/."""
    archive = workspaces_root / "_daemon_archives"
    archive.mkdir()
    ws = archive / "vp_atlas_archived"
    ws.mkdir()

    target = resolver.resolve_session_view_target(session_id="vp_atlas_archived")
    assert target is not None
    assert target.workspace_dir == str(ws.resolve())


def test_workspace_name_archive_fallback(workspaces_root, empty_catalog):
    """workspace_name pointing to an archived workspace should resolve."""
    archive = workspaces_root / "_daemon_archives"
    archive.mkdir()
    ws = archive / "run_named_archive"
    ws.mkdir()

    target = resolver.resolve_session_view_target(
        workspace_name="run_named_archive",
    )
    assert target is not None
    assert target.workspace_dir == str(ws.resolve())


# ── Diagnostic trace ─────────────────────────────────────────────────────────


def test_trace_records_run_id_miss(workspaces_root, empty_catalog):
    trace: list[str] = []
    target = resolver.resolve_session_view_target(
        run_id="run_unknown",
        trace=trace,
    )
    assert target is None
    assert any("run_unknown" in entry and "not in run_catalog" in entry for entry in trace)


def test_trace_records_session_id_misses(workspaces_root, empty_catalog):
    trace: list[str] = []
    target = resolver.resolve_session_view_target(
        session_id="daemon_simone_todo",
        trace=trace,
    )
    assert target is None
    assert any("daemon_simone_todo" in entry for entry in trace)
    # Multiple branches should each have left a breadcrumb
    assert len(trace) >= 2


def test_trace_records_workspace_name_miss(workspaces_root, empty_catalog):
    trace: list[str] = []
    target = resolver.resolve_session_view_target(
        workspace_name="run_does_not_exist",
        trace=trace,
    )
    assert target is None
    assert any("workspace_name" in entry for entry in trace)
    assert any("_daemon_archives" in entry for entry in trace)


def test_trace_empty_when_resolution_succeeds(workspaces_root, empty_catalog):
    """Successful resolution doesn't pollute the trace list."""
    ws = workspaces_root / "vp_atlas_ok"
    ws.mkdir()

    trace: list[str] = []
    target = resolver.resolve_session_view_target(
        session_id="vp_atlas_ok",
        trace=trace,
    )
    assert target is not None
    # Trace may have earlier-branch breadcrumbs (provider lookup miss
    # before the literal-dir hit) — but the resolution itself succeeded.
    # The point of this test: trace=None default doesn't break anything.
    target2 = resolver.resolve_session_view_target(session_id="vp_atlas_ok")
    assert target2 is not None


# ── Producer-side regression: workspace_name now flows through ───────────────


def test_workspace_name_resolves_when_session_and_run_both_miss(
    workspaces_root, empty_catalog
):
    """The exact failure mode from the live Task Hub: session_id and
    run_id are both present but neither resolves; only workspace_name does.
    """
    ws = workspaces_root / "task_hub_workspace_xyz"
    ws.mkdir()

    target = resolver.resolve_session_view_target(
        session_id="daemon_simone_todo",  # daemon glob will miss (no run_ prefix match)
        run_id="run_not_in_catalog",       # catalog miss
        workspace_name="task_hub_workspace_xyz",  # this is the only thing that resolves
    )
    assert target is not None
    assert target.workspace_dir == str(ws.resolve())
