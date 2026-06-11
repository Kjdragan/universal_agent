"""Read-only aggregator for the ZAI control dashboard.

Combines three fail-soft sources into one payload the gateway
``/api/v1/ops/zai/status`` endpoint serves and the dashboard polls:

1. the httpx observability events JSONL — per-model/tier 429 rejection RATES
   over rolling windows, FUP/1313 counts, per-caller breakdown;
2. the rate-limiter snapshot — effective tier caps, outcome counters
   (succeeded-after-retry / exhausted), pause/freeze timestamps;
3. the control plane (``services/zai_control``) — current intervention level,
   global/tier pauses, cap overrides.

Every read fails soft to empty/zero — the dashboard must render even when a
source is missing, so a status read can never crash the gateway.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Rolling windows (seconds) the dashboard shows.
WINDOWS = {"1m": 60, "10m": 600, "60m": 3600}


def _events_path():
    from universal_agent.services.zai_observability import _events_path as p

    return p()


def _model_to_tier(model: str) -> str:
    try:
        from universal_agent.utils.model_resolution import model_id_to_tier

        return model_id_to_tier(model)
    except Exception:  # noqa: BLE001
        return "unknown"


def _analyze_events(now: float) -> dict[str, Any]:
    """Per-window, per-tier 429 rejection rates + FUP counts + top callers.
    Fail-soft to a zeroed shape."""
    out: dict[str, Any] = {
        "available": False,
        "windows": {w: {"total": 0, "r429": 0, "fup": 0, "fup_texted": 0,
                        "pct": 0.0, "tiers": {}} for w in WINDOWS},
        "callers_429_60m": [],
    }
    try:
        path = _events_path()
    except Exception:  # noqa: BLE001
        return out
    try:
        if not path.exists():
            return out
        max_age = max(WINDOWS.values())
        callers: dict[str, int] = {}
        lines = path.read_text(errors="ignore").splitlines()
        out["available"] = True
        for line in lines:
            try:
                e = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            ts = e.get("ts")
            if not isinstance(ts, (int, float)):
                continue
            age = now - ts
            if age > max_age:
                continue
            cat = e.get("category")
            model = str(e.get("model") or "unknown")
            tier = _model_to_tier(model) if model != "unknown" else "unknown"
            is429 = cat == "rate_limited_429"
            isfup = cat == "fup_signal"
            fuptxt = bool(e.get("fup_texted"))
            for wname, wsec in WINDOWS.items():
                if age > wsec:
                    continue
                w = out["windows"][wname]
                w["total"] += 1
                if is429:
                    w["r429"] += 1
                if isfup:
                    w["fup"] += 1
                if fuptxt:
                    w["fup_texted"] += 1
                tb = w["tiers"].setdefault(tier, {"total": 0, "r429": 0, "fup": 0, "fup_texted": 0})
                tb["total"] += 1
                if is429:
                    tb["r429"] += 1
                if isfup:
                    tb["fup"] += 1
                if fuptxt:
                    tb["fup_texted"] += 1
            if is429 and age <= 3600:
                c = str(e.get("caller") or "?").split("/")[-1]
                callers[c] = callers.get(c, 0) + 1
        for w in out["windows"].values():
            w["pct"] = round(100.0 * w["r429"] / w["total"], 1) if w["total"] else 0.0
            for tb in w["tiers"].values():
                tb["pct"] = round(100.0 * tb["r429"] / tb["total"], 1) if tb["total"] else 0.0
        out["callers_429_60m"] = sorted(
            ({"caller": c, "count": n} for c, n in callers.items()),
            key=lambda d: -d["count"],
        )[:8]
    except Exception as exc:  # noqa: BLE001
        logger.debug("zai_status events analyze failed: %s", exc)
    return out


def _read_snapshot() -> dict[str, Any]:
    try:
        from universal_agent.rate_limiter import _get_state_path

        path = _get_state_path()
        if not path.exists():
            return {}
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("zai_status snapshot read failed: %s", exc)
        return {}


def build_status() -> dict[str, Any]:
    """The full dashboard payload. Never raises."""
    now = time.time()
    snapshot = _read_snapshot()
    control: dict[str, Any] = {}
    levels: dict[str, Any] = {}
    try:
        from universal_agent.services import zai_control

        control = zai_control.current_state()
        levels = {str(k): v for k, v in zai_control.LEVELS.items()}
    except Exception as exc:  # noqa: BLE001
        logger.debug("zai_status control read failed: %s", exc)

    tiers = snapshot.get("tiers") if isinstance(snapshot.get("tiers"), dict) else {}
    return {
        "generated_at": now,
        "events": _analyze_events(now),
        "snapshot": {
            "tier_caps": {t: (d or {}).get("cap") for t, d in tiers.items()},
            "tier_detail": tiers,
            "total_requests": snapshot.get("total_requests"),
            "total_429s": snapshot.get("total_429s"),
            "total_fup_events": snapshot.get("total_fup_events"),
            "total_429s_exhausted": snapshot.get("total_429s_exhausted"),
            "total_succeeded_after_retry": snapshot.get("total_succeeded_after_retry"),
            "acquire_pause_until": snapshot.get("acquire_pause_until"),
            "freeze_until": snapshot.get("freeze_until"),
            "cross_loop_conflicts": snapshot.get("cross_loop_conflicts"),
            "pid": snapshot.get("pid"),
            "process_name": snapshot.get("process_name"),
            "snapshot_written_at": snapshot.get("snapshot_written_at"),
        },
        "control": control,
        "level_presets": levels,
    }
