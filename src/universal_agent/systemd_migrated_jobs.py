"""Canonical registry of cron system-jobs migrated to deploy-independent systemd timers.

Extracted from ``gateway_server.py`` (2026-06-06, S5 Phase A follow-up) so that
non-gateway surfaces — e.g. the Mission Control Chief-of-Staff readout — can ask
"is this ``cron:<job>`` Task Hub row a stale relic of a job that now fires from a
systemd timer?" WITHOUT importing the gateway module (which builds the FastAPI
application at import time and registers crons).

When a job is migrated, its in-process cron registration is forced disabled so the
systemd timer is the SOLE firer (no double-fire). The global env escape hatch
``UA_SYSTEMD_TIMER_MIGRATION_DISABLED=1`` restores in-process firing for ALL
migrated jobs (emergency rollback without a redeploy — pair with disabling the
timers). This frozenset is the SOURCE OF TRUTH for "which cron jobs moved to
timers"; ``gateway_server`` re-exports it under its historical private names.
"""

from __future__ import annotations

import os

SYSTEMD_MIGRATED_SYSTEM_JOBS: frozenset[str] = frozenset(
    {
        # Batch 1 (#753) — low-blast-radius maintenance/audit jobs.
        "scratch_pruning",
        "vault_lint_contradictions",
        "architecture_canvas_drift",
        "insight_scoring_health",
        "vp_coder_workspace_pruning",
        # Batch 2 — content dailies. NOTE: most are gated via the
        # _register_system_cron_job(enabled=…) arg, BUT ``codie_proactive_cleanup``
        # registers through a bespoke _cron_service.add_job/update_job path, so its
        # disable lives directly in _ensure_codie_proactive_cleanup_cron_job (it
        # flips the existing row to disabled when migrated).
        "proactive_report_morning",
        "proactive_report_midday",
        "proactive_report_afternoon",
        "proactive_artifact_digest",
        "intel_auto_promoter",
        "codie_proactive_cleanup",
        # Batch 3 — hourly active-window producers. ``hourly_intel_digest`` is
        # gated via the _register_system_cron_job(enabled=…) arg; BUT
        # ``csi_convergence_sync`` registers through a bespoke
        # _cron_service.add_job/update_job path, so its disable lives directly in
        # _ensure_csi_convergence_cron_job, mirroring codie_proactive_cleanup.
        "hourly_intel_digest",
        "csi_convergence_sync",
        # Batch A4 — SECRET-BEARING jobs (YouTube OAuth tokens, NotebookLM cookies,
        # UA_OPS_TOKEN, Anthropic key). Highest-care batch: a botched
        # secret-bootstrap is a silent keyless prod failure, so each ExecStart
        # module is audited to call bare initialize_runtime_secrets() (honoring the
        # unit's UA_DEPLOYMENT_PROFILE=vps backstop). GATE MECHANISMS differ:
        # ``youtube_daily_digest`` and ``youtube_gold_channel_poller`` register
        # through a bespoke _cron_service.add_job/update_job path, so their disable
        # lives directly in their _ensure_* fns. The other five
        # (``youtube_oauth_watchdog``, ``nightly_wiki``, ``morning_briefing``,
        # ``evening_briefing``, ``csi_demo_triage_rank``) register via
        # _register_system_cron_job, so they AND `not is_migrated_to_systemd(..)`
        # into their enabled= arg. ``evening_briefing`` shares briefings_agent.py
        # with ``morning_briefing`` but uses a SEPARATE unit (--mode=evening).
        "youtube_daily_digest",
        "youtube_gold_channel_poller",
        "youtube_oauth_watchdog",
        "nightly_wiki",
        "morning_briefing",
        "evening_briefing",
        "csi_demo_triage_rank",
    }
)


def is_migrated_to_systemd(system_job: str) -> bool:
    """True when ``system_job`` is served by a deploy-independent systemd timer.

    When True, the job's in-process cron registration is forced disabled so the
    systemd timer is the sole firer (no double-fire). The global env escape hatch
    ``UA_SYSTEMD_TIMER_MIGRATION_DISABLED=1`` restores in-process firing for ALL
    migrated jobs (emergency rollback without a redeploy — pair with disabling the
    timers).
    """
    if os.getenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False
    return system_job in SYSTEMD_MIGRATED_SYSTEM_JOBS
