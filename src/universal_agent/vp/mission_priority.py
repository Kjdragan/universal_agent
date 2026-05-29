"""Semantic priority tiers for VP missions.

A VP mission lands in one of four tiers, ordered by how much delay we
can tolerate before the operator notices something is wrong:

  operator_daily   — Kevin reads it with morning coffee. Briefings, the
                     YouTube daily digest, the evening recap. >2h delay
                     = he notices = SLA breach.
  operator_signal  — Atlas-generated proactive intelligence the operator
                     wants but isn't blocking on. Insight briefs,
                     convergence briefs, research reports.
  maintenance      — System housekeeping. Curation, proactive wiki,
                     cleanup. Should run when the operator-facing tiers
                     are caught up.
  background       — Opportunistic. The default when nothing else is
                     specified — runs when the queue is otherwise clear.

Why tiers above numeric priority:
- Numeric-only schemes (lower=urgent, range 1-100, default 100) bite the
  forgetful caller: any cron author who omits priority gets the lowest
  urgency by default. That trap killed the 2026-05-27 morning briefing,
  which sat at priority=100 (default) behind ~110 insight_briefs at
  priority=3.
- Tier names make intent unambiguous in code review and grep.
- The default tier ('background') is the SAFE default — forgotten work
  runs last, not blocks the queue.
- Within a tier, numeric `priority` still works as a fine-grained
  tiebreaker, and `created_at` is the final tiebreaker.

Add new mission types here when you create them. If a mission_type is
missing from the map, the resolver returns 'background' — safe default,
no starvation.

See docs/01_Architecture/vp_mission_priority_tiers.md for the full
design rationale and operator-facing SLA expectations.
"""
from __future__ import annotations

from typing import Iterable, Literal

# Public tier type. Use this in type hints downstream.
PriorityTier = Literal[
    "operator_daily",
    "operator_signal",
    "maintenance",
    "background",
]

TIERS: tuple[PriorityTier, ...] = (
    "operator_daily",
    "operator_signal",
    "maintenance",
    "background",
)

# Lower rank = claimed sooner. Hardcoded so the SQL CASE expression in
# claim_next_vp_mission stays in lockstep with this map.
TIER_RANK: dict[str, int] = {
    "operator_daily": 0,
    "operator_signal": 1,
    "maintenance": 2,
    "background": 3,
}

DEFAULT_TIER: PriorityTier = "background"

# Source of truth: mission_type → tier. If a dispatch site needs to
# override (rare — usually means the mission_type should move tiers),
# pass priority_tier explicitly to dispatch_vp_mission.
MISSION_TYPE_TIER: dict[str, PriorityTier] = {
    # — operator_daily: Kevin reads these every morning/evening —
    "briefing": "operator_daily",
    "morning_briefing": "operator_daily",
    "evening_briefing": "operator_daily",
    "youtube_daily_digest": "operator_daily",

    # — operator_signal: proactive intelligence, operator-visible but
    #   not on a tight schedule —
    "insight_brief": "operator_signal",
    "convergence_brief": "operator_signal",
    # convergence_evaluation is Atlas's ship/skip/defer + author pass that
    # FEEDS Simone's hourly intel digest. It must outrank maintenance
    # (curation) and background, or the digest never gets a brief to batch.
    # (Omitting it here resolved to 'background' and starved the digest —
    # see docs/proactive_signals/insight_pipeline_remediation_plan_2026-05-28.md.)
    "convergence_evaluation": "operator_signal",
    # Ideation insight evaluation (Track B sweep) feeds the same digest. Simone
    # dispatches these under LLM-chosen names (observed: "evaluate_ideation_insight",
    # also "ideation_evaluation"); without an operator_signal tier they resolve to
    # 'background' and get drained dead-last behind every convergence mission.
    # The substring guard in resolve_tier() catches any other ideation_* variant.
    "evaluate_ideation_insight": "operator_signal",
    "ideation_evaluation": "operator_signal",
    "ideation_insight": "operator_signal",
    "research": "operator_signal",
    "research_and_report": "operator_signal",
    "research_report_email": "operator_signal",

    # — maintenance: housekeeping, system upkeep —
    "curation": "maintenance",
    "proactive_wiki": "maintenance",
    "doc-maintenance": "maintenance",

    # everything else falls through to DEFAULT_TIER
}


def resolve_tier(mission_type: str | None) -> PriorityTier:
    """Return the canonical tier for a mission_type, defaulting to background.

    Unknown / empty mission types resolve to 'background' so they can't
    starve operator-facing work just because nobody mapped them yet.
    """
    if not mission_type:
        return DEFAULT_TIER
    key = str(mission_type).strip()
    if key in MISSION_TYPE_TIER:
        return MISSION_TYPE_TIER[key]
    # Defensive guard: the proactive-insight mission families (convergence /
    # ideation / intel-brief authoring) are dispatched under LLM-chosen names
    # that we can't fully enumerate. They are always operator-facing signal,
    # never background — so a burst of them can never sink below maintenance.
    low = key.lower()
    if any(token in low for token in ("ideation", "convergence", "intel_brief", "insight_brief")):
        return "operator_signal"
    return DEFAULT_TIER


def is_valid_tier(value: object) -> bool:
    return value in TIERS


def tier_rank(tier: str) -> int:
    """Return the sort rank for a tier (lower = claimed sooner)."""
    return TIER_RANK.get(str(tier), TIER_RANK[DEFAULT_TIER])


def all_mission_types_in_tier(tier: PriorityTier) -> Iterable[str]:
    """Return the mission_types that map to the given tier."""
    return tuple(mt for mt, t in MISSION_TYPE_TIER.items() if t == tier)
