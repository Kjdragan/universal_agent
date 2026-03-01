#!/usr/bin/env python3
"""Materialize CSI report product artifacts and emit readiness signal to UA."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from csi_ingester.analytics.emission import emit_and_track
from csi_ingester.config import load_config
from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.store import opportunity_bundles as opportunity_bundle_store
from csi_ingester.store.sqlite import connect, ensure_schema


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _latest_trend_report(conn: sqlite3.Connection, window_hours: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT report_key, window_start_utc, window_end_utc, report_markdown, report_json, created_at
        FROM trend_reports
        WHERE created_at >= datetime('now', ?)
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (f"-{max(1, int(window_hours))} hours",),
    ).fetchone()
    if not row:
        return {}
    out = {key: row[key] for key in row.keys()}
    try:
        out["report_json"] = json.loads(str(out.get("report_json") or "{}"))
    except Exception:
        out["report_json"] = {}
    return out


def _latest_insight_reports(conn: sqlite3.Connection, window_hours: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT report_key, report_type, window_start_utc, window_end_utc, report_markdown, report_json, created_at
        FROM insight_reports
        WHERE created_at >= datetime('now', ?)
        ORDER BY created_at DESC, id DESC
        LIMIT 6
        """,
        (f"-{max(1, int(window_hours))} hours",),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = {key: row[key] for key in row.keys()}
        try:
            item["report_json"] = json.loads(str(item.get("report_json") or "{}"))
        except Exception:
            item["report_json"] = {}
        out.append(item)
    return out


def _token_snapshot(conn: sqlite3.Connection, window_hours: int) -> dict[str, Any]:
    totals_row = conn.execute(
        """
        SELECT
            COUNT(*) AS records,
            COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
            COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
            COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM token_usage
        WHERE occurred_at >= datetime('now', ?)
        """,
        (f"-{max(1, int(window_hours))} hours",),
    ).fetchone()

    process_rows = conn.execute(
        """
        SELECT process_name, COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM token_usage
        WHERE occurred_at >= datetime('now', ?)
        GROUP BY process_name
        ORDER BY total_tokens DESC
        LIMIT 12
        """,
        (f"-{max(1, int(window_hours))} hours",),
    ).fetchall()

    return {
        "records": int(totals_row["records"] or 0),
        "prompt_tokens": int(totals_row["prompt_tokens"] or 0),
        "completion_tokens": int(totals_row["completion_tokens"] or 0),
        "total_tokens": int(totals_row["total_tokens"] or 0),
        "top_processes": [
            {"process_name": str(row["process_name"] or ""), "total_tokens": int(row["total_tokens"] or 0)}
            for row in process_rows
        ],
    }


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _extract_weight(item: Any) -> int:
    if isinstance(item, dict):
        for key in ("count", "hits", "items", "mentions", "videos", "posts", "score", "total"):
            raw = item.get(key)
            if raw is not None:
                return max(1, _to_int(raw, default=1))
    return 1


def _extract_label(item: Any, *, keys: tuple[str, ...]) -> str:
    if isinstance(item, dict):
        for key in keys:
            label = str(item.get(key) or "").strip()
            if label:
                return label
    return str(item or "").strip()


def _safe_parse_dt(raw: Any) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _opportunity_id(label: str, *, index: int) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(label or "").strip().lower()).strip("-")
    if not slug:
        slug = f"opportunity-{index}"
    return f"{slug[:48]}-{index:02d}"


