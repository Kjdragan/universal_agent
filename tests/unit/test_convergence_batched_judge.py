"""Batched convergence judge — `_convergence_judge_batch_size`, the shared
`_gate_cluster_verdict` precision guards, and `_refine_clusters_batched`
(one structured-output call judging a chunk of buckets, aligned + gated).
"""

from __future__ import annotations

import json

import universal_agent.services.llm_classifier as llm_classifier
import universal_agent.services.proactive_convergence as pc


def _sig(vid: str, chan: str) -> dict:
    return {"video_id": vid, "channel_name": chan, "video_title": f"title-{vid}",
            "primary_topics": ["t"], "key_claims": []}


def test_batch_size_default_override_and_clamp(monkeypatch):
    monkeypatch.delenv("UA_CONVERGENCE_JUDGE_BATCH_SIZE", raising=False)
    assert pc._convergence_judge_batch_size() == 20
    monkeypatch.setenv("UA_CONVERGENCE_JUDGE_BATCH_SIZE", "8")
    assert pc._convergence_judge_batch_size() == 8
    monkeypatch.setenv("UA_CONVERGENCE_JUDGE_BATCH_SIZE", "999")
    assert pc._convergence_judge_batch_size() == 60  # clamped to <=60
    monkeypatch.setenv("UA_CONVERGENCE_JUDGE_BATCH_SIZE", "0")
    assert pc._convergence_judge_batch_size() == 1   # clamped to >=1
    monkeypatch.setenv("UA_CONVERGENCE_JUDGE_BATCH_SIZE", "garbage")
    assert pc._convergence_judge_batch_size() == 20  # fail-soft to default


def test_gate_cluster_verdict_precision_guards(monkeypatch):
    monkeypatch.setattr(pc, "_min_signal_strength", lambda: 7)
    bucket = [_sig("a", "C1"), _sig("b", "C2"), _sig("c", "C1")]

    # convergent, >=2 distinct channels, strength >= floor -> confirmed
    r = pc._gate_cluster_verdict(
        bucket, {"is_convergence": True, "signal_strength": 8, "thesis": "T",
                 "converging_video_ids": ["a", "b"]}, min_channels=2)
    assert r is not None and r["thesis"] == "T" and len(r["signatures"]) == 2

    # confirmed subset spans only ONE channel (a + c are both C1) -> gated out
    assert pc._gate_cluster_verdict(
        bucket, {"is_convergence": True, "signal_strength": 9, "thesis": "T",
                 "converging_video_ids": ["a", "c"]}, min_channels=2) is None

    # below strength floor -> None
    assert pc._gate_cluster_verdict(
        bucket, {"is_convergence": True, "signal_strength": 3, "thesis": "T",
                 "converging_video_ids": ["a", "b"]}, min_channels=2) is None

    # not a convergence / malformed -> None
    assert pc._gate_cluster_verdict(bucket, {"is_convergence": False}, min_channels=2) is None
    assert pc._gate_cluster_verdict(bucket, None, min_channels=2) is None


async def test_refine_clusters_batched_aligns_and_gates(monkeypatch):
    monkeypatch.setattr(pc, "_min_signal_strength", lambda: 7)
    monkeypatch.setattr(pc, "_cluster_judge_overrides", lambda: {})
    chunk = [
        [_sig("a", "C1"), _sig("b", "C2")],   # 0 -> genuine (2 channels)
        [_sig("c", "C3"), _sig("d", "C3")],   # 1 -> LLM says yes but single channel -> gated None
        [_sig("e", "C4"), _sig("f", "C5")],   # 2 -> LLM says no
    ]

    async def fake_call_llm(*, system, user, max_tokens, **kw):
        # one batched call returns a verdict per bucket_id
        return json.dumps({"verdicts": [
            {"bucket_id": 0, "is_convergence": True, "signal_strength": 9,
             "thesis": "Story0", "converging_video_ids": ["a", "b"]},
            {"bucket_id": 1, "is_convergence": True, "signal_strength": 9,
             "thesis": "Story1", "converging_video_ids": ["c", "d"]},
            {"bucket_id": 2, "is_convergence": False, "signal_strength": 0,
             "thesis": "", "converging_video_ids": []},
        ]})

    monkeypatch.setattr(llm_classifier, "_call_llm", fake_call_llm)
    out = await pc._refine_clusters_batched(chunk, min_channels=2)
    assert len(out) == 3
    assert out[0] is not None and out[0]["thesis"] == "Story0"
    assert out[1] is None  # single-channel -> precision gate drops it
    assert out[2] is None  # LLM negative


async def test_refine_clusters_batched_fails_closed_on_bad_json(monkeypatch):
    monkeypatch.setattr(pc, "_cluster_judge_overrides", lambda: {})
    chunk = [[_sig("a", "C1"), _sig("b", "C2")]]

    async def bad_call(*, system, user, max_tokens, **kw):
        return "not json at all"

    monkeypatch.setattr(llm_classifier, "_call_llm", bad_call)
    out = await pc._refine_clusters_batched(chunk, min_channels=2)
    assert out == [None]  # whole chunk fails closed, re-detected next run


async def test_refine_clusters_batched_reraises_fup(monkeypatch):
    monkeypatch.setattr(pc, "_cluster_judge_overrides", lambda: {})
    monkeypatch.setattr(pc, "_is_fup_error", lambda s: True)
    chunk = [[_sig("a", "C1"), _sig("b", "C2")]]

    async def fup_call(*, system, user, max_tokens, **kw):
        raise RuntimeError("[1313] Fair Usage Policy")

    monkeypatch.setattr(llm_classifier, "_call_llm", fup_call)
    try:
        await pc._refine_clusters_batched(chunk, min_channels=2)
        raised = False
    except RuntimeError:
        raised = True
    assert raised  # FUP re-raises so the caller's circuit breaker can trip
