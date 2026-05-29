"""Tests for the restored Track B ideation sweep.

Track A/convergence finds the same story across channels (news saturation, low
value). Track B ideation synthesizes NON-OBVIOUS abstract patterns — the higher-
value insight engine. Restored 2026-05-29 and routed through the same de-poisoned
convergence_candidate → Atlas → digest path with candidate_kind='ideation'.
See docs/proactive_signals/ideation_sweep_2026-05-29.md.
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from universal_agent.services import proactive_convergence as pc


def _sig(video_id: str, channel: str, title: str) -> dict:
    return {
        "video_id": video_id,
        "channel_name": channel,
        "channel_id": channel.lower(),
        "video_title": title,
        "primary_topics": ["misc"],
        "secondary_topics": [],
        "key_claims": [f"claim {video_id}"],
        "video_url": f"https://x/{video_id}",
        "ingested_at": "2026-05-29T00:00:00+00:00",
    }


CORPUS = [_sig(f"v{i}", f"Ch{i}", f"title {i}") for i in range(4)]


@pytest.mark.asyncio
async def test_ideation_sweep_gates_on_confidence(monkeypatch):
    monkeypatch.delenv("UA_IDEATION_MIN_CONFIDENCE", raising=False)  # floor 0.7
    high = {"narrative": "Non-obvious pattern X", "value": "why it matters",
            "confidence": 0.9, "signatures": [CORPUS[0], CORPUS[1]]}
    low = {"narrative": "weak hunch", "value": "meh",
           "confidence": 0.4, "signatures": [CORPUS[2], CORPUS[3]]}
    with patch.object(pc, "_load_recent_signatures", return_value=CORPUS), \
         patch.object(pc, "track_b_ideation_synthesis", AsyncMock(return_value=[high, low])):
        out = await pc._run_ideation_sweep_async(None, source_window_hours=72)
    assert len(out) == 1 and out[0]["narrative"] == "Non-obvious pattern X"  # low dropped


@pytest.mark.asyncio
async def test_ideation_sweep_fails_closed_on_batch_error():
    with patch.object(pc, "_load_recent_signatures", return_value=CORPUS), \
         patch.object(pc, "track_b_ideation_synthesis", AsyncMock(side_effect=RuntimeError("zai down"))):
        out = await pc._run_ideation_sweep_async(None, source_window_hours=72)
    assert out == []


@pytest.mark.asyncio
async def test_ideation_sweep_needs_two_signatures(monkeypatch):
    single = {"narrative": "x", "value": "y", "confidence": 0.9, "signatures": [CORPUS[0]]}
    with patch.object(pc, "_load_recent_signatures", return_value=CORPUS), \
         patch.object(pc, "track_b_ideation_synthesis", AsyncMock(return_value=[single])):
        out = await pc._run_ideation_sweep_async(None, source_window_hours=72)
    assert out == []  # single-signature insight is not a cross-cutting pattern


def test_write_ideation_candidate_sets_kind_and_framing(tmp_path):
    conn = sqlite3.connect(tmp_path / "activity.db")
    conn.row_factory = sqlite3.Row
    try:
        result = pc.write_convergence_candidate(
            conn,
            signatures=[CORPUS[0], CORPUS[1]],
            thesis="The manufactured-reality format has converged across rival channels.",
            value="A reusable narrative-manufacturing scorer becomes possible.",
            signal_strength=9.0,
            candidate_kind="ideation",
        )
        cid = result["candidate_id"]
        # candidate row metadata records the ideation kind + narrative
        row = conn.execute(
            "SELECT metadata_json FROM convergence_candidates WHERE candidate_id=?", (cid,)
        ).fetchone()
        meta = json.loads(row["metadata_json"])
        assert meta["candidate_kind"] == "ideation"
        assert "manufactured-reality" in meta["thesis"]
        # task carries the ideation framing for Atlas
        task = conn.execute(
            "SELECT title, description, metadata_json FROM task_hub_items WHERE source_kind='convergence_candidate'"
        ).fetchone()
        assert "ideation insight" in task["title"].lower()
        assert "NON-OBVIOUS" in task["description"]
        assert "same-story convergence" in task["description"].lower()
        tmeta = json.loads(task["metadata_json"])
        assert tmeta["candidate_kind"] == "ideation"
        assert tmeta["value"].startswith("A reusable")
    finally:
        conn.close()


def test_convergence_candidate_default_kind_unchanged(tmp_path):
    """Default (no candidate_kind) still produces a convergence candidate."""
    conn = sqlite3.connect(tmp_path / "activity.db")
    conn.row_factory = sqlite3.Row
    try:
        result = pc.write_convergence_candidate(
            conn, signatures=[CORPUS[0], CORPUS[1]], thesis="same story", signal_strength=8.0,
        )
        meta = json.loads(conn.execute(
            "SELECT metadata_json FROM convergence_candidates WHERE candidate_id=?",
            (result["candidate_id"],),
        ).fetchone()["metadata_json"])
        assert meta["candidate_kind"] == "convergence"
        task = conn.execute(
            "SELECT title FROM task_hub_items WHERE source_kind='convergence_candidate'"
        ).fetchone()
        assert "convergence candidate" in task["title"].lower()
    finally:
        conn.close()


def test_ideation_flags(monkeypatch):
    monkeypatch.delenv("UA_IDEATION_SWEEP_ENABLED", raising=False)
    assert pc._ideation_sweep_enabled() is True
    monkeypatch.setenv("UA_IDEATION_SWEEP_ENABLED", "0")
    assert pc._ideation_sweep_enabled() is False
    monkeypatch.setenv("UA_IDEATION_MIN_CONFIDENCE", "0.5")
    assert pc._ideation_min_confidence() == 0.5
    monkeypatch.setenv("UA_IDEATION_MIN_CONFIDENCE", "bad")
    assert pc._ideation_min_confidence() == 0.7
