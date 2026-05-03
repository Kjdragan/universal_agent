"""Mission Control Intelligence — Tier-0 traffic-light tiles.

Phase 1 deliverable. Defines the `Tile` base class plus the nine concrete
tiles that form the operator at-a-glance health strip:

  - Gateway, Database, CSI Ingester, Cron Pipelines, Heartbeat Daemon,
    Task Hub Pressure, Model Usage Today, Proactive Pipeline, VP Agent Health

Each tile is a Python-driven traffic-light component that costs near
zero compute on every poll. The LLM only fires to annotate a tile when
it transitions to yellow/red. State and transition timing are persisted
in `mission_control_tile_states` (see mission_control_db.py).

Design contract — every Tile subclass MUST:
  * Have a stable `name` (string, used as primary key in tile_states)
  * Implement `compute_state(conn)` returning a `TileState`
  * Be cheap: signature/state computation runs every 60s by the sweeper
  * Be defensive: a missing data source returns `unknown` — never raises

See docs/02_Subsystems/Mission_Control_Intelligence_System.md §4.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Tile color vocabulary lives at the column-check level in
# mission_control_tile_states; we mirror it here as constants so tests
# and tile authors share the same names.
COLOR_GREEN = "green"
COLOR_YELLOW = "yellow"
COLOR_RED = "red"
COLOR_UNKNOWN = "unknown"

VALID_COLORS = {COLOR_GREEN, COLOR_YELLOW, COLOR_RED, COLOR_UNKNOWN}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _safe_parse_iso(value: str | None) -> datetime | None:
    """Parse ISO-ish timestamps into a UTC-aware datetime.

    SQLite's `datetime('now', ...)` produces naive `YYYY-MM-DD HH:MM:SS`
    strings (always UTC). Application code writes timezone-aware
    ISO 8601 strings (`...+00:00`). Both must coerce to UTC-aware so
    subtraction against `_utc_now()` is well-defined.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass
class TileState:
    """Structured tile-state snapshot returned by `Tile.compute_state()`.

    `signature` is a deterministic hash of the inputs used to derive the
    state. The sweeper uses it to decide whether anything has actually
    moved since the last poll — if the signature is unchanged, no
    persistence write and no transition logic runs.
    """

    color: str
    one_line_status: str
    evidence: dict[str, Any] = field(default_factory=dict)
    signature: str = ""

    def __post_init__(self) -> None:
        if self.color not in VALID_COLORS:
            raise ValueError(
                f"TileState.color must be one of {sorted(VALID_COLORS)}, got {self.color!r}"
            )
        if not self.signature:
            payload = json.dumps(
                {"color": self.color, "status": self.one_line_status, "evidence": self.evidence},
                sort_keys=True,
                default=str,
            )
            self.signature = hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Tile:
    """Abstract base class for a Mission Control tier-0 tile.

    Subclasses override `compute_state` and may override
    `llm_annotation_prompt` and `auto_action_class`. The base class
    provides defensive scaffolding so a buggy tile never crashes the
    sweeper loop — exceptions inside `compute_state` are caught at the
    sweeper layer (see mission_control_intelligence_sweeper.py).
    """

    # Stable identifier; primary key in mission_control_tile_states.
    name: str = ""

    # Operator-facing label.
    display_name: str = ""

    def compute_state(self, conn: sqlite3.Connection) -> TileState:
        raise NotImplementedError

    def llm_annotation_prompt(self, state: TileState) -> str:
        """Prompt to enrich a yellow/red transition with a short LLM
        annotation. Default implementation returns a generic prompt;
        each tile may override with a domain-specific framing.
        """
        return (
            f"You are Universal Agent Mission Control. The {self.display_name or self.name} "
            f"tile just transitioned to color={state.color}. The mechanical status line is:\n"
            f"  {state.one_line_status}\n"
            f"Evidence:\n{json.dumps(state.evidence, indent=2, default=str)}\n\n"
            "In 2-4 short sentences, explain what this likely means for the operator and what "
            "they should consider checking next. Be specific. Avoid restating the status line."
        )

    def auto_action_class(self) -> str | None:
        """Return one of 'A', 'B', 'C', or None.

        Drives the auto-remediation registry in Phase 5. Phase 1 only
        records the value alongside the tile state for later use; no
        auto-action fires in Phase 1.
        """
        return None


# ── Concrete tile implementations ────────────────────────────────────────
# All tiles use the activity-events DB connection passed in by the sweeper.
# Tiles that need a different data source (e.g. CSI ingester DB) open
# their own short-lived connection inside compute_state().


