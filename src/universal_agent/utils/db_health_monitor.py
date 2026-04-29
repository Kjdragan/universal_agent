"""Database health monitor for heartbeat integration.

Checks all system databases for anomalies and returns structured
HeartbeatFinding objects that can be injected into the heartbeat prompt.

This module is deterministic Python — no LLM cost for detection.
Findings are surfaced to Simone ONLY during pure health-check heartbeats
(task_focused=False). They are never injected into task-dispatch runs.

Databases checked:
- runtime_state.db: stale runs, task hub backlogs, stale delegations
- activity_state.db: pending signal cards, proactive backlog
- vp_state.db: stuck VP missions
- csi.db: adapter health, source freshness, unemitted events, dedupe bloat
- youtube_playlist_watcher_state.json: stale watcher state
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any

from universal_agent.utils.heartbeat_findings_schema import HeartbeatFinding

logger = logging.getLogger(__name__)

# ── Thresholds ──────────────────────────────────────────────────────────
STALE_RUN_HOURS = 2.0          # runs stuck in running/queued > N hours
STALE_DELEGATION_HOURS = 4.0   # delegated tasks without VP progress > N hours
TASK_BACKLOG_WARN = 20         # open tasks older than 24h
SIGNAL_CARDS_PENDING_WARN = 50 # pending signal cards
CSI_CONSECUTIVE_FAIL_WARN = 3  # adapter consecutive failures
CSI_SOURCE_STALE_HOURS = 12.0  # channels not polled in > N hours
CSI_UNEMITTED_WARN = 100       # events not yet batched/emitted
CSI_DEDUPE_BLOAT_WARN = 50_000 # dedupe table entries
WATCHER_PENDING_WARN = 10      # pending dispatch items in watcher state


def _db_path(name: str) -> str:
    """Resolve DB path via env var or AGENT_RUN_WORKSPACES fallback."""
    env_map = {
        "runtime_state.db": "UA_RUNTIME_DB_PATH",
        "activity_state.db": "UA_ACTIVITY_DB_PATH",
        "vp_state.db": "UA_VP_DB_PATH",
    }
    env_key = env_map.get(name)
    if env_key:
        env_val = os.getenv(env_key)
        if env_val:
            return env_val
    # Fallback: repo_root / AGENT_RUN_WORKSPACES / name
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    return str(repo_root / "AGENT_RUN_WORKSPACES" / name)


def _csi_db_path() -> str:
    return os.getenv("CSI_DB_PATH", "/var/lib/universal-agent/csi/csi.db")


def _safe_connect(db_path: str, timeout: float = 5.0) -> sqlite3.Connection | None:
    """Connect to a SQLite DB or return None if it doesn't exist."""
    if not Path(db_path).exists():
        return None
    try:
        conn = sqlite3.connect(db_path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as exc:
        logger.debug("Cannot connect to %s: %s", db_path, exc)
        return None


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════
# Individual check functions
# ═══════════════════════════════════════════════════════════════════════


def check_stale_runs() -> list[HeartbeatFinding]:
    """Check runtime_state.db for runs stuck in running/queued > threshold.

    Since v2: also auto-reaps runs with no recent progress via the
    stuck_run_reaper, preventing the dispatch cascade that caused
    the VPS resource exhaustion incident.
    """
    findings: list[HeartbeatFinding] = []
    conn = _safe_connect(_db_path("runtime_state.db"))
    if not conn:
        return findings
    try:
        if not _table_exists(conn, "runs") or not _table_exists(conn, "run_attempts"):
            return findings
        cutoff = (_utc_now() - timedelta(hours=STALE_RUN_HOURS)).isoformat()
        rows = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM runs r
            JOIN run_attempts a ON a.attempt_id = r.latest_attempt_id
            WHERE a.status IN ('running', 'queued', 'blocked')
              AND a.started_at < ?
            """,
            (cutoff,),
        ).fetchone()
        count = int(rows["cnt"]) if rows else 0
        if count > 0:
            findings.append(HeartbeatFinding(
                finding_id="stale_runs_detected",
                category="gateway",
                severity="warn",
                metric_key="runtime.stale_runs",
                observed_value=count,
                threshold_text=f">{STALE_RUN_HOURS}h stuck",
                known_rule_match=True,
                confidence="high",
                title=f"{count} run(s) stuck in running/queued >{STALE_RUN_HOURS}h",
                recommendation="Check gateway logs for stuck sessions. May need manual finalization.",
                runbook_command="journalctl -u universal-agent-gateway --since '2 hours ago' | grep -i 'stuck\\|timeout'",
            ))

        # ── Auto-reap stuck runs (progress-based TTL) ────────────────────
        # Runs with no heartbeat/update within TTL are transitioned to
        # timed_out.  This prevents the dispatch cascade where orphaned
        # "running" DB entries cause infinite process spawning.
        try:
            from universal_agent.services.stuck_run_reaper import reap_stale_runs
            reaped = reap_stale_runs(conn)
            if reaped:
                reaped_summary = ", ".join(
                    f"{r.run_id}({r.stale_minutes:.0f}m stale)" for r in reaped[:5]
                )
                if len(reaped) > 5:
                    reaped_summary += f" ... and {len(reaped) - 5} more"
                findings.append(HeartbeatFinding(
                    finding_id="stuck_runs_reaped",
                    category="gateway",
                    severity="warn",
                    metric_key="runtime.reaped_runs",
                    observed_value=len(reaped),
                    threshold_text="auto-reaped by progress-based TTL",
                    known_rule_match=True,
                    confidence="high",
                    title=f"🪦 Reaper: auto-reaped {len(reaped)} stuck run(s): {reaped_summary}",
                    recommendation="Investigate why these runs stopped making progress. Check for OOM, process crashes, or MCP server failures.",
                    runbook_command="sqlite3 /opt/universal_agent/AGENT_RUN_WORKSPACES/runtime_state.db \"SELECT run_id, run_kind, terminal_reason FROM runs WHERE terminal_reason LIKE 'reaper:%' ORDER BY updated_at DESC LIMIT 10;\"",
                ))
        except Exception as reaper_err:
            logger.debug("stuck_run_reaper failed (non-fatal): %s", reaper_err)

    except Exception as exc:
        logger.debug("check_stale_runs failed: %s", exc)
    finally:
        conn.close()
    return findings


def check_task_hub_backlog() -> list[HeartbeatFinding]:
    """Check for excessive open task hub items older than 24h."""
    findings: list[HeartbeatFinding] = []
    conn = _safe_connect(_db_path("runtime_state.db"))
    if not conn:
        return findings
    try:
        if not _table_exists(conn, "task_hub_items"):
            return findings
        cutoff = (_utc_now() - timedelta(hours=24)).isoformat()
        rows = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM task_hub_items
            WHERE status = 'open'
              AND created_at < ?
            """,
            (cutoff,),
        ).fetchone()
        count = int(rows["cnt"]) if rows else 0
        if count >= TASK_BACKLOG_WARN:
            findings.append(HeartbeatFinding(
                finding_id="task_hub_backlog_high",
                category="database",
                severity="warn",
                metric_key="task_hub.open_stale_count",
                observed_value=count,
                threshold_text=f">={TASK_BACKLOG_WARN} open >24h",
                known_rule_match=True,
                confidence="high",
                title=f"{count} open tasks older than 24h in Task Hub",
                recommendation="Review and triage stale tasks. Some may need parking or closing.",
            ))
    except Exception as exc:
        logger.debug("check_task_hub_backlog failed: %s", exc)
    finally:
        conn.close()
    return findings


