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
def test_build_user_message_truncates_long_post_text():
    """A post longer than the text cap is truncated to exactly the cap (body
    + ellipsis) so the rendered message never exceeds _MAX_POST_TEXT_CHARS.

    Pins the _MAX_POST_TEXT_CHARS / _TRUNCATE_ELLIPSIS contract: the slice
    length is the cap minus the ellipsis width (previously the opaque 497).
    """
    long_text = "a" * 600
    cand = triage.TriageCandidate(
        post_id="long",
        handle="ClaudeDevs",
        tier=3,
        action_type="demo_task",
        post_url="/x",
        post_text=long_text,
        summary="",
        linked_sources=[],
        packet_dir="/x",
        first_seen_at="2026-05-09T00:00:00Z",
        state="pending",
    )
    msg = ranker._build_user_message([cand])
    cap = ranker._MAX_POST_TEXT_CHARS
    ellipsis = ranker._TRUNCATE_ELLIPSIS
    rendered = next(line for line in msg.splitlines() if line.startswith("post_text: "))
    body = rendered[len("post_text: "):]
    assert len(body) == cap
    assert body.endswith(ellipsis)
    assert body == long_text[: cap - len(ellipsis)] + ellipsis


def test_run_ranking_clamps_and_rounds_scores(tmp_path: Path):
    """Scores outside the 0-10 band clamp to the bounds; in-range scores round
    to one decimal. Pins _MIN_SCORE / _MAX_SCORE / _SCORE_ROUND_DECIMALS."""
    conn = triage.open_db(artifacts_root=tmp_path)
    _seed_pending(conn, post_ids=["hi", "lo", "mid"])

    canned = "\n".join(
        [
            '{"post_id": "hi", "score": 15.7, "rationale": "over max"}',
            '{"post_id": "lo", "score": -2.3, "rationale": "under min"}',
            '{"post_id": "mid", "score": 6.34, "rationale": "needs rounding"}',
        ]
    )

    with patch.object(ranker, "_call_llm", return_value=canned):
        result = ranker.run_ranking(conn=conn, artifacts_root=tmp_path)

    assert result.candidates_scored == 3
    rows = {
        row["post_id"]: row["ranking_score"]
        for row in conn.execute("SELECT post_id, ranking_score FROM demo_triage_candidates")
    }
    assert rows["hi"] == ranker._MAX_SCORE  # 15.7 -> 10.0
    assert rows["lo"] == ranker._MIN_SCORE  # -2.3 -> 0.0
    assert rows["mid"] == 6.3               # 6.34 -> round(_, 1)
    conn.close()
