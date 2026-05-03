"""Mission Control Intelligence System — backend sweeper daemon.

Phase 0 scaffolding. Defines the `MissionControlSweeper` class that, in
later phases, will drive the three-tier intelligence pipeline:

  - Tier 0: poll the 9 traffic-light tiles every tick
  - Tier 1: gate LLM card-discovery passes by bundle-signature change
  - Tier 2: gate page-synthesis on tier-0 transitions or tier-1 success
  - Drain at most 1 in-flight glm-4.7 call at a time on the dedicated lane

Phase 0 ships the skeleton ONLY. The sweeper does not run anywhere yet,
and `tick()` is a no-op until `UA_MC_PHASE_1_ENABLED=1` flips on tier-0
behavior. This file exists so:

  - the import surface is stable across phases
  - tests can verify the feature-flag gating without spinning real work
  - downstream phases can fill in the tier handlers in-place

See docs/02_Subsystems/Mission_Control_Intelligence_System.md §2.2 and §9.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from universal_agent.services.mission_control_cards import (
    SEVERITY_CRITICAL,
    SEVERITY_INFORMATIONAL,
    SEVERITY_WARNING,
    SEVERITY_WATCHING,
    SUBJECT_INFRASTRUCTURE,
    CardUpsert,
    upsert_card,
)
from universal_agent.services.mission_control_db import is_phase_enabled, open_store
from universal_agent.services.mission_control_tiles import (
    COLOR_GREEN,
    COLOR_RED,
    COLOR_UNKNOWN,
    COLOR_YELLOW,
    Tile,
    TileState,
    all_tiles,
)

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return default


def _get_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw.strip())
    except (TypeError, ValueError):
        return default


@dataclass
class SweeperConfig:
    """Runtime configuration for the Mission Control sweeper.

    All fields are env-overridable. Defaults match the policy table in
    docs/02_Subsystems/Mission_Control_Intelligence_System.md §2.3.
    """

    interval_seconds: float = 60.0
    tier1_floor_seconds: float = 180.0
    tier1_ceiling_seconds: float = 1800.0
    tier2_floor_seconds: float = 120.0
    tier2_ceiling_seconds: float = 300.0
    lane_concurrency: int = 1
    auto_remediation_enabled: bool = False

    @classmethod
    def from_env(cls) -> "SweeperConfig":
        return cls(
            interval_seconds=_get_float_env("UA_MISSION_CONTROL_SWEEPER_INTERVAL_S", 60.0),
            tier1_floor_seconds=_get_float_env("UA_MISSION_CONTROL_TIER1_FLOOR_S", 180.0),
            tier1_ceiling_seconds=_get_float_env("UA_MISSION_CONTROL_TIER1_CEILING_S", 1800.0),
            tier2_floor_seconds=_get_float_env("UA_MISSION_CONTROL_TIER2_FLOOR_S", 120.0),
            tier2_ceiling_seconds=_get_float_env("UA_MISSION_CONTROL_TIER2_CEILING_S", 300.0),
            lane_concurrency=max(1, _get_int_env("UA_MISSION_CONTROL_LANE_CONCURRENCY", 1)),
            auto_remediation_enabled=(os.getenv("UA_MISSION_CONTROL_AUTO_REMEDIATION") or "0").strip().lower()
                in {"1", "true", "yes", "on", "enabled"},
        )


@dataclass
class SweepResult:
    """Outcome of a single sweeper tick. Used for observability + tests."""

    started_at_utc: str
    finished_at_utc: str
    tier0_checked: bool = False
    tier0_transitions: list[str] = field(default_factory=list)
    tier1_evaluated: bool = False
    tier1_synthesized: bool = False
    tier2_evaluated: bool = False
    tier2_synthesized: bool = False
    skipped_reason: str | None = None
    errors: list[str] = field(default_factory=list)


class MissionControlSweeper:
    """Single-process backend sweeper.

    Phase 0: structural skeleton only. `tick()` early-returns with a
    `skipped_reason` until later phases enable their respective tiers.

    The sweeper is intentionally NOT a long-running async task at this
    phase. Wiring the gateway to call `tick()` on a schedule is part of
    Phase 1's deliverable.
    """

    def __init__(self, config: SweeperConfig | None = None) -> None:
        self.config = config or SweeperConfig.from_env()

    # ── Public API ─────────────────────────────────────────────────────

    def tick(self) -> SweepResult:
        """Run a single sweep cycle.

        Returns a `SweepResult` describing what was checked, what was
        skipped, and any errors. Always safe to call — never raises.
        """
        started = _utc_now_iso()

        if not is_phase_enabled(1):
            return SweepResult(
                started_at_utc=started,
                finished_at_utc=_utc_now_iso(),
                skipped_reason="phase_1_not_enabled",
            )

        # Phase 1+ implementation lands here. Each tier handler will be
        # filled in incrementally without changing this orchestration
        # surface.
        result = SweepResult(started_at_utc=started, finished_at_utc=started)
        try:
            self._run_tier0(result)
            if is_phase_enabled(2):
                self._run_tier1(result)
            if is_phase_enabled(3):
                self._run_tier2(result)
        except Exception as exc:  # never crash the sweeper loop
            logger.exception("Mission Control sweeper tick failed")
            result.errors.append(str(exc))
        finally:
            result.finished_at_utc = _utc_now_iso()
        return result

    # ── Tier handlers ──────────────────────────────────────────────────

    def _run_tier0(self, result: SweepResult) -> None:
        """Tier-0 sweep: poll tiles, persist state, detect transitions,
        auto-create infrastructure cards on yellow/red transitions.

        Errors inside an individual tile are caught and recorded in
        `result.errors` so a buggy tile cannot take out the whole tier-0
        cycle. State and transition writes happen inside a single MC
        store connection per sweep, so concurrent reads from the gateway
        always see a consistent snapshot.
        """
        result.tier0_checked = True
        try:
            mc_conn = open_store()
        except Exception as exc:  # store init failure is fatal for tier-0
            result.errors.append(f"open_store failed: {exc}")
            return

        try:
            data_conn = self._open_activity_db()
        except Exception as exc:
            result.errors.append(f"activity DB open failed: {exc}")
            mc_conn.close()
            return

        try:
            for tile in all_tiles():
                try:
                    state = tile.compute_state(data_conn)
                except Exception as exc:
                    logger.exception("tile %s compute_state failed", tile.name)
                    result.errors.append(f"tile {tile.name}: {exc}")
                    continue

                outcome = self._persist_tile_state(mc_conn, tile, state)
                if outcome.get("transitioned"):
                    result.tier0_transitions.append(
                        f"{tile.name}:{outcome['prior_color']}->{state.color}"
                    )
                # Card creation fires on:
                #   1. transition_to_non_green (was green/unknown, now yellow/red), OR
                #   2. first_appearance_non_green (no prior row, opens at yellow/red)
                # The latter is critical: a freshly-restarted gateway with a
                # red tile on its first tick must still produce a card.
                # `upsert_card` is idempotent on subject_id, so re-firing on
                # subsequent same-color sweeps is safe but unnecessary — we
                # gate so identical re-syntheses don't bloat history.
                needs_card = (
                    state.color in {COLOR_YELLOW, COLOR_RED}
                    and (
                        outcome.get("first_appearance")
                        or (outcome.get("transitioned")
                            and outcome.get("prior_color") not in {COLOR_YELLOW, COLOR_RED})
                        or (outcome.get("transitioned")
                            and outcome.get("prior_color") in {COLOR_YELLOW, COLOR_RED}
                            and outcome["prior_color"] != state.color)
                    )
                )
                if needs_card:
                    try:
                        self._auto_create_infrastructure_card(
                            mc_conn, tile, state,
                            trigger="first_appearance" if outcome.get("first_appearance") else "transition",
                        )
                    except Exception as exc:
                        logger.exception("auto-card creation failed for %s", tile.name)
                        result.errors.append(f"auto-card {tile.name}: {exc}")
        finally:
            data_conn.close()
            mc_conn.close()

    def _run_tier1(self, result: SweepResult) -> None:
        """Tier-1 LLM card-discovery pass. Phase 2 fills this in."""
        result.tier1_evaluated = True

    def _run_tier2(self, result: SweepResult) -> None:
        """Tier-2 page synthesis. Phase 3 fills this in."""
        result.tier2_evaluated = True

    # ── Tier-0 helpers ─────────────────────────────────────────────────

    def _open_activity_db(self):  # type: ignore[no-untyped-def]
        """Open a connection to the activity / task_hub DB.

        Indirection so tests can override with a fixture-backed
        connection. Default implementation uses the standard runtime DB
        helper.
        """
        from universal_agent.durable.db import (
            connect_runtime_db,
            get_activity_db_path,
        )

        return connect_runtime_db(get_activity_db_path())

    def _persist_tile_state(
        self, conn, tile: Tile, state: TileState
    ) -> dict[str, Any]:
        """Write the new tile state. Returns an outcome dict describing
        what just happened so the caller can decide whether to fire
        downstream effects (card creation, transition logging).

        Outcome keys:
          - signature_unchanged: bool — true when no real state change
            since the last poll; only `last_checked_at` was bumped.
          - first_appearance: bool — true when this tile had no prior
            row at all (sweeper's first encounter with the tile, or DB
            wipe). Critical: a freshly-booted gateway with a red tile
            on its first tick must still emit a card, so this is the
            signal to do that.
          - transitioned: bool — true when the color actually changed.
          - prior_color: previous color, or None on first_appearance.
        """
        existing = conn.execute(
            """
            SELECT current_state, last_signature, state_since
            FROM mission_control_tile_states
            WHERE tile_id = ?
            """,
            (tile.name,),
        ).fetchone()
        now = _utc_now_iso()
        prior_color = existing["current_state"] if existing else None
        prior_signature = existing["last_signature"] if existing else None
        prior_since = existing["state_since"] if existing else None

        # If signature unchanged, just bump last_checked_at and bail.
        if existing is not None and prior_signature == state.signature:
            conn.execute(
                """
                UPDATE mission_control_tile_states
                SET last_checked_at = ?
                WHERE tile_id = ?
                """,
                (now, tile.name),
            )
            return {
                "signature_unchanged": True,
                "first_appearance": False,
                "transitioned": False,
                "prior_color": prior_color,
            }

        first_appearance = existing is None
        transitioned = prior_color is not None and prior_color != state.color
        state_since = now if (prior_color != state.color) else (prior_since or now)
        evidence_json = self._dump_json(state.evidence)

        if existing is None:
            conn.execute(
                """
                INSERT INTO mission_control_tile_states (
                    tile_id, current_state, state_since, last_signature,
                    last_checked_at, current_annotation, evidence_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (tile.name, state.color, state_since, state.signature, now,
                 state.one_line_status, evidence_json),
            )
        else:
            # current_annotation is the operator-facing one-line status.
            # Phase 1 writes the mechanical status; Phase 5 auto-diagnostic
            # LLM passes will overwrite with richer prose. We do NOT
            # overwrite current_annotation if a richer annotation was
            # written by a Phase 5+ pass since the last poll — preserving
            # LLM enrichment across mechanical re-polls.
            existing_annotation = existing["current_annotation"] if "current_annotation" in existing.keys() else None
            annotation_to_write = state.one_line_status
            if existing_annotation and existing["last_annotation_at"] and existing["current_state"] == state.color:
                annotation_to_write = existing_annotation
            conn.execute(
                """
                UPDATE mission_control_tile_states
                SET current_state = ?,
                    state_since = ?,
                    last_signature = ?,
                    last_checked_at = ?,
                    current_annotation = ?,
                    evidence_payload_json = ?
                WHERE tile_id = ?
                """,
                (state.color, state_since, state.signature, now,
                 annotation_to_write, evidence_json, tile.name),
            )
        return {
            "signature_unchanged": False,
            "first_appearance": first_appearance,
            "transitioned": transitioned,
            "prior_color": prior_color,
        }

    def _auto_create_infrastructure_card(
        self, conn, tile: Tile, state: TileState, *, trigger: str = "transition"
    ) -> None:
        """Create or revive the infrastructure-kind tier-1 card paired
        with this tile. Phase 1 uses the tile's mechanical status as
        narrative; Phase 5's diagnostic mission and Phase 2's tier-1
        discovery pass enrich it later.

        `trigger` is one of:
          - "transition"        — tile changed color (was different, now non-green)
          - "first_appearance"  — tile had no prior row and opens at non-green
        Phrased differently in narrative so the card audit trail is
        accurate after the fact.
        """
        severity = self._severity_for_color(state.color)
        if trigger == "first_appearance":
            opener = (
                f"Tile `{tile.name}` ({tile.display_name}) first observed at "
                f"`{state.color}` on this sweeper boot."
            )
        else:
            opener = (
                f"Tile `{tile.name}` ({tile.display_name}) transitioned to "
                f"`{state.color}`."
            )
        narrative = (
            f"{opener} Mechanical status:\n  {state.one_line_status}\n\n"
            "Phase 1 produced this card mechanically from the tile state. "
            "Phase 2 tier-1 LLM discovery and Phase 5 auto-diagnostic missions "
            "will enrich the narrative on subsequent sweeps."
        )
        why_it_matters = (
            "Infrastructure tile transitions are the operator's first signal that "
            "a subsystem changed state. The auto-created card guarantees a deep-dive "
            "exists wherever a tile alarms — even before LLM enrichment runs."
        )
        upsert_card(
            conn,
            CardUpsert(
                subject_kind=SUBJECT_INFRASTRUCTURE,
                subject_id=f"infra:{tile.name}",
                severity=severity,
                title=f"{tile.display_name}: {state.one_line_status}",
                narrative=narrative,
                why_it_matters=why_it_matters,
                tags=["infrastructure", tile.name, state.color],
                evidence_refs=[
                    {
                        "kind": "tile",
                        "id": tile.name,
                        "uri": f"/dashboard/mission-control#tile-{tile.name}",
                        "label": f"{tile.display_name} tile",
                    }
                ],
                evidence_payload=state.evidence,
                synthesis_model=None,  # not LLM-synthesized in Phase 1
                evidence_signature=state.signature,
            ),
        )

    @staticmethod
    def _severity_for_color(color: str) -> str:
        if color == COLOR_RED:
            return SEVERITY_CRITICAL
        if color == COLOR_YELLOW:
            return SEVERITY_WARNING
        if color == COLOR_UNKNOWN:
            return SEVERITY_WATCHING
        if color == COLOR_GREEN:
            return SEVERITY_INFORMATIONAL
        return SEVERITY_INFORMATIONAL

    @staticmethod
    def _dump_json(value: Any) -> str | None:
        if value is None:
            return None
        try:
            return json.dumps(value, default=str)
        except (TypeError, ValueError):
            return json.dumps({"unserializable": str(value)})


