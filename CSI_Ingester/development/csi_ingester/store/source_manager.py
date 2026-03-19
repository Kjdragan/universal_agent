"""Unified source management for all CSI data sources.

Manages YouTube channels, Reddit subreddits, and Threads search terms
in SQLite with quality scoring, tier management, and seed-from-JSON
support.  JSON files (channels_watchlist.json, reddit_watchlist.json)
remain as version-controlled seed data; runtime state lives in the DB.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Domain classification rules for YouTube channels ────────────────────
# First match wins. More specific patterns before generic ones.
_DOMAIN_RULES: list[tuple[str, list[str]]] = [
    ("ai_coding", [
        r"cod(?:e|ing)", r"cursor", r"windsurf", r"copilot",
        r"agent", r"autogpt", r"langchain", r"mcp",
        r"dev(?:elop|s|ops)", r"engineer", r"prompt",
        r"llms?\s*for\s*dev", r"claude\s*code", r"oriented\s*dev",
        r"automat", r"builder", r"build",
        r"n8n", r"zapier",
    ]),
    ("ai_models", [
        r"\bai\b", r"artificial\s*intell", r"machine\s*learn",
        r"deep\s*learn", r"neural", r"llm", r"gpt",
        r"claude", r"anthropic", r"openai", r"gemini",
        r"llama", r"mistral", r"singularity",
        r"diffusion", r"transformer", r"latent\s*space",
        r"ml\b", r"nlp\b",
    ]),
    ("ai_applications", [
        r"chatgpt", r"notebook\s*lm", r"stable\s*diffusion",
        r"midjourney", r"dall-?e", r"whisper",
    ]),
    ("ai_business", [
        r"ai\s*(?:news|strategy|advantage|revolution|grid|war)",
        r"million", r"startup", r"business",
        r"seo", r"marketing",
    ]),
    ("geopolitics", [
        r"geopoliti", r"internation", r"foreign\s*(?:affairs|policy)",
    ]),
    ("conflict", [
        r"\bwar\b", r"military", r"defense", r"conflict",
    ]),
    ("economics", [
        r"econom", r"market", r"financ", r"crypto", r"invest",
    ]),
    ("technology", [
        r"tech", r"python", r"javascript", r"cloud",
        r"devops", r"server", r"hack", r"cyber", r"security",
        r"google", r"cloudflare", r"supabase",
        r"raspberry", r"linux", r"docker",
    ]),
]

_NON_SIGNAL_PATTERNS = [
    r"chef\b", r"cook", r"recipe", r"food",
    r"fitness", r"workout", r"yoga",
    r"lagerstrom", r"delauer", r"adam\s*conover",
]


def _classify_channel_name(name: str) -> str:
    """Return domain slug for a YouTube channel name."""
    lower = name.lower().strip()
    for pat in _NON_SIGNAL_PATTERNS:
        if re.search(pat, lower):
            return "other_signal"
    for domain, patterns in _DOMAIN_RULES:
        for pat in patterns:
            if re.search(pat, lower):
                return domain
    return "other_signal"


def _tier_from_video_count(video_count: int) -> int:
    """Assign initial tier based on user watch frequency."""
    if video_count >= 5:
        return 1
    elif video_count >= 3:
        return 2
    return 3


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Seed from JSON ─────────────────────────────────────────────────────

def seed_youtube_channels(conn: sqlite3.Connection, json_path: Path) -> int:
    """Upsert YouTube channels from a seed JSON file into the DB.

    New channels are inserted with auto-classified domain and tier.
    Existing channels keep their runtime state (quality_score, tier, etc.).
    Returns count of newly inserted channels.
    """
    if not json_path.exists():
        logger.warning("YouTube seed file not found: %s", json_path)
        return 0

    with open(json_path) as f:
        data = json.load(f)

    channels = data.get("channels", [])
    inserted = 0

    for ch in channels:
        channel_id = ch.get("channel_id", "")
        if not channel_id:
            continue

        name = ch.get("channel_name", "")
        video_count = int(ch.get("video_count", 1))

        # Use pre-tagged domain if present, otherwise auto-classify
        domain = ch.get("domain") or _classify_channel_name(name)
        tier = ch.get("tier") or _tier_from_video_count(video_count)

        try:
            conn.execute(
                """INSERT INTO youtube_channels
                   (channel_id, channel_name, rss_feed_url, youtube_url,
                    domain, tier, seed_video_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(channel_id) DO UPDATE SET
                       channel_name = excluded.channel_name,
                       rss_feed_url = excluded.rss_feed_url,
                       youtube_url  = excluded.youtube_url,
                       seed_video_count = excluded.seed_video_count
                """,
                (
                    channel_id,
                    name,
                    ch.get("rss_feed_url", ""),
                    ch.get("youtube_url", ""),
                    domain,
                    tier,
                    video_count,
                ),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            pass

    conn.commit()
    logger.info("Seeded %d YouTube channels from %s", inserted, json_path.name)
    return inserted


def seed_reddit_sources(conn: sqlite3.Connection, json_path: Path) -> int:
    """Upsert Reddit subreddits from a seed JSON file into the DB."""
    if not json_path.exists():
        logger.warning("Reddit seed file not found: %s", json_path)
        return 0

    with open(json_path) as f:
        data = json.load(f)

    subs = data.get("subreddits", [])
    inserted = 0

    for sub in subs:
        name = sub.get("name", "")
        if not name:
            continue

        domain = sub.get("domain", "other_signal")
        tier = sub.get("tier", 2)
        note = sub.get("note", "")

        try:
            conn.execute(
                """INSERT INTO reddit_sources
                   (subreddit, domain, tier, note)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(subreddit) DO UPDATE SET
                       note = COALESCE(NULLIF(excluded.note, ''), reddit_sources.note)
                """,
                (name, domain, tier, note),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            pass

    conn.commit()
    logger.info("Seeded %d Reddit sources from %s", inserted, json_path.name)
    return inserted


def seed_threads_terms(conn: sqlite3.Connection, query_packs: list[dict[str, Any]]) -> int:
    """Upsert Threads search terms from config query_packs into the DB."""
    inserted = 0
    for pack in query_packs:
        pack_name = pack.get("name", "")
        domain = pack.get("domain", "other_signal")
        terms = pack.get("terms", [])
        for term in terms:
            term_str = str(term).strip()
            if not term_str:
                continue
            try:
                conn.execute(
                    """INSERT INTO threads_search_terms
                       (term, query_pack, domain)
                       VALUES (?, ?, ?)
                       ON CONFLICT(term) DO UPDATE SET
                           query_pack = excluded.query_pack,
                           domain = excluded.domain
                    """,
                    (term_str, pack_name, domain),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                pass

    conn.commit()
    logger.info("Seeded %d Threads search terms", inserted)
    return inserted


# ── Query active sources ────────────────────────────────────────────────

def get_active_youtube_channels(
    conn: sqlite3.Connection,
    *,
    domain: str | None = None,
    tier: int | None = None,
    max_tier: int | None = None,
) -> list[dict[str, Any]]:
    """Return active YouTube channels, optionally filtered."""
    sql = "SELECT * FROM youtube_channels WHERE active = 1"
    params: list[Any] = []

    if domain:
        sql += " AND domain = ?"
        params.append(domain)
    if tier is not None:
        sql += " AND tier = ?"
        params.append(tier)
    if max_tier is not None:
        sql += " AND tier <= ?"
        params.append(max_tier)

    sql += " ORDER BY tier ASC, quality_score DESC"
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def get_active_reddit_sources(
    conn: sqlite3.Connection,
    *,
    domain: str | None = None,
) -> list[dict[str, Any]]:
    """Return active Reddit subreddits, optionally filtered."""
    sql = "SELECT * FROM reddit_sources WHERE active = 1"
    params: list[Any] = []

    if domain:
        sql += " AND domain = ?"
        params.append(domain)

    sql += " ORDER BY tier ASC, quality_score DESC"
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def get_active_threads_terms(
    conn: sqlite3.Connection,
    *,
    domain: str | None = None,
    query_pack: str | None = None,
) -> list[dict[str, Any]]:
    """Return active Threads search terms, optionally filtered."""
    sql = "SELECT * FROM threads_search_terms WHERE active = 1"
    params: list[Any] = []

    if domain:
        sql += " AND domain = ?"
        params.append(domain)
    if query_pack:
        sql += " AND query_pack = ?"
        params.append(query_pack)

    sql += " ORDER BY tier ASC, quality_score DESC"
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


# ── Quality scoring ─────────────────────────────────────────────────────

_SOURCE_TABLE_MAP = {
    "youtube": "youtube_channels",
    "reddit": "reddit_sources",
    "threads": "threads_search_terms",
}

_SOURCE_KEY_COL = {
    "youtube": "channel_id",
    "reddit": "subreddit",
    "threads": "term",
}


def record_quality_assessment(
    conn: sqlite3.Connection,
    *,
    source_type: str,
    source_key: str,
    relevance: float = 0.0,
    engagement: float = 0.0,
    novelty: float = 0.0,
    confidence: float = 0.0,
    items_count: int = 0,
    weights: dict[str, float] | None = None,
    notes: str = "",
) -> float:
    """Record a quality assessment and update the source's running score.

    Returns the computed composite score.
    """
    w = weights or {
        "relevance": 0.4,
        "engagement": 0.2,
        "novelty": 0.2,
        "confidence": 0.2,
    }

    score = (
        relevance * w.get("relevance", 0.25)
        + engagement * w.get("engagement", 0.25)
        + novelty * w.get("novelty", 0.25)
        + confidence * w.get("confidence", 0.25)
    )
    score = max(0.0, min(1.0, score))
    now = _utc_now()

    # Insert history record
    conn.execute(
        """INSERT INTO source_quality_history
           (source_type, source_key, assessed_at,
            score, items_count, relevance, engagement, novelty, confidence, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (source_type, source_key, now, score, items_count,
         relevance, engagement, novelty, confidence, notes),
    )

    # Update running score on the source table
    table = _SOURCE_TABLE_MAP.get(source_type)
    key_col = _SOURCE_KEY_COL.get(source_type)
    if table and key_col:
        conn.execute(
            f"""UPDATE {table} SET
                quality_score = ?,
                items_assessed = items_assessed + ?,
                last_assessed = ?
                WHERE {key_col} = ?""",
            (score, items_count, now, source_key),
        )

    conn.commit()
    return score


def auto_promote_demote(
    conn: sqlite3.Connection,
    *,
    source_type: str,
    tier1_min: float = 0.7,
    tier3_max: float = 0.3,
    min_assessments: int = 3,
) -> dict[str, list[str]]:
    """Auto-adjust tiers based on quality scores.

    Returns dict with 'promoted' and 'demoted' source keys.
    """
    table = _SOURCE_TABLE_MAP.get(source_type)
    key_col = _SOURCE_KEY_COL.get(source_type)
    if not table or not key_col:
        return {"promoted": [], "demoted": []}

    now = _utc_now()
    promoted: list[str] = []
    demoted: list[str] = []

    rows = conn.execute(
        f"""SELECT {key_col} as source_key, tier, quality_score, items_assessed
            FROM {table}
            WHERE active = 1 AND items_assessed >= ?""",
        (min_assessments,),
    ).fetchall()

    for row in rows:
        key = str(row["source_key"])
        current_tier = int(row["tier"])
        score = float(row["quality_score"])

        new_tier = current_tier
        if score >= tier1_min and current_tier > 1:
            new_tier = 1
            promoted.append(key)
        elif score < tier3_max and current_tier < 3:
            new_tier = 3
            demoted.append(key)
            conn.execute(
                f"UPDATE {table} SET tier = ?, demoted_at = ? WHERE {key_col} = ?",
                (new_tier, now, key),
            )
            continue

        if new_tier != current_tier:
            conn.execute(
                f"UPDATE {table} SET tier = ? WHERE {key_col} = ?",
                (new_tier, key),
            )

    conn.commit()

    if promoted:
        logger.info("Promoted %d %s sources to Tier 1", len(promoted), source_type)
    if demoted:
        logger.info("Demoted %d %s sources to Tier 3", len(demoted), source_type)

    return {"promoted": promoted, "demoted": demoted}


# ── Reporting ───────────────────────────────────────────────────────────

def get_source_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return a summary of all source counts by type, domain, and tier."""
    summary: dict[str, Any] = {}

    for source_type, table in _SOURCE_TABLE_MAP.items():
        rows = conn.execute(
            f"""SELECT domain, tier, COUNT(*) as cnt, AVG(quality_score) as avg_score
                FROM {table}
                WHERE active = 1
                GROUP BY domain, tier
                ORDER BY domain, tier""",
        ).fetchall()

        type_summary: dict[str, Any] = {"total": 0, "by_domain": {}}
        for row in rows:
            domain = str(row["domain"])
            tier = int(row["tier"])
            count = int(row["cnt"])
            avg = round(float(row["avg_score"]), 3)

            type_summary["total"] += count
            if domain not in type_summary["by_domain"]:
                type_summary["by_domain"][domain] = {}
            type_summary["by_domain"][domain][f"tier_{tier}"] = {
                "count": count,
                "avg_score": avg,
            }

        summary[source_type] = type_summary

    return summary
