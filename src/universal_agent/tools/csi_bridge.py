from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from claude_agent_sdk import tool

from universal_agent.utils.session_workspace import (
    build_interim_work_product_paths,
    resolve_current_session_workspace,
    safe_slug,
    write_json,
)

_DEFAULT_CSI_DB_PATH = "/opt/universal_agent/CSI_Ingester/development/var/csi.db"
_DEFAULT_YT_WATCHLIST = "/opt/universal_agent/CSI_Ingester/development/channels_watchlist.json"
_DEFAULT_REDDIT_WATCHLIST = "/opt/universal_agent/CSI_Ingester/development/reddit_watchlist.json"


def _ok(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=True)}]}


def _err(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": f"error: {message}"}]}


def _db_path() -> Path:
    return Path(os.getenv("CSI_DB_PATH", _DEFAULT_CSI_DB_PATH))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _loads_obj(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    text = str(value).strip()
    if not text:
        return default
    try:
        parsed = json.loads(text)
    except Exception:
        return default
    return parsed


def _read_watchlist(path_str: str, kind: str) -> Dict[str, Any]:
    path = Path(path_str).expanduser()
    out: Dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "count": 0,
        "preview": [],
    }
    if not path.exists():
        return out
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        out["error"] = f"parse_failed: {exc}"
        return out

    items: List[Any] = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get("watchlist"), list):
            items = payload.get("watchlist") or []
        elif isinstance(payload.get("channels"), list):
            items = payload.get("channels") or []
        elif isinstance(payload.get("subreddits"), list):
            items = payload.get("subreddits") or []

    normalized: List[str] = []
    for item in items:
        if isinstance(item, str):
            cleaned = item.strip()
        elif isinstance(item, dict):
            if kind == "youtube":
                cleaned = str(item.get("channel_id") or item.get("channel") or item.get("id") or "").strip()
            else:
                cleaned = str(item.get("subreddit") or item.get("name") or item.get("id") or "").strip()
        else:
            cleaned = ""
        if cleaned:
            normalized.append(cleaned)

    out["count"] = len(normalized)
    out["preview"] = normalized[:10]
    return out


def _parse_source_names(raw: str) -> List[str]:
    tokens = [str(item).strip() for item in str(raw or "").split(",")]
    out: List[str] = []
    seen: set[str] = set()
    for token in tokens:
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _default_source_names() -> List[str]:
    configured = _parse_source_names(str(os.getenv("UA_CSI_SOURCE_HEALTH_SOURCES") or ""))
    if configured:
        return configured
    return [
        "youtube_channel_rss",
        "reddit_discovery",
        "threads_owned",
        "threads_trends_seeded",
        "threads_trends_broad",
        "csi_analytics",
    ]


