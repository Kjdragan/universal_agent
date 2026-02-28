#!/usr/bin/env python3
"""Generate CSI-native RSS insight reports and emit them to UA."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.request
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from csi_ingester.analytics import canonicalize_category, ensure_taxonomy_state, format_category_label
from csi_ingester.analytics.emission import emit_and_track
from csi_ingester.config import load_config
from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.llm_auth import resolve_csi_llm_auth
from csi_ingester.store import token_usage as token_usage_store
from csi_ingester.store.sqlite import connect, ensure_schema

CORE_ORDER = ("ai", "political", "war", "other_interest")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


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


def _aggregate_token_usage(conn: sqlite3.Connection, start_db: str, end_db: str) -> dict[str, Any]:
    row = conn.execute(
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
    by_process: list[dict[str, Any]] = []
    for item in conn.execute(
        """
        SELECT process_name, COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM token_usage
        WHERE occurred_at >= ? AND occurred_at < ?
        GROUP BY process_name
        ORDER BY total_tokens DESC, process_name ASC
        LIMIT 8
        """,
        (start_db, end_db),
    ).fetchall():
        by_process.append(
            {
                "process_name": str(item["process_name"] or ""),
                "total_tokens": int(item["total_tokens"] or 0),
            }
        )
    return {
        "records": int(row["records"] or 0),
        "prompt_tokens": int(row["prompt_tokens"] or 0),
        "completion_tokens": int(row["completion_tokens"] or 0),
        "total_tokens": int(row["total_tokens"] or 0),
        "by_process": by_process,
    }


def _collect_rows(conn: sqlite3.Connection, *, start_db: str, end_db: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            event_id,
            video_id,
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


def _parse_themes(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw or "{}")
    except Exception:
        return []
    if not isinstance(parsed, dict):
        return []
    themes = parsed.get("themes")
    if not isinstance(themes, list):
        return []
    out: list[str] = []
    for item in themes:
        label = str(item).strip().lower()
        if label:
            out.append(label)
    return out


def _build_window_data(
    conn: sqlite3.Connection,
    *,
    start_dt: datetime,
    end_dt: datetime,
    taxonomy_state: dict[str, Any],
) -> dict[str, Any]:
    start_db = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    end_db = end_dt.strftime("%Y-%m-%d %H:%M:%S")
    rows = _collect_rows(conn, start_db=start_db, end_db=end_db)
    by_category: Counter[str] = Counter()
    by_channel: Counter[str] = Counter()
    by_theme: Counter[str] = Counter()
    theme_examples: dict[str, list[str]] = defaultdict(list)
    samples: dict[str, list[dict[str, str]]] = defaultdict(list)

    for row in rows:
        category = canonicalize_category(str(row["category"] or ""), state=taxonomy_state)
        by_category[category] += 1
        channel_name = str(row["channel_name"] or "").strip()
        channel_id = str(row["channel_id"] or "").strip()
        channel_label = channel_name or channel_id or "unknown_channel"
        by_channel[channel_label] += 1
        title = str(row["title"] or "").strip()
        summary = str(row["summary_text"] or "").strip()
        for theme in _parse_themes(str(row["analysis_json"] or "{}")):
            by_theme[theme] += 1
            if len(theme_examples[theme]) < 3 and title:
                theme_examples[theme].append(title)
        samples[category].append(
            {
                "event_id": str(row["event_id"] or ""),
                "video_id": str(row["video_id"] or ""),
                "channel": channel_label,
                "title": title,
                "summary": summary[:480],
                "analyzed_at": str(row["analyzed_at"] or ""),
            }
        )

    token_usage = _aggregate_token_usage(conn, start_db=start_db, end_db=end_db)
    return {
        "window_start_utc": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_end_utc": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_items": len(rows),
        "by_category": dict(by_category),
        "top_channels": [{"channel": key, "count": count} for key, count in by_channel.most_common(16)],
        "top_themes": [{"theme": key, "count": count} for key, count in by_theme.most_common(20)],
        "top_narratives": [
            {"theme": key, "count": count, "examples": theme_examples.get(key, [])}
            for key, count in by_theme.most_common(10)
        ],
        "samples": {key: value[:24] for key, value in samples.items()},
        "token_usage": token_usage,
    }


def _build_watchlist_movers(
    current: dict[str, Any],
    previous: dict[str, Any],
) -> list[dict[str, Any]]:
    prev_lookup = {str(item.get("channel")): int(item.get("count") or 0) for item in previous.get("top_channels", [])}
    movers: list[dict[str, Any]] = []
    for item in current.get("top_channels", []):
        channel = str(item.get("channel") or "")
        current_count = int(item.get("count") or 0)
        previous_count = int(prev_lookup.get(channel) or 0)
        movers.append(
            {
                "channel": channel,
                "count": current_count,
                "previous_count": previous_count,
                "delta": current_count - previous_count,
            }
        )
    movers.sort(key=lambda item: (int(item["delta"]), int(item["count"])), reverse=True)
    return movers[:10]


def _build_fallback_markdown(report_type: str, report_data: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# CSI RSS Insight Report ({report_type})")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append(f"- Items analyzed: {int(report_data.get('total_items') or 0)}")
    by_category = report_data.get("by_category", {})
    if isinstance(by_category, dict):
        for slug in CORE_ORDER:
            lines.append(f"- {format_category_label(slug)}: {int(by_category.get(slug) or 0)}")
        extras = [
            (slug, int(count or 0))
            for slug, count in by_category.items()
            if slug not in CORE_ORDER and str(slug).strip()
        ]
        extras.sort(key=lambda item: item[1], reverse=True)
        if extras:
            lines.append("- Emerging categories: " + ", ".join(f"{format_category_label(slug)}={count}" for slug, count in extras[:8]))
    lines.append("")
    lines.append("## Watchlist Movers")
    for item in report_data.get("watchlist_movers", [])[:8]:
        lines.append(
            f"- {item.get('channel')}: {item.get('count')} (prev {item.get('previous_count')}, delta {item.get('delta'):+d})"
        )
    lines.append("")
    lines.append("## Top Narratives")
    for item in report_data.get("top_narratives", [])[:10]:
        lines.append(f"- {item.get('theme')}: {item.get('count')}")
    lines.append("")
    lines.append("## Token Usage Snapshot")
    token_usage = report_data.get("token_usage", {})
    if isinstance(token_usage, dict):
        lines.append(
            f"- Total: {int(token_usage.get('total_tokens') or 0)} (prompt={int(token_usage.get('prompt_tokens') or 0)}, completion={int(token_usage.get('completion_tokens') or 0)})"
        )
        for item in token_usage.get("by_process", [])[:8]:
            if isinstance(item, dict):
                lines.append(f"- {item.get('process_name')}: {item.get('total_tokens')}")
    lines.append("")
    lines.append("## Notable Recent Videos")
    for slug in CORE_ORDER:
        bucket = report_data.get("samples", {}).get(slug, [])
        if not bucket:
            continue
        lines.append(f"### {format_category_label(slug)}")
        for sample in bucket[:4]:
            if isinstance(sample, dict):
                lines.append(f"- {sample.get('channel')}: {sample.get('title')}")
    return "\n".join(lines).strip()[:48000]


def _claude_markdown(
    *,
    report_type: str,
    report_data: dict[str, Any],
    model: str,
    endpoint: str,
    api_key: str,
) -> tuple[str | None, dict[str, int]]:
    prompt = (
        "Produce a concise CSI analyst report in markdown.\n"
        "Required sections: Executive Summary, Category Signals, Watchlist Movers, "
        "Top Narratives, Token Usage Snapshot, Suggested Next Actions.\n"
        "Keep under 1800 words.\n\n"
        f"Report Type: {report_type}\n"
        f"Data JSON:\n{json.dumps(report_data, ensure_ascii=False)}"
    )
    req_body = {
        "model": model,
        "max_tokens": 1100,
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
    markdown = "\n\n".join(part for part in parts if part).strip()
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
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
    return markdown[:48000], usage


def _save_insight_report(
    conn: sqlite3.Connection,
    *,
    report_key: str,
    report_type: str,
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
            report_type,
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


def _emit_report_event(
    conn: sqlite3.Connection,
    *,
    config,
    report_type: str,
    report_key: str,
    report_data: dict[str, Any],
    markdown: str,
    usage: dict[str, int],
) -> tuple[bool, int, dict[str, Any]]:
    now_iso = _utc_now_iso()
    event = CreatorSignalEvent(
        event_id=f"csi:rss_insight:{report_type}:{config.instance_id}:{uuid.uuid4().hex[:10]}",
        dedupe_key=report_key,
        source="csi_analytics",
        event_type=f"rss_insight_{report_type}",
        occurred_at=now_iso,
        received_at=now_iso,
        subject={
            "report_type": f"rss_insight_{report_type}",
            "report_key": report_key,
            "window_start_utc": report_data.get("window_start_utc"),
            "window_end_utc": report_data.get("window_end_utc"),
            "total_items": int(report_data.get("total_items") or 0),
            "by_category": report_data.get("by_category", {}),
            "watchlist_movers": report_data.get("watchlist_movers", []),
            "top_narratives": report_data.get("top_narratives", []),
            "token_usage": report_data.get("token_usage", {}),
            "llm_usage": usage,
            "markdown": markdown,
        },
        routing={
            "pipeline": "csi_rss_insight_analytics",
            "priority": "standard",
            "tags": ["csi", "insight", report_type, "rss"],
        },
        metadata={"source_adapter": "csi_rss_insight_analyst_v1", "report_key": report_key},
    )
    return emit_and_track(conn, config=config, event=event, retry_count=3)


def _run_one_report(
    conn: sqlite3.Connection,
    *,
    config,
    taxonomy_state: dict[str, Any],
    report_type: str,
    window_hours: int,
    use_claude: bool,
    api_key: str,
    model: str,
    endpoint: str,
    report_suffix: str,
) -> tuple[bool, str, int]:
    end_dt = datetime.now(timezone.utc).replace(microsecond=0)
    start_dt = end_dt - timedelta(hours=max(1, int(window_hours)))
    prev_end_dt = start_dt
    prev_start_dt = prev_end_dt - timedelta(hours=max(1, int(window_hours)))

    report_data = _build_window_data(conn, start_dt=start_dt, end_dt=end_dt, taxonomy_state=taxonomy_state)
    previous_data = _build_window_data(conn, start_dt=prev_start_dt, end_dt=prev_end_dt, taxonomy_state=taxonomy_state)
    report_data["watchlist_movers"] = _build_watchlist_movers(report_data, previous_data)
    report_data["window_hours"] = int(window_hours)
    report_data["comparison_previous_window_start_utc"] = prev_start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    report_data["comparison_previous_window_end_utc"] = prev_end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    if int(report_data.get("total_items") or 0) == 0:
        return False, "no_data", 0

    markdown = _build_fallback_markdown(report_type, report_data)
    usage: dict[str, int] = {}
    model_name = ""
    if use_claude and api_key:
        polished, usage = _claude_markdown(
            report_type=report_type,
            report_data=report_data,
            model=model,
            endpoint=endpoint,
            api_key=api_key,
        )
        if polished:
            markdown = polished
            model_name = model
        if usage:
            token_usage_store.insert_usage(
                conn,
                process_name="rss_insight_analyst_claude",
                model_name=model,
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                total_tokens=int(usage.get("total_tokens") or 0),
                metadata={"report_type": report_type, "window_hours": int(window_hours)},
            )

    report_key = f"rss_insight:{report_type}:{config.instance_id}:{report_suffix}"
    _save_insight_report(
        conn,
        report_key=report_key,
        report_type=report_type,
        report_data=report_data,
        markdown=markdown,
        model_name=model_name,
        usage=usage,
    )
    delivered, status_code, _payload = _emit_report_event(
        conn,
        config=config,
        report_type=report_type,
        report_key=report_key,
        report_data=report_data,
        markdown=markdown,
        usage=usage,
    )
    if delivered or status_code == 409:
        return True, report_key, int(report_data.get("total_items") or 0)
    return False, report_key, int(report_data.get("total_items") or 0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate CSI RSS insight reports.")
    parser.add_argument("--db-path", default="/opt/universal_agent/CSI_Ingester/development/var/csi.db")
    parser.add_argument(
        "--state-path",
        default="/opt/universal_agent/CSI_Ingester/development/var/rss_insight_analyst_state.json",
    )
    parser.add_argument("--daily-window-hours", type=int, default=24)
    parser.add_argument("--emerging-window-hours", type=int, default=6)
    parser.add_argument("--disable-daily", action="store_true")
    parser.add_argument("--disable-emerging", action="store_true")
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
    taxonomy_state = ensure_taxonomy_state(conn)

    use_claude = _resolve_setting(["CSI_RSS_INSIGHT_USE_CLAUDE"], env_file_values).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    auth = resolve_csi_llm_auth(env_file_values, default_base_url="https://api.anthropic.com")
    api_key = auth.api_key
    model = _resolve_setting(["CSI_RSS_INSIGHT_CLAUDE_MODEL"], env_file_values) or "claude-3-5-haiku-latest"
    base_url = auth.base_url
    endpoint = f"{base_url}/v1/messages" if not base_url.endswith("/v1/messages") else base_url

    now = datetime.now(timezone.utc).replace(microsecond=0)
    hour_key = now.strftime("%Y-%m-%dT%H:00:00Z")
    day_key = now.strftime("%Y-%m-%d")
    state_path = Path(args.state_path).expanduser()
    state = _load_state(state_path)

    sent_daily = False
    sent_emerging = False

    if not args.disable_emerging and (args.force or state.get("last_emerging_hour_key") != hour_key):
        ok, ref, items = _run_one_report(
            conn,
            config=config,
            taxonomy_state=taxonomy_state,
            report_type="emerging",
            window_hours=max(1, int(args.emerging_window_hours)),
            use_claude=use_claude,
            api_key=api_key,
            model=model,
            endpoint=endpoint,
            report_suffix=hour_key,
        )
        sent_emerging = ok
        state["last_emerging_hour_key"] = hour_key
        state["last_emerging_ref"] = ref
        state["last_emerging_items"] = int(items)

    if not args.disable_daily and (args.force or state.get("last_daily_day_key") != day_key):
        ok, ref, items = _run_one_report(
            conn,
            config=config,
            taxonomy_state=taxonomy_state,
            report_type="daily",
            window_hours=max(1, int(args.daily_window_hours)),
            use_claude=use_claude,
            api_key=api_key,
            model=model,
            endpoint=endpoint,
            report_suffix=day_key,
        )
        sent_daily = ok
        state["last_daily_day_key"] = day_key
        state["last_daily_ref"] = ref
        state["last_daily_items"] = int(items)

    state["updated_at"] = _utc_now_iso()
    _save_state(state_path, state)
    conn.close()

    print(f"RSS_INSIGHT_SENT_DAILY={1 if sent_daily else 0}")
    print(f"RSS_INSIGHT_SENT_EMERGING={1 if sent_emerging else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
