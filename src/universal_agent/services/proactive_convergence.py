"""Cross-channel convergence detection for proactive intelligence."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable, Optional

from universal_agent.services.proactive_artifacts import (
    ARTIFACT_STATUS_CANDIDATE,
    make_artifact_id,
    upsert_artifact,
)
from universal_agent.services.proactive_task_builder import queue_proactive_task

logger = logging.getLogger(__name__)

SignatureMatcher = Callable[[dict[str, Any], list[dict[str, Any]]], list[dict[str, Any]]]

_SIGNATURE_SYSTEM = """\
You extract compact topic signatures from AI/developer video transcripts for a proactive intelligence system.
Return ONLY JSON with this shape:
{
  "primary_topics": ["1-3 short topic names"],
  "secondary_topics": ["0-5 related topics"],
  "key_claims": ["2-6 concise claims from the source"],
  "content_type": "tutorial" | "analysis" | "news" | "opinion" | "other"
}
"""

_MATCH_SYSTEM = """\
You judge whether recent videos from independent channels substantially cover the same subject.
Return ONLY JSON:
{
  "matches": [
    {"video_id": "id", "reason": "short reason"}
  ],
  "signal_strength": 8
}
Match on semantic topic convergence, not exact wording. Exclude weakly related items.
Rate the convergence signal_strength from 1-10 (10 being an extremely tight, actionable match).
"""

_IDEATION_SYSTEM = """\
You are an expert intelligence synthesizer analyzing a batch of recent video schemas from a specific domain.
Do you see any abstract relationships, interesting consistencies, conflicting viewpoints, or macro-trends emerging that aren't obvious? Capture the spirit of the activity.
Return ONLY JSON:
{
  "insights": [
    {
      "narrative": "A compelling narrative or trend",
      "value": "Why this insight is actionable or non-obvious",
      "supporting_video_ids": ["id1", "id2"],
      "confidence": 0.0
    }
  ]
}
The `confidence` field is REQUIRED and is a self-rating from 0.0 to 1.0 indicating how confident
you are that this pattern is real (sourced from the supporting videos) rather than fabricated or
over-generalized from too few signals. Use 0.9+ only when the supporting videos explicitly converge
on the claim. Use 0.5-0.7 when the pattern is suggestive but speculative. Use <0.5 when you're
stretching to find a connection — downstream filters drop these.
If there are no non-obvious relationships, return an empty "insights" list. Do not generate generic summaries.
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create convergence tables and indexes if they do not exist."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS proactive_topic_signatures (
            video_id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL DEFAULT '',
            channel_name TEXT NOT NULL DEFAULT '',
            video_title TEXT NOT NULL DEFAULT '',
            video_url TEXT NOT NULL DEFAULT '',
            ingested_at TEXT NOT NULL,
            primary_topics_json TEXT NOT NULL DEFAULT '[]',
            secondary_topics_json TEXT NOT NULL DEFAULT '[]',
            key_claims_json TEXT NOT NULL DEFAULT '[]',
            content_type TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_proactive_topic_signatures_ingested
            ON proactive_topic_signatures(ingested_at DESC);
        CREATE INDEX IF NOT EXISTS idx_proactive_topic_signatures_channel
            ON proactive_topic_signatures(channel_id, ingested_at DESC);

        CREATE TABLE IF NOT EXISTS proactive_convergence_events (
            event_id TEXT PRIMARY KEY,
            primary_topic TEXT NOT NULL,
            video_ids_json TEXT NOT NULL DEFAULT '[]',
            channel_names_json TEXT NOT NULL DEFAULT '[]',
            brief_task_id TEXT NOT NULL DEFAULT '',
            artifact_id TEXT NOT NULL DEFAULT '',
            feedback_score INTEGER,
            detected_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_proactive_convergence_events_detected
            ON proactive_convergence_events(detected_at DESC);

        -- PR B: convergence candidate ledger.  Detected by SQL clustering in
        -- the rewritten CSI sync (PR C), evaluated by Atlas via the
        -- /evaluate-and-author-intel-brief skill (PR C).  PR B only ships
        -- the table — no code reads or writes it yet.
        CREATE TABLE IF NOT EXISTS convergence_candidates (
            candidate_id TEXT PRIMARY KEY,
            video_ids_json TEXT NOT NULL DEFAULT '[]',
            channel_names_json TEXT NOT NULL DEFAULT '[]',
            channel_count INTEGER NOT NULL DEFAULT 0,
            primary_topics_json TEXT NOT NULL DEFAULT '[]',
            signatures_json TEXT NOT NULL DEFAULT '[]',
            task_id TEXT NOT NULL DEFAULT '',
            verdict TEXT NOT NULL DEFAULT '',
            verdict_reasoning TEXT NOT NULL DEFAULT '',
            artifact_id TEXT NOT NULL DEFAULT '',
            detected_at TEXT NOT NULL,
            evaluated_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_convergence_candidates_verdict
            ON convergence_candidates(verdict, detected_at DESC);
        CREATE INDEX IF NOT EXISTS idx_convergence_candidates_detected
            ON convergence_candidates(detected_at DESC);
        CREATE INDEX IF NOT EXISTS idx_convergence_candidates_task
            ON convergence_candidates(task_id);
        """
    )
    conn.commit()


def upsert_topic_signature(
    conn: sqlite3.Connection,
    *,
    video_id: str,
    channel_id: str = "",
    channel_name: str = "",
    video_title: str = "",
    video_url: str = "",
    ingested_at: str = "",
    primary_topics: list[str] | None = None,
    secondary_topics: list[str] | None = None,
    key_claims: list[str] | None = None,
    content_type: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert or update a topic signature for a video, returning the stored row."""
    ensure_schema(conn)
    clean_video_id = str(video_id or "").strip()
    if not clean_video_id:
        raise ValueError("video_id is required")
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO proactive_topic_signatures (
            video_id, channel_id, channel_name, video_title, video_url, ingested_at,
            primary_topics_json, secondary_topics_json, key_claims_json,
            content_type, metadata_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            channel_id=excluded.channel_id,
            channel_name=excluded.channel_name,
            video_title=excluded.video_title,
            video_url=excluded.video_url,
            ingested_at=excluded.ingested_at,
            primary_topics_json=excluded.primary_topics_json,
            secondary_topics_json=excluded.secondary_topics_json,
            key_claims_json=excluded.key_claims_json,
            content_type=excluded.content_type,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        (
            clean_video_id,
            str(channel_id or "").strip(),
            str(channel_name or "").strip(),
            str(video_title or "").strip(),
            str(video_url or "").strip(),
            ingested_at or now,
            _json_dumps(_clean_list(primary_topics or [])),
            _json_dumps(_clean_list(secondary_topics or [])),
            _json_dumps(_clean_list(key_claims or [])),
            str(content_type or "").strip(),
            _json_dumps(metadata or {}),
            now,
            now,
        ),
    )
    conn.commit()
    return get_topic_signature(conn, clean_video_id) or {}


def sync_topic_signatures_from_csi(
    conn: sqlite3.Connection,
    *,
    csi_db_path: Path | None,
    limit: int = 400,
    source_window_hours: int = 72,
    min_channels: int = 2,
) -> dict[str, int]:
    """Sync transcript-backed CSI RSS analysis rows into topic signatures.

    After syncing new signatures, performs deterministic SQL-only cluster
    detection (GROUP BY primary_topic across distinct channels within
    ``source_window_hours``) and writes a ``convergence_candidate`` per
    cluster via :func:`write_convergence_candidate`. The candidate is the
    new evaluation handle for Atlas's ``/evaluate-and-author-intel-brief``
    skill.

    The legacy LLM-driven Track A/B pipeline (``detect_and_queue_convergence``,
    ``track_a_concrete_convergence``, ``track_b_ideation_synthesis``) is NOT
    invoked here anymore — it remains callable from the gateway's two
    hand-trigger convergence endpoints until PR E cleans it up.

    Return shape preserved for backward compatibility with callers that
    assert on ``upserted`` / ``seen``. ``convergence_events`` now reports the
    number of candidates written this run (was: number of LLM-confirmed
    convergence brief tasks).
    """
    if csi_db_path is None or not csi_db_path.exists():
        return {"seen": 0, "upserted": 0, "convergence_events": 0, "candidates_written": 0}
    ensure_schema(conn)
    db = sqlite3.connect(str(csi_db_path))
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            """
            SELECT
                e.event_id, e.occurred_at, e.subject_json,
                a.category, a.summary_text, a.analysis_json, a.analyzed_at,
                a.transcript_status
            FROM events e
            LEFT JOIN rss_event_analysis a ON a.event_id = e.event_id
            WHERE e.source = 'youtube_channel_rss'
              AND a.summary_text IS NOT NULL
              AND a.summary_text != ''
            ORDER BY COALESCE(a.analyzed_at, e.occurred_at) DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 1000)),),
        ).fetchall()
    except sqlite3.Error:
        return {"seen": 0, "upserted": 0, "convergence_events": 0, "candidates_written": 0}
    finally:
        db.close()

    upserted = 0
    for row in rows:
        subject = _json_loads_obj(row["subject_json"])
        analysis = _json_loads_obj(row["analysis_json"])
        video_id = str(subject.get("video_id") or row["event_id"] or "").strip()
        if not video_id:
            continue

        # Skip if we already have a topic signature for this video
        if get_topic_signature(conn, video_id):
            continue

        topics = _analysis_topics(analysis=analysis, category=str(row["category"] or ""), title=str(subject.get("title") or ""))
        upsert_topic_signature(
            conn,
            video_id=video_id,
            channel_id=str(subject.get("channel_id") or "").strip(),
            channel_name=str(subject.get("channel_name") or subject.get("author_name") or "").strip(),
            video_title=str(subject.get("title") or subject.get("media_title") or "").strip(),
            video_url=str(subject.get("url") or "").strip(),
            ingested_at=str(row["analyzed_at"] or row["occurred_at"] or _now_iso()),
            primary_topics=topics[:3],
            secondary_topics=topics[3:8],
            key_claims=_analysis_claims(analysis=analysis, summary_text=str(row["summary_text"] or "")),
            content_type=str(row["category"] or analysis.get("category") or "other").strip(),
            metadata={
                "event_id": str(row["event_id"] or ""),
                "source": "csi_rss_analysis",
                "transcript_status": str(row["transcript_status"] or ""),
            },
        )
        upserted += 1

    # Convergence detection. Runs every call (the cron is the cadence governor)
    # so genuine convergence in the existing window is still found when no new
    # signatures landed this run; candidate_id stability + write-once verdict
    # semantics keep it idempotent.
    #
    # LLM precision layer (default): SQL recall buckets → per-bucket LLM judge
    # that confirms a genuine shared thesis and emits only high-strength
    # clusters. Falls back to raw SQL buckets when UA_CONVERGENCE_LLM_CLUSTERING=0.
    candidates_written = 0
    if _llm_clustering_enabled():
        confirmed = _detect_clusters_llm(
            conn,
            source_window_hours=source_window_hours,
            min_channels=min_channels,
        )
        clusters = [
            (c["signatures"], c.get("thesis", ""), float(c.get("signal_strength") or 0))
            for c in confirmed
        ]
    else:
        clusters = [
            (sigs, "", 0.0)
            for sigs in _detect_clusters_sql(
                conn,
                source_window_hours=source_window_hours,
                min_channels=min_channels,
            )
        ]

    for cluster_signatures, thesis, strength in clusters:
        try:
            result = write_convergence_candidate(
                conn,
                signatures=cluster_signatures,
                source_window_hours=source_window_hours,
                thesis=thesis,
                signal_strength=strength,
            )
            # Only count rows that newly queued a task (verdict='' and a fresh task_id).
            if result and result.get("_newly_queued"):
                candidates_written += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "write_convergence_candidate failed for cluster (size=%d): %s",
                len(cluster_signatures),
                exc,
            )

    # Ideation sweep (Track B restored): non-obvious abstract patterns over the
    # recent corpus — the higher-value insight engine that convergence detection
    # (same-story = news saturation) cannot surface. Routes through the same
    # de-poisoned convergence_candidate → Atlas → digest path with
    # candidate_kind='ideation'. Disable via UA_IDEATION_SWEEP_ENABLED=0.
    ideation_written = 0
    if _ideation_sweep_enabled():
        try:
            ideations = _run_ideation_sweep(conn, source_window_hours=source_window_hours)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ideation sweep failed: %s", exc)
            ideations = []
        for ins in ideations:
            try:
                result = write_convergence_candidate(
                    conn,
                    signatures=ins.get("signatures") or [],
                    source_window_hours=source_window_hours,
                    thesis=str(ins.get("narrative") or ""),
                    value=str(ins.get("value") or ""),
                    signal_strength=float(ins.get("confidence") or 0.0) * 10.0,
                    candidate_kind="ideation",
                )
                if result and result.get("_newly_queued"):
                    ideation_written += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("write ideation candidate failed: %s", exc)

    return {
        "seen": len(rows),
        "upserted": upserted,
        # Legacy key kept for any caller still reading it.
        "convergence_events": candidates_written,
        "candidates_written": candidates_written,
        "ideation_candidates_written": ideation_written,
    }


def _detect_clusters_sql(
    conn: sqlite3.Connection,
    *,
    source_window_hours: int,
    min_channels: int,
) -> list[list[dict[str, Any]]]:
    """Group recent signatures by shared primary topic across ≥ ``min_channels`` channels.

    Pure SQL scan + Python grouping. No LLM. Window is rolling — last
    ``source_window_hours`` of ``ingested_at``. A signature can participate
    in multiple clusters if it has multiple primary_topics.
    """
    ensure_schema(conn)
    horizon = datetime.now(timezone.utc) - timedelta(hours=max(1, int(source_window_hours)))
    since = horizon.isoformat()
    rows = conn.execute(
        """
        SELECT *
        FROM proactive_topic_signatures
        WHERE ingested_at >= ?
        ORDER BY ingested_at DESC
        LIMIT 500
        """,
        (since,),
    ).fetchall()
    signatures = [_hydrate_signature(dict(row)) for row in rows]

    # Bucket by primary topic (case-insensitive, trimmed).
    buckets: dict[str, list[dict[str, Any]]] = {}
    for sig in signatures:
        for topic in sig.get("primary_topics") or []:
            key = str(topic or "").strip().lower()
            if not key:
                continue
            buckets.setdefault(key, []).append(sig)

    threshold = max(2, int(min_channels or 2))
    clusters: list[list[dict[str, Any]]] = []
    seen_cluster_keys: set[str] = set()
    for _topic_key, members in buckets.items():
        channels = {
            str(item.get("channel_name") or item.get("channel_id") or "").strip()
            for item in members
        }
        channels.discard("")
        if len(channels) < threshold:
            continue
        # De-duplicate clusters when two topics have the exact same video set.
        video_ids = sorted({
            str(item.get("video_id") or "").strip()
            for item in members
            if str(item.get("video_id") or "").strip()
        })
        if not video_ids:
            continue
        cluster_key = "|".join(video_ids)
        if cluster_key in seen_cluster_keys:
            continue
        seen_cluster_keys.add(cluster_key)
        clusters.append(members)
    return clusters


# ── LLM convergence-precision layer ────────────────────────────────────
#
# `_detect_clusters_sql` is a cheap RECALL net: it buckets by a coarse
# primary_topic tag, which lumps unrelated content (a single "ai_coding"
# bucket spanned cooking, true-crime, crypto, bodycam in production). Those
# false buckets caused Atlas to skip 100% of candidates → the digest never
# got fuel. This layer adds LLM PRECISION: for each coarse bucket, a single
# bounded LLM call (routed to ZAI via llm_classifier._call_llm) judges whether
# the videos genuinely converge on the same concrete story/thesis, returns the
# converging subset + a one-line thesis + a 1-10 signal_strength, and gates on
# strength. Aligns with the LLM-native design philosophy (code collects/pre-
# filters; the LLM synthesizes meaning). See
# docs/proactive_signals/llm_convergence_clustering_2026-05-29.md.

_CLUSTER_REFINE_SYSTEM = """\
You judge whether a group of recent videos from different channels GENUINELY
converge on the SAME concrete story, event, or specific thesis — not merely
sharing a broad topic label.

The videos were coarsely bucketed by a shared topic tag. Many such buckets are
false: a broad tag (e.g. "ai_coding", "general_interest") lumps together
unrelated content. Your job is PRECISION — identify only the subset (if any)
that truly converges on one specific subject.

Return ONLY JSON:
{
  "is_convergence": true,
  "thesis": "One sentence naming the SPECIFIC shared story/event/claim.",
  "converging_video_ids": ["id1", "id2"],
  "signal_strength": 8
}

Rules:
- Real convergence = >=2 INDEPENDENT channels covering the same SPECIFIC subject
  (same event / same claim / same narrow thesis), NOT just the same broad category.
- Drop videos that only share the broad tag but cover different subjects.
- signal_strength 1-10: 10 = tight, specific, actionable multi-channel convergence;
  <=6 = loose / topical-only / coincidental.
- If there is no genuine specific convergence, return
  {"is_convergence": false, "thesis": "", "converging_video_ids": [], "signal_strength": 0}.
"""


def _llm_clustering_enabled() -> bool:
    """LLM precision layer on by default; flip UA_CONVERGENCE_LLM_CLUSTERING=0 to
    fall back to raw SQL string-match clustering (legacy behaviour)."""
    return str(os.getenv("UA_CONVERGENCE_LLM_CLUSTERING", "1")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _min_signal_strength() -> int:
    raw = str(os.getenv("UA_CONVERGENCE_MIN_STRENGTH", "7")).strip()
    try:
        return max(1, min(10, int(raw)))
    except ValueError:
        return 7


def _independent_channels(signatures: list[dict[str, Any]]) -> set[str]:
    out = {
        str(s.get("channel_name") or s.get("channel_id") or "").strip()
        for s in signatures
    }
    out.discard("")
    return out


async def _refine_cluster_with_llm(
    bucket: list[dict[str, Any]],
    *,
    min_channels: int,
) -> Optional[dict[str, Any]]:
    """Refine one coarse topic bucket into a genuine convergence cluster.

    Returns ``{"signatures": [...], "thesis": str, "signal_strength": float}``
    for a confirmed cluster, or ``None`` when the bucket is not a real
    convergence (or the LLM call/parse fails — fail closed, no candidate).
    """
    if len(bucket) < 2:
        return None
    compact = [
        {
            "video_id": s.get("video_id"),
            "channel": s.get("channel_name") or s.get("channel_id"),
            "title": s.get("video_title"),
            "primary_topics": s.get("primary_topics"),
            "key_claims": (s.get("key_claims") or [])[:4],
        }
        for s in bucket
    ]
    user = json.dumps({"videos": compact}, ensure_ascii=True)
    try:
        from universal_agent.services.llm_classifier import (
            _call_llm,
            _parse_json_response,
        )

        raw = await _call_llm(system=_CLUSTER_REFINE_SYSTEM, user=user, max_tokens=1200)
        parsed = _parse_json_response(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("convergence LLM refine failed (bucket size=%d): %s", len(bucket), exc)
        return None

    if not isinstance(parsed, dict) or not parsed.get("is_convergence"):
        return None
    try:
        strength = float(parsed.get("signal_strength") or 0)
    except (TypeError, ValueError):
        strength = 0.0
    if strength < _min_signal_strength():
        return None

    confirmed_ids = {
        str(v).strip()
        for v in (parsed.get("converging_video_ids") or [])
        if str(v).strip()
    }
    confirmed = [s for s in bucket if str(s.get("video_id") or "").strip() in confirmed_ids]
    # The LLM must have kept a real multi-channel subset.
    if len(_independent_channels(confirmed)) < max(2, int(min_channels or 2)):
        return None
    return {
        "signatures": confirmed,
        "thesis": str(parsed.get("thesis") or "").strip(),
        "signal_strength": strength,
    }


async def _detect_clusters_llm_async(
    conn: sqlite3.Connection,
    *,
    source_window_hours: int,
    min_channels: int,
) -> list[dict[str, Any]]:
    """Recall (SQL buckets) → precision (LLM refine each). Returns confirmed
    clusters as dicts with ``signatures`` / ``thesis`` / ``signal_strength``."""
    buckets = _detect_clusters_sql(
        conn,
        source_window_hours=source_window_hours,
        min_channels=min_channels,
    )
    confirmed: list[dict[str, Any]] = []
    for bucket in buckets:
        refined = await _refine_cluster_with_llm(bucket, min_channels=min_channels)
        if refined:
            confirmed.append(refined)
    return confirmed


def _detect_clusters_llm(
    conn: sqlite3.Connection,
    *,
    source_window_hours: int,
    min_channels: int,
) -> list[dict[str, Any]]:
    """Sync wrapper around the async LLM clustering (mirrors the loop-handling
    pattern used by ``detect_and_queue_convergence``)."""
    coro = _detect_clusters_llm_async(
        conn, source_window_hours=source_window_hours, min_channels=min_channels
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import nest_asyncio

    nest_asyncio.apply()
    return loop.run_until_complete(coro)


# ── Ideation sweep (Track B restored) ──────────────────────────────────
#
# Track A / convergence detection finds the SAME story across channels — which
# is, by construction, news saturation (low marginal value). Track B is the
# higher-value engine: it synthesizes NON-OBVIOUS abstract patterns, trends, and
# cross-cutting relationships from the recent corpus (e.g. "the manufactured-
# reality FORMAT has converged across enemies" — the kind of insight the operator
# actually values). It was removed in PR C for cost; restored here because ZAI/GLM
# quota is abundant (operator decision 2026-05-29). Output routes through the SAME
# de-poisoned convergence_candidate → Atlas → digest path with candidate_kind=
# 'ideation'. See docs/proactive_signals/ideation_sweep_2026-05-29.md.


def _ideation_sweep_enabled() -> bool:
    return str(os.getenv("UA_IDEATION_SWEEP_ENABLED", "1")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _ideation_min_confidence() -> float:
    raw = str(os.getenv("UA_IDEATION_MIN_CONFIDENCE", "0.7")).strip()
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        return 0.7


def _load_recent_signatures(
    conn: sqlite3.Connection, *, source_window_hours: int, limit: int = 60
) -> list[dict[str, Any]]:
    """Most-recent topic signatures within the rolling window (corpus for ideation)."""
    ensure_schema(conn)
    horizon = datetime.now(timezone.utc) - timedelta(hours=max(1, int(source_window_hours)))
    rows = conn.execute(
        """
        SELECT * FROM proactive_topic_signatures
        WHERE ingested_at >= ?
        ORDER BY ingested_at DESC
        LIMIT ?
        """,
        (horizon.isoformat(), max(2, int(limit))),
    ).fetchall()
    return [_hydrate_signature(dict(row)) for row in rows]


async def _run_ideation_sweep_async(
    conn: sqlite3.Connection,
    *,
    source_window_hours: int,
    max_signatures: int = 60,
) -> list[dict[str, Any]]:
    """Run Track B ideation synthesis over the recent corpus.

    Chunks the corpus into batches of 20 (track_b's analysis cap) so more than
    20 recent videos are covered, gates each insight on the confidence floor,
    and returns ``[{narrative, value, confidence, signatures}]``. Fails closed
    per batch (a failed LLM call drops that batch, never a false insight).
    """
    sigs = _load_recent_signatures(
        conn, source_window_hours=source_window_hours, limit=max_signatures
    )
    if len(sigs) < 2:
        return []
    floor = _ideation_min_confidence()
    insights: list[dict[str, Any]] = []
    for start in range(0, len(sigs), 20):
        batch = sigs[start:start + 20]
        if len(batch) < 2:
            continue
        try:
            batch_insights = await track_b_ideation_synthesis(batch)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ideation sweep batch failed (size=%d): %s", len(batch), exc)
            continue
        for ins in batch_insights:
            if float(ins.get("confidence") or 0.0) >= floor and len(ins.get("signatures") or []) >= 2:
                insights.append(ins)
    return insights


def _run_ideation_sweep(
    conn: sqlite3.Connection, *, source_window_hours: int
) -> list[dict[str, Any]]:
    """Sync wrapper around the async ideation sweep (loop-handling like clustering)."""
    coro = _run_ideation_sweep_async(conn, source_window_hours=source_window_hours)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import nest_asyncio

    nest_asyncio.apply()
    return loop.run_until_complete(coro)


def write_convergence_candidate(
    conn: sqlite3.Connection,
    *,
    signatures: list[dict[str, Any]],
    source_window_hours: int = 72,
    thesis: str = "",
    signal_strength: float = 0.0,
    candidate_kind: str = "convergence",
    value: str = "",
) -> dict[str, Any]:
    """Upsert a ``convergence_candidates`` row and (idempotently) queue Atlas.

    ``candidate_id`` is ``f"cand_{sha256(sorted_video_ids).hexdigest()[:16]}"`` —
    deterministic across CSI runs for the exact same source cluster. If a
    candidate already carries a final verdict (``'ship'`` / ``'skip'`` /
    ``'defer'`` / ``'error'``), this call is a no-op and returns the
    existing row unchanged.

    If the candidate is new or still mid-processing (``verdict=''``), the row
    is upserted with the latest signatures and a Task Hub item is queued for
    Atlas with ``source_kind='convergence_candidate'``,
    ``metadata.preferred_vp='vp.general.primary'``, ``metadata.candidate_id``,
    and ``metadata.index_path``.

    Returns the candidate row dict. An internal ``_newly_queued`` boolean is
    set for callers that want to count fresh writes (the CSI sync uses it).
    """
    ensure_schema(conn)
    if not signatures:
        raise ValueError("signatures must contain at least one entry")

    # Build deterministic candidate_id from sorted unique non-empty video_ids.
    video_ids = sorted({
        str(item.get("video_id") or "").strip()
        for item in signatures
        if str(item.get("video_id") or "").strip()
    })
    if not video_ids:
        raise ValueError("signatures must include at least one video_id")
    seed = "|".join(video_ids)
    candidate_id = f"cand_{hashlib.sha256(seed.encode()).hexdigest()[:16]}"

    existing = _get_convergence_candidate(conn, candidate_id)
    final_verdicts = {"ship", "skip", "defer", "error"}
    if existing and str(existing.get("verdict") or "").strip() in final_verdicts:
        existing["_newly_queued"] = False
        return existing

    # Build channel + topic summary projections.
    channel_names_raw = [
        str(item.get("channel_name") or item.get("channel_id") or "").strip()
        for item in signatures
    ]
    channel_names = sorted({c for c in channel_names_raw if c})
    primary_topics_raw: list[str] = []
    for item in signatures:
        for topic in item.get("primary_topics") or []:
            clean = str(topic or "").strip()
            if clean:
                primary_topics_raw.append(clean)
    # Preserve order but de-duplicate.
    seen: set[str] = set()
    primary_topics: list[str] = []
    for topic in primary_topics_raw:
        key = topic.lower()
        if key in seen:
            continue
        seen.add(key)
        primary_topics.append(topic)

    # Compact signatures for storage (keep the fields Atlas's skill needs).
    compact_signatures = [
        {
            "video_id": item.get("video_id"),
            "channel_id": item.get("channel_id"),
            "channel_name": item.get("channel_name"),
            "video_title": item.get("video_title"),
            "video_url": item.get("video_url"),
            "ingested_at": item.get("ingested_at"),
            "primary_topics": item.get("primary_topics") or [],
            "secondary_topics": item.get("secondary_topics") or [],
            "key_claims": item.get("key_claims") or [],
        }
        for item in signatures
    ]

    # Build the headline topic for task title (most common primary_topic).
    headline = _primary_topic(signatures)

    # Resolve index path (skill consumes this metadata field).
    index_path_env = os.environ.get("UA_RECENT_BRIEFS_INDEX_PATH", "").strip()
    # Don't resolve relative-to-cwd here; pass through whatever the env says,
    # let the skill / helper default to the canonical workspaces dir when empty.

    is_ideation = str(candidate_kind or "convergence").strip().lower() == "ideation"
    task_id = f"convergence-candidate:{candidate_id.removeprefix('cand_')}"
    description = _candidate_task_description(
        candidate_id=candidate_id,
        headline=headline,
        candidate_count=len(signatures),
        channel_count=len(channel_names),
        index_path=index_path_env,
        thesis=thesis,
        signal_strength=signal_strength,
        candidate_kind=candidate_kind,
        value=value,
    )
    metadata_payload = {
        "source": "convergence_candidate",
        "candidate_id": candidate_id,
        "candidate_kind": "ideation" if is_ideation else "convergence",
        "preferred_vp": "vp.general.primary",
        "primary_topic": headline,
        "thesis": thesis,
        "value": value,
        "signal_strength": float(signal_strength or 0.0),
        "video_ids": video_ids,
        "channel_count": len(channel_names),
        "source_window_hours": int(source_window_hours),
        "index_path": index_path_env,
        "invoke_skill": "evaluate-and-author-intel-brief",
    }

    if is_ideation:
        title = f"ATLAS evaluate ideation insight: {headline}"
        labels = ["agent-ready", "ideation", "atlas", "candidate", "insight"]
    else:
        title = f"ATLAS evaluate convergence candidate: {headline}"
        labels = ["agent-ready", "convergence", "atlas", "candidate"]

    task = queue_proactive_task(
        conn,
        task_id=task_id,
        source_kind="convergence_candidate",
        source_ref=candidate_id,
        title=title,
        description=description,
        priority=3,
        labels=labels,
        metadata=metadata_payload,
    )

    # Persist / refresh the candidate row.
    now = _now_iso()
    created_at = (existing or {}).get("created_at") or now
    conn.execute(
        """
        INSERT INTO convergence_candidates (
            candidate_id, video_ids_json, channel_names_json, channel_count,
            primary_topics_json, signatures_json, task_id, verdict,
            verdict_reasoning, artifact_id, detected_at, evaluated_at,
            created_at, updated_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, '', '', '', ?, '', ?, ?, ?)
        ON CONFLICT(candidate_id) DO UPDATE SET
            video_ids_json=excluded.video_ids_json,
            channel_names_json=excluded.channel_names_json,
            channel_count=excluded.channel_count,
            primary_topics_json=excluded.primary_topics_json,
            signatures_json=excluded.signatures_json,
            task_id=excluded.task_id,
            updated_at=excluded.updated_at,
            metadata_json=excluded.metadata_json
        """,
        (
            candidate_id,
            _json_dumps(video_ids),
            _json_dumps(channel_names),
            len(channel_names),
            _json_dumps(primary_topics),
            _json_dumps(compact_signatures),
            str(task.get("task_id") or task_id),
            now,
            created_at,
            now,
            _json_dumps({
                "preferred_vp": "vp.general.primary",
                "headline": headline,
                "candidate_kind": "ideation" if is_ideation else "convergence",
                "thesis": thesis,
                "value": value,
                "signal_strength": float(signal_strength or 0.0),
                "source_window_hours": int(source_window_hours),
                "task_status": str(task.get("status") or ""),
            }),
        ),
    )
    conn.commit()

    row = _get_convergence_candidate(conn, candidate_id) or {}
    row["_newly_queued"] = True
    row["_task"] = task
    return row


def _get_convergence_candidate(
    conn: sqlite3.Connection, candidate_id: str
) -> Optional[dict[str, Any]]:
    """Fetch a single convergence_candidates row by candidate_id."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM convergence_candidates WHERE candidate_id = ? LIMIT 1",
        (str(candidate_id or "").strip(),),
    ).fetchone()
    if not row:
        return None
    data = dict(row)
    data["video_ids"] = _json_loads_list(data.pop("video_ids_json", "[]"))
    data["channel_names"] = _json_loads_list(data.pop("channel_names_json", "[]"))
    data["primary_topics"] = _json_loads_list(data.pop("primary_topics_json", "[]"))
    data["signatures"] = _json_loads_list(data.pop("signatures_json", "[]"))
    data["metadata"] = _json_loads_obj(data.pop("metadata_json", "{}"))
    return data


def _candidate_task_description(
    *,
    candidate_id: str,
    headline: str,
    candidate_count: int,
    channel_count: int,
    index_path: str,
    thesis: str = "",
    signal_strength: float = 0.0,
    candidate_kind: str = "convergence",
    value: str = "",
) -> str:
    """Render the Task Hub task description for a convergence_candidate item.

    Directs Atlas to invoke the ``/evaluate-and-author-intel-brief`` skill,
    explains batch-serial semantics, and forbids direct emailing (delivery is
    handled by the consolidated digest pipeline). For ``candidate_kind=='ideation'``
    the framing flips from same-story convergence to non-obvious-pattern judgment.
    """
    index_hint = index_path or "(default: workspaces dir)"
    is_ideation = str(candidate_kind or "convergence").strip().lower() == "ideation"

    framing = [
        "FRAMING: This task was generated by the csi_convergence_sync cron, NOT by Kevin.",
        "Kevin did not ask for this. When composing any operator-facing artifact,",
        "open with proactive-discovery phrasing — e.g. 'I noticed a non-obvious pattern'",
        "or 'Heads up: a trend worth two minutes'. Do NOT frame this as 'as you requested'.",
        "",
    ]
    common_tail = [
        "",
        "If multiple candidate tasks are claimed in the same batch, process them",
        "serially — each evaluation's verdict appears in the recent briefs index",
        "before the next one runs, enabling consistency across the batch.",
        "",
        f"Recent briefs index path: {index_hint}",
        "",
        "Do NOT email Kevin directly. Delivery is handled by the consolidated digest",
        "pipeline (Simone's /hourly-intel-digest skill).",
    ]

    if is_ideation:
        body = [
            f"IDEATION INSIGHT (candidate_kind=ideation, confidence {signal_strength:.0f}/10).",
            f"Candidate ID: {candidate_id}",
            f"Spans {candidate_count} videos across {channel_count} channels.",
            "",
            "This is NOT a same-story convergence — it is a NON-OBVIOUS abstract pattern,",
            "trend, or cross-cutting relationship the LLM ideation sweep synthesized from",
            "the recent corpus. Judge it on NOVELTY, INSIGHT, and ACTIONABILITY for an",
            "operator who builds/runs an AI agent platform — NOT on multi-channel overlap.",
            "Do NOT skip it merely because the videos cover different topics; that is the point.",
            "",
            "LLM-synthesized narrative (the insight):",
            f"  {thesis}",
            "Why it matters (the 'so what'):",
            f"  {value}",
            "",
            "Invoke the skill `/evaluate-and-author-intel-brief` with this candidate_id.",
            "Verify the narrative against the sources; ship if it is genuinely non-obvious",
            "and useful, skip if it is generic/obvious/unsupported, else defer.",
        ]
    else:
        thesis_lines: list[str] = []
        if thesis:
            thesis_lines = [
                f"LLM-detected convergence thesis (signal_strength {signal_strength:.0f}/10):",
                f"  {thesis}",
                "This thesis is the LLM clustering pass's reason for surfacing the cluster;",
                "verify it against the sources, then ship/skip/defer accordingly.",
                "",
            ]
        body = [
            f"Convergence candidate cluster: {candidate_count} sources across {channel_count} channels.",
            f"Candidate ID: {candidate_id}",
            f"Headline topic: {headline}",
            "",
            *thesis_lines,
            "Invoke the skill `/evaluate-and-author-intel-brief` with this candidate_id.",
            "The skill handles ship/skip/defer judgment and authoring.",
        ]

    return "\n".join(framing + body + common_tail)


def get_topic_signature(conn: sqlite3.Connection, video_id: str) -> Optional[dict[str, Any]]:
    """Fetch a single topic signature by video_id, returning None if not found."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM proactive_topic_signatures WHERE video_id = ? LIMIT 1",
        (str(video_id or "").strip(),),
    ).fetchone()
    return _hydrate_signature(dict(row)) if row else None


def detect_and_queue_convergence(
    conn: sqlite3.Connection,
    *,
    signature: dict[str, Any],
    window_hours: int = 72,
    min_channels: int = 2,
) -> list[dict[str, Any]]:
    """Detect concrete convergence and abstract insights synchronously.

    Legacy LLM-driven pipeline. Still callable from gateway hand-trigger
    endpoints; no longer invoked by ``sync_topic_signatures_from_csi``
    (replaced by SQL cluster detection + ``write_convergence_candidate``).
    Scheduled for removal in PR E.
    """
    coro = _detect_and_queue_convergence_async(
        conn,
        signature=signature,
        window_hours=window_hours,
        min_channels=min_channels,
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    if loop.is_running():
        import nest_asyncio

        nest_asyncio.apply()
        return loop.run_until_complete(coro)
    return loop.run_until_complete(coro)


async def detect_and_queue_convergence_llm(
    conn: sqlite3.Connection,
    *,
    signature: dict[str, Any],
    window_hours: int = 72,
    min_channels: int = 2,
) -> list[dict[str, Any]]:
    """Async API used by gateway endpoints that already run inside an event loop."""
    return await _detect_and_queue_convergence_async(
        conn,
        signature=signature,
        window_hours=window_hours,
        min_channels=min_channels,
    )


async def _detect_and_queue_convergence_async(
    conn: sqlite3.Connection,
    *,
    signature: dict[str, Any],
    window_hours: int = 72,
    min_channels: int = 2,
) -> list[dict[str, Any]]:
    """Core async convergence detection across both Track A and Track B."""
    ensure_schema(conn)
    candidates = _recent_other_channel_signatures(conn, signature=signature, window_hours=window_hours)

    created_events = []

    matched_a, insights_b = await asyncio.gather(
        track_a_concrete_convergence(signature, candidates),
        track_b_ideation_synthesis([signature] + candidates[:19]),
    )

    # Process Track A
    channels_a = {
        str(signature.get("channel_name") or signature.get("channel_id") or "").strip(),
        *[str(item.get("channel_name") or item.get("channel_id") or "").strip() for item in matched_a],
    }
    channels_a.discard("")
    if len(channels_a) >= max(2, int(min_channels or 2)):
        participants = [signature, *matched_a]
        # Pull the stashed signal_strength off any matched item (set by
        # track_a_concrete_convergence). Default 0.0 when not present so callers
        # of create_convergence_brief_task can still invoke it with raw sigs.
        signal_strength = 0.0
        for item in matched_a:
            val = item.get("_signal_strength")
            if val is not None:
                try:
                    signal_strength = float(val)
                except (TypeError, ValueError):
                    signal_strength = 0.0
                break
        result_a = create_convergence_brief_task(
            conn, signatures=participants, signal_strength=signal_strength
        )
        created_events.append(result_a)

    # Process Track B
    for insight in insights_b:
        sigs = insight["signatures"]
        channels_b = {str(item.get("channel_name") or item.get("channel_id") or "").strip() for item in sigs}
        channels_b.discard("")
        if len(channels_b) >= max(2, int(min_channels or 2)):
            result_b = create_insight_brief_task(
                conn,
                narrative=insight["narrative"],
                value=insight["value"],
                signatures=sigs,
                confidence=float(insight.get("confidence") or 0.0),
            )
            created_events.append(result_b)

    return created_events


async def extract_topic_signature_from_text(
    *,
    video_id: str,
    title: str = "",
    transcript_text: str = "",
    summary_text: str = "",
    channel_id: str = "",
    channel_name: str = "",
    video_url: str = "",
    ingested_at: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract a topic signature using an LLM, with deterministic fallback."""
    user = "\n".join(
        [
            f"Video ID: {video_id}",
            f"Title: {title}",
            f"Channel: {channel_name or channel_id}",
            f"Summary: {summary_text[:4000]}",
            "Transcript excerpt:",
            transcript_text[:12000],
        ]
    )
    try:
        from universal_agent.services.llm_classifier import (
            _call_llm,
            _parse_json_response,
        )

        raw = await _call_llm(system=_SIGNATURE_SYSTEM, user=user, max_tokens=900)
        parsed = _parse_json_response(raw)
    except Exception as exc:
        parsed = _fallback_signature(title=title, summary_text=summary_text, error=str(exc))

    return {
        "video_id": str(video_id or "").strip(),
        "channel_id": str(channel_id or "").strip(),
        "channel_name": str(channel_name or "").strip(),
        "video_title": str(title or "").strip(),
        "video_url": str(video_url or "").strip(),
        "ingested_at": ingested_at or _now_iso(),
        "primary_topics": _clean_list(parsed.get("primary_topics") if isinstance(parsed, dict) else []),
        "secondary_topics": _clean_list(parsed.get("secondary_topics") if isinstance(parsed, dict) else []),
        "key_claims": _clean_list(parsed.get("key_claims") if isinstance(parsed, dict) else []),
        "content_type": str((parsed or {}).get("content_type") or "other").strip().lower(),
        "metadata": {**(metadata or {}), "signature_method": "llm" if "fallback_error" not in (parsed or {}) else "fallback", **({"fallback_error": parsed.get("fallback_error")} if isinstance(parsed, dict) and parsed.get("fallback_error") else {})},
    }


async def track_a_concrete_convergence(
    signature: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Track A: Fast Filter -> Deep Semantic Comparison -> Quality Gate."""
    if not candidates:
        return []

    # 1. Fast Filter
    sig_topics = set(signature.get("primary_topics", []) + signature.get("secondary_topics", []))
    sig_topics = {t.lower() for t in sig_topics}

    scored_candidates = []
    for cand in candidates:
        cand_topics = set(cand.get("primary_topics", []) + cand.get("secondary_topics", []))
        cand_topics = {t.lower() for t in cand_topics}
        overlap = len(sig_topics.intersection(cand_topics))
        scored_candidates.append((overlap, cand))

    # Keep top 10 with at least some overlap
    scored_candidates.sort(key=lambda x: x[0], reverse=True)
    top_candidates = [cand for overlap, cand in scored_candidates[:10] if overlap > 0]

    if not top_candidates:
        return []

    # 2. Deep Semantic Comparison
    compact_candidates = [
        {
            "video_id": item.get("video_id"),
            "channel": item.get("channel_name") or item.get("channel_id"),
            "title": item.get("video_title"),
            "primary_topics": item.get("primary_topics"),
            "secondary_topics": item.get("secondary_topics"),
            "key_claims": item.get("key_claims"),
        }
        for item in top_candidates
    ]
    user = json.dumps(
        {
            "new_signature": {
                "video_id": signature.get("video_id"),
                "channel": signature.get("channel_name") or signature.get("channel_id"),
                "title": signature.get("video_title"),
                "primary_topics": signature.get("primary_topics"),
                "secondary_topics": signature.get("secondary_topics"),
                "key_claims": signature.get("key_claims"),
            },
            "recent_candidates": compact_candidates,
        },
        ensure_ascii=True,
    )
    try:
        from universal_agent.services.llm_classifier import (
            _call_llm,
            _parse_json_response,
        )

        raw = await _call_llm(system=_MATCH_SYSTEM, user=user, max_tokens=1200)
        parsed = _parse_json_response(raw)

        # 3. Quality Gate
        if not isinstance(parsed, dict):
            return []

        signal_strength = parsed.get("signal_strength", 0)
        if signal_strength < 8:
            return []

        matched_ids = {
            str(item.get("video_id") or "").strip()
            for item in (parsed.get("matches") if isinstance(parsed.get("matches"), list) else []) or []
            if isinstance(item, dict)
        }
        matched = [item for item in candidates if str(item.get("video_id") or "").strip() in matched_ids]
        if matched:
            # Stash the LLM-rated signal_strength on a sentinel key so the caller
            # can persist it into the brief artifact metadata downstream. Using a
            # leading underscore avoids colliding with topic signature fields.
            try:
                strength_val = float(signal_strength)
            except (TypeError, ValueError):
                strength_val = 0.0
            for item in matched:
                item["_signal_strength"] = strength_val
            return matched
    except Exception:
        pass
    return []


async def track_b_ideation_synthesis(
    batch: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Track B: LLM Ideation / Synthesis on a batch of schemas."""
    if len(batch) < 2:
        return []

    compact_batch = [
        {
            "video_id": item.get("video_id"),
            "channel": item.get("channel_name") or item.get("channel_id"),
            "title": item.get("video_title"),
            "primary_topics": item.get("primary_topics"),
            "secondary_topics": item.get("secondary_topics"),
            "key_claims": item.get("key_claims"),
        }
        for item in batch[:20]
    ]
    user = json.dumps(
        {"recent_schemas": compact_batch},
        ensure_ascii=True,
    )

    try:
        from universal_agent.services.llm_classifier import (
            _call_llm,
            _parse_json_response,
        )

        raw = await _call_llm(system=_IDEATION_SYSTEM, user=user, max_tokens=1500)
        parsed = _parse_json_response(raw)

        if not isinstance(parsed, dict):
            return []

        insights = parsed.get("insights", [])
        if not isinstance(insights, list):
            return []

        results = []
        for insight in insights:
            if not isinstance(insight, dict):
                continue
            narrative = str(insight.get("narrative") or "").strip()
            value = str(insight.get("value") or "").strip()
            supporting_ids = set(insight.get("supporting_video_ids") or [])

            try:
                raw_conf = insight.get("confidence")
                confidence = float(raw_conf) if raw_conf is not None else 0.0
            except (TypeError, ValueError):
                confidence = 0.0
            # Clamp to [0, 1]
            confidence = max(0.0, min(1.0, confidence))

            if narrative and value and len(supporting_ids) >= 2:
                supporting_sigs = [item for item in batch if item.get("video_id") in supporting_ids]
                if len(supporting_sigs) >= 2:
                    results.append({
                        "narrative": narrative,
                        "value": value,
                        "confidence": confidence,
                        "signatures": supporting_sigs,
                    })
        return results
    except Exception:
        pass
    return []


def create_convergence_brief_task(
    conn: sqlite3.Connection,
    *,
    signatures: list[dict[str, Any]],
    signal_strength: float = 0.0,
) -> dict[str, Any]:
    """Queue a convergence brief task and artifact from matched signatures.

    ``signal_strength`` is the LLM-rated 1-10 convergence score from
    :func:`track_a_concrete_convergence`. It is persisted into the artifact
    metadata under ``signal_strength`` (normalized to a float) for downstream
    scoring (e.g. ``hourly_insight_email``)."""
    ensure_schema(conn)
    if len(signatures) < 2:
        raise ValueError("at least two signatures are required")
    try:
        strength = float(signal_strength)
    except (TypeError, ValueError):
        strength = 0.0
    # Count of distinct channels backing this convergence — used by the
    # hourly-email composite-score channel_breadth term.
    supporting_channels = {
        str(item.get("channel_name") or item.get("channel_id") or "").strip()
        for item in signatures
    }
    supporting_channels.discard("")
    supporting_channel_count = len(supporting_channels)
    primary_topic = _primary_topic(signatures)
    video_ids = [str(item.get("video_id") or "").strip() for item in signatures if str(item.get("video_id") or "").strip()]
    event_id = _convergence_event_id(primary_topic=primary_topic, video_ids=video_ids)
    task_id = f"convergence-brief:{event_id.removeprefix('conv_')}"
    preference_context = _preference_context(conn, task_type="convergence_brief", topic_tags=["convergence", primary_topic])
    description = _brief_task_description(
        primary_topic=primary_topic,
        signatures=signatures,
        preference_context=preference_context,
    )
    task = queue_proactive_task(
        conn,
        task_id=task_id,
        source_kind="convergence_detection",
        source_ref=event_id,
        title=f"ATLAS convergence brief: {primary_topic}",
        description=description,
        priority=3,
        labels=["agent-ready", "convergence", "atlas", "research"],
        metadata={
            "source": "convergence_detection",
            "event_id": event_id,
            "primary_topic": primary_topic,
            "video_ids": video_ids,
            "preferred_vp": "vp.general.primary",
        },
    )
    artifact = upsert_artifact(
        conn,
        artifact_id=make_artifact_id(
            source_kind="convergence_detection",
            source_ref=event_id,
            artifact_type="convergence_brief_task",
            title=primary_topic,
        ),
        artifact_type="convergence_brief_task",
        source_kind="convergence_detection",
        source_ref=event_id,
        title=str(task.get("title") or ""),
        summary=f"Queued ATLAS convergence brief for {len(signatures)} independent sources on {primary_topic}.",
        status=ARTIFACT_STATUS_CANDIDATE,
        priority=3,
        topic_tags=["convergence", primary_topic],
        metadata={
            "task_id": task_id,
            "event_id": event_id,
            "video_ids": video_ids,
            "signal_strength": strength,
            "supporting_channel_count": supporting_channel_count,
        },
    )
    _record_convergence_event(conn, event_id=event_id, primary_topic=primary_topic, signatures=signatures, task_id=task_id, artifact_id=artifact["artifact_id"])
    return {"event": get_convergence_event(conn, event_id), "task": task, "artifact": artifact}


def create_insight_brief_task(
    conn: sqlite3.Connection,
    *,
    narrative: str,
    value: str,
    signatures: list[dict[str, Any]],
    confidence: float = 0.0,
) -> dict[str, Any]:
    """Queue an insight brief task from an abstract ideation narrative.

    ``confidence`` is the LLM self-rating (0.0-1.0) from the Track-B ideation
    prompt and is persisted into the artifact metadata under ``confidence``
    for downstream scoring (e.g. ``hourly_insight_email``)."""
    ensure_schema(conn)
    primary_topic = (narrative[:47] + "...") if len(narrative) > 50 else narrative
    video_ids = [str(item.get("video_id") or "").strip() for item in signatures if str(item.get("video_id") or "").strip()]
    event_id = _convergence_event_id(primary_topic=primary_topic, video_ids=video_ids)
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    supporting_channels = {
        str(item.get("channel_name") or item.get("channel_id") or "").strip()
        for item in signatures
    }
    supporting_channels.discard("")
    supporting_channel_count = len(supporting_channels)

    task_id = f"insight-brief:{event_id.removeprefix('conv_')}"
    preference_context = _preference_context(conn, task_type="insight_brief", topic_tags=["insight", primary_topic])

    lines = [
        "FRAMING: This task was generated by the csi_convergence_sync cron, NOT by Kevin.",
        "Kevin did not ask for this. When composing any email or chat reply, open with",
        "phrasing like 'I noticed a non-obvious pattern emerging — here's the insight' or",
        "'Heads up: insight signal on X.' Do NOT say 'as you requested', 'you asked for',",
        "'here's the X evaluation you wanted', or any framing that implies this was",
        "operator-initiated. If you cannot honestly attribute the request to Kevin, you",
        "must frame it as proactive discovery.",
        "",
        f"Generate an insight brief about: {primary_topic}",
        "",
        "An abstract macro-trend or non-obvious relationship has been detected.",
        f"Narrative: {narrative}",
        f"Value/Actionability: {value}",
        "",
        "Sources:",
    ]
    for item in signatures:
        claims = "; ".join(item.get("key_claims") or []) or "(no extracted claims)"
        lines.append(
            f"- {item.get('channel_name') or item.get('channel_id')}: {item.get('video_title') or item.get('video_id')} | {item.get('video_url') or ''} | claims: {claims}"
        )
    lines.extend(
        [
            "",
            "Produce a concise brief with:",
            "1. THE INSIGHT: what is the non-obvious relationship or macro-trend.",
            "2. THE EVIDENCE: how the sources support this.",
            "3. SO WHAT: why Kevin should care and what is actionable.",
            "",
            "DELIVERABLE FILENAME CONTRACT (mandatory):",
            "- HTML rendering MUST be saved as `insight_artifact.html` at the workspace root.",
            "- PDF rendering MUST be saved as `insight_artifact.pdf` at the workspace root.",
            "- Do NOT use `brief.html` / `brief.pdf` — those collide visually with `BRIEF.md`",
            "  (the pre-work self-briefing artifact) and have caused operator confusion.",
            "- If you delegate this work to another VP/agent, propagate this contract verbatim.",
            "",
            "Store the final brief as a durable artifact via "
            "services.proactive_artifacts.upsert_artifact. Do NOT email Kevin",
            "directly. Delivery is handled by the consolidated digest pipeline.",
        ]
    )
    if preference_context:
        lines.extend(["", "Preference context:", preference_context])
    description = "\n".join(lines)

    task = queue_proactive_task(
        conn,
        task_id=task_id,
        source_kind="insight_detection",
        source_ref=event_id,
        title=f"ATLAS insight brief: {primary_topic}",
        description=description,
        priority=3,
        labels=["agent-ready", "insight", "atlas", "research"],
        metadata={
            "source": "insight_detection",
            "event_id": event_id,
            "primary_topic": primary_topic,
            "video_ids": video_ids,
            "preferred_vp": "vp.general.primary",
        },
    )
    artifact = upsert_artifact(
        conn,
        artifact_id=make_artifact_id(
            source_kind="insight_detection",
            source_ref=event_id,
            artifact_type="insight_brief_task",
            title=primary_topic,
        ),
        artifact_type="insight_brief_task",
        source_kind="insight_detection",
        source_ref=event_id,
        title=str(task.get("title") or ""),
        summary=f"Queued ATLAS insight brief for {len(signatures)} sources.",
        status=ARTIFACT_STATUS_CANDIDATE,
        priority=3,
        topic_tags=["insight", primary_topic],
        metadata={
            "task_id": task_id,
            "event_id": event_id,
            "video_ids": video_ids,
            "confidence": conf,
            "supporting_channel_count": supporting_channel_count,
        },
    )
    _record_convergence_event(conn, event_id=event_id, primary_topic=primary_topic, signatures=signatures, task_id=task_id, artifact_id=artifact["artifact_id"])
    return {"event": get_convergence_event(conn, event_id), "task": task, "artifact": artifact}


def get_convergence_event(conn: sqlite3.Connection, event_id: str) -> Optional[dict[str, Any]]:
    """Fetch a single convergence event by event_id, returning None if not found."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM proactive_convergence_events WHERE event_id = ? LIMIT 1",
        (str(event_id or "").strip(),),
    ).fetchone()
    return _hydrate_event(dict(row)) if row else None


def _recent_other_channel_signatures(
    conn: sqlite3.Connection,
    *,
    signature: dict[str, Any],
    window_hours: int,
) -> list[dict[str, Any]]:
    """Query signatures from other channels within a rolling time window."""
    ingested = _parse_time(signature.get("ingested_at")) or datetime.now(timezone.utc)
    start = (ingested - timedelta(hours=max(1, int(window_hours or 72)))).isoformat()
    end = ingested.isoformat()
    channel_id = str(signature.get("channel_id") or "").strip()
    video_id = str(signature.get("video_id") or "").strip()
    content_type = str(signature.get("content_type") or "").strip()
    rows = conn.execute(
        """
        SELECT *
        FROM proactive_topic_signatures
        WHERE ingested_at >= ?
          AND ingested_at <= ?
          AND video_id != ?
          AND (? = '' OR channel_id != ?)
          AND (? = '' OR content_type = ?)
        ORDER BY ingested_at DESC
        LIMIT 80
        """,
        (start, end, video_id, channel_id, channel_id, content_type, content_type),
    ).fetchall()
    return [_hydrate_signature(dict(row)) for row in rows]


def _analysis_topics(*, analysis: dict[str, Any], category: str, title: str) -> list[str]:
    """Extract topic strings from CSI analysis fields and category."""
    raw_topics: list[Any] = []
    for key in ("themes", "topics", "primary_topics", "tags"):
        value = analysis.get(key)
        if isinstance(value, list):
            raw_topics.extend(value)
    if category:
        raw_topics.append(category)
    if not raw_topics:
        raw_topics.extend(_fallback_signature(title=title, summary_text="").get("primary_topics", []))
    return _clean_list(raw_topics)[:8]


def _analysis_claims(*, analysis: dict[str, Any], summary_text: str) -> list[str]:
    """Extract claim strings from analysis keys, falling back to summary."""
    raw_claims: list[Any] = []
    for key in ("key_claims", "claims", "takeaways"):
        value = analysis.get(key)
        if isinstance(value, list):
            raw_claims.extend(value)
    if not raw_claims and summary_text:
        raw_claims.append(summary_text[:300])
    return _clean_list(raw_claims)[:8]


def _fallback_signature(*, title: str, summary_text: str, error: str = "") -> dict[str, Any]:
    """Generate a deterministic topic signature from title and summary when LLM fails."""
    words = [
        word.strip(".,:;!?()[]{}\"'").lower()
        for word in f"{title} {summary_text}".split()
    ]
    stop = {"the", "and", "for", "with", "from", "this", "that", "into", "about", "your", "you", "are", "how", "why"}
    topics = []
    for word in words:
        if len(word) < 4 or word in stop:
            continue
        if word not in topics:
            topics.append(word)
        if len(topics) >= 3:
            break
    return {
        "primary_topics": topics or ["emerging topic"],
        "secondary_topics": [],
        "key_claims": [summary_text[:240]] if summary_text else [],
        "content_type": "other",
        "fallback_error": error,
    }


def _primary_topic(signatures: list[dict[str, Any]]) -> str:
    """Return the most common primary topic across a set of signatures."""
    counts: dict[str, int] = {}
    for signature in signatures:
        for topic in signature.get("primary_topics") or []:
            clean = str(topic or "").strip()
            if clean:
                counts[clean] = counts.get(clean, 0) + 1
    if not counts:
        return "emerging topic"
    return sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))[0][0]


def _brief_task_description(*, primary_topic: str, signatures: list[dict[str, Any]], preference_context: str = "") -> str:
    """Build the task description for a convergence brief research task."""
    lines = [
        "FRAMING: This task was generated by the csi_convergence_sync cron, NOT by Kevin.",
        "Kevin did not ask for this. When composing any email or chat reply, open with",
        "phrasing like 'I noticed multiple independent sources are covering X — here's a",
        "synthesis' or 'Heads up: convergence signal on X.' Do NOT say 'as you requested',",
        "'you asked for', 'here's the X evaluation you wanted', or any framing that implies",
        "this was operator-initiated. If you cannot honestly attribute the request to Kevin,",
        "you must frame it as proactive discovery.",
        "",
        f"Generate a convergence brief about: {primary_topic}",
        "",
        "Multiple independent channels covered this topic recently.",
        "",
        "Sources:",
    ]
    for item in signatures:
        claims = "; ".join(item.get("key_claims") or []) or "(no extracted claims)"
        lines.append(
            f"- {item.get('channel_name') or item.get('channel_id')}: {item.get('video_title') or item.get('video_id')} | {item.get('video_url') or ''} | claims: {claims}"
        )
    lines.extend(
        [
            "",
            "Produce a concise brief with:",
            "1. CONVERGENCE SIGNAL: what topic is converging and why now.",
            "2. CONSENSUS: where sources agree.",
            "3. DIVERGENCE: where sources differ.",
            "4. SO WHAT: why Kevin should care and what is actionable.",
            "",
            "Store the final brief as a durable artifact via "
            "services.proactive_artifacts.upsert_artifact. Do NOT email Kevin",
            "directly. Delivery is handled by the consolidated digest pipeline.",
        ]
    )
    if preference_context:
        lines.extend(["", "Preference context:", preference_context])
    return "\n".join(lines)


def _preference_context(conn: sqlite3.Connection, *, task_type: str, topic_tags: list[str]) -> str:
    """Fetch preference delegation context, returning empty string on failure."""
    try:
        from universal_agent.services.proactive_preferences import (
            get_delegation_context,
        )

        return get_delegation_context(conn, task_type=task_type, topic_tags=topic_tags)
    except Exception:
        return ""


def _record_convergence_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    primary_topic: str,
    signatures: list[dict[str, Any]],
    task_id: str,
    artifact_id: str,
) -> None:
    """Persist a convergence event row to the database."""
    now = _now_iso()
    video_ids = [str(item.get("video_id") or "").strip() for item in signatures if str(item.get("video_id") or "").strip()]
    channel_names = [str(item.get("channel_name") or item.get("channel_id") or "").strip() for item in signatures]
    conn.execute(
        """
        INSERT INTO proactive_convergence_events (
            event_id, primary_topic, video_ids_json, channel_names_json,
            brief_task_id, artifact_id, detected_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id) DO UPDATE SET
            brief_task_id=excluded.brief_task_id,
            artifact_id=excluded.artifact_id,
            metadata_json=excluded.metadata_json
        """,
        (
            event_id,
            primary_topic,
            _json_dumps(video_ids),
            _json_dumps(channel_names),
            task_id,
            artifact_id,
            now,
            _json_dumps({"source_count": len(signatures)}),
        ),
    )
    conn.commit()


def _convergence_event_id(*, primary_topic: str, video_ids: list[str]) -> str:
    """Generate a deterministic convergence event ID from topic and video IDs."""
    seed = "|".join([primary_topic.lower(), *sorted(video_ids)])
    return f"conv_{hashlib.sha256(seed.encode()).hexdigest()[:16]}"


def _hydrate_signature(row: dict[str, Any]) -> dict[str, Any]:
    """Convert JSON-prefixed columns into parsed Python lists and dicts."""
    row["primary_topics"] = _json_loads_list(row.pop("primary_topics_json", "[]"))
    row["secondary_topics"] = _json_loads_list(row.pop("secondary_topics_json", "[]"))
    row["key_claims"] = _json_loads_list(row.pop("key_claims_json", "[]"))
    row["metadata"] = _json_loads_obj(row.pop("metadata_json", "{}"))
    return row


def _hydrate_event(row: dict[str, Any]) -> dict[str, Any]:
    """Convert JSON-prefixed columns in an event row into parsed Python objects."""
    row["video_ids"] = _json_loads_list(row.pop("video_ids_json", "[]"))
    row["channel_names"] = _json_loads_list(row.pop("channel_names_json", "[]"))
    row["metadata"] = _json_loads_obj(row.pop("metadata_json", "{}"))
    return row


def _clean_list(values: list[Any]) -> list[str]:
    """Strip and filter empty strings from a list of values."""
    return [str(value).strip() for value in values if str(value).strip()]


def _json_dumps(value: Any) -> str:
    """Serialize value to compact, deterministic JSON with ASCII escaping."""
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _json_loads_list(raw: Any) -> list[Any]:
    """Parse a JSON list from raw text or return the input if already a list."""
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            return []
        if isinstance(parsed, list):
            return parsed
    return []


def _json_loads_obj(raw: Any) -> dict[str, Any]:
    """Parse a JSON object from raw text or return the input if already a dict."""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _parse_time(raw: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string into a timezone-aware datetime."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()
