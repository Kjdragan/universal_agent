"""Tests for the LLM precision layer on convergence clustering.

Background: the SQL recall step (`_detect_clusters_sql`) buckets by a coarse
`primary_topic` tag, which lumps unrelated content together — Atlas then
correctly skipped 100% of candidates and the digest never got fuel. The LLM
refine pass (`_refine_cluster_with_llm`, routed to ZAI) judges whether a bucket
genuinely converges on one specific thesis and gates on signal strength.
See docs/proactive_signals/llm_convergence_clustering_2026-05-29.md.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from universal_agent.services import proactive_convergence as pc


def _sig(video_id: str, channel: str, title: str) -> dict:
    return {
        "video_id": video_id,
        "channel_name": channel,
        "video_title": title,
        "primary_topics": ["ai_coding"],
        "key_claims": [f"claim about {title}"],
    }


# A coarse bucket: 3 videos, 3 distinct channels, sharing the broad "ai_coding"
# tag — but (per the scenario) only 2 truly converge on one specific story.
BUCKET = [
    _sig("v1", "ChannelA", "Anthropic ships Claude skills for coding agents"),
    _sig("v2", "ChannelB", "How Claude skills change coding agent workflows"),
    _sig("v3", "ChannelC", "Best sourdough starter techniques"),  # off-topic
]


def _llm_returning(payload: dict) -> AsyncMock:
    return AsyncMock(return_value=json.dumps(payload))


@pytest.mark.asyncio
async def test_refine_confirms_genuine_convergence():
    payload = {
        "is_convergence": True,
        "thesis": "Two independent channels cover Anthropic's new Claude coding skills.",
        "converging_video_ids": ["v1", "v2"],
        "signal_strength": 8,
    }
    with patch("universal_agent.services.llm_classifier._call_llm", _llm_returning(payload)):
        result = await pc._refine_cluster_with_llm(BUCKET, min_channels=2)
    assert result is not None
    assert result["signal_strength"] == 8
    assert "Claude coding skills" in result["thesis"]
    ids = {s["video_id"] for s in result["signatures"]}
    assert ids == {"v1", "v2"}  # off-topic v3 dropped


@pytest.mark.asyncio
async def test_refine_rejects_non_convergence():
    payload = {"is_convergence": False, "thesis": "", "converging_video_ids": [], "signal_strength": 0}
    with patch("universal_agent.services.llm_classifier._call_llm", _llm_returning(payload)):
        assert await pc._refine_cluster_with_llm(BUCKET, min_channels=2) is None


@pytest.mark.asyncio
async def test_refine_rejects_below_strength_floor():
    payload = {
        "is_convergence": True,
        "thesis": "loosely related",
        "converging_video_ids": ["v1", "v2"],
        "signal_strength": 5,  # below default floor of 7
    }
    with patch("universal_agent.services.llm_classifier._call_llm", _llm_returning(payload)):
        assert await pc._refine_cluster_with_llm(BUCKET, min_channels=2) is None


@pytest.mark.asyncio
async def test_refine_rejects_single_channel_subset():
    # LLM "confirms" but the converging subset is a single channel — not convergence.
    payload = {
        "is_convergence": True,
        "thesis": "one channel only",
        "converging_video_ids": ["v1"],
        "signal_strength": 9,
    }
    with patch("universal_agent.services.llm_classifier._call_llm", _llm_returning(payload)):
        assert await pc._refine_cluster_with_llm(BUCKET, min_channels=2) is None


@pytest.mark.asyncio
async def test_refine_fails_closed_on_llm_error():
    # If the judge errors, emit NO candidate (precision-first).
    with patch("universal_agent.services.llm_classifier._call_llm", AsyncMock(side_effect=RuntimeError("zai down"))):
        assert await pc._refine_cluster_with_llm(BUCKET, min_channels=2) is None


@pytest.mark.asyncio
async def test_refine_respects_lower_strength_env(monkeypatch):
    monkeypatch.setenv("UA_CONVERGENCE_MIN_STRENGTH", "4")
    payload = {
        "is_convergence": True,
        "thesis": "ok",
        "converging_video_ids": ["v1", "v2"],
        "signal_strength": 5,
    }
    with patch("universal_agent.services.llm_classifier._call_llm", _llm_returning(payload)):
        result = await pc._refine_cluster_with_llm(BUCKET, min_channels=2)
    assert result is not None  # 5 >= floor of 4


def test_llm_clustering_enabled_default_on(monkeypatch):
    monkeypatch.delenv("UA_CONVERGENCE_LLM_CLUSTERING", raising=False)
    assert pc._llm_clustering_enabled() is True


def test_llm_clustering_can_be_disabled(monkeypatch):
    monkeypatch.setenv("UA_CONVERGENCE_LLM_CLUSTERING", "0")
    assert pc._llm_clustering_enabled() is False


def test_min_signal_strength_default_and_clamp(monkeypatch):
    monkeypatch.delenv("UA_CONVERGENCE_MIN_STRENGTH", raising=False)
    assert pc._min_signal_strength() == 7
    monkeypatch.setenv("UA_CONVERGENCE_MIN_STRENGTH", "99")
    assert pc._min_signal_strength() == 10  # clamped
    monkeypatch.setenv("UA_CONVERGENCE_MIN_STRENGTH", "garbage")
    assert pc._min_signal_strength() == 7  # fallback


def test_cluster_judge_overrides_env(monkeypatch):
    for var in (
        "UA_CONVERGENCE_JUDGE_MODEL",
        "UA_CONVERGENCE_JUDGE_BASE_URL",
        "UA_CONVERGENCE_JUDGE_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    # All unset → the judge defaults to the sonnet tier (glm-5-turbo via
    # resolve_sonnet) — NOT opus. base_url/api_key stay on the shared ZAI env.
    # (2026-06-10: A/B showed sonnet matches opus precision at lower cost/latency.)
    from universal_agent.utils.model_resolution import resolve_sonnet

    assert pc._cluster_judge_overrides() == {"model": resolve_sonnet()}

    monkeypatch.setenv("UA_CONVERGENCE_JUDGE_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("UA_CONVERGENCE_JUDGE_BASE_URL", "https://api.anthropic.com")
    monkeypatch.setenv("UA_CONVERGENCE_JUDGE_API_KEY", "sk-test")
    assert pc._cluster_judge_overrides() == {
        "model": "claude-sonnet-4-6",
        "base_url": "https://api.anthropic.com",
        "api_key": "sk-test",
    }

    # Partial override (model only) is allowed — provider stays on the default.
    monkeypatch.delenv("UA_CONVERGENCE_JUDGE_BASE_URL", raising=False)
    monkeypatch.delenv("UA_CONVERGENCE_JUDGE_API_KEY", raising=False)
    assert pc._cluster_judge_overrides() == {"model": "claude-sonnet-4-6"}
