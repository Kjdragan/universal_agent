#!/usr/bin/env python3
"""Enrich RSS events with transcript-backed semantic summaries and category labels."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from csi_ingester.analytics.categories import (
    classify_and_update_category,
    normalize_existing_analysis_categories,
)
from csi_ingester.llm_auth import resolve_csi_llm_auth
from csi_ingester.store import token_usage as token_usage_store
from csi_ingester.net import parse_endpoint_list, post_json_with_failover
from csi_ingester.store.sqlite import connect, ensure_schema


def _load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if not item or item.startswith("#") or "=" not in item:
            continue
        key, raw_val = item.split("=", 1)
        val = raw_val.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        out[key.strip()] = val
    return out


def _resolve_setting(keys: list[str], env_file_values: dict[str, str]) -> str:
    for key in keys:
        env_val = os.getenv(key, "").strip()
        if env_val:
            return env_val
        file_val = env_file_values.get(key, "").strip()
        if file_val:
            return file_val
    return ""


def _apply_env_defaults(path: Path) -> None:
    for key, val in _load_env_file(path).items():
        os.environ.setdefault(key, val)


def _fetch_transcript_failover(
    *,
    endpoints: list[str],
    token: str,
    video_id: str,
    video_url: str,
    timeout_seconds: int,
    max_chars: int,
    min_chars: int,
) -> dict[str, Any]:
    payload = {
        "video_id": video_id,
        "video_url": video_url,
        "timeout_seconds": timeout_seconds,
        "max_chars": max_chars,
        "min_chars": min_chars,
    }
    return post_json_with_failover(
        endpoints=endpoints,
        payload=payload,
        token=token,
        timeout_seconds=max(10, timeout_seconds + 5),
        success_predicate=lambda result: bool(result.get("ok")) and bool(str(result.get("transcript_text") or "").strip()),
    )


def _fallback_summary(title: str, transcript_text: str, category: str) -> tuple[str, list[str], float]:
    clean = re.sub(r"\s+", " ", transcript_text).strip()
    base = clean[:300] if clean else ""
    if base:
        summary = f"{title.strip()} :: {base}"
    else:
        summary = f"{title.strip()} :: transcript unavailable; metadata-only classification"
    themes: list[str] = [category]
    if category == "ai":
        themes.extend(["ai_tools", "automation"])
    elif category == "political":
        themes.extend(["politics", "public_policy"])
    elif category == "war":
        themes.extend(["security", "geopolitics"])
    else:
        themes.extend(["general_interest"])
    return summary[:1000], themes, 0.55


def _extract_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    stripped = text.strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(stripped[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
    return None


def _analyze_with_claude(
    *,
    title: str,
    channel_name: str,
    channel_id: str,
    transcript_text: str,
    model: str,
    endpoint: str,
    api_key: str,
) -> tuple[dict[str, Any] | None, dict[str, int]]:
    transcript_excerpt = transcript_text[:12000]
    prompt = (
        "Classify and summarize this YouTube upload for trend tracking.\n"
        "Return ONLY valid JSON with keys:\n"
        "category (ai|political|war|other_interest|or short snake_case category), "
        "summary (string <= 700 chars), themes (array up to 6), confidence (0..1).\n\n"
        f"Channel Name: {channel_name}\n"
        f"Channel ID: {channel_id}\n"
        f"Title: {title}\n\n"
        f"Transcript:\n{transcript_excerpt}"
    )
    req_body = {
        "model": model,
        "max_tokens": 500,
        "temperature": 0.1,
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
        with urllib.request.urlopen(req, timeout=45) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None, {}

    text_parts: list[str] = []
    for block in payload.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(str(block.get("text") or ""))
    parsed = _extract_json_object("\n".join(text_parts))

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
    return parsed, usage


def _select_pending(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT e.id, e.event_id, e.source, e.subject_json, e.created_at
        FROM events e
        LEFT JOIN rss_event_analysis a ON a.event_id = e.event_id
        WHERE e.source = 'youtube_channel_rss' AND e.delivered = 1 AND a.event_id IS NULL
        ORDER BY e.id ASC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()


def _upsert_analysis(
    conn: sqlite3.Connection,
    *,
    event_db_id: int,
    event_id: str,
    source: str,
    video_id: str,
    channel_id: str,
    channel_name: str,
    title: str,
    published_at: str,
    transcript_status: str,
    transcript_chars: int,
    transcript_ref: str,
    category: str,
    summary_text: str,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    analysis_json: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO rss_event_analysis (
            event_id, event_db_id, source, video_id, channel_id, channel_name, title,
            published_at, transcript_status, transcript_chars, transcript_ref,
            category, summary_text, model_name, prompt_tokens, completion_tokens,
            total_tokens, analysis_json, analyzed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(event_id) DO UPDATE SET
            event_db_id=excluded.event_db_id,
            source=excluded.source,
            video_id=excluded.video_id,
            channel_id=excluded.channel_id,
            channel_name=excluded.channel_name,
            title=excluded.title,
            published_at=excluded.published_at,
            transcript_status=excluded.transcript_status,
            transcript_chars=excluded.transcript_chars,
            transcript_ref=excluded.transcript_ref,
            category=excluded.category,
            summary_text=excluded.summary_text,
            model_name=excluded.model_name,
            prompt_tokens=excluded.prompt_tokens,
            completion_tokens=excluded.completion_tokens,
            total_tokens=excluded.total_tokens,
            analysis_json=excluded.analysis_json,
            analyzed_at=datetime('now')
        """,
        (
            event_id,
            event_db_id,
            source,
            video_id,
            channel_id,
            channel_name,
            title,
            published_at,
            transcript_status,
            max(0, int(transcript_chars)),
            transcript_ref,
            category,
            summary_text,
            model_name or None,
            max(0, int(prompt_tokens)),
            max(0, int(completion_tokens)),
            max(0, int(total_tokens)),
            json.dumps(analysis_json, separators=(",", ":"), sort_keys=True),
        ),
    )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="RSS semantic enrichment with transcript summaries.")
    parser.add_argument("--db-path", default="/opt/universal_agent/CSI_Ingester/development/var/csi.db")
    parser.add_argument("--max-events", type=int, default=8)
    parser.add_argument("--env-file", default="/opt/universal_agent/.env")
    parser.add_argument(
        "--csi-env-file",
        default="/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env",
    )
    parser.add_argument(
        "--transcript-endpoint",
        default="http://127.0.0.1:8002/api/v1/youtube/ingest",
        help="UA transcript ingest endpoint",
    )
    parser.add_argument("--transcript-timeout-seconds", type=int, default=90)
    parser.add_argument("--transcript-max-chars", type=int, default=120000)
    parser.add_argument("--transcript-min-chars", type=int, default=120)
    parser.add_argument("--claude-model", default="")
    parser.add_argument("--max-categories", type=int, default=10)
    args = parser.parse_args()

    db_path = Path(args.db_path).expanduser()
    _apply_env_defaults(Path(args.csi_env_file).expanduser())
    _apply_env_defaults(Path(args.env_file).expanduser())
    env_file_values = _load_env_file(Path(args.env_file).expanduser())
    csi_env_values = _load_env_file(Path(args.csi_env_file).expanduser())
    merged_env_values = {**env_file_values, **csi_env_values}
    conn = connect(db_path)
    ensure_schema(conn)
    normalize_existing_analysis_categories(conn)

    rows = _select_pending(conn, max(1, int(args.max_events)))
    if not rows:
        print("RSS_ENRICH_PENDING=0")
        conn.close()
        return 0

    transcript_endpoints_raw = _resolve_setting(
        ["CSI_RSS_ANALYSIS_TRANSCRIPT_ENDPOINTS", "CSI_RSS_ANALYSIS_TRANSCRIPT_ENDPOINT"],
        merged_env_values,
    ).strip()
    transcript_endpoints = parse_endpoint_list(
        transcript_endpoints_raw,
        fallback=args.transcript_endpoint.strip(),
    )
    transcript_token = _resolve_setting(
        ["CSI_RSS_ANALYSIS_TRANSCRIPT_TOKEN", "UA_YOUTUBE_INGEST_TOKEN", "UA_INTERNAL_API_TOKEN"],
        merged_env_values,
    )

    use_claude = _resolve_setting(["CSI_RSS_ANALYSIS_USE_CLAUDE"], merged_env_values).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    auth = resolve_csi_llm_auth(merged_env_values, default_base_url="https://api.anthropic.com")
    api_key = auth.api_key
    base_url = auth.base_url
    claude_endpoint = f"{base_url}/v1/messages" if not base_url.endswith("/v1/messages") else base_url
    model = (
        args.claude_model.strip()
        or _resolve_setting(["CSI_RSS_ANALYSIS_CLAUDE_MODEL"], merged_env_values).strip()
        or "claude-3-5-haiku-latest"
    )
    max_categories_setting = _resolve_setting(["CSI_RSS_ANALYSIS_MAX_CATEGORIES"], merged_env_values).strip()
    try:
        max_categories = max(4, int(max_categories_setting)) if max_categories_setting else max(4, int(args.max_categories))
    except Exception:
        max_categories = max(4, int(args.max_categories))

    processed = 0
    transcript_ok = 0
    claude_used = 0
    category_counts: Counter[str] = Counter()
    endpoint_success_counts: dict[str, int] = {}

    for row in rows:
        event_db_id = int(row["id"])
        event_id = str(row["event_id"])
        source = str(row["source"] or "youtube_channel_rss")
        try:
            subject = json.loads(str(row["subject_json"] or "{}"))
            if not isinstance(subject, dict):
                subject = {}
        except Exception:
            subject = {}

        video_id = str(subject.get("video_id") or "").strip()
        video_url = str(subject.get("url") or "").strip()
        if not video_url and video_id:
            video_url = f"https://www.youtube.com/watch?v={video_id}"
        channel_id = str(subject.get("channel_id") or "").strip()
        channel_name = str(subject.get("channel_name") or "").strip()
        title = str(subject.get("title") or "").strip()
        published_at = str(subject.get("published_at") or "").strip()

        transcript_result = _fetch_transcript_failover(
            endpoints=transcript_endpoints,
            token=transcript_token,
            video_id=video_id,
            video_url=video_url,
            timeout_seconds=max(30, int(args.transcript_timeout_seconds)),
            max_chars=max(5000, int(args.transcript_max_chars)),
            min_chars=max(20, int(args.transcript_min_chars)),
        )
        transcript_text = str(transcript_result.get("transcript_text") or "")
        transcript_chars = int(transcript_result.get("transcript_chars") or len(transcript_text))
        transcript_status = "ok" if bool(transcript_result.get("ok")) and transcript_text else "failed"
        endpoint_used = str(transcript_result.get("_endpoint") or "").strip()
        source_or_error = str(transcript_result.get("source") or transcript_result.get("error") or "").strip()
        endpoint_host = ""
        if endpoint_used:
            try:
                endpoint_host = (urlparse(endpoint_used).netloc or endpoint_used).strip()
            except Exception:
                endpoint_host = endpoint_used
        transcript_ref = source_or_error
        if endpoint_host:
            transcript_ref = f"{source_or_error}@{endpoint_host}"
        if transcript_status == "ok":
            transcript_ok += 1
            if endpoint_host:
                endpoint_success_counts[endpoint_host] = endpoint_success_counts.get(endpoint_host, 0) + 1

        suggested_category = ""
        summary_text, themes, confidence = _fallback_summary(title, transcript_text, "other_interest")
        analysis_json: dict[str, Any] = {
            "category": "other_interest",
            "themes": themes,
            "confidence": confidence,
            "transcript_status": transcript_status,
            "transcript_ref": transcript_ref,
            "transcript_endpoint": endpoint_used,
            "transcript_endpoint_attempts": transcript_result.get("endpoint_attempts", []),
        }
        model_name = ""
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0

        if use_claude and api_key and transcript_text:
            parsed, usage = _analyze_with_claude(
                title=title,
                channel_name=channel_name,
                channel_id=channel_id,
                transcript_text=transcript_text,
                model=model,
                endpoint=claude_endpoint,
                api_key=api_key,
            )
            if parsed:
                parsed_category = str(parsed.get("category") or "").strip().lower()
                if parsed_category:
                    suggested_category = parsed_category
                summary_val = str(parsed.get("summary") or "").strip()
                if summary_val:
                    summary_text = summary_val[:1000]
                parsed_themes = parsed.get("themes")
                if isinstance(parsed_themes, list):
                    themes = [str(item).strip() for item in parsed_themes if str(item).strip()][:8]
                confidence_val = parsed.get("confidence")
                try:
                    confidence = float(confidence_val)
                except Exception:
                    confidence = confidence
                analysis_json = {
                    "category": suggested_category or "other_interest",
                    "themes": themes,
                    "confidence": max(0.0, min(1.0, confidence)),
                    "transcript_status": transcript_status,
                    "transcript_ref": transcript_ref,
                    "transcript_endpoint": endpoint_used,
                    "transcript_endpoint_attempts": transcript_result.get("endpoint_attempts", []),
                    "claude": parsed,
                }
                if usage:
                    prompt_tokens = int(usage.get("prompt_tokens") or 0)
                    completion_tokens = int(usage.get("completion_tokens") or 0)
                    total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
                    model_name = model
                    claude_used += 1
                    token_usage_store.insert_usage(
                        conn,
                        process_name="rss_semantic_enrich_claude",
                        model_name=model,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        metadata={
                            "event_id": event_id,
                            "video_id": video_id,
                            "category": suggested_category or "other_interest",
                        },
                    )

        category, taxonomy_state = classify_and_update_category(
            conn,
            suggested_category=suggested_category,
            title=title,
            channel_name=channel_name,
            summary_text=summary_text,
            transcript_text=transcript_text,
            themes=themes,
            confidence=confidence,
            max_categories=max_categories,
        )
        analysis_json["category"] = category
        analysis_json["taxonomy_categories"] = sorted(list((taxonomy_state.get("categories") or {}).keys()))
        analysis_json["taxonomy_total"] = int(taxonomy_state.get("total_classified") or 0)
        analysis_json["suggested_category"] = suggested_category

        _upsert_analysis(
            conn,
            event_db_id=event_db_id,
            event_id=event_id,
            source=source,
            video_id=video_id,
            channel_id=channel_id,
            channel_name=channel_name,
            title=title,
            published_at=published_at,
            transcript_status=transcript_status,
            transcript_chars=transcript_chars,
            transcript_ref=transcript_ref,
            category=category,
            summary_text=summary_text,
            model_name=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            analysis_json=analysis_json,
        )

        category_counts[category] += 1
        processed += 1

    conn.close()
    print(f"RSS_ENRICH_PENDING={len(rows)}")
    print(f"RSS_ENRICH_PROCESSED={processed}")
    print(f"RSS_ENRICH_TRANSCRIPT_OK={transcript_ok}")
    print(f"RSS_ENRICH_CLAUDE_USED={claude_used}")
    print(f"RSS_ENRICH_AI={int(category_counts.get('ai') or 0)}")
    print(f"RSS_ENRICH_POLITICAL={int(category_counts.get('political') or 0)}")
    print(f"RSS_ENRICH_WAR={int(category_counts.get('war') or 0)}")
    print(f"RSS_ENRICH_OTHER_INTEREST={int(category_counts.get('other_interest') or 0)}")
    # Backward-compatible metric for earlier dashboards.
    print(f"RSS_ENRICH_NON_AI={int(category_counts.get('other_interest') or 0)}")
    for slug, count in sorted(category_counts.items()):
        metric = re.sub(r"[^A-Z0-9_]+", "_", slug.upper())
        print(f"RSS_ENRICH_CATEGORY_{metric}={int(count)}")
    if endpoint_success_counts:
        for host, count in sorted(endpoint_success_counts.items()):
            print(f"RSS_ENRICH_ENDPOINT_OK host={host} count={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
