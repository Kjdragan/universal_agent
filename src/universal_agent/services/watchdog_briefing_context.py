"""Watchdog briefing context block.

P6 (2026-05-20): wire the proactive_health watchdog state into the
morning briefing so Simone's digest surfaces what the watchdog has
been detecting. Before this, briefings_agent.py had no awareness of
watchdog findings — operator got a daily digest that missed
critical-tier system issues the watchdog had been flagging for hours.

Data source: GET /api/v1/ops/proactive_health — the live
Layer-1 + Layer-2 watchdog state. It is always current because
``build_proactive_health_payload`` re-runs every invariant probe on each
call (best-effort: skipped if no UA_OPS_TOKEN, endpoint unreachable, etc.).

(2026-06-03) proactive_health findings are no longer parked as Task Hub
``needs_review`` rows. They are surfaced via this live endpoint, the
Mission Control "System Health" panel, and critical email. The former
``task_hub_items WHERE source_kind='proactive_health'`` backlog query was
removed when the Task Hub write was retired — those rows would otherwise
be permanently empty (and were a source of zombie rows, severity
mislabel, and Kanban "Needs Review" pollution).

Kill switch: `UA_BRIEFING_WATCHDOG_BLOCK_ENABLED=0` returns "".
Default ON. Any helper exception is swallowed — the briefing must
never break because the watchdog query did.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Severity labels for renderer; ordered worst-first so the block leads
# with the most actionable rows.
_SEVERITY_ORDER = ("critical", "warn", "info")

# How far back to look for a critical page that fired *in-window* but has since
# recovered. The digest renders once a day; a critical that paged at (say) 1:10a
# and self-recovered before the 6:30a render leaves no trace in the live
# endpoint, so the briefing would falsely read "no critical alerts". Default 24h
# matches the daily briefing cadence.
_RECOVERED_LOOKBACK_SECONDS = 86400  # 24h


def _enabled() -> bool:
    return (os.getenv("UA_BRIEFING_WATCHDOG_BLOCK_ENABLED") or "1") != "0"


def _recovered_lookback_seconds() -> int:
    raw = os.getenv("UA_BRIEFING_WATCHDOG_RECOVERED_LOOKBACK_SECONDS")
    if not raw:
        return _RECOVERED_LOOKBACK_SECONDS
    try:
        return max(60, int(raw))
    except ValueError:
        return _RECOVERED_LOOKBACK_SECONDS


def _parse_iso_ts(value: Any) -> float | None:
    """ISO-8601 (Z-tolerant) -> epoch seconds, or None on garbage/empty."""
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def render_in_window_recovered_criticals(
    *,
    current_critical_ids: set[str],
    last_digest_fingerprint: str | None,
    last_digest_sent_at_utc: str | None,
    now_ts: float | None = None,
    lookback_seconds: int | None = None,
) -> str:
    """Render the 'criticals fired in-window (since recovered)' line, or "".

    Reuses the durable digest-cooldown columns on the singleton
    ``proactive_health_snapshots`` row: ``last_digest_fingerprint`` is the
    pipe-joined set of critical finding-ids the timer last alerted on, and
    ``last_digest_sent_at_utc`` is when that page fired. A finding-id qualifies
    as "fired in-window but since recovered" when the last digest fired inside
    the lookback window AND that id is no longer in the current critical set
    (i.e. the live endpoint has nothing to show, so the briefing would otherwise
    imply a clean night).
    """
    if now_ts is None:
        now_ts = datetime.now(timezone.utc).timestamp()
    window = (
        lookback_seconds if lookback_seconds is not None else _recovered_lookback_seconds()
    )

    sent_ts = _parse_iso_ts(last_digest_sent_at_utc)
    if sent_ts is None or (now_ts - sent_ts) > window:
        return ""

    alerted_ids = {part for part in (last_digest_fingerprint or "").split("|") if part}
    recovered = sorted(alerted_ids - set(current_critical_ids))
    if not recovered:
        return ""

    ids = ", ".join(f"`{i}`" for i in recovered)
    return (
        "### Criticals fired in-window (since recovered)\n"
        f"- {ids} — paged after the last digest but recovered before this render; "
        "no longer active. Acknowledge that the night was not fully clean."
    )


def _read_latest_snapshot_safe() -> dict[str, Any] | None:
    """Read the singleton proactive_health snapshot row, or None on any failure.

    Best-effort: a missing table / DB / import never breaks the briefing.
    """
    try:
        from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
        from universal_agent.services.proactive_health_snapshot import (
            read_latest_snapshot,
        )

        conn = connect_runtime_db(get_activity_db_path())
        try:
            return read_latest_snapshot(conn)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001 — never break the briefing
        logger.debug("watchdog briefing: snapshot read failed: %s", exc)
        return None


def _fetch_current_findings() -> dict[str, Any] | None:
    """Hit the proactive_health endpoint. Returns None on any failure —
    we omit the watchdog block rather than block the briefing."""
    token = (os.getenv("UA_OPS_TOKEN") or "").strip()
    if not token:
        return None
    port = (os.getenv("UA_GATEWAY_PORT") or "8002").strip()
    url = f"http://127.0.0.1:{port}/api/v1/ops/proactive_health"
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=5.0,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001 — never break the briefing
        logger.debug("watchdog briefing: endpoint fetch failed: %s", exc)
        return None


def _render_findings(findings: list[dict[str, Any]]) -> str:
    """Render the current-heartbeat findings list in severity order."""
    if not findings:
        return ""
    by_sev: dict[str, list[dict[str, Any]]] = {s: [] for s in _SEVERITY_ORDER}
    for f in findings:
        sev = str(f.get("severity") or "info").lower()
        by_sev.setdefault(sev, []).append(f)

    lines: list[str] = []
    for sev in _SEVERITY_ORDER:
        items = by_sev.get(sev) or []
        if not items:
            continue
        for f in items:
            metric = f.get("metric_key") or f.get("finding_id") or "?"
            recommendation = (f.get("recommendation") or "").strip()
            runbook = (f.get("runbook_command") or "").strip()
            line = f"- **[{sev.upper()}] `{metric}`** — {recommendation}"
            lines.append(line)
            if runbook:
                # Truncate long runbooks; full text is in the finding's
                # observed_value on the live endpoint / System Health panel.
                truncated = runbook if len(runbook) <= 200 else runbook[:200] + "..."
                lines.append(f"  - Runbook: `{truncated}`")
    return "\n".join(lines)


def build_briefing_block() -> str:
    """Return the watchdog block as markdown, or "" on kill switch /
    nothing to report. Never raises."""
    if not _enabled():
        logger.info("watchdog briefing block disabled via UA_BRIEFING_WATCHDOG_BLOCK_ENABLED=0")
        return ""

    try:
        payload = _fetch_current_findings()
    except Exception as exc:  # noqa: BLE001 — defensive belt-and-suspenders
        logger.warning("watchdog briefing: top-level failure: %s", exc, exc_info=True)
        return ""

    overall_status = (payload or {}).get("overall_status") or "unknown"
    findings = (payload or {}).get("invariants") or []
    stale_count = ((payload or {}).get("stale_tasks") or {}).get("count") or 0
    parked_count = ((payload or {}).get("parked_tasks") or {}).get("count") or 0

    findings_md = _render_findings(findings)

    # Criticals that paged in-window (since the last digest) but recovered before
    # this render leave no trace in the live endpoint above — so a clean live
    # state would otherwise read "no critical alerts" even though the night was
    # not clean. Reconstruct them from the durable digest-cooldown columns on the
    # singleton proactive_health snapshot. Best-effort: never breaks the briefing.
    current_critical_ids = {
        str(f.get("finding_id") or f.get("metric_key") or "")
        for f in findings
        if str(f.get("severity") or "").lower() == "critical"
    }
    current_critical_ids.discard("")
    recovered_md = ""
    snapshot = _read_latest_snapshot_safe()
    if snapshot:
        recovered_md = render_in_window_recovered_criticals(
            current_critical_ids=current_critical_ids,
            last_digest_fingerprint=snapshot.get("last_digest_fingerprint"),
            last_digest_sent_at_utc=snapshot.get("last_digest_sent_at_utc"),
        )

    # If absolutely nothing to report (healthy state, no active findings, and no
    # in-window-recovered criticals), return "" so the briefing isn't padded.
    if not findings_md and not recovered_md and overall_status in ("ok", "unknown"):
        return ""

    block: list[str] = []
    block.append(f"## Watchdog Status — overall: {overall_status}")
    block.append("")
    block.append(
        f"_Layer 1: {stale_count} stale in-progress task(s), {parked_count} parked work item(s). "
        "Source: live `/api/v1/ops/proactive_health` (also on the Mission Control **System Health** panel)._"
    )

    if findings_md:
        block.append("")
        block.append("### Active alerts (current heartbeat)")
        block.append(findings_md)

    if recovered_md:
        block.append("")
        block.append(recovered_md)

    return "\n".join(block)
