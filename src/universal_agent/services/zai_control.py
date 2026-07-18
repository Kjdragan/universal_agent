"""ZAI emergency control plane — a live, fail-open control file.

The operator-facing control surface for ZAI inference pressure. A single JSON
file in ``AGENT_RUN_WORKSPACES`` (outside the git tree, so it **survives
deploys**) carries an emergency-lever state that the enforcement points read
LIVE:

- ``rate_limiter.py::ZAIRateLimiter.acquire`` — honors a global pause, per-tier
  pause, and per-tier cap overrides (routed callers).
- ``zai_observability.py::_on_request_*`` — aborts every ``api.z.ai`` request
  while a global pause is active (ALL callers, the 100% chokepoint).
- ``zai_inference_health.py`` — surfaces the control state (does not alarm on an
  intentional operator pause), and alerts CRITICAL on a fresh
  ``weekly_exhaustion`` stamp (see ``handle_weekly_exhaustion`` below).
- the gateway ``/api/v1/ops/zai/*`` endpoints read/write it.

``handle_weekly_exhaustion`` (2026-07-18) is the auto-trip entry point for
ZAI's weekly/monthly quota wall (error code 1310): callers in
``rate_limiter.py::with_rate_limit_retry`` and
``zai_observability.py::_capture`` invoke it on detection, and it applies a
pause-only global pause (``set_global_pause`` — NOT the L4 tier-override
preset) with a TTL parsed from the reset timestamp in the error body.

**The one rule that makes this safe to deploy without live testing: every read
FAILS OPEN.** A missing / corrupt / unreadable / permission-denied control file
yields the empty state — env-default caps, no pause — never "paused" and never a
raise. So the worst case of this module misbehaving is "the emergency controls
silently don't engage," never "the stack is bricked." The control file is also
NOT on the critical restore path: the system runs normally without it.

Reads are cached in-process for ``_CACHE_TTL_SECONDS`` so a burst of httpx
requests / acquires doesn't stat-storm the disk; a control change takes effect
within that window (≤2s by default).
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

CONTROL_VERSION = 1
_CACHE_TTL_SECONDS = 2.0

# Mirror rate_limiter.TIERS without importing it (avoid an import cycle: the
# limiter imports this module). Kept in sync by a unit test.
TIERS = ("opus", "sonnet", "mid", "haiku")

# The lever ladder, lowest → greatest. L0..L4 are control-file states the
# dashboard sets; L5 (full dark — stop systemd services) is out-of-band and is
# only described, never written here. Each preset is the FULL control state for
# that level (applied wholesale), so moving between levels is deterministic.
LEVELS: dict[int, dict[str, Any]] = {
    0: {  # Normal — env-default caps, no pause.
        "tier_overrides": {},
        "tier_pause": {},
        "global_pause": {"active": False},
    },
    1: {  # Trim — halve the hot tiers.
        "tier_overrides": {
            "opus": {"cap": 1, "max": 1},
            "sonnet": {"cap": 1, "max": 2},
            "mid": {"cap": 1, "max": 2},
            "haiku": {"cap": 1, "max": 2},
        },
        "tier_pause": {},
        "global_pause": {"active": False},
    },
    2: {  # Minimal — serialize every tier.
        "tier_overrides": {t: {"cap": 1, "max": 1} for t in TIERS},
        "tier_pause": {},
        "global_pause": {"active": False},
    },
    3: {  # Cheap-only — serialize, and hard-stop the expensive tiers.
        "tier_overrides": {t: {"cap": 1, "max": 1} for t in TIERS},
        "tier_pause": {"opus": True, "mid": True},
        "global_pause": {"active": False},
    },
    4: {  # Global pause — abort ALL ZAI at the httpx hook (TTL-defaulted).
        "tier_overrides": {t: {"cap": 1, "max": 1} for t in TIERS},
        "tier_pause": {"opus": True, "mid": True},
        "global_pause": {"active": True},
    },
}

# Default TTL applied to a level-4 global pause when the caller gives none, so a
# forgotten pause self-heals rather than starving the system forever. Parsed
# through a guard so a malformed env value can never raise at import time (the
# module's whole contract is "never raise"); mirrors rate_limiter._tier_env_int.
def _default_ttl() -> float:
    try:
        return max(0.0, float(os.getenv("UA_ZAI_GLOBAL_PAUSE_DEFAULT_TTL_SECONDS", "1800")))
    except (TypeError, ValueError):
        return 1800.0


DEFAULT_GLOBAL_PAUSE_TTL_SECONDS = _default_ttl()


def control_path() -> Path:
    """Where the control file lives. Mirrors ``rate_limiter._get_state_path``
    so it sits in ``AGENT_RUN_WORKSPACES`` (preserved across deploys)."""
    env = os.getenv("UA_ZAI_CONTROL_PATH")
    if env:
        return Path(env)
    # services/zai_control.py -> repo root is parents[3].
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "AGENT_RUN_WORKSPACES" / "zai_inference_control.json"


# In-process read cache: (path-mtime-ns, read-at-monotonic, data).
_cache: tuple[int, float, dict[str, Any]] | None = None


def _now() -> float:
    return time.time()


def read_control(*, use_cache: bool = True) -> dict[str, Any]:
    """Return the control dict. FAILS OPEN to ``{}`` on ANY error. Never raises.

    Cached for ``_CACHE_TTL_SECONDS`` keyed on file mtime so a control change is
    picked up promptly without a per-call disk read storm.
    """
    global _cache
    path = control_path()
    try:
        if use_cache and _cache is not None:
            _, read_at, data = _cache
            if (time.monotonic() - read_at) < _CACHE_TTL_SECONDS:
                return data
        if not path.exists():
            _cache = (0, time.monotonic(), {})
            return {}
        raw = path.read_text()
        data = json.loads(raw)
        if not isinstance(data, dict):
            data = {}
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            mtime = 0
        _cache = (mtime, time.monotonic(), data)
        return data
    except Exception as exc:  # noqa: BLE001 — FAIL OPEN, never raise
        logger.debug("zai_control read failed (failing open): %s", exc)
        return {}


def _invalidate_cache() -> None:
    global _cache
    _cache = None


def write_control(data: dict[str, Any]) -> bool:
    """Atomically write the control file (tmp + os.replace). Never raises;
    returns False on failure. Stamps ``version`` and ``updated_at``."""
    path = control_path()
    payload = dict(data)
    payload["version"] = CONTROL_VERSION
    payload["updated_at"] = _now()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        os.replace(tmp, path)
        _invalidate_cache()
        return True
    except Exception:  # noqa: BLE001 — never crash a caller over a control write
        logger.warning("zai_control write failed", exc_info=True)
        return False


def is_globally_paused(now: Optional[float] = None) -> tuple[bool, dict[str, Any]]:
    """(paused, pause_info). Active iff ``global_pause.active`` and not past
    ``until`` (TTL). Fails open to (False, {})."""
    data = read_control()
    gp = data.get("global_pause")
    if not isinstance(gp, dict) or not gp.get("active"):
        return False, {}
    until = gp.get("until")
    now = now if now is not None else _now()
    if until is not None:
        try:
            if float(until) <= now:
                return False, gp  # expired — auto-clears
        except (TypeError, ValueError):
            pass  # unparseable until → treat as no-expiry, still paused
    return True, gp


def is_tier_paused(tier: str, now: Optional[float] = None) -> bool:
    """True iff this tier is hard-stopped via ``tier_pause``. Fails open False."""
    data = read_control()
    tp = data.get("tier_pause")
    return isinstance(tp, dict) and bool(tp.get(tier))


def tier_cap_override(tier: str) -> Optional[dict[str, int]]:
    """Return ``{cap?, max?}`` for a tier if an override is set, else None.
    Values are coerced to ints ≥ 1; a malformed entry yields None (fail open)."""
    data = read_control()
    overrides = data.get("tier_overrides")
    if not isinstance(overrides, dict):
        return None
    entry = overrides.get(tier)
    if not isinstance(entry, dict):
        return None
    out: dict[str, int] = {}
    for key in ("cap", "max"):
        if key in entry:
            try:
                out[key] = max(1, int(entry[key]))
            except (TypeError, ValueError):
                continue
    return out or None


def effective_tier_cap(tier: str, ai_cap: int, tier_max: int) -> int:
    """Resolve the cap a tier gate should enforce RIGHT NOW.

    Operator intent wins over the autotuner: if a control override sets ``cap``,
    that is the effective cap (clamped to [1, override.max or tier_max]).
    Otherwise the AIMD-managed ``ai_cap`` stands. Fails open to ``ai_cap``.
    """
    override = tier_cap_override(tier)
    if not override or "cap" not in override:
        return max(1, ai_cap)
    ceiling = override.get("max", tier_max)
    try:
        ceiling = max(1, int(ceiling))
    except (TypeError, ValueError):
        ceiling = tier_max
    return max(1, min(int(override["cap"]), ceiling))


def current_state() -> dict[str, Any]:
    """A normalized snapshot of the control state for the status endpoint /
    dashboard. Always returns a complete shape (fail-open defaults)."""
    data = read_control()
    paused, gp = is_globally_paused()
    return {
        "intervention_level": int(data.get("intervention_level") or 0),
        "global_pause_active": paused,
        "global_pause": gp if paused else (data.get("global_pause") or {"active": False}),
        "tier_pause": {t: is_tier_paused(t) for t in TIERS},
        "tier_overrides": {
            t: tier_cap_override(t) for t in TIERS if tier_cap_override(t)
        },
        "updated_at": data.get("updated_at"),
        "updated_by": data.get("updated_by"),
    }


# ── Writers (used by the gateway control endpoint) ──────────────────────────

def apply_level(level: int, *, ttl_seconds: Optional[float] = None,
                reason: str = "", by: str = "dashboard") -> dict[str, Any]:
    """Write the full preset for an intervention level (0..4). Returns the
    written control dict. Unknown level → no-op, returns current state."""
    if level not in LEVELS:
        logger.warning("zai_control: unknown intervention level %r — ignored", level)
        return read_control()
    preset = json.loads(json.dumps(LEVELS[level]))  # deep copy
    gp = preset.get("global_pause") or {"active": False}
    if gp.get("active"):
        ttl = ttl_seconds if ttl_seconds is not None else DEFAULT_GLOBAL_PAUSE_TTL_SECONDS
        gp["until"] = (_now() + float(ttl)) if ttl and ttl > 0 else None
        gp["reason"] = reason or f"intervention level {level}"
        gp["set_at"] = _now()
    preset["global_pause"] = gp
    preset["intervention_level"] = level
    preset["updated_by"] = by
    write_control(preset)
    return read_control(use_cache=False)


# ── ZAI 1310 weekly/monthly quota-exhaustion auto-pause ────────────────────
#
# ZAI's account-level weekly/monthly quota wall (error code 1310) carries a
# reset timestamp in the body, Beijing-local (Asia/Shanghai, fixed UTC+8, no
# DST): "...Your limit will reset at 2026-07-19 00:54:25...". This section
# parses that timestamp and auto-trips a pause-only global pause (NOT the L4
# tier-override preset) with a TTL that expires ~2 minutes after the reset,
# so the pause self-clears exactly when the wall comes down instead of
# requiring an operator to notice and clear it by hand.

_RESET_TS_RE = re.compile(r"reset at (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

# Safety margin added past the parsed reset instant — ZAI's own clock/quota
# refresh may lag the wall-clock instant by a few seconds.
_RESET_MARGIN_SECONDS = 120.0


def _weekly_exhaustion_fallback_ttl() -> float:
    """TTL used when the reset timestamp can't be parsed (or already lies in
    the past). Deliberately NOT the 30-min ``DEFAULT_GLOBAL_PAUSE_TTL_SECONDS``
    — a weekly wall lasts days, so a short fallback would silently unpause
    ZAI while the account is still hard-blocked. Default 6h re-arms detection
    (the next 1310 re-trips the pause) if the fallback undershoots the real
    reset. Parsed through a guard per the module's "never raise" contract."""
    try:
        return max(0.0, float(os.getenv("UA_ZAI_1310_FALLBACK_TTL_SECONDS", "21600")))
    except (TypeError, ValueError):
        return 21600.0


