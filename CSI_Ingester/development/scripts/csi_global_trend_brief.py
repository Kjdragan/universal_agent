#!/usr/bin/env python3
"""Generate cross-source global CSI trend briefing and emit readiness event."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any
import urllib.request

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


def _rows_to_reports(rows: list[sqlite3.Row], *, source: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        report_json = {}
        try:
            report_json = json.loads(str(row["report_json"] or "{}"))
        except Exception:
            report_json = {}
        out.append(
            {
                "source": source,
                "report_key": str(row["report_key"] or ""),
                "report_type": str(row["report_type"] or source),
                "created_at": str(row["created_at"] or ""),
                "window_start_utc": str(row["window_start_utc"] or ""),
                "window_end_utc": str(row["window_end_utc"] or ""),
                "report_json": report_json,
                "report_markdown": str(row["report_markdown"] or ""),
            }
        )
    return out


def _collect_inputs(conn: sqlite3.Connection, start_db: str, end_db: str) -> dict[str, Any]:
    inputs: dict[str, Any] = {}

    trend_rows = conn.execute(
        """
        SELECT report_key, 'rss_trend_report' AS report_type, window_start_utc, window_end_utc, report_json, report_markdown, created_at
        FROM trend_reports
        WHERE created_at >= ? AND created_at < ?
        ORDER BY created_at DESC, id DESC
        LIMIT 8
        """,
        (start_db, end_db),
    ).fetchall()
    inputs["rss_trends"] = _rows_to_reports(trend_rows, source="youtube_channel_rss")

    insight_rows = conn.execute(
        """
        SELECT report_key, report_type, window_start_utc, window_end_utc, report_json, report_markdown, created_at
        FROM insight_reports
        WHERE created_at >= ? AND created_at < ?
        ORDER BY created_at DESC, id DESC
        LIMIT 20
        """,
        (start_db, end_db),
    ).fetchall()
    insight_reports = _rows_to_reports(insight_rows, source="insight")
    inputs["insights"] = insight_reports

    global_recent_rows = conn.execute(
        """
        SELECT brief_key, window_start_utc, window_end_utc, brief_json, created_at
        FROM global_trend_briefs
        ORDER BY created_at DESC, id DESC
        LIMIT 3
        """
    ).fetchall()
    recents: list[dict[str, Any]] = []
    for row in global_recent_rows:
        brief_json = {}
        try:
            brief_json = json.loads(str(row["brief_json"] or "{}"))
        except Exception:
            brief_json = {}
        recents.append(
            {
                "brief_key": str(row["brief_key"] or ""),
                "window_start_utc": str(row["window_start_utc"] or ""),
                "window_end_utc": str(row["window_end_utc"] or ""),
                "created_at": str(row["created_at"] or ""),
                "brief_json": brief_json,
            }
        )
    inputs["recent_global_briefs"] = recents
    return inputs


def _first_report_by_type(reports: list[dict[str, Any]], report_type: str) -> dict[str, Any] | None:
    for report in reports:
        if str(report.get("report_type") or "").lower() == report_type.lower():
            return report
    return None


def _source_delta(current_total: int, previous_total: int) -> str:
    delta = current_total - previous_total
    if delta > 0:
        return f"up by {delta}"
    if delta < 0:
        return f"down by {abs(delta)}"
    return "flat"


def _default_markdown(payload: dict[str, Any]) -> str:
    source_totals = payload.get("source_totals") if isinstance(payload.get("source_totals"), dict) else {}
    previous_source_totals = payload.get("previous_source_totals") if isinstance(payload.get("previous_source_totals"), dict) else {}
    top_narratives = payload.get("top_narratives") if isinstance(payload.get("top_narratives"), list) else []
    contradictions = payload.get("contradictions") if isinstance(payload.get("contradictions"), list) else []

    lines: list[str] = []
    lines.append(f"# CSI Global Trend Brief ({payload.get('window_start_utc')} -> {payload.get('window_end_utc')})")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append(
        "Cross-source signals were synthesized from YouTube RSS, Reddit discovery, and Threads trends. "
        "This briefing prioritizes narrative clarity with evidence references."
    )
    lines.append("")

    lines.append("## Source Throughput (Current vs Previous Window)")
    for source_key in ("youtube", "reddit", "threads"):
        current = int(source_totals.get(source_key) or 0)
        previous = int(previous_source_totals.get(source_key) or 0)
        lines.append(f"- {source_key}: {current} ({_source_delta(current, previous)})")
    lines.append("")

    lines.append("## Top Narratives")
    if not top_narratives:
        lines.append("- No strong narratives detected in this window.")
    else:
        for row in top_narratives[:8]:
            lines.append(
                f"- {row.get('title')}: {row.get('summary')} "
                f"(confidence={row.get('confidence')}, evidence={', '.join(row.get('evidence_refs') or [])})"
            )
    lines.append("")

    lines.append("## Contradictions / Consensus")
    if contradictions:
        for item in contradictions[:8]:
            lines.append(f"- {item}")
    else:
        lines.append("- No major contradiction flags; sources are directionally aligned in this window.")
    lines.append("")

    lines.append("## Why It Matters")
    lines.append(
        "Use this briefing as the primary narrative checkpoint. Operational health metrics stay in CSI Health tab."
    )
    return "\n".join(lines)


def _claude_markdown(*, payload: dict[str, Any], model: str, endpoint: str, api_key: str) -> tuple[str | None, dict[str, int]]:
    prompt = (
        "Produce a concise executive cross-source trend briefing in markdown.\n"
        "Sections required: Executive Summary, What Changed Since Last Window, "
        "Top Narratives, Contradictions vs Consensus, Confidence & Rationale, Evidence Links.\n"
        "Avoid debugging language; write for decision consumption.\n\n"
        f"Input JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    req_body = {
        "model": model,
        "max_tokens": 1600,
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
            body = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None, {}

    text_parts: list[str] = []
    for block in body.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(str(block.get("text") or ""))
    text = "\n".join(text_parts).strip() or None

    usage_obj = body.get("usage") if isinstance(body, dict) else None
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


def _build_payload(inputs: dict[str, Any], start_dt: datetime, end_dt: datetime) -> dict[str, Any]:
    insights = inputs.get("insights") if isinstance(inputs.get("insights"), list) else []
    rss_trends = inputs.get("rss_trends") if isinstance(inputs.get("rss_trends"), list) else []
    recent_global = inputs.get("recent_global_briefs") if isinstance(inputs.get("recent_global_briefs"), list) else []

    reddit_report = _first_report_by_type(insights, "reddit_trend_report")
    threads_report = _first_report_by_type(insights, "threads_trend_report")
    rss_report = rss_trends[0] if rss_trends else _first_report_by_type(insights, "rss_trend_report")

    def _extract_total(report: dict[str, Any] | None) -> int:
        if not report:
            return 0
        report_json = report.get("report_json") if isinstance(report.get("report_json"), dict) else {}
        return int(report_json.get("total_items") or report_json.get("totals", {}).get("items") or 0)

    source_totals = {
        "youtube": _extract_total(rss_report),
        "reddit": _extract_total(reddit_report),
        "threads": _extract_total(threads_report),
    }

    previous_source_totals = {"youtube": 0, "reddit": 0, "threads": 0}
    if recent_global:
        previous = recent_global[0].get("brief_json") if isinstance(recent_global[0], dict) else {}
        if isinstance(previous, dict):
            prev_totals = previous.get("source_totals") if isinstance(previous.get("source_totals"), dict) else {}
            previous_source_totals = {
                "youtube": int(prev_totals.get("youtube") or 0),
                "reddit": int(prev_totals.get("reddit") or 0),
                "threads": int(prev_totals.get("threads") or 0),
            }

    top_narratives: list[dict[str, Any]] = []

    def _add_narratives(report: dict[str, Any] | None, source_name: str) -> None:
        if not report:
            return
        report_json = report.get("report_json") if isinstance(report.get("report_json"), dict) else {}
        candidates = report_json.get("top_narratives") if isinstance(report_json.get("top_narratives"), list) else []
        if not candidates:
            candidates = report_json.get("top_themes") if isinstance(report_json.get("top_themes"), list) else []
        for item in candidates[:4]:
            if isinstance(item, dict):
                title = str(item.get("narrative") or item.get("theme") or item.get("label") or item.get("name") or "").strip()
                count = int(item.get("count") or item.get("hits") or item.get("items") or 0)
            else:
                title = str(item or "").strip()
                count = 0
            if not title:
                continue
            top_narratives.append(
                {
                    "title": f"{source_name}: {title}",
                    "summary": f"{title} appears in {source_name} signal stream.",
                    "confidence": round(min(0.95, 0.55 + min(40, count) / 100.0), 2),
                    "evidence_refs": [f"report:{report.get('report_key')}"] if report.get("report_key") else [],
                }
            )

    _add_narratives(rss_report, "YouTube")
    _add_narratives(reddit_report, "Reddit")
    _add_narratives(threads_report, "Threads")

    contradictions: list[str] = []
    if source_totals["threads"] > 0 and source_totals["youtube"] == 0:
        contradictions.append("Threads activity high while YouTube watchlist is quiet; cross-platform lag likely.")
    if source_totals["reddit"] > source_totals["youtube"] * 2 and source_totals["youtube"] > 0:
        contradictions.append("Reddit is outpacing YouTube signal volume in this window.")

    return {
        "window_start_utc": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_end_utc": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_totals": source_totals,
        "previous_source_totals": previous_source_totals,
        "top_narratives": top_narratives[:12],
        "contradictions": contradictions,
        "evidence_report_keys": [
            str(item.get("report_key") or "")
            for item in [rss_report, reddit_report, threads_report]
            if isinstance(item, dict) and str(item.get("report_key") or "").strip()
        ],
    }


def _save_artifacts(artifacts_root: Path, brief_key: str, markdown: str, payload: dict[str, Any]) -> tuple[str, str]:
    directory = artifacts_root / "csi" / "global_trend_briefs"
    directory.mkdir(parents=True, exist_ok=True)
    md_path = directory / f"{brief_key}.md"
    json_path = directory / f"{brief_key}.json"
    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(md_path), str(json_path)


def _insert_brief(
    conn: sqlite3.Connection,
    *,
    brief_key: str,
    window_start_utc: str,
    window_end_utc: str,
    markdown: str,
    payload: dict[str, Any],
    artifact_markdown_path: str,
    artifact_json_path: str,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
) -> None:
    conn.execute(
        """
        INSERT INTO global_trend_briefs (
            brief_key, window_start_utc, window_end_utc, model_name,
            prompt_tokens, completion_tokens, total_tokens,
            brief_markdown, brief_json, artifact_markdown_path, artifact_json_path, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(brief_key) DO UPDATE SET
            window_start_utc=excluded.window_start_utc,
            window_end_utc=excluded.window_end_utc,
            model_name=excluded.model_name,
            prompt_tokens=excluded.prompt_tokens,
            completion_tokens=excluded.completion_tokens,
            total_tokens=excluded.total_tokens,
            brief_markdown=excluded.brief_markdown,
            brief_json=excluded.brief_json,
            artifact_markdown_path=excluded.artifact_markdown_path,
            artifact_json_path=excluded.artifact_json_path,
            created_at=datetime('now')
        """,
        (
            brief_key,
            window_start_utc,
            window_end_utc,
            model_name or None,
            max(0, int(prompt_tokens)),
            max(0, int(completion_tokens)),
            max(0, int(total_tokens)),
            markdown,
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            artifact_markdown_path,
            artifact_json_path,
        ),
    )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate CSI global cross-source trend briefing.")
    parser.add_argument("--db-path", default="/var/lib/universal-agent/csi/csi.db")
    parser.add_argument("--window-hours", type=int, default=2)
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

    use_claude = _resolve_setting(["CSI_GLOBAL_BRIEF_USE_CLAUDE"], env_values).strip().lower() in {"1", "true", "yes", "on"}
    auth = resolve_csi_llm_auth(env_values, default_base_url="https://api.anthropic.com")
    api_key = auth.api_key
    base_url = auth.base_url
    endpoint = f"{base_url}/v1/messages" if not base_url.endswith("/v1/messages") else base_url
    model = (
        args.claude_model.strip()
        or _resolve_setting(["CSI_GLOBAL_BRIEF_CLAUDE_MODEL"], env_values).strip()
        or "claude-3-5-haiku-latest"
    )

    start_dt, end_dt = _window(args.window_hours)
    start_db = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    end_db = end_dt.strftime("%Y-%m-%d %H:%M:%S")

    conn = connect(db_path)
    ensure_schema(conn)

    inputs = _collect_inputs(conn, start_db, end_db)
    payload = _build_payload(inputs, start_dt, end_dt)

    source_totals = payload.get("source_totals") if isinstance(payload.get("source_totals"), dict) else {}
    if int(source_totals.get("youtube") or 0) + int(source_totals.get("reddit") or 0) + int(source_totals.get("threads") or 0) <= 0:
        print("CSI_GLOBAL_BRIEF_SKIPPED=no_source_activity")
        conn.close()
        return 0

    markdown = _default_markdown(payload)
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    model_name = ""

    if use_claude and api_key:
        generated, usage = _claude_markdown(payload=payload, model=model, endpoint=endpoint, api_key=api_key)
        if generated:
            markdown = generated
            model_name = model
            if usage:
                prompt_tokens = int(usage.get("prompt_tokens") or 0)
                completion_tokens = int(usage.get("completion_tokens") or 0)
                total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
                token_usage_store.insert_usage(
                    conn,
                    process_name="global_trend_brief_claude",
                    model_name=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    metadata={
                        "window_hours": int(args.window_hours),
                        "source_totals": source_totals,
                    },
                )

    brief_key = f"global_trend_brief:{end_dt.strftime('%Y%m%dT%H%M%SZ')}"
    artifact_md, artifact_json = _save_artifacts(Path(args.artifacts_root).expanduser(), brief_key, markdown, payload)

    payload["brief_key"] = brief_key
    payload["artifact_paths"] = {"markdown": artifact_md, "json": artifact_json}
    _insert_brief(
        conn,
        brief_key=brief_key,
        window_start_utc=str(payload.get("window_start_utc") or start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")),
        window_end_utc=str(payload.get("window_end_utc") or end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")),
        markdown=markdown,
        payload=payload,
        artifact_markdown_path=artifact_md,
        artifact_json_path=artifact_json,
        model_name=model_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )

    # Build a meaningful title from the top narrative for the CSI dashboard
    _top_narrs = payload.get("top_narratives") or []
    _brief_title = "Cross-Source Intelligence Brief"
    if _top_narrs and isinstance(_top_narrs[0], dict):
        _lead = str(_top_narrs[0].get("title") or _top_narrs[0].get("narrative") or "").strip()
        if _lead:
            _brief_title = _lead[:120]

    _yt = int(source_totals.get("youtube") or 0)
    _rd = int(source_totals.get("reddit") or 0)
    _th = int(source_totals.get("threads") or 0)
    _brief_summary = f"YouTube {_yt} · Reddit {_rd} · Threads {_th} signals"
    if len(_top_narrs) > 1 and isinstance(_top_narrs[1], dict):
        _second = str(_top_narrs[1].get("title") or _top_narrs[1].get("narrative") or "").strip()
        if _second:
            _brief_summary += f" — also: {_second[:80]}"

    cfg = load_config(Path(args.config_path).expanduser())
    event = CreatorSignalEvent(
        event_id=f"csi_global_trend_brief_ready:{brief_key}",
        dedupe_key=f"csi:global_trend_brief_ready:{brief_key}",
        source="csi_analytics",
        event_type="global_trend_brief_ready",
        occurred_at=end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        received_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        subject={
            "title": _brief_title,
            "summary": _brief_summary,
            "brief_key": brief_key,
            "window_hours": int(args.window_hours),
            "window_start_utc": payload.get("window_start_utc"),
            "window_end_utc": payload.get("window_end_utc"),
            "source_totals": source_totals,
            "top_narratives": payload.get("top_narratives") or [],
            "artifact_paths": {"markdown": artifact_md, "json": artifact_json},
            "markdown_preview": markdown[:2000],
        },
        routing={"pipeline": "csi_analytics", "priority": "high", "tags": ["csi", "global", "brief"]},
        metadata={
            "brief_key": brief_key,
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

    print(f"CSI_GLOBAL_BRIEF_KEY={brief_key}")
    print(f"CSI_GLOBAL_BRIEF_SOURCE_TOTALS={json.dumps(source_totals, sort_keys=True)}")
    print(f"CSI_GLOBAL_BRIEF_ARTIFACT_MD={artifact_md}")
    print(f"CSI_GLOBAL_BRIEF_EMIT_DELIVERED={1 if delivered else 0}")
    print(f"CSI_GLOBAL_BRIEF_EMIT_STATUS={int(status_code or 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