# Module-level singleton so the gateway can grab a stable handle. The
# instance is cheap to construct; consumers should treat this as the
# canonical sweeper for the process.
_sweeper_instance: MissionControlSweeper | None = None


def get_sweeper() -> MissionControlSweeper:
    """Return the process-wide MissionControlSweeper singleton."""
    global _sweeper_instance
    if _sweeper_instance is None:
        _sweeper_instance = MissionControlSweeper()
    return _sweeper_instance


def reset_sweeper_for_tests() -> None:
    """Test-only helper: drop the singleton so a fresh config is picked up."""
    global _sweeper_instance
    _sweeper_instance = None


def _config_summary(cfg: SweeperConfig) -> dict[str, Any]:
    """Compact dict for logging / health endpoints."""
    return {
        "interval_seconds": cfg.interval_seconds,
        "tier1_floor_seconds": cfg.tier1_floor_seconds,
        "tier1_ceiling_seconds": cfg.tier1_ceiling_seconds,
        "tier2_floor_seconds": cfg.tier2_floor_seconds,
        "tier2_ceiling_seconds": cfg.tier2_ceiling_seconds,
        "lane_concurrency": cfg.lane_concurrency,
        "auto_remediation_enabled": cfg.auto_remediation_enabled,
    }


# ── Async loop wrapper for gateway lifespan ────────────────────────────


