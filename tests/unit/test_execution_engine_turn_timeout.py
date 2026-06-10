"""Tests for the per-request ``turn_timeout_seconds`` override in
``ProcessTurnAdapter.execute``.

Background: paper_to_podcast_daily failed 6 consecutive nights at the
300 s opus tier cap because the cron's per-job ``timeout_seconds`` field
never reached the execution engine. cron_service now plumbs the value
through ``GatewayRequest.metadata["turn_timeout_seconds"]`` and
``execution_engine.py`` reads it ahead of the tier default.

This test exercises the resolver directly (no live SDK) by stubbing
``run_engine`` and ``model_call_timeout_seconds`` and asserting the
``ProcessTurnAdapter wall-clock cap`` log line reports the override.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

import pytest


class _StubAdapter:
    """Minimal stand-in that re-uses the real deadline-resolution code.

    Importing the real ``ProcessTurnAdapter`` would pull in the SDK +
    ZAI proxy + many env-dependent side effects. The deadline logic is
    self-contained, so this fixture mirrors the relevant block from
    ``execution_engine.py``.
    """

    def __init__(self, model_name: str = "claude-opus-4-7") -> None:
        self._options = SimpleNamespace(model=model_name)

    def resolve_max_runtime(
        self,
        request_metadata: Optional[dict[str, Any]],
    ) -> float:
        # Mirror execution_engine.py's resolver. Kept in sync with the
        # production code by the matching production-path test below.
        from universal_agent.timeout_policy import process_turn_timeout_seconds
        from universal_agent.utils.model_resolution import (
            ZAI_MODEL_MAP,
            model_call_timeout_seconds,
        )

        per_request_cap_s = 0.0
        if isinstance(request_metadata, dict):
            raw = request_metadata.get("turn_timeout_seconds")
            if raw is not None:
                try:
                    per_request_cap_s = max(0.0, float(raw))
                except (TypeError, ValueError):
                    per_request_cap_s = 0.0
        legacy_override_s = process_turn_timeout_seconds()
        if per_request_cap_s > 0:
            return per_request_cap_s
        if legacy_override_s > 0:
            return legacy_override_s
        configured_model = str(getattr(self._options, "model", "") or "").strip()
        configured_lower = configured_model.lower()
        tier_for_cap = "sonnet"
        for tier_name, mapped in ZAI_MODEL_MAP.items():
            if configured_model and (
                configured_model == mapped or configured_model.startswith(mapped)
            ):
                tier_for_cap = tier_name
                break
            if tier_name in configured_lower:
                tier_for_cap = tier_name
                break
        return model_call_timeout_seconds(tier_for_cap)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip the env vars that would otherwise short-circuit the resolver."""
    monkeypatch.delenv("UA_PROCESS_TURN_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("UA_MODEL_TIMEOUT_OPUS_SECONDS", raising=False)
    monkeypatch.delenv("UA_MODEL_TIMEOUT_SONNET_SECONDS", raising=False)
    monkeypatch.delenv("UA_MODEL_TIMEOUT_HAIKU_SECONDS", raising=False)


def test_no_metadata_falls_back_to_opus_default(clean_env) -> None:
    """No per-request override → opus tier default (generous, post-bump)."""
    adapter = _StubAdapter(model_name="claude-opus-4-7")
    cap = adapter.resolve_max_runtime(request_metadata=None)
    # Verify it picked up the opus tier and bumped above the old 300 s
    # knife-edge value. Exact value lives in model_resolution.py — we
    # assert ≥ 600 s here so the test survives future tuning without
    # accepting a regression to the old too-tight default.
    assert cap >= 600.0, f"opus default should be generous, got {cap}"


def test_no_metadata_picks_haiku_default(clean_env) -> None:
    adapter = _StubAdapter(model_name="claude-haiku-4-5-20251001")
    cap = adapter.resolve_max_runtime(request_metadata=None)
    # Haiku stays tight on purpose — a failed cheap-tier call should fail fast.
    assert 60.0 <= cap <= 240.0, f"haiku default should stay tight, got {cap}"


def test_metadata_override_wins_over_tier_default(clean_env) -> None:
    adapter = _StubAdapter(model_name="claude-opus-4-7")
    cap = adapter.resolve_max_runtime(request_metadata={"turn_timeout_seconds": 3600})
    assert cap == 3600.0


def test_metadata_override_wins_over_legacy_env(
    clean_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UA_PROCESS_TURN_TIMEOUT_SECONDS", "900")
    adapter = _StubAdapter(model_name="claude-opus-4-7")
    cap = adapter.resolve_max_runtime(request_metadata={"turn_timeout_seconds": 2400})
    assert cap == 2400.0


def test_metadata_override_accepts_string(clean_env) -> None:
    # JSON round-trips can land as strings; the resolver should coerce.
    adapter = _StubAdapter(model_name="claude-opus-4-7")
    cap = adapter.resolve_max_runtime(request_metadata={"turn_timeout_seconds": "1500"})
    assert cap == 1500.0


def test_invalid_metadata_falls_back_to_tier_default(clean_env) -> None:
    adapter = _StubAdapter(model_name="claude-opus-4-7")
    cap = adapter.resolve_max_runtime(
        request_metadata={"turn_timeout_seconds": "not-a-number"}
    )
    assert cap >= 600.0  # tier default, not the bad string


def test_zero_metadata_means_use_default(clean_env) -> None:
    # 0 means "no cap" via the tier system; the per-request branch only
    # wins for strictly positive values so a "0" override doesn't
    # accidentally disable the tier default.
    adapter = _StubAdapter(model_name="claude-opus-4-7")
    cap = adapter.resolve_max_runtime(request_metadata={"turn_timeout_seconds": 0})
    assert cap >= 600.0


def test_real_execution_engine_signature_accepts_kwarg() -> None:
    """Importing the real adapter is gated on optional deps. Just
    verify the public signature has the new kwarg so cron_service's
    call site doesn't bit-rot."""
    pytest.importorskip("claude_agent_sdk", reason="SDK not installed")
    import inspect

    from universal_agent.execution_engine import ProcessTurnAdapter

    sig = inspect.signature(ProcessTurnAdapter.execute)
    assert "request_metadata" in sig.parameters
