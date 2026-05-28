"""Hourly insight-delivery email composer.

Structural replacement for Simone's per-brief email loop. Once per hour the
``hourly_insight_email`` cron invokes :func:`compose_hourly_email`, which:

  1. Pulls every undelivered ATLAS brief artifact created in the last
     ``hour_window_hours`` (``insight_brief_task`` + ``convergence_brief_task``)
     from ``activity_state.db``.
  2. Scores each candidate via a composite:
        ``0.4*confidence + 0.3*channel_breadth_norm + 0.2*novelty + 0.1*pref``
     where ``channel_breadth_norm = min(supporting_channel_count / 5.0, 1.0)``
     and ``novelty = 1 - max_jaccard_topic_overlap_against_last_7d_briefs``.
  3. Applies a floor (``supporting_channel_count >= 3`` AND
     ``confidence >= 0.7``) but does NOT use it as a hard filter — sub-threshold
     briefs are still ranked and can fill the slot when nothing cleared.
  4. Picks the top-scored brief for ``insight_1``; picks ``insight_2`` from
     the remainder constrained to Jaccard topic-overlap < 0.30 with #1.
  5. Returns a render payload (subject + text/html) or ``None`` when there
     are zero candidates this hour.

Scoring decisions for every considered brief are persisted to
``proactive_brief_scoring_log`` (see ``proactive_scoring_log.log_score``) so
the weekly health-check has audit data to tune against.

This module is pure rendering + scoring. The cron entrypoint
(``scripts/hourly_insight_email.py``) is responsible for actually sending and
stamping ``delivered_at``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html as html_lib
import logging
import os
import sqlite3
from typing import Any, Optional

from universal_agent.services import (
    proactive_artifacts as _pa,
    proactive_scoring_log as _scoring,
)

logger = logging.getLogger(__name__)


# Composite-score weights — keep aligned with the docstring above and the
# weekly health-check report copy.
WEIGHT_CONFIDENCE = 0.4
WEIGHT_CHANNEL_BREADTH = 0.3
WEIGHT_NOVELTY = 0.2
WEIGHT_PREFERENCE = 0.1

# Floor thresholds (≥3 supporting channels AND confidence ≥0.8).
# Raised from 0.7 → 0.8 in PR A of the insight pipeline consolidation
# (docs/proactive_signals/insight_pipeline_consolidation_spec.md) to cut
# low-confidence noise from the hourly digest.
FLOOR_MIN_CHANNELS = 3
FLOOR_MIN_CONFIDENCE = 0.8

# Diversity: insight #2 must have Jaccard topic overlap < this with insight #1.
DIVERSITY_MAX_OVERLAP = 0.30

# Novelty: scan the last 7 days of briefs (capped) to compute overlap.
NOVELTY_WINDOW_DAYS = 7
NOVELTY_PRIOR_LIMIT = 100

ARTIFACT_TYPES = ("insight_brief_task", "convergence_brief_task")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    intersection = a.intersection(b)
    union = a.union(b)
    if not union:
        return 0.0
    return len(intersection) / len(union)


def _normalize_tags(tags: Any) -> set[str]:
    if not tags:
        return set()
    out: set[str] = set()
    for tag in tags:
        if not tag:
            continue
        clean = str(tag).strip().lower()
        if clean:
            out.add(clean)
    return out


def _candidates_in_window(
    conn: sqlite3.Connection,
    *,
    hour_window_hours: int,
) -> list[dict[str, Any]]:
    """Pull undelivered briefs created within the last ``hour_window_hours``.

    Uses the canonical ``ensure_schema`` so the ``delivered_at`` column is
    present even when the DB predates the migration.
    """
    _pa.ensure_schema(conn)
    cutoff = _iso(_now() - timedelta(hours=max(1, int(hour_window_hours))))
    placeholders = ",".join("?" for _ in ARTIFACT_TYPES)
    rows = conn.execute(
        f"""
        SELECT artifact_id, artifact_type, source_kind, source_ref, title,
               summary, status, delivery_state, priority, topic_tags_json,
               metadata_json, created_at, updated_at, delivered_at
        FROM proactive_artifacts
        WHERE artifact_type IN ({placeholders})
          AND created_at >= ?
          AND (delivered_at IS NULL OR delivered_at = '')
        ORDER BY created_at DESC
        """,
        (*ARTIFACT_TYPES, cutoff),
    ).fetchall()
    out = []
    for row in rows:
        out.append(_pa._hydrate_artifact(dict(row)))
    return out


def _prior_brief_topic_sets(
    conn: sqlite3.Connection,
    *,
    days: int = NOVELTY_WINDOW_DAYS,
    limit: int = NOVELTY_PRIOR_LIMIT,
) -> list[set[str]]:
    """Return topic-tag sets for the N most recent briefs in the last `days`.

    Used to compute the novelty term — a brief whose topic-tag set heavily
    overlaps with recently-delivered briefs scores lower. Both delivered AND
    not-delivered prior briefs are included so the score reflects topic
    saturation, not just delivery history.
    """
    cutoff = _iso(_now() - timedelta(days=days))
    placeholders = ",".join("?" for _ in ARTIFACT_TYPES)
    rows = conn.execute(
        f"""
        SELECT topic_tags_json FROM proactive_artifacts
        WHERE artifact_type IN ({placeholders})
          AND created_at >= ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (*ARTIFACT_TYPES, cutoff, max(1, int(limit))),
    ).fetchall()
    sets: list[set[str]] = []
    for row in rows:
        tags = _pa._normalize_list(row["topic_tags_json"])
        sets.append(_normalize_tags(tags))
    return sets