def _build_opportunity_bundle(
    *,
    now_iso: str,
    trend_report: dict[str, Any],
    insight_reports: list[dict[str, Any]],
    token_snapshot: dict[str, Any],
) -> dict[str, Any]:
    candidates: dict[str, dict[str, Any]] = {}
    source_presence: set[str] = set()
    total_signal_volume = 0
    window_bounds: list[datetime] = []

    trend_json = trend_report.get("report_json") if isinstance(trend_report.get("report_json"), dict) else {}
    trend_totals = trend_json.get("totals") if isinstance(trend_json.get("totals"), dict) else {}
    total_signal_volume += _to_int(trend_json.get("total_items") or trend_totals.get("items"), default=0)
    if trend_report:
        source_presence.add("youtube_channel_rss")
    trend_start = _safe_parse_dt(trend_report.get("window_start_utc")) if trend_report else None
    trend_end = _safe_parse_dt(trend_report.get("window_end_utc")) if trend_report else None
    if trend_start:
        window_bounds.append(trend_start)
    if trend_end:
        window_bounds.append(trend_end)

    def _add_candidate(
        *,
        key: str,
        title: str,
        thesis: str,
        source: str,
        report_key: str,
        weight: int,
    ) -> None:
        cleaned_title = str(title or "").strip()
        if not cleaned_title:
            return
        cleaned_source = str(source or "csi_analytics").strip().lower() or "csi_analytics"
        cleaned_report_key = str(report_key or "").strip()
        row = candidates.setdefault(
            key,
            {
                "title": cleaned_title,
                "thesis": str(thesis or "").strip() or f"Monitor {cleaned_title} for repeatable momentum.",
                "signal_score": 0,
                "source_mix": {},
                "evidence_refs": set(),
            },
        )
        row["title"] = row.get("title") or cleaned_title
        if not row.get("thesis"):
            row["thesis"] = str(thesis or "").strip()
        row["signal_score"] = int(row.get("signal_score") or 0) + max(1, int(weight))
        source_mix = row.get("source_mix") if isinstance(row.get("source_mix"), dict) else {}
        source_mix[cleaned_source] = int(source_mix.get(cleaned_source) or 0) + max(1, int(weight))
        row["source_mix"] = source_mix
        if cleaned_report_key:
            evidence_refs = row.get("evidence_refs")
            if not isinstance(evidence_refs, set):
                evidence_refs = set()
            evidence_refs.add(f"report:{cleaned_report_key}")
            row["evidence_refs"] = evidence_refs
        source_presence.add(cleaned_source)

    trend_report_key = str(trend_report.get("report_key") or "").strip()
    for theme in trend_json.get("top_themes", [])[:12] if isinstance(trend_json.get("top_themes"), list) else []:
        label = _extract_label(theme, keys=("theme", "label", "name"))
        _add_candidate(
            key=f"theme:{label.lower()}",
            title=f"Momentum theme: {label}",
            thesis=f"Theme '{label}' is accelerating in watched creator coverage.",
            source="youtube_channel_rss",
            report_key=trend_report_key,
            weight=_extract_weight(theme),
        )
    for channel in trend_json.get("top_channels", [])[:10] if isinstance(trend_json.get("top_channels"), list) else []:
        label = _extract_label(channel, keys=("channel_name", "channel", "name"))
        _add_candidate(
            key=f"channel:{label.lower()}",
            title=f"Channel momentum: {label}",
            thesis=f"Channel '{label}' has elevated signal volume and should be mined for reusable ideas.",
            source="youtube_channel_rss",
            report_key=trend_report_key,
            weight=_extract_weight(channel),
        )
    for narrative in trend_json.get("top_narratives", [])[:10] if isinstance(trend_json.get("top_narratives"), list) else []:
        label = _extract_label(narrative, keys=("narrative", "theme", "label", "name"))
        _add_candidate(
            key=f"narrative:{label.lower()}",
            title=f"Narrative candidate: {label}",
            thesis=f"Narrative '{label}' shows repeat mentions and may support a fast follow-up brief.",
            source="youtube_channel_rss",
            report_key=trend_report_key,
            weight=_extract_weight(narrative),
        )

    for report in insight_reports:
        report_type = str(report.get("report_type") or "").strip().lower()
        report_key = str(report.get("report_key") or "").strip()
        report_json = report.get("report_json") if isinstance(report.get("report_json"), dict) else {}
        totals = report_json.get("totals") if isinstance(report_json.get("totals"), dict) else {}
        total_signal_volume += _to_int(report_json.get("total_items") or totals.get("items"), default=0)
        source = "reddit_discovery" if "reddit" in report_type else "youtube_channel_rss"
        source_presence.add(source)
        insight_start = _safe_parse_dt(report.get("window_start_utc"))
        insight_end = _safe_parse_dt(report.get("window_end_utc"))
        if insight_start:
            window_bounds.append(insight_start)
        if insight_end:
            window_bounds.append(insight_end)
        for topic in report_json.get("top_topics", [])[:12] if isinstance(report_json.get("top_topics"), list) else []:
            label = _extract_label(topic, keys=("topic", "name", "label"))
            _add_candidate(
                key=f"topic:{label.lower()}",
                title=f"Topic breakout: {label}",
                thesis=f"Topic '{label}' is repeatedly present in {report_type or 'insight'} output.",
                source=source,
                report_key=report_key,
                weight=_extract_weight(topic),
            )
        for subreddit in report_json.get("top_subreddits", [])[:10] if isinstance(report_json.get("top_subreddits"), list) else []:
            label = _extract_label(subreddit, keys=("subreddit", "name", "label"))
            _add_candidate(
                key=f"subreddit:{label.lower()}",
                title=f"Community pulse: {label}",
                thesis=f"Subreddit '{label}' is generating meaningful activity relevant to current trend windows.",
                source=source,
                report_key=report_key,
                weight=_extract_weight(subreddit),
            )
        for narrative in report_json.get("top_narratives", [])[:10] if isinstance(report_json.get("top_narratives"), list) else []:
            label = _extract_label(narrative, keys=("narrative", "theme", "label", "name"))
            _add_candidate(
                key=f"narrative:{label.lower()}",
                title=f"Narrative candidate: {label}",
                thesis=f"Narrative '{label}' appeared in repeated insight windows and warrants targeted follow-up.",
                source=source,
                report_key=report_key,
                weight=_extract_weight(narrative),
            )

    ordered = sorted(
        candidates.values(),
        key=lambda row: (
            int(row.get("signal_score") or 0),
            len(row.get("source_mix", {})) if isinstance(row.get("source_mix"), dict) else 0,
            str(row.get("title") or ""),
        ),
        reverse=True,
    )
    opportunities: list[dict[str, Any]] = []
    for idx, candidate in enumerate(ordered[:8], start=1):
        source_mix = candidate.get("source_mix") if isinstance(candidate.get("source_mix"), dict) else {}
        source_count = max(1, len(source_mix))
        signal_score = int(candidate.get("signal_score") or 0)
        evidence_refs_raw = candidate.get("evidence_refs")
        if isinstance(evidence_refs_raw, set):
            evidence_refs = sorted(str(item) for item in evidence_refs_raw if str(item).strip())
        elif isinstance(evidence_refs_raw, list):
            evidence_refs = [str(item) for item in evidence_refs_raw if str(item).strip()]
        else:
            evidence_refs = []
        novelty_score = min(0.95, round(0.35 + (0.1 * min(4, source_count - 1)) + (0.04 * min(6, signal_score)), 3))
        confidence_score = min(0.95, round(0.5 + (0.06 * min(6, signal_score)) + (0.08 * min(3, source_count - 1)), 3))
        risk_flags: list[str] = []
        if source_count == 1:
            risk_flags.append("single_source")
        if signal_score <= 2:
            risk_flags.append("low_signal_density")
        opportunities.append(
            {
                "opportunity_id": _opportunity_id(str(candidate.get("title") or ""), index=idx),
                "title": str(candidate.get("title") or ""),
                "thesis": str(candidate.get("thesis") or ""),
                "source_mix": {str(k): int(v or 0) for k, v in source_mix.items()},
                "evidence_refs": evidence_refs,
                "novelty_score": novelty_score,
                "confidence_score": confidence_score,
                "risk_flags": risk_flags,
                "recommended_action": "Draft a focused trend brief and trigger one targeted follow-up research loop.",
                "followup_task_template": (
                    "Investigate opportunity '{{title}}' using latest CSI evidence, "
                    "summarize executable actions, and estimate 7-day upside."
                ),
            }
        )

    now_dt = _safe_parse_dt(now_iso) or datetime.now(timezone.utc)
    latest_bound = max(window_bounds) if window_bounds else now_dt
    earliest_bound = min(window_bounds) if window_bounds else now_dt
    freshness_minutes = max(0, int((now_dt.timestamp() - latest_bound.timestamp()) / 60.0))
    coverage_score = min(
        1.0,
        round(
            (0.35 if trend_report else 0.0)
            + (0.2 if insight_reports else 0.0)
            + (0.2 if len(source_presence) >= 2 else 0.0)
            + (0.25 * min(1.0, len(opportunities) / 6.0)),
            3,
        ),
    )
    if token_snapshot.get("records", 0) == 0 and total_signal_volume == 0:
        delivery_health = "blocked"
    elif len(opportunities) == 0:
        delivery_health = "degraded"
    else:
        delivery_health = "ok"
    return {
        "window_start_utc": earliest_bound.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_end_utc": latest_bound.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "confidence_method": "heuristic",
        "quality_summary": {
            "signal_volume": int(total_signal_volume),
            "freshness_minutes": int(freshness_minutes),
            "delivery_health": delivery_health,
            "coverage_score": float(coverage_score),
        },
        "opportunities": opportunities,
    }