def _parse_weekly_reset_epoch(error_text: str) -> Optional[float]:
    """Parse the Beijing-local reset timestamp out of a 1310 error body into
    epoch seconds. Returns ``None`` on any parse failure — never raises."""
    try:
        match = _RESET_TS_RE.search(error_text or "")
        if not match:
            return None
        naive = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
        aware = naive.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        return aware.timestamp()
    except Exception:  # noqa: BLE001 — parse failure: caller falls back to the fixed TTL
        return None


def handle_weekly_exhaustion(error_text: str, *, source: str) -> Optional[dict[str, Any]]:
    """Handle a ZAI weekly/monthly quota-exhaustion signal (error code 1310).

    Auto-trips a **pause-only** global pause (``set_global_pause(True, ...)``
    — deliberately NOT the L4 preset / ``apply_level(4, ...)``) with a TTL
    parsed from the reset timestamp embedded in ``error_text`` (+ a safety
    margin), falling back to ``UA_ZAI_1310_FALLBACK_TTL_SECONDS`` (default
    21600s / 6h) when the timestamp can't be parsed or already lies in the
    past. Pause-only is deliberate: during a global pause every tier's caps
    are irrelevant (nothing is dispatched at all), and only
    ``global_pause.until`` carries a TTL — the L4 preset's
    ``tier_overrides``/``tier_pause`` have no expiry, so applying it here
    would leave opus/mid permanently hard-stopped after the global pause
    self-clears at the reset.

    Idempotent: if the global pause is already active with a reason
    containing ``"zai_1310"``, this is a no-op that returns the current
    state — avoids rewriting/extending the pause when many concurrent
    callers observe the same 1310 wall.

    Also stamps a ``weekly_exhaustion`` marker (``last_seen_at`` /
    ``reset_at_epoch`` / ``source``) into the control file so
    ``zai_inference_health`` (services/invariants/zai_inference_health.py)
    can alert on it. When the reset timestamp couldn't be parsed (or lay in
    the past), ``reset_at_epoch`` is stamped as ``now + ttl`` (the fallback
    TTL) rather than ``None`` — the invariant's freshness condition requires
    a reset time in the future to fire, so a ``None`` stamp would silently
    never alert on an unparseable-body trip.

    Fail-open per the module-wide invariant: NEVER raises. Returns the
    resulting control state dict, or ``None`` on any internal failure.
    """
    try:
        already_paused, gp = is_globally_paused()
        if already_paused and "zai_1310" in str(gp.get("reason") or ""):
            return current_state()

        now = _now()
        reset_epoch = _parse_weekly_reset_epoch(error_text)
        ttl = None
        if reset_epoch is not None:
            ttl = (reset_epoch + _RESET_MARGIN_SECONDS) - now
        if not ttl or ttl <= 0:
            ttl = _weekly_exhaustion_fallback_ttl()
            reset_display = "unparsed"
            # Stamp a future reset so the fresh-AND-future-reset alerting
            # condition in zai_inference_health can still fire — a bare
            # `None` would leave the invariant permanently unable to detect
            # this trip (see docstring above).
            reset_epoch = now + ttl
        else:
            reset_display = datetime.fromtimestamp(
                reset_epoch, tz=ZoneInfo("UTC")
            ).isoformat()

        set_global_pause(
            True,
            ttl_seconds=ttl,
            reason=f"zai_1310_weekly_limit_exhausted (reset {reset_display})",
            by="auto_1310_detector",
        )

        # Merge the weekly_exhaustion stamp on top of the just-written pause
        # (set_global_pause rewrites the whole control dict, so this must be
        # a second write) — read/mutate/write, same pattern as the other
        # merge-writers in this module (set_tier_overrides / set_tier_pause).
        data = read_control()
        data["weekly_exhaustion"] = {
            "last_seen_at": now,
            "reset_at_epoch": reset_epoch,
            "source": source,
        }
        write_control(data)
        return read_control(use_cache=False)
    except Exception:  # noqa: BLE001 — fail-open, never raise (module invariant)
        logger.warning("zai_control: handle_weekly_exhaustion failed", exc_info=True)
        return None


