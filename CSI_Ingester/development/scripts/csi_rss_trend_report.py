#!/usr/bin/env python3
"""Generate and emit periodic RSS trend reports to UA."""

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

from csi_ingester.analytics.categories import (
    canonicalize_category,
    ensure_taxonomy_state,
    format_category_label,
)
from csi_ingester.config import load_config
from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.emitter.ua_client import UAEmitter
from csi_ingester.llm_auth import resolve_csi_llm_auth
from csi_ingester.store import dlq as dlq_store
from csi_ingester.store import events as event_store
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
    # Rolling window up to "now" so newly analyzed events are included immediately.
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(hours=max(1, hours))
    return start, end


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
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


def _build_report_data(
    conn: sqlite3.Connection,
    start_db: str,
    end_db: str,
    taxonomy_state: dict[str, Any],
    *,
    start_dt: datetime,
    end_dt: datetime,
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT
            event_id,
            channel_id,
            channel_name,
            title,
            category,
            summary_text,
            analysis_json,
            analyzed_at
        FROM rss_event_analysis
        WHERE analyzed_at >= ? AND analyzed_at < ?
        ORDER BY analyzed_at DESC, id DESC
        """,
        (start_db, end_db),
    ).fetchall()

    by_category: dict[str, int] = Counter()
    by_channel: Counter[str] = Counter()
    theme_counter: Counter[str] = Counter()
    theme_examples: dict[str, list[str]] = defaultdict(list)
    category_summaries: dict[str, list[dict[str, str]]] = defaultdict(list)

    for row in rows:
        category = canonicalize_category(str(row["category"] or ""), state=taxonomy_state)
        by_category[category] += 1
        channel_name = str(row["channel_name"] or "").strip()
        channel_id = str(row["channel_id"] or "").strip()
        channel_label = channel_name or channel_id or "unknown_channel"
        by_channel[channel_label] += 1

        try:
            analysis_payload = json.loads(str(row["analysis_json"] or "{}"))
            if not isinstance(analysis_payload, dict):
                analysis_payload = {}
        except Exception:
            analysis_payload = {}

        themes = analysis_payload.get("themes")
        if isinstance(themes, list):
            for theme in themes:
                label = str(theme).strip().lower()
                if label:
                    theme_counter[label] += 1
                    if len(theme_examples[label]) < 3 and str(row["title"] or "").strip():
                        theme_examples[label].append(str(row["title"] or "").strip())

        category_summaries[category].append(
            {
                "event_id": str(row["event_id"] or ""),
                "channel": channel_label,
                "title": str(row["title"] or ""),
                "summary": str(row["summary_text"] or "")[:700],
                "analyzed_at": str(row["analyzed_at"] or ""),
            }
        )

    top_channels = [{"channel": label, "count": count} for label, count in by_channel.most_common(12)]
    top_themes = [{"theme": label, "count": count} for label, count in theme_counter.most_common(20)]
    top_narratives = [
        {"theme": label, "count": count, "examples": theme_examples.get(label, [])}
        for label, count in theme_counter.most_common(12)
    ]

    prev_window = end_dt - start_dt
    prev_start_dt = start_dt - prev_window
    prev_end_dt = start_dt
    prev_start_db = prev_start_dt.strftime("%Y-%m-%d %H:%M:%S")
    prev_end_db = prev_end_dt.strftime("%Y-%m-%d %H:%M:%S")
    prev_channels = {
        str(item["channel_name"] or item["channel_id"] or "unknown_channel"): int(item["c"] or 0)
        for item in conn.execute(
            """
            SELECT
                channel_name,
                channel_id,
                COUNT(*) AS c
            FROM rss_event_analysis
            WHERE analyzed_at >= ? AND analyzed_at < ?
            GROUP BY channel_name, channel_id
            """,
            (prev_start_db, prev_end_db),
        ).fetchall()
    }
    watchlist_movers: list[dict[str, Any]] = []
    for item in top_channels:
        channel = str(item.get("channel") or "")
        count = int(item.get("count") or 0)
        prev_count = int(prev_channels.get(channel) or 0)
        watchlist_movers.append(
            {
                "channel": channel,
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
        "top_channels": top_channels,
        "top_themes": top_themes,
        "top_narratives": top_narratives,
        "watchlist_movers": watchlist_movers[:10],
        "token_usage_snapshot": {
            "records": int(token_usage["records"] or 0),
            "prompt_tokens": int(token_usage["prompt_tokens"] or 0),
            "completion_tokens": int(token_usage["completion_tokens"] or 0),
            "total_tokens": int(token_usage["total_tokens"] or 0),
            "by_process": token_usage_by_process,
        },
        "samples": {k: v[:30] for k, v in category_summaries.items()},
    }


def _fallback_markdown(report_data: dict[str, Any], start_iso: str, end_iso: str) -> str:
    lines: list[str] = []
    lines.append(f"# CSI RSS Trend Report ({start_iso} -> {end_iso})")
    lines.append("")
    lines.append("## Totals")
    lines.append(f"- Items analyzed: {int(report_data.get('total_items') or 0)}")
    by_category = report_data.get("by_category", {})
    if isinstance(by_category, dict):
        lines.append(f"- AI items: {int(by_category.get('ai') or 0)}")
        lines.append(f"- Political items: {int(by_category.get('political') or 0)}")
        lines.append(f"- War items: {int(by_category.get('war') or 0)}")
        lines.append(f"- Other-interest items: {int(by_category.get('other_interest') or 0)}")
    lines.append("")
    lines.append("## Top Channels")
    for item in report_data.get("top_channels", [])[:10]:
        if isinstance(item, dict):
            lines.append(f"- {item.get('channel')}: {item.get('count')}")
    lines.append("")
    lines.append("## Top Themes")
    for item in report_data.get("top_themes", [])[:12]:
        if isinstance(item, dict):
            lines.append(f"- {item.get('theme')}: {item.get('count')}")
    lines.append("")
    lines.append("## Watchlist Movers")
    for item in report_data.get("watchlist_movers", [])[:8]:
        if isinstance(item, dict):
            lines.append(
                f"- {item.get('channel')}: {item.get('count')} (prev {item.get('previous_count')}, delta {int(item.get('delta') or 0):+d})"
            )
    lines.append("")
    lines.append("## Top Narratives")
    for item in report_data.get("top_narratives", [])[:10]:
        if isinstance(item, dict):
            lines.append(f"- {item.get('theme')}: {item.get('count')}")
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
    lines.append("")
    lines.append("## AI Samples")
    for sample in report_data.get("samples", {}).get("ai", [])[:6]:
        if isinstance(sample, dict):
            lines.append(f"- {sample.get('channel')}: {sample.get('title')}")
    lines.append("")
    lines.append("## Political Samples")
    for sample in report_data.get("samples", {}).get("political", [])[:6]:
        if isinstance(sample, dict):
            lines.append(f"- {sample.get('channel')}: {sample.get('title')}")
    lines.append("")
    lines.append("## War Samples")
    for sample in report_data.get("samples", {}).get("war", [])[:6]:
        if isinstance(sample, dict):
            lines.append(f"- {sample.get('channel')}: {sample.get('title')}")
    lines.append("")
    lines.append("## Other Interest Samples")
    for sample in report_data.get("samples", {}).get("other_interest", [])[:6]:
        if isinstance(sample, dict):
            lines.append(f"- {sample.get('channel')}: {sample.get('title')}")

    extra: list[tuple[str, int]] = []
    if isinstance(by_category, dict):
        for slug, count in by_category.items():
            key = str(slug).strip()
            if key and key not in {"ai", "political", "war", "other_interest"}:
                extra.append((key, int(count or 0)))
    extra.sort(key=lambda item: item[1], reverse=True)
    if extra:
        lines.append("")
        lines.append("## Emerging Categories")
        for slug, count in extra[:8]:
            lines.append(f"- {format_category_label(slug)}: {count}")
        for slug, _count in extra[:3]:
            lines.append("")
            lines.append(f"### {format_category_label(slug)} Samples")
            for sample in report_data.get("samples", {}).get(slug, [])[:5]:
                if isinstance(sample, dict):
                    lines.append(f"- {sample.get('channel')}: {sample.get('title')}")
    return "\n".join(lines).strip()


def _claude_trend_markdown(
    *,
    report_data: dict[str, Any],
    start_iso: str,
    end_iso: str,
    api_key: str,
    model: str,
    endpoint: str,
) -> tuple[str | None, dict[str, int]]:
    prompt = (
        "Produce a concise trend report in markdown for monitored YouTube uploads.\n"
        "Focus on meaningful shifts and recurring narratives.\n"
        "Sections required: Executive Summary, AI Trend Signals, Political Trend Signals, "
        "War Trend Signals, Other Interest Signals, Emerging Categories, Watchlist Movers, "
        "Top Narratives, Token Usage Snapshot, Watch Items.\n"
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


def _save_report(conn: sqlite3.Connection, *, report_key: str, start_iso: str, end_iso: str, model_name: str, usage: dict[str, int], markdown: str, report_data: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO trend_reports (
            report_key, window_start_utc, window_end_utc, model_name,
            prompt_tokens, completion_tokens, total_tokens, report_markdown, report_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_key) DO UPDATE SET
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
            start_iso,
            end_iso,
            model_name or None,
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
            int(usage.get("total_tokens") or 0),
            markdown,
            json.dumps(report_data, separators=(",", ":"), sort_keys=True),
        ),
    )
    conn.commit()


def _emit_to_ua(event: CreatorSignalEvent, config) -> tuple[bool, int, dict[str, Any]]:
    import asyncio

    if not config.ua_endpoint or not config.ua_shared_secret:
        return False, 503, {"error": "ua_delivery_not_configured"}
    emitter = UAEmitter(
        endpoint=config.ua_endpoint,
        shared_secret=config.ua_shared_secret,
        instance_id=config.instance_id,
    )
    return asyncio.run(emitter.emit_with_retries([event], max_attempts=3))


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate RSS trend report and emit to UA.")
    parser.add_argument("--db-path", default="/opt/universal_agent/CSI_Ingester/development/var/csi.db")
    parser.add_argument("--env-file", default="/opt/universal_agent/.env")
    parser.add_argument(
        "--csi-env-file",
        default="/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env",
    )
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--state-path", default="/opt/universal_agent/CSI_Ingester/development/var/rss_trend_report_state.json")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    _apply_env_defaults(Path(args.csi_env_file).expanduser())
    _apply_env_defaults(Path(args.env_file).expanduser())
    env_file_values = _load_env_file(Path(args.env_file).expanduser())
    conn = connect(Path(args.db_path).expanduser())
    ensure_schema(conn)
    taxonomy_state = ensure_taxonomy_state(conn)
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
        print(f"RSS_TREND_REPORT_SKIPPED hour={hour_key}")
        conn.close()
        return 0

    report_data = _build_report_data(
        conn,
        start_db,
        end_db,
        taxonomy_state,
        start_dt=start_dt,
        end_dt=end_dt,
    )
    total_items = int(report_data.get("total_items") or 0)
    if total_items == 0:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"last_hour_key": hour_key, "last_status": "no_data"}, indent=2), encoding="utf-8")
        print("RSS_TREND_REPORT_NO_DATA=1")
        conn.close()
        return 0

    use_claude = _resolve_setting(["CSI_RSS_TREND_USE_CLAUDE"], env_file_values).strip().lower() in {"1", "true", "yes", "on"}
    auth = resolve_csi_llm_auth(env_file_values, default_base_url="https://api.anthropic.com")
    api_key = auth.api_key
    model = _resolve_setting(["CSI_RSS_TREND_CLAUDE_MODEL"], env_file_values) or "claude-3-5-haiku-latest"
    base_url = auth.base_url
    endpoint = f"{base_url}/v1/messages" if not base_url.endswith("/v1/messages") else base_url

    markdown = _fallback_markdown(report_data, start_iso, end_iso)
    usage: dict[str, int] = {}
    model_name = ""
    if use_claude and api_key:
        claude_markdown, claude_usage = _claude_trend_markdown(
            report_data=report_data,
            start_iso=start_iso,
            end_iso=end_iso,
            api_key=api_key,
            model=model,
            endpoint=endpoint,
        )
        if claude_markdown:
            markdown = claude_markdown
            usage = claude_usage
            model_name = model
            token_usage_store.insert_usage(
                conn,
                process_name="rss_trend_report_claude",
                model_name=model,
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                total_tokens=int(usage.get("total_tokens") or 0),
                metadata={"window_start_utc": start_iso, "window_end_utc": end_iso, "items": total_items},
            )

    report_key = f"rss_trend_report:{config.instance_id}:{hour_key}"
    _save_report(
        conn,
        report_key=report_key,
        start_iso=start_iso,
        end_iso=end_iso,
        model_name=model_name,
        usage=usage,
        markdown=markdown,
        report_data=report_data,
    )

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    event = CreatorSignalEvent(
        event_id=f"csi:rss_trend_report:{config.instance_id}:{end_dt.strftime('%Y%m%d%H')}",
        dedupe_key=f"csi:rss_trend_report:{config.instance_id}:{end_dt.strftime('%Y%m%d%H')}",
        source="csi_analytics",
        event_type="rss_trend_report",
        occurred_at=end_iso,
        received_at=now_iso,
        subject={
            "report_type": "rss_trend_report",
            "window_start_utc": start_iso,
            "window_end_utc": end_iso,
            "totals": {
                "items": total_items,
                "by_category": report_data.get("by_category", {}),
            },
            "top_channels": report_data.get("top_channels", []),
            "top_themes": report_data.get("top_themes", []),
            "top_narratives": report_data.get("top_narratives", []),
            "watchlist_movers": report_data.get("watchlist_movers", []),
            "token_usage_snapshot": report_data.get("token_usage_snapshot", {}),
            "markdown": markdown,
            "token_usage": usage,
        },
        routing={"pipeline": "csi_rss_trend_analytics", "priority": "standard", "tags": ["csi", "trend_report", "rss"]},
        metadata={"source_adapter": "csi_rss_trend_report_v1", "report_key": report_key},
    )

    event_store.insert_event(conn, event)
    delivered, status_code, payload = _emit_to_ua(event, config)
    if delivered:
        event_store.mark_delivered(conn, event.event_id)
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
        print(f"RSS_TREND_REPORT_SENT hour={hour_key} status={status_code} items={total_items}")
        return 0

    dlq_store.enqueue(
        conn,
        event_id=event.event_id,
        event=event.model_dump(),
        error_reason=f"ua_status_{status_code}",
        retry_count=3,
    )
    conn.close()
    print(f"RSS_TREND_REPORT_FAILED hour={hour_key} status={status_code} payload={payload}")
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
