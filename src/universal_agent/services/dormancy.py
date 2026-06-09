"""Single source of truth for the Houston active-hours dormancy window.

Several content-generation crons and operator-alert paths share one
policy: do work between 6 AM and 10 PM Houston (America/Chicago) local
time, and stay quiet ("dormant") overnight from 10 PM to 6 AM. Before
this module existed the same check was reimplemented in at least three
places (``cron_artifact_reminders._within_active_window``,
``gateway_server._csi_incident_in_waking_window`` and inline hour
comparisons in ``proactive_pipeline_invariants``), each with slightly
different error handling. This module consolidates the canonical
definition so every caller agrees on the boundary and the
permissive-on-error contract.

Canonical window
----------------
active  := 6 <= Houston-local-hour < 22   (i.e. 06:00 inclusive .. 22:00 exclusive)
dormant := everything else                (22:00 .. 06:00 the next day)

Note that ``hour < 22`` is identical to ``hour <= 21`` for integer hours,
so call sites that previously wrote ``6 <= now.hour <= 21`` migrate to
this module with no behavioral change.

Permissive-on-error contract
----------------------------
If the timezone database is unavailable (missing ``tzdata`` on a bare
box, ``ZoneInfo`` lookup failure, an unparseable input, etc.),
:func:`is_active_window` returns ``True`` — it treats every hour as
active. This is deliberate: the gated work is email/alert delivery, and
we would rather over-deliver than silently drop an operator alert on a
misconfigured host. This matches the historical fallback in
``cron_artifact_reminders._within_active_window``.

Opt-in declared-mode model
---------------------------
Dormancy is *opt-in* per process. A new process is dormancy-bound only
if it declares so. :func:`should_run` is the gate: ``mode="always"`` (the
default) never sleeps, while ``mode="dormancy_aware"`` defers to the
active window. An optional environment variable can override the
declared mode at runtime so operators can flip a process between the two
without code changes.

This module is stdlib-only (``datetime`` + ``zoneinfo``) and has no
universal_agent imports, so it is safe to import from anywhere.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Mapping, Optional, Union

__all__ = [
    "HOUSTON_TZ",
    "ACTIVE_START_HOUR",
    "ACTIVE_END_HOUR",
    "is_active_window",
    "is_dormant",
    "should_run",
    "active_window_desc",
    "cron_hour_field",
    "systemd_hour_range",
]

# IANA name for "Houston" local time. America/Chicago carries the
# CST/CDT (UTC-6 / UTC-5) DST rules, so per-tick conversion handles DST
# transitions correctly.
HOUSTON_TZ = "America/Chicago"

# Active window bounds, in Houston-local hours.
#   ACTIVE_START_HOUR is inclusive (06:00 is active).
#   ACTIVE_END_HOUR is exclusive (22:00 is dormant).
ACTIVE_START_HOUR = 6
ACTIVE_END_HOUR = 22  # 10 PM

# Truthy spellings accepted for the should_run() env override; any other
# value (falsy or unrecognized) maps to the non-dormancy "always" mode.
_TRUTHY = frozenset({"1", "true", "yes", "on"})

# Accepted "now" inputs: None (use current time), a UNIX epoch (int/float
# seconds), or a datetime (tz-aware or naive).
NowType = Union[None, int, float, datetime]


def _to_houston_hour(now: NowType) -> int:
    """Resolve ``now`` to the Houston-local hour (0-23).

    ``now`` may be ``None`` (current time), a UNIX epoch in seconds, or a
    :class:`datetime`. Naive datetimes are assumed to already be in UTC
    (consistent with epoch handling), tz-aware datetimes are converted
    from their own zone. Raises on any failure; callers in this module
    translate that into the permissive fallback.
    """
    from zoneinfo import ZoneInfo

    houston = ZoneInfo(HOUSTON_TZ)

    if now is None:
        moment = datetime.now(tz=timezone.utc)
    elif isinstance(now, datetime):
        if now.tzinfo is None:
            # Naive datetime: treat as UTC, mirroring epoch handling so a
            # bare datetime.utcnow() does not silently shift the window.
            moment = now.replace(tzinfo=timezone.utc)
        else:
            moment = now
    else:
        # int / float epoch seconds.
        moment = datetime.fromtimestamp(float(now), tz=timezone.utc)

    return moment.astimezone(houston).hour


def is_active_window(now: NowType = None) -> bool:
    """Return ``True`` when ``now`` falls within the Houston active window.

    The active window is ``ACTIVE_START_HOUR <= houston_hour < ACTIVE_END_HOUR``
    (06:00 inclusive .. 22:00 exclusive).

    ``now`` may be:
      * ``None``  — use the current wall-clock time (default),
      * an ``int``/``float`` — a UNIX epoch in seconds (UTC),
      * a ``datetime`` — tz-aware (converted from its zone) or naive
        (assumed UTC).

    PERMISSIVE-ON-ERROR: if the timezone lookup or any conversion fails,
    this returns ``True`` (every hour treated as active) so that gated
    work — operator email/alert delivery — is never silently dropped on
    a misconfigured host.
    """
    try:
        hour = _to_houston_hour(now)
        return ACTIVE_START_HOUR <= hour < ACTIVE_END_HOUR
    except Exception:  # noqa: BLE001
        # zoneinfo/tzdata unavailable or an unparseable input: default to
        # permissive so reminders/alerts aren't lost.
        return True


def is_dormant(now: NowType = None) -> bool:
    """Return ``True`` when ``now`` is outside the active window.

    Strict inverse of :func:`is_active_window`. Note that because
    ``is_active_window`` is permissive on error (returns ``True``),
    ``is_dormant`` is correspondingly *non*-dormant on error (returns
    ``False``) — work proceeds rather than being skipped.
    """
    return not is_active_window(now)


def should_run(
    mode: str = "always",
    *,
    env_var: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    now: NowType = None,
) -> bool:
    """Per-process gate for the opt-in dormancy model.

    Parameters
    ----------
    mode:
        Declared dormancy mode for the calling process.
          * ``"always"`` (default) — always run; the process is not
            dormancy-bound. This is the opt-in design: a process only
            observes dormancy if it explicitly declares
            ``"dormancy_aware"``.
          * ``"dormancy_aware"`` — run only inside the active window,
            i.e. defer to :func:`is_active_window` for ``now``.
        Any unrecognized value is treated as ``"always"`` (fail-open).
    env_var:
        Optional environment variable name. If provided AND the variable
        is present in ``env``, it OVERRIDES ``mode``:
          * a truthy value (one of ``{"1","true","yes","on"}``,
            case-insensitive) forces ``"dormancy_aware"``,
          * any other value (falsy or unrecognized) forces ``"always"``.
        If ``env_var`` is ``None`` or absent from ``env``, ``mode`` is
        used unchanged.
    env:
        Mapping to read ``env_var`` from. Defaults to ``os.environ``.
    now:
        Passed through to :func:`is_active_window` when the effective
        mode is dormancy-aware.

    Returns
    -------
    bool
        ``True`` if the process should run now, ``False`` if it should
        stay dormant.
    """
    effective_mode = mode

    if env_var is not None:
        source = os.environ if env is None else env
        raw = source.get(env_var)
        if raw is not None:
            effective_mode = (
                "dormancy_aware"
                if raw.strip().lower() in _TRUTHY
                else "always"
            )

    if effective_mode == "dormancy_aware":
        return is_active_window(now)

    # "always" (and any unrecognized mode) — never dormancy-bound.
    return True


def active_window_desc() -> str:
    """Human-readable description of the active window and its dormancy span."""
    return (
        f"{ACTIVE_START_HOUR:02d}:00-{ACTIVE_END_HOUR:02d}:00 {HOUSTON_TZ} "
        f"(dormant {ACTIVE_END_HOUR:02d}:00-{ACTIVE_START_HOUR:02d}:00)"
    )


def cron_hour_field() -> str:
    """The active window as a 5-field-cron hour range, e.g. ``"6-21"``.

    Derived from :data:`ACTIVE_START_HOUR` / :data:`ACTIVE_END_HOUR` so that
    windowed cron registrations build their schedule as
    ``f"0 {cron_hour_field()} * * *"`` instead of hardcoding ``6-21`` in each
    call site. ``ACTIVE_END_HOUR`` is exclusive, so the last firing hour is
    ``ACTIVE_END_HOUR - 1``.
    """
    return f"{ACTIVE_START_HOUR}-{ACTIVE_END_HOUR - 1}"


def systemd_hour_range() -> str:
    """The active window as a systemd ``OnCalendar`` hour range, e.g. ``"06..21"``.

    The same window as :func:`cron_hour_field`, in systemd's zero-padded
    ``H..H`` notation used by ``deployment/systemd/*.timer`` OnCalendar specs
    (``*-*-* 06..21:00:00 America/Chicago``). Those unit files are static text
    that only takes effect on reinstall, so they cannot literally import this
    helper; the companion drift-guard test
    (``tests/unit/test_dormancy_schedule_consistency.py``) pins their hour
    range to this value so they cannot silently diverge from the constants.
    """
    return f"{ACTIVE_START_HOUR:02d}..{ACTIVE_END_HOUR - 1:02d}"
