"""Invariants for the daily / sub-daily proactive pipelines.

Layer-2 watchdog checks for proactive crons whose silent failure is
operator-visible (briefings missing, digest emails not sent, HN snapshots
stale). Each probe is fast (<200ms target), read-only, and fails open: if
the data store / artifact tree isn't deployed yet, the probe returns None
rather than emitting a noisy "probe_error" on a fresh box.

Context keys consumed:
- ``activity_conn``  — opened sqlite3 connection to the activity DB
                       (where proactive_convergence_events and
                       proactive_artifact_emails live).
- ``artifacts_dir``  — Path to the canonical artifacts root.

If either key is missing, the affected probes return None.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from universal_agent.services.pipeline_invariants import invariant

logger = logging.getLogger(__name__)

HOUSTON_TZ = ZoneInfo("America/Chicago")

# Thresholds — tunable but conservative defaults. Each is named so future
# operators can grep and adjust without reading the body.
MORNING_BRIEFING_MAX_AGE_HOURS = 4.0
HACKERNEWS_MAX_GAP_MINUTES = 45.0
HACKERNEWS_ACTIVE_HOUR_MIN = 6  # inclusive
HACKERNEWS_ACTIVE_HOUR_MAX = 21  # inclusive (last tick :30 of hour 21 = 9:30 PM CDT)
CSI_CONVERGENCE_MAX_AGE_MINUTES = 90.0  # one missed cycle of grace
PROACTIVE_DIGEST_MAX_AGE_HOURS = 30.0  # 24h + 6h grace
NIGHTLY_WIKI_WINDOW_END_HOUR = 5
# `nightly_wiki` cron is "produce only when fresh CSI signals" — quiet
# nights with no inputs are legitimate. Only fire if NO wiki has appeared
# for this many days, which catches a stuck pipeline without false-firing
# on every signal-light day.
NIGHTLY_WIKI_QUIET_DAYS_FLOOR = 7


def _today_houston() -> str:
    return datetime.now(HOUSTON_TZ).strftime("%Y-%m-%d")


def _now_houston() -> datetime:
    return datetime.now(HOUSTON_TZ)


def _parse_iso(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# 1. morning_briefing artifact freshness
# ---------------------------------------------------------------------------


@invariant(
    id="morning_briefing_freshness",
    title="Today's morning briefing artifact exists",
    description=(
        "Cron `morning_briefing` runs at 6:30 AM Houston daily and writes "
        "artifacts/autonomous-briefings/<YYYY-MM-DD>/DAILY_BRIEFING.md. The "
        "today-dated parent directory is itself the freshness gate — if the "
        "file exists at today's path, it was written today. We only check "
        "existence, not mtime: the 6:30 AM cron fires once per day, so by "
        "afternoon the file is legitimately many hours old."
    ),
    severity="warn",
    runbook_command=(
        "ls -la artifacts/autonomous-briefings/$(TZ=America/Chicago date +%Y-%m-%d)/"
        "DAILY_BRIEFING.md 2>&1; "
        "journalctl -u universal-agent-gateway --since 'today 06:00' --no-pager | "
        "grep -i morning_briefing"
    ),
    metadata={
        "pipeline": "morning_briefing",
        "cron_expr": "30 6 * * *",
        "tz": "America/Chicago",
    },
)
def morning_briefing_freshness(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    artifacts_dir = ctx.get("artifacts_dir")
    if artifacts_dir is None:
        return None
    base = Path(artifacts_dir)
    if not base.exists():
        return None
    now = _now_houston()
    # Only meaningful after the 6:30 AM tick. Stay quiet earlier in the day.
    cron_time = now.replace(hour=6, minute=30, second=0, microsecond=0)
    if now < cron_time:
        return None
    today = _today_houston()
    briefing = base / "autonomous-briefings" / today / "DAILY_BRIEFING.md"
    if not briefing.exists():
        return {
            "observed_value": {"today": today, "path": str(briefing), "exists": False},
            "message": (
                f"No DAILY_BRIEFING.md for {today}. The 6:30 AM morning_briefing "
                "cron may have failed silently. Kevin will have no briefing on his phone."
            ),
            "threshold_text": "exists at today's path after 6:30 AM Houston",
        }
    return None


# ---------------------------------------------------------------------------
# 2. proactive_artifact_digest email delivery
# ---------------------------------------------------------------------------


@invariant(
    id="proactive_artifact_digest_delivery",
    title="Proactive artifact digest emailed at least once in last 24h",
    description=(
        "Cron `proactive_artifact_digest` runs at 8:35 AM Houston daily and "
        "emails Kevin a digest of new CODIE PRs, tutorial builds, and "
        "convergence insights via AgentMail. Delivery is recorded in the "
        "proactive_artifact_emails table. If no row exists in the last 24h, "
        "the operator's only push channel for these artifacts has gone silent."
    ),
    severity="warn",
    runbook_command=(
        "sqlite3 \"$UA_ACTIVITY_DB_PATH\" \"SELECT sent_at, recipient, subject "
        "FROM proactive_artifact_emails ORDER BY sent_at DESC LIMIT 5;\""
    ),
    metadata={
        "pipeline": "proactive_artifact_digest",
        "cron_expr": "35 8 * * *",
        "tables": ["proactive_artifact_emails"],
    },
)
def proactive_artifact_digest_delivery(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    conn = ctx.get("activity_conn")
    if conn is None:
        return None
    # Only probe after the 8:35 AM tick so a fresh morning doesn't false-fire.
    now = _now_houston()
    if now.hour < 9:
        return None
    try:
        row = conn.execute(
            "SELECT MAX(sent_at) AS last_sent, COUNT(*) AS total "
            "FROM proactive_artifact_emails"
        ).fetchone()
    except sqlite3.Error as exc:
        logger.debug("proactive_artifact_digest_delivery: query failed (%s)", exc)
        return None
    if row is None:
        return None
    # Row factory may be sqlite3.Row or plain tuple; handle both.
    try:
        total = int(row["total"] or 0)
        last_sent_raw = row["last_sent"]
    except (TypeError, KeyError, IndexError):
        last_sent_raw = row[0] if row else None
        total = int(row[1] or 0) if row and len(row) > 1 else 0
    if total == 0:
        # Empty table on a fresh box — stay quiet rather than scream.
        return None
    last_sent = _parse_iso(last_sent_raw)
    if last_sent is None:
        return None
    now_utc = datetime.now(timezone.utc)
    age_hours = (now_utc - last_sent).total_seconds() / 3600.0
    if age_hours > PROACTIVE_DIGEST_MAX_AGE_HOURS:
        return {
            "observed_value": {
                "last_sent_at": last_sent.isoformat(),
                "age_hours": round(age_hours, 2),
                "total_rows": total,
            },
            "message": (
                f"Last proactive artifact digest email was {age_hours:.1f}h ago "
                f"(threshold {PROACTIVE_DIGEST_MAX_AGE_HOURS:.0f}h). The 8:35 AM "
                "cron has likely been failing — Kevin isn't getting his daily "
                "PR/tutorial/convergence summary."
            ),
            "threshold_text": f"last sent within last {PROACTIVE_DIGEST_MAX_AGE_HOURS:.0f}h",
        }
    return None


# ---------------------------------------------------------------------------
# 3. hackernews_snapshot half-hour cadence during active hours
# ---------------------------------------------------------------------------


@invariant(
    id="hackernews_snapshot_cadence",
    title="HN snapshot produced within last 45m during active hours",
    description=(
        "Cron `hackernews_snapshot` runs every 30 min during 6 AM – 9:30 PM "
        "Houston and writes artifacts/hackernews/snapshots/<TS>.json. If the "
        "most recent snapshot is more than 45 minutes old during active hours, "
        "the snapshot pipeline has stalled."
    ),
    severity="warn",
    runbook_command=(
        "ls -lt artifacts/hackernews/snapshots/*.json 2>/dev/null | head -5; "
        "tail -20 logs/cron_hackernews_snapshot*.log 2>/dev/null"
    ),
    metadata={
        "pipeline": "hackernews_snapshot",
        "cron_expr": "0,30 6-21 * * *",
        "tz": "America/Chicago",
    },
)
def hackernews_snapshot_cadence(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    artifacts_dir = ctx.get("artifacts_dir")
    if artifacts_dir is None:
        return None
    snaps_dir = Path(artifacts_dir) / "hackernews" / "snapshots"
    if not snaps_dir.exists():
        return None
    now = _now_houston()
    if not (HACKERNEWS_ACTIVE_HOUR_MIN <= now.hour <= HACKERNEWS_ACTIVE_HOUR_MAX):
        return None
    # First active-hour tick of the day (6:00 AM Houston) can be up to ~8h
    # behind the last 9:30 PM snapshot from the prior evening. Stay quiet for
    # the first 30 minutes after 6 AM so that overnight gap doesn't false-fire.
    if now.hour == HACKERNEWS_ACTIVE_HOUR_MIN and now.minute < 30:
        return None
    files = list(snaps_dir.glob("*.json"))
    if not files:
        return {
            "observed_value": {"snapshots_dir": str(snaps_dir), "count": 0},
            "message": "No HN snapshots on disk during active hours.",
            "threshold_text": f"latest snapshot mtime within {HACKERNEWS_MAX_GAP_MINUTES:.0f} min",
        }
    latest = max(files, key=lambda p: p.stat().st_mtime)
    latest_mtime = datetime.fromtimestamp(latest.stat().st_mtime, tz=HOUSTON_TZ)
    age_minutes = (now - latest_mtime).total_seconds() / 60.0
    if age_minutes > HACKERNEWS_MAX_GAP_MINUTES:
        return {
            "observed_value": {
                "latest_file": latest.name,
                "age_minutes": round(age_minutes, 1),
                "snapshot_count": len(files),
            },
            "message": (
                f"Latest HN snapshot is {age_minutes:.1f} min old "
                f"(threshold {HACKERNEWS_MAX_GAP_MINUTES:.0f} min). The 30-min "
                "cadence cron may be failing or queued behind a slow upstream."
            ),
            "threshold_text": f"age <= {HACKERNEWS_MAX_GAP_MINUTES:.0f} min during active hours",
        }
    return None


# ---------------------------------------------------------------------------
# 4. csi_convergence_sync freshness
# ---------------------------------------------------------------------------


@invariant(
    id="csi_convergence_sync_freshness",
    title="CSI convergence detected_at within last 90 min",
    description=(
        "Cron `csi_convergence_sync` runs every 30 min (24/7) and records "
        "proactive_convergence_events rows when multiple independent sources "
        "converge on the same topic. If max(detected_at) is older than 90 min, "
        "the sync is either failing or the upstream CSI signal table has "
        "stopped producing — both block morning_briefing's signal feed."
    ),
    severity="warn",
    runbook_command=(
        "sqlite3 \"$UA_ACTIVITY_DB_PATH\" \"SELECT MAX(detected_at) latest, "
        "COUNT(*) total FROM proactive_convergence_events;\""
    ),
    metadata={
        "pipeline": "csi_convergence_sync",
        "cron_expr": "*/30 * * * *",
        "tables": ["proactive_convergence_events"],
    },
)
def csi_convergence_sync_freshness(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    conn = ctx.get("activity_conn")
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT MAX(detected_at) AS latest, COUNT(*) AS total "
            "FROM proactive_convergence_events"
        ).fetchone()
    except sqlite3.Error as exc:
        logger.debug("csi_convergence_sync_freshness: query failed (%s)", exc)
        return None
    if row is None:
        return None
    try:
        total = int(row["total"] or 0)
        latest_raw = row["latest"]
    except (TypeError, KeyError, IndexError):
        latest_raw = row[0] if row else None
        total = int(row[1] or 0) if row and len(row) > 1 else 0
    if total == 0:
        # Empty table — fresh box or genuinely no convergence yet. Stay quiet.
        return None
    latest = _parse_iso(latest_raw)
    if latest is None:
        return None
    age_minutes = (datetime.now(timezone.utc) - latest).total_seconds() / 60.0
    if age_minutes > CSI_CONVERGENCE_MAX_AGE_MINUTES:
        return {
            "observed_value": {
                "latest_detected_at": latest.isoformat(),
                "age_minutes": round(age_minutes, 1),
                "total_rows": total,
            },
            "message": (
                f"Latest convergence event is {age_minutes:.1f} min old "
                f"(threshold {CSI_CONVERGENCE_MAX_AGE_MINUTES:.0f} min). The "
                "30-min sync cron may be failing or the upstream signal "
                "table has dried up — investigate before morning_briefing reads it."
            ),
            "threshold_text": f"age <= {CSI_CONVERGENCE_MAX_AGE_MINUTES:.0f} min",
        }
    return None


# ---------------------------------------------------------------------------
# 5. nightly_wiki overnight output
# ---------------------------------------------------------------------------


@invariant(
    id="nightly_wiki_persistent_silence",
    title="Nightly wiki produced output within last 7 days",
    description=(
        "Cron `nightly_wiki` runs at 3:15 AM Houston and writes "
        "artifacts/nightly_wikis/<YYYY-MM-DD>_wiki_*.md|png ONLY when fresh "
        "CSI signals warrant a wiki. Quiet nights with no signal are "
        "legitimate (cron exits clean_exit_zero with no file). We only fire "
        "if NO wiki has appeared in the last 7 days — which catches a stuck "
        "agent or stuck upstream signal pipeline without false-firing on "
        "individual signal-light days."
    ),
    severity="warn",
    runbook_command=(
        "ls -lt artifacts/nightly_wikis/ 2>/dev/null | head; "
        "journalctl -u universal-agent-gateway --since '8 days ago' --no-pager | "
        "grep -i nightly_wiki | tail -20"
    ),
    metadata={
        "pipeline": "nightly_wiki",
        "cron_expr": "15 3 * * *",
        "tz": "America/Chicago",
        "design_note": (
            "Probe changed 2026-05-20: was 'file for today exists' which "
            "false-fired on every signal-quiet day. Now checks for persistent "
            "silence across NIGHTLY_WIKI_QUIET_DAYS_FLOOR days."
        ),
    },
)
def nightly_wiki_persistent_silence(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    artifacts_dir = ctx.get("artifacts_dir")
    if artifacts_dir is None:
        return None
    base = Path(artifacts_dir) / "nightly_wikis"
    if not base.exists():
        return None
    now = _now_houston()
    # Walk all wiki files and find the most recent mtime. If newest is older
    # than NIGHTLY_WIKI_QUIET_DAYS_FLOOR days, the pipeline is stuck.
    wiki_files = list(base.glob("*_wiki_*"))
    if not wiki_files:
        # Truly fresh / never-deployed box. Empty dir is informational only,
        # not a finding.
        return None
    newest = max(wiki_files, key=lambda p: p.stat().st_mtime)
    newest_mtime = datetime.fromtimestamp(newest.stat().st_mtime, tz=HOUSTON_TZ)
    quiet_days = (now - newest_mtime).total_seconds() / 86400.0
    if quiet_days > NIGHTLY_WIKI_QUIET_DAYS_FLOOR:
        return {
            "observed_value": {
                "newest_file": newest.name,
                "newest_mtime": newest_mtime.isoformat(),
                "quiet_days": round(quiet_days, 1),
                "dir": str(base),
            },
            "message": (
                f"No nightly_wiki output in {quiet_days:.1f} days "
                f"(threshold {NIGHTLY_WIKI_QUIET_DAYS_FLOOR}d). Either the "
                "agent is stuck or the upstream CSI signal pipeline has dried "
                "up. Inspect cron run logs and signal feed before assuming "
                "this is normal."
            ),
            "threshold_text": (
                f"newest wiki mtime within last {NIGHTLY_WIKI_QUIET_DAYS_FLOOR}d"
            ),
        }
    return None
