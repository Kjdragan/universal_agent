"""Tests for the runtime environment normalization helpers (``runtime_env.py``).

These are characterization tests for three small, previously-untested helpers
that build the process ``PATH`` and report tool availability for the runtime
startup path (used by ``execution_engine``, ``gateway_server``, ``api.server``).
No behavior is changed in this PR; the tests pin the non-trivial invariants of
``normalize_path`` (defaults-first ordering, first-occurrence dedup, empty/blank
segment dropping), the env mutation in ``ensure_runtime_path``, and the
``shutil.which``-backed shape of ``runtime_tool_status`` so regressions surface.
"""

from __future__ import annotations

import os
import shutil

from universal_agent import runtime_env
from universal_agent.runtime_env import (
    ensure_runtime_path,
    normalize_path,
    runtime_tool_status,
)


class TestNormalizePathDefaultsAndOrdering:
    def test_defaults_prepended_in_order_when_no_existing_path(self):
        # Explicit empty existing path -> result is exactly the default
        # segments in their declared order.
        result = normalize_path("").split(":")
        assert result == list(runtime_env._DEFAULT_PATH_SEGMENTS)

    def test_defaults_always_come_before_existing_segments(self):
        result = normalize_path("/custom/a:/custom/b").split(":")
        # The four default segments occupy the first four positions.
        assert result[: len(runtime_env._DEFAULT_PATH_SEGMENTS)] == list(
            runtime_env._DEFAULT_PATH_SEGMENTS
        )
        assert result[-2:] == ["/custom/a", "/custom/b"]

    def test_default_order_matches_module_constant(self):
        # Pin the shipped default order so a reorder is a visible break.
        assert runtime_env._DEFAULT_PATH_SEGMENTS == (
            "/home/ua/.local/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
        )


class TestNormalizePathDedup:
    def test_existing_segment_matching_a_default_is_not_duplicated(self):
        # /usr/bin is a default; appearing again in the existing PATH must not
        # add a second entry -- the default (front) position wins.
        result = normalize_path("/usr/bin:/opt/foo").split(":")
        assert result.count("/usr/bin") == 1
        assert "/opt/foo" in result

    def test_repeated_existing_segments_collapse_to_first_occurrence(self):
        result = normalize_path("/x:/y:/x:/z:/y").split(":")
        # Defaults first, then /x, /y, /z in first-seen order, no dupes.
        expected_tail = ["/x", "/y", "/z"]
        assert result[len(runtime_env._DEFAULT_PATH_SEGMENTS) :] == expected_tail

    def test_blank_and_empty_segments_are_dropped(self):
        result = normalize_path(":/foo:   ::/bar:").split(":")
        assert "" not in result
        assert "   " not in result
        # /foo and /bar survive past the defaults.
        assert "/foo" in result and "/bar" in result

    def test_segments_are_whitespace_stripped(self):
        result = normalize_path("  /foo  :/bar").split(":")
        assert "/foo" in result
        assert "  /foo  " not in result


class TestNormalizePathEnvFallback:
    def test_none_current_path_reads_path_env(self, monkeypatch):
        monkeypatch.setenv("PATH", "/from-env/a:/from-env/b")
        result = normalize_path(None).split(":")
        assert result[-2:] == ["/from-env/a", "/from-env/b"]

    def test_explicit_current_path_arg_overrides_env(self, monkeypatch):
        # Even when PATH env is set, an explicit arg wins.
        monkeypatch.setenv("PATH", "/from-env")
        result = normalize_path("/from-arg").split(":")
        assert "/from-arg" in result
        assert "/from-env" not in result


class TestEnsureRuntimePath:
    def test_writes_normalized_path_back_to_env(self, monkeypatch):
        monkeypatch.setenv("PATH", "/opt/extras")
        returned = ensure_runtime_path()
        assert returned == normalize_path("/opt/extras")
        # The process env is updated to the normalized value.
        assert os.environ["PATH"] == returned

    def test_idempotent_when_called_twice(self, monkeypatch):
        monkeypatch.setenv("PATH", "/opt/extras:/opt/extras")
        first = ensure_runtime_path()
        second = ensure_runtime_path()
        # Second call normalizes the already-normalized env -> same result.
        assert first == second


class TestRuntimeToolStatus:
    def test_available_when_which_resolves(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
        status = runtime_tool_status(("uv",))
        assert status == {"uv": {"available": True, "path": "/usr/bin/uv"}}

    def test_unavailable_when_which_returns_none(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        status = runtime_tool_status(("sqlite3",))
        assert status == {"sqlite3": {"available": False, "path": None}}

    def test_default_tool_set_is_uv_and_sqlite3(self, monkeypatch):
        # Pin the default tool_names contract used by the runtime endpoints
        # in gateway_server and api.server.
        seen: list[str] = []
        monkeypatch.setattr(shutil, "which", lambda name: seen.append(name) or None)
        status = runtime_tool_status()
        assert set(status.keys()) == {"uv", "sqlite3"}
        assert seen == ["uv", "sqlite3"]
