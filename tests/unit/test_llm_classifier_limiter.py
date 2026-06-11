"""Tests for the flag-gated ZAIRateLimiter routing of `llm_classifier._call_llm`.

One edit at the shared seam covers ~12 caller flows. The routing must:
- stay byte-identical with the flag OFF (default),
- engage ONLY for calls whose effective base URL targets the ZAI proxy
  (the per-stage `base_url` override can point at real Anthropic, whose
  429s must not consume ZAI tier slots or poison ZAI tier state),
- disable SDK-internal retries on the routed path (retry policy in one layer),
- always close the per-call client (success, failure, both flag states),
- pass the wire model through to `messages.create` while the limiter's
  keyword-only params (`model_tier`, `max_total_seconds`, `context`) never
  leak into the wrapped call's kwargs.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import universal_agent.rate_limiter as rate_limiter_mod
from universal_agent.rate_limiter import ZAIRateLimiter
import universal_agent.services.llm_classifier as llm_classifier


class _FakeClient:
    def __init__(self, fail_with: Exception | None = None):
        self.closed = False
        self.create_calls: list[dict] = []
        self._fail_with = fail_with
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.create_calls.append(kwargs)
        if self._fail_with is not None:
            raise self._fail_with
        block = SimpleNamespace(text="ok-response")
        return SimpleNamespace(content=[block])

    async def close(self):
        self.closed = True


@pytest.fixture
def fake_client_env(tmp_path, monkeypatch):
    """Stub the client factory; isolate the limiter singleton; fast knobs."""
    monkeypatch.setenv("UA_ZAI_INFERENCE_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("ZAI_MIN_INTERVAL", "0.0")
    monkeypatch.setenv("ZAI_INITIAL_BACKOFF", "0.01")
    monkeypatch.setenv("ZAI_MAX_BACKOFF", "0.02")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("UA_LLM_CLASSIFIER_LIMITER_ENABLED", raising=False)
    ZAIRateLimiter.reset_instance()

    state: dict = {"clients": [], "factory_kwargs": []}

    async def _factory(*, base_url=None, api_key=None, max_retries=None):
        state["factory_kwargs"].append(
            {"base_url": base_url, "api_key": api_key, "max_retries": max_retries}
        )
        client = _FakeClient(fail_with=state.get("fail_with"))
        state["clients"].append(client)
        return client

    monkeypatch.setattr(llm_classifier, "_get_anthropic_client", _factory)
    yield state
    ZAIRateLimiter.reset_instance()


def _spy_on_retry(monkeypatch, record: dict):
    """Wrap the real with_rate_limit_retry, recording invocations."""
    real = rate_limiter_mod.with_rate_limit_retry

    async def spy(func, *args, **kwargs):
        record["called"] = True
        record["kwargs"] = dict(kwargs)
        return await real(func, *args, **kwargs)

    monkeypatch.setattr(rate_limiter_mod, "with_rate_limit_retry", spy)


def test_flag_off_takes_direct_path(fake_client_env, monkeypatch):
    record: dict = {"called": False}
    _spy_on_retry(monkeypatch, record)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
    # Flag unset -> direct path even against ZAI.
    out = asyncio.run(llm_classifier._call_llm(system="s", user="u", model="glm-5-turbo"))
    assert out == "ok-response"
    assert record["called"] is False
    client = fake_client_env["clients"][0]
    assert client.create_calls[0]["model"] == "glm-5-turbo"
    assert client.closed is True
    # SDK retries keep their env-default behavior on the direct path.
    assert fake_client_env["factory_kwargs"][0]["max_retries"] is None


def test_flag_on_routes_zai_calls_with_tier_and_no_sdk_retries(fake_client_env, monkeypatch):
    record: dict = {"called": False}
    _spy_on_retry(monkeypatch, record)
    monkeypatch.setenv("UA_LLM_CLASSIFIER_LIMITER_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")

    out = asyncio.run(llm_classifier._call_llm(system="s", user="u", model="glm-5-turbo"))
    assert out == "ok-response"
    assert record["called"] is True
    assert record["kwargs"]["model_tier"] == "sonnet"  # wire identity: glm-5-turbo
    assert record["kwargs"]["context"] == "llm_classifier"
    assert record["kwargs"]["max_total_seconds"] == 300.0
    # The wire model reached messages.create; limiter params did not.
    client = fake_client_env["clients"][0]
    create_kwargs = client.create_calls[0]
    assert create_kwargs["model"] == "glm-5-turbo"
    assert "model_tier" not in create_kwargs
    assert "max_total_seconds" not in create_kwargs
    assert "context" not in create_kwargs
    assert client.closed is True
    # SDK-internal retries disabled: limiter owns retry policy.
    assert fake_client_env["factory_kwargs"][0]["max_retries"] == 0


def test_flag_on_default_model_buckets_as_opus(fake_client_env, monkeypatch):
    record: dict = {"called": False}
    _spy_on_retry(monkeypatch, record)
    monkeypatch.setenv("UA_LLM_CLASSIFIER_LIMITER_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
    asyncio.run(llm_classifier._call_llm(system="s", user="u"))  # model=None -> resolve_opus()
    assert record["kwargs"]["model_tier"] == "opus"


def test_flag_on_non_zai_base_url_not_routed(fake_client_env, monkeypatch):
    """The per-stage base_url override to real Anthropic must bypass the
    ZAI limiter entirely — its 429s are unrelated to the ZAI account."""
    record: dict = {"called": False}
    _spy_on_retry(monkeypatch, record)
    monkeypatch.setenv("UA_LLM_CLASSIFIER_LIMITER_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
    asyncio.run(
        llm_classifier._call_llm(
            system="s", user="u", model="claude-sonnet-4-6",
            base_url="https://api.anthropic.com",
        )
    )
    assert record["called"] is False
    assert fake_client_env["factory_kwargs"][0]["max_retries"] is None


def test_flag_on_no_base_url_at_all_not_routed(fake_client_env, monkeypatch):
    """No ANTHROPIC_BASE_URL -> default Anthropic endpoint -> not ZAI."""
    record: dict = {"called": False}
    _spy_on_retry(monkeypatch, record)
    monkeypatch.setenv("UA_LLM_CLASSIFIER_LIMITER_ENABLED", "1")
    asyncio.run(llm_classifier._call_llm(system="s", user="u", model="glm-5-turbo"))
    assert record["called"] is False


def test_client_closed_on_failure_both_flag_states(fake_client_env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
    fake_client_env["fail_with"] = RuntimeError("boom — not a rate limit")

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(llm_classifier._call_llm(system="s", user="u", model="glm-5-turbo"))
    assert fake_client_env["clients"][0].closed is True

    monkeypatch.setenv("UA_LLM_CLASSIFIER_LIMITER_ENABLED", "1")
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(llm_classifier._call_llm(system="s", user="u", model="glm-5-turbo"))
    assert fake_client_env["clients"][1].closed is True


def test_flag_on_429_retries_through_limiter(fake_client_env, monkeypatch):
    """A 1313-texted 429 from ZAI is retried by the LIMITER (gradient), and
    the client still closes after the saga exhausts."""
    monkeypatch.setenv("UA_LLM_CLASSIFIER_LIMITER_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
    fake_client_env["fail_with"] = RuntimeError(
        "Error code: 429 - [1313] Fair Usage Policy frequency limited"
    )
    with pytest.raises(RuntimeError, match="429"):
        asyncio.run(llm_classifier._call_llm(system="s", user="u", model="glm-4.5-air"))
    client = fake_client_env["clients"][0]
    assert len(client.create_calls) == 5  # limiter's max_retries attempts
    assert client.closed is True
    limiter = ZAIRateLimiter.get_instance()
    assert limiter.get_stats()["total_429s_exhausted"] == 1
    assert limiter.get_stats()["total_fup_events"] == 0  # gradient, not cliff
