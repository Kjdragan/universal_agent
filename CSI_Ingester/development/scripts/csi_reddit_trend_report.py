#!/usr/bin/env python3
"""Generate and emit periodic Reddit watchlist trend reports to UA."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from csi_ingester.analytics.emission import emit_and_track
from csi_ingester.config import load_config
from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.store import token_usage as token_usage_store
from csi_ingester.store.sqlite import connect, ensure_schema

CORE_ORDER = ("ai", "political", "war", "other_interest")

AI_HINTS = {
    "ai",
    "artificial",
    "machinelearning",
    "ml",
    "localllama",
    "llm",
    "chatgpt",
    "openai",
    "anthropic",
    "gemini",
    "generative",
    "language_model",
}
WAR_HINTS = {
    "war",
    "warcollege",
    "military",
    "combat",
    "battle",
    "defense",
    "ukrainewar",
    "conflict",
}
POLITICAL_HINTS = {
    "politics",
    "political",
    "geopolitics",
    "election",
    "government",
    "policy",
    "congress",
    "senate",
    "diplomacy",
}
STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "into",
    "about",
    "after",
    "before",
    "this",
    "that",
    "will",
    "have",
    "has",
    "are",
    "was",
    "were",
    "what",
    "when",
    "where",
    "why",
    "how",
    "new",
    "post",
    "reddit",
    "watchlist",
    "video",
    "videos",
}


def _load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if not item or item.startswith("#") or "=" not in item:
            continue
        key, raw = item.split("=", 1)
        val = raw.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        out[key.strip()] = val
    return out


def _apply_env_defaults(path: Path) -> None:
    for key, val in _load_env_file(path).items():
        os.environ.setdefault(key, val)


def _resolve_setting(keys: list[str], env_file_values: dict[str, str]) -> str:
    for key in keys:
        env_val = os.getenv(key, "").strip()
        if env_val:
            return env_val
        file_val = env_file_values.get(key, "").strip()
        if file_val:
            return file_val
    return ""


def _window(hours: int) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(hours=max(1, hours))
    return start, end


def _slugify(text: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower())
    out = re.sub(r"_+", "_", out).strip("_")
    return out


def _canonicalize_category(raw: str) -> str:
    slug = _slugify(raw)
    if slug in {"ai", "artificial_intelligence"}:
        return "ai"
    if slug in {"political", "politics", "geopolitics"}:
        return "political"
    if slug in {"war", "military", "conflict"}:
        return "war"
    if slug in {"other", "other_interest", "otherinterest", "misc"}:
        return "other_interest"
    if slug in CORE_ORDER:
        return slug
    return "other_interest"


def _format_category(slug: str) -> str:
    if slug == "ai":
        return "AI"
    if slug == "political":
        return "Political"
    if slug == "war":
        return "War"
    if slug == "other_interest":
        return "Other Interest"
    return slug.replace("_", " ").title() if slug else "Other Interest"


def _load_watchlist_category_map(path: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not path.exists():
        return mapping
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return mapping

    items: list[Any] = []
    if isinstance(payload, dict):
        maybe = payload.get("subreddits")
        if isinstance(maybe, list):
            items = maybe
    elif isinstance(payload, list):
        items = payload

    for row in items:
        if isinstance(row, str):
            name = row.strip()
            if name:
                mapping[name.lower()] = "other_interest"
            continue
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("subreddit") or "").strip()
        if not name:
            continue
        hint = _canonicalize_category(str(row.get("category_hint") or "other_interest"))
        mapping[name.lower()] = hint
    return mapping


def _infer_category(*, subreddit: str, title: str, watchlist_map: dict[str, str]) -> str:
    sub_l = subreddit.strip().lower()
    if sub_l in watchlist_map:
        return _canonicalize_category(watchlist_map[sub_l])

    text = f"{subreddit} {title}".lower()
    if any(hint in text for hint in AI_HINTS):
        return "ai"
    if any(hint in text for hint in WAR_HINTS):
        return "war"
    if any(hint in text for hint in POLITICAL_HINTS):
        return "political"
    return "other_interest"


def _extract_terms(title: str) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]{2,}", title.lower())
    out: list[str] = []
    for word in words:
        if word in STOPWORDS:
            continue
        out.append(word)
    return out


def _build_report_data(
    conn: sqlite3.Connection,
    start_db: str,
    end_db: str,
    watchlist_map: dict[str, str],
    *,
    start_dt: datetime,
    end_dt: datetime,
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT id, event_id, created_at, subject_json
        FROM events
        WHERE source = 'reddit_discovery' AND created_at >= ? AND created_at < ?
        ORDER BY created_at DESC, id DESC
        """,
        (start_db, end_db),
    ).fetchall()

    by_category: Counter[str] = Counter()
    by_subreddit: Counter[str] = Counter()
    term_counter: Counter[str] = Counter()
    term_examples: dict[str, list[str]] = defaultdict(list)
    category_samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    scored_posts: list[dict[str, Any]] = []

    for row in rows:
        try:
            subject = json.loads(str(row["subject_json"] or "{}"))
            if not isinstance(subject, dict):
                subject = {}
        except Exception:
            subject = {}

        subreddit = str(subject.get("subreddit") or "unknown").strip() or "unknown"
        title = str(subject.get("title") or "").strip()
        permalink = str(subject.get("permalink") or "").strip()
        url = str(subject.get("url") or "").strip()
        final_url = permalink or url
        score = int(subject.get("score") or 0)
        comments = int(subject.get("num_comments") or 0)

        category = _infer_category(subreddit=subreddit, title=title, watchlist_map=watchlist_map)
        by_category[category] += 1
        by_subreddit[subreddit] += 1

        for term in _extract_terms(title):
            term_counter[term] += 1
            if len(term_examples[term]) < 3 and title:
                term_examples[term].append(title)

        sample = {
            "event_id": str(row["event_id"] or ""),
            "subreddit": subreddit,
            "title": title,
            "url": final_url,
            "score": score,
            "comments": comments,
            "created_at": str(row["created_at"] or ""),
        }
        category_samples[category].append(sample)
        scored_posts.append(sample)

    top_subreddits = [{"subreddit": s, "count": c} for s, c in by_subreddit.most_common(16)]
    top_topics = [{"topic": t, "count": c} for t, c in term_counter.most_common(20)]
    top_narratives = [
        {"topic": t, "count": c, "examples": term_examples.get(t, [])}
        for t, c in term_counter.most_common(12)
    ]
    top_posts = sorted(
        scored_posts,
        key=lambda item: (int(item.get("score") or 0), int(item.get("comments") or 0)),
        reverse=True,
    )[:12]

    prev_window = end_dt - start_dt
    prev_start_dt = start_dt - prev_window
    prev_end_dt = start_dt
    prev_start_db = prev_start_dt.strftime("%Y-%m-%d %H:%M:%S")
    prev_end_db = prev_end_dt.strftime("%Y-%m-%d %H:%M:%S")
    prev_subreddit_counts: dict[str, int] = {}
    for row in conn.execute(
        """
        SELECT
            json_extract(subject_json, '$.subreddit') AS subreddit,
            COUNT(*) AS c
        FROM events
        WHERE source = 'reddit_discovery' AND created_at >= ? AND created_at < ?
        GROUP BY json_extract(subject_json, '$.subreddit')
        """,
        (prev_start_db, prev_end_db),
    ).fetchall():
        subreddit = str(row["subreddit"] or "unknown").strip() or "unknown"
        prev_subreddit_counts[subreddit] = int(row["c"] or 0)

    watchlist_movers: list[dict[str, Any]] = []
    for item in top_subreddits:
        subreddit = str(item.get("subreddit") or "")
        count = int(item.get("count") or 0)
        prev_count = int(prev_subreddit_counts.get(subreddit) or 0)
        watchlist_movers.append(
            {
                "subreddit": subreddit,
                "count": count,
                "previous_count": prev_count,
                "delta": count - prev_count,
            }
        )
    watchlist_movers.sort(key=lambda entry: (int(entry["delta"]), int(entry["count"])), reverse=True)

    token_usage = conn.execute(
        """
        SELECT
            COUNT(*) AS records,
            COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
            COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
            COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM token_usage
        WHERE occurred_at >= ? AND occurred_at < ?
        """,
        (start_db, end_db),
    ).fetchone()

    token_usage_by_process: list[dict[str, Any]] = []
    for item in conn.execute(
        """
        SELECT process_name, COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM token_usage
        WHERE occurred_at >= ? AND occurred_at < ?
        GROUP BY process_name
        ORDER BY total_tokens DESC, process_name ASC
        LIMIT 10
        """,
        (start_db, end_db),
    ).fetchall():
        token_usage_by_process.append(
            {
                "process_name": str(item["process_name"] or "unknown"),
                "total_tokens": int(item["total_tokens"] or 0),
            }
        )

    return {
        "total_items": len(rows),
        "by_category": dict(by_category),
        "top_subreddits": top_subreddits,
        "top_topics": top_topics,
        "top_narratives": top_narratives,
        "watchlist_movers": watchlist_movers[:10],
        "top_posts": top_posts,
        "token_usage_snapshot": {
            "records": int(token_usage["records"] or 0),
            "prompt_tokens": int(token_usage["prompt_tokens"] or 0),
            "completion_tokens": int(token_usage["completion_tokens"] or 0),
            "total_tokens": int(token_usage["total_tokens"] or 0),
            "by_process": token_usage_by_process,
        },
        "samples": {k: v[:20] for k, v in category_samples.items()},
    }


