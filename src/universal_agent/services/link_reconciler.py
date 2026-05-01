"""Link payments — non-terminal spend-request reconciler.

Background poller that catches up on spend requests whose status hasn't yet
landed in a terminal bucket (`approved`, `denied`, `expired`, `succeeded`,
`failed`). Use cases:

  - The original create call's `--request-approval` poll exited via
    `POLLING_TIMEOUT` while still pending. The user later approved in the
    Link app; without the reconciler the bridge never sees the transition.
  - A network blip mid-poll left the bridge in "unknown" state. The
    reconciler re-checks live status from Link.
  - `auth_status` failed mid-flight, the bridge fell back to error mode,
    auth has since been restored — the reconciler re-checks once.

The reconciler is intentionally **stateless** other than what the audit log
tells it: every tick reads the audit log to find non-terminal spend
requests, calls `link_bridge.retrieve_spend_request` for each, and lets the
notifier hook fire on transition to approved.

Designed to be called from a heartbeat / cron loop. Each tick is bounded:

  - Reads only the last `lookback_hours` of audit entries (default 48h).
  - De-duplicates by spend_request_id.
  - Stops after `max_per_tick` retrievals (default 10) to avoid hammering
    the CLI when there's a backlog.

The reconciler does NOT create new spend requests. It only observes.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Iterable

from universal_agent import feature_flags
from universal_agent.tools import link_bridge

logger = logging.getLogger(__name__)


_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"approved", "denied", "expired", "succeeded", "failed"}
)


def _is_truthy_env(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def reconciler_enabled(default: bool = True) -> bool:
    """Whether the reconciler should run. Default ON when Link is enabled."""
    if not feature_flags.link_enabled():
        return False
    if _is_truthy_env("UA_LINK_RECONCILER_DISABLED"):
        return False
    return default


def _read_audit_lines(*, lookback_seconds: int) -> Iterable[dict[str, Any]]:
    path = link_bridge.resolve_audit_path()
    if not path.exists():
        return []
    cutoff = time.time() - lookback_seconds
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = entry.get("ts")
                if isinstance(ts, (int, float)) and ts >= cutoff:
                    rows.append(entry)
    except OSError:
        return []
    return rows


def _candidate_ids_from_audit(
    lookback_hours: int,
) -> list[str]:
    """Find spend_request_ids from create_attempts + retrieve_attempts.

    Order: oldest-first, so backlog gets cleared in FIFO order.
    """
    seen: dict[str, float] = {}  # id -> first-seen ts
    last_status: dict[str, str | None] = {}

    rows = _read_audit_lines(lookback_seconds=lookback_hours * 3600)
    for entry in rows:
        sid = entry.get("spend_request_id")
        if not sid or not isinstance(sid, str):
            continue
        if sid not in seen:
            seen[sid] = entry.get("ts") or time.time()
        # Track the *last* status we observed in audit — note retrieve
        # entries don't always include status; we use absence of a terminal
        # status as a signal to keep watching.
        if entry.get("status") in _TERMINAL_STATUSES:
            last_status[sid] = entry.get("status")
        elif sid not in last_status:
            last_status[sid] = None

    # Keep only ids whose last observed status is NOT terminal.
    candidates = [
        (sid, ts)
        for sid, ts in seen.items()
        if last_status.get(sid) not in _TERMINAL_STATUSES
    ]
    candidates.sort(key=lambda pair: pair[1])
    return [sid for sid, _ in candidates]


def reconcile_once(
    *,
    lookback_hours: int = 48,
    max_per_tick: int = 10,
    caller: str = "ops",
) -> dict[str, Any]:
    """Run a single reconciliation pass. Returns a summary dict.

    Safe to call repeatedly. Never raises. When `link_enabled()` is False,
    short-circuits and returns `{"ran": False, "reason": "disabled"}`.
    """
    if not reconciler_enabled():
        return {"ran": False, "reason": "disabled"}

    started = time.time()
    candidates = _candidate_ids_from_audit(lookback_hours)
    if not candidates:
        return {
            "ran": True,
            "checked": 0,
            "transitioned_to_terminal": 0,
            "errors": 0,
            "duration_ms": int((time.time() - started) * 1000),
        }

    checked = 0
    transitioned = 0
    errors = 0

    for sid in candidates[:max_per_tick]:
        checked += 1
        try:
            result = link_bridge.retrieve_spend_request(
                caller=caller, spend_request_id=sid, include_card=False
            )
        except Exception as exc:  # pragma: no cover — defensive
            errors += 1
            logger.warning("Reconciler retrieve raised for %s: %s", sid, exc)
            continue

        if not result.get("ok"):
            errors += 1
            logger.info(
                "Reconciler retrieve failed for %s: %s",
                sid,
                (result.get("error") or {}).get("code"),
            )
            continue

        data = result.get("data") or {}
        status = data.get("status")
        if status in _TERMINAL_STATUSES:
            transitioned += 1
            logger.info(
                "Reconciler: spend request %s now %s.", sid, status
            )

    return {
        "ran": True,
        "checked": checked,
        "candidates": len(candidates),
        "transitioned_to_terminal": transitioned,
        "errors": errors,
        "duration_ms": int((time.time() - started) * 1000),
    }