class GatewayTile(Tile):
    name = "gateway"
    display_name = "Gateway"

    def compute_state(self, conn: sqlite3.Connection) -> TileState:
        # Gateway health is derived from the most recent activity-event
        # `health_check` style heartbeat. If we have no events at all in
        # the last 5 minutes the gateway is effectively silent.
        try:
            row = conn.execute(
                """
                SELECT MAX(created_at) AS last_event
                FROM activity_events
                WHERE created_at > datetime('now','-15 minutes')
                """
            ).fetchone()
        except sqlite3.OperationalError as exc:
            return TileState(
                color=COLOR_UNKNOWN,
                one_line_status=f"activity DB unavailable: {exc}",
                evidence={"error": str(exc)},
            )

        last_event = row["last_event"] if row else None
        if last_event is None:
            return TileState(
                color=COLOR_YELLOW,
                one_line_status="no activity events in last 15 min",
                evidence={"last_event": None},
            )
        last_dt = _safe_parse_iso(last_event)
        if last_dt is None:
            return TileState(
                color=COLOR_UNKNOWN,
                one_line_status=f"unparseable last_event timestamp: {last_event}",
                evidence={"last_event": last_event},
            )
        age_s = (_utc_now() - last_dt).total_seconds()
        if age_s <= 60:
            color = COLOR_GREEN
            status = f"active, last event {int(age_s)}s ago"
        elif age_s <= 300:
            color = COLOR_YELLOW
            status = f"quiet for {int(age_s)}s"
        else:
            color = COLOR_RED
            status = f"silent for {int(age_s)}s"
        return TileState(
            color=color,
            one_line_status=status,
            evidence={"last_event_iso": last_event, "age_seconds": age_s},
        )


class DatabaseTile(Tile):
    name = "database"
    display_name = "Database"

    def compute_state(self, conn: sqlite3.Connection) -> TileState:
        # A trivial SELECT 1 latency check. Anything sub-100ms is green;
        # 100ms-1s is yellow (lock contention or busy WAL); >1s is red.
        import time

        try:
            t0 = time.perf_counter()
            conn.execute("SELECT 1").fetchone()
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
        except sqlite3.OperationalError as exc:
            return TileState(
                color=COLOR_RED,
                one_line_status=f"activity DB unreachable: {exc}",
                evidence={"error": str(exc)},
            )

        if elapsed_ms < 100:
            color = COLOR_GREEN
            status = f"OK ({elapsed_ms:.1f}ms)"
        elif elapsed_ms < 1000:
            color = COLOR_YELLOW
            status = f"slow ({elapsed_ms:.0f}ms)"
        else:
            color = COLOR_RED
            status = f"very slow ({elapsed_ms:.0f}ms)"
        return TileState(
            color=color,
            one_line_status=status,
            evidence={"select1_ms": elapsed_ms},
        )


class CsiIngesterTile(Tile):
    name = "csi_ingester"
    display_name = "CSI Ingester"

    def auto_action_class(self) -> str | None:  # noqa: D401 — see docstring
        # Class A in Phase 5 (csi-ingester restart). Phase 1 just
        # records the intent; nothing fires.
        return "A"

    def compute_state(self, conn: sqlite3.Connection) -> TileState:
        # CSI is a TWICE-DAILY scheduled job (cron 0 8,16 America/Chicago
        # = ~13:00 / 21:00 UTC). Expected gap between events is ~8-12h,
        # so we tune the freshness traffic-light to match cadence rather
        # than hourly polling:
        #   green  = within one full polling window  (≤ 12h)
        #   yellow = missed exactly one cycle        (12h < age ≤ 25h)
        #   red    = missed ≥ 2 cycles               (> 25h)
        # SQL window is 48h so the 24-25h yellow zone resolves correctly.
        try:
            row = conn.execute(
                """
                SELECT MAX(created_at) AS last_event,
                       COUNT(*) AS events_24h
                FROM activity_events
                WHERE source_domain = 'csi'
                  AND created_at > datetime('now','-48 hours')
                """
            ).fetchone()
        except sqlite3.OperationalError as exc:
            return TileState(
                color=COLOR_UNKNOWN,
                one_line_status=f"activity DB unavailable: {exc}",
                evidence={"error": str(exc)},
            )

        events_24h = int(row["events_24h"] or 0) if row else 0
        last_event = row["last_event"] if row else None
        last_dt = _safe_parse_iso(last_event)

        if last_dt is None:
            return TileState(
                color=COLOR_RED,
                one_line_status="no CSI events in last 48h",
                evidence={"events_24h": 0, "last_event_iso": None},
            )

        age_s = (_utc_now() - last_dt).total_seconds()
        if age_s <= 43200:  # 12h — within one polling window
            color = COLOR_GREEN
            status = f"{events_24h} events recent, last {int(age_s/60)}m ago"
        elif age_s <= 90000:  # 25h — missed one cycle
            color = COLOR_YELLOW
            status = f"no CSI events in {int(age_s/3600)}h (missed 1 cycle)"
        else:  # > 25h — missed ≥ 2 cycles
            color = COLOR_RED
            status = f"no CSI events in {int(age_s/3600)}h (missed ≥2 cycles)"
        return TileState(
            color=color,
            one_line_status=status,
            evidence={
                "events_24h": events_24h,
                "last_event_iso": last_event,
                "age_seconds": age_s,
            },
        )


