"""Guard tests for the S5 Phase A batch-2 systemd-timer migration (content dailies).

Pins the batch-2 contract so the timer substrate can't silently drift from the
in-process registry. Batch 2 has two shapes the batch-1 guard didn't:

  * the three proactive-report slots SHARE one
    ``universal-agent-proactive-report.service`` (driven by three timers); and
  * ``codie_proactive_cleanup`` registers via a bespoke
    ``_cron_service.add_job/update_job`` path (NOT ``_register_system_cron_job``),
    so its double-fire disable lives directly in
    ``_ensure_codie_proactive_cleanup_cron_job`` — behaviorally tested here.

Only the network/LLM jobs (report, digest) carry ``TimeoutStartSec``; the
pure-SQLite jobs (promoter, codie) keep the oneshot ``infinity`` default.
"""

from __future__ import annotations

from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from universal_agent import gateway_server

REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEMD_DIR = REPO_ROOT / "deployment" / "systemd"
INSTALLER = REPO_ROOT / "scripts" / "install_vps_phase_a_batch2_timers.sh"
REMOTE_DEPLOY = REPO_ROOT / "scripts" / "deploy" / "remote_deploy.sh"
GATEWAY_SRC = REPO_ROOT / "src" / "universal_agent" / "gateway_server.py"

BATCH1 = {
    "scratch_pruning",
    "vault_lint_contradictions",
    "architecture_canvas_drift",
    "insight_scoring_health",
    "vp_coder_workspace_pruning",
}

# system_job -> (timer_basename, service_basename, scripts module, TimeoutStartSec|None)
# TimeoutStartSec None == intentionally keeps the oneshot infinity default.
A2 = {
    "proactive_report_morning": (
        "universal-agent-proactive-report-morning",
        "universal-agent-proactive-report",
        "proactive_report_agent",
        600,
    ),
    "proactive_report_midday": (
        "universal-agent-proactive-report-midday",
        "universal-agent-proactive-report",
        "proactive_report_agent",
        600,
    ),
    "proactive_report_afternoon": (
        "universal-agent-proactive-report-afternoon",
        "universal-agent-proactive-report",
        "proactive_report_agent",
        600,
    ),
    "proactive_artifact_digest": (
        "universal-agent-proactive-artifact-digest",
        "universal-agent-proactive-artifact-digest",
        "proactive_digest_agent",
        300,
    ),
    # 2026-06-08 — migrated alongside proactive_artifact_digest; network job so carries TimeoutStartSec.
    "cron_artifact_reminders_sweep": (
        "universal-agent-artifact-reminders-sweep",
        "universal-agent-artifact-reminders-sweep",
        "cron_artifact_reminders_sweep",
        300,
    ),
    "intel_auto_promoter": (
        "universal-agent-intel-auto-promoter",
        "universal-agent-intel-auto-promoter",
        "intel_auto_promoter_cron",
        None,
    ),
    "codie_proactive_cleanup": (
        "universal-agent-codie-proactive-cleanup",
        "universal-agent-codie-proactive-cleanup",
        "codie_cleanup_enqueue",
        None,
    ),
}

# Batch 3 jobs (hourly active-window producers) — pinned here only so the
# frozenset-equality assertion below stays exact as later batches land. Their
# full contract is guarded in test_phase_a_batch3_timers.py.
A3 = {
    "hourly_intel_digest",
    "csi_convergence_sync",
}

# Batch A4 jobs (secret-bearing dailies) — pinned here only so the
# frozenset-equality assertion below stays exact. Their full contract is
# guarded in test_phase_a_batch4_timers.py.
A4 = {
    "youtube_daily_digest",
    "youtube_gold_channel_poller",
    "youtube_oauth_watchdog",
    "nightly_wiki",
    "morning_briefing",
    "evening_briefing",
    "csi_demo_triage_rank",
}


