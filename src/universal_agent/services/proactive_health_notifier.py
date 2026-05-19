"""Heartbeat pre-flight that always runs the proactive watchdog.

Background: the prior wiring lived in `memory/HEARTBEAT.md` as a directive for
Simone to call `GET /api/v1/ops/proactive_health` each cycle. That directive
sits inside the agent prompt, so it never runs when the heartbeat takes the
skip-mode or quick-heartbeat short-circuit. Two consequences:

1. The endpoint exists and works, but Simone never called it in production.
2. Even if she had, the directive only wrote findings to a JSON file — it
   never emailed Kevin.

This module fixes both. It runs as code, not as an agent directive, so it
fires every tick regardless of skip-mode. For critical invariants it sends
Kevin one email per finding_id with a 6h cooldown so a stuck pipeline
doesn't spam the inbox.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Awaitable, Callable, Iterable, Optional, Protocol

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_SECONDS = 21600  # 6h
KEVIN_EMAIL = "kevinjdragan@gmail.com"
NOTIFICATION_KIND_PREFIX = "proactive_health_critical"


def _truthy(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _enabled() -> bool:
    return _truthy(os.getenv("UA_HEARTBEAT_PROACTIVE_HEALTH_ENABLED"), True)


def _email_enabled() -> bool:
    return _truthy(os.getenv("UA_HEARTBEAT_PROACTIVE_HEALTH_EMAIL_CRITICAL"), True)


def _cooldown_seconds() -> int:
    raw = os.getenv("UA_HEARTBEAT_PROACTIVE_HEALTH_COOLDOWN_SECONDS")
    if not raw:
        return DEFAULT_COOLDOWN_SECONDS
    try:
        return max(60, int(raw))
    except ValueError:
        return DEFAULT_COOLDOWN_SECONDS


class _AgentMailLike(Protocol):
    async def send_email(
        self, *, to: str, subject: str, text: str, force_send: bool = False, **kwargs: Any
    ) -> dict[str, Any]: ...


def _parse_iso_timestamp(value: Any) -> Optional[float]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        # tolerate Z suffix
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def _within_cooldown(
    *,
    kind: str,
    cooldown_seconds: int,
    notifications_list: Iterable[dict[str, Any]],
) -> bool:
    """Return True if a notification of this kind was emitted within the cooldown.

    Walks the in-memory _notifications cache (the same one populated by
    gateway_server._add_notification). Treats either `updated_at` or
    `created_at` as the recency marker.
    """
    now = time.time()
    for n in notifications_list or ():
        if str(n.get("kind") or "") != kind:
            continue
        ts = _parse_iso_timestamp(n.get("updated_at") or n.get("created_at"))
        if ts is None:
            continue
        if now - ts < cooldown_seconds:
            return True
    return False


def _write_sidecar(workspace_dir: Path, payload: dict[str, Any]) -> None:
    out_dir = workspace_dir / "work_products"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_dir.joinpath("proactive_health_latest.json").write_text(
            json.dumps(payload, indent=2, default=str)
        )
    except Exception:  # noqa: BLE001 — best-effort artifact write
        logger.warning("proactive_health: sidecar write failed", exc_info=True)


def _critical_invariants(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        f
        for f in (payload.get("invariants") or [])
        if str(f.get("severity") or "").lower() == "critical"
    ]


def _format_email(finding: dict[str, Any], payload_generated_at: str) -> tuple[str, str]:
    title = finding.get("title") or "Proactive health finding"
    metric_key = finding.get("metric_key") or "?"
    recommendation = finding.get("recommendation") or ""
    runbook = finding.get("runbook_command") or "(no runbook provided)"
    observed = finding.get("observed_value")
    observed_str = json.dumps(observed, indent=2, default=str) if observed is not None else "(none)"

    subject = f"[Proactive Health CRITICAL] {title}"
    text = (
        f"Critical proactive_health invariant fired.\n"
        f"\n"
        f"metric_key: {metric_key}\n"
        f"generated_at: {payload_generated_at}\n"
        f"\n"
        f"What's wrong:\n"
        f"  {recommendation}\n"
        f"\n"
        f"Observed:\n"
        f"{observed_str}\n"
        f"\n"
        f"Runbook (run this on the VPS to investigate):\n"
        f"  {runbook}\n"
        f"\n"
        f"You will not be re-notified about this same finding for "
        f"{_cooldown_seconds() // 3600}h. To force a fresh alert, run:\n"
        f"  curl -X POST -H \"Authorization: Bearer $UA_OPS_TOKEN\" "
        f"http://127.0.0.1:8002/api/v1/ops/proactive_health\n"
        f"\n"
        f"— Proactive Health Watchdog\n"
    )
    return subject, text


async def _notify_critical(
    *,
    finding: dict[str, Any],
    payload_generated_at: str,
    agentmail_service: _AgentMailLike,
    notifications_list: list[dict[str, Any]],
    add_notification_fn: Callable[..., dict[str, Any]],
    cooldown_seconds: int,
) -> bool:
    finding_id = str(finding.get("finding_id") or finding.get("metric_key") or "unknown")
    kind = f"{NOTIFICATION_KIND_PREFIX}:{finding_id}"

    if _within_cooldown(
        kind=kind,
        cooldown_seconds=cooldown_seconds,
        notifications_list=notifications_list,
    ):
        logger.debug("proactive_health: suppressed (within %ds cooldown): %s", cooldown_seconds, kind)
        return False

    subject, text = _format_email(finding, payload_generated_at)
    try:
        await agentmail_service.send_email(
            to=KEVIN_EMAIL,
            subject=subject,
            text=text,
            force_send=True,
        )
    except Exception:  # noqa: BLE001 — never crash the heartbeat over a send failure
        logger.warning("proactive_health: send_email failed for %s", finding_id, exc_info=True)
        return False

    # Record for dedup tracking.
    try:
        add_notification_fn(
            kind=kind,
            title=finding.get("title") or "Proactive health finding",
            message=finding.get("recommendation") or "",
            summary=f"Critical proactive health finding emailed: {finding_id}",
            severity="critical",
            requires_action=True,
            metadata={
                "finding_id": finding_id,
                "metric_key": finding.get("metric_key"),
                "runbook_command": finding.get("runbook_command"),
                "category": finding.get("category"),
            },
        )
    except Exception:  # noqa: BLE001
        logger.warning("proactive_health: failed to record dedup notification", exc_info=True)
    return True


async def run_pre_flight_check(
    *,
    workspace_dir: Path,
    payload_builder: Callable[[], dict[str, Any]],
    agentmail_service: Optional[_AgentMailLike],
    notifications_list: Optional[list[dict[str, Any]]],
    add_notification_fn: Optional[Callable[..., dict[str, Any]]],
) -> dict[str, Any]:
    """Run the proactive watchdog as a pre-flight before any agent invocation.

    Always-on side of the heartbeat. Caller passes a `payload_builder` thunk
    that invokes `build_proactive_health_payload(...)` with whatever DB / cron
    state is locally available — this keeps the notifier free of import-time
    coupling to gateway_server.

    Returns the payload dict (with possibly an `error` field) so the caller
    can log it. Never raises.
    """
    if not _enabled():
        return {"status": "disabled"}

    try:
        payload = payload_builder()
    except Exception as exc:  # noqa: BLE001
        logger.warning("proactive_health: payload builder failed", exc_info=True)
        return {"error": f"payload_builder_failed: {type(exc).__name__}: {exc}"}

    _write_sidecar(workspace_dir, payload)

    if not _email_enabled() or agentmail_service is None or add_notification_fn is None:
        return payload

    cooldown = _cooldown_seconds()
    generated_at = str(payload.get("generated_at_utc") or datetime.now(timezone.utc).isoformat())
    notifications = notifications_list if notifications_list is not None else []

    sent_count = 0
    for finding in _critical_invariants(payload):
        sent = await _notify_critical(
            finding=finding,
            payload_generated_at=generated_at,
            agentmail_service=agentmail_service,
            notifications_list=notifications,
            add_notification_fn=add_notification_fn,
            cooldown_seconds=cooldown,
        )
        if sent:
            sent_count += 1

    if sent_count:
        logger.info("proactive_health: emailed %d new critical finding(s)", sent_count)
    return payload