class CronPipelinesTile(Tile):
    name = "cron_pipelines"
    display_name = "Cron Pipelines"

    def auto_action_class(self) -> str | None:
        return "B"

    def compute_state(self, conn: sqlite3.Connection) -> TileState:
        # We count cron failures in last 24h grouped by job. Yellow = one
        # job failed once. Red = >=2 distinct jobs failing OR same job
        # failed >=3 times.
        try:
            rows = conn.execute(
                """
                SELECT
                    COALESCE(json_extract(metadata_json, '$.job_id'), 'unknown') AS job_id,
                    COUNT(*) AS failures
                FROM activity_events
                WHERE source_domain = 'cron'
                  AND severity = 'error'
                  AND created_at > datetime('now','-24 hours')
                GROUP BY job_id
                """
            ).fetchall()
        except sqlite3.OperationalError as exc:
            return TileState(
                color=COLOR_UNKNOWN,
                one_line_status=f"activity DB unavailable: {exc}",
                evidence={"error": str(exc)},
            )

        failures_by_job = {row["job_id"]: int(row["failures"]) for row in rows}
        total_failures = sum(failures_by_job.values())
        distinct_jobs = len(failures_by_job)
        max_per_job = max(failures_by_job.values(), default=0)

        if total_failures == 0:
            color = COLOR_GREEN
            status = "all scheduled jobs ran on time in last 24h"
        elif distinct_jobs >= 2 or max_per_job >= 3:
            color = COLOR_RED
            status = f"{distinct_jobs} job(s) failing, {total_failures} failures in 24h"
        else:
            color = COLOR_YELLOW
            status = f"1 job failed {max_per_job}x in 24h"
        return TileState(
            color=color,
            one_line_status=status,
            evidence={
                "failures_by_job": failures_by_job,
                "total_failures_24h": total_failures,
                "distinct_failing_jobs": distinct_jobs,
            },
        )


class HeartbeatDaemonTile(Tile):
    name = "heartbeat_daemon"
    display_name = "Heartbeat Daemon"

    def auto_action_class(self) -> str | None:
        return "A"

    def compute_state(self, conn: sqlite3.Connection) -> TileState:
        # Look for the most recent heartbeat-source activity event. If
        # the gap exceeds 2x the expected interval (default 90s), one
        # tick is missed; >=3 missed ticks (gap > ~5m) is red.
        expected_interval_s = float(os.getenv("UA_HEARTBEAT_INTERVAL_SECONDS", "90"))
        try:
            row = conn.execute(
                """
                SELECT MAX(created_at) AS last_tick
                FROM activity_events
                WHERE source_domain = 'heartbeat'
                """
            ).fetchone()
        except sqlite3.OperationalError as exc:
            return TileState(
                color=COLOR_UNKNOWN,
                one_line_status=f"activity DB unavailable: {exc}",
                evidence={"error": str(exc)},
            )

        last_tick = row["last_tick"] if row else None
        last_dt = _safe_parse_iso(last_tick)
        if last_dt is None:
            return TileState(
                color=COLOR_RED,
                one_line_status="no heartbeat ticks recorded",
                evidence={"last_tick_iso": None, "expected_interval_s": expected_interval_s},
            )
        age_s = (_utc_now() - last_dt).total_seconds()
        missed = max(0, int(age_s / expected_interval_s) - 1)
        if missed == 0:
            color = COLOR_GREEN
            status = f"tick {int(age_s)}s ago, interval {int(expected_interval_s)}s"
        elif missed < 3:
            color = COLOR_YELLOW
            status = f"{missed} missed tick(s), last {int(age_s)}s ago"
        else:
            color = COLOR_RED
            status = f">={missed} missed ticks, last {int(age_s)}s ago"
        return TileState(
            color=color,
            one_line_status=status,
            evidence={
                "last_tick_iso": last_tick,
                "age_seconds": age_s,
                "missed_ticks": missed,
                "expected_interval_s": expected_interval_s,
            },
        )