def _active_directives(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


# ----- frozenset / migration gate -------------------------------------------

def test_frozenset_is_exactly_batch1_plus_batch2_plus_batch3_plus_batch4():
    assert gateway_server._SYSTEMD_MIGRATED_SYSTEM_JOBS == frozenset(
        BATCH1 | set(A2) | A3 | A4
    )


@pytest.mark.parametrize("job", sorted(A2))
def test_is_migrated_true_by_default(job, monkeypatch):
    monkeypatch.delenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", raising=False)
    assert gateway_server._is_migrated_to_systemd(job) is True


@pytest.mark.parametrize("flag", ["1", "true", "yes", "on"])
def test_rollback_env_re_enables_all(flag, monkeypatch):
    monkeypatch.setenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", flag)
    for job in A2:
        assert gateway_server._is_migrated_to_systemd(job) is False


# ----- unit-file structure ---------------------------------------------------

@pytest.mark.parametrize("job", sorted(A2))
def test_timer_oncalendar_persistent_not_monotonic(job):
    timer_base, service_base, _module, _to = A2[job]
    lines = _active_directives((SYSTEMD_DIR / f"{timer_base}.timer").read_text())
    oncalendar = [ln for ln in lines if ln.startswith("OnCalendar=")]
    assert oncalendar, f"{timer_base}: no active OnCalendar"
    assert all("America/Chicago" in ln for ln in oncalendar)
    assert "Persistent=true" in lines
    assert not any(ln.startswith(("OnUnitActiveSec", "OnBootSec")) for ln in lines)
    assert f"Unit={service_base}.service" in lines


@pytest.mark.parametrize(
    "service_base,module",
    sorted({(v[1], v[2]) for v in A2.values()}),
)
def test_service_oneshot_execstart_and_backstop(service_base, module):
    lines = _active_directives((SYSTEMD_DIR / f"{service_base}.service").read_text())
    assert "Type=oneshot" in lines
    assert "User=ua" in lines
    assert "WorkingDirectory=/opt/universal_agent" in lines
    assert any(
        ln.startswith("ExecStart=") and f"-m universal_agent.scripts.{module}" in ln
        for ln in lines
    )
    env_files = [ln for ln in lines if ln.startswith("EnvironmentFile=")]
    assert env_files == ["EnvironmentFile=-/opt/universal_agent/.env"]
    assert "Environment=UA_DEPLOYMENT_PROFILE=vps" in lines


@pytest.mark.parametrize("job", sorted(A2))
def test_timeout_start_sec_only_on_network_llm_jobs(job):
    _timer, service_base, _module, timeout = A2[job]
    lines = _active_directives((SYSTEMD_DIR / f"{service_base}.service").read_text())
    timeouts = [ln for ln in lines if ln.startswith("TimeoutStartSec=")]
    if timeout is None:
        # pure-SQLite -> intentionally keep the oneshot infinity default.
        assert timeouts == [], f"{service_base}: unexpected TimeoutStartSec {timeouts}"
    else:
        assert timeouts == [f"TimeoutStartSec={timeout}"]


def test_three_report_timers_share_one_service():
    report_timers = [
        "universal-agent-proactive-report-morning",
        "universal-agent-proactive-report-midday",
        "universal-agent-proactive-report-afternoon",
    ]
    for t in report_timers:
        lines = _active_directives((SYSTEMD_DIR / f"{t}.timer").read_text())
        assert "Unit=universal-agent-proactive-report.service" in lines
    # exactly one shared report service file exists
    assert (SYSTEMD_DIR / "universal-agent-proactive-report.service").is_file()


def test_codie_execstart_passes_no_nudge():
    lines = _active_directives(
        (SYSTEMD_DIR / "universal-agent-codie-proactive-cleanup.service").read_text()
    )
    assert any(
        ln.startswith("ExecStart=") and ln.rstrip().endswith("--no-nudge")
        for ln in lines
    )


@pytest.mark.parametrize("job", sorted(A2))
def test_unit_module_is_run_by_the_registry(job):
    """The unit's ExecStart module must be a module the registry actually runs
    (a `!script universal_agent.scripts.<module>` command in gateway_server)."""
    _timer, _service, module, _to = A2[job]
    src = GATEWAY_SRC.read_text()
    assert f"!script universal_agent.scripts.{module}" in src


# ----- installer + deploy wiring --------------------------------------------

def test_installer_covers_all_units_and_arms_timers():
    text = INSTALLER.read_text()
    for _job, (timer_base, service_base, _m, _to) in A2.items():
        assert f"{timer_base}.timer" in text
        assert f"{service_base}.service" in text
    assert "daemon-reload" in text
    assert "enable --now" in text


def test_remote_deploy_wires_the_installer():
    assert "install_vps_phase_a_batch2_timers.sh" in REMOTE_DEPLOY.read_text()


# ----- codie bespoke double-fire gate (the new code path) --------------------

class _CronStub:
    def __init__(self):
        self.jobs: list[SimpleNamespace] = []
        self.updated: list[tuple[str, dict]] = []

    def list_jobs(self):
        return list(self.jobs)

    def add_job(self, **kw):
        job = SimpleNamespace(job_id=f"new_{len(self.jobs)}", to_dict=lambda: {"job_id": "new"}, **kw)
        self.jobs.append(job)
        return job

    def update_job(self, job_id, updates):
        self.updated.append((job_id, updates))
        return SimpleNamespace(job_id=job_id, to_dict=lambda: {"job_id": job_id, **updates})


def test_codie_gate_disables_existing_row_by_default(monkeypatch):
    """Default (migrated, no rollback): the bespoke ensure-fn flips an existing
    ENABLED codie row to disabled — so it stops double-firing — and adds no new
    enabled row."""
    stub = _CronStub()
    stub.jobs = [
        SimpleNamespace(
            job_id="legacy_codie",
            enabled=True,
            metadata={"system_job": "codie_proactive_cleanup"},
            to_dict=lambda: {"job_id": "legacy_codie", "enabled": False},
        )
    ]
    monkeypatch.setattr(gateway_server, "_cron_service", stub)
    monkeypatch.delenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", raising=False)
    monkeypatch.setenv("UA_CODIE_PROACTIVE_CLEANUP_ENABLED", "1")

    gateway_server._ensure_codie_proactive_cleanup_cron_job()

    assert stub.updated == [("legacy_codie", {"enabled": False})]


def test_codie_gate_rollback_registers_enabled(monkeypatch):
    """Rollback (UA_SYSTEMD_TIMER_MIGRATION_DISABLED=1): in-process registration
    resumes — the bespoke path adds an enabled codie job as before."""
    stub = _CronStub()
    monkeypatch.setattr(gateway_server, "_cron_service", stub)
    monkeypatch.setenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", "1")
    monkeypatch.setenv("UA_CODIE_PROACTIVE_CLEANUP_ENABLED", "1")

    result = gateway_server._ensure_codie_proactive_cleanup_cron_job()

    assert result is not None
    assert len(stub.jobs) == 1
    assert stub.jobs[0].metadata["system_job"] == "codie_proactive_cleanup"
    assert "codie_cleanup_enqueue" in (stub.jobs[0].command or "")
    assert not stub.updated
