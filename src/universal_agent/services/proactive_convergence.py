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
import time
from typing import Any, Callable, Optional

from universal_agent import task_hub
from universal_agent.rate_limiter import _is_fup_error
from universal_agent.services.llm_classifier import (
    _call_llm,
    _coerce_score,
    _parse_json_response,
    _resolve_judge_temperature,
)
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

_IDEATION_SYSTEM = """\
You are an expert intelligence synthesizer analyzing the FULL set of recent video schemas from a domain — the complete recent corpus, not a sample.
Reason ACROSS the entire corpus: what abstract relationships, interesting consistencies, conflicting viewpoints, or macro-trends connect videos that wouldn't obviously be grouped together? The most valuable insights span multiple channels/sub-topics — look for cross-cutting patterns over the whole set, not summaries of any single cluster. Capture the spirit of the activity.
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

        -- `proactive_convergence_events` (the legacy per-signature convergence
        -- event ledger) was decommissioned in 2026-05 and dropped in Phase 6
        -- (2026-06-03) — fully superseded by `convergence_candidates` below.
        -- No writers/readers remained; the prod table's historical rows were
        -- archived to a dump before the drop.

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

        -- Stage-2 cluster-refine result cache (added 2026-06-11).
        -- Keyed on the bucket's sorted-video-id set (the ``cluster_key`` from
        -- ``_detect_clusters_sql``).  A cache HIT within the TTL
        -- (``UA_CONVERGENCE_REFINE_CACHE_TTL_HOURS``, default 24h) reuses the
        -- stored verdict and skips the ZAI LLM call.  A bucket that gains or
        -- loses a video produces a new key and is always re-judged.
        CREATE TABLE IF NOT EXISTS convergence_refine_cache (
            cluster_key TEXT PRIMARY KEY,
            is_convergence INTEGER NOT NULL DEFAULT 0,
            signal_strength REAL NOT NULL DEFAULT 0,
            thesis TEXT NOT NULL DEFAULT '',
            converging_video_ids_json TEXT NOT NULL DEFAULT '[]',
            verdict_json TEXT NOT NULL DEFAULT '',
            judged_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_convergence_refine_cache_judged
            ON convergence_refine_cache(judged_at DESC);

        -- Tiny key/value state for the Track B ideation sweep.  Holds the
        -- "last_synth_watermark" (the newest signature ``ingested_at`` that was
        -- successfully synthesized) so the hourly sweep can SKIP when no
        -- materially-new corpus has arrived since the last run, instead of
        -- re-synthesizing the same recent window every cycle (the dominant
        -- source of redundant flagship/opus calls + ZAI 429s).  See
        -- ``_ideation_should_run`` / ``_ideation_advance_watermark``.
        CREATE TABLE IF NOT EXISTS proactive_ideation_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        );
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

    After syncing new signatures, runs convergence detection — SQL recall
    (GROUP BY topic across distinct channels within ``source_window_hours``)
    refined by an optional per-bucket LLM precision pass
    (``_detect_clusters_llm``, default on) — and an ideation sweep
    (``_run_ideation_sweep`` -> :func:`track_b_ideation_synthesis`) for
    non-obvious cross-cutting patterns. Both write a ``convergence_candidate``
    per cluster via :func:`write_convergence_candidate`, the evaluation handle
    for Atlas's ``/evaluate-and-author-intel-brief`` skill.

    The legacy per-signature LLM pipeline (``detect_and_queue_convergence`` /
    ``track_a_concrete_convergence`` / ``create_insight_brief_task``) was
    removed 2026-05; ``track_b_ideation_synthesis`` is retained and driven by
    the ideation sweep.

    Return shape preserved for backward compatibility with callers that
    assert on ``upserted`` / ``seen``. ``convergence_events`` now reports the
    number of candidates written this run (was: number of LLM-confirmed
    convergence brief tasks).
    """
    if csi_db_path is None or not csi_db_path.exists():
        return {"seen": 0, "upserted": 0, "convergence_events": 0, "candidates_written": 0}
    ensure_schema(conn)
    # csi.db is shared with the ~13-process CSI ingester fleet. Set a busy_timeout
    # so a momentary write lock WAITS instead of raising "database is locked" (this
    # is the one csi.db writer that runs as `ua` rather than root, and it does not
    # go through csi_ingester.store.sqlite.connect()). WAL journal mode, enabled by
    # the ingester, is a persistent db property this connection inherits for free.
    db = sqlite3.connect(str(csi_db_path), timeout=15.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=15000")

    # Relevance gate (default ON): drop non-domain categories (geopolitics,
    # cooking, health, noise, ...) at ingest so they never become topic
    # signatures and therefore never become ideation/convergence candidates.
    # Gates an already-LLM-produced judgment (rss_event_analysis.category) —
    # the sanctioned "code gates, LLM synthesizes" pattern. Unknown/NULL/empty
    # categories are KEPT (only known non-domain categories are excluded).
    gate_clause = ""
    gate_params: tuple[str, ...] = ()
    if _relevance_gate_enabled():
        denylist = sorted(_relevance_denylist())
        if denylist:
            placeholders = ", ".join("?" for _ in denylist)
            gate_clause = (
                f"      AND (a.category IS NULL "
                f"OR LOWER(TRIM(a.category)) NOT IN ({placeholders}))\n"
            )
            gate_params = tuple(denylist)

    try:
        rows = db.execute(
            f"""
            SELECT
                e.event_id, e.occurred_at, e.subject_json,
                a.category, a.summary_text, a.analysis_json, a.analyzed_at,
                a.transcript_status
            FROM events e
            LEFT JOIN rss_event_analysis a ON a.event_id = e.event_id
            WHERE e.source = 'youtube_channel_rss'
              AND a.summary_text IS NOT NULL
              AND a.summary_text != ''
            {gate_clause}            ORDER BY COALESCE(a.analyzed_at, e.occurred_at) DESC
            LIMIT ?
            """,
            (*gate_params, max(1, min(int(limit), 1000))),
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
    # Overall wall-clock budget shared by the LLM clustering, candidate-write
    # (triage), and ideation phases so the run always finishes under the cron's
    # 900s timeout. Work not reached this run is idempotently re-detected next.
    deadline = time.monotonic() + _convergence_budget_seconds()

    candidates_written = 0
    refine_stats: dict = {"sonnet_calls_made": 0, "cache_hits": 0}

    # Sweep-level BATCHED triage pre-pass (PR P2). Default OFF:
    # UA_INTEL_TRIAGE_BATCH_SIZE=1 ⇒ legacy per-candidate triage inside each
    # write_convergence_candidate call. When >1, one structured-output call judges
    # up to N candidates and lifts recent_briefs_index into the shared system
    # prompt once — stays OFF until a live batched-vs-per-item A/B holds (operator
    # quality bar). The index is built ONCE per sweep and reused for both passes.
    triage_batch_on = _intel_triage_enabled() and _intel_triage_batch_size() > 1
    triage_idx_text = _triage_index_text(conn) if triage_batch_on else ""
    triage_stats: dict = {"calls_made": 0, "chunks_failed": 0, "skipped": 0}

    if _llm_clustering_enabled():
        confirmed = _detect_clusters_llm(
            conn,
            source_window_hours=source_window_hours,
            min_channels=min_channels,
            deadline=deadline,
            stats=refine_stats,
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

    cluster_overrides: dict[str, dict[str, Any]] = {}
    if triage_batch_on and clusters:
        cluster_overrides = _run_batched_triage(
            conn,
            [
                {"signatures": sigs, "thesis": thesis, "value": "", "candidate_kind": "convergence"}
                for sigs, thesis, _strength in clusters
            ],
            idx_text=triage_idx_text,
            deadline=deadline,
            stats=triage_stats,
        )

    for cluster_signatures, thesis, strength in clusters:
        if time.monotonic() >= deadline:
            break
        try:
            cid = _candidate_id_for_signatures(cluster_signatures)
            result = write_convergence_candidate(
                conn,
                signatures=cluster_signatures,
                source_window_hours=source_window_hours,
                thesis=thesis,
                signal_strength=strength,
                triage_override=cluster_overrides.get(cid) if cid else None,
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
    if _ideation_sweep_enabled() and time.monotonic() < deadline:
        try:
            ideations = _run_ideation_sweep(
                conn, source_window_hours=source_window_hours, deadline=deadline
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ideation sweep failed: %s", exc)
            ideations = []

        ideation_overrides: dict[str, dict[str, Any]] = {}
        if triage_batch_on and ideations:
            ideation_overrides = _run_batched_triage(
                conn,
                [
                    {
                        "signatures": ins.get("signatures") or [],
                        "thesis": str(ins.get("narrative") or ""),
                        "value": str(ins.get("value") or ""),
                        "candidate_kind": "ideation",
                    }
                    for ins in ideations
                ],
                idx_text=triage_idx_text,
                deadline=deadline,
                stats=triage_stats,
            )

        for ins in ideations:
            if time.monotonic() >= deadline:
                break
            try:
                ins_sigs = ins.get("signatures") or []
                cid = _candidate_id_for_signatures(ins_sigs)
                result = write_convergence_candidate(
                    conn,
                    signatures=ins_sigs,
                    source_window_hours=source_window_hours,
                    thesis=str(ins.get("narrative") or ""),
                    value=str(ins.get("value") or ""),
                    signal_strength=float(ins.get("confidence") or 0.0) * 10.0,
                    candidate_kind="ideation",
                    triage_override=ideation_overrides.get(cid) if cid else None,
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
        "sonnet_calls_made": refine_stats["sonnet_calls_made"],
        "cache_hits": refine_stats["cache_hits"],
        # Batched triage telemetry (0 when UA_INTEL_TRIAGE_BATCH_SIZE=1 / off).
        "triage_batch_calls": triage_stats["calls_made"],
        "triage_batch_chunks_failed": triage_stats["chunks_failed"],
    }


def _detect_clusters_sql(
    conn: sqlite3.Connection,
    *,
    source_window_hours: int,
    min_channels: int,
    include_secondary: bool = False,
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

    # Bucket by topic (case-insensitive, trimmed). Recall stage: when
    # ``include_secondary`` is set (the LLM-gated path), a video also joins
    # buckets for its SECONDARY topics so convergences that share only a
    # secondary topic are surfaced for the downstream LLM precision refine to
    # judge. The raw-SQL fallback keeps primary-only bucketing because it has
    # no precision gate to drop the looser matches. (Ported from the retired
    # track_a fast-filter, which scored overlap on primary + secondary topics.)
    buckets: dict[str, list[dict[str, Any]]] = {}
    for sig in signatures:
        topics = list(sig.get("primary_topics") or [])
        if include_secondary:
            topics += list(sig.get("secondary_topics") or [])
        seen_keys: set[str] = set()
        for topic in topics:
            key = str(topic or "").strip().lower()
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
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


# Batched variant of the judge prompt: judges MANY coarse buckets in one call
# (see `_convergence_judge_batch_size`). The closing instruction forces the same
# precision bar per bucket and a verdict array keyed by bucket_id.
_BATCHED_REFINE_SYSTEM = _CLUSTER_REFINE_SYSTEM + """

You are given MULTIPLE coarse topic buckets AT ONCE, each with a numeric
`bucket_id`. Judge EACH bucket INDEPENDENTLY by the rules above, applying the
SAME precision bar to every one — do NOT become lax because there are many
buckets. Use the other buckets only as comparative context (most are broad-tag
lumps; flag only the genuinely specific multi-channel convergences). Return ONLY
JSON with one verdict per bucket_id, covering EVERY bucket_id you were given:
{"verdicts": [{"bucket_id": 0, "is_convergence": true, "thesis": "...", "converging_video_ids": ["id1","id2"], "signal_strength": 8}]}
"""


# Non-domain CSI categories excluded from the ideation/convergence corpus by
# the relevance gate. These are the EMPIRICAL category values the live CSI
# classifier actually emits (verified against rss_event_analysis.category in
# /var/lib/universal-agent/csi/csi.db, 2026-05-30), NOT the compound taxonomy
# (`geopolitics_and_conflict`, `ai_coding_and_agents`) an earlier handoff
# assumed — that mismatch let `geopolitics`/`conflict`/`economics` leak through.
#
# Kept (domain): ai_coding, ai_models, ai_news_and_business, ai_business,
# ai_applications, software_engineering, and `technology`. `technology` is a
# mixed bucket (genuine vibe-coding/dev content alongside occasional politics);
# coarse category gating intentionally keeps it rather than discard real dev
# content — disambiguating within a category is Stage 2's per-video job.
#
# Compound aliases (`geopolitics_and_conflict`) are retained as harmless
# belt-and-suspenders in case the classifier taxonomy reverts.
_DEFAULT_RELEVANCE_DENYLIST: frozenset[str] = frozenset({
    "geopolitics",
    "conflict",
    "economics",
    "cooking",
    "personal_health",
    "noise",
    "other_signal",
    "longform_interviews",
    "from",  # malformed/junk classifier label (e.g. "From the I/O main stage…")
    "geopolitics_and_conflict",  # compound-taxonomy alias (defensive)
})


def _relevance_gate_enabled() -> bool:
    """Category relevance gate on by default; flip UA_RELEVANCE_GATE_ENABLED=0 to
    ingest every category (legacy behaviour)."""
    return str(os.getenv("UA_RELEVANCE_GATE_ENABLED", "1")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _relevance_denylist() -> frozenset[str]:
    """Non-domain categories to exclude at ingest. Overridable via
    UA_IDEATION_RELEVANCE_DENYLIST (comma-separated, case-insensitive) so the
    list is tunable without a deploy; falls back to the code default when unset
    or empty."""
    raw = str(os.getenv("UA_IDEATION_RELEVANCE_DENYLIST", "")).strip()
    if not raw:
        return _DEFAULT_RELEVANCE_DENYLIST
    parsed = {c.strip().lower() for c in raw.split(",") if c.strip()}
    return frozenset(parsed) if parsed else _DEFAULT_RELEVANCE_DENYLIST


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


def _convergence_llm_concurrency() -> int:
    """Max concurrent per-bucket refine calls. `include_secondary` recall
    produces dozens of coarse buckets. The refine LLM path is NOT wrapped by the
    ZAIRateLimiter, so this is the ONLY bound on concurrent load against the ZAI
    proxy from this stage. History: 6 -> 2 (2026-06-10), then **2 -> 1
    (2026-06-13)** — a storm-avoidance pass. Empirically, ZAI Fair-Usage 429s are
    driven by account-wide request *concurrency*, not rate: a ZAI call sent while
    another is in flight rejects ~77%, while a call with nothing else in flight
    rejects ~10% (12h prod analysis). So even a 2-wide fan-out self-overlaps and
    invites the storm. A 1-wide (sequential) judge never overlaps itself, and a
    batched-vs-per-bucket POC (2026-06-13) confirmed the sequential per-bucket
    judge keeps full quality (F1 0.78 vs adjudicated truth) and clears all 61
    live buckets in ~76s — well within the convergence budget (hourly, latency-
    tolerant). Raise UA_CONVERGENCE_LLM_CONCURRENCY only if a measured need
    appears. Default 1 (sequential)."""
    raw = str(os.getenv("UA_CONVERGENCE_LLM_CONCURRENCY", "1")).strip()
    try:
        return max(1, min(16, int(raw)))
    except ValueError:
        return 1


def _convergence_judge_batch_size() -> int:
    """Number of coarse buckets judged per LLM call.

    A 2026-06-13 batch-size sweep (61 live buckets, scored against a blind-
    adjudicated reference truth) found a clear inverted-U: one-bucket-per-call
    (F1 0.78, 61 calls) and one-giant-call (F1 0.67 — attention diluted across
    all buckets) BOTH lose to MODERATE batches. ~20 buckets/call hit **F1 0.84 at
    4 calls and ~half the tokens** — the best point on every axis. Moderate
    batching gives the judge *comparative context* across buckets (sharpening the
    precision call — "these are all just broad-topic lumps; THIS one is a specific
    story") without diluting attention. Default 20; ``UA_CONVERGENCE_JUDGE_BATCH_SIZE``
    overrides. **1 == legacy per-bucket** (one call per bucket)."""
    raw = str(os.getenv("UA_CONVERGENCE_JUDGE_BATCH_SIZE", "20")).strip()
    try:
        return max(1, min(60, int(raw)))
    except ValueError:
        return 20


def _convergence_budget_seconds() -> float:
    """Overall wall-clock budget for the LLM convergence + ideation phases.
    Kept well under the cron's timeout (UA_CSI_CONVERGENCE_CRON_TIMEOUT_SECONDS,
    default 900s) so the run always exits cleanly with partial-but-durable
    results (candidate writes are idempotent; the cron's schedule resumes the
    rest next tick) instead of being SIGKILLed mid-flight and alert-emailing.
    The deadline is checked between calls, so an in-flight call (bounded by
    UA_LLM_CALL_TIMEOUT_SECONDS, default 60s) can overrun it — a live run under
    ZAI throttling overran a 700s budget to ~796s. Default 600s keeps the worst
    case (~660s) comfortably under 900s."""
    raw = str(os.getenv("UA_CSI_CONVERGENCE_BUDGET_SECONDS", "600")).strip()
    try:
        return max(30.0, float(raw))
    except ValueError:
        return 600.0


def _convergence_refine_cache_enabled() -> bool:
    """Stage-2 cluster-refine result cache; ON by default. Set
    UA_CONVERGENCE_REFINE_CACHE_ENABLED=0 to always re-judge every bucket."""
    return str(os.getenv("UA_CONVERGENCE_REFINE_CACHE_ENABLED", "1")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _convergence_refine_cache_ttl_hours() -> float:
    """TTL for a cached refine verdict; default 24h. A row older than this is a miss."""
    raw = str(os.getenv("UA_CONVERGENCE_REFINE_CACHE_TTL_HOURS", "24")).strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 24.0


def _independent_channels(signatures: list[dict[str, Any]]) -> set[str]:
    out = {
        str(s.get("channel_name") or s.get("channel_id") or "").strip()
        for s in signatures
    }
    out.discard("")
    return out


def _refine_cluster_key(bucket: list[dict[str, Any]]) -> str:
    """Same derivation as ``_detect_clusters_sql``'s cluster_key: sorted unique
    non-empty video_ids joined by '|'. Identical bucket -> identical key."""
    vids = sorted({
        str(s.get("video_id") or "").strip()
        for s in bucket
        if str(s.get("video_id") or "").strip()
    })
    return "|".join(vids)


def _refine_cache_get(cluster_key: str) -> Optional[dict[str, Any]]:
    """Fresh (within TTL) cached verdict, else None (miss). Any error -> None."""
    if not cluster_key:
        return None
    ttl_h = _convergence_refine_cache_ttl_hours()
    try:
        from universal_agent.durable.db import connect_runtime_db, get_activity_db_path

        with connect_runtime_db(get_activity_db_path()) as c:
            ensure_schema(c)
            c.row_factory = sqlite3.Row
            row = c.execute(
                "SELECT is_convergence, signal_strength, thesis, "
                "converging_video_ids_json, judged_at "
                "FROM convergence_refine_cache WHERE cluster_key = ? LIMIT 1",
                (cluster_key,),
            ).fetchone()
        if not row:
            return None
        if ttl_h > 0:
            judged = _parse_time(row["judged_at"])
            if judged is None or (datetime.now(timezone.utc) - judged) > timedelta(hours=ttl_h):
                return None
        try:
            conv_ids = json.loads(row["converging_video_ids_json"] or "[]")
        except Exception:
            conv_ids = []
        return {
            "is_convergence": bool(row["is_convergence"]),
            "thesis": str(row["thesis"] or ""),
            "signal_strength": float(row["signal_strength"] or 0.0),
            "converging_video_ids": [str(v) for v in conv_ids if str(v).strip()],
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("refine cache get failed (%s): %s", cluster_key, exc)
        return None


def _refine_cache_put(cluster_key: str, verdict: Optional[dict[str, Any]]) -> None:
    """Store a CLEAN verdict (incl. None = negative). NEVER call for LLM error/FUP."""
    if not cluster_key:
        return
    if verdict is None:
        is_conv, strength, thesis, conv_ids = 0, 0.0, "", []
    else:
        is_conv = 1
        strength = float(verdict.get("signal_strength") or 0.0)
        thesis = str(verdict.get("thesis") or "")
        conv_ids = [
            str(s.get("video_id") or "").strip()
            for s in (verdict.get("signatures") or [])
            if str(s.get("video_id") or "").strip()
        ]
    try:
        from universal_agent.durable.db import connect_runtime_db, get_activity_db_path

        with connect_runtime_db(get_activity_db_path()) as c:
            ensure_schema(c)
            c.execute(
                "INSERT OR REPLACE INTO convergence_refine_cache "
                "(cluster_key, is_convergence, signal_strength, thesis, "
                " converging_video_ids_json, verdict_json, judged_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cluster_key, is_conv, strength, thesis, json.dumps(conv_ids), "",
                 _now_iso()),
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("refine cache put failed (%s): %s", cluster_key, exc)


def _cluster_judge_overrides() -> dict[str, str]:
    """Per-stage model/provider override for the convergence cluster judge.

    DEFAULT (``UA_CONVERGENCE_JUDGE_MODEL`` unset): the **sonnet tier**
    (``glm-5-turbo`` via ``resolve_sonnet()``). A 2026-06-10 A/B over 30 live
    buckets (run twice) showed glm-5-turbo reaches the SAME precision as the
    former opus default (``glm-5.1`` — both confirmed 2/30) while running ~35%
    faster, whereas the cheaper haiku (`glm-4.5-air`, 15/30) and `glm-4.7`
    (11/30) tiers over-confirm broad-topic buckets and fail this precision gate.
    So the judge defaults to sonnet, not opus: equal quality, cheaper, faster,
    and less ZAI Fair-Usage pressure. Validate any tier change with
    ``scripts/convergence_model_ab.py``.

    Overrides (all optional):
    - ``UA_CONVERGENCE_JUDGE_MODEL``    — model id (overrides the sonnet default).
    - ``UA_CONVERGENCE_JUDGE_BASE_URL`` — provider base URL (set to Anthropic's
      to route this one stage to real Claude instead of the ZAI proxy).
    - ``UA_CONVERGENCE_JUDGE_API_KEY``  — key for that provider.
    """
    overrides: dict[str, str] = {}
    for kwarg, env in (
        ("model", "UA_CONVERGENCE_JUDGE_MODEL"),
        ("base_url", "UA_CONVERGENCE_JUDGE_BASE_URL"),
        ("api_key", "UA_CONVERGENCE_JUDGE_API_KEY"),
    ):
        val = (os.getenv(env) or "").strip()
        if val:
            overrides[kwarg] = val
    if "model" not in overrides:
        # No explicit override → default the judge to the sonnet tier
        # (glm-5-turbo). Without this the call falls through to
        # llm_classifier._call_llm's resolve_opus() default (glm-5.1).
        from universal_agent.utils.model_resolution import resolve_sonnet

        overrides["model"] = resolve_sonnet()
    return overrides


def _gate_cluster_verdict(
    bucket: list[dict[str, Any]],
    parsed: Optional[dict[str, Any]],
    *,
    min_channels: int,
) -> Optional[dict[str, Any]]:
    """Apply the convergence precision gates to ONE parsed LLM verdict against
    its bucket. Returns a confirmed cluster ``{signatures, thesis, signal_strength}``
    or None. SHARED by the per-bucket (`_refine_cluster_with_llm`) and batched
    (`_refine_clusters_batched`) judges so both enforce identical guards:
    ``is_convergence`` true, ``signal_strength >= _min_signal_strength()``, and a
    confirmed subset spanning **>=2 INDEPENDENT channels** (the hard structural
    guard against single-channel / topical-only over-confirmation)."""
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

        raw = await _call_llm(
            system=_CLUSTER_REFINE_SYSTEM,
            user=user,
            max_tokens=1200,
            **_cluster_judge_overrides(),
        )
        parsed = _parse_json_response(raw)
    except Exception as exc:  # noqa: BLE001
        # A Fair-Usage-Policy ([1313]/concurrency) error is account-level
        # throttling — grinding through the remaining ~60 buckets would just rack
        # up more doomed ZAI calls and deepen the FUP pressure. Re-raise so the
        # caller (`_detect_clusters_llm_async`) can trip a one-shot circuit
        # breaker and skip the rest of this run. Everything else fails closed
        # (no candidate) as before.
        if _is_fup_error(str(exc)):
            logger.warning(
                "convergence LLM refine hit a Fair-Usage signal (bucket size=%d): %s",
                len(bucket),
                exc,
            )
            raise
        logger.warning("convergence LLM refine failed (bucket size=%d): %s", len(bucket), exc)
        return None

    return _gate_cluster_verdict(bucket, parsed, min_channels=min_channels)


async def _refine_clusters_batched(
    chunk: list[list[dict[str, Any]]],
    *,
    min_channels: int,
) -> list[Optional[dict[str, Any]]]:
    """Judge a CHUNK of coarse buckets in ONE batched LLM call.

    Returns a list aligned 1:1 to ``chunk`` — each element a confirmed cluster
    dict (``{signatures, thesis, signal_strength}``) or None, the same per-bucket
    contract as ``_refine_cluster_with_llm``. Re-raises on a Fair-Usage ([1313])
    signal so the caller's circuit breaker can trip; any other failure or parse
    miss fails CLOSED (every bucket in the chunk -> None, re-detected on the next
    idempotent run). Batching at ``_convergence_judge_batch_size`` both collapses
    the call count AND lifts precision via cross-bucket comparative context
    (2026-06-13 sweep). Each bucket's verdict still passes the identical
    ``_gate_cluster_verdict`` precision guards."""
    out: list[Optional[dict[str, Any]]] = [None] * len(chunk)
    if not chunk:
        return out
    payload = {
        "buckets": [
            {
                "bucket_id": i,
                "videos": [
                    {
                        "video_id": s.get("video_id"),
                        "channel": s.get("channel_name") or s.get("channel_id"),
                        "title": s.get("video_title"),
                        "primary_topics": s.get("primary_topics"),
                        "key_claims": (s.get("key_claims") or [])[:4],
                    }
                    for s in bucket
                ],
            }
            for i, bucket in enumerate(chunk)
        ]
    }
    user = json.dumps(payload, ensure_ascii=True)
    # Output budget scales with chunk size (one verdict per bucket), bounded.
    max_tokens = min(8000, 400 + 220 * len(chunk))
    try:
        from universal_agent.services.llm_classifier import (
            _call_llm,
            _parse_json_response,
        )

        raw = await _call_llm(
            system=_BATCHED_REFINE_SYSTEM,
            user=user,
            max_tokens=max_tokens,
            **_cluster_judge_overrides(),
        )
        parsed = _parse_json_response(raw)
    except Exception as exc:  # noqa: BLE001
        if _is_fup_error(str(exc)):
            logger.warning(
                "convergence batched refine hit a Fair-Usage signal (chunk=%d): %s",
                len(chunk), exc,
            )
            raise
        logger.warning("convergence batched refine failed (chunk=%d): %s", len(chunk), exc)
        return out
    verdicts = parsed.get("verdicts") if isinstance(parsed, dict) else None
    if not isinstance(verdicts, list):
        # Single-bucket chunk: a model may return a BARE verdict object instead
        # of a 1-element `verdicts` array — accept it as the verdict for bucket 0.
        # (The production path uses ~20-bucket chunks and always gets the array.)
        if len(chunk) == 1 and isinstance(parsed, dict) and "is_convergence" in parsed:
            verdicts = [{**parsed, "bucket_id": 0}]
        else:
            logger.warning(
                "convergence batched refine: response had no 'verdicts' array (chunk=%d) — "
                "failing closed for this chunk", len(chunk),
            )
            return out
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        try:
            bid = int(v.get("bucket_id"))
        except (TypeError, ValueError):
            continue
        if 0 <= bid < len(chunk):
            out[bid] = _gate_cluster_verdict(chunk[bid], v, min_channels=min_channels)
    return out


async def _detect_clusters_llm_async(
    conn: sqlite3.Connection,
    *,
    source_window_hours: int,
    min_channels: int,
    deadline: float | None = None,
    stats: dict | None = None,
) -> list[dict[str, Any]]:
    """Recall (SQL buckets) → precision (LLM refine). Returns confirmed clusters
    as dicts with ``signatures`` / ``thesis`` / ``signal_strength``.

    The LLM judges buckets in **batches** of ``_convergence_judge_batch_size``
    (default 20) — one structured-output call per chunk rather than one per
    bucket. A 2026-06-13 batch-size sweep showed ~20/call dominates both extremes
    (per-bucket and one-giant-call) on F1, call count, AND tokens (it gives the
    judge comparative context across buckets without diluting attention). Set
    ``UA_CONVERGENCE_JUDGE_BATCH_SIZE=1`` for legacy per-bucket behavior. Chunks
    run sequentially (bounded by ``_convergence_llm_concurrency``, default 1 —
    storm-avoidance). ``deadline`` (monotonic seconds) caps total wall time:
    chunks not started when the budget is spent are skipped and re-detected next
    run (candidate writes are idempotent). The per-bucket refine cache is honored
    bucket-by-bucket up front, so only UNCACHED buckets reach the LLM.

    ``stats`` (optional mutable dict) accumulates ``sonnet_calls_made`` (now =
    batched chunk calls) and ``cache_hits`` for the caller to surface in
    ``latest_sync.json``."""
    buckets = _detect_clusters_sql(
        conn,
        source_window_hours=source_window_hours,
        min_channels=min_channels,
        include_secondary=True,
    )
    if not buckets:
        return []
    _stats = stats if stats is not None else {}
    _stats.setdefault("sonnet_calls_made", 0)
    _stats.setdefault("cache_hits", 0)
    cache_on = _convergence_refine_cache_enabled()

    # Cache pass: resolve cached buckets up front, collect the uncached for the
    # batched LLM pass. Keeps per-bucket cache granularity even though the LLM
    # now judges buckets in chunks.
    confirmed: list[dict[str, Any]] = []
    to_judge: list[list[dict[str, Any]]] = []
    for bucket in buckets:
        key = _refine_cluster_key(bucket) if cache_on else ""
        if key:
            cached = _refine_cache_get(key)
            if cached is not None:
                _stats["cache_hits"] += 1
                if cached["is_convergence"]:
                    ids = {v for v in cached["converging_video_ids"] if v}
                    csig = [s for s in bucket if str(s.get("video_id") or "").strip() in ids]
                    if len(_independent_channels(csig)) >= max(2, int(min_channels or 2)):
                        confirmed.append({
                            "signatures": csig,
                            "thesis": cached["thesis"],
                            "signal_strength": cached["signal_strength"],
                        })
                continue
        to_judge.append(bucket)

    # Batched LLM pass over uncached buckets — sequential chunks, with the FUP
    # circuit breaker and the wall-clock deadline applied per chunk. A single
    # Fair-Usage ([1313]) signal means ZAI is account-throttling us, so the
    # breaker skips the remaining chunks (re-detected next idempotent run).
    batch_size = _convergence_judge_batch_size()
    chunks = [to_judge[i:i + batch_size] for i in range(0, len(to_judge), batch_size)]
    sem = asyncio.Semaphore(_convergence_llm_concurrency())
    fup_tripped = False
    skipped_after_breaker = 0

    async def _judge_chunk(chunk: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
        nonlocal fup_tripped, skipped_after_breaker
        async with sem:
            if fup_tripped:
                skipped_after_breaker += len(chunk)
                return []
            if deadline is not None and time.monotonic() >= deadline:
                skipped_after_breaker += len(chunk)
                return []
            try:
                results = await _refine_clusters_batched(chunk, min_channels=min_channels)
            except Exception as exc:  # noqa: BLE001
                if _is_fup_error(str(exc)):
                    fup_tripped = True
                    return []
                logger.warning("convergence chunk judge failed: %s", exc)
                return []
            _stats["sonnet_calls_made"] += 1
            if cache_on:
                for bucket, res in zip(chunk, results):
                    key = _refine_cluster_key(bucket)
                    if key:
                        _refine_cache_put(key, res)
            return [r for r in results if r]

    chunk_lists = await asyncio.gather(*[_judge_chunk(c) for c in chunks])
    for cl in chunk_lists:
        confirmed.extend(cl)
    if fup_tripped:
        logger.warning(
            "convergence FUP circuit breaker tripped: a Fair-Usage ([1313]) signal "
            "aborted this run; %d of %d uncached buckets skipped without an LLM call. "
            "The next hourly run re-detects them (candidate writes are idempotent).",
            skipped_after_breaker,
            len(to_judge),
        )
    return confirmed


def _detect_clusters_llm(
    conn: sqlite3.Connection,
    *,
    source_window_hours: int,
    min_channels: int,
    deadline: float | None = None,
    stats: dict | None = None,
) -> list[dict[str, Any]]:
    """Sync wrapper around the async LLM clustering (mirrors the loop-handling
    pattern used by the other sync wrappers in this module).

    ``stats`` is forwarded to :func:`_detect_clusters_llm_async` to accumulate
    ``sonnet_calls_made`` / ``cache_hits`` for the caller."""
    coro = _detect_clusters_llm_async(
        conn,
        source_window_hours=source_window_hours,
        min_channels=min_channels,
        deadline=deadline,
        stats=stats,
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


def _ideation_model() -> str | None:
    """Model for the ideation synthesis call.

    Default ``None`` → ``_call_llm`` resolves the flagship/opus tier (unchanged
    behavior). Set ``UA_IDEATION_MODEL`` (e.g. ``glm-5-turbo``) as an escape
    hatch to move ideation off the contended opus tier *without a deploy* if the
    (now low-volume) flagship calls ever throttle. Not a downgrade by default.
    """
    return (os.getenv("UA_IDEATION_MODEL", "") or "").strip() or None


def _ideation_max_corpus() -> int:
    """Max signatures fed to ONE whole-corpus synthesis call (default 120).

    Replaces the old hardcoded ``limit=60`` + 20-item batch cap. 120 comfortably
    covers the full 72h window (≈131 observed) so the model reasons over the
    whole universe in a single call instead of 3 isolated recency slices.
    """
    try:
        return max(2, int(os.getenv("UA_IDEATION_MAX_CORPUS", "120") or "120"))
    except (TypeError, ValueError):
        return 120


def _ideation_max_tokens() -> int:
    """Output token budget for the whole-corpus synthesis call (default 8000).

    ``max_tokens`` is a CEILING, not a target — the API requires it and you only
    pay for tokens actually generated (live whole-corpus calls land ~1100-1700 on
    glm-5.1). It's set generous for two reasons: (1) headroom so a content-heavy
    run's JSON is never truncated (the one-call design already exceeds the old
    1500 cap), and (2) future-proofing — when the opus tier moves to glm-5.2
    (thinking ON by default, and thinking tokens count against ``max_tokens``),
    a tight budget would truncate the answer. NOTE: when 5.2 lands, the proper
    lever is managing the thinking budget explicitly, not just this ceiling.
    Override with ``UA_IDEATION_MAX_TOKENS``.
    """
    try:
        return max(256, int(os.getenv("UA_IDEATION_MAX_TOKENS", "8000") or "8000"))
    except (TypeError, ValueError):
        return 8000


def _ideation_min_new_signatures() -> int:
    """New-content gate threshold (default 5).

    The hourly sweep SKIPS unless at least this many signatures have arrived
    since the last *successful* synthesis — so we stop re-synthesizing the same
    recent window every cycle (the dominant source of redundant opus calls/429s).
    Set ``UA_IDEATION_MIN_NEW_SIGNATURES=0`` to disable the gate (always run).
    Raising it → fewer, richer runs (more new material per synthesis).
    """
    try:
        return max(0, int(os.getenv("UA_IDEATION_MIN_NEW_SIGNATURES", "5") or "5"))
    except (TypeError, ValueError):
        return 5


_IDEATION_WATERMARK_KEY = "last_synth_watermark"


def _ideation_read_watermark(conn: sqlite3.Connection) -> str:
    """Newest signature ``ingested_at`` synthesized on the last successful run ('' if none)."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT value FROM proactive_ideation_state WHERE key = ?",
        (_IDEATION_WATERMARK_KEY,),
    ).fetchone()
    if row is None:
        return ""
    return str(row[0] or "")


def _ideation_write_watermark(conn: sqlite3.Connection, watermark: str) -> None:
    """Persist the synthesis watermark (best-effort; idempotent upsert)."""
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO proactive_ideation_state(key, value, updated_at) VALUES(?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (_IDEATION_WATERMARK_KEY, str(watermark), _now_iso()),
    )
    conn.commit()


def _ideation_should_run(conn: sqlite3.Connection | None, sigs: list[dict[str, Any]]) -> bool:
    """New-content gate. Fail-OPEN: run unless we can prove little is new.

    Returns ``True`` (run) when ``conn`` is None, the gate is disabled
    (``min_new<=0``), there's no prior watermark (first run), or anything goes
    wrong reading state — we never silently starve the intel pipeline. Returns
    ``False`` (skip) only when a watermark exists AND fewer than ``min_new``
    signatures are newer than it.
    """
    if conn is None:
        return True
    min_new = _ideation_min_new_signatures()
    if min_new <= 0:
        return True
    try:
        watermark = _ideation_read_watermark(conn)
    except Exception:  # noqa: BLE001 — gate must never break the sweep
        return True
    if not watermark:
        return True
    new_count = sum(1 for s in sigs if str(s.get("ingested_at") or "") > watermark)
    return new_count >= min_new


def _ideation_advance_watermark(conn: sqlite3.Connection | None, sigs: list[dict[str, Any]]) -> None:
    """Advance the watermark to the newest ``ingested_at`` in the synthesized corpus.

    Called ONLY after a successful synthesis call (even if it yielded zero
    insights — the corpus was still processed). A failed/throttled call leaves
    the watermark untouched so the next cycle retries the same material.
    """
    if conn is None or not sigs:
        return
    try:
        newest = max((str(s.get("ingested_at") or "") for s in sigs), default="")
        if newest:
            _ideation_write_watermark(conn, newest)
    except Exception:  # noqa: BLE001 — best-effort; never break the sweep
        pass


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
    max_signatures: int | None = None,
    deadline: float | None = None,
) -> list[dict[str, Any]]:
    """Run Track B ideation synthesis over the FULL recent corpus in ONE call.

    Loads up to ``UA_IDEATION_MAX_CORPUS`` (default 120) of the recent window —
    the whole universe, not a 60-cap sample — and synthesizes it in a SINGLE
    flagship call so the model can surface cross-cutting macro-trends the old
    3×20 recency batching could never see (each batch was blind to the others).

    A new-content gate (:func:`_ideation_should_run`) SKIPS the sweep when too
    few signatures are new since the last successful synthesis, collapsing the
    hourly cron's redundant re-synthesis of the same window — the dominant
    source of flagship/opus 429s. On a successful call the watermark advances; a
    failed/throttled call leaves it so the next cycle retries the same material.

    Returns ``[{narrative, value, confidence, signatures}]`` filtered to the
    confidence floor. Fails closed (a failed LLM call yields no insights, never
    a false one). ``deadline`` (monotonic seconds) skips the call when the
    convergence budget is already spent.
    """
    sigs = _load_recent_signatures(
        conn,
        source_window_hours=source_window_hours,
        limit=max_signatures or _ideation_max_corpus(),
    )
    if len(sigs) < 2:
        return []
    if not _ideation_should_run(conn, sigs):
        logger.info(
            "ideation sweep skipped: <%d new signatures since last synthesis (corpus=%d)",
            _ideation_min_new_signatures(), len(sigs),
        )
        return []
    if deadline is not None and time.monotonic() >= deadline:
        logger.info("ideation sweep skipped: convergence budget already spent")
        return []
    try:
        raw_insights = await track_b_ideation_synthesis(sigs)
    except Exception as exc:  # noqa: BLE001 — fail closed; do NOT advance the watermark
        logger.warning("ideation sweep failed (corpus=%d): %s", len(sigs), exc)
        return []
    # The corpus was processed successfully — advance the watermark even on a
    # zero-insight result, so a barren-but-new window isn't re-run every hour.
    _ideation_advance_watermark(conn, sigs)
    floor = _ideation_min_confidence()
    return [
        ins
        for ins in raw_insights
        if float(ins.get("confidence") or 0.0) >= floor and len(ins.get("signatures") or []) >= 2
    ]


def _run_ideation_sweep(
    conn: sqlite3.Connection, *, source_window_hours: int, deadline: float | None = None
) -> list[dict[str, Any]]:
    """Sync wrapper around the async ideation sweep (loop-handling like clustering)."""
    coro = _run_ideation_sweep_async(
        conn, source_window_hours=source_window_hours, deadline=deadline
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import nest_asyncio

    nest_asyncio.apply()
    return loop.run_until_complete(coro)


# ── Pre-Task-Hub editorial triage ──────────────────────────────────────
#
# Today every candidate (convergence + ideation) unconditionally creates a Task
# Hub item, and a downstream VP mission decides ship/skip — so every idea,
# including skips, becomes a Kanban card. This cheap LLM triage runs at the
# candidate-write chokepoint so ONLY "ship" candidates create a Task Hub item.
# skip/defer get a recorded verdict but NO task and NO card. Mirrors Phase 1 of
# the .claude/skills/evaluate-and-author-intel-brief/SKILL.md editorial test.

_INTEL_TRIAGE_SYSTEM = """\
You are an editorial gate for a proactive intelligence system. A candidate
pattern has been detected across recent AI/developer videos. Decide whether it
is worth turning into an operator-facing intel brief.

You are given the candidate's thesis, an optional "why it matters" value, the
per-source claims that support it, and an index of the recent briefs already
shipped/skipped in the last 48 hours.

Apply this editorial test:
- SHIP only if it is a REAL pattern genuinely supported by the claims of >=2 of
  its sources (not apophenia or over-generalization from too few/weak signals)
  AND it is NOVEL versus the recent briefs index (not a near-duplicate of
  something already shipped OR skipped in the last 48h).
- SKIP if it is generic, unsupported by the sources, or a duplicate of a recent
  brief/verdict.
- DEFER if it is promising but under-sourced (worth revisiting once more sources
  land).

Also set demo_amenable=true ONLY if the candidate implies a concrete, buildable
software/coding demo.

Return ONLY JSON:
{"verdict":"ship|skip|defer","reasoning":"one or two sentences","demo_amenable":false}
"""

_TRIAGE_ALLOWED_VERDICTS = {"ship", "skip", "defer"}

# Batched twin of _INTEL_TRIAGE_SYSTEM (PR P2). The shared recent_briefs_index is
# appended ONCE per chunk (lifted into the system prompt) instead of being repeated
# inside every per-candidate user payload — that repetition is the token cost the
# batched pre-pass amortizes. Each candidate still gets the SAME editorial bar.
_INTEL_TRIAGE_BATCH_SYSTEM_PREFIX = _INTEL_TRIAGE_SYSTEM + """

BATCH MODE: You are given MULTIPLE candidates AT ONCE in a `candidates` array,
each with a numeric `index`. Judge EACH candidate INDEPENDENTLY by the editorial
test above, applying the SAME bar to every one — do NOT become lax because there
are many candidates. The recent_briefs_index (shared novelty context for ALL
candidates) is provided ONCE below; use it to judge novelty for every candidate.
Return ONLY JSON with one verdict per index, covering EVERY index you were given:
{"verdicts":[{"index":0,"verdict":"ship|skip|defer","reasoning":"one or two sentences","demo_amenable":false}]}

recent_briefs_index (last 48h, shared across all candidates below):
"""


# ── Graded variant (PR: graded-judge redesign) ──────────────────────────────
# The categorical prompts above emit a ship/skip/defer label; at temperature=0 a
# categorical judge ships ~everything (no filter). The graded prompt asks for a
# 0-100 SCORE instead, which a code-side threshold (_intel_ship_threshold) turns
# back into a real, tunable gate. Same editorial bar, but the rubric is ANCHORED
# to spread the score band (a live probe found a categorical judge piles verdicts
# at one value). Activated only when UA_INTEL_TRIAGE_SHIP_THRESHOLD is set; the
# default (unset) keeps the categorical prompts above. Mirrors the established
# #989 cluster-judge pattern (signal_strength + _min_signal_strength).
_INTEL_TRIAGE_GRADED_SYSTEM = """\
You are an editorial gate for a proactive intelligence system. A candidate
pattern has been detected across recent AI/developer videos. SCORE how worth
turning into an operator-facing intel brief it is, from 0 to 100.

You are given the candidate's thesis, an optional "why it matters" value, the
per-source claims that support it, and an index of the recent briefs already
shipped/skipped in the last 48 hours.

Judge THREE sub-dimensions, then COMBINE them into one score:
- SOURCE STRENGTH: is it a REAL pattern genuinely supported by the SPECIFIC
  claims of >=2 INDEPENDENT sources (not apophenia / over-generalization from too
  few or weak signals)? Single-source or hand-wavy support scores low.
- NOVELTY: is it NEW versus the recent briefs index — not a near-duplicate of
  something already shipped OR skipped in the last 48h? A near-duplicate scores
  very low no matter how strong the pattern is.
- SPECIFICITY: is the thesis concrete and actionable (a specific, falsifiable
  claim an operator could act on), versus generic/obvious commentary?

Weight NOVELTY and SOURCE STRENGTH most. Anchor the combined 0-100 score:
- 85-100: strong, well-sourced, clearly novel, specific — definitely brief it.
- 70-84: solid and novel enough to ship, with a minor weakness in one dimension.
- 50-69: borderline — promising but under-sourced or only partially novel.
- 25-49: weak — generic, thinly sourced, or largely overlapping a recent brief.
- 0-24: a clear duplicate, unsupported claim, or non-pattern.

Do NOT default to a round number. If torn between two bands, pick a SPECIFIC
value inside one of them (e.g. 62 or 78 — never a flat 70 or 75).

Also set demo_amenable=true ONLY if the candidate implies a concrete, buildable
software/coding demo.

Return ONLY JSON:
{"score": <integer 0-100>, "reasoning": "one or two sentences citing the sub-dimensions", "demo_amenable": false}
"""

# Batched twin of the graded prompt — shared recent_briefs_index appended ONCE.
_INTEL_TRIAGE_GRADED_BATCH_SYSTEM_PREFIX = _INTEL_TRIAGE_GRADED_SYSTEM + """

BATCH MODE: You are given MULTIPLE candidates AT ONCE in a `candidates` array,
each with a numeric `index`. SCORE EACH candidate INDEPENDENTLY by the rubric
above, applying the SAME anchors to every one — do NOT become lax (or harsher)
because there are many candidates. The recent_briefs_index (shared novelty
context for ALL candidates) is provided ONCE below; use it to judge novelty for
every candidate. Return ONLY JSON with one verdict per index, covering EVERY
index you were given:
{"verdicts":[{"index":0,"score":<integer 0-100>,"reasoning":"one or two sentences","demo_amenable":false}]}

recent_briefs_index (last 48h, shared across all candidates below):
"""


def _intel_triage_enabled() -> bool:
    return str(os.getenv("UA_INTEL_TRIAGE_ENABLED", "1")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _intel_triage_batch_size() -> int:
    """Candidates per batched triage LLM call. Default **1 == legacy per-candidate
    path** (the batched pre-pass stays OFF until a live batched-vs-per-item A/B
    holds — operator quality bar). ``UA_INTEL_TRIAGE_BATCH_SIZE``, clamped [1, 60]
    (mirrors :func:`_convergence_judge_batch_size`)."""
    try:
        n = int(os.getenv("UA_INTEL_TRIAGE_BATCH_SIZE", "1") or "1")
    except (TypeError, ValueError):
        n = 1
    return max(1, min(60, n))


def _intel_ship_threshold() -> Optional[int]:
    """Graded-triage ship cutoff. ``UA_INTEL_TRIAGE_SHIP_THRESHOLD`` (0-100).

    UNSET (default) ⇒ triage stays on the legacy CATEGORICAL verdict path
    (ship/skip/defer emitted directly) — byte-identical to today, so the PR ships
    inert. SET ⇒ triage switches to the graded 0-100 score path and ships when
    ``score >= this``. The single operator lever that activates graded triage;
    pick against desired briefs/day (a live probe: ~70 ⇒ ~8/10 ship, ~80 ⇒
    ~1/10). A non-numeric value is treated as unset."""
    raw = (os.getenv("UA_INTEL_TRIAGE_SHIP_THRESHOLD") or "").strip()
    if not raw:
        return None
    try:
        return max(0, min(100, int(float(raw))))
    except (TypeError, ValueError):
        return None


def _intel_defer_threshold() -> Optional[int]:
    """Optional graded-triage DEFER band. ``UA_INTEL_TRIAGE_DEFER_THRESHOLD``
    (0-100). When set BELOW the ship cutoff, a score in ``[defer, ship)`` ⇒
    'defer' instead of 'skip'. Unset ⇒ no defer band (ship/skip only)."""
    raw = (os.getenv("UA_INTEL_TRIAGE_DEFER_THRESHOLD") or "").strip()
    if not raw:
        return None
    try:
        return max(0, min(100, int(float(raw))))
    except (TypeError, ValueError):
        return None


def _grade_to_triage_kind(
    score: float, ship_threshold: int, defer_threshold: Optional[int]
) -> str:
    """Map a graded 0-100 score to the triage kind vocabulary the downstream
    consumer (``write_convergence_candidate``) already handles (ship/defer/skip)."""
    if score >= ship_threshold:
        return "ship"
    if (
        defer_threshold is not None
        and defer_threshold < ship_threshold
        and score >= defer_threshold
    ):
        return "defer"
    return "skip"


def _triage_index_text(conn: sqlite3.Connection) -> str:
    """Bounded recent-briefs index injected into triage prompts.

    Shared verbatim by the per-candidate (:func:`triage_candidate`) and batched
    (:func:`_batched_triage_overrides_async`) paths so both judge novelty against
    an IDENTICAL context — load-bearing for the batched-vs-per-item A/B. The index
    grows unbounded (~100K tokens / ~400KB observed 2026-06-03); embedding it whole
    pushed the glm/ZAI triage call past the per-call timeout, so it is hard-capped
    at ``UA_INTEL_TRIAGE_INDEX_MAX_CHARS`` chars (triage only needs a recency
    SAMPLE for novelty/dedup, not the full corpus)."""
    from universal_agent.services.recent_briefs_index import read_index_or_fallback

    try:
        idx = read_index_or_fallback(conn, lookback_hours=48, limit=200)
    except Exception:  # noqa: BLE001 — defensive; helper should never raise.
        idx = ""
    try:
        _idx_budget = int(os.getenv("UA_INTEL_TRIAGE_INDEX_MAX_CHARS", "12000") or "12000")
    except (TypeError, ValueError):
        _idx_budget = 12000
    if _idx_budget > 0 and len(idx) > _idx_budget:
        idx = idx[:_idx_budget].rstrip() + "\n…[index truncated for triage]"
    return idx


def _compact_triage_sources(signatures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-source projection sent to the triage LLM. Shared by the per-candidate
    and batched paths so each candidate's evidence is byte-identical across the
    A/B."""
    return [
        {
            "channel_name": s.get("channel_name") or s.get("channel_id") or "",
            "video_title": s.get("video_title") or "",
            "key_claims": (s.get("key_claims") or [])[:6],
        }
        for s in (signatures or [])
    ]


def _candidate_id_for_signatures(signatures: list[dict[str, Any]]) -> Optional[str]:
    """Deterministic ``cand_<sha256(sorted_video_ids)[:16]>`` for a signature set,
    or ``None`` when no video_id is present. The single source of truth for the
    candidate id — used by both the batched triage pre-pass (to key overrides) and
    :func:`write_convergence_candidate` (to key the row) so they never drift."""
    video_ids = sorted({
        str(item.get("video_id") or "").strip()
        for item in (signatures or [])
        if str(item.get("video_id") or "").strip()
    })
    if not video_ids:
        return None
    seed = "|".join(video_ids)
    return f"cand_{hashlib.sha256(seed.encode()).hexdigest()[:16]}"


def _run_triage_llm(
    *,
    system: str,
    user: str,
    max_tokens: int = 400,
    model: str | None = None,
    temperature: float | None = None,
) -> str:
    """Sync wrapper around the async ``_call_llm`` seam.

    Kept thin and separate so tests can monkeypatch
    ``proactive_convergence._call_llm`` with either a sync or async callable.
    Mirrors the loop-handling pattern used by the clustering/ideation wrappers.
    ``temperature`` (default None) forwards to ``_call_llm`` for graded-gate
    determinism — None leaves the call byte-unchanged.
    """
    result = _call_llm(
        system=system, user=user, max_tokens=max_tokens, model=model, temperature=temperature
    )
    if not asyncio.iscoroutine(result):
        return result
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(result)
    import nest_asyncio

    nest_asyncio.apply()
    return loop.run_until_complete(result)


def triage_candidate(
    conn: sqlite3.Connection,
    *,
    candidate_kind: str,
    thesis: str,
    value: str,
    signatures: list[dict[str, Any]],
) -> dict[str, Any]:
    """Cheap editorial validation BEFORE Task Hub.

    Returns ``{"kind": "ship"|"skip"|"defer"|"retry", "reasoning": str,
    "demo_amenable": bool, "model": str}``. ``'retry'`` means the LLM call
    failed/parsed badly — the caller must NOT finalize and NOT create a task
    (it will be re-tried on the next sweep run because verdict='' is not final).
    """
    idx = _triage_index_text(conn)
    compact_sources = _compact_triage_sources(signatures)
    user = json.dumps(
        {
            "candidate_kind": str(candidate_kind or "convergence"),
            "thesis": str(thesis or ""),
            "value": str(value or ""),
            "sources": compact_sources,
            "recent_briefs_index": idx,
        },
        ensure_ascii=True,
    )

    from universal_agent.utils.model_resolution import resolve_haiku

    # Triage is deliberately cheap (Haiku tier); override with UA_INTEL_TRIAGE_MODEL.
    model = os.getenv("UA_INTEL_TRIAGE_MODEL", "").strip() or resolve_haiku()
    ship_threshold = _intel_ship_threshold()
    graded = ship_threshold is not None
    system_prompt = _INTEL_TRIAGE_GRADED_SYSTEM if graded else _INTEL_TRIAGE_SYSTEM
    temperature = _resolve_judge_temperature("UA_INTEL_TRIAGE_TEMPERATURE")
    _retry = {"kind": "retry", "reasoning": "triage unavailable", "demo_amenable": False, "model": model}
    try:
        raw = _run_triage_llm(
            system=system_prompt, user=user, max_tokens=400, model=model, temperature=temperature
        )
        parsed = _parse_json_response(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("intel triage LLM failed: %s", exc)
        return dict(_retry)

    if not isinstance(parsed, dict):
        return dict(_retry)

    if graded:
        # Graded 0-100 score → code-side threshold. A missing/garbled score is an
        # un-decidable verdict → fail-closed 'retry' (no task, re-tried next sweep),
        # identical to an out-of-vocab categorical verdict.
        score = _coerce_score(parsed.get("score"))
        if score is None:
            return dict(_retry)
        return {
            "kind": _grade_to_triage_kind(score, ship_threshold, _intel_defer_threshold()),
            "reasoning": str(parsed.get("reasoning") or "").strip(),
            "demo_amenable": bool(parsed.get("demo_amenable")),
            "model": model,
            "score": score,
        }

    verdict = str(parsed.get("verdict") or "").strip().lower()
    if verdict not in _TRIAGE_ALLOWED_VERDICTS:
        return dict(_retry)
    return {
        "kind": verdict,
        "reasoning": str(parsed.get("reasoning") or "").strip(),
        "demo_amenable": bool(parsed.get("demo_amenable")),
        "model": model,
    }


async def _batched_triage_overrides_async(
    conn: sqlite3.Connection,
    specs: list[dict[str, Any]],
    *,
    idx_text: str,
    deadline: float | None,
    stats: dict | None,
) -> dict[str, dict[str, Any]]:
    """Sweep-level BATCHED editorial triage. Returns ``{candidate_id: triage_dict}``.

    The direct twin of the per-candidate :func:`triage_candidate`, collapsed onto
    the shared :func:`batched_judge` helper (the same pattern PR #989 proved on the
    cluster judge). One structured-output call judges up to
    ``UA_INTEL_TRIAGE_BATCH_SIZE`` candidates, and the shared
    ``recent_briefs_index`` is lifted into the system prompt ONCE per chunk instead
    of repeated per candidate — that repetition is the token win.

    Each returned ``triage_dict`` has the SAME shape ``triage_candidate`` returns
    (``{kind, reasoning, demo_amenable, model}``) so it can be handed to
    :func:`write_convergence_candidate` as a ``triage_override`` with identical
    downstream handling (ship→queue / skip,defer→record / retry→verdict='').

    Fail-closed is IDENTICAL to the per-candidate path: any chunk failure, missing
    verdict, or out-of-vocab verdict maps that candidate to ``kind='retry'`` (no
    task, verdict='', re-tried next idempotent sweep). A Fair-Usage signal trips
    ``batched_judge``'s one-shot breaker (remaining candidates stay 'retry').
    Only UN-finalized candidates enter the batch — the exact set the per-candidate
    path would triage (a finalized candidate short-circuits inside
    :func:`write_convergence_candidate` regardless)."""
    from universal_agent.services.batched_judge import batched_judge
    from universal_agent.utils.model_resolution import resolve_haiku

    final_verdicts = {"ship", "skip", "defer", "error"}
    eligible: list[dict[str, Any]] = []
    for spec in specs:
        cid = _candidate_id_for_signatures(spec.get("signatures") or [])
        if not cid:
            continue
        existing = _get_convergence_candidate(conn, cid)
        if existing and str(existing.get("verdict") or "").strip() in final_verdicts:
            continue
        eligible.append({**spec, "candidate_id": cid})
    if not eligible:
        return {}

    # Triage is deliberately cheap (Haiku tier); override with UA_INTEL_TRIAGE_MODEL
    # — identical resolution to triage_candidate.
    model = os.getenv("UA_INTEL_TRIAGE_MODEL", "").strip() or resolve_haiku()
    # Graded vs categorical — the SAME switch as the per-candidate triage_candidate.
    ship_threshold = _intel_ship_threshold()
    graded = ship_threshold is not None
    defer_threshold = _intel_defer_threshold()
    temperature = _resolve_judge_temperature("UA_INTEL_TRIAGE_TEMPERATURE")
    prefix = (
        _INTEL_TRIAGE_GRADED_BATCH_SYSTEM_PREFIX if graded else _INTEL_TRIAGE_BATCH_SYSTEM_PREFIX
    )
    system = prefix + (idx_text or "(none)")

    def build_prompt(chunk: list[dict[str, Any]]) -> str:
        return json.dumps(
            {
                "candidates": [
                    {
                        "index": i,
                        "candidate_kind": str(spec.get("candidate_kind") or "convergence"),
                        "thesis": str(spec.get("thesis") or ""),
                        "value": str(spec.get("value") or ""),
                        "sources": _compact_triage_sources(spec.get("signatures") or []),
                    }
                    for i, spec in enumerate(chunk)
                ]
            },
            ensure_ascii=True,
        )

    def parse(item: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
        if graded:
            # Graded score → threshold. A missing/garbled score is a per-ITEM clean
            # miss → this candidate stays fail-closed ('retry'), rest unaffected.
            score = _coerce_score(verdict.get("score"))
            if score is None:
                raise ValueError(f"missing/invalid graded triage score: {verdict.get('score')!r}")
            return {
                "kind": _grade_to_triage_kind(score, ship_threshold, defer_threshold),
                "reasoning": str(verdict.get("reasoning") or "").strip(),
                "demo_amenable": bool(verdict.get("demo_amenable")),
                "model": model,
                "score": score,
            }
        v = str(verdict.get("verdict") or "").strip().lower()
        if v not in _TRIAGE_ALLOWED_VERDICTS:
            # Out-of-vocab → per-ITEM clean miss → this candidate stays fail-closed
            # (its seeded 'retry' value), the rest of the chunk is unaffected.
            raise ValueError(f"out-of-vocab triage verdict: {v!r}")
        return {
            "kind": v,
            "reasoning": str(verdict.get("reasoning") or "").strip(),
            "demo_amenable": bool(verdict.get("demo_amenable")),
            "model": model,
        }

    fail_closed = {
        "kind": "retry",
        "reasoning": "triage unavailable (batch)",
        "demo_amenable": False,
        "model": model,
    }

    model_overrides: dict[str, Any] = {"model": model}
    if temperature is not None:
        model_overrides["temperature"] = temperature

    results = await batched_judge(
        eligible,
        build_prompt=build_prompt,
        parse=parse,
        fail_closed=fail_closed,
        system=system,
        batch_size=_intel_triage_batch_size(),
        model_overrides=model_overrides,
        deadline=deadline,
        stats=stats,
    )
    return {spec["candidate_id"]: res.value for spec, res in zip(eligible, results)}


def _run_batched_triage(
    conn: sqlite3.Connection,
    specs: list[dict[str, Any]],
    *,
    idx_text: str,
    deadline: float | None = None,
    stats: dict | None = None,
) -> dict[str, dict[str, Any]]:
    """Sync wrapper around :func:`_batched_triage_overrides_async` (mirrors the
    loop-handling pattern used by :func:`_detect_clusters_llm` and the other sync
    wrappers in this module)."""
    coro = _batched_triage_overrides_async(
        conn, specs, idx_text=idx_text, deadline=deadline, stats=stats
    )
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
    triage_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Upsert a ``convergence_candidates`` row and (idempotently) queue Atlas.

    ``triage_override`` (optional) is a pre-computed triage verdict (the shape
    :func:`triage_candidate` returns: ``{kind, reasoning, demo_amenable, model}``).
    When provided for a non-finalized candidate, it is used in place of an inline
    per-candidate :func:`triage_candidate` call — this is how the sweep-level
    BATCHED triage pre-pass (:func:`_run_batched_triage`) feeds its verdicts in
    while every downstream invariant (finalized short-circuit, in-flight mission
    dedup, the row UPSERT, ``_newly_queued`` accounting, metadata.triage) is
    preserved unchanged. ``None`` ⇒ legacy inline triage.

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
    candidate_id = _candidate_id_for_signatures(signatures)  # single source of truth

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
        labels = [task_hub.TASK_LABEL_AGENT_READY, "ideation", "atlas", "candidate", "insight"]
    else:
        title = f"ATLAS evaluate convergence candidate: {headline}"
        labels = [task_hub.TASK_LABEL_AGENT_READY, "convergence", "atlas", "candidate"]

    # ── Pre-Task-Hub editorial triage ──────────────────────────────
    # Decide whether this candidate is worth a Task Hub item BEFORE we create
    # one. With triage disabled we preserve the legacy behavior exactly (always
    # queue, verdict='') so the flag is a clean rollback.
    triage: dict[str, Any] = {}
    row_verdict = ""
    row_verdict_reasoning = ""
    row_evaluated_at = ""
    persist_task_id = ""
    task: dict[str, Any] = {}
    newly_queued = False

    # Durable double-author backstop: if a VP mission for THIS candidate is
    # already in flight (queued/running), never (re)queue a second one. Even a
    # future false-orphan of the mirror row (the bug PR2 also fixes in the
    # reconciler) cannot then spawn a duplicate ATLAS authoring run. Safe on
    # legacy/test DBs without a vp_missions table (helper returns None).
    inflight_mission_id = _inflight_vp_mission_for_candidate(
        conn, candidate_id=candidate_id, task_id=task_id
    )

    if not _intel_triage_enabled():
        # Legacy path: always queue a task, persist row with verdict=''.
        if inflight_mission_id:
            logger.info(
                "Skipping convergence re-queue for candidate %s (task %s): "
                "in-flight VP mission exists (mission_id=%s)",
                candidate_id, task_id, inflight_mission_id,
            )
            persist_task_id = task_id
            newly_queued = False
        else:
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
            persist_task_id = str(task.get("task_id") or task_id)
            newly_queued = True
    else:
        # Use the pre-computed batched verdict when the sweep ran a batched triage
        # pre-pass for this candidate; otherwise fall back to the inline
        # per-candidate call (legacy path / single-candidate callers).
        if triage_override is not None:
            triage = dict(triage_override)
        else:
            triage = triage_candidate(
                conn,
                candidate_kind=candidate_kind,
                thesis=thesis,
                value=value,
                signatures=signatures,
            )
        v = str(triage.get("kind") or "retry")
        if v == "ship":
            # Worth a card — queue the Task Hub item exactly as before. The
            # deterministic task_id keeps a re-queue on a later run idempotent.
            metadata_payload["triage"] = {
                "kind": v,
                "reasoning": triage.get("reasoning", ""),
                "demo_amenable": bool(triage.get("demo_amenable")),
                "model": triage.get("model", ""),
            }
            # Graded mode carries a 0-100 score; persist it as provenance for
            # threshold tuning. Categorical mode has no score → key omitted (the
            # metadata stays byte-identical to the pre-graded behavior).
            if triage.get("score") is not None:
                metadata_payload["triage"]["score"] = triage["score"]
            if inflight_mission_id:
                logger.info(
                    "Skipping convergence re-queue for candidate %s (task %s): "
                    "in-flight VP mission exists (mission_id=%s)",
                    candidate_id, task_id, inflight_mission_id,
                )
                persist_task_id = task_id
                newly_queued = False
            else:
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
                persist_task_id = str(task.get("task_id") or task_id)
                newly_queued = True
            # verdict stays '' so the downstream mission/skill still finalizes it.
        elif v in ("skip", "defer"):
            # Recorded verdict, NO task, NO card.
            row_verdict = v
            row_verdict_reasoning = str(triage.get("reasoning") or "")
            row_evaluated_at = _now_iso()
            newly_queued = False
        else:
            # retry: triage unavailable — persist with verdict='' and NO task so
            # it is re-tried on the next sweep run (verdict='' is not final).
            newly_queued = False

    # Persist / refresh the candidate row.
    now = _now_iso()
    created_at = (existing or {}).get("created_at") or now
    row_metadata = {
        "preferred_vp": "vp.general.primary",
        "headline": headline,
        "candidate_kind": "ideation" if is_ideation else "convergence",
        "thesis": thesis,
        "value": value,
        "signal_strength": float(signal_strength or 0.0),
        "source_window_hours": int(source_window_hours),
        "task_status": str(task.get("status") or ""),
    }
    if triage:
        row_metadata["triage"] = {
            "kind": triage.get("kind", ""),
            "reasoning": triage.get("reasoning", ""),
            "demo_amenable": bool(triage.get("demo_amenable")),
            "model": triage.get("model", ""),
        }
        if triage.get("score") is not None:
            row_metadata["triage"]["score"] = triage["score"]
    conn.execute(
        """
        INSERT INTO convergence_candidates (
            candidate_id, video_ids_json, channel_names_json, channel_count,
            primary_topics_json, signatures_json, task_id, verdict,
            verdict_reasoning, artifact_id, detected_at, evaluated_at,
            created_at, updated_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?)
        ON CONFLICT(candidate_id) DO UPDATE SET
            video_ids_json=excluded.video_ids_json,
            channel_names_json=excluded.channel_names_json,
            channel_count=excluded.channel_count,
            primary_topics_json=excluded.primary_topics_json,
            signatures_json=excluded.signatures_json,
            task_id=excluded.task_id,
            verdict=excluded.verdict,
            verdict_reasoning=excluded.verdict_reasoning,
            evaluated_at=excluded.evaluated_at,
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
            persist_task_id,
            row_verdict,
            row_verdict_reasoning,
            now,
            row_evaluated_at,
            created_at,
            now,
            _json_dumps(row_metadata),
        ),
    )
    conn.commit()

    row = _get_convergence_candidate(conn, candidate_id) or {}
    row["_newly_queued"] = newly_queued
    row["_task"] = task
    return row


def _inflight_vp_mission_for_candidate(
    conn: sqlite3.Connection,
    *,
    candidate_id: str,
    task_id: str,
) -> Optional[str]:
    """Return the mission_id of an in-flight VP mission for this candidate.

    Durable backstop against double-authoring: even a future false-orphan
    (the bug PR2 also fixes in the reconciler) must not be able to spawn a
    second ATLAS mission for the same convergence candidate. A re-queue is
    skipped when a ``vp_missions`` row already exists for THIS candidate in
    status ``queued`` or ``running``.

    Candidate -> mission linkage (confirmed in vp/dispatcher._build_payload
    and tools/vp_orchestration):
      * the Task Hub ``task_id`` is ``convergence-candidate:<hash>`` and the
        mission ``payload_json.task_id`` (lifted from
        ``metadata.linked_task_id``) equals it;
      * the dispatch ``idempotency_key`` is ``task-<task_id>`` so it CONTAINS
        the task_id (and thus the candidate hash);
      * ``source_ref`` on the Task Hub item is the ``cand_``-prefixed
        candidate_id.

    Returns ``None`` (caller proceeds to queue) when no in-flight mission is
    found or the ``vp_missions`` table is absent (legacy / test DBs) — the
    existence check mirrors the pattern used in task_hub.reconcile_task_lifecycle.
    """
    candidate_id = str(candidate_id or "").strip()
    task_id = str(task_id or "").strip()
    try:
        tables_row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vp_missions' LIMIT 1"
        ).fetchone()
    except Exception:
        return None
    if not tables_row:
        return None
    try:
        rows = conn.execute(
            """
            SELECT mission_id, payload_json
            FROM vp_missions
            WHERE status IN ('queued', 'running')
            """
        ).fetchall()
    except Exception:
        return None
    # The candidate hash without the cand_ prefix appears inside both the
    # task_id and the idempotency_key; match on it as a robust fallback.
    candidate_hash = candidate_id.removeprefix("cand_")
    for row in rows:
        payload = _json_loads_obj(row["payload_json"])
        payload_task_id = str(payload.get("task_id") or "").strip()
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        matches = (
            (task_id and payload_task_id == task_id)
            or (task_id and task_id in idempotency_key)
            or (candidate_hash and candidate_hash in idempotency_key)
        )
        if matches:
            return str(row["mission_id"] or "").strip()
    return None


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
        "VALIDATION ALREADY DONE: this task exists only because the pre-Task-Hub",
        "triage already returned verdict='ship'. Check task metadata.triage — if",
        "metadata.triage.kind=='ship', DO NOT re-run the ship/skip/defer rubric;",
        "the verdict is 'ship', just AUTHOR the brief (see the skill's Phase 0.5).",
        "Carry metadata.triage.demo_amenable into the artifact metadata.",
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
        from universal_agent.utils.model_resolution import resolve_haiku

        # Short structured signature extraction — Haiku tier (glm-4.5-air) by
        # default; override with UA_SIGNATURE_MODEL.
        sig_model = (os.getenv("UA_SIGNATURE_MODEL") or "").strip() or resolve_haiku()
        raw = await _call_llm(system=_SIGNATURE_SYSTEM, user=user, max_tokens=900, model=sig_model)
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


async def track_b_ideation_synthesis(
    batch: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Track B: LLM ideation / synthesis over the recent corpus in one call.

    ``batch`` is the whole recent corpus (capped at ``UA_IDEATION_MAX_CORPUS``),
    not a 20-item slice — the model reasons across the full set in a single call.
    Model is the flagship/opus tier by default; ``UA_IDEATION_MODEL`` overrides.
    """
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
        for item in batch[: _ideation_max_corpus()]
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

        raw = await _call_llm(
            system=_IDEATION_SYSTEM,
            user=user,
            model=_ideation_model(),
            max_tokens=_ideation_max_tokens(),
        )
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


def _preference_context(conn: sqlite3.Connection, *, task_type: str, topic_tags: list[str]) -> str:
    """Fetch preference delegation context, returning empty string on failure."""
    try:
        from universal_agent.services.proactive_preferences import (
            get_delegation_context,
        )

        return get_delegation_context(conn, task_type=task_type, topic_tags=topic_tags)
    except Exception:
        return ""


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
