"""Phase 3 — Chief-of-Staff readout layered on top of Mission Control cards.

Tier-2 synthesis (the existing `mission_control_chief_of_staff` service)
now consumes the tier-1 + tier-0 cards as already-synthesized
intelligence rather than synthesizing from raw evidence in parallel.

These tests cover:
  - collect_mission_control_cards_evidence: live / retired_recent /
    recurring lists, schema-missing graceful degradation, comment +
    thumbs surface in summaries
  - collect_evidence_bundle: cards source wired in; source_counts
    surfaces mission_control_cards_live/retired_recent/recurring
  - _llm_prompt: includes the card-layering block + counts; explicitly
    instructs the LLM to reference cards by subject_id and weave
    recurring patterns into narrative
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def mc_db_path(tmp_path: Path, monkeypatch) -> Path:
    """Point the MC store at a tmp DB for the duration of the test so
    we don't touch the real production DB. Both the chief_of_staff
    service AND the mission_control_db open_store() resolve via
    UA_MISSION_CONTROL_INTEL_DB_PATH.
    """
    db_path = tmp_path / "mc.db"
    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", str(db_path))
    # Force schema creation
    from universal_agent.services.mission_control_db import open_store

    open_store(db_path).close()
    return db_path


def _seed_card(mc_db_path: Path, **kw):
    """Insert a card directly into the MC store. Defaults reflect a
    minimum-valid card; pass kwargs to override."""
    from universal_agent.services.mission_control_cards import CardUpsert, upsert_card
    from universal_agent.services.mission_control_db import open_store

    payload = CardUpsert(
        subject_kind=kw.get("subject_kind", "task"),
        subject_id=kw.get("subject_id", "task:test"),
        severity=kw.get("severity", "warning"),
        title=kw.get("title", "Test card"),
        narrative=kw.get("narrative", "Narrative text"),
        why_it_matters=kw.get("why_it_matters", "Operator should care"),
        recommended_next_step=kw.get("recommended_next_step"),
        tags=kw.get("tags", []),
        evidence_refs=kw.get("evidence_refs", []),
        synthesis_model=kw.get("synthesis_model", "glm-4.7"),
    )
    conn = open_store(mc_db_path)
    try:
        upsert_card(conn, payload)
    finally:
        conn.close()


def _retire(mc_db_path: Path, subject_kind: str, subject_id: str):
    from universal_agent.services.mission_control_cards import make_card_id, retire_card
    from universal_agent.services.mission_control_db import open_store

    conn = open_store(mc_db_path)
    try:
        retire_card(conn, make_card_id(subject_kind, subject_id))
    finally:
        conn.close()


def _set_thumbs(mc_db_path: Path, subject_kind: str, subject_id: str, direction: str):
    from universal_agent.services.mission_control_cards import (
        make_card_id,
        set_card_thumbs,
    )
    from universal_agent.services.mission_control_db import open_store

    conn = open_store(mc_db_path)
    try:
        set_card_thumbs(conn, make_card_id(subject_kind, subject_id), direction)
    finally:
        conn.close()


def _add_comment(mc_db_path: Path, subject_kind: str, subject_id: str, text: str):
    from universal_agent.services.mission_control_cards import (
        add_card_comment,
        make_card_id,
    )
    from universal_agent.services.mission_control_db import open_store

    conn = open_store(mc_db_path)
    try:
        add_card_comment(conn, make_card_id(subject_kind, subject_id), text)
    finally:
        conn.close()


def _bump_recurrence(mc_db_path: Path, subject_kind: str, subject_id: str, target: int):
    """Hack: directly bump recurrence_count to simulate a card that's
    revived multiple times (the natural path goes through retire+upsert,
    which works but is verbose for this test)."""
    from universal_agent.services.mission_control_cards import make_card_id
    from universal_agent.services.mission_control_db import open_store

    conn = open_store(mc_db_path)
    try:
        conn.execute(
            "UPDATE mission_control_cards SET recurrence_count = ? WHERE card_id = ?",
            (target, make_card_id(subject_kind, subject_id)),
        )
    finally:
        conn.close()


# ── collect_mission_control_cards_evidence ──────────────────────────────


def test_cards_evidence_returns_empty_when_store_empty(mc_db_path):
    from universal_agent.services.mission_control_chief_of_staff import (
        collect_mission_control_cards_evidence,
    )

    out = collect_mission_control_cards_evidence()
    assert out["live"] == []
    assert out["retired_recent"] == []
    assert out["recurring"] == []
    assert out["counts"]["live"] == 0


def test_cards_evidence_surfaces_live_cards_severity_ordered(mc_db_path):
    from universal_agent.services.mission_control_chief_of_staff import (
        collect_mission_control_cards_evidence,
    )

    _seed_card(mc_db_path, subject_id="task:info", severity="informational",
               title="info card")
    _seed_card(mc_db_path, subject_id="task:critical", severity="critical",
               title="critical card")
    _seed_card(mc_db_path, subject_id="task:warn", severity="warning",
               title="warning card")

    out = collect_mission_control_cards_evidence()
    severities = [c["severity"] for c in out["live"]]
    assert severities == ["critical", "warning", "informational"]
    assert out["counts"]["live"] == 3


def test_cards_evidence_separates_retired_recent_from_live(mc_db_path):
    from universal_agent.services.mission_control_chief_of_staff import (
        collect_mission_control_cards_evidence,
    )

    _seed_card(mc_db_path, subject_id="task:still_live", title="alive")
    _seed_card(mc_db_path, subject_id="task:gone", title="retired")
    _retire(mc_db_path, "task", "task:gone")

    out = collect_mission_control_cards_evidence()
    live_titles = [c["title"] for c in out["live"]]
    retired_titles = [c["title"] for c in out["retired_recent"]]
    assert "alive" in live_titles
    assert "retired" in retired_titles
    assert "retired" not in live_titles


def test_cards_evidence_surfaces_recurring_pattern(mc_db_path):
    from universal_agent.services.mission_control_chief_of_staff import (
        collect_mission_control_cards_evidence,
    )

    _seed_card(mc_db_path, subject_id="task:fresh", title="fresh")
    _seed_card(mc_db_path, subject_id="task:repeat", title="recurring pattern")
    _bump_recurrence(mc_db_path, "task", "task:repeat", target=4)

    out = collect_mission_control_cards_evidence()
    recurring_subjects = [c["subject_id"] for c in out["recurring"]]
    assert "task:repeat" in recurring_subjects
    # recurrence_count=1 should NOT show up in the recurring list
    assert "task:fresh" not in recurring_subjects


def test_cards_evidence_surfaces_operator_feedback_in_summary(mc_db_path):
    from universal_agent.services.mission_control_chief_of_staff import (
        collect_mission_control_cards_evidence,
    )

    _seed_card(mc_db_path, subject_id="task:fed_back", title="card with feedback")
    _set_thumbs(mc_db_path, "task", "task:fed_back", "up")
    _add_comment(mc_db_path, "task", "task:fed_back", "Great catch — keep surfacing these")

    out = collect_mission_control_cards_evidence()
    target = next(c for c in out["live"] if c["subject_id"] == "task:fed_back")
    assert target["operator_thumbs"] == "up"
    comments = target["operator_comments"]
    assert len(comments) == 1
    assert "Great catch" in comments[0]["text"]


def test_cards_evidence_graceful_when_store_unavailable(monkeypatch, tmp_path):
    """If MC store can't be opened (e.g. Phase 0 not deployed yet),
    the evidence collector returns empty lists with an `unavailable`
    marker rather than crashing the entire COS readout."""
    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", "/nonexistent/path/that/cannot/be/created/db.db")
    from universal_agent.services.mission_control_chief_of_staff import (
        collect_mission_control_cards_evidence,
    )

    out = collect_mission_control_cards_evidence()
    # Should still return the keyed shape with empty lists
    assert "live" in out
    assert "retired_recent" in out
    assert "recurring" in out
    assert out["live"] == []
    # Either the open failed (unavailable marker) or the schema was
    # missing — both acceptable graceful-degrade outcomes.
    if out.get("unavailable"):
        assert isinstance(out["unavailable"], str)


# ── collect_evidence_bundle wiring ──────────────────────────────────────


def test_evidence_bundle_includes_cards_source(mc_db_path):
    from universal_agent.services.mission_control_chief_of_staff import (
        collect_evidence_bundle,
    )

    _seed_card(mc_db_path, subject_id="task:bundled", title="in bundle")

    bundle = collect_evidence_bundle()
    assert "mission_control_cards" in bundle["sources"]
    cards_src = bundle["sources"]["mission_control_cards"]
    assert cards_src["counts"]["live"] >= 1
    # Top-level source_counts surfaces MC card counts for diagnostics
    assert "mission_control_cards_live" in bundle["source_counts"]
    assert bundle["source_counts"]["mission_control_cards_live"] >= 1


def test_evidence_bundle_phase3_layering_note_in_collection_policy(mc_db_path):
    """Phase 3 documents intent in the bundle's collection_policy so
    future readers know why the cards source exists."""
    from universal_agent.services.mission_control_chief_of_staff import (
        collect_evidence_bundle,
    )

    bundle = collect_evidence_bundle()
    policy = bundle.get("collection_policy") or {}
    assert "phase3_layering" in policy or any(
        "Mission Control" in str(v) for v in policy.values()
    )


# ── LLM prompt surfaces card layering ──────────────────────────────────


def test_llm_prompt_includes_card_layering_block(mc_db_path):
    """The Phase 3 prompt MUST instruct the LLM about the cards layer
    so the synthesis treats them as already-resolved intelligence
    rather than re-synthesizing in parallel."""
    from universal_agent.services.mission_control_chief_of_staff import (
        _llm_prompt,
        collect_evidence_bundle,
    )

    _seed_card(mc_db_path, subject_id="task:in_prompt", title="prompt-visible")
    bundle = collect_evidence_bundle()
    prompt = _llm_prompt(bundle)
    assert "Mission Control card layering" in prompt or "Phase 3" in prompt
    # Key instructions
    assert "ALREADY-SYNTHESIZED" in prompt
    assert "subject_id" in prompt
    assert "RECURRING" in prompt or "recurrence_count" in prompt


def test_llm_prompt_quotes_actual_card_counts(mc_db_path):
    """Counts surfaced in the prompt body so the LLM has numerics to
    anchor its narrative."""
    from universal_agent.services.mission_control_chief_of_staff import (
        _llm_prompt,
        collect_evidence_bundle,
    )

    _seed_card(mc_db_path, subject_id="task:c1", title="c1")
    _seed_card(mc_db_path, subject_id="task:c2", title="c2")
    bundle = collect_evidence_bundle()
    prompt = _llm_prompt(bundle)
    assert "live=2" in prompt
