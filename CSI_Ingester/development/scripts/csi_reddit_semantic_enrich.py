#!/usr/bin/env python3
"""Enrich delivered Reddit discovery events with semantic summaries and categories."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from csi_ingester.analytics.categories import (
    classify_and_update_category,
    normalize_existing_analysis_categories,
)
from csi_ingester.llm_auth import resolve_csi_llm_auth
from csi_ingester.store import token_usage as token_usage_store
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


def _apply_env_defaults(path: Path) -> None:
    for key, value in _load_env_file(path).items():
        os.environ.setdefault(key, value)


def _resolve_setting(keys: list[str], env_file_values: dict[str, str]) -> str:
    for key in keys:
        env_val = os.getenv(key, "").strip()
        if env_val:
            return env_val
        file_val = env_file_values.get(key, "").strip()
        if file_val:
            return file_val
    return ""


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


def _fallback_summary(*, title: str, subreddit: str, url: str, score: int, comments: int) -> tuple[str, list[str], float, str]:
    summary = (
        f"Post in r/{subreddit}: {title}. "
        f"Current engagement snapshot: score={max(0, int(score))}, comments={max(0, int(comments))}."
    )
    if url:
        summary += f" Source link: {url}."
    themes = [f"r/{subreddit}" if subreddit else "reddit", "watchlist", "discovery"]
    return summary[:2000], themes[:12], 0.55, "Fallback heuristic due to unavailable LLM output."


def _analyze_with_claude(
    *,
    title: str,
    subreddit: str,
    author: str,
    url: str,
    score: int,
    num_comments: int,
    context_text: str,
    model: str,
    endpoint: str,
    api_key: str,
) -> tuple[dict[str, Any] | None, dict[str, int]]:
    prompt = (
        "Classify and summarize this Reddit watchlist discovery event for CSI trend tracking.\n"
        "Return ONLY valid JSON with keys:\n"
        "category (ai|political|war|other_interest|or short snake_case category), "
        "summary (string <= 2000 chars), themes (array of 10-12 concise themes), "
        "confidence (0..1), confidence_rationale (string <= 260 chars).\n\n"
        f"Subreddit: {subreddit}\n"
        f"Author: {author}\n"
        f"Title: {title}\n"
        f"Score: {score}\n"
        f"Comments: {num_comments}\n"
        f"URL: {url}\n\n"
        f"Context:\n{context_text[:12000]}"
    )
    req_body = {
        "model": model,
        "max_tokens": 900,
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
        SELECT e.id, e.event_id, e.source, e.occurred_at, e.subject_json
        FROM events e
        LEFT JOIN reddit_event_analysis a ON a.event_id = e.event_id
        WHERE e.source = 'reddit_discovery' AND e.delivered = 1 AND a.event_id IS NULL
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
    post_id: str,
    subreddit: str,
    title: str,
    url: str,
    author: str,
    score: int,
    num_comments: int,
    occurred_at: str,
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
        INSERT INTO reddit_event_analysis (
            event_id, event_db_id, source, post_id, subreddit, title, url, author,
            score, num_comments, occurred_at, category, summary_text, model_name,
            prompt_tokens, completion_tokens, total_tokens, analysis_json, analyzed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(event_id) DO UPDATE SET
            event_db_id=excluded.event_db_id,
            source=excluded.source,
            post_id=excluded.post_id,
            subreddit=excluded.subreddit,
            title=excluded.title,
            url=excluded.url,
            author=excluded.author,
            score=excluded.score,
            num_comments=excluded.num_comments,
            occurred_at=excluded.occurred_at,
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
            post_id,
            subreddit,
            title,
            url,
            author,
            max(0, int(score)),
            max(0, int(num_comments)),
            occurred_at,
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
    parser = argparse.ArgumentParser(description="CSI Reddit semantic enrichment")
    parser.add_argument("--db-path", default="/var/lib/universal-agent/csi/csi.db")
    parser.add_argument("--max-events", type=int, default=16)
    parser.add_argument("--env-file", default="/opt/universal_agent/.env")
    parser.add_argument(
        "--csi-env-file",
        default="/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env",
    )
    parser.add_argument("--claude-model", default="")
    parser.add_argument("--max-categories", type=int, default=12)
    args = parser.parse_args()

    db_path = Path(args.db_path).expanduser()
    _apply_env_defaults(Path(args.csi_env_file).expanduser())
    _apply_env_defaults(Path(args.env_file).expanduser())
    env_values = {**_load_env_file(Path(args.env_file).expanduser()), **_load_env_file(Path(args.csi_env_file).expanduser())}

    use_claude = _resolve_setting(["CSI_REDDIT_ANALYSIS_USE_CLAUDE"], env_values).strip().lower() in {"1", "true", "yes", "on"}
    auth = resolve_csi_llm_auth(env_values, default_base_url="https://api.anthropic.com")
    api_key = auth.api_key
    base_url = auth.base_url
    endpoint = f"{base_url}/v1/messages" if not base_url.endswith("/v1/messages") else base_url
    model = (
        args.claude_model.strip()
        or _resolve_setting(["CSI_REDDIT_ANALYSIS_CLAUDE_MODEL"], env_values).strip()
        or "claude-3-5-haiku-latest"
    )

    conn = connect(db_path)
    ensure_schema(conn)
    normalize_existing_analysis_categories(conn)

    rows = _select_pending(conn, max(1, int(args.max_events)))
    if not rows:
        print("REDDIT_ENRICH_PENDING=0")
        conn.close()
        return 0

    processed = 0
    claude_used = 0
    category_counts: Counter[str] = Counter()

    for row in rows:
        event_db_id = int(row["id"])
        event_id = str(row["event_id"])
        source = str(row["source"] or "reddit_discovery")
        occurred_at = str(row["occurred_at"] or "")

        try:
            subject = json.loads(str(row["subject_json"] or "{}"))
            if not isinstance(subject, dict):
                subject = {}
        except Exception:
            subject = {}

        post_id = str(subject.get("post_id") or "").strip()
        subreddit = str(subject.get("subreddit") or "").strip()
        title = str(subject.get("title") or "").strip()
        url = str(subject.get("url") or "").strip()
        author = str(subject.get("author") or "").strip()
        score = int(subject.get("score") or 0)
        num_comments = int(subject.get("num_comments") or 0)

        context_text = (
            f"subreddit={subreddit}\n"
            f"title={title}\n"
            f"author={author}\n"
            f"url={url}\n"
            f"score={score}\n"
            f"num_comments={num_comments}"
        )

        suggested_category = ""
        summary_text, themes, confidence, confidence_rationale = _fallback_summary(
            title=title,
            subreddit=subreddit,
            url=url,
            score=score,
            comments=num_comments,
        )
        analysis_json: dict[str, Any] = {
            "category": "other_interest",
            "themes": themes,
            "confidence": confidence,
            "confidence_rationale": confidence_rationale,
            "context_text": context_text,
        }
        model_name = ""
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0

        if use_claude and api_key:
            parsed, usage = _analyze_with_claude(
                title=title,
                subreddit=subreddit,
                author=author,
                url=url,
                score=score,
                num_comments=num_comments,
                context_text=context_text,
                model=model,
                endpoint=endpoint,
                api_key=api_key,
            )
            if parsed:
                parsed_category = str(parsed.get("category") or "").strip().lower()
                if parsed_category:
                    suggested_category = parsed_category
                summary_val = str(parsed.get("summary") or "").strip()
                if summary_val:
                    summary_text = summary_val[:2000]
                parsed_themes = parsed.get("themes")
                if isinstance(parsed_themes, list):
                    themes = [str(item).strip() for item in parsed_themes if str(item).strip()][:12]
                try:
                    confidence = float(parsed.get("confidence"))
                except Exception:
                    pass
                rationale_val = str(parsed.get("confidence_rationale") or "").strip()
                if rationale_val:
                    confidence_rationale = rationale_val[:260]
                analysis_json = {
                    "category": suggested_category or "other_interest",
                    "themes": themes,
                    "confidence": max(0.0, min(1.0, confidence)),
                    "confidence_rationale": confidence_rationale,
                    "claude": parsed,
                    "context_text": context_text,
                }
                if usage:
                    prompt_tokens = int(usage.get("prompt_tokens") or 0)
                    completion_tokens = int(usage.get("completion_tokens") or 0)
                    total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
                    model_name = model
                    claude_used += 1
                    token_usage_store.insert_usage(
                        conn,
                        process_name="reddit_semantic_enrich_claude",
                        model_name=model,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        metadata={
                            "event_id": event_id,
                            "post_id": post_id,
                            "subreddit": subreddit,
                            "category": suggested_category or "other_interest",
                        },
                    )

        category, taxonomy_state = classify_and_update_category(
            conn,
            suggested_category=suggested_category,
            title=title,
            channel_name=subreddit,
            summary_text=summary_text,
            transcript_text=context_text,
            themes=themes,
            confidence=confidence,
            max_categories=max(4, int(args.max_categories)),
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
            post_id=post_id,
            subreddit=subreddit,
            title=title,
            url=url,
            author=author,
            score=score,
            num_comments=num_comments,
            occurred_at=occurred_at,
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
    print(f"REDDIT_ENRICH_PENDING={len(rows)}")
    print(f"REDDIT_ENRICH_PROCESSED={processed}")
    print(f"REDDIT_ENRICH_CLAUDE_USED={claude_used}")
    for slug, count in sorted(category_counts.items()):
        metric = re.sub(r"[^A-Z0-9_]+", "_", slug.upper())
        print(f"REDDIT_ENRICH_CATEGORY_{metric}={int(count)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
