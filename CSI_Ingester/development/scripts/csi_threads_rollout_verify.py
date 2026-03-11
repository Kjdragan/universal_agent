#!/usr/bin/env python3
"""Post-Phase-1 Threads rollout verification (live probe + DB evidence checks)."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from csi_ingester.config import load_config
from csi_ingester.store import source_state as source_state_store

THREADS_SOURCES = ("threads_owned", "threads_trends_seeded", "threads_trends_broad")
THREADS_CREDENTIAL_KEYS = (
    "THREADS_APP_ID",
    "THREADS_APP_SECRET",
    "THREADS_USER_ID",
    "THREADS_ACCESS_TOKEN",
    "THREADS_TOKEN_EXPIRES_AT",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(raw: Any) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _truthy_env(key: str, default: bool = False) -> bool:
    raw = str(os.getenv(key) or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _run_probe(
    *,
    config_path: str,
    limit: int,
    max_terms: int,
    seed_term: str,
    require_all: bool,
    probe_env_path: str,
) -> tuple[dict[str, Any], str]:
    probe_script = SCRIPT_ROOT / "scripts" / "csi_threads_probe.py"
    cmd = [
        sys.executable,
        str(probe_script),
        "--config-path",
        str(config_path),
        "--source",
        "all",
        "--limit",
        str(limit),
        "--max-terms",
        str(max_terms),
        "--json",
        "--quiet",
    ]
    if seed_term.strip():
        cmd.extend(["--seed-term", seed_term.strip()])
    if require_all:
        cmd.append("--require-all")

    env = _build_probe_env(probe_env_path)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    stdout_lines = [line.strip() for line in str(proc.stdout or "").splitlines() if line.strip()]
    stderr_lines = [line.strip() for line in str(proc.stderr or "").splitlines() if line.strip()]

    payload: dict[str, Any] = {}
    if stdout_lines:
        tail = stdout_lines[-1]
        try:
            parsed = json.loads(tail)
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = {}

    if not payload:
        payload = {
            "ok_count": 0,
            "fail_count": 1,
            "all_passed": False,
            "probe_parse_error": True,
        }

    payload["exit_code"] = int(proc.returncode)
    if stderr_lines:
        payload["stderr"] = stderr_lines[-8:]
    return payload, "\n".join(stdout_lines[-20:])


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists() or not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        clean_key = key.strip()
        if not clean_key:
            continue
        clean_value = value.strip()
        if len(clean_value) >= 2 and ((clean_value[0] == clean_value[-1] == '"') or (clean_value[0] == clean_value[-1] == "'")):
            clean_value = clean_value[1:-1]
        values[clean_key] = clean_value
    return values


def _build_probe_env(probe_env_path: str) -> dict[str, str]:
    env = dict(os.environ)
    if all(str(env.get(key) or "").strip() for key in THREADS_CREDENTIAL_KEYS):
        return env
    env_path = Path(str(probe_env_path or "")).expanduser()
    env_values = _parse_env_file(env_path)
    for key in THREADS_CREDENTIAL_KEYS:
        current = str(env.get(key) or "").strip()
        if current:
            continue
        candidate = str(env_values.get(key) or "").strip()
        if candidate:
            env[key] = candidate
    return env


def _event_stats(conn: sqlite3.Connection, *, lookback_hours: int) -> dict[str, Any]:
    start = _utc_now() - timedelta(hours=max(1, int(lookback_hours)))
    rows = conn.execute(
        "SELECT source, occurred_at, received_at, delivered FROM events WHERE source IN (?,?,?)",
        THREADS_SOURCES,
    ).fetchall()
    out = {
        source: {
            "events": 0,
            "delivered": 0,
            "last_seen_utc": "",
            "events_received": 0,
            "delivered_received": 0,
            "last_received_utc": "",
        }
        for source in THREADS_SOURCES
    }
    for row in rows:
        source = str(row["source"] or "")
        if source not in out:
            continue
        occurred = _parse_iso(row["occurred_at"])
        received = _parse_iso(row["received_at"])
        delivered = int(row["delivered"] or 0) == 1
        if occurred is not None and occurred >= start:
            out[source]["events"] += 1
            if delivered:
                out[source]["delivered"] += 1
            last_seen = _parse_iso(out[source]["last_seen_utc"])
            if last_seen is None or occurred > last_seen:
                out[source]["last_seen_utc"] = occurred.strftime("%Y-%m-%dT%H:%M:%SZ")
        if received is not None and received >= start:
            out[source]["events_received"] += 1
            if delivered:
                out[source]["delivered_received"] += 1
            last_received = _parse_iso(out[source]["last_received_utc"])
            if last_received is None or received > last_received:
                out[source]["last_received_utc"] = received.strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


def _adapter_state_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    keys = {
        "threads_trends_seeded": "threads_trends_seeded:state",
        "threads_trends_broad": "threads_trends_broad:state",
        "threads_webhook": "threads_webhook:state",
    }
    out: dict[str, Any] = {}
    for source, key in keys.items():
        state = source_state_store.get_state(conn, key)
        if not isinstance(state, dict):
            out[source] = {}
            continue
        last_cycle = state.get("last_cycle") if isinstance(state.get("last_cycle"), dict) else {}
        out[source] = {
            "last_poll_at": str(state.get("last_poll_at") or ""),
            "last_cycle": last_cycle,
        }
        if source == "threads_webhook":
            out[source] = {
                "last_ingested_at": str(state.get("last_ingested_at") or ""),
                "last_cycle": state.get("last_cycle") if isinstance(state.get("last_cycle"), dict) else {},
                "totals": state.get("totals") if isinstance(state.get("totals"), dict) else {},
            }
    return out


def _seeded_probe_result_count(seeded_probe: dict[str, Any]) -> int:
    total = 0
    terms = seeded_probe.get("terms") if isinstance(seeded_probe.get("terms"), list) else []
    for term_row in terms:
        if not isinstance(term_row, dict):
            continue
        total += max(0, int(term_row.get("results") or 0))
    return int(total)


def _seeded_no_event_signal(
    *,
    seeded_rows: int,
    seeded_probe_ok: int,
    seeded_probe_results: int,
    seeded_polled_recently: bool,
    seeded_cycle_hits: int,
    seeded_cycle_new_hits: int,
    seeded_cycle_rate_limited: bool,
    seeded_cycle_timeout_aborted: bool,
    require_seeded_events: bool,
) -> tuple[str, bool]:
    # If seeded polling was constrained by rate limits/timeouts, keep this as
    # a non-blocking signal and avoid implying the source is broken.
    if seeded_cycle_rate_limited or seeded_cycle_timeout_aborted:
        return ("seeded_poll_constrained_recently", False)
    if seeded_probe_ok > 0 and seeded_probe_results > 0:
        if seeded_rows > 0:
            return ("", False)
        return ("seeded_live_but_no_new_events", False)
    if seeded_polled_recently and seeded_cycle_hits > 0 and seeded_cycle_new_hits <= 0:
        return ("seeded_polled_but_no_new_media_hits", False)
    if seeded_rows > 0:
        return ("no_seeded_events_in_lookback_but_seeded_analysis_present", False)
    if seeded_polled_recently:
        return ("seeded_polled_but_no_hits", False)
    if require_seeded_events:
        return ("no_seeded_events_in_lookback", True)
    return ("no_seeded_events_in_lookback", False)


def _webhook_activity_signal(
    *,
    webhook_enabled: bool,
    webhook_last_ingested: datetime | None,
    lookback_hours: int,
    require_webhook_activity: bool,
) -> tuple[str, bool]:
    if not webhook_enabled:
        return ("", False)
    threshold = _utc_now() - timedelta(hours=max(1, int(lookback_hours)))
    recent = bool(webhook_last_ingested is not None and webhook_last_ingested >= threshold)
    if recent:
        return ("", False)
    if require_webhook_activity:
        return ("webhook_enabled_but_no_ingest_in_lookback", True)
    return ("webhook_enabled_but_no_ingest_in_lookback", False)


def _analysis_stats(conn: sqlite3.Connection, *, lookback_hours: int) -> dict[str, Any]:
    lookback_expr = f"-{max(1, int(lookback_hours))} hours"
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_rows,
            SUM(CASE WHEN source='threads_owned' THEN 1 ELSE 0 END) AS owned_rows,
            SUM(CASE WHEN source='threads_trends_seeded' THEN 1 ELSE 0 END) AS seeded_rows,
            SUM(CASE WHEN source='threads_trends_broad' THEN 1 ELSE 0 END) AS broad_rows,
            MAX(analyzed_at) AS last_analyzed_at
        FROM threads_event_analysis
        WHERE analyzed_at >= datetime('now', ?)
        """,
        (lookback_expr,),
    ).fetchone()
    return {
        "total_rows": int((row["total_rows"] if row else 0) or 0),
        "owned_rows": int((row["owned_rows"] if row else 0) or 0),
        "seeded_rows": int((row["seeded_rows"] if row else 0) or 0),
        "broad_rows": int((row["broad_rows"] if row else 0) or 0),
        "last_analyzed_at": str((row["last_analyzed_at"] if row else "") or ""),
    }


