"""Resolve a per-process HEALTH verdict for each ZAI-activity unit.

The ZAI Control "Proactive activity controls" panel keys on systemd unit and shows
*operational* status (running/waiting). That answers "did the process run and exit
0?" — never "did the job actually produce its expected output?". The motivating bug:
``csi-quality-assessment`` ran green/exit-0 every night while grading an EMPTY
database. This module joins the activity registry to the deep
``proactive_health`` invariant signal so that class of silent failure surfaces.

Design — the verdict is the WORST of two independent signals, never an inference:

* **systemd baseline (the only source of GREEN).** For a long-running *service*:
  ``ActiveState``/``NRestarts`` (active=healthy, failed=critical, flapping=degraded).
  For a oneshot *timer*: the **sibling ``.service``**'s last-run ``Result`` /
  ``ExecMainStatus`` (success+0=healthy, non-success=critical, never-run=no_probe).
  Green is ONLY ever produced by a real last-run success — never by the absence of
  an invariant finding (the critical fix: pipeline invariants ``return None`` both
  when healthy AND when outside their active window, e.g. the reports trio is silent
  before 5 PM, so "no finding == healthy" would paint bright green at 3 AM).

* **invariant overlay (escalation only).** A mapped pipeline-invariant finding fires
  only on a *problem*; when present it escalates the row to ``degraded``/``critical``.
  It never produces green. Out-of-window probes emit nothing and so cannot false-green.

* **deep_probe flag.** Whether a deep "is it working" invariant covers this unit. A
  systemd-green row with ``deep_probe=False`` is "running but output unverified" — the
  monitoring blind spot. Surfacing it is the whole point of the column.

Pure module: ``resolve_activity_health`` takes already-built dicts (the
``build_proactive_health_payload`` output + a sibling-service systemd-props map) and
does no I/O, so it is unit-testable with fixtures.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# unit -> (invariant_id, sub_target_kind). sub_target_kind drills into an
# aggregated finding's observed_value so a shared invariant only escalates the
# specific row it concerns:
#   None          -> the finding (if present) applies to this unit directly
#   "trio_period:<period>"   -> escalate iff <period> in observed_value.periods_missing
#   "csi_source:<source>"    -> escalate iff observed_value.stale_sources[].source == <source>
# Every invariant_id here is a real @invariant(id=...) (verified against
# services/invariants/*.py); a typo would silently never escalate.
_UNIT_INVARIANT: Dict[str, Tuple[str, Optional[str]]] = {
    "universal-agent-csi-convergence-sync.timer": ("csi_convergence_sync_freshness", None),
    "universal-agent-morning-briefing.timer": ("morning_briefing_freshness", None),
    "universal-agent-proactive-report-morning.timer": ("proactive_reports_daily_trio", "trio_period:morning"),
    "universal-agent-proactive-report-midday.timer": ("proactive_reports_daily_trio", "trio_period:midday"),
    "universal-agent-proactive-report-afternoon.timer": ("proactive_reports_daily_trio", "trio_period:afternoon"),
    "universal-agent-nightly-wiki.timer": ("nightly_wiki_persistent_silence", None),
    "universal-agent-csi-demo-triage-rank.timer": ("csi_demo_triage_rank_artifact", None),
    "universal-agent-vault-lint-contradictions.timer": ("vault_lint_contradictions_monthly", None),
    "universal-agent-proactive-artifact-digest.timer": ("proactive_artifact_digest_delivery", None),
    "universal-agent-youtube-gold-channel-poller.timer": ("csi_source_liveness", "csi_source:youtube_channel_rss"),
    "universal-agent-youtube-playlist-poller.timer": ("csi_source_liveness", "csi_source:youtube_channel_rss"),
    "universal-agent-mission-control-sweeper.service": ("mission_control_sweeper_liveness", None),
    "universal-agent-vp-worker@vp.coder.primary.service": ("operator_daily_mission_freshness", None),
    "universal-agent-vp-worker@vp.general.primary.service": ("operator_daily_mission_freshness", None),
}

# Aggregate cron invariants whose observed_value lists per-job failures; any unit
# whose pipeline name appears there is escalated (catches a cron that fires-but-fails
# even when its dedicated artifact invariant has not tripped).
_CRON_AGGREGATE_IDS = ("cron_staleness", "cron_consecutive_failures")

# Severity rank for combining independent signals (higher wins).
_RANK = {"critical": 4, "degraded": 3, "healthy": 2, "no_probe": 1, "unknown": 0}
_SEV_TO_STATUS = {"critical": "critical", "warn": "degraded"}


def _to_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _clean(text: Any, limit: int = 220) -> str:
    """Collapse whitespace/newlines (recommendation/message can be multi-line) and
    truncate for a one-line tooltip."""
    s = " ".join(str(text or "").split())
    return s[:limit]


def _h(
    status: str,
    source: str,
    detail: str,
    *,
    deep_probe: bool,
    finding_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "status": status,          # healthy | degraded | critical | no_probe | unknown
        "source": source,          # invariant | systemd | none
        "detail": _clean(detail),
        "deep_probe": deep_probe,  # does a deep "is it working" invariant cover this unit
        "finding_id": finding_id,
    }


def _index_findings(invariants: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """metric_key -> finding dict. metric_key == the @invariant(id=...). Most-severe
    wins if a key appears twice (anomaly + probe_error variant)."""
    out: Dict[str, Dict[str, Any]] = {}
    for f in invariants or ():
        key = f.get("metric_key") or f.get("finding_id") or ""
        if not key:
            continue
        prev = out.get(key)
        if prev is None or (f.get("severity") == "critical" and prev.get("severity") != "critical"):
            out[key] = f
    return out


def _unit_to_pipeline(unit: str) -> str:
    """``universal-agent-csi-convergence-sync.timer`` -> ``csi_convergence_sync``
    (mirrors the cron job_id / invariant metadata.pipeline naming)."""
    stem = unit
    for suf in (".timer", ".service"):
        if stem.endswith(suf):
            stem = stem[: -len(suf)]
            break
    stem = stem.split("@", 1)[0]
    for pre in ("universal-agent-", "ua-"):
        if stem.startswith(pre):
            stem = stem[len(pre):]
            break
    return stem.replace("-", "_")


def _systemd_baseline(row: Dict[str, Any], sibling_props: Optional[Dict[str, str]]) -> Tuple[str, str]:
    """The ONLY source of a green verdict: did the unit's last real run succeed?
    Returns (status, detail). status in healthy|degraded|critical|no_probe|unknown."""
    if row.get("group") == "services":
        active = row.get("active_state") or "unknown"
        sub = row.get("sub_state") or "unknown"
        nrest = _to_int(row.get("n_restarts"))
        if active == "failed":
            return "critical", f"service failed (sub={sub})"
        if nrest >= 3:
            return "degraded", f"service restarted {nrest}x (flapping)"
        if active == "active":
            return "healthy", f"service active ({sub})"
        return "unknown", f"service state {active!r}"

    # timers: the last-run result lives on the TRIGGERED service, not the .timer.
    # NEVER-RAN detection MUST use ExecMainStartTimestampMonotonic — systemd
    # defaults Result=success/ExecMainStatus=0 for a loaded-but-never-run oneshot,
    # so keying on Result alone would paint a never-run timer bright green.
    props = sibling_props or {}
    started = str(props.get("ExecMainStartTimestampMonotonic") or "").strip()
    if started in ("", "0"):
        return "no_probe", "triggered service has not run this boot"
    result = props.get("Result") or ""
    exec_status = props.get("ExecMainStatus")
    if result == "success" and exec_status in (None, "", "0"):
        return "healthy", "last run exited 0"
    return "critical", f"last run {result or 'unknown'} (exit status {exec_status})"


def _sub_target_hit(finding: Dict[str, Any], sub_kind: Optional[str]) -> bool:
    """For a shared/aggregated finding, does it concern THIS row's sub-target?
    A finding with no sub-target requirement always applies."""
    if sub_kind is None:
        return True
    ov = finding.get("observed_value")
    if not isinstance(ov, dict):
        return False
    kind, _, value = sub_kind.partition(":")
    if kind == "trio_period":
        missing = ov.get("periods_missing")
        return isinstance(missing, list) and value in missing
    if kind == "csi_source":
        stale = ov.get("stale_sources")
        return isinstance(stale, list) and any(
            isinstance(s, dict) and s.get("source") == value for s in stale
        )
    return False


def _invariant_overlay(
    unit: str, findings: Dict[str, Dict[str, Any]]
) -> Optional[Tuple[str, str, str]]:
    """Escalation from a mapped deep invariant. Returns (status, detail, finding_id)
    only when a finding is FIRING for this unit; None otherwise (incl. the
    not-firing / out-of-window case — those never escalate and never paint green)."""
    mapping = _UNIT_INVARIANT.get(unit)
    if mapping is None:
        return None
    fid, sub_kind = mapping
    finding = findings.get(fid)
    if finding is not None:
        if not _sub_target_hit(finding, sub_kind):
            return None
        status = _SEV_TO_STATUS.get(finding.get("severity", "warn"), "degraded")
        detail = finding.get("recommendation") or finding.get("message") or finding.get("title") or fid
        return status, _clean(detail), fid
    # The probe itself crashed: its observed_value is a string, so _sub_target_hit
    # can't confirm the sub-target. A crashed probe cannot vouch for health, so
    # escalate every covered row unconditionally (probe_error severity is warn).
    perr = findings.get(f"{fid}_probe_error")
    if perr is not None:
        status = _SEV_TO_STATUS.get(perr.get("severity", "warn"), "degraded")
        detail = perr.get("recommendation") or perr.get("message") or perr.get("title") or f"{fid} probe failed"
        return status, _clean(detail), perr.get("metric_key") or f"{fid}_probe_error"
    return None


def _cron_overlay(
    pipeline: str, findings: Dict[str, Dict[str, Any]]
) -> Optional[Tuple[str, str, str]]:
    """Escalation if this unit's pipeline appears in a cron-aggregate finding's
    failing-job list (stale_crons[].job_id / streaks[].task_id, the latter
    'cron:'-prefixed)."""
    for fid in _CRON_AGGREGATE_IDS:
        finding = findings.get(fid)
        if not finding:
            continue
        ov = finding.get("observed_value")
        if not isinstance(ov, dict):
            continue
        for value in ov.values():
            if not isinstance(value, list):
                continue
            for item in value:
                if not isinstance(item, dict):
                    continue
                jid = item.get("job_id") or str(item.get("task_id", "")).removeprefix("cron:")
                if jid and jid == pipeline:
                    status = _SEV_TO_STATUS.get(finding.get("severity", "warn"), "degraded")
                    return status, _clean(f"cron '{pipeline}' flagged by {fid}"), fid
    return None


def resolve_activity_health(
    activities: List[Dict[str, Any]],
    health_payload: Dict[str, Any],
    sibling_service_props: Dict[str, Dict[str, str]],
) -> Dict[str, Dict[str, Any]]:
    """unit -> health dict for every activity row.

    ``health_payload`` = ``build_proactive_health_payload()`` output (findings under
    its ``"invariants"`` key). ``sibling_service_props`` = ``{sibling_service_unit:
    raw systemctl props}`` for timer rows (caller fetches via ``get_last_run_results``).
    Pure + total: every row gets a verdict; never raises on bad shapes."""
    findings = _index_findings(health_payload.get("invariants", []) if isinstance(health_payload, dict) else [])
    out: Dict[str, Dict[str, Any]] = {}

    for row in activities or ():
        if not isinstance(row, dict):
            continue
        unit = row.get("unit")
        if not unit:
            continue
        deep_probe = unit in _UNIT_INVARIANT
        pipeline = _unit_to_pipeline(unit)

        # systemd baseline (the only green source). For a timer, read the service
        # it actually triggers (the row's ``triggers``/Unit), NOT a by-name sibling
        # — the report timers all trigger one shared service.
        sib = None
        if str(unit).endswith(".timer"):
            triggered = row.get("triggers") or (str(unit)[:-6] + ".service")
            sib = sibling_service_props.get(triggered)
        base_status, base_detail = _systemd_baseline(row, sib)
        candidates: List[Tuple[str, str, str, Optional[str]]] = [
            (base_status, "systemd", base_detail, None)
        ]

        # invariant + cron escalations (problems only)
        inv = _invariant_overlay(unit, findings)
        if inv is not None:
            candidates.append((inv[0], "invariant", inv[1], inv[2]))
        cron = _cron_overlay(pipeline, findings)
        if cron is not None:
            candidates.append((cron[0], "invariant", cron[1], cron[2]))

        status, source, detail, finding_id = max(candidates, key=lambda c: _RANK.get(c[0], 0))
        out[unit] = _h(status, source, detail, deep_probe=deep_probe, finding_id=finding_id)

    return out


def inprocess_health(item: Dict[str, Any]) -> Dict[str, Any]:
    """Health verdict for an in-process loop row (heartbeat / cron). These have no
    systemd unit and no deep liveness probe in v1, so 'enabled' is operational-only:
    we deliberately mark it ``no_probe`` (running, output unverified) rather than
    paint it green — the same honesty the timer/service rows get."""
    enabled = bool(item.get("enabled"))
    if enabled:
        return _h("no_probe", "none",
                  f"{item.get('label', 'loop')} enabled; no deep liveness probe",
                  deep_probe=False)
    return _h("unknown", "none",
              f"{item.get('label', 'loop')} disabled ({item.get('env_var', '')})",
              deep_probe=False)
