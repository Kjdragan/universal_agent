"""Pipeline invariants — Layer 2 of the proactive activity watchdog.

Process-liveness checks (cron last-run, stale claims) catch trains that never
left the station.  They do NOT catch trains that left on time with empty cargo
— the failure mode the operator hit when youtube_daily_digest exited cleanly
but every card landed with transcript_status='missing'.

This module lets each pipeline owner declare a small post-condition probe:
"if my pipeline succeeded, the following must be true about the data I left
behind."  The runner executes every registered probe each heartbeat, catches
exceptions, and emits HeartbeatFinding objects under category='proactive_health'.

Design:
- Probes are pure callables that receive a context dict (runtime_conn,
  csi_db_path, ...) so different invariants can hit different stores without
  the runner needing to know about each one.
- Probe returning None  → invariant holds, no finding.
- Probe returning dict  → anomaly, finding emitted using the dict to populate
  observed_value / message / metadata.
- Probe raising         → severity='warn' "probe_error" finding (never crash
  the watchdog).

Importing this module is side-effect free.  Built-in invariants register
themselves when their submodules under `universal_agent.services.invariants`
are imported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from universal_agent.utils.heartbeat_findings_schema import HeartbeatFinding

logger = logging.getLogger(__name__)

ProbeResult = Optional[Dict[str, Any]]
ProbeFn = Callable[[Dict[str, Any]], ProbeResult]

CATEGORY = "proactive_health"
_VALID_SEVERITIES = {"warn", "critical"}


@dataclass
class Invariant:
    """A single declared post-condition for a pipeline."""

    id: str
    title: str
    description: str
    severity: str  # "warn" or "critical"
    probe: ProbeFn
    runbook_command: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in _VALID_SEVERITIES:
            raise ValueError(
                f"Invariant {self.id!r}: severity must be one of "
                f"{sorted(_VALID_SEVERITIES)}, got {self.severity!r}"
            )
        if not self.id:
            raise ValueError("Invariant.id must be non-empty")


_REGISTRY: Dict[str, Invariant] = {}


def register_invariant(invariant: Invariant) -> Invariant:
    """Add an invariant to the registry. Replaces any prior entry with the same id."""
    _REGISTRY[invariant.id] = invariant
    return invariant


def invariant(
    *,
    id: str,
    title: str,
    description: str,
    severity: str = "warn",
    runbook_command: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Callable[[ProbeFn], ProbeFn]:
    """Decorator form of register_invariant.

    Example:
        @invariant(id="my_check", title="...", description="...", severity="warn")
        def _probe(ctx):
            if bad_thing:
                return {"observed_value": ..., "message": "..."}
            return None
    """

    def _wrap(probe: ProbeFn) -> ProbeFn:
        register_invariant(
            Invariant(
                id=id,
                title=title,
                description=description,
                severity=severity,
                probe=probe,
                runbook_command=runbook_command,
                metadata=dict(metadata or {}),
            )
        )
        return probe

    return _wrap


def get_registered_invariants() -> List[Invariant]:
    """Return a list snapshot of registered invariants (id-sorted, stable order)."""
    return [_REGISTRY[k] for k in sorted(_REGISTRY.keys())]


def clear_registry_for_tests() -> None:
    """Test-only helper. Production code must not call this."""
    _REGISTRY.clear()


def _build_finding(
    inv: Invariant,
    result: Dict[str, Any],
) -> HeartbeatFinding:
    observed = result.get("observed_value")
    message = str(result.get("message") or inv.description)
    threshold_text = str(result.get("threshold_text") or "")
    extra_metadata = dict(inv.metadata)
    extra_metadata.update(result.get("metadata") or {})
    extra_metadata["invariant_id"] = inv.id
    # P4 (2026-05-20): allow a probe to override the declared severity
    # per-finding (e.g. zai_inference_health declares critical for the
    # FUP / sustained-429 cases but downgrades to warn when only the
    # process-count condition fires). Probes that don't set
    # `severity_override` keep the static value from the decorator.
    severity = result.get("severity_override") or inv.severity
    if severity not in _VALID_SEVERITIES:
        severity = inv.severity
    return HeartbeatFinding(
        finding_id=f"invariant:{inv.id}",
        category=CATEGORY,
        severity=severity,
        metric_key=inv.id,
        observed_value=observed,
        threshold_text=threshold_text,
        known_rule_match=True,
        confidence="high",
        title=inv.title,
        recommendation=message,
        runbook_command=inv.runbook_command,
        metadata=extra_metadata,
    )


def _build_probe_error_finding(
    inv: Invariant,
    exc: BaseException,
    elapsed_ms: int,
) -> HeartbeatFinding:
    return HeartbeatFinding(
        finding_id=f"invariant:{inv.id}:probe_error",
        category=CATEGORY,
        severity="warn",
        metric_key=f"{inv.id}_probe_error",
        observed_value=f"{type(exc).__name__}: {exc}",
        threshold_text="",
        known_rule_match=False,
        confidence="medium",
        title=f"Invariant probe error: {inv.title}",
        recommendation=(
            f"Probe for invariant {inv.id!r} raised {type(exc).__name__}; "
            "investigate the probe implementation or its data source."
        ),
        runbook_command=inv.runbook_command,
        metadata={
            "invariant_id": inv.id,
            "probe_elapsed_ms": elapsed_ms,
            "exception_type": type(exc).__name__,
        },
    )


def run_invariants(context: Optional[Dict[str, Any]] = None) -> List[HeartbeatFinding]:
    """Execute every registered invariant probe and return emitted findings.

    A probe is OK (returns None) → no finding emitted.
    A probe is anomalous (returns dict) → one finding emitted.
    A probe raises → one "probe_error" finding emitted; runner keeps going.
    """
    ctx: Dict[str, Any] = dict(context or {})
    findings: List[HeartbeatFinding] = []
    for inv in get_registered_invariants():
        started = time.monotonic()
        try:
            result = inv.probe(ctx)
        except Exception as exc:  # noqa: BLE001 — runner must never crash
            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.warning(
                "Invariant %s probe raised %s",
                inv.id,
                type(exc).__name__,
                exc_info=True,
            )
            findings.append(_build_probe_error_finding(inv, exc, elapsed_ms))
            continue
        if result is None:
            continue
        if not isinstance(result, dict):
            logger.warning(
                "Invariant %s probe returned %s; expected None or dict — ignoring",
                inv.id,
                type(result).__name__,
            )
            continue
        findings.append(_build_finding(inv, result))
    return findings
