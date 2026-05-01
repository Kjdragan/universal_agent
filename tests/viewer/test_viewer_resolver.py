"""Resolver tests — Track B Commit 1.

Covers:
  - run_id direct lookup → run target.
  - workspace_dir reverse lookup → run target.
  - workspace_dir without catalog row → path-based target (live session).
  - workspace_name → resolves to absolute path → recurses.
  - session_id via provider_session_id → run target with session_id backfilled.
  - session_id daemon fallback → daemon glob lookup → resolves.
  - Unknown inputs → returns None (never raises).
  - Resolver always emits a viewer_href that round-trips through the route.
  - is_live_session reflects the active_run_workspace marker.
  - source field correctly identifies the resolution branch.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from universal_agent.viewer import resolver


@pytest.fixture
def workspaces_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "AGENT_RUN_WORKSPACES"
    root.mkdir()
    monkeypatch.setenv("AGENT_RUN_WORKSPACES_DIR", str(root))
    return root


@pytest.fixture
def fake_catalog(monkeypatch: pytest.MonkeyPatch):
    """Replace the real RunCatalogService with an in-memory fake."""

    class FakeCatalog:
        def __init__(self):
            self.runs: dict[str, dict] = {}
            self.workspace_index: dict[str, dict] = {}
            self.session_index: dict[str, dict] = {}

        def add_run(
            self,
            run_id: str,
            workspace_dir: str,
            *,
            session_id: str | None = None,
        ) -> dict:
            run = {
                "run_id": run_id,
                "workspace_dir": workspace_dir,
                "provider_session_id": session_id,
            }
            self.runs[run_id] = run
            self.workspace_index[workspace_dir] = run
            if session_id:
                self.session_index[session_id] = run
            return run

        def get_run(self, run_id):
            return self.runs.get(str(run_id or "").strip())

        def find_run_for_workspace(self, workspace_dir):
            return self.workspace_index.get(str(workspace_dir or "").strip())

        def find_latest_run_for_provider_session(self, session_id):
            return self.session_index.get(str(session_id or "").strip())

    fake = FakeCatalog()
    monkeypatch.setattr(resolver, "_get_run_catalog", lambda: fake)
    return fake


# ── run_id branch ────────────────────────────────────────────────────────────


def test_resolve_by_run_id(fake_catalog, workspaces_root):
    ws = workspaces_root / "run_abc123"
    ws.mkdir()
    fake_catalog.add_run("run_abc123", str(ws))

    target = resolver.resolve_session_view_target(run_id="run_abc123")
    assert target is not None
    assert target.target_kind == "run"
    assert target.target_id == "run_abc123"
    assert target.run_id == "run_abc123"
    assert target.workspace_dir == str(ws)
    assert target.viewer_href == "/dashboard/viewer/run/run_abc123"
    assert target.source == "run_catalog.get_run"


# ── workspace_dir branch ─────────────────────────────────────────────────────


def test_resolve_by_workspace_dir(fake_catalog, workspaces_root):
    ws = workspaces_root / "run_abc"
    ws.mkdir()
    fake_catalog.add_run("run_abc", str(ws))

    target = resolver.resolve_session_view_target(workspace_dir=str(ws))
    assert target is not None
    assert target.run_id == "run_abc"
    assert target.source == "run_catalog.find_run_for_workspace"


def test_resolve_workspace_dir_without_catalog_row(fake_catalog, workspaces_root):
    """Live session: workspace exists on disk, no catalog row yet."""
    ws = workspaces_root / "live_session"
    ws.mkdir()

    target = resolver.resolve_session_view_target(
        workspace_dir=str(ws), session_id="daemon_simone_todo"
    )
    assert target is not None
    assert target.target_kind == "session"
    assert target.session_id == "daemon_simone_todo"
    assert target.run_id is None
    assert target.workspace_dir == str(ws.resolve())
    assert target.source == "workspace_dir_path"


# ── workspace_name branch ────────────────────────────────────────────────────


def test_resolve_by_workspace_name(fake_catalog, workspaces_root):
    ws = workspaces_root / "run_named"
    ws.mkdir()
    fake_catalog.add_run("run_named_id", str(ws))

    target = resolver.resolve_session_view_target(workspace_name="run_named")
    assert target is not None
    assert target.run_id == "run_named_id"


def test_resolve_workspace_name_not_found(fake_catalog, workspaces_root):
    target = resolver.resolve_session_view_target(workspace_name="does_not_exist")
    assert target is None


# ── session_id branches ──────────────────────────────────────────────────────


def test_resolve_session_id_via_provider_lookup(fake_catalog, workspaces_root):
    ws = workspaces_root / "run_session_provider"
    ws.mkdir()
    fake_catalog.add_run(
        "run_for_session", str(ws), session_id="vp_atlas_001"
    )

    target = resolver.resolve_session_view_target(session_id="vp_atlas_001")
    assert target is not None
    assert target.run_id == "run_for_session"
    assert target.session_id == "vp_atlas_001"
    assert target.source == "run_catalog.find_latest_run_for_provider_session"


def test_resolve_session_id_daemon_fallback_via_run(
    fake_catalog, workspaces_root
):
    """Daemon glob hit + run found in catalog."""
    ws = workspaces_root / "run_daemon_simone_todo_20260501"
    ws.mkdir()
    fake_catalog.add_run("run_daemon_xyz", str(ws.resolve()))

    target = resolver.resolve_session_view_target(session_id="daemon_simone_todo")
    assert target is not None
    assert target.run_id == "run_daemon_xyz"
    assert target.session_id == "daemon_simone_todo"
    assert target.source == "daemon_glob+run_catalog"
    assert target.is_live_session is True  # daemon_ → always live


def test_resolve_session_id_daemon_fallback_path_only(
    fake_catalog, workspaces_root
):
    """Daemon glob hits a workspace, but no catalog row for it."""
    ws = workspaces_root / "run_daemon_cody_todo_xyz"
    ws.mkdir()

    target = resolver.resolve_session_view_target(session_id="daemon_cody_todo")
    assert target is not None
    assert target.session_id == "daemon_cody_todo"
    assert target.run_id is None
    assert target.target_kind == "session"
    assert target.source == "daemon_glob_path"


def test_resolve_session_id_daemon_archive_fallback(
    fake_catalog, workspaces_root
):
    """Archived daemon workspace under _daemon_archives/."""
    archive = workspaces_root / "_daemon_archives"
    archive.mkdir()
    ws = archive / "run_daemon_simone_todo_old"
    ws.mkdir()

    target = resolver.resolve_session_view_target(session_id="daemon_simone_todo")
    assert target is not None
    assert target.workspace_dir == str(ws.resolve())


def test_resolve_session_id_daemon_picks_newest(fake_catalog, workspaces_root):
    """When multiple daemon candidates exist, newest mtime wins."""
    older = workspaces_root / "run_daemon_atlas_todo_001"
    older.mkdir()
    newer = workspaces_root / "run_daemon_atlas_todo_002"
    newer.mkdir()
    # Set mtime: older is older, newer is newer
    os.utime(older, (1000, 1000))
    os.utime(newer, (9999, 9999))

    target = resolver.resolve_session_view_target(session_id="daemon_atlas_todo")
    assert target is not None
    assert target.workspace_dir == str(newer.resolve())


# ── No-match cases ───────────────────────────────────────────────────────────


def test_resolve_returns_none_when_unknown(fake_catalog, workspaces_root):
    assert resolver.resolve_session_view_target() is None
    assert resolver.resolve_session_view_target(run_id="run_unknown") is None
    assert (
        resolver.resolve_session_view_target(session_id="not_a_daemon_session") is None
    )


def test_resolve_handles_empty_strings(fake_catalog, workspaces_root):
    target = resolver.resolve_session_view_target(
        session_id="", run_id="", workspace_dir="", workspace_name=""
    )
    assert target is None


# ── is_live_session ──────────────────────────────────────────────────────────


def test_is_live_session_via_marker(fake_catalog, workspaces_root):
    ws = workspaces_root / "run_marker"
    ws.mkdir()
    (ws / "active_run_workspace").touch()
    fake_catalog.add_run("run_marker", str(ws.resolve()))

    target = resolver.resolve_session_view_target(run_id="run_marker")
    assert target is not None
    assert target.is_live_session is True


def test_is_live_session_false_for_archived_run(fake_catalog, workspaces_root):
    ws = workspaces_root / "run_archived"
    ws.mkdir()
    fake_catalog.add_run("run_archived", str(ws.resolve()))

    target = resolver.resolve_session_view_target(run_id="run_archived")
    assert target is not None
    assert target.is_live_session is False


# ── viewer_href round-trip ───────────────────────────────────────────────────


def test_viewer_href_format(fake_catalog, workspaces_root):
    ws = workspaces_root / "run_href_test"
    ws.mkdir()
    fake_catalog.add_run("run_href_test", str(ws))

    target = resolver.resolve_session_view_target(run_id="run_href_test")
    assert target.viewer_href == "/dashboard/viewer/run/run_href_test"


def test_viewer_href_url_safe_for_session_with_underscores(
    fake_catalog, workspaces_root
):
    ws = workspaces_root / "run_daemon_simone_todo_xyz"
    ws.mkdir()

    target = resolver.resolve_session_view_target(session_id="daemon_simone_todo")
    assert target is not None
    # underscores survive URL encoding (they're unreserved)
    assert target.viewer_href == "/dashboard/viewer/session/daemon_simone_todo"
