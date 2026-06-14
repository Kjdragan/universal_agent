"""Phase 1 of the graded-judge redesign: the triage gate gains an opt-in graded
0-100 score + code-side threshold path, activated ONLY when
``UA_INTEL_TRIAGE_SHIP_THRESHOLD`` is set. Unset ⇒ the legacy categorical
ship/skip/defer path (pinned green by ``test_proactive_convergence_triage.py``).

Covers BOTH call sites — per-candidate ``triage_candidate`` and the batched
``_run_batched_triage`` — plus the temperature forwarding the graded gate relies
on for determinism.
"""

from __future__ import annotations

import json
import sqlite3

from universal_agent.services import proactive_convergence as pc


def _sig(video_id: str, channel: str = "chan", topic: str = "agents") -> dict:
    return {
        "video_id": video_id,
        "channel_id": channel,
        "channel_name": channel,
        "video_title": f"{topic} {video_id}",
        "key_claims": [f"claim about {topic}"],
    }


def _spec(video_id: str, *, thesis: str = "x", kind: str = "convergence") -> dict:
    return {
        "signatures": [_sig(video_id)],
        "thesis": thesis,
        "value": "",
        "candidate_kind": kind,
    }


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    pc.ensure_schema(conn)
    return conn


# ── per-candidate triage_candidate (graded) ─────────────────────────────────


def _patch_call_llm(monkeypatch, payload, record=None):
    text = payload if isinstance(payload, str) else json.dumps(payload)

    def _fake(**kwargs):
        if record is not None:
            record.append(dict(kwargs))
        return text

    monkeypatch.setattr(pc, "_call_llm", _fake)