def _parse_source_min_events(raw: str) -> Dict[str, int]:
    text = str(raw or "").strip()
    if not text:
        return {}
    out: Dict[str, int] = {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            for key, value in parsed.items():
                cleaned = str(key or "").strip()
                if not cleaned:
                    continue
                try:
                    out[cleaned] = max(0, int(value))
                except Exception:
                    continue
            return out
    except Exception:
        pass
    for token in text.split(","):
        item = token.strip()
        if not item or "=" not in item:
            continue
        key, raw_value = item.split("=", 1)
        key = key.strip()
        if not key:
            continue
        try:
            out[key] = max(0, int(raw_value.strip()))
        except Exception:
            continue
    return out


def _aggregate_source_mix(subject: Dict[str, Any], source: str) -> Dict[str, int]:
    mix: Dict[str, int] = {}
    direct_mix = subject.get("source_mix")
    if isinstance(direct_mix, dict):
        for key, value in direct_mix.items():
            cleaned = str(key or "").strip() or "unknown"
            mix[cleaned] = int(mix.get(cleaned) or 0) + int(value or 0)

    opportunities = subject.get("opportunities") if isinstance(subject.get("opportunities"), list) else []
    for item in opportunities:
        if not isinstance(item, dict):
            continue
        item_mix = item.get("source_mix")
        if not isinstance(item_mix, dict):
            continue
        for key, value in item_mix.items():
            cleaned = str(key or "").strip() or "unknown"
            mix[cleaned] = int(mix.get(cleaned) or 0) + int(value or 0)

    if not mix and source:
        mix[source] = 1
    return mix


def _save_snapshot(*, tool_name: str, args: Dict[str, Any], payload: Dict[str, Any]) -> Optional[str]:
    ws = resolve_current_session_workspace(repo_root=str(Path(__file__).resolve().parents[3]))
    if not ws:
        return None
    wp = build_interim_work_product_paths(
        workspace_dir=ws,
        domain="state_snapshot",
        source="csi",
        run_slug=safe_slug(tool_name, fallback="csi_snapshot"),
    )
    try:
        write_json(
            wp.request_path,
            {
                "tool": tool_name,
                "args": args,
            },
        )
        write_json(wp.result_path, payload)
        write_json(
            wp.manifest_path,
            {
                "type": "interim_work_product",
                "domain": "csi",
                "source": "csi",
                "kind": "state_snapshot",
                "paths": {
                    "request": str(wp.request_path.relative_to(ws)),
                    "result": str(wp.result_path.relative_to(ws)),
                },
                "retention": "session",
            },
        )
        return str(wp.result_path.relative_to(ws))
    except Exception:
        return None


@tool(
    name="csi_recent_reports",
    description="Read recent CSI report-ready events and return compact structured report metadata.",
    input_schema={
        "limit": int,
        "include_artifacts": bool,
        "save_to_workspace": bool,
    },
)
async def csi_recent_reports_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    limit = max(1, min(int(args.get("limit", 12) or 12), 100))
    include_artifacts = bool(args.get("include_artifacts", True))
    save_to_workspace = bool(args.get("save_to_workspace", False))

    db_path = _db_path()
    if not db_path.exists():
        return _err(f"CSI database not found at {db_path}")

    rows_out: List[Dict[str, Any]] = []
    with _connect() as conn:
        if not _table_exists(conn, "events"):
            return _err("CSI database does not include events table")
        rows = conn.execute(
            """
            SELECT event_id, event_type, source, occurred_at, subject_json
            FROM events
            WHERE event_type IN (
                'report_product_ready',
                'opportunity_bundle_ready',
                'rss_trend_report',
                'reddit_trend_report',
                'rss_insight_daily',
                'rss_insight_emerging'
            )
            ORDER BY occurred_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        for row in rows:
            subject = _loads_obj(row["subject_json"], default={})
            if not isinstance(subject, dict):
                subject = {}
            source = str(row["source"] or "").strip().lower()
            artifact_paths = subject.get("artifact_paths") if isinstance(subject.get("artifact_paths"), dict) else {}
            if not include_artifacts:
                artifact_paths = {}
            rows_out.append(
                {
                    "event_id": str(row["event_id"] or ""),
                    "event_type": str(row["event_type"] or ""),
                    "source": source,
                    "occurred_at": str(row["occurred_at"] or ""),
                    "report_type": str(subject.get("report_type") or row["event_type"] or ""),
                    "report_key": str(subject.get("report_key") or ""),
                    "window_start_utc": str(subject.get("window_start_utc") or ""),
                    "window_end_utc": str(subject.get("window_end_utc") or ""),
                    "artifact_paths": artifact_paths,
                    "source_mix": _aggregate_source_mix(subject, source),
                }
            )

    payload = {
        "status": "ok",
        "db_path": str(db_path),
        "count": len(rows_out),
        "reports": rows_out,
    }
    if save_to_workspace:
        rel = _save_snapshot(tool_name="csi_recent_reports", args=args, payload=payload)
        if rel:
            payload["saved_result_path"] = rel
    return _ok(payload)


@tool(
    name="csi_opportunity_bundles",
    description="Read latest CSI opportunity bundles with compact confidence/coverage metadata.",
    input_schema={
        "limit": int,
        "save_to_workspace": bool,
    },
)
async def csi_opportunity_bundles_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    limit = max(1, min(int(args.get("limit", 8) or 8), 50))
    save_to_workspace = bool(args.get("save_to_workspace", False))

    db_path = _db_path()
    if not db_path.exists():
        return _err(f"CSI database not found at {db_path}")

    bundles: List[Dict[str, Any]] = []
    with _connect() as conn:
        if _table_exists(conn, "opportunity_bundles"):
            rows = conn.execute(
                """
                SELECT
                    bundle_id,
                    report_key,
                    window_start_utc,
                    window_end_utc,
                    confidence_method,
                    quality_summary_json,
                    opportunities_json,
                    artifact_markdown_path,
                    artifact_json_path,
                    created_at
                FROM opportunity_bundles
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            for row in rows:
                quality_summary = _loads_obj(row["quality_summary_json"], default={})
                if not isinstance(quality_summary, dict):
                    quality_summary = {}
                opportunities = _loads_obj(row["opportunities_json"], default=[])
                if not isinstance(opportunities, list):
                    opportunities = []
                source_mix: Dict[str, int] = {}
                for item in opportunities:
                    if not isinstance(item, dict):
                        continue
                    mix = item.get("source_mix")
                    if not isinstance(mix, dict):
                        continue
                    for key, value in mix.items():
                        cleaned = str(key or "").strip() or "unknown"
                        source_mix[cleaned] = int(source_mix.get(cleaned) or 0) + int(value or 0)
                bundles.append(
                    {
                        "bundle_id": str(row["bundle_id"] or ""),
                        "report_key": str(row["report_key"] or ""),
                        "window_start_utc": str(row["window_start_utc"] or ""),
                        "window_end_utc": str(row["window_end_utc"] or ""),
                        "confidence_method": str(row["confidence_method"] or "heuristic"),
                        "quality_summary": quality_summary,
                        "opportunity_count": len([item for item in opportunities if isinstance(item, dict)]),
                        "source_mix": source_mix,
                        "artifact_paths": {
                            "markdown": str(row["artifact_markdown_path"] or ""),
                            "json": str(row["artifact_json_path"] or ""),
                        },
                        "created_at": str(row["created_at"] or ""),
                    }
                )
        elif _table_exists(conn, "events"):
            rows = conn.execute(
                """
                SELECT event_id, occurred_at, subject_json
                FROM events
                WHERE event_type = 'opportunity_bundle_ready'
                ORDER BY occurred_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            for row in rows:
                subject = _loads_obj(row["subject_json"], default={})
                if not isinstance(subject, dict):
                    subject = {}
                opportunities = subject.get("opportunities") if isinstance(subject.get("opportunities"), list) else []
                bundles.append(
                    {
                        "bundle_id": str(subject.get("bundle_id") or row["event_id"] or ""),
                        "report_key": str(subject.get("report_key") or ""),
                        "window_start_utc": str(subject.get("window_start_utc") or ""),
                        "window_end_utc": str(subject.get("window_end_utc") or ""),
                        "confidence_method": str(subject.get("confidence_method") or "heuristic"),
                        "quality_summary": subject.get("quality_summary") if isinstance(subject.get("quality_summary"), dict) else {},
                        "opportunity_count": len([item for item in opportunities if isinstance(item, dict)]),
                        "source_mix": _aggregate_source_mix(subject, "csi_analytics"),
                        "artifact_paths": subject.get("artifact_paths") if isinstance(subject.get("artifact_paths"), dict) else {},
                        "created_at": str(row["occurred_at"] or ""),
                    }
                )
        else:
            return _err("CSI database does not include opportunity_bundles or events table")

    payload = {
        "status": "ok",
        "db_path": str(db_path),
        "count": len(bundles),
        "bundles": bundles,
        "latest": bundles[0] if bundles else None,
    }
    if save_to_workspace:
        rel = _save_snapshot(tool_name="csi_opportunity_bundles", args=args, payload=payload)
        if rel:
            payload["saved_result_path"] = rel
    return _ok(payload)


@tool(
    name="csi_source_health",
    description="Read compact per-source CSI health over a rolling window from the CSI events table.",
    input_schema={
        "window_hours": int,
        "stale_minutes": int,
        "save_to_workspace": bool,
    },
)
async def csi_source_health_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    window_hours = max(1, min(int(args.get("window_hours", 24) or 24), 24 * 30))
    stale_minutes = max(15, min(int(args.get("stale_minutes", 240) or 240), 24 * 60 * 7))
    save_to_workspace = bool(args.get("save_to_workspace", False))

    db_path = _db_path()
    if not db_path.exists():
        return _err(f"CSI database not found at {db_path}")

    now_ts = time.time()
    source_names = _default_source_names()
    source_min_events = {
        "youtube_channel_rss": 1,
        "reddit_discovery": 1,
        "threads_owned": 0,
        "threads_trends_seeded": 0,
        "threads_trends_broad": 0,
        "csi_analytics": 0,
    }
    source_min_events.update(_parse_source_min_events(str(os.getenv("UA_CSI_DELIVERY_SOURCE_MIN_EVENTS") or "")))
    rows_out: List[Dict[str, Any]] = []
    with _connect() as conn:
        if not _table_exists(conn, "events"):
            return _err("CSI database does not include events table")
        window_expr = f"-{window_hours} hours"
        for source_name in source_names:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total, MAX(created_at) AS last_event_at
                FROM events
                WHERE source = ?
                  AND created_at >= datetime('now', ?)
                """,
                (source_name, window_expr),
            ).fetchone()
            total = int((row["total"] if row is not None else 0) or 0)
            last_event_at = str((row["last_event_at"] if row is not None else "") or "")
            lag_minutes: Optional[float] = None
            if last_event_at:
                try:
                    parsed = last_event_at.replace("Z", "+00:00")
                    lag_minutes = round((now_ts - float(datetime.fromisoformat(parsed).timestamp())) / 60.0, 2)
                except Exception:
                    lag_minutes = None
            status = "ok"
            expected_min = int(source_min_events.get(source_name) or 0)
            if expected_min > 0 and (total <= 0 or (lag_minutes is not None and lag_minutes > stale_minutes)):
                status = "stale"
            rows_out.append(
                {
                    "source": source_name,
                    "status": status,
                    "events": total,
                    "expected_min_events": expected_min,
                    "last_event_at": last_event_at,
                    "lag_minutes": lag_minutes,
                }
            )

    payload = {
        "status": "ok",
        "db_path": str(db_path),
        "window_hours": window_hours,
        "stale_minutes": stale_minutes,
        "sources": rows_out,
    }
    if save_to_workspace:
        rel = _save_snapshot(tool_name="csi_source_health", args=args, payload=payload)
        if rel:
            payload["saved_result_path"] = rel
    return _ok(payload)


@tool(
    name="csi_watchlist_snapshot",
    description="Read CSI watchlist coverage/freshness snapshot for YouTube and Reddit plus recent source activity.",
    input_schema={
        "window_hours": int,
        "save_to_workspace": bool,
    },
)
async def csi_watchlist_snapshot_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    window_hours = max(1, min(int(args.get("window_hours", 24) or 24), 24 * 30))
    save_to_workspace = bool(args.get("save_to_workspace", False))

    youtube_path = (
        str(os.getenv("CSI_YOUTUBE_WATCHLIST_FILE") or "").strip() or _DEFAULT_YT_WATCHLIST
    )
    reddit_path = (
        str(os.getenv("CSI_REDDIT_WATCHLIST_FILE") or "").strip() or _DEFAULT_REDDIT_WATCHLIST
    )

    health_payload: Dict[str, Any]
    health_resp = await csi_source_health_wrapper({"window_hours": window_hours, "stale_minutes": 240, "save_to_workspace": False})
    try:
        health_payload = json.loads(str(((health_resp.get("content") or [{}])[0]).get("text") or "{}"))
    except Exception:
        health_payload = {"status": "error", "sources": []}

    payload = {
        "status": "ok",
        "window_hours": window_hours,
        "watchlists": {
            "youtube": _read_watchlist(youtube_path, "youtube"),
            "reddit": _read_watchlist(reddit_path, "reddit"),
        },
        "source_activity": health_payload.get("sources") if isinstance(health_payload.get("sources"), list) else [],
    }
    if save_to_workspace:
        rel = _save_snapshot(tool_name="csi_watchlist_snapshot", args=args, payload=payload)
        if rel:
            payload["saved_result_path"] = rel
    return _ok(payload)
