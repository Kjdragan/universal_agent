"""Guard tests for the S5 Phase A batch-1 systemd-timer migration.

These pin the migration contract so the timer substrate cannot silently drift
from the in-process registry. For every migrated ``system_job``:

  (a) it is in ``gateway_server._SYSTEMD_MIGRATED_SYSTEM_JOBS`` — so its
      in-process cron registration is forced disabled (no double-fire);
  (b) a checked-in ``.timer``/``.service`` pair exists whose ``.timer`` is
      ``OnCalendar`` + ``Persistent=true`` and NEVER monotonic
      (``OnUnitActiveSec``/``OnBootSec`` would go ``NextElapse=infinity`` after
      the per-deploy ``daemon-reload`` — the S2 dead-timer class);
  (c) the ``.service`` ``ExecStart`` ``-m`` module equals the ``!script``
      module the registry actually runs (the module name != the system_job).

The batch installer + ``remote_deploy.sh`` wiring are also pinned so the units
reach ``/etc/systemd/system`` on every deploy.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

from universal_agent import gateway_server

REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEMD_DIR = REPO_ROOT / "deployment" / "systemd"
INSTALLER = REPO_ROOT / "scripts" / "install_vps_phase_a_batch1_timers.sh"
REMOTE_DEPLOY = REPO_ROOT / "scripts" / "deploy" / "remote_deploy.sh"
GATEWAY_SRC = REPO_ROOT / "src" / "universal_agent" / "gateway_server.py"


def _active_directives(text: str) -> list[str]:
    """Stripped, non-blank, non-comment lines — the real systemd directives.

    Unit files carry rationale comments that mention directive names
    (``OnUnitActiveSec``, ``EnvironmentFile=`` ...); assertions must look at the
    actual directives, not the prose.
    """
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out

# system_job -> (unit basename, scripts module the registry's `!script` runs).
# The module name deliberately differs from the system_job for 3 of 5.
MIGRATED = {
    "scratch_pruning": ("universal-agent-scratch-pruning", "prune_scratch"),
    "vault_lint_contradictions": (
        "universal-agent-vault-lint-contradictions",
        "vault_contradiction_lint",
    ),
    "architecture_canvas_drift": (
        "universal-agent-architecture-canvas-drift",
        "architecture_canvas_drift_check",
    ),
    "insight_scoring_health": (
        "universal-agent-insight-scoring-health",
        "insight_scoring_health",
    ),
    "vp_coder_workspace_pruning": (
        "universal-agent-vp-coder-workspace-pruning",
        "vp_coder_workspace_pruner",
    ),
}


def test_frozenset_contains_batch1_jobs():
    # Subset, not equality: later batches (A2+) grow the shared frozenset.
    # test_phase_a_batch2_timers.py pins the full membership.
    assert frozenset(MIGRATED) <= gateway_server._SYSTEMD_MIGRATED_SYSTEM_JOBS


@pytest.mark.parametrize("job", sorted(MIGRATED))
def test_is_migrated_to_systemd_true_by_default(job, monkeypatch):
    monkeypatch.delenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", raising=False)
    assert gateway_server._is_migrated_to_systemd(job) is True


@pytest.mark.parametrize("flag", ["1", "true", "yes", "on", "TRUE"])
def test_rollback_env_re_enables_all_in_process(flag, monkeypatch):
    monkeypatch.setenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", flag)
    for job in MIGRATED:
        assert gateway_server._is_migrated_to_systemd(job) is False


def test_unknown_job_is_never_migrated(monkeypatch):
    monkeypatch.delenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", raising=False)
    assert gateway_server._is_migrated_to_systemd("hackernews_snapshot") is False


@pytest.mark.parametrize("job", sorted(MIGRATED))
def test_timer_is_oncalendar_persistent_not_monotonic(job):
    base, _module = MIGRATED[job]
    lines = _active_directives((SYSTEMD_DIR / f"{base}.timer").read_text())
    oncalendar = [ln for ln in lines if ln.startswith("OnCalendar=")]
    assert oncalendar, "no active OnCalendar directive"
    # TZ-suffix form (systemd >= 240); DST handled by systemd, not a UTC convert.
    assert all("America/Chicago" in ln for ln in oncalendar)
    assert "Persistent=true" in lines
    # The dead-timer class (S2): a daemon-reload wipes a monotonic anchor.
    assert not any(
        ln.startswith(("OnUnitActiveSec", "OnBootSec")) for ln in lines
    )
    assert f"Unit={base}.service" in lines


@pytest.mark.parametrize("job", sorted(MIGRATED))
def test_service_oneshot_execstart_and_env_backstop(job):
    base, module = MIGRATED[job]
    lines = _active_directives((SYSTEMD_DIR / f"{base}.service").read_text())
    assert "Type=oneshot" in lines
    assert "User=ua" in lines
    assert "WorkingDirectory=/opt/universal_agent" in lines
    assert any(
        ln.startswith("ExecStart=")
        and f"-m universal_agent.scripts.{module}" in ln
        for ln in lines
    )
    # Single .env EnvironmentFile + the inline vps backstop (Phase B/C contract:
    # never a second EnvironmentFile; never drop the inline profile vars).
    env_files = [ln for ln in lines if ln.startswith("EnvironmentFile=")]
    assert env_files == ["EnvironmentFile=-/opt/universal_agent/.env"]
    assert "Environment=UA_DEPLOYMENT_PROFILE=vps" in lines


@pytest.mark.parametrize("job", sorted(MIGRATED))
def test_unit_execstart_module_matches_registry(job):
    """The .service ExecStart module must equal the module the in-process
    registry actually runs for this system_job, so the timer cannot drift from
    the registry's `!script` command."""
    _base, module = MIGRATED[job]
    src = GATEWAY_SRC.read_text()
    match = re.search(
        rf'system_job="{re.escape(job)}".*?'
        r'command="!script universal_agent\.scripts\.(\w+)"',
        src,
        re.DOTALL,
    )
    assert match, f"no registration command found for system_job={job}"
    assert match.group(1) == module


def test_installer_covers_all_units_and_arms_timers():
    text = INSTALLER.read_text()
    for _job, (base, _module) in MIGRATED.items():
        assert base in text, f"installer missing unit {base}"
    assert "daemon-reload" in text
    assert "enable --now" in text


def test_remote_deploy_wires_the_installer():
    text = REMOTE_DEPLOY.read_text()
    assert "install_vps_phase_a_batch1_timers.sh" in text


def test_insight_service_bounds_runtime_against_hang():
    """insight_scoring_health makes an LLM call + an AgentMail WebSocket
    connection — either can hang unboundedly. A Type=oneshot defaults to
    TimeoutStartSec=infinity, and a hung oneshot blocks its .timer from starting
    the next weekly run. Pin an explicit bound so a hang self-heals. (The
    pure-FS batch-1 jobs have no hang risk and intentionally keep the default.)"""
    lines = _active_directives(
        (SYSTEMD_DIR / "universal-agent-insight-scoring-health.service").read_text()
    )
    assert "TimeoutStartSec=600" in lines
