#!/usr/bin/env python3
"""Generate Threads trend narrative report and emit CSI analytics event."""

from __future__ import annotations

import argparse
import json
import os
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


def _window(hours: int) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(hours=max(1, int(hours)))
    return start, end


def _fetch_rows(conn: sqlite3.Connection, start_db: str, end_db: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM threads_event_analysis
        WHERE analyzed_at >= ? AND analyzed_at < ?
        ORDER BY analyzed_at DESC, id DESC
        """,
        (start_db, end_db),
    ).fetchall()


def _report_data(rows: list[sqlite3.Row], start_dt: datetime, end_dt: datetime) -> dict[str, Any]:
    by_bucket: Counter[str] = Counter()
    by_source: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    top_terms: Counter[str] = Counter()
    top_users: Counter[str] = Counter()
    theme_counter: Counter[str] = Counter()
    sample_items: list[dict[str, Any]] = []

    for row in rows:
        source = str(row["source"] or "unknown")
        bucket = str(row["trend_bucket"] or ("owned" if source == "threads_owned" else "unknown")).lower()
        category = str(row["category"] or "other_interest").lower()
        query_term = str(row["query_term"] or "").strip().lower()
        username = str(row["username"] or "").strip().lower()

        by_source[source] += 1
        by_bucket[bucket] += 1
        by_category[category] += 1
        if query_term:
            top_terms[query_term] += 1
        if username:
            top_users[username] += 1

        analysis_json = {}
        try:
            analysis_json = json.loads(str(row["analysis_json"] or "{}"))
        except Exception:
            analysis_json = {}
        if isinstance(analysis_json, dict):
            raw_themes = analysis_json.get("themes")
            if isinstance(raw_themes, list):
                for theme in raw_themes[:12]:
                    label = str(theme or "").strip().lower()
                    if label:
                        theme_counter[label] += 1

        if len(sample_items) < 8:
            sample_items.append(
                {
                    "source": source,
                    "bucket": bucket,
                    "category": category,
                    "query_term": query_term,
                    "username": username,
                    "summary": str(row["summary_text"] or "")[:500],
                    "permalink": str(row["permalink"] or ""),
                    "timestamp": str(row["timestamp"] or ""),
                }
            )

    return {
        "window_start_utc": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_end_utc": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_items": len(rows),
        "top_buckets": [{"bucket": k, "count": int(v)} for k, v in by_bucket.most_common(6)],
        "top_sources": [{"source": k, "count": int(v)} for k, v in by_source.most_common(6)],
        "top_categories": [{"category": k, "count": int(v)} for k, v in by_category.most_common(8)],
        "top_terms": [{"term": k, "count": int(v)} for k, v in top_terms.most_common(12)],
        "top_users": [{"username": k, "count": int(v)} for k, v in top_users.most_common(10)],
        "top_themes": [{"theme": k, "count": int(v)} for k, v in theme_counter.most_common(12)],
        "sample_items": sample_items,
    }


def _default_markdown(report_data: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# CSI Threads Trend Report ({report_data.get('window_start_utc')} -> {report_data.get('window_end_utc')})")
    lines.append("")
    lines.append(f"Total analyzed items: **{int(report_data.get('total_items') or 0)}**")
    lines.append("")

    lines.append("## Trend Bucket Mix")
    for row in report_data.get("top_buckets", []):
        lines.append(f"- {row.get('bucket')}: {int(row.get('count') or 0)}")
    lines.append("")

    lines.append("## Top Terms")
    for row in report_data.get("top_terms", [])[:10]:
        lines.append(f"- {row.get('term')}: {int(row.get('count') or 0)}")
    lines.append("")

    lines.append("## Top Themes")
    for row in report_data.get("top_themes", [])[:10]:
        lines.append(f"- {row.get('theme')}: {int(row.get('count') or 0)}")
    lines.append("")

    lines.append("## Top Users")
    for row in report_data.get("top_users", [])[:8]:
        lines.append(f"- @{row.get('username')}: {int(row.get('count') or 0)}")
    lines.append("")

    lines.append("## Evidence Samples")
    for item in report_data.get("sample_items", [])[:6]:
        summary = str(item.get("summary") or "").strip() or "(no summary)"
        link = str(item.get("permalink") or "").strip()
        prefix = f"[{item.get('bucket')}/{item.get('category')}]"
        lines.append(f"- {prefix} {summary}")
        if link:
            lines.append(f"  - {link}")
    lines.append("")

    lines.append("## Why It Matters")
    lines.append("Threads seeded + broad signals are now summarized into a narrative artifact for direct review in CSI.")
    return "\n".join(lines)


def _claude_markdown(
    *,
    report_data: dict[str, Any],
    model: str,
    endpoint: str,
    api_key: str,
) -> tuple[str | None, dict[str, int]]:
    prompt = (
        "You are producing a concise, decision-ready trend briefing.\n"
        "Write markdown with sections: Executive Summary, Top Narratives, What Changed, Risks/Contradictions, Evidence Links.\n"
        "Use plain language and include confidence rationale.\n\n"
        f"Input JSON:\n{json.dumps(report_data, ensure_ascii=False)}"
    )
    req_body = {
        "model": model,
        "max_tokens": 1400,
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

    text_parts: list[str] = []
    for block in payload.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(str(block.get("text") or ""))
    text = "\n".join(text_parts).strip() or None

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
    return text, usage


def _save_artifacts(artifacts_root: Path, report_key: str, markdown: str, payload: dict[str, Any]) -> tuple[str, str]:
    directory = artifacts_root / "csi" / "threads_trend_reports"
    directory.mkdir(parents=True, exist_ok=True)
    md_path = directory / f"{report_key}.md"
    json_path = directory / f"{report_key}.json"
    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(md_path), str(json_path)


def _insert_report(
    conn: sqlite3.Connection,
    *,
    report_key: str,
    window_start_utc: str,
    window_end_utc: str,
    markdown: str,
    report_json: dict[str, Any],
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
) -> None:
    conn.execute(
        """
        INSERT INTO insight_reports (
            report_key, report_type, window_start_utc, window_end_utc,
            model_name, prompt_tokens, completion_tokens, total_tokens,
            report_markdown, report_json, created_at
        ) VALUES (?, 'threads_trend_report', ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(report_key) DO UPDATE SET
            report_type='threads_trend_report',
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
            window_start_utc,
            window_end_utc,
            model_name or None,
            max(0, int(prompt_tokens)),
            max(0, int(completion_tokens)),
            max(0, int(total_tokens)),
            markdown,
            json.dumps(report_json, separators=(",", ":"), sort_keys=True),
        ),
    )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate CSI Threads trend narrative report.")
    parser.add_argument("--db-path", default="/var/lib/universal-agent/csi/csi.db")
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--config-path", default="/opt/universal_agent/CSI_Ingester/development/config/config.yaml")
    parser.add_argument("--env-file", default="/opt/universal_agent/.env")
    parser.add_argument(
        "--csi-env-file",
        default="/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env",
    )
    parser.add_argument("--claude-model", default="")
    parser.add_argument("--artifacts-root", default="/opt/universal_agent/artifacts")
    args = parser.parse_args()

    db_path = Path(args.db_path).expanduser()
    _apply_env_defaults(Path(args.csi_env_file).expanduser())
    _apply_env_defaults(Path(args.env_file).expanduser())
    env_values = {**_load_env_file(Path(args.env_file).expanduser()), **_load_env_file(Path(args.csi_env_file).expanduser())}

    use_claude = _resolve_setting(["CSI_THREADS_TREND_USE_CLAUDE"], env_values).strip().lower() in {"1", "true", "yes", "on"}
    auth = resolve_csi_llm_auth(env_values, default_base_url="https://api.anthropic.com")
    api_key = auth.api_key
    base_url = auth.base_url
    endpoint = f"{base_url}/v1/messages" if not base_url.endswith("/v1/messages") else base_url
    model = (
        args.claude_model.strip()
        or _resolve_setting(["CSI_THREADS_TREND_CLAUDE_MODEL"], env_values).strip()
        or "claude-3-5-haiku-latest"
    )

    start_dt, end_dt = _window(args.window_hours)
    start_db = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    end_db = end_dt.strftime("%Y-%m-%d %H:%M:%S")

    conn = connect(db_path)
    ensure_schema(conn)

    rows = _fetch_rows(conn, start_db, end_db)
    report_data = _report_data(rows, start_dt, end_dt)
    if int(report_data.get("total_items") or 0) <= 0:
        print("THREADS_TREND_REPORT_SKIPPED=no_rows")
        conn.close()
        return 0

    markdown = _default_markdown(report_data)
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    model_name = ""
    if use_claude and api_key:
        generated, usage = _claude_markdown(
            report_data=report_data,
            model=model,
            endpoint=endpoint,
            api_key=api_key,
        )
        if generated:
            markdown = generated
            model_name = model
            if usage:
                prompt_tokens = int(usage.get("prompt_tokens") or 0)
                completion_tokens = int(usage.get("completion_tokens") or 0)
                total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
                token_usage_store.insert_usage(
                    conn,
                    process_name="threads_trend_report_claude",
                    model_name=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    metadata={
                        "window_hours": int(args.window_hours),
                        "total_items": int(report_data.get("total_items") or 0),
                    },
                )

    report_key = f"threads_trend_report:{end_dt.strftime('%Y%m%dT%H%M%SZ')}"
    artifact_md, artifact_json = _save_artifacts(
        Path(args.artifacts_root).expanduser(),
        report_key,
        markdown,
        {
            "report_key": report_key,
            "report_type": "threads_trend_report",
            "window_start_utc": report_data.get("window_start_utc"),
            "window_end_utc": report_data.get("window_end_utc"),
            "report_data": report_data,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
        },
    )

    report_data["report_key"] = report_key
    report_data["artifact_paths"] = {"markdown": artifact_md, "json": artifact_json}
    _insert_report(
        conn,
        report_key=report_key,
        window_start_utc=str(report_data.get("window_start_utc") or start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")),
        window_end_utc=str(report_data.get("window_end_utc") or end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")),
        markdown=markdown,
        report_json=report_data,
        model_name=model_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )

    cfg = load_config(Path(args.config_path).expanduser())
    event = CreatorSignalEvent(
        event_id=f"csi_threads_trend_report:{report_key}",
        dedupe_key=f"csi:threads_trend_report:{report_key}",
        source="csi_analytics",
        event_type="threads_trend_report",
        occurred_at=end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        received_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        subject={
            "report_key": report_key,
            "report_type": "threads_trend_report",
            "window_hours": int(args.window_hours),
            "window_start_utc": report_data.get("window_start_utc"),
            "window_end_utc": report_data.get("window_end_utc"),
            "total_items": int(report_data.get("total_items") or 0),
            "top_buckets": report_data.get("top_buckets") or [],
            "top_terms": report_data.get("top_terms") or [],
            "top_themes": report_data.get("top_themes") or [],
            "artifact_paths": {"markdown": artifact_md, "json": artifact_json},
            "markdown_preview": markdown[:1800],
        },
        routing={"pipeline": "csi_analytics", "priority": "high", "tags": ["csi", "threads", "trend"]},
        metadata={
            "report_key": report_key,
            "artifact_paths": {"markdown": artifact_md, "json": artifact_json},
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
        },
    )

    delivered, status_code, _ = emit_and_track(conn, config=cfg, event=event, retry_count=3)
    conn.close()

    print(f"THREADS_TREND_REPORT_KEY={report_key}")
    print(f"THREADS_TREND_REPORT_ITEMS={int(report_data.get('total_items') or 0)}")
    print(f"THREADS_TREND_REPORT_ARTIFACT_MD={artifact_md}")
    print(f"THREADS_TREND_REPORT_EMIT_DELIVERED={1 if delivered else 0}")
    print(f"THREADS_TREND_REPORT_EMIT_STATUS={int(status_code or 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
