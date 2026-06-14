"""Phase 0 of the graded-judge redesign: ``_call_llm`` forwards an optional
``temperature`` into ``messages.create`` on BOTH the direct and the rate-limited
paths — but ONLY when set, so every existing caller's wire payload is unchanged.

This is the single plumbing change every judgment gate (triage / buildability)
relies on to become deterministic (``temperature=0``). It also pins the shared
knob/score helpers (``_resolve_judge_temperature`` / ``_coerce_score``).
"""

from __future__ import annotations

import asyncio

import universal_agent.services.llm_classifier as lc


class _FakeMessages:
    def __init__(self, captured: list[dict]):
        self._captured = captured

    async def create(self, **kwargs):
        self._captured.append(dict(kwargs))

        class _Block:
            text = "ok"

        class _Resp:
            content = [_Block()]

        return _Resp()


class _FakeClient:
    def __init__(self, captured: list[dict]):
        self.messages = _FakeMessages(captured)
        self.closed = False

    async def close(self):
        self.closed = True


def _patch_direct_client(monkeypatch, captured: list[dict]):
    async def _fake_get_client(**_kwargs):
        return _FakeClient(captured)

    monkeypatch.setattr(lc, "_get_anthropic_client", _fake_get_client)
    # Force the direct (non-limiter) path regardless of ambient env.
    monkeypatch.setattr(lc, "_limiter_enabled", lambda: False)


# ── _call_llm temperature forwarding (direct path) ──────────────────────────


def test_temperature_forwarded_when_set(monkeypatch):
    captured: list[dict] = []
    _patch_direct_client(monkeypatch, captured)
    out = asyncio.run(lc._call_llm(system="s", user="u", model="m", temperature=0))
    assert out == "ok"
    assert len(captured) == 1
    assert captured[0]["temperature"] == 0


def test_temperature_absent_when_unset(monkeypatch):
    """Default (no temperature) must NOT add the key — byte-identical to today."""
    captured: list[dict] = []
    _patch_direct_client(monkeypatch, captured)
    asyncio.run(lc._call_llm(system="s", user="u", model="m"))
    assert "temperature" not in captured[0]


def test_nonzero_temperature_forwarded(monkeypatch):
    captured: list[dict] = []
    _patch_direct_client(monkeypatch, captured)
    asyncio.run(lc._call_llm(system="s", user="u", model="m", temperature=0.7))
    assert captured[0]["temperature"] == 0.7


# ── _call_llm temperature forwarding (rate-limited path) ────────────────────


def test_temperature_forwarded_on_limiter_path(monkeypatch):
    captured: list[dict] = []

    async def _fake_get_client(**_kwargs):
        return _FakeClient(captured)

    monkeypatch.setattr(lc, "_get_anthropic_client", _fake_get_client)
    monkeypatch.setattr(lc, "_limiter_enabled", lambda: True)
    monkeypatch.setattr(lc, "_targets_zai", lambda _base: True)

    limiter_kwargs: list[dict] = []

    async def _fake_with_rate_limit_retry(create_fn, *, context, model_tier, max_total_seconds, **kwargs):
        limiter_kwargs.append(dict(kwargs))
        return await create_fn(**kwargs)

    import universal_agent.rate_limiter as rl

    monkeypatch.setattr(rl, "with_rate_limit_retry", _fake_with_rate_limit_retry)

    out = asyncio.run(lc._call_llm(system="s", user="u", model="m", temperature=0))
    assert out == "ok"
    # Forwarded through the limiter into create()'s kwargs.
    assert limiter_kwargs[0]["temperature"] == 0
    assert captured[0]["temperature"] == 0


# ── _resolve_judge_temperature ──────────────────────────────────────────────


def test_judge_temperature_default_none(monkeypatch):
    monkeypatch.delenv("UA_LLM_JUDGE_TEMPERATURE", raising=False)
    assert lc._resolve_judge_temperature() is None
    assert lc._resolve_judge_temperature("UA_INTEL_TRIAGE_TEMPERATURE") is None


def test_judge_temperature_global(monkeypatch):
    monkeypatch.setenv("UA_LLM_JUDGE_TEMPERATURE", "0")
    assert lc._resolve_judge_temperature() == 0.0


def test_judge_temperature_per_gate_overrides_global(monkeypatch):
    monkeypatch.setenv("UA_LLM_JUDGE_TEMPERATURE", "0.5")
    monkeypatch.setenv("UA_INTEL_TRIAGE_TEMPERATURE", "0")
    assert lc._resolve_judge_temperature("UA_INTEL_TRIAGE_TEMPERATURE") == 0.0


def test_judge_temperature_ignores_garbage(monkeypatch):
    monkeypatch.setenv("UA_LLM_JUDGE_TEMPERATURE", "not-a-number")
    assert lc._resolve_judge_temperature() is None


# ── _coerce_score ───────────────────────────────────────────────────────────


def test_coerce_score_clamps_and_parses():
    assert lc._coerce_score(75) == 75.0
    assert lc._coerce_score("80") == 80.0
    assert lc._coerce_score(140) == 100.0
    assert lc._coerce_score(-5) == 0.0


def test_coerce_score_missing_is_none():
    assert lc._coerce_score(None) is None
    assert lc._coerce_score("ship") is None
    assert lc._coerce_score({}) is None