def _compute_novelty(tags: set[str], prior_sets: list[set[str]]) -> float:
    """``novelty = 1 - max_jaccard_overlap_against_prior``.

    When ``prior_sets`` is empty (e.g. a fresh DB), novelty defaults to 1.0
    so the first ever brief isn't penalized for "uniqueness".
    """
    if not prior_sets:
        return 1.0
    max_overlap = 0.0
    for prior in prior_sets:
        overlap = _jaccard(tags, prior)
        if overlap > max_overlap:
            max_overlap = overlap
    return max(0.0, min(1.0, 1.0 - max_overlap))


def _preference_bonus(conn: sqlite3.Connection, artifact: dict[str, Any]) -> float:
    """Normalize ``score_artifact_for_review`` into [0, 1].

    The raw score is ``priority + bonus`` where bonus is unbounded; we clip to
    a [-1, 1] range (preference signals are weights in that range) then shift
    to [0, 1] by ``(clipped + 1) / 2``. Priority alone — without preference
    signal — would dominate; subtracting it isolates the preference term.
    """
    try:
        from universal_agent.services.proactive_preferences import (
            score_artifact_for_review,
        )

        raw = float(score_artifact_for_review(conn, artifact))
        priority = float(artifact.get("priority") or 0)
        bonus = raw - priority
    except Exception:  # noqa: BLE001 — never let scoring break the email
        return 0.5
    clipped = max(-1.0, min(1.0, bonus))
    return (clipped + 1.0) / 2.0


def _composite_score(
    *,
    confidence: float,
    channel_breadth_norm: float,
    novelty: float,
    preference: float,
) -> float:
    return (
        WEIGHT_CONFIDENCE * confidence
        + WEIGHT_CHANNEL_BREADTH * channel_breadth_norm
        + WEIGHT_NOVELTY * novelty
        + WEIGHT_PREFERENCE * preference
    )


def _short_title(title: str, *, max_len: int = 60) -> str:
    clean = (title or "").strip()
    # Strip the verbose "ATLAS insight brief: " / "ATLAS convergence brief: "
    # prefixes so the subject line shows the actual topic.
    for prefix in ("ATLAS insight brief: ", "ATLAS convergence brief: "):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1].rstrip() + "…"


def _dashboard_url(artifact_id: str) -> str:
    base = (os.getenv("UA_DASHBOARD_BASE_URL") or "").strip().rstrip("/")
    if base:
        return f"{base}/dashboard/proactive?artifact={artifact_id}"
    return f"?artifact={artifact_id}"


