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
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from universal_agent.services import dormancy
from universal_agent.services.pipeline_invariants import invariant

logger = logging.getLogger(__name__)

HOUSTON_TZ = ZoneInfo("America/Chicago")

# Thresholds — tunable but conservative defaults. Each is named so future
# operators can grep and adjust without reading the body.
MORNING_BRIEFING_MAX_AGE_HOURS = 4.0
# csi_convergence_sync writes `convergence_candidates` sporadically (only when a
# real convergence/ideation is detected) on an hourly active-hours cron — NOT the
# decommissioned `proactive_convergence_events` table on a 30-min 24/7 cadence the
# old probe assumed. 3h ≈ a few active-hour cron cycles of grace absorbs natural
# detection lulls; the active-hours gate below absorbs the overnight no-cron window.
CSI_CONVERGENCE_MAX_AGE_MINUTES = 180.0
CSI_CONVERGENCE_ACTIVE_HOUR_MIN = 8  # cron is `0 6-21`; allow 2 post-6AM cycles to produce
CSI_CONVERGENCE_ACTIVE_HOUR_MAX = 21
PROACTIVE_DIGEST_MAX_AGE_HOURS = 30.0  # 24h + 6h grace
NIGHTLY_WIKI_WINDOW_END_HOUR = 5
# `nightly_wiki` cron is "produce only when fresh CSI signals" — quiet
# nights with no inputs are legitimate. Only fire if NO wiki has appeared
# for this many days, which catches a stuck pipeline without false-firing
# on every signal-light day.
NIGHTLY_WIKI_QUIET_DAYS_FLOOR = 7

# WS3 thresholds (added 2026-05-20).
PROACTIVE_REPORTS_MIN_TODAY = 2  # at least 2 of 3 daily reports must exist
CLAUDE_CODE_INTEL_MAX_AGE_HOURS = 9.0  # active-hour cron runs 8 AM / 4 PM / 10 PM Houston
CSI_DEMO_TRIAGE_MAX_AGE_HOURS = 6.0  # twice-daily cron
PAPER_TO_PODCAST_MAX_AGE_HOURS = 30.0  # daily 9 PM Houston + 6h grace
# The paper_to_podcast_daily cron's job id. cron_artifact_notifier composes
# every subject as f"[{job_id}] {title}" (_compose_initial_email), so the
# bracketed id is a deterministic delivery marker — unlike the old
# subject LIKE '%Papers%' heuristic, which matched any email whose
# LLM-varied title happened to contain "Papers" (including the 2026-06-10
# false "podcast produced" disclosure built from a 2-day-old manifest,
# which silently reset this watchdog). Override via env if the cron is
# ever re-registered under a new id.
PAPER_TO_PODCAST_JOB_ID = "2afe05ab96"
VAULT_LINT_DAY_OF_MONTH_GATE = 2  # only probe after the 2nd to give the 1st's cron full day

# Brief→task funnel (added 2026-05-24). Each proactive source_kind should
# produce roughly one task_hub_items row per artifact. A wide gap (lots of
# artifacts, zero tasks) means the preference gate / queue insert path is
# silently dropping work — which is exactly the failure mode that the
# 2026-04-18 implicit-park-poison incident exposed.
BRIEF_TASK_FUNNEL_WINDOW_HOURS = 48
BRIEF_TASK_FUNNEL_MIN_ARTIFACTS = 5  # below this it's plausibly just slow CSI inputs
# `convergence_detection` / `insight_detection` were the legacy per-signature
# pipeline's source_kinds, removed in 2026-05 (#568). The live convergence/ideation
# path uses source_kind `convergence_candidate` and a different silent-drop guard:
# inline triage (#628) decides ship→task at candidate-write time, so its failure
# mode is "ship-triaged candidate with no task_hub_item", not "artifact with no
# task" — not the artifact→task shape this funnel checks. Tracking the dead
# source_kinds here just left an inert probe; `tutorial_build` (Cody tutorial
# builds) still produces artifacts+tasks in the artifact→task shape this guards.
BRIEF_TASK_FUNNEL_SOURCE_KINDS = ("tutorial_build",)


