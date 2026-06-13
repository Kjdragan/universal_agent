"""Guard tests for the proactive demo-build lane sweep systemd timer.

This is a NEW producer-invoker (P1) — a deploy-independent systemd oneshot that
runs the broad YouTube demo-build lane 3x/day, decoupled from the dashboard's
event-triggered proactive-signal sync
(gateway_server._run_proactive_signal_sync_background -> sync_generated_cards ->
proactive_tutorial_builds.sync_build_oriented_csi_videos). Unlike the batch-A4
units it is NOT a migration of a registered in-process cron, so there is no
_SYSTEMD_MIGRATED_SYSTEM_JOBS entry, no _ensure_*_cron_job, and no double-fire
gate to guard. The tutorial-build:<sha256(video_id)> dedup in
queue_tutorial_build_task makes the timer + dashboard-event runs idempotent.

These guards pin the structural subset that matters: the wall-clock timer anchor,
the 3 active-window slots, the oneshot service shape + secret backstop, the
load-bearing initialize_runtime_secrets() bootstrap, and the installer +
remote_deploy wiring.
"""

from __future__ import annotations

from pathlib import Path

from universal_agent.services import dormancy

REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEMD_DIR = REPO_ROOT / "deployment" / "systemd"
INSTALLER = REPO_ROOT / "scripts" / "install_proactive_demo_build_sweep_timer.sh"
REMOTE_DEPLOY = REPO_ROOT / "scripts" / "deploy" / "remote_deploy.sh"
SRC_ROOT = REPO_ROOT / "src" / "universal_agent"

SERVICE_BASE = "universal-agent-proactive-demo-build-sweep"
TIMER_BASE = "universal-agent-proactive-demo-build-sweep"
MODULE = "scripts.proactive_demo_build_sweep"
ONCALENDAR_FRAGMENT = "08,13,18:30:00"
SLOT_HOURS = {8, 13, 18}
TIMEOUT_START_SEC = 900


def _active_directives(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


# ----- unit-file structure ---------------------------------------------------

def test_timer_oncalendar_persistent_not_monotonic():
    lines = _active_directives((SYSTEMD_DIR / f"{TIMER_BASE}.timer").read_text())
    oncalendar = [ln for ln in lines if ln.startswith("OnCalendar=")]
    assert oncalendar, f"{TIMER_BASE}: no active OnCalendar"
    assert all("America/Chicago" in ln for ln in oncalendar)
    assert all(ONCALENDAR_FRAGMENT in ln for ln in oncalendar), (
        f"{TIMER_BASE}: OnCalendar {oncalendar} missing fragment {ONCALENDAR_FRAGMENT!r}"
    )
    assert "Persistent=true" in lines
    assert not any(ln.startswith(("OnUnitActiveSec", "OnBootSec")) for ln in lines)
    assert f"Unit={SERVICE_BASE}.service" in lines


def test_three_slots_inside_active_window():
    """All three OnCalendar hours (8, 13, 18) must fall inside the active window
    (6 AM–10 PM Houston) so a content-generation job never fires overnight."""
    for hour in SLOT_HOURS:
        assert dormancy.ACTIVE_START_HOUR <= hour < dormancy.ACTIVE_END_HOUR, (
            f"slot hour {hour} outside active window "
            f"[{dormancy.ACTIVE_START_HOUR}, {dormancy.ACTIVE_END_HOUR})"
        )


def test_service_oneshot_execstart_and_backstop():
    lines = _active_directives((SYSTEMD_DIR / f"{SERVICE_BASE}.service").read_text())
    assert "Type=oneshot" in lines
    assert "User=ua" in lines
    assert "WorkingDirectory=/opt/universal_agent" in lines
    assert "Environment=PYTHONPATH=/opt/universal_agent/src" in lines
    exec_lines = [ln for ln in lines if ln.startswith("ExecStart=")]
    assert exec_lines, f"{SERVICE_BASE}: no ExecStart"
    assert any(f"-m universal_agent.{MODULE}" in ln for ln in exec_lines), (
        f"{SERVICE_BASE}: ExecStart {exec_lines} missing module {MODULE}"
    )
    # Full secret backstop — LOAD BEARING for a keyless-safe standalone oneshot.
    env_files = [ln for ln in lines if ln.startswith("EnvironmentFile=")]
    assert env_files == ["EnvironmentFile=-/opt/universal_agent/.env"]
    assert "Environment=UA_DEPLOYMENT_PROFILE=vps" in lines
    assert "Environment=UA_INFISICAL_ENABLED=1" in lines
    assert "Environment=INFISICAL_ENVIRONMENT=production" in lines


def test_timeout_start_sec_present_and_correct():
    """The job touches an LLM (buildability judge) under the oneshot, so it
    carries an explicit TimeoutStartSec sized to that bounded call."""
    lines = _active_directives((SYSTEMD_DIR / f"{SERVICE_BASE}.service").read_text())
    timeouts = [ln for ln in lines if ln.startswith("TimeoutStartSec=")]
    assert timeouts == [f"TimeoutStartSec={TIMEOUT_START_SEC}"]


# ----- the load-bearing secret audit ----------------------------------------

def test_sweep_module_bootstraps_secrets():
    """The standalone oneshot reaches the buildability LLM judge (Anthropic/ZAI
    key from os.environ) and does NOT inherit the gateway's loaded secrets, so the
    ExecStart module MUST call bare initialize_runtime_secrets() (so the unit's
    UA_DEPLOYMENT_PROFILE=vps backstop drives a strict production load). A
    hardcoded non-vps profile (local_workstation) would override the backstop and
    leave the judge keyless -> silent zero-queue failure."""
    rel = Path(*MODULE.split(".")).with_suffix(".py")
    src = (SRC_ROOT / rel).read_text()
    assert "initialize_runtime_secrets()" in src, (
        f"{MODULE} runs as a systemd oneshot and touches secrets but does not "
        f"call bare initialize_runtime_secrets() -> keyless silent failure"
    )
    assert "local_workstation" not in src, (
        f"{MODULE} must not introduce a local_workstation profile -> would "
        f"override the unit's vps backstop (keyless under systemd)"
    )


# ----- installer + deploy wiring --------------------------------------------

def test_installer_covers_units_and_arms_timer():
    text = INSTALLER.read_text()
    assert f"{TIMER_BASE}.timer" in text
    assert f"{SERVICE_BASE}.service" in text
    assert "daemon-reload" in text
    assert "enable --now" in text


def test_remote_deploy_wires_the_installer():
    text = REMOTE_DEPLOY.read_text()
    assert "install_proactive_demo_build_sweep_timer.sh" in text
    # Wired AFTER the batch-4 line (next to the existing csi-demo-triage-rank
    # installer invocation).
    assert text.index("install_proactive_demo_build_sweep_timer.sh") > text.index(
        "install_vps_phase_a_batch4_timers.sh"
    )
