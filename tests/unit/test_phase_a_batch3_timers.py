"""Guard tests for the S5 Phase A batch-3 systemd-timer migration (hourly producers).

Pins the batch-3 contract so the timer substrate can't silently drift from the
in-process registry. Batch 3 is a 2-service / 2-timer shape — both jobs fire at
the top of every hour (00..23 America/Chicago) and gate dormancy at RUNTIME via
``dormancy.should_run(env_var="UA_<JOB>_DORMANCY")`` (default windowed; flip the
env var to run 24/7 without a timer reinstall) — with two gate mechanisms the
batch-1/2 guards already exercise:

  * ``hourly_intel_digest`` registers via ``_register_system_cron_job`` and is
    gated by ANDing ``not _is_migrated_to_systemd(...)`` into its ``enabled=``
    arg (the STANDARD gate, same as intel_auto_promoter); and
  * ``csi_convergence_sync`` registers via a bespoke
    ``_cron_service.get_job/update_job`` path (NOT ``_register_system_cron_job``),
    so its double-fire disable lives directly in
    ``_ensure_csi_convergence_cron_job`` — behaviorally tested here, mirroring
    the codie bespoke gate.

Both jobs do real work under the oneshot, so BOTH carry ``TimeoutStartSec``:
hourly-intel-digest sends an AgentMail digest (network, 300s); csi-convergence-
sync is pure-SQLite but long-running (real runtime up to ~1017s, budget-capped),
so 900s — equal to its in-process ``timeout_seconds=900``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from universal_agent import gateway_server

REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEMD_DIR = REPO_ROOT / "deployment" / "systemd"
INSTALLER = REPO_ROOT / "scripts" / "install_vps_phase_a_batch3_timers.sh"
REMOTE_DEPLOY = REPO_ROOT / "scripts" / "deploy" / "remote_deploy.sh"
GATEWAY_SRC = REPO_ROOT / "src" / "universal_agent" / "gateway_server.py"

# system_job -> (timer_basename, service_basename, scripts module, TimeoutStartSec)
A3 = {
    "hourly_intel_digest": (
        "universal-agent-hourly-intel-digest",
        "universal-agent-hourly-intel-digest",
        "hourly_intel_digest_cron",
        300,
    ),
    "csi_convergence_sync": (
        "universal-agent-csi-convergence-sync",
        "universal-agent-csi-convergence-sync",
        "csi_convergence_sync",
        900,
    ),
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

def test_both_a3_jobs_in_frozenset():
    for job in A3:
        assert job in gateway_server._SYSTEMD_MIGRATED_SYSTEM_JOBS


@pytest.mark.parametrize("job", sorted(A3))
def test_is_migrated_true_by_default(job, monkeypatch):
    monkeypatch.delenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", raising=False)
    assert gateway_server._is_migrated_to_systemd(job) is True


@pytest.mark.parametrize("flag", ["1", "true", "yes", "on"])
def test_rollback_env_re_enables_all(flag, monkeypatch):
    monkeypatch.setenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", flag)
    for job in A3:
        assert gateway_server._is_migrated_to_systemd(job) is False


# ----- unit-file structure ---------------------------------------------------

@pytest.mark.parametrize("job", sorted(A3))
def test_timer_oncalendar_persistent_not_monotonic(job):
    timer_base, service_base, _module, _to = A3[job]
    lines = _active_directives((SYSTEMD_DIR / f"{timer_base}.timer").read_text())
    oncalendar = [ln for ln in lines if ln.startswith("OnCalendar=")]
    assert oncalendar, f"{timer_base}: no active OnCalendar"
    assert all("America/Chicago" in ln for ln in oncalendar)
    # Runtime-gated 24/7: fires the top of every hour (00..23). The per-run
    # dormancy decision moved into the ExecStart script via
    # dormancy.should_run(env_var="UA_<JOB>_DORMANCY"); default stays windowed,
    # a flipped env var runs 24/7 with no timer reinstall. The schedule<->gate
    # pairing is guarded in tests/unit/test_dormancy_schedule_consistency.py.
    assert all("00..23:00:00" in ln for ln in oncalendar)
    assert "Persistent=true" in lines
    assert not any(ln.startswith(("OnUnitActiveSec", "OnBootSec")) for ln in lines)
    assert f"Unit={service_base}.service" in lines


@pytest.mark.parametrize("job", sorted(A3))
def test_service_oneshot_execstart_and_backstop(job):
    _timer, service_base, module, _to = A3[job]
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
    assert "Environment=UA_INFISICAL_ENABLED=1" in lines
    assert "Environment=INFISICAL_ENVIRONMENT=production" in lines
    assert "Environment=PYTHONPATH=/opt/universal_agent/src" in lines


@pytest.mark.parametrize("job", sorted(A3))
def test_timeout_start_sec_present_and_correct(job):
    _timer, service_base, _module, timeout = A3[job]
    lines = _active_directives((SYSTEMD_DIR / f"{service_base}.service").read_text())
    timeouts = [ln for ln in lines if ln.startswith("TimeoutStartSec=")]
    assert timeouts == [f"TimeoutStartSec={timeout}"]


@pytest.mark.parametrize("job", sorted(A3))
def test_unit_module_is_run_by_the_registry(job):
    """The unit's ExecStart module must be a module the registry actually runs
    (a `!script universal_agent.scripts.<module>` command in gateway_server)."""
    _timer, _service, module, _to = A3[job]
    src = GATEWAY_SRC.read_text()
    assert f"!script universal_agent.scripts.{module}" in src


# ----- installer + deploy wiring --------------------------------------------

def test_installer_covers_all_units_and_arms_timers():
    text = INSTALLER.read_text()
    for _job, (timer_base, service_base, _m, _to) in A3.items():
        assert f"{timer_base}.timer" in text
        assert f"{service_base}.service" in text
    assert "daemon-reload" in text
    assert "enable --now" in text


def test_remote_deploy_wires_the_installer():
    assert "install_vps_phase_a_batch3_timers.sh" in REMOTE_DEPLOY.read_text()


SCRIPTS_DIR = REPO_ROOT / "src" / "universal_agent" / "scripts"


@pytest.mark.parametrize("module", ["hourly_intel_digest_cron", "csi_convergence_sync"])
def test_a3_secret_touching_modules_bootstrap_secrets(module):
    """Both A3 jobs reach an LLM/AgentMail key under systemd. A standalone
    oneshot does NOT inherit the gateway's loaded secrets, so each ExecStart
    module MUST call initialize_runtime_secrets() itself, with NO hardcoded
    profile= (so the unit's UA_DEPLOYMENT_PROFILE=vps backstop drives a strict
    production load). This guard exists because csi_convergence_sync was
    initially shipped without the bootstrap — a keyless silent-failure trap."""
    src = (SCRIPTS_DIR / f"{module}.py").read_text()
    assert "initialize_runtime_secrets()" in src, (
        f"{module} runs as a systemd oneshot and touches secrets but does not "
        f"call initialize_runtime_secrets() -> keyless silent failure"
    )
    assert 'initialize_runtime_secrets(profile="local_workstation")' not in src, (
        f"{module} hardcodes profile=local_workstation, which overrides the "
        f"unit's vps backstop -> keyless under systemd"
    )


# ----- in-process double-fire gates (the two code paths) ---------------------

class _CronStub:
    """Minimal cron-service stub covering both gate code paths.

    ``list_jobs`` feeds ``_register_system_cron_job`` (standard gate) via
    ``_find_cron_job_by_system_job``; ``get_job`` feeds the bespoke csi gate.
    """

    def __init__(self):
        self.jobs: list[SimpleNamespace] = []
        self.updated: list[tuple[str, dict]] = []

    def list_jobs(self):
        return list(self.jobs)

    def get_job(self, job_id):
        for job in self.jobs:
            if str(getattr(job, "job_id", "")) == job_id:
                return job
        return None

    def update_job(self, job_id, updates):
        self.updated.append((job_id, updates))
        return SimpleNamespace(job_id=job_id, to_dict=lambda: {"job_id": job_id, **updates})


def test_hourly_intel_digest_standard_gate_disables_existing_row(monkeypatch):
    """Default (migrated, no rollback): the standard _register_system_cron_job
    gate forces enabled=False, so an existing ENABLED row is flipped to disabled
    and no new enabled row is added — the systemd timer is the sole firer."""
    stub = _CronStub()
    stub.jobs = [
        SimpleNamespace(
            job_id="legacy_hourly_intel_digest",
            enabled=True,
            metadata={"system_job": "hourly_intel_digest"},
            to_dict=lambda: {"job_id": "legacy_hourly_intel_digest", "enabled": False},
        )
    ]
    monkeypatch.setattr(gateway_server, "_cron_service", stub)
    monkeypatch.delenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", raising=False)
    monkeypatch.setenv("UA_INTEL_DIGEST_CRON_ENABLED", "1")

    gateway_server._ensure_hourly_intel_digest_cron_job()

    assert stub.updated == [("legacy_hourly_intel_digest", {"enabled": False})]


def test_hourly_intel_digest_rollback_registers_enabled(monkeypatch):
    """Rollback (UA_SYSTEMD_TIMER_MIGRATION_DISABLED=1): in-process registration
    resumes — _register_system_cron_job updates the existing row to enabled."""
    stub = _CronStub()
    stub.jobs = [
        SimpleNamespace(
            job_id="legacy_hourly_intel_digest",
            enabled=False,
            metadata={"system_job": "hourly_intel_digest"},
            to_dict=lambda: {"job_id": "legacy_hourly_intel_digest", "enabled": True},
        )
    ]
    monkeypatch.setattr(gateway_server, "_cron_service", stub)
    monkeypatch.setenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", "1")
    monkeypatch.setenv("UA_INTEL_DIGEST_CRON_ENABLED", "1")

    result = gateway_server._ensure_hourly_intel_digest_cron_job()

    assert result is not None
    assert stub.updated
    job_id, updates = stub.updated[-1]
    assert job_id == "legacy_hourly_intel_digest"
    assert updates["enabled"] is True
    assert "hourly_intel_digest_cron" in (updates.get("command") or "")


def test_csi_gate_disables_existing_row_by_default(monkeypatch):
    """Default (migrated, no rollback): the bespoke ensure-fn flips an existing
    ENABLED csi row to disabled — so it stops double-firing — and adds no new
    enabled row."""
    stub = _CronStub()
    stub.jobs = [
        SimpleNamespace(
            job_id="csi_convergence_sync",
            enabled=True,
            metadata={"system_job": "csi_convergence_sync"},
            to_dict=lambda: {"job_id": "csi_convergence_sync", "enabled": False},
        )
    ]
    monkeypatch.setattr(gateway_server, "_cron_service", stub)
    monkeypatch.delenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", raising=False)
    monkeypatch.setenv("UA_CSI_CONVERGENCE_CRON_ENABLED", "1")

    gateway_server._ensure_csi_convergence_cron_job()

    assert stub.updated == [("csi_convergence_sync", {"enabled": False})]


def test_csi_gate_rollback_registers_enabled(monkeypatch):
    """Rollback (UA_SYSTEMD_TIMER_MIGRATION_DISABLED=1): in-process registration
    resumes — the bespoke path updates the existing csi job to enabled."""
    stub = _CronStub()
    stub.jobs = [
        SimpleNamespace(
            job_id="csi_convergence_sync",
            enabled=False,
            metadata={"system_job": "csi_convergence_sync"},
            to_dict=lambda: {"job_id": "csi_convergence_sync", "enabled": True},
        )
    ]
    monkeypatch.setattr(gateway_server, "_cron_service", stub)
    monkeypatch.setenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", "1")
    monkeypatch.setenv("UA_CSI_CONVERGENCE_CRON_ENABLED", "1")

    gateway_server._ensure_csi_convergence_cron_job()

    assert stub.updated
    job_id, updates = stub.updated[-1]
    assert job_id == "csi_convergence_sync"
    assert updates["enabled"] is True
    assert "csi_convergence_sync" in (updates.get("command") or "")
