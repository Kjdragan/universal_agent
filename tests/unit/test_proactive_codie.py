"""Unit tests for proactive_codie.

Covers two layers:
  1. **Integration-style tests** (preserved from develop) for the public surface:
     ``queue_cleanup_task``, ``register_pr_artifact``, ``register_pr_artifact_from_text``,
     plus regression guards for the CODIE-worker restart-loop / laptop-path bug.
  2. **Pure-function tests** for under-tested helpers (added by the CODIE
     2026-05-04 cleanup PR): ``_slug``, ``_cleanup_task_id``,
     ``_cleanup_task_description``, ``_pick_daily_theme``.

No LLM/network dependencies. Layer (1) uses an on-disk SQLite tmp_path DB.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from universal_agent import task_hub
from universal_agent.services.proactive_artifacts import get_artifact
from universal_agent.services.proactive_codie import (
    DEFAULT_CLEANUP_THEMES,
    _cleanup_task_description,
    _cleanup_task_id,
    _pick_daily_theme,
    _slug,
    queue_cleanup_task,
    register_pr_artifact,
    register_pr_artifact_from_text,
)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Integration tests — public API surface
# ---------------------------------------------------------------------------


def test_queue_cleanup_task_creates_agent_ready_review_gated_task(tmp_path):
    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        seed = register_pr_artifact(
            conn,
            pr_url="https://github.com/Kjdragan/universal_agent/pull/999",
            title="Seed CODIE preference",
        )
        from universal_agent.services.proactive_artifacts import record_feedback
        from universal_agent.services.proactive_preferences import (
            record_artifact_feedback_signal,
        )

        updated = record_feedback(conn, artifact_id=seed["artifact_id"], score=5, text="more cleanup")
        record_artifact_feedback_signal(conn, artifact=updated, score=5, text="more cleanup")
        result = queue_cleanup_task(
            conn,
            theme="reduce brittle routing heuristics",
            note="Focus on small diffs.",
            priority=3,
        )
        task = task_hub.get_item(conn, result["task"]["task_id"])
        artifact = get_artifact(conn, result["artifact"]["artifact_id"])

    assert task is not None
    assert task["source_kind"] == "proactive_codie"
    assert task["agent_ready"] is True
    assert task["trigger_type"] == "heartbeat_poll"
    assert "pull request targeting develop" in task["description"].lower()
    assert "do not merge" in task["description"].lower()
    assert "Preference context:" in task["description"]
    assert task["metadata"]["workflow_manifest"]["workflow_kind"] == "code_change"
    assert task["metadata"]["workflow_manifest"]["target_agent"] == "vp.coder.primary"
    assert task["metadata"]["workflow_manifest"]["codebase_root"].endswith("/universal_agent")
    assert task["metadata"]["complexity_target"] == "low_to_medium"
    assert task["metadata"]["expected_work_product"] == "pull_request_to_develop"
    assert "low-to-medium complexity" in task["description"].lower()
    assert "pr is the required final work product" in task["description"].lower()
    assert "red-green tdd" in task["description"].lower()
    assert "red-green evidence" in task["description"].lower()
    assert artifact is not None
    assert artifact["artifact_type"] == "codie_cleanup_task"


def test_queue_cleanup_task_external_effect_policy_pins_hard_constraints(tmp_path):
    """The external_effect_policy on every CODIE-dispatched task must
    encode the eight hard-constraint categories from CODIE_SOUL.md.
    These are prompt-level (CODIE refuses based on the metadata it
    sees in the dispatched mission), so the contract is: the metadata
    is delivered, full stop.

    Removing any of these without an explicit policy decision is a
    regression — re-add the key to the policy AND update CODIE_SOUL.md
    in the same change.
    """
    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        result = queue_cleanup_task(conn, theme="add type hints", priority=2)
        task = task_hub.get_item(conn, result["task"]["task_id"])

    policy = task["metadata"]["external_effect_policy"]
    # Original four — PR allowed, deploy/merge/main-push denied.
    assert policy["allow_pr"] is True
    assert policy["allow_merge"] is False
    assert policy["allow_main_push"] is False
    assert policy["allow_deploy"] is False
    # New explicit hard constraints (per Kevin's directive).
    assert policy["allow_payments"] is False, "no financial transactions"
    assert policy["allow_public_communications"] is False, "no public posting"
    assert policy["allow_destructive_ops"] is False, "no rm -rf, force-push, --no-verify"
    assert policy["allow_secret_mutation"] is False, "no Infisical / .env writes"
    assert policy["allow_major_dep_bump"] is False, "no x.y.z → x+1.0.0 bumps without ask"
    assert policy["allow_control_plane_edits"] is False, "no Simone-prompt / heartbeat / cron core / vp/ edits"


def test_codie_soul_md_documents_hard_constraints():
    """CODIE_SOUL.md is CODIE's identity prompt — pin the hard-constraint
    section so prompt-level enforcement matches the metadata-level
    enforcement in external_effect_policy."""
    soul_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "universal_agent"
        / "prompt_assets"
        / "CODIE_SOUL.md"
    )
    text = soul_path.read_text(encoding="utf-8")

    assert "HARD CONSTRAINTS" in text, "section heading must exist"
    # Each constraint category — search loosely so wording can evolve
    # without breaking the test, but the concept must be present.
    required_phrases = [
        "financial transactions",
        "public-facing communications",
        "destructive actions",
        "production deploy",
        "secret",
        "major version dependency",
        "control-plane",
        "big-bang refactors",
    ]
    missing = [p for p in required_phrases if p.lower() not in text.lower()]
    assert not missing, f"CODIE_SOUL.md must document constraints: {missing}"

    # Autonomy framing — CODIE operates by default without Simone.
    assert "AUTONOMY BY DEFAULT" in text or "autonomous-by-default" in text.lower()


def test_register_pr_artifact_creates_review_candidate(tmp_path):
    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        artifact = register_pr_artifact(
            conn,
            pr_url="https://github.com/Kjdragan/universal_agent/pull/123",
            title="Clean up routing prompt drift",
            summary="CODIE removed stale routing prompt fragments.",
            branch="codie/cleanup-routing",
            theme="routing cleanup",
            tests="uv run pytest tests/test_llm_classifier.py -q",
            risk="narrow",
        )

    assert artifact["artifact_type"] == "codie_pr"
    assert artifact["artifact_uri"].endswith("/pull/123")
    assert artifact["metadata"]["review_gate"] == "kevin_review_required_before_merge"
    assert "pull-request" in artifact["topic_tags"]


def test_register_pr_artifact_from_text_detects_github_pr_url(tmp_path):
    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        artifact = register_pr_artifact_from_text(
            conn,
            text="Opened PR: https://github.com/Kjdragan/universal_agent/pull/456",
            title="CODIE cleanup PR",
            summary="PR ready for review.",
            theme="cleanup",
        )

    assert artifact is not None
    assert artifact["artifact_uri"].endswith("/pull/456")
    assert artifact["metadata"]["theme"] == "cleanup"


def test_queue_cleanup_task_uses_production_codebase_root_not_laptop_path(
    tmp_path, monkeypatch
):
    """Regression guard for the CODIE worker restart-loop incident:
    proactive cleanup tasks must NOT ship the developer's laptop path
    (`/home/kjdragan/lrepos/universal_agent`) as `codebase_root`. The
    production VPS doesn't have that path, so CODIE workers spawned,
    failed to access it, crashed, and restarted — producing a flood
    of orphan-reconciled vp-mission Task Hub items.

    Default must resolve to the production root (DEFAULT_APPROVED_CODEBASE_ROOT
    or first entry from UA_APPROVED_CODEBASE_ROOTS), with an explicit
    UA_PROACTIVE_CODIE_CODEBASE_ROOT env override winning when set.
    """
    monkeypatch.delenv("UA_PROACTIVE_CODIE_CODEBASE_ROOT", raising=False)
    monkeypatch.delenv("UA_APPROVED_CODEBASE_ROOTS", raising=False)

    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        result = queue_cleanup_task(
            conn,
            theme="add type hints to untyped public function signatures",
        )

    metadata = result["task"]["metadata"]
    codebase_root = metadata["codebase_root"]
    workflow_root = metadata["workflow_manifest"]["codebase_root"]

    # Both fields must resolve to the prod root, not the laptop path.
    assert codebase_root == "/opt/universal_agent", (
        f"Expected production root, got {codebase_root!r} — "
        "this is the bug that caused 4-restart CODIE worker loops."
    )
    assert workflow_root == "/opt/universal_agent"
    assert "kjdragan" not in codebase_root
    assert "lrepos" not in codebase_root


def test_queue_cleanup_task_respects_explicit_env_override(
    tmp_path, monkeypatch
):
    """UA_PROACTIVE_CODIE_CODEBASE_ROOT lets ops repoint without code change."""
    custom_root = "/srv/custom-codie-target"
    monkeypatch.setenv("UA_PROACTIVE_CODIE_CODEBASE_ROOT", custom_root)

    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        result = queue_cleanup_task(
            conn,
            theme="add type hints to untyped public function signatures",
        )

    assert result["task"]["metadata"]["codebase_root"] == custom_root


# ---------------------------------------------------------------------------
# Pure-function tests — _slug
# ---------------------------------------------------------------------------


def test_slug_converts_to_lowercase_and_replaces_non_alphanumeric():
    assert _slug("Hello World! 123") == "hello-world-123"


def test_slug_strips_leading_and_trailing_hyphens():
    assert _slug("---hello---") == "hello"


def test_slug_truncates_to_80_chars():
    long_input = "a" * 200
    result = _slug(long_input)
    assert len(result) == 80


def test_slug_returns_cleanup_for_empty_input():
    assert _slug("") == "cleanup"
    assert _slug(None) == "cleanup"


def test_slug_returns_cleanup_for_only_special_chars():
    assert _slug("---") == "cleanup"
    assert _slug("!@#$%^&*()") == "cleanup"


def test_slug_handles_mixed_case_and_spaces():
    assert _slug("Add Lightweight UNIT TESTS") == "add-lightweight-unit-tests"


# ---------------------------------------------------------------------------
# Pure-function tests — _cleanup_task_id
# ---------------------------------------------------------------------------


def test_cleanup_task_id_has_expected_prefix():
    result = _cleanup_task_id("some theme")
    assert result.startswith("proactive-codie:")


def test_cleanup_task_id_suffix_is_12_hex_chars():
    result = _cleanup_task_id("some theme")
    suffix = result.split(":", 1)[1]
    assert len(suffix) == 12
    # Must be valid hex
    int(suffix, 16)


def test_cleanup_task_id_deterministic_for_same_theme():
    theme = "add type hints to untyped public function signatures"
    assert _cleanup_task_id(theme) == _cleanup_task_id(theme)


def test_cleanup_task_id_different_for_different_themes():
    id_a = _cleanup_task_id("theme alpha")
    id_b = _cleanup_task_id("theme beta")
    assert id_a != id_b


# ---------------------------------------------------------------------------
# Pure-function tests — _cleanup_task_description
# ---------------------------------------------------------------------------


def test_cleanup_task_description_contains_theme():
    desc = _cleanup_task_description(chosen_theme="magic string extraction")
    assert "magic string extraction" in desc


def test_cleanup_task_description_contains_instructions_section():
    desc = _cleanup_task_description(chosen_theme="some theme")
    assert "Instructions:" in desc


def test_cleanup_task_description_contains_note_when_provided():
    desc = _cleanup_task_description(
        chosen_theme="some theme", note="Focus on helpers only."
    )
    assert "Focus on helpers only." in desc
    assert "Additional operator note:" in desc


def test_cleanup_task_description_omits_note_section_when_empty():
    desc = _cleanup_task_description(chosen_theme="some theme", note="")
    assert "Additional operator note:" not in desc


def test_cleanup_task_description_contains_preference_context_when_provided():
    desc = _cleanup_task_description(
        chosen_theme="some theme", preference_context="User prefers small PRs."
    )
    assert "Preference context:" in desc
    assert "User prefers small PRs." in desc


def test_cleanup_task_description_omits_preference_context_when_empty():
    desc = _cleanup_task_description(
        chosen_theme="some theme", preference_context=""
    )
    assert "Preference context:" not in desc


# ---------------------------------------------------------------------------
# Pure-function tests — _pick_daily_theme
# ---------------------------------------------------------------------------


def test_pick_daily_theme_returns_known_theme():
    theme = _pick_daily_theme()
    assert theme in DEFAULT_CLEANUP_THEMES


def test_pick_daily_theme_returns_string():
    theme = _pick_daily_theme()
    assert isinstance(theme, str)
    assert len(theme) > 0


def test_pick_daily_theme_matches_day_of_year_rotation():
    """Verify the rotation logic: day_of_year % len(themes) selects the theme."""
    day_of_year = datetime.now(timezone.utc).timetuple().tm_yday
    expected = DEFAULT_CLEANUP_THEMES[day_of_year % len(DEFAULT_CLEANUP_THEMES)]
    assert _pick_daily_theme() == expected
