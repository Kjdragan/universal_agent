"""Mission Control sweeper liveness invariant.

Phase B (#749) extracted the Mission Control sweeper into its own systemd
service (``universal-agent-mission-control-sweeper.service``). The is-active
service watchdog catches a *dead* (inactive) service, but not a
*wedged-but-alive* one — a process that is up yet no longer ticking. This probe
closes that gap using a **dual-signal** approach:

**Tier-0 tiles (primary liveness signal).** Tier-0 tiles
(``task_hub_pressure``, ``heartbeat_daemon``, ``gateway``) are written by
``_persist_tile_state`` on *every* sweep tick via ``_run_tier0``, regardless of
whether the LLM tier fires. Their ``last_checked_at`` advances unconditionally
and is therefore the true per-tick heartbeat.

**Tier-1 meta (secondary signal).** The ``__tier1_meta__`` row is written by
``_write_tier1_meta`` inside ``_run_tier1_async``, which only executes when
the sweeper reaches the LLM pass. During dormancy (10pm-6am), rate-limit floor
waits, or unchanged-signature periods, tier-1 legitimately idles and
``__tier1_meta__`` goes stale. Tier-1 staleness alone is **not** a liveness
signal — it merely indicates the LLM pass has not run recently.

The invariant only WARNs when **both** tier-0 tiles AND tier-1 meta are stale,
which means the entire sweeper loop has stopped ticking (genuinely wedged).
If tier-1 is stale but tier-0 tiles are fresh, the sweeper loop is alive and
tier-1 is legitimately idle — the invariant returns healthy (None).

IMPORTANT — reads ``last_checked_at``, NOT ``state_since``. ``state_since``
records the last actual card FIRE (synthesis), which is intentionally sparse on
an idle system (the S3 fix deliberately decoupled it from per-tick attempts);
using it for liveness would false-alarm on every quiet period. Only
``last_checked_at`` advances on every tick and is therefore the true liveness
signal.

Severity is WARN (not critical) on purpose: a wedged sweeper is worth surfacing
in the snapshot / dashboard / heartbeat prompt, but it is not an inbox-paging
emergency — and the proactive-health digest only emails on *criticals*, so this
probe can never become email spam (the false-Mission-Control-alarm failure mode
this codebase has hit repeatedly). It is gated to the sweeper's rollout phase:
when ``UA_MC_PHASE_1_ENABLED`` is off the sweeper is intentionally idle, so the
probe is a no-op.

Added 2026-06-05 as the Phase B follow-up flagged in the S5 Phase C work
(deploy-independent proactive-health timer). Dual-signal fix (2026-06-13):
tier-0 tiles as primary liveness, tier-1 meta as secondary, eliminating false
WARNs during dormancy / rate-limit floor waits / unchanged-signature periods.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
import sqlite3
from typing import Any, Dict, Optional

from universal_agent.services.pipeline_invariants import invariant

logger = logging.getLogger(__name__)

# The sweeper interval default is 60s. Warn when the per-tick heartbeat is stale
# beyond ~5x cadence — generous enough to ride through the ~19 daily deploy
# restarts (Restart=always, RestartSec=5 + first-tick latency) without a false
# WARN, while still catching a genuine wedge within a few minutes. A real wedge
# stays stale indefinitely, so a looser threshold costs nothing for true
# detection but materially cuts false positives.
_DEFAULT_MAX_STALE_SECONDS = 300

# Tier-0 tiles that are written by _persist_tile_state on every sweep tick via
# _run_tier0. Their last_checked_at is the true per-tick liveness signal.
_TIER0_LIVENESS_TILES = frozenset({
    "task_hub_pressure",
    "heartbeat_daemon",
    "gateway",
})


def _max_stale_seconds() -> int:
    raw = os.getenv("UA_MC_SWEEPER_LIVENESS_MAX_STALE_SECONDS")
    if not raw:
        return _DEFAULT_MAX_STALE_SECONDS
    try:
        return max(120, int(raw))
    except ValueError:
        return _DEFAULT_MAX_STALE_SECONDS


def _parse_iso(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _any_tier0_tile_fresh(conn: sqlite3.Connection, max_stale: int) -> bool:
    """Return True if any tier-0 tile's last_checked_at is within *max_stale* seconds of now.

    Returns True (fail-open) when no tier-0 tile rows exist -- we cannot confirm
    the sweeper loop is dead without tier-0 data.
    """
    placeholders = ",".join("?" for _ in _TIER0_LIVENESS_TILES)
    row = conn.execute(
        f"SELECT MAX(last_checked_at) AS max_checked "
        f"FROM mission_control_tile_states "
        f"WHERE tile_id IN ({placeholders})",
        tuple(_TIER0_LIVENESS_TILES),
    ).fetchone()
    if row is None or row["max_checked"] is None:
        # No tier-0 tile rows at all — cannot confirm the sweeper loop is dead
        # via tier-0. Fail open (could be a fresh deploy or phase-1-only).
        return True
    max_checked = _parse_iso(row["max_checked"])
    if max_checked is None:
        return False
    age = (datetime.now(timezone.utc) - max_checked).total_seconds()
    return age <= max_stale


@invariant(
    id="mission_control_sweeper_liveness",
    title="Mission Control sweeper liveness",
    description=(
        "Both tier-0 tiles AND tier-1 meta are stale beyond the allowed "
        "threshold — the sweeper loop appears to have stopped ticking "
        "(wedged). Tier-0 staleness alone confirms the loop is dead; tier-1 "
        "meta can legitimately idle during dormancy or rate-limit waits."
    ),
    severity="warn",
    runbook_command=(
        "systemctl status universal-agent-mission-control-sweeper.service; "
        "journalctl -u universal-agent-mission-control-sweeper.service -n 80 --no-pager"
    ),
    metadata={
        "context_key": "mission_control_intel_db",
        "design_note": (
            "Dual-signal fix (2026-06-13): tier-0 tiles (task_hub_pressure, "
            "heartbeat_daemon, gateway) are written by _persist_tile_state on "
            "every sweep tick and are the true liveness signal. Tier-1 meta "
            "(__tier1_meta__) is only written when the LLM pass fires, so it "
            "legitimately idles during dormancy / rate-limit waits / "
            "unchanged-signature periods. The invariant only WARNs when BOTH "
            "are stale — a genuinely wedged sweeper. Reads last_checked_at "
            "(per-tick) NOT state_since (last card FIRE, sparse on idle — "
            "the S3 trap). WARN-only so it never pages."
        ),
    },
)
def mission_control_sweeper_liveness(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Warn when both tier-0 tiles and tier-1 meta are stale.

    Fails open (returns None) when phase 1 is disabled (sweeper intentionally
    idle), when the DB / table / row is absent (fresh deploy or phase 2 not
    enabled — the service-down case is the is-active watchdog's job), or on
    any query error.
    """
    try:
        from universal_agent.services.mission_control_db import (
            default_db_path,
            is_phase_enabled,
            open_store,
        )
    except Exception:  # noqa: BLE001
        return None

    # When the sweeper rollout phase is off, the loop stays idle by design and
    # never ticks — monitoring it would be a perpetual false WARN.
    if not is_phase_enabled(1):
        return None

    try:
        db_path = default_db_path()
    except Exception:  # noqa: BLE001
        return None
    if not db_path.exists():
        # Fresh DB / sweeper never wrote — fail open (service-down belongs to
        # the is-active watchdog, not this probe).
        return None

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = open_store(db_path)
        row = conn.execute(
            "SELECT last_checked_at FROM mission_control_tile_states "
            "WHERE tile_id = ?",
            ("__tier1_meta__",),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        logger.debug("mc_sweeper_liveness: table unavailable (%s)", exc)
        return None
    except sqlite3.Error as exc:
        logger.warning("mc_sweeper_liveness: query failed: %s", exc, exc_info=True)
        return None

    if row is None:
        # No __tier1_meta__ row yet — could be a fresh deploy or phase 2 not
        # enabled. Fail open; the is-active watchdog owns the service-down case.
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        return None

    last_checked = _parse_iso(row["last_checked_at"])
    if last_checked is None:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        return None

    now = datetime.now(timezone.utc)
    stale_seconds = (now - last_checked).total_seconds()
    max_stale = _max_stale_seconds()
    if stale_seconds <= max_stale:
        # Tier-1 meta is fresh — definitely healthy.
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        return None

    # Tier-1 meta IS stale — but is the sweeper loop actually dead?
    # Check tier-0 tiles: if any is fresh, the loop is alive and tier-1 is
    # legitimately idle (dormancy, rate-limit wait, unchanged signature).
    if _any_tier0_tile_fresh(conn, max_stale):
        logger.debug(
            "mc_sweeper_liveness: tier-1 meta stale (%.0fs) but tier-0 tiles "
            "fresh — sweeper loop alive, tier-1 legitimately idle",
            stale_seconds,
        )
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        return None

    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass

    return {
        "observed_value": {
            "last_checked_at": last_checked.isoformat(),
            "stale_seconds": round(stale_seconds, 1),
            "max_stale_seconds": max_stale,
        },
        "message": (
            f"Both tier-0 tiles AND tier-1 meta are stale "
            f"({round(stale_seconds)}s > {max_stale}s threshold). "
            f"The Mission Control sweeper loop appears wedged — alive but "
            f"not ticking. Inspect the service + journal."
        ),
        "threshold_text": f"<= {max_stale}s since last tick (tier-0 or tier-1)",
    }
