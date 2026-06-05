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
import sys
import time
from typing import Any, Awaitable, Callable, Iterable, Optional, Protocol

from universal_agent.services.email_tags import ActionTag, KindTag

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_SECONDS = 21600  # 6h
KEVIN_EMAIL = "kevinjdragan@gmail.com"
NOTIFICATION_KIND_PREFIX = "proactive_health_critical"

# Module-level counter tracking consecutive skipped notifications per finding
# fingerprint. Lets operators grep journalctl for "consecutive=N" to spot
# long-running blocked email paths. Reset to 0 when a finding successfully
# notifies (or when the finding stops appearing in payloads — implicitly,
# since we only increment on observed skips).
_skipped_consecutive: dict[str, int] = {}


def _reset_skipped(fingerprint: str) -> None:
    """Drop a fingerprint from the consecutive-skip counter (called on send)."""
    _skipped_consecutive.pop(fingerprint, None)


def _bump_skipped(fingerprint: str) -> int:
    """Increment and return the consecutive-skip counter for a fingerprint."""
    _skipped_consecutive[fingerprint] = _skipped_consecutive.get(fingerprint, 0) + 1
    return _skipped_consecutive[fingerprint]


def _resolve_agentmail_service_via_gateway() -> Optional[Any]:
    """Resolve a live AgentMailService handle from the running gateway process.

    Two in-process lookups, cheapest first:

    1. ``sys.modules['__main__']`` — the gateway runs as
       ``python -m universal_agent.gateway_server`` so its module-level
       ``_agentmail_service`` assignment lands on the ``__main__`` module
       object. The ``importlib`` copy below is a *different*, pristine module
       object whose global is still None (the exact trap cron_service.py
       documents).
    2. ``importlib.import_module("universal_agent.gateway_server")`` — covers
       callers that hold the module by its dotted name.

    Returns None in a process that never ran the gateway lifespan (the daemon
    heartbeat subprocess) — callers escalate to
    ``_construct_started_agentmail_service`` for that case. Best-effort —
    never raises.
    """
    try:
        main_mod = sys.modules.get("__main__")
        if main_mod is not None:
            svc = getattr(main_mod, "_agentmail_service", None)
            if svc is not None:
                return svc
    except Exception:  # noqa: BLE001
        pass
    try:
        import importlib
        gs = importlib.import_module("universal_agent.gateway_server")
        return getattr(gs, "_agentmail_service", None)
    except Exception:  # noqa: BLE001
        return None


async def _construct_started_agentmail_service() -> Optional[Any]:
    """Stand up a fresh, started AgentMailService for a process with no
    gateway-injected handle (the daemon heartbeat subprocess, where
    ``gateway_server._agentmail_service`` is the pristine module-level None).

    Mirrors the one-shot cron mailers (scripts/insight_scoring_health.py,
    scripts/dependency_upgrade.py): construct → startup() → use → shutdown().
    The caller OWNS the returned service and MUST call ``await
    service.shutdown()``. Returns None when AgentMail is disabled or can't
    initialize (missing key / inbox error) so callers fall back to the existing
    graceful skip. Never raises.
    """
    try:
        from universal_agent.services.agentmail_service import AgentMailService

        svc = AgentMailService()
        await svc.startup()
        if getattr(svc, "_started", False):
            return svc
        # startup() short-circuited (disabled / missing AGENTMAIL_API_KEY /
        # inbox error) — close anything it opened and report "no mailer".
        try:
            await svc.shutdown()
        except Exception:  # noqa: BLE001
            pass
        return None
    except Exception:  # noqa: BLE001
        logger.warning(
            "proactive_health: failed to construct a fresh AgentMailService",
            exc_info=True,
        )
        return None


