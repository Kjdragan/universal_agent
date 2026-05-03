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

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from universal_agent.services.mission_control_db import is_phase_enabled

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

    # ── Tier handlers (skeletons; Phase 1+ fills these in) ─────────────

    def _run_tier0(self, result: SweepResult) -> None:
        """Tier-0 watermark check. Phase 1 fills this in."""
        result.tier0_checked = True

    def _run_tier1(self, result: SweepResult) -> None:
        """Tier-1 LLM card-discovery pass. Phase 2 fills this in."""
        result.tier1_evaluated = True

    def _run_tier2(self, result: SweepResult) -> None:
        """Tier-2 page synthesis. Phase 3 fills this in."""
        result.tier2_evaluated = True


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