def _fallback_markdown(report_data: dict[str, Any], start_iso: str, end_iso: str) -> str:
    lines: list[str] = []
    lines.append(f"# CSI Reddit Trend Report ({start_iso} -> {end_iso})")
    lines.append("")
    lines.append("## Totals")
    lines.append(f"- Posts analyzed: {int(report_data.get('total_items') or 0)}")
    by_category = report_data.get("by_category", {})
    if isinstance(by_category, dict):
        lines.append(f"- AI posts: {int(by_category.get('ai') or 0)}")
        lines.append(f"- Political posts: {int(by_category.get('political') or 0)}")
        lines.append(f"- War posts: {int(by_category.get('war') or 0)}")
        lines.append(f"- Other-interest posts: {int(by_category.get('other_interest') or 0)}")
        extras = [
            (str(slug), int(count or 0))
            for slug, count in by_category.items()
            if str(slug) not in {"ai", "political", "war", "other_interest"}
        ]
        extras.sort(key=lambda item: item[1], reverse=True)
        if extras:
            lines.append("- Emerging categories: " + ", ".join(f"{_format_category(slug)}={count}" for slug, count in extras[:8]))
    lines.append("")
    lines.append("## Top Subreddits")
    for item in report_data.get("top_subreddits", [])[:10]:
        if isinstance(item, dict):
            lines.append(f"- r/{item.get('subreddit')}: {item.get('count')}")
    lines.append("")
    lines.append("## Watchlist Movers")
    for item in report_data.get("watchlist_movers", [])[:8]:
        if isinstance(item, dict):
            lines.append(
                f"- r/{item.get('subreddit')}: {item.get('count')} (prev {item.get('previous_count')}, delta {int(item.get('delta') or 0):+d})"
            )
    lines.append("")
    lines.append("## Top Topics")
    for item in report_data.get("top_topics", [])[:12]:
        if isinstance(item, dict):
            lines.append(f"- {item.get('topic')}: {item.get('count')}")
    lines.append("")
    lines.append("## Top Posts")
    for item in report_data.get("top_posts", [])[:8]:
        if isinstance(item, dict):
            lines.append(
                f"- r/{item.get('subreddit')}: {item.get('title')} (score={int(item.get('score') or 0)}, comments={int(item.get('comments') or 0)})"
            )
            if item.get("url"):
                lines.append(f"  {item.get('url')}")
    lines.append("")
    lines.append("## Token Usage Snapshot")
    token_usage = report_data.get("token_usage_snapshot", {})
    if isinstance(token_usage, dict):
        lines.append(
            f"- Total tokens: {int(token_usage.get('total_tokens') or 0)} (prompt={int(token_usage.get('prompt_tokens') or 0)}, completion={int(token_usage.get('completion_tokens') or 0)})"
        )
        for item in token_usage.get("by_process", [])[:8]:
            if isinstance(item, dict):
                lines.append(f"- {item.get('process_name')}: {item.get('total_tokens')}")
    return "\n".join(lines).strip()