async def _acquire_agentmail_service() -> tuple[Optional[Any], bool]:
    """Resolve an existing AgentMail handle, or construct a fresh started one.

    Returns ``(service, owned)``. ``owned=True`` means we constructed it and the
    caller must shut it down; ``owned=False`` means it belongs to the gateway
    lifespan and must be left alone. Never raises.
    """
    resolved = _resolve_agentmail_service_via_gateway()
    if resolved is not None:
        return resolved, False
    svc = await _construct_started_agentmail_service()
    return svc, (svc is not None)


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
            action=ActionTag.ACTION,
            kind=KindTag.INCIDENT,
            source="proactive_health_notifier",
            related=[f"finding_id={finding_id}"],
        )
    except Exception:  # noqa: BLE001 — never crash the heartbeat over a send failure
        logger.warning("proactive_health: send_email failed for %s", finding_id, exc_info=True)
        return False

    # Email landed — reset the consecutive-skip counter for this fingerprint
    # so the next blocked tick starts counting from 1.
    _reset_skipped(finding_id)

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

    criticals_this_tick = _critical_invariants(payload)
    owns_agentmail = False

    # The daemon heartbeat runs in a subprocess where the gateway's
    # _agentmail_service global is the pristine module-level None — so the
    # injected handle is None here every tick. When there's actually a critical
    # finding to deliver, resolve a live handle or, as a last resort, stand up a
    # fresh short-lived AgentMailService (AgentMail-primary, with the built-in
    # gws/Gmail HTTP-429 fallback). Only pay the startup cost when there's
    # something to send; a fresh handle is owned here and shut down in finally.
    if (
        agentmail_service is None
        and _email_enabled()
        and add_notification_fn is not None
        and criticals_this_tick
    ):
        agentmail_service, owns_agentmail = await _acquire_agentmail_service()

    try:
        # If email is disabled OR plumbing still isn't ready, log explicitly per
        # critical finding so the operator can grep for blocked-email evidence.
        # This is the diagnostic fix for the 2026-05-20 race where the heartbeat
        # fired before gateway_server._agentmail_service was initialized.
        if not _email_enabled() or agentmail_service is None or add_notification_fn is None:
            for f in criticals_this_tick:
                fp = str(f.get("finding_id") or f.get("metric_key") or "unknown")
                consecutive = _bump_skipped(fp)
                if not _email_enabled():
                    reason = "email_disabled (UA_HEARTBEAT_PROACTIVE_HEALTH_EMAIL_CRITICAL=0)"
                elif agentmail_service is None and add_notification_fn is None:
                    reason = "agentmail_service=None AND add_notification_fn=None"
                elif agentmail_service is None:
                    reason = "agentmail_service=None (gateway race or init pending)"
                else:
                    reason = "add_notification_fn=None"
                logger.warning(
                    "proactive_health: notification SKIPPED — reason=%s, "
                    "fingerprint=%r, consecutive=%d",
                    reason,
                    fp,
                    consecutive,
                )
            return payload

        cooldown = _cooldown_seconds()
        generated_at = str(payload.get("generated_at_utc") or datetime.now(timezone.utc).isoformat())
        notifications = notifications_list if notifications_list is not None else []

        sent_count = 0
        for finding in criticals_this_tick:
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
    finally:
        if owns_agentmail and agentmail_service is not None:
            try:
                await agentmail_service.shutdown()
            except Exception:  # noqa: BLE001
                logger.debug(
                    "proactive_health: fresh AgentMailService shutdown failed",
                    exc_info=True,
                )


async def send_test_critical_email(
    *,
    agentmail_service: Optional[_AgentMailLike],
    note: str = "",
) -> dict[str, Any]:
    """Bypass-dedup synthetic critical-email send, for operator verification.

    Used by the POST /api/v1/ops/proactive_health/email_test endpoint. Builds
    a clearly-labeled fake finding, calls the real send path, returns a
    structured result so the endpoint can report what happened.

    Never raises — wraps the send in try/except and returns the exception
    name in the dict on failure.
    """
    owns_agentmail = False
    if agentmail_service is None:
        agentmail_service, owns_agentmail = await _acquire_agentmail_service()
    if agentmail_service is None:
        return {
            "sent": False,
            "reason": "agentmail_service=None (gateway init pending or disabled; fresh construct also failed)",
        }

    ts = datetime.now(timezone.utc).isoformat()
    finding = {
        "finding_id": f"manual_test_{int(time.time())}",
        "category": "proactive_health",
        "severity": "critical",
        "metric_key": "manual_test",
        "title": "[TEST] Manual proactive_health email verification",
        "recommendation": (
            "[TEST] This is a synthetic notification triggered via "
            "POST /api/v1/ops/proactive_health/email_test. If you received "
            "this, the watchdog's email path is working end-to-end. "
            + (f"Note: {note}" if note else "")
        ),
        "runbook_command": "(no runbook — this is a test ping)",
        "observed_value": {"trigger": "manual_test_endpoint", "timestamp": ts},
        "metadata": {"test": True},
    }
    subject, text = _format_email(finding, ts)
    try:
        await agentmail_service.send_email(
            to=KEVIN_EMAIL,
            subject=subject,
            text=text,
            force_send=True,
            action=ActionTag.FYI,
            kind=KindTag.INCIDENT,
            source="proactive_health_notifier (manual test)",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "proactive_health: test email failed (%s)", type(exc).__name__, exc_info=True
        )
        return {"sent": False, "reason": f"{type(exc).__name__}: {exc}"}
    finally:
        if owns_agentmail and agentmail_service is not None:
            try:
                await agentmail_service.shutdown()
            except Exception:  # noqa: BLE001
                pass

    logger.info("proactive_health: test email sent to %s", KEVIN_EMAIL)
    return {
        "sent": True,
        "to": KEVIN_EMAIL,
        "subject": subject,
        "finding_id": finding["finding_id"],
    }
