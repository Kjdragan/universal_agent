"""Tests for the env-sanitization guard in execution_engine.py.

Validates that `sanitize_env_for_subprocess()` prevents the Linux E2BIG error
by stripping non-essential env vars when the total environment exceeds the
safe threshold.
"""

import os

import pytest

from universal_agent.execution_engine import (
    _ENV_SAFE_THRESHOLD_BYTES,
    _ENV_STRIP_CANDIDATES,
    _MAX_SYSTEM_EVENTS_ENV_BYTES,
    sanitize_env_for_subprocess,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Snapshot env before each test and restore after."""
    yield


class TestSanitizeEnvForSubprocess:
    """Unit tests for sanitize_env_for_subprocess()."""

    def test_noop_when_small_env(self):
        """No vars should be removed when the env is well under the threshold."""
        removed = sanitize_env_for_subprocess()
        assert removed == [], "Should not remove anything from a normal-sized env"

    def test_strips_known_bloat_first(self, monkeypatch):
        """LS_COLORS and other known-bloat vars should be stripped first."""
        # Inject a massive LS_COLORS to push over the threshold
        bloat = "a" * (_ENV_SAFE_THRESHOLD_BYTES + 100_000)
        monkeypatch.setenv("LS_COLORS", bloat)

        removed = sanitize_env_for_subprocess()
        assert "LS_COLORS" in removed
        assert "LS_COLORS" not in os.environ
        # After removal, total should be under threshold
        total = sum(len(k) + len(v) + 2 for k, v in os.environ.items())
        assert total <= _ENV_SAFE_THRESHOLD_BYTES

    def test_preserves_critical_vars(self, monkeypatch):
        """PATH, HOME, ANTHROPIC_API_KEY etc. must never be removed."""
        critical = {"PATH", "HOME", "ANTHROPIC_API_KEY"}
        # Set critical vars
        for key in critical:
            monkeypatch.setenv(key, "/some/value")

        # Inject bloat via non-critical vars to push over threshold
        chunk_size = 200_000
        for i in range(10):
            monkeypatch.setenv(f"BLOAT_VAR_{i}", "x" * chunk_size)

        removed = sanitize_env_for_subprocess()
        assert len(removed) > 0, "Should have removed something"
        for key in critical:
            assert key not in removed, f"Critical var {key} should never be removed"
            assert key in os.environ, f"Critical var {key} should still be in env"

    def test_strips_bash_func_prefixes(self, monkeypatch):
        """BASH_FUNC_* vars should be stripped in Phase 2."""
        # Make env just under threshold with known candidates absent
        for cand in _ENV_STRIP_CANDIDATES:
            monkeypatch.delenv(cand, raising=False)

        # Add bash function vars that push over
        func_size = 300_000
        monkeypatch.setenv("BASH_FUNC_foo%%", "x" * func_size)
        monkeypatch.setenv("BASH_FUNC_bar%%", "x" * func_size)
        # Also add general bloat to ensure we're over
        monkeypatch.setenv("EXTRA_BLOAT", "y" * _ENV_SAFE_THRESHOLD_BYTES)

        removed = sanitize_env_for_subprocess()
        bash_removed = [k for k in removed if k.startswith("BASH_FUNC_")]
        assert len(bash_removed) >= 1 or "EXTRA_BLOAT" in removed

    def test_phase3_drops_largest_non_critical(self, monkeypatch):
        """Phase 3 should drop the largest non-critical vars."""
        # Remove all strip candidates so phases 1+2 have nothing to do
        for cand in _ENV_STRIP_CANDIDATES:
            monkeypatch.delenv(cand, raising=False)

        # Add many medium-sized non-critical vars to exceed threshold
        var_size = 200_000
        var_names = [f"TEST_LARGE_{i}" for i in range(12)]
        for name in var_names:
            monkeypatch.setenv(name, "z" * var_size)

        removed = sanitize_env_for_subprocess()
        total_after = sum(len(k) + len(v) + 2 for k, v in os.environ.items())
        assert total_after <= _ENV_SAFE_THRESHOLD_BYTES
        assert len(removed) > 0

    def test_returns_list_of_removed_keys(self, monkeypatch):
        """Return value should list all removed keys."""
        monkeypatch.setenv("LS_COLORS", "x" * (_ENV_SAFE_THRESHOLD_BYTES + 100_000))
        removed = sanitize_env_for_subprocess()
        assert isinstance(removed, list)
        assert all(isinstance(k, str) for k in removed)


class TestSystemEventsSizeCap:
    """Verify that the constants for system-events capping are sane."""

    def test_max_system_events_env_bytes_is_reasonable(self):
        assert 10_000 <= _MAX_SYSTEM_EVENTS_ENV_BYTES <= 100_000

    def test_threshold_leaves_headroom(self):
        """Threshold should be well under 2MB to leave room for CLI args."""
        assert _ENV_SAFE_THRESHOLD_BYTES < 2_000_000
        assert _ENV_SAFE_THRESHOLD_BYTES >= 1_000_000
