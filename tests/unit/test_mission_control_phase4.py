"""Phase 4 — action buttons (Generate Prompt + Send to Codie).

Tests cover:
  - prompt builder: subject-kind framing tails, evidence_payload included,
    prior synthesis_history surfaced for recurring subjects, codebase_root
    grounding, delivery-mode-specific preambles, operator steering text
  - dispatch-history audit trail: prompt_generated_for_external and
    dispatched_to_codie shapes, in-card mirror cap, long-form table

The actual gateway endpoints + Task Hub integration are covered by
integration tests; here we exercise the persistence helpers and the
prompt-builder logic directly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_agent.services.mission_control_cards import (
    CardUpsert,
    append_dispatch_history,
    list_dispatch_history,
    make_card_id,
    upsert_card,
)
from universal_agent.services.mission_control_db import open_store
from universal_agent.services.mission_control_prompts import (
    GeneratedPrompt,
    build_prompt,
)


# ── Prompt builder ────────────────────────────────────────────────────


def _sample_card(**overrides):
    base = {
        "card_id": make_card_id("task", "task:abc"),
        "subject_kind": "task",
        "subject_id": "task:abc",
        "severity": "warning",
        "title": "Stuck task X needs operator review",
        "narrative": "The task has been blocked since 2026-05-03 with retry_count=4.",
        "why_it_matters": "Five other tasks depend on this one completing.",
        "recommended_next_step": "Inspect the dispatcher logs.",
        "tags": ["stuck", "task-hub"],
        "evidence_refs": [
            {"kind": "task", "id": "task:abc", "uri": "/dashboard/todolist?focus=task:abc",
             "label": "Task Hub item"},
            {"kind": "session", "id": "daemon_simone_todo",
             "uri": "/?session_id=daemon_simone_todo", "label": "Execution session"},
        ],
        "evidence_payload": {"retry_count": 4, "blocked_reason": "claim_timeout"},
        "synthesis_history": [],
        "recurrence_count": 1,
        "first_observed_at": "2026-05-03T01:00:00Z",
        "last_synthesized_at": "2026-05-03T04:25:43Z",
    }
    base.update(overrides)
    return base


def test_build_prompt_includes_card_essentials_for_external_ai_coder():
    prompt = build_prompt(_sample_card(), delivery_mode="external_ai_coder")
    assert isinstance(prompt, GeneratedPrompt)
    assert prompt.delivery_mode == "external_ai_coder"
    text = prompt.text
    # Preamble identifies as external coder (NOT codie)
    assert "AI coding assistant" in text and "vp.coder.primary" not in text
    # All key fields surfaced
    assert "Stuck task X needs operator review" in text  # title
    assert "blocked since 2026-05-03" in text  # narrative
    assert "Five other tasks depend" in text  # why_it_matters
    assert "Inspect the dispatcher logs" in text  # next_step
    # Subject-kind specific framing tail (task)
    assert "Task Hub item" in text or "lifecycle" in text


def test_build_prompt_includes_codie_specific_constraints_when_dispatched():
    prompt = build_prompt(_sample_card(), delivery_mode="codie")
    text = prompt.text
    assert "Codie" in text or "vp.coder.primary" in text
    # Execution constraints section appears for codie mode only
    assert "Execution Constraints" in text
    assert "do NOT merge" in text
    assert "Do NOT push to main" in text


def test_build_prompt_external_mode_omits_codie_constraints():
    prompt = build_prompt(_sample_card(), delivery_mode="external_ai_coder")
    text = prompt.text
    # The Execution Constraints section is codie-mode-only
    assert "Execution Constraints" not in text


def test_build_prompt_evidence_payload_serialized_as_json():
    prompt = build_prompt(_sample_card())
    assert '"retry_count": 4' in prompt.text
    assert '"blocked_reason": "claude_timeout"' not in prompt.text  # sanity
    assert '"blocked_reason": "claim_timeout"' in prompt.text


def test_build_prompt_evidence_refs_with_uris_render_as_arrows():
    prompt = build_prompt(_sample_card())
    assert "→ /dashboard/todolist?focus=task:abc" in prompt.text
    assert "→ /?session_id=daemon_simone_todo" in prompt.text


def test_build_prompt_recurring_subject_includes_history():
    """recurrence_count > 1 means the subject has revived; surface prior
    synthesis so the AI can spot patterns rather than diagnose from
    scratch each time."""
    card = _sample_card(
        recurrence_count=3,
        synthesis_history=[
            {"ts": "2026-05-01T08:00", "narrative": "First occurrence: retry_count=2"},
            {"ts": "2026-05-02T08:00", "narrative": "Second occurrence: retry_count=3, similar root cause"},
        ],
    )
    prompt = build_prompt(card)
    assert "Prior Synthesis History" in prompt.text
    assert "First occurrence" in prompt.text
    assert "Second occurrence" in prompt.text


def test_build_prompt_non_recurring_subject_omits_history_section():
    """recurrence_count=1 means this is the first time — no history to show."""
    prompt = build_prompt(_sample_card(recurrence_count=1, synthesis_history=[]))
    assert "Prior Synthesis History" not in prompt.text


def test_build_prompt_subject_kind_failure_pattern_uses_pattern_framing():
    card = _sample_card(subject_kind="failure_pattern", subject_id="pattern:cron_recurring")
    prompt = build_prompt(card)
    # Failure-pattern framing emphasizes structural fix, not one-off patch
    assert "structural fix" in prompt.text or "common root cause" in prompt.text.lower()


def test_build_prompt_subject_kind_infrastructure_warns_about_blast_radius():
    card = _sample_card(subject_kind="infrastructure", subject_id="infra:csi_ingester")
    prompt = build_prompt(card)
    assert "blast-radius" in prompt.text or "DO NOT execute" in prompt.text


def test_build_prompt_subject_kind_idea_does_not_demand_action():
    card = _sample_card(subject_kind="idea", subject_id="idea:nice-thought")
    prompt = build_prompt(card)
    # Ideas should NOT be framed as urgent investigation
    assert "do NOT start building" in prompt.text or "design" in prompt.text.lower()


def test_build_prompt_operator_steering_text_renders_in_dedicated_section():
    prompt = build_prompt(
        _sample_card(),
        delivery_mode="codie",
        operator_steering_text="Focus on the proxy auth path",
    )
    assert "Operator Steering" in prompt.text
    assert "Focus on the proxy auth path" in prompt.text


def test_build_prompt_codebase_root_explicit_overrides_default():
    prompt = build_prompt(_sample_card(), codebase_root="/opt/some_other_root")
    assert "/opt/some_other_root" in prompt.text


def test_build_prompt_rejects_invalid_delivery_mode():
    with pytest.raises(ValueError):
        build_prompt(_sample_card(), delivery_mode="bogus")


def test_build_prompt_rejects_card_missing_required_fields():
    with pytest.raises(ValueError):
        build_prompt({"subject_kind": "task"})  # no subject_id, no card_id


def test_build_prompt_handles_evidence_payload_already_string():
    """Cards loaded straight from SQL have evidence_payload_json (str).
    The builder must accept that and parse into the prompt cleanly."""
    card = _sample_card()
    card["evidence_payload"] = json.dumps({"retry_count": 4, "blocked_reason": "claim_timeout"})
    prompt = build_prompt(card)
    assert '"retry_count": 4' in prompt.text


# ── Dispatch history persistence ──────────────────────────────────────


def _seed_card(conn) -> str:
    upsert_card(conn, CardUpsert(
        subject_kind="task", subject_id="dispatch_target", severity="warning",
        title="t", narrative="n", why_it_matters="w",
    ))
    return make_card_id("task", "dispatch_target")


def test_append_dispatch_external_records_audit_row(tmp_path):
    conn = open_store(tmp_path / "mc.db")
    try:
        cid = _seed_card(conn)
        result = append_dispatch_history(
            conn, card_id=cid,
            action="prompt_generated_for_external",
            prompt_text="full prompt text" * 20,
        )
        assert result["dispatch_id"].startswith("disp_")
        assert result["action"] == "prompt_generated_for_external"

        # Long-form audit table populated
        history = list_dispatch_history(conn, cid)
        assert len(history) == 1
        assert history[0]["action"] == "prompt_generated_for_external"
        assert "full prompt text" in history[0]["prompt_text"]
        assert history[0]["task_id"] is None
    finally:
        conn.close()


def test_append_dispatch_codie_records_task_id_and_steering(tmp_path):
    conn = open_store(tmp_path / "mc.db")
    try:
        cid = _seed_card(conn)
        append_dispatch_history(
            conn, card_id=cid,
            action="dispatched_to_codie",
            prompt_text="full prompt",
            operator_steering_text="focus on the proxy",
            task_id="mc-dispatch:test:12345",
        )
        history = list_dispatch_history(conn, cid)
        assert history[0]["task_id"] == "mc-dispatch:test:12345"
        assert history[0]["operator_steering_text"] == "focus on the proxy"
    finally:
        conn.close()


def test_append_dispatch_card_side_mirror_caps_at_20(tmp_path):
    conn = open_store(tmp_path / "mc.db")
    try:
        cid = _seed_card(conn)
        for i in range(25):
            append_dispatch_history(
                conn, card_id=cid,
                action="prompt_generated_for_external",
                prompt_text=f"prompt #{i}",
            )
        # in-card mirror is capped at 20
        row = conn.execute(
            "SELECT dispatch_history_json FROM mission_control_cards WHERE card_id = ?",
            (cid,),
        ).fetchone()
        mirror = json.loads(row[0])
        assert len(mirror) == 20
        # Newest first
        assert "prompt #24" in mirror[0]["dispatch_id"] or mirror[0]["ts"] >= mirror[-1]["ts"]
        # Long-form table preserves all 25
        long_form = list_dispatch_history(conn, cid, limit=200)
        assert len(long_form) == 25
    finally:
        conn.close()


def test_append_dispatch_rejects_invalid_action(tmp_path):
    conn = open_store(tmp_path / "mc.db")
    try:
        cid = _seed_card(conn)
        with pytest.raises(ValueError, match="action"):
            append_dispatch_history(
                conn, card_id=cid,
                action="not_a_real_action",
                prompt_text="x",
            )
    finally:
        conn.close()


def test_append_dispatch_rejects_blank_prompt_text(tmp_path):
    conn = open_store(tmp_path / "mc.db")
    try:
        cid = _seed_card(conn)
        with pytest.raises(ValueError, match="prompt_text"):
            append_dispatch_history(
                conn, card_id=cid,
                action="prompt_generated_for_external",
                prompt_text="   ",
            )
    finally:
        conn.close()


def test_append_dispatch_rejects_unknown_card(tmp_path):
    conn = open_store(tmp_path / "mc.db")
    try:
        with pytest.raises(ValueError, match="not found"):
            append_dispatch_history(
                conn, card_id="card_does_not_exist",
                action="prompt_generated_for_external",
                prompt_text="x",
            )
    finally:
        conn.close()
