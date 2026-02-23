#!/usr/bin/env python3
"""Run queued CSI analysis tasks and return results back to UA."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from csi_ingester.analytics import canonicalize_category
from csi_ingester.analytics.emission import emit_and_track
from csi_ingester.config import load_config
from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.store import analysis_tasks as analysis_task_store
from csi_ingester.store import token_usage as token_usage_store
from csi_ingester.store.sqlite import connect, ensure_schema


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


def _select_analysis_rows(
    conn: sqlite3.Connection,
    *,
    request_type: str,
    payload: dict[str, Any],
) -> list[sqlite3.Row]:
    lookback_hours = max(1, int(payload.get("lookback_hours") or 72))
    limit = max(20, min(500, int(payload.get("limit") or 160)))
    where: list[str] = ["analyzed_at >= datetime('now', ?)"]
    params: list[Any] = [f"-{lookback_hours} hours"]

    if request_type == "category_deep_dive":
        category = canonicalize_category(str(payload.get("category") or "other_interest"))
        where.append("category = ?")
        params.append(category)
    elif request_type == "channel_deep_dive":
        channel_id = str(payload.get("channel_id") or "").strip()
        channel_name = str(payload.get("channel_name") or "").strip()
        if channel_id:
            where.append("channel_id = ?")
            params.append(channel_id)
        elif channel_name:
            where.append("channel_name = ?")
            params.append(channel_name)
    elif request_type == "trend_followup":
        maybe_category = str(payload.get("category") or "").strip()
        if maybe_category:
            where.append("category = ?")
            params.append(canonicalize_category(maybe_category))

    rows = conn.execute(
        f"""
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
        WHERE {' AND '.join(where)}
        ORDER BY analyzed_at DESC, id DESC
        LIMIT ?
        """,
        tuple([*params, limit]),
    ).fetchall()
    return rows


def _build_result(
    rows: list[sqlite3.Row],
    *,
    request_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    by_category: Counter[str] = Counter()
    by_channel: Counter[str] = Counter()
    by_theme: Counter[str] = Counter()
    latest_items: list[dict[str, str]] = []

    for row in rows:
        category = canonicalize_category(str(row["category"] or "other_interest"))
        by_category[category] += 1
        channel_name = str(row["channel_name"] or "").strip()
        channel_id = str(row["channel_id"] or "").strip()
        channel_label = channel_name or channel_id or "unknown_channel"
        by_channel[channel_label] += 1
        try:
            parsed = json.loads(str(row["analysis_json"] or "{}"))
            if not isinstance(parsed, dict):
                parsed = {}
        except Exception:
            parsed = {}
        themes = parsed.get("themes")
        if isinstance(themes, list):
            for theme in themes:
                label = str(theme).strip().lower()
                if label:
                    by_theme[label] += 1
        latest_items.append(
            {
                "event_id": str(row["event_id"] or ""),
                "video_id": str(row["video_id"] or ""),
                "channel": channel_label,
                "title": str(row["title"] or ""),
                "category": category,
                "summary": str(row["summary_text"] or "")[:320],
                "analyzed_at": str(row["analyzed_at"] or ""),
            }
        )

    markdown_lines: list[str] = []
    markdown_lines.append(f"# CSI Analysis Task: {request_type}")
    markdown_lines.append("")
    markdown_lines.append("## Scope")
    markdown_lines.append(f"- Rows analyzed: {len(rows)}")
    markdown_lines.append(f"- Payload: `{json.dumps(payload, separators=(',', ':'), sort_keys=True)}`")
    markdown_lines.append("")
    markdown_lines.append("## Category Mix")
    for slug, count in by_category.most_common(12):
        markdown_lines.append(f"- {slug}: {count}")
    markdown_lines.append("")
    markdown_lines.append("## Top Channels")
    for channel, count in by_channel.most_common(10):
        markdown_lines.append(f"- {channel}: {count}")
    markdown_lines.append("")
    markdown_lines.append("## Top Narratives")
    for theme, count in by_theme.most_common(12):
        markdown_lines.append(f"- {theme}: {count}")
    markdown_lines.append("")
    markdown_lines.append("## Recent Items")
    for item in latest_items[:12]:
        markdown_lines.append(f"- {item['channel']}: {item['title']} ({item['category']})")

    return {
        "request_type": request_type,
        "input_payload": payload,
        "totals": {
            "rows": len(rows),
            "by_category": dict(by_category),
        },
        "top_channels": [{"channel": key, "count": count} for key, count in by_channel.most_common(12)],
        "top_themes": [{"theme": key, "count": count} for key, count in by_theme.most_common(16)],
        "latest_items": latest_items[:20],
        "markdown": "\n".join(markdown_lines).strip()[:48000],
    }


def _claude_polish_result(
    *,
    result: dict[str, Any],
    model: str,
    endpoint: str,
    api_key: str,
) -> tuple[str | None, dict[str, int]]:
    prompt = (
        "Rewrite this CSI analysis result into concise markdown for an analyst.\n"
        "Required sections: Executive Summary, Notable Signals, Suggested Follow-ups.\n"
        "Keep under 1200 words.\n\n"
        f"Data JSON:\n{json.dumps(result, ensure_ascii=False)}"
    )
    req_body = {
        "model": model,
        "max_tokens": 900,
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
        with urllib.request.urlopen(req, timeout=45) as resp:
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


def _emit_task_event(
    conn: sqlite3.Connection,
    *,
    config,
    task: dict[str, Any],
    status: str,
    result: dict[str, Any] | None,
    error_text: str = "",
) -> tuple[bool, int, dict[str, Any]]:
    now_iso = _utc_now_iso()
    task_id = str(task.get("task_id") or "")
    event = CreatorSignalEvent(
        event_id=f"csi:analysis_task:{task_id}:{status}:{uuid.uuid4().hex[:10]}",
        dedupe_key=f"csi:analysis_task:{task_id}:{status}",
        source="csi_analyst",
        event_type=f"analysis_task_{status}",
        occurred_at=now_iso,
        received_at=now_iso,
        subject={
            "task_id": task_id,
            "request_type": str(task.get("request_type") or ""),
            "status": status,
            "result": result or {},
            "error_text": error_text[:4000],
        },
        routing={
            "pipeline": "csi_analysis_task_runner",
            "priority": "standard",
            "tags": ["csi", "analysis_task", status],
        },
        metadata={"source_adapter": "csi_analysis_task_runner_v1"},
    )
    return emit_and_track(conn, config=config, event=event, retry_count=3)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run queued CSI analysis tasks.")
    parser.add_argument("--db-path", default="/opt/universal_agent/CSI_Ingester/development/var/csi.db")
    parser.add_argument("--max-tasks", type=int, default=4)
    parser.add_argument(
        "--request-types",
        default="",
        help="Comma list of task request types to process; empty = all pending types",
    )
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

    use_claude = _resolve_setting(["CSI_ANALYSIS_TASK_RUNNER_USE_CLAUDE"], env_file_values).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    api_key = _resolve_setting(["ANTHROPIC_API_KEY"], env_file_values)
    model = _resolve_setting(["CSI_ANALYSIS_TASK_RUNNER_CLAUDE_MODEL"], env_file_values) or "claude-3-5-haiku-latest"
    base_url = (_resolve_setting(["ANTHROPIC_BASE_URL"], env_file_values) or "https://api.anthropic.com").rstrip("/")
    endpoint = f"{base_url}/v1/messages" if not base_url.endswith("/v1/messages") else base_url

    configured_types = [item.strip() for item in args.request_types.split(",") if item.strip()]
    if not configured_types:
        configured_types_env = _resolve_setting(["CSI_ANALYSIS_TASK_RUNNER_REQUEST_TYPES"], env_file_values)
        configured_types = [item.strip() for item in configured_types_env.split(",") if item.strip()]

    processed = 0
    completed = 0
    failed = 0
    claimed = 0

    for _ in range(max(1, int(args.max_tasks))):
        claim_token = f"claim_{uuid.uuid4().hex[:16]}"
        task = analysis_task_store.claim_next_task(
            conn,
            claim_token=claim_token,
            request_types=configured_types,
            max_attempts=3,
        )
        if task is None:
            break
        claimed += 1
        task_id = str(task.get("task_id") or "")
        request_type = str(task.get("request_type") or "").strip() or "ad_hoc_query"
        payload = task.get("payload")
        if not isinstance(payload, dict):
            payload = {}

        try:
            rows = _select_analysis_rows(
                conn,
                request_type=request_type,
                payload=payload,
            )
            result = _build_result(rows, request_type=request_type, payload=payload)
            usage: dict[str, int] = {}
            if use_claude and api_key and int(result.get("totals", {}).get("rows") or 0) > 0:
                polished, usage = _claude_polish_result(
                    result=result,
                    model=model,
                    endpoint=endpoint,
                    api_key=api_key,
                )
                if polished:
                    result["markdown"] = polished
                if usage:
                    result["token_usage"] = usage
                    token_usage_store.insert_usage(
                        conn,
                        process_name="analysis_task_runner_claude",
                        model_name=model,
                        prompt_tokens=int(usage.get("prompt_tokens") or 0),
                        completion_tokens=int(usage.get("completion_tokens") or 0),
                        total_tokens=int(usage.get("total_tokens") or 0),
                        metadata={"task_id": task_id, "request_type": request_type},
                    )
            ok = analysis_task_store.complete_task(
                conn,
                task_id=task_id,
                claim_token=claim_token,
                result=result,
            )
            if not ok:
                raise RuntimeError("failed to update task status to completed")
            emit_and_track_ok, status_code, _payload = _emit_task_event(
                conn,
                config=config,
                task=task,
                status="completed",
                result=result,
            )
            processed += 1
            if emit_and_track_ok or status_code == 409:
                completed += 1
            else:
                completed += 1
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            analysis_task_store.fail_task(
                conn,
                task_id=task_id,
                claim_token=claim_token,
                error_text=error_text,
                retry=False,
            )
            _emit_task_event(
                conn,
                config=config,
                task=task,
                status="failed",
                result={},
                error_text=error_text,
            )
            processed += 1
            failed += 1

    conn.close()
    print(f"CSI_TASK_RUNNER_CLAIMED={claimed}")
    print(f"CSI_TASK_RUNNER_PROCESSED={processed}")
    print(f"CSI_TASK_RUNNER_COMPLETED={completed}")
    print(f"CSI_TASK_RUNNER_FAILED={failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
