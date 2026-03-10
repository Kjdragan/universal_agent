from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _summary_line(kpis: dict[str, Any], *, label: str) -> str:
    parts = [f"{label} snapshot"]
    if "dispatch_eligible" in kpis:
        parts.append(f"dispatch eligible {kpis['dispatch_eligible']}")
    if "open_csi_incidents" in kpis:
        parts.append(f"open CSI incidents {kpis['open_csi_incidents']}")
    if "source_degraded" in kpis:
        parts.append(f"degraded CSI sources {kpis['source_degraded']}")
    return " | ".join(parts)


def build_factory_snapshot(
    *,
    capabilities: dict[str, Any],
    registrations: list[dict[str, Any]],
    delegation_history: list[dict[str, Any]],
    todolist_overview: dict[str, Any],
    agent_queue: list[dict[str, Any]],
    dispatch_queue: list[dict[str, Any]],
    events: list[dict[str, Any]],
    timers: list[dict[str, Any]],
) -> dict[str, Any]:
    queue_health = _as_dict(todolist_overview.get("queue_health"))
    csi_summary = _as_dict(todolist_overview.get("csi_incident_summary"))
    agent_activity = _as_dict(todolist_overview.get("agent_activity"))
    heartbeat = _as_dict(todolist_overview.get("heartbeat"))

    dispatch_eligible = _safe_int(queue_health.get("dispatch_eligible"))
    open_csi_incidents = _safe_int(csi_summary.get("open_incidents"))
    backlog_open = _safe_int(agent_activity.get("backlog_open"))

    status_counts = _as_dict(queue_health.get("status_counts"))
    source_counts = _as_dict(queue_health.get("source_counts"))

    stale_count = 0
    offline_count = 0
    for row in registrations:
        status = str(_as_dict(row).get("registration_status") or "").strip().lower()
        if status == "stale":
            stale_count += 1
        elif status == "offline":
            offline_count += 1

    critical_conditions = [
        offline_count > 0,
        dispatch_eligible >= 40,
        backlog_open >= 80,
    ]
    warning_conditions = [
        stale_count > 0,
        open_csi_incidents >= 8,
        dispatch_eligible >= 15,
        backlog_open >= 30,
    ]

    severity = "critical" if any(critical_conditions) else "warning" if any(warning_conditions) else "info"

    recommendations: list[dict[str, Any]] = []
    if stale_count or offline_count:
        recommendations.append(
            {
                "action": "Review fleet liveness and delegation connectivity.",
                "rationale": (
                    f"Factories stale={stale_count}, offline={offline_count}. "
                    "Mission routing confidence drops when workers drift."
                ),
                "endpoint_or_command": "GET /api/v1/factory/registrations?limit=500",
                "requires_confirmation": False,
            }
        )
    if dispatch_eligible >= 15:
        recommendations.append(
            {
                "action": "Triage dispatch queue pressure and constrain low-value CSI mirroring.",
                "rationale": f"Dispatch eligible queue is {dispatch_eligible}; sustained pressure can delay execution.",
                "endpoint_or_command": "GET /api/v1/dashboard/todolist/dispatch-queue?limit=200",
                "requires_confirmation": False,
            }
        )
    if open_csi_incidents >= 8:
        recommendations.append(
            {
                "action": "Tune CSI TaskHub routing policy to reduce non-actionable incident noise.",
                "rationale": f"Open CSI incidents currently {open_csi_incidents}.",
                "endpoint_or_command": "GET /api/v1/dashboard/todolist/agent-queue?include_csi=true&collapse_csi=true",
                "requires_confirmation": False,
            }
        )
    effective_every = _safe_int(heartbeat.get("effective_default_every_seconds"))
    if effective_every and effective_every > 900:
        recommendations.append(
            {
                "action": "Shorten heartbeat interval for faster fleet visibility.",
                "rationale": f"Effective heartbeat interval is {effective_every}s.",
                "endpoint_or_command": "UA_HEARTBEAT_INTERVAL / UA_HEARTBEAT_MIN_INTERVAL_SECONDS",
                "requires_confirmation": True,
            }
        )

    diagnostics = {
        "posture": {
            "factory_role": capabilities.get("factory_role"),
            "gateway_mode": capabilities.get("gateway_mode"),
            "delegation_mode": capabilities.get("delegation_mode"),
            "heartbeat_scope": capabilities.get("heartbeat_scope"),
            "enable_csi_ingest": capabilities.get("enable_csi_ingest"),
            "enable_agentmail": capabilities.get("enable_agentmail"),
        },
        "fleet": {
            "registrations_total": len(registrations),
            "stale": stale_count,
            "offline": offline_count,
        },
        "flow": {
            "delegation_recent_total": len(delegation_history),
            "agent_queue_total": len(agent_queue),
            "dispatch_queue_total": len(dispatch_queue),
            "events_sampled": len(events),
            "timers_reported": len(timers),
        },
        "queue_health": {
            "status_counts": status_counts,
            "source_counts": source_counts,
            "dispatch_eligible": dispatch_eligible,
            "backlog_open": backlog_open,
            "open_csi_incidents": open_csi_incidents,
        },
        "heartbeat": heartbeat,
    }

    kpis = {
        "dispatch_eligible": dispatch_eligible,
        "backlog_open": backlog_open,
        "open_csi_incidents": open_csi_incidents,
        "registrations_total": len(registrations),
        "stale_factories": stale_count,
        "offline_factories": offline_count,
        "active_agents": _safe_int(agent_activity.get("active_agents")),
    }

    return {
        "status": "ok",
        "supervisor_id": "factory-supervisor",
        "generated_at": _iso_now(),
        "summary": _summary_line(kpis, label="Factory"),
        "severity": severity,
        "kpis": kpis,
        "diagnostics": diagnostics,
        "recommendations": recommendations,
        "artifacts": {
            "markdown_path": "",
            "json_path": "",
        },
    }