def check_stale_delegations() -> list[HeartbeatFinding]:
    """Check for delegated tasks without VP progress > threshold."""
    findings: list[HeartbeatFinding] = []
    conn = _safe_connect(_db_path("runtime_state.db"))
    if not conn:
        return findings
    try:
        if not _table_exists(conn, "task_hub_items"):
            return findings
        cutoff = (_utc_now() - timedelta(hours=STALE_DELEGATION_HOURS)).isoformat()
        rows = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM task_hub_items
            WHERE status = 'delegated'
              AND updated_at < ?
            """,
            (cutoff,),
        ).fetchone()
        count = int(rows["cnt"]) if rows else 0
        if count > 0:
            findings.append(HeartbeatFinding(
                finding_id="stale_delegations_detected",
                category="database",
                severity="warn",
                metric_key="task_hub.stale_delegations",
                observed_value=count,
                threshold_text=f">{STALE_DELEGATION_HOURS}h without VP update",
                known_rule_match=True,
                confidence="high",
                title=f"{count} delegated task(s) stale >{STALE_DELEGATION_HOURS}h",
                recommendation="VP may have failed silently. Check VP state and consider re-opening.",
            ))
    except Exception as exc:
        logger.debug("check_stale_delegations failed: %s", exc)
    finally:
        conn.close()
    return findings


def check_pending_signal_cards() -> list[HeartbeatFinding]:
    """Check activity_state.db for excessive pending signal cards."""
    findings: list[HeartbeatFinding] = []
    conn = _safe_connect(_db_path("activity_state.db"))
    if not conn:
        return findings
    try:
        if not _table_exists(conn, "proactive_signal_cards"):
            return findings
        rows = conn.execute(
            "SELECT COUNT(*) AS cnt FROM proactive_signal_cards WHERE status = 'pending'"
        ).fetchone()
        count = int(rows["cnt"]) if rows else 0
        if count >= SIGNAL_CARDS_PENDING_WARN:
            findings.append(HeartbeatFinding(
                finding_id="signal_cards_backlog",
                category="database",
                severity="warn",
                metric_key="activity.pending_signal_cards",
                observed_value=count,
                threshold_text=f">={SIGNAL_CARDS_PENDING_WARN} pending",
                known_rule_match=True,
                confidence="high",
                title=f"{count} pending proactive signal cards — dispatch may be stalled",
                recommendation="Check if the ToDo dispatch service is running. Review proactive pipeline health.",
            ))
    except Exception as exc:
        logger.debug("check_pending_signal_cards failed: %s", exc)
    finally:
        conn.close()
    return findings


def check_stuck_vp_missions() -> list[HeartbeatFinding]:
    """Check vp_state.db for VP missions stuck in active state."""
    findings: list[HeartbeatFinding] = []
    conn = _safe_connect(_db_path("vp_state.db"))
    if not conn:
        return findings
    try:
        if not _table_exists(conn, "vp_missions"):
            return findings
        cutoff = (_utc_now() - timedelta(hours=STALE_DELEGATION_HOURS)).isoformat()
        rows = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM vp_missions
            WHERE status IN ('dispatched', 'running')
              AND created_at < ?
            """,
            (cutoff,),
        ).fetchone()
        count = int(rows["cnt"]) if rows else 0
        if count > 0:
            findings.append(HeartbeatFinding(
                finding_id="stuck_vp_missions",
                category="gateway",
                severity="warn",
                metric_key="vp.stuck_missions",
                observed_value=count,
                threshold_text=f">{STALE_DELEGATION_HOURS}h in dispatched/running",
                known_rule_match=True,
                confidence="medium",
                title=f"{count} VP mission(s) stuck >{STALE_DELEGATION_HOURS}h",
                recommendation="Check VP runtime logs. Missions may need manual cancellation.",
            ))
    except Exception as exc:
        logger.debug("check_stuck_vp_missions failed: %s", exc)
    finally:
        conn.close()
    return findings


