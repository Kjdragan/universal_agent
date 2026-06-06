"""Guard tests for the S5 Phase A batch-A4 systemd-timer migration.

Batch A4 is the highest-care batch: 7 SECRET-BEARING jobs (YouTube OAuth tokens,
NotebookLM cookies, UA_OPS_TOKEN, an Anthropic key) migrated from in-process
cron to deploy-independent systemd timers. It is a 7-service / 7-timer shape —
one pair per job. The morning and evening briefings share ``briefings_agent.py``
but run as SEPARATE units (the evening ExecStart passes ``--mode=evening``).

Two gate mechanisms, both already exercised by the batch-1/2/3 guards:

  * ``youtube_daily_digest`` and ``youtube_gold_channel_poller`` register via a
    bespoke ``_find_cron_job_by_system_job``/``update_job`` path (NOT
    ``_register_system_cron_job``), so their double-fire disable lives directly
    in their ``_ensure_*`` fns (flip the existing enabled row to disabled when
    migrated), mirroring the codie bespoke gate; and
  * the other five (``youtube_oauth_watchdog``, ``nightly_wiki``,
    ``morning_briefing``, ``evening_briefing``, ``csi_demo_triage_rank``)
    register via ``_register_system_cron_job`` and are gated by ANDing
    ``not _is_migrated_to_systemd(...)`` into their ``enabled=`` arg (the
    STANDARD gate).

EVERY one of the 7 jobs touches an LLM / OAuth / network secret under the
oneshot, so EVERY service carries an explicit ``TimeoutStartSec`` sized to that
job's registered timeout (or, for the two bespoke jobs which registered no
in-process timeout, a deliberate explicit cap). The load-bearing secret guard
(``test_a4_secret_touching_modules_bootstrap_secrets``) pins that each ExecStart
module calls bare ``initialize_runtime_secrets()`` — the batch-3
csi_convergence_sync keyless-silent-failure lesson, applied to all 7.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from universal_agent import gateway_server

REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEMD_DIR = REPO_ROOT / "deployment" / "systemd"
INSTALLER = REPO_ROOT / "scripts" / "install_vps_phase_a_batch4_timers.sh"
REMOTE_DEPLOY = REPO_ROOT / "scripts" / "deploy" / "remote_deploy.sh"
GATEWAY_SRC = REPO_ROOT / "src" / "universal_agent" / "gateway_server.py"
SRC_ROOT = REPO_ROOT / "src" / "universal_agent"

# system_job -> (
#   timer_basename, service_basename,
#   module_dotted (relative to universal_agent., e.g. "scripts.x" / "services.x"),
#   TimeoutStartSec, oncalendar_fragment, execstart_suffix (or None)
# )
A4 = {
    "youtube_daily_digest": (
        "universal-agent-youtube-daily-digest",
        "universal-agent-youtube-daily-digest",
        "scripts.youtube_daily_digest",
        1800,
        "06:00:00",
        None,
    ),
    "youtube_gold_channel_poller": (
        "universal-agent-youtube-gold-channel-poller",
        "universal-agent-youtube-gold-channel-poller",
        "services.youtube_gold_channel_poller",
        300,
        "05:30:00",
        None,
    ),
    "youtube_oauth_watchdog": (
        "universal-agent-youtube-oauth-watchdog",
        "universal-agent-youtube-oauth-watchdog",
        "scripts.youtube_oauth_watchdog",
        120,
        "07:00:00",
        None,
    ),
    "nightly_wiki": (
        "universal-agent-nightly-wiki",
        "universal-agent-nightly-wiki",
        "scripts.nightly_wiki_agent",
        1800,
        "03:15:00",
        None,
    ),
    "morning_briefing": (
        "universal-agent-morning-briefing",
        "universal-agent-morning-briefing",
        "scripts.briefings_agent",
        900,
        "06:30:00",
        None,
    ),
    "evening_briefing": (
        "universal-agent-evening-briefing",
        "universal-agent-evening-briefing",
        "scripts.briefings_agent",
        900,
        "18:00:00",
        "--mode=evening",
    ),
    "csi_demo_triage_rank": (
        "universal-agent-csi-demo-triage-rank",
        "universal-agent-csi-demo-triage-rank",
        "scripts.csi_demo_triage_rank",
        600,
        "10,15:05:00",
        None,
    ),
}

# The two bespoke-gate jobs (mirror codie_proactive_cleanup): disabled directly
# in their _ensure_* fn, not via _register_system_cron_job(enabled=…).
BESPOKE_JOBS = {"youtube_daily_digest", "youtube_gold_channel_poller"}
# Every module the 7 ExecStarts run must bootstrap secrets itself.
SECRET_MODULES = sorted({v[2] for v in A4.values()})


def _active_directives(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


# ----- frozenset / migration gate -------------------------------------------

def test_all_a4_jobs_in_frozenset():
    for job in A4:
        assert job in gateway_server._SYSTEMD_MIGRATED_SYSTEM_JOBS


@pytest.mark.parametrize("job", sorted(A4))
def test_is_migrated_true_by_default(job, monkeypatch):
    monkeypatch.delenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", raising=False)
    assert gateway_server._is_migrated_to_systemd(job) is True


@pytest.mark.parametrize("flag", ["1", "true", "yes", "on"])
def test_rollback_env_re_enables_all(flag, monkeypatch):
    monkeypatch.setenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", flag)
    for job in A4:
        assert gateway_server._is_migrated_to_systemd(job) is False


# ----- unit-file structure ---------------------------------------------------

@pytest.mark.parametrize("job", sorted(A4))
def test_timer_oncalendar_persistent_not_monotonic(job):
    timer_base, service_base, _module, _to, fragment, _suffix = A4[job]
    lines = _active_directives((SYSTEMD_DIR / f"{timer_base}.timer").read_text())
    oncalendar = [ln for ln in lines if ln.startswith("OnCalendar=")]
    assert oncalendar, f"{timer_base}: no active OnCalendar"
    assert all("America/Chicago" in ln for ln in oncalendar)
    # The OnCalendar wall-clock fragment must match the job's exact cron slot.
    assert all(fragment in ln for ln in oncalendar), (
        f"{timer_base}: OnCalendar {oncalendar} missing fragment {fragment!r}"
    )
    assert "Persistent=true" in lines
    assert not any(ln.startswith(("OnUnitActiveSec", "OnBootSec")) for ln in lines)
    assert f"Unit={service_base}.service" in lines


@pytest.mark.parametrize("job", sorted(A4))
def test_service_oneshot_execstart_and_backstop(job):
    _timer, service_base, module, _to, _frag, suffix = A4[job]
    lines = _active_directives((SYSTEMD_DIR / f"{service_base}.service").read_text())
    assert "Type=oneshot" in lines
    assert "User=ua" in lines
    assert "WorkingDirectory=/opt/universal_agent" in lines
    exec_lines = [ln for ln in lines if ln.startswith("ExecStart=")]
    assert exec_lines, f"{service_base}: no ExecStart"
    assert any(f"-m universal_agent.{module}" in ln for ln in exec_lines), (
        f"{service_base}: ExecStart {exec_lines} missing module {module}"
    )
    if suffix is not None:
        assert any(ln.rstrip().endswith(suffix) for ln in exec_lines), (
            f"{service_base}: ExecStart {exec_lines} missing suffix {suffix!r}"
        )
    # Full secret backstop — LOAD BEARING for a keyless-safe standalone oneshot.
    env_files = [ln for ln in lines if ln.startswith("EnvironmentFile=")]
    assert env_files == ["EnvironmentFile=-/opt/universal_agent/.env"]
    assert "Environment=UA_DEPLOYMENT_PROFILE=vps" in lines
    assert "Environment=UA_INFISICAL_ENABLED=1" in lines
    assert "Environment=INFISICAL_ENVIRONMENT=production" in lines
    assert "Environment=PYTHONPATH=/opt/universal_agent/src" in lines


@pytest.mark.parametrize("job", sorted(A4))
def test_timeout_start_sec_present_and_correct(job):
    """ALL 7 jobs touch network/LLM/OAuth, so EACH carries an explicit
    TimeoutStartSec (unlike batch 2's pure-SQLite jobs which kept infinity)."""
    _timer, service_base, _module, timeout, _frag, _suffix = A4[job]
    lines = _active_directives((SYSTEMD_DIR / f"{service_base}.service").read_text())
    timeouts = [ln for ln in lines if ln.startswith("TimeoutStartSec=")]
    assert timeouts == [f"TimeoutStartSec={timeout}"]


def test_morning_and_evening_are_separate_units_same_module():
    """The two briefings share briefings_agent but cannot share one service —
    the evening unit passes --mode=evening, the morning does not."""
    morning = _active_directives(
        (SYSTEMD_DIR / "universal-agent-morning-briefing.service").read_text()
    )
    evening = _active_directives(
        (SYSTEMD_DIR / "universal-agent-evening-briefing.service").read_text()
    )
    m_exec = [ln for ln in morning if ln.startswith("ExecStart=")]
    e_exec = [ln for ln in evening if ln.startswith("ExecStart=")]
    assert m_exec and e_exec
    assert all("briefings_agent" in ln for ln in m_exec + e_exec)
    assert not any(ln.rstrip().endswith("--mode=evening") for ln in m_exec)
    assert all(ln.rstrip().endswith("--mode=evening") for ln in e_exec)


@pytest.mark.parametrize("job", sorted(A4))
def test_unit_module_is_run_by_the_registry(job):
    """The unit's ExecStart module must be a module the registry actually runs
    (a `!script universal_agent.<scripts|services>.<module>` command)."""
    _timer, _service, module, _to, _frag, _suffix = A4[job]
    src = GATEWAY_SRC.read_text()
    assert f"!script universal_agent.{module}" in src


# ----- the load-bearing secret audit ----------------------------------------

@pytest.mark.parametrize("module", SECRET_MODULES)
def test_a4_secret_touching_modules_bootstrap_secrets(module):
    """Every A4 job reaches an OAuth token / NotebookLM cookie / UA_OPS_TOKEN /
    Anthropic key under systemd. A standalone oneshot does NOT inherit the
    gateway's loaded secrets, so each ExecStart module MUST call
    initialize_runtime_secrets() itself, with NO hardcoded profile= (so the
    unit's UA_DEPLOYMENT_PROFILE=vps backstop drives a strict production load).
    This guard exists because csi_convergence_sync (batch 3) shipped without the
    bootstrap — a keyless silent-failure trap. The M2 fixes converted
    nightly_wiki_agent and briefings_agent off profile="local_workstation"."""
    # module is "scripts.x" / "services.x" relative to universal_agent.
    rel = Path(*module.split(".")).with_suffix(".py")
    src = (SRC_ROOT / rel).read_text()
    # Accept the bare call OR the faithful `profile=args.profile or None` form
    # (csi_demo_triage_rank's --profile defaults to "" -> None under systemd, so
    # UA_DEPLOYMENT_PROFILE=vps is still honored). What we forbid below is a
    # HARDCODED non-vps profile that would override the unit backstop.
    bootstraps = (
        "initialize_runtime_secrets()" in src
        or "initialize_runtime_secrets(profile=args.profile or None)" in src
    )
    assert bootstraps, (
        f"{module} runs as a systemd oneshot and touches secrets but does not "
        f"call initialize_runtime_secrets() in a vps-backstop-honoring form "
        f"-> keyless silent failure"
    )
    assert 'initialize_runtime_secrets(profile="local_workstation")' not in src, (
        f"{module} hardcodes profile=local_workstation, which overrides the "
        f"unit's vps backstop -> keyless under systemd"
    )


# ----- installer + deploy wiring --------------------------------------------

def test_installer_covers_all_units_and_arms_timers():
    text = INSTALLER.read_text()
    for _job, (timer_base, service_base, _m, _to, _f, _s) in A4.items():
        assert f"{timer_base}.timer" in text
        assert f"{service_base}.service" in text
    assert "daemon-reload" in text
    assert "enable --now" in text


def test_remote_deploy_wires_the_installer():
    text = REMOTE_DEPLOY.read_text()
    assert "install_vps_phase_a_batch4_timers.sh" in text
    # Wired AFTER the batch-3 line.
    assert text.index("install_vps_phase_a_batch4_timers.sh") > text.index(
        "install_vps_phase_a_batch3_timers.sh"
    )


# ----- in-process double-fire gates (both code paths) ------------------------

class _CronStub:
    """Minimal cron-service stub covering both gate code paths.

    ``list_jobs`` feeds both ``_register_system_cron_job`` (standard gate) and
    ``_find_cron_job_by_system_job`` (the bespoke youtube gates), each via the
    job's ``metadata['system_job']``. ``get_job`` is unused by A4 but kept for
    parity."""

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
        return SimpleNamespace(
            job_id=job_id, to_dict=lambda: {"job_id": job_id, **updates}
        )

    def add_job(self, **kwargs):  # pragma: no cover - must never fire when migrated
        raise AssertionError("add_job called while migrated — double-fire bug")


def _legacy_row(system_job: str, enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        job_id=f"legacy_{system_job}",
        enabled=enabled,
        metadata={"system_job": system_job},
        to_dict=lambda: {"job_id": f"legacy_{system_job}", "enabled": enabled},
    )


# --- standard gate (the five _register_system_cron_job jobs) -----------------

STANDARD_GATE = {
    "youtube_oauth_watchdog": (
        gateway_server._ensure_youtube_oauth_watchdog_cron_job,
        "UA_YOUTUBE_OAUTH_WATCHDOG_ENABLED",
    ),
    "nightly_wiki": (
        gateway_server._ensure_nightly_wiki_cron_job,
        "UA_NIGHTLY_WIKI_ENABLED",
    ),
    "morning_briefing": (
        gateway_server._ensure_morning_briefing_cron_job,
        "UA_MORNING_BRIEFING_ENABLED",
    ),
    "evening_briefing": (
        gateway_server._ensure_evening_briefing_cron_job,
        "UA_EVENING_BRIEFING_ENABLED",
    ),
    "csi_demo_triage_rank": (
        gateway_server._ensure_csi_demo_triage_rank_cron_job,
        "UA_CSI_DEMO_TRIAGE_RANK_CRON_ENABLED",
    ),
}


@pytest.mark.parametrize("job", sorted(STANDARD_GATE))
def test_standard_gate_disables_existing_row_by_default(job, monkeypatch):
    """Default (migrated, no rollback): the standard _register_system_cron_job
    gate forces enabled=False, so an existing ENABLED row is flipped to disabled
    and no new enabled row is added — the systemd timer is the sole firer."""
    ensure_fn, enable_var = STANDARD_GATE[job]
    stub = _CronStub()
    stub.jobs = [_legacy_row(job, enabled=True)]
    monkeypatch.setattr(gateway_server, "_cron_service", stub)
    monkeypatch.delenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", raising=False)
    monkeypatch.setenv(enable_var, "1")

    ensure_fn()

    assert stub.updated == [(f"legacy_{job}", {"enabled": False})]


@pytest.mark.parametrize("job", sorted(STANDARD_GATE))
def test_standard_gate_rollback_registers_enabled(job, monkeypatch):
    """Rollback (UA_SYSTEMD_TIMER_MIGRATION_DISABLED=1): in-process registration
    resumes — _register_system_cron_job updates the existing row to enabled."""
    ensure_fn, enable_var = STANDARD_GATE[job]
    stub = _CronStub()
    stub.jobs = [_legacy_row(job, enabled=False)]
    monkeypatch.setattr(gateway_server, "_cron_service", stub)
    monkeypatch.setenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", "1")
    monkeypatch.setenv(enable_var, "1")

    ensure_fn()

    assert stub.updated
    job_id, updates = stub.updated[-1]
    assert job_id == f"legacy_{job}"
    assert updates["enabled"] is True


# --- bespoke gate (the two youtube jobs) -------------------------------------

BESPOKE_GATE = {
    "youtube_daily_digest": (
        gateway_server._ensure_youtube_daily_digest_cron_job,
        "UA_YOUTUBE_DAILY_DIGEST_ENABLED",
        "youtube_daily_digest",
    ),
    "youtube_gold_channel_poller": (
        gateway_server._ensure_youtube_gold_poller_cron_job,
        "UA_YOUTUBE_GOLD_POLLER_ENABLED",
        "youtube_daily_digest",  # placeholder, command-substring checked below
    ),
}


@pytest.mark.parametrize("job", sorted(BESPOKE_GATE))
def test_bespoke_gate_disables_existing_row_by_default(job, monkeypatch):
    """Default (migrated, no rollback): the bespoke ensure-fn flips an existing
    ENABLED youtube row to disabled — so it stops double-firing — and adds no
    new enabled row (add_job would raise in the stub)."""
    ensure_fn, enable_var, _cmd = BESPOKE_GATE[job]
    stub = _CronStub()
    stub.jobs = [_legacy_row(job, enabled=True)]
    monkeypatch.setattr(gateway_server, "_cron_service", stub)
    monkeypatch.delenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", raising=False)
    monkeypatch.setenv(enable_var, "1")

    ensure_fn()

    assert stub.updated == [(f"legacy_{job}", {"enabled": False})]


@pytest.mark.parametrize("job", sorted(BESPOKE_GATE))
def test_bespoke_gate_rollback_registers_enabled(job, monkeypatch):
    """Rollback (UA_SYSTEMD_TIMER_MIGRATION_DISABLED=1): in-process registration
    resumes — the bespoke path updates the existing youtube row to enabled."""
    ensure_fn, enable_var, _cmd = BESPOKE_GATE[job]
    stub = _CronStub()
    stub.jobs = [_legacy_row(job, enabled=False)]
    monkeypatch.setattr(gateway_server, "_cron_service", stub)
    monkeypatch.setenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", "1")
    monkeypatch.setenv(enable_var, "1")

    ensure_fn()

    assert stub.updated
    job_id, updates = stub.updated[-1]
    assert job_id == f"legacy_{job}"
    assert updates["enabled"] is True