def _paper_to_podcast_job_id() -> str:
    return (
        os.getenv("UA_PAPER_TO_PODCAST_JOB_ID", "").strip()
        or PAPER_TO_PODCAST_JOB_ID
    )


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
    """Fire when today's ``DAILY_BRIEFING.md`` is missing after the 6:30 AM cron.

    Checks only for existence at today's dated artifact path (the directory name
    is itself the freshness gate), and only after the 6:30 AM Houston tick so a
    pre-dawn run stays quiet. Returns None when the artifacts root is absent or
    today's briefing exists.
    """
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
        "proactive_artifact_emails table in runtime_state.db. If no row "
        "exists in the last 24h+grace, the operator's only push channel "
        "for these artifacts has gone silent."
    ),
    severity="warn",
    runbook_command=(
        "sqlite3 /opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db "
        "\"SELECT sent_at, recipient, subject FROM proactive_artifact_emails "
        "ORDER BY sent_at DESC LIMIT 5;\""
    ),
    metadata={
        "pipeline": "proactive_artifact_digest",
        "cron_expr": "35 8 * * *",
        "tables": ["proactive_artifact_emails"],
        "db": "activity_state.db",
        "design_note": (
            "Probe corrected 2026-05-20 (P0b): writers use _activity_connect() "
            "→ activity_state.db. PR #376 wrote to runtime_conn (runtime_state.db), "
            "PR #392 then opened a separate runtime_conn — still wrong DB. "
            "P0b: invariants use activity_conn which already points at "
            "activity_state.db (same DB as task_hub_items). No parallel plumbing."
        ),
    },
)
def proactive_artifact_digest_delivery(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Fire when no proactive-artifact digest email has been sent in ~24h+grace.

    Reads the newest ``sent_at`` from ``proactive_artifact_emails`` (via the
    ``activity_conn`` context) and flags ages over ``PROACTIVE_DIGEST_MAX_AGE_HOURS``.
    Only probes after the 8:35 AM tick and stays quiet on an empty table (fresh
    box). Returns None when delivery is current.
    """
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
# 3. csi_convergence_sync freshness
# ---------------------------------------------------------------------------


@invariant(
    id="csi_convergence_sync_freshness",
    title="CSI convergence candidate created within last 3h (active hours)",
    description=(
        "Cron `csi_convergence_sync` runs hourly 06:00–21:00 America/Chicago "
        "(`UA_CSI_CONVERGENCE_CRON_EXPR`, default `0 6-21 * * *`) and writes "
        "`convergence_candidates` rows when YouTube RSS topic signatures converge "
        "or an ideation insight is synthesized. Reads the LIVE "
        "`convergence_candidates` table — the legacy `proactive_convergence_events` "
        "table was DECOMMISSIONED 2026-05-28 (frozen), so the old probe that read it "
        "fired a permanent false-RED while the pipeline was healthy. If no candidate "
        "has been created in 3h during active hours, the sync is failing or upstream "
        "CSI signal has dried up. Quiet outside active hours (no overnight cron)."
    ),
    severity="warn",
    runbook_command=(
        "sqlite3 \"$UA_ACTIVITY_DB_PATH\" \"SELECT MAX(created_at) latest, "
        "COUNT(*) total FROM convergence_candidates;\""
    ),
    metadata={
        "pipeline": "csi_convergence_sync",
        "cron_expr": "0 6-21 * * *",
        "tz": "America/Chicago",
        "tables": ["convergence_candidates"],
    },
)
def csi_convergence_sync_freshness(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Fire when no ``convergence_candidates`` row has appeared in ~3h (active hours).

    Reads the newest ``created_at`` from the LIVE ``convergence_candidates``
    table (not the decommissioned ``proactive_convergence_events``) and flags
    ages over ``CSI_CONVERGENCE_MAX_AGE_MINUTES``. Probes only well into the
    06:00-21:00 active window so the overnight no-cron gap can't false-fire.
    Returns None on an empty table or when fresh.
    """
    conn = ctx.get("activity_conn")
    if conn is None:
        return None
    now = _now_houston()
    # The cron only runs 06:00–21:00 CT and detection is sporadic, so the
    # overnight no-cron window (22:00–05:00) plus a couple of post-6AM cycles
    # would otherwise false-fire. Only probe once we're well into active hours.
    if not (CSI_CONVERGENCE_ACTIVE_HOUR_MIN <= now.hour <= CSI_CONVERGENCE_ACTIVE_HOUR_MAX):
        return None
    try:
        row = conn.execute(
            "SELECT MAX(created_at) AS latest, COUNT(*) AS total "
            "FROM convergence_candidates"
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
                "latest_created_at": latest.isoformat(),
                "age_minutes": round(age_minutes, 1),
                "total_rows": total,
            },
            "message": (
                f"Latest convergence_candidate is {age_minutes:.1f} min old "
                f"(threshold {CSI_CONVERGENCE_MAX_AGE_MINUTES:.0f} min). The hourly "
                "csi_convergence_sync cron may be failing or the upstream CSI signal "
                "(YouTube RSS topic signatures) has dried up."
            ),
            "threshold_text": f"age <= {CSI_CONVERGENCE_MAX_AGE_MINUTES:.0f} min during active hours",
        }
    return None


