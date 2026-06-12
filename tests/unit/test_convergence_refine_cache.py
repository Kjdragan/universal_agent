"""Tests for the Stage-2 cluster-refine TTL-bounded result cache.

Covers:
- HIT: same bucket run twice → LLM called only on run 1; reconstructed result
  matches run-1 result.
- MISS on changed video set: an added/removed video yields a new key → LLM
  called again.
- Negative caching: a bucket the LLM rejects (returns None) is cached → LLM
  skipped on run 2.
- Flag off (UA_CONVERGENCE_REFINE_CACHE_ENABLED=0) → LLM called both runs.
- TTL expiry: a row with an old ``judged_at`` is treated as a miss.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from universal_agent.services import proactive_convergence as pc

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sig(video_id: str, channel: str) -> dict:
    return {
        "video_id": video_id,
        "channel_name": channel,
        "video_title": f"Video {video_id}",
        "primary_topics": ["ai_coding"],
        "key_claims": [f"claim {video_id}"],
    }


BUCKET_AB = [_sig("v1", "ChanA"), _sig("v2", "ChanB")]
BUCKET_ABC = [_sig("v1", "ChanA"), _sig("v2", "ChanB"), _sig("v3", "ChanC")]

_GOOD_PAYLOAD = {
    "is_convergence": True,
    "thesis": "Both channels cover the same AI coding release.",
    "converging_video_ids": ["v1", "v2"],
    "signal_strength": 8,
}

_REJECT_PAYLOAD = {
    "is_convergence": False,
    "thesis": "",
    "converging_video_ids": [],
    "signal_strength": 0,
}


def _llm_mock(payload: dict) -> AsyncMock:
    import json
    return AsyncMock(return_value=json.dumps(payload))


# ---------------------------------------------------------------------------
# HIT: same bucket → LLM called once, skipped on run 2
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_hit_skips_llm_on_second_run(monkeypatch):
    """Run the same bucket through _detect_clusters_llm_async twice.
    The LLM must be called for all buckets on run 1 and ZERO times on run 2."""
    monkeypatch.delenv("UA_CONVERGENCE_REFINE_CACHE_ENABLED", raising=False)
    monkeypatch.setenv("UA_CONVERGENCE_LLM_CONCURRENCY", "4")
    monkeypatch.setenv("UA_CONVERGENCE_MIN_STRENGTH", "7")

    buckets = [BUCKET_AB]
    monkeypatch.setattr(pc, "_detect_clusters_sql", lambda *a, **k: buckets)

    call_count = {"n": 0}

    async def counting_llm(*args, **kwargs):
        import json
        call_count["n"] += 1
        return json.dumps(_GOOD_PAYLOAD)

    stats1: dict = {}
    stats2: dict = {}

    with patch("universal_agent.services.llm_classifier._call_llm", side_effect=counting_llm):
        out1 = await pc._detect_clusters_llm_async(
            None, source_window_hours=72, min_channels=2, stats=stats1
        )

    assert call_count["n"] == 1
    assert len(out1) == 1
    assert stats1.get("sonnet_calls_made") == 1
    assert stats1.get("cache_hits") == 0

    # Run 2 — same bucket; LLM must NOT be called again.
    with patch("universal_agent.services.llm_classifier._call_llm", side_effect=counting_llm):
        out2 = await pc._detect_clusters_llm_async(
            None, source_window_hours=72, min_channels=2, stats=stats2
        )

    assert call_count["n"] == 1, f"LLM was called again on run 2 (total calls={call_count['n']})"
    assert stats2.get("cache_hits") == 1
    assert stats2.get("sonnet_calls_made") == 0

    # Reconstructed result must equal run-1 result structurally.
    assert len(out2) == 1
    ids1 = {s["video_id"] for s in out1[0]["signatures"]}
    ids2 = {s["video_id"] for s in out2[0]["signatures"]}
    assert ids2 == ids1
    assert out2[0]["thesis"] == out1[0]["thesis"]
    assert out2[0]["signal_strength"] == out1[0]["signal_strength"]


# ---------------------------------------------------------------------------
# MISS on changed video set → re-judged
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_miss_on_changed_video_set(monkeypatch):
    """A bucket with an added video produces a new key → LLM called on run 2."""
    monkeypatch.delenv("UA_CONVERGENCE_REFINE_CACHE_ENABLED", raising=False)
    monkeypatch.setenv("UA_CONVERGENCE_LLM_CONCURRENCY", "4")
    monkeypatch.setenv("UA_CONVERGENCE_MIN_STRENGTH", "7")

    call_count = {"n": 0}

    async def counting_llm(*args, **kwargs):
        import json
        call_count["n"] += 1
        return json.dumps(_GOOD_PAYLOAD)

    # Run 1 with BUCKET_AB
    monkeypatch.setattr(pc, "_detect_clusters_sql", lambda *a, **k: [BUCKET_AB])
    with patch("universal_agent.services.llm_classifier._call_llm", side_effect=counting_llm):
        await pc._detect_clusters_llm_async(None, source_window_hours=72, min_channels=2)

    assert call_count["n"] == 1

    # Run 2 with BUCKET_ABC (different video set → different key → miss)
    import json as _json
    good_abc = {**_GOOD_PAYLOAD, "converging_video_ids": ["v1", "v2", "v3"]}

    async def llm_abc(*args, **kwargs):
        call_count["n"] += 1
        return _json.dumps(good_abc)

    monkeypatch.setattr(pc, "_detect_clusters_sql", lambda *a, **k: [BUCKET_ABC])
    with patch("universal_agent.services.llm_classifier._call_llm", side_effect=llm_abc):
        await pc._detect_clusters_llm_async(None, source_window_hours=72, min_channels=2)

    assert call_count["n"] == 2, "Changed bucket must re-judge (miss)"


# ---------------------------------------------------------------------------
# Negative caching: rejected bucket is cached → LLM skipped on run 2
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_negative_caching_rejected_bucket(monkeypatch):
    """A bucket the LLM rejects (None) is cached; LLM not called on run 2."""
    monkeypatch.delenv("UA_CONVERGENCE_REFINE_CACHE_ENABLED", raising=False)
    monkeypatch.setenv("UA_CONVERGENCE_LLM_CONCURRENCY", "4")
    monkeypatch.setenv("UA_CONVERGENCE_MIN_STRENGTH", "7")

    call_count = {"n": 0}

    async def rejecting_llm(*args, **kwargs):
        import json
        call_count["n"] += 1
        return json.dumps(_REJECT_PAYLOAD)

    monkeypatch.setattr(pc, "_detect_clusters_sql", lambda *a, **k: [BUCKET_AB])

    stats1: dict = {}
    stats2: dict = {}

    with patch("universal_agent.services.llm_classifier._call_llm", side_effect=rejecting_llm):
        out1 = await pc._detect_clusters_llm_async(
            None, source_window_hours=72, min_channels=2, stats=stats1
        )

    assert call_count["n"] == 1
    assert out1 == []  # LLM rejected
    assert stats1.get("sonnet_calls_made") == 1

    # Run 2 — should get a cache hit (negative) and skip the LLM entirely.
    with patch("universal_agent.services.llm_classifier._call_llm", side_effect=rejecting_llm):
        out2 = await pc._detect_clusters_llm_async(
            None, source_window_hours=72, min_channels=2, stats=stats2
        )

    assert call_count["n"] == 1, "LLM must not be called for a negatively-cached bucket"
    assert out2 == []
    assert stats2.get("cache_hits") == 1
    assert stats2.get("sonnet_calls_made") == 0


# ---------------------------------------------------------------------------
# Flag off → LLM called both runs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_disabled_always_calls_llm(monkeypatch):
    """UA_CONVERGENCE_REFINE_CACHE_ENABLED=0 → LLM called on every run."""
    monkeypatch.setenv("UA_CONVERGENCE_REFINE_CACHE_ENABLED", "0")
    monkeypatch.setenv("UA_CONVERGENCE_LLM_CONCURRENCY", "4")
    monkeypatch.setenv("UA_CONVERGENCE_MIN_STRENGTH", "7")

    call_count = {"n": 0}

    async def counting_llm(*args, **kwargs):
        import json
        call_count["n"] += 1
        return json.dumps(_GOOD_PAYLOAD)

    monkeypatch.setattr(pc, "_detect_clusters_sql", lambda *a, **k: [BUCKET_AB])

    with patch("universal_agent.services.llm_classifier._call_llm", side_effect=counting_llm):
        await pc._detect_clusters_llm_async(None, source_window_hours=72, min_channels=2)
        await pc._detect_clusters_llm_async(None, source_window_hours=72, min_channels=2)

    assert call_count["n"] == 2, "Cache disabled → LLM must be called both runs"


# ---------------------------------------------------------------------------
# TTL expiry: stale row is a miss
# ---------------------------------------------------------------------------

def test_ttl_expiry_is_a_miss(monkeypatch):
    """A cache row with judged_at older than the TTL is treated as a miss."""
    monkeypatch.setenv("UA_CONVERGENCE_REFINE_CACHE_TTL_HOURS", "1")

    key = pc._refine_cluster_key(BUCKET_AB)
    assert key  # sanity

    # Insert a stale row directly into the cache.
    stale_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    from universal_agent.durable.db import connect_runtime_db, get_activity_db_path

    with connect_runtime_db(get_activity_db_path()) as c:
        pc.ensure_schema(c)
        c.execute(
            "INSERT OR REPLACE INTO convergence_refine_cache "
            "(cluster_key, is_convergence, signal_strength, thesis, "
            " converging_video_ids_json, verdict_json, judged_at) "
            "VALUES (?, 1, 9.0, 'stale thesis', '[]', '', ?)",
            (key, stale_at),
        )

    result = pc._refine_cache_get(key)
    assert result is None, "A stale cache row (beyond TTL) must be a miss"


# ---------------------------------------------------------------------------
# env-knob helpers: default / override / clamp
# ---------------------------------------------------------------------------

def test_cache_enabled_default_on(monkeypatch):
    monkeypatch.delenv("UA_CONVERGENCE_REFINE_CACHE_ENABLED", raising=False)
    assert pc._convergence_refine_cache_enabled() is True


def test_cache_can_be_disabled(monkeypatch):
    monkeypatch.setenv("UA_CONVERGENCE_REFINE_CACHE_ENABLED", "0")
    assert pc._convergence_refine_cache_enabled() is False


def test_cache_ttl_default_and_clamp(monkeypatch):
    monkeypatch.delenv("UA_CONVERGENCE_REFINE_CACHE_TTL_HOURS", raising=False)
    assert pc._convergence_refine_cache_ttl_hours() == 24.0

    monkeypatch.setenv("UA_CONVERGENCE_REFINE_CACHE_TTL_HOURS", "garbage")
    assert pc._convergence_refine_cache_ttl_hours() == 24.0

    monkeypatch.setenv("UA_CONVERGENCE_REFINE_CACHE_TTL_HOURS", "-5")
    assert pc._convergence_refine_cache_ttl_hours() == 0.0


# ---------------------------------------------------------------------------
# _refine_cluster_key determinism
# ---------------------------------------------------------------------------

def test_refine_cluster_key_sorted_and_stable():
    b1 = [_sig("v2", "X"), _sig("v1", "Y")]
    b2 = [_sig("v1", "Y"), _sig("v2", "X")]
    # Same video set, different order → same key.
    assert pc._refine_cluster_key(b1) == pc._refine_cluster_key(b2)
    # Different set → different key.
    assert pc._refine_cluster_key(b1) != pc._refine_cluster_key(BUCKET_ABC)
