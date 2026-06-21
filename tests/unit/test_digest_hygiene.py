"""Digest hygiene: cross-path dedup, staleness backstop, chatter sanitization.

Locks the three additive fixes to the daily proactive-review digest in
``services/intelligence_reporter.py``:

  (a) two artifacts that point at the same underlying work target (a demo
      mirrored as both ``cody_demo_task`` and ``proactive_work_item``) collapse
      to one, keeping the richer cody_demo_task on a score tie;
  (b) a candidate older than the 7-day staleness backstop (e.g. an
      already-merged/closed PR) is excluded — and a metadata-resolved
      merged/closed PR is excluded regardless of age;
  (c) a summary that leaks a BRIEF/ACCEPTANCE boilerplate header or a
      conversational tail like "Want me to exit it?" is scrubbed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from universal_agent.services.intelligence_reporter import (
    _dedup_candidates,
    _is_stale_candidate,
    _sanitize_summary,
)


def test_same_target_demo_artifacts_collapse_to_one():
    # Same Task Hub task_id reached via two sync paths → two distinct rows.
    demo = {
        "artifact_id": "pa_demo",
        "artifact_type": "cody_demo_task",
        "source_ref": "opus-port-rust-to-ts__demo-1",
        "metadata": {"task_id": "task-123"},
    }
    work_item = {
        "artifact_id": "pa_work",
        "artifact_type": "proactive_work_item",
        "source_ref": "task-123",
        "metadata": {"task_id": "task-123"},
    }
    # Equal score → the richer cody_demo_task must win the tie.
    kept = _dedup_candidates([(work_item, 5.0), (demo, 5.0)])
    assert len(kept) == 1
    assert kept[0][0]["artifact_type"] == "cody_demo_task"


def test_higher_score_wins_over_type_preference():
    demo = {"artifact_type": "cody_demo_task", "metadata": {"task_id": "t1"}}
    work_item = {"artifact_type": "proactive_work_item", "metadata": {"task_id": "t1"}}
    kept = _dedup_candidates([(work_item, 9.0), (demo, 1.0)])
    assert len(kept) == 1
    assert kept[0][0]["artifact_type"] == "proactive_work_item"


def test_distinct_targets_are_not_collapsed():
    a = {"artifact_id": "pa_a", "metadata": {"task_id": "t1"}}
    b = {"artifact_id": "pa_b", "metadata": {"task_id": "t2"}}
    kept = _dedup_candidates([(a, 1.0), (b, 1.0)])
    assert len(kept) == 2


def test_stale_old_candidate_is_excluded():
    now = datetime(2026, 6, 21, tzinfo=timezone.utc)
    old = {
        "artifact_type": "codie_pr",
        "updated_at": (now - timedelta(days=10)).isoformat(),
        "created_at": (now - timedelta(days=12)).isoformat(),
        "metadata": {},
    }
    fresh = {
        "artifact_type": "codie_pr",
        "updated_at": (now - timedelta(days=1)).isoformat(),
        "metadata": {},
    }
    assert _is_stale_candidate(old, now=now) is True
    assert _is_stale_candidate(fresh, now=now) is False


def test_merged_pr_metadata_excluded_regardless_of_age():
    now = datetime(2026, 6, 21, tzinfo=timezone.utc)
    recent_but_merged = {
        "artifact_type": "codie_pr",
        "updated_at": now.isoformat(),
        "metadata": {"pr_state": "MERGED"},
    }
    assert _is_stale_candidate(recent_but_merged, now=now) is True


def test_summary_strips_conversational_tail():
    summary = "Ported the parser to TypeScript and added tests.\nWant me to exit it?"
    cleaned = _sanitize_summary(summary)
    assert "Want me to exit it?" not in cleaned
    assert "Ported the parser to TypeScript" in cleaned


def test_summary_strips_leading_brief_boilerplate():
    summary = "BRIEF:\nACCEPTANCE CRITERIA:\nThe real recap line that matters."
    cleaned = _sanitize_summary(summary)
    assert cleaned == "The real recap line that matters."


def test_summary_drops_should_i_tail():
    summary = "Wired up the digest dedup.\nShould I open a PR?"
    cleaned = _sanitize_summary(summary)
    assert "Should I open a PR?" not in cleaned
    assert "Wired up the digest dedup." in cleaned
