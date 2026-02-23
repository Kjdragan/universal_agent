"""Adaptive category taxonomy for RSS video analysis."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from csi_ingester.store import source_state

CATEGORY_STATE_KEY = "rss_adaptive_category_taxonomy_v1"

CORE_ORDER: tuple[str, ...] = ("ai", "political", "war", "other_interest")
CORE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "ai": {
        "label": "AI",
        "keywords": [
            "ai",
            "artificial intelligence",
            "machine learning",
            "llm",
            "gpt",
            "openai",
            "anthropic",
            "claude",
            "gemini",
            "model",
            "prompt",
            "agent",
            "agents",
            "automation",
            "neural",
            "deep learning",
        ],
    },
    "political": {
        "label": "Political",
        "keywords": [
            "politic",
            "election",
            "campaign",
            "policy",
            "government",
            "senate",
            "congress",
            "parliament",
            "white house",
            "prime minister",
            "president",
            "democrat",
            "republican",
            "left wing",
            "right wing",
            "geopolitic",
        ],
    },
    "war": {
        "label": "War",
        "keywords": [
            "war",
            "battle",
            "military",
            "defense",
            "airstrike",
            "missile",
            "drone strike",
            "troops",
            "frontline",
            "invasion",
            "conflict",
            "ceasefire",
            "ukraine",
            "gaza",
            "israel",
            "iran",
            "russia",
            "china sea",
            "nato",
        ],
    },
    "other_interest": {
        "label": "Other Interest",
        "keywords": [],
    },
}

CATEGORY_ALIASES: dict[str, str] = {
    "non_ai": "other_interest",
    "non-ai": "other_interest",
    "non ai": "other_interest",
    "unknown": "other_interest",
    "uncategorized": "other_interest",
    "uncategorised": "other_interest",
    "other": "other_interest",
    "misc": "other_interest",
    "general": "other_interest",
    "politics": "political",
    "geopolitics": "political",
    "warfare": "war",
}

GENERIC_TOPIC_WORDS = {
    "video",
    "youtube",
    "today",
    "update",
    "latest",
    "live",
    "news",
    "podcast",
    "episode",
    "official",
    "channel",
    "talk",
    "show",
    "watch",
    "review",
    "classification",
    "classified",
    "general_interest",
    "general_news",
    "ai_tools",
    "public_policy",
    "security",
    "geopolitics",
    "metadata",
    "transcript",
    "summary",
    "unavailable",
    "missing",
    "only",
    "general",
    "interest",
    "topic",
    "signal",
    "signals",
    "trend",
    "trends",
    "creator",
    "creators",
    "content",
    "category",
    "categories",
    "analysis",
    "analyze",
    "detected",
    "detector",
    "tracking",
    "monitoring",
    "watchlist",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(value: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    if not clean:
        return ""
    clean = re.sub(r"_+", "_", clean)
    return clean[:40]


def _normalize_key(value: str) -> str:
    key = value.strip().lower().replace("-", "_")
    key = re.sub(r"\s+", "_", key)
    return CATEGORY_ALIASES.get(key, key)


def format_category_label(slug: str) -> str:
    if slug in CORE_DEFINITIONS:
        return str(CORE_DEFINITIONS[slug]["label"])
    parts = [chunk for chunk in slug.split("_") if chunk]
    if not parts:
        return "Other Interest"
    return " ".join(part.capitalize() for part in parts)


def _extract_topic_candidates(
    *,
    title: str,
    channel_name: str,
    summary_text: str,
    transcript_text: str,
    themes: list[str] | tuple[str, ...],
) -> list[str]:
    candidates: list[str] = []

    for raw in themes:
        val = _slugify(str(raw))
        if val and len(val) >= 3 and val not in GENERIC_TOPIC_WORDS:
            candidates.append(val)

    source = " ".join(
        [
            title or "",
            summary_text or "",
            (transcript_text or "")[:1600],
        ]
    ).lower()
    for token in re.findall(r"[a-z][a-z0-9]{3,22}", source):
        if token in GENERIC_TOPIC_WORDS:
            continue
        candidates.append(token)

    ranked: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        slug = _slugify(item)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        ranked.append(slug)
        if len(ranked) >= 24:
            break
    return ranked


def _new_state(max_categories: int) -> dict[str, Any]:
    now = _utc_now_iso()
    categories: dict[str, dict[str, Any]] = {}
    for slug in CORE_ORDER:
        core = CORE_DEFINITIONS[slug]
        categories[slug] = {
            "slug": slug,
            "label": str(core["label"]),
            "kind": "core",
            "keywords": [str(item).lower() for item in core["keywords"]],
            "count": 0,
            "created_at": now,
            "updated_at": now,
        }
    return {
        "version": 1,
        "max_categories": max(4, int(max_categories)),
        "new_category_min_topic_hits": 8,
        "categories": categories,
        "other_interest_topic_counts": {},
        "total_classified": 0,
        "retired_categories": [],
        "updated_at": now,
    }


def reset_taxonomy_state(conn: sqlite3.Connection, *, max_categories: int = 10) -> dict[str, Any]:
    state = _new_state(max_categories)
    source_state.set_state(conn, CATEGORY_STATE_KEY, state)
    return state


def ensure_taxonomy_state(conn: sqlite3.Connection, *, max_categories: int = 10) -> dict[str, Any]:
    state = source_state.get_state(conn, CATEGORY_STATE_KEY)
    if not isinstance(state, dict):
        state = _new_state(max_categories)
        source_state.set_state(conn, CATEGORY_STATE_KEY, state)
        return state

    categories = state.get("categories")
    if not isinstance(categories, dict):
        state = _new_state(max_categories)
        source_state.set_state(conn, CATEGORY_STATE_KEY, state)
        return state

    changed = False
    now = _utc_now_iso()
    for slug in CORE_ORDER:
        if slug not in categories or not isinstance(categories.get(slug), dict):
            core = CORE_DEFINITIONS[slug]
            categories[slug] = {
                "slug": slug,
                "label": str(core["label"]),
                "kind": "core",
                "keywords": [str(item).lower() for item in core["keywords"]],
                "count": 0,
                "created_at": now,
                "updated_at": now,
            }
            changed = True

    state["max_categories"] = max(4, int(state.get("max_categories") or max_categories))
    state["new_category_min_topic_hits"] = max(5, int(state.get("new_category_min_topic_hits") or 8))
    if not isinstance(state.get("other_interest_topic_counts"), dict):
        state["other_interest_topic_counts"] = {}
        changed = True
    if not isinstance(state.get("retired_categories"), list):
        state["retired_categories"] = []
        changed = True
    if changed:
        state["updated_at"] = now
        source_state.set_state(conn, CATEGORY_STATE_KEY, state)
    return state


def _merge_or_retire_narrowest_dynamic(state: dict[str, Any]) -> bool:
    categories = state["categories"]
    dynamic = [
        (slug, data)
        for slug, data in categories.items()
        if slug not in CORE_ORDER and isinstance(data, dict) and str(data.get("kind") or "") == "dynamic"
    ]
    if not dynamic:
        return False
    slug, data = sorted(dynamic, key=lambda item: int(item[1].get("count") or 0))[0]
    retired = {
        "slug": slug,
        "label": str(data.get("label") or format_category_label(slug)),
        "count": int(data.get("count") or 0),
        "retired_at": _utc_now_iso(),
    }
    state["retired_categories"].append(retired)
    del categories[slug]
    return True


def _score_category(blob: str, keywords: list[str]) -> int:
    score = 0
    for kw in keywords:
        phrase = str(kw).strip().lower()
        if not phrase:
            continue
        if phrase in blob:
            score += 1 if " " not in phrase else 2
    return score


def canonicalize_category(raw_value: str, *, state: dict[str, Any] | None = None) -> str:
    value = _normalize_key(str(raw_value or ""))
    if not value:
        return "other_interest"
    if value in CORE_ORDER:
        return value
    if state is not None:
        categories = state.get("categories")
        if isinstance(categories, dict) and value in categories:
            return value
    if value in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[value]
    if value == "non_ai":
        return "other_interest"
    if value == "unknown":
        return "other_interest"
    if value.startswith("other"):
        return "other_interest"
    return value


def normalize_existing_analysis_categories(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT id, category FROM rss_event_analysis").fetchall()
    changed = 0
    for row in rows:
        row_id = int(row["id"])
        raw = str(row["category"] or "")
        normalized = canonicalize_category(raw)
        if normalized != raw:
            conn.execute("UPDATE rss_event_analysis SET category = ? WHERE id = ?", (normalized, row_id))
            changed += 1
    if changed > 0:
        conn.commit()
    return changed


def classify_and_update_category(
    conn: sqlite3.Connection,
    *,
    suggested_category: str,
    title: str,
    channel_name: str,
    summary_text: str,
    transcript_text: str,
    themes: list[str] | tuple[str, ...],
    confidence: float,
    max_categories: int = 10,
) -> tuple[str, dict[str, Any]]:
    state = ensure_taxonomy_state(conn, max_categories=max_categories)
    categories = state["categories"]
    state["max_categories"] = max(4, min(20, int(max_categories)))
    now = _utc_now_iso()

    blob = " ".join(
        [
            str(title or ""),
            str(channel_name or ""),
            str(summary_text or ""),
            str(transcript_text or "")[:6000],
            " ".join(str(item) for item in themes),
        ]
    ).lower()
    candidates = _extract_topic_candidates(
        title=title,
        channel_name=channel_name,
        summary_text=summary_text,
        transcript_text=transcript_text,
        themes=list(themes),
    )

    raw_suggestion = str(suggested_category or "").strip()
    normalized_suggestion = canonicalize_category(raw_suggestion, state=state) if raw_suggestion else ""
    selected = ""
    if normalized_suggestion in categories:
        selected = normalized_suggestion

    best_slug = ""
    best_score = 0
    for slug, data in categories.items():
        if not isinstance(data, dict):
            continue
        if slug == "other_interest":
            continue
        score = _score_category(blob, [str(item).lower() for item in data.get("keywords", [])])
        if score > best_score:
            best_slug = slug
            best_score = score
    if not selected and best_slug and best_score > 0:
        selected = best_slug

    if not selected:
        selected = "other_interest"

    if selected == "other_interest":
        topic_counts = state["other_interest_topic_counts"]
        for topic in candidates[:12]:
            topic_counts[topic] = int(topic_counts.get(topic) or 0) + 1

        create_hint = _slugify(suggested_category)
        if create_hint in CATEGORY_ALIASES:
            create_hint = ""
        if create_hint in CORE_ORDER:
            create_hint = ""
        if create_hint in GENERIC_TOPIC_WORDS:
            create_hint = ""

        threshold = int(state.get("new_category_min_topic_hits") or 8)
        category_candidate = ""
        if create_hint and create_hint not in categories:
            category_candidate = create_hint
        else:
            for topic, count in sorted(topic_counts.items(), key=lambda item: int(item[1]), reverse=True):
                if int(count) < threshold:
                    break
                if topic in categories or topic in CORE_ORDER or topic in GENERIC_TOPIC_WORDS:
                    continue
                category_candidate = topic
                break

        if category_candidate and float(confidence or 0.0) >= 0.45:
            while len(categories) >= int(state["max_categories"]):
                if not _merge_or_retire_narrowest_dynamic(state):
                    break
            if len(categories) < int(state["max_categories"]):
                seed_keywords = [category_candidate]
                for topic in candidates:
                    if topic == category_candidate or topic in seed_keywords:
                        continue
                    seed_keywords.append(topic)
                    if len(seed_keywords) >= 14:
                        break
                categories[category_candidate] = {
                    "slug": category_candidate,
                    "label": format_category_label(category_candidate),
                    "kind": "dynamic",
                    "keywords": seed_keywords,
                    "count": 0,
                    "created_at": now,
                    "updated_at": now,
                }
                selected = category_candidate

    if selected not in categories:
        selected = "other_interest"

    chosen = categories[selected]
    chosen["count"] = int(chosen.get("count") or 0) + 1
    chosen["updated_at"] = now

    if str(chosen.get("kind") or "") == "dynamic":
        kws = [str(item).lower() for item in chosen.get("keywords", []) if str(item).strip()]
        for token in candidates:
            if token in kws or token in GENERIC_TOPIC_WORDS:
                continue
            kws.append(token)
            if len(kws) >= 28:
                break
        chosen["keywords"] = kws

    state["total_classified"] = int(state.get("total_classified") or 0) + 1
    state["updated_at"] = now
    source_state.set_state(conn, CATEGORY_STATE_KEY, state)
    return selected, state