def _build_markdown(
    *,
    now_iso: str,
    window_hours: int,
    trend_report: dict[str, Any],
    insight_reports: list[dict[str, Any]],
    token_snapshot: dict[str, Any],
    opportunity_bundle: dict[str, Any],
) -> str:
    lines: list[str] = []
    lines.append("# CSI Report Product")
    lines.append("")
    lines.append(f"- generated_at_utc: {now_iso}")
    lines.append(f"- coverage_window_hours: {int(window_hours)}")
    lines.append("")

    lines.append("## Token Usage Snapshot")
    lines.append(
        f"- total_tokens={int(token_snapshot.get('total_tokens') or 0)} "
        f"(prompt={int(token_snapshot.get('prompt_tokens') or 0)}, completion={int(token_snapshot.get('completion_tokens') or 0)})"
    )
    lines.append(f"- records={int(token_snapshot.get('records') or 0)}")
    for item in token_snapshot.get("top_processes", [])[:8]:
        lines.append(f"- {item['process_name']}: {int(item['total_tokens'])}")
    lines.append("")

    lines.append("## Latest Trend Report")
    if trend_report:
        lines.append(f"- report_key: {str(trend_report.get('report_key') or '')}")
        lines.append(f"- window: {str(trend_report.get('window_start_utc') or '')} -> {str(trend_report.get('window_end_utc') or '')}")
        trend_json = trend_report.get("report_json") if isinstance(trend_report.get("report_json"), dict) else {}
        top_channels = trend_json.get("top_channels") if isinstance(trend_json.get("top_channels"), list) else []
        top_themes = trend_json.get("top_themes") if isinstance(trend_json.get("top_themes"), list) else []
        if top_channels:
            lines.append("- top_channels:")
            for channel in top_channels[:5]:
                lines.append(f"  - {json.dumps(channel, ensure_ascii=False)}")
        if top_themes:
            lines.append("- top_themes:")
            for theme in top_themes[:6]:
                lines.append(f"  - {json.dumps(theme, ensure_ascii=False)}")
    else:
        lines.append("- no trend report in window")
    lines.append("")

    lines.append("## Latest Insight Reports")
    if insight_reports:
        for report in insight_reports[:4]:
            lines.append(
                "- "
                f"{str(report.get('report_type') or '')}: "
                f"{str(report.get('window_start_utc') or '')} -> {str(report.get('window_end_utc') or '')} "
                f"(key={str(report.get('report_key') or '')})"
            )
    else:
        lines.append("- no insight reports in window")
    lines.append("")
    lines.append("## Ranked Opportunities")
    opportunities = opportunity_bundle.get("opportunities") if isinstance(opportunity_bundle.get("opportunities"), list) else []
    if opportunities:
        quality_summary = opportunity_bundle.get("quality_summary") if isinstance(opportunity_bundle.get("quality_summary"), dict) else {}
        lines.append(f"- confidence_method: {str(opportunity_bundle.get('confidence_method') or 'heuristic')}")
        lines.append(f"- coverage_score: {quality_summary.get('coverage_score')}")
        lines.append(f"- freshness_minutes: {quality_summary.get('freshness_minutes')}")
        for item in opportunities[:6]:
            lines.append(
                "- "
                f"{str(item.get('title') or '')} "
                f"(confidence={item.get('confidence_score')}, novelty={item.get('novelty_score')}, "
                f"signals={sum(int(v or 0) for v in (item.get('source_mix') or {}).values())})"
            )
            lines.append(f"  - action: {str(item.get('recommended_action') or '')}")
    else:
        lines.append("- no ranked opportunities in window")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize hourly CSI report product and emit UA readiness event.")
    parser.add_argument("--db-path", default="/opt/universal_agent/CSI_Ingester/development/var/csi.db")
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--output-root", default="/opt/universal_agent/artifacts/csi-reports")
    parser.add_argument(
        "--state-path",
        default="/opt/universal_agent/CSI_Ingester/development/var/rss_report_product_state.json",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    conn = connect(Path(args.db_path).expanduser())
    ensure_schema(conn)
    config = load_config()

    now = datetime.now(timezone.utc)
    hour_key = now.strftime("%Y%m%d%H")
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    hourly_report_key = f"hourly_report_product:{config.instance_id}:{hour_key}"
    opportunity_report_key = f"opportunity_bundle:{config.instance_id}:{hour_key}"
    opportunity_bundle_id = f"bundle:{config.instance_id}:{hour_key}"

    state_path = Path(args.state_path).expanduser()
    state = _load_state(state_path)
    if not args.force and str(state.get("last_hour_key") or "") == hour_key:
        print(f"CSI_REPORT_PRODUCT_SKIPPED=already_sent_hour:{hour_key}")
        conn.close()
        return 0

    trend_report = _latest_trend_report(conn, max(1, int(args.window_hours)))
    insight_reports = _latest_insight_reports(conn, max(1, int(args.window_hours)))
    token_snapshot = _token_snapshot(conn, max(1, int(args.window_hours)))
    opportunity_bundle = _build_opportunity_bundle(
        now_iso=now_iso,
        trend_report=trend_report,
        insight_reports=insight_reports,
        token_snapshot=token_snapshot,
    )
    opportunity_bundle["bundle_id"] = opportunity_bundle_id
    opportunity_bundle["report_key"] = opportunity_report_key

    day = now.strftime("%Y-%m-%d")
    output_root = Path(args.output_root).expanduser() / day
    output_dir = output_root / "product"
    output_dir.mkdir(parents=True, exist_ok=True)
    opportunity_dir = output_root / "opportunities"
    opportunity_dir.mkdir(parents=True, exist_ok=True)

    base_name = f"hourly_{hour_key}"
    md_path = output_dir / f"{base_name}.md"
    json_path = output_dir / f"{base_name}.json"
    opportunity_md_path = opportunity_dir / f"{base_name}_bundle.md"
    opportunity_json_path = opportunity_dir / f"{base_name}_bundle.json"

    markdown = _build_markdown(
        now_iso=now_iso,
        window_hours=max(1, int(args.window_hours)),
        trend_report=trend_report,
        insight_reports=insight_reports,
        token_snapshot=token_snapshot,
        opportunity_bundle=opportunity_bundle,
    )
    payload = {
        "generated_at_utc": now_iso,
        "window_hours": max(1, int(args.window_hours)),
        "report_key": hourly_report_key,
        "trend_report": trend_report,
        "insight_reports": insight_reports,
        "token_snapshot": token_snapshot,
        "opportunity_bundle": opportunity_bundle,
        "markdown_path": str(md_path),
    }

    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    opportunity_markdown_lines = [
        "# CSI Opportunity Bundle",
        "",
        f"- generated_at_utc: {now_iso}",
        f"- bundle_id: {opportunity_bundle_id}",
        f"- report_key: {opportunity_report_key}",
        f"- window: {opportunity_bundle.get('window_start_utc')} -> {opportunity_bundle.get('window_end_utc')}",
        f"- confidence_method: {opportunity_bundle.get('confidence_method')}",
        "",
        "## Quality Summary",
        f"- {json.dumps(opportunity_bundle.get('quality_summary') or {}, ensure_ascii=False)}",
        "",
        "## Ranked Opportunities",
    ]
    opportunities = (
        opportunity_bundle.get("opportunities")
        if isinstance(opportunity_bundle.get("opportunities"), list)
        else []
    )
    if opportunities:
        for item in opportunities:
            opportunity_markdown_lines.append(
                f"- {item.get('title')} "
                f"(confidence={item.get('confidence_score')}, novelty={item.get('novelty_score')})"
            )
            opportunity_markdown_lines.append(f"  - thesis: {item.get('thesis')}")
            opportunity_markdown_lines.append(f"  - action: {item.get('recommended_action')}")
    else:
        opportunity_markdown_lines.append("- No opportunities identified in this window.")
    opportunity_markdown = "\n".join(opportunity_markdown_lines).strip() + "\n"
    opportunity_md_path.write_text(opportunity_markdown, encoding="utf-8")
    opportunity_json_payload = {
        "generated_at_utc": now_iso,
        **opportunity_bundle,
        "artifact_paths": {
            "markdown": str(opportunity_md_path),
            "json": str(opportunity_json_path),
        },
    }
    opportunity_json_path.write_text(
        json.dumps(opportunity_json_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    opportunity_bundle_store.upsert_bundle(
        conn,
        bundle_id=opportunity_bundle_id,
        report_key=opportunity_report_key,
        window_start_utc=str(opportunity_bundle.get("window_start_utc") or now_iso),
        window_end_utc=str(opportunity_bundle.get("window_end_utc") or now_iso),
        confidence_method=str(opportunity_bundle.get("confidence_method") or "heuristic"),
        quality_summary=(
            opportunity_bundle.get("quality_summary")
            if isinstance(opportunity_bundle.get("quality_summary"), dict)
            else {}
        ),
        opportunities=[item for item in opportunities if isinstance(item, dict)],
        artifact_markdown_path=str(opportunity_md_path),
        artifact_json_path=str(opportunity_json_path),
    )

    event = CreatorSignalEvent(
        event_id=f"csi:report_product:{config.instance_id}:{hour_key}",
        dedupe_key=f"csi:report_product:{config.instance_id}:{hour_key}",
        source="csi_analytics",
        event_type="report_product_ready",
        occurred_at=now_iso,
        received_at=now_iso,
        subject={
            "report_type": "hourly_report_product",
            "report_key": hourly_report_key,
            "hour_key": hour_key,
            "generated_at_utc": now_iso,
            "window_hours": max(1, int(args.window_hours)),
            "artifact_paths": {
                "markdown": str(md_path),
                "json": str(json_path),
            },
            "token_snapshot": token_snapshot,
            "has_trend_report": bool(trend_report),
            "insight_report_count": len(insight_reports),
        },
        routing={
            "pipeline": "csi_report_product",
            "priority": "standard",
            "tags": ["csi", "report", "product", "hourly"],
        },
        metadata={
            "source_adapter": "csi_report_product_finalize_v2",
            "report_key": hourly_report_key,
            "bundle_id": opportunity_bundle_id,
        },
    )
    delivered, status_code, _resp = emit_and_track(conn, config=config, event=event, retry_count=3)

    opportunity_event = CreatorSignalEvent(
        event_id=f"csi:opportunity_bundle:{config.instance_id}:{hour_key}",
        dedupe_key=f"csi:opportunity_bundle:{config.instance_id}:{hour_key}",
        source="csi_analytics",
        event_type="opportunity_bundle_ready",
        occurred_at=now_iso,
        received_at=now_iso,
        subject={
            "report_type": "opportunity_bundle",
            "bundle_id": opportunity_bundle_id,
            "report_key": opportunity_report_key,
            "hour_key": hour_key,
            "generated_at_utc": now_iso,
            "window_hours": max(1, int(args.window_hours)),
            "window_start_utc": opportunity_bundle.get("window_start_utc"),
            "window_end_utc": opportunity_bundle.get("window_end_utc"),
            "confidence_method": opportunity_bundle.get("confidence_method"),
            "quality_summary": opportunity_bundle.get("quality_summary"),
            "opportunities": [item for item in opportunities if isinstance(item, dict)],
            "artifact_paths": {
                "markdown": str(opportunity_md_path),
                "json": str(opportunity_json_path),
            },
        },
        routing={
            "pipeline": "csi_opportunity_bundle",
            "priority": "standard",
            "tags": ["csi", "report", "opportunity", "bundle"],
        },
        metadata={
            "source_adapter": "csi_report_product_finalize_v2",
            "bundle_id": opportunity_bundle_id,
            "report_key": opportunity_report_key,
        },
    )
    opportunity_delivered, opportunity_status_code, _opp_resp = emit_and_track(
        conn,
        config=config,
        event=opportunity_event,
        retry_count=3,
    )
    conn.close()

    _save_state(
        state_path,
        {
            "last_hour_key": hour_key,
            "last_sent_at": now_iso,
            "last_status_code": int(status_code),
            "last_delivered": bool(delivered),
            "last_opportunity_status_code": int(opportunity_status_code),
            "last_opportunity_delivered": bool(opportunity_delivered),
            "report_key": hourly_report_key,
            "opportunity_report_key": opportunity_report_key,
            "opportunity_bundle_id": opportunity_bundle_id,
            "markdown": str(md_path),
            "json": str(json_path),
            "opportunity_markdown": str(opportunity_md_path),
            "opportunity_json": str(opportunity_json_path),
        },
    )

    print(f"CSI_REPORT_PRODUCT_HOUR={hour_key}")
    print(f"CSI_REPORT_PRODUCT_MD={md_path}")
    print(f"CSI_REPORT_PRODUCT_JSON={json_path}")
    print(f"CSI_OPPORTUNITY_BUNDLE_MD={opportunity_md_path}")
    print(f"CSI_OPPORTUNITY_BUNDLE_JSON={opportunity_json_path}")
    print(f"CSI_REPORT_PRODUCT_EMIT_STATUS={status_code}")
    print(f"CSI_OPPORTUNITY_BUNDLE_EMIT_STATUS={opportunity_status_code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