def check_csi_adapter_health() -> list[HeartbeatFinding]:
    """Check csi.db adapter_health for consecutive failures."""
    findings: list[HeartbeatFinding] = []
    conn = _safe_connect(_csi_db_path())
    if not conn:
        return findings
    try:
        if not _table_exists(conn, "source_state"):
            return findings
        row = conn.execute(
            "SELECT state_json FROM source_state WHERE source_key = 'adapter_health:youtube_channel_rss'"
        ).fetchone()
        if row:
            try:
                state = json.loads(row["state_json"])
                consecutive = state.get("consecutive_failures", 0)
                if consecutive >= CSI_CONSECUTIVE_FAIL_WARN:
                    last_error = state.get("last_error", "unknown")
                    findings.append(HeartbeatFinding(
                        finding_id="csi_adapter_consecutive_failures",
                        category="database",
                        severity="warn" if consecutive < 10 else "critical",
                        metric_key="csi.youtube_rss.consecutive_failures",
                        observed_value=consecutive,
                        threshold_text=f">={CSI_CONSECUTIVE_FAIL_WARN}",
                        known_rule_match=True,
                        confidence="high",
                        title=f"CSI YouTube RSS adapter: {consecutive} consecutive failures",
                        recommendation=f"Check proxy connectivity and CSI logs. Last error: {str(last_error)[:100]}",
                        runbook_command="journalctl -u csi-ingester --since '1 hour ago' | tail -50",
                    ))
            except (json.JSONDecodeError, TypeError):
                pass
    except Exception as exc:
        logger.debug("check_csi_adapter_health failed: %s", exc)
    finally:
        conn.close()
    return findings