def _report_stats(conn: sqlite3.Connection, *, lookback_hours: int) -> dict[str, Any]:
    lookback_expr = f"-{max(1, int(lookback_hours))} hours"
    threads_row = conn.execute(
        """
        SELECT COUNT(*) AS c, MAX(created_at) AS last_created
        FROM insight_reports
        WHERE report_type='threads_trend_report' AND created_at >= datetime('now', ?)
        """,
        (lookback_expr,),
    ).fetchone()
    latest_brief = conn.execute(
        """
        SELECT brief_key, created_at, brief_json
        FROM global_trend_briefs
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    brief_key = str((latest_brief["brief_key"] if latest_brief else "") or "")
    brief_created = str((latest_brief["created_at"] if latest_brief else "") or "")
    brief_threads_total = 0
    if latest_brief is not None:
        try:
            brief_json = json.loads(str(latest_brief["brief_json"] or "{}"))
        except Exception:
            brief_json = {}
        if isinstance(brief_json, dict):
            source_totals = brief_json.get("source_totals") if isinstance(brief_json.get("source_totals"), dict) else {}
            brief_threads_total = int(source_totals.get("threads") or 0)

    return {
        "threads_trend_reports": int((threads_row["c"] if threads_row else 0) or 0),
        "threads_trend_report_last_created": str((threads_row["last_created"] if threads_row else "") or ""),
        "latest_global_brief_key": brief_key,
        "latest_global_brief_created": brief_created,
        "latest_global_brief_threads_total": brief_threads_total,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-path", default="/opt/universal_agent/CSI_Ingester/development/config/config.yaml")
    parser.add_argument("--db-path", default="/var/lib/universal-agent/csi/csi.db")
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--probe-limit", type=int, default=4)
    parser.add_argument("--probe-max-terms", type=int, default=4)
    parser.add_argument("--seed-term", default="", help="Optional single seeded term for deterministic probe")
    parser.add_argument("--require-probe-all", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--probe-env-path",
        default="/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env",
        help="Optional env file to hydrate THREADS_* credentials for probe subprocess",
    )
    parser.add_argument(
        "--require-seeded-events",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Fail verify when seeded events are zero in lookback",
    )
    parser.add_argument(
        "--require-webhook-activity",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Fail verify when webhook is enabled but no webhook ingest occurred in lookback",
    )
    parser.add_argument("--write-json", default="", help="Optional output JSON file path")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config_path)
    sources = cfg.raw.get("sources") if isinstance(cfg.raw, dict) else {}
    if not isinstance(sources, dict):
        print("THREADS_ROLLOUT_VERIFY_FAIL reason=invalid_config")
        return 2

    enabled_map = {
        source: bool(isinstance(sources.get(source), dict) and sources.get(source, {}).get("enabled", False))
        for source in THREADS_SOURCES
    }
    all_enabled = all(enabled_map.values())

    probe_payload, probe_tail = _run_probe(
        config_path=args.config_path,
        limit=max(1, int(args.probe_limit)),
        max_terms=max(1, int(args.probe_max_terms)),
        seed_term=str(args.seed_term or ""),
        require_all=bool(args.require_probe_all),
        probe_env_path=str(args.probe_env_path or ""),
    )

    db_path = Path(args.db_path).expanduser()
    db_exists = db_path.exists()
    event_stats: dict[str, Any] = {}
    analysis_stats: dict[str, Any] = {}
    report_stats: dict[str, Any] = {}
    adapter_state: dict[str, Any] = {}
    if db_exists:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            event_stats = _event_stats(conn, lookback_hours=max(1, int(args.lookback_hours)))
            analysis_stats = _analysis_stats(conn, lookback_hours=max(1, int(args.lookback_hours)))
            report_stats = _report_stats(conn, lookback_hours=max(1, int(args.lookback_hours)))
            adapter_state = _adapter_state_stats(conn)
        finally:
            conn.close()

    warnings: list[str] = []
    failures: list[str] = []
    if not all_enabled:
        failures.append("threads_sources_not_all_enabled")
    probe_exit_raw = probe_payload.get("exit_code")
    try:
        probe_exit = int(probe_exit_raw if probe_exit_raw is not None else 1)
    except Exception:
        probe_exit = 1
    if probe_exit != 0:
        failures.append("live_probe_failed")
    if not db_exists:
        failures.append("db_missing")
    if db_exists:
        owned_events = int((event_stats.get("threads_owned") or {}).get("events_received") or 0)
        seeded_events = int((event_stats.get("threads_trends_seeded") or {}).get("events_received") or 0)
        broad_events = int((event_stats.get("threads_trends_broad") or {}).get("events_received") or 0)
        seeded_rows = int(analysis_stats.get("seeded_rows") or 0)
        broad_rows = int(analysis_stats.get("broad_rows") or 0)
        seeded_state = adapter_state.get("threads_trends_seeded") if isinstance(adapter_state.get("threads_trends_seeded"), dict) else {}
        seeded_last_poll = _parse_iso(seeded_state.get("last_poll_at")) if seeded_state else None
        seeded_cycle = seeded_state.get("last_cycle") if isinstance(seeded_state.get("last_cycle"), dict) else {}
        seeded_cycle_hits = int(seeded_cycle.get("total_hits") or 0)
        seeded_cycle_new_hits = int(seeded_cycle.get("new_media_hits") or 0)
        seeded_cycle_rate_limited = bool(seeded_cycle.get("rate_limited_cycle"))
        seeded_cycle_timeout_aborted = bool(seeded_cycle.get("timeout_aborted_cycle"))
        seeded_polled_recently = bool(seeded_last_poll is not None and seeded_last_poll >= (_utc_now() - timedelta(hours=max(1, int(args.lookback_hours)))))
        probe_source_results = probe_payload.get("source_results") if isinstance(probe_payload.get("source_results"), dict) else {}
        seeded_probe = probe_source_results.get("seeded") if isinstance(probe_source_results.get("seeded"), dict) else {}
        seeded_probe_ok = int(seeded_probe.get("ok") or 0)
        seeded_probe_results = _seeded_probe_result_count(seeded_probe)
        if owned_events <= 0:
            failures.append("no_owned_events_in_lookback")
        if seeded_events <= 0:
            seeded_signal, seeded_is_failure = _seeded_no_event_signal(
                seeded_rows=seeded_rows,
                seeded_probe_ok=seeded_probe_ok,
                seeded_probe_results=seeded_probe_results,
                seeded_polled_recently=seeded_polled_recently,
                seeded_cycle_hits=seeded_cycle_hits,
                seeded_cycle_new_hits=seeded_cycle_new_hits,
                seeded_cycle_rate_limited=seeded_cycle_rate_limited,
                seeded_cycle_timeout_aborted=seeded_cycle_timeout_aborted,
                require_seeded_events=bool(args.require_seeded_events),
            )
            if seeded_is_failure:
                failures.append(seeded_signal)
            elif seeded_signal:
                warnings.append(seeded_signal)
        if broad_events <= 0:
            if broad_rows > 0:
                warnings.append("no_broad_events_in_lookback_but_broad_analysis_present")
            else:
                warnings.append("no_broad_events_in_lookback")
        if int(analysis_stats.get("total_rows") or 0) <= 0:
            failures.append("no_threads_analysis_rows_in_lookback")
        if int(report_stats.get("threads_trend_reports") or 0) <= 0:
            warnings.append("no_threads_trend_report_in_lookback")
        if int(report_stats.get("latest_global_brief_threads_total") or 0) <= 0:
            warnings.append("latest_global_brief_threads_total_zero")
        webhook_enabled = _truthy_env("CSI_THREADS_WEBHOOK_ENABLED", False)
        webhook_state = adapter_state.get("threads_webhook") if isinstance(adapter_state.get("threads_webhook"), dict) else {}
        webhook_last_ingested = _parse_iso(webhook_state.get("last_ingested_at")) if webhook_state else None
        webhook_signal, webhook_is_failure = _webhook_activity_signal(
            webhook_enabled=webhook_enabled,
            webhook_last_ingested=webhook_last_ingested,
            lookback_hours=int(args.lookback_hours),
            require_webhook_activity=bool(args.require_webhook_activity),
        )
        if webhook_is_failure:
            failures.append(webhook_signal)
        elif webhook_signal:
            warnings.append(webhook_signal)

    payload = {
        "verified_at_utc": _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config_path": str(args.config_path),
        "db_path": str(db_path),
        "lookback_hours": int(max(1, int(args.lookback_hours))),
        "threads_enabled": enabled_map,
        "all_threads_enabled": all_enabled,
        "probe": probe_payload,
        "probe_tail": probe_tail,
        "events": event_stats,
        "analysis": analysis_stats,
        "reports": report_stats,
        "adapter_state": adapter_state,
        "webhook_enabled": _truthy_env("CSI_THREADS_WEBHOOK_ENABLED", False),
        "warnings": warnings,
        "failures": failures,
        "ok": len(failures) == 0,
    }

    if not args.quiet:
        print(f"THREADS_ROLLOUT_VERIFY_OK={1 if payload['ok'] else 0}")
        print(f"THREADS_ROLLOUT_VERIFY_FAILURES={len(failures)}")
        print(f"THREADS_ROLLOUT_VERIFY_WARNINGS={len(warnings)}")
        print(f"THREADS_ROLLOUT_VERIFY_PROBE_EXIT={probe_exit}")
        print(f"THREADS_ROLLOUT_VERIFY_EVENTS={json.dumps(event_stats, sort_keys=True)}")
        print(f"THREADS_ROLLOUT_VERIFY_ANALYSIS={json.dumps(analysis_stats, sort_keys=True)}")
        print(f"THREADS_ROLLOUT_VERIFY_REPORTS={json.dumps(report_stats, sort_keys=True)}")
        if failures:
            print(f"THREADS_ROLLOUT_VERIFY_FAILURE_LIST={','.join(failures)}")
        if warnings:
            print(f"THREADS_ROLLOUT_VERIFY_WARNING_LIST={','.join(warnings)}")

    if str(args.write_json or "").strip():
        out_path = Path(str(args.write_json)).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        if not args.quiet:
            print(f"THREADS_ROLLOUT_VERIFY_JSON={out_path}")

    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