def _claude_markdown(
    *,
    report_data: dict[str, Any],
    start_iso: str,
    end_iso: str,
    api_key: str,
    model: str,
    endpoint: str,
) -> tuple[str | None, dict[str, int]]:
    prompt = (
        "Produce a concise trend report in markdown for monitored Reddit watchlist posts.\n"
        "Focus on meaningful shifts and recurring narratives.\n"
        "Sections required: Executive Summary, AI Signals, Political Signals, War Signals, "
        "Other Interest Signals, Watchlist Movers, Top Narratives, Top Posts, Token Usage Snapshot, Watch Items.\n"
        "Keep under 2200 words.\n\n"
        f"Window: {start_iso} -> {end_iso}\n"
        f"Data JSON:\n{json.dumps(report_data, ensure_ascii=False)}"
    )
    req_body = {
        "model": model,
        "max_tokens": 1200,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(req_body).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None, {}

    parts: list[str] = []
    for block in payload.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or "").strip())
    markdown = "\n\n".join([p for p in parts if p]).strip()
    if not markdown:
        return None, {}

    usage_obj = payload.get("usage") if isinstance(payload, dict) else None
    usage: dict[str, int] = {}
    if isinstance(usage_obj, dict):
        input_tokens = int(usage_obj.get("input_tokens") or 0)
        cache_create = int(usage_obj.get("cache_creation_input_tokens") or 0)
        cache_read = int(usage_obj.get("cache_read_input_tokens") or 0)
        prompt_tokens = max(0, input_tokens + cache_create + cache_read)
        completion_tokens = int(usage_obj.get("output_tokens") or 0)
        total_tokens = int(usage_obj.get("total_tokens") or (prompt_tokens + completion_tokens))
        usage = {
            "prompt_tokens": max(0, prompt_tokens),
            "completion_tokens": max(0, completion_tokens),
            "total_tokens": max(0, total_tokens),
        }
    return markdown[:48000], usage


