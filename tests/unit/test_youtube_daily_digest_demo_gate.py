"""Tests for the deterministic demo-worthiness gate around
`_select_tutorial_dispatch_candidates`.

The gate sits between the LLM's `code_implementation_prospect` self-classification
and the actual dispatch to the YouTube tutorial pipeline. It exists to prevent
sales pitches, product overviews, and metadata-only judgments from being
sent to a build-tutorial agent.
"""

from __future__ import annotations

from typing import Any

from universal_agent.scripts import youtube_daily_digest as ydd


def _make_row(
    *,
    video_id: str,
    code_prospect: bool = True,
    score: int = 90,
    tier: str = "high",
    evidence: str = "transcript",
    rank: int = 1,
    title: str | None = None,
) -> dict[str, Any]:
    return {
        "video_id": video_id,
        "title": title or f"Video {video_id}",
        "value_score": score,
        "value_tier": tier,
        "code_implementation_prospect": code_prospect,
        "concept_only": not code_prospect,
        "tutorial_candidate": code_prospect,
        "recommended_tutorial_mode": "explainer_plus_code" if code_prospect else "concept_only",
        "evidence_quality": evidence,
        "reason": "test fixture",
        "rank": rank,
    }


def test_passes_when_all_signals_agree():
    row = _make_row(video_id="aaa", code_prospect=True, score=85, tier="high", evidence="transcript")
    ok, reason = ydd._is_demo_worthy(row, min_score=70)
    assert ok is True
    assert reason == "ok"


def test_rejects_when_llm_says_not_code_prospect():
    row = _make_row(video_id="aaa", code_prospect=False)
    ok, reason = ydd._is_demo_worthy(row, min_score=70)
    assert ok is False
    assert reason == "not_code_implementation_prospect"


def test_rejects_metadata_only_evidence():
    row = _make_row(video_id="aaa", code_prospect=True, evidence="metadata_only", score=95)
    ok, reason = ydd._is_demo_worthy(row, min_score=70)
    assert ok is False
    assert "evidence_quality" in reason


def test_rejects_when_score_below_threshold():
    row = _make_row(video_id="aaa", code_prospect=True, score=60, tier="medium")
    ok, reason = ydd._is_demo_worthy(row, min_score=70)
    assert ok is False
    assert "value_score" in reason and "min=70" in reason


def test_rejects_low_value_tier():
    row = _make_row(video_id="aaa", code_prospect=True, score=95, tier="low")
    ok, reason = ydd._is_demo_worthy(row, min_score=70)
    assert ok is False
    assert reason == "value_tier=low"


def test_rejects_unknown_value_tier():
    """`value_tier="unknown"` means the LLM didn't actually rank this video —
    we refuse to dispatch without a real tier signal."""
    row = _make_row(video_id="aaa", code_prospect=True, score=95, tier="unknown")
    ok, reason = ydd._is_demo_worthy(row, min_score=70)
    assert ok is False
    assert reason == "value_tier=unknown"


def test_select_returns_only_passing_candidates():
    decisions = {
        "ranked_videos": [
            _make_row(video_id="good1", rank=1, score=92),
            _make_row(video_id="metadata_only", rank=2, evidence="metadata_only", score=95),
            _make_row(video_id="lowscore", rank=3, score=55),
            _make_row(video_id="lowtier", rank=4, tier="low", score=80),
            _make_row(video_id="good2", rank=5, score=80, tier="medium"),
            _make_row(video_id="notcode", rank=6, code_prospect=False, score=88),
        ]
    }
    selected = ydd._select_tutorial_dispatch_candidates(decisions, top_n=4, min_score=70)
    assert [r["video_id"] for r in selected] == ["good1", "good2"]


def test_select_respects_top_n_and_marks_overflow():
    decisions = {
        "ranked_videos": [
            _make_row(video_id="a", rank=1, score=95),
            _make_row(video_id="b", rank=2, score=90),
            _make_row(video_id="c", rank=3, score=85),
            _make_row(video_id="d", rank=4, score=80),
        ]
    }
    selected = ydd._select_tutorial_dispatch_candidates(decisions, top_n=2, min_score=70)
    assert [r["video_id"] for r in selected] == ["a", "b"]
    statuses = {r["video_id"]: r["dispatch_status"] for r in decisions["ranked_videos"]}
    assert statuses == {
        "a": "selected",
        "b": "selected",
        "c": "eligible_overflow",
        "d": "eligible_overflow",
    }


def test_select_top_n_zero_marks_disabled():
    decisions = {"ranked_videos": [_make_row(video_id="a", rank=1, score=95)]}
    selected = ydd._select_tutorial_dispatch_candidates(decisions, top_n=0, min_score=70)
    assert selected == []
    assert decisions["ranked_videos"][0]["dispatch_status"] == "disabled_top_n_zero"


def test_select_annotates_reject_reason_on_failing_rows():
    decisions = {
        "ranked_videos": [
            _make_row(video_id="lowscore", rank=1, score=40),
            _make_row(video_id="good", rank=2, score=80),
        ]
    }
    ydd._select_tutorial_dispatch_candidates(decisions, top_n=4, min_score=70)
    lowscore = decisions["ranked_videos"][0]
    good = decisions["ranked_videos"][1]
    assert lowscore["dispatch_eligible"] is False
    assert "value_score=40" in lowscore["dispatch_reject_reason"]
    assert lowscore["dispatch_status"] == "rejected"
    assert good["dispatch_eligible"] is True
    assert good["dispatch_reject_reason"] == ""
    assert good["dispatch_status"] == "selected"