def check_csi_source_freshness() -> list[HeartbeatFinding]:
    """Check if YouTube RSS channels haven't been polled recently."""
    findings: list[HeartbeatFinding] = []
    conn = _safe_connect(_csi_db_path())
    if not conn:
        return findings
    try:
        if not _table_exists(conn, "source_state"):
            return findings
        cutoff = (_utc_now() - timedelta(hours=CSI_SOURCE_STALE_HOURS)).isoformat()
        # Count channels with state that haven't been updated recently
        rows = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM source_state
            WHERE source_key LIKE 'youtube_channel_rss:%'
              AND updated_at < ?
            """,
            (cutoff,),
        ).fetchone()
        stale_count = int(rows["cnt"]) if rows else 0

        total_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM source_state WHERE source_key LIKE 'youtube_channel_rss:%'"
        ).fetchone()
        total_count = int(total_row["cnt"]) if total_row else 0

        if total_count > 0 and stale_count == total_count:
            # ALL channels are stale — the adapter is likely not running
            findings.append(HeartbeatFinding(
                finding_id="csi_rss_all_channels_stale",
                category="database",
                severity="critical",
                metric_key="csi.youtube_rss.stale_channels",
                observed_value=stale_count,
                threshold_text=f"all {total_count} channels not polled in >{CSI_SOURCE_STALE_HOURS}h",
                known_rule_match=True,
                confidence="high",
                title=f"CSI RSS adapter appears down — all {total_count} channels stale >{CSI_SOURCE_STALE_HOURS}h",
                recommendation="Check if CSI Ingester service is running. May indicate proxy or network issue.",
                runbook_command="systemctl status csi-ingester --no-pager",
            ))
        elif stale_count > total_count * 0.5 and stale_count > 10:
            # >50% channels are stale — partial failure
            findings.append(HeartbeatFinding(
                finding_id="csi_rss_many_channels_stale",
                category="database",
                severity="warn",
                metric_key="csi.youtube_rss.stale_channels",
                observed_value=stale_count,
                threshold_text=f">{total_count // 2} of {total_count} channels stale",
                known_rule_match=True,
                confidence="medium",
                title=f"{stale_count}/{total_count} CSI RSS channels stale >{CSI_SOURCE_STALE_HOURS}h",
                recommendation="Partial adapter failure. Review CSI logs for specific channel errors.",
            ))
    except Exception as exc:
        logger.debug("check_csi_source_freshness failed: %s", exc)
    finally:
        conn.close()
    return findings


def check_csi_unemitted_events() -> list[HeartbeatFinding]:
    """Check csi.db for events not yet emitted/delivered."""
    findings: list[HeartbeatFinding] = []
    conn = _safe_connect(_csi_db_path())
    if not conn:
        return findings
    try:
        if not _table_exists(conn, "events"):
            return findings
        rows = conn.execute(
            "SELECT COUNT(*) AS cnt FROM events WHERE delivered = 0"
        ).fetchone()
        count = int(rows["cnt"]) if rows else 0
        if count >= CSI_UNEMITTED_WARN:
            findings.append(HeartbeatFinding(
                finding_id="csi_unemitted_events_backlog",
                category="database",
                severity="warn",
                metric_key="csi.unemitted_events",
                observed_value=count,
                threshold_text=f">={CSI_UNEMITTED_WARN}",
                known_rule_match=True,
                confidence="high",
                title=f"{count} CSI events awaiting delivery — batch emission may be stalled",
                recommendation="Check CSI batch delivery timer and UA ingest endpoint connectivity.",
            ))
    except Exception as exc:
        logger.debug("check_csi_unemitted_events failed: %s", exc)
    finally:
        conn.close()
    return findings


def check_csi_dedupe_bloat() -> list[HeartbeatFinding]:
    """Check whether expired CSI dedupe keys are failing to purge.

    A high active-key count can be healthy for high-volume sources with
    30-90 day TTLs. The operational problem is expired keys remaining after
    the CSI ingester's startup/hourly cleanup should have removed them.
    """
    findings: list[HeartbeatFinding] = []
    conn = _safe_connect(_csi_db_path())
    if not conn:
        return findings
    try:
        if not _table_exists(conn, "dedupe_keys"):
            return findings
        rows = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN expires_at <= datetime('now') THEN 1 ELSE 0 END) AS expired
            FROM dedupe_keys
            """
        ).fetchone()
        total = int(rows["total"]) if rows else 0
        expired = int(rows["expired"] or 0) if rows else 0
        if expired > 0:
            findings.append(HeartbeatFinding(
                finding_id="csi_dedupe_expired_keys_unpurged",
                category="database",
                severity="warn",
                metric_key="csi.dedupe_keys_expired_count",
                observed_value=expired,
                threshold_text=">0 expired keys",
                known_rule_match=False,
                confidence="high",
                title=f"CSI dedupe table has {expired:,} expired key(s) that were not purged",
                recommendation=(
                    "Verify csi-ingester startup/hourly dedupe cleanup is running. "
                    f"Total active+expired keys observed: {total:,}."
                ),
            ))
    except Exception as exc:
        logger.debug("check_csi_dedupe_bloat failed: %s", exc)
    finally:
        conn.close()
    return findings


