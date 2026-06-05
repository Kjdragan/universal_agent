"""Mission Control sweeper liveness invariant.

Phase B (#749) extracted the Mission Control sweeper into its own systemd
service (``universal-agent-mission-control-sweeper.service``). The is-active
service watchdog catches a *dead* (inactive) service, but not a
*wedged-but-alive* one — a process that is up yet no longer ticking. This probe
closes that gap: it reads the sweeper's per-tick heartbeat (``last_checked_at``
on the ``__tier1_meta__`` row of ``mission_control_intelligence.db``, which
``mission_control_intelligence_sweeper._write_tier1_meta`` always-writes every
tick — success, skip, OR error) and warns when it is stale beyond a threshold.

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
(deploy-independent proactive-health timer).
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


@invariant(
    id="mission_control_sweeper_liveness",
    title="Mission Control sweeper liveness",
    description=(
        "The Mission Control sweeper's per-tick heartbeat (last_checked_at on "
        "the __tier1_meta__ row) is stale beyond the allowed threshold — the "
        "service is alive but appears to have stopped ticking (wedged)."
    ),
    severity="warn",
    runbook_command=(
        "systemctl status universal-agent-mission-control-sweeper.service; "
        "journalctl -u universal-agent-mission-control-sweeper.service -n 80 --no-pager"
    ),
    metadata={
        "context_key": "mission_control_intel_db",
        "design_note": (
            "Phase B follow-up (2026-06-05): the is-active watchdog catches a "
            "dead sweeper; this catches a wedged-but-alive one. Reads "
            "last_checked_at (per-tick, always-written) NOT state_since (last "
            "card FIRE, sparse on idle — the S3 trap). WARN-only so it never "
            "pages: the proactive-health digest emails on criticals only."
        ),
    },
)
def mission_control_sweeper_liveness(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Warn when the sweeper's ``__tier1_meta__`` heartbeat is stale.

    Fails open (returns None) when phase 1 is disabled (sweeper intentionally
    idle), when the DB / table / row is absent (fresh deploy — the service-down
    case is the is-active watchdog's job), or on any query error.
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
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    if row is None:
        return None
    last_checked = _parse_iso(row["last_checked_at"])
    if last_checked is None:
        return None

    now = datetime.now(timezone.utc)
    stale_seconds = (now - last_checked).total_seconds()
    max_stale = _max_stale_seconds()
    if stale_seconds <= max_stale:
        return None

    return {
        "observed_value": {
            "last_checked_at": last_checked.isoformat(),
            "stale_seconds": round(stale_seconds, 1),
            "max_stale_seconds": max_stale,
        },
        "message": (
            f"Mission Control sweeper heartbeat stale {round(stale_seconds)}s "
            f"(threshold {max_stale}s). The service may be wedged — alive but "
            f"not ticking. Inspect the service + journal."
        ),
        "threshold_text": f"<= {max_stale}s since last __tier1_meta__ tick",
    }
