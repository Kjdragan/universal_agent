"""Unit tests for the workspace-catalog helpers.

``workspace_catalog`` is imported by four live call sites
(``api/server.py``, ``api/process_turn_bridge.py``, ``api/agent_bridge.py``,
``wiki/core.py``) but had no dedicated test coverage. These tests pin the
deterministic, side-effect-free helpers so future refactors of the catalog
shape or the workspace-detection heuristics cannot silently change which
directories the API classifies as agent workspaces.

Scope is the two public helpers plus the module-level prefix/marker tables:

- ``looks_like_agent_workspace`` — pure predicate over a ``Path``.
- ``list_workspace_summaries`` — exercised with a fake catalog (no DB) so the
  test never touches the real run-catalog SQLite store.

No behavior is changed in the production module; these are characterization
tests. Red-green was therefore not applicable (see PR body), but the full
focused suite is run green here as the safety proof.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from universal_agent.workspace_catalog import (
    _WORKSPACE_MARKERS,
    _WORKSPACE_PREFIXES,
    list_workspace_summaries,
    looks_like_agent_workspace,
)

# ── looks_like_agent_workspace ─────────────────────────────────────────


class TestLooksLikeAgentWorkspace:
    """Predicate logic: prefix match (case-insensitive) OR marker file present."""

    @pytest.mark.parametrize("prefix", sorted(_WORKSPACE_PREFIXES))
    def test_prefix_match_is_true(self, tmp_path: Path, prefix: str):
        ws = tmp_path / f"{prefix}abc123"
        ws.mkdir()
        assert looks_like_agent_workspace(ws) is True

    @pytest.mark.parametrize("marker", sorted(_WORKSPACE_MARKERS))
    def test_marker_file_present_is_true(self, tmp_path: Path, marker: str):
        ws = tmp_path / "plain_dir"
        ws.mkdir()
        (ws / marker).write_text("")
        assert looks_like_agent_workspace(ws) is True

    def test_plain_directory_with_no_signal_is_false(self, tmp_path: Path):
        ws = tmp_path / "random_folder"
        ws.mkdir()
        assert looks_like_agent_workspace(ws) is False

    def test_prefix_match_is_case_insensitive(self, tmp_path: Path):
        # The predicate lowercases the directory name before the prefix check,
        # so an all-caps workspace id still classifies.
        ws = tmp_path / "RUN_abc"
        ws.mkdir()
        assert looks_like_agent_workspace(ws) is True

    def test_prefix_must_be_a_true_prefix_not_substring(self, tmp_path: Path):
        # "rune_x" contains "run" but does not start with "run_" — must be False.
        ws = tmp_path / "rune_extraction"
        ws.mkdir()
        assert looks_like_agent_workspace(ws) is False

    def test_underscore_alone_is_not_a_prefix(self, tmp_path: Path):
        ws = tmp_path / "session"
        ws.mkdir()
        assert looks_like_agent_workspace(ws) is False

    def test_marker_check_uses_this_directory_not_its_name(self, tmp_path: Path):
        # A marker file placed in the *parent* must not classify the child.
        plain = tmp_path / "no_signal"
        plain.mkdir()
        # The parent (tmp_path) now contains a marker, but that's irrelevant.
        (tmp_path / "trace.json").write_text("")
        assert looks_like_agent_workspace(plain) is False


# ── list_workspace_summaries ───────────────────────────────────────────


class _FakeCatalog:
    """Stand-in for ``RunCatalogService`` — returns a fixed run list and
    records the args it was called with, so the test never opens a DB."""

    def __init__(self, runs: list[dict]):
        self._runs = runs
        self.calls: list[tuple[Path, int]] = []

    def list_runs_for_workspace_prefix(self, workspace_prefix, limit: int = 1000):
        self.calls.append((Path(str(workspace_prefix)), int(limit)))
        return list(self._runs)


def _touch(path: Path, mtime: float) -> None:
    """Set a deterministic mtime so list_workspace_summaries' sort is stable."""
    os.utime(path, (mtime, mtime))


