"""Tests for the P2 batched editorial triage pre-pass
(``proactive_convergence._run_batched_triage`` /
``_batched_triage_overrides_async``) and ``write_convergence_candidate``
honoring a precomputed ``triage_override``.

Mirrors ``test_wiki_facets_batched.py``: the fake patches the imported
``llm_classifier._call_llm`` OBJECT (the seam ``batched_judge`` lazily imports),
NOT a dotted string, so resolution is not test-order dependent.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from universal_agent.services import proactive_convergence as pc


def _new_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row  # _get_convergence_candidate does dict(row)
    pc.ensure_schema(conn)
    return conn


def _insert_finalized(conn: sqlite3.Connection, candidate_id: str, verdict: str) -> None:
    conn.execute(
        "INSERT INTO convergence_candidates (candidate_id, verdict, detected_at, created_at, updated_at) "
        "VALUES (?, ?, '2026-06-13T00:00:00Z', '2026-06-13T00:00:00Z', '2026-06-13T00:00:00Z')",
        (candidate_id, verdict),
    )
    conn.commit()


def _install_fake_triage_llm(monkeypatch, *, record=None, raise_exc=None, verdict_for=None):
    """Patch llm_classifier._call_llm with a deterministic triage fake.

    The fake parses the batched ``{"candidates":[{"index": i, ...}]}`` payload and
    returns ``{"verdicts":[{"index": i, "verdict": <v>, ...}]}``. ``verdict_for(c)``
    chooses the verdict per candidate (default 'skip'); a string in ``c['thesis']``
    is the convenient lever. Patch the module OBJECT (not a dotted string)."""
    import universal_agent.services.llm_classifier as llm_classifier

    async def fake_call_llm(*, system, user, max_tokens, **overrides):
        if record is not None:
            record.append({"system": system, "user": user, "overrides": dict(overrides)})
        if raise_exc is not None:
            raise raise_exc
        payload = json.loads(user)
        cands = payload["candidates"]
        verdicts = [
            {
                "index": c["index"],
                "verdict": (verdict_for(c) if verdict_for else "skip"),
                "reasoning": f"reason-{c['index']}",
                "demo_amenable": False,
            }
            for c in cands
        ]
        return json.dumps({"verdicts": verdicts})

    monkeypatch.setattr(llm_classifier, "_call_llm", fake_call_llm)


def _spec(video_id: str, *, thesis: str = "x", kind: str = "convergence") -> dict:
    return {
        "signatures": [{"video_id": video_id, "channel_name": "chan", "video_title": "t", "key_claims": ["k"]}],
        "thesis": thesis,
        "value": "",
        "candidate_kind": kind,
    }


# ── _run_batched_triage ────────────────────────────────────────────────────


def test_batched_triage_maps_each_candidate(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_BATCH_SIZE", "20")
    _install_fake_triage_llm(monkeypatch, verdict_for=lambda c: c["thesis"])
    conn = _new_conn()
    specs = [
        _spec("v_ship", thesis="ship"),
        _spec("v_skip", thesis="skip"),
        _spec("v_defer", thesis="defer"),
    ]
    out = pc._run_batched_triage(conn, specs, idx_text="INDEX")
    cid = pc._candidate_id_for_signatures
    assert out[cid([{"video_id": "v_ship"}])]["kind"] == "ship"
    assert out[cid([{"video_id": "v_skip"}])]["kind"] == "skip"
    assert out[cid([{"video_id": "v_defer"}])]["kind"] == "defer"
    # Triage-dict shape matches what triage_candidate returns.
    assert set(out[cid([{"video_id": "v_ship"}])]) == {"kind", "reasoning", "demo_amenable", "model"}


def test_batched_triage_single_call_for_whole_chunk(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_BATCH_SIZE", "20")
    record: list = []
    _install_fake_triage_llm(monkeypatch, record=record, verdict_for=lambda c: "skip")
    conn = _new_conn()
    specs = [_spec(f"v{i}") for i in range(5)]
    out = pc._run_batched_triage(conn, specs, idx_text="INDEX")
    assert len(out) == 5
    assert len(record) == 1  # one batched call for five candidates (the token win)


def test_batched_triage_lifts_index_into_system_once(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_BATCH_SIZE", "20")
    record: list = []
    _install_fake_triage_llm(monkeypatch, record=record, verdict_for=lambda c: "skip")
    conn = _new_conn()
    specs = [_spec(f"v{i}") for i in range(3)]
    pc._run_batched_triage(conn, specs, idx_text="UNIQUE_INDEX_MARKER")
    assert len(record) == 1
    r = record[0]
    # The shared recent_briefs_index is in the SYSTEM prompt exactly once...
    assert "UNIQUE_INDEX_MARKER" in r["system"]
    # ...and NOT repeated inside each candidate's user payload (the amortized cost).
    assert r["user"].count("UNIQUE_INDEX_MARKER") == 0
    payload = json.loads(r["user"])
    for c in payload["candidates"]:
        assert "recent_briefs_index" not in c


def test_batched_triage_out_of_vocab_is_retry(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_BATCH_SIZE", "20")
    _install_fake_triage_llm(monkeypatch, verdict_for=lambda c: "garbage")
    conn = _new_conn()
    out = pc._run_batched_triage(conn, [_spec("v0")], idx_text="I")
    cid = pc._candidate_id_for_signatures([{"video_id": "v0"}])
    # An out-of-vocab verdict is a per-item clean miss → fail-closed to 'retry'
    # (no task, verdict='', re-tried next sweep) — identical to triage_candidate.
    assert out[cid]["kind"] == "retry"


def test_batched_triage_call_failure_all_retry(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_BATCH_SIZE", "20")
    _install_fake_triage_llm(monkeypatch, raise_exc=RuntimeError("llm down, not fair-usage"))
    conn = _new_conn()
    specs = [_spec(f"v{i}") for i in range(3)]
    out = pc._run_batched_triage(conn, specs, idx_text="I")
    assert len(out) == 3
    assert all(v["kind"] == "retry" for v in out.values())


def test_batched_triage_excludes_finalized(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_BATCH_SIZE", "20")
    record: list = []
    _install_fake_triage_llm(monkeypatch, record=record, verdict_for=lambda c: "skip")
    conn = _new_conn()
    cid_final = pc._candidate_id_for_signatures([{"video_id": "vfinal"}])
    _insert_finalized(conn, cid_final, verdict="ship")  # already finalized downstream
    out = pc._run_batched_triage(conn, [_spec("vfinal"), _spec("vnew")], idx_text="I")
    # Finalized candidate never enters the batch (would short-circuit anyway)...
    assert cid_final not in out
    assert out[pc._candidate_id_for_signatures([{"video_id": "vnew"}])]["kind"] == "skip"
    # ...so only the un-finalized candidate is sent to the LLM.
    assert len(record) == 1
    assert len(json.loads(record[0]["user"])["candidates"]) == 1


def test_batched_triage_uses_haiku_tier(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_BATCH_SIZE", "20")
    monkeypatch.delenv("UA_INTEL_TRIAGE_MODEL", raising=False)
    record: list = []
    _install_fake_triage_llm(monkeypatch, record=record, verdict_for=lambda c: "skip")
    conn = _new_conn()
    pc._run_batched_triage(conn, [_spec("v0")], idx_text="I")
    from universal_agent.utils.model_resolution import resolve_haiku

    assert record[0]["overrides"].get("model") == resolve_haiku()


def test_batched_triage_model_override_env(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_BATCH_SIZE", "20")
    monkeypatch.setenv("UA_INTEL_TRIAGE_MODEL", "custom-triage-model")
    record: list = []
    _install_fake_triage_llm(monkeypatch, record=record, verdict_for=lambda c: "skip")
    conn = _new_conn()
    pc._run_batched_triage(conn, [_spec("v0")], idx_text="I")
    assert record[0]["overrides"].get("model") == "custom-triage-model"


def test_batched_triage_empty_specs_no_call(monkeypatch):
    record: list = []
    _install_fake_triage_llm(monkeypatch, record=record)
    conn = _new_conn()
    assert pc._run_batched_triage(conn, [], idx_text="I") == {}
    assert record == []


# ── write_convergence_candidate(triage_override=...) ───────────────────────


def test_write_candidate_override_skip_no_task(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_ENABLED", "1")

    def _boom(*a, **k):
        raise AssertionError("triage_candidate must NOT run when an override is supplied")

    monkeypatch.setattr(pc, "triage_candidate", _boom)
    conn = _new_conn()
    sigs = [{"video_id": "v0", "channel_name": "c", "video_title": "t"}]
    row = pc.write_convergence_candidate(
        conn, signatures=sigs,
        triage_override={"kind": "skip", "reasoning": "dup", "demo_amenable": False, "model": "m"},
    )
    assert row["verdict"] == "skip"
    assert row["verdict_reasoning"] == "dup"
    assert not row["_newly_queued"]


def test_write_candidate_override_retry_keeps_unfinalized(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_ENABLED", "1")
    monkeypatch.setattr(pc, "triage_candidate", lambda *a, **k: pytest.fail("override path only"))
    conn = _new_conn()
    sigs = [{"video_id": "v0"}]
    row = pc.write_convergence_candidate(
        conn, signatures=sigs,
        triage_override={"kind": "retry", "reasoning": "unavail", "demo_amenable": False, "model": "m"},
    )
    # retry ⇒ verdict stays '' (not final) and no task — re-tried next sweep.
    assert row["verdict"] == ""
    assert not row["_newly_queued"]


def test_write_candidate_override_ship_queues_task(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_ENABLED", "1")
    monkeypatch.setattr(pc, "triage_candidate", lambda *a, **k: pytest.fail("override path only"))
    calls: list = []

    def _fake_queue(conn, **kwargs):
        calls.append(kwargs)
        return {"task_id": kwargs.get("task_id"), "status": "queued"}

    monkeypatch.setattr(pc, "queue_proactive_task", _fake_queue)
    conn = _new_conn()
    sigs = [{"video_id": "v0", "channel_name": "c", "video_title": "t"}]
    row = pc.write_convergence_candidate(
        conn, signatures=sigs,
        triage_override={"kind": "ship", "reasoning": "novel", "demo_amenable": True, "model": "m"},
    )
    assert len(calls) == 1  # a Task Hub item was queued
    # ship leaves verdict='' so the downstream mission/skill finalizes it.
    assert row["verdict"] == ""
    assert row["_newly_queued"]
    # The triage decision is recorded in metadata for the downstream consumer.
    meta = json.loads(row["metadata_json"]) if isinstance(row.get("metadata_json"), str) else row.get("metadata", {})
    assert meta.get("triage", {}).get("kind") == "ship"


def test_write_candidate_finalized_short_circuit_ignores_override(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_ENABLED", "1")
    monkeypatch.setattr(pc, "triage_candidate", lambda *a, **k: pytest.fail("finalized must short-circuit"))
    conn = _new_conn()
    cid = pc._candidate_id_for_signatures([{"video_id": "v0"}])
    _insert_finalized(conn, cid, verdict="skip")
    row = pc.write_convergence_candidate(
        conn, signatures=[{"video_id": "v0"}],
        triage_override={"kind": "ship", "reasoning": "x", "demo_amenable": False, "model": "m"},
    )
    # Already-finalized ⇒ no-op; the override is irrelevant.
    assert row["verdict"] == "skip"
    assert not row["_newly_queued"]
