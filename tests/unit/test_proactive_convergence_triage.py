"""Tests for the pre-Task-Hub editorial triage in write_convergence_candidate.

Triage (default ON via UA_INTEL_TRIAGE_ENABLED) runs a cheap LLM verdict at the
candidate-write chokepoint so ONLY 'ship' candidates create a Task Hub item.
skip/defer get a recorded verdict but NO task and NO card. A failed/garbled LLM
call yields a non-final 'retry' (verdict='') so it is re-tried next sweep.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

from universal_agent import task_hub
from universal_agent.services import proactive_convergence as pc


def _sig(video_id: str, channel: str, topic: str) -> dict:
    return {
        "video_id": video_id,
        "channel_id": channel,
        "channel_name": channel,
        "video_title": f"{topic} video {video_id}",
        "video_url": f"https://example.com/{video_id}",
        "ingested_at": "2026-01-01T00:00:00+00:00",
        "primary_topics": [topic],
        "key_claims": [f"claim about {topic}"],
    }


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    pc.ensure_schema(conn)
    task_hub.ensure_schema(conn)
    return conn


def _cid(*video_ids: str) -> str:
    seed = "|".join(sorted(video_ids)).encode()
    return "cand_" + hashlib.sha256(seed).hexdigest()[:16]


def _task_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM task_hub_items").fetchone()[0]


def _patch_triage_llm(monkeypatch, payload):
    """Patch the module-level _call_llm seam to return a JSON string (sync)."""
    text = payload if isinstance(payload, str) else json.dumps(payload)

    def _fake(**_kwargs):
        return text

    monkeypatch.setattr(pc, "_call_llm", _fake)


def test_triage_skip_creates_no_task(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_ENABLED", "1")
    _patch_triage_llm(
        monkeypatch,
        {"verdict": "skip", "reasoning": "generic / duplicate", "demo_amenable": False},
    )
    conn = _conn()
    sigs = [_sig("v1", "Chan A", "agents"), _sig("v2", "Chan B", "agents")]

    result = pc.write_convergence_candidate(conn, signatures=sigs)

    assert result["_newly_queued"] is False
    assert _task_count(conn) == 0
    row = pc._get_convergence_candidate(conn, result["candidate_id"])
    assert row["verdict"] == "skip"
    assert row["task_id"] == ""
    assert row["verdict_reasoning"] == "generic / duplicate"
    conn.close()


def test_triage_ship_creates_task(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_ENABLED", "1")
    _patch_triage_llm(
        monkeypatch,
        {"verdict": "ship", "reasoning": "real, novel pattern", "demo_amenable": True},
    )
    conn = _conn()
    sigs = [_sig("v1", "Chan A", "agents"), _sig("v2", "Chan B", "agents")]

    result = pc.write_convergence_candidate(conn, signatures=sigs)

    assert result["_newly_queued"] is True
    assert _task_count(conn) == 1
    row = pc._get_convergence_candidate(conn, result["candidate_id"])
    # verdict stays '' — downstream mission/skill finalizes it in Phase 0-1.
    assert row["verdict"] == ""
    assert row["task_id"].startswith("convergence-candidate:")
    assert row["metadata"]["triage"]["demo_amenable"] is True
    assert row["metadata"]["triage"]["kind"] == "ship"
    conn.close()


def test_triage_defer_creates_no_task(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_ENABLED", "1")
    _patch_triage_llm(
        monkeypatch,
        {"verdict": "defer", "reasoning": "under-sourced", "demo_amenable": False},
    )
    conn = _conn()
    sigs = [_sig("v1", "Chan A", "agents"), _sig("v2", "Chan B", "agents")]

    result = pc.write_convergence_candidate(conn, signatures=sigs)

    assert result["_newly_queued"] is False
    assert _task_count(conn) == 0
    row = pc._get_convergence_candidate(conn, result["candidate_id"])
    assert row["verdict"] == "defer"
    assert row["task_id"] == ""
    conn.close()


def test_triage_failure_retry_not_locked_out(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_ENABLED", "1")
    conn = _conn()
    sigs = [_sig("v1", "Chan A", "agents"), _sig("v2", "Chan B", "agents")]

    def _boom(**_kwargs):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(pc, "_call_llm", _boom)

    # First call: triage fails -> retry path -> no task, verdict='' persisted.
    result = pc.write_convergence_candidate(conn, signatures=sigs)
    assert result["_newly_queued"] is False
    assert _task_count(conn) == 0
    row = pc._get_convergence_candidate(conn, result["candidate_id"])
    assert row["verdict"] == ""
    assert row["task_id"] == ""

    # Second call: NOT locked out (verdict='' is not final) — now triage ships.
    _patch_triage_llm(
        monkeypatch,
        {"verdict": "ship", "reasoning": "now decided", "demo_amenable": False},
    )
    result2 = pc.write_convergence_candidate(conn, signatures=sigs)
    assert result2["_newly_queued"] is True
    assert _task_count(conn) == 1
    conn.close()


def test_triage_disabled_legacy_always_queues(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_ENABLED", "0")

    # Triage must not be consulted at all; make it explode if it is.
    def _boom(**_kwargs):
        raise AssertionError("triage LLM must not be called when disabled")

    monkeypatch.setattr(pc, "_call_llm", _boom)
    conn = _conn()
    sigs = [_sig("v1", "Chan A", "agents"), _sig("v2", "Chan B", "agents")]

    result = pc.write_convergence_candidate(conn, signatures=sigs)

    assert result["_newly_queued"] is True
    assert _task_count(conn) == 1
    row = pc._get_convergence_candidate(conn, result["candidate_id"])
    assert row["verdict"] == ""
    assert row["task_id"].startswith("convergence-candidate:")
    conn.close()


def test_existing_final_verdict_short_circuits(monkeypatch):
    monkeypatch.setenv("UA_INTEL_TRIAGE_ENABLED", "1")
    # If triage were consulted it would ship; prove the early-return wins.
    _patch_triage_llm(
        monkeypatch,
        {"verdict": "ship", "reasoning": "would ship", "demo_amenable": False},
    )
    conn = _conn()
    sigs = [_sig("v1", "Chan A", "agents"), _sig("v2", "Chan B", "agents")]
    cid = _cid("v1", "v2")
    # Seed a final-verdict row directly.
    now = "2026-01-01T00:00:00+00:00"
    conn.execute(
        """
        INSERT INTO convergence_candidates (
            candidate_id, detected_at, created_at, updated_at, verdict
        ) VALUES (?, ?, ?, ?, 'ship')
        """,
        (cid, now, now, now),
    )
    conn.commit()
    before = _task_count(conn)

    result = pc.write_convergence_candidate(conn, signatures=sigs)

    assert result["_newly_queued"] is False
    assert _task_count(conn) == before  # no new task
    conn.close()


def test_skip_verdict_omitted_from_task_hub(monkeypatch):
    """Sanity: the skip row never reaches Task Hub (no get_item for its task_id)."""
    monkeypatch.setenv("UA_INTEL_TRIAGE_ENABLED", "1")
    _patch_triage_llm(
        monkeypatch, {"verdict": "skip", "reasoning": "dup", "demo_amenable": False}
    )
    conn = _conn()
    sigs = [_sig("v1", "Chan A", "agents"), _sig("v2", "Chan B", "agents")]
    result = pc.write_convergence_candidate(conn, signatures=sigs)
    expected_task_id = "convergence-candidate:" + result["candidate_id"].removeprefix("cand_")
    assert task_hub.get_item(conn, expected_task_id) is None
    conn.close()
