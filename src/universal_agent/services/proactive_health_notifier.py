"""Proactive-health email notifier: critical-finding delivery to the operator.

The live send path is ``send_critical_digest`` — ONE digest email covering all
current critical invariants — driven by the deploy-independent systemd timer
(``services/proactive_health_timer_main.py``, S5 Phase C), which owns the
durable 6h cooldown. ``send_test_critical_email`` is the operator's manual
end-to-end verification ping (the ``/api/v1/ops/proactive_health/email_test``
endpoint). Both share the mailer-acquire helpers
(``_acquire_agentmail_service`` / ``_construct_started_agentmail_service``),
which resolve the gateway's AgentMailService or stand up a fresh short-lived one
in the oneshot subprocess (AgentMail-primary, gws/Gmail HTTP-429 fallback).

History: this module used to host an in-process heartbeat pre-flight
(``run_pre_flight_check``) that emailed one message per finding every tick. That
compute moved to the timer in S5 Phase C (heartbeat skip-modes silenced it) and
the pre-flight was retired; the heartbeat now only READS the durable snapshot.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from typing import Any, Iterable, Optional, Protocol
import urllib.parse

from universal_agent.services.email_tags import ActionTag, KindTag

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_SECONDS = 21600  # 6h
KEVIN_EMAIL = "kevinjdragan@gmail.com"

# Per-finding "Acknowledge" links embedded in the digest email. 14 days —
# long enough that a link in an unread email still works, short enough that a
# leaked link eventually dies.
FINDING_ACK_TTL_SECONDS = 14 * 86400
ACK_ROUTE_PATH = "/api/v1/proactive_health/ack"


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


# ─── Digest send path (S5 Phase C — deploy-independent systemd timer) ─────────
# The retired in-process heartbeat pre-flight sent ONE email per critical
# finding, deduped against the gateway's in-memory _notifications cache. The
# deploy-independent timer (universal-agent-proactive-health.service) runs in a
# fresh oneshot subprocess with no such cache, so it (a) collapses all current
# criticals into a SINGLE digest email and (b) keys its 6h cooldown on the
# durable snapshot's finding-set fingerprint (see
# services/proactive_health_snapshot.py). This function just sends; the caller
# owns the cooldown decision.


# ── Per-finding "Acknowledge" links (suppress-until-recovered) ────────────────
# HMAC-is-the-auth GET links, mirroring cron_artifact_notifier's ack URLs but
# with the '{exp}.{sig}' TTL token shape from youtube_oauth_health
# (mint_signed_param / check_signed_param). The gateway endpoint
# (GET /api/v1/proactive_health/ack) verifies the token and records the ack in
# proactive_health_snapshot.record_ack; the timer then filters acked finding-ids
# out of the digest until the finding recovers (see reconcile_acks).


def _finding_ack_secret() -> bytes:
    """Same secret material/precedence as cron_artifact_notifier._ack_secret."""
    from universal_agent.services.cron_artifact_notifier import _ack_secret

    return _ack_secret()


def sign_finding_ack_token(
    finding_id: str, *, ttl_seconds: int = FINDING_ACK_TTL_SECONDS
) -> str:
    """Return ``"{expires_epoch}.{hex_sig}"`` over ``f"ph_ack:{finding_id}:{exp}"``.

    Returns the empty string when no signing secret is configured or the
    finding_id is blank — the caller MUST then omit the ack line entirely
    (never print a dead link in the email).
    """
    secret = _finding_ack_secret()
    fid = (finding_id or "").strip()
    if not secret or not fid:
        return ""
    exp = int(datetime.now(timezone.utc).timestamp()) + int(ttl_seconds)
    payload = f"ph_ack:{fid}:{exp}".encode("utf-8")
    sig = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def verify_finding_ack_token(finding_id: str, token: str) -> bool:
    """Validate a :func:`sign_finding_ack_token` token (TTL + signature)."""
    secret = _finding_ack_secret()
    fid = (finding_id or "").strip()
    value = (token or "").strip()
    if not secret or not fid or not value or "." not in value:
        return False
    exp_str, _, sig = value.partition(".")
    try:
        exp = int(exp_str)
    except ValueError:
        return False
    if exp < int(datetime.now(timezone.utc).timestamp()):
        return False  # expired
    payload = f"ph_ack:{fid}:{exp}".encode("utf-8")
    expected = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig.strip())


def _build_finding_ack_url(finding_id: str) -> str:
    """Operator-facing ack URL, or "" when no secret/base is available."""
    base = (
        os.getenv("FRONTEND_URL", "")
        or os.getenv("UA_PUBLIC_BASE_URL", "")
        or "https://app.clearspringcg.com"
    ).strip().rstrip("/")
    if not base:
        return ""
    token = sign_finding_ack_token(finding_id)
    if not token:
        return ""
    return (
        f"{base}{ACK_ROUTE_PATH}"
        f"?f={urllib.parse.quote(finding_id, safe='')}&t={token}"
    )


def _format_digest_email(
    criticals: list[dict[str, Any]], generated_at: str
) -> tuple[str, str]:
    n = len(criticals)
    plural = "s" if n != 1 else ""
    subject = f"[Proactive Health] {n} critical finding{plural}"
    lines = [
        f"{n} critical proactive_health invariant{plural} firing as of {generated_at}.",
        "",
    ]
    any_ack_url = False
    for idx, finding in enumerate(criticals, 1):
        title = finding.get("title") or "Proactive health finding"
        metric_key = finding.get("metric_key") or "?"
        recommendation = (finding.get("recommendation") or "").strip()
        runbook = (finding.get("runbook_command") or "").strip() or "(no runbook provided)"
        observed = finding.get("observed_value")
        observed_str = (
            json.dumps(observed, default=str) if observed is not None else "(none)"
        )
        lines.append(f"{idx}. {title}  [metric_key={metric_key}]")
        if recommendation:
            lines.append(f"   What's wrong: {recommendation}")
        lines.append(f"   Observed: {observed_str}")
        lines.append(f"   Runbook: {runbook}")
        finding_id = str(
            finding.get("finding_id") or finding.get("metric_key") or ""
        ).strip()
        ack_url = _build_finding_ack_url(finding_id) if finding_id else ""
        if ack_url:
            # Omitted entirely when no signing secret resolves — never print
            # a dead link.
            any_ack_url = True
            lines.append(f"   Acknowledge (mute until recovered): {ack_url}")
        lines.append("")
    lines.append(
        f"You will not be re-notified about this same finding-set for "
        f"{_cooldown_seconds() // 3600}h (a new or changed critical resets the "
        f"window). Live state: GET /api/v1/ops/proactive_health."
    )
    if any_ack_url:
        lines.append(
            "Acknowledge = mute that finding until it RECOVERS (stays green "
            "long enough), not a timed snooze: while it keeps firing you stay "
            "muted, and a NEW red after recovery emails again immediately. "
            "Ack links are valid 14 days."
        )
    lines.append("")
    lines.append("— Proactive Health Watchdog (systemd timer)")
    return subject, "\n".join(lines)


async def send_critical_digest(
    *,
    criticals: list[dict[str, Any]],
    generated_at: str,
    agentmail_service: Optional[_AgentMailLike] = None,
) -> dict[str, Any]:
    """Send ONE digest email covering all current critical findings.

    Used by the deploy-independent proactive_health systemd timer (S5 Phase C).
    Cooldown/dedup is the CALLER's responsibility (the timer keys it on the
    durable snapshot's finding-set fingerprint) — given a non-empty
    ``criticals`` list this always attempts a send.

    Acquires a mailer when none is passed (a fresh AgentMailService in the
    oneshot subprocess — AgentMail-primary with the built-in gws/HTTP-429
    fallback) and shuts down anything it owns in a ``finally``. Never raises.
    """
    if not criticals:
        return {"sent": False, "reason": "no_criticals"}

    owns_agentmail = False
    if agentmail_service is None:
        agentmail_service, owns_agentmail = await _acquire_agentmail_service()
    if agentmail_service is None:
        logger.warning(
            "proactive_health digest: no AgentMailService (disabled or init "
            "failed) — %d critical finding(s) NOT delivered",
            len(criticals),
        )
        return {"sent": False, "reason": "agentmail_service=None"}

    subject, text = _format_digest_email(criticals, generated_at)
    finding_ids = [
        str(f.get("finding_id") or f.get("metric_key") or "unknown") for f in criticals
    ]
    result: Any = None
    try:
        result = await agentmail_service.send_email(
            to=KEVIN_EMAIL,
            subject=subject,
            text=text,
            force_send=True,
            action=ActionTag.ACTION,
            kind=KindTag.INCIDENT,
            source="proactive_health_timer",
            related=[f"finding_id={fid}" for fid in finding_ids],
        )
    except Exception as exc:  # noqa: BLE001 — never crash the timer over a send
        logger.warning(
            "proactive_health digest: send_email failed (%s)",
            type(exc).__name__,
            exc_info=True,
        )
        return {"sent": False, "reason": f"{type(exc).__name__}: {exc}"}
    finally:
        if owns_agentmail and agentmail_service is not None:
            try:
                await agentmail_service.shutdown()
            except Exception:  # noqa: BLE001
                logger.debug(
                    "proactive_health digest: fresh AgentMailService shutdown failed",
                    exc_info=True,
                )

    message_id = result.get("message_id") if isinstance(result, dict) else None
    logger.info(
        "proactive_health digest: emailed %d critical finding(s) to %s (message_id=%s)",
        len(criticals),
        KEVIN_EMAIL,
        message_id,
    )
    return {
        "sent": True,
        "to": KEVIN_EMAIL,
        "subject": subject,
        "message_id": message_id,
        "finding_ids": finding_ids,
    }
