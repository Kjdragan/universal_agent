"""Capture token usage for IN-PROCESS Claude Agent SDK principal turns.

This is the consolidation lane for the bulk of ZAI spend that the httpx
observability hook (``services/zai_observability.py``) cannot see. Simone
heartbeat/daemon sessions and in-process VP coder turns run the Claude Agent
SDK *in-process* and return token usage via the SDK ``ResultMessage`` — NOT over
the patched httpx client to ``api.z.ai`` — so the httpx monkey-patch never
observes them. (External ``claude --print`` subprocess missions are a different
lane, captured separately into ``cody_token_usage`` by
``vp/clients/claude_cli_client.py::_record_mission_token_usage``.)

One row per ``main.py::process_turn`` invocation is written to
``activity_state.db::token_usage_events`` (schema in
``task_hub.py::ensure_schema``). The dashboard fans this in alongside the JSONL
lane and CSI's ``csi.db`` (see ``services/zai_status.py``).

Strictly best-effort: every public function swallows all exceptions and NEVER
raises into the caller (mirrors
``claude_cli_client._record_mission_token_usage``). The heartbeat runs inside
the gateway asyncio loop, so callers MUST offload the write via
``asyncio.to_thread`` and never block the loop.

Empirically verified (2026-06-14, real Simone ``trace.json`` on the VPS):

* SDK ``ResultMessage.usage`` is **per-iteration, NOT session-cumulative** —
  consecutive messages carry independent ``input_tokens`` (e.g. 49093, 5651,
  4586, ...), so a turn's tokens are the **SUM** of its delta-slice messages
  (taking only the tail message would under-count to ~0, since trailing no-op
  heartbeat messages report zero usage).
* ``ResultMessage.total_cost_usd`` **is** session-cumulative (monotonic), so a
  turn's cost is ``last_in_slice - pre_turn_baseline``.
* The trace's existing ``token_usage`` accumulator **excludes** cache-read
  tokens, which dominate real spend — so we sum input+output+cache here.
* ``activity_state.db`` is the home of ``cody_token_usage``; this lane writes
  there too (NOT ``runtime_state.db`` / ``_ctx.runtime_db_conn``).

Coverage boundary: only turns that reach ``process_turn``'s end-of-turn / guardrail
recorder are captured. The ``/reset``, ``/harness`` outer-wrapper, and
auth-required early returns, plus the SIMPLE fast path (``handle_simple_query``
consumes its ``ResultMessage`` without appending to ``sdk_result_messages``),
record an empty delta and therefore no row. These are UNDER-counts (never
double-counts); the bulk lanes (Simone heartbeat/daemon, in-process VP coder)
force the STANDARD path, so the omission is immaterial to the headline ~259M/day.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
import sqlite3
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── run_source (UA_RUN_SOURCE) → (source, principal) ───────────────────────
# `source` = WHERE the bytes were captured (the double-count invariant — this
# module owns only `cli-in-process`). `principal` = WHO spent them (the operator
# breakdown). Derived from UA_RUN_SOURCE, set deterministically by
# gateway.py::_run_adapter / claude_code_client.run_mission / execution_engine.
_PRINCIPAL_MAP: dict[str, tuple[str, str]] = {
    "heartbeat": ("cli-in-process", "simone"),
    "cron": ("cli-in-process", "simone"),
    "daemon": ("cli-in-process", "simone"),
    "vp.coder": ("cli-in-process", "vp-coder"),
    "vp.coder.external": ("cli-in-process", "vp-coder"),
}


def classify_run_source(run_source: str) -> tuple[str, str]:
    """Map a UA_RUN_SOURCE value to ``(source, principal)``. Unknown sources are
    kept (never dropped) as ``interactive`` so spend stays visible+attributable."""
    rs = (run_source or "user").strip().lower()
    if rs in _PRINCIPAL_MAP:
        return _PRINCIPAL_MAP[rs]
    if rs.startswith("vp.coder"):
        return ("cli-in-process", "vp-coder")
    if rs.startswith("vp."):
        return ("cli-in-process", "vp")
    return ("cli-in-process", "interactive")


def _enabled() -> bool:
    return os.getenv("UA_TOKEN_SINK_ENABLED", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _i(value: Any) -> int:
    try:
        return max(0, int(value))
    except Exception:  # noqa: BLE001
        return 0


def _f(value: Any) -> float:
    try:
        return float(value)
    except Exception:  # noqa: BLE001
        return 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def slice_turn_delta(
    messages: list[dict], start_idx: int
) -> tuple[list[dict], float]:
    """Return ``(delta_messages, baseline_cost_usd)`` for ONE turn.

    ``sdk_result_messages`` is REUSED across turns for persistent daemon
    adapters, so ``start_idx`` (the list length snapshotted at turn entry) makes
    ``messages[start_idx:]`` exactly this turn's new messages — adjacent turns
    therefore slice disjoint ranges and can never overlap. ``baseline`` is the
    cumulative ``total_cost_usd`` of the message immediately BEFORE this turn (or
    0.0), used by the writer to compute this turn's cost-delta. Pure + total: any
    odd ``start_idx`` clamps to "no delta", never raises.
    """
    msgs = messages or []
    n = len(msgs)
    si = start_idx if isinstance(start_idx, int) and 0 <= start_idx <= n else n
    delta = msgs[si:]
    baseline = 0.0
    if si > 0:
        try:
            baseline = float((msgs[si - 1] or {}).get("total_cost_usd") or 0.0)
        except Exception:  # noqa: BLE001
            baseline = 0.0
    return delta, baseline


def sum_turn_usage(delta_messages: list[dict]) -> dict[str, Any]:
    """Aggregate ONE turn's delta slice of ``sdk_result_messages``.

    Tokens are SUMMED across messages (per-iteration values, additive). The
    cumulative ``total_cost_usd`` of the LAST message in the slice is returned as
    ``last_cost`` (cost-delta vs the pre-turn baseline is applied by the writer).
    ``num_turns`` is the max seen; ``model`` is a best-effort id from
    ``model_usage``; ``status`` is ``error`` if any message flagged ``is_error``.
    """
    inp = out = cache_creation = cache_read = 0
    num_turns = 0
    model: Optional[str] = None
    last_cost: Optional[float] = None
    any_error = False
    for m in delta_messages or []:
        if not isinstance(m, dict):
            continue
        usage = m.get("usage")
        if isinstance(usage, dict):
            inp += _i(usage.get("input_tokens"))
            out += _i(usage.get("output_tokens"))
            cache_creation += _i(usage.get("cache_creation_input_tokens"))
            cache_read += _i(usage.get("cache_read_input_tokens"))
        nt = m.get("num_turns")
        if isinstance(nt, (int, float)):
            num_turns = max(num_turns, int(nt))  # cumulative-ish; max is the turn count
        tc = m.get("total_cost_usd")
        if isinstance(tc, (int, float)):
            last_cost = float(tc)  # cumulative; writer takes the delta vs baseline
        if m.get("is_error"):
            any_error = True
        if model is None:
            # First model id seen; mixed-model turns are rare for a single principal.
            mu = m.get("model_usage")
            if isinstance(mu, dict) and mu:
                try:
                    model = next(iter(mu.keys()))
                except Exception:  # noqa: BLE001
                    model = None
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "total_tokens": inp + out + cache_creation + cache_read,
        "num_turns": num_turns,
        "model": model,
        "last_cost": last_cost,
        "status": "error" if any_error else "ok",
    }


_INSERT_SQL = """
INSERT INTO token_usage_events (
    ts, recorded_at, source, principal, model, caller, caller_fn, status,
    input_tokens, output_tokens, cache_creation_input_tokens,
    cache_read_input_tokens, total_cost_usd, num_turns,
    mission_id, task_id, session_id, run_id
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS token_usage_events (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                          REAL NOT NULL,
    recorded_at                 TEXT NOT NULL,
    source                      TEXT NOT NULL,
    principal                   TEXT,
    model                       TEXT,
    caller                      TEXT,
    caller_fn                   TEXT,
    status                      TEXT,
    input_tokens                INTEGER NOT NULL DEFAULT 0,
    output_tokens               INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens     INTEGER NOT NULL DEFAULT 0,
    total_cost_usd              REAL NOT NULL DEFAULT 0.0,
    num_turns                   INTEGER NOT NULL DEFAULT 0,
    mission_id                  TEXT,
    task_id                     TEXT,
    session_id                  TEXT,
    run_id                      TEXT
)
"""


def ensure_token_usage_events_table(conn: sqlite3.Connection) -> None:
    """Create the table + indexes if missing (idempotent). The canonical
    definition lives in ``task_hub.ensure_schema``; this self-heal lets the
    writer work even against a connection that predates it."""
    conn.execute(_CREATE_SQL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tue_ts ON token_usage_events(ts DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tue_source_ts ON token_usage_events(source, ts DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tue_principal_ts ON token_usage_events(principal, ts DESC)")


def record_session_token_usage(
    *,
    delta_messages: list[dict],
    run_source: str,
    baseline_cost_usd: float = 0.0,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
    mission_id: Optional[str] = None,
    task_id: Optional[str] = None,
    model: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Optional[int]:
    """Best-effort single INSERT of ONE per-turn row. NEVER raises.

    Returns the new rowid, or ``None`` when disabled / nothing was spent / on any
    error. When ``conn`` is omitted, opens a short-lived connection to
    ``activity_state.db`` (the home of ``cody_token_usage``) and closes it.
    """
    own_conn: Optional[sqlite3.Connection] = None
    try:
        if not _enabled():
            return None
        agg = sum_turn_usage(delta_messages)
        if agg["total_tokens"] <= 0:
            return None  # no-op heartbeat turn — nothing spent, skip the row

        source, principal = classify_run_source(run_source)
        cost = 0.0
        if agg["last_cost"] is not None:
            # total_cost_usd is session-cumulative → this turn's cost is the rise
            # above the pre-turn baseline (clamped, in case the slice missed the
            # baseline message).
            cost = max(0.0, agg["last_cost"] - _f(baseline_cost_usd))

        row = (
            time.time(),
            _now_iso(),
            source,
            principal,
            (model or agg["model"]),
            principal,
            f"{principal}::turn",
            agg["status"],
            agg["input_tokens"],
            agg["output_tokens"],
            agg["cache_creation_input_tokens"],
            agg["cache_read_input_tokens"],
            cost,
            agg["num_turns"],
            mission_id,
            task_id,
            session_id,
            run_id,
        )

        c = conn
        if c is None:
            from universal_agent.durable.db import (
                connect_runtime_db,
                get_activity_db_path,
            )

            own_conn = connect_runtime_db(get_activity_db_path())
            c = own_conn

        try:
            cur = c.execute(_INSERT_SQL, row)
        except sqlite3.OperationalError:
            ensure_token_usage_events_table(c)
            cur = c.execute(_INSERT_SQL, row)
        try:
            # connect_runtime_db uses isolation_level=None (autocommit), so this
            # commit() is a harmless no-op there; it still finalizes an explicit
            # transaction when a caller passes a default-isolation conn (e.g. tests).
            c.commit()
        except Exception:  # noqa: BLE001
            pass
        return cur.lastrowid
    except Exception as exc:  # noqa: BLE001 — strictly fail-soft
        logger.warning("record_session_token_usage failed (swallowed): %s", exc)
        return None
    finally:
        if own_conn is not None:
            try:
                own_conn.close()
            except Exception:  # noqa: BLE001
                pass
