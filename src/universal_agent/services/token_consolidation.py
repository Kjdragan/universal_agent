"""Consolidated, multi-lane ZAI token-usage reader (PURE READ, fail-soft, no LLM).

The dashboard's old ``Token use by process`` panel read ONLY the httpx
observability JSONL (`zai_inference_events.jsonl`) — direct ``api.z.ai`` calls
made *inside the gateway process* (convergence, classifiers, mission-control).
That structurally **misses** the bulk of spend: the in-process Claude Agent SDK
principals (Simone heartbeat/daemon, in-process VP coder, ATLAS) run the model in
a spawned ``claude`` subprocess and return usage via the SDK ``ResultMessage`` —
never over the patched httpx client — and CSI is a separate OS process with its
own DB. This is the ~259M/day gap that made the internal chart (~22M) diverge
from z.ai's bill (~281M).

This module federates every lane on the READ path so the operator can see where
the majority of tokens actually go and spot a runaway (e.g. the 2026-06-14 ATLAS
nightly-wiki re-dispatch storm):

  - ``cli-in-process``  — ``activity_state.db::token_usage_events`` (Simone / VP / ATLAS)
  - ``cli-subprocess``  — ``activity_state.db::cody_token_usage`` (external claude --print)
  - ``csi-ingester``    — ``csi.db::token_usage`` (read-only federation)
  - (``httpx-zai`` is joined in by ``zai_status.build_token_usage`` itself.)

CANONICAL CACHE SEMANTICS: ``total_tokens`` here is **cache-INCLUSIVE**
(input + output + cache_creation + cache_read). cache_read DOMINATES real spend
(a single Simone turn ≈ 2M tokens, ~90 % cache_read), so a cache-exclusive total
would understate the in-process lane ~10× and hide exactly what we are trying to
surface. z.ai bills cache, so the cache-inclusive consolidated total reconciles
to the ~281M order of magnitude.

Every reader is read-only and fail-soft: a missing/locked DB degrades that one
source to ``available: false`` and never raises into the gateway.
"""

from __future__ import annotations

import datetime
import logging
import os
import sqlite3
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Stable per-source display metadata (the UI renders tabs in this order).
SOURCE_LABELS: dict[str, str] = {
    "httpx-zai": "httpx · direct api.z.ai (convergence, classifiers, mission-control)",
    "cli-in-process": "in-process SDK (Simone heartbeat/daemon, VP coder, ATLAS)",
    "cli-subprocess": "claude --print subprocess (Cody / VP missions)",
    "csi-ingester": "CSI ingester (separate process)",
}


def _utc(ts: float) -> datetime.datetime:
    """Timezone-aware UTC datetime (utcfromtimestamp is deprecated on py3.12+)."""
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)


