"""Test the LLM ranker pipeline (LLM call patched out)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from universal_agent.services import (
    csi_demo_triage as triage,
    csi_demo_triage_ranker as ranker,
)


def _seed_pending(conn, *, post_ids: list[str]) -> None:
    for pid in post_ids:
        conn.execute(
            """
            INSERT INTO demo_triage_candidates
              (post_id, handle, tier, action_type, packet_dir, first_seen_at, state)
            VALUES (?, 'ClaudeDevs', 3, 'demo_task', '/x', '2026-05-09T00:00:00Z', 'pending')
            """,
            (pid,),
        )
    conn.commit()


def test_run_ranking_parses_and_persists(tmp_path: Path):
    conn = triage.open_db(artifacts_root=tmp_path)
    _seed_pending(conn, post_ids=["alpha", "beta", "gamma"])

    canned = "\n".join(
        [
            '{"post_id": "alpha", "score": 8.5, "rationale": "Concrete, official, novel."}',
            '{"post_id": "beta", "score": 4.0, "rationale": "Vague."}',
            "```json",  # malformed garbage line — must be tolerated.
            '{"post_id": "gamma", "score": 6.5, "rationale": "Borderline."}',
        ]
    )

    with patch.object(ranker, "_call_llm", return_value=canned) as call:
        result = ranker.run_ranking(conn=conn, artifacts_root=tmp_path)

    assert call.called
    assert result.candidates_scored == 3
    assert result.error is None

    rows = {
        row["post_id"]: row
        for row in conn.execute(
            "SELECT post_id, ranking_score, ranking_rationale, ranking_run_id FROM demo_triage_candidates"
        )
    }
    assert rows["alpha"]["ranking_score"] == 8.5
    assert "Concrete" in (rows["alpha"]["ranking_rationale"] or "")
    assert rows["beta"]["ranking_score"] == 4.0
    assert rows["gamma"]["ranking_score"] == 6.5
    # All three must share the same run_id (single batched call).
    run_ids = {row["ranking_run_id"] for row in rows.values()}
    assert len(run_ids) == 1
    assert next(iter(run_ids)) == result.run_id
    conn.close()


def test_run_ranking_no_pending(tmp_path: Path):
    """Empty queue must short-circuit before the LLM call."""
    conn = triage.open_db(artifacts_root=tmp_path)

    with patch.object(ranker, "_call_llm") as call:
        result = ranker.run_ranking(conn=conn, artifacts_root=tmp_path)

    assert not call.called
    assert result.candidates_scored == 0
    assert result.error is None
    conn.close()


def test_run_ranking_llm_failure_marks_skipped(tmp_path: Path):
    conn = triage.open_db(artifacts_root=tmp_path)
    _seed_pending(conn, post_ids=["one", "two"])

    with patch.object(ranker, "_call_llm", side_effect=RuntimeError("z.ai 429")):
        result = ranker.run_ranking(conn=conn, artifacts_root=tmp_path)

    assert result.candidates_scored == 0
    assert result.candidates_skipped == 2
    assert result.error and "RuntimeError" in result.error
    # Rows must remain unscored.
    rows = conn.execute(
        "SELECT ranking_score FROM demo_triage_candidates"
    ).fetchall()
    assert all(row["ranking_score"] is None for row in rows)
    conn.close()
