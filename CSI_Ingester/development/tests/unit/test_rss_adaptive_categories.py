from __future__ import annotations

from pathlib import Path

from csi_ingester.analytics.categories import (
    canonicalize_category,
    classify_and_update_category,
    ensure_taxonomy_state,
    reset_taxonomy_state,
)
from csi_ingester.store.sqlite import connect, ensure_schema


def _conn(tmp_path: Path):
    db_path = tmp_path / "csi.db"
    conn = connect(db_path)
    ensure_schema(conn)
    return conn


def test_taxonomy_bootstraps_core_categories(tmp_path: Path):
    conn = _conn(tmp_path)
    try:
        state = ensure_taxonomy_state(conn, max_categories=10)
        categories = state["categories"]
        assert set(["ai", "political", "war", "other_interest"]).issubset(set(categories.keys()))
        assert int(state["max_categories"]) == 10
    finally:
        conn.close()


def test_classifies_core_categories_from_content(tmp_path: Path):
    conn = _conn(tmp_path)
    try:
        ai_cat, _ = classify_and_update_category(
            conn,
            suggested_category="",
            title="OpenAI unveils GPT roadmap for agents",
            channel_name="AI Daily",
            summary_text="",
            transcript_text="Anthropic and OpenAI models power automation workflows.",
            themes=["agents", "llm"],
            confidence=0.8,
            max_categories=10,
        )
        war_cat, _ = classify_and_update_category(
            conn,
            suggested_category="",
            title="Frontline military update from war zone",
            channel_name="World Report",
            summary_text="",
            transcript_text="Troops and missiles continue the conflict.",
            themes=["defense"],
            confidence=0.8,
            max_categories=10,
        )
        assert ai_cat == "ai"
        assert war_cat == "war"
    finally:
        conn.close()


def test_dynamic_categories_respect_max_and_retire_narrowest(tmp_path: Path):
    conn = _conn(tmp_path)
    try:
        first_cat, first_state = classify_and_update_category(
            conn,
            suggested_category="homesteading",
            title="Backyard homesteading setup",
            channel_name="Country Living",
            summary_text="",
            transcript_text="",
            themes=["homesteading"],
            confidence=0.9,
            max_categories=5,
        )
        assert first_cat == "homesteading"
        assert "homesteading" in first_state["categories"]
        assert len(first_state["categories"]) == 5

        second_cat, second_state = classify_and_update_category(
            conn,
            suggested_category="woodworking",
            title="Woodworking joinery basics",
            channel_name="Workshop Lab",
            summary_text="",
            transcript_text="",
            themes=["woodworking"],
            confidence=0.9,
            max_categories=5,
        )
        assert second_cat == "woodworking"
        assert "woodworking" in second_state["categories"]
        assert len(second_state["categories"]) <= 5
        assert "homesteading" not in second_state["categories"]
        assert canonicalize_category("non_ai") == "other_interest"
    finally:
        conn.close()


def test_reset_taxonomy_state_removes_dynamic_categories(tmp_path: Path):
    conn = _conn(tmp_path)
    try:
        created, _state = classify_and_update_category(
            conn,
            suggested_category="economics",
            title="Macro economics weekly",
            channel_name="Markets",
            summary_text="",
            transcript_text="",
            themes=["economics"],
            confidence=0.9,
            max_categories=10,
        )
        assert created == "economics"

        reset = reset_taxonomy_state(conn, max_categories=10)
        keys = set(reset["categories"].keys())
        assert "economics" not in keys
        assert set(["ai", "political", "war", "other_interest"]).issubset(keys)
    finally:
        conn.close()


def test_generic_fallback_words_do_not_spawn_dynamic_category(tmp_path: Path):
    conn = _conn(tmp_path)
    try:
        for _ in range(12):
            cat, state = classify_and_update_category(
                conn,
                suggested_category="other_interest",
                title="Video update",
                channel_name="General Channel",
                summary_text="transcript unavailable metadata-only classification",
                transcript_text="",
                themes=["general_interest"],
                confidence=0.55,
                max_categories=10,
            )
            assert cat == "other_interest"

        keys = set(state["categories"].keys())
        assert keys == {"ai", "political", "war", "other_interest"}
    finally:
        conn.close()