async def run_sweeper_loop(stop_event: "Any") -> None:
    """Background-task body for the gateway's `lifespan` startup hook.

    Awaits `stop_event.wait()` with a timeout equal to the configured
    sweeper interval. On every interval expiry it calls `tick()` once.
    Exits cleanly when `stop_event` is set so graceful shutdown works.

    The loop is itself defensive: any exception inside `tick()` (which
    should be rare since `tick()` already contains its handlers) is
    caught and logged so the loop keeps running.
    """
    import asyncio

    sweeper = get_sweeper()
    # Floor at 0.01s rather than 1.0s so unit tests can drive the loop
    # at sub-second cadence; production always sets a real interval via
    # UA_MISSION_CONTROL_SWEEPER_INTERVAL_S (default 60s).
    interval = max(0.01, sweeper.config.interval_seconds)
    logger.info(
        "🛰️  Mission Control sweeper loop starting (interval=%.1fs, model_lane=glm-4.7)",
        interval,
    )
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            break
        try:
            result = sweeper.tick()
            if result.skipped_reason is None and (
                result.tier0_transitions or result.errors
            ):
                logger.info(
                    "🛰️  MC sweep: transitions=%s errors=%s",
                    result.tier0_transitions,
                    result.errors,
                )
        except Exception:
            logger.exception("Mission Control sweeper tick raised — loop continues")
    logger.info("🛰️  Mission Control sweeper loop stopped")
