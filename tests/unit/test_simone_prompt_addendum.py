"""Hermes Ship 6 — Simone prompt addendum regression guard.

Verifies the verb-tool decision guidance is present in the assembled
Simone heartbeat directive at ``memory/HEARTBEAT.md``.

Without this guidance Simone has no decision-tree for choosing between
``task_re_evaluate`` / ``task_request_revision`` / ``task_redirect_to``
(beyond the tool descriptions on the ``@tool`` decorators), and tends
to use one verb inconsistently across mission outcomes.

This test is intentionally minimal — it pins SEMANTIC markers, not
exact phrasing, so future refinement of the prompt language won't
require updating the test. Its sole purpose is to catch accidental
deletion of the guidance section.
"""

from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
HEARTBEAT_PATH = REPO_ROOT / "memory" / "HEARTBEAT.md"


def test_heartbeat_directive_contains_verb_decision_tree() -> None:
    """The Cody-mission review decision tree must be present in HEARTBEAT.md.

    Markers asserted:
      * Section heading exists.
      * All three verb names are referenced.
      * The operator-baked budget invariant is present (re_evaluate
        does not bump retry budget; request_revision does).
    """
    assert HEARTBEAT_PATH.exists(), f"missing: {HEARTBEAT_PATH}"
    text = HEARTBEAT_PATH.read_text(encoding="utf-8")
    required_markers = [
        "Reviewing Cody's completed missions",
        "task_request_revision",
        "task_re_evaluate",
        "task_redirect_to",
        "does NOT bump retry budget",
    ]
    missing = [m for m in required_markers if m not in text]
    assert not missing, (
        "Simone heartbeat directive is missing required verb-tool guidance "
        f"markers: {missing}. See "
        "docs/reports/hermes-ship-6-simone-prompt-addendum-plan.md"
    )


def test_heartbeat_directive_distinguishes_initial_dispatch_from_followup() -> None:
    """Beyond just naming the three verbs, Simone must understand the
    distinction between ``vp_dispatch_mission`` (initial work) and the
    three follow-up verbs (after Cody finishes).  Pin that explicit
    distinction so a future edit doesn't collapse them."""
    text = HEARTBEAT_PATH.read_text(encoding="utf-8")
    # Both names should appear together (in the same paragraph or
    # neighborhood). We check both present + that the distinction
    # phrase ("INITIAL" / "after-the-fact" / similar) is there too.
    assert "vp_dispatch_mission" in text, (
        "HEARTBEAT.md no longer references vp_dispatch_mission alongside "
        "the follow-up verbs — Simone needs the initial-vs-followup "
        "distinction to choose correctly."
    )
    assert "INITIAL" in text or "after-the-fact" in text or "follow-up" in text, (
        "HEARTBEAT.md no longer makes the initial-dispatch vs follow-up "
        "distinction explicit; without it Simone may treat verbs as "
        "interchangeable with vp_dispatch_mission."
    )


def test_heartbeat_directive_marks_sign_off_as_default() -> None:
    """Catch: Simone must understand that doing NOTHING is the default
    when Cody nails it. Without this, she may reflexively invoke a
    follow-up verb on every completed task and burn budget on noise."""
    text = HEARTBEAT_PATH.read_text(encoding="utf-8")
    # The exact phrase isn't load-bearing; either "No action" or
    # "Sign-off is the default" works. Pin one marker.
    markers = ["Sign-off is the default", "No action", "stays `completed`"]
    found = [m for m in markers if m in text]
    assert found, (
        "HEARTBEAT.md no longer marks 'no follow-up needed' as the "
        "default Cody-review outcome. Simone needs this to avoid "
        f"reflexively invoking verbs. Expected one of: {markers}"
    )
