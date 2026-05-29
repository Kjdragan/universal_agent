"""Guards against tutorial-build misroutes (e.g. news clips reaching CODIE).

Real-world failure: a Kanal13 geopolitical news clip "Israeli strikes hit
southern Lebanon..." was auto-routed into the tutorial-build lane because
substring `"api"` matched inside an unrelated word and there were no upstream
gates.

The fix is two-layer:
  1. Cheap deterministic PREFILTER — reject obvious-no cases (news category,
     drama/reaction/podcast/vlog tokens) without burning an LLM call.
  2. LLM JUDGE — for everything that survives the prefilter, read CSI's
     Claude-distilled transcript summary and ask the model whether this is
     something a coding agent could actually build a working demo from.
     Verdicts are cached per video_id.

These tests cover both layers and the integration boundary.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from universal_agent.services.proactive_tutorial_builds import (
    _ensure_judge_table,
    _get_cached_judge_verdict,
    _looks_build_oriented as prefilter,
    is_video_buildable_with_judge,
)

# ── Prefilter (cheap, deterministic) ────────────────────────────────────────

def test_prefilter_blocks_news_category():
    """A news-category clip never reaches the LLM judge."""
    subject = {
        "title": "Israeli strikes hit southern Lebanon areas despite ceasefire extension",
        "description": "Rapid escalation, capital cities affected.",
        "channel_name": "Kanal13",
    }
    assert not prefilter(subject=subject, analysis={}, category="news", summary="")


def test_prefilter_blocks_negative_tokens():
    """Drama / reaction / podcast / vlog tokens block the prefilter."""
    subject = {"title": "Drama reaction to the new Python API", "description": "", "channel_name": "X"}
    assert not prefilter(subject=subject, analysis={}, category="", summary="")


def test_prefilter_lets_ambiguous_through_for_llm_to_decide():
    """The prefilter is intentionally permissive — the LLM judge makes the call."""
    subject = {
        "title": "My day in the city",  # not obviously coding
        "description": "Talking about tech.",
        "channel_name": "Vlogger",
    }
    # Note: this title has no positive tokens, but the prefilter no longer
    # requires them. It only blocks on category + negative tokens. The LLM
    # judge is what decides downstream.
    assert prefilter(subject=subject, analysis={}, category="education", summary="A walkthrough.")


def test_prefilter_passes_genuine_tutorial():
    subject = {
        "title": "Build an MCP server in Python — full tutorial",
        "description": "Step-by-step walkthrough.",
        "channel_name": "AI Builder",
    }
    assert prefilter(subject=subject, analysis={}, category="education", summary="MCP server demo.")


# ── LLM judge (with cache) ──────────────────────────────────────────────────

def _conn(tmp_path):
    db = sqlite3.connect(tmp_path / "activity.db")
    db.row_factory = sqlite3.Row
    return db


def test_judge_skips_when_summary_is_empty(tmp_path):
    """No transcript signal yet → skip without LLM call AND without caching.

    An empty summary almost always means the ingestion->analysis race (the sweep
    saw the CSI event before its summary was written), not a permanent verdict.
    Caching ``no_summary`` here used to lock the video out forever because the
    cache-read short-circuited the LLM judge on every later sweep — that is how
    production ended up 534/534 ``no_summary`` with zero buildable candidates.
    The skip must therefore NOT cache, so the video re-judges once its summary
    lands. (Mirrors ``test_judge_disabled_flag_skips_without_caching``.)
    """
    with _conn(tmp_path) as conn:
        result = is_video_buildable_with_judge(
            conn,
            video_id="vid_empty",
            title="Build an MCP server",
            channel_name="AI Builder",
            summary_text="",
        )
        assert result is False
        # No terminal verdict persisted → re-judged on a later sweep.
        assert _get_cached_judge_verdict(conn, "vid_empty") is None


def test_judge_returns_false_when_llm_says_news(tmp_path, monkeypatch):
    """Real Kanal13 case: prefilter would have passed if category weren't news,
    but the LLM judge reading the transcript summary catches it anyway."""
    async def fake_classify(*, title, channel_name, summary_text):
        return {
            "buildable": False,
            "reasoning": "Summary describes geopolitical news, no implementation detail.",
            "method": "llm",
        }

    with patch(
        "universal_agent.services.llm_classifier.classify_tutorial_buildability",
        side_effect=fake_classify,
    ):
        with _conn(tmp_path) as conn:
            result = is_video_buildable_with_judge(
                conn,
                video_id="vid_news",
                title="Israeli strikes hit southern Lebanon",
                channel_name="Kanal13",
                summary_text="Report on regional escalation, ceasefire dynamics, casualty figures.",
            )
            assert result is False
            cached = _get_cached_judge_verdict(conn, "vid_news")
            assert cached["buildable"] is False
            assert cached["method"] == "llm"


def test_judge_returns_true_for_genuine_tutorial(tmp_path):
    async def fake_classify(*, title, channel_name, summary_text):
        return {
            "buildable": True,
            "reasoning": "Summary walks through scaffolding an MCP server step by step.",
            "method": "llm",
        }

    with patch(
        "universal_agent.services.llm_classifier.classify_tutorial_buildability",
        side_effect=fake_classify,
    ):
        with _conn(tmp_path) as conn:
            result = is_video_buildable_with_judge(
                conn,
                video_id="vid_good",
                title="Build an MCP server in Python",
                channel_name="AI Builder",
                summary_text="Demonstrates building a working MCP server end to end.",
            )
            assert result is True
            cached = _get_cached_judge_verdict(conn, "vid_good")
            assert cached["buildable"] is True


def test_judge_cache_prevents_repeat_llm_calls(tmp_path):
    """A cached verdict is reused without re-invoking the LLM."""
    call_count = {"n": 0}

    async def fake_classify(**_kwargs):
        call_count["n"] += 1
        return {"buildable": True, "reasoning": "demo", "method": "llm"}

    with patch(
        "universal_agent.services.llm_classifier.classify_tutorial_buildability",
        side_effect=fake_classify,
    ):
        with _conn(tmp_path) as conn:
            for _ in range(3):
                is_video_buildable_with_judge(
                    conn,
                    video_id="vid_repeat",
                    title="T",
                    channel_name="C",
                    summary_text="S",
                )
            assert call_count["n"] == 1


def test_judge_disabled_flag_skips_without_caching(tmp_path, monkeypatch):
    """When the env flag disables the judge, we skip but do NOT cache —
    so re-enabling the judge later will re-evaluate."""
    monkeypatch.setenv("UA_TUTORIAL_BUILD_JUDGE_ENABLED", "0")
    with _conn(tmp_path) as conn:
        result = is_video_buildable_with_judge(
            conn,
            video_id="vid_disabled",
            title="Build something",
            channel_name="Some Channel",
            summary_text="A real summary.",
        )
        assert result is False
        _ensure_judge_table(conn)
        cached = _get_cached_judge_verdict(conn, "vid_disabled")
        assert cached is None


def test_judge_llm_fallback_is_not_cached(tmp_path):
    """A fallback verdict (LLM call itself failed) returns False but is not
    cached, so the next sync can retry."""
    async def fake_classify(**_kwargs):
        return {"buildable": False, "reasoning": "fallback", "method": "fallback"}

    with patch(
        "universal_agent.services.llm_classifier.classify_tutorial_buildability",
        side_effect=fake_classify,
    ):
        with _conn(tmp_path) as conn:
            assert not is_video_buildable_with_judge(
                conn,
                video_id="vid_fb",
                title="T",
                channel_name="C",
                summary_text="S",
            )
            _ensure_judge_table(conn)
            assert _get_cached_judge_verdict(conn, "vid_fb") is None
