"""Adaptive category taxonomy for RSS video analysis."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from csi_ingester.store import source_state

CATEGORY_STATE_KEY = "rss_adaptive_category_taxonomy_v2"

CORE_ORDER: tuple[str, ...] = (
    "ai_models",
    "ai_coding",
    "ai_applications",
    "ai_business",
    "geopolitics",
    "conflict",
    "economics",
    "technology",
    "other_signal",
)
CORE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "ai_models": {
        "label": "AI Models & Research",
        "keywords": [
            "llm",
            "large language model",
            "transformer",
            "foundation model",
            "frontier model",
            "benchmark",
            "model release",
            "fine tuning",
            "fine-tuning",
            "finetuning",
            "rlhf",
            "pretraining",
            "pre-training",
            "neural network",
            "deep learning",
            "diffusion model",
            "multimodal",
            "vision language",
            "rag",
            "retrieval augmented",
            "context window",
            "tokenizer",
            "inference",
            "quantization",
            "gguf",
            "onnx",
            "weights",
            "parameters",
            "arxiv",
            "paper",
            "openai",
            "anthropic",
            "claude",
            "gemini",
            "llama",
            "mistral",
            "deepseek",
            "qwen",
            "phi",
            "grok",
            "agi",
            "artificial general intelligence",
            "ai safety",
            "alignment",
        ],
    },
    "ai_coding": {
        "label": "AI Coding & Dev Tools",
        "keywords": [
            "agentic coding",
            "ai coding",
            "code generation",
            "code assistant",
            "copilot",
            "cursor",
            "windsurf",
            "aider",
            "devin",
            "codegen",
            "ai agent",
            "ai agents",
            "agent framework",
            "langchain",
            "langgraph",
            "crewai",
            "autogen",
            "swarm",
            "tool use",
            "function calling",
            "mcp",
            "model context protocol",
            "vscode",
            "ide",
            "prompt engineering",
            "prompt",
            "workflow automation",
            "n8n",
            "zapier",
            "make.com",
        ],
    },
    "ai_applications": {
        "label": "AI Applications",
        "keywords": [
            "chatbot",
            "ai tool",
            "ai tools",
            "ai app",
            "ai product",
            "ai feature",
            "ai powered",
            "ai-powered",
            "image generation",
            "text to image",
            "text to video",
            "text to speech",
            "speech to text",
            "voice clone",
            "ai art",
            "stable diffusion",
            "midjourney",
            "dalle",
            "suno",
            "ai music",
            "ai video",
            "ai search",
            "perplexity",
            "notebooklm",
            "ai writing",
            "ai assistant",
            "ai tutor",
            "ai education",
        ],
    },
    "ai_business": {
        "label": "AI Business & Strategy",
        "keywords": [
            "ai startup",
            "ai funding",
            "ai investment",
            "ai valuation",
            "ai market",
            "ai revenue",
            "ai company",
            "ai acquisition",
            "ai ipo",
            "ai regulation",
            "ai policy",
            "ai governance",
            "ai ethics",
            "ai job",
            "ai hiring",
            "ai talent",
            "nvidia",
            "gpu",
            "tpu",
            "compute",
            "data center",
            "ai chip",
            "ai hardware",
            "ai infrastructure",
            "scaling law",
            "ai race",
            "ai competition",
        ],
    },
    "geopolitics": {
        "label": "Geopolitics & IR",
        "keywords": [
            "geopolitic",
            "international relations",
            "diplomacy",
            "sanctions",
            "trade war",
            "tariff",
            "embargo",
            "treaty",
            "alliance",
            "politic",
            "election",
            "government",
            "senate",
            "congress",
            "parliament",
            "prime minister",
            "president",
            "democracy",
            "authoritarian",
            "trump",
            "biden",
            "xi jinping",
            "putin",
            "white house",
            "state department",
            "united nations",
            "european union",
            "brics",
        ],
    },
    "conflict": {
        "label": "Conflict & Defense",
        "keywords": [
            "war",
            "warfare",
            "battle",
            "military",
            "defense",
            "defence",
            "airstrike",
            "missile",
            "drone strike",
            "troops",
            "frontline",
            "invasion",
            "ceasefire",
            "ukraine",
            "gaza",
            "israel",
            "iran",
            "russia",
            "china sea",
            "taiwan strait",
            "nato",
            "pentagon",
            "arms",
            "nuclear",
            "hypersonic",
            "cyber attack",
            "intelligence",
        ],
    },
    "economics": {
        "label": "Economics & Markets",
        "keywords": [
            "economy",
            "economic",
            "inflation",
            "deflation",
            "interest rate",
            "federal reserve",
            "central bank",
            "gdp",
            "recession",
            "market",
            "stock",
            "bond",
            "treasury",
            "yield curve",
            "credit",
            "private credit",
            "market structure",
            "commodit",
            "oil price",
            "gold",
            "cryptocurrency",
            "bitcoin",
            "ethereum",
            "defi",
            "fintech",
            "banking",
            "venture capital",
        ],
    },
    "technology": {
        "label": "Technology & Infra",
        "keywords": [
            "cloud",
            "aws",
            "azure",
            "gcp",
            "kubernetes",
            "docker",
            "devops",
            "open source",
            "github",
            "linux",
            "programming",
            "software",
            "api",
            "database",
            "cybersecurity",
            "zero day",
            "vulnerability",
            "blockchain",
            "web3",
            "quantum computing",
            "robotics",
            "iot",
            "5g",
            "semiconductor",
            "chip",
            "apple",
            "google",
            "microsoft",
            "meta",
            "amazon",
        ],
    },
    "other_signal": {
        "label": "Other Signal",
        "keywords": [],
    },
}

# ── Legacy backward-compatibility aliases ───────────────────────────────
# Map old category slugs and common variations to the new domain taxonomy.
CATEGORY_ALIASES: dict[str, str] = {
    # Legacy v1 core categories → new domains
    "ai": "ai_models",
    "political": "geopolitics",
    "war": "conflict",
    "other_interest": "other_signal",
    # Common variations of legacy categories
    "non_ai": "other_signal",
    "non-ai": "other_signal",
    "non ai": "other_signal",
    "unknown": "other_signal",
    "uncategorized": "other_signal",
    "uncategorised": "other_signal",
    "other": "other_signal",
    "misc": "other_signal",
    "general": "other_signal",
    # Political aliases
    "politics": "geopolitics",
    "geopolitics": "geopolitics",
    "international_relations": "geopolitics",
    "trump": "geopolitics",
    "biden": "geopolitics",
    # Conflict aliases
    "warfare": "conflict",
    "putin": "conflict",
    "military": "conflict",
    "defense": "conflict",
    # Economics aliases
    "finance": "economics",
    "financial": "economics",
    "credit_repair": "economics",
    "financial_services": "economics",
    "financial_advice": "economics",
    "debt": "economics",
    "money": "economics",
    "markets": "economics",
    "crypto": "economics",
    # Technology aliases
    "tech": "technology",
    "devops": "technology",
    "programming": "technology",
    "software": "technology",
    "cybersecurity": "technology",
    # AI sub-category aliases
    "ai_research": "ai_models",
    "machine_learning": "ai_models",
    "deep_learning": "ai_models",
    "llm": "ai_models",
    "coding": "ai_coding",
    "ai_tools": "ai_applications",
    "automation": "ai_coding",
    "ai_company": "ai_business",
    "ai_startup": "ai_business",
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

LOW_SIGNAL_DYNAMIC_TOPICS = {
    "this",
    "that",
    "these",
    "those",
    "here",
    "there",
    "shorts",
    "short",
    "credit",
    "credits",
    "after",
    "before",
    "caught",
    "your",
    "with",
    "clip",
    "clips",
    "vlog",
    "reel",
    "reels",
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
        return "Other Signal"
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


def _topic_maps_to_core(topic: str) -> str:
    key = _normalize_key(topic)
    if key in CORE_ORDER:
        return key if key != "other_signal" else ""

    mapped = CATEGORY_ALIASES.get(key)
    if mapped and mapped in CORE_ORDER and mapped != "other_signal":
        return str(mapped)

    # Check all core categories except other_signal for keyword matches
    for core_slug in CORE_ORDER:
        if core_slug == "other_signal":
            continue
        keywords = CORE_DEFINITIONS.get(core_slug, {}).get("keywords", [])
        for kw in keywords:
            norm_kw = _normalize_key(str(kw))
            if not norm_kw:
                continue
            if key == norm_kw:
                return core_slug
            if key.startswith(f"{norm_kw}_") or key.endswith(f"_{norm_kw}"):
                return core_slug
            if f"_{norm_kw}_" in f"_{key}_":
                return core_slug
    return ""


def _is_low_signal_topic(topic: str) -> bool:
    slug = _slugify(topic)
    if not slug:
        return True
    if slug in GENERIC_TOPIC_WORDS or slug in LOW_SIGNAL_DYNAMIC_TOPICS:
        return True
    parts = [p for p in slug.split("_") if p]
    if not parts:
        return True
    if len(parts) == 1:
        token = parts[0]
        if token in LOW_SIGNAL_DYNAMIC_TOPICS:
            return True
        if len(token) <= 3:
            return True
    return False


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
        "version": 2,
        "max_categories": max(len(CORE_ORDER), int(max_categories)),
        "new_category_min_topic_hits": 8,
        "categories": categories,
        "other_interest_topic_counts": {},  # kept for backward compat with existing state
        "other_signal_topic_counts": {},
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

    state["max_categories"] = max(len(CORE_ORDER), int(state.get("max_categories") or max_categories))
    state["new_category_min_topic_hits"] = max(5, int(state.get("new_category_min_topic_hits") or 8))
    # Migrate legacy key name while keeping backward compatibility
    if not isinstance(state.get("other_signal_topic_counts"), dict):
        # Migrate from old key if it exists
        legacy_counts = state.get("other_interest_topic_counts", {})
        state["other_signal_topic_counts"] = legacy_counts if isinstance(legacy_counts, dict) else {}
        changed = True
    if not isinstance(state.get("other_interest_topic_counts"), dict):
        state["other_interest_topic_counts"] = state["other_signal_topic_counts"]
        changed = True
    if not isinstance(state.get("retired_categories"), list):
        state["retired_categories"] = []
        changed = True

    retired = state.get("retired_categories")
    if not isinstance(retired, list):
        retired = []
        state["retired_categories"] = retired
        changed = True

    to_remove: list[tuple[str, str]] = []
    for slug, payload in categories.items():
        if slug in CORE_ORDER or not isinstance(payload, dict):
            continue
        if str(payload.get("kind") or "") != "dynamic":
            continue
        mapped_core = _topic_maps_to_core(slug)
        if mapped_core:
            to_remove.append((slug, f"mapped_to_core:{mapped_core}"))
            continue
        if _is_low_signal_topic(slug):
            to_remove.append((slug, "low_signal"))

    for slug, reason in to_remove:
        payload = categories.get(slug)
        if not isinstance(payload, dict):
            continue
        retired.append(
            {
                "slug": slug,
                "label": str(payload.get("label") or format_category_label(slug)),
                "count": int(payload.get("count") or 0),
                "retired_at": now,
                "reason": reason,
            }
        )
        del categories[slug]
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
        return "other_signal"
    mapped_core = _topic_maps_to_core(value)
    if mapped_core:
        return mapped_core
    if _is_low_signal_topic(value):
        return "other_signal"
    if value in CORE_ORDER:
        return value
    if state is not None:
        categories = state.get("categories")
        if isinstance(categories, dict) and value in categories:
            return value
    if value in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[value]
    if value == "non_ai":
        return "other_signal"
    if value == "unknown":
        return "other_signal"
    if value.startswith("other"):
        return "other_signal"
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
        if slug == "other_signal":
            continue
        score = _score_category(blob, [str(item).lower() for item in data.get("keywords", [])])
        if score > best_score:
            best_slug = slug
            best_score = score
    if not selected and best_slug and best_score > 0:
        selected = best_slug

    if not selected:
        selected = "other_signal"

    if selected == "other_signal":
        topic_counts = state.get("other_signal_topic_counts") or state.get("other_interest_topic_counts") or {}
        for topic in candidates[:12]:
            if _is_low_signal_topic(topic):
                continue
            if _topic_maps_to_core(topic):
                continue
            topic_counts[topic] = int(topic_counts.get(topic) or 0) + 1

        create_hint = _slugify(suggested_category)
        if create_hint in CATEGORY_ALIASES:
            create_hint = ""
        if create_hint in CORE_ORDER:
            create_hint = ""
        if create_hint in GENERIC_TOPIC_WORDS:
            create_hint = ""
        if create_hint and _is_low_signal_topic(create_hint):
            create_hint = ""
        if create_hint and _topic_maps_to_core(create_hint):
            create_hint = ""

        threshold = int(state.get("new_category_min_topic_hits") or 8)
        category_candidate = ""
        conf_value = float(confidence or 0.0)
        if create_hint and create_hint not in categories:
            # Strong, explicit model suggestions can create immediately.
            high_quality_hint = (len(create_hint) >= 6) or ("_" in create_hint)
            if conf_value >= 0.9 and high_quality_hint:
                category_candidate = create_hint
            else:
                # Lower-confidence suggestions must recur before spawning a category.
                topic_counts[create_hint] = int(topic_counts.get(create_hint) or 0) + 2

        if not category_candidate:
            for topic, count in sorted(topic_counts.items(), key=lambda item: int(item[1]), reverse=True):
                if int(count) < threshold:
                    break
                if topic in categories or topic in CORE_ORDER or topic in GENERIC_TOPIC_WORDS:
                    continue
                if _is_low_signal_topic(topic):
                    continue
                if _topic_maps_to_core(topic):
                    continue
                category_candidate = topic
                break

        if category_candidate and conf_value >= 0.45:
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
        selected = "other_signal"

    chosen = categories[selected]
    chosen["count"] = int(chosen.get("count") or 0) + 1
    chosen["updated_at"] = now

    if str(chosen.get("kind") or "") == "dynamic":
        kws = [str(item).lower() for item in chosen.get("keywords", []) if str(item).strip()]
        for token in candidates:
            if token in kws or token in GENERIC_TOPIC_WORDS:
                continue
            if _is_low_signal_topic(token):
                continue
            if _topic_maps_to_core(token):
                continue
            kws.append(token)
            if len(kws) >= 28:
                break
        chosen["keywords"] = kws

    state["total_classified"] = int(state.get("total_classified") or 0) + 1
    state["updated_at"] = now
    source_state.set_state(conn, CATEGORY_STATE_KEY, state)
    return selected, state