def _score_candidate(
    conn: sqlite3.Connection,
    *,
    artifact: dict[str, Any],
    prior_sets: list[set[str]],
) -> dict[str, Any]:
    """Compute every scoring input + the composite for one candidate."""
    metadata = artifact.get("metadata") or {}
    # Convergence briefs persist 1-10 signal_strength; insight briefs persist
    # 0.0-1.0 confidence. Normalize signal_strength into [0, 1] so both flow
    # through the same composite term.
    raw_confidence = metadata.get("confidence")
    if raw_confidence is None and metadata.get("signal_strength") is not None:
        try:
            raw_confidence = float(metadata.get("signal_strength")) / 10.0
        except (TypeError, ValueError):
            raw_confidence = 0.0
    try:
        confidence = float(raw_confidence or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    try:
        channels = int(metadata.get("supporting_channel_count") or 0)
    except (TypeError, ValueError):
        channels = 0

    channel_breadth_norm = min(channels / 5.0, 1.0)

    tags = _normalize_tags(artifact.get("topic_tags") or [])
    novelty = _compute_novelty(tags, prior_sets)
    preference = _preference_bonus(conn, artifact)
    composite = _composite_score(
        confidence=confidence,
        channel_breadth_norm=channel_breadth_norm,
        novelty=novelty,
        preference=preference,
    )
    met_floor = channels >= FLOOR_MIN_CHANNELS and confidence >= FLOOR_MIN_CONFIDENCE
    return {
        "artifact": artifact,
        "tags": tags,
        "confidence": confidence,
        "channel_breadth": channels,
        "channel_breadth_norm": channel_breadth_norm,
        "novelty": novelty,
        "preference": preference,
        "composite": composite,
        "met_floor": met_floor,
    }


def _render_text(scored_picks: list[dict[str, Any]]) -> str:
    """Render the plain-text body — one section per surfaced insight."""
    if not scored_picks:
        return "No insights this hour."
    lines: list[str] = []
    for idx, pick in enumerate(scored_picks, start=1):
        artifact = pick["artifact"]
        title = (artifact.get("title") or "").strip() or "(untitled)"
        summary = (artifact.get("summary") or "").strip()
        metadata = artifact.get("metadata") or {}
        channels = pick["channel_breadth"]
        confidence = pick["confidence"]
        composite = pick["composite"]
        floor_note = "" if pick["met_floor"] else "  (below floor — included because no higher-scored candidate cleared)"
        link = _dashboard_url(str(artifact.get("artifact_id") or ""))
        lines.append(f"## Insight {idx}: {title}")
        lines.append("")
        if summary:
            lines.append(summary)
            lines.append("")
        # "So what" — pull the LLM's value rationale out of the task description
        # if present in metadata, else fall back to the artifact summary.
        value = str(metadata.get("value") or "").strip()
        if value:
            lines.append(f"Why it matters: {value}")
            lines.append("")
        video_ids = metadata.get("video_ids") or []
        if video_ids:
            lines.append(f"Supporting channels: {channels} ({len(video_ids)} source videos)")
        else:
            lines.append(f"Supporting channels: {channels}")
        lines.append(
            f"Confidence: {confidence:.2f} · Composite: {composite:.2f}{floor_note}"
        )
        lines.append(f"Dashboard: {link}")
        lines.append("")
        lines.append("---")
        lines.append("")
    lines.append(
        "Reply to this email with one number for quick feedback (1=useful, 5=more like this)."
    )
    return "\n".join(lines).rstrip() + "\n"


def _render_html(text: str) -> str:
    """Minimal styled wrapper around the plain-text body.

    We mirror :class:`IntelligenceReporter._compose_html` — small enough that
    the Gmail filter and threading still work, but a touch nicer to read.
    """
    escaped = html_lib.escape(text).replace("\n", "<br>")
    return (
        "<html><body style=\"font-family:-apple-system,Segoe UI,Roboto,sans-serif;"
        "max-width:680px;line-height:1.5\">"
        f"<p>{escaped}</p>"
        "</body></html>"
    )


def _build_subject(picks: list[dict[str, Any]]) -> str:
    """``[Hourly Intel] <title1> (+ <title2>)`` under 100 chars."""
    if not picks:
        return "[Hourly Intel] (no surfaceable signals this hour)"
    first_title = _short_title(picks[0]["artifact"].get("title") or "")
    if len(picks) == 1:
        subject = f"[Hourly Intel] {first_title}"
    else:
        second_title = _short_title(picks[1]["artifact"].get("title") or "", max_len=40)
        subject = f"[Hourly Intel] {first_title} (+ {second_title})"
    if len(subject) > 100:
        subject = subject[:99] + "…"
    return subject


def compose_hourly_email(
    conn: sqlite3.Connection,
    *,
    hour_window_hours: int = 1,
) -> Optional[dict[str, Any]]:
    """Score, select, and render the hourly insight email payload.

    Returns ``None`` when no candidate briefs exist in the window (cron should
    skip the send entirely). Otherwise returns:

      ``{"subject": str, "html": str, "text": str,
         "insight_1": {...}, "insight_2": {...} | None,
         "met_floor": [bool, bool], "considered": [scored…]}``

    Scoring rows are logged via :mod:`proactive_scoring_log` as a side effect
    so the weekly health-check can reconstruct exactly why each brief did or
    didn't surface this hour.
    """
    _pa.ensure_schema(conn)
    _scoring.ensure_schema(conn)
    candidates = _candidates_in_window(conn, hour_window_hours=hour_window_hours)
    if not candidates:
        return None

    prior_sets = _prior_brief_topic_sets(conn)
    scored = [
        _score_candidate(conn, artifact=art, prior_sets=prior_sets)
        for art in candidates
    ]
    scored.sort(key=lambda s: s["composite"], reverse=True)

    # Insight #1 = highest composite. PR A of the insight pipeline
    # consolidation: if nothing clears the floor, return None so the
    # cron skips the send entirely (empty hours stay empty rather than
    # padding with sub-threshold filler material).
    pick_1 = scored[0]
    if not pick_1["met_floor"]:
        return None
    pick_2: Optional[dict[str, Any]] = None
    for cand in scored[1:]:
        overlap = _jaccard(pick_1["tags"], cand["tags"])
        if overlap < DIVERSITY_MAX_OVERLAP:
            pick_2 = cand
            break

    picks = [pick_1] + ([pick_2] if pick_2 else [])

    subject = _build_subject(picks)
    text = _render_text(picks)
    html = _render_html(text)

    # Log scoring rows for every considered candidate (including the ones not
    # picked) so weekly tuning has a full picture.
    logged_at = _iso(_now())
    chosen_artifact_ids = {
        str(p["artifact"].get("artifact_id") or "") for p in picks
    }
    for idx, sc in enumerate(scored):
        artifact_id = str(sc["artifact"].get("artifact_id") or "")
        if not artifact_id:
            continue
        if artifact_id in chosen_artifact_ids:
            if sc is pick_1:
                slot = _scoring.SLOT_INSIGHT_1
            elif pick_2 is not None and sc is pick_2:
                slot = _scoring.SLOT_INSIGHT_2
            else:
                slot = _scoring.SLOT_NOT_DELIVERED
            delivered_hourly = True
            # PR A of the insight pipeline consolidation removed the
            # SLOT_SUB_THRESHOLD_FILLER fallback — when no candidate
            # clears the floor we return None above instead of padding.
        else:
            slot = _scoring.SLOT_NOT_DELIVERED
            delivered_hourly = False
        _scoring.log_score(
            conn,
            artifact_id=artifact_id,
            confidence=sc["confidence"],
            channel_breadth=sc["channel_breadth"],
            novelty=sc["novelty"],
            preference_bonus=sc["preference"],
            composite_score=sc["composite"],
            met_floor=sc["met_floor"],
            delivered_hourly=delivered_hourly,
            delivered_briefing=False,
            delivery_slot=slot,
            metadata={"rank": idx, "hour_window_hours": int(hour_window_hours)},
            logged_at=logged_at,
        )

    return {
        "subject": subject,
        "html": html,
        "text": text,
        "insight_1": pick_1,
        "insight_2": pick_2,
        "met_floor": [pick_1["met_floor"], bool(pick_2 and pick_2["met_floor"])],
        "considered": scored,
    }