class TaskHubPressureTile(Tile):
    name = "task_hub_pressure"
    display_name = "Task Hub Pressure"

    def auto_action_class(self) -> str | None:
        return "A"

    def compute_state(self, conn: sqlite3.Connection) -> TileState:
        # Two signals:
        #   in_progress count
        #   stuck claims = items in_progress with updated_at older than
        #     UA_TASK_HUB_STUCK_THRESHOLD_MINUTES (default 15).
        stuck_minutes = int(os.getenv("UA_TASK_HUB_STUCK_THRESHOLD_MINUTES", "15"))
        try:
            in_progress_row = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM task_hub_items
                WHERE status = 'in_progress'
                """
            ).fetchone()
            stuck_row = conn.execute(
                f"""
                SELECT COUNT(*) AS n
                FROM task_hub_items
                WHERE status = 'in_progress'
                  AND updated_at < datetime('now','-{stuck_minutes} minutes')
                """
            ).fetchone()
        except sqlite3.OperationalError as exc:
            return TileState(
                color=COLOR_UNKNOWN,
                one_line_status=f"task_hub query failed: {exc}",
                evidence={"error": str(exc)},
            )

        in_progress = int(in_progress_row["n"] or 0)
        stuck = int(stuck_row["n"] or 0)

        if in_progress > 25 or stuck >= 3:
            color = COLOR_RED
            status = f"{in_progress} in_progress, {stuck} stuck >{stuck_minutes}m"
        elif in_progress > 10 or stuck >= 1:
            color = COLOR_YELLOW
            status = f"{in_progress} in_progress, {stuck} stuck"
        else:
            color = COLOR_GREEN
            status = f"{in_progress} in_progress, no stuck claims"
        return TileState(
            color=color,
            one_line_status=status,
            evidence={
                "in_progress": in_progress,
                "stuck_claims": stuck,
                "stuck_threshold_minutes": stuck_minutes,
            },
        )


class ModelUsageTodayTile(Tile):
    name = "model_usage_today"
    display_name = "Model Usage Today"

    def compute_state(self, conn: sqlite3.Connection) -> TileState:
        # Counts 429 rate-limit error events from any model lane today.
        # We don't have a hard daily-spend table here in Phase 1, so we
        # focus on the alarm signal (rate-limiting) which is what
        # actually hurts the operator. Spend reporting is a follow-up.
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS rate_limits
                FROM activity_events
                WHERE created_at > date('now')
                  AND (
                    LOWER(COALESCE(summary,'')) LIKE '%429%'
                    OR LOWER(COALESCE(full_message,'')) LIKE '%rate limit%'
                  )
                """
            ).fetchone()
        except sqlite3.OperationalError as exc:
            return TileState(
                color=COLOR_UNKNOWN,
                one_line_status=f"activity DB unavailable: {exc}",
                evidence={"error": str(exc)},
            )

        rate_limits = int(row["rate_limits"] or 0) if row else 0
        if rate_limits == 0:
            color = COLOR_GREEN
            status = "no rate-limits today"
        elif rate_limits < 5:
            color = COLOR_YELLOW
            status = f"{rate_limits} rate-limit event(s) today"
        else:
            color = COLOR_RED
            status = f"{rate_limits} rate-limit events today"
        return TileState(
            color=color,
            one_line_status=status,
            evidence={"rate_limit_events_today": rate_limits},
        )