def test_graded_triage_ship(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", "70")
    record: list = []
    _patch_call_llm(monkeypatch, {"score": 82, "reasoning": "strong+novel", "demo_amenable": True}, record)
    conn = _conn()
    out = pc.triage_candidate(conn, candidate_kind="convergence", thesis="t", value="", signatures=[_sig("v1"), _sig("v2", "B")])
    assert out["kind"] == "ship"
    assert out["score"] == 82.0
    assert out["demo_amenable"] is True
    # The GRADED prompt (asks for a score), not the categorical verdict prompt.
    assert "SCORE how worth" in record[0]["system"]


def test_graded_triage_skip_below_threshold(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", "70")
    _patch_call_llm(monkeypatch, {"score": 40, "reasoning": "thin", "demo_amenable": False})
    conn = _conn()
    out = pc.triage_candidate(conn, candidate_kind="convergence", thesis="t", value="", signatures=[_sig("v1")])
    assert out["kind"] == "skip"
    assert out["score"] == 40.0


def test_graded_triage_defer_band(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", "70")
    monkeypatch.setenv("UA_INTEL_TRIAGE_DEFER_THRESHOLD", "50")
    _patch_call_llm(monkeypatch, {"score": 60, "reasoning": "promising", "demo_amenable": False})
    conn = _conn()
    out = pc.triage_candidate(conn, candidate_kind="convergence", thesis="t", value="", signatures=[_sig("v1")])
    assert out["kind"] == "defer"


def test_graded_triage_missing_score_is_retry(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", "70")
    _patch_call_llm(monkeypatch, {"reasoning": "no score field", "demo_amenable": False})
    conn = _conn()
    out = pc.triage_candidate(conn, candidate_kind="convergence", thesis="t", value="", signatures=[_sig("v1")])
    # Un-decidable verdict ⇒ fail-closed 'retry' (no task, re-tried next sweep).
    assert out["kind"] == "retry"


def test_graded_triage_forwards_temperature(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", "70")
    monkeypatch.setenv("UA_LLM_JUDGE_TEMPERATURE", "0")
    record: list = []
    _patch_call_llm(monkeypatch, {"score": 50, "reasoning": "x", "demo_amenable": False}, record)
    conn = _conn()
    pc.triage_candidate(conn, candidate_kind="convergence", thesis="t", value="", signatures=[_sig("v1")])
    assert record[0]["temperature"] == 0.0


def test_categorical_default_unaffected(monkeypatch):
    """Threshold UNSET ⇒ categorical prompt + verdict parsing + NO temperature."""
    monkeypatch.delenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", raising=False)
    monkeypatch.delenv("UA_LLM_JUDGE_TEMPERATURE", raising=False)
    record: list = []
    _patch_call_llm(monkeypatch, {"verdict": "ship", "reasoning": "x", "demo_amenable": False}, record)
    conn = _conn()
    out = pc.triage_candidate(conn, candidate_kind="convergence", thesis="t", value="", signatures=[_sig("v1")])
    assert out["kind"] == "ship"
    assert "score" not in out  # categorical return shape unchanged
    assert record[0].get("temperature") is None
    assert "SCORE how worth" not in record[0]["system"]


# ── batched _run_batched_triage (graded) ────────────────────────────────────


def _install_fake_graded_batch(monkeypatch, *, record=None, score_for=None):
    import universal_agent.services.llm_classifier as llm_classifier

    async def fake_call_llm(*, system, user, max_tokens, **overrides):
        if record is not None:
            record.append({"system": system, "user": user, "overrides": dict(overrides)})
        payload = json.loads(user)
        verdicts = [
            {
                "index": c["index"],
                "score": (score_for(c) if score_for else 80),
                "reasoning": f"r{c['index']}",
                "demo_amenable": False,
            }
            for c in payload["candidates"]
        ]
        return json.dumps({"verdicts": verdicts})

    monkeypatch.setattr(llm_classifier, "_call_llm", fake_call_llm)


def test_batched_graded_threshold(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_BATCH_SIZE", "20")
    monkeypatch.setenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", "70")
    record: list = []
    _install_fake_graded_batch(monkeypatch, record=record, score_for=lambda c: int(c["thesis"]))
    conn = _conn()
    specs = [_spec("v_high", thesis="85"), _spec("v_low", thesis="40")]
    out = pc._run_batched_triage(conn, specs, idx_text="IDX")
    cid = pc._candidate_id_for_signatures
    assert out[cid([{"video_id": "v_high"}])]["kind"] == "ship"
    assert out[cid([{"video_id": "v_high"}])]["score"] == 85.0
    assert out[cid([{"video_id": "v_low"}])]["kind"] == "skip"
    # graded BATCH prompt; no temperature override by default.
    assert "SCORE EACH candidate" in record[0]["system"]
    assert "temperature" not in record[0]["overrides"]


def test_batched_graded_defer_band(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_BATCH_SIZE", "20")
    monkeypatch.setenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", "70")
    monkeypatch.setenv("UA_INTEL_TRIAGE_DEFER_THRESHOLD", "50")
    _install_fake_graded_batch(monkeypatch, score_for=lambda c: int(c["thesis"]))
    conn = _conn()
    out = pc._run_batched_triage(conn, [_spec("v_mid", thesis="60")], idx_text="IDX")
    assert out[pc._candidate_id_for_signatures([{"video_id": "v_mid"}])]["kind"] == "defer"


def test_batched_graded_missing_score_is_retry(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_BATCH_SIZE", "20")
    monkeypatch.setenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", "70")
    import universal_agent.services.llm_classifier as llm_classifier

    async def fake(*, system, user, max_tokens, **overrides):
        payload = json.loads(user)
        return json.dumps({"verdicts": [{"index": c["index"], "reasoning": "no score"} for c in payload["candidates"]]})

    monkeypatch.setattr(llm_classifier, "_call_llm", fake)
    conn = _conn()
    out = pc._run_batched_triage(conn, [_spec("v0")], idx_text="I")
    assert out[pc._candidate_id_for_signatures([{"video_id": "v0"}])]["kind"] == "retry"


def test_batched_graded_forwards_temperature(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_BATCH_SIZE", "20")
    monkeypatch.setenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", "70")
    monkeypatch.setenv("UA_LLM_JUDGE_TEMPERATURE", "0")
    record: list = []
    _install_fake_graded_batch(monkeypatch, record=record)
    conn = _conn()
    pc._run_batched_triage(conn, [_spec("v0")], idx_text="I")
    assert record[0]["overrides"].get("temperature") == 0.0


# ── boundaries / thresholds / determinism wiring (review fast-follow) ────────


def test_graded_triage_score_at_ship_threshold_ships(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", "70")
    _patch_call_llm(monkeypatch, {"score": 70, "reasoning": "exactly at cutoff", "demo_amenable": False})
    conn = _conn()
    out = pc.triage_candidate(conn, candidate_kind="convergence", thesis="t", value="", signatures=[_sig("v1")])
    assert out["kind"] == "ship"  # >= cutoff


def test_graded_triage_score_at_defer_threshold_defers(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", "70")
    monkeypatch.setenv("UA_INTEL_TRIAGE_DEFER_THRESHOLD", "50")
    _patch_call_llm(monkeypatch, {"score": 50, "reasoning": "exactly at defer", "demo_amenable": False})
    conn = _conn()
    out = pc.triage_candidate(conn, candidate_kind="convergence", thesis="t", value="", signatures=[_sig("v1")])
    assert out["kind"] == "defer"  # >= defer, < ship


def test_graded_triage_defer_ge_ship_fails_safe_to_skip(monkeypatch):
    # Misconfiguration: defer >= ship → the defer band is suppressed (fail safe).
    monkeypatch.setenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", "70")
    monkeypatch.setenv("UA_INTEL_TRIAGE_DEFER_THRESHOLD", "70")
    _patch_call_llm(monkeypatch, {"score": 60, "reasoning": "below ship", "demo_amenable": False})
    conn = _conn()
    out = pc.triage_candidate(conn, candidate_kind="convergence", thesis="t", value="", signatures=[_sig("v1")])
    assert out["kind"] == "skip"


def test_graded_triage_per_gate_temperature_wins(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", "70")
    monkeypatch.setenv("UA_LLM_JUDGE_TEMPERATURE", "0.5")
    monkeypatch.setenv("UA_INTEL_TRIAGE_TEMPERATURE", "0")
    record: list = []
    _patch_call_llm(monkeypatch, {"score": 50, "reasoning": "x", "demo_amenable": False}, record)
    conn = _conn()
    pc.triage_candidate(conn, candidate_kind="convergence", thesis="t", value="", signatures=[_sig("v1")])
    assert record[0]["temperature"] == 0.0  # per-gate overrides the global


def test_categorical_global_temperature_still_forwards(monkeypatch):
    # Threshold UNSET (categorical) but a global judge temperature SET → it still
    # forwards. The determinism knob is INDEPENDENT of the graded switch (intended).
    monkeypatch.delenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", raising=False)
    monkeypatch.setenv("UA_LLM_JUDGE_TEMPERATURE", "0")
    record: list = []
    _patch_call_llm(monkeypatch, {"verdict": "ship", "reasoning": "x", "demo_amenable": False}, record)
    conn = _conn()
    out = pc.triage_candidate(conn, candidate_kind="convergence", thesis="t", value="", signatures=[_sig("v1")])
    assert out["kind"] == "ship"
    assert record[0]["temperature"] == 0.0


def test_intel_thresholds_unset_garbage_and_clamp(monkeypatch):
    monkeypatch.delenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", raising=False)
    assert pc._intel_ship_threshold() is None
    monkeypatch.setenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", "garbage")
    assert pc._intel_ship_threshold() is None
    monkeypatch.setenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", "150")
    assert pc._intel_ship_threshold() == 100
    monkeypatch.setenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", "-5")
    assert pc._intel_ship_threshold() == 0
    monkeypatch.delenv("UA_INTEL_TRIAGE_DEFER_THRESHOLD", raising=False)
    assert pc._intel_defer_threshold() is None
    monkeypatch.setenv("UA_INTEL_TRIAGE_DEFER_THRESHOLD", "x")
    assert pc._intel_defer_threshold() is None


def test_batched_graded_score_string_and_float(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_BATCH_SIZE", "20")
    monkeypatch.setenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", "70")
    import universal_agent.services.llm_classifier as llm_classifier

    scores = {"v_str": "85", "v_float": 85.5, "v_low": "40"}

    async def fake(*, system, user, max_tokens, **overrides):
        payload = json.loads(user)
        verdicts = [
            {"index": c["index"], "score": scores[c["thesis"]], "reasoning": "r", "demo_amenable": False}
            for c in payload["candidates"]
        ]
        return json.dumps({"verdicts": verdicts})

    monkeypatch.setattr(llm_classifier, "_call_llm", fake)
    conn = _conn()
    specs = [_spec("v_str", thesis="v_str"), _spec("v_float", thesis="v_float"), _spec("v_low", thesis="v_low")]
    out = pc._run_batched_triage(conn, specs, idx_text="I")
    cid = pc._candidate_id_for_signatures
    assert out[cid([{"video_id": "v_str"}])]["kind"] == "ship"
    assert out[cid([{"video_id": "v_str"}])]["score"] == 85.0  # numeric string coerced
    assert out[cid([{"video_id": "v_float"}])]["kind"] == "ship"
    assert out[cid([{"video_id": "v_float"}])]["score"] == 85.5  # float preserved
    assert out[cid([{"video_id": "v_low"}])]["kind"] == "skip"


def test_batched_graded_per_gate_temperature_wins(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_BATCH_SIZE", "20")
    monkeypatch.setenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", "70")
    monkeypatch.setenv("UA_LLM_JUDGE_TEMPERATURE", "0.5")
    monkeypatch.setenv("UA_INTEL_TRIAGE_TEMPERATURE", "0")
    record: list = []
    _install_fake_graded_batch(monkeypatch, record=record)
    conn = _conn()
    pc._run_batched_triage(conn, [_spec("v0")], idx_text="I")
    assert record[0]["overrides"].get("temperature") == 0.0  # per-gate overrides global


# ── score persisted into metadata.triage (the P1 fix) ───────────────────────


def test_graded_ship_persists_score_in_metadata(monkeypatch):
    from universal_agent import task_hub

    monkeypatch.setenv("UA_INTEL_TRIAGE_ENABLED", "1")
    monkeypatch.setenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", "70")
    _patch_call_llm(monkeypatch, {"score": 82, "reasoning": "x", "demo_amenable": False})
    conn = _conn()
    task_hub.ensure_schema(conn)
    result = pc.write_convergence_candidate(conn, signatures=[_sig("v1"), _sig("v2", "B")])
    row = pc._get_convergence_candidate(conn, result["candidate_id"])
    assert row["metadata"]["triage"]["kind"] == "ship"
    assert row["metadata"]["triage"]["score"] == 82.0  # graded provenance persisted


def test_categorical_ship_omits_score_in_metadata(monkeypatch):
    from universal_agent import task_hub

    monkeypatch.setenv("UA_INTEL_TRIAGE_ENABLED", "1")
    monkeypatch.delenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD", raising=False)
    _patch_call_llm(monkeypatch, {"verdict": "ship", "reasoning": "x", "demo_amenable": False})
    conn = _conn()
    task_hub.ensure_schema(conn)
    result = pc.write_convergence_candidate(conn, signatures=[_sig("v1"), _sig("v2", "B")])
    row = pc._get_convergence_candidate(conn, result["candidate_id"])
    assert "score" not in row["metadata"]["triage"]  # categorical shape unchanged