def _save_insight_report(
    conn: sqlite3.Connection,
    *,
    report_key: str,
    report_data: dict[str, Any],
    markdown: str,
    model_name: str,
    usage: dict[str, int],
) -> None:
    conn.execute(
        """
        INSERT INTO insight_reports (
            report_key, report_type, window_start_utc, window_end_utc, model_name,
            prompt_tokens, completion_tokens, total_tokens, report_markdown, report_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_key) DO UPDATE SET
            report_type=excluded.report_type,
            window_start_utc=excluded.window_start_utc,
            window_end_utc=excluded.window_end_utc,
            model_name=excluded.model_name,
            prompt_tokens=excluded.prompt_tokens,
            completion_tokens=excluded.completion_tokens,
            total_tokens=excluded.total_tokens,
            report_markdown=excluded.report_markdown,
            report_json=excluded.report_json,
            created_at=datetime('now')
        """,
        (
            report_key,
            "reddit_trend_report",
            str(report_data.get("window_start_utc") or ""),
            str(report_data.get("window_end_utc") or ""),
            model_name or None,
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
            int(usage.get("total_tokens") or 0),
            markdown,
            json.dumps(report_data, separators=(",", ":"), sort_keys=True),
        ),
    )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Reddit trend report and emit to UA.")
    parser.add_argument("--db-path", default="/opt/universal_agent/CSI_Ingester/development/var/csi.db")
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument(
        "--watchlist-file",
        default="",
    )
    parser.add_argument(
        "--state-path",
        default="/opt/universal_agent/CSI_Ingester/development/var/reddit_trend_report_state.json",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--env-file", default="/opt/universal_agent/.env")
    parser.add_argument(
        "--csi-env-file",
        default="/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env",
    )
    args = parser.parse_args()

    _apply_env_defaults(Path(args.csi_env_file).expanduser())
    _apply_env_defaults(Path(args.env_file).expanduser())
    env_file_values = _load_env_file(Path(args.env_file).expanduser())
    env_file_values.update(_load_env_file(Path(args.csi_env_file).expanduser()))

    conn = connect(Path(args.db_path).expanduser())
    ensure_schema(conn)
    config = load_config()

    start_dt, end_dt = _window(max(1, int(args.window_hours)))
    start_db = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    end_db = end_dt.strftime("%Y-%m-%d %H:%M:%S")
    start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    hour_key = end_dt.strftime("%Y-%m-%dT%H:00:00Z")

    state_path = Path(args.state_path).expanduser()
    state: dict[str, Any] = {}
    if state_path.exists():
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                state = raw
        except Exception:
            state = {}
    if not args.force and state.get("last_hour_key") == hour_key:
        print(f"REDDIT_TREND_REPORT_SKIPPED hour={hour_key}")
        conn.close()
        return 0

    watchlist_file = (
        (args.watchlist_file or "").strip()
        or _resolve_setting(["CSI_REDDIT_WATCHLIST_FILE"], env_file_values)
        or "/opt/universal_agent/CSI_Ingester/development/reddit_watchlist.json"
    )
    watchlist_map = _load_watchlist_category_map(Path(watchlist_file).expanduser())
    report_data = _build_report_data(
        conn,
        start_db,
        end_db,
        watchlist_map,
        start_dt=start_dt,
        end_dt=end_dt,
    )
    total_items = int(report_data.get("total_items") or 0)
    if total_items == 0:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({"last_hour_key": hour_key, "last_status": "no_data"}, indent=2),
            encoding="utf-8",
        )
        print("REDDIT_TREND_REPORT_NO_DATA=1")
        conn.close()
        return 0

    report_data["window_start_utc"] = start_iso
    report_data["window_end_utc"] = end_iso

    use_claude = _resolve_setting(["CSI_REDDIT_TREND_USE_CLAUDE"], env_file_values).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    api_key = _resolve_setting(["ANTHROPIC_API_KEY"], env_file_values)
    model = _resolve_setting(["CSI_REDDIT_TREND_CLAUDE_MODEL"], env_file_values) or "claude-3-5-haiku-latest"
    base_url = (_resolve_setting(["ANTHROPIC_BASE_URL"], env_file_values) or "https://api.anthropic.com").rstrip("/")
    endpoint = f"{base_url}/v1/messages" if not base_url.endswith("/v1/messages") else base_url

    markdown = _fallback_markdown(report_data, start_iso, end_iso)
    usage: dict[str, int] = {}
    model_name = ""
    if use_claude and api_key:
        polished, usage = _claude_markdown(
            report_data=report_data,
            start_iso=start_iso,
            end_iso=end_iso,
            api_key=api_key,
            model=model,
            endpoint=endpoint,
        )
        if polished:
            markdown = polished
            model_name = model
        if usage:
            token_usage_store.insert_usage(
                conn,
                process_name="reddit_trend_report_claude",
                model_name=model,
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                total_tokens=int(usage.get("total_tokens") or 0),
                metadata={"window_start_utc": start_iso, "window_end_utc": end_iso, "items": total_items},
            )

    report_key = f"reddit_trend_report:{config.instance_id}:{hour_key}"
    _save_insight_report(
        conn,
        report_key=report_key,
        report_data=report_data,
        markdown=markdown,
        model_name=model_name,
        usage=usage,
    )

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    event = CreatorSignalEvent(
        event_id=f"csi:reddit_trend_report:{config.instance_id}:{end_dt.strftime('%Y%m%d%H')}",
        dedupe_key=f"csi:reddit_trend_report:{config.instance_id}:{end_dt.strftime('%Y%m%d%H')}",
        source="csi_analytics",
        event_type="reddit_trend_report",
        occurred_at=end_iso,
        received_at=now_iso,
        subject={
            "report_type": "reddit_trend_report",
            "report_key": report_key,
            "window_start_utc": start_iso,
            "window_end_utc": end_iso,
            "totals": {
                "items": total_items,
                "by_category": report_data.get("by_category", {}),
            },
            "top_subreddits": report_data.get("top_subreddits", []),
            "top_topics": report_data.get("top_topics", []),
            "top_narratives": report_data.get("top_narratives", []),
            "watchlist_movers": report_data.get("watchlist_movers", []),
            "top_posts": report_data.get("top_posts", []),
            "token_usage_snapshot": report_data.get("token_usage_snapshot", {}),
            "markdown": markdown,
            "token_usage": usage,
        },
        routing={
            "pipeline": "csi_reddit_trend_analytics",
            "priority": "standard",
            "tags": ["csi", "trend_report", "reddit"],
        },
        metadata={"source_adapter": "csi_reddit_trend_report_v1", "report_key": report_key},
    )

    delivered, status_code, payload = emit_and_track(conn, config=config, event=event, retry_count=3)
    if delivered:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "last_hour_key": hour_key,
                    "last_sent_at": now_iso,
                    "last_status_code": status_code,
                    "items": total_items,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        conn.close()
        print(f"REDDIT_TREND_REPORT_SENT hour={hour_key} status={status_code} items={total_items}")
        return 0

    conn.close()
    print(f"REDDIT_TREND_REPORT_FAILED hour={hour_key} status={status_code} payload={payload}")
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