class TestListWorkspaceSummaries:
    def test_missing_root_returns_empty(self):
        catalog = _FakeCatalog([])
        result = list_workspace_summaries(
            Path("/nonexistent/path/xyz"), run_catalog=catalog
        )
        assert result == []

    def test_file_root_returns_empty(self, tmp_path: Path):
        file_path = tmp_path / "i_am_a_file_not_a_dir"
        file_path.write_text("nope")
        catalog = _FakeCatalog([])
        assert list_workspace_summaries(file_path, run_catalog=catalog) == []

    def test_empty_root_returns_empty(self, tmp_path: Path):
        catalog = _FakeCatalog([])
        assert list_workspace_summaries(tmp_path, run_catalog=catalog) == []

    def test_includes_prefixed_workspace_without_catalog_entry(self, tmp_path: Path):
        ws = tmp_path / "run_abc"
        ws.mkdir()
        catalog = _FakeCatalog([])
        summaries = list_workspace_summaries(tmp_path, run_catalog=catalog)
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary["session_id"] == "run_abc"
        # No catalog run + no trace.json → "incomplete"
        assert summary["status"] == "incomplete"
        assert summary["workspace_path"] == str(ws.resolve())
        # Catalog-derived fields are absent for catalog-less workspaces.
        assert "run_id" not in summary

    def test_includes_marker_only_workspace_without_catalog_entry(self, tmp_path: Path):
        ws = tmp_path / "strange_name"
        ws.mkdir()
        (ws / "trace.json").write_text("{}")
        catalog = _FakeCatalog([])
        summaries = list_workspace_summaries(tmp_path, run_catalog=catalog)
        assert len(summaries) == 1
        # No catalog run, but trace.json exists → "complete"
        assert summaries[0]["status"] == "complete"

    def test_excludes_unsignalized_directory_without_catalog_entry(
        self, tmp_path: Path
    ):
        (tmp_path / "run_matched").mkdir()
        (tmp_path / "random_folder").mkdir()  # neither prefix, marker, nor run
        catalog = _FakeCatalog([])
        summaries = list_workspace_summaries(tmp_path, run_catalog=catalog)
        session_ids = {s["session_id"] for s in summaries}
        assert session_ids == {"run_matched"}

    def test_includes_workspace_matched_only_by_catalog_run(self, tmp_path: Path):
        # A directory with no prefix and no marker still shows up when the
        # catalog has a run entry resolving to its path.
        ws = tmp_path / "external_workspace"
        ws.mkdir()
        run = {
            "workspace_dir": str(ws.resolve()),
            "run_id": "run-xyz",
            "status": "complete",
            "run_kind": "cron",
            "trigger_source": "cron",
            "attempt_count": 1,
            "latest_attempt_id": "att-1",
            "last_success_attempt_id": "att-1",
            "canonical_attempt_id": "att-1",
            "provider_session_id": "prov-1",
            "external_origin": None,
            "external_origin_id": None,
            "external_correlation_id": None,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
        }
        catalog = _FakeCatalog([run])
        summaries = list_workspace_summaries(tmp_path, run_catalog=catalog)
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary["run_id"] == "run-xyz"
        # Catalog run's status wins over the trace-file heuristic.
        assert summary["status"] == "complete"
        assert summary["run_status"] == "complete"
        assert summary["run_kind"] == "cron"

    def test_status_falls_back_to_trace_heuristic_when_run_status_missing(
        self, tmp_path: Path
    ):
        ws = tmp_path / "run_partial"
        ws.mkdir()
        (ws / "trace.json").write_text("{}")
        run = {"workspace_dir": str(ws.resolve()), "run_id": "run-1"}  # no "status"
        catalog = _FakeCatalog([run])
        summaries = list_workspace_summaries(tmp_path, run_catalog=catalog)
        assert len(summaries) == 1
        # No run status → heuristic: trace.json present → "complete"
        assert summaries[0]["status"] == "complete"

    def test_limit_truncates_to_N(self, tmp_path: Path):
        for i in range(5):
            (tmp_path / f"run_{i}").mkdir()
        catalog = _FakeCatalog([])
        summaries = list_workspace_summaries(tmp_path, run_catalog=catalog, limit=3)
        assert len(summaries) == 3

    def test_results_sorted_by_mtime_descending(self, tmp_path: Path):
        oldest = tmp_path / "run_old"
        mid = tmp_path / "run_mid"
        newest = tmp_path / "run_new"
        oldest.mkdir()
        mid.mkdir()
        newest.mkdir()
        _touch(oldest, 1000.0)
        _touch(mid, 2000.0)
        _touch(newest, 3000.0)
        catalog = _FakeCatalog([])
        summaries = list_workspace_summaries(tmp_path, run_catalog=catalog)
        session_ids = [s["session_id"] for s in summaries]
        assert session_ids == ["run_new", "run_mid", "run_old"]

    def test_skips_non_directory_entries_in_root(self, tmp_path: Path):
        (tmp_path / "run_real").mkdir()
        (tmp_path / "stray_file").write_text("not a dir")
        catalog = _FakeCatalog([])
        summaries = list_workspace_summaries(tmp_path, run_catalog=catalog)
        session_ids = {s["session_id"] for s in summaries}
        assert session_ids == {"run_real"}

    def test_catalog_called_with_resolved_root_and_amplified_limit(
        self, tmp_path: Path
    ):
        (tmp_path / "run_1").mkdir()
        catalog = _FakeCatalog([])
        list_workspace_summaries(tmp_path, run_catalog=catalog, limit=10)
        assert len(catalog.calls) == 1
        called_prefix, called_limit = catalog.calls[0]
        assert called_prefix == tmp_path.resolve()
        # Internal amplification: max(limit * 10, 500).
        assert called_limit == max(10 * 10, 500)

    def test_catalog_limit_floor_is_500_for_small_limits(self, tmp_path: Path):
        (tmp_path / "run_1").mkdir()
        catalog = _FakeCatalog([])
        list_workspace_summaries(tmp_path, run_catalog=catalog, limit=1)
        _, called_limit = catalog.calls[0]
        assert called_limit == 500
