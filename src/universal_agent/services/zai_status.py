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


# Dormancy window (America/Chicago) — interval content crons SHOULD be quiet
# here. Token spend inside this window from a non-24/7 process is an advisory
# waste signal (see CLAUDE.md "Operating Hours / Dormancy Default").
_DORMANCY_START_HOUR = 22
_DORMANCY_END_HOUR = 6


def _in_dormancy_window(ts: float) -> bool:
    """True if the event ts falls in 22:00–06:00 America/Chicago. Fail-soft to
    False (never over-flag) on any timezone error."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        hour = datetime.fromtimestamp(ts, ZoneInfo("America/Chicago")).hour
        return hour >= _DORMANCY_START_HOUR or hour < _DORMANCY_END_HOUR
    except Exception:  # noqa: BLE001
        return False


def analyze_token_usage(now: float, window_seconds: int, top_n: int = 25) -> dict[str, Any]:
    """Per-PROCESS ZAI token-use aggregation over one rolling window.

    Read-only, fail-soft (zeroed shape on any error), PURE PYTHON — no LLM, no
    DB. Single pass over the observability JSONL
    (``AGENT_RUN_WORKSPACES/zai_inference_events.jsonl``); the events file
    retains ~6 days, so ``window_seconds`` up to ~518400 (6d) is answerable
    without any durable store. Groups by ``caller`` (process source file) with a
    per-stage (``caller_fn`` = file::function) and per-tier breakdown, plus the
    churn signals the ZAI-Control token panel surfaces:

      - requests / r429 / reject_pct  — Fair-Usage pressure per process
      - input/output/total tokens     — burn
      - retry_input_tokens            — input tokens on 429'd attempts (the
                                        wasted full-prompt re-sends)
      - retry_multiplier              — ≈ total_input / first-attempt input
                                        (None when nothing succeeded in-window)
      - dormant_tokens                — tokens spent in the 22:00–06:00 CT
                                        dormancy window

    Token fields are absent on events captured before the capture upgrade — they
    read as 0, so an older window degrades gracefully to request counts with
    zero tokens (``token_events_seen`` reports how many in-window events carried
    real token data, so a consumer can tell "no tokens yet" from "genuinely 0").
    """
    out: dict[str, Any] = {
        "available": False,
        "window_seconds": int(window_seconds),
        "generated_at": now,
        "totals": {
            "requests": 0, "r429": 0,
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            "retry_input_tokens": 0, "dormant_tokens": 0,
            "cache_read_input_tokens": 0,
        },
        "token_events_seen": 0,
        "processes": [],
    }
    try:
        path = _events_path()
        if not path.exists():
            return out
        lines = path.read_text(errors="ignore").splitlines()
    except Exception as exc:  # noqa: BLE001
        logger.debug("zai_status token analyze read failed: %s", exc)
        return out

    out["available"] = True
    procs: dict[str, dict[str, Any]] = {}

    def _proc(caller: str) -> dict[str, Any]:
        p = procs.get(caller)
        if p is None:
            p = {
                "caller": caller, "requests": 0, "r429": 0,
                "input_tokens": 0, "output_tokens": 0,
                "cache_read_input_tokens": 0, "retry_input_tokens": 0,
                "dormant_tokens": 0, "tiers": {}, "stages": {},
            }
            procs[caller] = p
        return p

    try:
        for line in lines:
            try:
                e = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            ts = e.get("ts")
            if not isinstance(ts, (int, float)):
                continue
            if now - ts > window_seconds:
                continue
            caller = str(e.get("caller") or "unknown")
            caller_fn = str(e.get("caller_fn") or caller)
            model = str(e.get("model") or "unknown")
            tier = _model_to_tier(model) if model != "unknown" else "unknown"
            it = int(e.get("input_tokens") or 0)
            ot = int(e.get("output_tokens") or 0)
            crit = int(e.get("cache_read_input_tokens") or 0)
            is429 = e.get("category") == "rate_limited_429"
            dormant = _in_dormancy_window(ts)
            if it or ot:
                out["token_events_seen"] += 1

            p = _proc(caller)
            p["requests"] += 1
            p["input_tokens"] += it
            p["output_tokens"] += ot
            p["cache_read_input_tokens"] += crit
            if is429:
                p["r429"] += 1
                p["retry_input_tokens"] += it  # wasted full-prompt re-send
            if dormant:
                p["dormant_tokens"] += it + ot

            tb = p["tiers"].setdefault(
                tier, {"requests": 0, "input_tokens": 0, "output_tokens": 0, "r429": 0})
            tb["requests"] += 1
            tb["input_tokens"] += it
            tb["output_tokens"] += ot
            if is429:
                tb["r429"] += 1

            sb = p["stages"].setdefault(
                caller_fn,
                {"caller_fn": caller_fn, "requests": 0, "input_tokens": 0,
                 "output_tokens": 0, "r429": 0})
            sb["requests"] += 1
            sb["input_tokens"] += it
            sb["output_tokens"] += ot
            if is429:
                sb["r429"] += 1

            t = out["totals"]
            t["requests"] += 1
            t["input_tokens"] += it
            t["output_tokens"] += ot
            t["cache_read_input_tokens"] += crit
            if is429:
                t["r429"] += 1
                t["retry_input_tokens"] += it
            if dormant:
                t["dormant_tokens"] += it + ot
    except Exception as exc:  # noqa: BLE001
        logger.debug("zai_status token analyze failed: %s", exc)
        return out

    out["totals"]["total_tokens"] = (
        out["totals"]["input_tokens"] + out["totals"]["output_tokens"])

    processes: list[dict[str, Any]] = []
    for p in procs.values():
        total = p["input_tokens"] + p["output_tokens"]
        first_attempt_input = max(0, p["input_tokens"] - p["retry_input_tokens"])
        if first_attempt_input > 0:
            retry_mult: Any = round(p["input_tokens"] / first_attempt_input, 2)
        elif p["retry_input_tokens"] > 0:
            retry_mult = None  # all input was on 429'd attempts; nothing landed
        else:
            retry_mult = 1.0
        stages = sorted(
            ({**s, "total_tokens": s["input_tokens"] + s["output_tokens"]}
             for s in p["stages"].values()),
            key=lambda d: (-d["total_tokens"], -d["requests"]),
        )
        processes.append({
            "caller": p["caller"],
            "requests": p["requests"],
            "r429": p["r429"],
            "reject_pct": round(100.0 * p["r429"] / p["requests"], 1) if p["requests"] else 0.0,
            "input_tokens": p["input_tokens"],
            "output_tokens": p["output_tokens"],
            "total_tokens": total,
            "cache_read_input_tokens": p["cache_read_input_tokens"],
            "retry_input_tokens": p["retry_input_tokens"],
            "retry_multiplier": retry_mult,
            "dormant_tokens": p["dormant_tokens"],
            "tiers": p["tiers"],
            "stages": stages[:8],
        })
    # Sort by total tokens desc, then requests desc — so a window with no token
    # data yet still ranks processes by call volume (the pre-upgrade proxy).
    processes.sort(key=lambda d: (-d["total_tokens"], -d["requests"]))
    out["processes"] = processes[:top_n]
    return out


def _annotate_httpx_catalog(report: dict[str, Any]) -> None:
    """Join the committed function catalog onto the httpx source's stages (no
    LLM). Catalog coverage is scoped to httpx-zai — the other lanes are
    mission/principal granularity and have no per-function catalog entry."""
    try:
        from universal_agent.services import zai_function_catalog as cat

        catalog = cat.load_catalog()
        annotated = cat.annotate_stale(catalog)  # key -> entry + stale

        observed: list[str] = []
        for proc in report.get("processes", []):
            proc_entry = None
            for stage in proc.get("stages", []):
                cf = stage.get("caller_fn", "")
                observed.append(cf)
                entry = annotated.get(cf) or annotated.get(cf.split("::", 1)[0])
                if entry:
                    stage["catalog"] = {
                        "label": entry.get("label"),
                        "description": entry.get("description"),
                        "role": entry.get("role"),
                        "tier_current": entry.get("tier_current"),
                        "tier_verdict": entry.get("tier_verdict"),
                        "notes": entry.get("notes"),
                        "stale": entry.get("stale", False),
                    }
                    if proc_entry is None:
                        proc_entry = stage["catalog"]
                else:
                    stage["catalog"] = None
            proc["catalog"] = proc_entry

        report["catalog"] = {
            "version": catalog.get("version", 0),
            "generated_at": catalog.get("generated_at"),
            "coverage": cat.coverage(observed, catalog),
        }
    except Exception as exc:  # noqa: BLE001 — catalog is optional enrichment
        logger.debug("zai_status token catalog join failed: %s", exc)
        report["catalog"] = {"version": 0, "coverage": {}, "error": type(exc).__name__}


def _make_cache_inclusive(report: dict[str, Any]) -> None:
    """Recompute the httpx lane's ``total_tokens`` as cache-INCLUSIVE so it
    matches the other sources (cache_read dominates real spend; the httpx JSONL
    tracks cache_read but not cache_creation). Mutates in place."""
    from universal_agent.services.token_consolidation import total_incl_cache

    tt = report.get("totals") or {}
    tt["total_tokens"] = total_incl_cache(tt)
    for p in report.get("processes", []):
        p["total_tokens"] = total_incl_cache(p)
    report["processes"].sort(
        key=lambda d: (-int(d.get("total_tokens") or 0), -int(d.get("requests") or 0))
    )


def build_token_usage(window_seconds: int, top_n: int = 25) -> dict[str, Any]:
    """Consolidated, multi-lane ZAI token usage for the ZAI-Control panel.

    PURE READ, fail-soft, NO LLM. Fans out across EVERY capture lane so the panel
    shows where the **majority** of ZAI tokens actually go — not just the httpx
    slice that the old panel saw (which structurally missed the in-process SDK
    principals = the ~259M/day gap):

      - ``httpx-zai``      — JSONL (``analyze_token_usage``) + catalog join
      - ``cli-in-process`` — ``token_usage_events`` (Simone / VP / ATLAS)
      - ``cli-subprocess`` — ``cody_token_usage`` (claude --print)
      - ``csi-ingester``   — ``csi.db`` (read-only)

    ``total_tokens`` is cache-INCLUSIVE everywhere (cache_read dominates). Returns
    ``sources[]`` + ``consolidated`` + a per-day ``trend``, plus legacy top-level
    aliases (``totals``/``processes``/``catalog``/``token_events_seen`` =
    consolidated) so an un-upgraded UI keeps rendering. See ``token_consolidation``.
    """
    from universal_agent.services import token_consolidation as tc

    now = time.time()

    # Lane A — httpx JSONL + catalog join + cache-inclusive totals.
    httpx_src = analyze_token_usage(now, window_seconds, top_n=top_n)
    _annotate_httpx_catalog(httpx_src)
    httpx_src["source"] = "httpx-zai"
    httpx_src["label"] = tc.SOURCE_LABELS["httpx-zai"]
    _make_cache_inclusive(httpx_src)

    sources = [httpx_src]
    for reader in (
        tc.analyze_sink_token_usage,
        tc.analyze_cody_token_usage,
        tc.read_csi_token_usage,
    ):
        try:
            sources.append(reader(now, window_seconds, top_n=top_n))
        except Exception as exc:  # noqa: BLE001 — one lane must never break the panel
            logger.debug("token source reader %s failed: %s", reader.__name__, exc)

    consolidated = tc.consolidate(sources, top_n=top_n)
    try:
        trend = tc.build_trend(now, window_seconds)
    except Exception as exc:  # noqa: BLE001
        logger.debug("token trend build failed: %s", exc)
        trend = {"buckets": [], "series": []}

    return {
        "available": True,
        "generated_at": now,
        "window_seconds": int(window_seconds),
        "sources": sources,
        "consolidated": consolidated,
        "trend": trend,
        # Legacy top-level aliases = consolidated (keep the old UI rendering).
        "totals": consolidated["totals"],
        "processes": consolidated["processes"],
        "token_events_seen": consolidated["token_events_seen"],
        "catalog": httpx_src.get("catalog", {"version": 0, "coverage": {}}),
    }


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