def _i(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def total_incl_cache(d: dict[str, Any]) -> int:
    """Cache-INCLUSIVE token total — the real-spend number (see module docstring)."""
    return (
        _i(d.get("input_tokens"))
        + _i(d.get("output_tokens"))
        + _i(d.get("cache_creation_input_tokens"))
        + _i(d.get("cache_read_input_tokens"))
    )


def _empty_source(source: str, available: bool = False) -> dict[str, Any]:
    return {
        "source": source,
        "label": SOURCE_LABELS.get(source, source),
        "available": available,
        "token_events_seen": 0,
        "totals": {
            "requests": 0,
            "r429": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "total_tokens": 0,
            "retry_input_tokens": 0,
            "dormant_tokens": 0,
        },
        "processes": [],
    }


def _finalize_processes(
    procs: dict[str, dict[str, Any]], top_n: int
) -> list[dict[str, Any]]:
    """Compute cache-inclusive totals + stage sort + ranking for a source."""
    out: list[dict[str, Any]] = []
    for p in procs.values():
        stages = sorted(
            (
                {**s, "total_tokens": total_incl_cache(s)}
                for s in p.get("_stages", {}).values()
            ),
            key=lambda d: -d["total_tokens"],
        )
        total = total_incl_cache(p)
        requests = _i(p.get("requests"))
        out.append(
            {
                "caller": p["caller"],
                "requests": requests,
                "r429": _i(p.get("r429")),
                "reject_pct": round(100.0 * _i(p.get("r429")) / requests, 1)
                if requests
                else 0.0,
                "input_tokens": _i(p.get("input_tokens")),
                "output_tokens": _i(p.get("output_tokens")),
                "cache_creation_input_tokens": _i(p.get("cache_creation_input_tokens")),
                "cache_read_input_tokens": _i(p.get("cache_read_input_tokens")),
                "total_tokens": total,
                "retry_input_tokens": _i(p.get("retry_input_tokens")),
                "retry_multiplier": p.get("retry_multiplier", 1.0),
                "dormant_tokens": _i(p.get("dormant_tokens")),
                "total_cost_usd": round(float(p.get("total_cost_usd") or 0.0), 2),
                "stages": stages[:8],
            }
        )
    out.sort(key=lambda d: (-d["total_tokens"], -d["requests"]))
    return out[:top_n]


def _activity_ro_conn() -> Optional[sqlite3.Connection]:
    """Read-only connection to activity_state.db (token_usage_events +
    cody_token_usage live here). Returns None if absent."""
    try:
        from universal_agent.durable.db import get_activity_db_path

        path = get_activity_db_path()
    except Exception:
        return None
    if not path or not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(
            f"file:{path}?mode=ro&uri=true", uri=True, timeout=2.0
        )
        conn.execute("PRAGMA busy_timeout=2000;")
        return conn
    except Exception as exc:  # noqa: BLE001
        logger.debug("token_consolidation: activity_state.db ro open failed: %s", exc)
        return None


def analyze_sink_token_usage(
    now: float, window_seconds: int, top_n: int = 25
) -> dict[str, Any]:
    """``cli-in-process`` source: ``token_usage_events`` grouped by ``principal``
    (Simone / VP / ATLAS / interactive), with a per-model stage breakdown."""
    src = _empty_source("cli-in-process")
    conn = _activity_ro_conn()
    if conn is None:
        return src
    cutoff = now - window_seconds
    try:
        rows = conn.execute(
            """SELECT principal, model, status,
                      COUNT(*),
                      SUM(input_tokens), SUM(output_tokens),
                      SUM(cache_creation_input_tokens), SUM(cache_read_input_tokens),
                      SUM(total_cost_usd), SUM(num_turns)
               FROM token_usage_events
               WHERE source = 'cli-in-process' AND ts >= ?
               GROUP BY principal, model, status""",
            (cutoff,),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug("analyze_sink_token_usage query failed: %s", exc)
        conn.close()
        return src
    conn.close()

    src["available"] = True
    procs: dict[str, dict[str, Any]] = {}
    tot = src["totals"]
    for (principal, model, status, cnt, it, ot, cc, cr, cost, nturns) in rows:
        principal = str(principal or "unknown")
        model = str(model or "unknown")
        p = procs.setdefault(
            principal,
            {
                "caller": principal,
                "requests": 0,
                "r429": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "total_cost_usd": 0.0,
                "_stages": {},
            },
        )
        # one "request" per turn-row's num_turns (best-effort run count proxy)
        runs = _i(cnt)
        p["requests"] += runs
        p["input_tokens"] += _i(it)
        p["output_tokens"] += _i(ot)
        p["cache_creation_input_tokens"] += _i(cc)
        p["cache_read_input_tokens"] += _i(cr)
        p["total_cost_usd"] += float(cost or 0.0)
        st = p["_stages"].setdefault(
            f"{principal}::{model}",
            {
                "caller_fn": f"{principal}::{model}",
                "requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "r429": 0,
            },
        )
        st["requests"] += runs
        st["input_tokens"] += _i(it)
        st["output_tokens"] += _i(ot)
        st["cache_creation_input_tokens"] += _i(cc)
        st["cache_read_input_tokens"] += _i(cr)
        tot["requests"] += runs
        tot["input_tokens"] += _i(it)
        tot["output_tokens"] += _i(ot)
        tot["cache_creation_input_tokens"] += _i(cc)
        tot["cache_read_input_tokens"] += _i(cr)
        if it or ot or cr:
            src["token_events_seen"] += runs
    tot["total_tokens"] = total_incl_cache(tot)
    src["processes"] = _finalize_processes(procs, top_n)
    return src


def analyze_cody_token_usage(
    now: float, window_seconds: int, top_n: int = 25
) -> dict[str, Any]:
    """``cli-subprocess`` source: ``cody_token_usage`` grouped by cody_mode+model
    (external ``claude --print`` VP/Cody missions). ``recorded_at`` is ISO UTC."""
    src = _empty_source("cli-subprocess")
    conn = _activity_ro_conn()
    if conn is None:
        return src
    cutoff = (
        _utc(now - window_seconds)
        .strftime("%Y-%m-%dT%H:%M:%S")
    )
    try:
        rows = conn.execute(
            """SELECT cody_mode, model,
                      COUNT(*),
                      SUM(input_tokens), SUM(output_tokens),
                      SUM(cache_creation_input_tokens), SUM(cache_read_input_tokens),
                      SUM(total_cost_usd)
               FROM cody_token_usage
               WHERE recorded_at >= ?
               GROUP BY cody_mode, model""",
            (cutoff,),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug("analyze_cody_token_usage query failed: %s", exc)
        conn.close()
        return src
    conn.close()

    src["available"] = True
    procs: dict[str, dict[str, Any]] = {}
    tot = src["totals"]
    for (mode, model, cnt, it, ot, cc, cr, cost) in rows:
        mode = str(mode or "unknown")
        model = str(model or "unknown")
        caller = f"cody:{mode}"
        p = procs.setdefault(
            caller,
            {
                "caller": caller,
                "requests": 0,
                "r429": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "total_cost_usd": 0.0,
                "_stages": {},
            },
        )
        runs = _i(cnt)
        p["requests"] += runs
        p["input_tokens"] += _i(it)
        p["output_tokens"] += _i(ot)
        p["cache_creation_input_tokens"] += _i(cc)
        p["cache_read_input_tokens"] += _i(cr)
        p["total_cost_usd"] += float(cost or 0.0)
        st = p["_stages"].setdefault(
            f"{caller}::{model}",
            {
                "caller_fn": f"{caller}::{model}",
                "requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "r429": 0,
            },
        )
        st["requests"] += runs
        st["input_tokens"] += _i(it)
        st["output_tokens"] += _i(ot)
        st["cache_creation_input_tokens"] += _i(cc)
        st["cache_read_input_tokens"] += _i(cr)
        tot["requests"] += runs
        tot["input_tokens"] += _i(it)
        tot["output_tokens"] += _i(ot)
        tot["cache_creation_input_tokens"] += _i(cc)
        tot["cache_read_input_tokens"] += _i(cr)
        if it or ot or cr:
            src["token_events_seen"] += runs
    tot["total_tokens"] = total_incl_cache(tot)
    src["processes"] = _finalize_processes(procs, top_n)
    return src


def read_csi_token_usage(
    now: float, window_seconds: int, top_n: int = 25
) -> dict[str, Any]:
    """``csi-ingester`` source: ``csi.db::token_usage`` (read-only federation).

    ``occurred_at`` is naive-UTC text ``YYYY-MM-DD HH:MM:SS`` — the cutoff is
    formatted the same way so the comparison doesn't silently return empty. CSI's
    ``prompt_tokens`` is cache-inclusive already; we map it to ``input_tokens``
    and ``total_tokens`` follows from the row's ``total_tokens``.
    """
    src = _empty_source("csi-ingester")
    path = os.path.expanduser(
        os.getenv("CSI_DB_PATH", "/var/lib/universal-agent/csi/csi.db")
    )
    if not os.path.exists(path):
        return src
    cutoff = _utc(now - window_seconds).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro&uri=true", uri=True, timeout=2.0)
        conn.execute("PRAGMA busy_timeout=2000;")
        rows = conn.execute(
            """SELECT process_name, model_name,
                      COUNT(*),
                      SUM(prompt_tokens), SUM(completion_tokens), SUM(total_tokens)
               FROM token_usage
               WHERE occurred_at >= ?
               GROUP BY process_name, model_name""",
            (cutoff,),
        ).fetchall()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("read_csi_token_usage failed (fail-soft): %s", exc)
        return src

    src["available"] = True
    procs: dict[str, dict[str, Any]] = {}
    tot = src["totals"]
    for (proc_name, model, cnt, prompt, completion, total) in rows:
        proc_name = str(proc_name or "unknown")
        model = str(model or "unknown")
        caller = f"csi:{proc_name}"
        p = procs.setdefault(
            caller,
            {
                "caller": caller,
                "requests": 0,
                "r429": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "total_cost_usd": 0.0,
                "_stages": {},
                "_csi_total": 0,
            },
        )
        runs = _i(cnt)
        p["requests"] += runs
        p["input_tokens"] += _i(prompt)
        p["output_tokens"] += _i(completion)
        p["_csi_total"] += _i(total) or (_i(prompt) + _i(completion))
        st = p["_stages"].setdefault(
            caller,
            {
                "caller_fn": caller,
                "requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "r429": 0,
            },
        )
        st["requests"] += runs
        st["input_tokens"] += _i(prompt)
        st["output_tokens"] += _i(completion)
        tot["requests"] += runs
        tot["input_tokens"] += _i(prompt)
        tot["output_tokens"] += _i(completion)
        if prompt or completion:
            src["token_events_seen"] += runs
    # CSI's prompt_tokens is already cache-inclusive; total = sum of its total col.
    tot["total_tokens"] = sum(_i(p.get("_csi_total")) for p in procs.values()) or (
        _i(tot["input_tokens"]) + _i(tot["output_tokens"])
    )
    finalized = _finalize_processes(procs, top_n)
    # Override total with CSI's own cache-inclusive total per process.
    by_caller = {p["caller"]: p for p in procs.values()}
    for fp in finalized:
        csi_total = _i(by_caller.get(fp["caller"], {}).get("_csi_total"))
        if csi_total:
            fp["total_tokens"] = csi_total
    finalized.sort(key=lambda d: (-d["total_tokens"], -d["requests"]))
    src["processes"] = finalized
    return src


def consolidate(sources: list[dict[str, Any]], top_n: int = 25) -> dict[str, Any]:
    """Sum per-source totals (cache-inclusive) + concat & re-rank processes."""
    totals = {
        "requests": 0,
        "r429": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "total_tokens": 0,
        "retry_input_tokens": 0,
        "dormant_tokens": 0,
    }
    token_events_seen = 0
    all_procs: list[dict[str, Any]] = []
    for s in sources:
        st = s.get("totals") or {}
        for k in totals:
            totals[k] += _i(st.get(k))
        token_events_seen += _i(s.get("token_events_seen"))
        for p in s.get("processes") or []:
            all_procs.append({**p, "source": s.get("source")})
    totals["total_tokens"] = total_incl_cache(totals)
    all_procs.sort(key=lambda d: (-_i(d.get("total_tokens")), -_i(d.get("requests"))))
    return {
        "totals": totals,
        "token_events_seen": token_events_seen,
        "processes": all_procs[:top_n],
    }


def build_trend(
    now: float, window_seconds: int, max_series: int = 8
) -> dict[str, Any]:
    """Per-day token trend by source/principal from ``token_usage_events`` +
    ``cody_token_usage``, so trajectories and runaway spikes (e.g. the ATLAS
    nightly-wiki storm) stand out. Cache-inclusive. Fail-soft → empty trend."""
    out: dict[str, Any] = {"buckets": [], "series": []}
    conn = _activity_ro_conn()
    if conn is None:
        return out
    window_seconds = max(int(window_seconds), 86400)
    n_days = min(30, max(1, (window_seconds + 86399) // 86400))
    # UTC day buckets, oldest→newest.
    today = _utc(now).date()
    buckets = [
        (today - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(n_days - 1, -1, -1)
    ]
    bucket_idx = {b: i for i, b in enumerate(buckets)}
    cutoff = now - (n_days * 86400)
    series: dict[str, dict[str, Any]] = {}
    try:
        rows = conn.execute(
            """SELECT principal,
                      strftime('%Y-%m-%d', ts, 'unixepoch') AS day,
                      COUNT(*),
                      SUM(input_tokens + output_tokens
                          + cache_creation_input_tokens + cache_read_input_tokens)
               FROM token_usage_events
               WHERE source = 'cli-in-process' AND ts >= ?
               GROUP BY principal, day""",
            (cutoff,),
        ).fetchall()
        cody_rows = conn.execute(
            """SELECT 'cody:' || cody_mode AS principal,
                      substr(recorded_at, 1, 10) AS day,
                      COUNT(*),
                      SUM(input_tokens + output_tokens
                          + cache_creation_input_tokens + cache_read_input_tokens)
               FROM cody_token_usage
               WHERE recorded_at >= ?
               GROUP BY principal, day""",
            (
                _utc(cutoff).strftime(
                    "%Y-%m-%dT%H:%M:%S"
                ),
            ),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug("build_trend query failed: %s", exc)
        conn.close()
        return out
    conn.close()

    for (principal, day, cnt, toks) in list(rows) + list(cody_rows):
        key = str(principal or "unknown")
        if day not in bucket_idx:
            continue
        s = series.setdefault(
            key,
            {"key": key, "tokens": [0] * n_days, "runs": [0] * n_days, "_total": 0},
        )
        i = bucket_idx[day]
        s["tokens"][i] += _i(toks)
        s["runs"][i] += _i(cnt)
        s["_total"] += _i(toks)

    ranked = sorted(series.values(), key=lambda d: -d["_total"])[:max_series]
    for s in ranked:
        s.pop("_total", None)
    out["buckets"] = buckets
    out["series"] = ranked
    return out