def check_playlist_watcher() -> list[HeartbeatFinding]:
    """Check YouTube playlist watcher state for pending dispatch items."""
    findings: list[HeartbeatFinding] = []
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    state_file = repo_root / "AGENT_RUN_WORKSPACES" / "youtube_playlist_watcher_state.json"
    if not state_file.exists():
        return findings
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        pending = len(state.get("pending_dispatch_items", {}))
        failed = len(state.get("permanently_failed_video_ids", []))

        if pending >= WATCHER_PENDING_WARN:
            findings.append(HeartbeatFinding(
                finding_id="playlist_watcher_pending_backlog",
                category="gateway",
                severity="warn",
                metric_key="playlist_watcher.pending_dispatch",
                observed_value=pending,
                threshold_text=f">={WATCHER_PENDING_WARN}",
                known_rule_match=True,
                confidence="high",
                title=f"{pending} pending dispatches in playlist watcher — may indicate hook failures",
                recommendation="Check webhook dispatch queue and proxy connectivity.",
            ))

        if failed > 0:
            findings.append(HeartbeatFinding(
                finding_id="playlist_watcher_permanently_failed",
                category="gateway",
                severity="warn" if failed < 5 else "critical",
                metric_key="playlist_watcher.permanently_failed",
                observed_value=failed,
                threshold_text=">0",
                known_rule_match=True,
                confidence="high",
                title=f"{failed} permanently failed video(s) in playlist watcher",
                recommendation="Review failed videos. May need manual purge and retry.",
            ))
    except Exception as exc:
        logger.debug("check_playlist_watcher failed: %s", exc)
    return findings


# ═══════════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════════


def check_all_databases() -> list[HeartbeatFinding]:
    """Run all database health checks and return combined findings.

    This is the single entry point called by heartbeat_service.py.
    Each check is independent and catches its own exceptions — one failing
    check does not prevent others from running.
    """
    all_findings: list[HeartbeatFinding] = []

    checks = [
        check_stale_runs,
        check_task_hub_backlog,
        check_stale_delegations,
        check_pending_signal_cards,
        check_stuck_vp_missions,
        check_csi_adapter_health,
        check_csi_source_freshness,
        check_csi_unemitted_events,
        check_csi_dedupe_bloat,
        check_playlist_watcher,
    ]

    for check_fn in checks:
        try:
            all_findings.extend(check_fn())
        except Exception as exc:
            logger.warning("DB health check %s raised: %s", check_fn.__name__, exc)

    return all_findings
