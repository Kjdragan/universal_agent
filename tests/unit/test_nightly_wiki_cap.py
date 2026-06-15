"""Tests for the project-wide daily wiki-notebook cap in nightly_wiki_agent.

Covers the pure, deterministic pieces of the 2026-06-14 runaway fix:
- `_count_wikis_today_from_list` — count today's wiki notebooks, excluding the
  paper-to-podcast lane and other days.
- `_wiki_daily_hard_cap` — env-overridable cap with a safe default.
"""

from __future__ import annotations

from universal_agent.scripts import nightly_wiki_agent as nwa

TODAY = "2026-06-14"


def test_count_wikis_today_basic() -> None:
    notebooks = [
        {"title": "Hierarchical Planning for Long Context Agents", "updated_at": "2026-06-14T09:15:55Z"},
        {"title": "Recursive Self-Improvement", "updated_at": "2026-06-14T10:36:40Z"},
    ]
    assert nwa._count_wikis_today_from_list(notebooks, TODAY) == 2


def test_count_wikis_excludes_paper_to_podcast() -> None:
    notebooks = [
        {"title": "Hierarchical Planning", "updated_at": "2026-06-14T09:00:00Z"},
        {"title": "Paper to Podcast: Long-Context LLMs", "updated_at": "2026-06-14T21:00:00Z"},
        {"title": "paper to podcast: prompt engineering", "updated_at": "2026-06-14T21:05:00Z"},
    ]
    # Only the non-paper-to-podcast notebook counts toward the wiki cap.
    assert nwa._count_wikis_today_from_list(notebooks, TODAY) == 1


def test_count_wikis_ignores_other_days() -> None:
    notebooks = [
        {"title": "Yesterday wiki", "updated_at": "2026-06-13T23:59:00Z"},
        {"title": "Today wiki", "updated_at": "2026-06-14T00:01:00Z"},
        {"title": "Tomorrow wiki", "updated_at": "2026-06-15T00:01:00Z"},
    ]
    assert nwa._count_wikis_today_from_list(notebooks, TODAY) == 1


def test_count_wikis_handles_alt_timestamp_and_title_keys() -> None:
    notebooks = [
        {"name": "Alt keys wiki", "created": "2026-06-14T08:00:00Z"},
        {"title": "create_time wiki", "create_time": "2026-06-14T08:30:00Z"},
    ]
    assert nwa._count_wikis_today_from_list(notebooks, TODAY) == 2


def test_count_wikis_robust_to_garbage() -> None:
    notebooks = [
        None,
        "not a dict",
        {},  # no timestamp → not today → skipped
        {"title": "Good", "updated_at": "2026-06-14T08:00:00Z"},
    ]
    assert nwa._count_wikis_today_from_list(notebooks, TODAY) == 1
    assert nwa._count_wikis_today_from_list([], TODAY) == 0
    assert nwa._count_wikis_today_from_list(None, TODAY) == 0


def test_hard_cap_default(monkeypatch) -> None:
    monkeypatch.delenv("UA_DAILY_PROACTIVE_WIKI_HARD_CAP", raising=False)
    assert nwa._wiki_daily_hard_cap() == 3


def test_hard_cap_env_override(monkeypatch) -> None:
    monkeypatch.setenv("UA_DAILY_PROACTIVE_WIKI_HARD_CAP", "5")
    assert nwa._wiki_daily_hard_cap() == 5


def test_hard_cap_invalid_env_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("UA_DAILY_PROACTIVE_WIKI_HARD_CAP", "not-a-number")
    assert nwa._wiki_daily_hard_cap() == 3
    monkeypatch.setenv("UA_DAILY_PROACTIVE_WIKI_HARD_CAP", "-2")
    assert nwa._wiki_daily_hard_cap() == 3