def test_min_score_override_via_kwarg():
    """A caller passing min_score=60 should let an 80-tier video through that
    would have failed the default-70 gate (regression guard for env-var path)."""
    decisions = {"ranked_videos": [_make_row(video_id="a", rank=1, score=65, tier="medium")]}
    selected_default = ydd._select_tutorial_dispatch_candidates(decisions.copy(), top_n=4)
    assert selected_default == []
    decisions2 = {"ranked_videos": [_make_row(video_id="a", rank=1, score=65, tier="medium")]}
    selected_loose = ydd._select_tutorial_dispatch_candidates(decisions2, top_n=4, min_score=60)
    assert [r["video_id"] for r in selected_loose] == ["a"]


# ---------------------------------------------------------------------------
# Dynamic top_n: tie-extension at the cutoff score.
#
# Motivation: 2026-05-30 FRIDAY digest scored 6 videos at exactly 85 with a
# static top_n=4, so ranks 5-6 (two genuinely buildable talks) were dropped
# purely by arbitrary tie-order. When the cutoff score is a tie, every eligible
# video tied at that score should dispatch — up to a hard ceiling that bounds
# build fan-out.
# ---------------------------------------------------------------------------


def test_tie_extension_rescues_videos_tied_at_cutoff_score():
    """Six eligible videos all tied at 85 with top_n=4 should dispatch all six
    (the two beyond top_n tie the cutoff score and get rescued)."""
    decisions = {
        "ranked_videos": [
            _make_row(video_id=f"v{i}", rank=i, score=85, tier="high") for i in range(1, 7)
        ]
    }
    selected = ydd._select_tutorial_dispatch_candidates(decisions, top_n=4, min_score=70)
    assert [r["video_id"] for r in selected] == ["v1", "v2", "v3", "v4", "v5", "v6"]
    statuses = {r["video_id"]: r["dispatch_status"] for r in decisions["ranked_videos"]}
    assert all(s == "selected" for s in statuses.values())
    # Rows rescued beyond top_n are flagged for observability.
    tie_extended = {r["video_id"]: r.get("dispatch_tie_extended") for r in decisions["ranked_videos"]}
    assert tie_extended["v5"] is True and tie_extended["v6"] is True
    assert tie_extended["v1"] is False


def test_tie_extension_does_not_rescue_lower_scores():
    """Only videos tied at the cutoff score are rescued; strictly-lower-scored
    eligible videos remain overflow."""
    decisions = {
        "ranked_videos": [
            _make_row(video_id="a", rank=1, score=90),
            _make_row(video_id="b", rank=2, score=85),
            _make_row(video_id="c", rank=3, score=85),  # ties cutoff -> rescued
            _make_row(video_id="d", rank=4, score=80),  # below cutoff -> overflow
        ]
    }
    selected = ydd._select_tutorial_dispatch_candidates(decisions, top_n=2, min_score=70)
    # top_n=2 selects a(90), b(85). Cutoff score = 85. c(85) ties -> rescued.
    # d(80) is below cutoff -> overflow.
    assert [r["video_id"] for r in selected] == ["a", "b", "c"]
    statuses = {r["video_id"]: r["dispatch_status"] for r in decisions["ranked_videos"]}
    assert statuses == {
        "a": "selected",
        "b": "selected",
        "c": "selected",
        "d": "eligible_overflow",
    }


def test_tie_extension_respects_max_n_ceiling():
    """Tie-extension never exceeds the hard ceiling (default 2x top_n)."""
    decisions = {
        "ranked_videos": [
            _make_row(video_id=f"v{i}", rank=i, score=85, tier="high") for i in range(1, 13)
        ]
    }
    # top_n=4 -> default ceiling 8. 12 videos tied at 85 -> only 8 dispatch.
    selected = ydd._select_tutorial_dispatch_candidates(decisions, top_n=4, min_score=70)
    assert len(selected) == 8
    overflow = [r for r in decisions["ranked_videos"] if r["dispatch_status"] == "eligible_overflow"]
    assert len(overflow) == 4


def test_tie_extension_max_n_kwarg_override():
    """An explicit max_n kwarg overrides the default ceiling."""
    decisions = {
        "ranked_videos": [
            _make_row(video_id=f"v{i}", rank=i, score=85, tier="high") for i in range(1, 7)
        ]
    }
    selected = ydd._select_tutorial_dispatch_candidates(
        decisions, top_n=4, min_score=70, max_n=5
    )
    assert len(selected) == 5


def test_no_tie_when_cutoff_is_unique_score():
    """Regression guard: distinct scores around the cutoff must NOT tie-extend
    (preserves the original top_n behaviour for non-saturated days)."""
    decisions = {
        "ranked_videos": [
            _make_row(video_id="a", rank=1, score=95),
            _make_row(video_id="b", rank=2, score=90),
            _make_row(video_id="c", rank=3, score=85),
            _make_row(video_id="d", rank=4, score=80),
        ]
    }
    selected = ydd._select_tutorial_dispatch_candidates(decisions, top_n=2, min_score=70)
    assert [r["video_id"] for r in selected] == ["a", "b"]
    statuses = {r["video_id"]: r["dispatch_status"] for r in decisions["ranked_videos"]}
    assert statuses == {
        "a": "selected",
        "b": "selected",
        "c": "eligible_overflow",
        "d": "eligible_overflow",
    }