# ---------------------------------------------------------------------------
# 4. nightly_wiki overnight output
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
    """Fire only when no nightly-wiki artifact has appeared in ~7 days.

    The cron legitimately produces nothing on signal-quiet nights, so this
    checks for *persistent* silence: the newest mtime under ``nightly_wikis/``
    older than ``NIGHTLY_WIKI_QUIET_DAYS_FLOOR`` days flags a stuck agent or a
    dried-up upstream signal pipeline. Returns None when the dir is absent/empty
    or output is recent.
    """
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


# ---------------------------------------------------------------------------
# 5. proactive_reports daily trio (morning + midday + afternoon)
# ---------------------------------------------------------------------------


@invariant(
    id="proactive_reports_daily_trio",
    title="At least 2 of 3 daily proactive reports created today",
    description=(
        "Crons `proactive_report_morning` (7:05 AM), `proactive_report_midday` "
        "(12:05 PM), `proactive_report_afternoon` (4:05 PM) each insert a row "
        "into `proactive_intelligence_reports`. If fewer than 2 of 3 today's "
        "rows exist by 5 PM Houston, the three-time-a-day intel rhythm is "
        "broken. We tolerate 1 missed slot as routine ZAI-quota / API blip; "
        "2+ missing means the pipeline is degraded."
    ),
    severity="warn",
    runbook_command=(
        "sqlite3 /opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db "
        "\"SELECT period, COUNT(*) FROM proactive_intelligence_reports "
        "WHERE DATE(created_at) = DATE('now') GROUP BY period;\""
    ),
    metadata={
        "pipeline": "proactive_report_morning|midday|afternoon",
        "cron_exprs": ["5 7 * * *", "5 12 * * *", "5 16 * * *"],
        "tz": "America/Chicago",
        "tables": ["proactive_intelligence_reports"],
        "db": "activity_state.db",
    },
)
def proactive_reports_daily_trio(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Fire when fewer than 2 of today's 3 proactive reports exist by 5 PM.

    Counts today's ``proactive_intelligence_reports`` rows (morning/midday/
    afternoon) after the 4:05 PM cron + 1h grace. One missed slot is tolerated
    as a routine quota/API blip; below ``PROACTIVE_REPORTS_MIN_TODAY`` means the
    rhythm is degraded. Returns None before 5 PM or when the threshold is met.
    """
    conn = ctx.get("activity_conn")
    if conn is None:
        return None
    now = _now_houston()
    # Only probe after the 4:05 PM cron should have fired + 1h grace.
    if now.hour < 17:
        return None
    today = _today_houston()
    try:
        rows = conn.execute(
            "SELECT period, COUNT(*) AS n FROM proactive_intelligence_reports "
            "WHERE DATE(created_at) = ? GROUP BY period",
            (today,),
        ).fetchall()
    except sqlite3.Error as exc:
        logger.debug("proactive_reports_daily_trio: query failed (%s)", exc)
        return None
    periods_present = {str(r[0]).lower() for r in (rows or [])}
    counts_today = sum(int(r[1] or 0) for r in (rows or []))
    if counts_today >= PROACTIVE_REPORTS_MIN_TODAY:
        return None
    expected = {"morning", "midday", "afternoon"}
    missing = sorted(expected - periods_present)
    return {
        "observed_value": {
            "today": today,
            "reports_today": counts_today,
            "periods_present": sorted(periods_present),
            "periods_missing": missing,
        },
        "message": (
            f"Only {counts_today} proactive_intelligence_reports row(s) for {today} "
            f"(need >= {PROACTIVE_REPORTS_MIN_TODAY}). Missing periods: {missing}. "
            "The three-times-a-day intel rhythm is degraded."
        ),
        "threshold_text": f">= {PROACTIVE_REPORTS_MIN_TODAY} of 3 reports per day",
    }


# ---------------------------------------------------------------------------
# 6. claude_code_intel packet freshness
# ---------------------------------------------------------------------------


@invariant(
    id="claude_code_intel_packet_freshness",
    title="claude_code_intel produced a packet in the last 9h",
    description=(
        "Cron `claude_code_intel_sync` runs at 8 AM, 4 PM, and 10 PM Houston "
        "and writes JSON packets to artifacts/proactive/claude_code_intel/"
        "packets/. If no packet has appeared in the last 9h during active "
        "hours, the polling pipeline has stalled."
    ),
    severity="warn",
    runbook_command=(
        "ls -lt /opt/universal_agent/artifacts/proactive/claude_code_intel/packets/ | head -10; "
        "journalctl -u universal-agent-gateway --since '12 hours ago' | grep -i claude_code_intel"
    ),
    metadata={
        "pipeline": "claude_code_intel_sync",
        "cron_expr": "0 8,16,22 * * *",
        "tz": "America/Chicago",
    },
)
def claude_code_intel_packet_freshness(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Fire when no claude_code_intel packet has appeared in ~9h (active hours).

    Flags the newest mtime under ``proactive/claude_code_intel/packets/`` older
    than ``CLAUDE_CODE_INTEL_MAX_AGE_HOURS`` while inside active hours (with a
    grace after 6 AM for the overnight gap). Returns None when the dir is
    absent/empty, during dormancy, or the packet is fresh.
    """
    artifacts_dir = ctx.get("artifacts_dir")
    if artifacts_dir is None:
        return None
    packets_dir = Path(artifacts_dir) / "proactive" / "claude_code_intel" / "packets"
    if not packets_dir.exists():
        return None
    now = _now_houston()
    # Only meaningful during active hours; quiet during dormancy (10 PM – 6 AM Houston)
    if not dormancy.is_active_window(now):
        return None
    # First 30 min after 6 AM allows the overnight gap (last cron 10 PM yesterday).
    if now.hour == 6 and now.minute < 30:
        return None
    files = [p for p in packets_dir.iterdir() if p.is_file()]
    if not files:
        # Fresh box, no packets yet — don't false-fire.
        return None
    newest = max(files, key=lambda p: p.stat().st_mtime)
    newest_mtime = datetime.fromtimestamp(newest.stat().st_mtime, tz=HOUSTON_TZ)
    age_hours = (now - newest_mtime).total_seconds() / 3600.0
    if age_hours > CLAUDE_CODE_INTEL_MAX_AGE_HOURS:
        return {
            "observed_value": {
                "newest_file": newest.name,
                "age_hours": round(age_hours, 2),
                "file_count": len(files),
            },
            "message": (
                f"Newest claude_code_intel packet is {age_hours:.1f}h old "
                f"(threshold {CLAUDE_CODE_INTEL_MAX_AGE_HOURS:.0f}h). "
                "Likely the 8/16/22 cron is failing — packets are the "
                "operator's main signal for new Claude Code releases."
            ),
            "threshold_text": (
                f"newest packet mtime within last {CLAUDE_CODE_INTEL_MAX_AGE_HOURS:.0f}h "
                "during active hours"
            ),
        }
    return None