class ProactivePipelineTile(Tile):
    name = "proactive_pipeline"
    display_name = "Proactive Pipeline"

    def auto_action_class(self) -> str | None:
        return "B"

    def compute_state(self, conn: sqlite3.Connection) -> TileState:
        try:
            row = conn.execute(
                """
                SELECT
                  COUNT(*) AS completions_48h,
                  MAX(updated_at) AS last_completion
                FROM task_hub_items
                WHERE source_kind LIKE 'proactive_%'
                  AND status = 'completed'
                  AND updated_at > datetime('now','-48 hours')
                """
            ).fetchone()
            failures_row = conn.execute(
                """
                SELECT COUNT(*) AS recent_failures
                FROM task_hub_items
                WHERE source_kind LIKE 'proactive_%'
                  AND status IN ('failed','blocked')
                  AND updated_at > datetime('now','-24 hours')
                """
            ).fetchone()
        except sqlite3.OperationalError as exc:
            return TileState(
                color=COLOR_UNKNOWN,
                one_line_status=f"task_hub query failed: {exc}",
                evidence={"error": str(exc)},
            )

        completions = int(row["completions_48h"] or 0) if row else 0
        last_completion = row["last_completion"] if row else None
        failures = int(failures_row["recent_failures"] or 0) if failures_row else 0

        if failures >= 3:
            color = COLOR_RED
            status = f"{failures} consecutive proactive failures in 24h"
        elif completions == 0:
            color = COLOR_YELLOW
            status = "no proactive completions in 48h"
        else:
            color = COLOR_GREEN
            status = f"{completions} proactive completions in 48h"
        return TileState(
            color=color,
            one_line_status=status,
            evidence={
                "completions_48h": completions,
                "last_completion_iso": last_completion,
                "recent_failures_24h": failures,
            },
        )


class VPAgentHealthTile(Tile):
    name = "vp_agent_health"
    display_name = "VP Agent Health"

    def auto_action_class(self) -> str | None:
        return "B"

    def compute_state(self, conn: sqlite3.Connection) -> TileState:
        # VP missions land in task_hub_items with source_kind='vp_mission'
        # and source_ref = vp.coder.primary | vp.general.primary.
        try:
            rows = conn.execute(
                """
                SELECT
                  source_ref,
                  SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                  SUM(CASE WHEN status IN ('failed','blocked') THEN 1 ELSE 0 END) AS failed,
                  COUNT(*) AS total
                FROM task_hub_items
                WHERE source_kind = 'vp_mission'
                  AND updated_at > datetime('now','-7 days')
                GROUP BY source_ref
                """
            ).fetchall()
        except sqlite3.OperationalError as exc:
            return TileState(
                color=COLOR_UNKNOWN,
                one_line_status=f"task_hub query failed: {exc}",
                evidence={"error": str(exc)},
            )

        per_vp: dict[str, dict[str, int]] = {}
        for row in rows:
            per_vp[row["source_ref"]] = {
                "completed": int(row["completed"] or 0),
                "failed": int(row["failed"] or 0),
                "total": int(row["total"] or 0),
            }

        if not per_vp:
            return TileState(
                color=COLOR_YELLOW,
                one_line_status="no VP missions in last 7d",
                evidence={"per_vp": per_vp},
            )

        # Red if any VP has >=3 failures and zero completions in window;
        # yellow if any VP failure rate >50% over >=3 missions; otherwise
        # green.
        worst_color = COLOR_GREEN
        worst_summary = ""
        for vp_name, stats in per_vp.items():
            if stats["total"] == 0:
                continue
            fail_rate = stats["failed"] / stats["total"]
            if stats["failed"] >= 3 and stats["completed"] == 0:
                worst_color = COLOR_RED
                worst_summary = f"{vp_name}: {stats['failed']} failures, 0 completions"
                break
            if stats["total"] >= 3 and fail_rate > 0.5 and worst_color == COLOR_GREEN:
                worst_color = COLOR_YELLOW
                worst_summary = f"{vp_name}: {fail_rate:.0%} failure rate"

        if worst_color == COLOR_GREEN:
            total_completed = sum(v["completed"] for v in per_vp.values())
            worst_summary = f"both VPs healthy, {total_completed} completed in 7d"
        return TileState(
            color=worst_color,
            one_line_status=worst_summary,
            evidence={"per_vp": per_vp},
        )


# ── Registry ────────────────────────────────────────────────────────────


_ALL_TILE_CLASSES: list[type[Tile]] = [
    GatewayTile,
    DatabaseTile,
    CsiIngesterTile,
    CronPipelinesTile,
    HeartbeatDaemonTile,
    TaskHubPressureTile,
    ModelUsageTodayTile,
    ProactivePipelineTile,
    VPAgentHealthTile,
]


def all_tiles() -> list[Tile]:
    """Return a fresh list of tile instances in stable display order."""
    return [cls() for cls in _ALL_TILE_CLASSES]


def tile_by_name(name: str) -> Tile | None:
    for cls in _ALL_TILE_CLASSES:
        if cls.name == name:
            return cls()
    return None
