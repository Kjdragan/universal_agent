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

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Severity labels for renderer; ordered worst-first so the block leads
# with the most actionable rows.
_SEVERITY_ORDER = ("critical", "warn", "info")


def _enabled() -> bool:
    return (os.getenv("UA_BRIEFING_WATCHDOG_BLOCK_ENABLED") or "1") != "0"


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

    # If absolutely nothing to report (healthy state + no active findings),
    # return "" so the briefing isn't padded with noise.
    if not findings_md and overall_status in ("ok", "unknown"):
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

    return "\n".join(block)