# ---------------------------------------------------------------------------
# 7. csi_demo_triage_rank artifact freshness
# ---------------------------------------------------------------------------


@invariant(
    id="csi_demo_triage_rank_artifact",
    title="csi_demo_triage_rank produced an artifact in last 6h (active hours)",
    description=(
        "Cron `csi_demo_triage_rank` runs at 10:05 AM and 3:05 PM Houston "
        "and inserts an LLM-ranked candidate list into the proactive_artifacts "
        "table with artifact_type='csi_demo_triage_run'. Twice-daily cadence "
        "means a 6h gap is the maximum acceptable during active hours; "
        "longer than that means the ranking pipeline is broken — operator "
        "loses visibility into ranked CSI demo candidates."
    ),
    severity="critical",
    runbook_command=(
        "sqlite3 /opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db "
        "\"SELECT artifact_id, title, created_at FROM proactive_artifacts "
        "WHERE artifact_type='csi_demo_triage_run' "
        "ORDER BY created_at DESC LIMIT 5;\""
    ),
    metadata={
        "pipeline": "csi_demo_triage_rank",
        "cron_expr": "5 10,15 * * *",
        "tz": "America/Chicago",
        "tables": ["proactive_artifacts"],
        "db": "activity_state.db",
        "artifact_type": "csi_demo_triage_run",
    },
)
def csi_demo_triage_rank_artifact(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Fire when no ``csi_demo_triage_run`` artifact has appeared in ~6h.

    Reads the newest ``created_at`` for ``artifact_type='csi_demo_triage_run'``
    in ``proactive_artifacts`` and flags ages over ``CSI_DEMO_TRIAGE_MAX_AGE_HOURS``
    during active hours, after the first daily (10:05 AM) run. Stays quiet when
    the feature has produced no rows yet. Returns None when current.
    """
    conn = ctx.get("activity_conn")
    if conn is None:
        return None
    now = _now_houston()
    # Only probe during active hours. Skip first 30 min after 6 AM to allow
    # the overnight gap from yesterday's 3:05 PM run.
    if not dormancy.is_active_window(now):
        return None
    if now.hour == 6 and now.minute < 30:
        return None
    # Probe only after the first daily run (10:05 AM); earlier we have no
    # ground truth for today.
    if now.hour < 11:
        return None
    try:
        row = conn.execute(
            "SELECT MAX(created_at) AS latest, COUNT(*) AS total "
            "FROM proactive_artifacts "
            "WHERE artifact_type = 'csi_demo_triage_run'"
        ).fetchone()
    except sqlite3.Error as exc:
        logger.debug("csi_demo_triage_rank_artifact: query failed (%s)", exc)
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
        # No rows of this type at all — treat as not-yet-deployed-feature,
        # stay quiet rather than alarm. The feature gets exercised once
        # the operator actually approves a csi_demo_triage_rank cron run.
        return None
    latest = _parse_iso(latest_raw)
    if latest is None:
        return None
    age_hours = (datetime.now(timezone.utc) - latest).total_seconds() / 3600.0
    if age_hours > CSI_DEMO_TRIAGE_MAX_AGE_HOURS:
        return {
            "observed_value": {
                "latest_created_at": latest.isoformat(),
                "age_hours": round(age_hours, 2),
                "total_artifacts": total,
            },
            "message": (
                f"Latest csi_demo_triage_run artifact is {age_hours:.1f}h old "
                f"(threshold {CSI_DEMO_TRIAGE_MAX_AGE_HOURS:.0f}h). The 10:05 AM / "
                "3:05 PM Houston twice-daily cadence has stalled — operator "
                "loses ranked CSI demo candidates."
            ),
            "threshold_text": f"age <= {CSI_DEMO_TRIAGE_MAX_AGE_HOURS:.0f}h during active hours",
        }
    return None


# ---------------------------------------------------------------------------
# 8. paper_to_podcast_daily email delivery
# ---------------------------------------------------------------------------


@invariant(
    id="paper_to_podcast_email_delivery",
    title="paper_to_podcast emailed Kevin in last 30h",
    description=(
        "Cron `paper_to_podcast_daily` runs at 9 PM Houston daily, generates "
        "a podcast.mp3 + quiz + flashcards from the day's arXiv papers, and "
        "emails the bundle to kevinjdragan@gmail.com. Delivery is recorded "
        "in `proactive_artifact_emails`; the notifier's subject always "
        "starts with the bracketed cron job id (\"[2afe05ab96] ...\"), which "
        "is the deterministic marker matched here. "
        "Daily cadence + 6h grace = 30h max acceptable age. Critical because "
        "this is the operator's daily research-podcast pipeline — silent "
        "failure means he doesn't notice for days."
    ),
    severity="critical",
    runbook_command=(
        "sqlite3 /opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db "
        "\"SELECT sent_at, recipient, subject FROM proactive_artifact_emails "
        "WHERE subject LIKE '[2afe05ab96]%' ORDER BY sent_at DESC LIMIT 5;\""
    ),
    metadata={
        "pipeline": "paper_to_podcast_daily",
        "cron_expr": "0 21 * * *",
        "tz": "America/Chicago",
        "tables": ["proactive_artifact_emails"],
        "db": "activity_state.db",
    },
)
def paper_to_podcast_email_delivery(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Fire when the daily paper-to-podcast email is overdue (~30h).

    Reads the newest ``sent_at`` from ``proactive_artifact_emails`` for
    subjects carrying the notifier's bracketed cron job id (the
    deterministic ``[2afe05ab96]`` prefix minted by
    ``cron_artifact_notifier._compose_initial_email``) sent to
    kevinjdragan@gmail.com, and flags ages over
    ``PAPER_TO_PODCAST_MAX_AGE_HOURS`` (daily cadence + 6h grace). With the
    notifier's run-freshness gate in place, a matching email implies fresh
    artifacts — so this marker is sound where the old ``LIKE '%Papers%'``
    heuristic let a stale-manifest false disclosure reset the watchdog.
    Probes only after 6 AM and stays quiet on an unactivated pipeline.
    Returns None when current.
    """
    conn = ctx.get("activity_conn")
    if conn is None:
        return None
    now = _now_houston()
    # Probe only after 6 AM Houston (the morning after the 9 PM cron should
    # have fired). Before 6 AM the "30h" lookback overlaps the current cycle.
    if now.hour < 6:
        return None
    try:
        row = conn.execute(
            "SELECT MAX(sent_at) AS last_sent, COUNT(*) AS total "
            "FROM proactive_artifact_emails "
            "WHERE recipient = 'kevinjdragan@gmail.com' "
            "  AND subject LIKE ?",
            (f"[{_paper_to_podcast_job_id()}]%",),
        ).fetchone()
    except sqlite3.Error as exc:
        logger.debug("paper_to_podcast_email_delivery: query failed (%s)", exc)
        return None
    if row is None:
        return None
    try:
        total = int(row["total"] or 0)
        last_sent_raw = row["last_sent"]
    except (TypeError, KeyError, IndexError):
        last_sent_raw = row[0] if row else None
        total = int(row[1] or 0) if row and len(row) > 1 else 0
    if total == 0:
        # Pipeline hasn't been activated yet on this box — stay quiet.
        return None
    last_sent = _parse_iso(last_sent_raw)
    if last_sent is None:
        return None
    age_hours = (datetime.now(timezone.utc) - last_sent).total_seconds() / 3600.0
    if age_hours > PAPER_TO_PODCAST_MAX_AGE_HOURS:
        return {
            "observed_value": {
                "last_sent_at": last_sent.isoformat(),
                "age_hours": round(age_hours, 2),
                "total_rows": total,
            },
            "message": (
                f"Last paper_to_podcast email was {age_hours:.1f}h ago "
                f"(threshold {PAPER_TO_PODCAST_MAX_AGE_HOURS:.0f}h). The 9 PM Houston "
                "daily cron has failed — Kevin isn't getting his daily research "
                "podcast bundle."
            ),
            "threshold_text": f"last sent within last {PAPER_TO_PODCAST_MAX_AGE_HOURS:.0f}h",
        }
    return None


# ---------------------------------------------------------------------------
# 9. vault_lint_contradictions monthly cadence
# ---------------------------------------------------------------------------


@invariant(
    id="vault_lint_contradictions_monthly",
    title="Vault contradiction report exists for current month",
    description=(
        "Cron `vault_lint_contradictions` runs on the 1st of each month at "
        "7:00 AM Houston and writes "
        "artifacts/knowledge-vaults/*/contradiction-report-*.md. The probe "
        "fires after the 2nd of the month if no report covers the current "
        "month — one full day grace gives the cron time to complete."
    ),
    severity="warn",
    runbook_command=(
        "ls -lt /opt/universal_agent/artifacts/knowledge-vaults/*/contradiction-report-*.md 2>/dev/null | head; "
        "journalctl -u universal-agent-gateway --since '$(date -d \"1 day ago\")' | grep -i vault_lint"
    ),
    metadata={
        "pipeline": "vault_lint_contradictions",
        "cron_expr": "0 7 1 * *",
        "tz": "America/Chicago",
    },
)
def vault_lint_contradictions_monthly(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Fire when no vault contradiction report exists for the current month.

    After the 2nd of the month (one day grace past the 1st-of-month cron),
    checks for any ``contradiction-report-*.md`` under ``knowledge-vaults/*/``
    whose mtime falls in the current year+month. Returns None before the gate
    day, when the vault dir is absent/empty, or a current-month report exists.
    """
    artifacts_dir = ctx.get("artifacts_dir")
    if artifacts_dir is None:
        return None
    vault_root = Path(artifacts_dir) / "knowledge-vaults"
    if not vault_root.exists():
        return None
    now = _now_houston()
    # Only probe after the 2nd of the month so the 1st's cron has full grace.
    if now.day < VAULT_LINT_DAY_OF_MONTH_GATE:
        return None
    # Look for any contradiction-report-* file across all vault subdirs.
    candidates = list(vault_root.glob("*/contradiction-report-*.md"))
    if not candidates:
        # Probe stays quiet if the directory is empty — vault may not be
        # provisioned yet. Operator will discover by other means.
        return None
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    newest_mtime = datetime.fromtimestamp(newest.stat().st_mtime, tz=HOUSTON_TZ)
    # "Current month" means newest mtime year+month should match now's year+month.
    if newest_mtime.year == now.year and newest_mtime.month == now.month:
        return None
    age_days = (now - newest_mtime).total_seconds() / 86400.0
    return {
        "observed_value": {
            "newest_file": str(newest),
            "newest_mtime": newest_mtime.isoformat(),
            "age_days": round(age_days, 1),
            "current_month": now.strftime("%Y-%m"),
        },
        "message": (
            f"No contradiction report for {now.strftime('%Y-%m')}. Newest "
            f"report is {age_days:.0f}d old. The monthly 1st-of-month cron "
            "may have failed — vault contradiction sweep won't run again "
            "for another month."
        ),
        "threshold_text": "≥1 contradiction-report-*.md file dated current month",
    }


# ---------------------------------------------------------------------------
# 10. proactive brief → task_hub funnel coverage
# ---------------------------------------------------------------------------


@invariant(
    id="proactive_brief_task_funnel",
    title="Proactive artifacts produce matching task_hub_items",
    description=(
        "Each proactive source_kind (convergence_detection, insight_detection, "
        "tutorial_build) should produce roughly one task_hub_items row per "
        "artifact. A wide gap (≥N artifacts, 0 tasks in 48h) means the "
        "preference gate, dedup logic, or queue insert path is silently "
        "dropping work. This is the failure mode that hid the 2026-04-18 "
        "implicit-park preference poison incident for ~5 weeks."
    ),
    severity="warn",
    runbook_command=(
        "sqlite3 \"$UA_ACTIVITY_DB_PATH\" \""
        "SELECT source_kind, COUNT(*) AS arts FROM proactive_artifacts "
        "WHERE source_kind IN ('convergence_detection','insight_detection','tutorial_build') "
        "AND created_at >= datetime('now','-48 hours') GROUP BY source_kind;\""
    ),
    metadata={
        "pipeline": "proactive_brief_funnel",
        "tables": ["proactive_artifacts", "task_hub_items"],
    },
)
def proactive_brief_task_funnel(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Fire when a proactive source produces many artifacts but zero tasks.

    For each ``BRIEF_TASK_FUNNEL_SOURCE_KINDS`` entry, compares 48h artifact
    counts against ``task_hub_items`` counts; ``>= BRIEF_TASK_FUNNEL_MIN_ARTIFACTS``
    artifacts with 0 tasks signals the preference gate / dedup / queue-insert
    path is silently dropping work (the 2026-04-18 implicit-park poison shape).
    Returns None when no source is starved.
    """
    conn = ctx.get("activity_conn")
    if conn is None:
        return None
    starved: list[dict[str, Any]] = []
    for source_kind in BRIEF_TASK_FUNNEL_SOURCE_KINDS:
        try:
            arts = conn.execute(
                """
                SELECT COUNT(*) FROM proactive_artifacts
                WHERE source_kind = ?
                  AND created_at >= datetime('now', ?)
                """,
                (source_kind, f"-{BRIEF_TASK_FUNNEL_WINDOW_HOURS} hours"),
            ).fetchone()[0]
            tasks = conn.execute(
                """
                SELECT COUNT(*) FROM task_hub_items
                WHERE source_kind = ?
                  AND created_at >= datetime('now', ?)
                """,
                (source_kind, f"-{BRIEF_TASK_FUNNEL_WINDOW_HOURS} hours"),
            ).fetchone()[0]
        except sqlite3.Error as exc:
            logger.debug("proactive_brief_task_funnel: query failed for %s (%s)", source_kind, exc)
            continue
        if arts >= BRIEF_TASK_FUNNEL_MIN_ARTIFACTS and tasks == 0:
            starved.append({
                "source_kind": source_kind,
                "artifacts_48h": int(arts),
                "task_hub_items_48h": 0,
            })
    if not starved:
        return None
    return {
        "observed_value": {
            "starved_pipelines": starved,
            "window_hours": BRIEF_TASK_FUNNEL_WINDOW_HOURS,
        },
        "message": (
            f"{len(starved)} proactive pipeline(s) produced ≥"
            f"{BRIEF_TASK_FUNNEL_MIN_ARTIFACTS} artifacts in "
            f"{BRIEF_TASK_FUNNEL_WINDOW_HOURS}h but zero task_hub_items. "
            "The preference gate or queue insert path is silently dropping "
            "work. Check proactive_preference_signals for poison and "
            "should_block_proactive_task call sites."
        ),
        "threshold_text": (
            f"if artifacts_48h ≥ {BRIEF_TASK_FUNNEL_MIN_ARTIFACTS} then task_hub_items_48h > 0"
        ),
    }