def build_csi_snapshot(
    *,
    csi_health: dict[str, Any],
    csi_delivery_health: dict[str, Any],
    csi_reliability_slo: dict[str, Any],
    csi_specialist_loops: list[dict[str, Any]],
    csi_opportunities: dict[str, Any],
    agent_queue: list[dict[str, Any]],
    todolist_overview: dict[str, Any],
    csi_events: list[dict[str, Any]],
) -> dict[str, Any]:
    source_health = _as_list(csi_health.get("source_health"))
    degraded_sources = [
        row for row in source_health
        if str(_as_dict(row).get("status") or "").strip().lower() in {"degraded", "stale", "failing"}
    ]

    undelivered = _safe_int(csi_health.get("undelivered_last_24h"))
    dlq = _safe_int(csi_health.get("dead_letter_last_24h"))

    delivery_rollup = _as_dict(csi_delivery_health.get("rollup"))
    delivery_status = str(delivery_rollup.get("status") or csi_delivery_health.get("status") or "unknown")

    slo = _as_dict(csi_reliability_slo.get("slo"))
    slo_status = str(slo.get("status") or "unknown").strip().lower()

    loops_open = 0
    loops_suppressed = 0
    for row in csi_specialist_loops:
        status = str(_as_dict(row).get("status") or "").strip().lower()
        if status == "open":
            loops_open += 1
        elif status == "suppressed_low_signal":
            loops_suppressed += 1

    opportunities_latest = _as_dict(csi_opportunities.get("latest"))
    opportunities_count = len(_as_list(opportunities_latest.get("opportunities")))

    queue_health = _as_dict(todolist_overview.get("queue_health"))
    source_counts = _as_dict(queue_health.get("source_counts"))
    csi_task_count = _safe_int(source_counts.get("csi"))

    critical_conditions = [
        delivery_status == "degraded",
        slo_status in {"breached", "failing", "critical"},
        dlq > 0,
    ]
    warning_conditions = [
        len(degraded_sources) > 0,
        undelivered > 0,
        loops_open >= 8,
        csi_task_count >= 20,
    ]
    severity = "critical" if any(critical_conditions) else "warning" if any(warning_conditions) else "info"

    recommendations: list[dict[str, Any]] = []
    if len(degraded_sources) > 0:
        recommendations.append(
            {
                "action": "Investigate degraded CSI sources and adapter health.",
                "rationale": f"Degraded/stale source count: {len(degraded_sources)}.",
                "endpoint_or_command": "GET /api/v1/dashboard/csi/health",
                "requires_confirmation": False,
            }
        )
    if dlq > 0 or undelivered > 0:
        recommendations.append(
            {
                "action": "Review CSI delivery failure path and retry posture.",
                "rationale": f"Undelivered={undelivered}, dead_letter={dlq} in last 24h.",
                "endpoint_or_command": "GET /api/v1/dashboard/csi/delivery-health",
                "requires_confirmation": False,
            }
        )
    if csi_task_count >= 20:
        recommendations.append(
            {
                "action": "Reduce CSI-to-TaskHub conversion for low-value signals.",
                "rationale": f"Open CSI task footprint is {csi_task_count} in Task Hub.",
                "endpoint_or_command": "GET /api/v1/dashboard/todolist/agent-queue?include_csi=true&collapse_csi=true",
                "requires_confirmation": True,
            }
        )
    if loops_open >= 8:
        recommendations.append(
            {
                "action": "Triage CSI specialist loop backlog and suppression thresholds.",
                "rationale": f"Open specialist loops={loops_open}, suppressed={loops_suppressed}.",
                "endpoint_or_command": "GET /api/v1/dashboard/csi/specialist-loops?limit=50",
                "requires_confirmation": False,
            }
        )

    diagnostics = {
        "delivery": {
            "status": delivery_status,
            "undelivered_last_24h": undelivered,
            "dead_letter_last_24h": dlq,
            "rollup": delivery_rollup,
        },
        "reliability_slo": {
            "status": slo_status,
            "detail": slo.get("detail"),
            "last_checked_at": slo.get("last_checked_at"),
            "metrics": _as_dict(slo.get("metrics")),
            "thresholds": _as_dict(slo.get("thresholds")),
        },
        "source_health": {
            "total_sources": len(source_health),
            "degraded_sources": len(degraded_sources),
            "sample": degraded_sources[:10],
        },
        "loops": {
            "total": len(csi_specialist_loops),
            "open": loops_open,
            "suppressed_low_signal": loops_suppressed,
        },
        "flow": {
            "csi_taskhub_open_items": csi_task_count,
            "agent_queue_items": len(agent_queue),
            "events_sampled": len(csi_events),
            "latest_opportunity_count": opportunities_count,
        },
    }

    kpis = {
        "source_total": len(source_health),
        "source_degraded": len(degraded_sources),
        "undelivered_last_24h": undelivered,
        "dead_letter_last_24h": dlq,
        "loops_open": loops_open,
        "loops_suppressed": loops_suppressed,
        "csi_taskhub_open_items": csi_task_count,
        "latest_opportunity_count": opportunities_count,
    }

    return {
        "status": "ok",
        "supervisor_id": "csi-supervisor",
        "generated_at": _iso_now(),
        "summary": _summary_line(kpis, label="CSI"),
        "severity": severity,
        "kpis": kpis,
        "diagnostics": diagnostics,
        "recommendations": recommendations,
        "artifacts": {
            "markdown_path": "",
            "json_path": "",
        },
    }
