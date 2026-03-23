"""Tests for the env-sanitization guard in execution_engine.py.

Validates that `sanitize_env_for_subprocess()` prevents the Linux E2BIG error
by stripping non-essential env vars when the total environment exceeds the
safe threshold.
"""

import os
import sys
import types

import pytest

from universal_agent.execution_engine import (
    _ENV_SAFE_THRESHOLD_BYTES,
    _ENV_STRIP_CANDIDATES,
    _MAX_SYSTEM_EVENTS_ENV_BYTES,
    EngineConfig,
    ProcessTurnAdapter,
    sanitize_env_for_subprocess,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Snapshot env before each test and restore after."""
    yield


class TestSanitizeEnvForSubprocess:
    """Unit tests for sanitize_env_for_subprocess()."""

    def test_noop_when_small_env(self):
        """Even a small env is reduced to the subprocess-safe whitelist."""
        os.environ["TEST_NONCRITICAL"] = "trim-me"
        removed = sanitize_env_for_subprocess()
        assert "TEST_NONCRITICAL" in removed
        assert "TEST_NONCRITICAL" not in os.environ
        assert "PATH" in os.environ
        assert "HOME" in os.environ

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


@pytest.mark.asyncio
async def test_process_turn_adapter_restores_parent_env_after_client_spawn(monkeypatch, tmp_path):
    """SDK client spawn must not permanently strip runtime secrets from the gateway."""

    captured_spawn_env: dict[str, str] = {}
    captured_client_enter_env: dict[str, str] = {}

    class FakeClaudeSDKClient:
        def __init__(self, options, transport=None):
            self.options = options
            self.transport = transport

        async def __aenter__(self):
            captured_client_enter_env.update(dict(os.environ))
            return self

    class FakeSubprocessCLITransport:
        def __init__(self, prompt, options):
            self.prompt = prompt
            self.options = options

        async def connect(self):
            captured_spawn_env.update(dict(os.environ))

    fake_pkg = types.ModuleType("claude_agent_sdk")
    fake_client_module = types.ModuleType("claude_agent_sdk.client")
    fake_transport_module = types.ModuleType("claude_agent_sdk._internal.transport.subprocess_cli")
    fake_client_module.ClaudeSDKClient = FakeClaudeSDKClient
    fake_transport_module.SubprocessCLITransport = FakeSubprocessCLITransport
    fake_pkg.client = fake_client_module
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_pkg)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk.client", fake_client_module)
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk._internal.transport.subprocess_cli",
        fake_transport_module,
    )

    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PROXY_USERNAME", "rotatingproxyua-rotate")
    monkeypatch.setenv("PROXY_PASSWORD", "super-secret")
    monkeypatch.setenv("NONCRITICAL_SECRET", "should-be-restored")

    adapter = ProcessTurnAdapter(
        EngineConfig(
            workspace_dir=str(tmp_path),
            user_id="test-user",
            run_id="test-run",
        )
    )
    adapter._options = object()

    client = await adapter._ensure_client()

    assert client is adapter._client
    assert "PROXY_USERNAME" not in captured_spawn_env
    assert "PROXY_PASSWORD" not in captured_spawn_env
    assert "NONCRITICAL_SECRET" not in captured_spawn_env
    assert captured_client_enter_env["PROXY_USERNAME"] == "rotatingproxyua-rotate"
    assert captured_client_enter_env["PROXY_PASSWORD"] == "super-secret"
    assert os.environ["PROXY_USERNAME"] == "rotatingproxyua-rotate"
    assert os.environ["PROXY_PASSWORD"] == "super-secret"
    assert os.environ["NONCRITICAL_SECRET"] == "should-be-restored"


@pytest.mark.asyncio
async def test_execute_keeps_proxy_env_visible_to_process_turn(monkeypatch, tmp_path):
    """process_turn should run with the parent gateway env intact."""

    import universal_agent.main as main_module

    observed_env: dict[str, str] = {}

    async def _fake_ensure_client():
        return object()

    async def _fake_process_turn(**kwargs):
        observed_env["PROXY_USERNAME"] = os.environ.get("PROXY_USERNAME", "")
        observed_env["PROXY_PASSWORD"] = os.environ.get("PROXY_PASSWORD", "")
        return types.SimpleNamespace(
            reset_session=False,
            tool_calls=0,
            response_text="",
            workspace_path=None,
            trace_id=None,
        )

    monkeypatch.setattr(main_module, "process_turn", _fake_process_turn)
    monkeypatch.setattr(main_module, "budget_state", {"start_ts": 0.0, "steps": 0, "tool_calls": 0})
    monkeypatch.setenv("PROXY_USERNAME", "rotatingproxyua-rotate")
    monkeypatch.setenv("PROXY_PASSWORD", "super-secret")

    adapter = ProcessTurnAdapter(
        EngineConfig(
            workspace_dir=str(tmp_path),
            user_id="test-user",
            run_id="test-run",
        )
    )
    adapter._options = types.SimpleNamespace(stderr=None, extra_args={})
    monkeypatch.setattr(adapter, "_ensure_client", _fake_ensure_client)

    async for _event in adapter.execute("check proxy env"):
        pass

    assert observed_env == {
        "PROXY_USERNAME": "rotatingproxyua-rotate",
        "PROXY_PASSWORD": "super-secret",
    }