def set_global_pause(active: bool, *, ttl_seconds: Optional[float] = None,
                     reason: str = "", by: str = "dashboard") -> dict[str, Any]:
    """Toggle the global pause without touching tier caps/pauses."""
    data = read_control()
    if active:
        ttl = ttl_seconds if ttl_seconds is not None else DEFAULT_GLOBAL_PAUSE_TTL_SECONDS
        data["global_pause"] = {
            "active": True,
            "until": (_now() + float(ttl)) if ttl and ttl > 0 else None,
            "reason": reason or "manual global pause",
            "set_at": _now(),
        }
    else:
        data["global_pause"] = {"active": False}
    data["updated_by"] = by
    write_control(data)
    return read_control(use_cache=False)


def set_tier_overrides(overrides: dict[str, dict[str, int]], *,
                       by: str = "dashboard") -> dict[str, Any]:
    """Merge per-tier cap overrides (validated; only known tiers, caps ≥ 1).
    An empty/None entry for a tier clears that tier's override."""
    data = read_control()
    current = data.get("tier_overrides")
    if not isinstance(current, dict):
        current = {}
    for tier, entry in (overrides or {}).items():
        if tier not in TIERS:
            continue
        if not entry:
            current.pop(tier, None)
            continue
        clean: dict[str, int] = {}
        for key in ("cap", "max"):
            if key in entry:
                try:
                    clean[key] = max(1, int(entry[key]))
                except (TypeError, ValueError):
                    continue
        if clean:
            current[tier] = clean
    data["tier_overrides"] = current
    data["updated_by"] = by
    write_control(data)
    return read_control(use_cache=False)


def set_tier_pause(tiers: dict[str, bool], *, by: str = "dashboard") -> dict[str, Any]:
    """Merge per-tier hard-stop flags (only known tiers)."""
    data = read_control()
    current = data.get("tier_pause")
    if not isinstance(current, dict):
        current = {}
    for tier, on in (tiers or {}).items():
        if tier not in TIERS:
            continue
        if on:
            current[tier] = True
        else:
            current.pop(tier, None)
    data["tier_pause"] = current
    data["updated_by"] = by
    write_control(data)
    return read_control(use_cache=False)


def clear_all(*, by: str = "dashboard") -> dict[str, Any]:
    """Reset to normal operation (== level 0)."""
    return apply_level(0, by=by)
